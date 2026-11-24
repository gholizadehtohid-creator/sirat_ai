"""Tests for the asset allocation the panel leads with (allocation.py)."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import allocation                                               # noqa: E402


class ProfileTests(unittest.TestCase):

    def test_every_profile_is_fully_allocated(self):
        for profile in allocation.PROFILES:
            self.assertEqual(sum(profile["weights"].values()), 100,
                             profile["key"])

    def test_only_tested_arms_are_called_tested(self):
        """E28 accepted 70/30 gold, E27 accepted 100% equity; 50/50 fixed was
        never a hypothesis and must not borrow their standing."""

        tested = {p["key"] for p in allocation.PROFILES
                  if p["evidence"] == "tested"}
        self.assertEqual(tested, {"balanced", "growth"})

    def test_amounts_split_the_capital(self):
        saved = allocation.history, allocation.candidates
        allocation.history = lambda: None
        allocation.candidates = lambda limit=3: {}
        try:
            report = allocation.plan(equity=1_000_000_000)
        finally:
            allocation.history, allocation.candidates = saved

        for profile in report["profiles"]:
            self.assertAlmostEqual(sum(profile["amounts"].values()),
                                   1_000_000_000, delta=1)


class MixTests(unittest.TestCase):

    def test_a_flat_leg_and_a_rising_leg_rebalance_back_to_target(self):
        dates = pd.RangeIndex(allocation.REBALANCE_SESSIONS * 2)
        rising = pd.Series(0.01, index=dates)
        flat = pd.Series(0.0, index=dates)

        mixed = allocation._mix({"a": rising, "b": flat}, (0.5, 0.5))

        # First session: half the book earns 1%.
        self.assertAlmostEqual(mixed.iloc[0], 0.005)
        # Drift raises the rising leg's share, so before the rebalance the
        # book earns more than 0.5% a session ...
        before = mixed.iloc[allocation.REBALANCE_SESSIONS - 1]
        self.assertGreater(before, 0.005)
        # ... and right after it, back near half again, less the cost.
        after = mixed.iloc[allocation.REBALANCE_SESSIONS]
        self.assertLess(after, before)

    def test_stats_report_drawdown_as_a_loss(self):
        daily = pd.Series([0.1, -0.5, 0.2] + [0.0] * 300,
                          index=pd.date_range("2020-01-01", periods=303))
        stats = allocation._stats(daily)
        self.assertAlmostEqual(stats["max_drawdown_pct"], -50.0, places=1)




class TestRetestEvidence(unittest.TestCase):
    """E35's verdicts restate the evidence of the profiles it re-tested
    (2026-11-24) -- and never their weights."""

    def _doc(self, h1, h2):
        def t(p, verdict):
            return {"p": p}, {"holm_threshold": 0.0167, "null_rejected": verdict}
        tests = {"H1_gold_vs_real_bills": {"p": 0.041},
                 "H2_equity_vs_real_bills": {"p": 0.061},
                 "H3_three_part_vs_gold": {"p": 0.066},
                 "holm": {k: {"holm_threshold": 0.0167} for k in (
                     "H1_gold_vs_real_bills", "H2_equity_vs_real_bills",
                     "H3_three_part_vs_gold")}}
        book = {"cagr_pct": 50.0, "sharpe_annualised_vs_bills": 0.8,
                "max_drawdown_pct": -40.0}
        return {"window_months": ["2017-10", "2026-07"], "months": 106,
                "bills": {"cagr_pct": 25.5},
                "books": {k: dict(book) for k in ("equity_100", "eq70_gold30",
                                                  "eq70_bills30",
                                                  "eq70_gold15_bills15")},
                "tests": tests,
                "verdict": {"H1_gold_vs_real_bills": h1,
                            "H2_equity_vs_real_bills": h2,
                            "H3_three_part_vs_gold": "NOT ACCEPTED"}}

    def _write(self, doc):
        import json
        import tempfile
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        json.dump(doc, handle)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_not_accepted_makes_both_profiles_provisional(self):
        found = allocation.retest(self._write(self._doc("NOT ACCEPTED",
                                                        "NOT ACCEPTED")))
        profiles = {p["key"]: p for p in allocation._profiles_with_retest(found)}
        self.assertEqual(profiles["balanced"]["evidence"], "provisional")
        self.assertEqual(profiles["growth"]["evidence"], "provisional")
        self.assertIn("E35", profiles["balanced"]["evidence_text"])
        self.assertEqual(profiles["balanced"]["weights"],
                         {"equity": 70, "gold": 30, "fixed": 0})

    def test_accepted_keeps_tested(self):
        found = allocation.retest(self._write(self._doc("ACCEPT", "ACCEPT")))
        profiles = {p["key"]: p for p in allocation._profiles_with_retest(found)}
        self.assertEqual(profiles["balanced"]["evidence"], "tested")

    def test_no_result_file_changes_nothing(self):
        self.assertIsNone(allocation.retest(os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "no_such_file.json")))
        profiles = allocation._profiles_with_retest(None)
        self.assertEqual([p["evidence"] for p in profiles],
                         [p["evidence"] for p in allocation.PROFILES])


if __name__ == "__main__":
    unittest.main()
