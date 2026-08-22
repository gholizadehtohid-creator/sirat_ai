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


if __name__ == "__main__":
    unittest.main()
