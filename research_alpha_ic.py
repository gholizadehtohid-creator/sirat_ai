"""ALPHA CORE RANK-IC DIAGNOSTIC — evaluation only, no backtest, no tuning.

WHAT THIS IS
------------
A cross-sectional test of whether the Alpha Core score orders future returns.
It needs no portfolio, no allocator, no execution model and no exposure model,
so none of the four defects that blocked Experiment 4 can reach it.

WHAT IT DOES NOT DO
-------------------
It changes no formula, fits no parameter and selects no factor. It reads
`factor_panel.csv` and reproduces `AlphaCoreStrategy.alpha_scores()` exactly:

    momentum_rank = pct_rank(f_momentum) * 100      over ALL names on the date
    bb_rank       = pct_rank(bb_pct)     * 100      over ALL names on the date
    alpha         = 0.5*momentum_rank + 0.5*bb_rank, and None if either is NaN

The ranks are taken over every name scored on the date and the drop happens
AFTER, which is what the engine does -- so a half-measured name still moves the
denominator even though it never gets a score. Replicating that matters: doing
it the tidy way would rank a different universe and answer a question nobody
asked.

WHY THE PANEL IS THE RIGHT SOURCE
---------------------------------
`research_factors.py:332` writes f_momentum from
`factor_engine.momentum(closes[:index+1]).get("score")` and bb_pct from the
indicator frame's BB_PCT at the decision bar -- the identical calls and fields
`alpha_core.py:366/391` uses. The panel is not a proxy for Alpha Core's inputs;
it is those inputs.

PRE-SPECIFIED, BEFORE ANY NUMBER WAS READ
-----------------------------------------
    min names for IC          30
    min names for deciles     50
    horizons                  5, 10, 20, 60 sessions (the panel's own)
    sub-periods               2008-2014, 2015-2019, 2020..2024-08-31,
                              2024-09-01..2026  (the last boundary is FIT_START)
    hit ratio                 share of top-decile names whose forward return
                              beats the cross-sectional MEDIAN that date
    random control            200 seeded permutations of the score, per date
    t-statistics              Newey-West corrected where the horizon overlaps
                              the ~20-session date spacing
"""

import json
import os
import sys

import numpy as np
import pandas as pd

import backtest_config


PANEL = os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")
OUT_DIR = backtest_config.OUTPUT_DIR

HORIZONS = (5, 10, 20, 60)

MIN_NAMES_IC = 30
MIN_NAMES_DECILE = 50

RANDOM_TRIALS = 200
SEED = 20260818

SUBPERIODS = (
    ("2008-2014", "2008-01-01", "2014-12-31"),
    ("2015-2019", "2015-01-01", "2019-12-31"),
    ("2020-2024H1", "2020-01-01", "2024-08-31"),
    ("2024H2-2026", "2024-09-01", "2026-12-31"),
)

COMPONENTS = ("alpha", "momentum_rank", "bb_rank")


# ==============================
# THE SCORE, REPRODUCED
# ==============================

def score_frame(frame):
    """alpha_core.alpha_scores() for every decision date, verbatim in semantics.

    Vectorised per date rather than looped, but the semantics are the engine's:
    each rank is taken over ALL names carrying that input on the date, and the
    drop for a missing input happens AFTER, so a half-measured name still moves
    the denominator without ever receiving a score.
    """

    out = frame.copy()

    grouped = out.groupby("date", observed=True)

    m = grouped["f_momentum"].rank(pct=True, na_option="keep") * 100.0
    b = grouped["bb_pct"].rank(pct=True, na_option="keep") * 100.0

    out["momentum_rank"] = m.round(2)
    out["bb_rank"] = b.round(2)

    alpha = (0.5 * m + 0.5 * b).round(2)
    alpha[m.isna() | b.isna()] = np.nan
    out["alpha"] = alpha

    return out


# ==============================
# PER-DATE MEASUREMENT
# ==============================

def spearman(x, y):
    if len(x) < 3:
        return np.nan
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def pearson(x, y):
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


def measure(frame, column, horizon, rng=None):
    """One (date, signal, horizon) row."""

    fwd = "fwd_%d" % horizon

    good = frame[[column, fwd]].dropna()

    n = len(good)
    if n < MIN_NAMES_IC:
        return None

    x = good[column].to_numpy(dtype=float)
    y = good[fwd].to_numpy(dtype=float)

    row = {
        "names": n,
        "ic": spearman(x, y),
        "pearson": pearson(x, y),
        "top_decile_pct": np.nan,
        "bottom_decile_pct": np.nan,
        "spread_pct": np.nan,
        "hit_ratio_pct": np.nan,
        "market_pct": float(np.mean(y)),
    }

    if n >= MIN_NAMES_DECILE:
        k = max(1, int(round(n / 10.0)))
        order = np.argsort(-x, kind="stable")      # high score first
        top = y[order[:k]]
        bottom = y[order[-k:]]
        row["top_decile_pct"] = float(np.mean(top))
        row["bottom_decile_pct"] = float(np.mean(bottom))
        row["spread_pct"] = row["top_decile_pct"] - row["bottom_decile_pct"]
        row["hit_ratio_pct"] = float(np.mean(top > np.median(y))) * 100.0

    if rng is not None:
        # Random control: permute the score, keep the same names and returns.
        ics = np.empty(RANDOM_TRIALS)
        ry = pd.Series(y).rank().to_numpy()
        for trial in range(RANDOM_TRIALS):
            perm = rng.permutation(n).astype(float)
            ics[trial] = (np.nan if ry.std() == 0
                          else float(np.corrcoef(perm, ry)[0, 1]))
        row["random_ic_mean"] = float(np.nanmean(ics))
        row["random_ic_std"] = float(np.nanstd(ics))
        row["random_ic_p95"] = float(np.nanpercentile(ics, 95))

    return row


# ==============================
# AGGREGATION
# ==============================

def newey_west_t(series, lag):
    """t-stat of the mean, Newey-West corrected for `lag` overlaps."""

    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan

    mean = x.mean()
    dev = x - mean

    variance = float((dev ** 2).mean())
    for l in range(1, min(lag, n - 1) + 1):
        cov = float((dev[l:] * dev[:-l]).mean())
        variance += 2.0 * (1.0 - l / (lag + 1.0)) * cov

    if variance <= 0:
        return np.nan

    return float(mean / np.sqrt(variance / n))


def summarise(rows, horizon, label):
    """IC statistics for one signal over one set of dates."""

    if not rows:
        return None

    ic = np.array([r["ic"] for r in rows], dtype=float)
    ic = ic[~np.isnan(ic)]

    if len(ic) < 3:
        return None

    spread = np.array([r["spread_pct"] for r in rows], dtype=float)
    spread = spread[~np.isnan(spread)]

    hit = np.array([r["hit_ratio_pct"] for r in rows], dtype=float)
    hit = hit[~np.isnan(hit)]

    std = float(ic.std(ddof=1))

    # Dates sit ~20 sessions apart, so a 60-session horizon overlaps ~2 later
    # dates. Correct the t for it rather than quoting an inflated one.
    lag = max(0, int(np.ceil(horizon / 20.0)) - 1)

    return {
        "label": label,
        "horizon": horizon,
        "dates": int(len(ic)),
        "mean_ic": round(float(ic.mean()), 5),
        "median_ic": round(float(np.median(ic)), 5),
        "ic_std": round(std, 5),
        "icir": round(float(ic.mean() / std), 4) if std > 0 else None,
        "t_stat_iid": round(float(ic.mean() / std * np.sqrt(len(ic))), 3)
        if std > 0 else None,
        "t_stat_newey_west": round(newey_west_t(ic, lag), 3),
        "newey_west_lag": lag,
        "pct_positive_ic": round(float((ic > 0).mean()) * 100, 2),
        "mean_spread_pct": round(float(spread.mean()), 4) if len(spread) else None,
        "spread_dates": int(len(spread)),
        "mean_hit_ratio_pct": round(float(hit.mean()), 2) if len(hit) else None,
    }


# ==============================
# MAIN
# ==============================

def main():

    print("=" * 78)
    print("ALPHA CORE RANK-IC DIAGNOSTIC — evaluation only")
    print("=" * 78)

    frame = pd.read_csv(PANEL, encoding="utf-8-sig")
    frame["date"] = pd.to_datetime(frame["date"])

    print("\n[1] PANEL")
    print("    rows          %d" % len(frame))
    print("    span          %s .. %s"
          % (frame["date"].min().date(), frame["date"].max().date()))
    print("    decision dates %d" % frame["date"].nunique())
    print("    symbols        %d unique ins_code" % frame["ins_code"].nunique())
    print("    sectors        %d" % frame["sector"].nunique())

    coverage = {
        "f_momentum": int(frame["f_momentum"].notna().sum()),
        "bb_pct": int(frame["bb_pct"].notna().sum()),
    }
    for h in HORIZONS:
        coverage["fwd_%d" % h] = int(frame["fwd_%d" % h].notna().sum())
    print("    coverage      %s" % coverage)

    # ---- score every date ------------------------------------------------
    print("\n[2] SCORING (reproducing alpha_scores exactly)", flush=True)

    scored = score_frame(frame)

    both = scored["alpha"].notna()
    print("    names with BOTH inputs: %d of %d (%.1f%%)"
          % (int(both.sum()), len(scored), 100.0 * both.mean()))
    print("    names dropped for a missing input: %d" % int((~both).sum()))

    per_date = scored[both].groupby("date").size()
    print("    scoreable names per date: min %d, median %d, max %d"
          % (per_date.min(), int(per_date.median()), per_date.max()))
    print("    dates with >= %d scoreable names: %d of %d"
          % (MIN_NAMES_IC, int((per_date >= MIN_NAMES_IC).sum()),
             len(per_date)))
    print("    dates with >= %d (decile floor): %d"
          % (MIN_NAMES_DECILE, int((per_date >= MIN_NAMES_DECILE).sum())))

    # ---- per-date measurement -------------------------------------------
    print("\n[3] PER-DATE IC", flush=True)

    rng = np.random.default_rng(SEED)

    records = []

    for date, block in scored.groupby("date"):
        for column in COMPONENTS:
            for h in HORIZONS:
                row = measure(block, column, h,
                              rng=rng if column == "alpha" else None)
                if row is None:
                    continue
                row["date"] = str(date.date())
                row["signal"] = column
                row["horizon"] = h
                records.append(row)

    ic_frame = pd.DataFrame(records)
    ic_frame.to_csv(os.path.join(OUT_DIR, "rank_ic.csv"),
                    index=False, encoding="utf-8-sig")
    print("    wrote rank_ic.csv (%d rows)" % len(ic_frame))

    # ---- aggregate -------------------------------------------------------
    print("\n[4] FULL-SAMPLE SUMMARY")
    print("    %-14s %4s %6s %8s %7s %7s %8s %8s %7s"
          % ("signal/horizon", "n", "meanIC", "medianIC", "ICIR", "t(NW)",
             "%pos", "spread%", "hit%"))

    summary = {}
    for column in COMPONENTS:
        for h in HORIZONS:
            rows = ic_frame[(ic_frame["signal"] == column)
                            & (ic_frame["horizon"] == h)].to_dict("records")
            got = summarise(rows, h, "%s/%d" % (column, h))
            if got is None:
                continue
            summary["%s_%d" % (column, h)] = got
            print("    %-14s %4d %6.4f %8.4f %7.3f %7.2f %8.1f %8.3f %7.1f"
                  % (got["label"], got["dates"], got["mean_ic"],
                     got["median_ic"], got["icir"] or 0,
                     got["t_stat_newey_west"] or 0, got["pct_positive_ic"],
                     got["mean_spread_pct"] or 0,
                     got["mean_hit_ratio_pct"] or 0))

    # ---- random control --------------------------------------------------
    alpha_rows = ic_frame[ic_frame["signal"] == "alpha"]
    random_control = {}
    for h in HORIZONS:
        sub = alpha_rows[alpha_rows["horizon"] == h]
        if sub.empty:
            continue
        random_control["horizon_%d" % h] = {
            "alpha_mean_ic": round(float(sub["ic"].mean()), 5),
            "random_mean_ic": round(float(sub["random_ic_mean"].mean()), 5),
            "random_ic_std": round(float(sub["random_ic_std"].mean()), 5),
            "random_p95_ic": round(float(sub["random_ic_p95"].mean()), 5),
            "alpha_beats_random_p95": bool(
                sub["ic"].mean() > sub["random_ic_p95"].mean()),
        }

    print("\n[5] RANDOM CONTROL (%d permutations per date)" % RANDOM_TRIALS)
    for key, row in random_control.items():
        print("    %-12s alpha IC %+.5f   random IC %+.5f (sd %.4f, p95 %+.5f)"
              % (key, row["alpha_mean_ic"], row["random_mean_ic"],
                 row["random_ic_std"], row["random_p95_ic"]))

    # ---- sub-periods -----------------------------------------------------
    print("\n[6] SUB-PERIOD STABILITY")

    stability = {}
    ic_frame["_date"] = pd.to_datetime(ic_frame["date"])

    for label, lo, hi in SUBPERIODS:
        window = ic_frame[(ic_frame["_date"] >= lo)
                          & (ic_frame["_date"] <= hi)]
        stability[label] = {}
        for column in COMPONENTS:
            for h in HORIZONS:
                rows = window[(window["signal"] == column)
                              & (window["horizon"] == h)].to_dict("records")
                got = summarise(rows, h, "%s/%d" % (column, h))
                if got:
                    stability[label]["%s_%d" % (column, h)] = got

        row = stability[label].get("alpha_20")
        if row:
            print("    %-12s %s..%s  alpha/20: n=%3d  meanIC %+.4f  "
                  "ICIR %+.3f  %%pos %5.1f  spread %+.3f%%"
                  % (label, lo, hi, row["dates"], row["mean_ic"],
                     row["icir"] or 0, row["pct_positive_ic"],
                     row["mean_spread_pct"] or 0))
        else:
            print("    %-12s %s..%s  (insufficient dates)" % (label, lo, hi))

    # ---- decision rule ---------------------------------------------------
    print("\n[7] PRE-SPECIFIED DECISION RULE (Phase 5)")

    verdicts = {}
    for h in HORIZONS:
        got = summary.get("alpha_%d" % h)
        if not got:
            continue

        periods = [stability[label].get("alpha_%d" % h)
                   for label, _, _ in SUBPERIODS]
        periods = [p for p in periods if p]

        positive_periods = sum(1 for p in periods if p["mean_ic"] > 0)

        # "No single short period explains all performance": the full-sample
        # mean IC must stay positive after removing the strongest sub-period.
        concentrated = False
        if len(periods) >= 2 and got["mean_ic"] > 0:
            best = max(periods, key=lambda p: p["mean_ic"])
            rest = [p for p in periods if p is not best]
            weighted = (sum(p["mean_ic"] * p["dates"] for p in rest)
                        / max(1, sum(p["dates"] for p in rest)))
            concentrated = weighted <= 0
        else:
            weighted = None

        c1 = got["mean_ic"] > 0
        c2 = (got["icir"] or 0) > 0.3
        c3 = got["pct_positive_ic"] > 50.0
        c4 = not concentrated

        verdict = "PASS candidate" if (c1 and c2 and c3 and c4) else "FAIL"

        verdicts["horizon_%d" % h] = {
            "mean_ic_gt_0": bool(c1),
            "icir_gt_0.3": bool(c2),
            "pct_positive_gt_50": bool(c3),
            "not_concentrated": bool(c4),
            "mean_ic_excluding_best_subperiod": (
                round(weighted, 5) if weighted is not None else None),
            "verdict": verdict,
        }

        print("    horizon %2d:  IC>0 %-5s  ICIR>0.3 %-5s (%.3f)  "
              "%%pos>50 %-5s (%.1f)  not-concentrated %-5s  ->  %s"
              % (h, c1, c2, got["icir"] or 0, c3, got["pct_positive_ic"],
                 c4, verdict))

    # ---- artifacts -------------------------------------------------------
    payload = {
        "protocol": {
            "panel": PANEL,
            "rows": int(len(frame)),
            "span": [str(frame["date"].min().date()),
                     str(frame["date"].max().date())],
            "decision_dates": int(frame["date"].nunique()),
            "symbols": int(frame["ins_code"].nunique()),
            "horizons": list(HORIZONS),
            "min_names_ic": MIN_NAMES_IC,
            "min_names_decile": MIN_NAMES_DECILE,
            "random_trials_per_date": RANDOM_TRIALS,
            "seed": SEED,
            "score_definition": "0.5*pct_rank(f_momentum)+0.5*pct_rank(bb_pct), "
                                "ranks over all names on the date, name dropped "
                                "if either input is NaN",
            "hit_ratio_definition": "share of top-decile names whose forward "
                                    "return exceeds the cross-sectional median",
            "no_parameters_fitted": True,
            "no_model_changes": True,
        },
        "coverage": coverage,
        "full_sample": summary,
        "random_control": random_control,
        "stability": stability,
        "decision_rule": verdicts,
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    with open(os.path.join(OUT_DIR, "alpha_core_rank_ic.json"), "w",
              encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    rows = []
    for label, _, _ in SUBPERIODS:
        for key, got in stability[label].items():
            rows.append(dict(period=label, key=key, **got))
    pd.DataFrame(rows).to_csv(
        os.path.join(OUT_DIR, "alpha_core_ic_stability.csv"),
        index=False, encoding="utf-8-sig")

    pd.DataFrame(list(summary.values())).to_csv(
        os.path.join(OUT_DIR, "alpha_core_ic_summary.csv"),
        index=False, encoding="utf-8-sig")

    print("\n  wrote alpha_core_rank_ic.json, rank_ic.csv, "
          "alpha_core_ic_summary.csv, alpha_core_ic_stability.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
