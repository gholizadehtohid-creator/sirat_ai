"""Tests for gold-fund identification and listing.

The identification rule is the load-bearing part: it must catch gold ETFs and
reject both non-gold funds and equities that merely have طلا in their name
(حشکوه, مفاخر). If that rule drifts, the section either misses funds or pulls
in stocks, and both are silent until someone reads the list.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gold


class IdentificationTests(unittest.TestCase):

    def test_a_gold_fund_is_recognised(self):
        self.assertTrue(gold.is_gold_fund(
            {"kind": "fund", "name_fa": "صندوق طلای عیار مفید"}))
        self.assertTrue(gold.is_gold_fund(
            {"kind": "fund", "name_fa": "صندوق س.پشتوانه سکه طلا کهربا"}))

    def test_a_coin_backed_fund_counts_via_سکه(self):
        """Some funds carry سکه, not طلا, as the operative word."""
        self.assertTrue(gold.is_gold_fund(
            {"kind": "fund", "name_fa": "صندوق سکه طلای کیان"}))

    def test_a_non_gold_fund_is_rejected(self):
        self.assertFalse(gold.is_gold_fund(
            {"kind": "fund", "name_fa": "صندوق س.اعتماد ارغوان-د"}))

    def test_an_equity_with_طلا_in_its_name_is_rejected(self):
        """حشکوه / مفاخر contain طلا but are stocks, not gold ETFs.

        The kind check is what excludes them; without it the section would
        rank two equities as gold funds.
        """
        self.assertFalse(gold.is_gold_fund(
            {"kind": "equity", "name_fa": "دلیجان طلایی شکوه پارس"}))
        self.assertFalse(gold.is_gold_fund(
            {"kind": "equity", "name_fa": "توسعه فناوری اطلاعات خوارزمی"}))

    def test_missing_fields_do_not_crash(self):
        self.assertFalse(gold.is_gold_fund({}))
        self.assertFalse(gold.is_gold_fund({"kind": "fund"}))
        self.assertFalse(gold.is_gold_fund({"name_fa": "طلا"}))


class UniverseTests(unittest.TestCase):
    """Against the real DB when present; skips cleanly otherwise."""

    def _has_db(self):
        import database
        try:
            return bool(database.load_instruments(equities_only=False))
        except Exception:                                          # noqa: BLE001
            return False

    def test_gold_funds_are_all_funds_and_all_gold(self):
        if not self._has_db():
            self.skipTest("no database")

        funds = gold.gold_funds()

        # There are gold funds in this universe.
        self.assertGreater(len(funds), 0)

        for row in funds:
            self.assertEqual(row.get("kind"), "fund")
            name = row.get("name_fa") or ""
            self.assertTrue("طلا" in name or "سکه" in name, name)

        # No equity leaked in.
        symbols = {r.get("symbol_fa") for r in funds}
        self.assertNotIn("حشکوه", symbols)
        self.assertNotIn("مفاخر", symbols)

    def test_codes_match_funds(self):
        if not self._has_db():
            self.skipTest("no database")
        self.assertEqual(len(gold.gold_fund_codes()), len(gold.gold_funds()))

    def test_gold_list_is_json_safe(self):
        if not self._has_db():
            self.skipTest("no database")

        import json
        import technical
        rep = technical.gold_list()

        json.dumps(rep, ensure_ascii=False, allow_nan=False)
        self.assertIn("funds", rep)
        self.assertIn("caveat", rep)
        self.assertIn("E19", rep["caveat"])
        # counts reconcile
        c = rep["counts"]
        self.assertEqual(c["total"], c["with_data"] + c["missing"])

    def test_gold_list_carries_the_nav_fields(self):
        """Every row has the NAV keys, present or null -- never absent.

        The panel reads `price_to_nav` off each row and prints an em dash when
        it is null. A missing KEY and a null VALUE render identically there, so
        a silent rename upstream would look exactly like a market with no NAV.
        This asserts the shape, not the network: with TSETMC unreachable the
        values are all None and the test still passes.
        """
        if not self._has_db():
            self.skipTest("no database")

        import technical
        rep = technical.gold_list()

        if not rep["funds"]:
            self.skipTest("no gold fund has history yet")

        for row in rep["funds"]:
            for key in ("day_close", "nav", "nav_issue", "price_to_nav",
                        "bubble", "nav_date"):
                self.assertIn(key, row, "%s missing %s" % (row["symbol"], key))

        self.assertIn("with_nav", rep["counts"])
        self.assertIn("nav_band", rep)


class NavArithmeticTests(unittest.TestCase):
    """The ratio itself. No network -- these are the failure modes that would
    print a wrong number rather than no number."""

    def test_zero_is_read_as_absent_not_as_a_price(self):
        """GetClosingPriceInfo returns pRedTran = 0.0 for these funds.

        If zero were taken at face value the حباب would be a division by zero
        for every single row -- and an infinity is a number that renders.
        """
        self.assertIsNone(gold._number(0))
        self.assertIsNone(gold._number(0.0))
        self.assertIsNone(gold._number(None))
        self.assertIsNone(gold._number("not a number"))
        self.assertEqual(gold._number("1487864"), 1487864.0)

    def test_a_missing_side_yields_no_ratio(self):
        self.assertIsNone(gold._rate(None, 100.0))
        self.assertIsNone(gold._rate(100.0, None))
        self.assertIsNone(gold._rate(None, None))

    def test_the_ratio_is_price_over_nav(self):
        """Not NAV over price. Inverted, a 3% premium reads as a 3% discount
        and the colour on the panel flips with it."""
        self.assertAlmostEqual(gold._rate(103.0, 100.0), 1.03)


if __name__ == "__main__":
    unittest.main()
