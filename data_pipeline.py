"""One frame builder for every v2 decision. Live, paper and backtest come here.

WHY THIS FILE EXISTS
--------------------
The three v2 callers each built their own input frame, and the two steps they
disagreed on were invisible in every test that passed:

    live / paper    database.load_history_by_inscode(400)  ->  indicators
    backtest        pit bars (back-adjusted, full)         ->  indicators over
                                                               EVERYTHING, then
                                                               sliced to 400

MEASURED on 2026-08-11 over 749 shared names, that produced the same decision
for 21.8% of them. Not because the engines disagreed -- they are the same
functions -- but because they were shown different numbers. Two causes, both
data:

  CORPORATE ACTIONS. A capital increase drops the quoted price overnight and
  nothing in the feed marks it. backtesting/pit.py removes those gaps with
  back_adjust(); the live path did not, so any name with an increase inside the
  120-session momentum window scored differently in the two places. Names whose
  most recent adjustment fell OUTSIDE that window matched exactly, which is how
  the cause was identified rather than guessed.

  INDICATOR WARM-UP. ATR, RSI and the MACD line are recursive: their value on a
  given bar depends on how many bars came before. Computing over a full history
  and slicing the tail is NOT the same as computing over the tail, and two names
  with byte-identical bars still produced different stops.

So the frame is built in exactly one place, and the two steps that used to
differ are the two this module owns.

WHAT IT DOES NOT OWN
--------------------
No indicator formula, no factor, no threshold. calculate_indicators() is
unchanged and is called with the same params it always was. This module decides
WHICH BARS go in, and nothing about what comes out of them.

THE ONE DIFFERENCE THAT REMAINS, STATED RATHER THAN HIDDEN
----------------------------------------------------------
A backtest standing on a past date reads bars that pit.py adjusted using the
whole series, so a capital increase that happened AFTER that date has already
been scaled into them. That is the standard back-adjusted convention and it is
what keeps the backtest's decision prices consistent with its own fill prices --
but it means a past date's frame is not bit-identical to what a live run would
have seen on the day. On the newest session there is no "after", so the three
paths agree exactly, which is the comparison this module is measured on.
"""

import config
from backtest import back_adjust
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars


# Bars every v2 decision is computed over. THE standard, referenced by all three
# callers rather than repeated in each. 400 is what the live path always used,
# so adopting it changes the backtest's warm-up rather than the live numbers
# every measured result came from.
WINDOW_BARS = 400


def minimum_bars(params=None):
    """Fewest bars a frame needs before any v2 reading is meaningful."""

    params = params or DEFAULT_PARAMS

    return max(required_bars(params), max(config.MOMENTUM_HORIZONS) + 1)


def load_bars(ins_code, limit=None):
    """Raw stored bars for one instrument, newest `limit` of them.

    Bounded on purpose, and safe to bound: back_adjust() anchors at the NEWEST
    bar and scales backwards, so adjusting a trailing window gives the same
    numbers as adjusting the whole series and slicing it. VERIFIED on 396
    symbols, 231 of which carry an adjustment: zero difference, not a small one.
    """

    import database

    return database.load_history_by_inscode(
        ins_code, limit=WINDOW_BARS if limit is None else limit)


def frame(ins_code=None, bars=None, as_of=None, params=None,
          window=WINDOW_BARS, adjusted=False):
    """The indicator frame a v2 decision is made from. None when unusable.

        ins_code   loaded from the store when `bars` is not supplied
        bars       a caller's own frame -- the backtest passes point-in-time
                   bars it has already had adjusted
        as_of      keep only bars up to and including this date
        adjusted   True when the caller's bars have already been through
                   back_adjust(), so it is not applied twice

    The order is fixed and matters: adjust, then cut, then window, then compute.
    Computing before windowing is what made the backtest's ATR differ from the
    live one on identical bars.
    """

    params = params or DEFAULT_PARAMS

    if bars is None:

        if ins_code is None:
            return None

        bars = load_bars(ins_code)

    if bars is None or len(bars) == 0:
        return None

    if not adjusted:
        from backtest import known_dividends
        bars, _events = back_adjust(
            bars, dividends=known_dividends(ins_code) if ins_code else None)

    if as_of is not None:

        import pandas as pd

        stamps = pd.to_datetime(bars["date"])

        cut = int(stamps.searchsorted(pd.Timestamp(as_of), side="right"))

        if cut <= 0:
            return None

        bars = bars.iloc[:cut]

    if window:
        bars = bars.tail(int(window))

    if len(bars) < minimum_bars(params) + 1:
        return None

    try:
        return calculate_indicators(bars.reset_index(drop=True), params)
    except Exception:
        return None
