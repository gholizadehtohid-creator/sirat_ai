"""E33: would buying the best company of each cheaper-than-usual industry pay?

Preregistered in backtest_output/E33_preregistration.json before the first
run. Execution, costs, the decision grid and the statistics are E30's,
imported from examples/outlook_backtest.py; what differs is the choice:

  an industry is "cheaper than usual" when the panel's market-valuation card
  would say so -- its aggregate trailing P/E (market_valuation.point) below
  the 25th percentile of its own history -- and from each such industry the
  company with the highest fundamentals-card score is bought.

    python examples/cheap_sector_backtest.py
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import backtest_config  # noqa: E402
import config  # noqa: E402
import financials  # noqa: E402
import fundamental_outlook as fo  # noqa: E402
import fundamental_scoring  # noqa: E402
import market_valuation as mv  # noqa: E402
import outlook_backtest as base  # noqa: E402
from backtesting.execution import ExecutionModel  # noqa: E402
from backtesting.pit import PointInTimeData  # noqa: E402

ALPHA = 0.05 / 4                    # the fourth test on this window
THRESHOLD = 25.0                    # panel.js valuationWord: "cheaper than usual"
HISTORY_FROM = "2022-01-01"         # points exist from 2022-05 (MIN_COMPANIES)
NULL_DRAWS = 1000
SEED = 33
GROWTH_KEYS = ("revenue_growth", "profit_growth", "eps_growth",
               "operating_profit_growth", "equity_growth")
RESULTS = os.path.join(os.path.dirname(HERE), "backtest_output",
                       "E33_results.json")


# ---- the fundamentals card's score, point in time -------------------------

def _has_income(items):
    items = items or {}
    return any(items.get(k) is not None
               for k in ("revenue", "net_profit", "operating_profit"))


def card_score(history, result, price, shares):
    """codal_sync.metrics_for's score on what was public at the decision."""

    ordered = sorted(history, key=lambda r: (str(r["period_end"]),
                                             r["period_months"] or 0),
                     reverse=True)
    record = next((r for r in ordered if _has_income(r.get("items"))), None)
    if record is None:
        return None
    items = record.get("items") or {}
    ratios = financials.ratios(items, previous=None,
                               market={"close": price, "total_shares": shares},
                               months=record.get("period_months"))
    stored = record.get("ratios") or {}
    for key in GROWTH_KEYS:
        if ratios.get(key) is None:
            ratios[key] = financials.comparable_growth(stored.get(key))
    if result["ttm"]["profit"] is not None:
        # trailing_multiples: the trailing year where one can be built.
        pe = result["valuation"]["pe_ttm"]
        ratios["pe"] = pe if pe is not None and pe < config.PE_ABSURD else None
        ratios["earnings_yield"] = (round(100.0 / ratios["pe"], 2)
                                    if ratios["pe"] else None)
        cap, revenue = result["valuation"]["market_cap"], result["ttm"]["revenue"]
        if cap and revenue and revenue > 0:
            ratios["ps"] = round(cap / revenue, 2)
    one_off = fo.one_off_reason(items)
    scored = fundamental_scoring.score(
        dict(ratios, pe=None, earnings_yield=None) if one_off else ratios)
    return scored["score"]


def eligible_on(data, decision):
    """{code: {sector, score, ey_ttm}} on `decision`."""

    out = {}
    for code, inst in data.instruments().items():
        sector = inst["sector"]
        if not sector or sector in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            continue
        if data.bar_on(code, decision) is None:
            continue
        if data.superseded_on(code, decision):
            continue
        if (data.average_value(code, decision, sessions=20) or 0) < base.MIN_VALUE:
            continue
        history = data.fundamental_history(code, decision, periods=12)
        if not history:
            continue
        newest = max(history, key=lambda r: (str(r["period_end"]),
                                             r["period_months"] or 0))
        price = data.valuation_price(code, decision)
        shares = base._shares(data, code, newest, decision)
        result = fo.analyse(
            code, price=price, rows=base._rows(history),
            today=base._jalali(decision), monthly_rows=[], dividend=None,
            shares=shares, sector=sector, portfolio=None,
            opportunity=base.NO_HURDLE)
        if not result or result["kind"] != fo.OPERATING:
            continue
        age = result["latest"]["age_months"]
        if age is not None and age > fo.STALE_MONTHS:
            continue
        if fo.suspect_reason(result):
            continue
        score = card_score(history, result, price, shares)
        if score is None:
            continue
        pe = result["valuation"]["pe_ttm"]
        out[code] = {"sector": sector, "score": float(score),
                     "ey_ttm": 100.0 / pe if pe else None}
    return out


# ---- which industries are cheaper than usual ------------------------------

def industry_reads(points, decision, relative=False):
    """{industry: percentile of its P/E at `decision` in its own history},
    for the industries market_valuation._sectors would list on that day."""

    day = str(decision.date())
    series = {}
    for p in points:
        if p["date"] > day:
            break
        for name, (cap, profit, n) in (p.get("sectors") or {}).items():
            if n >= mv.SECTOR_MIN_COMPANIES and profit > 0 and cap > 0:
                value = cap / profit
                if relative:
                    value /= p["pe"]
                series.setdefault(name, []).append((p["date"], value))
    out = {}
    for name, rows in series.items():
        if len(rows) < mv.SECTOR_MIN_POINTS or rows[-1][0] != day:
            continue
        values = [v for _d, v in rows]
        out[name] = mv._percentile(values, values[-1])
    return out


# ---- books ------------------------------------------------------------------

def best_per_industry(p, industries, info, key="score"):
    """The best buyable name of each industry in `industries`."""

    if key == "score":
        order = p["names"]                      # already by score, best first
    else:
        order = sorted(p["names"], key=lambda n: -(info[n[1]]["ey_ttm"] or -1e9))
    chosen, seen = [], set()
    for name in order:
        sector = info[name[1]]["sector"]
        if sector in industries and sector not in seen:
            seen.add(sector)
            chosen.append(name)
    return chosen


def run_book(periods, choose):
    """Gross/net per period; `choose(p)` returns the names held, or None to
    hold every eligible name (the control) that period."""

    held_before = set()
    gross, net, counts, active = [], [], [], []
    for p in periods:
        chosen = choose(p)
        on = bool(chosen)
        if not chosen:
            chosen = p["names"]
        if not chosen:
            gross.append(0.0)
            net.append(0.0)
            counts.append(0)
            active.append(False)
            held_before = set()
            continue
        g = float(np.mean([r for _, _, r in chosen]))
        codes = {c for _, c, _ in chosen}
        changed = 1.0 - len(codes & held_before) / float(len(codes))
        gross.append(g)
        net.append((1 + g) * (1 - changed * base.ROUND_TRIP) - 1)
        counts.append(len(chosen) if on else 0)
        active.append(on)
        held_before = codes
    return {"gross": gross, "net": net, "counts": counts, "active": active}


def compare(periods, strategy, control):
    excess = [math.log1p(a) - math.log1p(b)
              for a, b in zip(strategy["net"], control["net"])]
    half = len(excess) // 2
    mean, t, p = base.newey_west_t(excess)
    active = [c for c, a in zip(strategy["counts"], strategy["active"]) if a]
    return {
        "periods": len(periods),
        "active_periods": int(sum(strategy["active"])),
        "mean_names_when_active": round(float(np.mean(active)), 1) if active else 0,
        "strategy_cumulative_net_pct": round(base.cumulative(strategy["net"]) * 100, 1),
        "strategy_annualised_net_pct": round((base.annualised(strategy["net"], periods) or 0) * 100, 1),
        "control_cumulative_net_pct": round(base.cumulative(control["net"]) * 100, 1),
        "control_annualised_net_pct": round((base.annualised(control["net"], periods) or 0) * 100, 1),
        "excess_log_per_period_pct": round(mean * 100, 3),
        "newey_west_t": round(t, 2), "p_one_sided": round(p, 4),
        "first_half_mean_excess_pct": round(float(np.mean(excess[:half])) * 100, 3),
        "second_half_mean_excess_pct": round(float(np.mean(excess[half:])) * 100, 3),
        "beat_control_share_of_active_periods": round(float(np.mean(
            [e > 0 for e, a in zip(excess, strategy["active"]) if a])), 3) if active else None,
    }


def random_null(periods, strategy, draws, seed):
    rng = random.Random(seed)
    totals = []
    for _ in range(draws):
        total = 1.0
        for p, n, on in zip(periods, strategy["counts"], strategy["active"]):
            pool = p["names"]
            if not pool:
                continue
            pick = rng.sample(pool, min(n, len(pool))) if on else pool
            total *= 1 + float(np.mean([r for _, _, r in pick]))
        totals.append(total - 1)
    return totals


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    data = PointInTimeData(backtest_config.BacktestConfig(), verbose=False)
    execution = ExecutionModel(backtest_config.BacktestConfig())
    sessions = data.sessions()
    grid = data.rebalance_dates(base.START, base.END, base.EVERY)

    # The industries' history: market_valuation.point on every grid session
    # from HISTORY_FROM (none qualifies before 2022-05).
    points = []
    for n, d in enumerate([d for d in grid if str(d.date()) >= HISTORY_FROM], 1):
        point = mv.point(data, d)
        if point:
            points.append(point)
        if n % 10 == 0:
            print("  history %d (%s: %s)" % (n, d.date(), "point" if point else "-"),
                  flush=True)

    reads = {d: industry_reads(points, d) for d in grid}
    relative = {d: industry_reads(points, d, relative=True) for d in grid}
    decisions = [d for d in grid if reads[d]]
    print("window: %s .. %s, %d decisions" % (decisions[0].date(),
                                              decisions[-1].date(),
                                              len(decisions)), flush=True)

    info, ranked = {}, {}
    for n, d in enumerate(decisions, 1):
        info[d] = eligible_on(data, d)
        ranked[d] = sorted(((v["score"], c) for c, v in info[d].items()),
                           reverse=True)
        if n % 10 == 0:
            print("  scored %d/%d (%s: %d eligible)" % (n, len(decisions), d.date(),
                                                         len(ranked[d])), flush=True)

    periods = base.period_returns(data, execution, sessions, decisions, ranked)

    def cheap(d, threshold=THRESHOLD, table=None):
        return {s for s, pct in (table or reads)[d].items() if pct < threshold}

    def picks(key="score", threshold=THRESHOLD, table=None, every=False):
        def choose(p):
            d = p["decision"]
            industries = ({info[d][c]["sector"] for _s, c, _r in p["names"]}
                          if every else cheap(d, threshold, table))
            return best_per_industry(p, industries, info[d], key)
        return choose

    control = run_book(periods, lambda p: None)
    strategy = run_book(periods, picks())
    primary = compare(periods, strategy, control)
    null = random_null(periods, strategy, NULL_DRAWS, SEED)
    strategy_gross = base.cumulative(strategy["gross"])
    percentile = 100.0 * sum(1 for x in null if x < strategy_gross) / len(null)

    accept = (primary["p_one_sided"] < ALPHA and percentile >= 90
              and primary["first_half_mean_excess_pct"] > 0
              and primary["second_half_mean_excess_pct"] > 0)

    def all_of_cheap(p):
        d = p["decision"]
        industries = cheap(d)
        return [n for n in p["names"] if info[d][n[1]]["sector"] in industries]

    diagnostics = {
        "every_company_of_the_cheap_industries":
            compare(periods, run_book(periods, all_of_cheap), control),
        "best_company_of_every_industry":
            compare(periods, run_book(periods, picks(every=True)), control),
        "threshold_50th_percentile":
            compare(periods, run_book(periods, picks(threshold=50.0)), control),
        "cheap_relative_to_the_market":
            compare(periods, run_book(periods, picks(table=relative)), control),
        "cheapest_company_of_each_cheap_industry":
            compare(periods, run_book(periods, picks(key="ey_ttm")), control),
    }

    index_returns = base.tedpix(data, periods)
    bill_returns = base.bills(periods)
    symbols = {c: i["symbol"] for c, i in data.instruments().items()}
    chooser = picks()
    per_period = []
    for p, s, c, t in zip(periods, strategy["net"], control["net"], index_returns):
        d = p["decision"]
        per_period.append({
            "decision": str(d.date()), "exit": str(p["exit"].date()),
            "eligible": len(p["names"]),
            "cheap_industries": {k: v for k, v in sorted(reads[d].items(),
                                                         key=lambda kv: kv[1])
                                 if v < THRESHOLD},
            "held": [symbols.get(code) for _s, code, _r in chooser(p)],
            "strategy_net_pct": round(s * 100, 2),
            "control_net_pct": round(c * 100, 2),
            "tedpix_pct": round(t * 100, 2)})

    result = {
        "experiment_id": "E33-best-of-cheap-industries",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "window": [str(periods[0]["start"].date()), str(periods[-1]["exit"].date())],
        "alpha": ALPHA,
        "decision": "ACCEPT" if accept else "REJECT",
        "primary": primary,
        "random_null": {"draws": NULL_DRAWS,
                        "strategy_gross_pct": round(strategy_gross * 100, 1),
                        "percentile": round(percentile, 1),
                        "null_median_pct": round(float(np.median(null)) * 100, 1),
                        "null_p90_pct": round(float(np.percentile(null, 90)) * 100, 1)},
        "benchmarks": {
            "tedpix_cumulative_pct": round(base.cumulative(index_returns) * 100, 1),
            "tedpix_annualised_pct": round((base.annualised(index_returns, periods) or 0) * 100, 1),
            "bills_30pct_cumulative_pct": round(base.cumulative(bill_returns) * 100, 1)},
        "diagnostics_not_decisive": diagnostics,
        "industry_points": len(points),
        "pit_blocked_future_reads": data.blocked_future_reads,
        "per_period": per_period,
    }

    with open(RESULTS, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=str)

    print(json.dumps({k: v for k, v in result.items() if k != "per_period"},
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
