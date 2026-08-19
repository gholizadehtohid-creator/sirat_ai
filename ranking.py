"""Composite 0-100 scoring model.

Two correctness fixes over the previous version:

  * NaN guard. Indicators need up to 50 bars to warm up. Any comparison with
    NaN is False, so an unwarmed bar fell through every elif chain and scored
    as "Bearish / MACD Bearish / Momentum Negative" -- a confident-looking 0
    that was really "no data yet". Those bars are now rejected explicitly.
  * The returned rsi/macd values were round(nan, 2) in that case, which is
    still nan and rendered as "nan" downstream.

Also returns a per-component breakdown so the panel can show where a score
came from rather than just the total.
"""

import math

import pandas as pd

from config import (
    RSI_OVERBOUGHT,
    STATUS_BANDS,
    WEIGHT_MACD,
    WEIGHT_MOMENTUM,
    WEIGHT_RISK,
    WEIGHT_RSI,
    WEIGHT_TREND,
    WEIGHT_VOLUME,
)


REQUIRED_COLUMNS = [
    "close", "RSI", "MACD", "MACD_SIGNAL",
    "MACD_HIST", "MA_FAST", "MA_SLOW", "volume", "VOLUME_MA"
]


def _is_valid(value):
    return value is not None and not pd.isna(value) and math.isfinite(value)


def _no_trade(reason, rsi=None, macd=None, trend="Invalid Data", scored=False):
    """Uniform rejection payload so callers never see NaN.

    `scored` is False on every one of these: the 0 is a placeholder for a
    score that does not exist, not a verdict that the stock scored badly.
    Averaging it into a combined score turns "we cannot say" into "we say
    zero", which is exactly the confusion screening.py's INSUFFICIENT_DATA
    verdict exists to prevent on the fundamental side.
    """

    return {
        "score": 0,
        "scored": scored,
        "report": f"DATA WARNING: {reason}\n\nFINAL STATUS: NO TRADE",
        "rsi": round(rsi, 2) if _is_valid(rsi) else None,
        "macd": round(macd, 2) if _is_valid(macd) else None,
        "trend": trend,
        "volume": 0,
        "momentum": 0,
        "status": "NO TRADE",
        "signals": [f"Data: {reason}"],
        "breakdown": {
            "Trend": 0,
            "MACD": 0,
            "RSI": 0,
            "Volume": 0,
            "Momentum": 0,
            "Risk": 0
        }
    }


def rank_stocks(df):
    """Score the most recent bar of `df`."""

    if df is None or df.empty:
        return _no_trade("No price data")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]

    if missing:
        return _no_trade(f"Missing columns: {', '.join(missing)}")

    last = df.iloc[-1]

    last_close = last["close"]
    last_rsi = last["RSI"]
    last_macd = last["MACD"]
    last_signal = last["MACD_SIGNAL"]
    last_hist = last["MACD_HIST"]
    ma_fast = last["MA_FAST"]
    ma_slow = last["MA_SLOW"]
    volume = last["volume"]
    volume_ma = last["VOLUME_MA"]

    # Indicators have not warmed up yet -- refuse rather than score NaN.
    unwarmed = [
        name
        for name, value in [
            ("close", last_close),
            ("RSI", last_rsi),
            ("MACD", last_macd),
            ("MACD signal", last_signal),
            ("fast MA", ma_fast),
            ("slow MA", ma_slow),
        ]
        if not _is_valid(value)
    ]

    if unwarmed:
        return _no_trade(
            f"Indicators not warmed up ({', '.join(unwarmed)})",
            rsi=last_rsi,
            macd=last_macd
        )

    # A zero-volume last bar is not corrupt data: TSETMC emits a row for every
    # session an instrument is listed, and a halted or simply untraded name
    # carries its frozen close at zero volume. A third of the bars in a
    # whole-market fetch look like this. There is no current signal to read,
    # but nothing is wrong with the series either -- say which it is.
    if not _is_valid(volume) or volume <= 0:
        return _no_trade(
            "Did not trade in the most recent session",
            rsi=last_rsi, macd=last_macd, trend="Not Traded"
        )

    signals = []

    breakdown = {
        "Trend": 0,
        "MACD": 0,
        "RSI": 0,
        "Volume": 0,
        "Momentum": 0,
        "Risk": 0
    }

    # =========================
    # MOMENTUM INPUTS
    # =========================

    price_5 = df["close"].iloc[-6] if len(df) >= 6 else last_close
    price_10 = df["close"].iloc[-11] if len(df) >= 11 else last_close

    momentum_5 = (
        ((last_close - price_5) / price_5) * 100
        if _is_valid(price_5) and price_5 > 0 else 0
    )

    momentum_10 = (
        ((last_close - price_10) / price_10) * 100
        if _is_valid(price_10) and price_10 > 0 else 0
    )

    # =========================
    # TREND - 25 POINTS
    # =========================

    if last_close > ma_fast and ma_fast > ma_slow:
        breakdown["Trend"] = WEIGHT_TREND
        trend = "Strong Uptrend"
        signals.append("Trend: Strong Uptrend")

    elif last_close > ma_fast:
        breakdown["Trend"] = round(WEIGHT_TREND * 0.72)
        trend = "Uptrend"
        signals.append("Trend: Above fast MA")

    elif last_close > ma_slow:
        breakdown["Trend"] = round(WEIGHT_TREND * 0.32)
        trend = "Recovery"
        signals.append("Trend: Above slow MA")

    else:
        trend = "Bearish"
        signals.append("Trend: Bearish")

    # =========================
    # MACD - 20 POINTS
    # =========================

    hist_positive = _is_valid(last_hist) and last_hist > 0

    if last_macd > last_signal and hist_positive:
        breakdown["MACD"] = WEIGHT_MACD
        signals.append("MACD: Strong Bullish")

    elif last_macd > last_signal:
        breakdown["MACD"] = round(WEIGHT_MACD * 0.6)
        signals.append("MACD: Bullish")

    elif hist_positive:
        breakdown["MACD"] = round(WEIGHT_MACD * 0.3)
        signals.append("MACD: Improving")

    else:
        signals.append("MACD: Bearish")

    # =========================
    # RSI - 15 POINTS
    # =========================

    if 45 <= last_rsi <= 60:
        breakdown["RSI"] = WEIGHT_RSI
        signals.append("RSI: Healthy")

    elif 60 < last_rsi < 68:
        breakdown["RSI"] = round(WEIGHT_RSI * 0.8)
        signals.append("RSI: Strong")

    elif 40 <= last_rsi < 45:
        breakdown["RSI"] = round(WEIGHT_RSI * 0.67)
        signals.append("RSI: Recovering")

    elif last_rsi < 30:
        breakdown["RSI"] = round(WEIGHT_RSI * 0.53)
        signals.append("RSI: Oversold")

    elif last_rsi >= RSI_OVERBOUGHT:
        signals.append("RSI: Overbought Risk")

    else:
        breakdown["RSI"] = round(WEIGHT_RSI * 0.33)
        signals.append("RSI: Neutral")

    # =========================
    # VOLUME - 15 POINTS
    # =========================

    if _is_valid(volume_ma) and volume_ma > 0:

        volume_ratio = volume / volume_ma

        if volume_ratio >= 1.5:
            breakdown["Volume"] = WEIGHT_VOLUME
            signals.append("Volume: Strong")

        elif volume_ratio >= 1.2:
            breakdown["Volume"] = round(WEIGHT_VOLUME * 0.8)
            signals.append("Volume: Above Average")

        elif volume_ratio >= 0.8:
            breakdown["Volume"] = round(WEIGHT_VOLUME * 0.47)
            signals.append("Volume: Normal")

        else:
            breakdown["Volume"] = round(WEIGHT_VOLUME * 0.13)
            signals.append("Volume: Weak")

    else:
        signals.append("Volume: No baseline")

    # =========================
    # MOMENTUM - 15 POINTS
    # =========================

    if momentum_5 >= 3 and momentum_10 >= 5:
        breakdown["Momentum"] = WEIGHT_MOMENTUM
        signals.append("Momentum: Strong")

    elif momentum_5 > 0 and momentum_10 > 0:
        breakdown["Momentum"] = round(WEIGHT_MOMENTUM * 0.67)
        signals.append("Momentum: Positive")

    elif momentum_10 > -5:
        breakdown["Momentum"] = round(WEIGHT_MOMENTUM * 0.33)
        signals.append("Momentum: Neutral")

    else:
        signals.append("Momentum: Negative")

    # =========================
    # RISK CONTROL - 10 POINTS
    # =========================

    if 45 <= last_rsi <= 65:
        breakdown["Risk"] = WEIGHT_RISK
        signals.append("Risk: Favorable")

    elif last_rsi < RSI_OVERBOUGHT:
        breakdown["Risk"] = round(WEIGHT_RISK * 0.5)
        signals.append("Risk: Moderate")

    else:
        signals.append("Risk: High")

    # =========================
    # FINAL DECISION
    # =========================

    score = max(0, min(sum(breakdown.values()), 100))

    status = next(
        label
        for floor, label in STATUS_BANDS
        if score >= floor
    )

    # The top band additionally requires RSI to be out of overbought territory.
    if status == STATUS_BANDS[0][1] and last_rsi >= RSI_OVERBOUGHT:
        status = STATUS_BANDS[1][1]

    report = "\n".join(signals) + "\n\nFINAL STATUS: " + status

    return {
        "score": score,
        "scored": True,
        "report": report,
        "rsi": round(float(last_rsi), 2),
        "macd": round(float(last_macd), 2),
        "trend": trend,
        "volume": int(volume),
        "momentum": round(float(momentum_10), 2),
        "status": status,
        "signals": signals,
        "breakdown": breakdown
    }
