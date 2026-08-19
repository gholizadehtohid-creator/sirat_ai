"""EXPERIMENT 3 — EXECUTION-REALISTIC CONDITIONAL SELECTION. The final gate.

    HYPOTHESIS  Alpha Core's advantage over random selection survives the real
                execution model
    TREATMENT   frozen exposure engine + frozen Alpha Core + production engine
    CONTROL     frozen exposure engine + random selection + the same engine,
                30 pre-registered seeds
    CHANGED     stock identity, and nothing else
    PRIMARY     Alpha Core Sharpe > random p95 Sharpe. Nothing else decides.

WHY THIS EXPERIMENT EXISTS
--------------------------
Experiment 2 put Alpha Core at the 100th percentile of random selection. The V2
Random-Ranking Null Test put the same selection at the 20th. The difference
between them is that V2 ran through backtesting/engine.py -- T+1 orders, the صف
queue test, participation caps, partial fills, sector caps -- and Experiment 2
assumed every name filled at the close.

Alpha Core buys names that have just surged. On this exchange those are the
names locked limit-up, which a real order cannot buy. If that is the whole
story, this experiment will show it.

WHAT IS FROZEN
--------------
Everything. The exposure engine and its four signals, Alpha Core's ranking, the
decision grid, the basket size, the sector caps, the costs, the seed list. The
only new code is the glue that puts the exposure target above the selection --
`policy_for()` on the strategy -- and the harness that runs both arms.

    python experiment_execution_realism.py
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
import market_exposure as mx
import portfolio_engine
from backtesting import engine as engine_module
from backtesting.alpha_core import AlphaCoreStrategy, ReadingPanel
from backtesting.pit import PointInTimeData

from experiment_exposure import (SEEDS, WINDOW_END, WINDOW_START,
                                 breadth_by_date, index_series)


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "execution_realism.json")

TOP_N = 10


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


# ==============================
# THE ONE PIECE OF GLUE
# ==============================

class ExposureConstrainedAlphaCore(AlphaCoreStrategy):
    """Alpha Core with a target exposure imposed from above.

    Overrides ONE method -- policy_for() -- and nothing else. The ranking, the
    eligibility, the sizing, the stops and the exit rule are the base class's,
    untouched, so treatment and control differ only in stock identity and both
    face the identical exposure path.

    The exposure is applied the way the codebase already applies a market read:
    by tightening the policy's invested target and cash floor, which
    portfolio_engine.allocate() then respects. No new allocation logic exists.
    """

    def __init__(self, *args, **kwargs):

        self.exposure_by_date = kwargs.pop("exposure_by_date", None) or {}

        self.applied = {}

        super(ExposureConstrainedAlphaCore, self).__init__(*args, **kwargs)

    def policy_for(self, date):

        policy = super(ExposureConstrainedAlphaCore, self).policy_for(date)

        target = self.exposure_by_date.get(pd.Timestamp(date))

        if target is None:
            return policy

        percent = float(target) * 100.0

        self.applied[pd.Timestamp(date)] = percent

        # Only ever tightens: the engine's own floor still applies, and a
        # target above it cannot loosen the book.
        return policy.replace(
            target_invested_pct=min(policy.target_invested_pct, percent),
            min_cash_pct=max(policy.min_cash_pct, 100.0 - percent),
        )


# ==============================
# COUNTER OWNERSHIP — WHICH LAYER PRODUCED WHICH NUMBER
# ==============================
#
# Two layers count refusals, they count DIFFERENT OBJECTS, and adding them
# produces a number that describes nothing.
#
#   LAYER A  the order book (backtesting/execution.py, filler.stats). Counts
#            ORDER ATTEMPTS: an order that met a queue, got capped by the
#            participation limit, filled partly, or expired.
#
#   LAYER B  portfolio construction (portfolio_engine.allocate() and
#            book.can_open()). Counts CANDIDATE ADMISSIONS: a name the ranking
#            proposed that the caps refused before an order ever existed.
#
# A candidate refused in Layer B never becomes an order, so it cannot appear in
# Layer A. An order blocked in Layer A was already admitted by Layer B. The
# stored artifact shows what happens when this is forgotten: wanted_positions
# 448 and opened_positions 448 sit beside a blocked_by that sums to 492, which
# is impossible for a single funnel and correct for two.
#
# The mapping is `report name -> the key the producing layer actually writes`.
# The four report names on the left of LAYER_A were previously read under names
# nothing writes, which is why they were reported as null.

LAYER_A_EXECUTION = {
    "buy_queue_blocked": "blocked_buy_queue",
    "sell_queue_blocked": "blocked_sell_queue",
    "participation_capped": "capped_by_participation",
    "orders_expired_total": "expired",
    "filled": "filled",
    "partial_fills": "partial",
    "queue_blocked_exit": "queue_blocked_exit",
}

LAYER_B_PORTFOLIO = {
    "blocked_by": "blocked_by",
    "wanted_positions": "wanted_positions",
    "opened_positions": "opened_positions",
    "sized_to_zero": "sized_to_zero",
    "ranked_out": "ranked_out",
    "rotated_out": "rotated_out",
    "empty_rebalances": "empty_rebalances",
    "signals_total": "signals_total",
}

# Counters the old report asked for that NO layer produces. Declared rather
# than dropped: a field silently removed looks like a field nobody wanted,
# while a permanently-null one looks like a measurement that came back empty.
# Neither is true of `refused`, so it is named here with its reason.
UNAVAILABLE_COUNTERS = {
    "refused": ("no layer writes this key. Entry refusals live in "
                "entry_rejections (engine.py:420, built by the strategy's "
                "rejection_summary()); allocator refusals live in blocked_by. "
                "Read those two instead."),
}


def assert_layers_disjoint():
    """No counter may be owned by both layers.

    Cheap, but it is the guard that stops a future edit from moving a key into
    the other registry and quietly making the two totals addable again.
    """

    overlap = set(LAYER_A_EXECUTION) & set(LAYER_B_PORTFOLIO)

    if overlap:
        raise AssertionError(
            "a counter cannot belong to both layers: %s" % sorted(overlap))

    sources = set(LAYER_A_EXECUTION.values()) & set(LAYER_B_PORTFOLIO.values())

    if sources:
        raise AssertionError(
            "two layers claim the same source key: %s" % sorted(sources))

    return True


def _extract(diagnostics, registry):
    """Report-name -> value, with an explicit None for anything absent.

    Absent means absent. A missing counter is not zero: zero says the engine
    looked and found none, None says nobody recorded it, and conflating them is
    how a broken key mapping reads as "no queues were ever hit".
    """

    out = {}

    for report_name, source_key in registry.items():
        out[report_name] = (diagnostics[source_key]
                            if source_key in diagnostics else None)

    return out


# ==============================
# THE RUN
# ==============================

def profile(result, label):

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

    costs = ((diagnostics.get("commission_paid") or 0)
             + (diagnostics.get("tax_paid") or 0))

    # Read each layer through its own registry, so a key that nobody writes
    # surfaces as None instead of silently becoming a zero.
    layer_a = _extract(diagnostics, LAYER_A_EXECUTION)
    layer_b = _extract(diagnostics, LAYER_B_PORTFOLIO)

    raised = diagnostics.get("buy_orders_raised") or 0
    expired = diagnostics.get("buy_orders_expired") or 0

    filled = layer_a["filled"] or 0
    partial = layer_a["partial_fills"] or 0

    annual = metrics.get("annual_returns") or {}

    worst_year = None

    if isinstance(annual, dict) and annual:
        values = [v for v in annual.values() if isinstance(v, (int, float))]
        worst_year = min(values) if values else None

    return {
        "label": label,
        "available": result.available,
        "cagr_pct": metrics.get("cagr_pct"),
        "total_return_pct": metrics.get("total_return_pct"),
        "volatility_pct": metrics.get("volatility_pct"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "worst_year_pct": worst_year,

        "trades": trades.get("trades"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "turnover_per_year": turnover,
        "commission": diagnostics.get("commission_paid"),
        "tax": diagnostics.get("tax_paid"),
        "cost_total": costs,
        "cost_drag_pct": round(costs / float(result.cfg.initial_capital) * 100,
                               2) if costs else 0.0,

        "average_positions": exposure.get("average_positions"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "realized_exposure_pct": (
            None if exposure.get("average_cash_pct") is None
            else round(100.0 - exposure["average_cash_pct"], 2)),

        # ---- LAYER A: the order book ----
        # Order attempts. Never add these to the Layer B block.
        "execution_layer": layer_a,

        "buy_orders_raised": raised,
        "buy_orders_expired": expired,
        "fill_rate_pct": (round((raised - expired) / float(raised) * 100, 2)
                          if raised else None),
        "filled": filled,
        "partial_fills": partial,
        "partial_rate_pct": (round(partial / float(filled + partial) * 100, 2)
                             if (filled + partial) else None),
        "buy_queue_blocked": layer_a["buy_queue_blocked"],
        "sell_queue_blocked": layer_a["sell_queue_blocked"],
        "queue_blocked_exit": layer_a["queue_blocked_exit"],
        "participation_capped": layer_a["participation_capped"],

        # ---- LAYER B: portfolio construction ----
        # Candidate admissions. Never add these to the Layer A block.
        "portfolio_layer": layer_b,

        "blocked_by": layer_b["blocked_by"],
        "wanted_positions": layer_b["wanted_positions"],
        "opened_positions": layer_b["opened_positions"],

        # ---- attribution the engine already computed ----
        # Passed through verbatim. _cash_analysis() and rejection_summary()
        # run inside the engine; recomputing them here would be a second
        # opinion on a number that already has an owner.
        "cash_analysis": diagnostics.get("cash_analysis"),
        "entry_rejections": diagnostics.get("entry_rejections"),

        # ---- the rule, carried in the artifact itself ----
        "counter_layers": {
            "note": ("Layer A counts ORDER ATTEMPTS, Layer B counts CANDIDATE "
                     "ADMISSIONS. They describe different objects at different "
                     "points in the loop and MUST NOT be summed into one "
                     "funnel."),
            "layer_a_source": ("backtesting/execution.py filler.stats, plus "
                               "queue_blocked_exit which engine.py raises "
                               "inside the same fill loop"),
            "layer_b_source": ("portfolio_engine.allocate() and "
                               "backtesting/engine.py book.can_open()"),
            "disjoint_verified": assert_layers_disjoint(),
            "unavailable": UNAVAILABLE_COUNTERS,
        },
    }


def run_arm(label, cfg, data, panel, dates, exposure_by_date, start, end,
            ranking=None, seed=None, verbose=True):

    started = time.time()

    original = engine_module.ALPHA_CORE

    # engine.run() builds AlphaCoreStrategy itself; the exposure-constrained
    # subclass is injected by patching the import site for the duration of the
    # call. Restored in `finally`, so nothing leaks into another arm.
    import backtesting.alpha_core as module

    saved = module.AlphaCoreStrategy

    def factory(*args, **kwargs):
        kwargs["exposure_by_date"] = exposure_by_date
        return ExposureConstrainedAlphaCore(*args, **kwargs)

    module.AlphaCoreStrategy = factory

    try:
        result = engine_module.run(
            start, end, cfg=cfg, data=data, verbose=False,
            engine=original, use_cache=False, rebalance_dates=dates,
            equal_weight=True, alpha_top_n=TOP_N,
            alpha_ranking=ranking, alpha_seed=seed, alpha_panel=panel,
        )
    except Exception as exc:
        module.AlphaCoreStrategy = saved
        print("    %-18s FAILED %s: %s" % (label, type(exc).__name__, exc),
              flush=True)
        return None
    finally:
        module.AlphaCoreStrategy = saved

    row = profile(result, label)

    if verbose:
        print("    %-18s CAGR %7s%%  Sharpe %6s  DD %8s%%  fill %6s%%  "
              "exp %5s%%  %.0fs"
              % (label, row["cagr_pct"], row["sharpe"],
                 row["max_drawdown_pct"], row["fill_rate_pct"],
                 row["realized_exposure_pct"], time.time() - started),
              flush=True)

    return row


def main(argv=None):

    parser = argparse.ArgumentParser(description="Experiment 3")

    parser.add_argument("--start", default=WINDOW_START)
    parser.add_argument("--end", default=WINDOW_END)

    args = parser.parse_args(argv)

    print("=" * 78)
    print("EXPERIMENT 3 — EXECUTION-REALISTIC CONDITIONAL SELECTION")
    print("=" * 78)

    cfg = backtest_config.load()

    # Stops OFF, matching Experiment 2 exactly. The exit rule is rotation at
    # each decision date, which is what Experiment 2 simulated; leaving stops on
    # would change two things at once and the experiment would answer neither.
    cfg = cfg.replace(check_exits_daily=False)

    data = PointInTimeData(cfg, verbose=False)

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    # ---- the frozen exposure engine ---------------------------------------

    equal = index_series("TEDPIX_EW")

    window = equal[(equal["date"] >= start) & (equal["date"] <= end)]

    session_set = set(window["date"])

    breadth = breadth_by_date()

    grid = [d for d in sorted(breadth) if start <= d <= end and d in session_set]

    all_closes = equal["close"].tolist()
    all_dates = pd.DatetimeIndex(equal["date"])

    exposure_by_date = {}

    for date in grid:
        cut = int(all_dates.searchsorted(date, side="right"))
        value, _ = mx.exposure_for(all_closes[:cut], above_pct=breadth.get(date))
        exposure_by_date[date] = None if value is None else value / 100.0

    live = [v for v in exposure_by_date.values() if v is not None]

    print("  window        %s .. %s" % (start.date(), end.date()))
    print("  decisions     %d (the same grid as Experiments 1 and 2)"
          % len(grid))
    print("  exposure      FROZEN, mean target %.1f%%" % (np.mean(live) * 100))
    print("  execution     production engine: T+1, صف queue, participation "
          "cap,\n                partial fills, sector caps, commission, tax")
    print("  costs         round trip %.3f%%, slippage %.2f%%/side"
          % (cfg.round_trip_pct(), cfg.slippage_pct))
    print("  stops         OFF (as in Experiment 2); exit is rotation")
    print("  seeds         %d, pre-registered %d..%d"
          % (len(SEEDS), SEEDS[0], SEEDS[-1]))

    panel = ReadingPanel()

    out = {"protocol": {
        "window": [str(start.date()), str(end.date())],
        "decisions": len(grid), "top_n": TOP_N, "seeds": list(SEEDS),
        "primary_criterion": "alpha core sharpe > random p95 sharpe",
        "mean_target_exposure_pct": round(float(np.mean(live)) * 100, 2),
    }}

    # ---- treatment ---------------------------------------------------------

    print("\n[1] TREATMENT — exposure + Alpha Core, production execution",
          flush=True)

    treatment = run_arm("alpha core", cfg, data, panel, grid, exposure_by_date,
                        start, end)

    if treatment is None:
        print("\n  STOP: the treatment arm did not run.")
        return 1

    out["treatment"] = treatment

    write(out)

    # ---- control -----------------------------------------------------------

    print("\n[2] CONTROL — exposure + random selection, %d seeds"
          % len(SEEDS), flush=True)

    controls = []

    for seed in SEEDS:

        row = run_arm("seed %02d" % seed, cfg, data, panel, grid,
                      exposure_by_date, start, end, ranking="random",
                      seed=seed, verbose=False)

        if row is not None:
            row["seed"] = seed
            controls.append(row)

        out["controls"] = controls

        write(out)

    print("    %d of %d seeds completed" % (len(controls), len(SEEDS)))

    # ---- distribution ------------------------------------------------------

    def column(key):
        return [r[key] for r in controls if r.get(key) is not None]

    sharpes = column("sharpe")
    cagrs = column("cagr_pct")

    if len(sharpes) < 10:
        out["verdict"] = "INCONCLUSIVE: too few seeds produced a Sharpe"
        write(out)
        print("\n  INCONCLUSIVE — %d usable seeds" % len(sharpes))
        return 3

    def stats(values):
        return {
            "mean": round(float(np.mean(values)), 3),
            "median": round(float(np.percentile(values, 50)), 3),
            "p75": round(float(np.percentile(values, 75)), 3),
            "p90": round(float(np.percentile(values, 90)), 3),
            "p95": round(float(np.percentile(values, 95)), 3),
            "p99": round(float(np.percentile(values, 99)), 3),
            "min": round(float(min(values)), 3),
            "max": round(float(max(values)), 3),
        }

    distribution = {
        "sharpe": stats(sharpes),
        "cagr_pct": stats(cagrs),
        "sortino": stats(column("sortino")),
        "max_drawdown_pct": stats(column("max_drawdown_pct")),
        "calmar": stats(column("calmar")),
        "turnover_per_year": stats(column("turnover_per_year")),
        "cost_drag_pct": stats(column("cost_drag_pct")),
        "fill_rate_pct": stats(column("fill_rate_pct")),
        "realized_exposure_pct": stats(column("realized_exposure_pct")),
    }

    beaten = sum(1 for v in sharpes if treatment["sharpe"] > v)

    percentile = round(beaten / len(sharpes) * 100, 1)

    # Empirical one-sided p-value with the +1 correction, which is the honest
    # form for a finite null: with 30 draws the smallest achievable p is 1/31.
    p_value = round((sum(1 for v in sharpes if v >= treatment["sharpe"]) + 1)
                    / float(len(sharpes) + 1), 4)

    verdict = ("PASS" if treatment["sharpe"] > distribution["sharpe"]["p95"]
               else "FAIL")

    out["distribution"] = distribution
    out["percentile"] = percentile
    out["p_value"] = p_value
    out["verdict"] = verdict

    write(out)

    # ---- report ------------------------------------------------------------

    print("\n" + "=" * 78)
    print("RESULT: %s" % verdict)
    print("=" * 78)
    print("  %-22s %10s | %9s %9s %9s %9s"
          % ("metric", "ALPHA", "rnd mean", "rnd p50", "rnd p95", "rnd p99"))

    for key, label in (("cagr_pct", "CAGR %"), ("sharpe", "Sharpe"),
                       ("sortino", "Sortino"),
                       ("max_drawdown_pct", "Max DD %"),
                       ("calmar", "Calmar"),
                       ("turnover_per_year", "Turnover/yr"),
                       ("cost_drag_pct", "Cost drag %"),
                       ("fill_rate_pct", "Fill rate %"),
                       ("realized_exposure_pct", "Realized exp %")):

        block = distribution.get(key, {})

        print("  %-22s %10s | %9s %9s %9s %9s"
              % (label, treatment.get(key), block.get("mean"),
                 block.get("median"), block.get("p95"), block.get("p99")))

    print("\n  Alpha Core percentile   %.1f" % percentile)
    print("  empirical p-value       %.4f" % p_value)
    print("  Sharpe minus rnd mean   %+.3f"
          % (treatment["sharpe"] - distribution["sharpe"]["mean"]))
    print("  Sharpe minus rnd p95    %+.3f"
          % (treatment["sharpe"] - distribution["sharpe"]["p95"]))

    print("\n  PRIMARY CRITERION: alpha Sharpe %.3f %s random p95 %.3f"
          % (treatment["sharpe"],
             ">" if verdict == "PASS" else "<=",
             distribution["sharpe"]["p95"]))

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
