"""Phase 11: the promotion negative controls, kept permanently in CI.

WHY THESE LIVE HERE AND NOT ONLY IN A SCRIPT
----------------------------------------------
`examples/verify_universe.py` demonstrates the promotion refusal, and a
demonstration is something somebody runs. A control nobody runs is a control
nobody notices breaking, and the whole point of a promotion gate is that it
still works on the day it matters -- which will be a day when someone very much
wants it to say yes.

So each precondition gets a test that makes evidence VALID first and then
destroys it, and asserts the refusal. If a future change makes promotion
easier, one of these goes red.

The positive case is here too. A gate that refuses everything is
indistinguishable from a gate that is broken, and only the pair together shows
the control discriminates.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sirat.experiments import ledger, provenance, stages
from sirat.experiments import manager as mgr
from sirat.experiments import universe as universe_module

from tests.test_experiment_manager import _tiny_market, register


class PromotionControl(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_promo_")
        self.path = os.path.join(self.dir, "research.db")
        self.market = _tiny_market(self.dir)

        self.saved = (provenance.git_commit, provenance.git_dirty)

        # A clean committed tree, simulated, so these tests measure the
        # EVIDENCE preconditions rather than the developer's git state.
        provenance.git_commit = lambda: "0" * 40
        provenance.git_dirty = lambda: False

    def tearDown(self):
        provenance.git_commit, provenance.git_dirty = self.saved

    def ready(self, experiment_id="E-PROMO"):
        """An experiment with valid evidence and all six stages passed."""

        snapshot = universe_module.capture(market_db=self.market,
                                           path=self.path)

        handle = register(self.path, experiment_id=experiment_id,
                          universe_snapshot=snapshot["snapshot_id"])

        for stage in stages.ORDER:
            handle.run_stage(
                stage, {c: True for c in stages.REQUIRED_CHECKS[stage]},
                detail={"note": "promotion control test"})

        return handle, snapshot

    def skip_without_dataset(self, handle):
        fingerprint = handle.record()["provenance"]["dataset_snapshot"]
        if fingerprint.startswith(provenance.UNAVAILABLE):
            self.skipTest("no market database on this machine, so the dataset "
                          "precondition cannot be satisfied")

    # ---- the positive case ------------------------------------------------

    def test_valid_evidence_promotes(self):
        """Without this, every refusal below could be a broken gate."""

        handle, _ = self.ready()

        self.skip_without_dataset(handle)

        out = handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                            regression_check=lambda: None)

        self.assertEqual(out["decision"], mgr.PROMOTE)

    # ---- the five refusals ------------------------------------------------

    def test_destroying_the_snapshot_after_the_fact_blocks_promotion(self):
        """Evidence valid at registration, destroyed before promotion.

        The exact scenario Phase 5 requires: a citation that was good when it
        was made and is not good any more.
        """

        handle, snapshot = self.ready()

        self.skip_without_dataset(handle)

        conn = sqlite3.connect(self.path)
        try:
            conn.execute("UPDATE universe_snapshots SET payload = '[]' "
                         "WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(mgr.EvidenceMissing) as caught:
            handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                          regression_check=lambda: None)

        self.assertIn("no longer restores", str(caught.exception))

    def test_a_missing_snapshot_blocks_promotion(self):
        handle = register(self.path, experiment_id="E-NOSNAP",
                          universe_snapshot=None)

        for stage in stages.ORDER:
            handle.run_stage(
                stage, {c: True for c in stages.REQUIRED_CHECKS[stage]})

        with self.assertRaises(mgr.EvidenceMissing):
            handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                          regression_check=lambda: None)

    def test_an_unidentified_dataset_blocks_promotion(self):
        saved = provenance.dataset_snapshot

        provenance.dataset_snapshot = lambda: provenance.UNAVAILABLE + ":test"

        try:
            handle, _ = self.ready("E-NODATA")
        finally:
            provenance.dataset_snapshot = saved

        with self.assertRaises(mgr.EvidenceMissing) as caught:
            handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                          regression_check=lambda: None)

        self.assertIn("dataset snapshot", str(caught.exception))

    def test_a_red_regression_suite_blocks_promotion(self):
        handle, _ = self.ready("E-REDCI")

        self.skip_without_dataset(handle)

        def broken():
            raise AssertionError("queue_ratio measured 1.01, expected 3.03")

        with self.assertRaises(mgr.RegressionFailing) as caught:
            handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                          regression_check=broken)

        self.assertIn("queue_ratio", str(caught.exception))

    def test_an_unreproducible_result_blocks_promotion_permanently(self):
        """And the message must not tell the reader to commit and retry."""

        provenance.git_commit = lambda: provenance.UNAVAILABLE
        provenance.git_dirty = lambda: None

        handle, _ = self.ready("E-NOGIT")

        provenance.git_commit = lambda: "0" * 40
        provenance.git_dirty = lambda: False

        with self.assertRaises(mgr.NotReproducible) as caught:
            handle.decide(mgr.PROMOTE, "all six stages passed", "tester",
                          regression_check=lambda: None)

        message = str(caught.exception)

        self.assertIn("no commit was recorded", message)
        self.assertIn("cannot be repaired after the fact", message)

    def test_an_incomplete_stage_record_blocks_promotion(self):
        snapshot = universe_module.capture(market_db=self.market,
                                           path=self.path)

        handle = register(self.path, experiment_id="E-PARTIAL",
                          universe_snapshot=snapshot["snapshot_id"])

        for stage in (stages.DATA_INTEGRITY, stages.ECONOMIC_SIGNAL):
            handle.run_stage(
                stage, {c: True for c in stages.REQUIRED_CHECKS[stage]})

        with self.assertRaises(mgr.ValidationMissing):
            handle.decide(mgr.PROMOTE, "good enough", "tester",
                          regression_check=lambda: None)

    # ---- the ledger records the refusal path too --------------------------

    def test_a_rejected_experiment_cannot_later_be_promoted(self):
        handle, _ = self.ready("E-CLOSED")

        handle.decide(mgr.ARCHIVE, "archived pending review", "tester")

        with self.assertRaises(mgr.ExperimentClosed):
            handle.decide(mgr.PROMOTE, "on reflection", "tester",
                          regression_check=lambda: None)

    def test_every_decision_lands_in_the_ledger_with_its_chain(self):
        handle, _ = self.ready("E-LEDGER")

        handle.decide(mgr.ARCHIVE, "archived", "tester")

        chain = ledger.verify(self.path)

        self.assertTrue(chain["intact"])
        self.assertEqual(chain["entries"], 1)


if __name__ == "__main__":
    unittest.main()
