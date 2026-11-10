"""fast_ta is equal to ta's ATR and ADX bit for bit -- the only reason it may exist."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ta.trend import ADXIndicator  # noqa: E402
from ta.volatility import AverageTrueRange  # noqa: E402

import fast_ta  # noqa: E402


def bars(seed, length, gaps=False, flat=False):
    rng = np.random.default_rng(seed)
    close = pd.Series(rng.lognormal(8, 0.3, length))
    high = close * (1 + rng.random(length) * 0.05)
    low = close * (1 - rng.random(length) * 0.05)
    if gaps:
        high.iloc[rng.integers(0, length, 5)] = np.nan
        low.iloc[rng.integers(0, length, 3)] = np.nan
    if flat:
        low = high.copy()
    return high, low, close


class FastTaTests(unittest.TestCase):

    def assert_identical(self, ours, theirs):
        self.assertEqual(ours.name, theirs.name)
        self.assertTrue(ours.index.equals(theirs.index))
        self.assertEqual(ours.to_numpy().tobytes(), theirs.to_numpy().tobytes())

    def cases(self):
        for seed in range(40):
            for gaps, flat in ((False, False), (True, False), (False, True)):
                yield bars(seed, 30 + seed * 9, gaps=gaps, flat=flat)

    def test_atr_matches_ta_bit_for_bit(self):
        for high, low, close in self.cases():
            for window in (7, 14):
                self.assert_identical(
                    fast_ta.average_true_range(high, low, close, window),
                    AverageTrueRange(high, low, close, window=window)
                    .average_true_range())

    def test_adx_and_both_di_match_ta_bit_for_bit(self):
        for high, low, close in self.cases():
            for window in (7, 14):
                ours = fast_ta.adx(high, low, close, window)
                ref = ADXIndicator(high, low, close, window=window)
                for got, want in zip(ours, (ref.adx(), ref.adx_pos(), ref.adx_neg())):
                    self.assert_identical(got, want)

    def test_a_non_default_index_is_kept(self):
        high, low, close = bars(7, 60)
        index = pd.date_range("2026-01-01", periods=60)
        for series in (high, low, close):
            series.index = index
        self.assertTrue(fast_ta.adx(high, low, close)[0].index.equals(index))
        self.assertTrue(fast_ta.average_true_range(high, low, close).index.equals(index))


class ShortSeriesTests(unittest.TestCase):
    """Below the seed, ta raises; fast_ta answers NaN (the one departure).

    One young listing above the liquidity floor used to take a whole-market
    card down with it: the IndexError escaped top_technical and the route
    answered 500 for every name.
    """

    def assert_nan_like(self, got, close, name):
        self.assertEqual(got.name, name)
        self.assertTrue(got.index.equals(close.index))
        self.assertTrue(got.isna().all())

    def test_atr_shorter_than_its_window_is_nan_not_an_exception(self):
        for length in (1, 5, 13):
            high, low, close = bars(length, length)
            with self.assertRaises(IndexError):
                AverageTrueRange(high, low, close, window=14).average_true_range()
            self.assert_nan_like(
                fast_ta.average_true_range(high, low, close, 14), close, "atr")

    def test_adx_shorter_than_twice_its_window_is_nan_not_an_exception(self):
        # ADX's first value sits `window` places into a series already cut by
        # window - 1: ta fails up to 27 bars at the default 14, not just 13.
        for length in (1, 5, 13, 14, 27):
            high, low, close = bars(length, length)
            with self.assertRaises((IndexError, ValueError)):
                ADXIndicator(high, low, close, window=14).adx()
            got = fast_ta.adx(high, low, close, 14)
            for series, name in zip(got, ("adx", "adx_pos", "adx_neg")):
                self.assert_nan_like(series, close, name)

    def test_the_first_length_ta_can_compute_is_still_bit_identical(self):
        for window in (7, 14):
            high, low, close = bars(window, window)
            self.assertEqual(
                fast_ta.average_true_range(high, low, close, window)
                .to_numpy().tobytes(),
                AverageTrueRange(high, low, close, window=window)
                .average_true_range().to_numpy().tobytes())

            high, low, close = bars(window, 2 * window)
            ref = ADXIndicator(high, low, close, window=window)
            for got, want in zip(fast_ta.adx(high, low, close, window),
                                 (ref.adx(), ref.adx_pos(), ref.adx_neg())):
                self.assertEqual(got.to_numpy().tobytes(),
                                 want.to_numpy().tobytes())

    def test_calculate_indicators_survives_a_ten_bar_listing(self):
        import indicators

        high, low, close = bars(3, 10)
        frame = pd.DataFrame({
            "date": pd.date_range("2026-11-01", periods=10),
            "open": close, "high": high, "low": low, "close": close,
            "volume": [1000.0] * 10})

        enriched = indicators.calculate_indicators(frame)

        self.assertEqual(len(enriched), 10)
        for column in indicators.RANGE_COLUMNS:
            self.assertTrue(enriched[column].isna().all(), column)


if __name__ == "__main__":
    unittest.main()
