"""P3 reporting repair: the counters must reach the report under their own names.

The defect this guards against is not a crash. It is silence: `profile()` asked
for `buy_queue_blocked` while `execution.py` writes `blocked_buy_queue`, so four
counters that were measured correctly arrived in the artifact as null and were
read as "this never happened".

These tests assert the mapping, the two-layer separation, and the one property
that matters most -- that an absent counter stays None instead of becoming a
zero, because zero is a measurement and None is the absence of one.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import experiment_execution_realism as exp


class FakeResult(object):
    """The three attributes profile() reads, and nothing else."""

    def __init__(self, diagnostics):
        self.metrics = {"exposure": {}, "drawdown": {}, "trades": {}}
        self.diagnostics = diagnostics
        self.trades = []
        self.curve = pd.DataFrame()
        self.available = True

        class Cfg(object):
            initial_capital = 1000.0

        self.cfg = Cfg()


# What backtesting/execution.py and backtesting/engine.py actually write.
REAL_DIAGNOSTICS = {
    # Layer A -- filler.stats, merged by engine.py:400
    "blocked_buy_queue": 11,
    "blocked_sell_queue": 5,
    "capped_by_participation": 7,
    "expired": 3,
    "filled": 800,
    "partial": 200,
    "queue_blocked_exit": 33,
    # Layer B -- portfolio construction
    "blocked_by": {"portfolio_full": 240, "cash_floor": 172},
    "wanted_positions": 448,
    "opened_positions": 448,
    "sized_to_zero": 2,
    "ranked_out": 90,
    "rotated_out": 61,
    "empty_rebalances": 4,
    "signals_total": 5000,
    # attribution the engine already builds
    "cash_analysis": {"basket_too_small": 0.4, "portfolio_caps": 0.6},
    "entry_rejections": [{"reason": "دروازه", "count": 12, "share": 1.0}],
    "buy_orders_raised": 448,
    "buy_orders_expired": 30,
}


class LayerOwnershipTest(unittest.TestCase):

    def test_the_two_registries_are_disjoint(self):
        self.assertTrue(exp.assert_layers_disjoint())

    def test_no_report_name_is_claimed_by_both_layers(self):
        self.assertFalse(
            set(exp.LAYER_A_EXECUTION) & set(exp.LAYER_B_PORTFOLIO))

    def test_no_source_key_is_claimed_by_both_layers(self):
        self.assertFalse(set(exp.LAYER_A_EXECUTION.values())
                         & set(exp.LAYER_B_PORTFOLIO.values()))

    def test_layer_a_maps_to_the_names_execution_actually_writes(self):
        """The exact defect: report name != source key, and only one is real."""

        self.assertEqual(exp.LAYER_A_EXECUTION["buy_queue_blocked"],
                         "blocked_buy_queue")
        self.assertEqual(exp.LAYER_A_EXECUTION["sell_queue_blocked"],
                         "blocked_sell_queue")
        self.assertEqual(exp.LAYER_A_EXECUTION["participation_capped"],
                         "capped_by_participation")


class MappingTest(unittest.TestCase):

    def setUp(self):
        self.row = exp.profile(FakeResult(dict(REAL_DIAGNOSTICS)), "test")

    def test_the_four_previously_null_counters_now_carry_their_values(self):
        self.assertEqual(self.row["buy_queue_blocked"], 11)
        self.assertEqual(self.row["sell_queue_blocked"], 5)
        self.assertEqual(self.row["participation_capped"], 7)
        self.assertEqual(self.row["queue_blocked_exit"], 33)

    def test_the_engine_attribution_is_passed_through_verbatim(self):
        self.assertEqual(self.row["cash_analysis"],
                         REAL_DIAGNOSTICS["cash_analysis"])
        self.assertEqual(self.row["entry_rejections"],
                         REAL_DIAGNOSTICS["entry_rejections"])

    def test_both_layer_blocks_are_present_and_separate(self):
        self.assertIn("execution_layer", self.row)
        self.assertIn("portfolio_layer", self.row)

        self.assertEqual(self.row["execution_layer"]["buy_queue_blocked"], 11)
        self.assertEqual(self.row["portfolio_layer"]["wanted_positions"], 448)

        self.assertFalse(set(self.row["execution_layer"])
                         & set(self.row["portfolio_layer"]))

    def test_the_artifact_carries_the_do_not_sum_rule(self):
        note = self.row["counter_layers"]
        self.assertIn("MUST NOT be summed", note["note"])
        self.assertTrue(note["disjoint_verified"])

    def test_existing_fields_are_unchanged(self):
        """The repair adds and corrects; it does not move anything else."""

        self.assertEqual(self.row["filled"], 800)
        self.assertEqual(self.row["partial_fills"], 200)
        self.assertEqual(self.row["buy_orders_raised"], 448)
        self.assertEqual(self.row["buy_orders_expired"], 30)
        self.assertEqual(self.row["fill_rate_pct"],
                         round((448 - 30) / 448.0 * 100, 2))
        self.assertEqual(self.row["partial_rate_pct"],
                         round(200 / 1000.0 * 100, 2))


class AbsenceIsNotZeroTest(unittest.TestCase):
    """The property that made the original defect invisible."""

    def test_a_counter_nobody_wrote_stays_none(self):
        row = exp.profile(FakeResult({}), "empty")

        for key in ("buy_queue_blocked", "sell_queue_blocked",
                    "participation_capped"):
            self.assertIsNone(row[key],
                              "%s must be None when absent, not 0" % key)

    def test_a_counter_written_as_zero_reports_zero(self):
        row = exp.profile(FakeResult({"blocked_buy_queue": 0}), "zero")

        self.assertEqual(row["buy_queue_blocked"], 0)
        self.assertIsNotNone(row["buy_queue_blocked"])

    def test_missing_attribution_blocks_are_none_not_invented(self):
        row = exp.profile(FakeResult({}), "empty")

        self.assertIsNone(row["cash_analysis"])
        self.assertIsNone(row["entry_rejections"])


if __name__ == "__main__":
    unittest.main()
