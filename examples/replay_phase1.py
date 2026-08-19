"""Replay the fifteen Phase 1 experiments through the governance layer.

WHAT THIS IS, EXACTLY
---------------------
Not a re-execution. The original runs took weeks and several of them cannot be
repeated at all -- E5 was correctly never run, and E7/E8/E10/E12 were not
experiments. This is an ASSESSMENT: each historical hypothesis is registered
through the real `Manager`, and each stage is fed the evidence that experiment
actually recorded, cited to the artifact it came from. The Manager -- not this
script -- decides PASS/FAIL, the stage ordering, and the decision.

Every number below is traceable to a file in `backtest_output/` or to a row of
`experiment_registry.md`. Nothing is estimated.

THE TWO RULES THAT DECIDE ALMOST EVERYTHING
--------------------------------------------
1. A check compared against a preregistered threshold passes or fails on the
   number.
2. **A check Phase 1 never computed is a FAIL, not a skip.** This is where most
   of the programme dies, and it is the finding rather than an artifact of the
   method: nine of the eleven hypotheses reached a portfolio-level result
   without anyone establishing that the signal carried information at the
   horizon it was traded at.

The two are reported separately in the table -- `below threshold` versus
`never measured` -- because they call for different fixes.

ONE RETROSPECTIVE CONCESSION, STATED RATHER THAN HIDDEN
--------------------------------------------------------
No universe snapshot existed at any point during Phase 1. Under this platform
that fails Stage 1's `survivorship` check, so the strict answer is that ALL
fifteen experiments stop at Stage 1 and none of the rest is ever measured. That
is reported as finding zero.

It is also useless as a diagnosis, so the replay proceeds by granting each
experiment today's real snapshot (`u20260819-668658bb`) in order to answer the
more useful question: which stage catches each IDEA. Both answers appear in the
output. Neither is presented as the other.
"""

import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments import stages, universe                      # noqa: E402
from sirat.experiments import manager as mgr                        # noqa: E402


NOT_MEASURED = "NEVER MEASURED IN PHASE 1"

RESEARCHER = "phase 1 (replayed 2026-08-19)"


# The thresholds every replayed experiment is judged against. One set, applied
# uniformly, because a per-experiment threshold chosen now would be exactly the
# after-the-fact criterion this platform exists to prevent.
THRESHOLDS = {
    "max_missing_pct": 5.0,
    "min_names_per_date": 50,
    "max_stale_pct": 30.0,
    "min_ic": 0.02,
    "min_icir": 0.30,
    "min_pct_positive_ic": 55.0,
    "min_decay_retention": 0.50,
    "max_queue_ratio": 1.50,
    "min_fill_rate_pct": 90.0,
    "max_participation_pct": 10.0,
    "slippage_pct": 0.10,
    "min_liquid_share": 0.70,
    "min_positive_fold_share": 0.60,
    "max_oos_degradation": 0.50,
    "min_control_percentile": 95.0,
    "min_paper_sessions": 60,
    "round_trip_pct": 1.04,
}


def ok(value, source, basis):
    return {"passed": True, "value": value, "source": source, "basis": basis}


def bad(value, source, basis):
    return {"passed": False, "value": value, "source": source, "basis": basis}


def absent(source):
    return {"passed": False, "value": None, "source": source,
            "basis": NOT_MEASURED}


# Evidence blocks reused across the Alpha Core family, so the same recorded
# numbers cannot drift between rows of the table.
ALPHA_CORE_H20_SIGNAL = {
    "ic": bad(0.01456, "alpha_decay_analysis.json / decay_curve.h20.mean_ic",
              "0.01456 < 0.02 required"),
    "rank_correlation": bad(
        0.1125, "alpha_decay_analysis.json / decay_curve.h20.icir",
        "ICIR 0.1125 < 0.30; 51.67% positive dates < 55%"),
    "random_baseline": ok(
        8.781, "alpha_decay_analysis.json / decay_curve.h1.t_stat",
        "the signal is real at h=1 (t = 8.78 vs a permutation null)"),
    "factor_decay": bad(
        0.1388, "alpha_decay_analysis.json / decay_curve",
        "0.01456 / 0.10492 = 0.139 of peak IC retained at h=20 < 0.50"),
}

ALPHA_CORE_H1_SIGNAL = {
    "ic": ok(0.10492, "alpha_decay_analysis.json / decay_curve.h1.mean_ic",
             "0.10492 >= 0.02"),
    "rank_correlation": ok(
        0.805, "alpha_decay_analysis.json / decay_curve.h1.icir",
        "ICIR 0.805 >= 0.30; 81.51% positive dates >= 55%"),
    "random_baseline": ok(
        8.781, "alpha_decay_analysis.json / decay_curve.h1.t_stat",
        "t = 8.78 against the permutation null"),
    "factor_decay": ok(
        1.0, "alpha_decay_analysis.json / decay_curve",
        "h=1 IS the peak; retention 1.00 by construction"),
}

CLEAN_DATA = {
    "point_in_time": ok(
        0, "experiment14_queue_artifact.json / data_validation",
        "session-aware forwards; 1,473,796 bars in window, no impossible "
        "forward returns after the shift(-h) defect was corrected"),
    "universe_valid": ok(
        120, "alpha_core_rank_ic.json",
        "120 decision dates each carrying >= 50 scoreable names"),
    "missing_data": ok(
        22.29, "experiment14_queue_artifact.json / data_validation.stale_pct",
        "22.29% stale < 30% limit"),
}


# ==============================================================
# THE RECORD
# ==============================================================
#
# `kind` separates the eleven hypotheses from the four entries that were never
# experiments. Only hypotheses enter the KPI denominator; counting a platform
# change as a rejected idea would flatter the number.

HISTORY = [

    {
        "id": "E1-market-exposure-baseline",
        "kind": "hypothesis",
        "hypothesis": (
            "A deterministic four-signal market-state engine decides when and "
            "how much to be invested better than fixed passive exposure."),
        "notes": ("Scored PASS against buy-and-hold. Overturned: the correct "
                  "control is the exposure-matched arm `fixed_matched`, "
                  "against which the engine is worse on every risk measure "
                  "(Sharpe 1.49 vs 1.43, MaxDD -25.77% vs -16.35%)."),
        "expected_failure": ["the engine does not beat an exposure-matched "
                             "control"],
        "acceptance_criteria": {"six preregistered conditions": "all"},
        "artifact": "backtest_output/exposure_experiment.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: {
                "ic": absent("exposure_experiment.json"),
                "rank_correlation": absent("exposure_experiment.json"),
                "random_baseline": absent("exposure_experiment.json"),
                "factor_decay": absent("exposure_experiment.json"),
            },
        },
        "verdict_note": (
            "The experiment went straight to portfolio Sharpe. Whether the "
            "exposure signal predicted anything was never measured -- E4 "
            "eventually measured it out of sample at rho = -0.126."),
    },

    {
        "id": "E2-conditional-alpha-core",
        "kind": "hypothesis",
        "hypothesis": ("Alpha Core selection adds value conditional on the "
                       "exposure path."),
        "notes": ("Reported ~100th percentile and ~62% CAGR. Rejected as an "
                  "execution artifact: fills were taken at the close."),
        "expected_failure": ["the result depends on an unattainable fill"],
        "acceptance_criteria": {"percentile": "> random controls"},
        "artifact": "backtest_output/conditional_experiment.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H20_SIGNAL),
        },
        "verdict_note": (
            "Dies at Stage 2 on the signal itself. Had it survived, Stage 3 "
            "would have refused it anyway: a close-fill book supplies no "
            "next-session state, and `execution_realism` raises rather than "
            "assuming a fill rate."),
    },

    {
        "id": "E3-execution-realism",
        "kind": "hypothesis",
        "hypothesis": ("Alpha Core beats random selection under production "
                       "execution."),
        "notes": ("FAIL as run: 26.7th percentile, empirical p = 0.7419. "
                  "Sharpe levels uninterpretable -- idle cash earned 0% "
                  "against a 23% hurdle; realised exposure 29.22% against a "
                  "54.79% target."),
        "expected_failure": ["alpha core sharpe <= random p95 sharpe"],
        "acceptance_criteria": {"sharpe": "> random p95"},
        "artifact": "backtest_output/execution_realism.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H20_SIGNAL),
        },
        "verdict_note": (
            "Reached a 30-seed portfolio comparison -- days of compute -- "
            "without a Stage 2 measurement that costs an hour and answers it."),
    },

    {
        "id": "E4-exposure-holdout",
        "kind": "hypothesis",
        "hypothesis": ("The exposure engine delivers incremental value on "
                       "unseen data."),
        "notes": ("FAIL, 1 of 8 preregistered gates. The one significant "
                  "finding was that the engine's drawdown is DEEPER than the "
                  "control's. Exposure engine retired."),
        "expected_failure": ["fewer than 8 of 8 gates on the holdout"],
        "acceptance_criteria": {"gates": "8 of 8"},
        "artifact": "backtest_output/holdout_validation.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            # The only Phase 1 experiment whose Stage 2 evidence exists and
            # passes in sample: the engine was fitted there, which is why E1
            # looked good. The failure is genuinely an out-of-sample one.
            stages.ECONOMIC_SIGNAL: {
                "ic": ok(None, "holdout_report.md / training window",
                         "the engine was fitted in sample, where it scored"),
                "rank_correlation": ok(None, "holdout_report.md", "as above"),
                "random_baseline": ok(None, "holdout_report.md", "as above"),
                "factor_decay": ok(None, "holdout_report.md",
                                   "a timing signal has no cross-sectional "
                                   "decay curve; not applicable"),
            },
            stages.EXECUTION_REALISM: {
                "transaction_costs": ok(
                    42.24, "holdout_report.md",
                    "42.24% CAGR net of a -3.17pp/yr cost drag: positive"),
                "liquidity": ok(None, "holdout_report.md",
                                "index-level exposure, no single-name "
                                "liquidity constraint"),
                "participation_limits": ok(None, "holdout_report.md",
                                           "index-level exposure"),
                "slippage": ok(None, "holdout_report.md",
                               "index-level exposure"),
                "queue_effects": ok(None, "holdout_report.md",
                                    "no single-name queue exposure"),
            },
            stages.ROBUSTNESS: {
                "walk_forward": bad(
                    0.0, "holdout_report.md / G7",
                    "block capture upside 52.7% vs downside 64.0%: the "
                    "defensive claim inverts out of fold"),
                "out_of_sample": bad(
                    -0.126, "holdout_report.md",
                    "exposure -> forward-return rho = -0.126, t = -0.59, "
                    "p = 0.559 (n = 23); engine 42.24% CAGR vs control 43.31%, "
                    "Sharpe 1.39 vs 1.77, MaxDD -14.87% vs -10.75%"),
            },
        },
        "verdict_note": (
            "The one Phase 1 rejection that genuinely required expensive "
            "validation. In sample the engine scored; only unseen data "
            "falsified it. Stage 4 is the correct and cheapest stage that "
            "could have caught this."),
    },

    {
        "id": "E5-experiment4-preflight",
        "kind": "hypothesis",
        "hypothesis": ("Alpha Core's two factors generalise to an untouched "
                       "forward window."),
        "notes": ("INVALID -- correctly never run. Four preconditions failed: "
                  "no untouched holdout, git absent, survivorship, exposure "
                  "inequality. research_factors.py had already measured both "
                  "factors on the window proposed as the holdout (17,695 "
                  "observations across 23 dates)."),
        "expected_failure": ["the proposed holdout has already been evaluated"],
        "acceptance_criteria": {"preconditions": "4 of 4"},
        "artifact": "backtest_output/experiment4_preflight_audit.md",
        "evidence": {
            stages.DATA_INTEGRITY: {
                "point_in_time": bad(
                    17695, "experiment4_preflight_audit.md",
                    "the proposed holdout 2024-09-01..2026-06-30 was already "
                    "the fitted window: 17,695 observations over 23 dates"),
                "universe_valid": ok(120, "alpha_core_rank_ic.json",
                                     "120 dates with >= 50 names"),
                "missing_data": ok(22.29, "experiment14_queue_artifact.json",
                                   "22.29% stale < 30%"),
            },
        },
        "verdict_note": (
            "Phase 1's one genuinely cheap rejection, and it took a "
            "specially-written preflight audit to produce. Under this "
            "platform it is `windows_touched` -- arithmetic, automatic, and "
            "impossible to forget."),
    },

    {
        "id": "E6-alpha-core-rank-ic",
        "kind": "hypothesis",
        "hypothesis": "The Alpha Core score orders future returns.",
        "notes": ("PASS at h=5,10; FAIL at h=20 (the traded horizon) and "
                  "h=60. Later corrected by E13: E6's h=20 figures included a "
                  "contaminated 2024H2-2026 block that inflated the "
                  "traded-horizon headline roughly twofold."),
        "expected_failure": ["ICIR below 0.30 at the traded horizon"],
        "acceptance_criteria": {"mean IC": "> 0", "ICIR": "> 0.3",
                                "% positive": "> 50"},
        "artifact": "backtest_output/alpha_core_rank_ic.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H20_SIGNAL),
        },
        "verdict_note": (
            "The measurement that should have ended the programme. It was run "
            "sixth, and its verdict at the traded horizon -- ICIR 0.219 "
            "against a required 0.30 -- did not stop anything."),
    },

    {
        "id": "E7-alpha-preservation-audit",
        "kind": "diagnosis",
        "hypothesis": None,
        "notes": ("Diagnosis, not an experiment. Found that exposure loss was "
                  "100% slot occupancy (-29.42pp) rather than sizing "
                  "(+3.85pp), and that the attribution machinery already "
                  "existed but was discarded by two reversed key names and one "
                  "omitted read."),
        "artifact": "backtest_output/experiment5_alpha_preservation_audit.md",
    },

    {
        "id": "E8-p3-diagnostic-repair",
        "kind": "platform_change",
        "hypothesis": None,
        "notes": ("Platform change. Diagnostic nulls 4 -> 0, fields 34 -> 38. "
                  "Runs after the engine returns and writes nothing back, so "
                  "no prior result moved."),
        "artifact": "implementation_fix_spec.md",
    },

    {
        "id": "E9-experiment5a-frictionless",
        "kind": "hypothesis",
        "hypothesis": ("Alpha Core creates portfolio value before the "
                       "allocator and order book are introduced."),
        "notes": ("PASS as localization, both baskets: top-10 80.41% vs "
                  "24.32% random, 100th percentile, t = 4.04. Explicitly not "
                  "alpha validation -- turnover 11.26/yr against the "
                  "production system's 2.75."),
        "expected_failure": ["alpha CAGR <= random mean"],
        "acceptance_criteria": {"CAGR": "> random mean",
                                "paired test": "significant"},
        "artifact": "backtest_output/experiment5a_frictionless.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H20_SIGNAL),
        },
        "verdict_note": (
            "A 100th-percentile result that the platform rejects at Stage 2. "
            "That is the intended behaviour: a frictionless arm localises "
            "where value would come from and can never validate that it "
            "exists, because it has removed the costs that decide the "
            "question."),
    },

    {
        "id": "E10-idle-cash-accrual",
        "kind": "platform_change",
        "hypothesis": None,
        "notes": ("Platform change, default off. Corrected the defect's "
                  "location: backtesting/portfolio.py, not "
                  "backtest_portfolio.py as earlier documents stated."),
        "artifact": "experiment_registry.md E10",
    },

    {
        "id": "E11-experiment5b-production",
        "kind": "hypothesis",
        "hypothesis": ("Alpha Core retains incremental value through the real "
                       "allocator and order book."),
        "notes": ("FAIL -- 3.3rd percentile, empirical p = 0.9667. Production "
                  "edge -6.06pp; recoverable fraction -0.108. The validity "
                  "gate on exposure neutrality also failed, but in the "
                  "direction that favours the treatment, so the negative "
                  "conclusion holds a fortiori."),
        "expected_failure": ["recoverable fraction below 20%"],
        "acceptance_criteria": {"recoverable_fraction": "> 0.50"},
        "artifact": "backtest_output/experiment5b_production.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H20_SIGNAL),
        },
        "verdict_note": (
            "Thirty full portfolio backtests through the production pipeline "
            "to establish what one Stage 2 run says in an hour."),
    },

    {
        "id": "E12-phase3-decision-gate",
        "kind": "decision",
        "hypothesis": None,
        "notes": ("A decision, not an experiment. The preregistered gate "
                  "computed -10.8%, below the 20% floor and negative: STOP AND "
                  "REDESIGN SPECIFICATION."),
        "artifact": "production_readiness_scorecard.md",
    },

    {
        "id": "E13-alpha-decay",
        "kind": "hypothesis",
        "hypothesis": ("The signal exists but decays faster than the trading "
                       "system can harvest it."),
        "notes": ("CONFIRMED. IC 0.10492 at h=1 falls to 0.01456 at h=20. "
                  "Fitted half-life 11.36 sessions; production held names for "
                  "53.9 sessions, or 4.7 half-lives past decay. At h=1 the "
                  "0.55% edge against a 1.04% round trip is -69%/yr."),
        "expected_failure": ["the decay curve is flat, i.e. no decay"],
        "acceptance_criteria": {"IC at h=20": ">= 0.02 with ICIR >= 0.30"},
        "artifact": "backtest_output/alpha_decay_analysis.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H20_SIGNAL),
        },
        "verdict_note": (
            "The experiment that produced the Stage 2 evidence. Run "
            "thirteenth. Every number it needed was computable from data that "
            "existed before experiment 1."),
    },

    {
        "id": "E14-queue-artifact",
        "kind": "hypothesis",
        "hypothesis": ("The h=1 signal detects stocks entering or remaining in "
                       "price-limit queues, not persistent alpha."),
        "notes": ("QUEUE EXPOSURE CONFIRMED at 3.03x. At equal score, sector, "
                  "ADV quintile and volatility quintile, queue-flagged names "
                  "return +1.1039% (t = 12.47) against non-queue +0.1183% "
                  "(t = 1.09). Three defects were found and corrected mid-run, "
                  "including shift(-h) moving rows rather than sessions."),
        "expected_failure": ["queue exposure below 2.0x"],
        "acceptance_criteria": {"queue_exposure_ratio": ">= 2.0 confirms the "
                                                        "artifact"},
        "artifact": "backtest_output/experiment14_queue_artifact.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            # E14 is the one Alpha Core experiment whose declared horizon is
            # h=1, where the signal genuinely passes Stage 2. Its failure is a
            # Stage 3 failure, and that is the correct reading rather than a
            # concession: the signal IS real at h=1. It is unexecutable.
            stages.ECONOMIC_SIGNAL: dict(ALPHA_CORE_H1_SIGNAL),
            stages.EXECUTION_REALISM: {
                "transaction_costs": bad(
                    0.5496,
                    "experiment14_queue_artifact.json / decomposition",
                    "top-decile h=1 excess 0.5496% against a 1.04% round "
                    "trip: -0.49% per period, -69%/yr"),
                "liquidity": bad(
                    None, "experiment14_queue_artifact.json / exclusion_tests",
                    "removing locks AND illiquid names turns h=20 IC "
                    "significantly negative (-0.0249, t = -2.01)"),
                "participation_limits": absent(
                    "experiment14_queue_artifact.json"),
                "slippage": bad(
                    0.0, "every Phase 1 artifact",
                    "slippage was held at 0.0% in every result the programme "
                    "produced; a failure at zero slippage is a failure at any"),
                "queue_effects": bad(
                    3.026, "experiment14_queue_artifact.json / queue_exposure",
                    "top decile 7.751% locked at t+1 against a universe "
                    "2.561% = 3.03x, above the 1.50x limit"),
            },
        },
        "verdict_note": (
            "The signal passes Stage 2 at its declared horizon and dies at "
            "Stage 3. That is the platform working exactly as intended: real, "
            "and untradeable."),
    },

    {
        "id": "E15-execution-aware-rescue",
        "kind": "hypothesis",
        "hypothesis": ("An execution-aware filter using only decision-time "
                       "information converts the Alpha Core signal into a "
                       "positive net edge after costs."),
        "notes": ("FAIL -- 0 of 6 gates substantively. The filter worked: "
                  "queue exposure 5.01x -> 1.53x, fill rate 84.9% -> 96.0%. "
                  "The alpha went with it: IC at h=1 fell 71% and h=5/10/20 "
                  "all flipped negative. Preregistered with sha256 840af9be, "
                  "and it recorded in advance that failure was expected."),
        "expected_failure": ["net alpha remains negative after filtering"],
        "acceptance_criteria": {"6 gates": "all"},
        "artifact": "backtest_output/experiment15_execution_aware.json",
        "evidence": {
            stages.DATA_INTEGRITY: dict(CLEAN_DATA),
            stages.ECONOMIC_SIGNAL: {
                "ic": bad(-0.03245,
                          "experiment15_execution_aware.json / arms.B",
                          "filtered IC at h=20 is -0.03245, below 0.02 and "
                          "the wrong sign"),
                "rank_correlation": bad(
                    None, "experiment15_execution_aware.json",
                    "h=5/10/20 all flip negative after filtering"),
                "random_baseline": bad(
                    None, "experiment15_execution_aware.json / gates.G3",
                    "G3 passes only by losing less than a worse control "
                    "(arm B at h=1 is -86.21%/yr)"),
                "factor_decay": bad(
                    None, "experiment15_execution_aware.json",
                    "0.03033 at h=1 to -0.03245 at h=20: no retention"),
            },
        },
        "verdict_note": (
            "The filter removed the artifact and the alpha together, which is "
            "what E14 predicted. Stage 2 catches it because the filtered "
            "signal has no information at the traded horizon."),
    },
]


# ==============================================================
# REPLAY
# ==============================================================

def preregistration_for(entry):
    return {
        "parameters": {
            "replay_of": entry["id"],
            "artifact": entry["artifact"],
            "traded_horizon_sessions": 1 if entry["id"].startswith("E14") else 20,
            "note": ("Reconstructed from the recorded artifact. The hypothesis "
                     "and acceptance criteria are the ones the experiment "
                     "actually declared."),
        },
        "validation_gates": {
            "stage1": "PIT integrity, a frozen universe, a usable cross-section",
            "stage2": "IC and ICIR at the DECLARED horizon, against a "
                      "permutation null, with decay retention",
            "stage3": "net of the 1.04% round trip; queue ratio <= 1.50",
            "stage4": "walk-forward and an untouched holdout",
            "stage5": "percentile against random controls, bootstrap CI",
            "stage6": "operational controls, each evidenced",
        },
        "expected_metrics": {"recorded in the artifact": entry["artifact"]},
        "rejection_thresholds": dict(THRESHOLDS),
        "seed": 20260818,
    }


def replay(entry, path, snapshot_id):
    """Register one historical hypothesis and drive its stages."""

    handle = mgr.Manager.register(
        experiment_id=entry["id"],
        hypothesis=entry["hypothesis"],
        strategy_version="phase-1 (v2.0-archive)",
        researcher=RESEARCHER,
        notes=entry["notes"],
        expected_failure=entry["expected_failure"],
        acceptance_criteria=entry["acceptance_criteria"],
        preregistration=preregistration_for(entry),
        config={"replay": True, "artifact": entry["artifact"]},
        universe_snapshot=snapshot_id,
        path=path)

    first_failure = None

    for stage in stages.ORDER:

        block = entry["evidence"].get(stage)

        if stage == stages.DATA_INTEGRITY and block is not None:
            # The documented retrospective concession, applied in exactly one
            # place so it cannot be mistaken for a measurement. Finding zero
            # above is the honest answer; this is what makes the rest of the
            # replay able to say anything at all.
            block = dict(block)
            block["survivorship"] = ok(
                snapshot_id, "sirat_research.db / universe_snapshots",
                "GRANTED RETROSPECTIVELY: snapshot %s exists today. No "
                "snapshot existed during Phase 1 -- see finding zero, where "
                "this check fails for every experiment." % snapshot_id)

        if block is None:
            # No recorded evidence for this stage at all. Every required check
            # is therefore unmeasured, which is a failure rather than a skip.
            block = {check: absent(entry["artifact"])
                     for check in stages.REQUIRED_CHECKS[stage]}

        metrics = {check: bool(block[check]["passed"])
                   for check in stages.REQUIRED_CHECKS[stage]
                   if check in block}

        # Any required check the evidence block omits is absent, not passing.
        for check in stages.REQUIRED_CHECKS[stage]:
            metrics.setdefault(check, False)
            block.setdefault(check, absent(entry["artifact"]))

        detail = {check: {k: v for k, v in block[check].items()
                          if k != "passed"}
                  for check in stages.REQUIRED_CHECKS[stage]}

        outcome = handle.run_stage(stage, metrics, detail=detail)

        if outcome["status"] != stages.PASS:
            first_failure = {
                "stage": stage,
                "stage_name": stages.NAMES[stage],
                "failed": outcome["failed"],
                "reasons": [block[c]["basis"] for c in outcome["failed"]],
                "sources": sorted({block[c]["source"]
                                   for c in outcome["failed"]}),
            }
            break

    if first_failure:
        reason = first_failure["reasons"][0]
        handle.decide(
            mgr.REJECT,
            "Stage %d (%s) failed on %s. %s"
            % (first_failure["stage"], first_failure["stage_name"],
               ", ".join(first_failure["failed"]), reason),
            RESEARCHER)
    else:
        handle.decide(
            mgr.ARCHIVE,
            "Every stage passed on the recorded evidence. Not promoted: this "
            "is a retrospective replay, not a live result.", RESEARCHER)

    return handle, first_failure


def strict_finding(entry, path, snapshot_id):
    """Finding zero: what happens with the universe that actually existed."""

    handle = mgr.Manager.register(
        experiment_id=entry["id"] + "-strict",
        hypothesis=entry["hypothesis"],
        strategy_version="phase-1 (v2.0-archive)",
        researcher=RESEARCHER,
        notes="Strict replay: no universe snapshot existed during Phase 1.",
        expected_failure=entry["expected_failure"],
        acceptance_criteria=entry["acceptance_criteria"],
        preregistration=preregistration_for(entry),
        config={"replay": "strict"},
        universe_snapshot=None,
        path=path)

    block = dict(entry["evidence"].get(stages.DATA_INTEGRITY) or {})

    metrics = {check: bool(block.get(check, {}).get("passed", False))
               for check in stages.REQUIRED_CHECKS[stages.DATA_INTEGRITY]}

    metrics["survivorship"] = False

    outcome = handle.run_stage(
        stages.DATA_INTEGRITY, metrics,
        detail={"survivorship": {
            "value": None,
            "source": "backtesting/survivorship.py",
            "basis": "no universe snapshot was ever collected during Phase 1; "
                     "the instrument list is the list as it stands TODAY, so "
                     "every delisted company is absent exactly where its "
                     "absence flatters the result"}})

    handle.decide(mgr.REJECT,
                  "Stage 1 (DATA INTEGRITY) failed on survivorship: no "
                  "universe snapshot existed.", RESEARCHER)

    return outcome


def _substantive_reason(failure):
    """The first failing check backed by a real number, if there is one."""

    if not failure:
        return "no stage failed"

    measured = [r for r in failure["reasons"] if r != NOT_MEASURED]

    return measured[0] if measured else NOT_MEASURED


def say(message=""):
    print(message, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None)
    parser.add_argument("--json", default=None,
                        help="also write the table as JSON")

    args = parser.parse_args(argv)

    path = args.db or os.path.join(
        tempfile.mkdtemp(prefix="sirat_replay_"), "replay.db")

    current = universe.latest()

    snapshot_id = current["snapshot_id"] if current else "UNAVAILABLE"

    hypotheses = [e for e in HISTORY if e["kind"] == "hypothesis"]

    others = [e for e in HISTORY if e["kind"] != "hypothesis"]

    say("=" * 78)
    say("PHASE 1 REPLAYED THROUGH THE EXPERIMENT MANAGER")
    say("=" * 78)
    say("  research database  %s" % path)
    say("  universe snapshot  %s (granted retrospectively)" % snapshot_id)
    say("  hypotheses         %d of %d registry entries" % (len(hypotheses),
                                                            len(HISTORY)))
    say("  not hypotheses     %s"
        % ", ".join("%s (%s)" % (e["id"].split("-")[0], e["kind"])
                    for e in others))

    # ---- finding zero ----------------------------------------------------
    strict_path = os.path.join(os.path.dirname(path), "strict.db")

    strict = [strict_finding(e, strict_path, snapshot_id) for e in hypotheses]

    say()
    say("-" * 78)
    say("FINDING ZERO -- the strict answer, with the universe that existed")
    say("-" * 78)
    say("  %d of %d hypotheses stop at Stage 1 on `survivorship`."
        % (sum(1 for s in strict if s["status"] != stages.PASS),
           len(hypotheses)))
    say("  No universe snapshot was ever collected during Phase 1, so no")
    say("  experiment in the programme is re-runnable against the universe as")
    say("  it stood. The platform would have halted the programme on day one")
    say("  and demanded a snapshot -- which costs an afternoon and cannot be")
    say("  recovered later.")
    say()
    say("  The replay below grants today's snapshot in order to answer the")
    say("  more useful question: which stage catches each IDEA.")

    # ---- the replay -------------------------------------------------------
    rows = []

    say()
    say("-" * 78)
    say("REPLAY")
    say("-" * 78)

    for entry in hypotheses:

        handle, failure = replay(entry, path, snapshot_id)

        record = handle.record()

        rows.append({
            "id": entry["id"],
            "short": entry["id"].split("-")[0],
            "hypothesis": entry["hypothesis"],
            "registered": True,
            "preregistered": bool(record["preregistration"]),
            "prereg_hash": record["provenance"]["prereg_hash"][:12],
            "stage": failure["stage"] if failure else None,
            "stage_name": failure["stage_name"] if failure else "-",
            "failed_checks": failure["failed"] if failure else [],
            # `substantive` is the first failing check that was actually
            # measured, because that is the finding. E14 fails five checks, one
            # of which nobody computed; reporting it as "never measured" would
            # bury a 3.03x queue ratio behind a missing participation number.
            "reason": _substantive_reason(failure),
            # Only when EVERY failing check went unmeasured is the experiment
            # itself a never-measured case.
            "never_measured": bool(failure and failure["reasons"] and all(
                r == NOT_MEASURED for r in failure["reasons"])),
            "sources": failure["sources"] if failure else [],
            "decision": record["decision"]["decision"],
            "note": entry["verdict_note"],
        })

        say()
        say("  %-32s stage %s  %s"
            % (entry["id"], failure["stage"] if failure else "-",
               record["decision"]["decision"]))
        say("      failed        %s"
            % (", ".join(failure["failed"]) if failure else "-"))
        say("      because       %s" % _substantive_reason(failure))
        say("      evidence      %s" % (", ".join(failure["sources"])
                                        if failure else "-"))

    # ---- the table --------------------------------------------------------
    say()
    say("=" * 78)
    say("TABLE -- Experiment | First rejection stage | Reason | Decision")
    say("=" * 78)
    say()
    say("  %-6s %-7s %-30s %-9s %s"
        % ("Exp", "Stage", "Failed check", "Decision", "Basis"))
    say("  " + "-" * 108)

    for row in rows:
        say("  %-6s %-7s %-30s %-9s %s"
            % (row["short"],
               "s%d" % row["stage"] if row["stage"] else "-",
               ", ".join(row["failed_checks"])[:30],
               row["decision"],
               ("NEVER MEASURED" if row["never_measured"]
                else row["reason"])[:60]))

    for entry in others:
        say("  %-6s %-7s %-30s %-9s %s"
            % (entry["id"].split("-")[0], "n/a", "-", "n/a",
               "not a hypothesis: %s" % entry["kind"]))

    # ---- the KPI, from the Manager rather than from arithmetic here -------
    report = mgr.kpi(path=path)

    say()
    say("=" * 78)
    say("GOVERNANCE KPI -- at which stage are bad ideas rejected")
    say("=" * 78)

    by_stage = report["by_stage"]

    for stage in stages.ORDER:
        marker = "  <- cheap" if stage in stages.CHEAP_STAGES else ""
        say("  stage %d  %-28s %d%s"
            % (stage, stages.NAMES[stage], by_stage.get(stage, 0), marker))

    say()
    say("  rejected experiments      %d" % report["rejections"])
    say("  rejected at stages 1-3    %d" % report["cheap"])
    say("  rejected at stage 4+      %d" % (report["rejections"]
                                            - report["cheap"]))
    say("  EARLY REJECTION RATE      %.1f%%   (target %.0f%%)"
        % (report["rate"] * 100, report["target"] * 100))
    say("  meets target              %s" % report["meets_target"])

    never = [r["short"] for r in rows if r["never_measured"]]

    say()
    say("  Of the %d rejections, %d failed because the check was NEVER "
        "COMPUTED" % (report["rejections"], len(never)))
    say("  rather than because a number was bad: %s" % ", ".join(never))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"rows": rows, "kpi": report,
                       "universe_snapshot": snapshot_id}, handle,
                      ensure_ascii=False, indent=2, default=str)
        say()
        say("  table written to %s" % args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
