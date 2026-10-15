"""Turn stored articles into the analytical report the dashboard shows.

Pure read layer: no network, no writes, no decision output.

SHAPED BY USER FEEDBACK (2026-08-23)
------------------------------------
- PRICE FORECASTS FIRST. Stories that forecast the price of oil, gold, the
  dollar or stocks lead the report, in their own section. That was the explicit
  ask ("اخبار پیش‌بینی قیمت ... را در اول بیاور").
- ECONOMIC ANALYSIS OVER POLITICS. Political-only items are demoted at scoring
  time and the market sections lead with economic content.
- AN ACTUAL READ, NOT JUST HEADLINES. Each market carries an "outlook" -- a
  synthesis of the tone and drivers of its economic news. It is labelled a
  heuristic reading of the news, explicitly NOT a trading signal.
"""

from datetime import datetime, timedelta, timezone

from event_intelligence import store, dedup
from event_intelligence import sources as sources_mod


# markets, in the order the user named them (نفت، طلا، دلار، سهام)
MARKET_ORDER = ["oil", "gold", "fx", "tse"]
MARKET_LABELS = {"gold": "طلا", "oil": "نفت", "fx": "دلار / ارز", "tse": "بورس"}
TONE_LABELS = {"up": "مثبت", "down": "منفی", "neutral": "خنثی"}
LEAN_LABELS = {
    "supportive": "برآیند خبری مثبت / حمایتی",
    "pressure": "برآیند خبری منفی / فشار",
    "balanced": "برآیند خبری متعادل",
    "insufficient": "داده‌ی کافی نیست",
}
CATEGORY_LABELS = {
    "monetary_policy": "سیاست پولی",
    "geopolitics": "ژئوپلیتیک",
    "sanctions_iran": "تحریم",
    "oil_energy": "نفت و انرژی",
    "gold_metals": "طلا و فلزات",
    "fx_currency": "ارز",
    "inflation_macro": "تورم و اقتصاد کلان",
    "tse_market": "بازار سرمایه",
    "nuclear_talks": "مذاکرات هسته‌ای",
}

DISCLAIMER = (
    "این پنجره فقط گزارش و تحلیل خبری است و هیچ سیگنال خرید یا فروشی تولید "
    "نمی‌کند. «چشم‌انداز» هر بازار صرفاً برآیند لحن اخبار است، نه پیش‌بینی ما "
    "و نه توصیه‌ی معاملاتی. اخبار به موتور تصمیم‌گیری متصل نیست."
)


def _now():
    return datetime.now(timezone.utc)


def _tone_counts(events):
    c = {"up": 0, "down": 0, "neutral": 0}
    for e in events:
        t = e.get("tone", "neutral")
        c[t] = c.get(t, 0) + 1
    return c


def _event_view(e):
    return {
        "title": e["title"],
        "summary": e.get("summary", ""),
        "link": e.get("link", ""),
        "markets": e.get("markets", []),
        "market_labels": [MARKET_LABELS.get(m, m) for m in e.get("markets", [])],
        "categories": e.get("categories", []),
        "category_labels": [CATEGORY_LABELS.get(c, c)
                            for c in e.get("categories", [])],
        "tone": e.get("tone", "neutral"),
        "tone_label": TONE_LABELS.get(e.get("tone", "neutral"), ""),
        "published_at": e.get("published_at"),
        "sources": e.get("sources", []),
        "corroboration": e.get("corroboration", 1),
        "salience": e.get("salience", 0.0),
        "is_economic": e.get("is_economic", 0),
        "is_analysis": e.get("is_analysis", 0),
        "is_price_forecast": e.get("is_price_forecast", 0),
    }


def _outlook(events):
    """A heuristic read of a market's economic news -- NOT a signal.

    Summarises tone (how many stories lean positive vs negative) and the drivers
    (top categories). The lean is a description of the news balance, deliberately
    coarse, and always labelled as such in the UI.
    """
    econ = [e for e in events if e.get("is_economic")]
    base = econ or events
    up = sum(1 for e in base if e.get("tone") == "up")
    down = sum(1 for e in base if e.get("tone") == "down")
    total = len(base)

    if total < 3:
        lean = "insufficient"
    else:
        margin = max(2, round(0.2 * total))
        if up - down >= margin:
            lean = "supportive"
        elif down - up >= margin:
            lean = "pressure"
        else:
            lean = "balanced"

    cats = {}
    for e in base:
        for c in e.get("categories", []):
            cats[c] = cats.get(c, 0) + 1
    drivers = [CATEGORY_LABELS.get(c, c) for c, _ in
               sorted(cats.items(), key=lambda kv: kv[1], reverse=True)[:3]]

    return {
        "lean": lean,
        "lean_label": LEAN_LABELS[lean],
        "up": up, "down": down, "total": total,
        "drivers": drivers,
    }


def _market_section(events, market, top=12):
    picked = [e for e in events if market in (e.get("markets") or [])]
    econ_first = sorted(
        picked,
        key=lambda e: (e.get("is_price_forecast", 0), e.get("is_economic", 0),
                       e.get("salience", 0.0)),
        reverse=True)
    return {
        "market": market,
        "label": MARKET_LABELS.get(market, market),
        "events": [_event_view(e) for e in econ_first[:top]],
        "total": len(picked),
        "economic": sum(1 for e in picked if e.get("is_economic")),
        "tone": _tone_counts(picked),
        "outlook": _outlook(picked),
    }


def build(window_hours=72, top=20):
    """Assemble the report dict. `window_hours=None` uses all stored articles."""

    since = None
    if window_hours:
        since = (_now() - timedelta(hours=window_hours)).isoformat(
            timespec="seconds")

    articles = store.load_articles(since_iso=since, limit=4000)
    events = dedup.group_events(articles)

    # ---- price forecasts, first (the user's priority) ---------------------
    forecasts = [e for e in events if e.get("is_price_forecast")]
    forecasts.sort(key=lambda e: (e.get("salience", 0.0),
                                  e.get("published_at") or ""), reverse=True)
    price_forecasts = [_event_view(e) for e in forecasts[:top]]

    # economic events lead the "top events" list; political ones fall below
    econ_events = sorted(
        events,
        key=lambda e: (e.get("is_price_forecast", 0), e.get("is_economic", 0),
                       e.get("salience", 0.0)),
        reverse=True)

    cat_tally = {}
    for e in events:
        for c in e.get("categories", []):
            cat_tally[c] = cat_tally.get(c, 0) + 1
    themes = sorted(cat_tally.items(), key=lambda kv: kv[1], reverse=True)

    pubs = [a["published_at"] for a in articles if a.get("published_at")]

    health = store.source_health()
    ok = [h for h in health if (h.get("last_status") == "ok")]
    html_only = [h for h in health if h.get("last_status") == "html-no-feed"]
    failed = [h for h in health if h.get("last_status")
              and h["last_status"] not in ("ok", "html-no-feed")]

    n_econ = sum(1 for e in events if e.get("is_economic"))

    return {
        "generated_at": _now().isoformat(timespec="seconds"),
        "window_hours": window_hours,
        "disclaimer": DISCLAIMER,
        "overview": {
            "articles": len(articles),
            "events": len(events),
            "economic": n_econ,
            "political": len(events) - n_econ,
            "analysis": sum(1 for e in events if e.get("is_analysis")),
            "forecasts": len(forecasts),
            "undated": sum(1 for a in articles if not a.get("published_at")),
            "time_range": {"from": min(pubs) if pubs else None,
                           "to": max(pubs) if pubs else None},
            "tone": _tone_counts([e for e in events if e.get("is_economic")]),
            "by_market": {m: sum(1 for e in events
                                 if m in (e.get("markets") or []))
                          for m in MARKET_ORDER},
            "themes": [{"key": k, "label": CATEGORY_LABELS.get(k, k),
                        "count": n} for k, n in themes[:8]],
        },
        "price_forecasts": price_forecasts,
        "top_events": [_event_view(e) for e in econ_events[:top]],
        "markets": {m: _market_section(events, m) for m in MARKET_ORDER},
        "market_order": MARKET_ORDER,
        "sources": {
            "ok": [{"name": h["name"], "tier": h["tier"],
                    "items": h["last_items"],
                    "fetched_at": h["last_fetched_at"]} for h in ok],
            "failed": [{"name": h["name"], "status": h["last_status"]}
                       for h in failed],
            "html_only": [{"name": h["name"], "url": h["url"]}
                          for h in html_only],
            "unavailable": [{"name": n, "url": u, "reason": r}
                            for (n, u, r) in sources_mod.UNAVAILABLE],
        },
    }
