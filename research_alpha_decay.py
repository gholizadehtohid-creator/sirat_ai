"""ALPHA DECAY + EXECUTION FEASIBILITY DIAGNOSIS. No tuning, no model change.

    QUESTION 1   does the Alpha Core signal decay, and at what horizon does its
                 information disappear?
    QUESTION 2   what minimum signal half-life would be needed to clear the
                 friction this market actually charges?

WHAT THIS DOES NOT DO
---------------------
It changes no weight, no factor, no threshold and no lookback. The score is
reproduced from research_alpha_ic.score_frame(), which reproduces
alpha_core.alpha_scores() verbatim. Horizons 1/5/10/20/60 are the panel's own
plus a one-session point built here; none was chosen after seeing a result.

THE ONE NEW MEASUREMENT, AND WHY IT IS VALIDATED FIRST
-----------------------------------------------------
The panel carries fwd_5/10/20/60 but no one-session forward return, and the
decay curve is least informative exactly where it matters most. So fwd_1 is
built from price_history -- and because this project has already been bitten by
mixing adjusted backtest prices with raw execution prices, it is NOT trusted on
assertion: the same construction is used to rebuild fwd_5, and if that does not
reproduce the panel's own fwd_5 the one-session point is discarded rather than
reported.
"""

import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

import backtest_config
import config
import research_alpha_ic as ric


DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sirat_market.db")
OUT_DIR = backtest_config.OUTPUT_DIR
OUTPUT = os.path.join(OUT_DIR, "alpha_decay_analysis.json")

WINDOW_START = "2014-09-21"
WINDOW_END = "2024-09-01"

PANEL_HORIZONS = (5, 10, 20, 60)

ROUND_TRIP = config.TRANSACTION_COST_PCT / 100.0
SESSIONS_PER_YEAR = config.TRADING_DAYS_PER_YEAR

# Validation tolerance for the rebuilt fwd_5 against the panel's own, in
# percentage points of return. Fixed before the check ran.
FWD5_TOLERANCE_PP = 0.05


def load_prices():
    """(ins_code, date, close) from the price table, window only."""

    con = sqlite3.connect(DB)
    frame = pd.read_sql_query(
        "select ins_code, date, close from price_history "
        "where date >= ? and date <= ? and close is not null",
        con, params=("2014-01-01", "2025-01-01"))
    con.close()

    frame["date"] = pd.to_datetime(frame["date"])
    frame["ins_code"] = frame["ins_code"].astype(str)

    return frame.sort_values(["ins_code", "date"]).reset_index(drop=True)


def forward_returns(prices, horizons):
    """Per (ins_code, date): the return over the next h SESSIONS of that name."""

    out = {}

    grouped = prices.groupby("ins_code", sort=False)["close"]

    for h in horizons:
        # shift(-h) inside the group = the close h sessions later for that name.
        future = grouped.shift(-h)
        out["fwd_%d_rebuilt" % h] = (future / prices["close"] - 1.0) * 100.0

    built = prices[["ins_code", "date"]].copy()

    for key, values in out.items():
        built[key] = values.values

    return built


def spearman(x, y):
    if len(x) < 3:
        return np.nan
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def measure(frame, column, fwd):
    """Per-date IC, decile spread and hit rate for one horizon."""

    good = frame[[column, fwd]].dropna()

    n = len(good)
    if n < ric.MIN_NAMES_IC:
        return None

    x = good[column].to_numpy(dtype=float)
    y = good[fwd].to_numpy(dtype=float)

    row = {"names": n, "ic": spearman(x, y), "market_pct": float(np.mean(y))}

    if n >= ric.MIN_NAMES_DECILE:
        k = max(1, int(round(n / 10.0)))
        order = np.argsort(-x, kind="stable")
        top = y[order[:k]]
        bottom = y[order[-k:]]
        row["top_pct"] = float(np.mean(top))
        row["bottom_pct"] = float(np.mean(bottom))
        row["spread_pct"] = row["top_pct"] - row["bottom_pct"]
        row["top_minus_market_pct"] = row["top_pct"] - row["market_pct"]
        row["hit_ratio_pct"] = float(np.mean(top > np.median(y))) * 100.0

    return row


def summarise(rows, horizon):

    ic = np.array([r["ic"] for r in rows], dtype=float)
    ic = ic[~np.isnan(ic)]

    if len(ic) < 3:
        return None

    def col(key):
        v = np.array([r.get(key, np.nan) for r in rows], dtype=float)
        return v[~np.isnan(v)]

    sd = float(ic.std(ddof=1))
    tmm = col("top_minus_market_pct")
    spread = col("spread_pct")
    hit = col("hit_ratio_pct")

    return {
        "horizon": horizon,
        "dates": int(len(ic)),
        "mean_ic": round(float(ic.mean()), 5),
        "median_ic": round(float(np.median(ic)), 5),
        "ic_sd": round(sd, 5),
        "icir": round(float(ic.mean() / sd), 4) if sd > 0 else None,
        "t_stat": round(float(ic.mean() / sd * math.sqrt(len(ic))), 3)
        if sd > 0 else None,
        "pct_positive_ic": round(float((ic > 0).mean()) * 100, 2),
        "top_minus_market_pct": round(float(tmm.mean()), 4) if len(tmm) else None,
        "top_minus_bottom_pct": round(float(spread.mean()), 4)
        if len(spread) else None,
        "hit_ratio_pct": round(float(hit.mean()), 2) if len(hit) else None,
        "ic_per_sqrt_session": round(float(ic.mean()) * math.sqrt(horizon), 5),
    }


def main():

    print("=" * 78)
    print("ALPHA DECAY + EXECUTION FEASIBILITY — diagnosis only")
    print("=" * 78)

    panel = pd.read_csv(os.path.join(OUT_DIR, "factor_panel.csv"),
                        encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ins_code"] = panel["ins_code"].astype(str)

    scored = ric.score_frame(panel)

    window = scored[(scored["date"] >= WINDOW_START)
                    & (scored["date"] <= WINDOW_END)].copy()

    print("\n[1] SOURCE")
    print("    window        %s .. %s" % (WINDOW_START, WINDOW_END))
    print("    panel rows    %d over %d dates"
          % (len(window), window["date"].nunique()))

    print("\n[2] REBUILDING FORWARD RETURNS FROM price_history", flush=True)

    prices = load_prices()
    print("    price rows    %d, %d instruments"
          % (len(prices), prices["ins_code"].nunique()))

    built = forward_returns(prices, (1, 5))

    merged = window.merge(built, on=["ins_code", "date"], how="left")

    # ---- VALIDATION: does the rebuild reproduce the panel's own fwd_5? ----
    check = merged[["fwd_5", "fwd_5_rebuilt"]].dropna()

    diff = (check["fwd_5"] - check["fwd_5_rebuilt"]).abs()

    agree = float((diff <= FWD5_TOLERANCE_PP).mean()) * 100
    median_diff = float(diff.median())

    print("\n[3] VALIDATION — rebuilt fwd_5 vs the panel's own")
    print("    comparable rows      %d" % len(check))
    print("    median abs diff      %.5f pp" % median_diff)
    print("    within %.2f pp       %.2f%%" % (FWD5_TOLERANCE_PP, agree))

    fwd1_trusted = agree >= 95.0

    print("    -> one-session point %s"
          % ("TRUSTED" if fwd1_trusted else
             "DISCARDED (price sources disagree)"))

    if not fwd1_trusted:
        print("       reporting 5/10/20/60 only; h=1 is not on a consistent"
              " basis with the panel")

    # ---- decay curve -----------------------------------------------------
    print("\n[4] DECAY CURVE", flush=True)

    horizons = []
    if fwd1_trusted:
        horizons.append((1, "fwd_1_rebuilt"))
    horizons += [(h, "fwd_%d" % h) for h in PANEL_HORIZONS]

    curve = {}

    for h, column in horizons:
        rows = []
        for _, block in merged.groupby("date"):
            got = measure(block, "alpha", column)
            if got:
                rows.append(got)
        got = summarise(rows, h)
        if got:
            curve["h%d" % h] = got

    print("    %3s %5s %8s %7s %7s %7s %10s %10s %7s"
          % ("h", "dates", "meanIC", "ICIR", "t", "%pos", "top-mkt%",
             "top-bot%", "hit%"))
    for key, row in curve.items():
        print("    %3d %5d %8.5f %7.3f %7.2f %6.1f%% %10.4f %10.4f %6.1f%%"
              % (row["horizon"], row["dates"], row["mean_ic"], row["icir"],
                 row["t_stat"], row["pct_positive_ic"],
                 row["top_minus_market_pct"] or 0,
                 row["top_minus_bottom_pct"] or 0, row["hit_ratio_pct"] or 0))

    # ---- marginal accrual ------------------------------------------------
    print("\n[5] MARGINAL ALPHA ACCRUAL — where the information actually is")

    points = sorted((row["horizon"], row["top_minus_market_pct"])
                    for row in curve.values()
                    if row["top_minus_market_pct"] is not None)

    marginal = []
    previous_h, previous_a = 0, 0.0

    for h, alpha in points:
        span = h - previous_h
        gained = alpha - previous_a
        rate = gained / span if span else float("nan")
        marginal.append({"from_session": previous_h, "to_session": h,
                         "sessions": span,
                         "alpha_gained_pct": round(gained, 4),
                         "per_session_pct": round(rate, 5)})
        print("    sessions %2d-%2d (%2d)  alpha +%.4f%%  ->  %.5f%%/session"
              % (previous_h + 1, h, span, gained, rate))
        previous_h, previous_a = h, alpha

    # ---- half-life -------------------------------------------------------
    print("\n[6] SIGNAL HALF-LIFE")

    rates = [(0.5 * (m["from_session"] + m["to_session"]), m["per_session_pct"])
             for m in marginal if m["per_session_pct"] > 0]

    half_life = None
    tau = None

    if len(rates) >= 2:
        t = np.array([r[0] for r in rates], dtype=float)
        r = np.array([r[1] for r in rates], dtype=float)
        slope, intercept = np.polyfit(t, np.log(r), 1)
        if slope < 0:
            tau = -1.0 / slope
            half_life = tau * math.log(2.0)
            print("    exponential fit on the per-session accrual rate")
            print("    decay constant tau   %.2f sessions" % tau)
            print("    HALF-LIFE            %.2f sessions" % half_life)
            print("    r0 (day 0 rate)      %.5f%%/session" % math.exp(intercept))

    # ---- cost break-even -------------------------------------------------
    print("\n[7] SIGNAL HALF-LIFE vs TRADING COST")
    print("    round trip %.3f%%; a rebalance every h sessions pays it %.1f/h"
          " times a year" % (ROUND_TRIP * 100, SESSIONS_PER_YEAR))

    print("\n    %3s %11s %10s %11s %12s %12s"
          % ("h", "top-mkt%", "cost%", "net/period%", "periods/yr", "net/yr%"))

    feasibility = {}

    for key, row in curve.items():
        h = row["horizon"]
        alpha = row["top_minus_market_pct"]
        if alpha is None:
            continue
        periods = SESSIONS_PER_YEAR / float(h)
        net_period = alpha - ROUND_TRIP * 100
        net_year = ((1 + net_period / 100.0) ** periods - 1) * 100
        feasibility["h%d" % h] = {
            "horizon": h,
            "alpha_pct": round(alpha, 4),
            "cost_pct": round(ROUND_TRIP * 100, 4),
            "net_per_period_pct": round(net_period, 4),
            "periods_per_year": round(periods, 2),
            "net_per_year_pct": round(net_year, 2),
            "clears_commission": bool(net_period > 0),
        }
        print("    %3d %11.4f %10.4f %11.4f %12.1f %12.2f"
              % (h, alpha, ROUND_TRIP * 100, net_period, periods, net_year))

    # Minimum horizon at which the per-period alpha clears the round trip.
    breakeven_h = None
    for h in sorted(int(k[1:]) for k in curve):
        if feasibility.get("h%d" % h, {}).get("clears_commission"):
            breakeven_h = h
            break

    print("\n    shortest horizon whose per-period alpha clears the round"
          " trip: h = %s" % breakeven_h)

    # What the production system ACTUALLY held for.
    realized_turnover = 4.45      # measured in Experiment 5B
    realized_hold = SESSIONS_PER_YEAR / realized_turnover

    print("\n[8] WHAT THE PRODUCTION SYSTEM ACTUALLY HELD FOR")
    print("    Experiment 5B turnover      %.2f /year" % realized_turnover)
    print("    implied average holding     %.1f sessions" % realized_hold)
    print("    rebalance interval          %d sessions"
          % backtest_config.REBALANCE_SESSIONS)
    print("    -> the book held names %.1fx longer than it intended to"
          % (realized_hold / backtest_config.REBALANCE_SESSIONS))

    if half_life:
        print("    -> that is %.1f half-lives past the signal's own decay"
              % (realized_hold / half_life))

    payload = {
        "protocol": {
            "purpose": "diagnosis only -- no tuning, no model change",
            "window": [WINDOW_START, WINDOW_END],
            "horizons": [h for h, _ in horizons],
            "round_trip_pct": ROUND_TRIP * 100,
            "sessions_per_year": SESSIONS_PER_YEAR,
            "fwd1_validation": {
                "comparable_rows": int(len(check)),
                "median_abs_diff_pp": round(median_diff, 5),
                "pct_within_tolerance": round(agree, 2),
                "tolerance_pp": FWD5_TOLERANCE_PP,
                "trusted": bool(fwd1_trusted),
            },
        },
        "decay_curve": curve,
        "marginal_accrual": marginal,
        "half_life": {
            "tau_sessions": round(tau, 3) if tau else None,
            "half_life_sessions": round(half_life, 3) if half_life else None,
        },
        "feasibility": feasibility,
        "breakeven_horizon": breakeven_h,
        "realized": {
            "turnover_per_year": realized_turnover,
            "implied_holding_sessions": round(realized_hold, 2),
            "rebalance_sessions": backtest_config.REBALANCE_SESSIONS,
            "half_lives_held": round(realized_hold / half_life, 2)
            if half_life else None,
        },
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__, "pandas": pd.__version__,
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
