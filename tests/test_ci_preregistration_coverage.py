"""The build must fail when an experiment sits outside the record.

WHY (2026-11-23): E26-E33 were preregistered as files, run, and never
registered with the Experiment Manager -- and `ci.py` stayed green for three
weeks, because it verified the ledger it had and never asked what was missing
from it. `ci.preregistration_coverage` asks. These tests pin the two halves:
the check finds a file with nothing behind it, and finding one turns the
governance stage red rather than adding one more warning nobody reads.

No research database is opened: the registered ids are passed in, and the
governance stage's collaborators are patched.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

import ci                                                           # noqa: E402


class TestPreregistrationCoverage(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sirat-ci-coverage-")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _write(self, name, payload):
        with open(os.path.join(self.folder, name), "w",
                  encoding="utf-8") as handle:
            if isinstance(payload, str):
                handle.write(payload)
            else:
                json.dump(payload, handle)

    def test_a_registered_experiment_is_covered(self):
        self._write("E90_preregistration.json", {"experiment_id": "E90-x"})

        report = ci.preregistration_coverage(self.folder, {"E90-x"})

        self.assertEqual(report, {"files": 1, "outside": []})

    def test_a_file_with_no_experiment_behind_it_is_reported(self):
        self._write("E90_preregistration.json", {"experiment_id": "E90-x"})
        self._write("E91_preregistration.json", {"experiment_id": "E91-y"})

        report = ci.preregistration_coverage(self.folder, {"E90-x"})

        self.assertEqual(report["files"], 2)
        self.assertEqual([o["file"] for o in report["outside"]],
                         ["E91_preregistration.json"])
        self.assertEqual(report["outside"][0]["experiment_id"], "E91-y")

    def test_the_id_must_match_whole_not_by_prefix(self):
        """E27's file is not covered by some other experiment that happens to
        start with E27."""

        self._write("E27_preregistration.json",
                    {"experiment_id": "E27-market-exposure-allocation"})

        report = ci.preregistration_coverage(self.folder, {"E27-other"})

        self.assertEqual(len(report["outside"]), 1)

    def test_a_file_that_cannot_be_judged_is_outside_not_ignored(self):
        self._write("E92_preregistration.json", "{ not json")
        self._write("E93_preregistration.json", {"question": "no id here"})
        self._write("E94_preregistration.json", "[1, 2, 3]")

        report = ci.preregistration_coverage(self.folder, {"E92", "E93"})

        self.assertEqual(report["files"], 3)
        self.assertEqual(len(report["outside"]), 3)
        self.assertTrue(all(o["experiment_id"] is None
                            for o in report["outside"]))

    def test_other_files_are_not_preregistrations(self):
        self._write("holdout_preregistration.json", {"anything": 1})
        self._write("E30_results.json", {"experiment_id": "E30-x"})
        self._write("experiment15_preregistration.json", {"anything": 1})

        report = ci.preregistration_coverage(self.folder, set())

        self.assertEqual(report, {"files": 0, "outside": []})

    def test_a_missing_directory_is_no_files_not_a_crash(self):
        report = ci.preregistration_coverage(
            os.path.join(self.folder, "absent"), set())

        self.assertEqual(report, {"files": 0, "outside": []})


class TestGovernanceStage(unittest.TestCase):
    """The stage's verdict, with everything it reads held still."""

    def _run(self, coverage, intact=True, incomplete=None):
        from sirat.experiments import ledger, manager, multiplicity, universe

        kpi = {"rejections": 0, "meets_target": None,
               "reproducibility": {"reproducible": True}}
        chain = {"entries": 3, "intact": intact, "broken_at": 2,
                 "experiment_id": "E-x", "reason": "altered"}
        quiet = {"n_trials": 3, "budgets": {},
                 "luck_hurdle_annual_sharpe": {"5.3y": 0.5, "15y": 0.3}}

        with mock.patch.object(manager, "kpi", return_value=kpi), \
                mock.patch.object(universe, "due",
                                  return_value={"due": False, "latest": "u"}), \
                mock.patch.object(ledger, "verify", return_value=chain), \
                mock.patch.object(ci, "preregistration_coverage",
                                  return_value=coverage), \
                mock.patch.object(multiplicity, "summary", return_value=quiet), \
                mock.patch.object(ci, "preregistration_completeness",
                                  return_value=incomplete or {}):
            return ci.run_governance()

    def test_green_when_every_preregistration_is_in_the_record(self):
        report = self._run({"files": 9, "outside": []})

        self.assertTrue(report["passed"])
        self.assertEqual(report["detail"], [])
        self.assertEqual(report["warnings"], [])

    def test_red_when_one_is_outside_and_the_file_is_named(self):
        report = self._run({"files": 9, "outside": [
            {"file": "E40_preregistration.json",
             "experiment_id": "E40-something-new",
             "reason": "no such experiment in the record"}]})

        self.assertFalse(report["passed"])
        self.assertIn("E40_preregistration.json", report["detail"][0])
        self.assertIn("E40_preregistration.json", report["warnings"][0])
        self.assertIn("1 OF 9", report["warnings"][0])

    def test_the_seven_that_predate_the_rule_warn_by_name_and_do_not_fail(self):
        # Their retroactive registration is the owner's step (2026-11-23):
        # named on every build, but only a NEW experiment outside the record
        # turns it red. The tuple was emptied on 2026-11-24 when E26-E33
        # were registered; the mechanism is still pinned on its seven names.
        seven = ("E26_preregistration.json", "E27_preregistration.json",
                 "E28_preregistration.json", "E30_preregistration.json",
                 "E31_preregistration.json", "E32_preregistration.json",
                 "E33_preregistration.json")
        patch = mock.patch.object(ci, "PENDING_RETROACTIVE", seven)
        patch.start()
        self.addCleanup(patch.stop)
        outside = [{"file": name, "experiment_id": name[:3],
                    "reason": "no such experiment in the record"}
                   for name in ci.PENDING_RETROACTIVE]
        report = self._run({"files": 9, "outside": outside})

        self.assertTrue(report["passed"])
        self.assertEqual(report["detail"], [])
        self.assertIn("7 OF 9", report["warnings"][0])
        self.assertIn("E27_preregistration.json", report["warnings"][1])
        self.assertIn("--apply", report["warnings"][1])

        mixed = self._run({"files": 10, "outside": outside + [
            {"file": "E40_preregistration.json", "experiment_id": "E40",
             "reason": "no such experiment in the record"}]})
        self.assertFalse(mixed["passed"])
        self.assertEqual(mixed["detail"], ["outside the record: E40_preregistration.json"])

    def test_a_broken_ledger_still_fails_on_its_own(self):
        report = self._run({"files": 9, "outside": []}, intact=False)

        self.assertFalse(report["passed"])
        self.assertIn("ledger broken", report["detail"][0])

    def test_the_summary_prints_the_count(self):
        import contextlib
        import io

        report = self._run({"files": 9, "outside": [
            {"file": "E27_preregistration.json", "experiment_id": "E27",
             "reason": "no such experiment in the record"}]})

        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            ci._print_governance(report)

        self.assertIn("1 OUTSIDE THE RECORD", out.getvalue())


class TestCompletenessRule(unittest.TestCase):
    """From E36 on, an incomplete preregistration turns the build red."""

    def _run(self, incomplete):
        return TestGovernanceStage._run(self, {"files": 9, "outside": []},
                                        incomplete=incomplete)

    def test_a_new_incomplete_preregistration_fails(self):
        report = self._run({"E36_preregistration.json": {
            "number": 36, "missing": ["the cost model"]}})
        self.assertFalse(report["passed"])
        self.assertIn("E36_preregistration.json lacks the cost model",
                      report["detail"][0])

    def test_older_files_are_reported_not_failed(self):
        report = self._run({"E26_preregistration.json": {
            "number": 26, "missing": ["family and multiple-testing correction"]}})
        self.assertTrue(report["passed"])

    def test_the_live_e35_file_is_complete(self):
        from sirat.experiments import multiplicity
        with open(os.path.join(ROOT, "backtest_output",
                               "E35_preregistration.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(multiplicity.prereg_completeness(json.load(fh)), [])


if __name__ == "__main__":
    unittest.main()
