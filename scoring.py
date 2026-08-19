"""The combined engine: four readings in, one overall score and signal out.

Each reading answers a different question and none of them subsumes another:

    technical    is the price acting well *now*
    fundamental  is the business worth owning at all
    risk         what would owning it cost in volatility and drawdown
    flow         who is on each side while the price does what it does

The blend is weighted in config.py and is a preference, not a fact -- which is
why it is tunable and why `weights` can be overridden per call.

Two rules that matter more than the weights:

  MISSING IS NOT ZERO. Weights are renormalised over the readings that exist.
  A company with no Codal filings is ranked on its technicals and flow rather
  than pushed to the bottom by an absent fundamental score, because "we have
  not downloaded its statements yet" is a fact about us, not about the company.
  Below SCORE_MIN_COVERAGE of total weight there is no overall score at all.

  AN AVERAGE HIDES A DISAGREEMENT. A stock scoring 85 technically and 30
  fundamentally averages to a respectable 58 and disappears into the middle of
  a ranked list -- exactly the stock a user most needs to see flagged. The
  divergence check names both directions instead of letting the mean bury them.
"""

import config


VALUE_TRAP = "VALUE_WITHOUT_MOMENTUM"       # sound and cheap, chart says no
MOMENTUM_TRAP = "MOMENTUM_WITHOUT_SUBSTANCE"  # chart says yes, nothing behind it
ALIGNED = "ALIGNED"


# Selection modes -- how the basket decides which approved names to hold.
BLEND = "blend"
GATE_THEN_FLOW = "gate_then_flow"


def _value(x):
    if x is None:
        return None
    try:
        number = float(x)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number


def default_weights():
    return {
        "technical": config.SCORE_WEIGHT_TECHNICAL,
        "fundamental": config.SCORE_WEIGHT_FUNDAMENTAL,
        "risk": config.SCORE_WEIGHT_RISK,
        "flow": config.SCORE_WEIGHT_FLOW,
    }


def status_for(score):
    return next(label for floor, label in config.FINAL_STATUS_BANDS
                if score >= floor)


def divergence(technical, fundamental, gap=None):
    """Name the disagreement between the two main readings, if there is one."""

    gap = config.DIVERGENCE_GAP if gap is None else gap

    technical = _value(technical)
    fundamental = _value(fundamental)

    if technical is None or fundamental is None:
        return None

    if fundamental - technical >= gap:
        return VALUE_TRAP

    if technical - fundamental >= gap:
        return MOMENTUM_TRAP

    return ALIGNED


def combine(technical=None, fundamental=None, risk=None, flow=None,
            weights=None, min_coverage=None):
    """Blend whichever readings are present into one 0-100 overall score.

    Every argument is a 0-100 score or None. `risk` is expected already
    oriented so that higher is better -- a *low* risk score meaning a risky
    stock -- because a component where higher is worse would silently invert
    the blend. risk_score() below does that conversion.
    """

    weights = dict(weights or default_weights())

    min_coverage = (config.SCORE_MIN_COVERAGE if min_coverage is None
                    else min_coverage)

    components = {
        "technical": _value(technical),
        "fundamental": _value(fundamental),
        "risk": _value(risk),
        "flow": _value(flow),
    }

    total_weight = float(sum(weights.values())) or 1.0

    available = {
        key: value for key, value in components.items()
        if value is not None and weights.get(key)
    }

    covered_weight = sum(weights[key] for key in available)

    coverage = round(covered_weight / total_weight, 2)

    contributions = {}

    if not available or coverage < min_coverage:
        return {
            "score": None,
            "scored": False,
            "status": None,
            "coverage": coverage,
            "components": components,
            "contributions": contributions,
            "weights": weights,
            "divergence": divergence(technical, fundamental),
            "reason": (f"only {int(coverage*100)}% of the scoring model had "
                       f"inputs (need {int(min_coverage*100)}%)"),
        }

    # Renormalise over what is present, so the result stays on 0-100 whatever
    # subset of readings exists.
    scale = 1.0 / covered_weight

    total = 0.0

    for key, value in available.items():
        points = weights[key] * scale * value
        contributions[key] = round(points, 1)
        total += points

    overall = int(round(max(0.0, min(100.0, total))))

    return {
        "score": overall,
        "scored": True,
        "status": status_for(overall),
        "coverage": coverage,
        "components": components,
        "contributions": contributions,
        "weights": weights,
        "divergence": divergence(technical, fundamental),
        "reason": None,
    }


def selection_score(technical=None, flow=None, combined=None,
                    mode=None, gate=None):
    """The number the basket is ranked by, and the reason for it.

    Separated from combine() because ranking and scoring are different jobs.
    combine() answers "how good is this name overall" and belongs in the
    report; this answers "which of the approved names do we hold", and the
    measurement says those two are not the same question.

    MEASURED, on 3,393 point-in-time signals over 756 symbols (backtest_
    combined.py, 20-session hold, technical gate 70):

        technical 70-79   n=2110   mean +8.30%
        technical 80-89   n= 942   mean +5.58%
        technical 90-100  n= 341   mean +6.80%

        flow 0-19         n= 454   mean +5.59%   PF 2.25
        flow 20-39        n= 573   mean +6.43%   PF 2.64
        flow 40-59        n= 806   mean +6.60%   PF 2.73
        flow 60-79        n= 663   mean +7.00%   PF 2.90
        flow 80-100       n= 896   mean +9.90%   PF 3.90

    The technical score sorts the wrong way above its own threshold, while the
    flow score rises monotonically across every bucket. So technical is used
    as a GATE -- clearing 70 is what matters, not clearing it by more -- and
    the ordering within the approved set comes from flow.

    A name with no flow reading falls back to the blend rather than being
    dropped: absent client-type data is a fact about our coverage, not about
    the company, and the same rule governs missing weight in combine().
    """

    mode = mode or getattr(config, "SELECTION_MODE", BLEND)
    gate = getattr(config, "TECHNICAL_GATE", 70) if gate is None else gate

    technical = _value(technical)
    flow = _value(flow)
    combined = _value(combined)

    if mode != GATE_THEN_FLOW:
        return {
            "score": combined,
            "eligible": combined is not None,
            "mode": BLEND,
            "basis": "combined",
            "reason": None,
        }

    if technical is None:
        return {
            "score": None,
            "eligible": False,
            "mode": mode,
            "basis": None,
            "reason": "no technical reading — the gate cannot be evaluated",
        }

    if technical < gate:
        return {
            "score": None,
            "eligible": False,
            "mode": mode,
            "basis": None,
            "reason": f"technical {technical:.0f} below the gate of {gate}",
        }

    if flow is not None:
        return {
            "score": flow,
            "eligible": True,
            "mode": mode,
            "basis": "flow",
            "reason": None,
        }

    return {
        "score": combined,
        "eligible": combined is not None,
        "mode": mode,
        "basis": "combined (no flow data)",
        "reason": "no client-type data — ranked on the blend instead",
    }


def risk_score(metrics):
    """Volatility and drawdown exposure as a 0-100 where higher is safer.

    Deliberately inverted at the source rather than at the call site. A "risk
    score" that means *more* risk when it is higher, blended into an average
    where every other component means better when higher, is a sign error that
    produces a plausible ranked list -- the worst kind, because nothing about
    the output looks wrong.
    """

    volatility = _value(metrics.get("volatility_60")) if metrics else None
    atr_pct = _value(metrics.get("atr_pct")) if metrics else None
    liquidity = _value(metrics.get("avg_value_20")) if metrics else None

    parts = []
    notes = []

    if volatility is not None:
        # Annualised realised volatility. TSE's ±3% band keeps most names
        # under 60%; past that, position sizing is doing all the work.
        if volatility <= 25:
            parts.append(1.0); notes.append(f"volatility {volatility:.0f}% — low")
        elif volatility <= 40:
            parts.append(0.75); notes.append(f"volatility {volatility:.0f}% — moderate")
        elif volatility <= 60:
            parts.append(0.5); notes.append(f"volatility {volatility:.0f}% — elevated")
        elif volatility <= 85:
            parts.append(0.25); notes.append(f"volatility {volatility:.0f}% — high")
        else:
            parts.append(0.05); notes.append(f"volatility {volatility:.0f}% — extreme")
    else:
        notes.append("volatility unavailable")

    if atr_pct is not None:
        if atr_pct <= 2:
            parts.append(1.0)
        elif atr_pct <= 3.5:
            parts.append(0.7)
        elif atr_pct <= 5:
            parts.append(0.45)
        else:
            parts.append(0.2)
        notes.append(f"ATR {atr_pct:.1f}% of price")

    if liquidity is not None:
        # An illiquid holding cannot be exited at the modelled price, which is
        # a risk the volatility number does not contain.
        multiple = liquidity / config.MIN_DAILY_VALUE if config.MIN_DAILY_VALUE else 0
        if multiple >= 10:
            parts.append(1.0); notes.append("very liquid")
        elif multiple >= 4:
            parts.append(0.8); notes.append("liquid")
        elif multiple >= 1:
            parts.append(0.5); notes.append("adequate liquidity")
        else:
            parts.append(0.15); notes.append("thin — hard to exit")
    else:
        notes.append("liquidity unavailable")

    if not parts:
        return {"score": None, "notes": notes}

    value = int(round(sum(parts) / len(parts) * 100))

    return {"score": value, "notes": notes}


def evaluate(technical=None, fundamental=None, risk=None, flow=None,
             weights=None):
    """One row's full verdict: every score, the blend, and the final signal.

    This is what the ranking table is built from, and it is deliberately the
    only place the final status is decided -- so "STRONG BUY CANDIDATE" means
    the same thing wherever it appears.
    """

    result = combine(
        technical=technical, fundamental=fundamental, risk=risk, flow=flow,
        weights=weights,
    )

    result["technical_score"] = _value(technical)
    result["fundamental_score"] = _value(fundamental)
    result["risk_score"] = _value(risk)
    result["flow_score"] = _value(flow)

    # A divergence does not change the number -- it is a label on it, so the
    # ranking stays explainable and the user decides what to do about the gap.
    if result["divergence"] == VALUE_TRAP:
        result["divergence_note"] = (
            "بنیاد قوی‌تر از تکنیکال — ارزنده ولی هنوز حرکتی نکرده"
        )
    elif result["divergence"] == MOMENTUM_TRAP:
        result["divergence_note"] = (
            "تکنیکال قوی‌تر از بنیاد — حرکت قیمت پشتوانه‌ی بنیادی ندارد"
        )
    else:
        result["divergence_note"] = None

    return result
