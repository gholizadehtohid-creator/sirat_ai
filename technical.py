"""Single-instrument technical read, for the dashboard's Technical panel.

WHY A NEW MODULE RATHER THAN A ROUTE BODY
------------------------------------------
Every indicator here already exists: `indicators.calculate_indicators` produces
MA, RSI, MACD, Bollinger + %B, ATR/ADX, OBV and a trend label. Nothing is
recomputed. What was missing is a path that takes ONE symbol and returns its
latest reading in a shape the browser can render. That is all this module is --
lookup, load, read the last row, and serialise. No new indicator, no scoring,
no BUY/SELL. The dashboard decides how to show it; this only reports.

POINT-IN-TIME IS NOT CLAIMED HERE
----------------------------------
This is a LIVE dashboard read of the latest bars, not a research surface. It
uses whatever `price_history` holds now. It must never be wired into a
backtest, a holdout, or an experiment -- the governed research path loads its
own data through the PIT layer. Keeping this separate is the point.
"""

import math

import database
import indicators


# The fields worth surfacing as "current reading" numbers, mapped to the
# column names calculate_indicators writes. Anything absent is reported as
# null, never as zero -- a missing RSI is not an RSI of 0.
LATEST_FIELDS = (
    ("close", "close"),
    ("rsi", "RSI"),
    ("macd", "MACD"),
    ("macd_signal", "MACD_SIGNAL"),
    ("macd_hist", "MACD_HIST"),
    ("ma_fast", "MA_FAST"),
    ("ma_slow", "MA_SLOW"),
    ("bb_upper", "BB_UPPER"),
    ("bb_lower", "BB_LOWER"),
    ("bb_pct", "BB_PCT"),
    ("bb_width", "BB_WIDTH"),
    ("atr", "ATR"),
    ("atr_pct", "ATR_PCT"),
    ("adx", "ADX"),
    ("obv", "OBV"),
    ("volume", "volume"),
    ("volume_ma", "VOLUME_MA"),
    ("trend", "TREND"),
)

# How many bars of history to hand back for a sparkline. Enough to see shape,
# not so many the payload bloats.
SERIES_BARS = 120


def _finite(value):
    """A JSON-safe number, or None. NaN and inf are not numbers to a browser."""

    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        # A trend label is a string; pass non-numerics through untouched.
        return value

    return number if math.isfinite(number) else None


def resolve_symbol(symbol):
    """Map a Persian symbol (نماد) to its insCode, or return None.

    Matches symbol_fa exactly first, then case-folded, so "وبملت" resolves
    whether or not the caller normalised it. Returns the full instrument dict
    so the caller can echo the name back.
    """

    if not symbol:
        return None

    wanted = str(symbol).strip()

    instruments = database.load_instruments()

    for row in instruments:
        if (row.get("symbol_fa") or "").strip() == wanted:
            return row

    folded = wanted.casefold()

    for row in instruments:
        if (row.get("symbol_fa") or "").strip().casefold() == folded:
            return row

    return None


def analyse(symbol, params=None, bars=SERIES_BARS):
    """The technical reading for one symbol.

    Returns a dict with the resolved instrument, the latest indicator values,
    and a short OHLC+indicator series for charting. On any failure it returns
    a dict carrying an `error` key rather than raising, so the route can pass
    it straight to jsonify.
    """

    instrument = resolve_symbol(symbol)

    if instrument is None:
        return {"error": "unknown symbol", "symbol": symbol}

    ins_code = instrument["ins_code"]

    params = indicators.clean_params(params)

    # Load enough history to warm the slowest indicator, plus the display
    # window. required_bars knows the longest lookback the params imply.
    need = indicators.required_bars(params) + bars

    history = database.load_history_by_inscode(ins_code, limit=need)

    if history is None or history.empty:
        return {"error": "no price history", "symbol": symbol,
                "ins_code": ins_code}

    frame = indicators.calculate_indicators(history, params)

    if frame.empty:
        return {"error": "not enough bars for indicators", "symbol": symbol,
                "ins_code": ins_code, "bars_loaded": int(len(history))}

    last = frame.iloc[-1]

    latest = {}
    for out_name, column in LATEST_FIELDS:
        latest[out_name] = _finite(last[column]) if column in frame.columns \
            else None

    # A short tail for the sparkline. Only columns the chart needs, and every
    # value passed through _finite so the JSON never carries NaN.
    tail = frame.tail(bars)

    series_cols = ("date", "close", "MA_FAST", "MA_SLOW",
                   "BB_UPPER", "BB_LOWER", "RSI", "MACD", "MACD_SIGNAL")

    series = []
    for _, row in tail.iterrows():
        point = {}
        for column in series_cols:
            if column == "date":
                value = row.get("date")
                point["date"] = (value.strftime("%Y-%m-%d")
                                 if value is not None and not _is_nat(value)
                                 else None)
            else:
                point[column.lower()] = _finite(row.get(column))
        series.append(point)

    return {
        "symbol": instrument.get("symbol_fa"),
        "name": instrument.get("name_fa"),
        "ins_code": ins_code,
        "sector": instrument.get("sector_name"),
        "bars_used": int(len(frame)),
        "as_of": series[-1]["date"] if series else None,
        "params": params,
        "latest": latest,
        "series": series,
    }


def _is_nat(value):
    try:
        return value != value          # NaT != NaT is True
    except Exception:                  # noqa: BLE001
        return False
