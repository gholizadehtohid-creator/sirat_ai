"""Fetch and parse RSS/Atom feeds -- the only network layer of the report.

BINDING, BECAUSE THE ROUTE DIFFERS BY DESTINATION
-------------------------------------------------
Iranian sites (bind=True) are geo-blocked through the VPN tunnel and must exit
the physical NIC; foreign sites are reachable through the tunnel and must NOT be
bound to the local adapter. So two sessions are built -- one source-bound, one
not -- and each feed uses the right one. This mirrors what the rest of the
codebase learned the hard way about TSETMC (see providers/transport.py).

PARSING IS DEFENSIVE
--------------------
Feeds are RSS 2.0, Atom, or something in between; some return an HTML error page
with a 200. The parser walks by local element name (namespace-agnostic), accepts
both <item> and <entry>, and treats a body with zero items as an empty-but-ok
fetch rather than an error. HTML in descriptions is stripped; a missing or
unparseable date yields published_at=None -- never a guessed timestamp.
"""

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import settings
from providers import transport
from event_intelligence import sources as sources_mod


RSS_HEADERS = {
    "Accept": "application/rss+xml, application/atom+xml, application/xml, "
              "text/xml, */*",
    "Accept-Language": "en-US,en;q=0.8,fa;q=0.6",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# ---- text helpers ---------------------------------------------------------
def _local(tag):
    """Local element name, stripping any {namespace} prefix."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _clean(text, limit=600):
    """Strip HTML/CDATA, unescape entities, collapse whitespace, bound length."""
    if not text:
        return ""
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)          # entities revealed after tag removal
    text = _WS_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _parse_date(raw):
    """RFC822/Atom date -> UTC ISO string, or None if absent/unparseable.

    Naive datetimes (a feed that gave a zone name the stdlib does not know) are
    treated as UTC rather than as local time -- deterministic, and this is a
    report, not a trade.
    """
    if not raw:
        return None
    raw = raw.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        # Atom ISO-8601 (e.g. 2026-08-22T19:40:52Z or +03:30)
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _link_of(item):
    """Extract the article URL from an <item>/<entry>, RSS or Atom style."""
    best = ""
    for ch in item:
        if _local(ch.tag) != "link":
            continue
        href = ch.get("href")
        rel = (ch.get("rel") or "").lower()
        if href:
            if rel in ("", "alternate"):
                return href
            best = best or href
        elif ch.text and ch.text.strip():
            return ch.text.strip()
    return best


def _first_text(item, names):
    for ch in item:
        if _local(ch.tag) in names and ch.text and ch.text.strip():
            return ch.text.strip()
    return ""


def parse_feed(content):
    """Parse feed bytes/str into a list of raw item dicts. Never raises on data."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None                     # signal: body was not XML

    # A well-formed XML document that is not a feed (e.g. an HTML error page
    # served as XHTML) parses fine but is not a feed -- treat it as not-a-feed.
    if _local(root.tag) not in ("rss", "feed", "rdf"):
        return None

    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _clean(_first_text(el, {"title"}), limit=300)
        if not title:
            continue
        summary = _clean(_first_text(
            el, {"description", "summary", "content", "encoded"}))
        guid = _first_text(el, {"guid", "id"})
        link = _link_of(el)
        raw_date = _first_text(
            el, {"pubdate", "published", "updated", "date"})
        items.append({
            "title": title,
            "summary": summary,
            "link": link,
            "guid": guid,
            "published_raw": raw_date,
            "published_at": _parse_date(raw_date),
        })
    return items


def _build_sessions():
    """One source-bound session (Iranian hosts) and one unbound (foreign)."""
    iran = transport.build_session(
        headers=RSS_HEADERS, bind_source=True, host="www.isna.ir")
    world = transport.build_session(headers=RSS_HEADERS, bind_source=False)
    for s in (iran, world):
        # A tsetmc Referer on a BBC request is at best odd, at worst WAF-bait.
        s.headers.pop("Referer", None)
    return iran, world


def fetch_source(source, session, limiter=None, cap=40, timeout=15.0):
    """Fetch and parse one RSS source. Returns a result dict, never raises."""
    name, url = source["name"], source["url"]
    if limiter is not None:
        limiter.wait()
    try:
        resp = session.get(url, timeout=timeout)
        status = resp.status_code
        if status != 200:
            return {"source": name, "ok": False, "status": str(status),
                    "items": []}
        items = parse_feed(resp.content)
        if items is None:
            return {"source": name, "ok": False, "status": "not-xml",
                    "items": []}
        return {"source": name, "ok": True, "status": "ok",
                "items": items[:cap]}
    except Exception as exc:                                   # noqa: BLE001
        return {"source": name, "ok": False,
                "status": f"{type(exc).__name__}", "items": []}


def fetch_all(source_list=None, cap=40, limiter_interval=0.2):
    """Fetch every RSS source concurrently. Returns list of result dicts.

    Each result carries the originating source dict under 'meta' so the caller
    can classify with the source's tier/lang/market-hint without a second lookup.
    """
    src = source_list if source_list is not None else sources_mod.rss_sources()
    iran, world = _build_sessions()
    limiter = transport.RateLimiter(limiter_interval)

    # map_concurrent keys its result dict by the item, so items must be hashable
    # -- pass indices, not the source dicts. It also drives the limiter itself,
    # so fetch_source is called with limiter=None to avoid double spacing.
    def one(i):
        s = src[i]
        session = iran if s.get("bind") else world
        result = fetch_source(s, session, limiter=None, cap=cap)
        result["meta"] = s
        return result

    res_map = transport.map_concurrent(
        one, list(range(len(src))), max_workers=settings.MAX_WORKERS,
        limiter=limiter)

    results = []
    for i, s in enumerate(src):
        r = res_map.get(i)
        if not isinstance(r, dict):     # map_concurrent captured an exception
            r = {"source": s["name"], "ok": False,
                 "status": f"{type(r).__name__}", "items": [], "meta": s}
        results.append(r)
    return results
