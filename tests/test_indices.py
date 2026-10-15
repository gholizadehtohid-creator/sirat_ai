"""Tests for the index technical read behind /api/indices.

The index panel now runs the same analyses the share panel runs -- indicator
reading, pivot levels, a transparent vote, a target ladder -- so most of what
could break is shared code already covered by test_technical.py. What is tested
here is the part that is NOT shared, and that exists precisely because an index
is not a share:

  * the two index-only votes (market turnover, breadth) and the fact that
    adding one re-decides the call rather than just padding the tally;
  * the ladder being two-sided, with nothing above the price on the downside
    and nothing below it on the upside;
  * the measuring unit being the raw ATR, NOT risk_engine's stop distance --
    the 3% equity floor would swallow an index whose ATR is 1.4%;
  * duration not being clamped to the 5..40 session swing horizon, which is a
    statement about a position an index reader does not have.

And, as in test_technical.py, that the payload survives strict JSON: a NaN in
the response silently blanks the whole panel in the browser.
"""

import json
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import indices
import technical


def _vote(score):
    return technical._vote("t", score, "reason")


class FiniteTests(unittest.TestCase):

    def test_nan_and_inf_become_none(self):
        self.assertIsNone(indices._finite(float("nan")))
        self.assertIsNone(indices._finite(float("inf")))
        self.assertIsNone(indices._finite(-float("inf")))

    def test_strings_pass_through_untouched(self):
        self.assertEqual(indices._finite("Uptrend"), "Uptrend")

    def test_none_stays_none_not_zero(self):
        self.assertIsNone(indices._finite(None))


class AddVoteTests(unittest.TestCase):
    """An added vote must re-decide the call, not just pad the tally."""

    def test_none_vote_is_a_no_op(self):
        sig = technical.technical_signal({"close": 1}, None)
        before = dict(sig)
        self.assertIs(indices._add_vote(sig, None), sig)
        self.assertEqual(sig["net"], before["net"])
        self.assertEqual(len(sig["votes"]), len(before["votes"]))

    def test_the_added_vote_can_flip_the_call(self):
        """+1 short of BUY, one more bullish vote must produce BUY."""

        sig = {"votes": [_vote(1)], "conviction": "normal"}
        indices._add_vote(sig, _vote(0))
        self.assertEqual(sig["call"], technical.HOLD)

        indices._add_vote(sig, _vote(1))
        self.assertEqual(sig["call"], technical.BUY)
        self.assertEqual(sig["net"], 2)

    def test_counts_match_the_votes_shown(self):
        sig = {"votes": [_vote(1), _vote(-1)]}
        indices._add_vote(sig, _vote(1))

        self.assertEqual(sig["total"], len(sig["votes"]))
        self.assertEqual(sig["net"], sum(v["vote"] for v in sig["votes"]))
        self.assertEqual(sig["bullish"],
                         sum(1 for v in sig["votes"] if v["vote"] > 0))
        self.assertEqual(sig["bearish"],
                         sum(1 for v in sig["votes"] if v["vote"] < 0))
        self.assertEqual(sig["neutral"],
                         sum(1 for v in sig["votes"] if v["vote"] == 0))

    def test_the_persian_call_label_follows_the_call(self):
        sig = {"votes": [_vote(-1), _vote(-1)]}
        indices._add_vote(sig, _vote(0))
        self.assertEqual(sig["call"], technical.SELL)
        self.assertEqual(sig["call_fa"], technical.SIGNAL_FA[technical.SELL])


class TurnoverVoteTests(unittest.TestCase):
    """Is the move being paid for? The one reading a share does not need."""

    def _frame(self, closes, volumes, volume_ma):
        import pandas as pd
        n = len(closes)
        return pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=n, freq="D"),
            "close": closes,
            "volume": volumes,
            "VOLUME_MA": [volume_ma] * n,
        })

    def test_no_turnover_column_gives_no_vote(self):
        import pandas as pd
        frame = pd.DataFrame({"close": [1, 2, 3, 4, 5, 6, 7]})
        self.assertIsNone(indices._turnover_vote(frame))

    def test_a_missing_average_gives_no_vote_not_a_neutral_one(self):
        """An invented neutral vote would dilute a real tally."""

        frame = self._frame([100] * 8, [10] * 8, 0)
        self.assertIsNone(indices._turnover_vote(frame))

    def test_rise_on_heavy_turnover_is_bullish(self):
        closes = [100, 100, 100, 100, 100, 102, 104, 106]
        frame = self._frame(closes, [10] * 7 + [20], 10)

        vote = indices._turnover_vote(frame)

        self.assertIsNotNone(vote)
        self.assertEqual(vote["vote"], +1)

    def test_rise_on_thin_turnover_is_bearish(self):
        """The classic warning: an advance nobody is paying for."""

        closes = [100, 100, 100, 100, 100, 102, 104, 106]
        frame = self._frame(closes, [10] * 7 + [4], 10)

        vote = indices._turnover_vote(frame)

        self.assertEqual(vote["vote"], -1)

    def test_fall_on_heavy_turnover_is_bearish(self):
        closes = [100, 100, 100, 100, 100, 98, 96, 94]
        frame = self._frame(closes, [10] * 7 + [20], 10)

        self.assertEqual(indices._turnover_vote(frame)["vote"], -1)

    def test_a_flat_tape_is_neutral(self):
        frame = self._frame([100] * 8, [10] * 7 + [20], 10)

        self.assertEqual(indices._turnover_vote(frame)["vote"], 0)


class BreadthVoteTests(unittest.TestCase):

    def test_equal_weighted_leading_is_bullish(self):
        vote = indices._breadth_vote(indices.BREADTH_SPREAD_PP + 1)
        self.assertEqual(vote["vote"], +1)

    def test_equal_weighted_lagging_is_bearish(self):
        vote = indices._breadth_vote(-indices.BREADTH_SPREAD_PP - 1)
        self.assertEqual(vote["vote"], -1)

    def test_moving_together_is_neutral(self):
        self.assertEqual(indices._breadth_vote(0.0)["vote"], 0)

    def test_no_spread_gives_no_vote(self):
        self.assertIsNone(indices._breadth_vote(None))


class SessionsTests(unittest.TestCase):
    """Duration must not inherit the swing book's 5..40 session clamp."""

    def test_a_level_under_one_session_away_is_one_session(self):
        # 0.3% away at an ATR of 1.5%: a fraction of a session.
        sessions = indices._sessions_to(0.3, 1.5)

        self.assertEqual(sessions, 1)
        self.assertLess(sessions, 5)          # the swing floor must NOT apply

    def test_a_distant_level_is_not_capped_at_forty(self):
        sessions = indices._sessions_to(200.0, 1.0)

        self.assertGreater(sessions, 40)

    def test_no_atr_means_no_estimate(self):
        self.assertIsNone(indices._sessions_to(5.0, None))
        self.assertIsNone(indices._sessions_to(5.0, 0))

    def test_zero_distance_has_no_duration(self):
        self.assertIsNone(indices._sessions_to(0.0, 1.5))


class LadderTests(unittest.TestCase):

    CANDIDATES = [
        ("pivot", "سقف پیوتی", 110.0, None),
        ("projection", "1 برابر ATR بالاتر", 110.05, None),   # duplicate of ^
        ("rolling", "سقف 60 جلسه", 120.0, None),
        ("fib", "اصلاح 0.5 سوئینگ آخر", 95.0, None),
        ("projection", "1 برابر ATR پایین‌تر", 90.0, None),
    ]

    def test_upside_holds_nothing_at_or_below_the_price(self):
        rungs = indices._ladder(self.CANDIDATES, 100.0, 1.0, above=True)

        self.assertTrue(rungs)
        for rung in rungs:
            self.assertGreater(rung["price"], 100.0)
            self.assertGreater(rung["distance_pct"], 0)

    def test_downside_holds_nothing_at_or_above_the_price(self):
        rungs = indices._ladder(self.CANDIDATES, 100.0, 1.0, above=False)

        self.assertTrue(rungs)
        for rung in rungs:
            self.assertLess(rung["price"], 100.0)
            self.assertLess(rung["distance_pct"], 0)

    def test_nearest_first(self):
        rungs = indices._ladder(self.CANDIDATES, 100.0, 1.0, above=True)
        distances = [abs(r["distance_pct"]) for r in rungs]

        self.assertEqual(distances, sorted(distances))

    def test_a_projection_does_not_displace_a_real_level(self):
        """Two levels 0.05% apart are one level; the pivot is the survivor."""

        rungs = indices._ladder(self.CANDIDATES, 100.0, 1.0, above=True)
        near = [r for r in rungs if abs(r["price"] - 110.0) < 1.0]

        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["kind"], "pivot")

    def test_depth_is_bounded(self):
        many = [("projection", "x", 100.0 + i, None) for i in range(1, 40)]
        rungs = indices._ladder(many, 100.0, 1.0, above=True)

        self.assertLessEqual(len(rungs), indices.LADDER_DEPTH)


class TargetsTests(unittest.TestCase):
    """The ladder is two-sided and measured in the index's own volatility."""

    def _frame(self, closes):
        import pandas as pd
        n = len(closes)
        return pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=n, freq="D"),
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        })

    def _closes(self):
        # A shape with a real swing in it, ending mid-range so both sides of
        # the ladder have something to hold.
        return ([100 + i * 0.5 for i in range(40)]
                + [120 - i * 0.4 for i in range(30)]
                + [108 + i * 0.2 for i in range(30)])

    def test_no_price_means_no_ladder(self):
        self.assertIsNone(indices.index_targets(self._frame(self._closes()),
                                                {"close": None}, None))
        self.assertIsNone(indices.index_targets(self._frame(self._closes()),
                                                {"close": 0}, None))

    def test_the_unit_is_the_raw_atr_not_the_equity_stop_floor(self):
        """risk_engine floors a stop at 3%; an index ATR of ~0.7% must survive."""

        import config

        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        atr = price * 0.007
        targets = indices.index_targets(
            frame, {"close": price, "atr": atr, "atr_pct": 0.7}, None)

        self.assertAlmostEqual(targets["unit"], round(atr, 2), places=2)
        self.assertLess(targets["unit_pct"], config.RISK_MIN_STOP_PCT)

    def test_it_carries_no_position_fields(self):
        """No stop, no reward/risk: there is no position on an index."""

        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        targets = indices.index_targets(
            frame, {"close": price, "atr": price * 0.01, "atr_pct": 1.0}, None)

        for field in ("stop_loss", "risk_reward", "stop_distance_pct",
                      "min_risk_reward"):
            self.assertNotIn(field, targets)

    def test_both_sides_are_populated_and_correctly_signed(self):
        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        targets = indices.index_targets(
            frame, {"close": price, "atr": price * 0.01, "atr_pct": 1.0}, None)

        self.assertTrue(targets["upside"])
        self.assertTrue(targets["downside"])
        self.assertTrue(all(r["distance_pct"] > 0 for r in targets["upside"]))
        self.assertTrue(all(r["distance_pct"] < 0 for r in targets["downside"]))

    def test_the_headline_rungs_are_the_ends_of_the_ladders(self):
        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        targets = indices.index_targets(
            frame, {"close": price, "atr": price * 0.01, "atr_pct": 1.0}, None)

        self.assertEqual(targets["objective_1"], targets["upside"][0])
        self.assertEqual(targets["objective_2"], targets["upside"][1])
        self.assertEqual(targets["break_level"], targets["downside"][0])

    def test_no_atr_still_returns_a_structural_ladder(self):
        """Without ATR there are no projections, but the real levels remain."""

        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        targets = indices.index_targets(
            frame, {"close": price, "atr": None, "atr_pct": None}, None)

        self.assertIsNone(targets["unit"])
        self.assertTrue(any("ATR" in note for note in targets["notes"]))
        for rung in targets["upside"] + targets["downside"]:
            self.assertNotEqual(rung["kind"], "projection")

    def test_the_caveat_is_always_present(self):
        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        targets = indices.index_targets(
            frame, {"close": price, "atr": price * 0.01, "atr_pct": 1.0}, None)

        self.assertTrue(targets["caveat"])
        self.assertIn("ATR", targets["caveat"])

    def test_the_speed_limit_comes_from_the_index_not_the_price_band(self):
        """The ±3% band caps constituents; the index travels a fraction of it."""

        frame = self._frame(self._closes())
        price = float(frame["close"].iloc[-1])

        targets = indices.index_targets(
            frame, {"close": price, "atr": price * 0.01, "atr_pct": 1.0}, None)

        import config

        self.assertIsNotNone(targets["max_session_move_pct"])
        self.assertLess(targets["max_session_move_pct"],
                        config.PRICE_BAND_PCT)


class AnalyseContractTests(unittest.TestCase):
    """analyse() must never raise; the route jsonifies whatever comes back."""

    def test_unknown_index_returns_an_error_dict(self):
        out = indices.analyse("NOT_AN_INDEX")

        self.assertIn("error", out)
        self.assertIn("unknown index", out["error"])

    def test_a_stored_index_is_json_clean_and_carries_every_section(self):
        """Skips when no index history is stored (CI without data)."""

        import config

        report = indices.analyse_all()

        usable = [r for r in report["indices"] if not r.get("error")]

        if not usable:
            self.skipTest("no index history stored")

        # strict: allow_nan=False raises if any NaN/inf slipped through, which
        # is the failure that blanks the panel in a browser.
        json.dumps(report, ensure_ascii=False, allow_nan=False)

        for index in usable:
            for section in ("latest", "levels", "signal", "targets",
                            "series", "returns", "technical"):
                self.assertIn(section, index, index["name"])

            for key, value in index["latest"].items():
                self.assertTrue(
                    value is None or isinstance(value, (int, float, str)), key)
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value), key)

            # The series the chart reads must line up column for column, or
            # the browser silently plots one indicator against another's date.
            series = index["series"]
            length = len(series["dates"])
            for name, column in series.items():
                if column:
                    self.assertEqual(len(column), length, name)

        self.assertLessEqual(len(usable), len(config.INDEX_LIST))

    def test_the_shown_votes_add_up_to_the_call(self):
        """The anti-black-box guarantee, after the index-only votes are added."""

        report = indices.analyse_all()

        usable = [r for r in report["indices"] if r.get("signal")]

        if not usable:
            self.skipTest("no index history stored")

        for index in usable:
            sig = index["signal"]
            self.assertEqual(sig["net"], sum(v["vote"] for v in sig["votes"]))
            self.assertEqual(sig["total"], len(sig["votes"]))

            expected = (technical.BUY if sig["net"] >= 2
                        else technical.SELL if sig["net"] <= -2
                        else technical.HOLD)
            self.assertEqual(sig["call"], expected, index["name"])

    def test_the_breadth_vote_reaches_both_indices(self):
        report = indices.analyse_all()

        usable = [r for r in report["indices"] if r.get("signal")]

        if len(usable) < 2 or not report.get("breadth"):
            self.skipTest("breadth needs both index series")

        for index in usable:
            names = [v["name"] for v in index["signal"]["votes"]]
            self.assertTrue(any("عمق بازار" in n for n in names), index["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
