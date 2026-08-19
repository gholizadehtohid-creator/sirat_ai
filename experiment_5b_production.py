"""EXPERIMENT 5B — PRODUCTION-PIPELINE ALPHA (SIRAT-AI v2.1).

    QUESTION    how much of the frictionless edge measured in 5A survives the
                real allocator and the real order book?
    ARM B       Alpha Core, production allocator, production execution
    ARM CB      random ranking, IDENTICAL universe/allocator/execution/costs
    GATE        recoverable_fraction = (B - CB) / (A1 - C1)

WHAT IS DIFFERENT FROM EXPERIMENT 3, AND WHY
--------------------------------------------
    no exposure engine   Experiment 3 imposed the four-signal exposure path.
                         That engine failed its holdout and is retired, so it
                         must not participate. Exposure here is whatever the
                         allocator produces from its own caps -- which is the
                         thing being measured.

    idle cash earns      cfg.credit_idle_cash=True. Experiment 3 ran with cash
                         at 0% while metrics.py charged a 23% hurdle, which is
                         why its Sharpe levels were uninterpretable. This is
                         the v2.1 correction; v2.0 results are NOT overwritten.

    counters readable    the P3 repair means blocked_buy_queue,
                         capped_by_participation, cash_analysis and
                         entry_rejections now reach the artifact instead of
                         arriving as null.

Everything else is held: same 120 decision dates, same top-10 basket, same 30
seeds, stops off so the exit rule is rotation, same costs. Alpha Core itself is
untouched -- only `alpha_ranking` differs between the arms.
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import backtest_config
import config
from backtesting import engine as engine_module
from backtesting.alpha_core import ReadingPanel
from backtesting.pit import PointInTimeData

from experiment_exposure import SEEDS, WINDOW_END, WINDOW_START, breadth_by_date, index_series
from experiment_execution_realism import profile


VERSION = "SIRAT-AI-v2.1"

OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "experiment5b_production.json")

TOP_N = 10


def run_arm(label, cfg, data, panel, dates, start, end,
            ranking=None, seed=None):
    """One arm through the unmodified production engine."""

    started = time.time()

    try:
        result = engine_module.run(
            start, end, cfg=cfg, data=data, verbose=False,
            engine=engine_module.ALPHA_CORE, use_cache=False,
            rebalance_dates=dates, equal_weight=True, alpha_top_n=TOP_N,
            alpha_ranking=ranking, alpha_seed=seed, alpha_panel=panel,
        )
    except Exception as exc:
        print("    %-20s FAILED %s: %s" % (label, type(exc).__name__, exc),
              flush=True)
        return None

    row = profile(result, label)
    row["seconds"] = round(time.time() - started, 1)
    row["seed"] = seed

    return row


def main(argv=None):

    parser = argparse.ArgumentParser(description="Experiment 5B")
    parser.add_argument("--start", default=str(WINDOW_START))
    parser.add_argument("--end", default=str(WINDOW_END))
    parser.add_argument("--seeds", type=int, default=len(SEEDS))

    args = parser.parse_args(argv)

    print("=" * 78)
    print("EXPERIMENT 5B — PRODUCTION PIPELINE  (%s)" % VERSION)
    print("=" * 78)

    cfg = backtest_config.load()

    # Stops OFF -- the exit rule is rotation, as in Experiments 2, 3 and 5A.
    # Idle cash ON -- the v2.1 correction.
    cfg = cfg.replace(check_exits_daily=False, credit_idle_cash=True)

    data = PointInTimeData(cfg, verbose=False)

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    equal = index_series("TEDPIX_EW")
    window = equal[(equal["date"] >= start) & (equal["date"] <= end)]
    session_set = set(window["date"])

    breadth = breadth_by_date()

    grid = [d for d in sorted(breadth)
            if start <= d <= end and d in session_set]

    seeds = tuple(SEEDS[:args.seeds])

    print("  version       %s" % VERSION)
    print("  window        %s .. %s" % (start.date(), end.date()))
    print("  decisions     %d (the grid of Experiments 1, 2, 3 and 5A)"
          % len(grid))
    print("  basket        top %d, equal weight" % TOP_N)
    print("  exposure      NOT imposed -- the retired exposure engine takes")
    print("                no part. Whatever the allocator produces is the")
    print("                measurement.")
    print("  idle cash     credited at %.0f%%/yr  <-- v2.1 correction"
          % (cfg.cash_annual_rate * 100))
    print("  execution     production: T+1, صف queue, participation cap,")
    print("                partial fills, sector caps, commission, tax")
    print("  costs         round trip %.3f%%, slippage %.2f%%/side"
          % (cfg.round_trip_pct(), cfg.slippage_pct))
    print("  stops         OFF; exit is rotation")
    print("  seeds         %d" % len(seeds))

    panel = ReadingPanel()

    # ---- ARM B ------------------------------------------------------------
    print("\n[1] ARM B — Alpha Core through the production pipeline",
          flush=True)

    treatment = run_arm("B alpha core", cfg, data, panel, grid, start, end)

    if treatment is None:
        raise SystemExit("Arm B failed; nothing to compare")

    print("    CAGR %7.2f%%  Sharpe %6s  DD %7.2f%%  exposure %5.2f%%  "
          "positions %.2f  turnover %s  (%ss)"
          % (treatment["cagr_pct"], treatment["sharpe"],
             treatment["max_drawdown_pct"], treatment["realized_exposure_pct"],
             treatment["average_positions"], treatment["turnover_per_year"],
             treatment["seconds"]))
    print("    panel: %d readings cached" % len(panel))

    # ---- ARM CB -----------------------------------------------------------
    print("\n[2] ARM CB — random ranking, identical everything else",
          flush=True)

    controls = []

    for seed in seeds:
        row = run_arm("CB seed %02d" % seed, cfg, data, panel, grid,
                      start, end, ranking="random", seed=seed)
        if row is None:
            continue
        controls.append(row)
        print("    seed %02d  CAGR %7.2f%%  Sharpe %6s  exposure %5.2f%%  "
              "(%ss)"
              % (seed, row["cagr_pct"], row["sharpe"],
                 row["realized_exposure_pct"], row["seconds"]), flush=True)

    if not controls:
        raise SystemExit("no control arm completed")

    # ---- distribution -----------------------------------------------------
    def spread(field):
        values = np.array([c[field] for c in controls
                           if c.get(field) is not None], dtype=float)
        if not len(values):
            return None
        return {
            "mean": round(float(values.mean()), 3),
            "median": round(float(np.median(values)), 3),
            "sd": round(float(values.std(ddof=1)), 3),
            "p5": round(float(np.percentile(values, 5)), 3),
            "p95": round(float(np.percentile(values, 95)), 3),
            "min": round(float(values.min()), 3),
            "max": round(float(values.max()), 3),
        }

    fields = ("cagr_pct", "sharpe", "max_drawdown_pct", "turnover_per_year",
              "realized_exposure_pct", "average_positions", "fill_rate_pct",
              "cost_drag_pct")

    distribution = {f: spread(f) for f in fields}

    cagrs = np.array([c["cagr_pct"] for c in controls], dtype=float)

    percentile = round(float((cagrs < treatment["cagr_pct"]).mean()) * 100, 1)

    # Empirical p: how often does random match or beat the treatment?
    p_value = round(float((cagrs >= treatment["cagr_pct"]).mean()), 4)

    print("\n[3] DISTRIBUTION")
    print("    control CAGR  mean %.2f%%  sd %.2f  p95 %.2f%%  max %.2f%%"
          % (distribution["cagr_pct"]["mean"], distribution["cagr_pct"]["sd"],
             distribution["cagr_pct"]["p95"], distribution["cagr_pct"]["max"]))
    print("    alpha CAGR    %.2f%%  -> percentile %.1f, empirical p %.4f"
          % (treatment["cagr_pct"], percentile, p_value))

    # ---- exposure neutrality gate (V1 from the 5A design) -----------------
    exposure_gap = abs(treatment["realized_exposure_pct"]
                       - distribution["realized_exposure_pct"]["mean"])

    v1_pass = bool(exposure_gap <= 1.0)

    print("\n[4] VALIDITY GATE V1 — exposure neutrality")
    print("    B %.2f%%  vs  CB mean %.2f%%  -> gap %.2fpp  (%s, limit 1.0pp)"
          % (treatment["realized_exposure_pct"],
             distribution["realized_exposure_pct"]["mean"], exposure_gap,
             "PASS" if v1_pass else "FAIL"))

    # ---- the decision gate ------------------------------------------------
    frictionless = os.path.join(backtest_config.OUTPUT_DIR,
                                "experiment5a_frictionless.json")

    recoverable = None
    gate = None

    if os.path.exists(frictionless):
        with open(frictionless, encoding="utf-8") as handle:
            fa = json.load(handle)

        a1 = fa["arms"]["A_top10"]["cagr_pct"]
        c1 = fa["controls"]["C_top10"]["cagr_mean"]

        frictionless_edge = a1 - c1
        production_edge = treatment["cagr_pct"] - distribution["cagr_pct"]["mean"]

        recoverable = {
            "A1_cagr_pct": a1,
            "C1_mean_cagr_pct": c1,
            "frictionless_edge_pp": round(frictionless_edge, 3),
            "B_cagr_pct": treatment["cagr_pct"],
            "CB_mean_cagr_pct": distribution["cagr_pct"]["mean"],
            "production_edge_pp": round(production_edge, 3),
            "recoverable_fraction": round(production_edge / frictionless_edge, 4)
            if frictionless_edge else None,
        }

        fraction = recoverable["recoverable_fraction"]

        if fraction is None:
            gate = "UNDEFINED"
        elif fraction > 0.50:
            gate = "CONTINUE TOWARD PRODUCTION"
        elif fraction >= 0.20:
            gate = "IDENTIFY STRUCTURAL LIMITATIONS"
        else:
            gate = "STOP AND REDESIGN SPECIFICATION"

        print("\n[5] DECISION GATE")
        print("    frictionless edge  A1 - C1 = %.2f - %.2f = %+.2f pp"
              % (a1, c1, frictionless_edge))
        print("    production edge    B  - CB = %.2f - %.2f = %+.2f pp"
              % (treatment["cagr_pct"], distribution["cagr_pct"]["mean"],
                 production_edge))
        print("    recoverable fraction = %+.1f%%" % (fraction * 100)
              if fraction is not None else "    undefined")
        print("    -> %s" % gate)

    payload = {
        "version": VERSION,
        "protocol": {
            "window": [str(start.date()), str(end.date())],
            "decisions": len(grid),
            "top_n": TOP_N,
            "seeds": list(seeds),
            "exposure_engine": "RETIRED — takes no part in this experiment",
            "credit_idle_cash": True,
            "cash_annual_rate": cfg.cash_annual_rate,
            "check_exits_daily": False,
            "round_trip_pct": cfg.round_trip_pct(),
            "slippage_pct": cfg.slippage_pct,
        },
        "treatment": treatment,
        "controls": controls,
        "distribution": distribution,
        "percentile": percentile,
        "empirical_p_value": p_value,
        "validity": {
            "V1_exposure_neutrality": {
                "pass": v1_pass,
                "b_exposure_pct": treatment["realized_exposure_pct"],
                "cb_mean_exposure_pct":
                    distribution["realized_exposure_pct"]["mean"],
                "gap_pp": round(exposure_gap, 3),
                "limit_pp": 1.0,
            },
        },
        "recoverable": recoverable,
        "gate": gate,
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__, "pandas": pd.__version__,
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    os.makedirs(backtest_config.OUTPUT_DIR, exist_ok=True)

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    pd.DataFrame([treatment] + controls).to_csv(
        os.path.join(backtest_config.OUTPUT_DIR, "experiment5b_arms.csv"),
        index=False, encoding="utf-8-sig")

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
