"""Market regime, extended into a trading mode -- what the market permits today.

WHAT THIS ADDS OVER regime.py, AND WHY regime.py IS NOT TOUCHED
--------------------------------------------------------------
`regime.py` already votes BULL / BEAR / SIDEWAYS over four inputs -- TEDPIX
trend, equal-weight breadth, constituent participation and realised volatility
-- and converts the verdict into a cash target. It is measured, it is used by
the live advisor and by the backtest's regime-cash option, and reproducing its
vote here would create two regime detectors that could disagree. So this module
CALLS it and adds the two things the v2 specification asks for that it does not
have:

  TURNOVER AS AN INPUT. regime.py reads no volume at all. On this exchange that
  is a real gap and not a small one: the ±3% band means a market losing
  conviction goes quiet before it goes down, so turnover contracting against its
  own baseline is often the earliest of the five readings to move. It is added
  as a MODIFIER rather than a fifth vote, because a vote would let a quiet week
  overturn a trend-and-breadth agreement, and volume is the least reliable of
  the readings on a market where a single block trade moves the daily total.

  A TRADING MODE. A cash percentage is an allocation, not an instruction. The
  same 25% cash target means "buy selectively" in a drifting market and "take
  what the market gives you" in a quiet uptrend, and the difference belongs in
  the vocabulary rather than in the reader's head. The mode is what the factor
  engine and the portfolio engine consult.

WHAT THIS MODULE DOES *NOT* DO ANY MORE
---------------------------------------
It does not contribute to a stock's Swing Score. regime_score() still exists and
is still reported, but its weight in factor_engine is 0, because the value is
identical for every name on a session and a constant cannot order a list --
measured, and the measurement is in config beside SWING_WEIGHT_REGIME.

The market read now acts through three levers, and all three are about HOW MUCH
and WHETHER rather than about WHICH:

    position_multiplier()   how much of a full position this market deserves,
                            which risk_engine applies to the per-trade budget
    entry_floor()           how far into the buy band a name must be before it
                            is entered at all, which factor_engine.decide()
                            applies
    max_invested_pct        how much of the account may be at risk, which
                            portfolio_engine applies

None of the three can move one name above another, which is exactly the property
the regime factor lacked.

THE BUDGET NEVER LOOSENS WHAT regime.py DECIDED
-----------------------------------------------
`max_invested_pct` from the mode is regime.py's own figure MULTIPLIED by the
mode's budget share, which is at most 1.0. This layer can therefore only ever
hold more cash than regime.py asked for, never less. That direction is chosen
deliberately: a bug in a new layer that makes the system too cautious costs
return, and one that makes it too aggressive costs capital.

NOT A FORECAST. BULL does not mean "will rise". It means the market has been
rising, broadly, on participating volume -- a description of what already
happened, used to size exposure rather than to predict.
"""

import math

import config
import regime


BULL = regime.BULL
BEAR = regime.BEAR
SIDEWAYS = regime.SIDEWAYS

# The specification's vocabulary, mapped onto regime.py's. Kept as an explicit
# table rather than a string transformation so a rename on either side is a
# failing test instead of a silently unmatched label.
SPEC_NAMES = {
    BULL: "Bullish",
    BEAR: "Bearish",
    SIDEWAYS: "Sideways",
}

AGGRESSIVE = "AGGRESSIVE"
NORMAL = "NORMAL"
SELECTIVE = "SELECTIVE"
DEFENSIVE = "DEFENSIVE"
CASH_ONLY = "CASH_ONLY"

MODES = (AGGRESSIVE, NORMAL, SELECTIVE, DEFENSIVE, CASH_ONLY)

MODE_LABELS = {
    AGGRESSIVE: "تهاجمی — بازار اجازه می‌دهد",
    NORMAL: "عادی",
    SELECTIVE: "گزینشی — فقط بهترین‌ها",
    DEFENSIVE: "تدافعی — کاهش موقعیت",
    CASH_ONLY: "نقد — ورود جدید ممنوع",
}

EXPANDING = "EXPANDING"
STEADY = "STEADY"
CONTRACTING = "CONTRACTING"

# Confidence at or above which a BULL reading earns the aggressive mode. Below
# it the market is rising on a split vote, which is a different situation from
# rising on all three.
CONFIDENT = 0.66


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# TURNOVER
# ==============================

def turnover_state(series=None, window=None, baseline=None):
    """Recent market turnover against its own longer baseline.

    `series` is a sequence of per-session turnover figures, oldest first, or a
    frame with a `turnover` column -- database.market_turnover() returns the
    latter. Pass it explicitly and this function is pure, which is how it is
    tested; pass nothing and it reads the database.

    Returns None when there is not enough history. A ratio computed from four
    sessions against a baseline of four sessions is 1.0 by construction and
    would report "steady" for any market at all.
    """

    window = int(window or config.REGIME_TURNOVER_WINDOW)
    baseline = int(baseline or config.REGIME_TURNOVER_BASELINE)

    values = _turnover_values(series)

    if values is None or len(values) < baseline:
        return {
            "state": None,
            "ratio": None,
            "recent": None,
            "baseline": None,
            "sessions": 0 if values is None else len(values),
            "reason": "need %d sessions of turnover, have %d"
                      % (baseline, 0 if values is None else len(values)),
        }

    # The baseline INCLUDES the recent window. Excluding it would compare the
    # last ten sessions against the ten before them on a long enough lever to
    # call every second week a regime change; including it means the ratio
    # measures a deviation from the period's own normal.
    tail = values[-baseline:]

    recent = sum(values[-window:]) / float(window)

    mean = sum(tail) / float(len(tail))

    if not mean or mean <= 0:
        return {
            "state": None, "ratio": None, "recent": recent, "baseline": mean,
            "sessions": len(values),
            "reason": "the baseline turnover is zero — nothing traded",
        }

    ratio = recent / mean

    if ratio >= config.REGIME_TURNOVER_EXPANDING:
        state = EXPANDING
    elif ratio <= config.REGIME_TURNOVER_CONTRACTING:
        state = CONTRACTING
    else:
        state = STEADY

    return {
        "state": state,
        "ratio": round(ratio, 3),
        "recent": recent,
        "baseline": mean,
        "sessions": len(values),
        "window": window,
        "baseline_sessions": baseline,
        "reason": None,
    }


def _turnover_values(series):
    """A turnover sequence out of a frame, a list, or the database."""

    if series is None:
        try:
            import database
            frame = database.market_turnover()
        except Exception:
            return None
        series = frame

    if series is None:
        return None

    # A pandas frame, without importing pandas to find out.
    if hasattr(series, "columns"):
        if "turnover" not in series.columns:
            return None
        series = series["turnover"]

    if hasattr(series, "tolist"):
        series = series.tolist()

    values = [_finite(v) for v in series]

    return [v for v in values if v is not None and v >= 0]


# ==============================
# THE REGIME FACTOR
# ==============================

def regime_score(verdict, confidence=None, volatility_regime=None,
                 turnover=None):
    """The market read on one 0-100 scale. NO LONGER A FACTOR IN A STOCK SCORE.

    It used to carry 20% of the Swing Score and was removed from it, because a
    number that is identical for every name on a session cannot order those
    names -- adding the same value to every row of a list cannot reorder the
    list -- while still consuming its share of the weight. See
    SWING_WEIGHT_REGIME in config for the measurement.

    What it is for now: a legible summary of the market on one axis, shown in
    the panel and the advisor next to the mode. It informs a human and it feeds
    nothing that ranks. The instruction the SYSTEM acts on is trading_mode()
    below, together with position_multiplier() and entry_floor().

    50 is neutral. The base is set by the verdict and then pulled TOWARD 50 by
    low confidence -- a bull call on a 0.5 vote is worth half a bull call, and
    the alternative (a fixed 85 whenever the vote leans bullish) would treat a
    split market as an agreeing one.

    Volatility and turnover then adjust it by a few points each, deliberately
    small: a single volume reading should not be able to swing a market summary
    by 30 points.
    """

    base = {
        BULL: config.REGIME_SCORE_BULL,
        BEAR: config.REGIME_SCORE_BEAR,
        SIDEWAYS: config.REGIME_SCORE_SIDEWAYS,
    }.get(verdict)

    if base is None:
        return None

    neutral = config.REGIME_SCORE_SIDEWAYS

    confidence = _finite(confidence)

    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))
        base = neutral + (base - neutral) * confidence

    if volatility_regime == regime.HIGH_VOLATILITY:
        base -= 8.0
    elif volatility_regime == regime.LOW_VOLATILITY:
        base += 3.0

    state = (turnover or {}).get("state")

    # Expanding turnover confirms a rise and warns during a fall: volume
    # arriving in a bear market is distribution, not accumulation. So the sign
    # of the adjustment depends on the verdict rather than being fixed.
    if state == EXPANDING:
        base += 5.0 if verdict != BEAR else -5.0
    elif state == CONTRACTING:
        base -= 5.0 if verdict != BEAR else 0.0

    return int(round(max(0.0, min(100.0, base))))


def trading_mode(verdict, confidence=None, volatility_regime=None,
                 turnover=None):
    """The instruction that follows from the regime. Returns (mode, reasons)."""

    reasons = []

    confidence = _finite(confidence)

    high_volatility = volatility_regime == regime.HIGH_VOLATILITY

    state = (turnover or {}).get("state")

    if verdict == BEAR:
        if high_volatility:
            reasons.append("بازار نزولی با نوسان بالا — هیچ ورود جدیدی توجیه ندارد")
            return CASH_ONLY, reasons
        reasons.append("بازار نزولی — کاهش موقعیت و توقف ورود عادی")
        return DEFENSIVE, reasons

    if verdict == BULL:

        if high_volatility:
            reasons.append("روند صعودی است اما نوسان بالاست — گزینشی")
            return SELECTIVE, reasons

        if confidence is not None and confidence >= CONFIDENT:

            if state == CONTRACTING:
                # The most common trap on this market: the index keeps printing
                # highs while turnover halves. The trend reading is real and the
                # exit door is narrowing at the same time.
                reasons.append(
                    "روند صعودی و رأی قوی، اما ارزش معاملات در حال کاهش است — "
                    "خروج از موقعیت‌ها سخت‌تر می‌شود")
                return NORMAL, reasons

            reasons.append("بازار صعودی با رأی قوی")
            return AGGRESSIVE, reasons

        reasons.append("بازار صعودی اما رأی شکننده")
        return NORMAL, reasons

    # SIDEWAYS
    if state == CONTRACTING:
        reasons.append("بازار خنثی با ارزش معاملات نزولی — فقط موقعیت‌های ممتاز")
        return SELECTIVE, reasons

    if high_volatility:
        reasons.append("بازار خنثی با نوسان بالا — تدافعی")
        return DEFENSIVE, reasons

    reasons.append("بازار خنثی")
    return NORMAL, reasons


# ==============================
# WHAT THE REGIME IS ACTUALLY FOR
# ==============================

def position_multiplier(mode, verdict=None):
    """How much of a full position this market deserves. 0 to 1.

    This is the regime's main lever since it left the Swing Score, and it is
    deliberately ONE number rather than a second sizing knob beside the budget
    share: they are the same quantity asked twice, and two of them would
    compound silently into a position a quarter of the intended size.

    The specification's bands land where the modes already sat -- a confident
    bull at 1.0, a bear at 0.35, no entries at 0 -- with one correction. NORMAL
    is reached both by a calm sideways market and by a bull on a fragile vote,
    and 0.85 is too much size for the first. A SIDEWAYS verdict is therefore
    capped, which lands it in the specified 0.5-0.7 band and only ever tightens.
    """

    share = config.TRADING_MODE_BUDGET.get(mode, 1.0)

    if verdict == SIDEWAYS:
        share = min(share, config.REGIME_SIDEWAYS_MAX_MULTIPLIER)

    return share


def entry_floor(assessment):
    """Extra Swing Score points required to ENTER in this market. 0 in a good one.

    The specification's "in a bear market, only strong signals". Expressed as a
    floor rather than as a score adjustment on purpose: adding or subtracting
    points from every name would move the whole distribution without changing
    which name is best, which is the mistake that put the regime inside the
    Swing Score in the first place. A floor changes how many names qualify and
    nothing about their order.
    """

    if not assessment:
        return 0

    mode = assessment.get("trading_mode")

    try:
        return int(config.TRADING_MODE_ENTRY_FLOOR.get(mode, 0) or 0)
    except (TypeError, ValueError):
        return 0


# ==============================
# PUBLIC
# ==============================

def assess(report=None, turnover=None, params=None, bars=None, as_of=None):
    """The full v2 market read. Everything regime.py says, plus mode and score.

    `report` is regime.detect()'s output. Pass it and this function is pure --
    which is how the tests drive every branch without a database. Omit it and it
    is detected, with `as_of` handed straight through so a past date can be
    reproduced.
    """

    if report is None:
        report = regime.detect(params=params, bars=bars, as_of=as_of)

    if not report or not report.get("available"):
        return {
            "available": False,
            "regime": None,
            "spec_name": None,
            "confidence": None,
            "trading_mode": CASH_ONLY,
            "mode_label": MODE_LABELS[CASH_ONLY],
            "mode_budget_share": 0.0,
            "position_multiplier": 0.0,
            "entry_floor": 0,      # nothing to raise -- entries are refused
            "score": None,
            "max_invested_pct": 0.0,
            "cash_target_pct": 100.0,
            "turnover": turnover if isinstance(turnover, dict) else None,
            "evidence": [],
            "reasons": ["وضعیت بازار قابل تشخیص نیست — ورود جدید متوقف است"],
            "reason": (report or {}).get("reason")
                      or "regime.detect() returned nothing",
            "base": report,
        }

    if turnover is None or not isinstance(turnover, dict):
        turnover = turnover_state(turnover)

    verdict = report.get("regime")
    confidence = report.get("confidence")
    volatility_regime = report.get("volatility_regime")

    mode, reasons = trading_mode(verdict, confidence, volatility_regime,
                                 turnover)

    score = regime_score(verdict, confidence, volatility_regime, turnover)

    # The budget only ever tightens regime.py's figure -- see the module
    # docstring. min() is belt and braces in case a future TRADING_MODE_BUDGET
    # entry is edited above 1.0.
    base_invested = _finite(report.get("max_invested_pct"))

    if base_invested is None:
        base_invested = 100.0 - config.MIN_CASH_PCT

    share = position_multiplier(mode, verdict)

    invested = min(base_invested, base_invested * share)

    evidence = list(report.get("evidence") or [])

    if turnover and turnover.get("state"):
        # .get() throughout: a caller may hand in a partial reading -- the tests
        # and the factor sweep both do, passing only a state -- and a KeyError
        # here would take down a market read over a missing display figure.
        evidence.append(
            "ارزش معاملات %d روز اخیر %.0f%% میانگین %d روزه — %s"
            % (turnover.get("window") or config.REGIME_TURNOVER_WINDOW,
               (turnover.get("ratio") or 0) * 100,
               turnover.get("baseline_sessions") or config.REGIME_TURNOVER_BASELINE,
               {EXPANDING: "در حال افزایش",
                STEADY: "پایدار",
                CONTRACTING: "در حال کاهش"}.get(turnover["state"], turnover["state"]))
        )
    elif turnover and turnover.get("reason"):
        evidence.append("ارزش معاملات: %s" % turnover["reason"])

    return {
        "available": True,
        "regime": verdict,
        "spec_name": SPEC_NAMES.get(verdict, verdict),
        "label": report.get("label"),
        "confidence": confidence,
        "votes": report.get("votes"),
        "volatility_regime": volatility_regime,
        "trading_mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "mode_budget_share": share,
        # The same number under the name the specification uses. One quantity,
        # two keys, deliberately -- see position_multiplier().
        "position_multiplier": share,
        "entry_floor": int(config.TRADING_MODE_ENTRY_FLOOR.get(mode, 0) or 0),
        # Kept and reported, no longer blended into any stock's score.
        "score": score,
        "max_invested_pct": round(invested, 1),
        "cash_target_pct": round(100.0 - invested, 1),
        "regime_cash_target_pct": report.get("cash_target_pct"),
        "participation": report.get("participation"),
        "cap_weighted": report.get("cap_weighted"),
        "equal_weighted": report.get("equal_weighted"),
        "turnover": turnover,
        "evidence": evidence,
        "reasons": reasons,
        "missing": report.get("missing"),
        "caveat": report.get("caveat"),
        "reason": None,
        "base": report,
    }


def allows_new_entries(assessment):
    """One question the factor engine asks constantly, answered in one place."""

    if not assessment or not assessment.get("available"):
        return False

    return assessment.get("trading_mode") != CASH_ONLY


if __name__ == "__main__":

    result = assess()

    print("=" * 74)
    print("SIRAT AI — وضعیت بازار و حالت معاملاتی / REGIME ENGINE")
    print("=" * 74)

    if not result["available"]:
        print("\n  %s" % result["reason"])
        print("  حالت: %s" % result["mode_label"])
        raise SystemExit(0)

    print("\n  وضعیت        %s  (%s)" % (result["label"], result["spec_name"]))
    print("  اطمینان      %s" % result["confidence"])
    print("  حالت معاملاتی %s" % result["mode_label"])
    print("  امتیاز رژیم   %s / 100" % result["score"])
    print("  سرمایه‌گذاری  حداکثر %.1f%%  (نقد %.1f%%)"
          % (result["max_invested_pct"], result["cash_target_pct"]))

    if result["regime_cash_target_pct"] is not None:
        print("               regime.py به‌تنهایی %.1f%% نقد می‌خواست"
              % result["regime_cash_target_pct"])

    print("\n  شواهد:")
    for line in result["evidence"]:
        print("    • %s" % line)

    print("\n  دلیل حالت:")
    for line in result["reasons"]:
        print("    • %s" % line)
