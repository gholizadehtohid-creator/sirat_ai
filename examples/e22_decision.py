"""E22 -- record the decision. REJECT at Stage 3.

The rejection is sound on `queue_effects` alone, which depends on no cost model
and no volatility estimate. The two cost checks also failed, and those two are
NOT attributable to the filter -- they come from contaminated data. Both facts
are recorded, because reporting only the first would overstate the case against
the filter and reporting only the second would rescue it.

USAGE
    python examples/e22_decision.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from examples import e22_preregister as prereg                  # noqa: E402
from sirat.experiments.manager import Manager, GovernanceError  # noqa: E402


RATIONALE = (
    "REJECT at Stage 3 (EXECUTION REALISM), the first Stage 3 ever run in "
    "this programme. Three of five checks failed: queue_effects, slippage, "
    "transaction_costs.\n"
    "\n"
    "THE REJECTION IS SOUND ON queue_effects ALONE, and that check depends on "
    "no cost model, no volatility estimate and no imported constant -- only on "
    "whether an order filled. Over 112,400 random baskets across 2,810 "
    "sessions, 562,000 positions per variant: the baseline failed to fill "
    "3.6155% of positions and the filtered basket 3.0907%, a ratio of 0.8549 "
    "against a preregistered ceiling of 0.70. The filter reduces failed fills "
    "by 14.5% where 30% was declared necessary. It works, and it works less "
    "than half as well as required.\n"
    "\n"
    "THE ECONOMICS FAIL INDEPENDENTLY OF EVERY COST MODEL. Converting a failed "
    "fill into a fill happens for 0.5248pp of positions while the filter adds "
    "0.1504pp to every filled position, so break-even requires each converted "
    "slot to be worth 28.66pp. The reference basket is drawn uniformly at "
    "random and therefore has an expected excess over cash of zero by "
    "construction. A cost with no offsetting benefit is not a close call.\n"
    "\n"
    "THE TWO COST CHECKS FAILED FOR A REASON THAT IS NOT THE FILTER, AND THIS "
    "IS RECORDED SO THE FILTER IS NOT BLAMED FOR IT. Measured per session, the "
    "filter is CHEAPER on the typical date: median per-date impact on filled "
    "positions is 0.0824% baseline against 0.0697% filtered, p95 0.1803% "
    "against 0.1422%, and only 26.1% of the 2,809 sessions are worse under the "
    "filter (median delta -0.0011pp, mean -0.0064pp). The aggregate reversed "
    "because of five consecutive sessions in June 2022 reporting mean impact "
    "between 56% and 76% of price across the whole 550-name cross-section. "
    "The worst 1% of dates account for -254.6% of the total delta -- they do "
    "not merely dominate the mean, they invert its sign.\n"
    "\n"
    "ROOT CAUSE, IDENTIFIED: eleven rows in a two-month sample carry "
    "impossible returns, the largest being +19,976,600% on 2022-05-22, which "
    "means a prior close of 1 rial. These are new listings, post-suspension "
    "reopenings and unadjusted capital increases. One such row sits inside a "
    "20-session trailing volatility window for twenty sessions, and a single "
    "contaminated name dominates the mean impact of every basket drawn on "
    "those dates. It also explains the panel-wide signature: sigma mean 8.79% "
    "against a median of 2.26%.\n"
    "\n"
    "DEFECT CLASSIFICATION: **data and tooling, not strategy.**\n"
    "  data     - price_history carries unadjusted and placeholder prices that "
    "produce returns no market ever printed.\n"
    "  tooling  - backtesting/impact.py accepted a daily volatility of "
    "several hundred percent because it validates only 'positive and finite'. "
    "It was written two days ago and this is its first real use.\n"
    "  gate     - Stage 1 passed this panel. Its four checks cover forward "
    "alignment, universe restoration, cross-section width and coverage; not "
    "one of them looks at whether a return is physically possible. E14's "
    "`annotate` has flagged exactly these bars as `extreme_gap` (|ret| > 12%) "
    "since it was written, so the platform already held the knowledge the "
    "volatility estimator failed to use.\n"
    "\n"
    "WHAT IS NOT BEING DONE. The panel is not being cleaned and Stage 3 is not "
    "being re-run under this experiment id. The thresholds were frozen before "
    "any number existed and the measurement was exactly what was declared; "
    "repairing the data after seeing the verdict and re-measuring is the "
    "textbook forbidden move, and it would be forbidden even though the repair "
    "is obviously correct. E22 rejects. A successor experiment may ask the "
    "cost question again with a volatility sanity bound preregistered.\n"
    "\n"
    "WHAT E22 SETTLES: the filter does not pay for itself in front of a "
    "basket with no expected edge. WHAT IT DOES NOT SETTLE: whether it pays in "
    "front of a basket that has one. That needs a validated return signal, and "
    "this programme does not have one -- E19 and E20 established that Alpha "
    "Core is not it.\n"
    "\n"
    "No out-of-sample claim was available and none is made; that limitation "
    "was declared at registration, not discovered here. Alpha Core is "
    "untouched at 90024d86d7a5c268."
)


def main():
    manager = Manager.load(prereg.EXPERIMENT_ID)

    print("E22 DECISION")
    print("=" * 70)
    for row in manager.stage_results():
        print("  stage %d  %-28s %s"
              % (row["stage"], row["stage_name"], row["status"]))
    print()

    try:
        outcome = manager.decide("REJECT", RATIONALE, by="SIRAT-AI Research")
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1

    print("decision           : %s" % outcome["decision"])
    print("rejected at stage  : %d (%s)"
          % (outcome["rejected_at_stage"], outcome["rejected_at_stage_name"]))
    print("cheap rejection    : %s" % outcome["cheap_rejection"])
    print("ledger entry hash  : %s" % outcome["ledger_entry_hash"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
