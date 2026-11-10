"""opportunities.py: the arithmetic and parsing that need no network."""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import opportunities as op  # noqa: E402


class MaturityTests(unittest.TestCase):

    def test_trailing_six_digits_are_a_jalali_maturity(self):
        # 1405/10/21 is 2027-01-11.
        self.assertEqual(op.maturity_of("اسناد خزانه-م13بودجه02-051021"),
                         date(2027, 1, 11))

    def test_a_name_without_a_date_has_no_maturity(self):
        self.assertIsNone(op.maturity_of("صندوق طلا"))

    def test_an_impossible_date_is_refused_not_guessed(self):
        self.assertIsNone(op.maturity_of("اسنادخزانه-م1بودجه02-051340"))


class YieldTests(unittest.TestCase):

    def test_a_year_at_80_percent_of_face_is_25_percent(self):
        today = date(2026, 1, 1)
        ytm = op.bill_yield(800_000, date(2027, 1, 1), today)
        self.assertAlmostEqual(ytm, 0.25, places=6)

    def test_a_bill_about_to_mature_is_not_annualised(self):
        today = date(2026, 1, 1)
        self.assertIsNone(op.bill_yield(999_000, date(2026, 1, 3), today))

    def test_a_matured_or_missing_price_gives_nothing(self):
        today = date(2026, 1, 1)
        self.assertIsNone(op.bill_yield(None, date(2027, 1, 1), today))
        self.assertIsNone(op.bill_yield(900_000, date(2025, 1, 1), today))

    def test_a_price_at_or_above_face_is_no_yield_rather_than_zero(self):
        # An untraded series is quoted at face value. It used to be listed at
        # 0.00% among the real yields; no bill earns that, so it is refused.
        today = date(2026, 1, 1)
        self.assertIsNone(op.bill_yield(1_000_000, date(2027, 1, 1), today))
        self.assertIsNone(op.bill_yield(1_200_000, date(2027, 1, 1), today))

        # One rial under face is still a price, and a (tiny) positive yield.
        self.assertGreater(op.bill_yield(999_999, date(2027, 1, 1), today), 0)


class SleeveTests(unittest.TestCase):

    def test_names_map_to_allocation_sleeves(self):
        self.assertEqual(op.fund_sleeve("صندوق س. طلای زرافشان-ک"), "gold")
        self.assertEqual(op.fund_sleeve("صندوق س نوع دوم افق اتی-ثابت"), "fixed")
        self.assertEqual(op.fund_sleeve("صندوق س. نوع دوم اکام-د"), "fixed")
        self.assertEqual(op.fund_sleeve("صندوق شاخص30 شرکت فیروزه- سهام"),
                         "equity")

    def test_leveraged_funds_are_left_out(self):
        # Two unit classes: the traded unit's NAV is not the fund's.
        self.assertIsNone(op.fund_sleeve("صندوق س. اهرمی کاریزما-س"))


if __name__ == "__main__":
    unittest.main()
