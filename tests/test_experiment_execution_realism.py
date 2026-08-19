"""Pre-flight for Experiment 3: the seam must change nothing it should not.

Two things need proving before the final gate runs.

  1. `policy_for()` was extracted from inline code. With no exposure supplied it
     must return exactly the policy the base class always built, or every
     measured Alpha Core result silently moved.

  2. The exposure constraint must only ever TIGHTEN, must be identical for both
     arms, and must not touch the ranking.

The leakage properties of the exposure engine itself are asserted in
test_market_exposure.py and are not repeated here.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import backtest_config
import portfolio_engine
from backtesting.alpha_core import AlphaCoreStrategy

from experiment_execution_realism import ExposureConstrainedAlphaCore


class FakeData(object):
    """Enough of PointInTimeData for a strategy to be constructed."""

    def instruments(self):
        return {"1": {"symbol": "الف", "sector": "فلز"},
                "2": {"symbol": "ب", "sector": "شيميايي"}}

    def bars(self, ins_code):
        return None

    def stale_sessions(self, ins_code, date):
        return 0

    def index_series(self, key):
        return None


class PolicySeamTest(unittest.TestCase):
    """The extraction must be behaviour-preserving."""

    def setUp(self):
        self.cfg = backtest_config.load()
        self.data = FakeData()

    def test_the_base_policy_is_what_the_inline_code_built(self):
        strategy = AlphaCoreStrategy(self.cfg, self.data, verbose=False)

        produced = strategy.policy_for(pd.Timestamp("2020-01-01")).as_dict()

        expected = portfolio_engine.from_config().replace(
            max_positions=self.cfg.max_positions,
            max_per_sector=self.cfg.max_per_sector,
        ).as_dict()

        self.assertEqual(produced, expected)

    def test_the_policy_does_not_vary_by_date_without_exposure(self):
        strategy = AlphaCoreStrategy(self.cfg, self.data, verbose=False)

        a = strategy.policy_for(pd.Timestamp("2018-05-05")).as_dict()
        b = strategy.policy_for(pd.Timestamp("2023-11-11")).as_dict()

        self.assertEqual(a, b)


class ExposureConstraintTest(unittest.TestCase):

    def setUp(self):
        self.cfg = backtest_config.load()
        self.data = FakeData()

        self.date = pd.Timestamp("2020-06-01")

    def _strategy(self, exposure):
        return ExposureConstrainedAlphaCore(
            self.cfg, self.data, verbose=False,
            exposure_by_date={self.date: exposure})

    def test_no_exposure_supplied_is_the_base_policy(self):
        strategy = ExposureConstrainedAlphaCore(
            self.cfg, self.data, verbose=False, exposure_by_date={})

        base = AlphaCoreStrategy(self.cfg, self.data, verbose=False)

        self.assertEqual(strategy.policy_for(self.date).as_dict(),
                         base.policy_for(self.date).as_dict())

    def test_a_lower_target_tightens_the_invested_ceiling(self):
        policy = self._strategy(0.25).policy_for(self.date)

        self.assertLessEqual(policy.target_invested_pct, 25.0)
        self.assertGreaterEqual(policy.min_cash_pct, 75.0)

    def test_the_constraint_only_ever_tightens(self):
        """A 100% target must not loosen the config's own cash floor."""

        base = AlphaCoreStrategy(self.cfg, self.data,
                                 verbose=False).policy_for(self.date)

        policy = self._strategy(1.0).policy_for(self.date)

        self.assertLessEqual(policy.target_invested_pct,
                             base.target_invested_pct + 1e-9)
        self.assertGreaterEqual(policy.min_cash_pct,
                                base.min_cash_pct - 1e-9)

    def test_zero_exposure_permits_nothing(self):
        policy = self._strategy(0.0).policy_for(self.date)

        self.assertEqual(policy.target_invested_pct, 0.0)
        self.assertEqual(policy.min_cash_pct, 100.0)

    def test_a_date_with_no_target_falls_back_to_the_base_policy(self):
        strategy = self._strategy(0.25)

        other = pd.Timestamp("2021-01-01")

        base = AlphaCoreStrategy(self.cfg, self.data,
                                 verbose=False).policy_for(other)

        self.assertEqual(strategy.policy_for(other).as_dict(), base.as_dict())

    def test_the_exposure_path_is_identical_for_both_rankings(self):
        """Treatment and control must face the same ceiling on every date."""

        schedule = {self.date: 0.5,
                    pd.Timestamp("2020-07-01"): 0.75}

        alpha = ExposureConstrainedAlphaCore(
            self.cfg, self.data, verbose=False, ranking="alpha",
            exposure_by_date=schedule)

        random_arm = ExposureConstrainedAlphaCore(
            self.cfg, self.data, verbose=False, ranking="random", seed=7,
            exposure_by_date=schedule)

        for date in schedule:
            self.assertEqual(alpha.policy_for(date).as_dict(),
                             random_arm.policy_for(date).as_dict())

    def test_the_constraint_does_not_touch_the_ranking(self):
        """Selection is the only thing allowed to differ between arms."""

        readings = [
            {"momentum": 90, "bb_pct": 0.9},
            {"momentum": 10, "bb_pct": 0.1},
            {"momentum": 50, "bb_pct": 0.5},
        ]

        constrained = self._strategy(0.25).alpha_scores(
            [dict(r) for r in readings], date=self.date)

        plain = AlphaCoreStrategy(self.cfg, self.data,
                                  verbose=False).alpha_scores(
            [dict(r) for r in readings], date=self.date)

        self.assertEqual([r["alpha"] for r in constrained],
                         [r["alpha"] for r in plain])


if __name__ == "__main__":
    unittest.main()
