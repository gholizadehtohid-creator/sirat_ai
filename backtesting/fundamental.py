"""Point-in-time fundamental scoring, with the trend the single-filing form loses.

WHAT THIS ADDS TO fundamental_scoring.py
----------------------------------------
fundamental_scoring.py scores ONE filing. That is the right shape for the live
panel, which wants "what does this company look like now", and it is the wrong
shape for a backtest, which is asking a question with a time axis in it. Two
companies with an identical newest filing are not the same investment when one
has widened its operating margin for four quarters and the other has halved it.

So this module scores a SERIES:

    a seventh pillar, TREND, over the multi-period history that was public on
    the decision date -- margins, returns, per-share earnings, cash conversion
    and leverage, each read for direction rather than level

    DEBT / EQUITY as a named ratio, alongside the debt-to-assets ratio the
    existing health pillar already uses. They are not the same statement: a
    company can carry modest debt against a large asset base and still be
    geared four times against its owners' money

    an AUDIT RECORD -- every input, every pillar, the filing each number came
    from and the session it became public -- so a score in a backtest report
    can be traced to the document behind it rather than believed

WHERE THE LOOK-AHEAD WOULD HAVE BEEN
------------------------------------
Nothing here reads a filing. It is handed a history that backtesting/pit.py has
already cut at the decision date, and a price from that date's bar. Two
specific traps are closed by construction rather than by care:

  VALUATION. P/E, P/S and P/B are recomputed from the decision date's price.
  The `pe` stored in codal_financials is None throughout this database, and
  that absence is lucky -- a stored multiple would carry today's price against
  a filing from two years ago.

  RESTATEMENT. When a period was filed twice, the history holds the release
  that was public on the decision date, not the one that superseded it later.
  That is pit.fundamental_history()'s job, and it is stated here because a
  reader of this file would otherwise have to assume it.

THE WEIGHTS ARE NOT TUNED
-------------------------
The six original weights are config.py's, unchanged. The trend weight is set
equal to the cash-flow pillar's and was not chosen by running backtests at
different values -- doing that would be fitting the scorer to the answer, which
is the one thing the specification forbids outright. It is swept in the
sensitivity analysis like every other parameter, so its influence is measured
rather than asserted.
"""

import math

import config
import fundamental_scoring


SCORED = fundamental_scoring.SCORED
INSUFFICIENT = fundamental_scoring.INSUFFICIENT

# Share of the weighted model that must run before the number is a judgement
# rather than an artefact of which pillars happened to be present.
MIN_COVERAGE = fundamental_scoring.FUND_MIN_COVERAGE

# Periods needed before a direction is a trend rather than a pair of points.
# Two points is a difference; three is the smallest series in which the middle
# point can disagree with the line through the ends.
MIN_TREND_PERIODS = 3

# Every metric below is period-length independent -- a margin, a ratio, or a
# figure financials.ratios() has already annualised -- so a 3-month filing and
# a 12-month one can sit in the same series. Mixing in a raw flow (revenue in
# rials) would show a company collapsing every time it filed an interim.
TREND_METRICS = (
    ("operating_margin", "higher", "operating margin"),
    ("net_margin", "higher", "net margin"),
    ("roe", "higher", "ROE"),
    ("eps_annual", "higher", "EPS"),
    ("ocf_margin", "higher", "cash margin"),
    ("debt_to_equity", "lower", "debt/equity"),
)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# DERIVED RATIOS
# ==============================

def debt_to_equity(items, ratios=None):
    """Total liabilities over owners' equity. None when equity is not positive.

    Negative equity makes the ratio meaningless rather than infinitely bad: a
    company whose liabilities exceed its assets produces a negative D/E that
    would sort ABOVE a conservatively financed one on any "lower is better"
    comparison. The health pillar handles insolvency through the debt-to-assets
    ratio, which stays well defined there.
    """

    items = items or {}

    liabilities = _finite(items.get("total_liabilities"))

    equity = (_finite(items.get("equity_owners"))
              or _finite(items.get("equity")))

    if liabilities is None or equity is None or equity <= 0:
        return None

    return round(liabilities / equity, 3)


def enrich(ratios, items):
    """The ratio set with the figures V2 names explicitly added."""

    out = dict(ratios or {})

    if out.get("debt_to_equity") is None:
        out["debt_to_equity"] = debt_to_equity(items, out)

    return out


# ==============================
# TREND
# ==============================

def _slope(values):
    """Least-squares slope over evenly spaced points, scaled by their level.

    Scaled because the pillar compares a margin in percent against a ratio
    around 1.0 and an EPS in rials: an unscaled slope would let whichever
    metric happens to have the largest units decide the whole direction.
    Division is by the mean ABSOLUTE value, so a series crossing zero does not
    produce an explosive number.
    """

    n = len(values)

    if n < 2:
        return None

    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n

    numerator = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(n))

    if denominator <= 0:
        return None

    slope = numerator / denominator

    scale = sum(abs(v) for v in values) / n

    if scale <= 1e-12:
        return None

    return slope / scale


# A slope smaller than this, per period, relative to the series' own level, is
# read as flat. Below it the "direction" is rounding in the filed figures.
FLAT_BAND = 0.02


def trend(history, ratios_by_record=None):
    """(fraction, notes, detail) over the multi-period history.

    `fraction` is 0-1 and is the share of measurable metrics moving the right
    way, with a flat series counting as half. None when fewer than
    MIN_TREND_PERIODS filings were public -- a company that has filed twice has
    no trend, and scoring it as though it did would rank young listings on
    noise.
    """

    history = list(history or [])

    if len(history) < MIN_TREND_PERIODS:
        return None, ["trend needs %d periods, %d were public"
                      % (MIN_TREND_PERIODS, len(history))], {}

    series = {}

    for index, record in enumerate(history):

        ratios = (ratios_by_record or {}).get(id(record))

        if ratios is None:
            ratios = enrich(record.get("ratios"), record.get("items"))

        for key, _direction, _label in TREND_METRICS:
            value = _finite(ratios.get(key))
            if value is not None:
                series.setdefault(key, []).append(value)

    parts = []
    notes = []
    detail = {}

    for key, direction, label in TREND_METRICS:

        values = series.get(key) or []

        if len(values) < MIN_TREND_PERIODS:
            detail[key] = {"periods": len(values), "slope": None,
                           "verdict": "insufficient"}
            continue

        slope = _slope(values)

        if slope is None:
            detail[key] = {"periods": len(values), "slope": None,
                           "verdict": "flat"}
            continue

        # "lower is better" metrics are read with the sign flipped, so every
        # entry in `parts` means the same thing: 1.0 is improvement.
        oriented = slope if direction == "higher" else -slope

        if oriented > FLAT_BAND:
            fraction, verdict = 1.0, "improving"
        elif oriented < -FLAT_BAND:
            fraction, verdict = 0.0, "deteriorating"
        else:
            fraction, verdict = 0.5, "flat"

        parts.append(fraction)

        detail[key] = {
            "periods": len(values),
            "first": round(values[0], 4),
            "last": round(values[-1], 4),
            "slope": round(slope, 4),
            "verdict": verdict,
        }

        notes.append("%s %s across %d periods (%.4g -> %.4g)"
                     % (label, verdict, len(values), values[0], values[-1]))

    if not parts:
        return None, (notes or ["no metric had enough periods to show a trend"]), detail

    return sum(parts) / len(parts), notes, detail


# ==============================
# HEALTH, WITH LEVERAGE NAMED
# ==============================

def _score_leverage(ratios):
    """Debt/equity as its own reading, on bands set against Iranian gearing.

    The thresholds are the debt-to-ASSETS bands in config.py translated into
    their debt-to-equity equivalents, so the two readings cannot contradict
    each other: assets = debt + equity, so a debt ratio d implies D/E of
    d / (1 - d). config.DEBT_SAFE = 40% therefore becomes 0.67, DEBT_FAIR =
    60% becomes 1.5, and DEBT_HIGH = 75% becomes 3.0. Nothing new was chosen
    here; one existing judgement is expressed in a second unit.
    """

    value = _finite(ratios.get("debt_to_equity"))

    if value is None:
        return None, ["debt/equity unavailable (needs positive equity)"]

    def implied(debt_pct):
        share = debt_pct / 100.0
        return share / (1 - share) if share < 1 else float("inf")

    safe = implied(config.DEBT_SAFE)
    fair = implied(config.DEBT_FAIR)
    high = implied(config.DEBT_HIGH)

    if value <= safe:
        return 1.0, ["debt/equity %.2f — conservatively financed" % value]
    if value <= fair:
        return 0.7, ["debt/equity %.2f — moderate gearing" % value]
    if value <= high:
        return 0.4, ["debt/equity %.2f — geared" % value]

    return 0.15, ["debt/equity %.2f — heavily geared" % value]


def _score_health_v2(ratios):
    """The existing health pillar, with leverage folded in as a third input."""

    base, notes = fundamental_scoring._score_health(ratios)

    leverage, leverage_notes = _score_leverage(ratios)

    notes = list(notes) + list(leverage_notes)

    parts = [p for p in (base, leverage) if p is not None]

    if not parts:
        return None, notes

    return sum(parts) / len(parts), notes


# ==============================
# THE MODEL
# ==============================

def pillars(trend_weight=None):
    """(label, weight, scorer) for the seven pillars, in report order.

    Built as a function rather than a module constant so a sweep can move the
    trend weight without mutating shared state -- the same reason
    backtest_config.BacktestConfig exists.
    """

    if trend_weight is None:
        trend_weight = config.FUND_WEIGHT_CASHFLOW

    return [
        ("Profitability", config.FUND_WEIGHT_PROFIT,
         fundamental_scoring._score_profitability),
        ("Growth", config.FUND_WEIGHT_GROWTH,
         fundamental_scoring._score_growth),
        ("Valuation", config.FUND_WEIGHT_VALUE,
         fundamental_scoring._score_valuation),
        ("Financial health", config.FUND_WEIGHT_HEALTH, _score_health_v2),
        ("Cash flow", config.FUND_WEIGHT_CASHFLOW,
         fundamental_scoring._score_cashflow),
        ("Earnings quality", config.FUND_WEIGHT_QUALITY,
         fundamental_scoring._score_quality),
        ("Trend", trend_weight, None),          # scored from the history
    ]


def evaluate(history, ratios, trend_weight=None, min_coverage=None,
             price=None, sector=None):
    """Score a point-in-time fundamental picture. Returns the audit record.

    `history` is the filings that were public on the decision date, oldest
    first, from pit.fundamental_history(). `ratios` is the NEWEST filing's
    ratio set with the valuation figures already recomputed at that date's
    price -- pit.market_ratios() does that, and doing it here would need this
    module to know about prices, which is how a look-ahead gets in.

    An empty history is not an error. It is the answer for most of this
    market's companies over most of this window, and it returns
    INSUFFICIENT_DATA with the reason attached.
    """

    min_coverage = MIN_COVERAGE if min_coverage is None else min_coverage

    history = list(history or [])

    if not history:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "status": None,
            "reason": "no filing was public on this date",
            "breakdown": {},
            "notes": [],
            "weight_covered": 0.0,
            "periods": 0,
            "trend": {},
            "audit": {"periods": 0},
        }

    newest = history[-1]

    ratios = enrich(ratios if ratios is not None else newest.get("ratios"),
                    newest.get("items"))

    trend_fraction, trend_notes, trend_detail = trend(history)

    breakdown = {}
    notes = []

    available = []
    total_weight = 0.0

    for label, weight, scorer in pillars(trend_weight):

        if scorer is None:
            fraction, pillar_notes = trend_fraction, trend_notes
        else:
            fraction, pillar_notes = scorer(ratios)

        notes.extend("%s: %s" % (label, note) for note in pillar_notes)

        if fraction is None:
            breakdown[label] = None
            continue

        available.append((label, weight, fraction))
        total_weight += weight

    model_weight = float(sum(w for _label, w, _fn in pillars(trend_weight)))

    covered = round(total_weight / model_weight, 2) if model_weight else 0.0

    audit = {
        "periods": len(history),
        "period_ends": [r.get("period_end") for r in history],
        "newest_period": newest.get("period_end"),
        "newest_period_months": newest.get("period_months"),
        "newest_tracing_no": newest.get("tracing_no"),
        "newest_published_at": newest.get("published_at"),
        "newest_visible_from": (str(newest.get("visible_from"))
                                if newest.get("visible_from") is not None
                                else None),
        "audited": newest.get("audited"),
        "consolidated": newest.get("consolidated"),
        "price_used": price,
        "sector": sector,
        "trend_detail": trend_detail,
        "inputs": {
            key: ratios.get(key)
            for key in ("revenue_growth", "eps_growth", "profit_growth",
                        "operating_margin", "net_margin", "roe", "roa",
                        "debt_ratio", "debt_to_equity", "current_ratio",
                        "pe", "ps", "pb", "operating_cash_flow", "ocf_margin",
                        "earnings_quality", "eps_annual", "shares")
        },
    }

    if not available or covered < min_coverage:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "status": None,
            "reason": ("only %d%% of the fundamental model could run "
                       "(need %d%%)" % (covered * 100, min_coverage * 100)),
            "breakdown": breakdown,
            "notes": notes,
            "weight_covered": covered,
            "periods": len(history),
            "trend": trend_detail,
            "audit": audit,
        }

    scale = 100.0 / total_weight

    total = 0.0

    for label, weight, fraction in available:
        points = weight * scale * fraction
        breakdown[label] = round(points, 1)
        total += points

    value = int(round(max(0.0, min(100.0, total))))

    return {
        "verdict": SCORED,
        "score": value,
        "status": fundamental_scoring.status_for(value),
        "reason": None,
        "breakdown": breakdown,
        "notes": notes,
        "weight_covered": covered,
        "periods": len(history),
        "trend": trend_detail,
        "audit": audit,
    }
