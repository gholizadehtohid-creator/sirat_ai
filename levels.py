"""Price levels: where the market has actually traded, not where ATR lands.

WHY THIS EXISTS
---------------
Every exit in this project is a multiple of volatility. A target is
entry + MIN_RISK_REWARD x STOP_ATR_MULTIPLE x ATR, which knows nothing about
whether a three-month high sits half a percent above it. On an exchange with a
+/-3% band that matters more than it would elsewhere: supply parked at a
remembered level is not absorbed in one session, it caps the move for days.

So this module answers one question -- how much clear air is above the price
-- from several independent constructions, because each draws its levels a
different way and none of them is obviously right.

WHAT IS HERE, AND WHAT IS HONESTLY NOT
--------------------------------------
  swings()      pivot highs and lows. Deterministic: a bar is a pivot high if
                nothing within `depth` bars either side traded higher.
  fibonacci()   retracements and extensions of the last completed swing.
  ichimoku()    the five lines. Note the cloud is NOT forward-looking -- both
                spans are computed from past prices and merely PLOTTED ahead,
                so a value 26 sessions out is fully determined today. It is
                included because it draws levels a different way, not because
                it forecasts.
  structure()   the alternating impulse/correction read that wave counting is
                built on.

Deliberately absent: an Elliott wave count. A chart admits several valid
counts and practitioners disagree on live data, so an automated label would
be an opinion wearing the costume of a measurement. structure() exposes the
part that is reproducible -- leg direction, extent and retracement depth --
and leaves the naming to the reader.

NOTHING HERE IS A SIGNAL YET. It produces levels; whether headroom to them
predicts anything is a separate question, measured elsewhere.
"""

import math
import warnings

import numpy as np
import pandas as pd


# Retracement ratios. 0.5 is not a Fibonacci ratio and is included because it
# is watched anyway -- the levels that matter are the ones people act on.
RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)

# Projection ratios beyond the swing, used for targets rather than support.
EXTENSIONS = (1.272, 1.618, 2.0, 2.618)

# Bars either side of a pivot that must not exceed it.
DEFAULT_DEPTH = 5


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# SWINGS
# ==============================

def swings(df, depth=DEFAULT_DEPTH):
    """Alternating pivot highs and lows, oldest first.

    A pivot is confirmed only `depth` bars after it forms, and this returns
    confirmed pivots only -- an unconfirmed one is a guess about bars that
    have not happened, which is exactly the look-ahead this project keeps
    finding in its own code.
    """

    if df is None or len(df) < depth * 2 + 1:
        return []

    frame = df.reset_index(drop=True)

    highs = frame["high"] if "high" in frame.columns else frame["close"]
    lows = frame["low"] if "low" in frame.columns else frame["close"]
    dates = frame["date"] if "date" in frame.columns else pd.Series(frame.index)

    # Vectorised over sliding windows. The loop this replaces sliced pandas
    # per bar -- three million .iloc calls for one v2 scan of the market,
    # two-thirds of the three minutes /api/portfolio took (2026-11-05).
    # Same rule, same NaN handling: the window extreme skips NaN (as
    # Series.max did) and the centre must itself be finite.
    high_values = pd.to_numeric(highs, errors="coerce").to_numpy(dtype=float)
    low_values = pd.to_numeric(lows, errors="coerce").to_numpy(dtype=float)
    width = depth * 2 + 1
    centre = slice(depth, len(frame) - depth)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN windows
        window_high = np.nanmax(
            np.lib.stride_tricks.sliding_window_view(high_values, width), axis=1)
        window_low = np.nanmin(
            np.lib.stride_tricks.sliding_window_view(low_values, width), axis=1)

    high = high_values[centre]
    low = low_values[centre]
    is_high = np.isfinite(high) & (high >= window_high)
    is_low = ~is_high & np.isfinite(low) & (low <= window_low)

    found = []

    for k in np.flatnonzero(is_high | is_low):
        i = int(k) + depth
        if is_high[k]:
            found.append({"index": i, "date": dates.iloc[i],
                          "price": float(high[k]), "kind": "high"})
        else:
            found.append({"index": i, "date": dates.iloc[i],
                          "price": float(low[k]), "kind": "low"})

    # Collapse consecutive pivots of the same kind, keeping the extreme one.
    # Two highs in a row is not a swing, it is one swing sampled twice.
    cleaned = []

    for pivot in found:
        if cleaned and cleaned[-1]["kind"] == pivot["kind"]:
            better = (pivot["price"] > cleaned[-1]["price"]
                      if pivot["kind"] == "high"
                      else pivot["price"] < cleaned[-1]["price"])
            if better:
                cleaned[-1] = pivot
            continue
        cleaned.append(pivot)

    return cleaned


def last_swing(df, depth=DEFAULT_DEPTH):
    """The most recent completed low->high or high->low move."""

    points = swings(df, depth)

    if len(points) < 2:
        return None

    start, end = points[-2], points[-1]

    return {
        "from": start["price"],
        "to": end["price"],
        "direction": "up" if end["kind"] == "high" else "down",
        "from_date": start["date"],
        "to_date": end["date"],
        "span_bars": end["index"] - start["index"],
    }


# ==============================
# FIBONACCI
# ==============================

def fibonacci(df, depth=DEFAULT_DEPTH):
    """Retracement and extension levels of the last completed swing."""

    swing = last_swing(df, depth)

    if not swing:
        return None

    low = min(swing["from"], swing["to"])
    high = max(swing["from"], swing["to"])

    size = high - low

    if size <= 0:
        return None

    levels = {}

    # Retracements are measured back from the end of the move, so an upswing
    # retraces downward and a downswing retraces upward.
    for ratio in RETRACEMENTS:
        if swing["direction"] == "up":
            levels[f"retr_{ratio}"] = high - size * ratio
        else:
            levels[f"retr_{ratio}"] = low + size * ratio

    for ratio in EXTENSIONS:
        if swing["direction"] == "up":
            levels[f"ext_{ratio}"] = low + size * ratio
        else:
            levels[f"ext_{ratio}"] = high - size * ratio

    return {"swing": swing, "levels": levels}


# ==============================
# ICHIMOKU
# ==============================

def ichimoku(df, tenkan=9, kijun=26, senkou_b=52):
    """The five lines, as of the last bar.

    The spans are returned as their computed values, NOT shifted forward.
    Shifting is a drawing convention; the number is knowable today either way,
    and pretending otherwise is how a lagging construction gets mistaken for a
    forecast.
    """

    if df is None or len(df) < senkou_b:
        return None

    frame = df.reset_index(drop=True)

    highs = frame["high"] if "high" in frame.columns else frame["close"]
    lows = frame["low"] if "low" in frame.columns else frame["close"]
    closes = frame["close"]

    def midpoint(window):
        high = _finite(highs.tail(window).max())
        low = _finite(lows.tail(window).min())
        if high is None or low is None:
            return None
        return (high + low) / 2

    tenkan_sen = midpoint(tenkan)
    kijun_sen = midpoint(kijun)
    span_b = midpoint(senkou_b)

    span_a = ((tenkan_sen + kijun_sen) / 2
              if tenkan_sen is not None and kijun_sen is not None else None)

    close = _finite(closes.iloc[-1])

    cloud_top = cloud_bottom = None

    if span_a is not None and span_b is not None:
        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

    return {
        "tenkan": tenkan_sen,
        "kijun": kijun_sen,
        "senkou_a": span_a,
        "senkou_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "above_cloud": (close > cloud_top
                        if close is not None and cloud_top is not None
                        else None),
        "close": close,
    }


# ==============================
# STRUCTURE
# ==============================

def structure(df, depth=DEFAULT_DEPTH, legs=6):
    """The alternating leg sequence wave counting is built on.

    Reports each recent leg's direction, size and how deeply it retraced the
    one before it. A retracement under ~0.5 with legs making progress in one
    direction is what impulsive structure looks like; deep alternating
    retracements are what corrective structure looks like. The labels are left
    to the reader on purpose -- see the module docstring.
    """

    points = swings(df, depth)

    if len(points) < 3:
        return None

    recent = points[-(legs + 1):]

    out = []

    for i in range(1, len(recent)):

        start, end = recent[i - 1], recent[i]

        size = end["price"] - start["price"]

        retrace = None

        if i >= 2:
            previous = recent[i - 2]["price"] - start["price"]
            if previous:
                retrace = abs(size / previous)

        out.append({
            "direction": "up" if size > 0 else "down",
            "from": start["price"],
            "to": end["price"],
            "size_pct": (size / start["price"] * 100) if start["price"] else None,
            "retraced": round(retrace, 3) if retrace is not None else None,
            "bars": end["index"] - start["index"],
        })

    same_direction = sum(1 for leg in out if leg["direction"] == out[-1]["direction"])

    return {
        "legs": out,
        "current": out[-1]["direction"] if out else None,
        "legs_counted": len(out),
        "trend_legs": same_direction,
        # Shallow retracements with progress = impulsive; deep = corrective.
        "mean_retracement": round(
            sum(l["retraced"] for l in out if l["retraced"] is not None)
            / max(1, sum(1 for l in out if l["retraced"] is not None)), 3
        ),
    }


# ==============================
# HEADROOM -- THE MEASURABLE QUESTION
# ==============================

def overhead(df, price=None, depth=DEFAULT_DEPTH):
    """Distance from price to the nearest level above it, per construction.

    This is the number the rest of the system can actually use: not "is the
    chart bullish" but "how far can this run before it meets something".
    """

    if df is None or df.empty:
        return None

    frame = df.reset_index(drop=True)

    price = _finite(price if price is not None else frame["close"].iloc[-1])

    if price is None or price <= 0:
        return None

    def gap(level):
        level = _finite(level)
        if level is None or level <= price:
            return None
        return (level - price) / price * 100

    candidates = {}

    # ---- prior pivot highs -------------------------------------------------
    pivots = [p["price"] for p in swings(frame, depth) if p["kind"] == "high"]

    above = [p for p in pivots if p > price]
    candidates["pivot"] = gap(min(above)) if above else None

    # ---- rolling highs -----------------------------------------------------
    #
    # The current bar is excluded. Its own high sits above its own close by
    # construction, so including it reports every share as having roughly
    # half a percent of headroom -- a level the price touched today is not
    # resistance, it is where the price already is.
    highs = frame["high"] if "high" in frame.columns else frame["close"]

    past = highs.iloc[:-1]

    for window in (60, 120, 250):
        if len(past) >= window:
            candidates[f"high_{window}"] = gap(past.tail(window).max())

    # ---- fibonacci ---------------------------------------------------------
    fib = fibonacci(frame, depth)

    if fib:
        fib_above = [v for v in fib["levels"].values()
                     if _finite(v) is not None and v > price]
        candidates["fib"] = gap(min(fib_above)) if fib_above else None

    # ---- ichimoku ----------------------------------------------------------
    cloud = ichimoku(frame)

    if cloud:
        for key in ("cloud_top", "cloud_bottom", "kijun"):
            value = cloud.get(key)
            if value is not None and value > price:
                candidates[key] = gap(value)

    live = [v for v in candidates.values() if v is not None]

    return {
        "price": price,
        "by_source": candidates,
        # The binding one: whichever wall is closest is the one that caps the
        # move, whoever drew it.
        "nearest_pct": min(live) if live else None,
        "sources": len(live),
    }
