"""EXPERIMENT 2 — CONDITIONAL STOCK SELECTION. Pre-committed, frozen.

    HYPOTHESIS  once the Market Exposure Engine has decided how much capital is
                at risk, choosing WHICH names to hold adds return over choosing
                them at random
    DATASET     the point-in-time factor panel for selection, stored daily bars
                for the basket's returns, the equal-weight index for reference
    WINDOW      2014-09-21 .. 2024-09-01, the same 120 decision dates
    CONTROL     exposure engine + random top-10, 30 seeds
    TREATMENT   exposure engine + Alpha Core top-10 (50% momentum rank,
                50% Bollinger %B rank)
    CHANGED     the selection rule, and nothing else
    FROZEN      the exposure engine and every one of its four signals, the
                decision grid, basket size, equal weighting, the cost model,
                the seed list
    PASS        Alpha Core above the 95th percentile of the random seeds AND
                drawdown no worse than the random mean AND ahead in both halves
    FAIL        anything less

    Selection was already rejected once, on its own, at the 20th percentile of
    thirty random rankings. This asks the ONE thing that result did not: whether
    selection works CONDITIONALLY -- only on the capital the market read has
    already approved. A different question deserves its own null, and gets one.

WHY THE SLEEVE IS A REAL BASKET HERE
------------------------------------
Experiment 1 held an index because the question was timing and a basket would
have added selection noise. This question IS selection, so the sleeve has to be
actual names with actual daily prices. That reintroduces survivorship bias --
the universe is today's listings -- and it applies EQUALLY to both arms, so the
comparison between them survives it even though the absolute levels do not.
"""

import argparse
import json
import math
import os
import random
import sys

import numpy as np
import pandas as pd

import backtest_config
import config
import market_exposure as mx
import performance
from backtesting.pit import PointInTimeData

from experiment_exposure import (ROUND_TRIP, SEEDS, WINDOW_END, WINDOW_START,
                                 breadth_by_date, index_series, metrics)


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "conditional_experiment.json")

PANEL = os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")

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
# SELECTION
# ==============================

def rank_alpha(block):
    """Alpha Core: 50% momentum rank + 50% %B rank, exactly as V2 defined it."""

    rows = block.dropna(subset=["f_momentum", "bb_pct"]).copy()

    if rows.empty:
        return []

    momentum = rows["f_momentum"].rank(pct=True) * 100.0
    bollinger = rows["bb_pct"].rank(pct=True) * 100.0

    rows["alpha"] = 0.5 * momentum + 0.5 * bollinger

    return rows.sort_values("alpha", ascending=False)["ins_code"].tolist()


def rank_random(block, rng):
    """The null: the same eligible names, in an order that knows nothing.

    Eligibility is untouched -- exactly the names Alpha Core could have picked
    are the names the null picks from. Randomising eligibility as well would
    compare two different universes.
    """

    rows = block.dropna(subset=["f_momentum", "bb_pct"])

    codes = rows["ins_code"].tolist()

    rng.shuffle(codes)

    return codes


# ==============================
# THE SIMULATION
# ==============================

def simulate(sessions, returns_by_code, rf_daily, schedule):
    """A daily curve for {decision date: (exposure, [ins_codes])}.

    Equal weight inside the basket at each rebalance, then drift. Cost is the
    one-way traded fraction times the round trip -- the same convention
    Experiment 1 used, so the two experiments' cost figures mean the same thing.
    """

    equity = 1.0

    weights = {}          # ins_code -> fraction of the book
    cash = 1.0

    curve = []
    exposures = []
    traded_total = 0.0
    names_held = []

    for position, date in enumerate(sessions):

        # ---- 1. earn the session on the basket already held ----------------
        #
        # BEFORE any rebalance, for the reason experiment_exposure.simulate()
        # states at length: a daily return keyed on `date` is the move INTO
        # that session, and crediting it to a basket selected on that same
        # close would hand the strategy the move that caused the selection.
        # Alpha Core selects on momentum and %B, so that leak would flatter it
        # specifically and enormously.
        invested = sum(weights.values())

        growth = cash * (1.0 + rf_daily)

        updated = {}

        for code, weight in weights.items():
            r = returns_by_code.get(code)
            change = 0.0 if r is None else r.get(date, 0.0)
            grown = weight * (1.0 + change)
            updated[code] = grown
            growth += grown

        equity *= growth

        exposures.append(invested)
        names_held.append(len(weights))

        # Drift: rescale so the weights stay a partition of the book.
        if growth > 0:
            weights = {c: w / growth for c, w in updated.items()}
            cash = cash * (1.0 + rf_daily) / growth

        # ---- 2. then rebalance at this close, effective from tomorrow ------
        if date in schedule:

            exposure, codes = schedule[date]

            if exposure is not None:

                each = (exposure / float(len(codes))) if codes else 0.0

                target = {code: each for code in codes}

                moved = sum(abs(target.get(code, 0.0) - weights.get(code, 0.0))
                            for code in set(target) | set(weights))

                # /2 : a sale and the purchase it funds are one round trip.
                equity *= (1.0 - (moved / 2.0) * ROUND_TRIP)

                traded_total += moved / 2.0

                weights = {c: w for c, w in target.items() if w > 0}
                cash = max(0.0, 1.0 - sum(weights.values()))

        curve.append(equity)

    return {"curve": pd.Series(curve, index=sessions),
            "exposure": pd.Series(exposures, index=sessions),
            "turnover": traded_total,
            "average_names": float(np.mean(names_held))}


# ==============================
# THE EXPERIMENT
# ==============================

def main(argv=None):

    parser = argparse.ArgumentParser(description="Experiment 2 — conditional")

    parser.add_argument("--start", default=WINDOW_START)
    parser.add_argument("--end", default=WINDOW_END)

    args = parser.parse_args(argv)

    print("=" * 78)
    print("EXPERIMENT 2 — CONDITIONAL STOCK SELECTION")
    print("=" * 78)

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    equal = index_series("TEDPIX_EW")

    window = equal[(equal["date"] >= start) & (equal["date"] <= end)]

    sessions = list(window["date"])

    closes = window["close"].tolist()

    market_returns = [0.0] + list(np.diff(closes) / np.array(closes[:-1]))

    rf_daily = (1.0 + config.RISK_FREE_RATE) ** (1.0 / 240.0) - 1.0

    years = len(sessions) / float(config.TRADING_DAYS_PER_YEAR)

    # ---- the frozen exposure engine ---------------------------------------

    breadth = breadth_by_date()

    session_set = set(sessions)

    grid = [d for d in sorted(breadth) if start <= d <= end and d in session_set]

    all_closes = equal["close"].tolist()
    all_dates = pd.DatetimeIndex(equal["date"])

    exposure_by_date = {}

    for date in grid:
        cut = int(all_dates.searchsorted(date, side="right"))
        value, _ = mx.exposure_for(all_closes[:cut], above_pct=breadth.get(date))
        exposure_by_date[date] = None if value is None else value / 100.0

    live = [v for v in exposure_by_date.values() if v is not None]

    print("  window        %s .. %s  (%d sessions, %.2f years)"
          % (start.date(), end.date(), len(sessions), years))
    print("  decisions     %d" % len(grid))
    print("  exposure      FROZEN from Experiment 1, mean %.1f%%"
          % (np.mean(live) * 100))
    print("  basket        top %d, equal weight inside the exposed sleeve"
          % TOP_N)

    # ---- selection inputs --------------------------------------------------

    panel = pd.read_csv(PANEL, encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ins_code"] = panel["ins_code"].astype(str)

    blocks = {date: block for date, block in panel.groupby("date")
              if date in set(grid)}

    # ---- daily returns for every name the panel can propose ----------------

    print("  loading daily bars for the selectable universe ...", flush=True)

    cfg = backtest_config.load()
    data = PointInTimeData(cfg, verbose=False)

    wanted = set()

    for block in blocks.values():
        wanted.update(block.dropna(subset=["f_momentum", "bb_pct"])
                      ["ins_code"].tolist())

    returns_by_code = {}

    for code in wanted:
        bars = data.bars(code)
        if bars is None or bars.empty:
            continue
        frame = bars[["date", "close"]].dropna()
        frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]
        if len(frame) < 2:
            continue
        # dropna(): pct_change() leaves NaN on the first row, and a single NaN
        # multiplied into the book turns the whole curve into NaN. inf guards
        # against a zero close in the stored history.
        series = (frame.set_index("date")["close"].pct_change()
                  .replace([np.inf, -np.inf], np.nan).dropna())
        if series.empty:
            continue
        returns_by_code[code] = series.to_dict()

    print("  %d of %d selectable names have usable daily bars"
          % (len(returns_by_code), len(wanted)))

    # ---- arms ---------------------------------------------------------------

    print("\n[1] TREATMENT — exposure + Alpha Core", flush=True)

    alpha_schedule = {}

    for date in grid:
        exposure = exposure_by_date[date]
        block = blocks.get(date)
        codes = [] if block is None else rank_alpha(block)
        codes = [c for c in codes if c in returns_by_code][:TOP_N]
        alpha_schedule[date] = (exposure, codes)

    alpha_run = simulate(sessions, returns_by_code, rf_daily, alpha_schedule)

    benchmark_curve = pd.Series(
        np.cumprod([1.0 + r for r in market_returns]), index=sessions)

    arms = {"alpha_core": metrics(alpha_run, benchmark_curve, years)}

    arms["alpha_core"]["average_names"] = round(alpha_run["average_names"], 2)

    row = arms["alpha_core"]

    print("    %-16s CAGR %7s%%  Sharpe %6s  DD %8s%%  exp %5s%%  turn %5s"
          % ("alpha core", row["cagr_pct"], row["sharpe"],
             row["max_drawdown_pct"], row["average_exposure_pct"],
             row["turnover_per_year"]))

    # ---- reference: the exposure engine on the index itself ---------------

    from experiment_exposure import simulate as simulate_index

    index_run = simulate_index(sessions, market_returns, rf_daily,
                               exposure_by_date)

    arms["exposure_only_index"] = metrics(index_run, benchmark_curve, years)

    print("    %-16s CAGR %7s%%  Sharpe %6s  DD %8s%%  (Experiment 1 arm)"
          % ("exposure+index", arms["exposure_only_index"]["cagr_pct"],
             arms["exposure_only_index"]["sharpe"],
             arms["exposure_only_index"]["max_drawdown_pct"]))

    write({"arms": arms})

    # ---- the null -----------------------------------------------------------

    print("\n[2] NULL — exposure + random top-%d, %d seeds"
          % (TOP_N, len(SEEDS)), flush=True)

    nulls = []

    for seed in SEEDS:

        schedule = {}

        for date in grid:
            rng = random.Random("sirat-conditional|%d|%s" % (seed, date))
            block = blocks.get(date)
            codes = [] if block is None else rank_random(block, rng)
            codes = [c for c in codes if c in returns_by_code][:TOP_N]
            schedule[date] = (exposure_by_date[date], codes)

        run = simulate(sessions, returns_by_code, rf_daily, schedule)

        result = metrics(run, benchmark_curve, years)
        result["seed"] = seed
        result["average_names"] = round(run["average_names"], 2)

        nulls.append(result)

    null_cagr = [r["cagr_pct"] for r in nulls]
    null_sharpe = [r["sharpe"] for r in nulls if r["sharpe"] is not None]
    null_dd = [r["max_drawdown_pct"] for r in nulls]

    print("    CAGR    mean %6.2f%%  p50 %6.2f%%  p95 %6.2f%%  min %6.2f%%  "
          "max %6.2f%%"
          % (np.mean(null_cagr), np.percentile(null_cagr, 50),
             np.percentile(null_cagr, 95), min(null_cagr), max(null_cagr)))
    print("    Sharpe  mean %6.2f   p50 %6.2f   p95 %6.2f"
          % (np.mean(null_sharpe), np.percentile(null_sharpe, 50),
             np.percentile(null_sharpe, 95)))
    print("    maxDD   mean %6.2f%%" % np.mean(null_dd))
    print("    exposure mean %5.2f%%"
          % np.mean([r["average_exposure_pct"] for r in nulls]))

    # ---- subperiods ----------------------------------------------------------

    print("\n[3] SUBPERIODS", flush=True)

    half = len(sessions) // 2

    subs = {}

    for label, block in (("first_half", slice(0, half)),
                         ("second_half", slice(half, len(sessions)))):

        part_sessions = sessions[block]
        part_years = len(part_sessions) / float(config.TRADING_DAYS_PER_YEAR)
        part_set = set(part_sessions)

        part_alpha = {d: v for d, v in alpha_schedule.items() if d in part_set}

        alpha_part = simulate(part_sessions, returns_by_code, rf_daily,
                              part_alpha)

        seeds_part = []

        for seed in SEEDS[:10]:      # ten is enough to place one arm
            schedule = {}
            for date in grid:
                if date not in part_set:
                    continue
                rng = random.Random("sirat-conditional|%d|%s" % (seed, date))
                b = blocks.get(date)
                codes = [] if b is None else rng_pick(b, rng, returns_by_code)
                schedule[date] = (exposure_by_date[date], codes)
            run = simulate(part_sessions, returns_by_code, rf_daily, schedule)
            seeds_part.append(metrics(run, None, part_years))

        subs[label] = {
            "from": str(part_sessions[0].date()),
            "to": str(part_sessions[-1].date()),
            "alpha_core": metrics(alpha_part, None, part_years),
            "random_mean_cagr": round(float(np.mean(
                [r["cagr_pct"] for r in seeds_part])), 2),
            "random_mean_sharpe": round(float(np.mean(
                [r["sharpe"] for r in seeds_part
                 if r["sharpe"] is not None])), 3),
        }

        print("    %-12s alpha CAGR %7s%% (Sharpe %5s)   random mean %7s%% "
              "(Sharpe %5s)"
              % (label, subs[label]["alpha_core"]["cagr_pct"],
                 subs[label]["alpha_core"]["sharpe"],
                 subs[label]["random_mean_cagr"],
                 subs[label]["random_mean_sharpe"]))

    # ---- verdict --------------------------------------------------------------

    alpha_row = arms["alpha_core"]

    beaten = sum(1 for v in null_cagr if alpha_row["cagr_pct"] > v)

    conditions = {
        "1_beats_random_p95_cagr":
            alpha_row["cagr_pct"] > float(np.percentile(null_cagr, 95)),
        "2_beats_random_p95_sharpe":
            alpha_row["sharpe"] is not None
            and alpha_row["sharpe"] > float(np.percentile(null_sharpe, 95)),
        "3_drawdown_no_worse":
            abs(alpha_row["max_drawdown_pct"]) <= abs(float(np.mean(null_dd))),
        "4_exposure_held_constant":
            abs(alpha_row["average_exposure_pct"]
                - float(np.mean([r["average_exposure_pct"]
                                 for r in nulls]))) <= 2.0,
        "5_stable_in_both_halves": all(
            subs[label]["alpha_core"]["cagr_pct"]
            > subs[label]["random_mean_cagr"]
            for label in ("first_half", "second_half")),
    }

    verdict = "PASS" if all(conditions.values()) else "FAIL"

    out = {
        "arms": arms,
        "nulls": nulls,
        "null_summary": {
            "cagr_mean": round(float(np.mean(null_cagr)), 3),
            "cagr_p50": round(float(np.percentile(null_cagr, 50)), 3),
            "cagr_p95": round(float(np.percentile(null_cagr, 95)), 3),
            "sharpe_mean": round(float(np.mean(null_sharpe)), 3),
            "sharpe_p95": round(float(np.percentile(null_sharpe, 95)), 3),
            "dd_mean": round(float(np.mean(null_dd)), 3),
        },
        "alpha_percentile": round(beaten / len(null_cagr) * 100, 1),
        "subperiods": subs,
        "conditions": conditions,
        "verdict": verdict,
    }

    write(out)

    print("\n" + "=" * 78)
    print("RESULT: %s" % verdict)
    print("=" * 78)
    print("  alpha core CAGR %.2f%%  Sharpe %.2f  DD %.2f%%"
          % (alpha_row["cagr_pct"], alpha_row["sharpe"],
             alpha_row["max_drawdown_pct"]))
    print("  random     CAGR %.2f%%  Sharpe %.2f  DD %.2f%%  (means)"
          % (np.mean(null_cagr), np.mean(null_sharpe), np.mean(null_dd)))
    print("  alpha core percentile among the seeds: %.1f"
          % out["alpha_percentile"])

    for name, passed in conditions.items():
        print("  [%s] %s" % ("PASS" if passed else "FAIL", name))

    print("\n  wrote %s" % OUTPUT)

    return 0


def rng_pick(block, rng, returns_by_code):
    codes = rank_random(block, rng)
    return [c for c in codes if c in returns_by_code][:TOP_N]


if __name__ == "__main__":
    sys.exit(main())
