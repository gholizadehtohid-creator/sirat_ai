"""Collapse near-duplicate articles into events.

The same story reaches us from many outlets (a wire item republished, a rate
decision covered by five papers). For the report we want ONE event with a
corroboration count -- "5 outlets" is itself information -- not five rows.

Two levels:
- title_signature(): a cheap, stable key stored per article (identical headlines
  collapse for free). Used as event_key at collect time.
- group_events(): report-time fuzzy clustering by token-Jaccard over normalised
  titles, which also merges paraphrases the signature misses. O(n^2) over a
  report window of a few hundred items -- negligible, and never on the hot path.

None of this is a signal; corroboration and recency only rank what to show.
"""

import hashlib
import re

import persian


_STOP = set("""
the a an of to in on for and or is are was were be as at by with from that this
it its into over after amid vs after new say says said report reports update
از با به در و را که این آن های یک برای تا هم روی طی درباره اعلام کرد گفت خبر
""".split())

_WORD_RE = re.compile(r"[0-9a-z؀-ۿ]+")


def _tokens(title):
    text = persian.normalize(title or "").lower()
    toks = _WORD_RE.findall(text)
    return [t for t in toks if len(t) >= 3 and t not in _STOP]


def title_signature(title):
    """Stable key for identical/near-identical headlines."""
    toks = sorted(set(_tokens(title)))
    if not toks:
        toks = [persian.normalize(title or "").lower()[:40]]
    raw = " ".join(toks).encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:16]


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _merge_tone(tones):
    up = tones.count("up")
    down = tones.count("down")
    if up > down:
        return "up"
    if down > up:
        return "down"
    return "neutral"


def group_events(articles, threshold=0.6):
    """Cluster article dicts into events. Returns events sorted by rank desc.

    `articles` are rows from store.load_articles (dicts). Clustering is greedy:
    each article joins the first existing cluster whose representative title is
    similar enough, else it starts a new one.
    """
    clusters = []          # each: {"tokens": set, "arts": [rows]}
    for art in articles:
        toks = set(_tokens(art.get("title", "")))
        placed = False
        for cl in clusters:
            if _jaccard(toks, cl["tokens"]) >= threshold:
                cl["arts"].append(art)
                cl["tokens"] |= toks
                placed = True
                break
        if not placed:
            clusters.append({"tokens": toks, "arts": [art]})

    events = []
    for cl in clusters:
        arts = cl["arts"]
        # representative: a price forecast wins, then highest salience, then most
        # recent -- so the headline shown for an event is its most analytical one.
        rep = max(arts, key=lambda a: (
            a.get("is_price_forecast") or 0,
            a.get("salience") or 0.0, a.get("published_at") or ""))
        sources = []
        for a in arts:
            if a.get("source") and a["source"] not in sources:
                sources.append(a["source"])
        markets, categories = [], []
        for a in arts:
            for m in (a.get("markets") or "").split(","):
                if m and m not in markets:
                    markets.append(m)
            for c in (a.get("categories") or "").split(","):
                if c and c not in categories:
                    categories.append(c)
        pubs = [a.get("published_at") for a in arts if a.get("published_at")]
        events.append({
            "key": title_signature(rep.get("title", "")),
            "title": rep.get("title", ""),
            "summary": rep.get("summary", ""),
            "link": rep.get("link", ""),
            "markets": markets,
            "categories": categories,
            "tone": _merge_tone([a.get("tone") for a in arts]),
            "salience": round(max((a.get("salience") or 0.0)
                                  for a in arts), 4),
            "published_at": max(pubs) if pubs else None,
            "sources": sources,
            "corroboration": len(sources),
            "count": len(arts),
            "is_economic": 1 if any(a.get("is_economic") for a in arts) else 0,
            "is_analysis": 1 if any(a.get("is_analysis") for a in arts) else 0,
            "is_price_forecast": 1 if any(
                a.get("is_price_forecast") for a in arts) else 0,
        })

    events.sort(key=lambda e: (
        e["salience"] * (1 + 0.15 * (e["corroboration"] - 1)),
        e["published_at"] or ""), reverse=True)
    return events
