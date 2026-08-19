"""EXPERIMENT 1 — MARKET EXPOSURE BASELINE. Pre-committed, frozen, one question.

    HYPOTHESIS  a deterministic market-state engine can decide WHEN and HOW MUCH
                to be invested better than a fixed passive exposure
    DATASET     the stored equal-weight index (a real index, so the sleeve
                carries no survivorship bias) plus the point-in-time factor
                panel for breadth
    WINDOW      2014-09-21 .. 2024-09-01. Everything after is a holdout and is
                not read by this experiment.
    CONTROL     passive equal-weight, 100% invested, same dates and costs
    TREATMENT   market_exposure.exposure_for(), rebalanced every decision date
    NULL        30 seeded random exposures drawn from the treatment's OWN
                realised distribution, so the average exposure matches and only
                the TIMING differs
    FROZEN      the four signals, their windows, the thresholds, the ladder,
                the decision grid, the seed list, the costs
    PASS        all six conditions in the protocol
    FAIL        fixed beats it, or random matches it, or the edge is only
                exposure level, or it dies after costs

WHY THE SLEEVE IS AN INDEX AND NOT A BASKET
-------------------------------------------
The question is whether timing the market adds value. Running 800 names through
the order book would add two things the question does not want: selection noise,
which V2 already showed is worth nothing and would swamp a timing effect, and
survivorship bias, because the tradable universe is today's listings while the
equal-weight INDEX is a real contemporaneous series.

The cost is stated rather than hidden: this experiment does NOT test queue
behaviour, participation caps or T+1 fills on the exposure trades. Those belong
to Experiment 3 and are only worth running if this one passes.
"""

import argparse
import json
import math
import os
import random
import sys  # noqa: F401  (used by the __main__ guard)

import numpy as np
import pandas as pd

import backtest_config
import config
import database
import market_exposure as mx
import performance


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "exposure_experiment.json")

PANEL = os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")

# ---- frozen before the first run -----------------------------------------
WINDOW_START = "2014-09-21"
WINDOW_END = "2024-09-01"

SEEDS = tuple(range(1, 31))

ROUND_TRIP = config.TRANSACTION_COST_PCT / 100.0

# Condition 2 asks for a "meaningful" drawdown reduction. Fixed here, before any
# result, so it cannot be softened afterwards.
DRAWDOWN_IMPROVEMENT_REQUIRED = 0.20      # 20% shallower, relative
SHARPE_IMPROVEMENT_REQUIRED = 0.10        # in Sharpe units


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
# INPUTS
# ==============================

def index_series(key):
    code = config.INDICES.get(key, {}).get("insCode")

    frame = database.load_index_history(code) if code else None

    if frame is None or frame.empty:
        return None

    frame = frame.dropna(subset=["close"]).copy()
    frame["date"] = pd.to_datetime(frame["date"])

    return frame.sort_values("date").reset_index(drop=True)


def breadth_by_date(path=PANEL):
    """Percent of names above their own slow mean, per decision date.

    Straight off the point-in-time panel: `ma_gap_pct` is close-minus-slow-mean
    for one name on one date, computed by the same builder every other study
    used, so breadth inherits its point-in-time guarantee rather than needing a
    new one.
    """

    if not os.path.exists(path):
        raise SystemExit("no factor panel at %s" % path)

    frame = pd.read_csv(path, encoding="utf-8-sig")

    frame["date"] = pd.to_datetime(frame["date"])

    rows = frame.dropna(subset=["ma_gap_pct"])

    out = {}

    for date, block in rows.groupby("date"):
        if len(block) < 100:
            # Too thin to describe a market. Left absent so the engine abstains
            # on that vote rather than reading six names as "the market".
            continue
        out[date] = float((block["ma_gap_pct"] > 0).mean() * 100.0)

    return out


# ==============================
# THE SIMULATION
# ==============================

def simulate(sessions, market_returns, rf_daily, targets):
    """One exposure path -> a daily equity curve, net of rebalancing cost.

    `targets` is {decision date: exposure fraction}. Between decision dates the
    weight DRIFTS with the market rather than being held constant, because a
    book that is rebalanced daily to a target is a book paying daily costs, and
    no one runs that. Cost is charged on what actually changes hands.
    """

    equity = 1.0
    weight = 0.0

    curve = []
    exposures = []
    traded = 0.0

    for position, date in enumerate(sessions):

        # ---- 1. earn the session, on the weight that was already held ------
        #
        # THE RETURN COMES FIRST, AND THAT ORDER IS THE WHOLE POINT.
        # market_returns[i] is the move INTO session i, so a weight set at
        # session i and credited with it would be earning the return that
        # produced the very close the decision was made on -- a one-session
        # look-ahead. Rebalancing after the return means a decision taken at
        # this close first earns the NEXT session, which is the T+1 convention
        # the rest of the project executes on.
        r = market_returns[position]

        growth = weight * (1.0 + r) + (1.0 - weight) * (1.0 + rf_daily)

        equity *= growth

        exposures.append(weight)

        # Drift: the invested sleeve and the cash sleeve grew differently.
        if growth > 0:
            weight = weight * (1.0 + r) / growth

        # ---- 2. then rebalance at this close, effective from tomorrow ------
        if date in targets:

            target = targets[date]

            if target is not None:
                traded += abs(target - weight)
                equity *= (1.0 - abs(target - weight) * ROUND_TRIP)
                weight = target

        curve.append(equity)

    return {"curve": pd.Series(curve, index=sessions),
            "exposure": pd.Series(exposures, index=sessions),
            "turnover": traded}


def metrics(run, benchmark=None, years=None):
    """The protocol's metric list, from the project's own definitions."""

    curve = run["curve"]

    returns = curve.pct_change().dropna()

    report = performance.from_daily_returns(returns)

    # from_daily_returns() nests the drawdown block; downside deviation is not
    # in it, so it is taken from the same helper the module uses internally.
    drawdown = report.get("drawdown") or {}

    exposure = run["exposure"]

    total = float(curve.iloc[-1] / curve.iloc[0] - 1.0)

    years = years or (len(curve) / float(config.TRADING_DAYS_PER_YEAR))

    out = {
        "total_return_pct": round(total * 100, 2),
        "cagr_pct": round(((1 + total) ** (1 / years) - 1) * 100, 2),
        "volatility_pct": report.get("volatility_pct"),
        "sharpe": report.get("sharpe"),
        "sortino": report.get("sortino"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "calmar": report.get("calmar"),
        "downside_deviation_pct": round(
            float(returns[returns < 0].std()
                  * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100), 2)
        if (returns < 0).any() else 0.0,
        "excess_over_cash_pct": report.get("excess_over_cash_pct"),
        "average_exposure_pct": round(float(exposure.mean()) * 100, 2),
        "exposure_volatility_pct": round(float(exposure.std()) * 100, 2),
        "pct_sessions_invested": round(
            float((exposure > 0).mean()) * 100, 2),
        "pct_sessions_full": round(float((exposure >= 0.999).mean()) * 100, 2),
        "turnover_total": round(run["turnover"], 2),
        "turnover_per_year": round(run["turnover"] / years, 2),
        "cost_drag_pct": round(run["turnover"] * ROUND_TRIP * 100, 2),
    }

    if benchmark is not None:
        relative = performance.relative_to(
            returns, benchmark.pct_change().dropna())
        for key in ("beta", "alpha_pct", "information_ratio", "correlation",
                    "tracking_error_pct", "r_squared"):
            if key in relative:
                out[key] = relative[key]

    # Calendar-year returns, for the worst/best year the protocol asks for.
    annual = curve.groupby(curve.index.year).agg(["first", "last"])

    yearly = ((annual["last"] / annual["first"] - 1.0) * 100).round(2)

    out["worst_year_pct"] = float(yearly.min())
    out["best_year_pct"] = float(yearly.max())
    out["annual_returns"] = {int(y): float(v) for y, v in yearly.items()}

    return out


# ==============================
# THE EXPERIMENT
# ==============================

def main(argv=None):

    parser = argparse.ArgumentParser(description="Experiment 1 — exposure")

    parser.add_argument("--start", default=WINDOW_START)
    parser.add_argument("--end", default=WINDOW_END)

    args = parser.parse_args(argv)

    print("=" * 78)
    print("EXPERIMENT 1 — MARKET EXPOSURE BASELINE")
    print("=" * 78)

    equal = index_series("TEDPIX_EW")
    cap = index_series("TEDPIX")

    if equal is None:
        raise SystemExit("no equal-weight index history")

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    window = equal[(equal["date"] >= start) & (equal["date"] <= end)]

    sessions = list(window["date"])

    closes = window["close"].tolist()

    market_returns = [0.0] + list(np.diff(closes) / np.array(closes[:-1]))

    rf_daily = (1.0 + config.RISK_FREE_RATE) ** (1.0 / 240.0) - 1.0

    years = len(sessions) / float(config.TRADING_DAYS_PER_YEAR)

    print("  window        %s .. %s  (%d sessions, %.2f years)"
          % (start.date(), end.date(), len(sessions), years))
    print("  sleeve        equal-weight index (real index, no survivorship)")
    print("  cash          %.0f%% annual, %.5f%% daily"
          % (config.RISK_FREE_RATE * 100, rf_daily * 100))
    print("  cost          %.3f%% per unit of exposure traded"
          % (ROUND_TRIP * 100))

    # ---- decision grid and breadth ---------------------------------------

    breadth = breadth_by_date()

    grid = [d for d in sorted(breadth) if start <= d <= end]

    # Only dates that are real sessions of the sleeve.
    session_set = set(sessions)

    grid = [d for d in grid if d in session_set]

    print("  decisions     %d dates from the point-in-time panel" % len(grid))

    # ---- the engine -------------------------------------------------------

    all_closes = equal["close"].tolist()
    all_dates = pd.DatetimeIndex(equal["date"])

    targets = {}
    trace = []

    for date in grid:

        cut = int(all_dates.searchsorted(date, side="right"))

        history = all_closes[:cut]      # ends AT the decision session

        exposure, why = mx.exposure_for(history,
                                        above_pct=breadth.get(date))

        targets[date] = None if exposure is None else exposure / 100.0

        trace.append({"date": str(date.date()), "exposure_pct": exposure,
                      "votes": {k: v for k, v in why["votes"].items()},
                      "available": why.get("available")})

    live = [t for t in targets.values() if t is not None]

    print("  engine        mean target %.1f%%, range %.0f%%..%.0f%%"
          % (np.mean(live) * 100, min(live) * 100, max(live) * 100))

    # ---- arms -------------------------------------------------------------

    print("\n[1] ARMS", flush=True)

    arms = {}

    engine_run = simulate(sessions, market_returns, rf_daily, targets)

    benchmark_curve = pd.Series(
        np.cumprod([1.0 + r for r in market_returns]), index=sessions)

    arms["engine"] = metrics(engine_run, benchmark_curve, years)

    average_exposure = arms["engine"]["average_exposure_pct"] / 100.0

    fixed_full = simulate(sessions, market_returns, rf_daily,
                          {sessions[0]: 1.0})

    arms["passive_100"] = metrics(fixed_full, benchmark_curve, years)

    # REBALANCED to the matched level on every decision date, not set once. A
    # weight left alone drifts upward in a rising market -- the first version of
    # this control opened at 55% and averaged 73%, which would have compared the
    # engine against a book carrying a third more exposure and called the
    # difference timing. It also pays the same rebalancing cost the engine pays,
    # so the comparison is like for like on both counts.
    fixed_matched = simulate(sessions, market_returns, rf_daily,
                             {d: average_exposure for d in grid})

    arms["fixed_matched"] = metrics(fixed_matched, benchmark_curve, years)

    cash_only = simulate(sessions, market_returns, rf_daily, {sessions[0]: 0.0})

    arms["cash"] = metrics(cash_only, benchmark_curve, years)

    if cap is not None:
        cap_window = cap[(cap["date"] >= start) & (cap["date"] <= end)]
        cap_total = (cap_window["close"].iloc[-1]
                     / cap_window["close"].iloc[0] - 1.0)
        arms["tedpix_buy_hold"] = {
            "total_return_pct": round(cap_total * 100, 2),
            "cagr_pct": round(((1 + cap_total) ** (1 / years) - 1) * 100, 2),
            "note": "capitalisation index, buy and hold, no cost",
        }

    for name in ("engine", "passive_100", "fixed_matched", "cash"):
        row = arms[name]
        print("    %-16s CAGR %7s%%  vol %6s%%  Sharpe %6s  DD %8s%%  "
              "exp %5s%%  turn %5s"
              % (name, row["cagr_pct"], row["volatility_pct"], row["sharpe"],
                 row["max_drawdown_pct"], row["average_exposure_pct"],
                 row["turnover_per_year"]))

    if "tedpix_buy_hold" in arms:
        print("    %-16s CAGR %7s%%"
              % ("tedpix_b&h", arms["tedpix_buy_hold"]["cagr_pct"]))

    write({"arms": arms, "trace": trace,
           "protocol": {"window": [str(start.date()), str(end.date())],
                        "sessions": len(sessions), "decisions": len(grid),
                        "seeds": list(SEEDS),
                        "drawdown_improvement_required":
                            DRAWDOWN_IMPROVEMENT_REQUIRED,
                        "sharpe_improvement_required":
                            SHARPE_IMPROVEMENT_REQUIRED}})

    # ---- the null ---------------------------------------------------------

    print("\n[2] RANDOM EXPOSURE NULL — %d seeds" % len(SEEDS), flush=True)

    pool = [t for t in targets.values() if t is not None]

    nulls = []

    for seed in SEEDS:

        rng = random.Random("sirat-exposure|%d" % seed)

        shuffled = {}

        draw = list(pool)

        rng.shuffle(draw)

        position = 0

        for date in grid:
            if targets[date] is None:
                shuffled[date] = None
                continue
            shuffled[date] = draw[position % len(draw)]
            position += 1

        run = simulate(sessions, market_returns, rf_daily, shuffled)

        row = metrics(run, benchmark_curve, years)
        row["seed"] = seed

        nulls.append(row)

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
    print("    exposure mean %5.2f%%" % np.mean(
        [r["average_exposure_pct"] for r in nulls]))

    # ---- subperiods -------------------------------------------------------

    print("\n[3] SUBPERIODS — two independent halves", flush=True)

    half = len(sessions) // 2

    subs = {}

    for label, block in (("first_half", slice(0, half)),
                         ("second_half", slice(half, len(sessions)))):

        part_sessions = sessions[block]
        part_returns = market_returns[block]

        part_years = len(part_sessions) / float(config.TRADING_DAYS_PER_YEAR)

        part_targets = {d: v for d, v in targets.items()
                        if d in set(part_sessions)}

        # Seed the arm at its opening weight so the half is self-contained.
        engine_part = simulate(part_sessions, part_returns, rf_daily,
                               part_targets)

        passive_part = simulate(part_sessions, part_returns, rf_daily,
                                {part_sessions[0]: 1.0})

        subs[label] = {
            "from": str(part_sessions[0].date()),
            "to": str(part_sessions[-1].date()),
            "engine": metrics(engine_part, None, part_years),
            "passive_100": metrics(passive_part, None, part_years),
        }

        print("    %-12s %s..%s  engine CAGR %7s%% (Sharpe %5s)  "
              "passive %7s%% (Sharpe %5s)"
              % (label, subs[label]["from"], subs[label]["to"],
                 subs[label]["engine"]["cagr_pct"],
                 subs[label]["engine"]["sharpe"],
                 subs[label]["passive_100"]["cagr_pct"],
                 subs[label]["passive_100"]["sharpe"]))

    # ---- verdict ----------------------------------------------------------

    engine_row = arms["engine"]
    passive = arms["passive_100"]
    matched = arms["fixed_matched"]

    conditions = {}

    conditions["1_risk_adjusted_beats_fixed"] = (
        engine_row["sharpe"] is not None and passive["sharpe"] is not None
        and engine_row["sharpe"] >= passive["sharpe"]
                                    + SHARPE_IMPROVEMENT_REQUIRED)

    conditions["2_drawdown_meaningfully_lower"] = (
        abs(engine_row["max_drawdown_pct"])
        <= abs(passive["max_drawdown_pct"])
        * (1.0 - DRAWDOWN_IMPROVEMENT_REQUIRED))

    conditions["3_not_just_exposure_level"] = (
        engine_row["sharpe"] is not None and matched["sharpe"] is not None
        and engine_row["sharpe"] > matched["sharpe"])

    # Costs are already inside every curve, so surviving them means the net
    # result still clears the bar rather than the gross one.
    conditions["4_survives_costs"] = (
        engine_row["cagr_pct"] > config.RISK_FREE_RATE * 100)

    conditions["5_two_subperiods"] = all(
        subs[label]["engine"]["sharpe"] is not None
        and subs[label]["engine"]["sharpe"]
        >= subs[label]["passive_100"]["sharpe"]
        for label in ("first_half", "second_half"))

    conditions["6_beats_random_exposure"] = (
        engine_row["sharpe"] is not None
        and engine_row["sharpe"] > float(np.percentile(null_sharpe, 95)))

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
            "exposure_mean": round(float(np.mean(
                [r["average_exposure_pct"] for r in nulls])), 2),
        },
        "subperiods": subs,
        "conditions": conditions,
        "verdict": verdict,
    }

    write(out)

    print("\n" + "=" * 78)
    print("RESULT: %s" % verdict)
    print("=" * 78)

    for name, passed in conditions.items():
        print("  [%s] %s" % ("PASS" if passed else "FAIL", name))

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
