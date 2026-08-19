"""Technical indicators.

Windows are parameters, not constants, so the panel can vary them. That means
the moving-average columns cannot be named after their length any more (an
"MA20" holding a 35-bar average is a trap), so they are MA_FAST / MA_SLOW and
the actual windows travel alongside in the returned params dict.

Also note: the previous version called reset_index(drop=True) after dropna,
which threw away the index. backtest.py then read df.index[i] as the trade
date and silently recorded row numbers. `date` is kept as a real column here.
"""

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator

from config import (
    ADX_WINDOW,
    ATR_WINDOW,
    BB_STD,
    BB_WINDOW,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    MA_FAST,
    MA_SLOW,
    RSI_WINDOW,
    VOLUME_MA,
    WINDOW_MAX,
    WINDOW_MIN,
    min_bars,
)


NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]

DEFAULT_PARAMS = {
    "ma_fast": MA_FAST,
    "ma_slow": MA_SLOW,
    "rsi_window": RSI_WINDOW,
    "macd_fast": MACD_FAST,
    "macd_slow": MACD_SLOW,
    "macd_signal": MACD_SIGNAL,
    "volume_ma": VOLUME_MA,
    "atr_window": ATR_WINDOW,
    "adx_window": ADX_WINDOW,
    "bb_window": BB_WINDOW,
}

# ATR, ADX and the Bollinger %B all need a real high/low range. An index series
# carries one; some cached equity frames do not. Rather than feed close-as-high
# into an average TRUE range -- which would return an average of zeros and make
# every stop infinitely tight -- these columns are simply absent, and callers
# check for them.
RANGE_COLUMNS = ["ATR", "ATR_PCT", "ADX", "DI_PLUS", "DI_MINUS"]


def clean_params(raw=None):
    """Clamp user-supplied windows into a sane range and keep them ordered."""

    params = dict(DEFAULT_PARAMS)

    for key, value in (raw or {}).items():

        if key not in params or value is None:
            continue

        try:
            number = int(value)
        except (TypeError, ValueError):
            continue

        params[key] = max(WINDOW_MIN, min(WINDOW_MAX, number))

    # A "fast" average longer than -- or equal to -- the slow one inverts every
    # trend rule. Equality is the subtler half: ranking.py's top band requires
    # `ma_fast > ma_slow`, which two identical windows can never satisfy, so
    # "Strong Uptrend" silently becomes unreachable rather than wrong.
    params["ma_fast"], params["ma_slow"] = _order(
        params["ma_fast"], params["ma_slow"]
    )

    params["macd_fast"], params["macd_slow"] = _order(
        params["macd_fast"], params["macd_slow"]
    )

    return params


def _order(fast, slow):
    """Force fast < slow, staying inside [WINDOW_MIN, WINDOW_MAX].

    Raising the slow window is the fix to prefer -- it honours the fast window
    the caller asked for. Whether that is possible depends on FAST, not on
    slow: room above only exists while fast < WINDOW_MAX. Testing slow instead
    lets `min(WINDOW_MAX, fast + 1)` clamp straight back onto fast and return
    two equal windows, which is the state this function exists to prevent.
    """

    if fast < slow:
        return fast, slow

    if fast < WINDOW_MAX:
        return fast, fast + 1

    # fast sits on the ceiling: the only way to separate them is downwards.
    return max(WINDOW_MIN, WINDOW_MAX - 1), WINDOW_MAX


def required_bars(params):
    """Warm-up length implied by a params dict."""

    return min_bars(
        ma_slow=params["ma_slow"],
        macd_slow=params["macd_slow"],
        macd_signal=params["macd_signal"],
        volume_ma=params["volume_ma"],
        rsi_window=params["rsi_window"],
        atr_window=params.get("atr_window", ATR_WINDOW),
        adx_window=params.get("adx_window", ADX_WINDOW),
        bb_window=params.get("bb_window", BB_WINDOW),
    )


def calculate_indicators(df, params=None):
    """Attach indicator columns. Returns a new frame with a 0..n-1 index."""

    if df is None or df.empty:
        return pd.DataFrame()

    params = params if params else DEFAULT_PARAMS

    df = df.copy()

    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df.sort_values("date")

    df = df.dropna(subset=["close"]).reset_index(drop=True)

    if df.empty:
        return df

    df["MA_FAST"] = df["close"].rolling(window=params["ma_fast"]).mean()
    df["MA_SLOW"] = df["close"].rolling(window=params["ma_slow"]).mean()

    df["RSI"] = RSIIndicator(
        close=df["close"],
        window=params["rsi_window"]
    ).rsi()

    macd = MACD(
        close=df["close"],
        window_fast=params["macd_fast"],
        window_slow=params["macd_slow"],
        window_sign=params["macd_signal"]
    )

    df["MACD"] = macd.macd()
    df["MACD_SIGNAL"] = macd.macd_signal()
    df["MACD_HIST"] = macd.macd_diff()

    if "volume" in df.columns:
        df["VOLUME_MA"] = df["volume"].rolling(window=params["volume_ma"]).mean()
    else:
        df["volume"] = 0
        df["VOLUME_MA"] = 0

    _add_bollinger(df, params)
    _add_range_indicators(df, params)
    _add_obv(df)

    df["TREND"] = _classify_trend(df)

    return df


def _add_bollinger(df, params):
    """Bands plus %B -- where the close sits between them, 0 to 1."""

    window = params.get("bb_window", BB_WINDOW)

    bands = BollingerBands(
        close=df["close"],
        window=window,
        window_dev=BB_STD
    )

    df["BB_UPPER"] = bands.bollinger_hband()
    df["BB_LOWER"] = bands.bollinger_lband()
    df["BB_MID"] = bands.bollinger_mavg()

    width = df["BB_UPPER"] - df["BB_LOWER"]

    # A flat-lined share -- a whole window at one price -- has zero band width.
    # Dividing by it yields inf, which is not "at the top of the range"; it is
    # "there is no range". Leave those bars NaN.
    df["BB_PCT"] = (df["close"] - df["BB_LOWER"]).where(width > 0) / width.where(width > 0)

    # Band width relative to the middle band: the standard squeeze measure.
    df["BB_WIDTH"] = (width / df["BB_MID"].where(df["BB_MID"] > 0)) * 100


def _add_range_indicators(df, params):
    """ATR and ADX, which need a genuine high/low range to mean anything.

    Feeding close as both high and low would make every true range zero, and a
    zero ATR sizes an infinitely large position. When the range is missing the
    columns are simply absent and callers fall back to a percentage stop.
    """

    has_range = (
        "high" in df.columns
        and "low" in df.columns
        and df["high"].notna().any()
        and df["low"].notna().any()
        and (df["high"] > df["low"]).any()
    )

    if not has_range:
        for column in RANGE_COLUMNS:
            df[column] = float("nan")
        return

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=params.get("atr_window", ATR_WINDOW)
    )

    df["ATR"] = atr.average_true_range()

    # ATR as a percentage of price is the comparable form: 400 rials of daily
    # range means something entirely different on a 2,000-rial share than on a
    # 90,000-rial one, and position sizing compares across the whole market.
    df["ATR_PCT"] = (df["ATR"] / df["close"].where(df["close"] > 0)) * 100

    adx = ADXIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=params.get("adx_window", ADX_WINDOW)
    )

    df["ADX"] = adx.adx()
    df["DI_PLUS"] = adx.adx_pos()
    df["DI_MINUS"] = adx.adx_neg()


def _add_obv(df):
    """On-balance volume: cumulative volume signed by the day's direction."""

    if "volume" not in df.columns or not df["volume"].notna().any():
        df["OBV"] = float("nan")
        return

    df["OBV"] = OnBalanceVolumeIndicator(
        close=df["close"],
        volume=df["volume"].fillna(0)
    ).on_balance_volume()


def _classify_trend(df):
    """Label each bar against the two moving averages.

    Bars where the slow average has not warmed up stay "Unknown" rather than
    being lumped in with genuine downtrends -- a NaN comparison is False, so
    the old chained .loc assignments quietly classified the warm-up bars of
    every symbol as "Downtrend".
    """

    fast = df["MA_FAST"]
    slow = df["MA_SLOW"]
    close = df["close"]

    trend = pd.Series("Unknown", index=df.index, dtype="object")

    known = fast.notna() & slow.notna()

    above_fast = close > fast
    above_slow = close > slow

    trend[known & above_fast & above_slow] = "Strong Uptrend"
    trend[known & above_fast & ~above_slow] = "Uptrend"
    trend[known & ~above_fast & above_slow] = "Weak Downtrend"
    trend[known & ~above_fast & ~above_slow] = "Downtrend"

    return trend
