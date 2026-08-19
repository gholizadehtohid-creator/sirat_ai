"""EXPERIMENT 5A — FRICTIONLESS ALPHA ISOLATION.

    QUESTION    does Alpha Core create portfolio value BEFORE the allocator and
                the order book are introduced?
    NOT         alpha validation. The window is in-sample for Alpha Core's
                factor selection, so a pass here localises the problem; it does
                not establish an edge.
    ARMS        A1 top-10, A2 top-47, C1 random-10, C2 random-47
    WINDOW      2014-09-21 .. 2024-09-01 -- the research window. The post-2024
                panel dates exist and are NOT read.

WHY THE PANEL AND NOT THE ENGINE
--------------------------------
The point of a frictionless arm is that no allocator, queue, participation cap
or cash floor touches it. Running it through the production engine and then
switching those off would leave the result depending on switches being off. The
panel already carries the two ranking inputs -- written by the same
factor_engine.momentum() call alpha_core.py makes -- and a forward return over
exactly the 20-session rebalance horizon. So the arm is built where those
effects do not exist rather than where they are disabled.

COSTS ARE KEPT. A frictionless arm without costs measures nothing anyone can
trade. Fills are perfect; the 1.04% round trip is still charged on the fraction
of the book replaced at each rebalance, the same convention Experiment 1 used.

NOTHING IS TUNED. The score is reproduced from research_alpha_ic.score_frame(),
which reproduces alpha_core.alpha_scores() verbatim. Basket sizes 10 and 47 are
the production size and the decile the rank-IC study measured; neither was
chosen from a result.
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd

import backtest_config
import config
import research_alpha_ic as ric


OUT_DIR = backtest_config.OUTPUT_DIR
OUTPUT = os.path.join(OUT_DIR, "experiment5a_frictionless.json")

WINDOW_START = pd.Timestamp("2014-09-21")
WINDOW_END = pd.Timestamp("2024-09-01")

HORIZON = 20                    # sessions; equals the rebalance schedule
PERIODS_PER_YEAR = config.TRADING_DAYS_PER_YEAR / float(HORIZON)   # 12

BASKETS = (10, 47)              # production size, and the rank-IC decile
SEEDS = tuple(range(1, 31))     # 30 random controls per basket size

ROUND_TRIP = config.TRANSACTION_COST_PCT / 100.0

SUBPERIODS = (
    ("2014-2016", "2014-01-01", "2016-12-31"),
    ("2017-2019", "2017-01-01", "2019-12-31"),
    ("2020-2022", "2020-01-01", "2022-12-31"),
    ("2023-2024", "2023-01-01", "2024-12-31"),
)


def period_rf():
    """The 23% policy rate over one 20-session period, compounded."""
    return (1.0 + config.RISK_FREE_RATE) ** (1.0 / PERIODS_PER_YEAR) - 1.0


# ==============================
# ONE ARM
# ==============================

def run_arm(blocks, basket, ranking="alpha", seed=None):
    """Equal-weight top-N, rebalanced every decision date, perfect fills.

    `blocks` is [(date, frame)] with the frame already scored and already
    filtered to names carrying both inputs AND a forward return, so every arm
    draws from an identical eligible set on every date.
    """

    rng = None if ranking == "alpha" else np.random.default_rng(seed)

    equity = 1.0
    held = set()

    curve, rows = [], []
    turnover_total = 0.0

    for date, frame in blocks:

        if ranking == "alpha":
            order = frame.sort_values("alpha", ascending=False)
        else:
            # Same eligibility, only the order is replaced -- the rule
            # alpha_core.py uses for its own random arm.
            order = frame.iloc[rng.permutation(len(frame))]

        taken = order.head(basket)

        names = set(taken["ins_code"])

        # Fraction of the book replaced. One full round trip is charged on it,
        # matching Experiment 1's convention.
        replaced = (1.0 if not held
                    else len(names - held) / float(len(names)))

        cost = replaced * ROUND_TRIP
        turnover_total += replaced

        gross = float(taken["fwd_%d" % HORIZON].mean()) / 100.0

        equity *= (1.0 + gross) * (1.0 - cost)

        rows.append({
            "date": str(date.date()),
            "held": len(names),
            "eligible": int(len(frame)),
            "gross_pct": round(gross * 100, 4),
            "replaced_frac": round(replaced, 4),
            "cost_pct": round(cost * 100, 4),
            "net_pct": round(((1 + gross) * (1 - cost) - 1) * 100, 4),
            "equity": equity,
        })

        curve.append(equity)
        held = names

    return {"rows": rows, "curve": np.array(curve),
            "turnover_total": turnover_total}


# ==============================
# METRICS
# ==============================

def metrics(run, label):

    rows = run["rows"]

    net = np.array([r["net_pct"] for r in rows], dtype=float) / 100.0
    gross = np.array([r["gross_pct"] for r in rows], dtype=float) / 100.0

    periods = len(net)
    years = periods / PERIODS_PER_YEAR

    growth = float(np.prod(1 + net))

    cagr = (growth ** (1 / years) - 1) * 100 if growth > 0 else None

    sd = float(net.std(ddof=1))

    rf = period_rf()
    excess = net - rf

    shortfall = np.clip(excess, None, 0.0)
    downside = float(math.sqrt((shortfall ** 2).mean()))

    equity = np.cumprod(1 + net)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak

    return {
        "label": label,
        "periods": periods,
        "years": round(years, 3),
        "cumulative_return_pct": round((growth - 1) * 100, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "gross_cagr_pct": round(
            (float(np.prod(1 + gross)) ** (1 / years) - 1) * 100, 2),
        "volatility_pct": round(sd * math.sqrt(PERIODS_PER_YEAR) * 100, 2),
        "sharpe": round(float(excess.mean() / sd)
                        * math.sqrt(PERIODS_PER_YEAR), 3) if sd > 0 else None,
        "sortino": round(float(excess.mean() / downside)
                         * math.sqrt(PERIODS_PER_YEAR), 3) if downside > 0
        else None,
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
        "average_holdings": round(
            float(np.mean([r["held"] for r in rows])), 2),
        "realized_exposure_pct": 100.0,
        "turnover_per_year": round(
            run["turnover_total"] / years, 2),
        "rebalance_events": periods,
        "mean_period_return_pct": round(float(net.mean()) * 100, 4),
        "median_period_return_pct": round(float(np.median(net)) * 100, 4),
    }


# ==============================
# STATISTICS
# ==============================

def paired_test(treatment, control, label):
    """Paired t-test on per-period net return differences."""

    a = np.array([r["net_pct"] for r in treatment["rows"]], dtype=float)
    b = np.array([r["net_pct"] for r in control["rows"]], dtype=float)

    diff = a - b
    n = len(diff)

    mean = float(diff.mean())
    sd = float(diff.std(ddof=1))
    se = sd / math.sqrt(n)

    t = mean / se if se > 0 else float("nan")

    # Normal approximation; n = 120 so the t and z tails agree to ~1e-3.
    p = math.erfc(abs(t) / math.sqrt(2))

    return {
        "comparison": label,
        "observations": n,
        "mean_period_diff_pct": round(mean, 4),
        "median_period_diff_pct": round(float(np.median(diff)), 4),
        "sd_pct": round(sd, 4),
        "standard_error_pct": round(se, 4),
        "ci95_pct": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
        "t_stat": round(t, 3),
        "p_value": round(p, 5),
        "significant_at_5pct": bool(p < 0.05),
        "pct_periods_positive": round(float((diff > 0).mean()) * 100, 2),
    }


def spread_profile(treatment, controls):
    """Per-date alpha basket vs the MEAN random basket."""

    a = np.array([r["gross_pct"] for r in treatment["rows"]], dtype=float)

    stack = np.array([[r["gross_pct"] for r in c["rows"]] for c in controls],
                     dtype=float)

    b = stack.mean(axis=0)

    diff = a - b

    return {
        "mean_spread_pct": round(float(diff.mean()), 4),
        "median_spread_pct": round(float(np.median(diff)), 4),
        "pct_periods_positive": round(float((diff > 0).mean()) * 100, 2),
        "best_period_pct": round(float(diff.max()), 3),
        "worst_period_pct": round(float(diff.min()), 3),
        "per_date": [
            {"date": treatment["rows"][i]["date"],
             "alpha_basket_pct": round(float(a[i]), 4),
             "random_basket_pct": round(float(b[i]), 4),
             "difference_pct": round(float(diff[i]), 4)}
            for i in range(len(a))],
    }


def subperiod_view(run, controls, label):

    rows = run["rows"]
    dates = pd.to_datetime([r["date"] for r in rows])

    net = np.array([r["net_pct"] for r in rows], dtype=float) / 100.0
    gross = np.array([r["gross_pct"] for r in rows], dtype=float)

    ctrl_gross = np.array(
        [[r["gross_pct"] for r in c["rows"]] for c in controls],
        dtype=float).mean(axis=0)

    out = {}
    rf = period_rf()

    for name, lo, hi in SUBPERIODS:
        mask = (dates >= lo) & (dates <= hi)
        if mask.sum() < 3:
            continue

        seg = net[mask]
        years = len(seg) / PERIODS_PER_YEAR
        growth = float(np.prod(1 + seg))
        sd = float(seg.std(ddof=1))

        out[name] = {
            "periods": int(mask.sum()),
            "cagr_pct": round((growth ** (1 / years) - 1) * 100, 2)
            if growth > 0 else None,
            "sharpe": round(float((seg - rf).mean() / sd)
                            * math.sqrt(PERIODS_PER_YEAR), 3) if sd > 0
            else None,
            "alpha_spread_pct": round(
                float((gross[mask] - ctrl_gross[mask]).mean()), 4),
            "pct_periods_alpha_ahead": round(
                float((gross[mask] > ctrl_gross[mask]).mean()) * 100, 1),
        }

    return out


# ==============================
# MAIN
# ==============================

def main():

    print("=" * 78)
    print("EXPERIMENT 5A — FRICTIONLESS ALPHA ISOLATION")
    print("=" * 78)
    print("  NOT alpha validation. In-sample localisation only.")

    panel = pd.read_csv(os.path.join(OUT_DIR, "factor_panel.csv"),
                        encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])

    scored = ric.score_frame(panel)

    fwd = "fwd_%d" % HORIZON

    # One eligible set, shared by every arm: both ranking inputs present and a
    # forward return to be measured on.
    eligible = scored.dropna(subset=["alpha", fwd])

    window = eligible[(eligible["date"] >= WINDOW_START)
                      & (eligible["date"] <= WINDOW_END)]

    counts = window.groupby("date").size()
    usable = counts[counts >= max(BASKETS)].index

    blocks = [(d, window[window["date"] == d]) for d in sorted(usable)]

    print("\n[1] SCOPE")
    print("    window            %s .. %s"
          % (WINDOW_START.date(), WINDOW_END.date()))
    print("    decision dates    %d" % len(blocks))
    print("    first / last      %s .. %s"
          % (blocks[0][0].date(), blocks[-1][0].date()))
    print("    eligible names    min %d, median %d, max %d"
          % (counts.min(), int(counts.median()), counts.max()))
    print("    dropped, too thin %d dates (< %d names)"
          % (int((counts < max(BASKETS)).sum()), max(BASKETS)))
    dropped_fwd = int(scored["alpha"].notna().sum()
                      - scored[["alpha", fwd]].notna().all(axis=1).sum())
    print("    names with a score but no fwd_%d: %d" % (HORIZON, dropped_fwd))
    print("    horizon           %d sessions = the rebalance schedule" % HORIZON)
    print("    cost              %.2f%% round trip on the replaced fraction"
          % (ROUND_TRIP * 100))
    print("    cash rate         %.0f%%/yr (%.4f%% per period)"
          % (config.RISK_FREE_RATE * 100, period_rf() * 100))

    # ---- arms ------------------------------------------------------------
    print("\n[2] ARMS", flush=True)

    arms, controls, stats, spreads, stability = {}, {}, {}, {}, {}

    for basket in BASKETS:

        treat = run_arm(blocks, basket, ranking="alpha")
        arms["A_top%d" % basket] = metrics(treat, "alpha top-%d" % basket)

        pool = [run_arm(blocks, basket, ranking="random", seed=s)
                for s in SEEDS]

        rows = [metrics(r, "random top-%d seed %d" % (basket, s))
                for r, s in zip(pool, SEEDS)]

        cagrs = np.array([r["cagr_pct"] for r in rows], dtype=float)
        sharpes = np.array([r["sharpe"] for r in rows], dtype=float)

        a = arms["A_top%d" % basket]

        controls["C_top%d" % basket] = {
            "seeds": len(SEEDS),
            "cagr_mean": round(float(cagrs.mean()), 2),
            "cagr_median": round(float(np.median(cagrs)), 2),
            "cagr_sd": round(float(cagrs.std(ddof=1)), 2),
            "cagr_p5": round(float(np.percentile(cagrs, 5)), 2),
            "cagr_p95": round(float(np.percentile(cagrs, 95)), 2),
            "cagr_min": round(float(cagrs.min()), 2),
            "cagr_max": round(float(cagrs.max()), 2),
            "sharpe_mean": round(float(sharpes.mean()), 3),
            "sharpe_p95": round(float(np.percentile(sharpes, 95)), 3),
            "alpha_percentile": round(
                float((cagrs < a["cagr_pct"]).mean()) * 100, 1),
            "alpha_beats_p95": bool(a["cagr_pct"] > np.percentile(cagrs, 95)),
            "per_seed": rows,
        }

        # Paired test against the mean control path.
        mean_control = {"rows": [
            {"net_pct": float(np.mean([c["rows"][i]["net_pct"]
                                       for c in pool])),
             "gross_pct": float(np.mean([c["rows"][i]["gross_pct"]
                                         for c in pool]))}
            for i in range(len(blocks))]}

        stats["A%d_vs_C%d" % (basket, basket)] = paired_test(
            treat, mean_control, "alpha top-%d vs random top-%d mean"
            % (basket, basket))

        spreads["top%d" % basket] = spread_profile(treat, pool)

        stability["top%d" % basket] = subperiod_view(treat, pool,
                                                     "top%d" % basket)

        m = arms["A_top%d" % basket]
        c = controls["C_top%d" % basket]
        print("    A top-%-3d  CAGR %7.2f%%  vol %6.2f%%  Sharpe %6.3f  "
              "DD %7.2f%%  turn %.2f"
              % (basket, m["cagr_pct"], m["volatility_pct"], m["sharpe"],
                 m["max_drawdown_pct"], m["turnover_per_year"]))
        print("    C top-%-3d  CAGR %7.2f%% (sd %.2f, p95 %.2f)  "
              "-> alpha percentile %.1f"
              % (basket, c["cagr_mean"], c["cagr_sd"], c["cagr_p95"],
                 c["alpha_percentile"]))

    # ---- statistics ------------------------------------------------------
    print("\n[3] PAIRED TESTS (per-period net return difference)")
    for key, row in stats.items():
        print("    %-14s mean %+.4f%%  CI95 [%+.4f, %+.4f]  t %+.3f  "
              "p %.5f  n %d  %s"
              % (key, row["mean_period_diff_pct"], row["ci95_pct"][0],
                 row["ci95_pct"][1], row["t_stat"], row["p_value"],
                 row["observations"],
                 "SIG" if row["significant_at_5pct"] else "n.s."))

    print("\n[4] ALPHA-MINUS-RANDOM SPREAD (gross, per rebalance)")
    for key, row in spreads.items():
        print("    %-8s mean %+.4f%%  median %+.4f%%  positive %5.1f%%  "
              "best %+.2f%%  worst %+.2f%%"
              % (key, row["mean_spread_pct"], row["median_spread_pct"],
                 row["pct_periods_positive"], row["best_period_pct"],
                 row["worst_period_pct"]))

    print("\n[5] STABILITY")
    for key, block in stability.items():
        print("    %s" % key)
        for name, row in block.items():
            print("      %-11s n=%2d  CAGR %8.2f%%  Sharpe %7.3f  "
                  "spread %+.4f%%  ahead %5.1f%%"
                  % (name, row["periods"], row["cagr_pct"] or 0,
                     row["sharpe"] or 0, row["alpha_spread_pct"],
                     row["pct_periods_alpha_ahead"]))

    # ---- localisation verdict -------------------------------------------
    print("\n[6] LOCALISATION")

    verdicts = {}
    for basket in BASKETS:
        a = arms["A_top%d" % basket]
        c = controls["C_top%d" % basket]
        s = stats["A%d_vs_C%d" % (basket, basket)]

        passed = bool(a["cagr_pct"] > c["cagr_mean"]
                      and s["significant_at_5pct"]
                      and s["mean_period_diff_pct"] > 0)

        verdicts["top%d" % basket] = {
            "pass_localization": passed,
            "alpha_cagr_pct": a["cagr_pct"],
            "random_mean_cagr_pct": c["cagr_mean"],
            "alpha_percentile": c["alpha_percentile"],
            "p_value": s["p_value"],
        }
        print("    top-%-3d %s   alpha %.2f%% vs random mean %.2f%%  "
              "(pct %.1f, p %.5f)"
              % (basket, "PASS LOCALIZATION" if passed else "FAIL LOCALIZATION",
                 a["cagr_pct"], c["cagr_mean"], c["alpha_percentile"],
                 s["p_value"]))

    payload = {
        "protocol": {
            "purpose": "localisation only -- NOT alpha validation",
            "window": [str(WINDOW_START.date()), str(WINDOW_END.date())],
            "decision_dates": len(blocks),
            "horizon_sessions": HORIZON,
            "periods_per_year": PERIODS_PER_YEAR,
            "baskets": list(BASKETS),
            "seeds": list(SEEDS),
            "round_trip_cost_pct": ROUND_TRIP * 100,
            "risk_free_pct": config.RISK_FREE_RATE * 100,
            "fills": "perfect",
            "allocator": "not used",
            "execution_model": "not used",
            "post_2024_data_read": False,
        },
        "arms": arms,
        "controls": controls,
        "statistics": stats,
        "spread": spreads,
        "stability": stability,
        "verdicts": verdicts,
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__, "pandas": pd.__version__,
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    for basket in BASKETS:
        pd.DataFrame(spreads["top%d" % basket]["per_date"]).to_csv(
            os.path.join(OUT_DIR, "experiment5a_spread_top%d.csv" % basket),
            index=False, encoding="utf-8-sig")

    pd.DataFrame(list(arms.values())).to_csv(
        os.path.join(OUT_DIR, "experiment5a_arms.csv"),
        index=False, encoding="utf-8-sig")

    print("\n  wrote %s" % OUTPUT)
    print("  wrote experiment5a_arms.csv, experiment5a_spread_top{10,47}.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
