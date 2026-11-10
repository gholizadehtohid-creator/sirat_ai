"""ATR and ADX, bit-identical to the `ta` library (0.11.0), without its loops.

indicators.calculate_indicators spent half its time in ta's AverageTrueRange
and ADXIndicator (2026-11-06 profile): their Wilder smoothing is a Python
loop that reads one pandas element per step -- `series.iloc[i]`,
`series[window + i]` -- at ~10 us a read, for every bar of 818 symbols on
every scan.

This is a port, not a reimplementation. The vectorised parts (true range,
directional movement, the seed sums and means) call the SAME pandas/numpy
operations ta calls, and the recursions run the same IEEE-754 double
arithmetic in the same order on plain floats. The result is equal bit for
bit, NaN for NaN, including ta's quirks: the zero warm-up values instead of
NaN, the last smoothed element left at 0, and the DI series shifted by
`window`. tests/test_fast_ta.py holds that equality against ta itself; if
ta is upgraded, that test is what says whether this still matches.

ONE DELIBERATE DEPARTURE: a series too short for the seed. ta writes its
first ATR at index window - 1 and its first ADX `window` places into a series
already shortened by window - 1, so it raises (IndexError, or ValueError for
a negative array size) below `window` bars for ATR and below 2 * window for
ADX. Inside a whole-market scan that exception is not one name's problem: it
escapes top_technical / best_technical_inflow / gold_list and the route
answers 500 for every name, because one fresh listing made the list. Here
those lengths return all-NaN instead -- "not enough bars for this
indicator", the same absent column calculate_indicators already writes for a
series with no high/low range, which every caller handles. Every length ta
can compute is untouched.
"""

import numpy as np
import pandas as pd
from ta.trend import _get_min_max
from ta.utils import IndicatorMixin


def _undefined(close, name):
    """All-NaN on close's index: too few bars for the indicator to exist."""

    return pd.Series(np.nan, index=close.index, dtype=float, name=name)


def average_true_range(high, low, close, window=14):
    """ta.volatility.AverageTrueRange(...).average_true_range()."""

    # ta raises here: its seed is written at index window - 1.
    if len(close) < window:
        return _undefined(close, "atr")

    close_shift = close.shift(1)
    true_range = IndicatorMixin._true_range(high, low, close_shift)

    tr = true_range.to_numpy(dtype=float).tolist()
    atr = np.zeros(len(close))
    atr[window - 1] = true_range[0:window].mean()

    previous = float(atr[window - 1])
    for i in range(window, len(atr)):
        previous = (previous * (window - 1) + tr[i]) / float(window)
        atr[i] = previous

    return pd.Series(pd.Series(data=atr, index=true_range.index), name="atr")


def _smooth(seed, values, window, length):
    """ta's directional smoothing: out[i] = out[i-1] - out[i-1]/w + v[w+i]."""

    out = np.zeros(length)
    out[0] = seed
    previous = float(seed)
    for i in range(1, length - 1):
        previous = previous - (previous / float(window)) + values[window + i]
        out[i] = previous
    return out


def adx(high, low, close, window=14):
    """(adx, adx_pos, adx_neg) of ta.trend.ADXIndicator, as three Series."""

    if window == 0:
        raise ValueError("window may not be 0")

    # ta raises below this: the smoothed series is len - (window - 1) long and
    # its first ADX is written at index `window` of it.
    if len(close) < 2 * window:
        return (_undefined(close, "adx"), _undefined(close, "adx_pos"),
                _undefined(close, "adx_neg"))

    close_shift = close.shift(1)
    pdm = _get_min_max(high, close_shift, "max")
    pdn = _get_min_max(low, close_shift, "min")
    diff_directional_movement = pdm - pdn

    length = len(close) - (window - 1)

    trs = _smooth(diff_directional_movement.dropna().iloc[0:window].sum(),
                  diff_directional_movement.reset_index(drop=True)
                  .to_numpy(dtype=float).tolist(), window, length)

    diff_up = high - high.shift(1)
    diff_down = low.shift(1) - low
    pos = abs(((diff_up > diff_down) & (diff_up > 0)) * diff_up)
    neg = abs(((diff_down > diff_up) & (diff_down > 0)) * diff_down)

    dip = _smooth(pos.dropna().iloc[0:window].sum(),
                  pos.reset_index(drop=True).to_numpy(dtype=float).tolist(),
                  window, length)
    din = _smooth(neg.dropna().iloc[0:window].sum(),
                  neg.reset_index(drop=True).to_numpy(dtype=float).tolist(),
                  window, length)

    with np.errstate(divide="ignore", invalid="ignore"):
        # adx(): the DI over the whole smoothed range, then the DX.
        nonzero = trs != 0
        di_plus = np.where(nonzero, 100 * (dip / trs), 0.0)
        di_minus = np.where(nonzero, 100 * (din / trs), 0.0)
        total = di_plus + di_minus
        directional_index = np.where(
            total != 0, 100 * np.abs((di_plus - di_minus) / total), 0.0)

        adx_values = np.zeros(length)
        adx_values[window] = directional_index[0:window].mean()
        previous = float(adx_values[window])
        dx = directional_index.tolist()
        for i in range(window + 1, length):
            previous = ((previous * (window - 1)) + dx[i - 1]) / float(window)
            adx_values[i] = previous

        # adx_pos() / adx_neg(): indices 1 .. length-2, placed `window` later.
        inner = np.arange(1, length - 1)
        plus = np.zeros(len(close))
        minus = np.zeros(len(close))
        plus[inner + window] = np.where(
            trs[inner] != 0, 100 * (dip[inner] / trs[inner]), 0)
        minus[inner + window] = np.where(
            trs[inner] != 0, 100 * (din[inner] / trs[inner]), 0)

    adx_series = pd.Series(
        data=np.concatenate((np.zeros(window - 1), adx_values), axis=0),
        index=close.index)

    return (pd.Series(adx_series, name="adx"),
            pd.Series(pd.Series(plus, index=close.index), name="adx_pos"),
            pd.Series(pd.Series(minus, index=close.index), name="adx_neg"))
