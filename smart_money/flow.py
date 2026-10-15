"""The Money Flow Index, and the two things about it that are not the number.

MFI is RSI with volume in it: the same up-days-against-down-days arithmetic,
but each session weighted by typical price x volume instead of counted as one.
That difference is the whole reason it is here. Price momentum says the market
moved; money-flow momentum says how much money it took to move it, and a name
absorbing money without moving is the object this scanner exists to find.

Three readings come out, and only the first is the textbook one:

    value        MFI on the last bar, 0-100.
    rising       whether it has climbed over the recent sessions, and by how
                 much. A flat 74 and a 74 that was 41 a week ago are the same
                 number describing opposite situations.
    divergence   price making a LOWER low while MFI makes a HIGHER low. This
                 is the accumulation tell -- sellers are still setting the
                 price, and buyers are no longer following them down.

WHY MFI IS COMPUTED HERE AND NOT IN indicators.py
--------------------------------------------------
indicators.calculate_indicators() builds the frame every backtest, the screener
and both decision engines read. Adding a column to it changes the warm-up
length `required_bars` computes and the params key every cached endpoint is
keyed on -- for the benefit of one scanner. MFI is this scanner's indicator, so
it lives with the scanner. `ta` is the same library indicators.py uses, so the
two cannot drift on what an RSI-family calculation means.
"""

import math

import pandas as pd
from ta.volume import MFIIndicator


# The standard window. Deliberately not exposed as a tunable: a scanner whose
# threshold (70) is fixed by the brief and whose window is free is a scanner
# with one knob left to turn until the list looks good.
MFI_WINDOW = 14

# Sessions over which "MFI is rising" is judged. A week of trading -- long
# enough that one strong session cannot manufacture the reading, short enough
# that it still describes now.
RISE_SESSIONS = 5

# Divergence lookback: two halves of this window are compared, so each half is
# ~20 sessions, about a month. Shorter, and every wobble is a divergence.
DIVERGENCE_WINDOW = 40

# Bars needed before MFI means anything: the window itself plus the divergence
# halves, so neither reading is built out of the indicator's own warm-up.
MIN_BARS = DIVERGENCE_WINDOW + MFI_WINDOW


def _finite(value):
    """A float, or None. NaN is not a number here.

    Same rule as moneyflow._value, kept identical so a missing reading looks
    the same everywhere in this project.
    """

    if value is None:
        return None

    try:
        out = float(value)
    except (TypeError, ValueError):
        return None

    return out if math.isfinite(out) else None


def series(frame, window=MFI_WINDOW):
    """The MFI series for a price frame, or None when it cannot be computed.

    Requires genuine high/low/volume. A frame carrying close as all three --
    which is what a close-only source looks like after a careless reindex --
    would give a typical price equal to the close and a money flow that is just
    volume-weighted RSI. That is a different indicator wearing this one's name,
    so it is refused rather than returned.
    """

    if frame is None or len(frame) < MIN_BARS:
        return None

    for column in ("high", "low", "close", "volume"):
        if column not in frame.columns:
            return None

    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")

    # A whole window of high == low == close has no range for a typical price
    # to differ from, and a whole window of zero volume has nothing to weight
    # it by. Both describe a name suspended for the entire window.
    if (high - low).abs().tail(window).sum() <= 0:
        return None

    if volume.tail(window).fillna(0).sum() <= 0:
        return None

    try:
        return MFIIndicator(
            high=high, low=low, close=close, volume=volume, window=window
        ).money_flow_index()
    except Exception:                                           # noqa: BLE001
        # ta raises on shapes this project should never produce, but a scanner
        # that dies on one malformed name out of six hundred is worse than one
        # that reports that name as unreadable and carries on.
        return None


def divergence(closes, mfi, window=DIVERGENCE_WINDOW):
    """Positive MFI divergence over `window` sessions, or None.

    Compares the lowest close of the recent half against the lowest close of
    the prior half, and MFI at those same two bars. Price lower, MFI higher, is
    a positive divergence: the second bottom cost the sellers more money to
    make than the first one did.

    Returns both bottoms so the reader can check the claim against the chart --
    "divergence detected" with no numbers attached is an assertion, not
    evidence.
    """

    if closes is None or mfi is None:
        return None

    if len(closes) < window or len(mfi) < window:
        return None

    tail_close = closes.tail(window).reset_index(drop=True)
    tail_mfi = mfi.tail(window).reset_index(drop=True)

    half = window // 2

    prior = tail_close.iloc[:half]
    recent = tail_close.iloc[half:]

    if prior.dropna().empty or recent.dropna().empty:
        return None

    i_prior = int(prior.idxmin())
    i_recent = int(recent.idxmin())

    price_prior = _finite(tail_close.iloc[i_prior])
    price_recent = _finite(tail_close.iloc[i_recent])
    mfi_prior = _finite(tail_mfi.iloc[i_prior])
    mfi_recent = _finite(tail_mfi.iloc[i_recent])

    if None in (price_prior, price_recent, mfi_prior, mfi_recent):
        return None

    if price_prior <= 0:
        return None

    positive = price_recent < price_prior and mfi_recent > mfi_prior

    return {
        "positive": bool(positive),
        "price_prior": price_prior,
        "price_recent": price_recent,
        "price_change_pct": (price_recent - price_prior) / price_prior * 100.0,
        "mfi_prior": mfi_prior,
        "mfi_recent": mfi_recent,
        "mfi_change": mfi_recent - mfi_prior,
    }


def read(frame, window=MFI_WINDOW, rise_sessions=RISE_SESSIONS):
    """Everything this module knows about one name's money flow.

    Returns None when the series cannot be built -- an absent reading, never a
    zero. Zero is a real MFI value (nothing but down-sessions in the window),
    and using it for "no data" would file the most sold-off names in the market
    alongside the ones nobody has any data for.
    """

    mfi = series(frame, window=window)

    if mfi is None:
        return None

    value = _finite(mfi.iloc[-1])

    if value is None:
        return None

    earlier = None

    if len(mfi) > rise_sessions:
        earlier = _finite(mfi.iloc[-1 - rise_sessions])

    change = None if earlier is None else value - earlier

    closes = pd.to_numeric(frame["close"], errors="coerce")

    return {
        "value": value,
        "window": window,
        "change": change,
        "rise_sessions": rise_sessions,
        # "Rising" is a strict climb, not "not falling". A reading that has
        # gone sideways for a week is not momentum and must not be scored as
        # if it were.
        "rising": bool(change is not None and change > 0),
        "divergence": divergence(closes, mfi),
    }
