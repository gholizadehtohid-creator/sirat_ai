"""The source registry -- every feed here was reachability-tested, not assumed.

Each SOURCES entry was probed from this machine on 2026-08-23 and returned a
valid RSS/XML body (or, for بانک مرکزی, reachable HTML with no RSS). Sources
that answered 401/403/404 or refused the connection are in UNAVAILABLE with the
observed reason, so the gap is on record rather than silently missing -- the
project's "absence is not approval" rule.

Tiers follow the brief's credibility ladder:
    1  official / primary   (central bank, EIA, Federal Reserve)
    2  major wire / paper    (Bloomberg, FT, AP, BBC, CNBC, Al Jazeera, ...)
    3  analytical / sector   (OilPrice, Mining.com, MarketWatch, Yahoo)
    0  Iranian economic press (ISNA, Mehr, دنیای اقتصاد, اقتصادآنلاین)

`bind` marks an Iranian host that must exit the physical NIC (the VPN tunnel is
geo-blocked by Iranian sites); everything else takes the default route.
`markets` is a HINT for classification, never a verdict -- the classifier reads
each article's own text. `kind` is 'rss' (parse as a feed) or 'html' (reachable
but no feed; collected only if an HTML extractor is added).
"""


def _s(name, url, tier, bind, lang, markets, kind="rss", focus="econ"):
    # focus: "econ"  -> an economic / financial-analysis outlet (what the user
    #                   asked for: تحلیل اقتصادی, not political wire copy)
    #        "wire"  -> a general/political news wire, kept but demoted; only its
    #                   economically-substantive items surface in the report.
    return {"name": name, "url": url, "tier": tier, "bind": bind,
            "lang": lang, "markets": markets, "kind": kind, "focus": focus}


# ---- reachable sources (RSS confirmed unless kind='html') -----------------
SOURCES = [
    # Central bank -- reachable but publishes no RSS, so HTML-only for now.
    _s("بانک مرکزی", "https://www.cbi.ir/", 1, True, "fa", ["tse", "gold"],
       kind="html"),

    # Iranian economic press (tier 0).
    _s("دنیای اقتصاد", "https://donya-e-eqtesad.com/rss", 0, True, "fa",
       ["tse", "gold", "oil"]),
    _s("اقتصادآنلاین", "https://www.eghtesadonline.com/rss", 0, True, "fa",
       ["tse", "gold", "oil"]),
    _s("ایسنا", "https://www.isna.ir/rss", 0, True, "fa", ["tse"],
       focus="wire"),
    _s("مهر اقتصادی", "https://www.mehrnews.com/rss/tp/26", 0, True, "fa",
       ["tse"]),

    # Official / primary (tier 1).
    _s("EIA Today in Energy", "https://www.eia.gov/rss/todayinenergy.xml", 1,
       False, "en", ["oil"]),
    _s("EIA Press", "https://www.eia.gov/rss/press_rss.xml", 1, False, "en",
       ["oil"]),
    _s("Federal Reserve",
       "https://www.federalreserve.gov/feeds/press_all.xml", 1, False, "en",
       ["gold"]),

    # Major wire / paper (tier 2).
    _s("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss", 2,
       False, "en", ["gold", "oil", "tse"]),
    _s("Bloomberg Economics",
       "https://feeds.bloomberg.com/economics/news.rss", 2, False, "en",
       ["gold"]),
    _s("Bloomberg Politics",
       "https://feeds.bloomberg.com/politics/news.rss", 2, False, "en",
       ["gold", "oil"], focus="wire"),
    _s("Financial Times", "https://www.ft.com/rss/home", 2, False, "en",
       ["gold", "oil"]),
    _s("FT World", "https://www.ft.com/world?format=rss", 2, False, "en",
       ["gold", "oil"]),
    _s("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", 2,
       False, "en", ["gold", "oil"]),
    _s("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", 2,
       False, "en", ["gold", "oil"]),
    _s("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html",
       2, False, "en", ["gold"]),
    _s("AP", "https://feedx.net/rss/ap.xml", 2, False, "en", ["gold", "oil"],
       focus="wire"),
    _s("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", 2, False,
       "en", ["oil", "gold"], focus="wire"),
    _s("Guardian Business", "https://www.theguardian.com/business/rss", 2,
       False, "en", ["gold", "oil"]),
    _s("NYT Economy",
       "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", 2, False,
       "en", ["gold"]),

    # Analytical / sector (tier 3).
    _s("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", 3, False,
       "en", ["gold", "oil"]),
    _s("MarketWatch",
       "https://feeds.content.dowjones.io/public/rss/mw_topstories", 3, False,
       "en", ["gold", "oil"]),
    _s("OilPrice", "https://oilprice.com/rss/main", 3, False, "en", ["oil"]),
    _s("Mining.com", "https://www.mining.com/feed/", 3, False, "en",
       ["gold", "oil"]),
]

# ---- probed but NOT usable (recorded, not faked) --------------------------
UNAVAILABLE = [
    ("Reuters (direct)", "https://www.reuters.com/", "401 subscription wall"),
    ("Reuters feeds", "https://feeds.reuters.com/", "connection refused"),
    ("Investing.com", "https://www.investing.com/", "403 anti-bot"),
    ("Kitco", "https://www.kitco.com/rss/", "404 -- no standard RSS feed"),
    ("OPEC", "https://www.opec.org/opec_web/en/rss/rss.xml", "403"),
    ("IMF", "https://www.imf.org/en/News/RSS", "403"),
    ("World Bank", "https://www.worldbank.org/en/news/rss", "404"),
    ("TradingEconomics", "https://tradingeconomics.com/rss/news.aspx", "403"),
    ("Bloomberg (site)", "https://www.bloomberg.com/", "anti-bot on the site; "
     "the feeds.bloomberg.com RSS endpoints ARE reachable and are used above"),
    ("cbi.ir RSS", "https://www.cbi.ir/rss.aspx", "reachable but no RSS items; "
     "بانک مرکزی is carried as an HTML source"),
]


def rss_sources():
    """Reachable sources that are real RSS feeds."""
    return [s for s in SOURCES if s["kind"] == "rss"]


def all_sources():
    return list(SOURCES)
