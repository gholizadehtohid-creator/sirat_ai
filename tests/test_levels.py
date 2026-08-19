"""Levels must be found where they actually are, and never from future bars.

The failure this guards against is quiet: a pivot detector that peeks at bars
after the signal produces beautiful backtests and nothing else.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import levels


def frame(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "open": closes,
        "high": highs if highs is not None else [c * 1.005 for c in closes],
        "low": lows if lows is not None else [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


def zigzag(turns, step=10, start=1000):
    """A series that actually oscillates.

    A single peak with monotonic sides yields exactly ONE pivot, because a
    rising run has no local low and a falling run has no local high. Anything
    testing swings, retracements or leg structure needs several completed
    turns, and building them here rather than by hand keeps that explicit.
    """

    closes = [start]

    for target in turns:
        current = closes[-1]
        direction = step if target > current else -step
        while abs(closes[-1] - target) >= step:
            closes.append(closes[-1] + direction)
        # Flat bars either side so the turn is a confirmed pivot, not a corner
        # the detector has to guess at.
        closes.extend([closes[-1]] * 6)

    return closes


def peak_series():
    """Four completed turns: up, down, up, down."""
    return zigzag([1400, 900, 1300, 1000])


class TestSwings(unittest.TestCase):

    def test_a_clear_peak_is_found(self):
        points = levels.swings(frame(peak_series()), depth=5)

        self.assertTrue(points)

        highs = [p for p in points if p["kind"] == "high"]
        lows = [p for p in points if p["kind"] == "low"]

        self.assertTrue(highs)
        self.assertTrue(lows)

        self.assertAlmostEqual(max(p["price"] for p in highs), 1400 * 1.005,
                               delta=20)

    def test_pivots_alternate(self):
        points = levels.swings(frame(peak_series()), depth=5)

        kinds = [p["kind"] for p in points]

        for a, b in zip(kinds, kinds[1:]):
            self.assertNotEqual(a, b, "two pivots of one kind in a row")

    def test_no_pivot_is_reported_from_the_unconfirmed_tail(self):
        """A pivot needs `depth` bars after it, which the last bars lack."""

        data = frame(peak_series())

        points = levels.swings(data, depth=5)

        if points:
            self.assertLessEqual(points[-1]["index"], len(data) - 6)

    def test_a_flat_series_has_no_swings(self):
        points = levels.swings(frame([1000.0] * 60), depth=5)

        # A flat line has no pivot that stands out; whatever is returned must
        # not claim alternating structure.
        self.assertLessEqual(len(points), 1)

    def test_short_input_is_handled(self):
        self.assertEqual(levels.swings(frame([1000.0] * 4), depth=5), [])
        self.assertEqual(levels.swings(None), [])


class TestFibonacci(unittest.TestCase):

    def test_retracements_sit_inside_the_swing(self):
        result = levels.fibonacci(frame(peak_series()), depth=5)

        self.assertIsNotNone(result)

        swing = result["swing"]
        low = min(swing["from"], swing["to"])
        high = max(swing["from"], swing["to"])

        for name, value in result["levels"].items():
            if name.startswith("retr_"):
                self.assertGreaterEqual(value, low - 1)
                self.assertLessEqual(value, high + 1)

    def test_the_618_level_is_where_arithmetic_puts_it(self):
        result = levels.fibonacci(frame(zigzag([2000, 1500, 1900])), depth=5)

        self.assertIsNotNone(result)

        swing = result["swing"]
        low = min(swing["from"], swing["to"])
        high = max(swing["from"], swing["to"])
        size = high - low

        expected = (high - size * 0.618 if swing["direction"] == "up"
                    else low + size * 0.618)

        self.assertAlmostEqual(result["levels"]["retr_0.618"], expected, places=6)

    def test_extensions_lie_beyond_the_swing(self):
        result = levels.fibonacci(frame(peak_series()), depth=5)

        swing = result["swing"]
        high = max(swing["from"], swing["to"])
        low = min(swing["from"], swing["to"])

        if swing["direction"] == "up":
            self.assertGreater(result["levels"]["ext_1.618"], high)
        else:
            self.assertLess(result["levels"]["ext_1.618"], low)


class TestIchimoku(unittest.TestCase):

    def test_lines_are_midpoints_of_their_windows(self):
        closes = [1000 + i for i in range(60)]
        data = frame(closes)

        result = levels.ichimoku(data)

        self.assertIsNotNone(result)

        highs, lows = data["high"], data["low"]

        self.assertAlmostEqual(
            result["tenkan"],
            (highs.tail(9).max() + lows.tail(9).min()) / 2, places=6
        )
        self.assertAlmostEqual(
            result["kijun"],
            (highs.tail(26).max() + lows.tail(26).min()) / 2, places=6
        )

    def test_cloud_boundaries_are_ordered(self):
        result = levels.ichimoku(frame(peak_series()))

        self.assertGreaterEqual(result["cloud_top"], result["cloud_bottom"])

    def test_short_history_returns_nothing_rather_than_a_guess(self):
        self.assertIsNone(levels.ichimoku(frame([1000.0] * 20)))

    def test_a_rising_series_sits_above_its_cloud(self):
        result = levels.ichimoku(frame([1000 + i * 5 for i in range(80)]))

        self.assertTrue(result["above_cloud"])


class TestOverhead(unittest.TestCase):
    """The number the rest of the system would actually consume."""

    def test_a_price_below_a_prior_high_has_headroom(self):
        result = levels.overhead(frame(peak_series()))

        self.assertIsNotNone(result)
        self.assertIsNotNone(result["nearest_pct"])
        self.assertGreater(result["nearest_pct"], 0)

    def test_a_price_at_all_time_highs_has_no_meaningful_overhead(self):
        rising = frame([1000 + i * 10 for i in range(120)])

        result = levels.overhead(rising)

        # No prior pivot can sit above a price that has never been exceeded.
        self.assertIsNone(result["by_source"].get("pivot"))

        # The rolling high may clear the close by a hair -- yesterday's high is
        # above today's close whenever the daily advance is smaller than the
        # bar's own range. That is arithmetic, not resistance, so what matters
        # is that it is negligible rather than absent.
        rolling = result["by_source"].get("high_60")

        if rolling is not None:
            self.assertLess(rolling, 1.0)

    def test_nearest_is_the_smallest_live_gap(self):
        result = levels.overhead(frame(peak_series()))

        live = [v for v in result["by_source"].values() if v is not None]

        self.assertEqual(result["nearest_pct"], min(live))

    def test_every_reported_gap_is_above_the_price(self):
        result = levels.overhead(frame(peak_series()))

        for name, gap in result["by_source"].items():
            if gap is not None:
                self.assertGreater(gap, 0, "%s reported a level below price" % name)

    def test_empty_input_is_survivable(self):
        self.assertIsNone(levels.overhead(None))
        self.assertIsNone(levels.overhead(pd.DataFrame()))


class TestStructure(unittest.TestCase):

    def test_legs_alternate_direction(self):
        result = levels.structure(frame(peak_series()), depth=5)

        self.assertIsNotNone(result)

        directions = [leg["direction"] for leg in result["legs"]]

        for a, b in zip(directions, directions[1:]):
            self.assertNotEqual(a, b)

    def test_short_history_returns_nothing(self):
        self.assertIsNone(levels.structure(frame([1000.0] * 10), depth=5))


if __name__ == "__main__":
    unittest.main()
