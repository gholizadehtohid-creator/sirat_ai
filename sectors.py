"""Per-sector (صنعت) fundamental and technical analysis.

Two things this module will not do, and the reasons are the whole design:

  IT DOES NOT SCORE A SECTOR WITH THE COMPANY MODEL. screening.py's bands are
      calibrated for one company -- "very large" is 50 trillion rials, "very
      liquid" is 20x the daily-value floor. Summing a sector's constituents
      clears every one of those bands automatically, so a sector score built
      that way would say nothing except "this is a sector". The fundamental
      read here is instead the DISTRIBUTION of the already-calibrated company
      scores: the median, and how the verdicts split. A sector where 8 of 10
      names are approved is a different proposition from one carried by a
      single cheap giant, and only the distribution shows that.

  IT DOES NOT PRETEND ITS INDEX IS THE EXCHANGE'S. TSETMC publishes sector
      indices but exposes no list of their instrument codes -- every listing
      endpoint tried returns 404 or an HTML page. So the sector index here is
      DERIVED, and `index_method` says so in every payload. It is equal-
      weighted on purpose: a capitalisation weighting would need shares
      outstanding applied backwards over the whole window, and today's share
      count is wrong for any date before the last capital increase. Weighting
      every constituent equally needs no such assumption.

Breadth is reported alongside the derived index precisely because it depends
on no construction at all -- it just counts how many constituents are above
their own moving average.

    python sectors.py
    python sectors.py --sector "فلزات اساسي"
"""

import statistics

import pandas as pd

import config
import database
import fundamentals
import screening
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from ranking import rank_stocks


def _median(values):
    clean = [v for v in values if v is not None]
    return round(statistics.median(clean), 2) if clean else None


def _total(values):
    clean = [v for v in values if v is not None]
    return sum(clean) if clean else None


def build_sector_index(histories, bars=None):
    """Equal-weighted sector index, chain-linked from daily returns, base 100.

    Each session's index move is the average of its constituents' returns for
    that session, and the levels are chained from those averages. Averaging
    RETURNS rather than normalised price levels is what lets the membership
    change without the index jumping: a share listed part-way through the
    window simply contributes nothing until it has two closes to compare, and
    a share that stops trading drops out, neither event moving the index by a
    single point.

    The obvious alternative -- normalise every constituent to its first close
    and average the levels -- forces every constituent to cover the whole
    window, so one recent listing either shortens the window for the entire
    sector or gets thrown away. On this universe that discarded 8 of 13 shares
    in عرضه برق and 3 of 5 in اطلاعات و ارتباطات. Chaining keeps them.

    Returns (frame, contributors) where the frame carries `close` and `volume`
    columns ready for the ordinary indicator pipeline.
    """

    bars = bars or config.SECTOR_INDEX_BARS

    closes = {}
    values = {}

    for ins_code, frame in histories.items():

        if frame is None or frame.empty or "close" not in frame.columns:
            continue

        tail = frame.tail(bars).dropna(subset=["date", "close"])
        tail = tail[tail["close"] > 0]

        if len(tail) < 2:
            continue

        closes[ins_code] = tail.set_index("date")["close"]

        if "value" in tail.columns:
            values[ins_code] = tail.set_index("date")["value"].fillna(0)

    if len(closes) < 2:
        return None, []

    wide = pd.DataFrame(closes).sort_index()

    if len(wide) < 2:
        return None, []

    returns = wide.pct_change()

    # A capital increase or dividend adjustment shows up in an unadjusted
    # series as a single move far outside the daily price band. It is not a
    # price change and must not become one in the index -- one افزایش سرمایه
    # would otherwise drag a whole sector down by tens of percent on a day
    # nothing happened. Those observations are dropped, not clipped: the true
    # move is unknown, and clipping would assert a limit-down that never
    # traded.
    band = config.CORPORATE_ACTION_THRESHOLD / 100.0
    returns = returns.mask(returns.abs() > band)

    contributing = returns.notna().sum(axis=1)

    mean_return = returns.mean(axis=1, skipna=True)

    # Start where the sector genuinely has breadth, so the base is not one
    # share's move.
    floor = min(config.MIN_SECTOR_CONSTITUENTS, len(closes))
    usable = contributing >= floor

    if not usable.any():
        return None, []

    start = usable.idxmax()

    mean_return = mean_return.loc[start:].fillna(0.0)

    # The first session is the base, not a move: without this the index opens
    # at 100 x (1 + that day's return) and every level is offset.
    if len(mean_return):
        mean_return.iloc[0] = 0.0

    level = (1.0 + mean_return).cumprod() * 100.0

    turnover = (
        pd.DataFrame(values).sort_index().sum(axis=1)
        if values else None
    )

    frame = pd.DataFrame({
        "date": level.index,
        "close": level.values,
    })

    if turnover is not None:
        frame["volume"] = turnover.reindex(level.index).fillna(0).values
    else:
        frame["volume"] = 0

    contributors = [
        code for code in closes
        if returns[code].loc[start:].notna().any()
    ]

    return frame.reset_index(drop=True), contributors


def analyse_sector(name, group, snapshot_by_code, info_by_code,
                   params=None, approve_score=None, bars=None):
    """Fundamental distribution + derived technical read for one sector."""

    params = params or DEFAULT_PARAMS
    bars = bars or config.SECTOR_INDEX_BARS

    members = group["members"]

    histories = {}
    rows = []

    verdicts = {
        screening.APPROVED: 0,
        screening.PROVISIONAL: 0,
        screening.REJECTED: 0,
        screening.INSUFFICIENT: 0,
    }

    for member in members:

        ins_code = member["ins_code"]

        history = database.load_history_by_inscode(ins_code, limit=bars)

        if history is not None and not history.empty:
            histories[ins_code] = history

        metrics = fundamentals.build(
            history,
            snapshot_by_code.get(ins_code),
            sector=name,
        )
        verdict = screening.screen(metrics, approve_score=approve_score)

        verdicts[verdict["verdict"]] = verdicts.get(verdict["verdict"], 0) + 1

        rows.append({
            "ins_code": ins_code,
            "symbol": member["symbol_fa"],
            "name": member["name_fa"],
            "fundamental_score": verdict["score"],
            # A score survives even when almost nothing could be computed: one
            # available component gets the whole redistributed weight, so a
            # share we know a single fact about scores 100 and would otherwise
            # sort above every genuinely-assessed name in its sector. The
            # verdict is what says whether the score means anything.
            "scored": verdict["verdict"] != screening.INSUFFICIENT,
            "verdict": verdict["verdict"],
            "pe": metrics.get("pe"),
            "earnings_yield": metrics.get("earnings_yield"),
            "market_cap": metrics.get("market_cap"),
            "avg_value_20": metrics.get("avg_value_20"),
            "volatility_60": metrics.get("volatility_60"),
            "return_60d": metrics.get("return_60d"),
            "bars": metrics.get("bars", 0),
        })

    judged = [r for r in rows if r["verdict"] != screening.INSUFFICIENT]

    # The exchange publishes its own P/E per sector on every constituent's
    # info record. It is the same figure repeated, so any constituent carrying
    # it answers for the sector -- but take the median in case of drift.
    published_sector_pe = _median([
        (info_by_code.get(r["ins_code"]) or {}).get("sector_pe")
        for r in rows
    ])

    fundamental = {
        "constituents": len(members),
        "judged": len(judged),
        "verdicts": verdicts,
        "median_score": _median([r["fundamental_score"] for r in judged]),
        "median_pe": _median([r["pe"] for r in rows]),
        "published_sector_pe": published_sector_pe,
        "median_earnings_yield": _median([r["earnings_yield"] for r in rows]),
        "median_volatility": _median([r["volatility_60"] for r in rows]),
        "total_market_cap": _total([r["market_cap"] for r in rows]),
        "total_daily_value": _total([r["avg_value_20"] for r in rows]),
        "approval_rate": (
            round(verdicts[screening.APPROVED] / len(judged), 3)
            if judged else None
        ),
    }

    technical, breadth, contributors = _technical(histories, params, bars)

    return {
        "sector_code": group["sector_code"],
        "sector_name": name,
        "fundamental": fundamental,
        "technical": technical,
        "breadth": breadth,
        "index_method": (
            f"equal-weighted, chain-linked from the daily returns of "
            f"{len(contributors)} constituents, base 100 — computed here, "
            f"NOT the exchange's published sector index"
        ),
        "index_contributors": len(contributors),
        "members": sorted(
            rows,
            key=lambda r: (r["scored"], r["fundamental_score"]),
            reverse=True
        ),
    }


def _technical(histories, params, bars):
    """Derived-index technical read plus construction-free breadth."""

    if len(histories) < config.MIN_SECTOR_CONSTITUENTS:
        return (
            {"error": f"only {len(histories)} constituents have history "
                      f"(need {config.MIN_SECTOR_CONSTITUENTS})"},
            None, []
        )

    frame, contributors = build_sector_index(histories, bars=bars)

    breadth = _breadth(histories, params)

    if frame is None or len(frame) < required_bars(params):
        return (
            {"error": "constituents share too little common history to build "
                      "a sector index"},
            breadth, contributors
        )

    enriched = calculate_indicators(frame, params)

    technical = rank_stocks(enriched)

    closes = enriched["close"]
    last = closes.iloc[-1]

    returns = {}
    for label, back in (("1d", 1), ("5d", 5), ("20d", 20), ("60d", 60)):
        if len(closes) > back:
            past = closes.iloc[-1 - back]
            returns[label] = round((last - past) / past * 100, 2) if past else None
        else:
            returns[label] = None

    return (
        {
            "score": technical["score"],
            "scored": technical.get("scored", True),
            "status": technical["status"],
            "trend": technical["trend"],
            "rsi": technical["rsi"],
            "macd": technical["macd"],
            "signals": technical["signals"],
            "breakdown": technical["breakdown"],
            "returns": returns,
            "bars": len(enriched),
            "first_date": enriched["date"].iloc[0].strftime("%Y-%m-%d"),
            "last_date": enriched["date"].iloc[-1].strftime("%Y-%m-%d"),
        },
        breadth, contributors
    )


def _breadth(histories, params):
    """How many constituents are individually in an uptrend.

    This needs no index and no weighting scheme, so it cannot be distorted by
    one. When it disagrees with the derived index, believe this.
    """

    above_slow = 0
    above_fast = 0
    counted = 0

    for frame in histories.values():

        enriched = calculate_indicators(frame, params)

        if enriched.empty:
            continue

        last = enriched.iloc[-1]

        slow = last.get("MA_SLOW")
        fast = last.get("MA_FAST")
        close = last.get("close")

        if slow is None or slow != slow or close is None or close != close:
            continue

        counted += 1
        above_slow += bool(close > slow)

        if fast is not None and fast == fast:
            above_fast += bool(close > fast)

    if not counted:
        return None

    return {
        "measured": counted,
        "above_slow_ma": above_slow,
        "above_fast_ma": above_fast,
        "pct_above_slow_ma": round(above_slow / counted * 100, 1),
        "pct_above_fast_ma": round(above_fast / counted * 100, 1),
    }


def analyse_all(params=None, approve_score=None, bars=None, min_members=None,
                progress=None):
    """Every sector with enough constituents to be worth aggregating."""

    min_members = (
        config.MIN_SECTOR_CONSTITUENTS if min_members is None else min_members
    )

    grouped = database.load_sector_members()

    if not grouped:
        return {
            "sectors": [],
            "warnings": ["No sector membership stored. Run: "
                         "python seed_import.py --sectors"],
        }

    snapshot_df = database.load_snapshot()
    snapshot_by_code = {}

    if snapshot_df is not None and not snapshot_df.empty:
        for _, row in snapshot_df.iterrows():
            snapshot_by_code[str(row.get("ins_code"))] = row.to_dict()

    info_by_code = database.load_fundamentals()

    warnings = []

    if not snapshot_by_code:
        warnings.append(
            "No market snapshot stored: sector P/E, earnings yield and market "
            "cap are unavailable. Fetch one with: "
            "python seed_import.py --snapshot"
        )

    if not info_by_code:
        warnings.append(
            "No instrument-info payloads stored, so the exchange's own "
            "published sector P/E is unavailable. Fetch with: "
            "python seed_import.py --sectors"
        )

    reports = []
    skipped = 0

    # Halted members leave the sector before it is aggregated: a company
    # frozen for months would hold its sector's median at a price and P/E
    # nobody can trade at.
    halted = database.halted_codes()
    for group in grouped.values():
        group["members"] = [m for m in group["members"]
                            if m["ins_code"] not in halted]

    for name, group in grouped.items():

        if len(group["members"]) < min_members:
            skipped += 1
            continue

        if progress:
            progress(name)

        reports.append(analyse_sector(
            name, group, snapshot_by_code, info_by_code,
            params=params, approve_score=approve_score, bars=bars
        ))

    if skipped:
        warnings.append(
            f"{skipped} sectors had fewer than {min_members} constituents and "
            f"were not aggregated — one company is not a sector."
        )

    # Rank by the technical read, sectors without one last.
    reports.sort(
        key=lambda r: (
            (r["technical"] or {}).get("score") is not None,
            (r["technical"] or {}).get("score") or 0,
        ),
        reverse=True
    )

    return {"sectors": reports, "warnings": warnings}


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(description="SIRAT AI sector analysis")
    parser.add_argument("--sector", help="show one sector in detail")
    parser.add_argument("--min-members", type=int,
                        help="minimum constituents to aggregate")
    args = parser.parse_args()

    report = analyse_all(min_members=args.min_members)

    if args.sector:

        match = next(
            (s for s in report["sectors"] if args.sector in s["sector_name"]),
            None
        )

        if not match:
            print(f"No sector matching '{args.sector}'.")
            raise SystemExit(1)

        fundamental = match["fundamental"]
        technical = match["technical"] or {}

        print("=" * 78)
        print(f"{match['sector_name']}   (sector {match['sector_code']})")
        print("=" * 78)
        print(f"constituents      {fundamental['constituents']}"
              f"   judged {fundamental['judged']}")
        print(f"verdicts          {fundamental['verdicts']}")
        print(f"median fund score {fundamental['median_score']}")
        print(f"median P/E        {fundamental['median_pe']}"
              f"   (exchange sector P/E: {fundamental['published_sector_pe']})")
        print(f"median yield      {fundamental['median_earnings_yield']}%")
        # 1e13 rials = 1000 miliard toman = 1 همت, the unit the market is
        # actually quoted in here.
        print(f"total market cap  {(fundamental['total_market_cap'] or 0)/1e13:,.0f} همت")
        print(f"total daily value {(fundamental['total_daily_value'] or 0)/1e10:,.1f} همت/day")

        print()
        if technical.get("error"):
            print(f"technical         unavailable: {technical['error']}")
        else:
            print(f"technical score   {technical['score']}/100  {technical['status']}")
            print(f"trend             {technical['trend']}   RSI {technical['rsi']}")
            print(f"returns           " + "   ".join(
                f"{k} {v}%" for k, v in technical["returns"].items()))
            print(f"signals           " + "; ".join(technical["signals"]))

        breadth = match["breadth"]
        if breadth:
            print(f"breadth           {breadth['above_slow_ma']}/"
                  f"{breadth['measured']} above the slow MA "
                  f"({breadth['pct_above_slow_ma']}%)")

        print(f"\nindex             {match['index_method']}")

        print("\ntop constituents by fundamental score:")
        print(f"  {'SYMBOL':<12}{'FUND':>5}  {'VERDICT':<18}{'P/E':>8}")
        for row in match["members"][:15]:
            pe = f"{row['pe']:.1f}" if row["pe"] else "-"
            score = f"{row['fundamental_score']}" if row["scored"] else "-"
            print(f"  {row['symbol']:<12}{score:>5}  "
                  f"{row['verdict']:<18}{pe:>8}")

        raise SystemExit(0)

    print("=" * 96)
    print("SIRAT AI - SECTOR ANALYSIS (صنایع)")
    print("=" * 96)
    print(f"{'SECTOR':<34}{'N':>4}{'FUND':>6}{'APPR':>6}{'P/E':>7}"
          f"{'TECH':>6}  {'TREND':<16}{'BREADTH':>9}{'60D':>9}{'USED':>6}")
    print("-" * 96)

    for sector in report["sectors"]:

        fundamental = sector["fundamental"]
        technical = sector["technical"] or {}
        breadth = sector["breadth"] or {}

        name = sector["sector_name"][:32]
        pe = fundamental["published_sector_pe"] or fundamental["median_pe"]
        approval = fundamental["approval_rate"]
        returns = (technical.get("returns") or {}).get("60d")

        median_score = fundamental["median_score"]

        column_score = "-" if median_score is None else f"{median_score:.0f}"
        column_approval = "-" if approval is None else f"{approval * 100:.0f}%"
        column_pe = "-" if not pe else f"{pe:.1f}"
        column_tech = "-" if technical.get("error") else str(technical.get("score", "-"))
        column_trend = (technical.get("trend") or "n/a")[:16]
        column_breadth = (
            f"{breadth['pct_above_slow_ma']}%" if breadth else "-"
        )
        column_return = "-" if returns is None else f"{returns:+.1f}%"

        print(
            f"{name:<34}{fundamental['constituents']:>4}{column_score:>6}"
            f"{column_approval:>6}{column_pe:>7}{column_tech:>6}  "
            f"{column_trend:<16}{column_breadth:>9}{column_return:>9}"
            f"{sector['index_contributors']:>6}"
        )

    for warning in report["warnings"]:
        print(f"\nNOTE: {warning}")
