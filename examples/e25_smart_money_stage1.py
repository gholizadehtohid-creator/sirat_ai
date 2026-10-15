"""E25 -- SMART MONEY ACCUMULATION SCANNER. Preregistration and Stage 1 only.

    QUESTION    do the names this scanner selects -- small/mid caps at their
                own multi-month floor, near a tested support, with MFI above
                70, a net حقوقی->حقیقی transfer, a concentrated receiving side
                and a P/E below their sector's -- earn higher forward returns
                than the market?
    THIS FILE   does NOT answer that. It registers the question and runs
                Stage 1, DATA INTEGRITY, which is where the platform decides
                whether the question can be asked at all on the data that
                exists.

WHY REGISTER AN EXPERIMENT THAT IS EXPECTED TO FAIL STAGE 1
------------------------------------------------------------
Because that is what Stage 1 is for. The platform's stage order exists because
Phase 1 answered these questions in reverse and it cost fifteen experiments:
data integrity is minutes, portfolio construction is days, and a pipeline that
asks the expensive question first is a backtester rather than a governance
layer.

The Stage 1 checks were measured BEFORE this file was written, by
examples/smart_money_validation.py, and the numbers are declared below as
expected values. Four of the five are expected to fail. Declaring that in
advance is the difference between a preregistration and a post-hoc excuse: if
a check unexpectedly PASSES, the record shows the expectation was wrong, which
is information. If they fail as declared, the experiment is REJECTED at Stage
1 and the portfolio backtest never runs -- not because anyone lost interest,
but because the data to run it honestly is not in this database.

WHAT WOULD UNBLOCK A SUCCESSOR, NAMED HERE SO IT IS NOT REDISCOVERED
---------------------------------------------------------------------
  point_in_time   market_snapshot must become a versioned series (one row per
                  instrument per session) OR the valuation and size components
                  must be dropped and the remaining 75 points rescaled -- which
                  is a DIFFERENT model and needs its own registration.
  survivorship    census_price_history must be populated. delisted_sweep.py has
                  already discovered 13,245 instruments outside today's
                  universe and 9,851 inactive ones; it has fetched 0 bars.
  missing_data    P/E coverage is 59.8% of the equity universe against a 60%
                  floor. It fails by 0.2 points, which is a coincidence and not
                  a near-miss worth arguing about -- 40% of the universe has no
                  multiple at all.
  price_sanity    47 impossible session transitions remain in the 2023+ panel.
                  A decontamination rule must be preregistered BEFORE any
                  verdict, as E24 did.

NO WINDOW IS SPENT
------------------
Stage 1 reads coverage and integrity, not outcomes. R-FORWARD-2027
(2026-08-19..2027-08-19) is NAMED as the only window on which the return claim
could ever be tested, and is NOT claimed here.

USAGE
    python examples/e25_smart_money_stage1.py            register and run
    python examples/e25_smart_money_stage1.py --status   show the record
"""

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from sirat.experiments import stages                             # noqa: E402
from sirat.experiments.manager import (                          # noqa: E402
    Manager, GovernanceError, REJECT,
)


EXPERIMENT_ID = "E25-smart-money-accumulation-stage1"

UNIVERSE_SNAPSHOT = "u20260819-668658bb"

SEED = 20260824

FORWARD_RESERVATION = "R-FORWARD-2027"

# Measured by examples/smart_money_validation.py before this file was written.
# Restated here as declared expectations, not discovered afterwards.
MEASURED = {
    "snapshot_timestamps": 1,
    "median_names_per_date": 776.0,
    "sessions_2023_onward": 867,
    "price_coverage_pct": 96.9,
    "flow_coverage_pct": 95.4,
    "pe_coverage_pct": 59.8,
    "market_cap_coverage_pct": 63.2,
    "impossible_rows_2023_onward": 47,
    "impossible_names": 47,
    "panel_rows": 666954,
    "census_discovered": 14633,
    "census_inactive": 9851,
    "census_outside_todays_universe": 13245,
    "census_bars_fetched": 0,
    "breadth_upper_bound_median_per_session": 11,
    "breadth_share_sessions_with_10_plus": 55.9,
    "breadth_2026_mean_per_session": 2.9,
}

MIN_NAMES_PER_DATE = 100
MIN_COVERAGE_PCT = 60.0


PREREGISTRATION = {
    "parameters": {
        "instrument": "smart_money.scan() -- the live Smart Money Accumulation "
                      "Scanner, package smart_money/, weights 15/15/10/20/15/"
                      "15/10 summing to 100",
        "hard_gates": (
            "liquidity >= config.MIN_DAILY_VALUE on the decision session; "
            "traded share >= 85% of BOTH the 14-session MFI window and the "
            "20-session volume window; no impossible session move; MFI > 70; "
            "range position <= 35% of the 120-session range; a support below "
            "the price within 5%; net حقیقی inflow over the 5-session window"),
        "scope": (
            "STAGE 1 ONLY. Stages 2-6 are not attempted and the terminal "
            "decision is REJECT or ARCHIVE. No return, no portfolio, no "
            "Sharpe, no drawdown is computed anywhere in this experiment."),
        "double_counting_control": (
            "net حقیقی flow and net حقوقی flow are the same quantity with the "
            "sign flipped, so the 20-point transfer component scores it ONCE. "
            "The 15-point کد به کد component reads the participant COUNT "
            "columns instead, which are independent of the value columns."),
        "code_to_code_is_an_estimate": (
            "TSETMC publishes no per-code data. Smart Money Ratio = (individual "
            "share of session volume) x max(0, 1 - 1/elevation), where "
            "elevation is the recent mean individual ticket over the same "
            "name's own prior-window mean ticket. Declared as an estimator "
            "with a stated formula, never as a measurement of large-buyer "
            "volume."),
        "universe_snapshot": UNIVERSE_SNAPSHOT,
        "oos_claim": (
            "NONE POSSIBLE and none attempted. E19 spent 2023-03-01.."
            "2024-09-01 and E21 spent 2024-09-02..2026-08-18. The return claim "
            "may be tested out of sample ONLY against %s, by a successor "
            "registered after that window accrues. This experiment does not "
            "touch it." % FORWARD_RESERVATION),
        "in_sample_diagnostics_declared": (
            "the historical breadth measurement in "
            "examples/smart_money_validation.py section 7 runs over the two "
            "SPENT windows and computes no outcome. It is a capacity count, "
            "declared here so it cannot later be read as a hidden backtest."),
    },

    "validation_gates": {
        "point_in_time": (
            "every input to the score must be knowable on the decision "
            "session. market_snapshot must carry more than one captured_at, "
            "or the valuation and size components must be absent"),
        "survivorship": (
            "the historical universe must be restorable per decision date -- "
            "delisted and suspended names present with bars, not merely "
            "discovered"),
        "universe_valid": (
            "median measurable equities per session >= %d" % MIN_NAMES_PER_DATE),
        "missing_data": (
            "every scored input covers >= %.0f%% of the equity universe"
            % MIN_COVERAGE_PCT),
        "price_sanity": (
            "zero session transitions exceed gates.IMPOSSIBLE_SESSION_MOVE_PCT "
            "on the panel the scanner reads"),
    },

    "expected_metrics": {
        "point_in_time": (
            "FAIL EXPECTED -- market_snapshot holds %d captured_at; the "
            "valuation (15 pts) and size (10 pts) components have no history "
            "at all" % MEASURED["snapshot_timestamps"]),
        "survivorship": (
            "FAIL EXPECTED -- %d instruments discovered outside today's "
            "universe, %d bars fetched"
            % (MEASURED["census_outside_todays_universe"],
               MEASURED["census_bars_fetched"])),
        "universe_valid": (
            "PASS EXPECTED -- median %.0f names per session over %d sessions"
            % (MEASURED["median_names_per_date"],
               MEASURED["sessions_2023_onward"])),
        "missing_data": (
            "FAIL EXPECTED -- price %.1f%%, flow %.1f%%, but P/E %.1f%% and "
            "market cap %.1f%% against a %.0f%% floor"
            % (MEASURED["price_coverage_pct"], MEASURED["flow_coverage_pct"],
               MEASURED["pe_coverage_pct"],
               MEASURED["market_cap_coverage_pct"], MIN_COVERAGE_PCT)),
        "price_sanity": (
            "FAIL EXPECTED -- %d impossible transitions across %d names in %d "
            "panel rows, with no decontamination rule preregistered for this "
            "scanner" % (MEASURED["impossible_rows_2023_onward"],
                         MEASURED["impossible_names"],
                         MEASURED["panel_rows"])),
        "breadth_diagnostic": (
            "NOT A GATE. Upper-bound median %d candidates per session; 10 or "
            "more available on %.1f%% of sessions; 2026 mean %.1f. Recorded "
            "because the requested portfolio rule needs 10 names per rebalance."
            % (MEASURED["breadth_upper_bound_median_per_session"],
               MEASURED["breadth_share_sessions_with_10_plus"],
               MEASURED["breadth_2026_mean_per_session"])),
    },

    "rejection_thresholds": {
        "min_names_per_date": MIN_NAMES_PER_DATE,
        "min_coverage_pct": MIN_COVERAGE_PCT,
        "max_impossible_rows": 0,
        "min_snapshot_timestamps": 2,
        "min_census_bars_fetched": 1,
    },

    "seed": SEED,
}


HYPOTHESIS = (
    "سهام کوچک و متوسطی که در کف حمایتی، با ورود پول هوشمند، MFI بالای ۷۰، "
    "P/E پایین‌تر از میانه‌ی صنعت و کد به کد حقوقی به حقیقی قرار دارند، در "
    "آینده بازدهی بالاتری از بازار ایجاد می‌کنند. -- The scanner's own "
    "hypothesis, registered so that Stage 1 can decide whether it is testable "
    "on the data this database holds. Stage 1 alone is attempted here."
)

EXPECTED_FAILURE = (
    "Stage 1 fails on point_in_time and survivorship. The valuation and size "
    "components read market_snapshot, which holds ONE timestamp and is "
    "replaced on every pull, so 25 of the model's 100 points cannot be "
    "computed for any past decision date; and the universe is today's list, "
    "with the delisted census discovered but unfetched. Secondarily, P/E "
    "coverage (59.8%) sits below the 60% floor and 47 impossible session "
    "transitions remain in the panel. If Stage 1 unexpectedly passes, the "
    "expectation recorded here was wrong and that is the finding."
)

ACCEPTANCE_CRITERIA = (
    "Stage 1 PASSES only if all five checks pass: a versioned snapshot series "
    "exists (point_in_time), the historical universe is restorable with bars "
    "for delisted names (survivorship), median measurable names per session "
    ">= 100 (universe_valid), every scored input covers >= 60% of the equity "
    "universe (missing_data), and zero impossible session transitions remain "
    "(price_sanity). Anything less is a REJECT at Stage 1 and Stage 2 is not "
    "attempted."
)

NOTES = (
    "Companion to examples/smart_money_validation.py, which measured every "
    "number quoted in expected_metrics before this file existed. That script "
    "also found and fixed two defects in the scanner itself: names suspended "
    "or queue-locked for most of the reading window passed every gate on an "
    "MFI and a volume ratio computed over sessions that did not trade (فارس "
    "qualified on 2 of 14), and the scanner's impossible-move constant was "
    "200% where the governance layer's is 100%. Both are fixed and both are "
    "now guarded by tests. The scanner is CORRECT as a live instrument; this "
    "experiment is about whether it is TESTABLE as a historical one."
)


# The measured verdicts. False is a check that ran and did not pass -- never a
# check that was skipped, which the platform refuses to accept as either.
STAGE_1_METRICS = {
    "point_in_time": False,
    "survivorship": False,
    "universe_valid": True,
    "missing_data": False,
    "price_sanity": False,
}

STAGE_1_DETAIL = dict(MEASURED)
STAGE_1_DETAIL["measured_by"] = "examples/smart_money_validation.py"
STAGE_1_DETAIL["thresholds"] = PREREGISTRATION["rejection_thresholds"]


RATIONALE = (
    "REJECTED AT STAGE 1 -- DATA INTEGRITY. Four of five checks failed, all of "
    "them about the data rather than the idea. The hypothesis is UNTESTED, not "
    "disproved: nothing here measured a return.\n\n"
    "point_in_time: market_snapshot carries one captured_at and is replaced on "
    "every pull, so the valuation (15 pts) and size (10 pts) components have no "
    "value on any past date. Scoring a 2024 signal with 2026 P/E is look-ahead.\n"
    "survivorship: the universe is today's list. delisted_sweep.py has "
    "discovered 13,245 instruments outside it and fetched 0 bars, so no "
    "historical universe can be restored per decision date.\n"
    "missing_data: P/E covers 59.8% and market cap 63.2% of the equity "
    "universe, against a 60% floor.\n"
    "price_sanity: 47 impossible session transitions remain in the 2023+ panel "
    "and no decontamination rule was preregistered for this scanner.\n\n"
    "universe_valid passed (median 776 names per session over 867 sessions).\n\n"
    "Recorded as a diagnostic, gating nothing: an UPPER BOUND on breadth over "
    "the two already-spent windows gives a median of 11 candidates per session, "
    "10 or more on 55.9% of sessions, and a 2026 mean of 2.9. The requested "
    "portfolio rule takes the top 10 at Score >= 75; on the live session that "
    "threshold yielded 1 name. Breadth is therefore a second, independent "
    "obstacle to the requested design, and it is a property of the filter "
    "rather than of any market regime.\n\n"
    "SUCCESSOR PATH, in the order that unblocks the most for the least work:\n"
    "(a) version market_snapshot into a per-session series -- this alone makes "
    "a full-model historical test possible from that day forward;\n"
    "(b) finish delisted_sweep.py's fetch phase to populate census_price_history;\n"
    "(c) OR register a DIFFERENT, point-in-time-only model: MFI 15 + transfer "
    "20 + کد به کد 15 + support 15 + volume 10, rescaled from 75 to 100, which "
    "is testable on stored data today and must not be described as this "
    "scanner;\n"
    "(d) the full return claim may be tested out of sample only against "
    + FORWARD_RESERVATION + ", by a successor registered once it accrues."
)


def register():
    manager = Manager.register(
        experiment_id=EXPERIMENT_ID,
        hypothesis=HYPOTHESIS,
        strategy_version="smart-money-accumulation-v1",
        researcher="SIRAT-AI Research",
        notes=NOTES,
        expected_failure=EXPECTED_FAILURE,
        acceptance_criteria=ACCEPTANCE_CRITERIA,
        preregistration=PREREGISTRATION,
        universe_snapshot=UNIVERSE_SNAPSHOT,
    )

    record = manager.record()
    prereg = record.get("preregistration") or {}
    provenance = record.get("provenance") or {}

    print("registered  %s" % EXPERIMENT_ID)
    print("  prereg hash  %s" % str(prereg.get("content_hash"))[:32])
    print("  code hash    %s" % str(provenance.get("code_hash"))[:32])
    print("  git commit   %s" % str(provenance.get("git_commit"))[:16])

    return manager


def run(manager):
    result = manager.run_stage(stages.DATA_INTEGRITY, STAGE_1_METRICS,
                               detail=STAGE_1_DETAIL)

    print()
    print("stage 1  %s  --  %s" % (stages.NAMES[stages.DATA_INTEGRITY],
                                   result["status"]))

    for check, passed in sorted(STAGE_1_METRICS.items()):
        print("   %-16s %s" % (check, "PASS" if passed else "FAIL"))

    print()
    print("next stage allowed: %s" % manager.next_stage())

    return result


def decide(manager):
    manager.decide(REJECT, RATIONALE, by="SIRAT-AI Research")

    decision = manager.decision() or {}

    print()
    print("decision recorded: %s at stage %s"
          % (decision.get("decision"), decision.get("rejected_at_stage")))


def status():
    manager = Manager.load(EXPERIMENT_ID)
    record = manager.record()

    print("experiment  %s" % EXPERIMENT_ID)
    print("  status    %s" % record.get("status"))
    print("  stages")
    for row in manager.stage_results():
        print("    %d  %-28s %s" % (row["stage"], row["stage_name"],
                                    row["status"]))
    decision = manager.decision()
    if decision:
        print("  decision  %s (stage %s)" % (decision["decision"],
                                             decision.get("rejected_at_stage")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)

    if args.status:
        status()
        return 0

    # Idempotent by design. A registration cannot be deleted -- the platform
    # refuses, and it is right to: an erasable record is not a record. So a
    # re-run RESUMES from whatever is already on file rather than trying to
    # start over.
    try:
        manager = register()
    except GovernanceError as exc:
        print("already registered, resuming: %s" % exc.__class__.__name__)
        manager = Manager.load(EXPERIMENT_ID)

    if not manager.stage_results():
        run(manager)
    else:
        print()
        print("stage 1 already recorded, not re-run")

    if not manager.decision():
        decide(manager)
    else:
        print()
        print("decision already recorded, unchanged")

    print()
    status()

    return 0


if __name__ == "__main__":
    sys.exit(main())
