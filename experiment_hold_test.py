"""Alpha Core Hold Test v1 -- where exactly does the alpha disappear?

THE ISOLATION, STATED BEFORE THE RUN
------------------------------------
The ranking is validated: +1.97% decile alpha, t = 5.68, on 120 unseen dates,
positive in 9 of 9 basket/horizon cells. The same ranking through the full
portfolio system returned -64.0%. Something between the signal and the book
consumes more than the whole edge.

There are three candidates and this run separates them by changing ONE thing at
a time:

    A   stop exit + risk sizing     the shipped arrangement            (measured)
    A'  stop exit + equal weight    isolates the SIZING model
    B   time exit + equal weight    isolates the EXIT model
    B-n time exit at 10/20/40/60    does the term itself matter

Everything else is the same object in every arm: the same frames, the same
ranking, the same T+1 window, the same صف model, the same participation cap,
the same commission and tax, the same allocator and the same metrics.

WHAT A TIME EXIT IS NOT
-----------------------
It is not a strategy proposal. A book with no stop has no defined loss, and
nothing here should be read as a recommendation to trade without one. It is a
measurement instrument: if the edge reappears when the stop stops interrupting,
the stop is the thing to redesign; if it does not, the stop was never the
problem and the execution layer is.

    python experiment_hold_test.py
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

from experiment_alpha_core import OOS_END, OOS_START, profile


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "hold_test.json")


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


def one(label, cfg, data, start, end, **kwargs):

    started = time.time()

    try:
        result = engine_module.run(start, end, cfg=cfg, data=data,
                                   verbose=False, engine="alpha_core",
                                   use_cache=False, **kwargs)
    except Exception as exc:
        print("    %-22s FAILED %s: %s" % (label, type(exc).__name__, exc),
              flush=True)
        return None

    row = profile(result, label)

    diagnostics = result.diagnostics or {}

    row["time_exits"] = diagnostics.get("time_exits", 0)
    row["stopped_out"] = diagnostics.get("stopped_out", 0)
    row["target_hit"] = diagnostics.get("target_hit", 0)
    row["rotated_out"] = diagnostics.get("rotated_out", 0)

    print("    %-22s %9s%%  DD %8s%%  Sharpe %6s  PF %5s  trades %4s  "
          "exposure %5s%%  %.0fs"
          % (label, row["total_return_pct"], row["max_drawdown_pct"],
             row["sharpe"], row["profit_factor"], row["trades"],
             row["average_exposure_pct"], time.time() - started), flush=True)

    return row


def main(argv=None):

    parser = argparse.ArgumentParser(description="Alpha Core Hold Test v1")

    parser.add_argument("--start", default=OOS_START)
    parser.add_argument("--end", default=OOS_END)
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)

    cfg = backtest_config.load()

    data = PointInTimeData(cfg, verbose=False)

    print("=" * 78)
    print("ALPHA CORE HOLD TEST v1")
    print("=" * 78)
    print("  window     %s .. %s" % (args.start, args.end))
    print("  capital    {:,.0f}".format(cfg.initial_capital))
    print("  costs      %.3f%% round trip" % cfg.round_trip_pct())
    print("  book       top %d, %d per sector, rebalance every %d sessions"
          % (cfg.max_positions, cfg.max_per_sector, cfg.rebalance_sessions))

    out = {"window": {"start": args.start, "end": args.end}}

    rows = {}

    print("\n[1] EXIT MODEL — one change at a time", flush=True)

    # A' -- the shipped exit, equal sizing. Isolates the sizing model alone.
    rows["stop_equal"] = one("A' stop + equal wt", cfg, data, args.start,
                             args.end, limit=args.limit, equal_weight=True)

    write({"arms": rows})

    # B -- the experiment. Time exit, equal sizing.
    rows["hold_20"] = one("B  hold 20 sessions", cfg, data, args.start,
                          args.end, limit=args.limit, hold_sessions=20,
                          equal_weight=True)

    write({"arms": rows})

    print("\n[2] HOLDING PERIOD — all reported, none selected", flush=True)

    for hold in (10, 40, 60):

        rows["hold_%d" % hold] = one("B  hold %d sessions" % hold, cfg, data,
                                     args.start, args.end, limit=args.limit,
                                     hold_sessions=hold, equal_weight=True)

        write({"arms": rows})

    out["arms"] = rows

    # The benchmark travels with any completed arm.
    for row in rows.values():
        if row and row.get("benchmarks"):
            out["benchmarks"] = row["benchmarks"]
            break

    write(out)

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
