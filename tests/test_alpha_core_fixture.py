"""The Alpha Core regression fixture, run as a test on every build.

If the platform cannot rediscover Alpha Core's four failures, the platform is
invalid. A governance layer that cannot re-derive its own known negative has no
standing to judge a new idea, and every green build after that point means
nothing.

WHAT MAKES THIS ADVERSARIAL RATHER THAN NOSTALGIC
--------------------------------------------------
The tolerances are two-sided. A queue ratio of 1.2x fails exactly as hard as one
of 5.0x, and the failure message says which direction it moved, because "Alpha
Core got better" is the reading that most needs to be unmissable. Every time
this project has seen a rejected result improve, the cause was a defect: a
stale-bar trap, a `shift(-h)` across a suspension, a fill model that silently
refilled unbuyable slots.

TIER 1 ONLY, HERE
-----------------
These tests read the frozen panel and recompute every anchor through the live
analytics code -- seconds, no database. Tier 2 rebuilds that panel from
sirat_market.db and is run by `ci.py --full`, not by the unit suite: a test that
needs 424 MB on disk stops running the moment the database is missing, which is
the same standard the rest of this suite already holds itself to.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sirat.fixtures import alpha_core
from sirat.fixtures import build_panel


PANEL_AVAILABLE = os.path.exists(build_panel.PANEL_PATH)

SKIP = ("the frozen fixture panel is absent; rebuild it with "
        "`python -m sirat.fixtures.build_panel`")


@unittest.skipUnless(PANEL_AVAILABLE, SKIP)
class Fixture(unittest.TestCase):
    """One test per anchor, so a failure names the failure that stopped
    reproducing rather than saying 'the fixture is red'."""

    @classmethod
    def setUpClass(cls):
        cls.panel = build_panel.load()
        cls.report = alpha_core.run(panel=cls.panel, verbose=False)
        cls.by_check = {c["check"]: c for c in cls.report["checks"]}

    def assertAnchor(self, name):
        check = self.by_check[name]
        self.assertTrue(
            check["passed"],
            "%s: measured %s against %s%s"
            % (name, check.get("measured"), check.get("expected"),
               " -- " + check["direction"] if check.get("direction") else ""))

    # ---- the frozen strategy ---------------------------------------------

    def test_alpha_core_has_not_been_edited(self):
        """Alpha Core is frozen. It is a fixture, not a strategy to improve."""

        self.assertAnchor("alpha_core_frozen")

    # ---- 1. queue artifact -----------------------------------------------

    def test_queue_ratio_reproduces(self):
        """E14: the top decile is 3.03x more likely to be limit-locked."""

        self.assertAnchor("queue_ratio")

    # ---- 2. cost failure --------------------------------------------------

    def test_the_cost_failure_direction_holds(self):
        """E13: a 0.55% edge against a 1.04% round trip."""

        self.assertAnchor("cost_failure_direction")

    def test_h1_excess_reproduces(self):
        self.assertAnchor("h1_excess")

    # ---- 3. holding period ------------------------------------------------

    def test_requested_return_reproduces(self):
        """E15: the unfiltered book asks for +3.17% at h=20."""

        self.assertAnchor("requested_h20")

    def test_realised_return_reproduces(self):
        """E15: it receives +0.59%. 81% of the alpha is lost to execution."""

        self.assertAnchor("realised_h20")

    def test_the_execution_shortfall_is_large_and_positive(self):
        holding = self.report["measurements"]["holding_period"]

        self.assertGreater(holding["shortfall_pct"], 2.0)
        self.assertLess(holding["shortfall_share"], 0.30)

    # ---- 4. tail ----------------------------------------------------------

    def test_alpha_core_sits_in_the_tail_of_its_own_null(self):
        """E11: 3.3rd percentile against 30 random-ranking controls."""

        self.assertAnchor("tail_percentile")

    # ---- 5. IC curve ------------------------------------------------------

    def test_ic_curve_reproduces_at_every_horizon(self):
        for horizon in sorted(alpha_core.IC_CURVE):
            self.assertAnchor("ic@h%d" % horizon)

    def test_the_signal_decays_toward_the_traded_horizon(self):
        """The decay is the finding, not a detail of the curve."""

        curve = self.report["measurements"]["ic_curve"]

        self.assertGreater(curve[1], curve[5])
        self.assertGreater(curve[5], curve[10])
        self.assertGreater(curve[10], curve[20])

    # ---- the fixture as a whole ------------------------------------------

    def test_every_anchor_reproduces(self):
        self.assertTrue(self.report["passed"],
                        "anchors that stopped reproducing: %s"
                        % ", ".join(self.report["failed"]))

    def test_assert_passes_does_not_raise(self):
        alpha_core.assert_passes(verbose=False)


@unittest.skipUnless(PANEL_AVAILABLE, SKIP)
class Adversarial(unittest.TestCase):
    """The fixture must fail when the failures stop reproducing.

    A regression fixture that cannot go red is decoration. These tests corrupt
    the panel in exactly the ways a defect would and check that each anchor
    notices -- including, and especially, the corruptions that make Alpha Core
    look BETTER.
    """

    def setUp(self):
        self.panel = build_panel.load()

    def failed(self, frame):
        report = alpha_core.run(panel=frame, verbose=False)
        return set(report["failed"])

    def test_a_queue_detector_that_stopped_detecting_fails_the_build(self):
        """The favourable-direction case. This is the whole point.

        If something stops flagging limit-locked bars, the queue ratio falls
        toward 1.0 and Alpha Core looks like a clean signal. The correct
        response is a red build, not relief.
        """

        frame = self.panel.copy()
        frame["next_locked_up"] = False

        failures = self.failed(frame)

        self.assertIn("queue_ratio", failures)

    def test_refilling_unbuyable_slots_fails_the_build(self):
        """The fill-model defect that would erase the execution failure."""

        frame = self.panel.copy()
        frame["next_unbuyable"] = False

        failures = self.failed(frame)

        self.assertIn("realised_h20", failures)

    def test_an_inflated_signal_fails_the_build(self):
        """Alpha Core looking stronger is a failure, not an improvement."""

        frame = self.panel.copy()

        for horizon in (1, 5, 10, 20):
            column = "fwd_%d" % horizon
            # Push returns toward the score: a leak, in the shape a leak takes.
            frame[column] = frame[column] + (frame["alpha"] - 50.0) / 10.0

        failures = self.failed(frame)

        self.assertTrue(failures, "an inflated signal produced a green build")
        self.assertIn("ic@h20", failures)

    def test_editing_alpha_core_fails_the_build(self):
        import hashlib
        import json
        import tempfile

        original = alpha_core._expected()

        tampered = dict(original, alpha_core_sha256=hashlib.sha256(
            b"a different alpha core").hexdigest())

        path = os.path.join(tempfile.mkdtemp(prefix="sirat_fix_"),
                            "expected.json")

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(tampered, handle)

        saved = alpha_core.EXPECTED_PATH
        try:
            alpha_core.EXPECTED_PATH = path
            self.assertFalse(alpha_core.check_frozen()["passed"])
        finally:
            alpha_core.EXPECTED_PATH = saved

    def test_a_favourable_move_is_reported_as_favourable(self):
        """The failure message has to say which way it moved."""

        frame = self.panel.copy()
        frame["next_locked_up"] = False

        report = alpha_core.run(panel=frame, verbose=False)

        check = [c for c in report["checks"] if c["check"] == "queue_ratio"][0]

        self.assertIn("FAVOURABLY", check["direction"])


class PanelIntegrity(unittest.TestCase):

    @unittest.skipUnless(PANEL_AVAILABLE, SKIP)
    def test_the_frozen_panel_matches_its_recorded_hash(self):
        frame = build_panel.load()

        expected = alpha_core._expected()

        self.assertEqual(build_panel.panel_hash(frame),
                         expected["panel_sha256"],
                         "the frozen panel file has been altered since it was "
                         "built from the database")

    @unittest.skipUnless(PANEL_AVAILABLE, SKIP)
    def test_the_panel_covers_the_recorded_window(self):
        frame = build_panel.load()

        self.assertEqual(frame["date"].nunique(), 120)
        self.assertGreaterEqual(str(frame["date"].min().date()), "2014-09-21")
        self.assertLessEqual(str(frame["date"].max().date()), "2024-09-01")

    @unittest.skipUnless(PANEL_AVAILABLE, SKIP)
    def test_the_panel_carries_every_column_the_anchors_need(self):
        frame = build_panel.load()

        for column in build_panel.KEEP:
            self.assertIn(column, frame.columns)


if __name__ == "__main__":
    unittest.main()
