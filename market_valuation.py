"""Is the market cheap or dear? Its P/E against its own history and bills.

WHY THIS EXISTS
---------------
The allocation card (E27/E28) says how much to hold in shares, gold and
fixed income; it says nothing about whether shares are cheap or expensive
right now, which is the first question a professional asks before adding to
them. This answers it with the market's aggregate trailing P/E:

    sum of market values / sum of trailing-twelve-month owners' profits

over the operating companies (banks, insurers, investment and holding
companies excluded, as in the outlook), read point-in-time: every point uses
only the filings public on its date and that date's printed close, through
backtesting/pit.py -- the machinery E30 ran on. Loss-makers count with their
losses; a company with no trailing year yet is left out and counted.

Two comparisons, both stated, neither tested as a timing signal:
  * the current P/E's percentile in its own history since 2021-06;
  * the market's earnings yield against the treasury-bill yield.
E27 rejected a trend-timing rule; a valuation-timing rule has not been
tested. The panel says so.

The history is heavy (bars for every equity, every filing), so it is built
by `python market_valuation.py --update` in the evening task and kept in
data/market_valuation.json; the panel only reads that file.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(ROOT, "data", "market_valuation.json")

START = "2021-06-01"
EVERY = 21                    # sessions between history points

# A point needs this many companies with a trailing year to be published.
# Before 2022-05 the store holds a prior year for only 63-170 of them and
# the aggregate swung 10 -> 17 -> 10 on composition alone; from 2022-05 every
# point has 220 or more.
MIN_COMPANIES = 200

# An industry is read against its own history only with at least this many
# companies in a point (fewer is one company's P/E with a sector's name) and
# this many points (fewer is no history to be cheap or dear against).
SECTOR_MIN_COMPANIES = 4
SECTOR_MIN_POINTS = 12


def _jalali(ts):
    import jalali
    return jalali.today_jalali(ts.date())


def _rows(history):
    return [{"period_end": r["period_end"], "period_months": r["period_months"],
             "items": r.get("items") or {}, "ratios": r.get("ratios") or {},
             "published_at": r.get("published_at"), "audited": r.get("audited"),
             "consolidated": r.get("consolidated")} for r in reversed(history)]


def _factor(data, code, when):
    frame = data.bars(code)
    i = data.position(code, when)
    if frame is None or i < 0:
        return None
    raw = float(frame["raw_close"].iloc[i] or 0)
    return float(frame["close"].iloc[i]) / raw if raw > 0 else None


# A price gap implying more than this many times the shares between a filing
# and the decision is a broken bar, not a capital increase.
MAX_SHARE_RATIO = 50.0


def _shares(data, code, newest, previous, decision):
    """Capital at par from the newest filing, carried across later bonus
    issues by the back-adjustment factor (the exchange's share count has no
    history in the database).

    Not when the filing's capital line already carries that raise. شپدیس's
    1404/12/29 statement shows capital up 38.9x on the previous one -- a
    revaluation registered by the period end -- while the market adjusted its
    price only on 2026-04-06, after it. Scaling the new capital by the gap
    counted the raise twice: 120,299 thousand billion rials of market value,
    which alone took the market's P/E from 7.3 to 21.1 (found 2026-11-10).
    None for a ratio no corporate action produces.
    """

    import fundamental_outlook as fo
    import jalali

    capital = fo._f((newest.get("items") or {}).get("capital"))
    if not capital:
        return None
    shares = capital / fo.PAR_VALUE
    try:
        jy, jm, jd = (int(x) for x in str(newest.get("period_end")).split("/")[:3])
        then = datetime(*jalali.to_gregorian(jy, jm, jd))
    except (TypeError, ValueError):
        then = None
    f_then = _factor(data, code, then) if then else None
    f_now = _factor(data, code, decision)
    if not (f_then and f_now):
        return shares
    ratio = f_now / f_then
    if not 1.0 / MAX_SHARE_RATIO <= ratio <= MAX_SHARE_RATIO:
        return None
    before = fo._f(((previous or {}).get("items") or {}).get("capital"))
    if ratio > 1.2 and before and capital / before >= ratio * 0.75:
        return shares
    return shares * ratio


def _traded_recently(data, code, decision, days=None):
    """Whether `code` traded (volume > 0) within `days` calendar days up to
    `decision`, using only bars on or before it.

    The market's P/E is over the companies one can trade. Asking for a bar ON
    the decision date instead made the rule depend on how the store happens to
    record a halt: until 2026-11-07 a halted share kept getting zero-volume
    bars at its frozen close and counted, from 11-08 the fetch stopped writing
    them and ~75 halted names dropped out of the newest points only -- 476
    companies on 10-10, 395 on 11-08, a composition change inside the very
    series the percentile compares against (found 2026-11-11). A short halt
    (an AGM, a few days) keeps the name at its last printed close, the way the
    exchange's own market value does; a halt past config.HALT_EXCLUDE_DAYS
    takes it out on every date alike.
    """

    import config
    import pandas as pd

    days = config.HALT_EXCLUDE_DAYS if days is None else days
    frame = data.bars(code)
    i = data.position(code, decision)
    if frame is None or i < 0 or "volume" not in frame.columns:
        return False
    upto = frame.iloc[: i + 1]
    traded = upto["date"][upto["volume"].fillna(0) > 0]
    if traded.empty:
        return False
    return (pd.Timestamp(decision) - pd.Timestamp(traded.iloc[-1])).days <= days


def _latest_with_ttm(analyse, history, newest):
    """The outlook of the newest filing, or of the one before it when the
    newest cannot give a trailing year.

    The store keeps one version of each period, the latest public: last
    year's 6-month statement is held as the audited CONSOLIDATED one, which
    arrives months after the period, while this year's newest 6-month is
    still the first, SEPARATE release. The two cannot be netted, so the
    trailing year is missing the month the 6-month reports land -- 48
    companies without one on 10-10, 113 on 11-10, the difference leaving
    the newest points only (found 2026-11-11). The filing before it still
    gives one: the figure the company was counted with a month earlier,
    and STALE_MONTHS still bounds how old it may be.
    """

    import fundamental_outlook as fo

    result = analyse(history)
    if not result or result["kind"] != fo.OPERATING             or result["ttm"]["profit"] is not None:
        return result
    earlier = [r for r in history if r is not newest]
    if not earlier:
        return result
    again = analyse(earlier)
    if again and again["kind"] == fo.OPERATING             and again["ttm"]["profit"] is not None:
        return again
    return result


def point(data, decision):
    """The market's aggregate trailing P/E on session `decision`, or None."""

    import config
    import fundamental_outlook as fo

    no_hurdle = {"rate_pct": 0.0, "as_of": "-", "source": "valuation"}
    cap = profit = 0.0
    used = skipped = halted = 0
    by_sector = {}
    for code, inst in data.instruments().items():
        if inst["sector"] in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            continue
        if data.position(code, decision) < 0:
            continue                     # not listed yet
        if not _traded_recently(data, code, decision):
            halted += 1
            continue
        history = data.fundamental_history(code, decision, periods=12)
        if not history:
            continue
        ordered = sorted(history, key=lambda r: (str(r["period_end"]),
                                                 r["period_months"] or 0))
        newest = ordered[-1]
        previous = ordered[-2] if len(ordered) > 1 else None
        shares = _shares(data, code, newest, previous, decision)
        if shares is None:
            skipped += 1
            continue
        price = data.valuation_price(code, decision)

        def analyse(rows, code=code, price=price, shares=shares,
                    sector=inst["sector"]):
            return fo.analyse(
                code, price=price, rows=_rows(rows), today=_jalali(decision),
                monthly_rows=[], dividend=None, shares=shares, sector=sector,
                portfolio=None, opportunity=no_hurdle)

        result = _latest_with_ttm(analyse, history, newest)
        if not result or result["kind"] != fo.OPERATING:
            continue
        age = result["latest"]["age_months"]
        ttm = result["ttm"]["profit"]
        value = result["valuation"]["market_cap"]
        if (age is not None and age > fo.STALE_MONTHS) or ttm is None or not value \
                or fo.suspect_reason(result):
            skipped += 1
            continue
        cap += value
        profit += ttm
        used += 1
        sector = by_sector.setdefault(inst["sector"] or "بدون صنعت", [0.0, 0.0, 0])
        sector[0] += value
        sector[1] += ttm
        sector[2] += 1
    if used < MIN_COMPANIES or profit <= 0:
        return None
    return {"date": str(decision.date()), "pe": round(cap / profit, 2),
            "earnings_yield_pct": round(profit / cap * 100, 2),
            "companies": used, "left_out": skipped, "halted": halted,
            "market_cap": cap, "profit_ttm": profit,
            # [market value, trailing profit, companies] per industry, for
            # reading each against its own history.
            "sectors": {name: [round(c), round(pr), n]
                        for name, (c, pr, n) in by_sector.items()}}


def load():
    try:
        with open(FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"points": []}


def update(verbose=True, data=None, rebuild=False):
    """Fill the history grid and refresh the newest session's point.

    `rebuild` recomputes every stored point: needed whenever the method
    changes, because stored points are otherwise kept as computed and a
    series mixing two methods compares unlike with unlike.
    """

    import backtest_config
    from backtesting.pit import PointInTimeData

    data = data or PointInTimeData(backtest_config.BacktestConfig(), verbose=False)
    stored = {} if rebuild else {p["date"]: p for p in load().get("points", [])}

    sessions = data.sessions()
    if not sessions:
        return {"added": 0}
    wanted = data.rebalance_dates(START, str(sessions[-1].date()), EVERY)
    if sessions[-1] not in wanted:
        wanted.append(sessions[-1])

    newest = str(sessions[-1].date())
    added = 0
    for decision in wanted:
        key = str(decision.date())
        if key in stored and key != newest:
            continue
        found = point(data, decision)
        if found:
            stored[key] = found
            added += 1
            if verbose:
                print("valuation %s: P/E %.2f over %d companies"
                      % (key, found["pe"], found["companies"]), flush=True)

    # The newest session replaces whatever older "newest" point preceded it
    # off the 21-session grid, so the series does not keep one per evening.
    grid = {str(d.date()) for d in wanted}
    points = sorted((p for k, p in stored.items()
                     if k in grid and p.get("companies", 0) >= MIN_COMPANIES),
                    key=lambda p: p["date"])

    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE, "w", encoding="utf-8") as handle:
        json.dump({"points": points,
                   "computed_at": datetime.now().isoformat(timespec="seconds")},
                  handle, ensure_ascii=False)
    return {"added": added, "points": len(points)}


def _percentile(values, x):
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return round((below + 0.5 * equal) / len(values) * 100, 1)


def _quantile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    k = (len(ordered) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _sectors(points):
    """Each industry's aggregate P/E now, against its own history.

    Cheapest against itself first. A point counts for an industry only with
    SECTOR_MIN_COMPANIES companies and a positive total profit (a loss-making
    industry has no P/E), and an industry is listed only with
    SECTOR_MIN_POINTS such points, the newest among them.
    """

    if not points:
        return []
    series = {}
    for p in points:
        for name, (cap, profit, n) in (p.get("sectors") or {}).items():
            if n >= SECTOR_MIN_COMPANIES and profit > 0 and cap > 0:
                series.setdefault(name, []).append((p["date"], cap / profit, n))
    newest = points[-1]["date"]
    out = []
    for name, rows in series.items():
        if len(rows) < SECTOR_MIN_POINTS or rows[-1][0] != newest:
            continue
        values = [pe for _d, pe, _n in rows]
        out.append({"sector": name, "pe": round(values[-1], 2),
                    "median_pe": round(_quantile(values, 0.5), 2),
                    "percentile": _percentile(values, values[-1]),
                    "companies": rows[-1][2], "points": len(rows)})
    out.sort(key=lambda r: (r["percentile"], r["sector"]))
    return out


def summary(opportunity=None):
    """What the panel shows: now, against history, against bills."""

    points = load().get("points", [])
    if not points:
        return {"available": False,
                "reason": "تاریخچه هنوز ساخته نشده؛ کار عصرگاهی آن را می‌سازد"}

    import fundamental_outlook as fo

    opportunity = opportunity or fo.opportunity_rate()
    now = points[-1]
    history = [p["pe"] for p in points]
    rate = opportunity["rate_pct"]
    return {
        "available": True,
        "now": {k: v for k, v in now.items() if k != "sectors"},
        "percentile": _percentile(history, now["pe"]),
        "median_pe": round(_quantile(history, 0.5), 2),
        "p20_pe": round(_quantile(history, 0.2), 2),
        "p80_pe": round(_quantile(history, 0.8), 2),
        "since": points[0]["date"],
        "series": [{"date": p["date"], "pe": p["pe"]} for p in points],
        "bill_yield_pct": rate,
        "bill_as_of": opportunity.get("as_of"),
        "yield_gap_pct": round(now["earnings_yield_pct"] - rate, 2),
        "sectors": _sectors(points),
        "sector_rules": {"min_companies": SECTOR_MIN_COMPANIES,
                         "min_points": SECTOR_MIN_POINTS},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="fill the history and refresh the newest point")
    parser.add_argument("--rebuild", action="store_true",
                        help="with --update: recompute every stored point")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if args.update:
        result = update(verbose=not args.quiet, rebuild=args.rebuild)
        print("valuation: %(added)d points computed, %(points)d stored" % result)
    print(json.dumps({k: v for k, v in summary().items()
                      if k not in ("series", "sectors")},
                     ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
