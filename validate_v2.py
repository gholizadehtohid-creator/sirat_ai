"""Institutional validation of SIRAT-AI V2. Measures, never tunes.

WHAT THIS IS FOR
----------------
Deciding whether the V2 engine has evidence of edge worth allocating capital to.
It changes nothing: no weight, no threshold, no parameter. Every run below is the
shipped configuration, and the only thing that varies between runs is the
question being asked.

Both arms go through backtesting/engine.run(), so the benchmark, V1 and V2 share
the same T+1 window, the same صف model, the same participation cap, the same
commission and tax, and the same metric definitions including the risk-free
hurdle in Sharpe. A difference between columns is therefore the strategy, or it
is nothing.

    python validate_v2.py                 everything (slow -- ~90 minutes)
    python validate_v2.py --stages ab     just the headline comparison
    python validate_v2.py --limit 120     a fast smoke test of the harness

RESULTS ARE WRITTEN INCREMENTALLY to backtest_output/validation_v2.json, so a
run that is interrupted still leaves the stages it finished.
"""

import argparse
import json
import math
import os
import sys
import time

import pandas as pd

import backtest_config
import config
from backtesting import engine as engine_module
from backtesting import validation
from backtesting.pit import PointInTimeData


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "validation_v2.json")

STAGES = ("ab", "walkforward", "regime", "montecarlo", "robustness")


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# ONE RUN, PROFILED
# ==============================

def profile(result, label):
    """Every metric this report quotes, from one BacktestResult."""

    metrics = result.metrics or {}
    trades = metrics.get("trades") or {}
    drawdown = metrics.get("drawdown") or {}
    exposure = metrics.get("exposure") or {}
    diagnostics = result.diagnostics or {}

    curve = result.curve

    turnover = None
    max_weight = None
    sector_top = None

    if not curve.empty:

        bought = sum((t.get("entry_price") or 0) * (t.get("shares") or 0)
                     for t in (result.trades or []))

        average_equity = float(curve["portfolio_value"].mean())

        years = len(curve) / float(config.TRADING_DAYS_PER_YEAR)

        if average_equity and years > 0:
            turnover = round(bought / average_equity / years, 2)

    # Largest single position ever held, and the heaviest sector, as a share of
    # the book on the day it was heaviest.
    weights = []
    sectors = {}

    for trade in (result.trades or []):
        value = (trade.get("entry_price") or 0) * (trade.get("shares") or 0)
        equity = _finite(trade.get("equity_at_entry"))
        if value and equity:
            weights.append(value / equity * 100.0)
        sector = trade.get("sector") or "?"
        sectors[sector] = sectors.get(sector, 0) + 1

    if weights:
        max_weight = round(max(weights), 2)

    if sectors:
        total = sum(sectors.values())
        top = max(sectors.items(), key=lambda kv: kv[1])
        sector_top = {"sector": top[0], "trades": top[1],
                      "share_pct": round(top[1] / total * 100, 1),
                      "distinct_sectors": len(sectors)}

    return {
        "label": label,
        "engine": getattr(result, "engine", None),
        "available": result.available,
        "insufficient": result.insufficient,

        "total_return_pct": metrics.get("total_return_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "volatility_pct": metrics.get("volatility_pct"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "risk_free_pct": metrics.get("risk_free_pct"),
        "excess_over_cash_pct": metrics.get("excess_over_cash_pct"),

        "trades": trades.get("trades"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "average_win_pct": trades.get("average_win_pct"),
        "average_loss_pct": trades.get("average_loss_pct"),
        "average_win": trades.get("average_win"),
        "average_loss": trades.get("average_loss"),
        "profit_factor": trades.get("profit_factor"),
        "payoff_ratio": trades.get("payoff_ratio"),
        "expectancy_pct": trades.get("expectancy_pct"),
        "average_holding_days": trades.get("average_holding_days"),
        "max_consecutive_losses": trades.get("max_consecutive_losses"),

        "turnover_per_year": turnover,
        "average_positions": exposure.get("average_positions"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "sessions_fully_in_cash": exposure.get("sessions_fully_in_cash"),
        "max_position_weight_pct": max_weight,
        "sector_concentration": sector_top,

        "commission": diagnostics.get("commission_paid"),
        "tax": diagnostics.get("tax_paid"),
        "stopped_out": diagnostics.get("stopped_out"),
        "target_hit": diagnostics.get("target_hit"),
        "benchmarks": {k: v for k, v in (result.benchmarks or {}).items()
                       if isinstance(v, dict)},
    }


def run_engine(which, start, end, cfg, data, limit=None, verbose=True):

    started = time.time()

    result = engine_module.run(start, end, cfg=cfg, data=data, verbose=False,
                               engine=which, limit=limit,
                               use_cache=(which == "v1"))

    if verbose:
        print("    %s  %s..%s  %.0fs  available=%s"
              % (which, pd.Timestamp(start).date(), pd.Timestamp(end).date(),
                 time.time() - started, result.available), flush=True)

    return result


# ==============================
# STAGES
# ==============================

def stage_ab(cfg, data, start, end, limit, out):
    """The headline: benchmark, V1 and V2 on one window and one cost model."""

    print("\n[1/5] A/B — benchmark vs V1 vs V2", flush=True)

    results = {}

    for which in ("v1", "v2"):
        results[which] = run_engine(which, start, end, cfg, data, limit)

    out["ab"] = {which: profile(result, which.upper())
                 for which, result in results.items()}

    reference = results.get("v2")

    out["benchmarks"] = {
        name: row for name, row in (reference.benchmarks or {}).items()
        if isinstance(row, dict)
    }

    return results


def stage_walkforward(cfg, data, start, end, limit, out, windows=4):
    """Consecutive out-of-sample windows.

    NOT a parameter search: nothing is fitted here, so there is no training
    half. The question is narrower and still worth asking -- does the result
    hold up period after period, or is it one window carrying the average?
    """

    print("\n[2/5] WALK-FORWARD — consecutive windows", flush=True)

    sessions = data.sessions(start, end)

    if len(sessions) < windows * 40:
        out["walkforward"] = {"available": False,
                              "reason": "not enough sessions to split"}
        return

    size = len(sessions) // windows

    rows = []

    for index in range(windows):

        first = sessions[index * size]
        last = (sessions[(index + 1) * size - 1] if index < windows - 1
                else sessions[-1])

        fold = {"window": index + 1,
                "from": str(pd.Timestamp(first).date()),
                "to": str(pd.Timestamp(last).date())}

        for which in ("v1", "v2"):
            result = run_engine(which, first, last, cfg, data, limit)
            fold[which] = profile(result, which)
            if which == "v2":
                fold["benchmark_pct"] = (
                    (result.benchmarks or {}).get("tedpix", {}).get("return_pct"))

        rows.append(fold)

        out["walkforward"] = {"available": True, "windows": rows}
        write(out)

    out["walkforward"] = {"available": True, "windows": rows}


def stage_regime(results, data, out):
    """Trade outcomes bucketed by what the INDEX did while the trade was open.

    Deliberately not the engine's own regime label. That label is an input to
    the strategy, so grouping results by it asks "did the engine do well when it
    said the market was good", which is a question about self-consistency. The
    index's realised move over the holding window is external to the decision
    and is what a committee means by "which market did this work in".
    """

    print("\n[3/5] REGIME — outcomes by what the index did", flush=True)

    result = results.get("v2")

    if result is None or not result.available:
        out["regime"] = {"available": False, "reason": "no v2 result"}
        return

    index_frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)

    if index_frame is None or index_frame.empty:
        out["regime"] = {"available": False, "reason": "no index history"}
        return

    series = index_frame.set_index("date")["close"]

    buckets = {"bull": [], "bear": [], "sideways": []}

    for trade in result.trades or []:

        entry = pd.Timestamp(trade.get("entry_date"))
        exit_date = pd.Timestamp(trade.get("exit_date"))

        window = series.loc[(series.index >= entry) & (series.index <= exit_date)]

        if len(window) < 2:
            continue

        move = (float(window.iloc[-1]) / float(window.iloc[0]) - 1.0) * 100.0

        key = "bull" if move > 2.0 else ("bear" if move < -2.0 else "sideways")

        buckets[key].append({"pnl_pct": trade.get("pnl_pct"),
                             "index_pct": move})

    summary = {}

    for key, rows in buckets.items():

        values = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]

        if not values:
            summary[key] = {"trades": 0}
            continue

        wins = [v for v in values if v > 0]

        index_moves = [r["index_pct"] for r in rows]

        summary[key] = {
            "trades": len(values),
            "mean_pct": round(sum(values) / len(values), 2),
            "median_pct": round(sorted(values)[len(values) // 2], 2),
            "win_rate_pct": round(len(wins) / len(values) * 100, 1),
            "worst_pct": round(min(values), 2),
            "best_pct": round(max(values), 2),
            "mean_index_pct": round(sum(index_moves) / len(index_moves), 2),
            # The number that matters: did the trade beat simply holding the
            # index over the same days?
            "mean_excess_vs_index_pct": round(
                sum(values) / len(values)
                - sum(index_moves) / len(index_moves), 2),
        }

    out["regime"] = {"available": True, "buckets": summary}


def stage_montecarlo(results, out):
    """Resample the trades. Shuffle asks about order; bootstrap about the set."""

    print("\n[4/5] MONTE CARLO", flush=True)

    result = results.get("v2")

    if result is None or not result.available:
        out["montecarlo"] = {"available": False}
        return

    out["montecarlo"] = {
        "bootstrap": validation.monte_carlo(result, runs=5000,
                                            method="bootstrap"),
        "shuffle": validation.monte_carlo(result, runs=5000, method="shuffle"),
    }

    # A confidence interval on the mean trade return, from the trades
    # themselves. Reported beside the Monte Carlo because they answer the same
    # question with different assumptions and a reader should see both.
    values = [float(t["pnl_pct"]) for t in result.trades
              if t.get("pnl_pct") is not None]

    if len(values) >= 5:

        mean = sum(values) / len(values)

        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)

        stderr = math.sqrt(variance / len(values))

        out["montecarlo"]["mean_trade_pct"] = round(mean, 3)
        out["montecarlo"]["stderr"] = round(stderr, 3)
        out["montecarlo"]["t_stat"] = (round(mean / stderr, 2)
                                       if stderr > 0 else None)
        out["montecarlo"]["ci95"] = [round(mean - 1.96 * stderr, 3),
                                     round(mean + 1.96 * stderr, 3)]
        out["montecarlo"]["n"] = len(values)


def stage_robustness(cfg, data, start, end, limit, out):
    """Does the result survive a different book size or a different start?"""

    print("\n[5/5] ROBUSTNESS", flush=True)

    rows = []

    for size in (10, 20, 30):

        variant = cfg.replace(max_positions=size)

        result = run_engine("v2", start, end, variant, data, limit)

        rows.append({"variant": "%d names" % size,
                     **profile(result, "size %d" % size)})

        out["robustness"] = rows
        write(out)

    # A later start, so the result does not rest on one entry point.
    sessions = data.sessions(start, end)

    if len(sessions) > 260:

        later = sessions[len(sessions) // 3]

        result = run_engine("v2", later, end, cfg, data, limit)

        rows.append({"variant": "start %s" % pd.Timestamp(later).date(),
                     **profile(result, "later start")})

    out["robustness"] = rows


# ==============================
# DRIVER
# ==============================

def write(out):
    os.makedirs(backtest_config.OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=1, default=str)


def run(start=None, end=None, limit=None, stages=None, verbose=True):

    stages = set(stages or STAGES)

    cfg = backtest_config.load()

    if limit:
        cfg = cfg.replace(universe_limit=limit)

    data = PointInTimeData(cfg, verbose=False)

    calendar = data.calendar()

    if len(calendar) == 0:
        print("no trading calendar; run seed_import.py --indices")
        return None

    start = pd.Timestamp(start or backtest_config.DEFAULT_START)
    end = pd.Timestamp(end) if end else calendar[-1]

    started = time.time()

    print("=" * 78)
    print("SIRAT-AI V2 — VALIDATION")
    print("=" * 78)
    print("  window          %s .. %s" % (start.date(), end.date()))
    print("  capital         {:,.0f}".format(cfg.initial_capital))
    print("  round trip      %.3f%%   slippage %.2f%%/side"
          % (cfg.round_trip_pct(), cfg.slippage_pct))
    print("  book            %d names, %d per sector, risk %.2f%%/trade"
          % (cfg.max_positions, cfg.max_per_sector, cfg.risk_per_trade_pct))
    print("  rebalance       every %d sessions" % cfg.rebalance_sessions)
    print("  universe        %s" % (limit or len(data.instruments())))
    print("  stages          %s" % ", ".join(sorted(stages)))

    out = {
        "window": {"start": str(start.date()), "end": str(end.date())},
        "config": {
            "initial_capital": cfg.initial_capital,
            "round_trip_pct": round(cfg.round_trip_pct(), 4),
            "slippage_pct": cfg.slippage_pct,
            "max_positions": cfg.max_positions,
            "max_per_sector": cfg.max_per_sector,
            "risk_per_trade_pct": cfg.risk_per_trade_pct,
            "rebalance_sessions": cfg.rebalance_sessions,
            "universe_limit": limit,
            "risk_free_rate_pct": config.RISK_FREE_RATE * 100,
        },
    }

    results = {}

    if "ab" in stages:
        results = stage_ab(cfg, data, start, end, limit, out)
        write(out)

    if "walkforward" in stages:
        stage_walkforward(cfg, data, start, end, limit, out)
        write(out)

    if "regime" in stages and results:
        stage_regime(results, data, out)
        write(out)

    if "montecarlo" in stages and results:
        stage_montecarlo(results, out)
        write(out)

    if "robustness" in stages:
        stage_robustness(cfg, data, start, end, limit, out)
        write(out)

    out["seconds"] = round(time.time() - started, 1)

    write(out)

    print("\n  finished in %.0fs — wrote %s" % (out["seconds"], OUTPUT))

    return out


def main(argv=None):

    parser = argparse.ArgumentParser(description="Validate SIRAT-AI V2.")

    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--stages", nargs="*", default=None, choices=STAGES)
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    out = run(start=args.start, end=args.end, limit=args.limit,
              stages=args.stages, verbose=not args.quiet)

    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
