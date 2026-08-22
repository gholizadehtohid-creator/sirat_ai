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


class SupportResistanceTests(unittest.TestCase):
    """Levels come from confirmed pivots; each carries a signed distance."""

    def _frame(self, closes):
        import pandas as pd
        n = len(closes)
        dates = pd.date_range("2025-01-01", periods=n, freq="D")
        return pd.DataFrame({
            "date": dates, "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "volume": [1000] * n,
        })

    def test_resistance_above_support_below(self):
        # a clear peak in the middle, price ends lower -> that peak is resistance
        closes = [10, 11, 12, 15, 20, 15, 12, 11, 13, 14, 13, 12, 11, 13]
        sr = technical.support_resistance(self._frame(closes), price=13.0)

        for r in sr["resistances"]:
            self.assertGreater(r["price"], 13.0)
            self.assertGreater(r["distance_pct"], 0)
        for s in sr["supports"]:
            self.assertLess(s["price"], 13.0)
            self.assertLess(s["distance_pct"], 0)

    def test_empty_frame_returns_empty_levels(self):
        import pandas as pd
        sr = technical.support_resistance(pd.DataFrame(), price=100.0)
        self.assertEqual(sr["resistances"], [])
        self.assertEqual(sr["supports"], [])

    def test_none_price_does_not_crash(self):
        sr = technical.support_resistance(self._frame([10, 11, 12]), price=None)
        self.assertEqual(sr["resistances"], [])


class TechnicalSignalTests(unittest.TestCase):
    """A transparent vote: the call must follow the shown votes, never lead."""

    EMPTY_SR = {"resistances": [], "supports": []}

    def test_all_bullish_gives_buy(self):
        latest = {"close": 110, "ma_fast": 105, "ma_slow": 100,
                  "macd": 2.0, "macd_signal": 1.0, "rsi": 58,
                  "bb_pct": 0.2, "trend": "Strong Uptrend", "adx": 30}
        sig = technical.technical_signal(latest, self.EMPTY_SR)
        self.assertEqual(sig["call"], "BUY")
        self.assertGreaterEqual(sig["net"], 2)

    def test_all_bearish_gives_sell(self):
        latest = {"close": 90, "ma_fast": 95, "ma_slow": 100,
                  "macd": 1.0, "macd_signal": 2.0, "rsi": 42,
                  "bb_pct": 0.85, "trend": "Downtrend", "adx": 30}
        sig = technical.technical_signal(latest, self.EMPTY_SR)
        self.assertEqual(sig["call"], "SELL")
        self.assertLessEqual(sig["net"], -2)

    def test_mixed_gives_hold(self):
        latest = {"close": 100, "ma_fast": 100, "ma_slow": 100,
                  "macd": 1.0, "macd_signal": 1.0, "rsi": 50,
                  "bb_pct": 0.5, "trend": "Unknown", "adx": 18}
        sig = technical.technical_signal(latest, self.EMPTY_SR)
        self.assertEqual(sig["call"], "HOLD")

    def test_the_call_matches_the_votes_shown(self):
        """The number of net votes must equal the sum of the shown votes.

        This is the anti-black-box guarantee: the badge cannot disagree with
        the reasons under it.
        """
        latest = {"close": 110, "ma_fast": 105, "ma_slow": 100,
                  "macd": 2.0, "macd_signal": 1.0, "rsi": 58,
                  "bb_pct": 0.5, "trend": "Uptrend", "adx": 22}
        sig = technical.technical_signal(latest, self.EMPTY_SR)
        self.assertEqual(sig["net"], sum(v["vote"] for v in sig["votes"]))
        self.assertEqual(sig["bullish"],
                         sum(1 for v in sig["votes"] if v["vote"] > 0))

    def test_the_caveat_is_always_present(self):
        """The signal must never ship without its 'not a validated edge' note."""
        sig = technical.technical_signal(
            {"close": 100, "ma_fast": 100, "ma_slow": 100}, self.EMPTY_SR)
        self.assertTrue(sig["caveat"])
        self.assertIn("E19", sig["caveat"])

    def test_a_single_indicator_never_decides(self):
        """One bullish vote is not enough to say BUY -- needs a margin of 2."""
        latest = {"close": 100, "ma_fast": 100, "ma_slow": 100,
                  "macd": 2.0, "macd_signal": 1.0, "rsi": 50, "bb_pct": 0.5}
        sig = technical.technical_signal(latest, self.EMPTY_SR)
        self.assertEqual(sig["call"], "HOLD")
        self.assertEqual(sig["bullish"], 1)


class TopTechnicalTests(unittest.TestCase):
    """The whole-market ranking: liquidity floor, ordering, and caveat."""

    def test_shape_and_ordering(self):
        import database
        try:
            n = len(database.load_instruments(equities_only=True))
        except Exception:                                          # noqa: BLE001
            self.skipTest("no database")
        if not n:
            self.skipTest("empty universe")

        rep = technical.top_technical(top=5)

        self.assertIn("top", rep)
        self.assertLessEqual(len(rep["top"]), 5)
        self.assertIn("caveat", rep)
        self.assertIn("E19", rep["caveat"])

        # scores descend
        scores = [r["score"] for r in rep["top"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

        # every ranked name cleared the liquidity floor -- the whole point
        for r in rep["top"]:
            self.assertGreaterEqual(r["turnover"], rep["min_turnover"])

        # strict JSON, no NaN reaches the browser
        json.dumps(rep, ensure_ascii=False, allow_nan=False)

    def test_the_floor_actually_excludes(self):
        """A floor that excludes nothing is not a floor. On real TSE data many
        names sit below MIN_DAILY_VALUE, so illiquid_skipped must be > 0 and
        the parts must never over-count the universe."""
        import database
        try:
            if not database.load_instruments(equities_only=True):
                self.skipTest("empty universe")
        except Exception:                                          # noqa: BLE001
            self.skipTest("no database")

        rep = technical.top_technical(top=10)
        c = rep["counts"]

        self.assertGreater(c["illiquid_skipped"], 0,
                           "the liquidity floor excluded nothing")
        self.assertGreaterEqual(c["liquid_scored"], len(rep["top"]))
        # scored + skipped buckets can be <= universe (some liquid names may be
        # refused by rank_stocks), but never more.
        self.assertLessEqual(
            c["liquid_scored"] + c["illiquid_skipped"] + c["no_data"],
            c["universe"])


class BestTechnicalInflowTests(unittest.TestCase):
    """The intersection: technically strong AND taking money in."""

    def _has_db(self):
        import database
        try:
            return bool(database.load_instruments(equities_only=True))
        except Exception:                                          # noqa: BLE001
            return False

    def test_every_row_meets_both_conditions(self):
        if not self._has_db():
            self.skipTest("no database")

        rep = technical.best_technical_inflow(top=15)

        for r in rep["top"]:
            # strong technical
            self.assertGreaterEqual(r["score"], rep["min_score"])
            # positive net individual inflow -- the AND half
            self.assertGreater(r["net_pct"], 0)
            # liquid
            self.assertGreaterEqual(r["turnover"], rep["min_turnover"])

    def test_counts_are_a_shrinking_funnel(self):
        """liquid >= strong_technical >= strong_and_inflow: each condition can
        only remove names, never add them."""
        if not self._has_db():
            self.skipTest("no database")

        c = technical.best_technical_inflow(top=15)["counts"]
        self.assertGreaterEqual(c["liquid"], c["strong_technical"])
        self.assertGreaterEqual(c["strong_technical"], c["strong_and_inflow"])

    def test_json_safe_and_caveated(self):
        if not self._has_db():
            self.skipTest("no database")

        rep = technical.best_technical_inflow(top=15)
        json.dumps(rep, ensure_ascii=False, allow_nan=False)
        self.assertIn("caveat", rep)
        # the doubled caveat names the flow research specifically
        self.assertIn("E21", rep["caveat"])

    def test_ranked_by_score_descending(self):
        if not self._has_db():
            self.skipTest("no database")
        top = technical.best_technical_inflow(top=15)["top"]
        scores = [r["score"] for r in top]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
