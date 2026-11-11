"""Single-instrument technical read, for the dashboard's Technical panel.

WHY A NEW MODULE RATHER THAN A ROUTE BODY
------------------------------------------
Every indicator here already exists: `indicators.calculate_indicators` produces
MA, RSI, MACD, Bollinger + %B, ATR/ADX, OBV and a trend label; `levels.swings`
produces confirmed pivot highs and lows. Nothing is recomputed. What was
missing is a path that takes ONE symbol and returns its latest reading, its
support/resistance levels, a technical call and a price-target ladder, in a
shape the browser can render. The targets are not new arithmetic either: they
come from `risk_engine.stop_distance`, `levels.overhead` and
`trade_manager.targets` -- the same three the swing book uses -- so the panel
and the book cannot quote different objectives for the same bar.

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

BACK-ADJUSTED, LIKE THE V2 FRAME
---------------------------------
`price_history` is raw: a capital increase or a fund's unit split drops the
quote overnight by the adjustment factor and nothing in the feed marks it.
Every read here back-adjusts first, with the same backtest.back_adjust the v2
frame builder (data_pipeline.frame) applies. Measured 2026-11-11 on raw bars,
طلا's -89.9% unit split of 2026-11-02 read RSI 7.5, a 50-session average nine
times the price, an ATR of 57% of price and resistances 516-607% overhead;
adjusted, RSI 36.4, ATR 2.46% and resistances +0.4/+4.3/+10.8%. گوهر, split
on 2026-09-28, called HOLD on raw bars and SELL on adjusted ones. The
adjustment anchors at the newest bar, so today's close -- and every distance
measured from it -- is unchanged.
"""

import math

import config
import database
import indicators
import levels
import ranking
import risk_engine
import trade_manager
from backtest import back_adjust


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
    # The two directional indicators ADX is built from. They were computed and
    # thrown away, which left the panel showing a trend's STRENGTH with no way
    # to say which way it points -- the half of Wilder's system that actually
    # has a sign.
    ("di_plus", "DI_PLUS"),
    ("di_minus", "DI_MINUS"),
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


def _day(value):
    """A pandas Timestamp as YYYY-MM-DD, or None. Never raises."""

    try:
        return value.strftime("%Y-%m-%d")
    except Exception:                                          # noqa: BLE001
        return None


def _adjusted(bars):
    """Stored bars with corporate-action gaps scaled out (see module doc).

    Safe on the trailing windows loaded here: back_adjust scales backwards from
    the newest bar, so adjusting a tail equals adjusting the whole series and
    slicing it (data_pipeline.load_bars verified this on 396 symbols). A series
    with no gap past PRICE_ADJUST_THRESHOLD comes back with its prices
    untouched, so ordinary names read exactly as before.
    """

    adjusted, _events = back_adjust(bars)

    return adjusted


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

# Two pivots this close together are ONE level tested twice, not two levels.
# The distinction is the difference between a ladder and a list: a shelf the
# price has turned at four times inside half a percent used to consume all
# three resistance slots and hide every other level above it. 0.75% is a
# quarter of the exchange's own +/-3% daily band -- the scale at which two
# prices stop being distinguishable to anyone trading them.
LEVEL_CLUSTER_PCT = 0.75


def _cluster_levels(entries, price, tolerance_pct=LEVEL_CLUSTER_PCT):
    """Merge pivots within `tolerance_pct` into one level, nearest first.

    `entries` must already be ordered by distance from the price. The merged
    level is the MEAN of its members -- the shelf itself, not whichever single
    touch happened to overshoot furthest -- and it carries:

        touches   how many confirmed pivots formed it. This is the one
                  strength measure available from price alone: a level the
                  market has turned at three times is a different object from
                  one it turned at once, and nothing else in this reading can
                  say which is which.
        date      the most RECENT touch, because a level last tested this
                  month and one last tested three years ago are not equally
                  live, and the reader can only judge that if told.
    """

    groups = []

    for entry in entries:
        if groups:
            current = groups[-1]
            mean = current["sum"] / current["touches"]
            if abs(entry["price"] - mean) / price * 100.0 <= tolerance_pct:
                current["sum"] += entry["price"]
                current["touches"] += 1
                current["dates"].append(entry["date"])
                continue

        groups.append({"sum": entry["price"], "touches": 1,
                       "dates": [entry["date"]]})

    out = []

    for group in groups:
        level = group["sum"] / group["touches"]
        dates = [d for d in group["dates"] if d]
        out.append({
            "price": round(level, 2),
            # Dates are ISO strings by here, so max() is chronological.
            "date": max(dates) if dates else None,
            "touches": group["touches"],
            "distance_pct": round((level - price) / price * 100.0, 2),
        })

    return out


def support_resistance(frame, price, depth=None):
    """Nearest supports below and resistances above the current price.

    Built from `levels.swings` -- confirmed pivot highs and lows, no
    look-ahead. A pivot high above the price is resistance; a pivot low below
    it is support. Each level carries its distance from the price in percent,
    because on a +/-3% band market "2% away" and "9% away" are different kinds
    of level entirely, and a `touches` count, because on the same market the
    number of times supply has actually appeared at a price is the only
    strength measure the tape offers.
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
        entry = {"price": lvl, "date": _day(p.get("date"))}
        if lvl > price:
            resistances.append(entry)
        elif lvl < price:
            supports.append(entry)

    # Nearest first: resistances just above, supports just below. Clustering
    # depends on this order -- it merges neighbours, so neighbours must be
    # adjacent.
    resistances.sort(key=lambda e: e["price"])
    supports.sort(key=lambda e: e["price"], reverse=True)

    return {
        "price": price,
        "resistances": _cluster_levels(resistances, price)[:LEVELS_EACH_SIDE],
        "supports": _cluster_levels(supports, price)[:LEVELS_EACH_SIDE],
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


# A level this close is a level the next session can actually reach: it is
# inside half the exchange's daily band, so it binds today rather than someday.
NEAR_LEVEL_PCT = 1.5

# Wilder's own cut points for the ADX system, not numbers fitted here. Below
# the floor there is no trend for a direction to belong to; above STRONG the
# trend is worth calling one.
ADX_TREND_FLOOR = 20
ADX_STRONG = 25

# Sessions the volume vote measures the move over. One trading week: shorter is
# a single bar's noise, longer stops describing "now".
VOLUME_LOOKBACK = 5


def _directional_vote(latest):
    """+DI against -DI, gated by ADX. The signed half of Wilder's system.

    ADX says how strong a trend is and says nothing at all about its
    direction; +DI/-DI say the direction. Reading the first without the second
    is what let the panel print a confident "trending" conviction beside a
    call built entirely from other indicators. Below ADX_TREND_FLOOR the vote
    is explicitly neutral rather than absent -- there IS a reading, and the
    reading is "there is no trend here to have a direction".

    Returns None only when the DI columns are missing, which happens for any
    series with no genuine high/low range.
    """

    di_plus = latest.get("di_plus")
    di_minus = latest.get("di_minus")
    adx = latest.get("adx")

    if di_plus is None or di_minus is None:
        return None

    name = "جهت روند (DI)"

    if adx is not None and adx < ADX_TREND_FLOOR:
        return _vote(name, 0,
                     "ADX برابر %.0f و زیر %d است — روندی نیست که جهت داشته باشد"
                     % (adx, ADX_TREND_FLOOR))

    if di_plus > di_minus:
        return _vote(name, +1,
                     "قدرت جهت صعودی %.0f در برابر نزولی %.0f"
                     % (di_plus, di_minus))

    if di_minus > di_plus:
        return _vote(name, -1,
                     "قدرت جهت نزولی %.0f در برابر صعودی %.0f"
                     % (di_minus, di_plus))

    return _vote(name, 0, "دو جهت برابرند")


def _volume_vote(frame):
    """Is the move being paid for?

    The oldest question in tape reading, and the one component of this signal
    that looks at anything but price. A rise on volume below its own average
    is not the same event as a rise on volume above it, and no other vote here
    can tell them apart.

    Needs the frame, not just the latest bar: "the move" is a change over
    VOLUME_LOOKBACK sessions, which a single row cannot express. Callers with
    no frame simply do not get this vote -- a missing vote is honest, a
    neutral one invented out of nothing is not.
    """

    if frame is None or getattr(frame, "empty", True):
        return None

    if "volume" not in frame.columns or "VOLUME_MA" not in frame.columns:
        return None

    volume = _finite(frame["volume"].iloc[-1])
    average = _finite(frame["VOLUME_MA"].iloc[-1])

    if volume is None or not average or average <= 0:
        return None

    name = "حجم (تأیید حرکت)"

    # A zero-volume bar is not "demand collapsed", it is "this did not trade".
    # TSETMC emits a row for every session an instrument is listed, and a
    # halted or simply untraded name carries its frozen close at zero volume --
    # roughly a third of the bars in a whole-market fetch look like this.
    # Scored as a ratio it reads as 0% of average and casts a bearish vote,
    # which is a claim about supply and demand on a day neither appeared.
    if volume <= 0:
        return _vote(name, 0, "در آخرین جلسه معامله‌ای انجام نشده — "
                              "حجمی برای تأیید یا رد حرکت وجود ندارد")

    closes = frame["close"]

    if len(closes) <= VOLUME_LOOKBACK:
        return None

    now = _finite(closes.iloc[-1])
    then = _finite(closes.iloc[-1 - VOLUME_LOOKBACK])

    if now is None or not then:
        return None

    change = (now - then) / then * 100.0
    ratio = volume / average

    detail = "حجم %.0f%% میانگین خودش" % (ratio * 100.0)

    # A flat price says nothing about whether volume confirms it.
    if abs(change) < 0.5:
        return _vote(name, 0, "قیمت در %d جلسه کم‌تغییر بوده — %s"
                     % (VOLUME_LOOKBACK, detail))

    if change > 0:
        if ratio >= 1.0:
            return _vote(name, +1,
                         "رشد %.1f%% در %d جلسه با پول تازه — %s"
                         % (change, VOLUME_LOOKBACK, detail))
        return _vote(name, -1,
                     "رشد %.1f%% در %d جلسه اما حجم زیر میانگین — %s"
                     % (change, VOLUME_LOOKBACK, detail))

    if ratio >= 1.0:
        return _vote(name, -1,
                     "افت %.1f%% در %d جلسه با حجم بالا — فروش فعال، %s"
                     % (abs(change), VOLUME_LOOKBACK, detail))

    return _vote(name, 0,
                 "افت %.1f%% در %d جلسه با حجم کم — %s"
                 % (abs(change), VOLUME_LOOKBACK, detail))


def _touch_note(level):
    """"tested N times" -- but only when N is more than one."""

    touches = (level or {}).get("touches") or 1

    return "، %d بار آزموده شده" % touches if touches > 1 else ""


def technical_signal(latest, sr, frame=None):
    """Aggregate the indicators into BUY / HOLD / SELL, showing every vote.

    Equal-weight sum of per-indicator votes. No fitting, no tuned thresholds --
    the cut points are the textbook ones (RSI 30/70, MACD vs signal, price vs
    MAs, %B 0.2/0.8, ADX 20), chosen from convention rather than from this
    data, so the call cannot be an artifact of tuning on the symbol being
    viewed.

    `frame` is optional and adds only the votes a single row cannot express --
    at present the volume confirmation, which needs a change over several
    sessions. Callers that pass nothing get exactly the votes they got before.
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

    # --- the trend's DIRECTION, not just its strength ---
    directional = _directional_vote(latest)
    if directional:
        votes.append(directional)

    # --- is the move paid for? (needs the frame, skipped without it) ---
    volume_vote = _volume_vote(frame)
    if volume_vote:
        votes.append(volume_vote)

    # --- proximity to the nearest support AND the nearest resistance ---
    #
    # BOTH are asked. distance_pct is negative for supports (below price) and
    # positive for resistances (above); "capped just overhead" is a weak
    # headwind and "standing on a floor" is a weak tailwind, and a price
    # squeezed between the two is in a box -- a common and meaningful state on
    # a +/-3% band market.
    #
    # These used to be an if/elif, so a price one percent under a ceiling and
    # one percent over a floor cast the bearish vote only: a two-sided box was
    # reported as a one-sided headwind, and the missing half was invisible
    # because a vote that is never cast leaves no trace in a table of votes.
    res = (sr or {}).get("resistances") or []
    sup = (sr or {}).get("supports") or []
    near_res = res[0]["distance_pct"] if res else None
    near_sup = sup[0]["distance_pct"] if sup else None

    if near_res is not None and near_res <= NEAR_LEVEL_PCT:
        votes.append(_vote("مقاومت نزدیک", -1,
                           "مقاومت تنها %.1f%% بالاتر%s"
                           % (near_res, _touch_note(res[0]))))

    if near_sup is not None and near_sup >= -NEAR_LEVEL_PCT:
        votes.append(_vote("حمایت نزدیک", +1,
                           "حمایت تنها %.1f%% پایین‌تر%s"
                           % (abs(near_sup), _touch_note(sup[0]))))

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
        if adx >= ADX_STRONG:
            conviction = "trending"
        elif adx < ADX_TREND_FLOOR:
            conviction = "weak"

    return {
        "call": call,
        "call_fa": SIGNAL_FA[call],
        "net": net,
        # The margin as a fraction of the votes actually cast. +2 out of four
        # votes and +2 out of nine are the same call and very different
        # agreements, and the bare net cannot tell them apart -- which matters
        # more here than usual, because the number of votes VARIES with what
        # the data supports.
        "strength": round(net / total, 2) if total else None,
        "bullish": sum(1 for v in votes if v["vote"] > 0),
        "bearish": sum(1 for v in votes if v["vote"] < 0),
        "neutral": sum(1 for v in votes if v["vote"] == 0),
        "total": total,
        "conviction": conviction,
        "adx": adx,
        "votes": votes,
        "caveat": SIGNAL_CAVEAT,
    }


# ==============================
# PRICE TARGETS -- a projection, not a forecast
# ==============================

# How many structural levels to surface as an "objective ladder". The Fibonacci
# extensions of the last completed swing run 1.272 / 1.618 / 2.0 / 2.618, and by
# the third one the projection is further from the price than any ±3% market
# travels inside a swing horizon. Three is the honest depth.
STRUCTURAL_TARGETS = 3

# The standing caveat on the targets themselves, separate from the signal's.
# The measured target is arithmetic on a volatility unit -- it says where a
# normal move WOULD end, given the risk taken. It does not say the move happens.
TARGET_CAVEAT = (
    "اهداف قیمتی محاسبه‌اند، نه پیش‌بینی. هدف اندازه‌گیری‌شده از فاصله‌ی حد ضرر "
    "(مضربی از ATR) ساخته می‌شود و اگر مقاومتی زیر آن باشد، به زیر همان مقاومت "
    "کشیده می‌شود؛ اهداف ساختاری، تصویرِ آینه‌ای سوئینگ آخرند. هیچ‌کدام نمی‌گویند "
    "قیمت به آن‌جا می‌رسد — می‌گویند اگر برسد، کجاست و چقدر طول می‌کشد."
)


def price_targets(frame, latest, sr):
    """Where a move from here would end, and what it costs to be wrong.

    Reuses the production trade path rather than inventing a panel-only rule:
    `risk_engine.stop_distance` for the ATR-derived stop, `levels.overhead` for
    the nearest remembered ceiling, `trade_manager.targets` to build the two
    measured objectives and pull them under that ceiling, and
    `trade_manager.holding_period` for the duration. The panel therefore shows
    the SAME targets the swing book would compute for this bar -- a target that
    disagreed with the engine's would be worse than no target.

    LONG-ONLY, AND SAID SO. There is no equity short on this exchange, so the
    ladder is built upward. On a SELL reading the numbers still describe where
    the resistance sits; they are not an invitation, and the panel says that.

    Returns None when there is no usable price -- a target ladder with no
    price under it is decoration.
    """

    latest = latest or {}

    price = _finite(latest.get("close"))

    if price is None or price <= 0:
        return None

    # ---- the stop: what the target is measured in ------------------------
    distance, stop_source, stop_notes = risk_engine.stop_distance(
        price, atr=_finite(latest.get("atr")))

    if distance is None or distance <= 0:
        return None

    stop_price = price - distance

    # ---- the ceiling that is allowed to cut the target -------------------
    head = levels.overhead(frame, price=price) or {}
    overhead_pct = _finite(head.get("nearest_pct"))

    measured = trade_manager.targets(price, distance, overhead_pct=overhead_pct)

    sessions, duration_note = trade_manager.holding_period(
        measured.get("target_1_pct"), atr_pct=_finite(latest.get("atr_pct")))

    # ---- the floor of the ±3% band --------------------------------------
    #
    # The exchange caps a session at PRICE_BAND_PCT. However strong the read,
    # a 9% target cannot be reached in fewer than three limit-up sessions, and
    # a reader looking at a target with no time attached will assume otherwise.
    band = _finite(config.PRICE_BAND_PCT) or 3.0
    t1_pct = _finite(measured.get("target_1_pct"))
    band_sessions = (int(math.ceil(t1_pct / band))
                     if t1_pct and t1_pct > 0 else None)

    # ---- structural targets: the extensions of the last swing ------------
    #
    # A different family of objective entirely: not derived from the risk
    # taken, but from the size of the move the market last made. Only the ones
    # ABOVE the price are objectives -- an extension the price already passed
    # is history, not a target.
    fib = levels.fibonacci(frame) or {}
    structural = []
    for name, value in sorted((fib.get("levels") or {}).items(),
                              key=lambda kv: (_finite(kv[1]) or 0.0)):
        if not name.startswith("ext_"):
            continue
        lvl = _finite(value)
        if lvl is None or lvl <= price:
            continue
        structural.append({
            "ratio": name[4:],
            "price": round(lvl, 2),
            "distance_pct": round((lvl - price) / price * 100.0, 2),
            # What the rung is worth in units of the risk taken to reach it.
            # A structural target 0.4R above the price is not an objective at
            # all -- it sits inside the noise the stop is sized to absorb --
            # and a ladder that prints it with no R beside it invites exactly
            # that trade. The measured targets already show their R; the
            # structural ones were the half of the table that did not.
            "r_multiple": round((lvl - price) / distance, 2),
        })
    structural = structural[:STRUCTURAL_TARGETS]

    swing = (fib.get("swing") or {}) if fib else {}

    # ---- the nearest resistance, echoed as the first real obstacle -------
    resistances = (sr or {}).get("resistances") or []
    first_wall = resistances[0] if resistances else None

    supports = (sr or {}).get("supports") or []
    first_floor = supports[0] if supports else None

    # Two readings the panel must not present as ordinary targets.
    #
    # `no_room`: trade_manager floors a target crushed against a ceiling at the
    # entry price rather than below it, which is right for a field an order
    # router reads -- but a "target" equal to today's close is not a target, and
    # printing it as one would invite a trade with zero reward. The flag lets
    # the panel say "no room" instead of quoting the price back.
    #
    # `target_2_above_wall`: the ceiling only cuts the FIRST target. The second
    # deliberately sits beyond it, and a reader shown +1% and +23% side by side
    # with no explanation would take the second for a normal extension of the
    # first. It is not -- it requires the wall to break.
    reward_1_pct = _finite(measured.get("target_1_pct"))
    no_room = (measured.get("cap_source") == "overhead_tight"
               and (reward_1_pct is None or reward_1_pct <= 0.1))

    t2 = _finite(measured.get("target_2"))
    wall = (price * (1.0 + overhead_pct / 100.0)
            if overhead_pct is not None and overhead_pct > 0 else None)
    target_2_above_wall = bool(wall is not None and t2 is not None and t2 > wall)

    notes = list(stop_notes or [])
    notes.extend(measured.get("notes") or [])
    # Only when there IS a duration. holding_period returns an English
    # diagnostic when the capped target left nothing to travel, and `no_room`
    # already says that in the panel's own language.
    if sessions is not None and duration_note:
        notes.append(duration_note)
    if target_2_above_wall:
        notes.append("هدف دوم بالای مقاومت %.1f%% است — رسیدن به آن مستلزم "
                     "شکستن همان مقاومت است" % overhead_pct)
    if band_sessions and not no_room:
        notes.append(
            "دامنه‌ی نوسان %.0f%% است — رسیدن به هدف اول دست‌کم %d سشن کامل "
            "لازم دارد، حتی در بهترین حالت" % (band, band_sessions))
    if not structural:
        notes.append("سوئینگ کاملی برای اهداف ساختاری یافت نشد یا همه‌ی "
                     "بسط‌ها زیر قیمت‌اند")

    return {
        "price": round(price, 2),

        "stop_loss": round(stop_price, 2),
        "stop_distance_pct": round(distance / price * 100.0, 2),
        "stop_source": stop_source,

        "target_1": measured.get("target_1"),
        "target_2": measured.get("target_2"),
        "target_1_pct": measured.get("target_1_pct"),
        "target_2_pct": measured.get("target_2_pct"),
        "risk_reward": measured.get("rr_1"),
        "risk_reward_target_2": measured.get("rr_2"),
        "capped": bool(measured.get("capped")),
        "cap_source": measured.get("cap_source"),
        "no_room": bool(no_room),
        "target_2_above_wall": target_2_above_wall,
        "overhead_pct": measured.get("overhead_pct"),
        "overhead_sources": head.get("by_source") or {},

        "expected_sessions": sessions,
        "band_min_sessions": band_sessions,
        "band_pct": band,

        "structural": structural,
        "swing_direction": swing.get("direction"),
        # WHEN the swing being extended happened, and how big it was. An
        # extension of a move that ended two years ago is arithmetic on
        # history; one that ended last month is arithmetic on the current
        # market. Only the reader can tell those apart, and only if the dates
        # are on the card.
        "swing_from": _day(swing.get("from_date")),
        "swing_to": _day(swing.get("to_date")),
        "swing_size_pct": (
            round((swing["to"] - swing["from"]) / swing["from"] * 100.0, 2)
            if swing.get("from") else None),

        "nearest_resistance": first_wall,
        "nearest_support": first_floor,

        "min_risk_reward": _finite(config.MIN_RISK_REWARD),
        "notes": notes,
        "caveat": TARGET_CAVEAT,
    }


def analyse(symbol, params=None, bars=SERIES_BARS, halted=None):
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

    # Halted over config.HALT_EXCLUDE_DAYS: indicators on a frozen close
    # describe a market that no longer exists (user request, 2026-11-05).
    # `halted` may be passed in by a caller analysing many names: the map
    # costs a scan of every bar, and gold_list paid it once per fund
    # (22.7 of the gold card's 29 s, 2026-11-11).
    halted = (database.halted_codes() if halted is None else halted)         .get(str(ins_code), False)
    if halted is not False:
        return {"error": "نماد %s %s معامله نشده (متوقف) و تحلیل نمی‌شود."
                         % (symbol, "از %s" % halted if halted else "هیچ‌گاه"),
                "code": "halted",
                "symbol": symbol, "ins_code": ins_code, "halted_since": halted}

    params = indicators.clean_params(params)

    # Load enough history to warm the slowest indicator, plus the display
    # window. required_bars knows the longest lookback the params imply.
    need = indicators.required_bars(params) + bars

    history = database.load_history_by_inscode(ins_code, limit=need)

    if history is None or history.empty:
        return {"error": "no price history", "symbol": symbol,
                "ins_code": ins_code}

    frame = indicators.calculate_indicators(_adjusted(history), params)

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

    signal = technical_signal(latest, sr, frame=frame)

    # Targets run on the full frame for the same reason the levels do: the
    # swing whose extension becomes an objective may start well left of the
    # display window.
    targets = price_targets(frame, latest, sr)

    # A short tail for the sparkline. Only columns the chart needs, and every
    # value passed through _finite so the JSON never carries NaN.
    #
    # MACD_HIST, the middle band and the volume pair are here because the panel
    # draws them: a MACD chart without its histogram is missing the part that
    # turns first, and a price chart with no volume under it cannot show
    # whether a move was paid for -- the same question the volume vote asks in
    # words.
    tail = frame.tail(bars)

    series_cols = ("date", "close", "MA_FAST", "MA_SLOW",
                   "BB_UPPER", "BB_MID", "BB_LOWER",
                   "RSI", "MACD", "MACD_SIGNAL", "MACD_HIST",
                   "volume", "VOLUME_MA")

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
        "targets": targets,
        "series": series,
    }


def _is_nat(value):
    try:
        return value != value          # NaT != NaT is True
    except Exception:                  # noqa: BLE001
        return False


# ==============================
# TOP-N TECHNICAL RANKING
# ==============================

# History depth loaded per name for the scan. rank_stocks needs its indicators
# warmed; the pipeline uses 300 and this matches it so the ranking here and the
# screener's cannot diverge on warm-up.
SCAN_BARS = 300

# The liquidity floor. Ranking names that cannot absorb a position is exactly
# the failure this programme's own research (E20/E21) documented -- the signal
# concentrates where trading is impossible. A whole-market technical ranking
# that ignored this would put the least tradable names at the top, which is
# worse than no ranking. Same floor the fundamental screen and money-flow use.
MIN_TURNOVER = config.MIN_DAILY_VALUE


def top_technical(top=10, params=None, min_turnover=MIN_TURNOVER):
    """The strongest names by the platform's own technical score.

    Ranks the whole equity universe with `ranking.rank_stocks` -- the same
    scorer the screener's Stage 3 uses -- after a liquidity floor, and returns
    the top `top`. This is a convenience ranking for a human, NOT a validated
    edge: it carries the same caveat as the per-symbol signal, because it is
    built from the same indicators the research found no tradable edge in.
    """

    params = indicators.clean_params(params)

    instruments = database.load_instruments(equities_only=True)
    by_code = {str(r["ins_code"]): r for r in instruments}
    # Halted longer than config.HALT_EXCLUDE_DAYS: its bars are a frozen
    # close, and a last bar that traded heavily months ago would pass the
    # turnover floor on stale data (user request, 2026-11-05).
    halted = database.halted_codes()
    codes = [c for c in by_code if c not in halted]

    frames = database.load_history_tail(codes, SCAN_BARS)

    scored = []
    considered = 0
    illiquid = 0
    no_data = 0

    for code in codes:
        frame = frames.get(code)
        if frame is None or frame.empty:
            no_data += 1
            continue

        # Liquidity floor on the most recent traded value -- the same figure
        # the money-flow ranking gates on, so the two lists agree on what is
        # tradable.
        last_value = _finite(frame["value"].iloc[-1]) if "value" in frame \
            else None
        if last_value is None or last_value < min_turnover:
            illiquid += 1
            continue

        considered += 1

        enriched = indicators.calculate_indicators(_adjusted(frame), params)
        if enriched.empty:
            no_data += 1
            continue

        result = ranking.rank_stocks(enriched)
        if not result.get("scored", False):
            continue

        instrument = by_code.get(code, {})

        # The transparent buy/sell call, reusing the same vote engine as the
        # single-symbol panel so a name's rank and its call cannot disagree.
        last = enriched.iloc[-1]
        latest = {}
        for out_name, column in LATEST_FIELDS:
            latest[out_name] = _finite(last[column]) \
                if column in enriched.columns else None
        sr = support_resistance(enriched, latest.get("close"))
        signal = technical_signal(latest, sr, frame=enriched)

        scored.append({
            "ins_code": code,
            "symbol": instrument.get("symbol_fa"),
            "name": instrument.get("name_fa"),
            "sector": instrument.get("sector_name"),
            "score": result["score"],
            "status": result["status"],
            "trend": result.get("trend"),
            "rsi": result.get("rsi"),
            "macd": result.get("macd"),
            "momentum": result.get("momentum"),
            "turnover": last_value,
            "call": signal["call"],
            "call_fa": signal["call_fa"],
            "net": signal["net"],
        })

    ranked = sorted(scored, key=lambda r: r["score"], reverse=True)

    return {
        "top": ranked[:top],
        "min_turnover": min_turnover,
        "counts": {
            "universe": len(codes),
            "liquid_scored": len(scored),
            "illiquid_skipped": illiquid,
            "halted_skipped": len(halted),
            "no_data": no_data,
        },
        "caveat": SIGNAL_CAVEAT,
    }


# ==============================
# GOLD FUNDS
# ==============================

def gold_list(params=None):
    """A technical read for every gold fund that has price history.

    No liquidity floor and no fundamental scoring -- these are ETFs tracking
    bullion, not stocks. Technical analysis applies (they have a price series);
    the six-factor model and the fundamental screen do not, which is exactly
    why they live in their own list rather than the equity ranking. Sorted by
    technical score so the list has an order, with the same standing caveat.

    Each row also carries the day's price against the fund's published NAV --
    the حباب. That ratio is the one number here that is not a technical read:
    it is a valuation, and for an instrument whose entire holding is priced
    metal it is the valuation. `with_nav` in the counts says how many rows
    actually got it, because the NAV comes off the network and the network is
    not always there.
    """

    import gold

    funds = gold.gold_funds()

    # One pull for the whole list, before the loop: fourteen funds is
    # twenty-eight requests, and doing them per-row inside analyse() would
    # interleave network latency with the indicator maths for no gain.
    navs = gold.nav_snapshot([f.get("ins_code") for f in funds])

    halted_map = database.halted_codes()        # once, not once per fund

    rows = []
    missing = []

    for fund in funds:
        symbol = fund.get("symbol_fa")
        rep = analyse(symbol, params=params, halted=halted_map)

        if rep.get("error"):
            # A fund with no stored history yet -- reported, not silently
            # dropped, so the panel can say "press fetch" honestly.
            missing.append({"symbol": symbol, "name": fund.get("name_fa"),
                            "reason": rep["error"]})
            continue

        L = rep["latest"]
        sig = rep["signal"]

        nav = navs.get(fund.get("ins_code")) or {}

        rows.append({
            "symbol": symbol,
            "name": fund.get("name_fa"),
            "ins_code": fund.get("ins_code"),
            "as_of": rep.get("as_of"),
            "close": L.get("close"),
            "rsi": L.get("rsi"),
            "macd": L.get("macd"),
            "trend": L.get("trend"),
            "call": sig["call"],
            "call_fa": sig["call_fa"],
            "net": sig["net"],
            # The NAV block. `day_close` is the price the ratio was actually
            # computed from -- the live پایانی, which is not necessarily the
            # `close` above: that one comes from the last stored bar, and on a
            # day whose bars have not been imported yet the two differ. Sending
            # both is what lets the panel show the ratio's own price rather
            # than implying it was struck against a stale bar.
            "day_close": nav.get("close"),
            "day_last": nav.get("last"),
            "nav": nav.get("nav_redeem"),
            "nav_issue": nav.get("nav_issue"),
            "price_to_nav": nav.get("price_to_nav"),
            "bubble": nav.get("bubble"),
            "nav_date": nav.get("date"),
            # Why the NAV is absent, when it is. A blank cell that cannot say
            # whether the host was unreachable or the fund simply has no NAV
            # published is a cell nobody can act on.
            "nav_error": nav.get("error"),
        })

    rows.sort(key=lambda r: (r["call"] != "BUY", -(r["net"] or 0),
                             r["symbol"] or ""))

    with_nav = sum(1 for r in rows if r.get("price_to_nav") is not None)

    # The first reason any row failed, hoisted for the panel's banner. They
    # fail together far more often than they fail individually -- one
    # unreachable host takes all fourteen -- so one line says it.
    nav_error = next((r["nav_error"] for r in rows if r.get("nav_error")), None)

    return {
        "funds": rows,
        "missing": missing,
        "counts": {"total": len(funds), "with_data": len(rows),
                   "missing": len(missing), "with_nav": with_nav},
        "nav_band": gold.BUBBLE_BAND,
        "nav_error": nav_error,
        "caveat": SIGNAL_CAVEAT,
    }


# ==============================
# TECHNICAL STRENGTH + MONEY INFLOW
# ==============================

# A name must clear this technical score to count as "good technicals". It is
# the scorer's own STRONG band floor, not a number tuned here -- so this list
# agrees with what rank_stocks already calls strong.
MIN_TECHNICAL_SCORE = 70

# The combined caveat. Both halves are textbook, and the programme's research
# is specifically why the combination is not sold as an edge: E21 found flow
# predicts TRADABILITY (queue locks), not returns, and technical signals showed
# no edge net of cost. Two things with no proven return edge do not make one by
# being intersected.
COMBINED_CAVEAT = (
    "تلاقی «تکنیکال قوی» و «ورود پول» است — هر دو خوانش کتاب‌درسی، نه لبه‌ی "
    "اعتبارسنجی‌شده. تحقیق این پلتفرم نشان داد جریان پول (E21) تشخیص‌پذیری صف "
    "را پیش‌بینی می‌کند نه بازده را، و سیگنال تکنیکال (E19–E23) خالص از هزینه "
    "لبه‌ای نداشت. تقاطع دو چیزِ بدون لبه، لبه نمی‌سازد."
)


def best_technical_inflow(top=15, params=None, min_turnover=MIN_TURNOVER,
                          min_score=MIN_TECHNICAL_SCORE):
    """Names that are BOTH technically strong AND taking money in.

    One pass over the liquid universe: technical score from rank_stocks (the
    screener's own scorer) and money flow from moneyflow.analyse (net حقیقی
    inflow). A name qualifies only when its technical score clears the strong
    band AND its net individual flow is positive -- the intersection the user
    asked for. Ranked by technical score, flow shown alongside so the reader
    sees both reasons, never a blended single number that hides a disagreement.
    """

    import moneyflow

    params = indicators.clean_params(params)

    instruments = database.load_instruments(equities_only=True)
    by_code = {str(r["ins_code"]): r for r in instruments}
    # Halted longer than config.HALT_EXCLUDE_DAYS: its bars are a frozen
    # close, and a last bar that traded heavily months ago would pass the
    # turnover floor on stale data (user request, 2026-11-05).
    halted = database.halted_codes()
    codes = [c for c in by_code if c not in halted]

    frames = database.load_history_tail(codes, SCAN_BARS)
    # Load the BASELINE window, not the (shorter) flow window: moneyflow.analyse
    # returns INSUFFICIENT below FLOW_MIN_SESSIONS and also measures a baseline
    # over FLOW_BASELINE_WINDOW. Loading only FLOW_WINDOW sessions made every
    # name INSUFFICIENT -- the bug this line's comment exists to prevent
    # recurring.
    flow_frames = database.load_client_type_tail(
        codes, config.FLOW_BASELINE_WINDOW)
    flow_since = moneyflow.current_from(flow_frames)

    qualified = []
    liquid = 0
    strong = 0
    strong_and_inflow = 0

    for code in codes:
        frame = frames.get(code)
        if frame is None or frame.empty:
            continue

        last_value = _finite(frame["value"].iloc[-1]) if "value" in frame \
            else None
        if last_value is None or last_value < min_turnover:
            continue

        liquid += 1

        enriched = indicators.calculate_indicators(_adjusted(frame), params)
        if enriched.empty:
            continue

        tech = ranking.rank_stocks(enriched)
        if not tech.get("scored", False):
            continue

        if tech["score"] < min_score:
            continue

        strong += 1

        # Money flow. A name with no flow data, or with outflow, does not
        # qualify -- the question was "AND money inflow", so absence is a no,
        # not a maybe.
        flow = moneyflow.analyse(flow_frames.get(code))
        # A window that ends before this week's is another week's flow (the
        # rule the money-flow card applies): no inflow, not old inflow.
        if not moneyflow.is_current(flow, flow_since):
            continue
        reading = flow.get("reading") or {}
        net_pct = reading.get("net_pct")

        if net_pct is None or net_pct <= 0:
            continue

        strong_and_inflow += 1

        instrument = by_code.get(code, {})

        last = enriched.iloc[-1]
        latest = {}
        for out_name, column in LATEST_FIELDS:
            latest[out_name] = _finite(last[column]) \
                if column in enriched.columns else None
        sr = support_resistance(enriched, latest.get("close"))
        signal = technical_signal(latest, sr, frame=enriched)

        qualified.append({
            "ins_code": code,
            "symbol": instrument.get("symbol_fa"),
            "name": instrument.get("name_fa"),
            "sector": instrument.get("sector_name"),
            "score": tech["score"],
            "status": tech["status"],
            "trend": tech.get("trend"),
            "rsi": tech.get("rsi"),
            "macd": tech.get("macd"),
            "turnover": last_value,
            "flow_score": flow.get("score"),
            "flow_label": flow.get("label"),
            "net_pct": round(net_pct, 2),
            "power_ratio": (round(reading["power_ratio"], 2)
                            if reading.get("power_ratio") is not None else None),
            "call": signal["call"],
            "call_fa": signal["call_fa"],
        })

    ranked = sorted(qualified, key=lambda r: (r["score"], r["net_pct"]),
                    reverse=True)

    return {
        "top": ranked[:top],
        "min_turnover": min_turnover,
        "min_score": min_score,
        "counts": {
            "universe": len(codes),
            "liquid": liquid,
            "strong_technical": strong,
            "strong_and_inflow": strong_and_inflow,
        },
        "caveat": COMBINED_CAVEAT,
    }
