"""Tests for the market-impact model.

The theme running through these: the module's value is not the arithmetic --
that is one square root -- it is the set of cases where it REFUSES to produce a
number. So most of what is tested here is the refusals, and specifically that
none of them degrades into a zero.
"""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtesting import impact                                  # noqa: E402


class ArithmeticTests(unittest.TestCase):

    def test_matches_the_published_form(self):
        """I = c * sigma * sqrt(Q/V), at the published effective prefactor."""

        result = impact.estimate(order_value=2_000_000,
                                 adv_value=100_000_000,
                                 daily_volatility_pct=3.0)

        self.assertTrue(result.executable)
        self.assertAlmostEqual(result.participation, 0.02, places=9)
        self.assertAlmostEqual(result.cost_pct,
                               0.34 * 3.0 * math.sqrt(0.02), places=9)

    def test_the_worked_example_from_the_source(self):
        """5% of daily volume ~ 0.076 daily volatilities of adverse move."""

        result = impact.estimate(order_value=5.0, adv_value=100.0,
                                 daily_volatility_pct=1.0)

        self.assertAlmostEqual(result.cost_pct, 0.34 * math.sqrt(0.05),
                               places=4)
        self.assertAlmostEqual(result.cost_pct, 0.076, places=3)

    def test_impact_grows_with_the_square_root_not_linearly(self):
        """Quadrupling the order doubles the cost. That IS the law."""

        small = impact.estimate(1_000_000, 100_000_000, 3.0)
        large = impact.estimate(4_000_000, 100_000_000, 3.0)

        self.assertAlmostEqual(large.cost_pct / small.cost_pct, 2.0, places=9)

    def test_cost_scales_with_volatility(self):
        calm = impact.estimate(2_000_000, 100_000_000, 1.0)
        wild = impact.estimate(2_000_000, 100_000_000, 4.0)

        self.assertAlmostEqual(wild.cost_pct / calm.cost_pct, 4.0, places=9)


class RefusalTests(unittest.TestCase):
    """Every one of these must refuse. None may return 0.0."""

    def test_a_locked_name_gets_no_number_at_all(self):
        result = impact.estimate(2_000_000, 100_000_000, 3.0, locked=True)

        self.assertFalse(result.executable)
        self.assertIsNone(result.cost_pct)
        self.assertIn("pinned", result.reason)

    def test_a_locked_name_is_not_merely_expensive(self):
        """The distinction that decides everything on this market."""

        locked = impact.estimate(2_000_000, 100_000_000, 3.0, locked=True)
        huge = impact.estimate(900_000_000, 100_000_000, 3.0)

        self.assertFalse(locked.executable)
        self.assertTrue(huge.executable)      # expensive, but possible

    def test_missing_volume_refuses_rather_than_guessing(self):
        for adv in (None, 0, -1, float("nan"), float("inf")):
            result = impact.estimate(2_000_000, adv, 3.0)
            self.assertFalse(result.executable, adv)
            self.assertIsNone(result.cost_pct, adv)

    def test_missing_volatility_refuses(self):
        for sigma in (None, 0, -2.0, float("nan")):
            result = impact.estimate(2_000_000, 100_000_000, sigma)
            self.assertFalse(result.executable, sigma)
            self.assertIsNone(result.cost_pct, sigma)

    def test_missing_order_refuses(self):
        result = impact.estimate(None, 100_000_000, 3.0)

        self.assertFalse(result.executable)

    def test_no_refusal_ever_reports_zero_cost(self):
        """The whole point. A refusal that returned 0.0 would be the old bug."""

        refusals = [
            impact.estimate(2_000_000, 100_000_000, 3.0, locked=True),
            impact.estimate(2_000_000, None, 3.0),
            impact.estimate(2_000_000, 100_000_000, None),
            impact.estimate(None, 100_000_000, 3.0),
            impact.estimate(2_000_000, 100_000_000, 3.0, prefactor=0),
        ]

        for result in refusals:
            self.assertFalse(result.executable, result.reason)
            self.assertIsNone(result.cost_pct, result.reason)
            self.assertNotEqual(result.cost_pct, 0.0)
            self.assertTrue(result.reason, "a refusal must say why")


class CalibrationRangeTests(unittest.TestCase):

    def test_inside_the_published_band_is_not_flagged(self):
        result = impact.estimate(2_000_000, 100_000_000, 3.0)   # 2.0%

        self.assertTrue(result.executable)
        self.assertFalse(result.extrapolated)

    def test_above_the_band_is_flagged_but_still_answered(self):
        result = impact.estimate(5_000_000, 100_000_000, 3.0)   # 5.0%

        self.assertTrue(result.executable)
        self.assertTrue(result.extrapolated)
        self.assertIsNotNone(result.cost_pct)
        self.assertIn("outside", result.reason)

    def test_below_the_band_is_also_flagged(self):
        """Extrapolation downward is extrapolation too."""

        result = impact.estimate(100_000, 100_000_000, 3.0)     # 0.1%

        self.assertTrue(result.extrapolated)

    def test_the_platform_cap_sits_above_the_calibrated_band(self):
        """A finding, pinned as a test so it cannot quietly stop being true.

        config.MAX_ADV_PARTICIPATION_PCT is 5%, and the published bins reach
        2.7%. Every estimate at the platform's own cap is an extrapolation.
        """

        import config

        cap = config.MAX_ADV_PARTICIPATION_PCT / 100.0

        self.assertGreater(cap, impact.CALIBRATED_MAX_PARTICIPATION)

        at_cap = impact.estimate(cap * 100_000_000, 100_000_000, 3.0)

        self.assertTrue(at_cap.extrapolated)


class RoundTripTests(unittest.TestCase):

    def test_round_trip_is_two_legs(self):
        both = impact.round_trip(2_000_000, 100_000_000, 3.0)
        one = impact.estimate(2_000_000, 100_000_000, 3.0)

        self.assertTrue(both["executable"])
        self.assertAlmostEqual(both["total_impact_pct"], 2 * one.cost_pct,
                               places=9)

    def test_a_locked_exit_makes_the_round_trip_inexecutable(self):
        """Getting in is not the problem on this market. Getting out is."""

        result = impact.round_trip(2_000_000, 100_000_000, 3.0,
                                   locked_on_exit=True)

        self.assertFalse(result["executable"])
        self.assertIsNone(result["total_impact_pct"])
        self.assertTrue(result["entry"].executable)
        self.assertFalse(result["exit"].executable)

    def test_impact_stacks_on_top_of_commission_rather_than_replacing_it(self):
        """1.04% is the commission. Impact is additional, and it is not small.

        At 2% participation and 3% daily volatility the round trip adds ~0.29pp
        to a 1.04pp commission -- a 28% increase in the cost hurdle that every
        prior result in this repository assumed away.
        """

        import backtest_config

        commission = backtest_config.EXPECTED_ROUND_TRIP_PCT

        result = impact.round_trip(2_000_000, 100_000_000, 3.0)

        self.assertGreater(result["total_impact_pct"], 0.25)
        self.assertLess(result["total_impact_pct"], 0.35)

        combined = commission + result["total_impact_pct"]

        self.assertGreater(combined, commission * 1.2)


class SensitivityTests(unittest.TestCase):

    def test_sweeps_every_plausible_prefactor(self):
        out = impact.sensitivity(2_000_000, 100_000_000, 3.0)

        self.assertEqual(set(out), set(impact.SENSITIVITY_PREFACTORS))

        for c, cost in out.items():
            self.assertAlmostEqual(cost, c * 3.0 * math.sqrt(0.02), places=9)

    def test_the_raw_prefactor_doubles_the_bill(self):
        """c_raw = 0.69 against c_eff = 0.34. A conclusion must survive both."""

        out = impact.sensitivity(2_000_000, 100_000_000, 3.0)

        self.assertAlmostEqual(out[0.69] / out[0.34], 0.69 / 0.34, places=9)


class VolatilityTests(unittest.TestCase):

    def test_measures_dispersion_not_level(self):
        flat = [100.0] * 30

        self.assertAlmostEqual(impact.daily_volatility_pct(flat), 0.0,
                               places=9)

    def test_too_little_history_returns_none_not_zero(self):
        """Zero volatility would make a new listing look like the safest name."""

        self.assertIsNone(impact.daily_volatility_pct([100.0, 101.0, 102.0]))

    def test_ignores_non_positive_prices(self):
        closes = [100.0, 0.0, None, 101.0] + [100.0 + i for i in range(20)]

        self.assertIsNotNone(impact.daily_volatility_pct(closes))

    def test_a_volatile_series_reads_higher_than_a_calm_one(self):
        calm = [100.0 + 0.1 * i for i in range(30)]
        wild = [100.0 * (1.05 if i % 2 else 0.95) for i in range(30)]

        self.assertGreater(impact.daily_volatility_pct(wild),
                           impact.daily_volatility_pct(calm))


class ImportSafetyTests(unittest.TestCase):

    def test_importing_changes_no_existing_behaviour(self):
        """Nothing calls this yet, on purpose.

        Switching slippage on must be a measured delta inside a registered
        experiment, not a silent revision of every result in the ledger. If
        this test ever fails, someone has wired it in without an experiment.
        """

        import subprocess

        out = subprocess.run(
            [sys.executable, "-c",
             "import ast,os,sys;"
             "root=sys.argv[1];"
             "hits=[];"
             "[hits.append(p) for p in "
             " [os.path.join(d,f) for d,_,fs in os.walk(root) for f in fs "
             "  if f.endswith('.py')] "
             " if 'impact' in open(p,encoding='utf-8',errors='ignore').read()"
             " and 'test_impact' not in p and 'backtesting\\\\impact' not in p"
             " and 'backtesting/impact' not in p];"
             "print('\\n'.join(h for h in hits if 'import impact' in "
             "open(h,encoding='utf-8',errors='ignore').read() or "
             "'from backtesting import impact' in "
             "open(h,encoding='utf-8',errors='ignore').read()))",
             ROOT],
            capture_output=True, text=True)

        importers = [line for line in out.stdout.splitlines() if line.strip()]

        self.assertEqual(importers, [],
                         "impact.py is imported by %s -- wiring it in changes "
                         "existing results and belongs in a registered "
                         "experiment" % importers)


if __name__ == "__main__":
    unittest.main()
