"""The Market Exposure Engine — how much of the market to own, and when.

WHY THIS EXISTS
---------------
V2 measured, on 120 unseen decision dates, that choosing ten names by Alpha Core
produced a CAGR at the 20th percentile of thirty random rankings. Selection
added nothing. The one lever that moved every result in that programme was how
much capital was in the market at all: the regime brake cost 12 points of
return, exposure explained a third of the hold-period effect, and every arm that
held more, earned more.

So this module answers a different question from anything in V2:

    not   which shares
    but   should we be invested, and how much

It is deliberately the dumbest thing that could answer it.

FOUR VOTES, NO FITTED NUMBERS
-----------------------------
    trend        the equal-weight index is above its own 200-session mean
    momentum     the index's 60-session return is positive
    breadth      more than half the market is above its own slow mean
    volatility   20-session realised volatility is at or below its own
                 one-year median

Each vote is 0 or 1 and each is worth the same. Exposure is 25% per vote, so the
engine can only ever say 0%, 25%, 50%, 75% or 100%.

EVERY THRESHOLD IS A NATURAL CONSTANT, and that is the whole design. Zero for a
return. One half for a breadth share. A series' own median for its own
volatility. A 200-session mean, a 60-session return and a 20-session volatility
are the conventional windows, chosen before any result was seen and never moved
after. There is no parameter here that could have been fitted, which is what
makes the out-of-sample claim meaningful rather than decorative.

POINT-IN-TIME BY CONSTRUCTION
-----------------------------
Every function takes the history UP TO AND INCLUDING the decision session and
reads nothing else. There is no `as_of` filtering inside, because there is
nothing to filter: a caller that hands it a future bar has already broken the
rule, and `test_exposure_engine.py` asserts that truncating the series at the
decision date changes no answer.

WHAT THIS IS NOT
----------------
Not a forecast, not a probability, and not tuned. If a market read has value,
four obvious votes on conventional windows should show some of it. If they show
nothing, the hypothesis that exposure timing adds value is the thing that
failed -- not the cleverness of the indicator.
"""

import math

import numpy as np
import pandas as pd


# The votes, in a fixed order so a reported state is always readable the same
# way. Adding a fifth would change the exposure grid, so the list is the spec.
SIGNALS = ("trend", "momentum", "breadth", "volatility")

# Conventional windows. Chosen a priori; never moved after a result was seen.
TREND_WINDOW = 200          # sessions in the long mean
MOMENTUM_WINDOW = 60        # sessions in the return
VOLATILITY_WINDOW = 20      # sessions in the realised volatility
VOLATILITY_REFERENCE = 240  # sessions of volatility history the median uses

# Exposure per vote. Four votes, so the ladder is 0/25/50/75/100.
STEP_PCT = 25.0

# Natural thresholds, not fitted values.
MOMENTUM_FLOOR = 0.0        # a positive return
BREADTH_FLOOR = 50.0        # more than half the market


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series(closes):
    """A close series as a float list, oldest first, NaNs dropped."""

    if closes is None:
        return []

    if hasattr(closes, "tolist"):
        closes = closes.tolist()

    out = []

    for value in closes:
        number = _finite(value)
        if number is not None:
            out.append(number)

    return out


# ==============================
# THE FOUR READINGS
# ==============================

def trend(closes, window=TREND_WINDOW):
    """Is the index above its own long mean? None when the history is short."""

    values = _series(closes)

    if len(values) < window:
        return None, {"reason": "need %d sessions, have %d"
                                % (window, len(values))}

    mean = sum(values[-window:]) / float(window)

    if mean <= 0:
        return None, {"reason": "non-positive mean"}

    gap = (values[-1] - mean) / mean * 100.0

    return values[-1] > mean, {"close": round(values[-1], 2),
                               "mean": round(mean, 2),
                               "gap_pct": round(gap, 2)}


def momentum(closes, window=MOMENTUM_WINDOW, floor=MOMENTUM_FLOOR):
    """Is the index's own return over `window` sessions positive?"""

    values = _series(closes)

    if len(values) <= window:
        return None, {"reason": "need %d sessions, have %d"
                                % (window + 1, len(values))}

    past = values[-1 - window]

    if past <= 0:
        return None, {"reason": "non-positive base"}

    change = (values[-1] - past) / past * 100.0

    return change > floor, {"return_pct": round(change, 2)}


def breadth(above_pct, floor=BREADTH_FLOOR):
    """Is more than half the market above its own slow mean?

    `above_pct` is computed by the caller from the same point-in-time readings
    the rest of the system uses, because breadth is a property of the universe
    rather than of the index and this module never touches the universe.
    """

    value = _finite(above_pct)

    if value is None:
        return None, {"reason": "no breadth reading"}

    return value > floor, {"above_pct": round(value, 2)}


def volatility(closes, window=VOLATILITY_WINDOW,
               reference=VOLATILITY_REFERENCE):
    """Is realised volatility at or below its own one-year median?

    Compared against the series' OWN history rather than a fixed percentage,
    because a volatility level that is calm for this market would be a crisis
    in another one, and a constant would be a fitted number wearing a
    theoretical hat.
    """

    values = _series(closes)

    if len(values) < window + reference + 1:
        return None, {"reason": "need %d sessions, have %d"
                                % (window + reference + 1, len(values))}

    frame = pd.Series(values)

    returns = frame.pct_change()

    realised = returns.rolling(window).std() * math.sqrt(240) * 100.0

    current = _finite(realised.iloc[-1])

    history = realised.iloc[-reference:].dropna()

    if current is None or history.empty:
        return None, {"reason": "volatility not computable"}

    median = float(history.median())

    return current <= median, {"realised_pct": round(current, 2),
                               "median_pct": round(median, 2)}


# ==============================
# THE STATE, AND THE EXPOSURE
# ==============================

def state(index_closes, above_pct=None):
    """All four votes for one decision session.

    `index_closes` must end at the decision session. Nothing here looks past
    the last element, which is what makes the point-in-time guarantee checkable
    rather than asserted.
    """

    votes = {}
    detail = {}

    votes["trend"], detail["trend"] = trend(index_closes)
    votes["momentum"], detail["momentum"] = momentum(index_closes)
    votes["breadth"], detail["breadth"] = breadth(above_pct)
    votes["volatility"], detail["volatility"] = volatility(index_closes)

    return {"votes": votes, "detail": detail}


def target_exposure(reading):
    """Votes -> a target exposure in percent, on the 0/25/50/75/100 ladder.

    A MISSING VOTE IS NOT A NO. An indicator that cannot be computed carries no
    information either way, and scoring it as bearish would make a short history
    look like a falling market -- which is how a backtest ends up holding cash
    through its own warm-up and calling that risk management. Missing votes are
    dropped and the remaining ones are scaled up, so the ladder keeps its
    meaning. With nothing computable at all the engine returns None and the
    caller decides; this module will not invent a number.
    """

    votes = (reading or {}).get("votes") or {}

    present = [value for value in votes.values() if value is not None]

    if not present:
        return None, {"reason": "no signal was computable",
                      "votes_carried": 0, "available": 0}

    carried = sum(1 for value in present if value)

    exposure = 100.0 * carried / float(len(present))

    # Back onto the ladder, so the output stays discrete and explainable.
    exposure = round(exposure / STEP_PCT) * STEP_PCT

    # `votes_carried`, not `votes`: exposure_for() merges this dict beside the
    # vote MAP, and two different things under one key is how a count silently
    # replaced the map it was supposed to summarise.
    return float(min(100.0, max(0.0, exposure))), {
        "votes_carried": carried,
        "available": len(present),
        "missing": [name for name, value in votes.items() if value is None],
    }


def exposure_for(index_closes, above_pct=None):
    """The whole engine in one call: history in, target exposure out."""

    reading = state(index_closes, above_pct=above_pct)

    exposure, why = target_exposure(reading)

    return exposure, {"votes": reading["votes"], "detail": reading["detail"],
                      **why}
