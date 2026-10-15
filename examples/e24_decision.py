"""E24 -- record the decision. The stages already decided; this writes it down.

Stage 2 failed factor_decay against the frozen floors, so the decision is
REJECT. This file exists so the ledger entry carries the numbers and the
reasoning rather than a bare verdict, exactly as E19-E22 did.

USAGE
    python examples/e24_decision.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from examples import e24_preregister as prereg                  # noqa: E402
from sirat.experiments.manager import Manager, GovernanceError  # noqa: E402


RATIONALE = """REJECT at Stage 2 (ECONOMIC SIGNAL VALIDATION), failing factor_decay. Preregistration 87b995fae3e862c72210361c6205bcdef44d9c567ed36dd613f5d5767802b512, frozen before any number below existed.

WHAT PASSED, measured on the discovery window 2014-01-01..2023-02-28 (2,137 scored dates, 930,054 rows, floor 100 names/date): at the corrected sign of -1, mean per-date Spearman IC 0.08193 against a floor of 0.05; t = 56.28 against a floor of 3.0; the observed mean sits at the 100th percentile of a 200-fold within-date label permutation null whose p95 is 0.00161. The direction E21 recorded as FALSIFIED for its +1 declaration is therefore confirmed at -1 under a preregistration that declared it in advance -- but this confirmation was near-assured from E21's own diagnostic on the same window and is deliberately the least-weighted part of this record. It is an in-sample formalisation, declared as such at registration, with no out-of-sample claim available: every historical holdout is spent and the forward test belongs to R-FORWARD-2027 when it accrues.

WHAT FAILED, and it is the part that was genuinely unmeasured before this run: decay. The top decile of the signed score carries a 2.407x unbuyable-rate lift at h=1 (9.525% vs 3.957%), clearing the 2.0x floor -- but by h=3 the lift is 1.290x and by h=5 it is 1.100x, a retention of 0.206 against a preregistered floor of 0.30. Institutional net selling is a ONE-SESSION warning: the excess concentration is nearly gone two sessions later. A feasibility filter must survive the lag between computing a signal after the close and acting on it, and this component does not.

THE PREREGISTERED DIAGNOSTIC (reported, not gating): excluding names already locked_up at t retains 96.2% of rows and still measures IC 0.05374 (t 43.47) with a top-decile lift of 1.994x. About seven-tenths of the h=1 excess lift survives the exclusion, so the signal is not merely lock persistence -- but its usable concentration sits at the 2x line even before decay is charged.

DECONTAMINATION, applied exactly as preregistered and worth recording for successors: the both-legs rule on |return| > 100% transitions dropped 104,188 rows (5.50%) across 323 names and 3,040 dates over 1,448 iterations. Around an irreconcilable price-level break (an unadjusted rebasing), the iteration walks outward and consumes real rows on both sides of the break equally -- blunter than identifying the fake side would be. The rule is not being refined mid-experiment and the stages are not being re-run; a successor should preregister a placeholder-level test instead. Stage 1 passed on the cleaned panel: forward alignment 100% of 1,996 resolved samples, universe snapshot u20260819-668658bb restored, median 419 names/date, coverage 78.53%, zero impossible sessions remaining with 6,735 legitimate >12% reopenings retained.

WHAT THIS SETTLES: inst_net_ratio at -1 is real, correctly-signed, highly significant ORDERING information with a one-session horizon, and it is now on the record the legitimate way. Any future composite preregistration may cite this entry for the -1 sign. WHAT IT DOES NOT SETTLE OR TOUCH: E21's composite result stands (it passed DESPITE this component at the wrong sign); indiv_power remains the strongest carrier (E21: IC 0.16855, lift 4.349x); E19, E20 and E22 remain adjudicated; Alpha Core is untouched at 90024d86d7a5c268. No window was spent: the discovery window was reused as declared and R-FORWARD-2027 remains unclaimed."""


def main():
    manager = Manager.load(prereg.EXPERIMENT_ID)

    try:
        decision = manager.decide(
            "REJECT",
            rationale=RATIONALE,
            by="SIRAT-AI Research",
        )
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1

    print("E24 DECISION RECORDED")
    print("=" * 70)
    print("decision  : %s" % decision.get("decision", "REJECT"))

    record = manager.record()

    ledger = record.get("ledger") or {}

    if isinstance(ledger, dict):
        print("ledger    : %s" % (ledger.get("entry_hash") or ledger))

    print()
    print("The experiment is closed. A new question needs a new registration.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
