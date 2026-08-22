"""Market impact. The model that closes a known falsity in every backtest.

WHAT WAS WRONG
--------------
Slippage has been reported as 0.0% in every result this programme has ever
produced -- not because trades were free, but because nothing computed it. A
zero that means "not measured" is indistinguishable in a report from a zero
that means "measured, and it was nothing", and the second reading is the one
every reader takes. This module exists so that the number is either real or
absent, never silently zero.

THE MODEL, AND WHERE THE NUMBERS COME FROM
-------------------------------------------
The square-root law of market impact:

    I / sigma_D  =  c * sqrt(Q / V_D)

    I        expected adverse price move over the execution
    sigma_D  the name's daily volatility
    Q        order size
    V_D      the name's daily volume
    c        a dimensionless prefactor

The prefactor is **IMPORTED, NOT CALIBRATED HERE**, and that distinction is the
most important thing in this file. It comes from a 2026 confirmation on a U.S.
large-cap that consumed a full Level-3 market-by-order tape -- 178 trading
days, roughly half a billion book events -- to reconstruct 1,012 metaorders:

    c_raw = 0.69   [0.63, 0.77]     uncorrected
    c_eff = 0.34                    bias-corrected; the default here

Nothing in this repository can reproduce that calibration. There is no
order-by-order feed, no aggressor sign and no mid-price -- only daily bars and
aggregated client-type counts. So `PREFACTOR_EFFECTIVE` is a borrowed constant
from a different market, and every result that uses it must say so and must
sweep it. `sensitivity()` exists to make that cheap rather than optional.

THREE REFUSALS
--------------
1. A LIMIT-LOCKED NAME GETS NO NUMBER. The square-root law rests on a diffusive,
   near-martingale price. A name pinned at its band has zero realised variance
   and unfillable demand, so the precondition fails by construction rather than
   approximately. `estimate` returns `executable=False` with a reason. It never
   returns a cost, and it never returns zero.

2. OUTSIDE THE CALIBRATED RANGE, THE ANSWER IS FLAGGED. The published bins span
   roughly 0.6% to 2.7% of daily volume. This platform's own participation cap
   is 5%, which is already ABOVE that range -- so the honest default is to
   return the extrapolated figure WITH `extrapolated=True` attached, not to
   pretend the curve was measured out there.

3. MISSING INPUTS ARE MISSING, NOT ZERO. No volatility, no volume, or a
   non-positive one, returns `executable=False`. Absence is not approval, and
   this is the direction the failure has always taken here.

WHAT THIS MODULE DOES NOT DO
-----------------------------
It does not touch commissions -- `config.ROUND_TRIP_PCT` owns those, and impact
is a separate cost that stacks on top. It does not decide whether an order
should be placed. And it changes no existing result on import: nothing calls it
until a registered experiment does, so that the effect of switching slippage on
is a measured delta rather than a silent revision.
"""

import math


# The published prefactors. Named rather than inlined so that a result which
# used one can be read back and identified.
PREFACTOR_RAW = 0.69

PREFACTOR_RAW_CI = (0.63, 0.77)

PREFACTOR_EFFECTIVE = 0.34

# The participation band the published bins actually cover, recovered from the
# reported non-dimensional impacts of 0.026 and 0.056 at c_eff = 0.34:
#
#     0.026 = 0.34 * sqrt(Q/V)  ->  Q/V ~ 0.0058
#     0.056 = 0.34 * sqrt(Q/V)  ->  Q/V ~ 0.0271
#
# Outside this, the square-root form is an extrapolation. It is a well-supported
# functional form, so extrapolating is reasonable -- but it must be labelled.
CALIBRATED_MIN_PARTICIPATION = 0.006

CALIBRATED_MAX_PARTICIPATION = 0.027

# A sweep wide enough to answer "does the conclusion depend on the prefactor?"
# rather than to decorate a report with three points that all agree.
SENSITIVITY_PREFACTORS = (0.17, 0.34, 0.51, 0.69)

# ---- the volatility sanity bound, and why it is not a tuned number ---------
#
# E22 charged impact for the first time and read 56-76% of price across an
# entire 550-name cross-section on five sessions in June 2022. The cause was
# not the market: 252 rows in `price_history` carry returns above 200%, the
# largest +19,976,600%, which is a prior close of 1 rial -- new listings,
# post-suspension reopenings and unadjusted capital increases. One such row
# sits inside a 20-session trailing window for twenty sessions and dominates
# every basket drawn on those dates.
#
# The bound below is E14's LOCK_MAX_RETURN, cited rather than reinvented.
# `annotate` has classified bars beyond it as `extreme_gap` -- reopenings, not
# band moves -- since it was written, so the platform already held the
# knowledge this estimator was failing to use. It is a physical ceiling on a
# band-limited market rather than a threshold chosen to make a result pass: a
# name moving the full band in alternating directions every session is the
# theoretical maximum, and it lands here.
#
# It was added AFTER E22 rejected. E22 is not re-run under it -- repairing
# data after seeing a verdict and re-measuring is the move this platform
# exists to refuse, and it stays refused when the repair is obviously correct.
EXTREME_MOVE_PCT = 12.0

MAX_PLAUSIBLE_DAILY_VOLATILITY_PCT = 12.0


class ImpactEstimate(object):
    """One estimate, carrying why it is or is not usable.

    A bare float could not distinguish "0.0% because the order is tiny" from
    "0.0% because nothing was computed", which is the exact confusion this
    module was written to end.
    """

    __slots__ = ("cost_pct", "participation", "sigma_pct", "prefactor",
                 "executable", "extrapolated", "reason")

    def __init__(self, cost_pct=None, participation=None, sigma_pct=None,
                 prefactor=None, executable=False, extrapolated=False,
                 reason=""):
        self.cost_pct = cost_pct
        self.participation = participation
        self.sigma_pct = sigma_pct
        self.prefactor = prefactor
        self.executable = executable
        self.extrapolated = extrapolated
        self.reason = reason

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self):
        if not self.executable:
            return "<ImpactEstimate NOT EXECUTABLE: %s>" % self.reason
        return ("<ImpactEstimate %.4f%% at %.3f%% of ADV, c=%.2f%s>"
                % (self.cost_pct, 100.0 * self.participation, self.prefactor,
                   ", EXTRAPOLATED" if self.extrapolated else ""))


def _positive(value):
    """A strictly positive finite float, or None. NaN is not a number here."""

    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number) or number <= 0:
        return None

    return number


def estimate(order_value, adv_value, daily_volatility_pct, locked=False,
             prefactor=PREFACTOR_EFFECTIVE):
    """Expected one-way impact, in percent of the traded price.

    `order_value` and `adv_value` are in the same currency unit -- rials here,
    though the model only ever sees their ratio. `daily_volatility_pct` is the
    name's own daily volatility in percent, and must be a decision-time figure:
    a volatility estimated over the window being traded is the return in
    disguise.

    `locked` is the caller's decision-time microstructure state, from E14's
    detector. Passing it is not optional politeness -- a locked name is where
    this model is not merely imprecise but inapplicable.
    """

    if locked:
        return ImpactEstimate(
            executable=False,
            reason="limit-locked: price is pinned at the band, so the "
                   "diffusive-price precondition of the square-root law fails "
                   "by construction. This is unfillable inventory, not an "
                   "expensive fill.")

    order = _positive(order_value)
    adv = _positive(adv_value)
    sigma = _positive(daily_volatility_pct)
    c = _positive(prefactor)

    if order is None:
        return ImpactEstimate(executable=False,
                              reason="order value missing or non-positive")

    if adv is None:
        return ImpactEstimate(
            executable=False,
            reason="no average daily value: a name with no measured volume "
                   "has no measurable participation, and guessing one would "
                   "invent the very number this module exists to stop faking")

    if sigma is None:
        return ImpactEstimate(executable=False,
                              reason="no decision-time daily volatility")

    if sigma > MAX_PLAUSIBLE_DAILY_VOLATILITY_PCT:
        # Not a volatile name -- a contaminated one. On a band-limited market
        # the theoretical maximum daily volatility is the band itself, so a
        # reading above it means the return series contains a bar the market
        # never printed. See the constant's note: E22 found 252 such rows.
        return ImpactEstimate(
            executable=False,
            reason="daily volatility %.1f%% exceeds the %.1f%% band ceiling: "
                   "the return series contains a reopening, an unadjusted "
                   "capital increase or a placeholder price, not a volatile "
                   "name. Refusing rather than charging an impact no market "
                   "could produce."
                   % (sigma, MAX_PLAUSIBLE_DAILY_VOLATILITY_PCT))

    if c is None:
        return ImpactEstimate(executable=False,
                              reason="prefactor missing or non-positive")

    participation = order / adv

    cost = c * sigma * math.sqrt(participation)

    extrapolated = bool(participation < CALIBRATED_MIN_PARTICIPATION
                        or participation > CALIBRATED_MAX_PARTICIPATION)

    reason = ""

    if extrapolated:
        reason = ("participation %.3f%% lies outside the published calibration "
                  "band %.1f%%-%.1f%%; the functional form is extrapolated"
                  % (100.0 * participation,
                     100.0 * CALIBRATED_MIN_PARTICIPATION,
                     100.0 * CALIBRATED_MAX_PARTICIPATION))

    return ImpactEstimate(cost_pct=cost, participation=participation,
                          sigma_pct=sigma, prefactor=c, executable=True,
                          extrapolated=extrapolated, reason=reason)


def round_trip(order_value, adv_value, daily_volatility_pct,
               locked_on_entry=False, locked_on_exit=False,
               prefactor=PREFACTOR_EFFECTIVE):
    """Impact paid twice: once getting in, once getting out.

    Returned as a dict rather than a float because either leg can be
    inexecutable on its own, and collapsing that into a single number would
    lose the distinction between "expensive" and "impossible" -- which on this
    market is the distinction that decides everything.
    """

    entry = estimate(order_value, adv_value, daily_volatility_pct,
                     locked=locked_on_entry, prefactor=prefactor)

    exit_ = estimate(order_value, adv_value, daily_volatility_pct,
                     locked=locked_on_exit, prefactor=prefactor)

    executable = bool(entry.executable and exit_.executable)

    total = ((entry.cost_pct + exit_.cost_pct) if executable else None)

    return {
        "entry": entry,
        "exit": exit_,
        "executable": executable,
        "total_impact_pct": total,
        "extrapolated": bool(entry.extrapolated or exit_.extrapolated),
    }


def sensitivity(order_value, adv_value, daily_volatility_pct,
                prefactors=SENSITIVITY_PREFACTORS):
    """The same order under every plausible prefactor.

    The prefactor is borrowed from another market, so any conclusion that holds
    at c = 0.34 and fails at c = 0.69 is a conclusion about the constant rather
    than about the strategy. This makes that visible in one call.
    """

    out = {}

    for c in prefactors:
        result = estimate(order_value, adv_value, daily_volatility_pct,
                          prefactor=c)
        out[c] = result.cost_pct if result.executable else None

    return out


def daily_volatility_pct(closes, window=20, min_periods=10,
                         max_move_pct=EXTREME_MOVE_PCT):
    """Trailing daily volatility in percent, from a name's own closes.

    The series must END at the decision session -- this function does not shift
    for the caller, because a shift applied in two places cancels and a shift
    applied in neither is lookahead. The caller owns the alignment and this
    docstring is where that is stated.

    Returns beyond `max_move_pct` are DROPPED rather than winsorised. E14 calls
    them `extreme_gap` and treats them as reopenings rather than band moves;
    they describe a corporate action or a placeholder price, so they are not
    observations of this name's volatility at all. Winsorising would keep them
    at the boundary and still bias the estimate upward; dropping says what is
    true, which is that there was no usable observation that session.

    Returns None rather than 0.0 when there is not enough history. A name with
    nine observations has no volatility estimate; reporting zero for it would
    make it look like the safest name in the universe.
    """

    values = [_positive(c) for c in closes]
    values = [v for v in values if v is not None]

    if len(values) < min_periods + 1:
        return None

    if window is not None:
        values = values[-(window + 1):]

    returns = [(values[i] / values[i - 1] - 1.0) * 100.0
               for i in range(1, len(values))]

    if max_move_pct is not None:
        returns = [r for r in returns if abs(r) <= max_move_pct]

    if len(returns) < min_periods:
        return None

    mean = sum(returns) / len(returns)

    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)

    return math.sqrt(variance)
