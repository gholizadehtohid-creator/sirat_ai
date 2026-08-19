"""Percentile blending of two independent signals.

The failure mode this guards against is a name winning the ranking on a
partial reading -- absent data scoring like average data, which puts a symbol
we know nothing about ahead of one we do.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_portfolio as bp
import config


def reading(flow=None, headroom=None, technical=80, sector="ص", close=1000.0):
    return {
        "symbol": "SYM",
        "sector": sector,
        "technical": technical,
        "flow": 90,
        "relative_flow": flow,
        "headroom": headroom,
        "close": close,
        "atr": 30.0,
    }


class TestPercentiles(unittest.TestCase):

    def test_ordering_is_preserved(self):
        readings = {
            "a": reading(flow=-1.0),
            "b": reading(flow=0.0),
            "c": reading(flow=2.0),
        }

        ranks = bp._percentiles(readings, ("relative_flow",))

        self.assertLess(ranks["a"]["relative_flow"], ranks["b"]["relative_flow"])
        self.assertLess(ranks["b"]["relative_flow"], ranks["c"]["relative_flow"])

    def test_the_range_is_zero_to_one_hundred(self):
        readings = {str(i): reading(flow=float(i)) for i in range(10)}

        ranks = bp._percentiles(readings, ("relative_flow",))

        values = [r["relative_flow"] for r in ranks.values()]

        self.assertAlmostEqual(min(values), 0.0, places=6)
        self.assertAlmostEqual(max(values), 100.0, places=6)

    def test_ties_share_a_rank(self):
        readings = {
            "a": reading(flow=1.0),
            "b": reading(flow=1.0),
            "c": reading(flow=5.0),
        }

        ranks = bp._percentiles(readings, ("relative_flow",))

        self.assertEqual(ranks["a"]["relative_flow"], ranks["b"]["relative_flow"])
        self.assertGreater(ranks["c"]["relative_flow"], ranks["a"]["relative_flow"])

    def test_a_missing_value_gets_no_rank_rather_than_a_middling_one(self):
        readings = {
            "a": reading(flow=-2.0),
            "b": reading(flow=None),
            "c": reading(flow=3.0),
        }

        ranks = bp._percentiles(readings, ("relative_flow",))

        self.assertIsNone(ranks["b"].get("relative_flow"))

    def test_fields_are_ranked_independently(self):
        readings = {
            "a": reading(flow=3.0, headroom=1.0),
            "b": reading(flow=-1.0, headroom=50.0),
        }

        ranks = bp._percentiles(readings, ("relative_flow", "headroom"))

        self.assertGreater(ranks["a"]["relative_flow"], ranks["b"]["relative_flow"])
        self.assertLess(ranks["a"]["headroom"], ranks["b"]["headroom"])

    def test_a_single_candidate_produces_no_ranks(self):
        ranks = bp._percentiles({"a": reading(flow=1.0)}, ("relative_flow",))

        self.assertEqual(ranks["a"], {})


class TestCombinedSelection(unittest.TestCase):

    def setUp(self):
        self._headroom = config.MIN_HEADROOM_PCT
        config.MIN_HEADROOM_PCT = None

    def tearDown(self):
        config.MIN_HEADROOM_PCT = self._headroom

    def test_a_name_strong_on_both_beats_one_strong_on_either(self):
        readings = {
            "both":  reading(flow=3.0, headroom=40.0, sector="a"),
            "flow":  reading(flow=3.0, headroom=1.0, sector="b"),
            "room":  reading(flow=-2.0, headroom=40.0, sector="c"),
            "worst": reading(flow=-2.0, headroom=1.0, sector="d"),
        }

        picked = bp.select(readings, size=4, max_per_sector=1, mode="combined")

        self.assertEqual([code for code, _ in picked][0], "both")
        self.assertEqual([code for code, _ in picked][-1], "worst")

    def test_a_candidate_missing_either_input_is_skipped(self):
        readings = {
            "full":    reading(flow=1.0, headroom=20.0, sector="a"),
            "no_flow": reading(flow=None, headroom=90.0, sector="b"),
            "no_room": reading(flow=9.0, headroom=None, sector="c"),
            "other":   reading(flow=0.5, headroom=15.0, sector="d"),
        }

        picked = bp.select(readings, size=4, max_per_sector=1, mode="combined")

        codes = [code for code, _ in picked]

        self.assertIn("full", codes)
        self.assertNotIn("no_flow", codes)
        self.assertNotIn("no_room", codes)

    def test_the_sector_cap_still_binds(self):
        readings = {
            str(i): reading(flow=float(i), headroom=float(i) * 5, sector="same")
            for i in range(6)
        }

        picked = bp.select(readings, size=6, max_per_sector=2, mode="combined")

        self.assertEqual(len(picked), 2)

    def test_weights_move_the_ranking(self):
        readings = {
            "flowy": reading(flow=3.0, headroom=1.0, sector="a"),
            "roomy": reading(flow=-2.0, headroom=40.0, sector="b"),
        }

        original = (config.COMBINED_WEIGHT_FLOW, config.COMBINED_WEIGHT_HEADROOM)

        try:
            config.COMBINED_WEIGHT_FLOW = 1.0
            config.COMBINED_WEIGHT_HEADROOM = 0.0
            first = bp.select(readings, 2, 1, mode="combined")[0][0]

            config.COMBINED_WEIGHT_FLOW = 0.0
            config.COMBINED_WEIGHT_HEADROOM = 1.0
            second = bp.select(readings, 2, 1, mode="combined")[0][0]
        finally:
            (config.COMBINED_WEIGHT_FLOW,
             config.COMBINED_WEIGHT_HEADROOM) = original

        self.assertEqual(first, "flowy")
        self.assertEqual(second, "roomy")


class TestHeadroomFilterInteraction(unittest.TestCase):

    def test_the_filter_applies_in_combined_mode_too(self):
        original = config.MIN_HEADROOM_PCT

        try:
            config.MIN_HEADROOM_PCT = 10.0

            readings = {
                "tight": reading(flow=5.0, headroom=2.0, sector="a"),
                "roomy": reading(flow=0.0, headroom=30.0, sector="b"),
                "mid":   reading(flow=1.0, headroom=15.0, sector="c"),
            }

            picked = bp.select(readings, 3, 1, mode="combined")

            codes = [code for code, _ in picked]
        finally:
            config.MIN_HEADROOM_PCT = original

        self.assertNotIn("tight", codes,
                         "a name under the ceiling must be filtered first")


if __name__ == "__main__":
    unittest.main()
