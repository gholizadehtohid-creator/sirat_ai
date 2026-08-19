"""Fundamental screening: hard gates, then a 0-100 soft score.

The split matters. A hard gate is a fact that makes a symbol untradeable or
uninvestable regardless of how attractive everything else looks -- suspended,
loss-making, too illiquid to exit. A soft score is a preference: cheaper is
better, more liquid is better. Mixing the two lets a wonderfully cheap but
suspended share reach the technical stage.

Verdicts:
    APPROVED           passed every gate and scored >= FUND_APPROVE_SCORE
    REJECTED           failed a hard gate, or scored below the cut
    INSUFFICIENT_DATA  not enough data to judge -- explicitly NOT approved

The third verdict carries the weight here. On a network where the market
snapshot is unreachable, most of the universe lands in it. Reporting that
honestly is the whole point: a screen that silently approves symbols it knows
nothing about is worse than one that admits the gap.
"""

import config


# ==============================
# HARD GATES
# ==============================

def _gate_stale(m):
    stale = m.get("stale_days")
    if stale is not None and stale > config.MAX_STALE_DAYS:
        return f"no trade for {stale} days (likely suspended)"
    return None


def _gate_state(m):
    state = (m.get("state") or "").strip().upper()
    # TSETMC instrument states: I = allowed, others cover halted/suspended.
    if state and state not in ("I", "A", ""):
        return f"instrument state '{state}' is not open for trading"
    return None


def _gate_liquidity(m):
    value = m.get("avg_value_20")
    if value is None:
        return None                       # unknown, not a failure -- see coverage
    if value < config.MIN_DAILY_VALUE:
        return (f"average daily value {value/1e9:.2f}B rials is below the "
                f"{config.MIN_DAILY_VALUE/1e9:.0f}B floor")
    return None


def _gate_lossmaking(m):
    eps = m.get("eps")

    if eps is None or eps > 0:
        return None

    # A hard gate has to be a fact that makes the share uninvestable. For an
    # investment company or an insurer, a year with no disposals and no
    # reserve release produces a negative EPS while the portfolio behind it is
    # untouched -- so this is not that fact, and rejecting on it discards a
    # sound company for having had a quiet year.
    if not m.get("earnings_multiple_meaningful", True):
        return None

    return f"loss-making (EPS {eps:,.0f})"


# Each gate: (key, label, function). Order is the order they are reported in.
#
# "No price history" is deliberately NOT a gate. Rejecting a share because we
# hold no data on it would report an absence of information as a negative
# judgement -- the screen would look like it had assessed 813 companies and
# found them wanting, when it had assessed nothing. That case is
# INSUFFICIENT_DATA instead.
HARD_GATES = [
    ("stale", "Stale / suspended", _gate_stale),
    ("state", "Not open for trading", _gate_state),
    ("liquidity", "Too illiquid", _gate_liquidity),
    ("lossmaking", "Loss-making", _gate_lossmaking),
]


def apply_gates(metrics):
    """Return the list of hard-gate failures for one symbol."""

    failures = []

    for key, label, check in HARD_GATES:
        reason = check(metrics)
        if reason:
            failures.append({"gate": key, "label": label, "reason": reason})

    return failures


# ==============================
# SOFT SCORE COMPONENTS
# ==============================

def _score_nav(m):
    """Price against net asset value, for companies that own rather than earn.

    Below 1.0 the market prices the company under the assets it holds, which
    for an investment company is the discount that matters. The bands are wide
    on purpose: a persistent discount is normal on this exchange, so 0.8 is
    ordinary rather than remarkable, and the score separates deep discounts
    from outright premiums instead of pretending to finer resolution than the
    data supports.

    NAV built while unlisted holdings sit at cost is a FLOOR, so the implied
    P/NAV is a ceiling. Capping the reward at that point keeps an understated
    NAV from reading as a bargain.
    """

    ratio = m.get("price_to_nav")

    if ratio is None or ratio <= 0:
        return None, "P/NAV unavailable"

    complete = m.get("nav_complete")

    if ratio <= 0.7:
        fraction, label = 1.0, "deep discount to NAV"
    elif ratio <= 0.9:
        fraction, label = 0.8, "discount to NAV"
    elif ratio <= 1.15:
        fraction, label = 0.55, "around NAV"
    elif ratio <= 1.6:
        fraction, label = 0.3, "premium to NAV"
    else:
        fraction, label = 0.1, "large premium to NAV"

    note = f"P/NAV {ratio:.2f} — {label}"

    if not complete:
        # The true ratio is lower than this one, so a high score here would be
        # resting on the half of the portfolio we cannot value.
        fraction = min(fraction, 0.8)
        note += " (NAV understated: unlisted holdings at cost)"

    return fraction, note


def _score_valuation(m):
    """Cheaper is better, against TSE-calibrated P/E bands."""

    # Where earnings multiples do not apply, NAV takes this component's place
    # rather than leaving it empty -- an investment company priced at half its
    # assets is genuinely cheap, and the screen should be able to say so.
    if not m.get("earnings_multiple_meaningful", True):

        fraction, note = _score_nav(m)

        if fraction is not None:
            return fraction, note

        pe = m.get("pe")

        if pe is None or pe <= 0:
            return None, "P/E and P/NAV both unavailable"

        return None, (f"P/E {pe:.1f} withheld — "
                      f"{m.get('sector_name') or 'this sector'} is valued on "
                      f"assets, not earnings, and no portfolio filing is held")

    pe = m.get("pe")

    if pe is None or pe <= 0:
        return None, "P/E unavailable"

    if pe <= config.PE_EXCELLENT:
        return 1.0, f"P/E {pe:.1f} — very cheap"
    if pe <= config.PE_GOOD:
        return 0.8, f"P/E {pe:.1f} — cheap"
    if pe <= config.PE_FAIR:
        return 0.55, f"P/E {pe:.1f} — fair"
    if pe <= config.PE_EXPENSIVE:
        return 0.25, f"P/E {pe:.1f} — expensive"

    return 0.05, f"P/E {pe:.1f} — very expensive"


def _score_profitability(m):
    """Earnings yield against the risk-free rate.

    With a ~23% policy rate, a share yielding less than cash needs growth to
    justify itself -- and growth is not something this screen can measure.
    """

    yield_pct = m.get("earnings_yield")

    if yield_pct is None:
        return None, "earnings yield unavailable"

    # Same objection as the valuation component: a yield computed on a
    # disposal gain is not a yield the holder can expect again.
    if not m.get("earnings_multiple_meaningful", True):
        return None, (f"earnings yield {yield_pct:.1f}% withheld — "
                      f"realised gains are not recurring earnings")

    hurdle = config.RISK_FREE_RATE * 100

    ratio = yield_pct / hurdle if hurdle else 0

    if ratio >= 1.5:
        return 1.0, f"earnings yield {yield_pct:.1f}% — well above the {hurdle:.0f}% rate"
    if ratio >= 1.0:
        return 0.8, f"earnings yield {yield_pct:.1f}% — beats the {hurdle:.0f}% rate"
    if ratio >= 0.7:
        return 0.5, f"earnings yield {yield_pct:.1f}% — near the {hurdle:.0f}% rate"
    if ratio >= 0.4:
        return 0.25, f"earnings yield {yield_pct:.1f}% — below the {hurdle:.0f}% rate"

    return 0.05, f"earnings yield {yield_pct:.1f}% — far below the {hurdle:.0f}% rate"


def _score_liquidity(m):
    """More traded value is better, on a log scale above the floor."""

    value = m.get("avg_value_20")

    if value is None or value <= 0:
        return None, "traded value unavailable"

    floor = config.MIN_DAILY_VALUE

    multiple = value / floor

    if multiple >= 20:
        return 1.0, f"very liquid ({value/1e9:.0f}B/day)"
    if multiple >= 8:
        return 0.85, f"liquid ({value/1e9:.0f}B/day)"
    if multiple >= 3:
        return 0.6, f"adequate ({value/1e9:.1f}B/day)"
    if multiple >= 1:
        return 0.35, f"thin ({value/1e9:.1f}B/day)"

    return 0.1, f"very thin ({value/1e9:.2f}B/day)"


def _score_size(m):
    """Larger companies are less prone to manipulation and easier to exit."""

    cap = m.get("market_cap")

    if cap is None or cap <= 0:
        return None, "market cap unavailable"

    trillions = cap / 1e13          # 10 trillion rials = 1 unit

    if trillions >= 50:
        return 1.0, f"very large ({cap/1e13:.0f}0B rials)"
    if trillions >= 10:
        return 0.85, f"large ({cap/1e13:.0f}0B rials)"
    if trillions >= 2:
        return 0.6, f"mid ({cap/1e13:.1f}0B rials)"
    if trillions >= 0.5:
        return 0.35, "small"

    return 0.1, "micro-cap"


def _score_stability(m):
    """Lower realised volatility scores higher."""

    vol = m.get("volatility_60")

    if vol is None:
        return None, "volatility unavailable"

    if vol <= 25:
        return 1.0, f"low volatility ({vol:.0f}%)"
    if vol <= 40:
        return 0.75, f"moderate volatility ({vol:.0f}%)"
    if vol <= 60:
        return 0.5, f"elevated volatility ({vol:.0f}%)"
    if vol <= 85:
        return 0.25, f"high volatility ({vol:.0f}%)"

    return 0.05, f"extreme volatility ({vol:.0f}%)"


COMPONENTS = [
    ("Valuation", config.FUND_WEIGHT_VALUATION, _score_valuation),
    ("Profitability", config.FUND_WEIGHT_PROFITABILITY, _score_profitability),
    ("Liquidity", config.FUND_WEIGHT_LIQUIDITY, _score_liquidity),
    ("Size", config.FUND_WEIGHT_SIZE, _score_size),
    ("Stability", config.FUND_WEIGHT_STABILITY, _score_stability),
]


def score_fundamentals(metrics):
    """Weighted 0-100 score over whatever components are computable.

    Missing components have their weight redistributed across the rest, so a
    score stays on a 0-100 scale -- but `weight_covered` reports how much of
    the intended model actually ran, and the caller must not treat a score
    built from 20% of the weights as equivalent to a full one.
    """

    breakdown = {}
    notes = []

    available = []
    total_weight = 0.0

    for label, weight, fn in COMPONENTS:

        fraction, note = fn(metrics)

        if fraction is None:
            breakdown[label] = None
            notes.append(f"{label}: {note}")
            continue

        available.append((label, weight, fraction))
        total_weight += weight
        notes.append(f"{label}: {note}")

    if not available:
        return {
            "score": 0,
            "breakdown": {label: None for label, _, _ in COMPONENTS},
            "notes": notes,
            "weight_covered": 0.0,
        }

    # Redistribute the missing weight proportionally over what we do have.
    scale = 100.0 / total_weight

    score = 0.0

    for label, weight, fraction in available:
        points = weight * scale * fraction
        breakdown[label] = round(points, 1)
        score += points

    return {
        "score": int(round(max(0.0, min(100.0, score)))),
        "breakdown": breakdown,
        "notes": notes,
        "weight_covered": round(total_weight / 100.0, 2),
    }


# ==============================
# VERDICT
# ==============================

APPROVED = "APPROVED"
PROVISIONAL = "PROVISIONAL"
REJECTED = "REJECTED"
INSUFFICIENT = "INSUFFICIENT_DATA"

# Share of the scoring model needed before a verdict is a full judgement.
FULL_WEIGHT_COVERAGE = 0.60

# Below this, nothing meaningful ran at all.
MIN_WEIGHT_COVERAGE = 0.15


def screen(metrics, approve_score=None):
    """Full fundamental verdict for one instrument.

    Four outcomes, deliberately distinct:

      APPROVED          gates passed, most of the model ran, score cleared
      PROVISIONAL       gates passed and the score cleared, but key
                        fundamentals (EPS/PE/market cap) were unavailable, so
                        this is a liquidity-and-stability judgement only
      REJECTED          a hard gate failed, or the score fell short
      INSUFFICIENT_DATA nothing could be evaluated -- an absence of evidence,
                        never reported as evidence of absence
    """

    approve_score = (
        config.FUND_APPROVE_SCORE if approve_score is None else approve_score
    )

    import fundamentals

    scored = score_fundamentals(metrics)

    missing = fundamentals.missing_fields(metrics)

    base = {
        "score": scored["score"],
        "breakdown": scored["breakdown"],
        "weight_covered": scored["weight_covered"],
        "gates_failed": [],
        "notes": scored["notes"],
        "missing": missing,
    }

    # Nothing to judge on. Not a rejection -- we simply have no data.
    if not metrics.get("has_price_data") or scored["weight_covered"] < MIN_WEIGHT_COVERAGE:
        bars = metrics.get("bars", 0)
        reason = (
            f"no price history cached ({bars} bars, need {config.MIN_HISTORY_BARS})"
            if bars < config.MIN_HISTORY_BARS
            else f"only {int(scored['weight_covered']*100)}% of the model could run"
        )
        return {**base, "verdict": INSUFFICIENT, "reasons": [reason]}

    # A hard gate is decisive and needs no score.
    failures = apply_gates(metrics)

    if failures:
        return {
            **base,
            "verdict": REJECTED,
            "reasons": [f["reason"] for f in failures],
            "gates_failed": failures,
        }

    if scored["score"] < approve_score:
        return {
            **base,
            "verdict": REJECTED,
            "reasons": [
                f"fundamental score {scored['score']} is below the "
                f"{approve_score} threshold"
            ],
        }

    # Passed, but on a partial model: say so rather than calling it approved.
    if scored["weight_covered"] < FULL_WEIGHT_COVERAGE:
        return {
            **base,
            "verdict": PROVISIONAL,
            "reasons": [
                f"passed on {int(scored['weight_covered']*100)}% of the model — "
                f"missing: {', '.join(missing)}"
            ],
        }

    return {**base, "verdict": APPROVED, "reasons": []}
