"""P0 — idle cash earns the policy rate, and ONLY when asked.

The defect: backtesting/portfolio.py never credited idle cash, while
backtesting/metrics.py charged a 23% Sharpe hurdle. A book that is 71% in cash
paid that hurdle twice.

The fix is versioned rather than applied: default OFF, so every v2.0 result
stays reproducible bit for bit, and the v2.1 experiments opt in explicitly.
These tests pin both halves -- that it works when on, and that it is genuinely
inert when off.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_config
import config
from backtesting.portfolio import Portfolio


class DefaultIsOffTest(unittest.TestCase):
    """The half that protects every number the project has already published."""

    def test_the_shipped_default_is_off(self):
        self.assertFalse(backtest_config.CREDIT_IDLE_CASH)
        self.assertFalse(backtest_config.load().credit_idle_cash)

    def test_cash_does_not_move_when_off(self):
        book = Portfolio(backtest_config.load(), initial_capital=1_000_000.0)

        opening = book.cash

        for _ in range(240):
            book.accrue_cash()

        self.assertEqual(book.cash, opening)
        self.assertEqual(book.cash_interest_earned, 0.0)

    def test_accrue_returns_zero_when_off(self):
        book = Portfolio(backtest_config.load(), initial_capital=1_000.0)
        self.assertEqual(book.accrue_cash(), 0.0)


class V2CreditTest(unittest.TestCase):
    """E26: the v2 book's idle cash earns the fixed-income rate; nothing else's."""

    def test_v2_is_credited_and_the_global_default_is_not(self):
        cfg = backtest_config.load()
        self.assertTrue(cfg.v2_credit_idle_cash)
        self.assertFalse(cfg.credit_idle_cash)

    def test_the_v2_switch_survives_replace(self):
        cfg = backtest_config.load().replace(v2_credit_idle_cash=False)
        self.assertFalse(cfg.v2_credit_idle_cash)


class AccrualTest(unittest.TestCase):

    def _book(self, capital=1_000_000.0, rate=None):
        cfg = backtest_config.load().replace(credit_idle_cash=True)
        if rate is not None:
            cfg = cfg.replace(cash_annual_rate=rate)
        return Portfolio(cfg, initial_capital=capital)

    def test_a_full_year_compounds_to_the_annual_rate(self):
        book = self._book()

        for _ in range(config.TRADING_DAYS_PER_YEAR):
            book.accrue_cash()

        expected = 1_000_000.0 * (1 + config.RISK_FREE_RATE)

        self.assertAlmostEqual(book.cash, expected, places=2)

    def test_the_rate_is_compounded_not_divided(self):
        """23%/240 per session would overshoot; the difference is material."""

        book = self._book()
        book.accrue_cash()

        naive = 1_000_000.0 * (1 + config.RISK_FREE_RATE / 240.0)

        self.assertLess(book.cash, naive)

    def test_interest_is_tracked_separately(self):
        book = self._book()

        for _ in range(config.TRADING_DAYS_PER_YEAR):
            book.accrue_cash()

        self.assertAlmostEqual(
            book.cash_interest_earned,
            1_000_000.0 * config.RISK_FREE_RATE, places=2)

    def test_no_cash_earns_nothing(self):
        book = self._book()
        book.cash = 0.0

        self.assertEqual(book.accrue_cash(), 0.0)

    def test_a_zero_rate_earns_nothing(self):
        book = self._book(rate=0.0)

        self.assertEqual(book.accrue_cash(), 0.0)
        self.assertEqual(book.cash, 1_000_000.0)

    def test_the_nav_identity_still_holds(self):
        book = self._book()

        for _ in range(50):
            book.accrue_cash()

        self.assertAlmostEqual(book.value(),
                               book.cash + book.holdings_value(), places=6)


if __name__ == "__main__":
    unittest.main()
