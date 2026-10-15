"""Technical analysis of the market indices (شاخص کل، شاخص کل هم‌وزن).

An index is not a share, and three differences shape this module:

  NO VOLUME.  The exchange publishes a closing, low and high value for an
      index and nothing else. The scoring model in ranking.py weights volume
      at 15 of its 100 points, so an index either loses that component or is
      given a volume from somewhere. This module gives it market turnover --
      the total rials traded across every instrument we hold bars for -- and
      says so in `volume_source`. That is the figure a trader would look at
      anyway: an index rising on falling turnover is the classic warning, and
      here that warning is an actual vote (`_turnover_vote`), not a remark.

  TWO INDICES, ONE MARKET.  شاخص کل is capitalisation-weighted, so a handful of
      giants can carry it while most of the market falls. شاخص کل هم‌وزن gives
      every constituent the same say. Read alone, either one misleads; read
      together, the gap between them measures breadth directly, which is why
      compare() exists, why the panel shows both, and why the breadth reading
      is fed back into BOTH indices' signals as a vote.

  IT IS NOT A POSITION.  The equity panel (technical.py) answers "what would I
      buy this at, where is the stop, what is the reward for the risk". None of
      those questions has a meaning for an index nobody can hold. So the
      reading below carries the SAME analyses -- latest indicator values,
      support and resistance from confirmed pivots, a transparent BUY/SELL
      vote, and a target ladder -- but the ladder is TWO-SIDED and measured in
      the index's own ATR rather than in a stop distance. See `index_targets`
      for why the equity risk floor must not be applied here.

WHAT IS REUSED, AND WHY
------------------------
`technical.support_resistance` and `technical.technical_signal` are called
directly rather than reimplemented. A support level that differed between the
share panel and the index panel would be a bug the reader could not see; the
only honest way to keep them equal is to run the same function. What is NOT
reused is `technical.price_targets`, for the reason given in `index_targets`.

    python indices.py
"""

import math

import config
import database
import levels
import technical
from indicators import (DEFAULT_PARAMS, calculate_indicators, clean_params,
                        required_bars)
from ranking import rank_stocks


# A turnover figure computed from a handful of instruments is not market
# turnover. Dates below this share of the best-covered day are dropped rather
# than contributing a misleadingly small number.
TURNOVER_COVERAGE_RATIO = 0.25


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


def _turnover_series(limit=None):
    """Date -> total rials traded, restricted to well-covered sessions."""

    frame = database.market_turnover()

    if frame is None or frame.empty:
        return None

    best = frame["instruments"].max()

    if best:
        frame = frame[frame["instruments"] >= best * TURNOVER_COVERAGE_RATIO]

    frame = frame[frame["turnover"] > 0]

    if frame.empty:
        return None

    if limit:
        frame = frame.tail(limit)

    return frame[["date", "turnover"]].reset_index(drop=True)


def load_series(key, bars=None):
    """One index's series with market turnover attached as `volume`.

    Returns (frame, volume_source). The frame is empty if the index has not
    been imported; volume_source is None when no turnover could be attached,
    and the caller must then not pretend the volume component ran.
    """

    spec = config.INDICES.get(key)

    if spec is None:
        return None, None

    frame = database.load_index_history(spec["insCode"])

    if frame is None or frame.empty:
        return frame, None

    turnover = _turnover_series()

    if turnover is None:
        frame = frame.tail(bars or config.INDEX_ANALYSIS_BARS).copy()
        frame["volume"] = None
        return frame.reset_index(drop=True), None

    # Inner join: the analysed window is where BOTH a published index value
    # and a trustworthy turnover figure exist.
    merged = frame.merge(turnover, on="date", how="inner")

    merged = merged.rename(columns={"turnover": "volume"})

    if bars:
        merged = merged.tail(bars)

    return merged.reset_index(drop=True), "market turnover (sum of traded value)"


# ==============================
# THE INDEX-ONLY VOTES
# ==============================
#
# technical.technical_signal votes on trend, MACD, RSI, Bollinger and the
# nearest level -- everything readable from a price series alone. Two things an
# INDEX says that a single share does not are added on top of it, through the
# same vote structure, so the panel renders them identically and the reader
# cannot tell which came from where without reading the name.

# How far back the turnover vote looks to decide whether the index is rising.
# One week of sessions: shorter is noise, longer stops describing "now".
TURNOVER_LOOKBACK = 5

# Percentage-point gap between the two indices over 20 sessions that counts as
# a real breadth signal rather than the two moving together. The same cut point
# compare() has always used for its verdict -- one number, one meaning.
BREADTH_SPREAD_PP = 2.0


def _turnover_vote(frame):
    """Does market turnover confirm the index's move, or contradict it?

    The reading an index has that a share does not need. The `volume` column
    here is the whole market's traded value, so this asks the oldest question
    in tape reading: is the move being paid for? A rise on turnover below its
    own average is distribution dressed as strength, and no other component of
    this model can see it.

    Returns None when there is no turnover to read -- a missing vote is honest;
    a neutral vote invented out of nothing is not.
    """

    if frame is None or frame.empty or "volume" not in frame.columns:
        return None

    volume = _finite(frame["volume"].iloc[-1])

    average = (_finite(frame["VOLUME_MA"].iloc[-1])
               if "VOLUME_MA" in frame.columns else None)

    if volume is None or not average or average <= 0:
        return None

    closes = frame["close"]

    if len(closes) <= TURNOVER_LOOKBACK:
        return None

    now = _finite(closes.iloc[-1])
    then = _finite(closes.iloc[-1 - TURNOVER_LOOKBACK])

    if now is None or not then:
        return None

    change = (now - then) / then * 100.0
    ratio = volume / average

    name = "گردش معاملات بازار"
    detail = "گردش %.0f%% میانگین خود" % (ratio * 100)

    # A flat tape says nothing about whether turnover confirms it.
    if abs(change) < 0.5:
        return technical._vote(name, 0, "شاخص کم‌تغییر است — %s" % detail)

    if change > 0:
        if ratio >= 1.0:
            return technical._vote(
                name, +1,
                "رشد %.1f%% در %d جلسه با پول تازه — %s"
                % (change, TURNOVER_LOOKBACK, detail))
        return technical._vote(
            name, -1,
            "رشد %.1f%% در %d جلسه اما گردش زیر میانگین — %s"
            % (change, TURNOVER_LOOKBACK, detail))

    if ratio >= 1.0:
        return technical._vote(
            name, -1,
            "افت %.1f%% در %d جلسه با گردش بالا — فروش فعال، %s"
            % (abs(change), TURNOVER_LOOKBACK, detail))

    return technical._vote(
        name, 0,
        "افت %.1f%% در %d جلسه، کم‌رمق — %s"
        % (abs(change), TURNOVER_LOOKBACK, detail))


def _breadth_vote(spread_20):
    """Is the advance broad, or carried by a few large names?

    Fed to BOTH indices, deliberately. The gap between them is a statement
    about the market, not about either series, so it belongs in both readings;
    the vote's own name says where it came from.
    """

    spread = _finite(spread_20)

    if spread is None:
        return None

    name = "عمق بازار (هم‌وزن منهای کل)"

    if spread > BREADTH_SPREAD_PP:
        return technical._vote(
            name, +1, "هم‌وزن %.1f واحد جلوتر — رشد فراگیر است" % spread)

    if spread < -BREADTH_SPREAD_PP:
        return technical._vote(
            name, -1,
            "هم‌وزن %.1f واحد عقب‌تر — بازار را چند نماد بزرگ می‌کشند"
            % abs(spread))

    return technical._vote(
        name, 0,
        "فاصله‌ی دو شاخص %.1f واحد — هم‌جهت حرکت می‌کنند" % spread)


def _add_vote(signal, vote):
    """Append an index-only vote and recompute the call it feeds.

    The call must be recomputed rather than left alone: a vote counted in the
    tally the reader sees but not in the verdict printed beside it would be
    decoration, and the panel prints both.
    """

    if not signal or not vote:
        return signal

    votes = list(signal.get("votes") or [])
    votes.append(vote)

    net = sum(v["vote"] for v in votes)

    # The same margin rule as technical.technical_signal: a single indicator
    # never moves the call off HOLD.
    if net >= 2:
        call = technical.BUY
    elif net <= -2:
        call = technical.SELL
    else:
        call = technical.HOLD

    signal.update({
        "votes": votes,
        "net": net,
        # Recomputed for the same reason the call is: `strength` is the margin
        # as a fraction of the votes cast, and appending a vote changes both
        # halves of that fraction. Leaving it would print a strength struck
        # against a tally the reader can no longer see.
        "strength": round(net / len(votes), 2) if votes else None,
        "call": call,
        "call_fa": technical.SIGNAL_FA[call],
        "bullish": sum(1 for v in votes if v["vote"] > 0),
        "bearish": sum(1 for v in votes if v["vote"] < 0),
        "neutral": sum(1 for v in votes if v["vote"] == 0),
        "total": len(votes),
    })

    return signal


# ==============================
# LEVELS AND TARGETS -- two-sided, because an index has no side
# ==============================

# Rolling windows whose extremes count as remembered levels. The same three
# levels.overhead() consults, so the "nearest ceiling" it reports and the rung
# printed on the ladder are the same price.
ROLLING_WINDOWS = (60, 120, 250)

# ATR multiples projected either side of the price: one normal session's true
# range, then its two- and three-session extensions.
PROJECTION_MULTIPLES = (1.0, 2.0, 3.0)

# Two levels closer together than this are the same level twice. Structural
# ones win the tie -- a price the market actually traded at outranks arithmetic.
DEDUPE_PCT = 0.20

# Rungs published per side. Beyond this the ladder is further away than any
# reading of today's chart can speak to.
LADDER_DEPTH = 6

# Which kind of level displaces which when two land on the same price.
SOURCE_RANK = {"pivot": 0, "rolling": 1, "fib": 2, "projection": 3}

SOURCE_FA = {
    "pivot": "پیوت تأییدشده",
    "rolling": "سقف/کف دوره‌ای",
    "fib": "فیبوناچی",
    "projection": "پروژکشن نوسان",
}

INDEX_TARGET_CAVEAT = (
    "شاخص نمادی نیست که بتوان خرید، پس این‌ها «حد ضرر» و «حد سود» یک موقعیت "
    "نیستند. نقشه‌ی جایی‌اند که بازار پیش‌تر در آن معامله کرده، یا نوسان معمول "
    "خودِ شاخص به آن می‌رسد. واحد اندازه‌گیری، ATR همین شاخص است — بدون کف ۳٪ "
    "سهام، چون شاخص در کسری از درصد حرکت می‌کند و آن کف پاسخ را می‌بلعید. هیچ "
    "سطحی نمی‌گوید شاخص به آن‌جا می‌رسد؛ می‌گوید اگر برسد، کجاست و چند جلسه راه."
)

INDEX_SIGNAL_NOTE = (
    "این همان رأی‌گیری پنل سهام است، به‌اضافه‌ی دو رأی که فقط یک شاخص دارد: "
    "گردش معاملات کل بازار، و فاصله‌ی شاخص هم‌وزن از شاخص کل."
)


def _sessions_to(distance_pct, atr_pct):
    """Sessions a move of `distance_pct` takes at the observed daily range.

    The same arithmetic as trade_manager.holding_period -- distance divided by
    the progress an ATR actually delivers in a day -- but WITHOUT that
    function's clamp to the 5..40 session swing horizon. The clamp encodes
    "this is no longer a swing trade", which is a statement about a position.
    An index reading has no position: a level half a session away is half a
    session away, and rounding it up to five would misdescribe the chart.
    """

    distance_pct = abs(_finite(distance_pct) or 0.0)
    atr_pct = _finite(atr_pct)

    if not distance_pct or atr_pct is None or atr_pct <= 0:
        return None

    progress = atr_pct * (_finite(config.HOLD_ATR_EFFICIENCY) or 0.45)

    if progress <= 0:
        return None

    return max(1, int(math.ceil(distance_pct / progress)))


def _max_session_move(frame):
    """The largest close-to-close move the index made in this window, percent.

    A hard empirical speed limit, and a better one than config.PRICE_BAND_PCT.
    The ±3% band caps each CONSTITUENT; the index is a weighted mean of them
    and in practice travels a fraction of that, so quoting the band as the
    index's own limit would overstate how fast a level can be reached, by
    several times.
    """

    if frame is None or len(frame) < 2 or "close" not in frame.columns:
        return None

    moves = frame["close"].pct_change().abs() * 100.0

    return _finite(moves.max())


def _candidate_levels(frame, price, sr, atr):
    """Every level worth a rung, as (kind, label, price, date) tuples."""

    found = []

    # ---- confirmed pivots: the levels the market itself drew ---------------
    for entry in (sr or {}).get("resistances") or []:
        found.append(("pivot", "سقف پیوتی", entry.get("price"), entry.get("date")))

    for entry in (sr or {}).get("supports") or []:
        found.append(("pivot", "کف پیوتی", entry.get("price"), entry.get("date")))

    # ---- rolling extremes --------------------------------------------------
    #
    # The last bar is excluded on both sides for the reason levels.overhead
    # gives: today's own high sits above today's close by construction, so
    # including it would report a ceiling a few basis points overhead on every
    # single day. A level the price is standing on is not a level.
    highs = frame["high"] if "high" in frame.columns else frame["close"]
    lows = frame["low"] if "low" in frame.columns else frame["close"]

    past_high = highs.iloc[:-1]
    past_low = lows.iloc[:-1]

    # A period high the index has since climbed above is not a ceiling any
    # more, it is the floor it broke through -- and printing "60-session high"
    # on the DOWNSIDE ladder without saying so reads as a mistake. The same
    # goes, inverted, for a period low the index has fallen under.
    for window in ROLLING_WINDOWS:

        if len(past_high) >= window:
            level = past_high.tail(window).max()
            found.append(("rolling",
                          "سقف %d جلسه%s"
                          % (window, "" if _finite(level) is None
                             or level > price else " — اکنون حمایت"),
                          level, None))

        if len(past_low) >= window:
            level = past_low.tail(window).min()
            found.append(("rolling",
                          "کف %d جلسه%s"
                          % (window, "" if _finite(level) is None
                             or level < price else " — اکنون مقاومت"),
                          level, None))

    # ---- fibonacci on the last completed swing -----------------------------
    fib = levels.fibonacci(frame) or {}

    for name, value in (fib.get("levels") or {}).items():
        if name.startswith("ext_"):
            label = "بسط %s سوئینگ آخر" % name[4:]
        elif name.startswith("retr_"):
            label = "اصلاح %s سوئینگ آخر" % name[5:]
        else:
            continue
        found.append(("fib", label, value, None))

    # ---- volatility projections -------------------------------------------
    #
    # The only rungs not drawn from history. They answer a different question:
    # not "where has the market stopped before" but "how far does a normal move
    # from here even reach".
    if atr and atr > 0:
        for multiple in PROJECTION_MULTIPLES:
            found.append(("projection", "%g برابر ATR بالاتر" % multiple,
                          price + atr * multiple, None))
            found.append(("projection", "%g برابر ATR پایین‌تر" % multiple,
                          price - atr * multiple, None))

    return found, fib


def _ladder(candidates, price, atr_pct, above):
    """One side of the ladder: nearest level first, duplicates collapsed."""

    rows = []

    for kind, label, level, date in candidates:
        level = _finite(level)
        if level is None:
            continue
        if above and level <= price:
            continue
        if not above and level >= price:
            continue
        rows.append((kind, label, level, date))

    # Walk structural levels before projections, and within one kind the
    # nearest first, so the survivor of a collision is always the more real of
    # the two.
    rows.sort(key=lambda row: (SOURCE_RANK.get(row[0], 9), abs(row[2] - price)))

    picked = []

    for kind, label, level, date in rows:

        if any(abs(level - kept["price"]) / price * 100.0 < DEDUPE_PCT
               for kept in picked):
            continue

        distance = (level - price) / price * 100.0

        try:
            date = date.strftime("%Y-%m-%d")
        except Exception:                                      # noqa: BLE001
            date = date if isinstance(date, str) else None

        picked.append({
            "kind": kind,
            "kind_fa": SOURCE_FA.get(kind, kind),
            "label": label,
            "price": round(level, 2),
            "distance_pct": round(distance, 2),
            "sessions": _sessions_to(distance, atr_pct),
            "date": date,
        })

    picked.sort(key=lambda row: abs(row["distance_pct"]))

    return picked[:LADDER_DEPTH]


def index_targets(frame, latest, sr):
    """Where the index runs into something, up and down, and how far that is.

    WHY NOT technical.price_targets
    --------------------------------
    That function builds its ladder on `risk_engine.stop_distance`, which
    floors the distance at config.RISK_MIN_STOP_PCT (3%). The floor is right
    for a share -- a stop tighter than one daily band is not a stop -- and it
    destroys the reading here: twice the index ATR is about 2.9%, so the floor,
    not the ATR, would decide every index target on every day. It also returns
    a stop-loss and a reward/risk ratio, and there is no position on an index
    for either to describe.

    So the unit is the raw ATR, unfloored, and the ladder runs BOTH WAYS. An
    index reader is not choosing an entry; they are asking where the market
    meets something, in whichever direction it moves.

    Returns None when there is no usable price.
    """

    latest = latest or {}

    price = _finite(latest.get("close"))

    if price is None or price <= 0:
        return None

    atr = _finite(latest.get("atr"))
    atr_pct = _finite(latest.get("atr_pct"))

    candidates, fib = _candidate_levels(frame, price, sr, atr)

    upside = _ladder(candidates, price, atr_pct, above=True)
    downside = _ladder(candidates, price, atr_pct, above=False)

    # The ceiling the rest of the system already agrees on, so this panel and
    # levels.overhead cannot quote different walls for the same bar.
    head = levels.overhead(frame, price=price) or {}

    speed_limit = _max_session_move(frame)

    notes = []

    if atr is None or atr <= 0:
        notes.append("ATR در دسترس نبود — پروژکشن‌های نوسانی ساخته نشدند و "
                     "برآورد تعداد جلسه ممکن نیست")
    else:
        notes.append("واحد اندازه‌گیری: ATR برابر %s واحد (%.2f%% از شاخص) — "
                     "دامنه‌ی معمول یک جلسه"
                     % (format(int(round(atr)), ","), atr_pct or 0.0))

    if not upside:
        notes.append("هیچ سطح شناخته‌شده‌ای بالای شاخص نیست — شاخص در سقف "
                     "پنجره‌ی بررسی‌شده ایستاده و مقاومتی از جنس حافظه‌ی قیمتی "
                     "ندارد")

    if not downside:
        notes.append("هیچ سطح شناخته‌شده‌ای زیر شاخص نیست")

    # Only worth saying when the speed limit actually bites. "At least one
    # session away" is true of every level and tells the reader nothing.
    if speed_limit and upside:
        need = max(1, int(math.ceil(abs(upside[0]["distance_pct"]) / speed_limit)))
        if need > 1:
            notes.append("بزرگ‌ترین حرکت یک‌جلسه‌ای شاخص در این پنجره %.2f%% "
                         "بوده — حتی با آن سرعت، نزدیک‌ترین سطح بالا دست‌کم %d "
                         "جلسه فاصله دارد" % (speed_limit, need))

    swing = (fib.get("swing") or {}) if fib else {}

    if not swing:
        notes.append("سوئینگ کاملی برای سطوح فیبوناچی یافت نشد")

    resistances = (sr or {}).get("resistances") or []
    supports = (sr or {}).get("supports") or []

    return {
        "price": round(price, 2),

        "unit": None if atr is None else round(atr, 2),
        "unit_pct": None if atr_pct is None else round(atr_pct, 2),
        "unit_source": "ATR — بدون کف ریسک سهام",

        "upside": upside,
        "downside": downside,

        # The three the panel puts in tiles: the two objectives above, and the
        # first level that would be lost on the way down.
        "objective_1": upside[0] if upside else None,
        "objective_2": upside[1] if len(upside) > 1 else None,
        "break_level": downside[0] if downside else None,

        "nearest_resistance": resistances[0] if resistances else None,
        "nearest_support": supports[0] if supports else None,

        "overhead_pct": _finite(head.get("nearest_pct")),
        "overhead_sources": head.get("by_source") or {},

        "max_session_move_pct": (None if speed_limit is None
                                 else round(speed_limit, 2)),
        "swing_direction": swing.get("direction"),
        "swing_from": _finite(swing.get("from")),
        "swing_to": _finite(swing.get("to")),

        "notes": notes,
        "caveat": INDEX_TARGET_CAVEAT,
    }


# ==============================
# THE FULL READING
# ==============================

def analyse(key, params=None, bars=None):
    """Full technical read on one index.

    Carries everything the equity panel carries -- the 0-100 score, the latest
    indicator values, support and resistance from confirmed pivots, a
    transparent vote, and a two-sided target ladder -- plus the turnover vote
    only an index needs. The breadth vote is added later, by analyse_all,
    because it cannot be computed from one series.
    """

    spec = config.INDICES.get(key)

    if spec is None:
        return {"key": key, "error": f"unknown index '{key}'"}

    params = clean_params(params or DEFAULT_PARAMS)
    bars = bars or config.INDEX_ANALYSIS_BARS

    frame, volume_source = load_series(key, bars=bars)

    base = {
        "key": key,
        "name": spec["name"],
        "ins_code": spec["insCode"],
        "weighting": spec["weighting"],
        "volume_source": volume_source,
    }

    if frame is None or frame.empty:
        return {
            **base,
            "technical": None,
            "error": "no index history stored — run: "
                     "python seed_import.py --indices",
        }

    warmup = required_bars(params)

    if len(frame) < warmup:
        return {
            **base,
            "bars": len(frame),
            "technical": None,
            "error": (f"only {len(frame)} sessions overlap with market "
                      f"turnover; the indicators need {warmup}"),
        }

    enriched = calculate_indicators(frame, params)

    technical_score = rank_stocks(enriched)

    last = enriched.iloc[-1]

    # The same field list the equity panel publishes, so a reader comparing a
    # share to the index is comparing like with like -- and so the one shared
    # renderer can draw both.
    latest = {}
    for out_name, column in technical.LATEST_FIELDS:
        latest[out_name] = (_finite(last[column]) if column in enriched.columns
                            else None)

    # Levels and the vote run on the FULL analysed frame, not the charted tail:
    # a pivot off the left edge of the chart is still a level.
    sr = technical.support_resistance(enriched, latest.get("close"))

    # The frame is deliberately NOT passed. technical_signal would add its own
    # share-level volume-confirmation vote from it, and for an index the
    # `volume` column IS market turnover -- so the reading would be counted
    # twice, once in that vote and once in _turnover_vote below, which says the
    # same thing in the index's own language.
    signal = technical.technical_signal(latest, sr)

    signal = _add_vote(signal, _turnover_vote(enriched))

    signal["note"] = INDEX_SIGNAL_NOTE

    targets = index_targets(enriched, latest, sr)

    return {
        **base,
        "bars": len(enriched),
        "first_date": enriched["date"].iloc[0].strftime("%Y-%m-%d"),
        "last_date": last["date"].strftime("%Y-%m-%d"),
        "close": float(last["close"]),
        "params": params,
        "returns": _returns(enriched),
        "technical": technical_score,
        "latest": latest,
        "levels": sr,
        "signal": signal,
        "targets": targets,
        "series": _series_payload(enriched),
    }


def _returns(df):
    """Percentage change over the usual review horizons."""

    closes = df["close"]
    last = closes.iloc[-1]

    out = {}

    for label, back in (("1d", 1), ("5d", 5), ("20d", 20), ("60d", 60)):
        if len(closes) > back:
            past = closes.iloc[-1 - back]
            out[label] = round((last - past) / past * 100, 2) if past else None
        else:
            out[label] = None

    return out


def _series_payload(df, points=260):
    """Recent series for charting, as plain JSON-safe lists.

    Carries the Bollinger bands and the MACD pair as well as the moving
    averages, because the index panel now draws the same two charts the equity
    panel does and a chart missing the bands cannot show a squeeze.
    """

    tail = df.tail(points)

    def column(name):
        if name not in tail.columns:
            return []
        return [None if value != value else round(float(value), 2)
                for value in tail[name]]

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in tail["date"]],
        "close": column("close"),
        "ma_fast": column("MA_FAST"),
        "ma_slow": column("MA_SLOW"),
        "bb_upper": column("BB_UPPER"),
        "bb_lower": column("BB_LOWER"),
        "rsi": column("RSI"),
        "macd": column("MACD"),
        "macd_signal": column("MACD_SIGNAL"),
        "macd_hist": column("MACD_HIST"),
        "volume": column("volume"),
    }


def compare(reports):
    """What the two indices say about breadth when read against each other.

    A capitalisation-weighted index rising while the equal-weighted one falls
    means the market is not rising -- a few large companies are. The reverse,
    equal-weighted leading, means the advance is broad and the giants are
    lagging. Neither is visible in either index alone.
    """

    cap = next((r for r in reports if r.get("weighting") == "capitalisation"
                and r.get("returns")), None)
    equal = next((r for r in reports if r.get("weighting") == "equal"
                  and r.get("returns")), None)

    if not cap or not equal:
        return None

    spreads = {}

    for horizon in ("1d", "5d", "20d", "60d"):
        a = equal["returns"].get(horizon)
        b = cap["returns"].get(horizon)
        spreads[horizon] = round(a - b, 2) if a is not None and b is not None else None

    spread_20 = spreads.get("20d")

    if spread_20 is None:
        verdict = "not enough overlapping history to judge breadth"
    elif spread_20 > BREADTH_SPREAD_PP:
        verdict = ("broad advance — the equal-weighted index is beating the "
                   "capitalisation-weighted one, so smaller names are leading")
    elif spread_20 < -BREADTH_SPREAD_PP:
        verdict = ("narrow market — the capitalisation-weighted index is being "
                   "carried by large caps while the average share lags")
    else:
        verdict = "large caps and the broad market are moving together"

    return {
        "cap_weighted": cap["name"],
        "equal_weighted": equal["name"],
        "spreads": spreads,
        "verdict": verdict,
    }


def analyse_all(params=None, bars=None):
    """Both indices plus the breadth read between them.

    Breadth is computed after the two readings exist and then fed BACK into
    both as a vote. That ordering is why it lives here and not in analyse():
    the gap between the indices is not knowable from one of them.
    """

    reports = [analyse(key, params=params, bars=bars) for key in config.INDEX_LIST]

    breadth = compare(reports)

    if breadth:
        vote = _breadth_vote((breadth.get("spreads") or {}).get("20d"))
        for report in reports:
            if report.get("signal"):
                _add_vote(report["signal"], vote)

    return {
        "indices": reports,
        "breadth": breadth,
    }


if __name__ == "__main__":

    report = analyse_all()

    print("=" * 74)
    print("SIRAT AI - INDEX TECHNICAL ANALYSIS")
    print("=" * 74)

    for index in report["indices"]:

        print(f"\n{index['name']}  ({index['weighting']}-weighted)")

        if index.get("error"):
            print(f"  unavailable: {index['error']}")
            continue

        technical_score = index["technical"]
        returns = index["returns"]

        print(f"  {index['bars']} sessions, {index['first_date']} -> "
              f"{index['last_date']}")
        print(f"  close    {index['close']:,.1f}")
        print(f"  returns  1d {returns['1d']}%   5d {returns['5d']}%   "
              f"20d {returns['20d']}%   60d {returns['60d']}%")
        print(f"  score    {technical_score['score']}/100   "
              f"{technical_score['status']}")
        print(f"  trend    {technical_score['trend']}   "
              f"RSI {technical_score['rsi']}")
        print(f"  volume   {index['volume_source'] or 'not available'}")
        print("  signals  " + "; ".join(technical_score["signals"]))

        signal = index.get("signal") or {}

        if signal.get("call"):
            print(f"\n  CALL     {signal['call']} ({signal['call_fa']})   "
                  f"net {signal['net']:+d} of {signal['total']} votes   "
                  f"conviction {signal['conviction']}")
            for vote in signal.get("votes") or []:
                mark = "+" if vote["vote"] > 0 else "-" if vote["vote"] < 0 else "."
                print(f"    [{mark}] {vote['name']}: {vote['reason']}")

        lv = index.get("levels") or {}

        print("\n  LEVELS")
        for entry in lv.get("resistances") or []:
            print(f"    resistance {entry['price']:>14,.1f}  "
                  f"{entry['distance_pct']:+6.2f}%  {entry['date']}")
        for entry in lv.get("supports") or []:
            print(f"    support    {entry['price']:>14,.1f}  "
                  f"{entry['distance_pct']:+6.2f}%  {entry['date']}")
        if not (lv.get("resistances") or lv.get("supports")):
            print("    no confirmed pivots")

        targets = index.get("targets") or {}

        if targets:
            print("\n  TARGET LADDER  (unit: ATR = "
                  f"{targets.get('unit')}, {targets.get('unit_pct')}%)")
            for row in reversed(targets.get("upside") or []):
                print(f"    ^ {row['price']:>14,.1f}  {row['distance_pct']:+6.2f}%  "
                      f"{row['sessions'] or '?'} sessions   {row['label']}")
            print(f"    = {targets['price']:>14,.1f}       now")
            for row in targets.get("downside") or []:
                print(f"    v {row['price']:>14,.1f}  {row['distance_pct']:+6.2f}%  "
                      f"{row['sessions'] or '?'} sessions   {row['label']}")
            for note in targets.get("notes") or []:
                print(f"    note: {note}")

    breadth = report["breadth"]

    if breadth:
        print()
        print("=" * 74)
        print("BREADTH — equal-weighted minus capitalisation-weighted")
        print("=" * 74)
        for horizon, spread in breadth["spreads"].items():
            print(f"  {horizon:<5} {spread:+.2f} pp" if spread is not None
                  else f"  {horizon:<5} n/a")
        print(f"\n  {breadth['verdict']}")
