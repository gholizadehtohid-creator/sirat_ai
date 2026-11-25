"""A company's profile beside its outlook: growth over three and five years,
and how its price has moved -- beta, volatility, and where it stands against
its year's high and low.

WHY THIS EXISTS
---------------
From the owner's walk through BourseView (2026-11-26): its screener reads a
share by 1/3/5-year revenue and profit growth, beta, standard deviation and
the distance from its highs and lows, and the bot read none of them. They
describe the company and the share; none is a signal, and nothing here has
been tested as one.

  growth   compound annual growth of revenue and of owners' profit over the
           last 3 and 5 fiscal years, from the annual statements on one
           basis. A short fiscal year (fundamental_outlook.short_years) is not
           a year and breaks the chain: no growth is read across it.
  price    from the last ~60 weeks of bars, back-adjusted for capital raises
           and dividends (backtest.back_adjust) so a bonus issue is not a
           50% fall: weekly beta against TEDPIX and weekly volatility,
           annualised; and the last close against the 52-week high and low
           and the 200-day average.

Weekly, not daily: a share that sits in a buy or sell queue for days shows
zero daily moves the index does not, and a daily beta reads that as low risk.
"""

import math

import fundamental_outlook as fo

TEDPIX = "32097828799138957"
WEEKS = 60
MIN_WEEKS = 30
SESSIONS = 300          # bars read: 60 weeks and the 200-day average
YEARS = (3, 5)
# Rial nominal growth in a 40-80% inflation economy: a compound rate outside
# this band is a restatement or a change of business, not growth.
GROWTH_BAND = (-0.9, 5.0)


def _annuals(rows):
    """{fiscal year end (jy, jm): period} of the full-year statements, one
    basis (the newest annual's), newest first."""

    periods = [p for p in fo._periods(rows) if p["annual"] and not p["short_year"]
               and fo._has_income(p["items"])]
    if not periods:
        return [], None
    basis = periods[0]["consolidated"]
    same = [p for p in periods if p["consolidated"] == basis]
    by_fy = {}
    for p in same:
        by_fy.setdefault(p["fy"], p)
    return [by_fy[k] for k in sorted(by_fy, reverse=True)], basis


def growth(rows):
    """{"revenue_3y", "revenue_5y", "profit_3y", "profit_5y", "basis",
    "latest_year"}: compound annual rates (fractions) or None each."""

    annuals, basis = _annuals(rows)
    out = {"basis": basis, "latest_year": annuals[0]["end"] if annuals else None}
    for years in YEARS:
        for key, read in (("revenue", lambda it: fo._f(it.get("revenue"))),
                          ("profit", fo._profit)):
            out["%s_%dy" % (key, years)] = None
            if len(annuals) <= years:
                continue
            now, then = annuals[0], annuals[years]
            # consecutive fiscal years only: a gap is a missing statement or
            # a moved year-end, and the rate across it would be a guess
            if now["fy"][0] - then["fy"][0] != years or now["fy"][1] != then["fy"][1]:
                continue
            a, b = read(now["items"]), read(then["items"])
            if a is None or b is None or a <= 0 or b <= 0:
                continue
            rate = (a / b) ** (1.0 / years) - 1
            if GROWTH_BAND[0] <= rate <= GROWTH_BAND[1]:
                out["%s_%dy" % (key, years)] = rate
    return out


def _weekly(frame, column="close"):
    series = frame.set_index("date")[column].astype(float)
    return series.resample("W-WED").last().dropna()


def price(bars, index):
    """{"beta", "volatility", "weeks", "from_high", "from_low", "from_sma200",
    "high_52w", "low_52w"} or None. `bars` and `index` are DataFrames with
    date and close (database.load_history_tail / load_index_history)."""

    from backtest import back_adjust

    if bars is None or len(bars) < 60 or index is None or index.empty:
        return None
    frame, _events = back_adjust(bars)
    frame = frame.dropna(subset=["close"])
    if frame.empty:
        return None
    last = float(frame["close"].iloc[-1])
    year = frame[frame["date"] >= frame["date"].iloc[-1] - __import__("pandas").Timedelta(days=365)]
    high, low = float(year["close"].max()), float(year["close"].min())
    sma200 = float(frame["close"].tail(200).mean()) if len(frame) >= 200 else None

    stock_w = _weekly(frame).pct_change()
    index_w = _weekly(index).pct_change()
    joined = stock_w.to_frame("s").join(index_w.to_frame("i"), how="inner").dropna().tail(WEEKS)
    beta = volatility = None
    if len(joined) >= MIN_WEEKS:
        var = joined["i"].var()
        if var and var > 0:
            beta = float(joined["s"].cov(joined["i"]) / var)
        volatility = float(joined["s"].std() * math.sqrt(52))
    return {"beta": beta, "volatility": volatility, "weeks": len(joined),
            "high_52w": high, "low_52w": low, "sma200": sma200,
            "from_high": last / high - 1 if high else None,
            "from_low": last / low - 1 if low else None,
            "from_sma200": last / sma200 - 1 if sma200 else None}


def profile(ins_code, rows=None, bars=None, index=None):
    """growth() and price() for one company, read from the stores when not
    given. Never raises: a part that cannot be read is None."""

    import database
    import fundamental_db

    out = {"growth": None, "price": None}
    try:
        rows = rows if rows is not None else fundamental_db.load_metrics(ins_code, limit=40)
        out["growth"] = growth(rows)
    except Exception:                                           # noqa: BLE001
        pass
    try:
        if bars is None:
            bars = database.load_history_tail([str(ins_code)], SESSIONS).get(str(ins_code))
        if index is None:
            index = database.load_index_history(TEDPIX, limit=SESSIONS + 20)
        out["price"] = price(bars, index)
    except Exception:                                           # noqa: BLE001
        pass
    return out
