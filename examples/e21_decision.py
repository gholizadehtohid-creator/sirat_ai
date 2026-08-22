"""E21 -- record the decision. Cites the report, decides nothing on its own.

ARCHIVE, not PROMOTE, and the reason is in the preregistration rather than in
the numbers: E21's declared scope ends at Stage 2 because Stage 3 requires a
slippage figure and no impact model exists yet. Reporting that check as passing
would be reading absence as approval.

USAGE
    python examples/e21_decision.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from examples import e21_preregister as prereg                  # noqa: E402
from sirat.experiments.manager import Manager, GovernanceError  # noqa: E402


RATIONALE = (
    "ARCHIVE. Stage 1 and Stage 2 -- the experiment's entire declared scope -- "
    "both PASS, and the holdout held.\n"
    "\n"
    "MEASURED, discovery window 2014-01-01..2023-02-28, 2,128 dates, median "
    "425 names/date: mean per-date Spearman(score, next_unbuyable) = 0.13650 "
    "against a frozen floor of 0.05; t = 80.52 against a floor of 3.0; the "
    "observed mean sits at the 100th percentile of a 200-fold within-date "
    "label permutation null whose p95 is 0.00204; top-decile unbuyable rate "
    "10.650% against a universe rate of 4.057%, a lift of 2.625x against a "
    "floor of 2.0x, retaining 0.506 of its excess lift at h=3 against a floor "
    "of 0.30.\n"
    "\n"
    "HOLDOUT 2024-09-02..2026-08-18, spent once and never previously "
    "evaluated by any experiment in the ledger: mean IC 0.15280, t = 29.24, "
    "lift 2.049x. The sign held and the magnitude grew. E19's holdout IC "
    "flipped sign to -0.02449; this one did not.\n"
    "\n"
    "THREE DIAGNOSTICS, discovery window only, holdout not reopened:\n"
    "\n"
    "(1) NOT MERELY LOCK PERSISTENCE. A name locked at session t has "
    "high == low and therefore close == high, so the closed_at_high component "
    "risked making the whole result a restatement of 'locked today, locked "
    "tomorrow'. Excluding every name already locked at t -- 96.0% of the panel "
    "survives, base rate falls from 4.057% to 2.300% -- the composite still "
    "measures IC 0.08823 (t = 62.61) at a lift of 2.233x, and the two flow "
    "components alone still measure 0.05031 at a lift of 1.729x. The signal is "
    "not the tautology.\n"
    "\n"
    "(2) E20'S FAILURE MODE DOES NOT REPEAT. E20 killed Alpha Core because its "
    "information sat only where trading was impossible: IC +0.212 in the most "
    "illiquid quintile and -0.030 in the most liquid. Here the decile lift is "
    "almost flat across the liquidity spectrum -- 2.375x / 2.335x / 2.414x / "
    "2.417x / 2.237x from most illiquid to most liquid -- and IC stays "
    "positive and significant in the most liquid quintile at 0.110 (t = "
    "23.46). Predicting tradability is not predicting returns, and it does not "
    "inherit that failure.\n"
    "\n"
    "(3) ONE DECLARED SIGN WAS WRONG, AND IT IS NOT BEING CORRECTED HERE. "
    "inst_net_ratio was preregistered at +1 on the magnet-effect argument that "
    "institutional trading drives price toward the band. Measured alone it "
    "carries IC -0.08707 (t = -60.22) at a lift of 0.416x -- the opposite "
    "direction, strongly. Mechanically this is coherent: in a صف خرید the "
    "حقوقی side is typically supplying the queue rather than forming it. The "
    "composite passed DESPITE this component, not because of it. Flipping the "
    "sign now would be choosing a parameter after seeing the result, so the "
    "directional claim in the hypothesis is recorded as FALSIFIED and the "
    "corrected sign belongs in a successor preregistration, not in this "
    "record. The predictive content that survives is indiv_power (قدرت خریدار "
    "حقیقی), IC 0.16855 at a lift of 4.349x -- also a client_type flow "
    "variable, so the broad hypothesis holds while its specific direction "
    "did not.\n"
    "\n"
    "WHY NOT PROMOTE. Stage 3 is EXECUTION REALISM and one of its five "
    "required checks is slippage, which has been 0.0% in every result this "
    "programme has produced because no impact model exists. That check cannot "
    "be reported as passing without reading absence as approval, so E21 was "
    "registered with a scope ending at Stage 2 and it ends there. The "
    "provenance also records git_dirty=True and reproducible=False at "
    "registration time, which independently bars promotion.\n"
    "\n"
    "SUCCESSOR WORK, in order: (a) an impact model, RESEARCH_OPTIONS.md "
    "Option 2, without which no execution claim is measurable; (b) a "
    "successor experiment preregistering inst_net_ratio at -1 with the "
    "mechanism stated in advance; (c) only then, whether using this as a "
    "Stage 1 feasibility filter improves realised versus requested returns.\n"
    "\n"
    "This experiment does not touch, re-score or reference Alpha Core "
    "(90024d86d7a5c268). E19 and E20 remain adjudicated."
)


def main():
    manager = Manager.load(prereg.EXPERIMENT_ID)

    results = manager.stage_results()

    print("E21 DECISION")
    print("=" * 70)
    for row in results:
        print("  stage %d  %-28s %s"
              % (row["stage"], row["stage_name"], row["status"]))
    print()

    try:
        outcome = manager.decide(
            "ARCHIVE", RATIONALE, by="SIRAT-AI Research")
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1

    print("decision           : %s" % outcome["decision"])
    print("rejected at stage  : %s" % outcome["rejected_at_stage_name"])
    print("ledger entry hash  : %s" % outcome["ledger_entry_hash"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
