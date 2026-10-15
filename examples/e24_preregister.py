"""E24 -- INST_NET_RATIO AT THE CORRECTED SIGN. Preregistration only.

    QUESTION    does institutional net flow at session t predict next-session
                unbuyability at the NEGATIVE sign -- the direction E21
                measured and refused to adopt mid-experiment?
    NOT         a return prediction, and NOT an independent discovery. The
                direction was learned from E21's post-hoc diagnostics on this
                same window. This file exists to put the corrected claim on
                the record the only legitimate way: declared first, measured
                second, against floors frozen here.
    WHY NOW     E21's verdict names this as successor (b), after the impact
                model (a) landed in backtesting/impact.py. Successor (c)
                stays blocked on a validated return signal, which E19/E20
                established does not exist.

WHY THIS EXPERIMENT CANNOT CLAIM OUT-OF-SAMPLE CONFIRMATION
------------------------------------------------------------
Every historical holdout is spent: E19 took 2023-03-01..2024-09-01, E21 took
2024-09-02..2026-08-18, and market data ends 2026-08-18. Like E20, this
experiment declares up front that it can establish a preregistered in-sample
record and a REJECT, never an out-of-sample confirmation. The ONLY window on
which the directional claim may ever be tested out of sample is the forward
reservation R-FORWARD-2027 (2026-08-19..2027-08-19), which this experiment
NAMES and does NOT claim -- spending it belongs to a successor registered
after the window accrues.

WHAT IS GENUINELY UNMEASURED BEFORE THIS RUN
---------------------------------------------
E21's diagnostic reported three numbers for this component under the WRONG
declared sign: IC -0.08707, t -60.22, top-decile lift 0.416x. It never
measured, for the SIGNED single component:

    - placement against the within-date label-permutation null
    - the decile lift at the corrected sign (the 0.416x figure is the +1
      top decile; the -1 top decile is a different set of names and its
      event rate has never been computed)
    - decay retention at h=3
    - the already-locked-at-t exclusion

Those four are what Stage 2 gates on, so the run can genuinely fail. The
floors are E21's, reused rather than re-derived, so that a pass and E21's
pass mean the same thing.

THE ONE DATA DECISION, DECLARED BEFORE MEASUREMENT
---------------------------------------------------
E22 found 252 rows in price_history carrying returns no market ever printed
(the largest +19,976,600%, a prior close of 1 rial), and its verdict was
recorded on the contaminated panel because repairing data AFTER seeing a
verdict is forbidden. This experiment declares the repair BEFORE any number
exists, citing the platform's own constant rather than inventing a bound:

    a per-name close-to-close transition with |return| above
    IMPOSSIBLE_SESSION_MOVE_PCT (100.0, sirat/experiments/gates.py) marks
    BOTH legs contaminated; contaminated rows are dropped, iterating until
    no impossible transition remains; a forward target that would read a
    dropped session becomes UNKNOWN (NaN), never False.

Both legs, because the impossible arithmetic says one of the two closes is
fake without saying which. Unknown-never-False, because asserting a dropped
session was buyable is untrue in exactly the direction that flatters a
positive signal. Stage 1's price_sanity check then re-verifies the cleaned
panel and fails the experiment if anything impossible survived.

USAGE
    python examples/e24_preregister.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments.manager import Manager, GovernanceError   # noqa: E402


EXPERIMENT_ID = "E24-inst-net-supply-direction"

UNIVERSE_SNAPSHOT = "u20260819-668658bb"

SEED = 20260823

# E21's windows, unchanged. Discovery starts where the flow panel becomes a
# panel (median 2 names/date before 2014); the two later windows are spent
# holdouts and are not read.
DISCOVERY_START = "2014-01-01"
DISCOVERY_END = "2023-02-28"

FORWARD_RESERVATION = "R-FORWARD-2027"

# The single component and its corrected sign. No composite: revising E21's
# four-component composite is a third experiment's question, and bundling it
# here would blur which claim the record certifies.
COMPONENT = "inst_net_ratio"

SIGN = -1

MIN_NAMES_PER_DATE = 100

PERMUTATIONS = 200

DECAY_HORIZONS = (1, 2, 3, 5)

# The platform's own impossibility ceiling, cited not chosen. gates.py counts
# a session move beyond it as a price the market never printed.
IMPOSSIBLE_SESSION_MOVE_PCT = 100.0


PREREGISTRATION = {
    "parameters": {
        "target": "next_unbuyable",
        "target_definition": (
            "E14's detector, reused unchanged through E21's panel artifact: "
            "unbuyable = locked_up | reopen | extreme_gap. The panel is "
            "E21_flow_lock_panel.csv.gz, sha256 recorded at build time in "
            "E21_flow_lock_panel.sha256 -- reusing the artifact keeps the "
            "estimator and inputs byte-identical to the record that "
            "motivated this experiment."),
        "component": COMPONENT,
        "sign": SIGN,
        "score": (
            "score = -1 * within-date percentile rank of inst_net_ratio. "
            "Single component, no weights, no fitting, no threshold "
            "search."),
        "mechanism": (
            "Queue supply: in a buy queue the institutional side typically "
            "SUPPLIES the queue rather than forming it, so heavy "
            "institutional net buying (as a share of traded value) marks a "
            "name where retail is not queuing, and relative institutional "
            "net selling accompanies the names about to lock. Declared from "
            "E21's falsification record before this measurement ran."),
        "provenance_of_direction": (
            "NOT an independent discovery. E21 preregistered this component "
            "at +1 and measured IC -0.08707 (t -60.22, +1 top-decile lift "
            "0.416x) in post-hoc diagnostics on this same discovery window. "
            "E21 recorded its directional claim FALSIFIED and refused to "
            "flip the sign mid-experiment; this preregistration is that "
            "flip, made where it belongs. In-sample formalisation only."),
        "discovery_window": [DISCOVERY_START, DISCOVERY_END],
        "oos_claim": (
            "NONE POSSIBLE, declared up front as in E20. Every historical "
            "holdout is spent and data ends 2026-08-18. The directional "
            "claim may be tested out of sample ONLY against %s "
            "(2026-08-19..2027-08-19), by a successor that names that "
            "reservation once the window accrues. This experiment does not "
            "touch it." % FORWARD_RESERVATION),
        "decontamination": {
            "impossible_session_move_pct": IMPOSSIBLE_SESSION_MOVE_PCT,
            "rule": (
                "drop BOTH legs of every per-name close-to-close transition "
                "with |return| > 100%, iterate to a fixed point, set forward "
                "targets that would read a dropped session to unknown (NaN), "
                "never False"),
            "constant_cited_from": "sirat/experiments/gates.py",
        },
        "measurement_base": (
            "rows where inst_net_ratio is measurable (flow present and "
            "total buy value > 0). Wider than E21's four-feature complete "
            "base, declared here so the difference cannot later be read as "
            "selection."),
        "already_locked_exclusion": (
            "DIAGNOSTIC, reported not gating, mirroring E21: IC and lift "
            "recomputed excluding names locked_up at t, so the record shows "
            "how much of the result is lock persistence."),
        "min_names_per_date": MIN_NAMES_PER_DATE,
        "permutations": PERMUTATIONS,
        "decay_horizons": list(DECAY_HORIZONS),
        "scope": "stages 1-2 only; terminal decision is ARCHIVE or REJECT",
        "universe_snapshot": UNIVERSE_SNAPSHOT,
    },

    "validation_gates": {
        "point_in_time": (
            "the component reads session t only; targets come from E21's "
            "calendar-aware forward state, re-verified by sampling on the "
            "cleaned panel"),
        "survivorship": (
            "universe snapshot %s restores and hash-verifies"
            % UNIVERSE_SNAPSHOT),
        "universe_valid": (
            "median measurable names per date in the discovery window >= %d"
            % MIN_NAMES_PER_DATE),
        "missing_data": (
            "inst_net_ratio measurable on >= 60% of panel price rows"),
        "price_sanity": (
            "after the preregistered decontamination, zero per-name session "
            "returns exceed 100%; the drop counts are reported"),
        "ic": (
            "mean per-date Spearman(score, next_unbuyable) >= 0.05 at the "
            "declared sign"),
        "rank_correlation": "t-statistic of the per-date IC series >= 3.0",
        "random_baseline": (
            "observed mean IC > 95th percentile of a within-date label "
            "permutation null, %d permutations, seed %d"
            % (PERMUTATIONS, SEED)),
        "factor_decay": (
            "top-decile (of the SIGNED score) unbuyable rate >= 2.0x the "
            "universe rate at h=1, retaining >= 30% of that excess at h=3"),
    },

    "expected_metrics": {
        "mean_ic": ">= 0.05 (E21 diagnostic implies ~ +0.087 at this sign)",
        "ic_t_stat": ">= 3.0",
        "null_percentile": "> 95.0",
        "decile_lift_h1": (
            ">= 2.0 -- GENUINELY UNKNOWN. The 0.416x on record is the +1 top "
            "decile; the -1 top decile has never been computed and this is "
            "the gate most likely to fail."),
        "lift_retention_h3": ">= 0.30",
        "excluding_already_locked": (
            "reported as diagnostic; no floor, mirroring E21"),
    },

    "rejection_thresholds": {
        "min_mean_ic": 0.05,
        "min_ic_t_stat": 3.0,
        "min_null_percentile": 95.0,
        "min_decile_lift_h1": 2.0,
        "min_lift_retention_h3": 0.30,
        "min_names_per_date": MIN_NAMES_PER_DATE,
        "min_median_names_discovery": float(MIN_NAMES_PER_DATE),
        "min_flow_coverage_pct": 60.0,
        "max_impossible_rows_after_clean": 0,
    },

    "seed": SEED,
}


HYPOTHESIS = (
    "Institutional net flow at session t predicts, cross-sectionally, that a "
    "Tehran-listed name is UNBUYABLE at session t+1 -- at the NEGATIVE sign: "
    "relative institutional net selling, not buying, accompanies queue "
    "formation, because the institutional side supplies a buy queue rather "
    "than forming it. This is a tradability prediction, not a return "
    "prediction, and it is the preregistered correction of the one "
    "directional claim E21 recorded as falsified."
)

EXPECTED_FAILURE = (
    "The corrected-sign top decile does not reach a 2.0x event-rate lift -- "
    "the component orders names (the IC is nearly assured given E21's "
    "diagnostic on this same window) but its extreme decile is not "
    "concentrated enough to gate a feasibility filter. In that case the "
    "record shows REJECT at Stage 2 on factor_decay, and the composite "
    "successor proceeds on indiv_power alone. The subtler failure is the "
    "E20 mechanism: lift that exists only among names already locked at t, "
    "which the preregistered exclusion diagnostic is written to expose."
)

ACCEPTANCE_CRITERIA = (
    "All four Stage 2 checks pass on the discovery window against the "
    "floors frozen here. Passing earns an ARCHIVE recording the corrected "
    "sign as a legitimate, preregistered claim available to any successor "
    "composite -- explicitly NOT an out-of-sample confirmation, which no "
    "spent-window experiment can provide, and NOT a promotion: scope ends "
    "at Stage 2, no execution claim is made, and the out-of-sample test "
    "belongs to %s when it accrues." % FORWARD_RESERVATION
)

NOTES = (
    "Successor (b) from E21's verdict, registered after successor (a) -- the "
    "impact model -- landed in backtesting/impact.py. The label E23 is "
    "deliberately skipped: it is attached to the sell-queue feasibility scan "
    "(E23_FEASIBILITY.md) that was rejected before registration and never "
    "issued an experiment id, and reusing it would blur a scan with a "
    "registered experiment. Reads E21_flow_lock_panel.csv.gz (sha256 on "
    "record); reads no database. Does not touch, re-score or reference "
    "Alpha Core (frozen at 90024d86d7a5c268); E19, E20, E21 and E22 remain "
    "adjudicated and nothing here can reopen them. The spent windows "
    "2023-03-01..2024-09-01 and 2024-09-02..2026-08-18 are not read, not "
    "even for context."
)


def main():
    print("E24 PREREGISTRATION")
    print("=" * 70)
    print("component : %s at sign %+d" % (COMPONENT, SIGN))
    print("discovery : %s .. %s" % (DISCOVERY_START, DISCOVERY_END))
    print("oos claim : none possible; forward test reserved to %s"
          % FORWARD_RESERVATION)
    print("seed      : %d" % SEED)
    print("scope     : stages 1-2; terminal decision ARCHIVE or REJECT")
    print()

    try:
        manager = Manager.register(
            experiment_id=EXPERIMENT_ID,
            hypothesis=HYPOTHESIS,
            strategy_version="inst-net-supply-v1",
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
