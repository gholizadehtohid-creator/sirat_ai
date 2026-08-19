"""Tests for the Experiment Manager -- mostly tests that it REFUSES.

A governance layer is not tested by checking that a valid experiment runs. It is
tested by checking that every invalid one is stopped, because the invalid ones
are what it exists for. Each test below corresponds to something that actually
happened during Phase 1 and cost real time.

Nothing here touches sirat_market.db or the real research database. Every test
gets its own temporary sqlite file, so a test run cannot write into the audit
trail -- which would be its own governance failure.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sirat.experiments import manager as mgr
from sirat.experiments import stages, store


def prereg(**overrides):
    base = {
        "parameters": {"horizon": 20, "basket": 10, "window": ["2014", "2024"]},
        "validation_gates": {
            "stage2": "mean IC at h=20 >= 0.02",
            "stage3": "net of a 1.04% round trip > 0",
        },
        "expected_metrics": {"mean_ic": 0.03, "queue_ratio": 1.2},
        "rejection_thresholds": {
            "min_ic": 0.02, "min_icir": 0.30, "max_queue_ratio": 1.5,
        },
        "seed": 20260818,
    }
    base.update(overrides)
    return base


def register(path, experiment_id="E-TEST", **overrides):
    kwargs = {
        "experiment_id": experiment_id,
        "hypothesis": "a hypothesis with an economic mechanism behind it",
        "strategy_version": "v3.0",
        "researcher": "tester",
        "notes": "registered by the test suite",
        "expected_failure": ["the signal decays before the traded horizon"],
        "acceptance_criteria": {"stage2": "IC >= 0.02", "stage3": "net > 0"},
        "preregistration": prereg(),
        "config": {"cost_pct": 1.04},
        "path": path,
    }
    kwargs.update(overrides)
    return mgr.Manager.register(**kwargs)


def register_reproducible(path, **overrides):
    """Register with provenance reporting a clean, committed tree.

    The promotion preconditions are checked in order, and reproducibility comes
    second. Without this the tests for preconditions three, four and five would
    pass or fail depending on whether the developer happened to have committed
    -- they would be testing the state of the working directory rather than the
    control. Reproducibility itself is covered by
    `test_promote_needs_a_reproducible_result`, against the real thing.
    """

    from sirat.experiments import provenance

    saved = (provenance.git_commit, provenance.git_dirty)

    provenance.git_commit = lambda: "0" * 40
    provenance.git_dirty = lambda: False

    try:
        return register(path, **overrides)
    finally:
        provenance.git_commit, provenance.git_dirty = saved


def _tiny_market(directory):
    """A stand-in market database, so a snapshot can be frozen without the
    424 MB file being present."""

    path = os.path.join(directory, "market.db")

    conn = sqlite3.connect(path)

    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS instruments ("
            "ins_code TEXT PRIMARY KEY, symbol_fa TEXT, symbol_raw TEXT, "
            "name_fa TEXT, isin TEXT, kind TEXT, board TEXT, "
            "is_equity INTEGER, sector_code TEXT, sector_name TEXT, "
            "updated_at TEXT)")
        conn.executemany(
            "INSERT OR IGNORE INTO instruments (ins_code, symbol_fa, "
            "is_equity) VALUES (?,?,?)",
            [("%d" % (1000 + i), "SYM%d" % i, 1) for i in range(8)])
        conn.commit()
    finally:
        conn.close()

    return path


PASSING = {
    stages.DATA_INTEGRITY: {"point_in_time": True, "survivorship": True,
                            "universe_valid": True, "missing_data": True},
    stages.ECONOMIC_SIGNAL: {"ic": True, "rank_correlation": True,
                             "random_baseline": True, "factor_decay": True},
    stages.EXECUTION_REALISM: {"transaction_costs": True, "liquidity": True,
                               "participation_limits": True, "slippage": True,
                               "queue_effects": True},
    stages.ROBUSTNESS: {"walk_forward": True, "out_of_sample": True},
    stages.STATISTICAL: {"monte_carlo": True, "bootstrap": True,
                         "confidence_intervals": True},
    stages.PAPER_READINESS: {"risk_limits": True, "kill_switch": True,
                             "monitoring": True, "reconciliation": True},
}


class ManagerTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_test_")
        self.path = os.path.join(self.dir, "research.db")

    def run_all_stages(self, handle, through=stages.PAPER_READINESS):
        for stage in stages.ORDER:
            if stage > through:
                break
            handle.run_stage(stage, dict(PASSING[stage]))


# ==============================
# REGISTRATION
# ==============================

class Registration(ManagerTestCase):

    def test_registers_with_every_field(self):
        handle = register(self.path)

        record = handle.record()

        self.assertEqual(record["experiment_id"], "E-TEST")
        self.assertEqual(record["status"], "REGISTERED")
        self.assertEqual(record["preregistration"]["seed"], 20260818)

    def test_provenance_is_recorded_on_every_experiment(self):
        record = register(self.path).record()

        for field in ("git_commit", "code_hash", "config_hash",
                      "dataset_snapshot", "universe_snapshot", "prereg_hash"):
            self.assertIn(field, record["provenance"])
            self.assertTrue(record["provenance"][field])

    def test_missing_hypothesis_is_refused(self):
        with self.assertRaises(mgr.RegistrationIncomplete):
            register(self.path, hypothesis="")

    def test_whitespace_is_not_a_hypothesis(self):
        with self.assertRaises(mgr.RegistrationIncomplete):
            register(self.path, hypothesis="   ")

    def test_missing_expected_failure_is_refused(self):
        # "What would make me abandon this" is the field Alpha Core never had.
        with self.assertRaises(mgr.RegistrationIncomplete):
            register(self.path, expected_failure=[])

    def test_missing_acceptance_criteria_is_refused(self):
        with self.assertRaises(mgr.RegistrationIncomplete):
            register(self.path, acceptance_criteria={})

    def test_duplicate_id_is_refused(self):
        register(self.path)
        with self.assertRaises(mgr.GovernanceError):
            register(self.path)

    def test_unregistered_experiment_cannot_be_loaded(self):
        with self.assertRaises(mgr.UnknownExperiment):
            mgr.Manager.load("never-registered", path=self.path)

    def test_empty_database_reports_the_experiment_not_the_schema(self):
        # Before the first registration there are no tables. The caller must be
        # told the experiment is unregistered, not that a table is missing.
        with self.assertRaises(mgr.UnknownExperiment):
            mgr.Manager("anything", path=os.path.join(self.dir, "fresh.db"))


# ==============================
# PREREGISTRATION
# ==============================

class Preregistration(ManagerTestCase):

    def test_missing_thresholds_are_refused(self):
        with self.assertRaises(mgr.PreregistrationIncomplete):
            register(self.path,
                     preregistration=prereg(rejection_thresholds={}))

    def test_missing_seed_is_refused(self):
        spec = prereg()
        del spec["seed"]
        with self.assertRaises(mgr.PreregistrationIncomplete):
            register(self.path, preregistration=spec)

    def test_non_integer_seed_is_refused(self):
        with self.assertRaises(mgr.PreregistrationIncomplete):
            register(self.path, preregistration=prereg(seed="whatever"))

    def test_thresholds_come_back_exactly_as_frozen(self):
        handle = register(self.path)
        self.assertEqual(handle.thresholds(),
                         prereg()["rejection_thresholds"])

    def test_sql_update_of_a_frozen_prereg_raises(self):
        register(self.path)

        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE preregistration SET rejection_thresholds = '{}' "
                    "WHERE experiment_id = 'E-TEST'")
        finally:
            conn.close()

    def test_sql_delete_of_a_frozen_prereg_raises(self):
        register(self.path)

        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM preregistration "
                             "WHERE experiment_id = 'E-TEST'")
        finally:
            conn.close()

    def test_a_rebuilt_row_that_does_not_hash_is_detected(self):
        """The failure the triggers cannot see.

        Someone restores a backup, or a script recreates the table. No UPDATE
        ever runs, so no trigger fires, and the thresholds are different from
        the ones that were frozen. The stored hash catches it.
        """

        handle = register(self.path)

        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(
                "PRAGMA writable_schema = ON;"
                "DROP TRIGGER prereg_no_update;"
                "DROP TRIGGER prereg_no_delete;")
            conn.execute(
                "UPDATE preregistration SET rejection_thresholds = ? "
                "WHERE experiment_id = 'E-TEST'",
                (json.dumps({"min_ic": -99.0}),))
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(mgr.PreregistrationTampered):
            handle.thresholds()


# ==============================
# STAGES
# ==============================

class Stages(ManagerTestCase):

    def test_first_stage_is_data_integrity(self):
        self.assertEqual(register(self.path).next_stage(),
                         stages.DATA_INTEGRITY)

    def test_stage_two_before_stage_one_is_refused(self):
        handle = register(self.path)
        with self.assertRaises(mgr.StageOutOfOrder):
            handle.run_stage(stages.ECONOMIC_SIGNAL,
                             dict(PASSING[stages.ECONOMIC_SIGNAL]))

    def test_expensive_stage_after_a_cheap_failure_is_refused(self):
        """The single most valuable refusal in the platform.

        Alpha Core's cost arithmetic was an afternoon's work and was not done
        until experiment 13. Under this rule nothing reaches a walk-forward
        without it.
        """

        handle = register(self.path)

        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]))

        handle.run_stage(stages.ECONOMIC_SIGNAL,
                         dict(PASSING[stages.ECONOMIC_SIGNAL],
                              factor_decay=False))

        with self.assertRaises(mgr.StageOutOfOrder):
            handle.run_stage(stages.EXECUTION_REALISM,
                             dict(PASSING[stages.EXECUTION_REALISM]))

    def test_incomplete_stage_is_not_a_passing_stage(self):
        """Absence is not approval.

        Four `null` execution counters were read as 'no queues were ever hit'
        for an entire programme. A stage that does not report a required check
        raises rather than passing.
        """

        handle = register(self.path)

        with self.assertRaises(stages.StageIncomplete):
            handle.run_stage(stages.DATA_INTEGRITY,
                             {"point_in_time": True, "survivorship": True})

    def test_the_platform_decides_the_verdict_not_the_caller(self):
        handle = register(self.path)

        result = handle.run_stage(
            stages.DATA_INTEGRITY,
            dict(PASSING[stages.DATA_INTEGRITY], survivorship=False))

        self.assertEqual(result["status"], stages.FAIL)
        self.assertEqual(result["failed"], ["survivorship"])

    def test_a_stage_cannot_be_re_run(self):
        handle = register(self.path)

        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]))

        with self.assertRaises(mgr.StageOutOfOrder):
            handle.run_stage(stages.DATA_INTEGRITY,
                             dict(PASSING[stages.DATA_INTEGRITY]))

    def test_sql_update_of_a_stage_result_raises(self):
        handle = register(self.path)
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]))

        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE stage_results SET status = 'PASS'")
        finally:
            conn.close()

    def test_detail_is_stored_with_the_verdict(self):
        handle = register(self.path)

        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]),
                         detail={"stale_pct": 22.29})

        entry = handle.record()["stages"][0]

        self.assertEqual(entry["metrics"]["detail"]["stale_pct"], 22.29)

    def test_next_stage_is_none_after_a_failure(self):
        handle = register(self.path)

        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY],
                              point_in_time=False))

        self.assertIsNone(handle.next_stage())


# ==============================
# DECISIONS
# ==============================

class Decisions(ManagerTestCase):

    def test_decision_without_evidence_is_refused(self):
        handle = register(self.path)
        with self.assertRaises(mgr.NoEvidence):
            handle.decide(mgr.REJECT, "gut feeling", "tester")

    def test_reject_records_the_stage_it_died_at(self):
        handle = register(self.path)

        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY],
                              survivorship=False))

        out = handle.decide(mgr.REJECT, "no universe snapshot exists", "tester")

        self.assertEqual(out["rejected_at_stage"], stages.DATA_INTEGRITY)
        self.assertTrue(out["cheap_rejection"])

    def test_reject_without_a_failed_stage_is_refused(self):
        handle = register(self.path)
        self.run_all_stages(handle)

        with self.assertRaises(mgr.GovernanceError):
            handle.decide(mgr.REJECT, "changed my mind", "tester")

    def test_promote_without_every_stage_is_refused(self):
        handle = register(self.path)
        self.run_all_stages(handle, through=stages.ROBUSTNESS)

        with self.assertRaises(mgr.ValidationMissing):
            handle.decide(mgr.PROMOTE, "looks strong", "tester")

    def test_promote_needs_a_reproducible_result(self):
        """Today this refuses everything, and that is the correct behaviour.

        git is not installed, so no result in this repository can be re-derived.
        Research proceeds; promotion does not.
        """

        handle = register(self.path)
        self.run_all_stages(handle)

        reproducible = handle.record()["provenance"]["reproducible"]

        if reproducible:
            out = handle.decide(mgr.PROMOTE, "every gate passed", "tester")
            self.assertEqual(out["decision"], mgr.PROMOTE)
        else:
            with self.assertRaises(mgr.NotReproducible):
                handle.decide(mgr.PROMOTE, "every gate passed", "tester")

    def test_promote_needs_a_universe_snapshot(self):
        """Missing evidence is never a pass, and this is the sharpest case.

        Without a frozen universe the survivorship question has no answer and
        the experiment cannot be re-run against the universe it actually used.
        """

        handle = register_reproducible(self.path, universe_snapshot=None)
        self.run_all_stages(handle)

        with self.assertRaises(mgr.EvidenceMissing) as caught:
            handle.decide(mgr.PROMOTE, "every gate passed", "tester",
                          regression_check=lambda: None)

        self.assertIn("universe snapshot", str(caught.exception))

    def test_promote_re_verifies_the_snapshot_at_promotion_time(self):
        """An experiment can be registered against a good snapshot and promoted
        a month later, by which time the snapshot may be gone. The check has to
        happen now, not at registration."""

        from sirat.experiments import universe as universe_module

        market = _tiny_market(self.dir)

        snapshot = universe_module.capture(market_db=market, path=self.path)

        handle = register_reproducible(
            self.path, universe_snapshot=snapshot["snapshot_id"])
        self.run_all_stages(handle)

        # The snapshot is rewritten after every stage passed.
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("UPDATE universe_snapshots SET payload = '[]' "
                         "WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(mgr.EvidenceMissing) as caught:
            handle.decide(mgr.PROMOTE, "every gate passed", "tester",
                          regression_check=lambda: None)

        self.assertIn("no longer restores", str(caught.exception))

    def test_promote_is_refused_while_the_regression_suite_is_red(self):
        """A platform that cannot rediscover its own documented failures has no
        standing to certify a new strategy for capital."""

        from sirat.experiments import universe as universe_module

        market = _tiny_market(self.dir)

        snapshot = universe_module.capture(market_db=market, path=self.path)

        handle = register_reproducible(
            self.path, universe_snapshot=snapshot["snapshot_id"])
        self.run_all_stages(handle)

        def broken():
            raise AssertionError("queue_ratio measured 1.01, expected 3.03")

        with self.assertRaises(mgr.RegressionFailing) as caught:
            handle.decide(mgr.PROMOTE, "every gate passed", "tester",
                          regression_check=broken)

        self.assertIn("queue_ratio", str(caught.exception))

    def test_promote_succeeds_when_every_precondition_holds(self):
        """The positive case, so the refusals above are not vacuous.

        A control that refuses everything is indistinguishable from a control
        that is broken, and this is the test that tells them apart.
        """

        from sirat.experiments import provenance
        from sirat.experiments import universe as universe_module

        market = _tiny_market(self.dir)

        snapshot = universe_module.capture(market_db=market, path=self.path)

        handle = register_reproducible(
            self.path, universe_snapshot=snapshot["snapshot_id"])
        self.run_all_stages(handle)

        if handle.record()["provenance"]["dataset_snapshot"].startswith(
                provenance.UNAVAILABLE):
            self.skipTest("no market database on this machine, so the dataset "
                          "precondition cannot be satisfied here")

        out = handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                            regression_check=lambda: None)

        self.assertEqual(out["decision"], mgr.PROMOTE)

    def test_promote_needs_an_identified_dataset(self):
        from sirat.experiments import provenance
        from sirat.experiments import universe as universe_module

        market = _tiny_market(self.dir)

        snapshot = universe_module.capture(market_db=market, path=self.path)

        saved = provenance.dataset_snapshot

        provenance.dataset_snapshot = lambda: provenance.UNAVAILABLE + ":test"

        try:
            handle = register_reproducible(
                self.path, universe_snapshot=snapshot["snapshot_id"])
        finally:
            provenance.dataset_snapshot = saved

        self.run_all_stages(handle)

        with self.assertRaises(mgr.EvidenceMissing) as caught:
            handle.decide(mgr.PROMOTE, "every gate passed", "tester",
                          regression_check=lambda: None)

        self.assertIn("dataset snapshot", str(caught.exception))

    def test_unattributed_decision_is_refused(self):
        handle = register(self.path)
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY],
                              missing_data=False))

        with self.assertRaises(mgr.GovernanceError):
            handle.decide(mgr.REJECT, "bad data", "")

    def test_nothing_runs_after_a_decision(self):
        handle = register(self.path)
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY],
                              missing_data=False))
        handle.decide(mgr.REJECT, "22% of bars are stale", "tester")

        with self.assertRaises(mgr.ExperimentClosed):
            handle.run_stage(stages.ECONOMIC_SIGNAL,
                             dict(PASSING[stages.ECONOMIC_SIGNAL]))

    def test_sql_update_of_a_decision_raises(self):
        handle = register(self.path)
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY],
                              missing_data=False))
        handle.decide(mgr.REJECT, "stale data", "tester")

        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE decisions SET decision = 'PROMOTE'")
        finally:
            conn.close()


# ==============================
# HOLDOUT LEDGER
# ==============================

class HoldoutLedger(ManagerTestCase):

    def test_a_holdout_can_be_spent_once(self):
        first = register(self.path, experiment_id="E-A")
        first.touch_window("2026-01-01", "2026-08-01", mgr.HOLDOUT)

        second = register(self.path, experiment_id="E-B")

        with self.assertRaises(mgr.WindowAlreadySpent):
            second.touch_window("2026-01-01", "2026-08-01", mgr.HOLDOUT)

    def test_the_same_experiment_may_re_record_its_own_window(self):
        handle = register(self.path)
        handle.touch_window("2026-01-01", "2026-08-01", mgr.HOLDOUT)
        handle.touch_window("2026-01-01", "2026-08-01", mgr.HOLDOUT)

    def test_non_holdout_purposes_may_repeat(self):
        first = register(self.path, experiment_id="E-A")
        first.touch_window("2014-09-21", "2024-09-01", "training")

        second = register(self.path, experiment_id="E-B")
        second.touch_window("2014-09-21", "2024-09-01", "training")


# ==============================
# THE PLATFORM KPI
# ==============================

class Kpi(ManagerTestCase):

    def _reject_at(self, experiment_id, stage):
        handle = register(self.path, experiment_id=experiment_id)

        for earlier in stages.ORDER:
            if earlier > stage:
                break
            metrics = dict(PASSING[earlier])
            if earlier == stage:
                metrics[sorted(metrics)[0]] = False
            handle.run_stage(earlier, metrics)

        handle.decide(mgr.REJECT, "gate failed at stage %d" % stage, "tester")

    def test_undefined_rather_than_perfect_with_no_rejections(self):
        report = mgr.kpi(path=self.path)
        self.assertEqual(report["rejections"], 0)
        self.assertIsNone(report["rate"])
        self.assertIsNone(report["meets_target"])

    def test_all_cheap_rejections_meet_the_target(self):
        self._reject_at("E-1", stages.DATA_INTEGRITY)
        self._reject_at("E-2", stages.ECONOMIC_SIGNAL)
        self._reject_at("E-3", stages.EXECUTION_REALISM)

        report = mgr.kpi(path=self.path)

        self.assertEqual(report["rate"], 1.0)
        self.assertTrue(report["meets_target"])

    def test_phase_one_pattern_misses_the_target(self):
        """Every rejection at stage 4+ is a backtester, not a platform."""

        self._reject_at("E-1", stages.ROBUSTNESS)
        self._reject_at("E-2", stages.STATISTICAL)
        self._reject_at("E-3", stages.DATA_INTEGRITY)

        report = mgr.kpi(path=self.path)

        self.assertAlmostEqual(report["rate"], 1 / 3.0, places=4)
        self.assertFalse(report["meets_target"])

    def test_registry_lists_every_experiment(self):
        register(self.path, experiment_id="E-1")
        register(self.path, experiment_id="E-2")

        self.assertEqual(len(mgr.registry(path=self.path)), 2)


class RecordFile(ManagerTestCase):

    def test_record_is_json_serialisable(self):
        handle = register(self.path)
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]),
                         detail={"rows": 66623})

        payload = json.dumps(handle.record(), default=str)

        self.assertIn("66623", payload)

    def test_written_record_carries_provenance(self):
        handle = register(self.path)
        handle.run_stage(stages.DATA_INTEGRITY,
                         dict(PASSING[stages.DATA_INTEGRITY]))

        path = handle.write_record(directory=self.dir)

        with open(path, encoding="utf-8") as file_handle:
            written = json.load(file_handle)

        self.assertIn("code_hash", written["provenance"])
        self.assertIn("dataset_snapshot", written["provenance"])


if __name__ == "__main__":
    unittest.main()
