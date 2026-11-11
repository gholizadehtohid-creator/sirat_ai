"""Market regime: what kind of market is this, and how much cash to hold.

A stock-picking model has no opinion about the market it picks in. It will
rank the ten best names in a collapse exactly as confidently as in a boom, and
those ten will still fall. Regime detection is the layer that decides how much
capital the picks are allowed to hold at all.

Four inputs, deliberately different in kind so they cannot all be wrong the
same way:

    TREND      شاخص کل against its own moving averages -- direction
    BREADTH    شاخص کل هم‌وزن against شاخص کل -- is the move broad or carried
               by a handful of giants
    PARTICIPATION  share of the market above its own slow MA -- counted from
               constituents, depending on no index construction at all
    VOLATILITY realised volatility of the index against its own history

The regimes are not predictions. BULL does not mean "will rise"; it means "is
rising, broadly, calmly" -- a description of conditions that have already
happened, used to size exposure rather than to forecast.

A structural caveat specific to Tehran: the ±3% band means a genuine crash
arrives as a run of limit-down sessions with almost no volume, which realised
volatility reads as *low*. Volatility alone would call the worst week of the
year calm. That is why participation and breadth carry equal weight here, and
why a collapse in participation forces a defensive verdict regardless of what
the volatility reading says.
"""

import pandas as pd

import config
import database
import indices
from backtest import back_adjust
from indicators import DEFAULT_PARAMS, calculate_indicators


BULL = "BULL"
BEAR = "BEAR"
SIDEWAYS = "SIDEWAYS"

HIGH_VOLATILITY = "HIGH_VOLATILITY"
NORMAL_VOLATILITY = "NORMAL_VOLATILITY"
LOW_VOLATILITY = "LOW_VOLATILITY"

# Share of constituents above their own slow MA. Above the first the advance is
# broad; below the second the market is falling regardless of what the
# capitalisation-weighted index says.
PARTICIPATION_STRONG = 60.0
PARTICIPATION_WEAK = 35.0

# Realised volatility of the index, annualised, against these bands.
VOLATILITY_HIGH = 30.0
VOLATILITY_LOW = 12.0

# Target cash by regime. Risk management outranks signal quality: in a bear
# market the model's best idea is still an idea about a falling market.
CASH_TARGET = {
    BULL: 10.0,
    SIDEWAYS: 25.0,
    BEAR: 60.0,
}

# Extra cash when volatility is elevated, added to the regime target.
HIGH_VOLATILITY_CASH_PENALTY = 15.0


def _halted(frame, clock, days=None):
    """True when a series' last bar WITH VOLUME is over `days` before `clock`.

    database.halted_codes()'s rule, applied to bars already cut at a date so
    it holds for that date: a frozen close keeps arriving at zero volume, so
    the newest bar's date says nothing, and a series with bars but no traded
    session among them is halted.
    """

    days = config.HALT_EXCLUDE_DAYS if days is None else days

    traded = frame.loc[frame["volume"].fillna(0) > 0, "date"].max()

    if pd.isna(traded):
        return True

    return (clock - traded).days > days


def participation(params=None, bars=None, as_of=None):
    """Share of instruments trading above their own slow moving average.

    Computed from constituents rather than from any index, so it is immune to
    how an index is weighted or whether one is published at all. This is the
    single most honest breadth reading available offline.

    `as_of` truncates every series to that date, which is what makes this
    answerable for a past date rather than only for today. Without it the
    regime call cannot be measured against what followed it -- and a market
    read nobody has ever checked against outcomes is the most expensive kind
    of confident output.

    "The market" is the LIVE EQUITIES, on back-adjusted closes. It used to be
    every series in price_history on raw closes, and three things in that
    count were not the stock market's breadth. As of 2026-11-09 it read 63.13%
    of 830 against the BULL line of 60; the same bars read 75.04% of 697 with
    all three removed:

      * the 14 gold funds, which follow bullion rather than the bourse;
      * 119 names halted for over config.HALT_EXCLUDE_DAYS. A halted name keeps
        getting bars at its frozen close, so once the freeze outlasts the slow
        MA, close == MA exactly and it can never count as above -- 81 of the
        119 sat on that line, a guaranteed "below" in every reading;
      * capital-increase gaps. TSETMC's closes are unadjusted, so a name whose
        price was cut by an increase inside the window reads as having fallen
        by the whole cut, and stays under its MA until the gap leaves the
        window unless it rallies by all of it.
    """

    params = params or DEFAULT_PARAMS

    # A past date is read with the listings a board move has retired since:
    # they were the market then (database.load_instruments, with_superseded),
    # and the halt rule below drops each one once its trading stops. Today's
    # reading is of the live codes only -- شکبیر's old code last traded on
    # 2026-11-04, inside HALT_EXCLUDE_DAYS, and would sit beside its new one.
    equities = {str(row["ins_code"])
                for row in database.load_instruments(
                    equities_only=True, with_superseded=as_of is not None)}

    # A retired listing leaves once its company has a bar under another code
    # on or before the cutoff: from there the company is that code, and the
    # halt rule alone kept the old one beside it for HALT_EXCLUDE_DAYS after
    # its last trade (database.superseded_listings, found 2026-11-11).
    if as_of is not None:
        for code, moved in database.superseded_listings().items():
            if moved["until"] and \
                    pd.Timestamp(moved["until"]) <= pd.Timestamp(as_of):
                equities.discard(code)

    codes = [code for code in database.inscodes_with_history()
             if code in equities]

    if not codes:
        return None

    cutoff = pd.Timestamp(as_of) if as_of is not None else None

    frames = []

    # Today's read loads every name's tail in one pass (database.
    # load_history_tail, an indexed search per code on one connection); the
    # per-name reader opened a connection and a read_sql 902 times
    # (2026-11-11). A past date keeps the per-name reader, which bounds by it.
    tails = (database.load_history_tail(codes, bars or 120)
             if cutoff is None else None)

    for code in codes:

        # Both bounds go to the database: the newest N sessions on or before
        # the cutoff. Reading the whole series and slicing in pandas returns
        # the same answer but moves ~25x the rows, which turns a sweep over a
        # few hundred dates from minutes into an hour.
        frame = (tails.get(str(code)) if tails is not None else
                 database.load_history_by_inscode(code, limit=bars or 120,
                                                  before=cutoff))

        if frame is None or frame.empty or "close" not in frame.columns:
            continue

        # A bar without a date cannot be placed against the halt clock below.
        frame = frame.dropna(subset=["date", "close"])

        if len(frame) < params["ma_slow"] + 1:
            continue

        frames.append(frame)

    if not frames:
        return None

    # The halt clock is the market's newest session on or before the cutoff,
    # read from these same bars -- not database.halted_codes(), which counts
    # from the newest session in the WHOLE table and so would apply today's
    # halts to a past date, making detect(as_of=...) depend on when it was run.
    # Not the cutoff itself either: a cutoff on a holiday, or past the last
    # fetch, must not age every name by the gap (halted_codes' own rule).
    clock = max(frame["date"].max() for frame in frames)

    above = 0
    counted = 0
    above_fast = 0

    for frame in frames:

        if _halted(frame, clock):
            continue

        # The helper the v2 path uses, on the raw stored bars. It anchors at
        # the newest bar it is given and scales backwards, so it needs nothing
        # past the cutoff: an increase after it would only rescale this whole
        # window, which no close-against-its-MA comparison can see.
        frame, _events = back_adjust(frame)

        closes = frame["close"]

        slow = closes.rolling(window=params["ma_slow"]).mean().iloc[-1]
        fast = closes.rolling(window=params["ma_fast"]).mean().iloc[-1]

        last = closes.iloc[-1]

        if slow != slow or last != last:
            continue

        counted += 1

        if last > slow:
            above += 1

        if fast == fast and last > fast:
            above_fast += 1

    if not counted:
        return None

    return {
        "instruments": counted,
        "above_slow_ma_pct": round(above / counted * 100, 2),
        "above_fast_ma_pct": round(above_fast / counted * 100, 2),
        "slow_ma": params["ma_slow"],
        "fast_ma": params["ma_fast"],
    }


def index_state(key="TEDPIX", params=None, bars=None, as_of=None):
    """Trend, momentum and realised volatility of one index."""

    params = params or DEFAULT_PARAMS

    if as_of is None:
        frame, volume_source = indices.load_series(key, bars=bars)
    else:
        # The whole series, then cut -- asking for `bars` first would take the
        # most recent N, which for a past date is data that had not happened.
        frame, volume_source = indices.load_series(key)
        if frame is not None and not frame.empty and "date" in frame:
            frame = frame[frame["date"] <= pd.Timestamp(as_of)]
            if bars:
                frame = frame.tail(bars)

    if frame is None or frame.empty:
        return None

    enriched = calculate_indicators(frame, params)

    if enriched.empty or len(enriched) < params["ma_slow"]:
        return None

    last = enriched.iloc[-1]

    close = float(last["close"])
    fast = last["MA_FAST"]
    slow = last["MA_SLOW"]

    if fast != fast or slow != slow:
        return None

    returns = enriched["close"].pct_change().tail(60).dropna()

    volatility = (
        float(returns.std() * (config.TRADING_DAYS_PER_YEAR ** 0.5) * 100)
        if len(returns) >= 10 else None
    )

    def change(back):
        if len(enriched) <= back:
            return None
        past = float(enriched["close"].iloc[-1 - back])
        return round((close - past) / past * 100, 2) if past else None

    return {
        "key": key,
        "name": config.INDICES[key]["name"],
        "close": round(close, 1),
        "above_fast_ma": bool(close > fast),
        "above_slow_ma": bool(close > slow),
        "fast_above_slow": bool(fast > slow),
        "volatility_pct": round(volatility, 2) if volatility is not None else None,
        "return_20d": change(20),
        "return_60d": change(60),
        "rsi": round(float(last["RSI"]), 1) if last["RSI"] == last["RSI"] else None,
        "volume_source": volume_source,
        "bars": len(enriched),
    }


def _trend_verdict(state):
    """Three-state read of one index's own trend."""

    if state is None:
        return None

    if state["above_slow_ma"] and state["fast_above_slow"]:
        return BULL

    if not state["above_slow_ma"] and not state["fast_above_slow"]:
        return BEAR

    return SIDEWAYS


def _volatility_verdict(state):
    if state is None or state.get("volatility_pct") is None:
        return None

    volatility = state["volatility_pct"]

    if volatility >= VOLATILITY_HIGH:
        return HIGH_VOLATILITY

    if volatility <= VOLATILITY_LOW:
        return LOW_VOLATILITY

    return NORMAL_VOLATILITY


def detect(params=None, bars=None, as_of=None):
    """Full regime read with the evidence that produced it.

    `as_of` makes the read reproducible for a past date, using only what was
    knowable then.
    """

    params = params or DEFAULT_PARAMS

    cap = index_state("TEDPIX", params=params, bars=bars, as_of=as_of)
    equal = index_state("TEDPIX_EW", params=params, bars=bars, as_of=as_of)

    breadth = participation(params=params, bars=bars, as_of=as_of)

    return assemble(cap, equal, breadth, params=params)


def assemble(cap, equal, breadth, params=None):
    """The vote, the veto and the cash target, from three ready readings.

    Split out of detect() so a caller that has ALREADY computed the three inputs
    can reach the same verdict without fetching them again. The sweep in
    swing_backtest.py is the case that needed it: it iterates every symbol's bars
    per date anyway, so it can produce the participation figure as a by-product
    for a fraction of what participation() costs -- and then must vote on it with
    exactly this logic rather than a second copy of it.

    Nothing about the vote changed in the extraction. detect() is now this
    function plus three fetches, which is what it always was.
    """

    params = params or DEFAULT_PARAMS

    evidence = []
    missing = []

    # ---- trend -----------------------------------------------------------

    cap_trend = _trend_verdict(cap)
    equal_trend = _trend_verdict(equal)

    if cap_trend is None:
        missing.append("شاخص کل")
    else:
        evidence.append(
            f"شاخص کل: {'بالای' if cap['above_slow_ma'] else 'زیر'} میانگین "
            f"{params['ma_slow']} روزه، بازده ۶۰ روزه {cap['return_60d']}٪"
        )

    if equal_trend is None:
        missing.append("شاخص کل هم‌وزن")
    else:
        evidence.append(
            f"شاخص هم‌وزن: {'بالای' if equal['above_slow_ma'] else 'زیر'} میانگین "
            f"{params['ma_slow']} روزه، بازده ۶۰ روزه {equal['return_60d']}٪"
        )

    # ---- participation ---------------------------------------------------

    if breadth is None:
        missing.append("مشارکت بازار")
        participation_verdict = None
    else:
        pct = breadth["above_slow_ma_pct"]
        evidence.append(
            f"مشارکت: {pct}٪ از {breadth['instruments']} نماد بالای میانگین "
            f"{breadth['slow_ma']} روزه‌ی خودشان"
        )
        if pct >= PARTICIPATION_STRONG:
            participation_verdict = BULL
        elif pct <= PARTICIPATION_WEAK:
            participation_verdict = BEAR
        else:
            participation_verdict = SIDEWAYS

    # ---- vote ------------------------------------------------------------

    votes = [v for v in (cap_trend, equal_trend, participation_verdict) if v]

    if not votes:
        return {
            "available": False,
            "reason": "هیچ‌کدام از ورودی‌های تشخیص وضعیت بازار در دسترس نیست: "
                      + "، ".join(missing),
            "missing": missing,
        }

    bulls = votes.count(BULL)
    bears = votes.count(BEAR)

    if bulls > bears and bulls >= 2:
        regime = BULL
    elif bears > bulls and bears >= 2:
        regime = BEAR
    else:
        regime = SIDEWAYS

    # Participation has a veto on the bullish case. A capitalisation-weighted
    # index can rise on five companies while most of the market falls, and a
    # basket of ten names is far more likely to be drawn from the falling part.
    if (
        regime == BULL
        and participation_verdict == BEAR
    ):
        regime = SIDEWAYS
        evidence.append(
            "شاخص صعودی است اما اکثر نمادها زیر میانگین خود هستند — "
            "رشد بازار باریک است و به سبد ده‌نمادی تعمیم داده نمی‌شود."
        )

    volatility_regime = _volatility_verdict(cap) or _volatility_verdict(equal)

    # ---- cash ------------------------------------------------------------

    cash = CASH_TARGET.get(regime, config.MIN_CASH_PCT)

    if volatility_regime == HIGH_VOLATILITY:
        cash = min(90.0, cash + HIGH_VOLATILITY_CASH_PENALTY)
        evidence.append(
            "نوسان بالا — سهم نقد افزایش یافت."
        )

    return {
        "available": True,
        "regime": regime,
        "volatility_regime": volatility_regime,
        "label": _label(regime, volatility_regime),
        "confidence": round(max(bulls, bears, votes.count(SIDEWAYS)) / len(votes), 2),
        "votes": {"bull": bulls, "bear": bears, "sideways": votes.count(SIDEWAYS)},
        "cash_target_pct": cash,
        "max_invested_pct": round(100 - cash, 1),
        "cap_weighted": cap,
        "equal_weighted": equal,
        "participation": breadth,
        "evidence": evidence,
        "missing": missing,
        "caveat": (
            f"دامنه‌ی نوسان ±{config.PRICE_BAND_PCT}٪ باعث می‌شود سقوط بازار به‌صورت "
            "چند روز صف فروش با نوسان ظاهراً پایین دیده شود؛ به همین دلیل مشارکت و "
            "شاخص هم‌وزن هم‌وزنِ نوسان در تصمیم دخالت داده شده‌اند."
        ),
    }


def _label(regime, volatility_regime):
    names = {
        BULL: "بازار صعودی",
        BEAR: "بازار نزولی",
        SIDEWAYS: "بازار خنثی",
    }

    volatility_names = {
        HIGH_VOLATILITY: "نوسان بالا",
        LOW_VOLATILITY: "نوسان پایین",
        NORMAL_VOLATILITY: "نوسان عادی",
    }

    base = names.get(regime, regime)

    extra = volatility_names.get(volatility_regime)

    return f"{base} — {extra}" if extra else base


if __name__ == "__main__":

    report = detect()

    print("=" * 74)
    print("SIRAT AI — تشخیص وضعیت بازار / MARKET REGIME")
    print("=" * 74)

    if not report.get("available"):
        print(report["reason"])
        raise SystemExit(0)

    print(f"\n  وضعیت      {report['label']}")
    print(f"  اطمینان    {report['confidence']}  "
          f"(bull {report['votes']['bull']}, bear {report['votes']['bear']}, "
          f"sideways {report['votes']['sideways']})")
    print(f"  نقد هدف    {report['cash_target_pct']}%  "
          f"— حداکثر {report['max_invested_pct']}% سرمایه‌گذاری")

    print("\n  شواهد:")
    for line in report["evidence"]:
        print(f"    • {line}")

    if report["missing"]:
        print(f"\n  در دسترس نبود: {'، '.join(report['missing'])}")

    print(f"\n  {report['caveat']}")
