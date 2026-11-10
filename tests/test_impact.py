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
    """Impact may be charged by an EXPERIMENT and never by the engine.

    The first version of this test asserted that nothing anywhere imported the
    module, and it went red the moment E22 used it as designed. That was the
    contract stated too loosely, not a real violation, so the contract is
    stated properly here rather than the control being dropped.

    The line that matters: switching slippage on must be a measured delta
    inside a registered, preregistered experiment. If the engine imports it,
    every result in the ledger silently changes meaning and no experiment
    records that it happened.
    """

    # Registered experiments live here. They are the sanctioned caller.
    ALLOWED_PREFIXES = ("examples",)

    EXEMPT = ("backtesting/impact.py", "tests/test_impact.py")

    def _importers(self):
        needles = ("from backtesting import impact", "import backtesting.impact")

        found = []

        for directory, _, files in os.walk(ROOT):
            # .claude holds tool state, including git worktrees: whole copies
            # of this repository, whose examples/ then read as "outside
            # examples/" (21 false offenders on 2026-11-11).
            if any(part in directory for part in
                   (".git", "__pycache__", "backtest_output", ".venv", ".claude")):
                continue

            for name in files:
                if not name.endswith(".py"):
                    continue

                path = os.path.join(directory, name)
                rel = os.path.relpath(path, ROOT).replace("\\", "/")

                if rel in self.EXEMPT:
                    continue

                with open(path, encoding="utf-8", errors="ignore") as handle:
                    source = handle.read()

                if any(needle in source for needle in needles):
                    found.append(rel)

        return found

    def test_the_engine_never_charges_impact_on_its_own(self):
        offenders = [rel for rel in self._importers()
                     if not rel.startswith(self.ALLOWED_PREFIXES)]

        self.assertEqual(
            offenders, [],
            "impact.py is imported outside examples/ by %s. Wiring it into the "
            "engine changes every existing result without any experiment "
            "recording that it changed." % offenders)

    def test_the_control_is_actually_looking_at_something(self):
        """A scanner that finds nothing anywhere is a scanner that is broken."""

        self.assertTrue(self._importers(),
                        "no importer found at all -- the scan is not working")


if __name__ == "__main__":
    unittest.main()


class ContaminationTests(unittest.TestCase):
    """The defect E22 exposed on the model's first real use.

    E22 charged impact for the first time and read 56-76% of price across a
    550-name cross-section on five sessions. The cause was 252 rows in
    price_history carrying returns above 200% -- the largest +19,976,600%,
    a prior close of 1 rial. These tests pin the fix.
    """

    def test_an_impossible_volatility_is_refused_not_charged(self):
        result = impact.estimate(2_000_000, 100_000_000,
                                 daily_volatility_pct=400.0)

        self.assertFalse(result.executable)
        self.assertIsNone(result.cost_pct)
        self.assertIn("band ceiling", result.reason)

    def test_the_ceiling_is_e14s_constant_not_a_new_one(self):
        """Reusing a validated threshold, rather than picking one that passes."""

        import experiment_14_queue_artifact as e14

        self.assertEqual(impact.EXTREME_MOVE_PCT, e14.LOCK_MAX_RETURN)
        self.assertEqual(impact.MAX_PLAUSIBLE_DAILY_VOLATILITY_PCT,
                         e14.LOCK_MAX_RETURN)

    def test_a_genuinely_volatile_name_still_gets_a_number(self):
        """The bound must not refuse the wild-but-real end of the market."""

        result = impact.estimate(2_000_000, 100_000_000,
                                 daily_volatility_pct=11.5)

        self.assertTrue(result.executable)
        self.assertGreater(result.cost_pct, 0.0)

    def test_a_capital_increase_does_not_inflate_the_estimate(self):
        """One 1-rial prior close used to carry a name for twenty sessions."""

        clean = [100.0 + (1.0 if i % 2 else -1.0) for i in range(30)]

        contaminated = list(clean)
        contaminated[15] = 1.0          # placeholder price, then it recovers

        honest = impact.daily_volatility_pct(clean)
        repaired = impact.daily_volatility_pct(contaminated)

        self.assertIsNotNone(repaired)
        self.assertLess(repaired, 2.0 * honest,
                        "one placeholder price still dominates the estimate")

    def test_extreme_returns_are_dropped_not_winsorised(self):
        """Winsorising keeps the bar at the boundary and still biases upward."""

        clean = [100.0] * 30

        spiked = list(clean)
        spiked[15] = 500.0

        self.assertAlmostEqual(impact.daily_volatility_pct(spiked), 0.0,
                               places=6)

    def test_dropping_can_leave_too_little_history_and_that_returns_none(self):
        """A name that is nothing but gaps has no volatility, not a small one."""

        gappy = [100.0 * (5.0 ** (i % 2)) for i in range(30)]

        self.assertIsNone(impact.daily_volatility_pct(gappy))
