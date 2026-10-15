"""Rule-based classification: article text -> categories, markets, tone, flags.

Every output is traceable to a keyword. There is no model and no probability.

WHAT CHANGED, AND WHY (user feedback, 2026-08-23)
-------------------------------------------------
The first cut surfaced political headlines with no economic substance. The user
wanted economic ANALYSIS from financial outlets, not a political chronicle. So
classification now also decides:
  - is_economic: does the story carry an economic category or concrete economic
    vocabulary (rates, prices, inflation, exports, forecasts)? Pure geopolitics
    with no economic content is NOT economic and is pushed down.
  - is_analysis: does it read as interpretation / outlook rather than a bulletin?
  - salience: rebuilt to rank economic + analytical + economic-source content to
    the top, and to demote political-wire copy.

None of this is a signal. `tone` describes the news itself, `salience` is a
display-ranking weight, and any market lean shown in the report is labelled a
heuristic reading of news, never a buy/sell.
"""

import re

import persian
import event_intelligence.taxonomy as taxonomy


def _norm(text):
    return persian.normalize(text or "").lower()


# a per-UNIT price amount: "$100", "$4,000" -- but NOT a magnitude-suffixed sum
# ("$27M", "$163M", "$4B", "$40tn"). A commodity price forecast quotes a per-unit
# level (oil ~$50-150, gold ~$2,000-5,000); a corporate deal or fiscal figure
# quotes millions/billions/trillions. Excluding the suffixed form removes mining
# M&A, financing and policy-cost copy from the price-outlook path. A bare "$" or
# a "50%" never matches either.
# The (?![\d,\.]) guard forces the whole number to be consumed first, so the
# magnitude-suffix lookahead cannot be defeated by backtracking to a shorter
# digit run (e.g. matching "$2" out of "$27M").
_DOLLAR_AMOUNT = re.compile(
    r"\$\s?\d[\d,\.]*(?![\d,\.])"
    r"(?!\s?(?:m|mn|bn|b|k|tn|t|million|billion|trillion)\b)")


# pre-normalise the lexicons so keyword matching is fold-safe and case-safe
_LEXICON = {cat: [_norm(k) for k in kws]
            for cat, kws in taxonomy.LEXICON.items()}
_MARKET_TERMS = {m: [_norm(k) for k in kws]
                 for m, kws in taxonomy.MARKET_TERMS.items()}
_TONE_UP = [_norm(k) for k in taxonomy.TONE_UP]
_TONE_DOWN = [_norm(k) for k in taxonomy.TONE_DOWN]
_ECON_TERMS = [_norm(k) for k in taxonomy.ECON_TERMS]
_ANALYSIS_TERMS = [_norm(k) for k in taxonomy.ANALYSIS_TERMS]
_EXPLICIT_FC = [_norm(k) for k in taxonomy.EXPLICIT_FORECAST]
_PROJECTION_FC = [_norm(k) for k in taxonomy.PROJECTION_FORECAST]
_PRICE_TERMS = [_norm(k) for k in taxonomy.PRICE_TERMS]
_PROJECTION_PRICE = [_norm(k) for k in taxonomy.PROJECTION_PRICE_TERMS]
_OUTLOOK_MOVE = [_norm(k) for k in taxonomy.OUTLOOK_MOVE_TERMS]

# markets whose price forecasts the user wants surfaced first
_FORECAST_MARKETS = {"oil", "gold", "fx", "tse"}

_TIER_WEIGHT = {1: 1.0, 2: 0.85, 3: 0.65, 0: 0.7, None: 0.6}


def _hits(text, keywords):
    return [k for k in keywords if k and k in text]


def classify(title, summary="", tier=None, lang=None, markets_hint=None,
             focus="econ"):
    """Classify one article. Returns a dict of report fields (no side effects)."""

    text = _norm(f"{title} {summary}")

    # ---- categories -------------------------------------------------------
    categories = [cat for cat, kws in _LEXICON.items() if _hits(text, kws)]

    # ---- markets ----------------------------------------------------------
    direct = [m for m, kws in _MARKET_TERMS.items() if _hits(text, kws)]
    # A matched category contributes its market prior; a direct term adds on top.
    cat_markets = [m for cat in categories
                   for m in taxonomy.CATEGORIES.get(cat, [])]
    markets = []
    for m in direct + cat_markets:
        if m not in markets:
            markets.append(m)
    # No source-hint fallback: a story that names no market and matches no
    # market-specific category simply has no market. Assigning the source's hint
    # to every unmatched item (e.g. tagging all Guardian-Business copy "gold")
    # was exactly what polluted the market sections.

    # ---- economic vs political, and analysis vs bulletin ------------------
    econ_hits = _hits(text, _ECON_TERMS)
    has_econ_cat = any(c in taxonomy.ECONOMIC_CATEGORIES for c in categories)
    is_economic = bool(has_econ_cat or econ_hits)
    is_analysis = bool(_hits(text, _ANALYSIS_TERMS))

    # ---- tone (لحن خبر, not a forecast) -----------------------------------
    up = len(_hits(text, _TONE_UP))
    down = len(_hits(text, _TONE_DOWN))
    tone = "up" if up > down else "down" if down > up else "neutral"

    # ---- price forecast (the user's top priority) -------------------------
    # The market must be named DIRECTLY (a MARKET_TERMS hit), never inferred from
    # a category -- that inference is what mislabelled macro/tariff stories as
    # gold/dollar forecasts. Given a directly-named market, the story is a price
    # forecast if EITHER an explicit forecast phrase is present ("پیش‌بینی بورس",
    # "gold price forecast", "dollar outlook" -- self-sufficient, a direction
    # call needs no number), OR a generic projection phrase co-occurs with an
    # actual price/level word ("expected to reach $100"), which keeps "expected
    # to retaliate" out.
    explicit_fc = bool(_hits(text, _EXPLICIT_FC))
    projection_fc = bool(_hits(text, _PROJECTION_FC))
    projection_price = bool(_hits(text, _PROJECTION_PRICE))
    direct_forecast_market = any(m in _FORECAST_MARKETS for m in direct)

    # Foreign commodity price-outlook: names oil/gold directly + a real $amount +
    # a move-to-a-level verb. This is the path that lets foreign price commentary
    # ("Oil Nears $100") into the section without readmitting tariff/policy copy.
    commodity_direct = any(m in ("oil", "gold") for m in direct)
    outlook_move = commodity_direct and bool(_DOLLAR_AMOUNT.search(text)) \
        and bool(_hits(text, _OUTLOOK_MOVE))

    is_price_forecast = bool(
        direct_forecast_market
        and (explicit_fc or (projection_fc and projection_price)
             or outlook_move))

    # ---- salience (display ranking only) ----------------------------------
    # Economics and analysis rise; political-wire copy sinks. Every term below
    # traces to a keyword, so a ranking can always be explained.
    cat_w = max((taxonomy.CATEGORY_WEIGHT.get(c, 0.4) for c in categories),
                default=0.3)
    tier_w = _TIER_WEIGHT.get(tier, 0.6)
    focus_w = 1.0 if focus == "econ" else 0.55

    salience = (
        0.34 * cat_w
        + 0.12 * tier_w
        + 0.10 * (min(len(direct), 2) / 2.0)
        + 0.12 * (1.0 if is_economic else 0.0)
        + 0.10 * (1.0 if is_analysis else 0.0)
        + 0.08 * focus_w
        + 0.14 * (1.0 if is_price_forecast else 0.0)   # the priority lever
    )
    # A non-economic (political-only) story is halved, so it can still appear but
    # never leads over real economic analysis.
    if not is_economic:
        salience *= 0.5
    salience = round(min(1.0, max(0.0, salience)), 4)

    return {
        "categories": ",".join(categories),
        "markets": ",".join(markets),
        "tone": tone,
        "salience": salience,
        "is_economic": 1 if is_economic else 0,
        "is_analysis": 1 if is_analysis else 0,
        "is_price_forecast": 1 if is_price_forecast else 0,
    }
