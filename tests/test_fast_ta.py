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


if __name__ == "__main__":
    unittest.main()
