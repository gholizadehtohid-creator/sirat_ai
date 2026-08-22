"""Tests for the single-instrument technical read behind /api/technical.

The module is thin -- lookup, load, calculate, serialise -- so the tests focus
on the two things that actually break a dashboard panel: a missing indicator
must serialise as null (never a fabricated zero), and the payload must contain
no NaN/inf (which silently breaks JSON.parse in the browser).
"""

import json
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import technical


class FiniteTests(unittest.TestCase):

    def test_nan_and_inf_become_none(self):
        self.assertIsNone(technical._finite(float("nan")))
        self.assertIsNone(technical._finite(float("inf")))
        self.assertIsNone(technical._finite(-float("inf")))

    def test_real_numbers_pass_through(self):
        self.assertEqual(technical._finite(3.5), 3.5)
        self.assertEqual(technical._finite(0), 0.0)

    def test_none_stays_none_not_zero(self):
        """A missing RSI is not an RSI of 0 -- the whole point."""

        self.assertIsNone(technical._finite(None))

    def test_strings_pass_through_untouched(self):
        """The trend label is a string and must survive."""

        self.assertEqual(technical._finite("Strong Uptrend"), "Strong Uptrend")


class ResolveTests(unittest.TestCase):

    def test_empty_symbol_resolves_to_none(self):
        self.assertIsNone(technical.resolve_symbol(""))
        self.assertIsNone(technical.resolve_symbol(None))


class AnalyseContractTests(unittest.TestCase):
    """analyse() must never raise; it returns an error dict the route jsonifies."""

    def test_unknown_symbol_returns_error_dict(self):
        out = technical.analyse("definitely-not-a-symbol-xyz")

        self.assertIn("error", out)
        self.assertEqual(out["error"], "unknown symbol")

    def test_a_real_symbol_if_present_is_json_and_nan_free(self):
        """Skips cleanly when the DB has no instruments (CI without data).

        When data IS present, the payload must survive strict JSON with no NaN
        -- the exact failure that would blank the panel in a browser.
        """

        import database

        try:
            instruments = database.load_instruments(equities_only=True)
        except Exception:                                          # noqa: BLE001
            self.skipTest("no database available")

        with_history = None
        for row in instruments:
            rep = technical.analyse(row["symbol_fa"])
            if not rep.get("error"):
                with_history = rep
                break

        if with_history is None:
            self.skipTest("no instrument with usable history")

        # strict: allow_nan=False raises if any NaN/inf slipped through.
        json.dumps(with_history, ensure_ascii=False, allow_nan=False)

        self.assertIn("latest", with_history)
        self.assertIn("series", with_history)
        self.assertTrue(with_history["ins_code"])

        # every latest value is JSON-safe: a number, a string, or None.
        for key, value in with_history["latest"].items():
            self.assertTrue(
                value is None or isinstance(value, (int, float, str)), key)
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value), key)


if __name__ == "__main__":
    unittest.main()
