"""The market P/E's history on a constant composition (found 2026-11-11).

The raw aggregate moved whenever the set of companies with a usable trailing
profit changed (489 -> 423 between 10-10 and 11-08), and the percentile was
read on it. Each step is now taken on the companies counted at both points.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_valuation as mv  # noqa: E402


def point(date, companies):
    cap = sum(c for c, _ in companies.values())
    profit = sum(p for _, p in companies.values())
    return {"date": date, "pe": round(cap / profit, 2),
            "by_company": {k: list(v) for k, v in companies.items()}}


class LinkedSeriesTests(unittest.TestCase):

    def setUp(self):
        self.min_companies = mv.MIN_COMPANIES
        mv.MIN_COMPANIES = 1

    def tearDown(self):
        mv.MIN_COMPANIES = self.min_companies

    def test_a_company_leaving_the_count_does_not_move_the_level(self):
        # B (P/E 30) drops out of the count; A and C did not change at all.
        before = point("2026-10-10", {"A": (100, 10), "B": (300, 10), "C": (50, 10)})
        after = point("2026-11-08", {"A": (100, 10), "C": (50, 10)})

        self.assertNotEqual(before["pe"], after["pe"])      # the raw jump
        self.assertEqual(mv._linked([before, after]), [before["pe"], before["pe"]])

    def test_a_real_change_on_the_common_companies_carries_through(self):
        before = point("2026-10-10", {"A": (100, 10), "B": (100, 10)})
        after = point("2026-11-08", {"A": (100, 20), "B": (100, 20), "N": (10, 10)})

        self.assertEqual(mv._linked([before, after]), [10.0, 5.0])

    def test_points_without_companies_fall_back_to_the_raw_series(self):
        self.assertIsNone(mv._linked([{"date": "x", "pe": 8.0}]))


if __name__ == "__main__":
    unittest.main()
