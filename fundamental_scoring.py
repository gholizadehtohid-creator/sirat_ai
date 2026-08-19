"""Filed financials -> a 0-100 fundamental score.

Six pillars, weighted in config.py and renormalised over whichever ones could
actually be computed. That renormalisation is the whole reason this is not a
simple average: Iranian filing templates differ by industry, so a bank has no
gross margin, an investment company's revenue is not comparable to a
manufacturer's, and roughly a third of any company's pillars are routinely
unavailable. Scoring the missing ones as zero would rank a bank below a
loss-making manufacturer for the crime of being a bank.

`weight_covered` reports how much of the model ran, and below
FUND_MIN_COVERAGE the verdict is INSUFFICIENT rather than a number -- the same
contract screening.py uses, for the same reason.

Two judgements encoded here that a generic scorer would get wrong on this
market:

  NOMINAL GROWTH IS NOT GROWTH. With ~40% inflation, a company growing revenue
  25% is shrinking in real terms. The growth bands sit on the inflation rate,
  not on zero, so "grew a bit" scores as the contraction it is.

  EQUITY MUST BEAT CASH. At a ~23% policy rate, an ROE of 15% means the owners
  would be better off in a deposit. ROE_FAIR is the risk-free rate itself
  rather than some textbook 10%.
"""

import config


# Share of the model that must run before a score is a judgement rather than
# an artefact of which pillars happened to be present.
FUND_MIN_COVERAGE = 0.40

SCORED = "SCORED"
INSUFFICIENT = "INSUFFICIENT_DATA"


def _value(x):
    if x is None:
        return None
    try:
        number = float(x)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _band(value, bands):
    """First (fraction, note) whose threshold `value` clears. Bands descend."""

    for threshold, fraction, note in bands:
        if value >= threshold:
            return fraction, note(value)

    threshold, fraction, note = bands[-1]

    return fraction, note(value)


def _average(parts):
    """Mean of the sub-scores that exist, or None when none do.

    Several pillars are built from two or three metrics and any of them can be
    absent. Averaging only what is present keeps a pillar alive on partial
    input instead of dropping the whole thing because one input was missing.
    """

    usable = [p for p in parts if p is not None]

    if not usable:
        return None

    return sum(usable) / len(usable)


# ==============================
# PILLARS
# ==============================

def _score_profitability(r):
    """ROE, ROA and net margin -- is the business actually making money."""

    notes = []
    parts = []

    roe = _value(r.get("roe"))
    if roe is not None:
        fraction, note = _band(roe, [
            (config.ROE_EXCELLENT, 1.0, lambda v: f"ROE {v:.0f}% — outstanding"),
            (config.ROE_GOOD, 0.85, lambda v: f"ROE {v:.0f}% — strong"),
            (config.ROE_FAIR, 0.6,
             lambda v: f"ROE {v:.0f}% — beats the {config.RISK_FREE_RATE*100:.0f}% rate"),
            (config.ROE_WEAK, 0.3,
             lambda v: f"ROE {v:.0f}% — below the risk-free rate"),
            (0.0, 0.1, lambda v: f"ROE {v:.0f}% — barely positive"),
            (float("-inf"), 0.0, lambda v: f"ROE {v:.0f}% — equity is losing money"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("ROE unavailable")

    roa = _value(r.get("roa"))
    if roa is not None:
        fraction, note = _band(roa, [
            (config.ROA_EXCELLENT, 1.0, lambda v: f"ROA {v:.0f}% — outstanding"),
            (config.ROA_GOOD, 0.85, lambda v: f"ROA {v:.0f}% — strong"),
            (config.ROA_FAIR, 0.6, lambda v: f"ROA {v:.0f}% — solid"),
            (config.ROA_WEAK, 0.3, lambda v: f"ROA {v:.0f}% — thin"),
            (float("-inf"), 0.0, lambda v: f"ROA {v:.0f}% — assets are losing money"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("ROA unavailable")

    margin = _value(r.get("net_margin"))
    if margin is not None:
        fraction, note = _band(margin, [
            (config.NET_MARGIN_EXCELLENT, 1.0,
             lambda v: f"net margin {v:.0f}% — excellent"),
            (config.NET_MARGIN_GOOD, 0.8, lambda v: f"net margin {v:.0f}% — healthy"),
            (config.NET_MARGIN_FAIR, 0.55, lambda v: f"net margin {v:.0f}% — modest"),
            (config.NET_MARGIN_WEAK, 0.25, lambda v: f"net margin {v:.0f}% — slim"),
            (float("-inf"), 0.0, lambda v: f"net margin {v:.0f}% — loss-making"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("net margin unavailable")

    return _average(parts), notes


def _score_growth(r):
    """Revenue, profit and EPS growth, read against inflation."""

    notes = []
    parts = []

    def growth_band(label, value):
        return _band(value, [
            (config.GROWTH_EXCELLENT, 1.0,
             lambda v: f"{label} {v:+.0f}% — far ahead of inflation"),
            (config.GROWTH_GOOD, 0.8,
             lambda v: f"{label} {v:+.0f}% — beats ~{config.INFLATION_RATE:.0f}% inflation"),
            (config.GROWTH_FAIR, 0.45,
             lambda v: f"{label} {v:+.0f}% — growing, but shrinking in real terms"),
            (config.GROWTH_WEAK, 0.2, lambda v: f"{label} {v:+.0f}% — barely moving"),
            (float("-inf"), 0.0, lambda v: f"{label} {v:+.0f}% — contracting"),
        ])

    for key, label in (("revenue_growth", "revenue"),
                       ("profit_growth", "profit"),
                       ("eps_growth", "EPS")):
        value = _value(r.get(key))
        if value is None:
            notes.append(f"{label} growth unavailable")
            continue
        fraction, note = growth_band(label, value)
        parts.append(fraction)
        notes.append(note)

    return _average(parts), notes


def _score_valuation(r):
    """P/E, P/S and P/B on TSE levels. Cheap is better, absent is not cheap."""

    notes = []
    parts = []

    pe = _value(r.get("pe"))
    if pe is not None and pe > 0:
        fraction, note = _band(-pe, [
            (-config.PE_EXCELLENT, 1.0, lambda v: f"P/E {-v:.1f} — very cheap"),
            (-config.PE_GOOD, 0.8, lambda v: f"P/E {-v:.1f} — cheap"),
            (-config.PE_FAIR, 0.55, lambda v: f"P/E {-v:.1f} — fair"),
            (-config.PE_EXPENSIVE, 0.25, lambda v: f"P/E {-v:.1f} — expensive"),
            (float("-inf"), 0.05, lambda v: f"P/E {-v:.1f} — very expensive"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("P/E unavailable (no positive earnings)")

    ps = _value(r.get("ps"))
    if ps is not None and ps > 0:
        fraction, note = _band(-ps, [
            (-config.PS_EXCELLENT, 1.0, lambda v: f"P/S {-v:.1f} — very cheap"),
            (-config.PS_GOOD, 0.75, lambda v: f"P/S {-v:.1f} — reasonable"),
            (-config.PS_FAIR, 0.45, lambda v: f"P/S {-v:.1f} — full"),
            (float("-inf"), 0.1, lambda v: f"P/S {-v:.1f} — rich"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("P/S unavailable")

    pb = _value(r.get("pb"))
    if pb is not None and pb > 0:
        fraction, note = _band(-pb, [
            (-config.PB_EXCELLENT, 1.0, lambda v: f"P/B {-v:.1f} — below book+"),
            (-config.PB_GOOD, 0.75, lambda v: f"P/B {-v:.1f} — reasonable"),
            (-config.PB_FAIR, 0.45, lambda v: f"P/B {-v:.1f} — full"),
            (float("-inf"), 0.1, lambda v: f"P/B {-v:.1f} — rich"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("P/B unavailable")

    return _average(parts), notes


def _score_health(r):
    """Leverage and short-term solvency."""

    notes = []
    parts = []

    debt = _value(r.get("debt_ratio"))
    if debt is not None:
        fraction, note = _band(-debt, [
            (-config.DEBT_SAFE, 1.0, lambda v: f"debt {-v:.0f}% of assets — conservative"),
            (-config.DEBT_FAIR, 0.7, lambda v: f"debt {-v:.0f}% of assets — moderate"),
            (-config.DEBT_HIGH, 0.4, lambda v: f"debt {-v:.0f}% of assets — leveraged"),
            (float("-inf"), 0.15, lambda v: f"debt {-v:.0f}% of assets — heavily leveraged"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("debt ratio unavailable")

    current = _value(r.get("current_ratio"))
    if current is not None:
        fraction, note = _band(current, [
            (config.CURRENT_RATIO_STRONG, 1.0,
             lambda v: f"current ratio {v:.2f} — comfortable"),
            (config.CURRENT_RATIO_FAIR, 0.7,
             lambda v: f"current ratio {v:.2f} — adequate"),
            (config.CURRENT_RATIO_WEAK, 0.4,
             lambda v: f"current ratio {v:.2f} — tight"),
            (float("-inf"), 0.1,
             lambda v: f"current ratio {v:.2f} — current liabilities exceed assets"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("current ratio unavailable")

    return _average(parts), notes


def _score_cashflow(r):
    """Is the business generating cash, and how much of revenue becomes cash."""

    notes = []
    parts = []

    ocf = _value(r.get("operating_cash_flow"))
    if ocf is not None:
        parts.append(1.0 if ocf > 0 else 0.0)
        notes.append(
            "operating cash flow is positive" if ocf > 0
            else "operating cash flow is NEGATIVE — the business consumed cash"
        )
    else:
        notes.append("operating cash flow unavailable")

    margin = _value(r.get("ocf_margin"))
    if margin is not None:
        fraction, note = _band(margin, [
            (25.0, 1.0, lambda v: f"cash margin {v:.0f}% — strong conversion"),
            (12.0, 0.75, lambda v: f"cash margin {v:.0f}% — healthy"),
            (5.0, 0.5, lambda v: f"cash margin {v:.0f}% — modest"),
            (0.0, 0.25, lambda v: f"cash margin {v:.0f}% — thin"),
            (float("-inf"), 0.0, lambda v: f"cash margin {v:.0f}% — negative"),
        ])
        parts.append(fraction)
        notes.append(note)
    else:
        notes.append("cash margin unavailable")

    return _average(parts), notes


def _score_quality(r):
    """Operating cash flow against reported profit.

    The single most useful line in a filing. A company reporting large profits
    it has not collected is either extending credit it will not recover or
    recognising revenue early, and both show up here long before they show up
    in the profit line.
    """

    quality = _value(r.get("earnings_quality"))

    if quality is None:
        return None, ["earnings quality unavailable (needs profit and cash flow)"]

    fraction, note = _band(quality, [
        (config.QUALITY_EXCELLENT, 1.0,
         lambda v: f"cash/profit {v:.2f} — profit is fully collected"),
        (config.QUALITY_GOOD, 0.8,
         lambda v: f"cash/profit {v:.2f} — most profit arrives as cash"),
        (config.QUALITY_FAIR, 0.5,
         lambda v: f"cash/profit {v:.2f} — much of the profit is uncollected"),
        (0.0, 0.2,
         lambda v: f"cash/profit {v:.2f} — little of the profit is cash"),
        (float("-inf"), 0.0,
         lambda v: f"cash/profit {v:.2f} — profitable on paper, burning cash"),
    ])

    return fraction, [note]


PILLARS = [
    ("Profitability", config.FUND_WEIGHT_PROFIT, _score_profitability),
    ("Growth", config.FUND_WEIGHT_GROWTH, _score_growth),
    ("Valuation", config.FUND_WEIGHT_VALUE, _score_valuation),
    ("Financial health", config.FUND_WEIGHT_HEALTH, _score_health),
    ("Cash flow", config.FUND_WEIGHT_CASHFLOW, _score_cashflow),
    ("Earnings quality", config.FUND_WEIGHT_QUALITY, _score_quality),
]


# ==============================
# PUBLIC
# ==============================

def status_for(score):
    return next(label for floor, label in config.FUND_STATUS_BANDS
                if score >= floor)


def score(ratios, min_coverage=None):
    """0-100 fundamental score over whichever pillars could be computed."""

    min_coverage = FUND_MIN_COVERAGE if min_coverage is None else min_coverage

    ratios = ratios or {}

    breakdown = {}
    notes = []

    available = []
    total_weight = 0.0

    for label, weight, fn in PILLARS:

        fraction, pillar_notes = fn(ratios)

        notes.extend(f"{label}: {n}" for n in pillar_notes)

        if fraction is None:
            breakdown[label] = None
            continue

        available.append((label, weight, fraction))
        total_weight += weight

    covered = round(total_weight / 100.0, 2)

    if not available or covered < min_coverage:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "status": None,
            "breakdown": {label: None for label, _, _ in PILLARS},
            "notes": notes,
            "weight_covered": covered,
            "reason": (f"only {int(covered*100)}% of the fundamental model could "
                       f"run (need {int(min_coverage*100)}%)"),
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
        "status": status_for(value),
        "breakdown": breakdown,
        "notes": notes,
        "weight_covered": covered,
        "reason": None,
    }
