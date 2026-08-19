"""V1 against V2 against the index, through one backtest engine.

WHY THIS IS A RUNNER AND NOT AN ENGINE
--------------------------------------
It owns no simulation. Both columns come from backtesting/engine.run(), which
means the T+1 window, the صف test, the participation cap, the commission, the
tax, the slippage, the cash floor, the equity curve and every metric are
literally the same code for both. The only thing that differs between the two
runs is the argument `engine="v1"` / `engine="v2"`.

That is what makes the comparison worth reading. A V2 result produced by its own
harness could differ from V1 for a dozen reasons that have nothing to do with
the strategy -- a different cost model, a kinder fill, a Sharpe without the
risk-free rate. Here it cannot: a difference is the strategy, or it is nothing.

    python backtest_v2.py                  the full window, both engines
    python backtest_v2.py --limit 120      a fast smoke test
    python backtest_v2.py --engine v2      one engine only

WHAT IT DOES NOT DO
-------------------
No walk-forward, no Monte Carlo, no sensitivity sweep for v2. Those stages in
validation.py each re-run the engine several times, and a v2 run is minutes
rather than seconds; wiring them up would produce a command nobody waits for.
The single-window comparison is what this stage set out to produce, and the gap
is stated rather than hidden.
"""

import argparse
import json
import os
import sys
import time

import pandas as pd

import backtest_config
import config
from backtesting import engine as engine_module
from backtesting.pit import PointInTimeData


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "backtest_v1_vs_v2.json")


def _pct(value, digits=2):
    return "—" if value is None else "%.*f%%" % (digits, value)


def _num(value, digits=2):
    return "—" if value is None else "%.*f" % (digits, value)


def turnover(result, cfg):
    """Round trips per year, as a share of the book.

    Measured, not modelled: the rials that changed hands on entries divided by
    the average equity, annualised over the window. A metric the report never
    had -- and the one that says whether a cost assumption matters.
    """

    trades = result.trades or []

    if result.curve.empty:
        return None

    bought = sum((t.get("entry_price") or 0) * (t.get("shares") or 0)
                 for t in trades)

    average_equity = float(result.curve["portfolio_value"].mean())

    if not average_equity:
        return None

    sessions = len(result.curve)

    years = sessions / float(config.TRADING_DAYS_PER_YEAR)

    if years <= 0:
        return None

    return round(bought / average_equity / years, 2)


def costs(result):
    """Every rial the simulation charged, split by kind."""

    diagnostics = result.diagnostics or {}

    commission = diagnostics.get("commission_paid") or 0.0
    tax = diagnostics.get("tax_paid") or 0.0

    return {
        "commission": commission,
        "tax": tax,
        "total": commission + tax,
        # Slippage is applied to the fill PRICE by execution.py rather than
        # charged as a fee, so it has no rial line of its own. Reported as the
        # configured rate so the reader knows what was assumed.
        "slippage_pct_per_side": result.cfg.slippage_pct,
    }


def profile(result, cfg):
    """One column of the comparison table."""

    metrics = result.metrics or {}

    trades = metrics.get("trades") or {}
    drawdown = metrics.get("drawdown") or {}

    return {
        "engine": result.engine,
        "available": result.available,
        "total_return_pct": metrics.get("total_return_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "volatility_pct": metrics.get("volatility_pct"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "profit_factor": trades.get("profit_factor"),
        "average_win_pct": trades.get("average_win_pct"),
        "average_loss_pct": trades.get("average_loss_pct"),
        "trades": trades.get("trades"),
        "average_holding_days": trades.get("average_holding_days"),
        "turnover_per_year": turnover(result, cfg),
        "costs": costs(result),
        "rebalances": (result.diagnostics or {}).get("rebalances"),
        "opened": (result.diagnostics or {}).get("opened_positions"),
    }


def benchmark_of(result):
    """Buy & hold on the index, as the engine already computes it.

    The engine reports a benchmark as a total return over the window and
    nothing else -- it holds no daily equity curve for it, so there is no
    Sharpe, no drawdown and no trade count to report. The column is therefore
    mostly empty on purpose: inventing those cells would mean building a second
    benchmark model here, and a benchmark that this file computed differently
    from the engine is worse than a blank.
    """

    row = (result.benchmarks or {}).get("tedpix") or {}

    if not row or row.get("return_pct") is None:
        return None, {}

    return row.get("label", "TEDPIX"), {
        "total_return_pct": row.get("return_pct"),
        "excess_pct": row.get("excess_pct"),
        "sessions": row.get("sessions"),
    }


def run(start=None, end=None, limit=None, engines=("v1", "v2"), verbose=True,
        write=True):

    cfg = backtest_config.load()

    if limit:
        cfg = cfg.replace(universe_limit=limit)

    data = PointInTimeData(cfg, verbose=verbose)

    calendar = data.calendar()

    if len(calendar) == 0:
        print("no trading calendar: run `python seed_import.py --indices`")
        return None

    start = pd.Timestamp(start or backtest_config.DEFAULT_START)
    end = pd.Timestamp(end) if end else calendar[-1]

    if verbose:
        print("=" * 78)
        print("SIRAT-AI — V1 vs V2 PORTFOLIO BACKTEST")
        print("=" * 78)
        print("  window            %s .. %s" % (start.date(), end.date()))
        print("  initial capital   {:,.0f}".format(cfg.initial_capital))
        print("  round trip        %.3f%%   slippage %.2f%%/side"
              % (cfg.round_trip_pct(), cfg.slippage_pct))
        print("  rebalance         every %d sessions" % cfg.rebalance_sessions)
        print("  book              %d names, %d per sector"
              % (cfg.max_positions, cfg.max_per_sector))
        print("  universe          %s"
              % (limit or len(data.instruments())))
        print("  SAME engine, same costs, same execution — only the strategy "
              "differs.\n")

    results = {}

    for name in engines:

        started = time.time()

        if verbose:
            print("-" * 78)
            print("running %s ..." % name.upper())
            print("-" * 78, flush=True)

        result = engine_module.run(
            start, end, cfg=cfg, data=data, verbose=verbose,
            engine=name, limit=limit,
            # The v1 panel cache is keyed on the data fingerprint and knows
            # nothing about which engine asked, so it is left off here rather
            # than risk one engine reading the other's panel.
            use_cache=False,
        )

        results[name] = result

        if verbose:
            print("  %s finished in %.1fs — available=%s"
                  % (name, time.time() - started, result.available))

            if result.insufficient:
                for line in result.insufficient:
                    print("  INSUFFICIENT: %s" % line)

    profiles = {name: profile(result, cfg)
                for name, result in results.items()}

    reference = results.get("v2") or results.get("v1")

    benchmark_name, benchmark = benchmark_of(reference) if reference else (None, {})

    if verbose:
        report(profiles, benchmark_name, benchmark)

    payload = {
        "start": str(start.date()),
        "end": str(end.date()),
        "universe_limit": limit,
        "initial_capital": cfg.initial_capital,
        "round_trip_pct": cfg.round_trip_pct(),
        "slippage_pct": cfg.slippage_pct,
        "profiles": profiles,
        "benchmark": {"name": benchmark_name, **benchmark},
    }

    if write:
        os.makedirs(backtest_config.OUTPUT_DIR, exist_ok=True)
        with open(OUTPUT, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1,
                      default=str)
        if verbose:
            print("\n  wrote %s" % OUTPUT)

    return {"results": results, "profiles": profiles,
            "benchmark": benchmark, "payload": payload}


ROWS = (
    ("Total Return", "total_return_pct", _pct),
    ("CAGR", "cagr_pct", _pct),
    ("Annualised Volatility", "volatility_pct", _pct),
    ("Max Drawdown", "max_drawdown_pct", _pct),
    ("Sharpe", "sharpe", _num),
    ("Sortino", "sortino", _num),
    ("Calmar", "calmar", _num),
    ("Win Rate", "win_rate_pct", _pct),
    ("Profit Factor", "profit_factor", _num),
    ("Average Win", "average_win_pct", _pct),
    ("Average Loss", "average_loss_pct", _pct),
    ("Trades", "trades", lambda v: "—" if v is None else str(v)),
    ("Avg Holding (days)", "average_holding_days",
     lambda v: "—" if v is None else str(v)),
    ("Turnover / year", "turnover_per_year", _num),
)


def report(profiles, benchmark_name=None, benchmark=None):

    names = list(profiles)

    print("\n" + "=" * 78)
    print("V1 vs V2 — same window, same costs, same execution")
    print("=" * 78)

    header = "  %-24s" % "metric"

    for name in names:
        header += " %14s" % name.upper()

    if benchmark:
        header += " %14s" % "BENCHMARK"

    print(header)
    print("  " + "-" * 74)

    for label, key, render in ROWS:

        line = "  %-24s" % label

        for name in names:
            line += " %14s" % render(profiles[name].get(key))

        if benchmark:
            line += " %14s" % (render(benchmark.get(key))
                               if key in benchmark else "—")

        print(line)

    print("  " + "-" * 74)

    line = "  %-24s" % "Trading costs (rial)"

    for name in names:
        line += " %14s" % "{:,.0f}".format(profiles[name]["costs"]["total"])

    print(line)

    for label, key in (("  — commission", "commission"), ("  — tax", "tax")):
        line = "  %-24s" % label
        for name in names:
            line += " %14s" % "{:,.0f}".format(profiles[name]["costs"][key])
        print(line)

    if benchmark_name:
        print("\n  benchmark: %s (buy & hold, %s)"
              % (benchmark_name,
                 "net of one round trip" if benchmark.get("costed")
                 else "gross"))


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Run V1 and V2 through the same backtest engine.")

    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the universe, for a fast run")
    parser.add_argument("--engine", default=None, choices=["v1", "v2"],
                        help="run only this engine")
    parser.add_argument("--no-write", action="store_true", dest="no_write")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    engines = (args.engine,) if args.engine else ("v1", "v2")

    out = run(start=args.start, end=args.end, limit=args.limit,
              engines=engines, verbose=not args.quiet,
              write=not args.no_write)

    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
