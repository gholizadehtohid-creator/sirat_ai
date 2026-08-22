"""E21 -- FLOW-BASED LIMIT-LOCK FEASIBILITY SIGNAL. Preregistration only.

    QUESTION    does institutional (حقوقی) flow at session t predict that a
                name is UNBUYABLE at session t+1?
    NOT         a return prediction. Not an alpha factor. The output is a
                feasibility filter and it is registered as one.
    WHY NOW     RESEARCH_OPTIONS.md Option 1. It is measurement rather than
                prediction, so it structurally cannot produce a false alpha
                discovery -- the failure mode that ended Phase 1.

THIS FILE FREEZES THE EXPERIMENT AND COMPUTES NOTHING
------------------------------------------------------
Run it once, before the panel exists. Everything a result could be compared
against is decided here: the window, the components, their signs, the four
Stage 2 thresholds and the permutation seed. `Manager.thresholds` re-verifies
the preregistration hash on every read, so a number moved after this point
raises rather than quietly rescoring the experiment.

THE THREE WINDOW DECISIONS, AND WHY EACH IS WHAT IT IS
-------------------------------------------------------
DISCOVERY  2014-01-01 .. 2023-02-28

    The flow panel is 1.68M rows spanning 2008-2026, but row count does not
    make a cross-section. Measured 2026-08-20, the median number of names
    carrying flow on a date is 2 before 2014 and 339-653 after it. A signal
    ranked within date cannot be measured on two names, so the panel starts
    where it becomes a panel. That boundary is declared HERE, before any
    result exists, because a start date chosen after seeing an IC is a tuned
    parameter wearing a data-quality argument.

EXCLUDED   2023-03-01 .. 2024-09-01

    E19 spent this as its holdout. It is not reused, not even for training.
    Eighteen months of fitting data is a smaller loss than an argument about
    whether looking at it for a different target counts as looking at it.

HOLDOUT    2024-09-02 .. 2026-08-18

    Never evaluated by any experiment in the ledger. E20 could make no
    out-of-sample claim at all because E19 had already spent the only clean
    window; this one is genuinely untouched, and E21 spends it exactly once.

SCOPE ENDS AT STAGE 2, DELIBERATELY
------------------------------------
Stage 3 is EXECUTION REALISM and one of its five required checks is slippage.
Slippage has been 0.0% in every result this programme has ever produced,
because no impact model exists yet (RESEARCH_OPTIONS.md Option 2). Reporting
that check as passing would be reading absence as approval, which is the exact
defect this platform was built to refuse.

So E21 is a signal-EXISTENCE experiment. Its terminal decision is ARCHIVE (the
evidence stands, nothing is promoted) or REJECT. Turning it into a live filter
requires a successor experiment registered after the impact model lands. That
is declared here so it cannot later look like scope that quietly shrank.

USAGE
    python examples/e21_preregister.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments.manager import Manager, GovernanceError   # noqa: E402


EXPERIMENT_ID = "E21-flow-lock-feasibility"

UNIVERSE_SNAPSHOT = "u20260819-668658bb"

SEED = 20260822

DISCOVERY_START = "2014-01-01"
DISCOVERY_END = "2023-02-28"

EXCLUDED_START = "2023-03-01"      # E19's spent holdout
EXCLUDED_END = "2024-09-01"

HOLDOUT_START = "2024-09-02"
HOLDOUT_END = "2026-08-18"

# The composite. Four components, equal weight, ranked within date, signs fixed
# from theory before measurement. There is no fitting anywhere in this
# experiment -- an equal-weight rank composite has no free parameter to tune,
# which is what lets a single measurement be the whole test.
#
# The signs come from the magnet-effect literature: institutional trading
# contributes more to price acceleration toward the دامنه نوسان band than
# individual trading (23 of 24 sampled TSE companies). If that is right, a name
# absorbing institutional demand today is more likely to be queue-locked
# tomorrow.
COMPONENTS = {
    "inst_net_ratio": +1,      # institutional net buying, share of traded value
    "indiv_power": +1,         # per-capita individual buy / per-capita sell
    "closed_at_high": +1,      # session t already closed on its high
    "vol_ratio": +1,           # volume against its own trailing median
}

# A date with a handful of names cannot carry a cross-sectional rank
# correlation. 100 is the floor; the measured median is 339-653.
MIN_NAMES_PER_DATE = 100

PERMUTATIONS = 200

DECAY_HORIZONS = (1, 2, 3, 5)


PREREGISTRATION = {
    "parameters": {
        "target": "next_unbuyable",
        "target_definition": (
            "E14's detector, reused unchanged: unbuyable = locked_up | reopen "
            "| extreme_gap, where locked_up = (high == low) AND volume > 0 AND "
            "traded_prev AND +1% < return <= +12%. Reusing E14 rather than "
            "defining a new lock is deliberate -- a fresh lock definition "
            "would be a free parameter, and this one was validated in anger."),
        "secondary_target": "next_locked_up",
        "components": COMPONENTS,
        "combination": (
            "equal-weight mean of within-date percentile ranks, signs as "
            "declared. No fitting, no weights, no threshold search."),
        "discovery_window": [DISCOVERY_START, DISCOVERY_END],
        "excluded_window": [EXCLUDED_START, EXCLUDED_END],
        "excluded_reason": "E19 spent this as its holdout on 2026-08-19",
        "holdout_window": [HOLDOUT_START, HOLDOUT_END],
        "min_names_per_date": MIN_NAMES_PER_DATE,
        "permutations": PERMUTATIONS,
        "decay_horizons": list(DECAY_HORIZONS),
        "scope": "stages 1-2 only; terminal decision is ARCHIVE or REJECT",
        "universe_snapshot": UNIVERSE_SNAPSHOT,
    },

    "validation_gates": {
        "point_in_time": (
            "every component reads session t or earlier; the target is built "
            "by E14's session-aware next_session_state, never by shift(-h) "
            "over rows"),
        "survivorship": (
            "universe snapshot %s restores and hash-verifies" % UNIVERSE_SNAPSHOT),
        "universe_valid": (
            "median names per date in the discovery window >= %d"
            % MIN_NAMES_PER_DATE),
        "missing_data": "flow present for >= 60% of price rows in the window",
        "ic": "mean per-date Spearman(score, next_unbuyable) >= 0.05",
        "rank_correlation": "t-statistic of the per-date IC series >= 3.0",
        "random_baseline": (
            "observed mean IC > 95th percentile of a within-date label "
            "permutation null, %d permutations, seed %d"
            % (PERMUTATIONS, SEED)),
        "factor_decay": (
            "top-decile unbuyable rate >= 2.0x the universe rate at h=1, and "
            "retains >= 30% of that lift at h=3"),
    },

    "expected_metrics": {
        "mean_ic": ">= 0.05",
        "ic_t_stat": ">= 3.0",
        "null_percentile": "> 95.0",
        "decile_lift_h1": ">= 2.0",
        "lift_retention_h3": ">= 0.30",
        "holdout_mean_ic": (
            "reported once, spent once. Not a gate -- the discovery window "
            "decides Stage 2. The holdout says whether the answer held."),
    },

    # Every one of these is a number a result will be measured against. None
    # has a default anywhere in the platform, and none may move after this run.
    "rejection_thresholds": {
        "min_mean_ic": 0.05,
        "min_ic_t_stat": 3.0,
        "min_null_percentile": 95.0,
        "min_decile_lift_h1": 2.0,
        "min_lift_retention_h3": 0.30,
        "min_names_per_date": MIN_NAMES_PER_DATE,
        "min_median_names_discovery": float(MIN_NAMES_PER_DATE),
        "min_flow_coverage_pct": 60.0,
    },

    "seed": SEED,
}


HYPOTHESIS = (
    "Institutional (حقوقی) net flow and per-capita individual buying power at "
    "session t predict, cross-sectionally, whether a Tehran-listed name is "
    "UNBUYABLE at session t+1 -- locked in a صف خرید, reopening, or gapped "
    "away. This is a tradability prediction, not a return prediction: the "
    "output is a feasibility filter for the Stage 1 gate, and it is measured "
    "against a microstructure state, never against a forward return."
)

EXPECTED_FAILURE = (
    "The equal-weight composite shows no per-date rank correlation with "
    "next-session unbuyability beyond a label-permutation null -- i.e. "
    "client_type flow carries no cross-sectional information about queue "
    "formation on this market. The second, more likely failure is the E20 "
    "mechanism repeating: the composite predicts locks only among illiquid "
    "names that no position could enter anyway, making the filter true and "
    "useless. Stage 2's decile-lift check is where that would show, and it is "
    "written to catch it rather than to be passed."
)

ACCEPTANCE_CRITERIA = (
    "All four Stage 2 checks pass on the DISCOVERY window against the "
    "thresholds frozen here, and the holdout -- evaluated exactly once -- "
    "carries the same sign. Passing earns an ARCHIVE with the evidence "
    "recorded and a successor experiment for the execution question. It does "
    "not earn a promotion: Stage 3 requires a slippage model that does not "
    "exist yet, and reporting that check as passing would be reading absence "
    "as approval."
)

NOTES = (
    "RESEARCH_OPTIONS.md Option 1, chosen 2026-08-22. Uses client_type_history "
    "(1,681,938 rows, 780 instruments), which no experiment in this programme "
    "has ever read. Panel width measured 2026-08-20: median names per date is "
    "2 before 2014, then 339 / 533 / 653 across 2014-2018 / 2019-2022 / "
    "2023-2026, joinable to prices on 1,453,648 rows (75.9%) with 0% of rows "
    "missing the per-participant count columns. Does not touch, re-run, "
    "re-score or reference Alpha Core (frozen at 90024d86d7a5c268); E19 and "
    "E20 remain adjudicated and nothing here can reopen them."
)


def main():
    print("E21 PREREGISTRATION")
    print("=" * 70)
    print("discovery : %s .. %s" % (DISCOVERY_START, DISCOVERY_END))
    print("excluded  : %s .. %s   (E19's spent holdout)"
          % (EXCLUDED_START, EXCLUDED_END))
    print("holdout   : %s .. %s   (never evaluated)"
          % (HOLDOUT_START, HOLDOUT_END))
    print("seed      : %d" % SEED)
    print("scope     : stages 1-2; terminal decision ARCHIVE or REJECT")
    print()

    try:
        manager = Manager.register(
            experiment_id=EXPERIMENT_ID,
            hypothesis=HYPOTHESIS,
            strategy_version="flow-lock-composite-v1",
            researcher="SIRAT-AI Research",
            notes=NOTES,
            expected_failure=EXPECTED_FAILURE,
            acceptance_criteria=ACCEPTANCE_CRITERIA,
            preregistration=PREREGISTRATION,
            universe_snapshot=UNIVERSE_SNAPSHOT,
        )
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1

    record = manager.record()
    prov = record["provenance"]

    print("registered.")
    print("  prereg hash   : %s" % prov.get("prereg_hash"))
    print("  git commit    : %s" % prov.get("git_commit"))
    print("  git dirty     : %s" % prov.get("git_dirty"))
    print("  reproducible  : %s" % prov.get("reproducible"))
    print("  next stage    : %s" % manager.next_stage())
    print()
    print("Thresholds are frozen. Nothing has been computed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
