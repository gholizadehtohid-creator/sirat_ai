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

    def test_every_level_carries_a_touch_count(self):
        closes = [10, 11, 12, 15, 20, 15, 12, 11, 13, 14, 13, 12, 11, 13]
        sr = technical.support_resistance(self._frame(closes), price=13.0)

        for level in sr["resistances"] + sr["supports"]:
            self.assertGreaterEqual(level["touches"], 1)


class ClusterLevelsTests(unittest.TestCase):
    """Pivots close together are ONE level tested repeatedly, not several.

    Tested directly rather than through a price series, because the property
    that matters -- that a shelf does not consume the whole ladder -- needs
    pivots placed to the basis point, which a generated series cannot promise.
    """

    def _entries(self, prices):
        return [{"price": p, "date": "2025-01-%02d" % (i + 1)}
                for i, p in enumerate(prices)]

    def test_neighbours_inside_the_tolerance_become_one_level(self):
        # Four touches inside 0.4%, then a genuinely separate level.
        out = technical._cluster_levels(
            self._entries([100.0, 100.1, 100.2, 100.3, 108.0]), price=99.0)

        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["touches"], 4)
        self.assertEqual(out[1]["touches"], 1)
        # The shelf is the mean of its members, not the first or the extreme.
        self.assertAlmostEqual(out[0]["price"], 100.15, places=2)

    def test_levels_beyond_the_tolerance_stay_separate(self):
        out = technical._cluster_levels(
            self._entries([100.0, 102.0, 104.0]), price=99.0)

        self.assertEqual(len(out), 3)
        self.assertTrue(all(o["touches"] == 1 for o in out))

    def test_the_date_kept_is_the_most_recent_touch(self):
        """A level last tested this month and one last tested years ago are
        not equally live, and only the newest date can say which this is."""

        entries = [{"price": 100.0, "date": "2023-04-01"},
                   {"price": 100.2, "date": "2025-06-30"},
                   {"price": 100.1, "date": "2024-01-15"}]

        out = technical._cluster_levels(entries, price=99.0)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["date"], "2025-06-30")

    def test_a_shelf_cannot_crowd_out_the_rest_of_the_ladder(self):
        """The regression this exists for: five touches of one price used to
        fill every resistance slot, hiding every other level above."""

        prices = [100.0, 100.1, 100.2, 100.3, 100.4, 110.0, 120.0]
        out = technical._cluster_levels(self._entries(prices), price=99.0)

        self.assertEqual(len(out[:technical.LEVELS_EACH_SIDE]), 3)
        self.assertEqual([round(o["price"]) for o in out], [100, 110, 120])

    def test_no_entries_is_no_levels(self):
        self.assertEqual(technical._cluster_levels([], price=100.0), [])


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

    def test_both_sides_of_a_squeeze_are_voted(self):
        """A price boxed between a near wall and a near floor casts BOTH.

        The regression this exists for: the two proximity checks were an
        if/elif, so a price one percent under a ceiling and one percent over a
        floor cast only the bearish vote -- and a vote that is never cast
        leaves no trace in the table of votes, so the missing half was
        invisible to the reader as well as to the tally.
        """

        latest = {"close": 100, "ma_fast": 100, "ma_slow": 100,
                  "macd": 1.0, "macd_signal": 1.0, "rsi": 50, "bb_pct": 0.5}
        sr = {"resistances": [{"price": 101, "distance_pct": 1.0}],
              "supports": [{"price": 99, "distance_pct": -1.0}]}

        sig = technical.technical_signal(latest, sr)
        names = [v["name"] for v in sig["votes"]]

        self.assertIn("مقاومت نزدیک", names)
        self.assertIn("حمایت نزدیک", names)
        # The box nets to nothing, which is the honest reading of a box.
        self.assertEqual(sig["net"], 0)

    def test_strength_is_the_margin_over_the_votes_cast(self):
        latest = {"close": 110, "ma_fast": 105, "ma_slow": 100,
                  "macd": 2.0, "macd_signal": 1.0, "rsi": 58,
                  "bb_pct": 0.5, "adx": 30}
        sig = technical.technical_signal(latest, self.EMPTY_SR)

        self.assertAlmostEqual(sig["strength"],
                               round(sig["net"] / sig["total"], 2))

    def test_a_single_indicator_never_decides(self):
        """One bullish vote is not enough to say BUY -- needs a margin of 2."""
        latest = {"close": 100, "ma_fast": 100, "ma_slow": 100,
                  "macd": 2.0, "macd_signal": 1.0, "rsi": 50, "bb_pct": 0.5}
        sig = technical.technical_signal(latest, self.EMPTY_SR)
        self.assertEqual(sig["call"], "HOLD")
        self.assertEqual(sig["bullish"], 1)


class DirectionalVoteTests(unittest.TestCase):
    """ADX says how strong; DI says which way. The vote needs both."""

    def test_no_di_means_no_vote_not_a_neutral_one(self):
        """A series with no high/low range has no DI at all. An invented
        neutral vote there would dilute a real tally."""

        self.assertIsNone(technical._directional_vote({"adx": 30}))

    def test_a_trendless_tape_has_no_direction_to_vote_on(self):
        vote = technical._directional_vote(
            {"adx": 12, "di_plus": 30, "di_minus": 10})

        self.assertEqual(vote["vote"], 0)

    def test_direction_follows_di_when_a_trend_exists(self):
        up = technical._directional_vote(
            {"adx": 30, "di_plus": 30, "di_minus": 10})
        down = technical._directional_vote(
            {"adx": 30, "di_plus": 10, "di_minus": 30})

        self.assertEqual(up["vote"], +1)
        self.assertEqual(down["vote"], -1)


class VolumeVoteTests(unittest.TestCase):
    """Is the move being paid for -- and the one case that is not a move."""

    def _frame(self, closes, volumes, average):
        import pandas as pd
        return pd.DataFrame({
            "close": closes, "volume": volumes,
            "VOLUME_MA": [average] * len(closes),
        })

    def test_no_frame_means_no_vote(self):
        self.assertIsNone(technical._volume_vote(None))

    def test_rise_on_heavy_volume_is_bullish(self):
        frame = self._frame([100, 101, 102, 103, 104, 108],
                            [1000] * 6, 800)
        self.assertEqual(technical._volume_vote(frame)["vote"], +1)

    def test_rise_on_thin_volume_is_bearish(self):
        """The classic warning: an advance nobody is paying for."""

        frame = self._frame([100, 101, 102, 103, 104, 108],
                            [400] * 6, 800)
        self.assertEqual(technical._volume_vote(frame)["vote"], -1)

    def test_an_untraded_session_is_neutral_not_bearish(self):
        """A zero-volume last bar means the instrument did not trade.

        Scored as a ratio it reads as 0% of average and casts a bearish vote
        -- a claim about supply and demand on a day neither appeared. TSETMC
        emits such a row for every session a listed name is halted or simply
        untraded, so this is common, not exotic.
        """

        frame = self._frame([100, 101, 102, 103, 104, 108],
                            [1000, 1000, 1000, 1000, 1000, 0], 800)

        vote = technical._volume_vote(frame)

        self.assertEqual(vote["vote"], 0)
        self.assertIn("معامله", vote["reason"])


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


class AdjustedBarsTests(unittest.TestCase):
    """Every technical read back-adjusts before computing, as the v2 frame does.

    Built on a throwaway database: one name with an unmarked 1:10 unit split
    fifteen sessions before the end (طلا's, on 2026-11-02, read RSI 7.5 and an
    ATR of 57% of price on raw bars), one with no gap at all, and one listed
    ten sessions ago -- too young for ATR and ADX, which used to raise inside
    fast_ta and take a whole-market card down with it.
    """

    @staticmethod
    def bars(n, seed, split_at=None):
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(seed)
        moves = np.clip(rng.normal(0.0005, 0.015, n), -0.03, 0.03)
        close = 10_000.0 * np.cumprod(1.0 + moves)
        if split_at is not None:
            close[split_at:] *= 0.1
        return pd.DataFrame({
            "date": pd.bdate_range(end="2026-11-10", periods=n),
            "open": close,
            "high": close * (1 + rng.uniform(0.002, 0.02, n)),
            "low": close * (1 - rng.uniform(0.002, 0.02, n)),
            "close": close,
            "volume": 1_000_000.0,
            "value": 2e10,
            "trades": 500.0,
        })

    def setUp(self):
        import tempfile

        import database

        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        self.raw = {
            "split": self.bars(240, 1, split_at=225),
            "plain": self.bars(240, 2),
            "young": self.bars(10, 3),
        }

        conn = database._connect()
        for code, frame in self.raw.items():
            conn.execute("INSERT INTO instruments (ins_code, symbol_fa, name_fa, "
                         "kind, is_equity) VALUES (?, ?, ?, 'equity', 1)",
                         (code, "T" + code, code))
            for row in frame.itertuples(index=False):
                conn.execute(
                    "INSERT INTO price_history (ins_code, date, open, high, low, "
                    "close, volume, value, trades) VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, row.date.strftime("%Y-%m-%d"), row.open, row.high,
                     row.low, row.close, row.volume, row.value, row.trades))
        conn.commit()
        conn.close()

        import indicators
        self.need = (indicators.required_bars(indicators.DEFAULT_PARAMS)
                     + technical.SERIES_BARS)

    def tearDown(self):
        import database

        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def test_the_technical_tab_reads_back_adjusted_bars(self):
        import database
        import indicators
        from backtest import back_adjust

        raw = database.load_history_by_inscode("split", limit=self.need)
        adjusted, events = back_adjust(raw)
        self.assertEqual(len(events), 1)

        want = indicators.calculate_indicators(adjusted).iloc[-1]
        shipped = indicators.calculate_indicators(raw).iloc[-1]

        rep = technical.analyse("Tsplit")

        # The newest bar is the anchor: today's price is never rescaled.
        self.assertEqual(rep["latest"]["close"], float(raw["close"].iloc[-1]))
        for key, column in technical.LATEST_FIELDS:
            self.assertEqual(rep["latest"][key], technical._finite(want[column]),
                             key)

        # ...and what it replaced really was the split, not the market: an ATR
        # ten times too wide and a 50-session average far above the price.
        self.assertGreater(shipped["ATR_PCT"], 10 * want["ATR_PCT"])
        self.assertGreater(shipped["MA_SLOW"], 3 * rep["latest"]["close"])
        for level in rep["levels"]["resistances"]:
            self.assertLess(level["distance_pct"], 50)
        json.dumps(rep, ensure_ascii=False, allow_nan=False)

    def test_a_name_with_no_gap_reads_exactly_as_before(self):
        """Adjusting a series with no corporate action changes nothing at all."""

        import database
        import indicators
        import pandas as pd
        from backtest import back_adjust

        raw = database.load_history_by_inscode("plain", limit=self.need)
        adjusted, events = back_adjust(raw)

        self.assertEqual(events, [])
        pd.testing.assert_frame_equal(indicators.calculate_indicators(adjusted),
                                      indicators.calculate_indicators(raw),
                                      check_exact=True)

        want = indicators.calculate_indicators(raw).iloc[-1]
        rep = technical.analyse("Tplain")
        for key, column in technical.LATEST_FIELDS:
            self.assertEqual(rep["latest"][key], technical._finite(want[column]),
                             key)

    def test_every_whole_market_card_hands_the_indicators_continuous_bars(self):
        import config
        import indicators
        import numpy as np
        from unittest import mock

        real = indicators.calculate_indicators
        seen = []

        def spy(df, params=None):
            seen.append(df["close"].astype(float).to_numpy())
            return real(df, params)

        split_close = float(self.raw["split"]["close"].iloc[-1])
        limit = config.PRICE_ADJUST_THRESHOLD / 100.0

        for name, run in (
                ("top_technical", lambda: technical.top_technical(top=10)),
                ("best_technical_inflow",
                 lambda: technical.best_technical_inflow(top=10)),
                ("analyse", lambda: technical.analyse("Tsplit"))):
            with self.subTest(path=name):
                seen.clear()
                with mock.patch.object(indicators, "calculate_indicators", spy):
                    run()
                self.assertTrue(any(c[-1] == split_close for c in seen),
                                "%s never analysed the split name" % name)
                for closes in seen:
                    moves = np.abs(closes[1:] / closes[:-1] - 1.0)
                    self.assertLessEqual(moves.max(), limit, name)

    def test_the_top_list_ranks_the_split_name_on_adjusted_bars(self):
        import database
        import indicators
        import ranking
        from backtest import back_adjust

        tail = database.load_history_tail(["split"], technical.SCAN_BARS)["split"]
        want = ranking.rank_stocks(
            indicators.calculate_indicators(back_adjust(tail)[0]))
        raw = ranking.rank_stocks(indicators.calculate_indicators(tail))

        row = next(r for r in technical.top_technical(top=10)["top"]
                   if r["ins_code"] == "split")

        self.assertEqual((row["score"], row["rsi"]), (want["score"], want["rsi"]))
        self.assertNotEqual(row["rsi"], raw["rsi"])

    def test_a_ten_bar_listing_does_not_take_the_cards_down(self):
        top = technical.top_technical(top=10)
        inflow = technical.best_technical_inflow(top=10)
        rep = technical.analyse("Tyoung")

        self.assertEqual(top["counts"]["universe"], 3)
        self.assertEqual(inflow["counts"]["liquid"], 3)
        self.assertNotIn("error", rep)
        # Too young for a true range: absent, never a fabricated zero.
        self.assertIsNone(rep["latest"]["atr"])
        self.assertIsNone(rep["latest"]["adx"])
        for payload in (top, inflow, rep):
            json.dumps(payload, ensure_ascii=False, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
