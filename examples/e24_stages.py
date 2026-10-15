"""E24 -- run Stage 1 and Stage 2 against the frozen preregistration.

Reads E21's panel artifact so the inputs are byte-identical to the record
that motivated this experiment, applies the decontamination declared in the
preregistration (and only that), and measures the single signed component.

Every number compared against here is read through `Manager.thresholds`,
which re-verifies the preregistration hash on each call, and the estimator
is IMPORTED from e21_stages rather than rewritten -- a second implementation
of the same closed-form IC would be a place for the two records to drift
apart without anyone deciding they should.

USAGE
    python examples/e24_stages.py
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
from examples import e21_preregister as e21_prereg              # noqa: E402
from examples import e21_stages as e21                          # noqa: E402
from examples import e24_preregister as prereg                  # noqa: E402
from sirat.experiments import universe                          # noqa: E402
from sirat.experiments.manager import Manager                   # noqa: E402

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

REPORT_PATH = os.path.join(OUT_DIR, "E24_stage_results.json")

# e21.measure_ic reads ITS preregistration's per-date floor. The two floors
# are the same number today; this assertion is what turns "today" into a
# guarantee, so the import cannot silently measure against a different one.
assert e21_prereg.MIN_NAMES_PER_DATE == prereg.MIN_NAMES_PER_DATE


def say(message):
    print(message, flush=True)


# ==============================
# DECONTAMINATION -- the preregistered rule, and nothing else
# ==============================

def decontaminate(panel, ceiling_pct):
    """Drop both legs of every impossible close-to-close transition.

    Iterates because removing a fake close creates new adjacencies; a fixed
    point is reached when no per-name transition exceeds the ceiling. The
    dropped (ins_code, slot) pairs are returned so forward targets that
    would have read a dropped session can be set to unknown -- the rule is
    unknown-never-False, in the panel and in the targets alike.
    """

    calendar = np.sort(panel["date"].unique())
    position = pd.Series(np.arange(len(calendar)), index=calendar)

    panel = panel.sort_values(["ins_code", "date"]).reset_index(drop=True)
    panel["slot"] = panel["date"].map(position).astype("int64")

    dropped = []
    rounds = 0

    while True:
        rounds += 1

        grouped = panel.groupby("ins_code", sort=False)["close"]
        ret = grouped.pct_change().to_numpy(float) * 100.0

        bad = np.isfinite(ret) & (np.abs(ret) > ceiling_pct)

        if not bad.any():
            break

        index = np.flatnonzero(bad)

        # Both legs: the row carrying the impossible return, and the row
        # whose close was its denominator. pct_change guarantees the
        # previous row belongs to the same name (the first row of each
        # name is NaN and NaN is never bad).
        legs = np.unique(np.concatenate([index, index - 1]))

        dropped.append(panel.iloc[legs][["ins_code", "slot", "date"]])

        panel = panel.drop(panel.index[legs]).reset_index(drop=True)

    dropped = (pd.concat(dropped, ignore_index=True) if dropped
               else pd.DataFrame(columns=["ins_code", "slot", "date"]))

    # Forward targets that reference a dropped session become unknown. The
    # slots were computed on the full calendar BEFORE any drop, so they
    # still mean the same session the targets were built against.
    dropped_keys = set(zip(dropped["ins_code"].tolist(),
                           dropped["slot"].tolist()))

    blanked = {}

    for horizon in prereg.DECAY_HORIZONS:
        ahead = panel["slot"].to_numpy() + horizon
        names = panel["ins_code"].to_numpy()

        mask = np.fromiter(
            ((name, slot) in dropped_keys
             for name, slot in zip(names, ahead)),
            dtype=bool, count=len(panel))

        for stem in ("next_unbuyable_h%d", "next_locked_up_h%d"):
            column = stem % horizon
            if column in panel.columns:
                panel.loc[mask, column] = np.nan

        blanked["h%d" % horizon] = int(mask.sum())

    return panel, {
        "rounds": rounds - 1,
        "rows_dropped": int(len(dropped)),
        "names_touched": int(dropped["ins_code"].nunique()),
        "dates_touched": int(dropped["date"].nunique()),
        "targets_set_unknown": blanked,
    }


# ==============================
# STAGE 1
# ==============================

def stage_one(manager, panel, clean_detail, thresholds, parameters):
    say("STAGE 1 -- DATA INTEGRITY")

    detail = {"decontamination": clean_detail}

    # ---- point in time --------------------------------------------------
    alignment = e21.verify_forward_alignment(panel)
    detail["forward_alignment"] = alignment
    say("  forward alignment : %.2f%% of %d resolved samples agree; "
        "%d absent left unknown"
        % (alignment["agreement_pct"], alignment["resolved"],
           alignment["missing_left_unknown"]))
    point_in_time = bool(alignment["agreement_pct"] == 100.0)

    # ---- survivorship ---------------------------------------------------
    snapshot_id = parameters["universe_snapshot"]
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
    start, end = parameters["discovery_window"]

    measurable = panel[panel[parameters["component"]].notna()]

    discovery = measurable[(measurable["date"] >= start)
                           & (measurable["date"] <= end)]

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
    coverage = 100.0 * float(panel[parameters["component"]].notna().mean())
    detail["component_coverage_pct"] = coverage
    missing_data = bool(coverage >= thresholds["min_flow_coverage_pct"])
    say("  flow coverage     : %.2f%% (floor %.1f%%)"
        % (coverage, thresholds["min_flow_coverage_pct"]))

    # ---- price sanity ----------------------------------------------------
    #
    # Re-verified on the CLEANED panel rather than trusted from the cleaning
    # step: the check and the repair are different code paths on purpose,
    # so a defect in one is caught by the other.
    ceiling = parameters["decontamination"]["impossible_session_move_pct"]

    ordered = panel.sort_values(["ins_code", "date"])
    session = (ordered.groupby("ins_code", sort=False)["close"]
               .pct_change().to_numpy(float) * 100.0)
    session = session[np.isfinite(session)]

    impossible = int((np.abs(session) > ceiling).sum())
    extreme = int((np.abs(session) > 12.0).sum())

    detail["price_evidence"] = {
        "close_session_returns": int(len(session)),
        "impossible_sessions": impossible,
        "extreme_sessions_over_12pct": extreme,
        "largest_session_move_pct": (float(np.max(np.abs(session)))
                                     if len(session) else None),
    }

    price_sanity = bool(len(session) > 0
                        and impossible
                        <= thresholds["max_impossible_rows_after_clean"])
    say("  price sanity      : %d impossible after clean (max %d); "
        "%d legitimate reopenings above 12%% retained"
        % (impossible, thresholds["max_impossible_rows_after_clean"],
           extreme))

    metrics = {
        "point_in_time": point_in_time,
        "survivorship": survivorship,
        "universe_valid": universe_valid,
        "missing_data": missing_data,
        "price_sanity": price_sanity,
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

def stage_two(manager, scored, thresholds, parameters, seed):
    say("STAGE 2 -- ECONOMIC SIGNAL VALIDATION")

    start, end = parameters["discovery_window"]

    discovery = scored[(scored["date"] >= start) & (scored["date"] <= end)]

    say("  discovery window %s .. %s, %d scored rows"
        % (start, end, len(discovery)))

    say("  primary target: next_unbuyable_h1")
    primary = e21.measure_ic(discovery, "next_unbuyable_h1", seed,
                             parameters["permutations"])

    detail = {"primary": primary, "decay": {}, "lift": {}}

    say("")
    say("  decile lift and decay (top decile of the SIGNED score):")

    for horizon in parameters["decay_horizons"]:
        target = "next_unbuyable_h%d" % horizon
        lift = e21.decile_lift(discovery, target)
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

    # ---- the preregistered diagnostic: reported, not gating --------------
    say("")
    say("  diagnostic -- excluding names already locked_up at t:")

    unlocked = discovery[~discovery["locked_up"].astype(bool)]

    excluded_ic = e21.measure_ic(unlocked, "next_unbuyable_h1", seed,
                                 parameters["permutations"], verbose=False)
    excluded_lift = e21.decile_lift(unlocked, "next_unbuyable_h1")

    detail["excluding_already_locked"] = {
        "rows_retained_pct": 100.0 * len(unlocked) / max(len(discovery), 1),
        "ic": excluded_ic,
        "lift_h1": excluded_lift,
    }

    say("    %.1f%% of rows survive; IC %.5f (t %.2f), lift %.3fx"
        % (detail["excluding_already_locked"]["rows_retained_pct"],
           excluded_ic["mean_ic"], excluded_ic["t_stat"],
           excluded_lift["lift"]))

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
# MAIN
# ==============================

def main():
    say("E24 -- INST_NET_RATIO AT THE CORRECTED SIGN")
    say("=" * 70)

    manager = Manager.load(prereg.EXPERIMENT_ID)

    thresholds = manager.thresholds()
    parameters = manager.parameters()
    seed = manager.seed()

    say("preregistration verified. seed %d, next stage %s"
        % (seed, manager.next_stage()))
    say("")

    say("loading E21 panel artifact ...")
    panel = pd.read_csv(PANEL_PATH, compression="gzip")
    say("  %d rows" % len(panel))

    say("decontaminating (both legs of |return| > %.0f%% transitions) ..."
        % parameters["decontamination"]["impossible_session_move_pct"])

    panel, clean_detail = decontaminate(
        panel, parameters["decontamination"]["impossible_session_move_pct"])

    say("  %d rows dropped across %d names, %d dates; %d rounds"
        % (clean_detail["rows_dropped"], clean_detail["names_touched"],
           clean_detail["dates_touched"], clean_detail["rounds"]))
    say("")

    report = {"experiment_id": prereg.EXPERIMENT_ID,
              "thresholds": thresholds, "seed": seed}

    stage1, detail1 = stage_one(manager, panel, clean_detail, thresholds,
                                parameters)
    report["stage_1"] = {"status": stage1["status"],
                         "failed": stage1["failed"], "detail": detail1}

    if stage1["status"] != "PASS":
        say("Stage 1 failed. Nothing downstream runs -- that is the point.")
        _write(report)
        return 0

    say("scoring (sign %+d within-date percentile rank of %s) ..."
        % (parameters["sign"], parameters["component"]))

    scored = panel[panel[parameters["component"]].notna()].copy()

    scored["score"] = (parameters["sign"]
                       * scored.groupby("date", sort=False)[
                           parameters["component"]].rank(pct=True))

    say("  %d scored rows" % len(scored))
    say("")

    stage2, detail2 = stage_two(manager, scored, thresholds, parameters, seed)
    report["stage_2"] = {"status": stage2["status"],
                         "failed": stage2["failed"], "detail": detail2}

    _write(report)

    say("Stages complete. No holdout exists to spend -- the record says so. "
        "The decision is recorded separately, citing this report.")

    return 0


def _write(report):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    say("wrote %s" % REPORT_PATH)


if __name__ == "__main__":
    sys.exit(main())
