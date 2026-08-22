"""E21 -- run Stage 1 and Stage 2 against the frozen preregistration.

Every number this file compares against is read through `Manager.thresholds`,
which re-verifies the preregistration hash on each call. Nothing here chooses a
threshold; if one is missing the run raises rather than inventing a default.

WHY THE IC IS COMPUTED IN CLOSED FORM
--------------------------------------
The target is binary, so Spearman between the score rank and the label reduces
to an expression in the mean rank of the positive class:

    corr = (M1 - rbar) * sqrt(p / (1 - p)) / sd(r)

That is not an approximation -- ranking a 0/1 vector is an affine map, so
Pearson on (rank_score, y) IS Spearman. It matters because the preregistered
null is 200 within-date label permutations over ~2,200 dates, and the closed
form turns that from an overnight job into a minute.

USAGE
    python examples/e21_stages.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
from examples import e21_preregister as prereg                  # noqa: E402
from sirat.experiments import universe                          # noqa: E402
from sirat.experiments.manager import Manager                   # noqa: E402

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

REPORT_PATH = os.path.join(OUT_DIR, "E21_stage_results.json")

FEATURES = ("inst_net_ratio", "indiv_power", "closed_at_high", "vol_ratio")


def say(message):
    print(message, flush=True)


# ==============================
# SCORE
# ==============================

def score_panel(panel):
    """Equal-weight mean of within-date percentile ranks. No fitting."""

    complete = panel[panel["complete"].astype(bool)].copy()

    grouped = complete.groupby("date", sort=False)

    parts = []

    for name in FEATURES:
        sign = prereg.COMPONENTS[name]
        parts.append(sign * grouped[name].rank(pct=True))

    complete["score"] = sum(parts) / float(len(FEATURES))

    return complete


# ==============================
# THE IC, AND ITS NULL
# ==============================

def _per_date_ic(date_id, rank, y, counts):
    """Spearman per date, closed form. Returns one IC per date."""

    n = counts.astype(float)

    sum_r = np.bincount(date_id, weights=rank)
    sum_r2 = np.bincount(date_id, weights=rank * rank)
    sum_y = np.bincount(date_id, weights=y)
    sum_ry = np.bincount(date_id, weights=rank * y)

    mean_r = sum_r / n
    var_r = sum_r2 / n - mean_r * mean_r
    sd_r = np.sqrt(np.maximum(var_r, 0.0))

    p = sum_y / n

    with np.errstate(divide="ignore", invalid="ignore"):
        m1 = sum_ry / sum_y                       # mean rank of the positives
        ic = (m1 - mean_r) * np.sqrt(p / (1.0 - p)) / sd_r

    # A date with no positive class, or every name positive, carries no rank
    # information at all. It is dropped, not scored zero -- zero would be a
    # measurement, and there was none.
    ic[(sum_y <= 0) | (p >= 1.0) | (sd_r <= 0)] = np.nan

    return ic


def measure_ic(frame, target, seed, permutations, verbose=True):
    frame = frame[frame[target].notna()].copy()

    dates = np.sort(frame["date"].unique())
    lookup = pd.Series(np.arange(len(dates)), index=dates)

    date_id = frame["date"].map(lookup).to_numpy(dtype=np.int64)
    rank = frame["score"].to_numpy(dtype=float)
    y = frame[target].to_numpy(dtype=float)

    counts = np.bincount(date_id, minlength=len(dates))

    observed = _per_date_ic(date_id, rank, y, counts)

    usable = counts >= prereg.MIN_NAMES_PER_DATE

    valid = usable & np.isfinite(observed)

    ic_series = observed[valid]

    mean_ic = float(np.mean(ic_series))
    n_dates = int(valid.sum())
    t_stat = (float(mean_ic / (np.std(ic_series, ddof=1) / np.sqrt(n_dates)))
              if n_dates > 1 else float("nan"))

    if verbose:
        say("    dates scored     : %d  (of %d, floor %d names)"
            % (n_dates, len(dates), prereg.MIN_NAMES_PER_DATE))
        say("    mean IC          : %.5f" % mean_ic)
        say("    t-stat           : %.3f" % t_stat)

    # ---- the preregistered null ----------------------------------------
    #
    # Labels are shuffled WITHIN date. Shuffling across dates would destroy the
    # date structure as well as the signal, and would make almost anything look
    # significant.

    order_base = np.argsort(date_id, kind="stable")
    date_sorted = date_id[order_base]
    rank_sorted = rank[order_base]
    y_sorted = y[order_base]

    rng = np.random.default_rng(seed)

    null_means = np.empty(permutations, dtype=float)

    for index in range(permutations):
        shuffle = np.lexsort((rng.random(len(y_sorted)), date_sorted))
        permuted = y_sorted[shuffle]
        null_ic = _per_date_ic(date_sorted, rank_sorted, permuted, counts)
        null_valid = usable & np.isfinite(null_ic)
        null_means[index] = np.mean(null_ic[null_valid])

    percentile = float(100.0 * np.mean(null_means < mean_ic))

    if verbose:
        say("    null p95         : %.5f" % float(np.percentile(null_means, 95)))
        say("    observed sits at : %.2f th percentile" % percentile)

    return {
        "mean_ic": mean_ic,
        "t_stat": t_stat,
        "dates": n_dates,
        "rows": int(len(frame)),
        "base_rate_pct": float(100.0 * np.mean(y)),
        "null_p95": float(np.percentile(null_means, 95)),
        "null_percentile": percentile,
    }


def decile_lift(frame, target):
    """Top-decile event rate against the universe rate. E14's own criterion."""

    frame = frame[frame[target].notna()]

    if frame.empty:
        return {"lift": float("nan"), "top_rate_pct": float("nan"),
                "universe_rate_pct": float("nan"), "rows": 0}

    cut = frame.groupby("date", sort=False)["score"].transform(
        lambda s: s.rank(pct=True))

    top = frame[cut > 0.90]

    universe_rate = float(frame[target].mean())
    top_rate = float(top[target].mean()) if len(top) else float("nan")

    return {
        "lift": (top_rate / universe_rate) if universe_rate > 0 else float("nan"),
        "top_rate_pct": 100.0 * top_rate,
        "universe_rate_pct": 100.0 * universe_rate,
        "rows": int(len(top)),
    }


# ==============================
# STAGE 1
# ==============================

def verify_forward_alignment(panel, samples=2000, seed=20260822):
    """Is `next_unbuyable_h1` really session t+1's state, on the calendar?

    A structural claim in a docstring is not a check. This recomputes the
    alignment on a random sample from the panel itself: for each sampled
    (name, date), find the next calendar session that name actually has, and
    compare. A mismatch means the shift crossed a suspension.
    """

    calendar = np.sort(panel["date"].unique())
    position = pd.Series(np.arange(len(calendar)), index=calendar)

    frame = panel[["ins_code", "date", "unbuyable", "next_unbuyable_h1"]].copy()
    frame["slot"] = frame["date"].map(position).astype("int64")

    truth = frame.set_index(["ins_code", "slot"])["unbuyable"]

    rng = np.random.default_rng(seed)
    picks = frame.iloc[rng.choice(len(frame), size=min(samples, len(frame)),
                                  replace=False)]

    agree = 0
    checked = 0
    missing_stayed_nan = 0

    for row in picks.itertuples():
        key = (row.ins_code, row.slot + 1)
        expected = truth.get(key, None)

        if expected is None:
            # No row at t+1 on the calendar. The panel must say "unknown".
            if pd.isna(row.next_unbuyable_h1):
                missing_stayed_nan += 1
                checked += 1
            continue

        checked += 1
        if bool(expected) == bool(row.next_unbuyable_h1):
            agree += 1

    resolved = checked - missing_stayed_nan

    return {
        "sampled": int(len(picks)),
        "checked": checked,
        "resolved": resolved,
        "agree": agree,
        "agreement_pct": (100.0 * agree / resolved) if resolved else float("nan"),
        "missing_left_unknown": missing_stayed_nan,
    }


def stage_one(manager, panel, thresholds):
    say("STAGE 1 -- DATA INTEGRITY")

    detail = {}

    # ---- point in time --------------------------------------------------
    alignment = verify_forward_alignment(panel)
    detail["forward_alignment"] = alignment
    say("  forward alignment : %.2f%% of %d resolved samples agree; "
        "%d absent left unknown"
        % (alignment["agreement_pct"], alignment["resolved"],
           alignment["missing_left_unknown"]))
    point_in_time = bool(alignment["agreement_pct"] == 100.0)

    # ---- survivorship ---------------------------------------------------
    snapshot_id = prereg.UNIVERSE_SNAPSHOT
    try:
        restored = universe.restore(snapshot_id)
        survivorship = True
        detail["universe_snapshot"] = {
            "snapshot_id": snapshot_id,
            "instruments": len(restored.get("instruments", restored)
                               if isinstance(restored, dict) else restored),
            "restored": True,
        }
    except Exception as exc:                                    # noqa: BLE001
        survivorship = False
        detail["universe_snapshot"] = {"snapshot_id": snapshot_id,
                                       "restored": False,
                                       "error": "%s: %s"
                                                % (type(exc).__name__, exc)}
    say("  universe snapshot : %s" % ("restored and hash-verified"
                                      if survivorship else "FAILED TO RESTORE"))

    # ---- universe validity ----------------------------------------------
    discovery = panel[(panel["date"] >= prereg.DISCOVERY_START)
                      & (panel["date"] <= prereg.DISCOVERY_END)
                      & panel["complete"].astype(bool)]

    per_date = discovery.groupby("date").size()
    median_names = float(per_date.median()) if len(per_date) else 0.0

    detail["discovery_width"] = {
        "dates": int(len(per_date)),
        "median_names_per_date": median_names,
        "min_names_per_date": int(per_date.min()) if len(per_date) else 0,
        "max_names_per_date": int(per_date.max()) if len(per_date) else 0,
    }

    universe_valid = bool(median_names
                          >= thresholds["min_median_names_discovery"])
    say("  discovery width   : %d dates, median %.0f names/date (floor %.0f)"
        % (len(per_date), median_names,
           thresholds["min_median_names_discovery"]))

    # ---- missing data ---------------------------------------------------
    coverage = 100.0 * float(panel["complete"].astype(bool).mean())
    detail["flow_coverage_pct"] = coverage
    missing_data = bool(coverage >= thresholds["min_flow_coverage_pct"])
    say("  flow coverage     : %.2f%% (floor %.1f%%)"
        % (coverage, thresholds["min_flow_coverage_pct"]))

    metrics = {
        "point_in_time": point_in_time,
        "survivorship": survivorship,
        "universe_valid": universe_valid,
        "missing_data": missing_data,
    }

    result = manager.run_stage(1, metrics, detail)

    say("  -> %s%s" % (result["status"],
                       ("  failed: " + ", ".join(result["failed"]))
                       if result["failed"] else ""))
    say("")

    return result, detail


# ==============================
# STAGE 2
# ==============================

def stage_two(manager, scored, thresholds, seed):
    say("STAGE 2 -- ECONOMIC SIGNAL VALIDATION")

    discovery = scored[(scored["date"] >= prereg.DISCOVERY_START)
                       & (scored["date"] <= prereg.DISCOVERY_END)]

    say("  discovery window %s .. %s, %d scored rows"
        % (prereg.DISCOVERY_START, prereg.DISCOVERY_END, len(discovery)))

    say("  primary target: next_unbuyable_h1")
    primary = measure_ic(discovery, "next_unbuyable_h1", seed,
                         prereg.PERMUTATIONS)

    detail = {"primary": primary, "decay": {}, "lift": {}}

    say("")
    say("  decile lift and decay:")

    for horizon in prereg.DECAY_HORIZONS:
        target = "next_unbuyable_h%d" % horizon
        lift = decile_lift(discovery, target)
        detail["lift"]["h%d" % horizon] = lift
        say("    h=%-2d top-decile %.3f%% vs universe %.3f%%  -> lift %.3fx"
            % (horizon, lift["top_rate_pct"], lift["universe_rate_pct"],
               lift["lift"]))

    lift_h1 = detail["lift"]["h1"]["lift"]
    lift_h3 = detail["lift"]["h3"]["lift"]

    excess_h1 = lift_h1 - 1.0
    excess_h3 = lift_h3 - 1.0

    retention = (excess_h3 / excess_h1) if excess_h1 > 0 else float("nan")

    detail["decay"] = {"lift_h1": lift_h1, "lift_h3": lift_h3,
                       "excess_h1": excess_h1, "excess_h3": excess_h3,
                       "retention_h3": retention}

    say("    retention at h=3 : %.3f  (floor %.2f)"
        % (retention, thresholds["min_lift_retention_h3"]))

    metrics = {
        "ic": bool(primary["mean_ic"] >= thresholds["min_mean_ic"]),
        "rank_correlation": bool(primary["t_stat"]
                                 >= thresholds["min_ic_t_stat"]),
        "random_baseline": bool(primary["null_percentile"]
                                > thresholds["min_null_percentile"]),
        "factor_decay": bool(
            (lift_h1 >= thresholds["min_decile_lift_h1"])
            and (retention >= thresholds["min_lift_retention_h3"])),
    }

    say("")
    for name in ("ic", "rank_correlation", "random_baseline", "factor_decay"):
        say("    %-18s %s" % (name, "PASS" if metrics[name] else "FAIL"))

    result = manager.run_stage(2, metrics, detail)

    say("  -> %s%s" % (result["status"],
                       ("  failed: " + ", ".join(result["failed"]))
                       if result["failed"] else ""))
    say("")

    return result, detail


# ==============================
# HOLDOUT
# ==============================

def spend_holdout(manager, scored, seed):
    """Evaluated exactly once. `touch_window` refuses a second visit."""

    say("HOLDOUT -- %s .. %s (spent once)"
        % (prereg.HOLDOUT_START, prereg.HOLDOUT_END))

    manager.touch_window(prereg.HOLDOUT_START, prereg.HOLDOUT_END, "holdout")

    frame = scored[(scored["date"] >= prereg.HOLDOUT_START)
                   & (scored["date"] <= prereg.HOLDOUT_END)]

    out = measure_ic(frame, "next_unbuyable_h1", seed, prereg.PERMUTATIONS)

    lift = decile_lift(frame, "next_unbuyable_h1")

    say("    top-decile %.3f%% vs universe %.3f%%  -> lift %.3fx"
        % (lift["top_rate_pct"], lift["universe_rate_pct"], lift["lift"]))
    say("")

    out["lift_h1"] = lift

    return out


# ==============================
# MAIN
# ==============================

def main():
    say("E21 -- FLOW-BASED LIMIT-LOCK FEASIBILITY SIGNAL")
    say("=" * 70)

    manager = Manager.load(prereg.EXPERIMENT_ID)

    thresholds = manager.thresholds()
    seed = manager.seed()

    say("preregistration verified. seed %d, next stage %s"
        % (seed, manager.next_stage()))
    say("")

    say("loading panel ...")
    panel = pd.read_csv(PANEL_PATH, compression="gzip")
    say("  %d rows" % len(panel))
    say("")

    report = {"experiment_id": prereg.EXPERIMENT_ID,
              "thresholds": thresholds, "seed": seed}

    stage1, detail1 = stage_one(manager, panel, thresholds)
    report["stage_1"] = {"status": stage1["status"],
                         "failed": stage1["failed"], "detail": detail1}

    if stage1["status"] != "PASS":
        say("Stage 1 failed. Nothing downstream runs -- that is the point.")
        _write(report)
        return 0

    say("scoring (equal-weight within-date rank composite) ...")
    scored = score_panel(panel)
    say("  %d scored rows" % len(scored))
    say("")

    stage2, detail2 = stage_two(manager, scored, thresholds, seed)
    report["stage_2"] = {"status": stage2["status"],
                         "failed": stage2["failed"], "detail": detail2}

    report["holdout"] = spend_holdout(manager, scored, seed)

    _write(report)

    say("Stages complete. The decision is recorded separately, by a human, "
        "citing this report.")

    return 0


def _write(report):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    say("wrote %s" % REPORT_PATH)


if __name__ == "__main__":
    sys.exit(main())
