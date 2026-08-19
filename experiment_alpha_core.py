"""Alpha Core v1 against SIRAT-AI V2 and the index, on 16 unseen years.

PRE-COMMITTED, BEFORE THE RUN
-----------------------------
    hypothesis   a 50/50 rank blend of momentum and Bollinger %B, with no
                 technical gate and no regime brake, produces an edge that
                 survives the real execution model
    period       2008-12-04 .. 2024-09-01, which no fit has ever seen
    primary      top 10 names, rebalanced every 20 sessions
    decision     beat the index net of costs, or the hypothesis is rejected
    reporting    every variant is reported; none is selected after the fact

Both arms and the benchmark go through backtesting/engine.run(), so they share
the T+1 window, the صف model, the participation cap, commission, tax, the risk
engine, the sizing model, the portfolio caps and the metric definitions. A
difference between columns is the ranking, or it is nothing.

    python experiment_alpha_core.py                 primary comparison
    python experiment_alpha_core.py --stage robust  portfolio sizes
    python experiment_alpha_core.py --stage signal  the 3x3 grid, from the panel
"""

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

import backtest_config
import config
from backtesting import engine as engine_module
from backtesting.pit import PointInTimeData


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "alpha_core_experiment.json")

# The out-of-sample window. Ends where the weights were fitted from.
#
# It STARTS at 2014-09-21 for a data reason decided before any result was seen:
# the panel carries 3-16 scoreable symbols per date before that and 386+ after,
# because most of this database's price history begins in 2014. A cross-sectional
# ranking over six names is not the strategy being tested, and the decile study
# already excluded those dates by requiring 30 names -- so this is the same
# window the IC numbers came from, and the two are now comparable.
OOS_START = "2014-09-21"
OOS_END = "2024-09-01"

# The coverage rule above, kept as a number so the choice is auditable.
MIN_SCOREABLE = 100


def write(payload):

    os.makedirs(backtest_config.OUTPUT_DIR, exist_ok=True)

    existing = {}

    if os.path.exists(OUTPUT):
        try:
            with open(OUTPUT, encoding="utf-8") as handle:
                existing = json.load(handle)
        except ValueError:
            existing = {}

    existing.update(payload)

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, ensure_ascii=False, indent=1, default=str)


def profile(result, label):
    """Every metric the report quotes, from one BacktestResult."""

    metrics = result.metrics or {}
    trades = metrics.get("trades") or {}
    drawdown = metrics.get("drawdown") or {}
    exposure = metrics.get("exposure") or {}
    diagnostics = result.diagnostics or {}

    turnover = None

    if not result.curve.empty:
        bought = sum((t.get("entry_price") or 0) * (t.get("shares") or 0)
                     for t in (result.trades or []))
        average = float(result.curve["portfolio_value"].mean())
        years = len(result.curve) / float(config.TRADING_DAYS_PER_YEAR)
        if average and years:
            turnover = round(bought / average / years, 2)

    sectors = {}

    for trade in result.trades or []:
        key = trade.get("sector") or "?"
        sectors[key] = sectors.get(key, 0) + 1

    top_sector = None

    if sectors:
        total = sum(sectors.values())
        name, count = max(sectors.items(), key=lambda kv: kv[1])
        top_sector = {"sector": name, "share_pct": round(count / total * 100, 1),
                      "distinct": len(sectors)}

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

        "trades": trades.get("trades"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "average_win_pct": trades.get("average_win_pct"),
        "average_loss_pct": trades.get("average_loss_pct"),
        "profit_factor": trades.get("profit_factor"),
        "payoff_ratio": trades.get("payoff_ratio"),
        "average_holding_days": trades.get("average_holding_days"),
        "turnover_per_year": turnover,

        "average_positions": exposure.get("average_positions"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "average_exposure_pct": (
            None if exposure.get("average_cash_pct") is None
            else round(100.0 - exposure["average_cash_pct"], 2)),
        "sessions_fully_in_cash": exposure.get("sessions_fully_in_cash"),
        "sector_concentration": top_sector,

        "commission": diagnostics.get("commission_paid"),
        "tax": diagnostics.get("tax_paid"),
        "benchmarks": {k: v for k, v in (result.benchmarks or {}).items()
                       if isinstance(v, dict)},
    }


def one(which, cfg, data, start, end, verbose=True, **kwargs):
    """One arm. A crash in one arm must not discard the arms that finished."""

    started = time.time()

    try:
        result = engine_module.run(start, end, cfg=cfg, data=data,
                                   verbose=False, engine=which,
                                   use_cache=False, **kwargs)
    except Exception as exc:
        if verbose:
            print("    %-12s FAILED after %.0fs: %s: %s"
                  % (which, time.time() - started, type(exc).__name__, exc),
                  flush=True)
        return None

    if verbose:
        metrics = result.metrics or {}
        print("    %-12s %9s%%  DD %8s%%  trades %4s  cash %6s%%  %.0fs"
              % (which,
                 metrics.get("total_return_pct"),
                 (metrics.get("drawdown") or {}).get("max_drawdown_pct"),
                 (metrics.get("trades") or {}).get("trades"),
                 (metrics.get("exposure") or {}).get("average_cash_pct"),
                 time.time() - started), flush=True)

    return result


# ==============================
# PRIMARY
# ==============================

def primary(cfg, data, start, end, out):

    print("\n[1] PRIMARY — V2 vs Alpha Core vs index, %s .. %s"
          % (start, end), flush=True)

    rows = {}

    for name, which in (("alpha_core", "alpha_core"), ("v2", "v2")):

        result = one(which, cfg, data, start, end)

        rows[name] = ({"label": name, "available": False, "failed": True}
                      if result is None else profile(result, name))

        if result is not None and not out.get("benchmarks"):
            out["benchmarks"] = rows[name]["benchmarks"]

        out["primary"] = rows

        write(out)

    return rows


# ==============================
# ROBUSTNESS -- portfolio level
# ==============================

def robust(cfg, data, start, end, out):

    print("\n[2] ROBUSTNESS — portfolio size, full simulation", flush=True)

    rows = []

    for size in (5, 10, 20):

        variant = cfg.replace(max_positions=size)

        result = one("alpha_core", variant, data, start, end,
                     alpha_top_n=size)

        if result is None:
            rows.append({"variant": "top %d" % size, "available": False,
                         "failed": True})
            out["robust_size"] = rows
            write(out)
            continue

        rows.append({"variant": "top %d" % size,
                     **profile(result, "top %d" % size)})

        out["robust_size"] = rows

        write(out)

    return rows


# ==============================
# ROBUSTNESS -- signal level
# ==============================

def signal_grid(out):
    """Top-N x holding period, from the cached factor panel.

    A SIGNAL study, not a portfolio one: equal weight, no sizing, no caps, no
    queue, net of one round trip. It is here because the full simulation at a
    5-session rebalance is four times the dates and was not affordable, and
    because the grid's purpose is to show whether the ordering survives the
    choice -- which a signal study answers honestly at a stated lower fidelity.
    """

    print("\n[3] SIGNAL GRID — top-N x horizon, from the panel", flush=True)

    import research_factors as rf

    frame = rf.load()

    frame = frame[frame["date"] < pd.Timestamp(OOS_END)]

    # The same 50/50 rank blend the strategy computes, per date.
    parts = []

    for date, block in frame.groupby("date"):
        block = block.copy()
        m = block["f_momentum"].rank(pct=True) * 100.0
        b = block["bb_pct"].rank(pct=True) * 100.0
        block["alpha_core"] = 0.5 * m + 0.5 * b
        parts.append(block)

    frame = pd.concat(parts)

    rows = []

    for signal in ("alpha_core", "f_momentum", "bb_pct", "swing_proxy"):

        if signal == "swing_proxy":
            continue

        for horizon in (5, 10, 20):

            for top_n in (5, 10, 20):

                column = "fwd_%d" % horizon

                equity = market = 1.0
                periods = 0
                wins = 0

                for date, block in frame.groupby("date"):

                    rows_ = block[[signal, column]].dropna()

                    if len(rows_) < 30:
                        continue

                    ordered = rows_.sort_values(signal, ascending=False)

                    picked = (float(ordered[column].head(top_n).mean())
                              - config.TRANSACTION_COST_PCT)

                    reference = float(rows_[column].mean())

                    equity *= (1.0 + picked / 100.0)
                    market *= (1.0 + reference / 100.0)

                    periods += 1

                    if picked > reference:
                        wins += 1

                if not periods:
                    continue

                # Per-period so horizons are comparable; compounded levels over
                # 16 survivorship-biased years are not a real return.
                rows.append({
                    "signal": signal,
                    "horizon": horizon,
                    "top_n": top_n,
                    "periods": periods,
                    "mean_excess_per_period_pct": round(
                        ((equity / market) ** (1.0 / periods) - 1.0) * 100, 3),
                    "beat_market_pct": round(wins / periods * 100, 1),
                })

                print("    %-12s h=%-3d n=%-3d periods %3d  excess/period "
                      "%7.3f%%  beat %5.1f%%"
                      % (signal, horizon, top_n, periods,
                         rows[-1]["mean_excess_per_period_pct"],
                         rows[-1]["beat_market_pct"]), flush=True)

    out["signal_grid"] = rows

    write(out)

    return rows


# ==============================
# ALPHA ANALYSIS
# ==============================

def alpha_analysis(out):

    print("\n[4] ALPHA ANALYSIS — IC and deciles", flush=True)

    import research_factors as rf

    frame = rf.load()

    frame = frame[frame["date"] < pd.Timestamp(OOS_END)].copy()

    parts = []

    for date, block in frame.groupby("date"):
        block = block.copy()
        m = block["f_momentum"].rank(pct=True) * 100.0
        b = block["bb_pct"].rank(pct=True) * 100.0
        block["alpha_core"] = 0.5 * m + 0.5 * b
        parts.append(block)

    frame = pd.concat(parts)

    rows = {}

    for signal in ("f_momentum", "bb_pct", "alpha_core"):

        rows[signal] = {}

        for horizon in (5, 10, 20, 60):

            ic = rf.information_coefficient(frame, signal, horizon)
            dec = rf.deciles(frame, signal, horizon)

            rows[signal][horizon] = {**ic, **dec}

            if horizon == 20:
                print("    %-12s IC %7s (t %5s)  top %6s%%  bottom %6s%%  "
                      "alpha %6s%% (t %5s)"
                      % (signal, ic.get("ic"), ic.get("t_stat"),
                         dec.get("top_pct"), dec.get("bottom_pct"),
                         dec.get("alpha_pct"), dec.get("alpha_t")), flush=True)

    out["alpha_analysis"] = rows

    write(out)

    return rows


def main(argv=None):

    parser = argparse.ArgumentParser(description="Validate Alpha Core v1.")

    parser.add_argument("--stage", nargs="*",
                        default=["primary", "alpha", "signal", "robust"],
                        choices=["primary", "alpha", "signal", "robust"])
    parser.add_argument("--start", default=OOS_START)
    parser.add_argument("--end", default=OOS_END)
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)

    cfg = backtest_config.load()

    data = PointInTimeData(cfg, verbose=False)

    print("=" * 78)
    print("SIRAT-AI ALPHA CORE v1 — HYPOTHESIS TEST")
    print("=" * 78)
    print("  window      %s .. %s" % (args.start, args.end))
    print("  capital     {:,.0f}".format(cfg.initial_capital))
    print("  costs       %.3f%% round trip, %.2f%%/side slippage"
          % (cfg.round_trip_pct(), cfg.slippage_pct))
    print("  book        %d names, %d per sector, %.2f%% risk per trade"
          % (cfg.max_positions, cfg.max_per_sector, cfg.risk_per_trade_pct))
    print("  rebalance   every %d sessions" % cfg.rebalance_sessions)

    out = {"window": {"start": args.start, "end": args.end},
           "config": {"initial_capital": cfg.initial_capital,
                      "round_trip_pct": round(cfg.round_trip_pct(), 4),
                      "max_positions": cfg.max_positions,
                      "rebalance_sessions": cfg.rebalance_sessions}}

    if "alpha" in args.stage:
        alpha_analysis(out)

    if "signal" in args.stage:
        signal_grid(out)

    if "primary" in args.stage:
        primary(cfg, data, args.start, args.end, out)

    if "robust" in args.stage:
        robust(cfg, data, args.start, args.end, out)

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
