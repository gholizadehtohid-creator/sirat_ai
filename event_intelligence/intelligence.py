"""Public entry points for the intelligence report -- what app.py calls.

collect() is the write path: fetch every reachable feed, classify each article,
store it, and record source health plus a provenance row. report() is the read
path: a thin wrapper over report.build().

Nothing here touches factor_engine, swing_advisor, or the PIT firewall. The only
database it writes is news_articles / news_sources (its own tables) and the
shared data_provenance ledger, which is a record of WHERE data came from -- not a
trading input.
"""

from datetime import datetime, timezone

import database
from event_intelligence import feeds, classify, dedup, store
from event_intelligence import report as report_mod
from event_intelligence import taxonomy
from event_intelligence import sources as sources_mod


PROVENANCE_SCOPE = "news_intelligence"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect(cap=40):
    """Fetch, classify, and store all reachable feeds. Returns a run summary."""

    store.ensure_schema()
    fetched_at = _now_iso()
    results = feeds.fetch_all(cap=cap)

    rows = []
    per_source = []
    ok = failed = 0

    for res in results:
        meta = res.get("meta", {})
        name = res["source"]
        items = res.get("items", [])
        status = res.get("status", "?")

        store.record_source_health(
            name=name, url=meta.get("url", ""), tier=meta.get("tier"),
            lang=meta.get("lang"), kind=meta.get("kind", "rss"),
            markets_hint=",".join(meta.get("markets", [])),
            status=status, items=len(items), fetched_at=fetched_at)

        if res.get("ok"):
            ok += 1
        else:
            failed += 1
        per_source.append({"source": name, "status": status,
                           "items": len(items)})

        for it in items:
            cls = classify.classify(
                it["title"], it.get("summary", ""),
                tier=meta.get("tier"), lang=meta.get("lang"),
                markets_hint=meta.get("markets"),
                focus=meta.get("focus", "econ"))
            rows.append({
                "uid": store.make_uid(name, it.get("guid"),
                                      it.get("link"), it["title"]),
                "source": name,
                "tier": meta.get("tier"),
                "lang": meta.get("lang"),
                "title": it["title"],
                "summary": it.get("summary", ""),
                "link": it.get("link", ""),
                "published_at": it.get("published_at"),
                "published_raw": it.get("published_raw", ""),
                "fetched_at": fetched_at,
                "categories": cls["categories"],
                "markets": cls["markets"],
                "tone": cls["tone"],
                "salience": cls["salience"],
                "event_key": dedup.title_signature(it["title"]),
                "taxonomy_ver": taxonomy.VERSION,
                "is_economic": cls["is_economic"],
                "is_analysis": cls["is_analysis"],
                "is_price_forecast": cls["is_price_forecast"],
            })

    # HTML-only sources (e.g. بانک مرکزی) are known but not RSS-fetchable yet;
    # record them so the panel shows the gap honestly rather than hiding it.
    for s in sources_mod.all_sources():
        if s.get("kind") == "html":
            store.record_source_health(
                name=s["name"], url=s["url"], tier=s.get("tier"),
                lang=s.get("lang"), kind="html",
                markets_hint=",".join(s.get("markets", [])),
                status="html-no-feed", items=0, fetched_at=fetched_at)

    written = store.upsert_articles(rows)

    detail = (f"{written['inserted']} new / {written['updated']} updated from "
              f"{ok}/{ok + failed} feeds ok; {failed} failed; "
              f"taxonomy {taxonomy.VERSION}")
    database.set_provenance(PROVENANCE_SCOPE, database.VERIFIED_REAL, detail)

    return {
        "generated_at": fetched_at,
        "sources_ok": ok,
        "sources_failed": failed,
        "inserted": written["inserted"],
        "updated": written["updated"],
        "articles_seen": len(rows),
        "per_source": per_source,
        "total_stored": store.counts()["articles"],
    }


def reclassify():
    """Re-run classification over every stored article, without re-fetching.

    Used when the lexicon (taxonomy) changes: the markets/categories/tone/salience
    columns are recomputed in place from the stored title+summary. No network,
    and the article rows themselves (title, link, dates) are untouched.
    """
    store.ensure_schema()
    # source name -> focus, so a re-run scores wire vs econ outlets correctly
    focus_by_name = {s["name"]: s.get("focus", "econ")
                     for s in sources_mod.all_sources()}
    articles = store.load_articles(since_iso=None, limit=100000)
    rows = []
    for a in articles:
        hint = (a.get("markets") or "").split(",")
        cls = classify.classify(
            a["title"], a.get("summary", ""),
            tier=a.get("tier"), lang=a.get("lang"),
            markets_hint=[h for h in hint if h] or None,
            focus=focus_by_name.get(a["source"], "econ"))
        rows.append({
            "uid": a["uid"], "source": a["source"], "tier": a.get("tier"),
            "lang": a.get("lang"), "title": a["title"],
            "summary": a.get("summary", ""), "link": a.get("link", ""),
            "published_at": a.get("published_at"),
            "published_raw": a.get("published_raw", ""),
            "fetched_at": a.get("fetched_at"),
            "categories": cls["categories"], "markets": cls["markets"],
            "tone": cls["tone"], "salience": cls["salience"],
            "event_key": dedup.title_signature(a["title"]),
            "taxonomy_ver": taxonomy.VERSION,
            "is_economic": cls["is_economic"],
            "is_analysis": cls["is_analysis"],
            "is_price_forecast": cls["is_price_forecast"],
        })
    written = store.upsert_articles(rows)
    return {"reclassified": len(rows), **written}


def report(window_hours=72, top=20):
    """Read-only analytical report over stored articles."""
    return report_mod.build(window_hours=window_hours, top=top)
