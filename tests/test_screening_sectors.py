"""Earnings multiples must be withheld where they measure something else.

An investment company's profit is the realised gain on holdings it chose to
sell, so its P/E answers "did it sell something last year", not "is it cheap".
Banks and insurers fail the same test through provisioning and reserve
releases. The screen computes those ratios perfectly well, which is exactly
the danger: the output looks like an assessment.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import fundamentals
import screening


NAV_SECTOR = "سرمايه گذاريها"
BANK_SECTOR = "بانكها و موسسات اعتباري"
INSURANCE_SECTOR = "بيمه وصندوق بازنشستگي به جزتامين اجتماعي"
ORDINARY_SECTOR = "فلزات اساسي"


def bars(n=260, close=1000.0):
    dates = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({
        "date": dates,
        "open": [close] * n,
        "high": [close * 1.01] * n,
        "low": [close * 0.99] * n,
        "close": [close] * n,
        "volume": [5_000_000] * n,
        "value": [5_000_000 * close] * n,
    })


def snapshot(eps, close=1000.0, shares=1_000_000_000):
    return {
        "eps": eps,
        "close": close,
        "total_shares": shares,
        "base_volume": 100_000,
        "state": "I",
    }


class TestEarningsMultipleApplicability(unittest.TestCase):

    def test_an_ordinary_sector_still_scores_on_pe(self):
        metrics = fundamentals.build(bars(), snapshot(eps=500.0),
                                     sector=ORDINARY_SECTOR)

        self.assertTrue(metrics["earnings_multiple_meaningful"])
        self.assertIsNotNone(metrics["pe"])

        fraction, note = screening._score_valuation(metrics)

        self.assertIsNotNone(fraction, "an industrial P/E must still be scored")

    def test_investment_company_pe_is_withheld(self):
        metrics = fundamentals.build(bars(), snapshot(eps=500.0),
                                     sector=NAV_SECTOR)

        self.assertFalse(metrics["earnings_multiple_meaningful"])
        # The ratio is still computed and reported -- it is simply not scored.
        self.assertIsNotNone(metrics["pe"])

        fraction, note = screening._score_valuation(metrics)

        self.assertIsNone(fraction)
        self.assertIn("withheld", note)

    def test_a_cheap_looking_investment_company_gains_no_points(self):
        """P/E 2.0 must not read as 'very cheap' for a holding company."""

        cheap = snapshot(eps=500.0, close=1000.0)      # P/E 2.0

        ordinary = fundamentals.build(bars(), cheap, sector=ORDINARY_SECTOR)
        holding = fundamentals.build(bars(), cheap, sector=NAV_SECTOR)

        self.assertAlmostEqual(ordinary["pe"], 2.0, places=2)
        self.assertAlmostEqual(holding["pe"], 2.0, places=2)

        ordinary_score = screening.score_fundamentals(ordinary)
        holding_score = screening.score_fundamentals(holding)

        self.assertIsNotNone(ordinary_score["breakdown"].get("Valuation"))
        self.assertIsNone(holding_score["breakdown"].get("Valuation"))

        # And the honesty requirement: less of the model ran, and it says so.
        self.assertLess(holding_score["weight_covered"],
                        ordinary_score["weight_covered"])

    def test_banks_and_insurers_are_covered_too(self):
        for sector in (BANK_SECTOR, INSURANCE_SECTOR):
            metrics = fundamentals.build(bars(), snapshot(eps=400.0),
                                         sector=sector)
            self.assertFalse(metrics["earnings_multiple_meaningful"], sector)

    def test_earnings_yield_is_withheld_as_well(self):
        metrics = fundamentals.build(bars(), snapshot(eps=500.0),
                                     sector=NAV_SECTOR)

        fraction, note = screening._score_profitability(metrics)

        self.assertIsNone(fraction)
        self.assertIn("حساب نشد", note)


class TestLossMakingGate(unittest.TestCase):
    """A quiet year is not a failing business."""

    def test_an_ordinary_loss_maker_is_still_rejected(self):
        metrics = fundamentals.build(bars(), snapshot(eps=-200.0),
                                     sector=ORDINARY_SECTOR)

        reason = screening._gate_lossmaking(metrics)

        self.assertIsNotNone(reason)
        self.assertIn("loss-making", reason)

    def test_an_investment_company_with_no_disposals_is_not_rejected(self):
        metrics = fundamentals.build(bars(), snapshot(eps=-200.0),
                                     sector=NAV_SECTOR)

        self.assertIsNone(screening._gate_lossmaking(metrics))

    def test_an_insurer_with_a_negative_year_is_not_rejected(self):
        metrics = fundamentals.build(bars(), snapshot(eps=-50.0),
                                     sector=INSURANCE_SECTOR)

        self.assertIsNone(screening._gate_lossmaking(metrics))

    def test_a_missing_sector_keeps_the_old_behaviour(self):
        """No sector known: the gate stays as strict as it was."""

        metrics = fundamentals.build(bars(), snapshot(eps=-200.0))

        self.assertTrue(metrics["earnings_multiple_meaningful"])
        self.assertIsNotNone(screening._gate_lossmaking(metrics))


class TestSectorListIntegrity(unittest.TestCase):

    def test_the_configured_names_match_the_exchange_spelling(self):
        """These are matched exactly, so a typo silently disables the rule."""

        for name in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            metrics = fundamentals.build(bars(), snapshot(eps=100.0),
                                         sector=name)
            self.assertFalse(metrics["earnings_multiple_meaningful"], name)

    def test_an_unlisted_sector_is_unaffected(self):
        metrics = fundamentals.build(bars(), snapshot(eps=100.0),
                                     sector="محصولات شيميايي")

        self.assertTrue(metrics["earnings_multiple_meaningful"])


if __name__ == "__main__":
    unittest.main()
