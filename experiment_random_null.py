"""PHASE 1 — the Random-Ranking Null Test. Pre-committed, frozen, one question.

    HYPOTHESIS   Alpha Core's ranking adds return beyond being invested at that
                 exposure and turnover.
    DATASET      the stored point-in-time database, universe = listed equities
    WINDOW       2014-09-21 .. 2024-09-01, 120 decision dates
    CONTROL      Alpha Core (50% momentum rank + 50% %B rank), hold 40, equal
                 weight, top 10 -- known CAGR 17.25%
    TREATMENT    30 seeded random rankings, seeds 1..30
    CHANGED      the ranking, and nothing else
    FROZEN       factor formulas, the 50/50 weighting, universe, holding period,
                 portfolio size, sector caps, execution, costs, seed list,
                 decision dates
    METRICS      CAGR, total return, Sharpe, max drawdown, profit factor,
                 exposure, turnover, costs -- per seed, full distribution
    PASS         control CAGR above the 95th percentile of the 30 seeds AND
                 control exposure within +/-5 points of the seed mean
    FAIL         control CAGR at or below the seed median
    DECISION     FAIL closes the Alpha Core program. No tuning, no retry.

THE GATE BEFORE THE GATE
------------------------
The control is re-run here through the shared reading cache and must reproduce
17.25% within 0.10 points. If it does not, the cache or a refactor has changed
the subject and no seeded number below means anything -- the run stops and says
so rather than reporting a comparison against a control that drifted.

    python experiment_random_null.py
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
from backtesting.alpha_core import ReadingPanel
from backtesting.pit import PointInTimeData

from experiment_alpha_core import OOS_END, OOS_START, profile


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "random_null_test.json")

# ---- frozen before the first run -----------------------------------------
SEEDS = tuple(range(1, 31))

HOLD = 40
TOP_N = 10

CONTROL_CAGR = 17.25
CONTROL_TOLERANCE = 0.10


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


def run_arm(label, cfg, data, panel, start, end, ranking=None, seed=None):

    started = time.time()

    try:
        result = engine_module.run(
            start, end, cfg=cfg, data=data, verbose=False,
            engine="alpha_core", use_cache=False,
            hold_sessions=HOLD, equal_weight=True, alpha_top_n=TOP_N,
            alpha_ranking=ranking, alpha_seed=seed, alpha_panel=panel,
        )
    except Exception as exc:
        print("    %-16s FAILED %s: %s" % (label, type(exc).__name__, exc),
              flush=True)
        return None

    row = profile(result, label)

    print("    %-16s CAGR %7s%%  total %9s%%  DD %8s%%  Sharpe %6s  "
          "PF %5s  exp %5s%%  %.0fs"
          % (label, row["cagr_pct"], row["total_return_pct"],
             row["max_drawdown_pct"], row["sharpe"], row["profit_factor"],
             row["average_exposure_pct"], time.time() - started), flush=True)

    return row


def percentile(values, q):
    return float(np.percentile(np.array(values, dtype=float), q))


def main(argv=None):

    parser = argparse.ArgumentParser(description="Random-Ranking Null Test")

    parser.add_argument("--start", default=OOS_START)
    parser.add_argument("--end", default=OOS_END)
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)

    cfg = backtest_config.load()

    data = PointInTimeData(cfg, verbose=False)

    panel = ReadingPanel()

    print("=" * 78)
    print("PHASE 1 — RANDOM-RANKING NULL TEST")
    print("=" * 78)
    print("  window     %s .. %s" % (args.start, args.end))
    print("  control    Alpha Core, hold %d, equal weight, top %d"
          % (HOLD, TOP_N))
    print("  treatment  %d seeded random rankings, seeds %d..%d"
          % (len(SEEDS), SEEDS[0], SEEDS[-1]))
    print("  frozen     formulas, weights, universe, hold, size, caps, "
          "execution, costs")
    print("  gate       control must reproduce CAGR %.2f%% +/- %.2f"
          % (CONTROL_CAGR, CONTROL_TOLERANCE))

    out = {
        "protocol": {
            "window": {"start": args.start, "end": args.end},
            "hold_sessions": HOLD, "top_n": TOP_N,
            "seeds": list(SEEDS),
            "control_cagr_expected": CONTROL_CAGR,
            "control_tolerance": CONTROL_TOLERANCE,
            "pass_rule": "control CAGR > p95 of seeds AND exposure within "
                         "+/-5 points of seed mean",
            "fail_rule": "control CAGR <= median of seeds",
        }
    }

    # ---- gate: the control, through the cache ----------------------------

    print("\n[1] CONTROL REPRODUCTION", flush=True)

    control = run_arm("control", cfg, data, panel, args.start, args.end)

    if control is None:
        print("\n  STOP: the control did not run.")
        return 1

    out["control"] = control

    write(out)

    drift = abs((control["cagr_pct"] or 0) - CONTROL_CAGR)

    print("\n  reproduced CAGR %.2f%% against expected %.2f%% — drift %.3f "
          "points" % (control["cagr_pct"], CONTROL_CAGR, drift))

    if drift > CONTROL_TOLERANCE:
        out["verdict"] = "HALTED: control did not reproduce"
        write(out)
        print("\n  STOP. The control drifted beyond the tolerance, so the "
              "subject of the\n  experiment is not the arm that produced "
              "17.25%. Find the discrepancy\n  before any seeded result is "
              "read.")
        return 2

    print("  gate passed — the panel replays the measured control exactly")
    print("  panel: %d dates cached" % len(panel))

    # ---- treatment --------------------------------------------------------

    print("\n[2] TREATMENT — %d random rankings" % len(SEEDS), flush=True)

    seeds = []

    for seed in SEEDS:

        row = run_arm("seed %02d" % seed, cfg, data, panel, args.start,
                      args.end, ranking="random", seed=seed)

        if row is not None:
            row["seed"] = seed
            seeds.append(row)

        out["seeds"] = seeds

        write(out)

    if len(seeds) < len(SEEDS):
        print("\n  WARNING: %d of %d seeds completed"
              % (len(seeds), len(SEEDS)))

    # ---- verdict ----------------------------------------------------------

    cagrs = [r["cagr_pct"] for r in seeds if r["cagr_pct"] is not None]

    exposures = [r["average_exposure_pct"] for r in seeds
                 if r["average_exposure_pct"] is not None]

    if len(cagrs) < 10:
        out["verdict"] = "INVALID: too few seeds completed"
        write(out)
        print("\n  INVALID — %d seeds is not a distribution" % len(cagrs))
        return 3

    stats = {
        "n": len(cagrs),
        "mean": round(float(np.mean(cagrs)), 3),
        "std": round(float(np.std(cagrs, ddof=1)), 3),
        "p5": round(percentile(cagrs, 5), 3),
        "p50": round(percentile(cagrs, 50), 3),
        "p95": round(percentile(cagrs, 95), 3),
        "min": round(min(cagrs), 3),
        "max": round(max(cagrs), 3),
        "exposure_mean": round(float(np.mean(exposures)), 2),
    }

    control_cagr = control["cagr_pct"]

    beaten = sum(1 for value in cagrs if control_cagr > value)

    stats["control_percentile"] = round(beaten / len(cagrs) * 100, 1)

    exposure_gap = abs((control["average_exposure_pct"] or 0)
                       - stats["exposure_mean"])

    stats["exposure_gap"] = round(exposure_gap, 2)

    if control_cagr > stats["p95"] and exposure_gap <= 5.0:
        verdict = "PASS"
    elif control_cagr <= stats["p50"]:
        verdict = "FAIL"
    else:
        verdict = "INCONCLUSIVE"

    out["distribution"] = stats
    out["verdict"] = verdict

    write(out)

    print("\n" + "=" * 78)
    print("RESULT: %s" % verdict)
    print("=" * 78)
    print("  control CAGR        %7.2f%%   exposure %5.2f%%"
          % (control_cagr, control["average_exposure_pct"]))
    print("  random n=%d         mean %6.2f%%  sd %5.2f  exposure %5.2f%%"
          % (stats["n"], stats["mean"], stats["std"], stats["exposure_mean"]))
    print("  random p5/p50/p95   %6.2f%% / %6.2f%% / %6.2f%%"
          % (stats["p5"], stats["p50"], stats["p95"]))
    print("  random min/max      %6.2f%% / %6.2f%%"
          % (stats["min"], stats["max"]))
    print("  control percentile  %5.1f" % stats["control_percentile"])
    print("  exposure gap        %5.2f points (limit 5.0)" % exposure_gap)
    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
