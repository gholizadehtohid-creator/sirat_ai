"""The classification lexicon -- versioned, rule-based, no ML.

WHY A LEXICON AND NOT A MODEL
-----------------------------
The user's rule is that this layer is a report, not a signal, and the brief
forbids unexplainable classification. A keyword lexicon is fully auditable: every
label a story receives can be traced to the exact word that produced it. There is
no training set to leak, no black box to trust, and no accuracy claim to defend
statistically -- because we make no edge claim at all.

STRUCTURE
---------
- CATEGORIES: topic -> the markets it typically bears on. The mapping is a prior,
  not a verdict; classify.py still requires the article's own text to mention the
  topic before it assigns the market.
- LEXICON: category -> keyword list (Persian and English mixed). Keywords are
  matched against normalised text (persian.normalize folds Arabic/Persian forms
  and digits), so 'ی' vs 'ي' never causes a miss.
- TONE_UP / TONE_DOWN: words describing the DIRECTION OF THE NEWS ITSELF (a price
  rose, a deal collapsed). This is the article's framing -- reported as لحن (tone),
  explicitly NOT a market forecast and never fed to any strategy.
- MARKET_TERMS: words that tie a story directly to gold / oil / the Tehran market
  even when the topic category is generic.

VERSIONING
----------
Bump VERSION on any change to keywords or mappings. Stored on every classified
row (taxonomy_ver) so a report can tell which rows predate a lexicon change and
a reclassify pass knows what is stale.
"""

VERSION = "1.1"


# topic category -> markets it typically affects.
# Deliberately CONSERVATIVE (v1.1): the first cut mapped every macro/monetary/fx
# story onto gold+tse+fx, which flooded every market section with the same
# generic pieces and made the forecast list fire on stories that never named a
# price. A category now contributes a market ONLY where the link is direct and
# defensible; a broad topic (geopolitics, inflation_macro) contributes NOTHING on
# its own and must earn a market by naming it (MARKET_TERMS). The one retained
# prior is monetary_policy -> gold/fx, because Fed/rate news bears on bullion and
# the dollar even when it does not print the word "gold".
CATEGORIES = {
    "monetary_policy": ["gold", "fx"],    # Fed/rates ↔ bullion + dollar (kept)
    "geopolitics":     [],                # too broad; needs a direct market term
    "sanctions_iran":  ["tse"],           # sanctions ↔ Tehran market
    "oil_energy":      ["oil"],
    "gold_metals":     ["gold"],
    "fx_currency":     ["fx"],
    "inflation_macro": [],                # too broad; needs a direct market term
    "tse_market":      ["tse"],
    "nuclear_talks":   ["tse"],           # JCPOA ↔ Tehran market
}


# category -> keywords (fa + en), matched against normalised text
LEXICON = {
    "monetary_policy": [
        "نرخ بهره", "بهره", "فدرال رزرو", "بانک مرکزی", "سیاست پولی",
        "کاهش نرخ", "افزایش نرخ", "نقدینگی",
        "federal reserve", "the fed", "interest rate", "rate cut",
        "rate hike", "monetary policy", "fomc", "powell", "central bank",
        "jackson hole", "basis points", "quantitative",
    ],
    "geopolitics": [
        "جنگ", "حمله", "درگیری", "تنش", "بحران", "موشک", "پهپاد",
        "war", "attack", "conflict", "tension", "strike", "military",
        "missile", "drone", "ceasefire", "invasion", "escalation",
    ],
    # Sanctions specifically -- NOT a bare "Iran" mention. A generic Iran story
    # (a football result, a regional-politics wire item) should not be forced
    # into the Tehran-market section; only actual sanction language maps to tse.
    "sanctions_iran": [
        "تحریم", "تحریم‌ها", "اسنپ‌بک", "snapback",
        "sanction", "sanctions", "embargo", "sanctioned",
    ],
    "oil_energy": [
        "نفت", "بشکه", "اوپک", "اوپک پلاس", "گاز", "انرژی", "پالایش",
        "میعانات", "برنت", "وست تگزاس",
        "oil", "crude", "opec", "brent", "wti", "barrel", "gas",
        "energy", "petroleum", "refinery", "pipeline", "eia",
    ],
    "gold_metals": [
        "طلا", "سکه", "انس", "انس طلا", "فلزات گران‌بها", "شمش",
        "gold", "bullion", "ounce", "precious metal", "silver",
        "xau", "safe haven",
    ],
    "fx_currency": [
        "دلار", "نرخ ارز", "ریال", "تومان", "بازار ارز", "نیما", "صرافی",
        "dollar", "forex", "exchange rate", "greenback", "dollar index",
    ],
    "inflation_macro": [
        "تورم", "رشد اقتصادی", "رکود", "بیکاری", "تولید ناخالص",
        "شاخص قیمت", "بودجه", "کسری",
        "inflation", "recession", "gdp", "unemployment", "cpi",
        "deficit", "growth", "economy", "economic",
    ],
    "tse_market": [
        "بورس", "شاخص کل", "شاخص", "سهام", "بازار سرمایه", "معاملات",
        "حقوقی", "حقیقی", "صف خرید", "صف فروش", "نماد", "عرضه اولیه",
        "tse", "tehran stock", "equity market",
    ],
    "nuclear_talks": [
        "مذاکرات", "مذاکره", "هسته‌ای", "برجام", "توافق", "وین",
        "nuclear", "jcpoa", "talks", "negotiation", "vienna", "deal",
        "enrichment", "iaea",
    ],
}


# Which categories are ECONOMIC. The user's feedback: the panel was surfacing
# political headlines with no economic substance. Economic categories are ranked
# far above the purely political ones (geopolitics / sanctions / nuclear talks),
# which are kept but demoted -- a report on markets should lead with rates,
# prices and inflation, not with who sanctioned whom.
ECONOMIC_CATEGORIES = {
    "monetary_policy", "oil_energy", "gold_metals",
    "fx_currency", "inflation_macro", "tse_market",
}

# per-category rank weight (economics high, politics low)
CATEGORY_WEIGHT = {
    "gold_metals": 1.0,
    "oil_energy": 1.0,
    "monetary_policy": 1.0,
    "inflation_macro": 0.95,
    "fx_currency": 0.9,
    "tse_market": 0.9,
    "nuclear_talks": 0.55,
    "sanctions_iran": 0.55,
    "geopolitics": 0.4,
}

# concrete economic / market vocabulary: numbers, prices, rates, forecasts.
# A story that carries these is economic even if its topic word is political
# (e.g. "sanctions cut oil EXPORTS 20%"). Forecast words are included because the
# user asked to see a forward-looking read, not just a chronicle.
ECON_TERMS = [
    "نرخ", "بهره", "تورم", "قیمت", "درصد", "رشد", "رکود", "بازده", "بازدهی",
    "عرضه", "تقاضا", "صادرات", "واردات", "تولید", "بشکه", "انس", "دلار",
    "بودجه", "کسری", "نقدینگی", "سود", "زیان", "معامله", "معاملات", "شاخص",
    "پیش‌بینی", "پیش بینی", "چشم‌انداز", "چشم انداز", "برآورد", "انتظار",
    "price", "prices", "rate", "rates", "yield", "inflation", "percent",
    "%", "supply", "demand", "output", "exports", "imports", "barrel",
    "ounce", "dollar", "gdp", "growth", "deficit", "budget", "earnings",
    "forecast", "outlook", "expected", "projected", "estimate", "guidance",
]

# markers of ANALYSIS / outlook writing (as opposed to a plain news bulletin).
# The user asked for analytical reports, not a political chronicle -- an article
# carrying these reads as interpretation or a forward look.
# v1.1: dropped the noisiest generics ("why", "could", "view", "here's") that
# tagged ordinary bulletins as analysis. Kept markers that genuinely signal
# interpretation or a forward look.
ANALYSIS_TERMS = [
    "تحلیل", "تحلیلی", "چشم‌انداز", "چشم انداز", "پیش‌بینی", "پیش بینی",
    "برآورد", "انتظار می‌رود", "انتظار می رود", "پیامد", "تأثیر", "تاثیر",
    "گزارش تحلیلی", "یادداشت", "دیدگاه", "دلایل",
    "analysis", "outlook", "forecast", "projected", "explainer",
    "what it means", "implications", "commentary", "deep dive", "explained",
    "why is", "why the", "here's why", "here is why",
]

# words that tie a story to a specific market regardless of topic.
# `fx` (dollar / currency) is a first-class market: the user explicitly wants
# dollar price forecasts alongside gold, oil and stocks.
MARKET_TERMS = {
    "gold": ["طلا", "سکه", "انس", "gold", "bullion", "ounce", "xau"],
    "oil":  ["نفت", "بشکه", "اوپک", "برنت", "oil", "crude", "opec",
             "brent", "wti", "barrel"],
    # NB: bare "euro" (matches "europe/european"), "currency" (matches
    # "cryptocurrency"), and Persian "ارز" (matches "ارزش" = value) were removed
    # -- each was a large false-positive source for the dollar section.
    "fx":   ["دلار", "نرخ ارز", "بازار ارز", "ریال", "تومان",
             "dollar", "greenback", "forex", "dollar index"],
    # Tehran market specifically -- NOT a bare "Iran" mention (that flooded the
    # section with foreign-policy wire copy). Iran relevance reaches tse through
    # the sanctions_iran / nuclear_talks categories, which require real context.
    "tse":  ["بورس", "شاخص", "سهام", "بازار سرمایه", "بورس تهران",
             "tehran stock", "tehran bourse", "tse"],
}

# ---- price-forecast detection --------------------------------------------
# The user's priority: bring PRICE-FORECAST stories for oil, gold, dollar and
# stocks to the top. A story qualifies when it carries a forward-looking word
# AND a price/level word AND names one of those markets. All keyword-based, so
# the "why this is a forecast" is always explainable -- and it is a report of
# what analysts forecast, NEVER our own signal.
# v1.1: two tiers, because a single loose list mislabelled policy/tariff copy as
# price forecasts. is_price_forecast (classify.py) also requires the story to
# DIRECTLY name a market (oil/gold/dollar/stocks), and then:
#
#   EXPLICIT_FORECAST  -- self-sufficient. "پیش‌بینی بورس", "gold price forecast",
#       "dollar outlook", "price target": the phrase itself declares a forecast,
#       so no number is required (a market-direction call is still a forecast).
#   PROJECTION_FORECAST -- forward-looking but generic ("expected to", "could
#       reach", "to hit"). These require a price/level word (PRICE_TERMS) too,
#       so "expected to retaliate" is not a price forecast but "expected to reach
#       $100" is.
# EXPLICIT English is market-ANCHORED ("dollar outlook", not bare "outlook") --
# bare "outlook"/"outlook:" appears in ordinary financial copy and was tagging
# tariff/policy stories as forecasts. Persian "پیش‌بینی"/"چشم‌انداز" are reliable
# forecast words in the financial press and are kept bare.
EXPLICIT_FORECAST = [
    "پیش‌بینی", "پیش بینی", "چشم‌انداز", "چشم انداز", "دورنما", "هدف قیمتی",
    "forecast", "price forecast", "price target", "year-end target",
    "gold outlook", "oil outlook", "crude outlook", "dollar outlook",
    "price outlook", "market outlook", "stocks outlook", "bullion outlook",
    "gold forecast", "oil forecast", "dollar forecast",
]
PROJECTION_FORECAST = [
    # Persian -- future/expectation verbs (NOT the bare "می‌رسد", which is the
    # everyday "arrives/comes to" and fired on "به بهره‌برداری می‌رسد").
    "انتظار می‌رود", "انتظار می رود", "خواهد رسید",
    # English -- forward-looking phrasing. Substring-safe: "reaching $" (not bare
    # "reaching", which matches "far-reaching"); multiword move phrases only.
    "expected to", "projected to", "poised to", "set to reach", "on track to",
    "could reach", "could hit", "could climb", "could rise", "could fall",
    "will rise to", "will fall to", "to reach", "to hit", "reaching $",
    "climbing to", "rising to", "falling to", "predicted to", "forecast to",
]
# kept as the union for any caller wanting "is this forward-looking at all"
FORECAST_TERMS = EXPLICIT_FORECAST + PROJECTION_FORECAST

# The projection path additionally requires a hard PRICE UNIT -- an unambiguous
# money-per-unit token, not a bare "$" (which matched "$40tn cost of...") nor a
# "%" (a tariff/rate). This separates "expected to reach $100 a barrel" from
# "expected to retaliate with 50% tariffs".
PROJECTION_PRICE_TERMS = [
    "per barrel", "per ounce", "a barrel", "an ounce",
    "بشکه", "انس", "تومان", "ریال", "هدف قیمتی", "price target",
]

# ---- foreign commodity price-outlook path --------------------------------
# The user wants PRICE OUTLOOK from FOREIGN sites too, but those outlets phrase
# it as "Oil Nears $100", "Gold could climb to $4,000" -- price commentary, not
# the word "forecast". classify.py admits such a story ONLY when it (a) directly
# names a COMMODITY market (oil/gold -- excludes the dollar-idiom and tariff
# false positives), (b) carries a real "$<number>" amount (a regex, so "$40tn
# cost" of a policy piece is caught but "50% tariffs" is not -- and a bare "$"
# never qualifies), and (c) uses a move-toward-a-level verb below.
OUTLOOK_MOVE_TERMS = [
    "nears", "tops", "eyes", "targets", "hits", "surpass", "surpasses",
    "breach", "breaches", "climbs to", "jumps to", "to hit", "to reach",
    "could reach", "could hit", "could climb", "rally to", "rise to",
    "rises to", "fall to", "falls to", "drops to", "slides to", "soars to",
    "surges to", "heads to", "headed for", "on pace for", "set for",
    "poised to", "expected to", "seen at", "seen reaching",
]
PRICE_TERMS = [
    "قیمت", "نرخ", "انس", "بشکه", "دلار", "سکه", "هدف", "سطح", "درصد",
    "price", "prices", "level", "$", "per barrel", "per ounce",
    "a barrel", "an ounce", "target", "percent", "%",
]

# markers of ANALYSIS / outlook writing (as opposed to a plain news bulletin).


# tone (لحن خبر) -- the direction the NEWS describes, not a market forecast
TONE_UP = [
    "افزایش", "رشد", "جهش", "صعود", "بهبود", "رکورد", "اوج", "توافق", "تقویت",
    "gain", "gains", "rise", "rises", "rose", "surge", "surges", "jump",
    "jumps", "rally", "climb", "climbs", "up", "higher", "record high",
    "boost", "recovery", "soar", "soars", "deal", "agreement",
    "strengthen", "strengthens", "strengthened",
]
TONE_DOWN = [
    "کاهش", "افت", "سقوط", "ریزش", "بحران", "شکست", "تنش", "رکود",
    "تحریم", "نگرانی", "زیان", "تضعیف",
    "fall", "falls", "fell", "drop", "drops", "plunge", "plunges",
    "slump", "decline", "declines", "down", "lower", "loss", "losses",
    "crash", "tumble", "tumbles", "collapse", "collapses", "fears",
    "crisis", "recession", "sanction", "sanctions", "war", "conflict",
    "weaken", "weakens", "weakened",
]
