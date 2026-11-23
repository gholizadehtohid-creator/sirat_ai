"""The retroactive registration of E26-E33 (2026-11-23).

The script writes into an append-only, hash-chained record, so what is tested
is what it must never do: grant a check nobody ran, claim the reservation,
record a verdict the result file does not state, or write the same experiment
twice. Every test runs against a temporary research database; the live one is
read-only to the suite (tests/__init__.py) and is never opened here.
"""

import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

from sirat.experiments import ledger, stages, store                 # noqa: E402
from sirat.experiments import manager as mgr                        # noqa: E402


def _load_script():
    path = os.path.join(ROOT, "examples", "register_retroactive_e26_e33.py")
    spec = importlib.util.spec_from_file_location("register_retroactive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


retro = _load_script()


def _reserve(path):
    mgr.Manager.reserve_window(
        retro.RESERVATION_ID, "2026-08-19", "2027-08-19", "holdout",
        reserved_by="tester", reason="the only unspent window", path=path)


def _windows(path, experiment_id):
    conn = store.connect(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM windows_touched WHERE experiment_id = ?",
            (experiment_id,))]
    finally:
        conn.close()


class TestRetroactiveRegistration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="sirat-retro-test-")
        cls.path = os.path.join(cls.folder, "research.db")
        _reserve(cls.path)
        # check_git=False: the suite must not depend on git being on PATH.
        # The commit facts are checked against git by the script itself.
        cls.outcomes = retro.apply(cls.path, check_git=False)
        cls.ids = [r["experiment_id"] for r in retro.RECORDS]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_all_seven_are_in_the_record_and_the_chain_holds(self):
        self.assertEqual([o["experiment_id"] for o in self.outcomes], self.ids)
        self.assertEqual({o["action"] for o in self.outcomes}, {"registered"})

        chain = ledger.verify(self.path)

        self.assertTrue(chain["intact"])
        self.assertEqual(chain["entries"], 7)

    def test_the_decision_is_the_experiments_own_verdict_never_promote(self):
        decisions = {o["experiment_id"]: o["decision"] for o in self.outcomes}

        for experiment_id, decision in decisions.items():
            self.assertNotEqual(decision, mgr.PROMOTE)

            key = experiment_id.split("-")[0]

            # Only the two whose own test accepted a hypothesis are archived.
            self.assertEqual(decision, mgr.ARCHIVE if key in ("E27", "E28")
                             else mgr.REJECT, experiment_id)

    def test_no_check_is_granted_in_retrospect(self):
        for experiment_id in self.ids:
            handle = mgr.Manager.load(experiment_id, path=self.path)
            results = handle.stage_results()

            self.assertEqual([r["stage"] for r in results],
                             [stages.DATA_INTEGRITY])
            self.assertEqual(results[0]["status"], stages.FAIL)

            metrics = json.loads(results[0]["metrics"])

            self.assertEqual(metrics["measured_by"], "caller")
            self.assertEqual(
                set(metrics["checks"]),
                set(stages.REQUIRED_CHECKS[stages.DATA_INTEGRITY]))
            self.assertFalse(any(metrics["checks"].values()), experiment_id)

            for check in stages.REQUIRED_CHECKS[stages.DATA_INTEGRITY]:
                self.assertIn("NOT RUN", metrics["detail"][check]["basis"])

    def test_the_reservation_is_read_on_the_record_and_never_claimed(self):
        held = [r for r in store.reservations(path=self.path)
                if r["reservation_id"] == retro.RESERVATION_ID][0]

        self.assertIsNone(held["claimed_by"])

        for experiment_id in self.ids:
            rows = _windows(self.path, experiment_id)
            purposes = {r["purpose"] for r in rows}

            self.assertNotIn(mgr.HOLDOUT, purposes)
            self.assertIn(retro.PURPOSE_EVALUATION, purposes)

            inside = [r for r in rows if r["purpose"] == retro.PURPOSE_INSIDE]

            # Every one of the seven read past 2026-08-19.
            self.assertTrue(inside, experiment_id)

            for row in inside:
                self.assertGreaterEqual(row["window_start"], "2026-08-19")
                self.assertLessEqual(row["window_end"], "2027-08-19")

    def test_every_entry_says_it_was_registered_after_the_fact(self):
        for experiment_id in self.ids:
            record = mgr.Manager.load(experiment_id, path=self.path).record()

            for text in (record["hypothesis"], record["researcher"],
                         record["notes"], record["decision"]["rationale"],
                         record["decision"]["decided_by"]):
                self.assertIn(retro.REGISTERED_ON, text, experiment_id)

            self.assertIn("RETROACTIVE", record["notes"])
            self.assertIn("RETROACTIVE", record["decision"]["rationale"])

            parameters = record["preregistration"]["parameters"]

            self.assertTrue(parameters["retroactive_record"])
            self.assertFalse(parameters["read_inside_reservation"]["claimed"])

            # No universe snapshot existed for these runs; none is granted.
            self.assertEqual(record["provenance"]["universe_snapshot"],
                             "UNAVAILABLE")

    def test_the_frozen_record_carries_the_original_file_whole(self):
        for record in retro.RECORDS:
            handle = mgr.Manager.load(record["experiment_id"], path=self.path)
            parameters = handle.parameters()

            with open(os.path.join(ROOT, retro.prereg_file(record["key"])),
                      encoding="utf-8") as fh:
                original = json.load(fh)

            self.assertEqual(parameters["original_preregistration"], original)
            self.assertEqual(parameters["preregistration_commit"],
                             record["prereg_commit"])
            self.assertEqual(parameters["result_commit"],
                             record["result_commit"])

    def test_a_second_run_writes_nothing(self):
        again = retro.apply(self.path, check_git=False)

        self.assertEqual({o["action"] for o in again}, {"skipped"})
        self.assertEqual(ledger.verify(self.path)["entries"], 7)

        for experiment_id in self.ids:
            rows = _windows(self.path, experiment_id)
            keys = [(r["window_start"], r["window_end"], r["purpose"])
                    for r in rows]

            self.assertEqual(len(keys), len(set(keys)), experiment_id)


class TestRefusals(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sirat-retro-test-")
        self.path = os.path.join(self.folder, "research.db")
        _reserve(self.path)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _results(self):
        return {r["key"]: retro._load(retro.result_file(r["key"]))
                for r in retro.RECORDS}

    def test_a_verdict_the_file_does_not_state_is_refused(self):
        """E30's file says REJECT. If it said anything else, recording REJECT
        would be this script's verdict, not the experiment's."""

        results = self._results()
        results["E30"] = copy.deepcopy(results["E30"])
        results["E30"]["decision"] = "ACCEPT"

        record = [r for r in retro.RECORDS if r["key"] == "E30"][0]

        with self.assertRaises(retro.Refused):
            retro.plan(record, results, retro.RESERVATION_WINDOW)

    def test_an_accept_is_never_recorded_as_a_reject(self):
        results = self._results()
        record = dict([r for r in retro.RECORDS if r["key"] == "E27"][0],
                      decision=mgr.REJECT)

        with self.assertRaises(retro.Refused):
            retro.plan(record, results, retro.RESERVATION_WINDOW)

    def test_a_holdout_purpose_is_refused_before_anything_is_written(self):
        item = retro.plans(self.path, retro.RECORDS[:1])[0]
        item["windows"].append(("2026-08-19", "2026-11-04", mgr.HOLDOUT))

        with self.assertRaises(retro.Refused):
            retro.apply_one(item, path=self.path)

        with self.assertRaises(mgr.UnknownExperiment):
            mgr.Manager.load(item["register"]["experiment_id"],
                             path=self.path)

    def test_an_interrupted_run_is_completed_not_doubled(self):
        item = retro.plans(self.path, retro.RECORDS[:1])[0]
        experiment_id = item["register"]["experiment_id"]

        # Registered, one window recorded, then the process died.
        handle = mgr.Manager.register(path=self.path, **item["register"])
        handle.touch_window(*item["windows"][0])

        outcome = retro.apply(self.path, check_git=False,
                              records=retro.RECORDS[:1])[0]

        self.assertEqual(outcome["action"], "completed")
        self.assertEqual(len(_windows(self.path, experiment_id)),
                         len(item["windows"]))
        self.assertEqual(ledger.verify(self.path)["entries"], 1)

    def test_a_later_claim_is_still_possible(self):
        """The informational rows are a trace, not a spend. They do not claim
        the reservation -- and, stated because it is a limit: they do not
        stop a later claim that starts after the last recorded read."""

        retro.apply(self.path, check_git=False, records=retro.RECORDS[:1])

        handle = mgr.Manager.register(
            experiment_id="E-LATER", hypothesis="a successor that waits",
            strategy_version="v", researcher="tester", notes="n",
            expected_failure=["it fails"], acceptance_criteria={"a": "b"},
            preregistration={"parameters": {"p": 1},
                             "validation_gates": {"g": "c"},
                             "expected_metrics": {"m": 1},
                             "rejection_thresholds": {"t": 1}, "seed": 1},
            path=self.path)

        handle.touch_window("2026-12-01", "2027-08-19", mgr.HOLDOUT,
                            claim=retro.RESERVATION_ID)

        held = [r for r in store.reservations(path=self.path)
                if r["reservation_id"] == retro.RESERVATION_ID][0]

        self.assertEqual(held["claimed_by"], "E-LATER")

    def test_the_windows_are_the_ones_the_result_files_state(self):
        results = self._results()

        self.assertEqual(
            [w[:2] for w in retro.windows_of("E27", results["E27"], results)],
            [("2009-01-03", "2023-02-28"), ("2023-03-01", "2026-11-04")])
        self.assertEqual(
            retro.inside_reservation(
                retro.windows_of("E27", results["E27"], results),
                retro.RESERVATION_WINDOW),
            [("2026-08-19", "2026-11-04")])
        # E31 stores no window; it is E30's, and the role says so.
        window = retro.windows_of("E31", results["E31"], results)[0]

        self.assertEqual(list(window[:2]), results["E30"]["window"])
        self.assertIn("E30", window[2])


if __name__ == "__main__":
    unittest.main()
