"""holdings: the user's own book; market_valuation: the market's P/E in context."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import holdings  # noqa: E402
import market_valuation as mv  # noqa: E402


class ParseTests(unittest.TestCase):

    def test_lines_commas_and_persian_digits(self):
        self.assertEqual(
            holdings.parse("فولاد 1000، شپنا 5000\nزر 20000, وبملت\r\nفولاد ۵۰۰"),
            [("فولاد", 1500.0), ("شپنا", 5000.0), ("زر", 20000.0), ("وبملت", None)])

    def test_nothing_is_an_empty_book(self):
        self.assertEqual(holdings.parse(""), [])
        self.assertEqual(holdings.parse(" ,، \n "), [])

    def test_the_book_is_capped(self):
        text = "\n".join("س%d" % i for i in range(holdings.MAX_SYMBOLS + 5))
        self.assertEqual(len(holdings.parse(text)), holdings.MAX_SYMBOLS)


def row(symbol, value, sector="فلزات", flags=0, halted=None):
    return {"symbol": symbol, "value": value, "sector": sector, "as_of": "2026-11-10",
            "halted_since": halted,
            "outlook": {"flags": ["x"] * flags} if flags is not None else None}


class SummaryTests(unittest.TestCase):

    def test_weights_add_to_the_book(self):
        out = holdings._summarise([row("الف", 300), row("ب", 700, sector="سیمان")])
        self.assertEqual([r["weight_pct"] for r in out["rows"]], [30.0, 70.0])
        self.assertEqual(out["total_value"], 1000)

    def test_one_name_or_one_industry_carrying_the_book_is_called_out(self):
        rows = [row("الف", 500), row("ب", 300), row("ج", 100, sector="سیمان"),
                row("د", 100, sector="دارو")]
        warnings = holdings._summarise(rows)["warnings"]
        self.assertTrue(any("الف" in w and "تمرکز" in w for w in warnings))
        self.assertTrue(any("فلزات" in w and "صنعت" in w for w in warnings))
        self.assertFalse(any("«ج»" in w for w in warnings))

    def test_halts_warnings_and_unknown_symbols_are_listed(self):
        rows = [row("الف", 100, flags=3), row("ب", 100, halted="2026-10-01"),
                {"symbol": "زززز", "error": "نماد پیدا نشد"}]
        warnings = holdings._summarise(rows)["warnings"]
        self.assertTrue(any("3 هشدار" in w for w in warnings))
        self.assertTrue(any("متوقف" in w for w in warnings))
        self.assertTrue(any("زززز" in w for w in warnings))

    def test_no_quantities_means_no_weights_and_no_concentration(self):
        out = holdings._summarise([row("الف", None), row("ب", None)])
        self.assertIsNone(out["total_value"])
        self.assertFalse(any("تمرکز" in w for w in out["warnings"]))


class ValuationSummaryTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        points = [{"date": "2021-%02d-01" % (i + 1), "pe": pe,
                   "earnings_yield_pct": round(100 / pe, 2), "companies": 300}
                  for i, pe in enumerate([4.0, 5.0, 6.0, 7.0, 8.0, 5.0])]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"points": points}, handle)
        self.patch = mock.patch.object(mv, "FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        os.unlink(self.path)

    def test_now_is_placed_in_its_own_history(self):
        s = mv.summary(opportunity={"rate_pct": 43.0, "as_of": "x", "source": "t"})
        self.assertTrue(s["available"])
        self.assertEqual(s["now"]["pe"], 5.0)
        # below: 4.0; equal: two 5.0s -> (1 + 0.5*2) / 6
        self.assertAlmostEqual(s["percentile"], 33.3)
        self.assertEqual(s["median_pe"], 5.5)
        self.assertAlmostEqual(s["yield_gap_pct"], 20.0 - 43.0)

    def test_no_history_is_said_plainly(self):
        with mock.patch.object(mv, "FILE", self.path + ".missing"):
            self.assertFalse(mv.summary()["available"])


if __name__ == "__main__":
    unittest.main()
