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


class SettlementTests(unittest.TestCase):
    """A bill's yield runs from the day after its price traded (T+1), not
    from today: a stale close read a fair 43% as ~71% (the full test)."""

    def test_the_trade_date_settles_the_next_day(self):
        self.assertEqual(op._settles("20261011", date(2026, 11, 12)), date(2026, 10, 12))
        self.assertEqual(op._settles("2026-11-11", date(2026, 11, 12)), date(2026, 11, 12))

    def test_no_trade_date_settles_tomorrow(self):
        self.assertEqual(op._settles(None, date(2026, 11, 12)), date(2026, 11, 13))
        self.assertEqual(op._settles("junk", date(2026, 11, 12)), date(2026, 11, 13))

    def test_a_stale_price_is_not_read_as_a_higher_yield(self):
        maturity = date(2027, 1, 10)
        traded = op._settles("20261011", date(2026, 11, 12))
        fair = op.bill_yield(920_000, maturity, traded)
        from_today = op.bill_yield(920_000, maturity, date(2026, 11, 12))
        self.assertLess(fair, from_today)


class BillQuotesTests(unittest.TestCase):

    def test_the_last_quotes_are_kept_for_the_holdings_card(self):
        import json
        import tempfile
        from unittest import mock
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "bill_quotes.json")
        rows = [{"symbol": "اخزا 412", "price": 900_000, "price_date": "20261111",
                 "ytm_pct": 43.0, "maturity_jalali": "1406/04/12", "days": 200}]
        with mock.patch.object(op, "BILL_QUOTES_FILE", path):
            op._remember_bill_quotes(rows)
        with open(path, encoding="utf-8") as handle:
            quotes = json.load(handle)
        self.assertEqual(quotes["اخزا412"]["price"], 900_000)
        self.assertFalse(os.path.exists(path + ".partial"))


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


class ParkingBillTests(unittest.TestCase):
    """The review panel, 2026-11-11: not the top of a list sorted by yield."""

    def test_a_fresh_long_traded_bill_is_chosen_over_a_stale_high_yield(self):
        import opportunities
        bills = [
            {"symbol": "stale", "ytm_pct": 71.0, "days": 60, "price_date": "2026-10-10",
             "traded_value": 5e9},
            {"symbol": "short", "ytm_pct": 50.0, "days": 20, "price_date": "2026-11-10",
             "traded_value": 9e9},
            {"symbol": "thin", "ytm_pct": 45.0, "days": 200, "price_date": "2026-11-10",
             "traded_value": 1e8},
            {"symbol": "good", "ytm_pct": 43.0, "days": 180, "price_date": "2026-11-10",
             "traded_value": 4e9},
        ]
        self.assertEqual(opportunities.parking_bill(bills)["symbol"], "good")
        self.assertIsNone(opportunities.parking_bill([]))

    def test_the_real_yield_nets_out_inflation(self):
        import opportunities
        self.assertAlmostEqual(opportunities.real_yield_pct(43.73, 76.8), -18.7, places=1)
