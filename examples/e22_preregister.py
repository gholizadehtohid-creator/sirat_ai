"""E22 -- DOES THE FLOW FILTER SURVIVE EXECUTION? Preregistration only.

    QUESTION    if you refuse to buy the names E21's score ranks most likely to
                lock, do you actually get filled more often -- and what does
                that refusal cost you in breadth, liquidity and impact?
    NOT         a return test. No basket here is chosen for its expected
                return; the reference basket is drawn AT RANDOM on purpose.
    NEW         Stage 3, EXECUTION REALISM. It has never run in this programme,
                because until `backtesting/impact.py` landed there was no
                slippage figure that could be reported honestly.

TWO ADMISSIONS, MADE BEFORE ANY NUMBER EXISTS
----------------------------------------------
1. **E22 CANNOT MAKE AN OUT-OF-SAMPLE CLAIM.** Every window is spent. E19 took
   2023-03-01..2024-09-01 and E21 took 2024-09-02..2026-08-18, and the data
   ends 2026-08-18 so there is no forward window to wait for. E20 was in the
   same position and said so; this says so too. E22 can establish that the
   filter FAILS an execution test. It cannot establish that it generalises.

2. **STAGE 2 HERE IS A RE-MEASUREMENT, NOT EVIDENCE.** E21 already measured
   this score on this window and archived the result. Running Stage 2 again is
   required by the pipeline's ordering, not by the science, and its verdict
   carries no independent information. It is labelled as such in the record so
   that nobody later counts it as a second confirmation.

WHY THE PRIMARY SCORE IS E21'S, WRONG SIGN AND ALL
---------------------------------------------------
E21's diagnostics showed `inst_net_ratio` was preregistered at +1 and measures
at -0.087 -- the opposite direction. The tempting move is to fix it here.

That temptation is the whole reason not to. A composite corrected after seeing
which component misbehaved is a composite fitted to the discovery window, and
on a market with no unspent holdout there is nothing left that could catch it.

So the PRIMARY score is E21's frozen composite, unchanged, wrong sign included.
The corrected variant runs as a SECONDARY comparison and is declared
non-independent in advance -- it can inform the next preregistration and it can
never be reported as a result.

THE EXECUTION TEST, AND WHY THE BASKET IS RANDOM
--------------------------------------------------
A filter's value is measured against what you would have done without it. If
the reference basket came from any alpha model, the measurement would blend the
filter's execution effect with that model's selection effect, and the two could
not be separated afterwards.

So the reference basket is drawn UNIFORMLY AT RANDOM, with a frozen seed, from
the names that clear the participation cap on that date. A random basket has no
expected edge by construction, so anything the filter changes is execution and
nothing else. It also means this experiment structurally cannot discover an
alpha, which is the property that made E21 safe to run first.

    baseline   draw 10 names at random from the eligible set
    filtered   draw 10 names at random from the eligible set MINUS the names
               in the top decile of the E21 score

Both are then bought at t+1. A name that is unbuyable at t+1 is a failed fill.

USAGE
    python examples/e22_preregister.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config                                                   # noqa: E402
from backtesting import impact                                  # noqa: E402
from sirat.experiments.manager import Manager, GovernanceError  # noqa: E402


EXPERIMENT_ID = "E22-flow-filter-execution"

UNIVERSE_SNAPSHOT = "u20260819-668658bb"

SEED = 20260823

# The whole panel. There is no train/test split because there is nothing left
# to split -- see admission 1. Measuring on everything and saying so is more
# honest than carving an arbitrary boundary and calling the second half a test.
WINDOW_START = "2014-01-01"
WINDOW_END = "2026-08-18"

BASKET = 10

DRAWS_PER_DATE = 20            # independent random baskets per session

ACCOUNT_EQUITY = 1_000_000_000
POSITION_VALUE = 100_000_000

MAX_PARTICIPATION_PCT = config.MAX_ADV_PARTICIPATION_PCT

FILTER_DECILE = 0.90           # exclude the top 10% by E21 score

MIN_ELIGIBLE_NAMES = 50


PREREGISTRATION = {
    "parameters": {
        "primary_score": (
            "E21's frozen composite, UNCHANGED: equal-weight mean of "
            "within-date percentile ranks of inst_net_ratio(+1), "
            "indiv_power(+1), closed_at_high(+1), vol_ratio(+1). The +1 on "
            "inst_net_ratio is known to be the wrong sign and is kept anyway "
            "-- correcting it here would fit the composite to a window with no "
            "holdout left to catch it."),
        "secondary_score": (
            "the same composite with inst_net_ratio at -1. DECLARED "
            "NON-INDEPENDENT: the sign was chosen after seeing E21's "
            "diagnostics. It may inform the next preregistration and may never "
            "be reported as a result."),
        "reference_basket": (
            "%d names drawn uniformly at random from the eligible set, %d "
            "independent draws per session, seed %d. Random by design: a "
            "basket with no expected edge isolates the filter's execution "
            "effect from any selection effect."
            % (BASKET, DRAWS_PER_DATE, SEED)),
        "filtered_basket": (
            "the same draw from the eligible set minus names above the %.0fth "
            "percentile of the primary score" % (100 * FILTER_DECILE)),
        "eligibility": (
            "position %d rials is <= %.1f%% of the name's trailing 20-session "
            "median traded value, and all four score components are present"
            % (POSITION_VALUE, MAX_PARTICIPATION_PCT)),
        "fill_rule": (
            "bought at t+1. E14's `unbuyable` at t+1 is a failed fill, not a "
            "fill at a worse price."),
        "impact_model": (
            "backtesting/impact.py, square-root law, IMPORTED prefactor "
            "c_eff=%.2f. Swept over %s. A limit-locked name returns no number "
            "at all rather than a cost."
            % (impact.PREFACTOR_EFFECTIVE,
               list(impact.SENSITIVITY_PREFACTORS))),
        "window": [WINDOW_START, WINDOW_END],
        "out_of_sample": (
            "NONE AVAILABLE. E19 spent 2023-03-01..2024-09-01 and E21 spent "
            "2024-09-02..2026-08-18; data ends 2026-08-18. E22 can establish a "
            "negative result and can never establish generalisation."),
        "stage_2_status": (
            "RE-MEASUREMENT of E21's archived result on E21's window. Required "
            "by the pipeline ordering, carries no independent information, and "
            "must not be counted as a second confirmation."),
        "scope": "stages 1-3; terminal decision ARCHIVE or REJECT",
        "universe_snapshot": UNIVERSE_SNAPSHOT,
    },

    "validation_gates": {
        "point_in_time": (
            "every score component and the eligibility test read session t or "
            "earlier; the fill outcome is session t+1 on the market calendar"),
        "survivorship": "universe snapshot %s restores" % UNIVERSE_SNAPSHOT,
        "universe_valid": (
            "median eligible names per date >= %d" % MIN_ELIGIBLE_NAMES),
        "missing_data": "score present for >= 60% of price rows",

        "ic": "as E21. Re-measurement, not evidence.",
        "rank_correlation": "as E21. Re-measurement, not evidence.",
        "random_baseline": "as E21. Re-measurement, not evidence.",
        "factor_decay": "as E21. Re-measurement, not evidence.",

        "queue_effects": (
            "THE PRIMARY TEST. The filtered basket's failed-fill rate is <= "
            "70% of the baseline's -- a >= 30% reduction in failed fills."),
        "slippage": (
            "impact is COMPUTED, not assumed, for >= 95% of filled positions, "
            "AND the filter does not raise mean impact cost by more than "
            "0.05pp. A run that cannot compute impact FAILS rather than "
            "reporting zero."),
        "participation_limits": (
            "the filtered eligible set still holds >= %d names on the median "
            "date at the %.1f%% cap" % (MIN_ELIGIBLE_NAMES,
                                        MAX_PARTICIPATION_PCT)),
        "liquidity": (
            "median trailing ADV of admitted names >= median ADV of excluded "
            "names. The filter must not buy fills by moving into thinner "
            "stock -- that is E20's failure mode wearing a different hat."),
        "transaction_costs": (
            "the filter does not raise the round-trip cost of a filled "
            "position, commission plus impact, by more than 0.05pp"),
    },

    "expected_metrics": {
        "baseline_failed_fill_pct": "measured, no threshold",
        "filtered_failed_fill_pct": "<= 70% of baseline",
        "fill_improvement_pp": "measured",
        "impact_computed_pct": ">= 95.0",
        "impact_delta_pp": "<= 0.05",
        "admitted_vs_excluded_adv_ratio": ">= 1.0",
        "median_eligible_after_filter": ">= %d" % MIN_ELIGIBLE_NAMES,
    },

    "rejection_thresholds": {
        "max_filtered_over_baseline_fill_failure": 0.70,
        "min_impact_computed_pct": 95.0,
        "max_impact_delta_pp": 0.05,
        "max_round_trip_delta_pp": 0.05,
        "min_admitted_excluded_adv_ratio": 1.0,
        "min_eligible_names": MIN_ELIGIBLE_NAMES,
        "min_median_names_discovery": float(MIN_ELIGIBLE_NAMES),
        "min_flow_coverage_pct": 60.0,
        # Carried from E21 so Stage 2 has thresholds to read. They are the same
        # numbers, because re-measuring against different ones would be a
        # different test wearing E21's name.
        "min_mean_ic": 0.05,
        "min_ic_t_stat": 3.0,
        "min_null_percentile": 95.0,
        "min_decile_lift_h1": 2.0,
        "min_lift_retention_h3": 0.30,
    },

    "seed": SEED,
}


HYPOTHESIS = (
    "Refusing to buy the names E21's composite ranks in its top decile raises "
    "the fill rate of an otherwise randomly chosen basket by at least 30%, "
    "without moving the basket into thinner stock, without raising the "
    "round-trip cost of a filled position once square-root impact is charged, "
    "and without shrinking the eligible universe below 50 names on the median "
    "session."
)

EXPECTED_FAILURE = (
    "Three distinct ways this fails, and the second is the one I expect.\n"
    "(a) The filter removes locks that were never reachable anyway -- the "
    "baseline failed-fill rate is already low among names that clear a 5% "
    "participation cap, so there is nothing to improve and the ratio sits near "
    "1.0.\n"
    "(b) The filter works and costs too much: fills improve, but the admitted "
    "set is systematically thinner than the excluded set, which is E20's "
    "failure mode in a new costume -- buying tradability by moving into stock "
    "that cannot absorb the position.\n"
    "(c) Impact cannot be computed for enough positions, because trailing "
    "volatility or traded value is missing on the names that matter. That is "
    "a FAIL, not a zero, and it is written that way on purpose."
)

ACCEPTANCE_CRITERIA = (
    "All five Stage 3 checks pass against the thresholds frozen here. Because "
    "no out-of-sample window exists, passing earns an ARCHIVE and a "
    "recommendation to collect forward data before any promotion -- it does "
    "not earn a promotion, and no result from E22 may be cited as evidence "
    "that the filter generalises."
)

NOTES = (
    "Successor to E21 (prereg 2ce1d49dd90ebd46, ledger f5d3af47da67b8ac). "
    "First experiment in this programme to reach Stage 3, unblocked by "
    "backtesting/impact.py (IMPACT_MODEL.md). Does not touch, re-score or "
    "reference Alpha Core (90024d86d7a5c268); E19 and E20 remain adjudicated. "
    "Uses E21's composite unchanged -- including the component whose "
    "preregistered sign E21 recorded as falsified."
)


def main():
    print("E22 PREREGISTRATION")
    print("=" * 70)
    print("window        : %s .. %s" % (WINDOW_START, WINDOW_END))
    print("out of sample : NONE -- every window is spent, declared up front")
    print("primary score : E21's frozen composite, wrong sign included")
    print("basket        : %d names, %d random draws/session, seed %d"
          % (BASKET, DRAWS_PER_DATE, SEED))
    print("scope         : stages 1-3; ARCHIVE or REJECT")
    print()

    try:
        manager = Manager.register(
            experiment_id=EXPERIMENT_ID,
            hypothesis=HYPOTHESIS,
            strategy_version="e21-composite-frozen + impact-v1",
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

    prov = manager.record()["provenance"]

    print("registered.")
    print("  prereg hash   : %s" % prov.get("prereg_hash"))
    print("  git commit    : %s" % prov.get("git_commit"))
    print("  git dirty     : %s" % prov.get("git_dirty"))
    print("  reproducible  : %s" % prov.get("reproducible"))
    print("  next stage    : %s" % manager.next_stage())
    print()
    print("Thresholds frozen. Nothing computed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
