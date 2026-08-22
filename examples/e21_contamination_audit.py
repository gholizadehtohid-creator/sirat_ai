"""Is E21's positive result an artifact of contaminated data? POST-HOC AUDIT.

E22 found 252 rows in `price_history` carrying returns above 200% -- the
largest +19,976,600%, a prior close of 1 rial -- and 7,388 rows (0.39%) beyond
E14's 12% band ceiling. That was diagnosed as a cost-model problem.

It is not only a cost-model problem, and this file exists because of what it
implies for the one positive result this programme has produced.

THE SPECIFIC WORRY
------------------
E21's target is E14's detector:

    unbuyable = locked_up | reopen | extreme_gap
                                     ^^^^^^^^^^^  |ret| > 12%

So every contaminated bar is a member of E21's POSITIVE CLASS. And two of the
four score components see exactly the pattern a new listing or an unadjusted
capital increase produces: `vol_ratio` (volume against a short trailing median)
spikes on a first trade, and `closed_at_high` is true when a price jump is an
adjustment rather than a move.

If E21's IC of 0.13650 and lift of 2.625x come substantially from predicting
data artifacts, then the result is a measurement of the database rather than of
the market, and saying so is worth more than the result was.

WHAT THIS FILE MAY AND MAY NOT DO
----------------------------------
MAY: decompose the positive class, and re-measure the SAME frozen score against
narrower targets that were already built into E21's panel.

MAY NOT: change E21's verdict. E21 is ARCHIVED on the target it preregistered,
measured against thresholds frozen before the panel existed. This is an audit
attached to that record, not a re-run of it. If the result turns out to be
artifact-driven, the honest response is a recorded caveat and a successor
experiment on a clean target -- not a quiet edit of what E21 concluded.

Note that `next_locked_up` was preregistered by E21 as its SECONDARY target and
is already in the panel, so measuring it here is reading a column E21 committed
to in advance rather than searching for a target that flatters the answer.

USAGE
    python examples/e21_contamination_audit.py
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
from examples import e21_stages as e21s                         # noqa: E402

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

REPORT_PATH = os.path.join(OUT_DIR, "E21_contamination_audit.json")

EXTREME_MOVE_PCT = 12.0

IMPOSSIBLE_MOVE_PCT = 200.0


def say(message):
    print(message, flush=True)


def main():
    say("E21 CONTAMINATION AUDIT -- is the positive result real?")
    say("=" * 78)
    say("E21 is ARCHIVED and its verdict is not touched here.")
    say("")

    panel = pd.read_csv(PANEL_PATH, compression="gzip")

    # Rebuild the per-bar state E14 derives, so the positive class can be split
    # into the three things it is a union of.
    panel = panel.sort_values(["ins_code", "date"]).copy()
    grouped = panel.groupby("ins_code", sort=False)

    panel["ret"] = grouped["close"].pct_change() * 100.0
    traded = panel["volume"] > 0
    traded_prev = grouped["volume"].shift(1) > 0

    panel["is_reopen"] = traded & ~traded_prev
    panel["is_extreme"] = traded & (panel["ret"].abs() > EXTREME_MOVE_PCT)
    panel["is_impossible"] = traded & (panel["ret"].abs() > IMPOSSIBLE_MOVE_PCT)
    panel["is_locked"] = panel["locked_up"].astype(bool)

    calendar = np.sort(panel["date"].unique())
    position = pd.Series(np.arange(len(calendar)), index=calendar)
    panel["slot"] = panel["date"].map(position).astype("int64")

    # Attach each COMPONENT of the t+1 state, the same way the panel attached
    # the union.
    nxt = panel[["ins_code", "slot", "is_locked", "is_reopen", "is_extreme",
                 "is_impossible"]].copy()
    nxt["slot"] = nxt["slot"] - 1
    nxt = nxt.rename(columns={"is_locked": "n_locked",
                              "is_reopen": "n_reopen",
                              "is_extreme": "n_extreme",
                              "is_impossible": "n_impossible"})

    panel = panel.merge(nxt, on=["ins_code", "slot"], how="left")

    frame = panel[panel["complete"].astype(bool)
                  & (panel["date"] >= prereg.DISCOVERY_START)
                  & (panel["date"] <= prereg.DISCOVERY_END)
                  & panel["next_unbuyable_h1"].notna()].copy()

    say("discovery rows with a resolved target: %d" % len(frame))
    say("")

    # ---- 1. what is the positive class actually made of? ----------------
    say("1. COMPOSITION OF E21'S POSITIVE CLASS")

    positives = frame[frame["next_unbuyable_h1"].astype(bool)]

    total = len(positives)

    say("   positives: %d  (%.4f%% of rows)"
        % (total, 100.0 * total / len(frame)))

    composition = {}

    for label, column in (("locked_up (صف خرید)", "n_locked"),
                          ("reopen", "n_reopen"),
                          ("extreme_gap (|ret|>12%)", "n_extreme"),
                          ("  of which impossible (>200%)", "n_impossible")):
        count = int(positives[column].fillna(False).astype(bool).sum())
        composition[column] = {"count": count,
                               "share_pct": 100.0 * count / total}
        say("     %-32s %8d  %6.2f%%"
            % (label, count, 100.0 * count / total))

    say("")
    say("   (these overlap -- a bar can be both a reopening and a gap)")
    say("")

    # ---- 2. the same frozen score against narrower targets --------------
    say("2. THE SAME FROZEN SCORE, AGAINST NARROWER TARGETS")
    say("   next_locked_up was E21's PREREGISTERED SECONDARY target, so this")
    say("   is reading a committed column, not hunting for a flattering one.")
    say("")

    scored = e21s.score_panel(frame)

    # A target with every trace of the contaminated class removed. Built here,
    # declared here, and used only for the audit.
    scored["clean_unbuyable"] = (
        scored["next_unbuyable_h1"].astype(bool)
        & ~scored["n_extreme"].fillna(False).astype(bool))

    scored["pure_lock"] = scored["n_locked"].fillna(False).astype(bool)

    results = {}

    for label, target in (("next_unbuyable_h1  (E21's declared target)",
                           "next_unbuyable_h1"),
                          ("next_locked_up_h1  (E21's secondary target)",
                           "next_locked_up_h1"),
                          ("pure lock, gaps removed", "pure_lock"),
                          ("unbuyable minus every extreme_gap",
                           "clean_unbuyable")):
        base = float(scored[target].astype(float).mean())

        out = e21s.measure_ic(scored, target, prereg.SEED, 50, verbose=False)
        lift = e21s.decile_lift(scored, target)

        results[target] = {"mean_ic": out["mean_ic"], "t_stat": out["t_stat"],
                           "lift": lift["lift"], "base_rate_pct": 100.0 * base,
                           "dates": out["dates"]}

        say("   %-44s" % label)
        say("       base rate %6.3f%%   IC %8.5f   t %8.2f   lift %6.3fx"
            % (100.0 * base, out["mean_ic"], out["t_stat"], lift["lift"]))

    say("")

    # ---- 3. the verdict --------------------------------------------------
    declared = results["next_unbuyable_h1"]
    clean = results["clean_unbuyable"]
    pure = results["pure_lock"]

    retained_clean = clean["mean_ic"] / declared["mean_ic"]
    retained_pure = pure["mean_ic"] / declared["mean_ic"]

    say("3. VERDICT")
    say("   IC retained with every extreme_gap removed : %.1f%%"
        % (100.0 * retained_clean))
    say("   IC retained against pure queue locks only  : %.1f%%"
        % (100.0 * retained_pure))
    say("")

    threshold = 0.50

    survived = bool(retained_clean >= threshold and retained_pure >= threshold)

    if survived:
        say("   E21's result SURVIVES the contamination it could have been")
        say("   built on. The score predicts genuine صف خرید formation, not a")
        say("   database artifact. The archived verdict stands as recorded.")
    else:
        say("   E21's result is SUBSTANTIALLY ARTIFACT-DRIVEN. The archived")
        say("   verdict is not edited -- it was measured honestly against a")
        say("   preregistered target -- but it must carry this caveat wherever")
        say("   it is cited, and a successor experiment must preregister a")
        say("   target that excludes extreme_gap bars.")

    report = {
        "note": ("post-hoc audit; E21 remains ARCHIVED on its preregistered "
                 "target and this changes no verdict"),
        "discovery_rows": int(len(frame)),
        "positive_class_composition": composition,
        "by_target": results,
        "ic_retained_clean": retained_clean,
        "ic_retained_pure_lock": retained_pure,
        "retention_threshold": threshold,
        "survives": survived,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)

    say("")
    say("wrote %s" % REPORT_PATH)

    return 0


if __name__ == "__main__":
    sys.exit(main())
