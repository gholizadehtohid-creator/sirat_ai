"""One experiment, end to end, through the governance layer.

    python examples/lifecycle_demo.py            the failed experiment
    python examples/lifecycle_demo.py --passing  one that survives every stage
    python examples/lifecycle_demo.py --both

WHAT THIS DEMONSTRATES, AND WHY THE FAILING ONE IS THE DEFAULT
---------------------------------------------------------------
The success criterion for this platform is not that a good idea gets through. It
is that a bad idea dies cheaply, with an audit trail a third party could check.
So the default run is a HYPOTHESIS THAT FAILS, and what it shows is:

  * it could not run at all until it was registered and preregistered
  * its thresholds came from the frozen record, so they could not move once a
    number existed
  * it died at Stage 2 -- hours of work -- rather than at Stage 5, which is
    where all fifteen Phase 1 experiments died, after days
  * the stages it never reached are recorded as never reached, not as passed
  * the decision names the stage, and the KPI counts it as a cheap rejection

The hypothesis under test is a real one, chosen because Phase 1 already knows
the answer: **short-horizon reversal.** Yesterday's biggest losers bounce. On
this data it produces a signal at h=1 that is gone by the horizon anyone could
trade -- the same shape as Alpha Core, discovered in an afternoon instead of
thirteen experiments.

NO MARKET DATA IS TOUCHED. The panel is synthetic and built with the answer put
into it deliberately, because the point of the demonstration is the lifecycle,
not the finding. A run against the real database would take twenty minutes and
show exactly the same sequence of refusals.
"""

import argparse
import math
import os
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments import gates, stages, universe               # noqa: E402
from sirat.experiments import manager as mgr                        # noqa: E402


SEED = 20260819

TRADED_HORIZON = 20

BASKET = 10

ROUND_TRIP_PCT = 1.04

# The decision-date grid, split BEFORE anything is measured.
#
# The split is here, at module level and above the preregistration, because
# that is the only place it can honestly be: a holdout carved out after a panel
# has been looked at is not a holdout. The training window and the holdout
# window are disjoint by construction and the preregistration below quotes both
# by date, so Stage 1 can check that no row strayed across the boundary.
GRID = pd.bdate_range("2020-01-06", periods=180, freq="W-MON")

TRAIN_DATES = GRID[:140]

HOLDOUT_DATES = GRID[140:]

WINDOW = (str(TRAIN_DATES[0].date()), str(TRAIN_DATES[-1].date()))

HOLDOUT_WINDOW = (str(HOLDOUT_DATES[0].date()),
                  str(HOLDOUT_DATES[-1].date()))


def synthetic_panel(decays, seed=SEED, dates=None, names=240):
    """A panel whose answer is known because it was put there.

    `decays=True` gives a strong h=1 signal that is noise by h=20 -- the shape
    E13 measured on Alpha Core. `decays=False` keeps the signal at every
    horizon, which no factor in this repository has ever done.

    SIZED FOR A STABLE QUEUE RATIO, not for realism. Locks here are drawn
    independently of the score, so the true basket-to-universe ratio is 1.0 --
    but a ten-name basket over sixty dates estimates that from ~600 Bernoulli
    draws at p=0.025, which puts a standard error of about 0.26 on the ratio.
    The demonstration was tripping Stage 3 on a two-sigma draw and reporting it
    as a queue artifact, which is a good illustration of why small samples
    should not be trusted and a bad illustration of the lifecycle. The real
    Alpha Core fixture measures the same quantity on 120 dates of actual market
    data and gets 3.03 with no such wobble.
    """

    rng = np.random.default_rng(seed)

    stamps = TRAIN_DATES if dates is None else dates

    rows = []

    for date in stamps:

        scores = rng.uniform(0, 100, names)

        locked = rng.random(names) < 0.025

        for index in range(names):

            centred = (scores[index] - 50.0) / 50.0

            row = {
                "date": date,
                "ins_code": "S%03d" % index,
                "score": float(scores[index]),
                "stale": bool(rng.random() < 0.06),
                "adv": float(rng.lognormal(20.5, 1.2)),
                "participation_pct": 1.8,
                "next_locked_up": bool(locked[index]),
                "next_unbuyable": bool(locked[index]
                                       or rng.random() < 0.015),
            }

            for horizon in (1, 5, 10, 20):
                # Signal per unit of sqrt(time): constant when the factor does
                # not decay, shrinking fast when it does.
                strength = (2.2 / horizon if decays else 1.6)
                row["fwd_%d" % horizon] = float(
                    strength * centred * math.sqrt(horizon)
                    + rng.normal(0, 2.4) * math.sqrt(horizon))

            rows.append(row)

    return pd.DataFrame(rows)


# The preregistration. Written before a single number exists, which is the only
# time it can honestly be written.
def preregistration():
    return {
        "parameters": {
            "signal": "short-horizon reversal: -1 x the previous session's "
                      "return, cross-sectionally ranked",
            "universe": "TSE equities, decision-time eligible",
            "traded_horizon_sessions": TRADED_HORIZON,
            "basket": BASKET,
            "window": list(WINDOW),
            "holdout_window": list(HOLDOUT_WINDOW),
            "round_trip_pct": ROUND_TRIP_PCT,
        },
        "validation_gates": {
            "stage1": "no leaked forward returns; a universe snapshot exists; "
                      "at least 50 scoreable names per date",
            "stage2": "mean IC at h=20 >= 0.02, ICIR >= 0.30, beats a "
                      "200-permutation null, retains >= 50% of peak IC at the "
                      "traded horizon",
            "stage3": "net of a 1.04% round trip and 0.10% slippage per leg is "
                      "positive; queue ratio <= 1.50",
            "stage4": ">= 60% of walk-forward folds positive out of fold",
            "stage5": ">= 95th percentile against 30 random controls, and a "
                      "block-bootstrap CI strictly above zero",
            "stage6": "risk limits, kill switch, monitoring and reconciliation "
                      "each evidenced by a named artifact",
        },
        "expected_metrics": {
            "mean_ic_h20": 0.03,
            "net_per_period_pct": 0.4,
            "queue_ratio": 1.1,
        },
        "rejection_thresholds": {
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
        },
        "seed": SEED,
    }


def paper_evidence():
    return {
        "risk_limits": {
            "control": "8% per position, 25% per sector, 10% cash floor",
            "artifact": "portfolio_engine.from_config()",
        },
        "kill_switch": {
            "control": "halt on -8% NAV in one session; PM releases the halt",
            "artifact": "backtesting/paper.py::PaperTrader",
        },
        "monitoring": {
            "control": "fill rate, queue exposure and slippage, every session",
            "artifact": "backtest_output/paper_daily.json",
        },
        "reconciliation": {
            "control": "paper fills against the tape, daily, breaks escalated",
            "artifact": "backtesting/paper.py::print_report",
        },
        "paper_sessions": 90,
    }


def say(message=""):
    print(message, flush=True)


def rule(title):
    say()
    say("=" * 74)
    say(title)
    say("=" * 74)


def run(decays, experiment_id, path, snapshot_id):
    """One experiment, all the way through, refusals and all."""

    rule("REGISTRATION")

    # ---- the refusal that comes first ------------------------------------
    say("  Attempting to run a stage before registering ...")

    try:
        mgr.Manager.load(experiment_id, path=path)
    except mgr.UnknownExperiment as exc:
        say("  REFUSED: %s" % exc)

    say()
    say("  Attempting to register without expected failure conditions ...")

    try:
        mgr.Manager.register(
            experiment_id=experiment_id, hypothesis="reversal works",
            strategy_version="v3.0-reversal", researcher="quant research",
            notes="n", expected_failure=[], acceptance_criteria={"ic": ">0"},
            preregistration=preregistration(), path=path)
    except mgr.RegistrationIncomplete as exc:
        say("  REFUSED: %s" % exc)

    say()

    handle = mgr.Manager.register(
        experiment_id=experiment_id,
        hypothesis=(
            "Short-horizon reversal: names that fell hardest in the previous "
            "session bounce over the following month, because forced selling "
            "into the lower band overshoots and unwinds once the queue clears."),
        strategy_version="v3.0-reversal",
        researcher="quant research",
        notes=("Chosen because the mechanism is microstructural rather than "
               "informational, so it should survive the cost gate that killed "
               "Alpha Core. Tested at h=20 only; other horizons are measured "
               "for the decay curve and cannot pass the gate."),
        expected_failure=[
            "IC at h=20 below 0.02, or ICIR below 0.30",
            "the signal exists at h=1 and is gone at h=20 (Alpha Core's shape)",
            "the edge does not clear a 1.04% round trip",
            "the basket is limit-locked more than 1.5x the universe rate",
        ],
        acceptance_criteria={
            "stage2": "mean IC at h=20 >= 0.02 with ICIR >= 0.30",
            "stage3": "net per period positive after cost and slippage",
            "stage5": ">= 95th percentile against 30 random controls",
            "promotion": "all six stages pass AND the result is reproducible",
        },
        preregistration=preregistration(),
        config={"round_trip_pct": ROUND_TRIP_PCT, "basket": BASKET},
        universe_snapshot=snapshot_id,
        path=path)

    record = handle.record()

    say("  registered      %s" % handle.experiment_id)
    say("  prereg hash     %s" % record["provenance"]["prereg_hash"][:32])
    say("  code hash       %s" % record["provenance"]["code_hash"][:32])
    say("  config hash     %s" % record["provenance"]["config_hash"][:32])
    say("  dataset         %s" % record["provenance"]["dataset_snapshot"])
    say("  universe        %s" % record["provenance"]["universe_snapshot"])
    say("  git commit      %s" % record["provenance"]["git_commit"])
    say("  reproducible    %s" % record["provenance"]["reproducible"])

    rule("PREREGISTRATION IS FROZEN")

    say("  The thresholds the gates will use come from the frozen record:")

    thresholds = handle.thresholds()

    for key in ("min_ic", "min_icir", "min_decay_retention",
                "max_queue_ratio"):
        say("    %-24s %s" % (key, thresholds[key]))

    say()
    say("  Attempting to move a threshold now that the experiment exists ...")

    import sqlite3

    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE preregistration SET rejection_thresholds = '{}' "
                     "WHERE experiment_id = ?", (experiment_id,))
    except sqlite3.IntegrityError as exc:
        say("  REFUSED at the database: %s" % exc)
    finally:
        conn.close()

    # ---- the panel --------------------------------------------------------
    panel = synthetic_panel(decays=decays)

    holdout = synthetic_panel(decays=decays, seed=SEED + 1,
                              dates=HOLDOUT_DATES)

    rule("STAGE PIPELINE")

    say("  Attempting to skip to Stage 4 ...")

    try:
        handle.run_stage(stages.ROBUSTNESS,
                         {"walk_forward": True, "out_of_sample": True})
    except mgr.StageOutOfOrder as exc:
        say("  REFUSED: %s" % exc)

    say()

    outcomes = []

    # ---- stage 1 ----------------------------------------------------------
    result = gates.data_integrity(
        panel, thresholds, window=WINDOW,
        universe_snapshot=snapshot_id, horizons=(1, 5, 10, 20))

    outcomes.append(handle.run_stage(stages.DATA_INTEGRITY, result))

    _report(outcomes[-1], {
        "rows": result.detail["rows"],
        "dates": result.detail["dates"],
        "names/date (median)": result.detail["names_per_date_median"],
        "stale %": result.detail["stale_pct"],
        "impossible forwards": result.detail["impossible_forward_returns"],
        "universe snapshot": result.detail["universe_snapshot"],
    })

    if outcomes[-1]["status"] != stages.PASS:
        return _close(handle, outcomes)

    # ---- stage 2 ----------------------------------------------------------
    result = gates.economic_signal(
        panel, thresholds, horizon=TRADED_HORIZON, seed=handle.seed(),
        permutations=200)

    outcomes.append(handle.run_stage(stages.ECONOMIC_SIGNAL, result))

    _report(outcomes[-1], {
        "mean IC @ h=20": result.detail["mean_ic"],
        "ICIR": result.detail["icir"],
        "% positive": result.detail["pct_positive_ic"],
        "null p95": result.detail["null_p95_ic"],
        "decay curve": result.detail["decay_curve"],
        "retention at h=20": result.detail["retention_at_traded_horizon"],
    })

    if outcomes[-1]["status"] != stages.PASS:
        return _close(handle, outcomes)

    # ---- stage 3 ----------------------------------------------------------
    result = gates.execution_realism(
        panel, thresholds, horizon=TRADED_HORIZON, basket=BASKET,
        round_trip_pct=ROUND_TRIP_PCT)

    outcomes.append(handle.run_stage(stages.EXECUTION_REALISM, result))

    _report(outcomes[-1], {
        "requested excess %": result.detail["requested_excess_pct"],
        "realised excess %": result.detail["realised_excess_pct"],
        "turnover/period": result.detail["turnover_per_period"],
        "cost/period %": result.detail["cost_per_period_pct"],
        "net/period %": result.detail["net_per_period_pct"],
        "net after slippage %": result.detail["net_after_slippage_pct"],
        "queue ratio": result.detail["queue_ratio"],
        "fill rate %": result.detail["fill_rate_pct"],
    })

    if outcomes[-1]["status"] != stages.PASS:
        return _close(handle, outcomes)

    # ---- stage 4 ----------------------------------------------------------
    handle.touch_window(WINDOW[0], WINDOW[1], "training")
    handle.touch_window(HOLDOUT_WINDOW[0], HOLDOUT_WINDOW[1], mgr.HOLDOUT)

    result = gates.robustness(panel, thresholds, horizon=TRADED_HORIZON,
                              train_dates=40, test_dates=20, holdout=holdout)

    outcomes.append(handle.run_stage(stages.ROBUSTNESS, result))

    _report(outcomes[-1], {
        "folds": result.detail["folds"],
        "positive fold share": result.detail["positive_fold_share"],
        "in-sample IC": result.detail["in_sample_ic"],
        "out-of-fold IC": result.detail["out_sample_ic"],
        "holdout IC": result.detail["holdout_ic"],
    })

    if outcomes[-1]["status"] != stages.PASS:
        return _close(handle, outcomes)

    # ---- stage 5 ----------------------------------------------------------
    treatment, controls = _arms(panel, seed=handle.seed())

    result = gates.statistical(treatment, controls, thresholds,
                               seed=handle.seed(), draws=2000)

    outcomes.append(handle.run_stage(stages.STATISTICAL, result))

    _report(outcomes[-1], {
        "percentile": result.detail["percentile"],
        "empirical p": result.detail["empirical_p"],
        "paired difference": result.detail["paired_difference_mean"],
        "95% CI": result.detail["ci95"],
    })

    if outcomes[-1]["status"] != stages.PASS:
        return _close(handle, outcomes)

    # ---- stage 6 ----------------------------------------------------------
    result = gates.paper_readiness(paper_evidence(), thresholds)

    outcomes.append(handle.run_stage(stages.PAPER_READINESS, result))

    _report(outcomes[-1], {"paper sessions": result.detail["paper_sessions"]})

    return _close(handle, outcomes)


def _arms(panel, seed, arms=30):
    """The treatment book against random-ranking controls.

    The controls keep eligibility, basket size, costs and the fill model
    identical and replace ONLY the order. Anything else would be a different
    strategy rather than a null.
    """

    column = "fwd_%d" % TRADED_HORIZON

    rng = np.random.default_rng(seed)

    def book(order_by):
        values = []
        for _, block in panel.groupby("date", sort=True):
            pool = block.dropna(subset=["score", column])
            if len(pool) < 50:
                continue
            picks = order_by(pool)
            unbuyable = picks["next_unbuyable"].astype(bool).to_numpy()
            filled = picks[~unbuyable]
            values.append(float(filled[column].sum() / len(picks))
                          - float(pool[column].mean()))
        return np.asarray(values, float)

    treatment = book(
        lambda pool: pool.sort_values("score", ascending=False).head(BASKET))

    controls = [
        book(lambda pool: pool.iloc[rng.permutation(len(pool))].head(BASKET))
        for _ in range(arms)
    ]

    return treatment, controls


def _report(outcome, numbers):
    say("  STAGE %d  %-28s  %s"
        % (outcome["stage"], outcome["stage_name"], outcome["status"]))

    for key, value in numbers.items():
        say("      %-22s %s" % (key, value))

    if outcome["failed"]:
        say("      FAILED CHECKS          %s" % ", ".join(outcome["failed"]))

    say()


def _close(handle, outcomes):
    """The decision, and the refusals around it."""

    rule("DECISION")

    failed = [o for o in outcomes if o["status"] != stages.PASS]

    if failed:
        stage = failed[0]

        say("  Attempting to PROMOTE anyway ...")

        try:
            handle.decide(mgr.PROMOTE, "the h=1 numbers looked strong",
                          "quant research")
        except mgr.GovernanceError as exc:
            say("  REFUSED: %s" % str(exc).split(". ")[0])

        say()

        decision = handle.decide(
            mgr.REJECT,
            "Stage %d (%s) failed on %s. Preregistered as an expected failure "
            "condition before the run."
            % (stage["stage"], stage["stage_name"],
               ", ".join(stage["failed"])),
            "quant research")
    else:
        say("  Every stage passed. Attempting PROMOTE ...")

        try:
            decision = handle.decide(
                mgr.PROMOTE,
                "All six stages passed against thresholds frozen before the "
                "run.", "quant research")
        except mgr.NotReproducible as exc:
            say("  REFUSED: %s" % exc)
            say()
            decision = handle.decide(
                mgr.ARCHIVE,
                "Every gate passed, but the result cannot be re-derived: git "
                "is not installed, so there is no code version to reproduce "
                "from. Archived pending Week 1 item 1.",
                "quant research")

    say("  decision            %s" % decision["decision"])
    say("  rejected at stage   %s" % (decision["rejected_at_stage_name"]
                                      or "-"))
    say("  cheap rejection     %s" % decision["cheap_rejection"])

    say()
    say("  Attempting to run another stage after the decision ...")

    try:
        handle.run_stage(stages.PAPER_READINESS, {})
    except mgr.ExperimentClosed as exc:
        say("  REFUSED: %s" % exc)

    path = handle.write_record(
        directory=os.path.join(ROOT, "backtest_output"))

    say()
    say("  record written to %s" % os.path.relpath(path, ROOT))

    return handle


def prove_promotion_blocked(path, snapshot_id):
    """Six stages passed, and PROMOTE still refused. On purpose.

    Once git exists this refusal stops firing in the ordinary demonstration,
    which is exactly when it most needs proving: a control nobody can see
    working is a control nobody will notice breaking.

    Nothing is faked to produce it. `provenance` is pointed at a directory that
    genuinely has no repository, so `git_commit()` returns UNAVAILABLE because
    it really cannot answer -- the same measurement, in a place where the answer
    is honestly absent. That is the state every Phase 1 experiment was recorded
    in, and the point is that such a record can never be promoted afterwards,
    however good its numbers turn out to be.
    """

    from sirat.experiments import provenance

    rule("PROMOTION CONTROL -- a result that cannot be re-derived")

    elsewhere = tempfile.mkdtemp(prefix="sirat_norepo_")

    saved = provenance.ROOT

    try:
        provenance.ROOT = elsewhere
        say("  capturing provenance from a directory with no repository ...")
        say("    git_commit    %s" % provenance.git_commit())
        say("    git_dirty     %s" % provenance.git_dirty())
        say()

        handle = mgr.Manager.register(
            experiment_id="E18-unreproducible",
            hypothesis=("Identical to E17 in every respect, registered from a "
                        "working tree whose code version cannot be recovered."),
            strategy_version="v3.0-reversal",
            researcher="quant research",
            notes=("Exists to prove the promotion control fires. The numbers "
                   "are not the point; the provenance is."),
            expected_failure=["the same conditions as E17"],
            acceptance_criteria={"promotion": "all six stages AND reproducible"},
            preregistration=preregistration(),
            config={"round_trip_pct": ROUND_TRIP_PCT, "basket": BASKET},
            universe_snapshot=snapshot_id,
            path=path)
    finally:
        provenance.ROOT = saved

    record = handle.record()

    say("  registered      %s" % handle.experiment_id)
    say("  git commit      %s" % record["provenance"]["git_commit"])
    say("  reproducible    %s" % record["provenance"]["reproducible"])
    say()

    # Every stage passes. The refusal is not about the evidence.
    for stage in stages.ORDER:
        handle.run_stage(stage, {check: True
                                 for check in stages.REQUIRED_CHECKS[stage]},
                         detail={"note": "passed for the purposes of the "
                                         "promotion-control demonstration"})

    say("  all six stages recorded as PASS")
    say()
    say("  Attempting PROMOTE ...")

    try:
        handle.decide(mgr.PROMOTE, "every gate passed", "quant research")
        say("  *** NOT REFUSED -- the promotion control is broken ***")
        return False
    except mgr.NotReproducible as exc:
        say("  REFUSED: %s" % exc)

    handle.decide(mgr.ARCHIVE,
                  "Every gate passed and the result cannot be re-derived. "
                  "Archived rather than promoted.", "quant research")

    say()
    say("  Research can continue. Capital promotion cannot.")

    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--passing", action="store_true",
                        help="run a hypothesis that survives every stage")
    parser.add_argument("--both", action="store_true")
    parser.add_argument("--promotion-control", action="store_true",
                        help="prove PROMOTE is refused without reproducibility")
    parser.add_argument("--db", default=None,
                        help="research database (default: a temporary one, so "
                             "the demonstration never writes into the real "
                             "audit trail)")

    args = parser.parse_args(argv)

    path = args.db or os.path.join(
        tempfile.mkdtemp(prefix="sirat_demo_"), "research.db")

    say("research database: %s" % path)

    # A real snapshot, captured INTO the demonstration's own database.
    #
    # Citing the live record's snapshot id would not do: promotion re-verifies
    # that the cited universe still restores, and it looks for it in the
    # experiment's own research database. An id that resolves nowhere is a
    # citation to nothing, and the platform is right to refuse it -- so the
    # demonstration has to freeze a real universe of its own.
    try:
        snapshot = universe.capture(path=path)
        snapshot_id = snapshot["snapshot_id"]
    except Exception as exc:                                   # noqa: BLE001
        say("could not freeze a universe (%s); Stage 1 will fail on "
            "survivorship, which is the correct behaviour" % exc)
        snapshot_id = "UNAVAILABLE"

    say("universe snapshot: %s" % snapshot_id)

    runs = []

    if args.both or not args.passing:
        runs.append((True, "E16-reversal-h20"))

    if args.both or args.passing:
        runs.append((False, "E17-reversal-nodecay"))

    for decays, experiment_id in runs:
        rule("EXPERIMENT %s -- %s" % (
            experiment_id,
            "a hypothesis that fails" if decays
            else "a hypothesis that survives every gate"))
        run(decays, experiment_id, path, snapshot_id)

    if args.promotion_control or args.both:
        prove_promotion_blocked(path, snapshot_id)

    rule("PLATFORM KPI")

    report = mgr.kpi(path=path)

    if report["rejections"]:
        say("  rejections            %d" % report["rejections"])
        say("  at stages 1-3         %d" % report["cheap"])
        say("  cheap rejection rate  %.0f%%  (target %.0f%%)"
            % (report["rate"] * 100, report["target"] * 100))
        say("  meets target          %s" % report["meets_target"])
        say()
        for stage in stages.ORDER:
            say("    stage %d  %-28s %d"
                % (stage, stages.NAMES[stage],
                   report["by_stage"].get(stage, 0)))
    else:
        say("  no rejections recorded")

    say()
    say("  The KPI is not how many experiments ran. It is where they died.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
