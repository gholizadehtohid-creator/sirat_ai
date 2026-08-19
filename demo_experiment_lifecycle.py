"""An experiment's complete life, including the shortcuts the manager refuses.

WHAT THIS DEMONSTRATES
----------------------
A hypothesis that would have looked plausible in Phase 1 -- "high-turnover
short-horizon momentum on the thin end of the universe" -- registered properly
and then killed at Stage 1 in the time it takes to check its data.

Every refusal shown here is a thing that actually happened in Phase 1 and was
caught late or not at all. The point is not that the manager says no; it is
WHERE it says no. This idea dies before anyone computes a return.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sirat.experiments import GovernanceError, Manager, kpi, stages


def show(title):
    print("\n" + "-" * 74)
    print(title)
    print("-" * 74)


def refused(action, call):
    try:
        call()
    except (GovernanceError, stages.StageIncomplete) as exc:
        print("  REFUSED  %s" % action)
        print("           %s" % " ".join(str(exc).split()))
        return
    print("  !! ALLOWED (should not be): %s" % action)


def main():

    handle, db = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(db)

    print("=" * 74)
    print("EXPERIMENT LIFECYCLE DEMONSTRATION")
    print("=" * 74)

    # ---- 0. what the manager refuses before registration ------------------
    show("0. Before registration")

    refused("loading an experiment that was never registered",
            lambda: Manager("EXP-GHOST", path=db))

    refused("registering without expected failure conditions",
            lambda: Manager.register(
                experiment_id="EXP-BAD", hypothesis="it goes up",
                strategy_version="v1", researcher="demo", notes="none",
                expected_failure=[],
                acceptance_criteria={"cagr": "> 0"},
                preregistration={"parameters": {"n": 10},
                                 "validation_gates": {"g": 1},
                                 "expected_metrics": {"ic": 0.03},
                                 "rejection_thresholds": {"ic": 0.02},
                                 "seed": 1},
                path=db))

    refused("registering without an explicit seed",
            lambda: Manager.register(
                experiment_id="EXP-BAD2", hypothesis="h",
                strategy_version="v1", researcher="demo", notes="n",
                expected_failure=["ic <= 0"],
                acceptance_criteria={"ic": "> 0.02"},
                preregistration={"parameters": {"n": 10},
                                 "validation_gates": {"ic": 0.02},
                                 "expected_metrics": {"ic": 0.03},
                                 "rejection_thresholds": {"ic": 0.02},
                                 "seed": None},
                path=db))

    # ---- 1. a proper registration -----------------------------------------
    show("1. Registration + preregistration (frozen together)")

    experiment_id = "EXP-2026-001-THIN-MOMENTUM"

    manager = Manager.register(
        experiment_id=experiment_id,
        hypothesis="5-session momentum ranks forward returns among the least "
                   "liquid quintile, where attention is scarcest.",
        strategy_version="thin-momentum-v1",
        researcher="demo",
        notes="Economic mechanism: slow information diffusion in "
              "under-covered names. Reviewed by: second reader.",
        expected_failure=[
            "IC at h=5 indistinguishable from zero",
            "required turnover exceeds measured market capacity",
            "the illiquid quintile cannot absorb a position",
        ],
        acceptance_criteria={
            "ic_h5": "> 0.02",
            "net_edge_per_period_pct": "> 0",
            "queue_exposure_ratio": "< 1.5",
        },
        preregistration={
            "parameters": {"basket": 10, "horizon": 5,
                           "universe": "bottom ADV quintile"},
            "validation_gates": {"ic_floor": 0.02, "net_edge_floor": 0.0,
                                 "queue_ratio_ceiling": 1.5},
            "expected_metrics": {"ic_h5": 0.03, "turnover_per_year": 48},
            "rejection_thresholds": {"ic_h5": 0.02, "net_edge_pct": 0.0},
            "seed": 20260818,
        },
        path=db)

    record = manager.record()

    print("  registered   %s" % manager.experiment_id)
    print("  seed         %d  (from the frozen record, not a default)"
          % manager.seed())
    print("  thresholds   %s" % manager.thresholds())
    print("  git commit   %s" % record["provenance"]["git_commit"])
    print("  code hash    %s" % record["provenance"]["code_hash"][:32])
    print("  dataset      %s" % record["provenance"]["dataset_snapshot"])
    print("  reproducible %s" % record["provenance"]["reproducible"])

    # ---- 2. refusals that protect the record ------------------------------
    show("2. What the manager refuses now that the record is frozen")

    refused("skipping straight to robustness",
            lambda: manager.run_stage(stages.ROBUSTNESS,
                                      {"walk_forward": True,
                                       "out_of_sample": True}))

    refused("deciding before any stage has run",
            lambda: manager.decide("ARCHIVE", "changed my mind", "demo"))

    # ---- 3. Stage 1 — and this is where it dies ---------------------------
    show("3. Stage 1 — DATA INTEGRITY")

    refused("reporting a verdict without reporting every check",
            lambda: manager.run_stage(stages.DATA_INTEGRITY,
                                      {"point_in_time": True,
                                       "survivorship": True}))

    outcome = manager.run_stage(
        stages.DATA_INTEGRITY,
        metrics={
            "point_in_time": True,
            "survivorship": False,
            "universe_valid": True,
            "missing_data": False,
        },
        detail={
            "note": "The bottom ADV quintile is where delisting concentrates, "
                    "and no historical universe snapshot covers the research "
                    "window.",
            "stale_bar_share_pct": 22.29,
            "universe_snapshots_covering_window": 0,
        })

    print("  stage 1      %s" % outcome["status"])
    print("  failed       %s" % ", ".join(outcome["failed"]))

    refused("continuing to Stage 2 after Stage 1 failed",
            lambda: manager.run_stage(stages.ECONOMIC_SIGNAL,
                                      {"ic": True, "rank_correlation": True,
                                       "random_baseline": True,
                                       "factor_decay": True}))

    # ---- 4. decision -------------------------------------------------------
    show("4. Decision")

    decision = manager.decide(
        "REJECT",
        rationale="Rejected at Stage 1. The hypothesis targets the illiquid "
                  "quintile, which is exactly where survivorship bias and "
                  "stale pricing are worst, and no point-in-time universe "
                  "exists to correct for it. No return was computed.",
        by="demo")

    print("  decision            %s" % decision["decision"])
    print("  rejected at stage   %s (%s)"
          % (decision["rejected_at_stage"],
             decision["rejected_at_stage_name"]))
    print("  cheap rejection     %s" % decision["cheap_rejection"])

    refused("changing a decision after the fact",
            lambda: manager.decide("PROMOTE", "reconsidered", "demo"))

    # ---- 5. the KPI --------------------------------------------------------
    show("5. Platform KPI — at which stage are ideas rejected?")

    got = kpi(path=db)

    print("  rejections          %d" % got["rejections"])
    print("  at stages 1-3       %d" % got["cheap"])
    print("  rate                %.0f%%  (target %.0f%%)"
          % (got["rate"] * 100, got["target"] * 100))
    print("  meets target        %s" % got["meets_target"])

    # ---- 6. what it cost ---------------------------------------------------
    show("6. What this experiment cost")

    print("  returns computed    0")
    print("  backtests run       0")
    print("  portfolios built    0")
    print("  stages consumed     1 of 6")
    print()
    print("  In Phase 1 the same idea would have reached a portfolio result")
    print("  and been argued about. Here it produced a recorded, hashed,")
    print("  reviewable rejection before anyone opened a price series.")

    os.unlink(db)

    print("\n" + "=" * 74)
    print("A rejection at Stage 1 is the product working.")
    print("=" * 74)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
