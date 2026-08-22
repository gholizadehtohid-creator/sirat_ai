"""Single-instrument technical read, for the dashboard's Technical panel.

WHY A NEW MODULE RATHER THAN A ROUTE BODY
------------------------------------------
Every indicator here already exists: `indicators.calculate_indicators` produces
MA, RSI, MACD, Bollinger + %B, ATR/ADX, OBV and a trend label; `levels.swings`
produces confirmed pivot highs and lows. Nothing is recomputed. What was
missing is a path that takes ONE symbol and returns its latest reading, its
support/resistance levels, and a technical call, in a shape the browser can
render.

THE TECHNICAL SIGNAL IS A TEXTBOOK READING, NOT A VALIDATED EDGE
----------------------------------------------------------------
The dashboard now asks for a BUY/SELL call from technicals alone. It is built
as a TRANSPARENT VOTE across the indicators -- every component's vote and
reason are returned, so the reader sees the mechanism, not a black box. But it
must be read for what it is:

    This programme's own research (E19-E23) established that on this market,
    technical signals of exactly this kind showed NO tradable edge net of the
    1.04% round trip. The signal here is what the textbooks say the indicators
    say. It is NOT a claim that acting on it makes money, and it carries a
    standing caveat that the panel surfaces.

That is why the call lives here, in a live dashboard read, and NOT in the
governed research path. It is a convenience for a human reading a chart, not a
strategy. It must never be wired into a backtest, a holdout, or an experiment.

POINT-IN-TIME IS NOT CLAIMED HERE
----------------------------------
This is a LIVE dashboard read of the latest bars. It uses whatever
`price_history` holds now. The governed research path loads its own data
through the PIT layer; keeping this separate is the point.
"""

import math

import database
import indicators
import levels


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


# ==============================
# SUPPORT / RESISTANCE
# ==============================

# How many levels of each kind to surface. Enough to see the ladder, not so
# many that distant, stale pivots crowd the reading.
LEVELS_EACH_SIDE = 3


def support_resistance(frame, price, depth=None):
    """Nearest supports below and resistances above the current price.

    Built from `levels.swings` -- confirmed pivot highs and lows, no
    look-ahead. A pivot high above the price is resistance; a pivot low below
    it is support. Each level carries its distance from the price in percent,
    because on a +/-3% band market "2% away" and "9% away" are different kinds
    of level entirely.
    """

    pivots = levels.swings(frame) if depth is None \
        else levels.swings(frame, depth=depth)

    price = _finite(price)

    if not pivots or price is None:
        return {"resistances": [], "supports": [], "price": price}

    resistances = []
    supports = []
    for p in pivots:
        lvl = _finite(p.get("price"))
        if lvl is None:
            continue
        date = p.get("date")
        try:
            date = date.strftime("%Y-%m-%d")
        except Exception:                                      # noqa: BLE001
            date = None
        entry = {"price": lvl, "date": date,
                 "distance_pct": round((lvl - price) / price * 100.0, 2)}
        if lvl > price:
            resistances.append(entry)
        elif lvl < price:
            supports.append(entry)

    # Nearest first: resistances just above, supports just below.
    resistances.sort(key=lambda e: e["price"])
    supports.sort(key=lambda e: e["price"], reverse=True)

    return {
        "price": price,
        "resistances": resistances[:LEVELS_EACH_SIDE],
        "supports": supports[:LEVELS_EACH_SIDE],
    }


# ==============================
# TECHNICAL SIGNAL -- a transparent vote, not a validated edge
# ==============================

# The standing caveat the panel must show. This programme's own research found
# no tradable edge from technical signals net of costs; this is a textbook
# reading, not a claim that acting on it makes money.
SIGNAL_CAVEAT = (
    "این یک خوانش تکنیکال کتاب‌درسی است، نه یک لبه‌ی اعتبارسنجی‌شده. تحقیق همین "
    "پلتفرم (E19 تا E23) نشان داد سیگنال‌های تکنیکال از این نوع، خالص از هزینه‌ی "
    "۱.۰۴٪ رفت‌وبرگشت، لبه‌ی قابل‌معامله‌ای نداشتند. رأی‌ها را ببینید، نه فقط "
    "نتیجه را."
)

BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"

SIGNAL_FA = {BUY: "خرید", SELL: "فروش", HOLD: "خنثی / نگهداری"}


def _vote(name, score, reason):
    """One indicator's vote: +1 bullish, -1 bearish, 0 neutral, with a reason."""

    return {"name": name, "vote": score, "reason": reason}


def technical_signal(latest, sr):
    """Aggregate the indicators into BUY / HOLD / SELL, showing every vote.

    Equal-weight sum of per-indicator votes. No fitting, no tuned thresholds --
    the cut points are the textbook ones (RSI 30/70, MACD vs signal, price vs
    MAs, %B 0.2/0.8), chosen from convention rather than from this data, so the
    call cannot be an artifact of tuning on the symbol being viewed.
    """

    votes = []

    close = latest.get("close")
    rsi = latest.get("rsi")
    macd = latest.get("macd")
    macd_signal = latest.get("macd_signal")
    ma_fast = latest.get("ma_fast")
    ma_slow = latest.get("ma_slow")
    bb_pct = latest.get("bb_pct")
    trend = latest.get("trend")
    adx = latest.get("adx")

    # --- trend: price vs the two moving averages ---
    if close is not None and ma_fast is not None and ma_slow is not None:
        if close > ma_fast > ma_slow:
            votes.append(_vote("روند (میانگین‌های متحرک)", +1,
                               "قیمت بالای هر دو میانگین، کوتاه بالای بلند"))
        elif close < ma_fast < ma_slow:
            votes.append(_vote("روند (میانگین‌های متحرک)", -1,
                               "قیمت زیر هر دو میانگین، کوتاه زیر بلند"))
        else:
            votes.append(_vote("روند (میانگین‌های متحرک)", 0,
                               "میانگین‌ها هم‌جهت نیستند"))

    # --- MACD vs its signal line ---
    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            votes.append(_vote("MACD", +1, "خط MACD بالای خط سیگنال"))
        elif macd < macd_signal:
            votes.append(_vote("MACD", -1, "خط MACD زیر خط سیگنال"))
        else:
            votes.append(_vote("MACD", 0, "MACD روی خط سیگنال"))

    # --- RSI: momentum, with overbought/oversold as the stronger read ---
    if rsi is not None:
        if rsi >= 70:
            votes.append(_vote("RSI", -1,
                               "اشباع خرید (%.0f) — احتمال اصلاح" % rsi))
        elif rsi <= 30:
            votes.append(_vote("RSI", +1,
                               "اشباع فروش (%.0f) — احتمال بازگشت" % rsi))
        elif rsi >= 55:
            votes.append(_vote("RSI", +1, "مومنتوم رو به بالا (%.0f)" % rsi))
        elif rsi <= 45:
            votes.append(_vote("RSI", -1, "مومنتوم رو به پایین (%.0f)" % rsi))
        else:
            votes.append(_vote("RSI", 0, "خنثی (%.0f)" % rsi))

    # --- Bollinger %B: where price sits in its band ---
    if bb_pct is not None:
        if bb_pct >= 0.8:
            votes.append(_vote("باند بولینگر", -1,
                               "نزدیک باند بالا (%.2f) — کشیده به بالا" % bb_pct))
        elif bb_pct <= 0.2:
            votes.append(_vote("باند بولینگر", +1,
                               "نزدیک باند پایین (%.2f) — کشیده به پایین" % bb_pct))
        else:
            votes.append(_vote("باند بولینگر", 0,
                               "میانه‌ی باند (%.2f)" % bb_pct))

    # --- proximity to the nearest support / resistance ---
    res = (sr or {}).get("resistances") or []
    sup = (sr or {}).get("supports") or []
    near_res = res[0]["distance_pct"] if res else None
    near_sup = sup[0]["distance_pct"] if sup else None
    # distance_pct is negative for supports (below price), positive for
    # resistance (above). "Room to run" is a weak tailwind; "capped just above"
    # is a weak headwind.
    if near_res is not None and near_res <= 1.5:
        votes.append(_vote("مقاومت نزدیک", -1,
                           "مقاومت تنها %.1f%% بالاتر" % near_res))
    elif near_sup is not None and near_sup >= -1.5:
        votes.append(_vote("حمایت نزدیک", +1,
                           "حمایت تنها %.1f%% پایین‌تر" % abs(near_sup)))

    net = sum(v["vote"] for v in votes)
    total = len(votes)

    # The call. A margin of >=2 net votes is needed to move off HOLD, so a
    # single indicator never decides -- convention, not tuning.
    if net >= 2:
        call = BUY
    elif net <= -2:
        call = SELL
    else:
        call = HOLD

    # ADX only qualifies conviction; it never flips the call. A trend call in a
    # trendless tape (ADX < 20) is explicitly marked weak.
    conviction = "normal"
    if adx is not None:
        if adx >= 25:
            conviction = "trending"
        elif adx < 20:
            conviction = "weak"

    return {
        "call": call,
        "call_fa": SIGNAL_FA[call],
        "net": net,
        "bullish": sum(1 for v in votes if v["vote"] > 0),
        "bearish": sum(1 for v in votes if v["vote"] < 0),
        "neutral": sum(1 for v in votes if v["vote"] == 0),
        "total": total,
        "conviction": conviction,
        "adx": adx,
        "votes": votes,
        "caveat": SIGNAL_CAVEAT,
    }


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

    # Support/resistance from confirmed pivots, and the technical call built on
    # top of the same latest reading. Both are computed on the full frame, not
    # the display tail, so a level off the left edge of the chart still counts.
    sr = support_resistance(frame, latest.get("close"))

    signal = technical_signal(latest, sr)

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
        "levels": sr,
        "signal": signal,
        "series": series,
    }


def _is_nat(value):
    try:
        return value != value          # NaT != NaT is True
    except Exception:                  # noqa: BLE001
        return False
