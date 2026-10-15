"""Can you actually buy it, and has the move already happened?

THE DEFECT THIS MODULE EXISTS TO CLOSE
---------------------------------------
The scanner's premise is one sentence: money is entering WHILE THE PRICE HAS
NOT YET MOVED. Everything else is corroboration.

Until this module existed, "the price has not yet moved" was implemented as
`position within the 120-session range` -- and that is a statement about where
the price stands relative to its own past, not about whether it is moving NOW. A
name that fell 60% and is bouncing violently off the floor sits in the bottom
third of its range while being the exact opposite of quiet accumulation.

Measured on the validation session, all three surviving candidates had already
run:

    پارس    +9.1% over 10 sessions        سخوز   +15.0%,  band-close 6 of 20
    حشکوه   +13.8%,  band-close 6 of 20

and across the 231 names that cleared MFI>70, the MEDIAN 10-session return was
+19.0% against a liquid-universe median of +11.0%. MFI above 70 on this market
does not find quiet names. It finds names that have already run.

The user's observation closed the loop: two of the three were later sitting in a
صف فروش. That is the natural end of a violent bounce -- retail queues in, the
bounce fails, and the queue flips to the sell side. E21 measured the same thing
from the other direction and this programme has it on record: the flow signal
predicts QUEUE LOCKS, not returns.

TWO GATES, MEASURING TWO DIFFERENT THINGS
------------------------------------------
    locks()     CAN you trade it? A session where high == low and the price
                moved by the band is a queue: everyone is on one side and the
                other side is empty. E14's detector, reused unchanged.

    advance()   IS the move already over? The name's trailing return against
                the LIQUID UNIVERSE'S OWN MEDIAN, not against a fixed number.

WHY THE ADVANCE TEST IS MARKET-RELATIVE AND NOT AN ABSOLUTE PERCENTAGE
-----------------------------------------------------------------------
A fixed threshold breaks in exactly one direction and this scanner already
demonstrated it. On the validation session every liquid name was up a median
11% over ten sessions, so a fixed "+8% or less" would have returned almost
nothing -- not because no name was quiet, but because the market was loud. That
is the same mechanism that collapsed 2026 breadth to 2.9 candidates per session
while 2024 ran at 28.9.

Against the market's own median, "quiet" keeps meaning quiet in a rally and in a
crash. A name that merely matched the market has not been paid for its inflow
yet, which is the condition the whole scanner is looking for.
"""

import math

import numpy as np
import pandas as pd

import config


# E14's thresholds, cited rather than re-chosen. experiment_14_queue_artifact.py
# separates three things a careless reading pools together: a queue lock (the
# price moved by the band with no range), a stale bar (nothing traded), and a
# reopening gap (the only trade of the day happens far from yesterday's close).
# Only the first is a queue.
LOCK_MIN_RETURN = 1.0

# Above this it is a reopening, not a band. E14's own comment: a "locked" bar
# returning 115% on average is a symbol reopening after a suspension.
LOCK_MAX_RETURN = 12.0

# Sessions the advance is measured over. Two trading weeks -- long enough that
# one strong session cannot trip it, short enough that it describes the move
# happening now rather than one that finished a quarter ago.
ADVANCE_WINDOW = 10

# How far above the market's own median the name may still be and count as
# quiet.
#
# ONE DAILY BAND. Tehran's ordinary band is +/-3%, so this says: over two
# trading weeks the name may have outperformed its peers by at most a single
# session's maximum move. Beyond that the market has already re-priced it
# relative to everything else, which is precisely the event this scanner is
# trying to precede.
#
# The anchor is the exchange's own microstructure rather than a round number,
# and deliberately not a value chosen by looking at which names it excludes.
# For the record of what it does exclude: in the MFI>70 pool the median name
# outperforms by +8.0pp over this window, so most of that pool has already run.
ADVANCE_MARGIN_PCT = config.PRICE_BAND_PCT

# Sessions examined for band behaviour.
#
# REPORTED, NOT GATED -- and that decision was measured, not assumed. Chronic
# band-closing looked like an obvious disqualifier: two of the three candidates
# on the validation session closed at the band 6 times in 20. Then the
# distribution was checked, and the MEDIAN name in the eligible universe also
# closes at the band 6 times in 20 (MFI>70 pool: 7). Gating on it would reject
# the median name of this market, which measures the market's daily band and
# not the candidate.
#
# So the count is surfaced on every row as context and decides nothing. A
# reader who wants to avoid queue-prone names can see the number; the scanner
# does not pretend it is evidence.
BAND_WINDOW = 20


def _finite(value):
    if value is None:
        return None

    try:
        out = float(value)
    except (TypeError, ValueError):
        return None

    return out if math.isfinite(out) else None


def locks(frame, window=BAND_WINDOW):
    """Queue state now, and band behaviour lately. E14's detector, unchanged.

    A lock is `high == low` on a session that traded, whose predecessor also
    traded, and whose return is between the band floor and the reopening
    ceiling. All four conditions matter: without `traded` a suspended bar reads
    as a lock, without `traded_prev` a reopening does, and without the ceiling a
    post-suspension gap does.

    Returns None when the frame is too short to say anything. `locked_now` is
    the gate; everything else is context.
    """

    if frame is None or len(frame) < 3:
        return None

    for column in ("high", "low", "close", "volume"):
        if column not in frame.columns:
            return None

    tail = frame.tail(window + 1)

    close = pd.to_numeric(tail["close"], errors="coerce")
    high = pd.to_numeric(tail["high"], errors="coerce")
    low = pd.to_numeric(tail["low"], errors="coerce")
    volume = pd.to_numeric(tail["volume"], errors="coerce").fillna(0)

    ret = close.pct_change() * 100.0

    traded = volume > 0
    traded_prev = volume.shift(1) > 0
    no_range = high == low

    locked_up = (no_range & traded & traded_prev
                 & (ret > LOCK_MIN_RETURN) & (ret <= LOCK_MAX_RETURN))
    locked_down = (no_range & traded & traded_prev
                   & (ret < -LOCK_MIN_RETURN) & (ret >= -LOCK_MAX_RETURN))

    # Closing at the session high on a band-sized up move: not a full lock, but
    # the shape of a name where the buyers ran out of sellers before the close.
    at_band = ((close == high) & traded
               & (ret > LOCK_MIN_RETURN) & (ret <= LOCK_MAX_RETURN))

    up_now = bool(locked_up.iloc[-1])
    down_now = bool(locked_down.iloc[-1])

    if up_now:
        reason = ("در آخرین جلسه صف خرید بوده — با قیمت عادی قابل خرید نیست")
    elif down_now:
        reason = ("در آخرین جلسه صف فروش بوده — عرضه سنگین، نه جمع‌آوری")
    else:
        reason = None

    return {
        "locked_now": up_now or down_now,
        "locked_up_now": up_now,
        "locked_down_now": down_now,
        "reason": reason,
        "locked_up_recent": int(locked_up.sum()),
        "locked_down_recent": int(locked_down.sum()),
        "band_closes_recent": int(at_band.sum()),
        "window": int(len(tail) - 1),
    }


def market_advance(frames, window=ADVANCE_WINDOW):
    """The liquid universe's own median trailing return -- the yardstick.

    Computed once per scan over whatever frames the caller has already loaded,
    so the benchmark and the candidates come from exactly the same population.
    Returns None when too few names can be measured to call anything a median.
    """

    values = []

    for frame in (frames or {}).values():
        value = trailing_return(frame, window)
        if value is not None:
            values.append(value)

    if len(values) < 30:
        # A median over a handful of names is not a market reading. Better to
        # have no yardstick -- and to say so -- than a confident wrong one.
        return None

    return float(np.median(values))


def trailing_return(frame, window=ADVANCE_WINDOW):
    """Close-to-close return over `window` sessions, in percent, or None."""

    if frame is None or "close" not in frame.columns or len(frame) <= window:
        return None

    close = pd.to_numeric(frame["close"], errors="coerce")

    now = _finite(close.iloc[-1])
    then = _finite(close.iloc[-1 - window])

    if now is None or then is None or then <= 0:
        return None

    return (now / then - 1.0) * 100.0


def advance(frame, market=None, window=ADVANCE_WINDOW,
            margin=ADVANCE_MARGIN_PCT):
    """Has this name already made the move the scanner is trying to precede?

    `market` is the liquid universe's median trailing return over the same
    window, from market_advance(). Without it there is no verdict -- an absolute
    threshold would mean something different in every regime, which is the
    failure this function exists to avoid, so absence returns `quiet=None`
    rather than a guess.
    """

    own = trailing_return(frame, window)

    if own is None:
        return None

    if market is None:
        return {
            "return_pct": own,
            "market_pct": None,
            "excess_pct": None,
            "quiet": None,
            "reason": "میانه‌ی بازار برای مقایسه در دسترس نیست",
            "window": window,
        }

    excess = own - market

    quiet = excess <= margin

    return {
        "return_pct": own,
        "market_pct": market,
        "excess_pct": excess,
        "quiet": bool(quiet),
        "reason": None if quiet else (
            "در %d جلسه‌ی اخیر %+.1f%% بازده داشته، %+.1f%% بیشتر از میانه‌ی "
            "بازار (%+.1f%%) — حرکت شروع شده و این اسکنر دنبال قبل از حرکت است"
            % (window, own, excess, market)),
        "window": window,
        "margin": margin,
    }


# ==============================
# THE MARKET'S OWN STATE
# ==============================
#
# WHY A RELATIVE GATE IS NOT ENOUGH, AND HOW THAT WAS FOUND OUT
# --------------------------------------------------------------
# advance() asks whether a name has outrun THE MARKET. That is the right
# question in a normal market and the wrong one in a melt-up, because when
# everything has run, "it merely matched the market" still means the price is
# extended in absolute terms.
#
# Measured: on 2026-08-08, 51% of liquid names closed limit-up. On 2026-08-19,
# 15% did, against 0.5% limit-down, with the median name up 11% over ten
# sessions. Two candidates selected in that state matched the market (excess
# +0.3pp and -1.9pp) while being up 9.1% and 13.8% outright -- and three
# sessions later both had turned down and gone into a صف فروش.
#
# So the market's own state is measured and reported beside every run. A
# scanner looking for quiet accumulation has very little to say on a session
# where a sixth of the exchange is locked limit-up: whatever it finds, the tide
# is what lifted it.

# Share of liquid names locked limit-up above which the session is a melt-up:
# the market, not any name in it, is what a "money entering" reading is
# describing. 10% is well beyond the 2.4% median session of the stored panel.
MELT_UP_LOCK_PCT = 10.0

# The mirror, for a market in disorderly decline.
MELT_DOWN_LOCK_PCT = 10.0

CALM = "متعادل"
MELT_UP = "صعود شتابان"
MELT_DOWN = "ریزش شتابان"


def market_state(frames, window=ADVANCE_WINDOW):
    """What the market itself was doing on the session being scanned.

    Returns the share of the population in each queue, the median trailing
    return, and a regime label. `informative` is False when the market's own
    state dominates -- not a veto, a warning that the cross-section is being
    driven by the tide rather than by anything specific to a name.
    """

    if not frames:
        return None

    up = down = counted = 0
    returns = []

    for frame in frames.values():

        reading = locks(frame)

        if reading is None:
            continue

        counted += 1

        if reading["locked_up_now"]:
            up += 1
        if reading["locked_down_now"]:
            down += 1

        value = trailing_return(frame, window)
        if value is not None:
            returns.append(value)

    if counted < 30 or not returns:
        return None

    up_pct = 100.0 * up / counted
    down_pct = 100.0 * down / counted
    median = float(np.median(returns))

    if up_pct >= MELT_UP_LOCK_PCT:
        regime, informative = MELT_UP, False
        note = ("%.0f%% از نمادهای نقدشونده در صف خرید قفل‌اند و میانه‌ی بازده "
                "%d جلسه %+.1f%% است. در چنین جلسه‌ای «ورود پول» ویژگی بازار "
                "است نه نماد — هر چیزی که این اسکنر پیدا کند، جزر و مد آن را "
                "بالا برده." % (up_pct, window, median))
    elif down_pct >= MELT_DOWN_LOCK_PCT:
        regime, informative = MELT_DOWN, False
        note = ("%.0f%% از نمادهای نقدشونده در صف فروش قفل‌اند. «کف قیمتی» در "
                "این وضعیت هنوز کف نیست." % down_pct)
    else:
        regime, informative = CALM, True
        note = None

    return {
        "regime": regime,
        "informative": informative,
        "locked_up_pct": round(up_pct, 2),
        "locked_down_pct": round(down_pct, 2),
        "median_return_pct": round(median, 2),
        "window": window,
        "names": counted,
        "note": note,
    }
