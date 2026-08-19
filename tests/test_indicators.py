"""Indicator correctness, especially the cases that silently produce nonsense.

Every test here exists because the failure it guards against is invisible: a
NaN that compares False and reads as "bearish", a zero ATR that sizes an
infinite position, a division by a zero band width that yields inf. None of
these raise; they produce confident-looking numbers that are wrong.
"""

import math
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import indicators


def make_frame(bars=200, start=1000.0, drift=0.002, seed=7, with_range=True):
    """A synthetic OHLCV series with a real intraday range."""

    rng = np.random.default_rng(seed)

    steps = rng.normal(drift, 0.012, bars)
    close = start * np.cumprod(1 + steps)

    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=bars, freq="D"),
        "close": close,
    })

    if with_range:
        spread = close * rng.uniform(0.004, 0.02, bars)
        frame["high"] = close + spread
        frame["low"] = close - spread
        frame["open"] = close * (1 + rng.normal(0, 0.003, bars))
    else:
        frame["open"] = close

    frame["volume"] = rng.integers(50_000, 500_000, bars)

    return frame


class TestIndicatorColumns(unittest.TestCase):

    def setUp(self):
        self.frame = make_frame()
        self.enriched = indicators.calculate_indicators(self.frame)

    def test_all_columns_present(self):
        expected = [
            "MA_FAST", "MA_SLOW", "RSI", "MACD", "MACD_SIGNAL", "MACD_HIST",
            "VOLUME_MA", "ATR", "ATR_PCT", "ADX", "DI_PLUS", "DI_MINUS",
            "BB_UPPER", "BB_LOWER", "BB_MID", "BB_PCT", "BB_WIDTH", "OBV",
            "TREND",
        ]
        for column in expected:
            self.assertIn(column, self.enriched.columns, f"{column} missing")

    def test_atr_is_positive_and_finite_after_warmup(self):
        warmup = indicators.required_bars(indicators.DEFAULT_PARAMS)
        atr = self.enriched["ATR"].iloc[warmup:]
        self.assertTrue((atr > 0).all(), "ATR must be strictly positive")
        self.assertTrue(np.isfinite(atr).all(), "ATR must be finite")

    def test_atr_pct_is_scale_free(self):
        """A 100x price change must not change ATR as a percentage of price."""

        cheap = make_frame(start=1_000.0)
        expensive = cheap.copy()
        for column in ("close", "high", "low", "open"):
            expensive[column] = expensive[column] * 100

        a = indicators.calculate_indicators(cheap)["ATR_PCT"].iloc[-1]
        b = indicators.calculate_indicators(expensive)["ATR_PCT"].iloc[-1]

        self.assertAlmostEqual(a, b, places=6)

    def test_bollinger_pct_between_zero_and_one_for_typical_bars(self):
        warmup = indicators.required_bars(indicators.DEFAULT_PARAMS)
        pct = self.enriched["BB_PCT"].iloc[warmup:].dropna()
        self.assertGreater(len(pct), 0)
        # %B may exceed [0,1] on a breakout, but should mostly sit inside it.
        inside = ((pct >= -0.5) & (pct <= 1.5)).mean()
        self.assertGreater(inside, 0.95)

    def test_adx_within_bounds(self):
        adx = self.enriched["ADX"].dropna()
        self.assertTrue(((adx >= 0) & (adx <= 100)).all())

    def test_obv_is_cumulative(self):
        obv = self.enriched["OBV"].dropna()
        self.assertGreater(len(obv), 0)
        self.assertTrue(np.isfinite(obv).all())


class TestMissingRange(unittest.TestCase):
    """No high/low means ATR and ADX must be absent, not zero.

    A zero ATR divides into the risk budget and sizes an unbounded position.
    Absence has to stay absence all the way to the caller.
    """

    def setUp(self):
        frame = make_frame(with_range=False).drop(columns=["high", "low"],
                                                  errors="ignore")
        self.enriched = indicators.calculate_indicators(frame)

    def test_range_columns_are_nan_not_zero(self):
        for column in indicators.RANGE_COLUMNS:
            self.assertIn(column, self.enriched.columns)
            values = self.enriched[column]
            self.assertTrue(
                values.isna().all(),
                f"{column} should be entirely NaN when no high/low exists"
            )

    def test_price_indicators_still_computed(self):
        for column in ("RSI", "MACD", "MA_FAST", "BB_PCT"):
            self.assertTrue(
                self.enriched[column].notna().any(),
                f"{column} needs only closes and must still be computed"
            )


class TestFlatSeries(unittest.TestCase):
    """A share frozen at one price has no range -- not a range of zero width."""

    def test_bollinger_pct_is_nan_not_inf(self):
        bars = 120
        frame = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=bars, freq="D"),
            "open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0,
            "volume": 0,
        })

        enriched = indicators.calculate_indicators(frame)

        pct = enriched["BB_PCT"]

        self.assertFalse(
            np.isinf(pct.to_numpy(dtype=float)).any(),
            "zero band width must yield NaN, never infinity"
        )


class TestParams(unittest.TestCase):

    def test_clean_params_clamps_and_orders(self):
        params = indicators.clean_params({
            "ma_fast": 999, "ma_slow": 1, "atr_window": 0, "bb_window": 5,
        })

        self.assertLessEqual(params["ma_fast"], config.WINDOW_MAX)
        self.assertGreaterEqual(params["atr_window"], config.WINDOW_MIN)
        self.assertLess(params["ma_fast"], params["ma_slow"],
                        "a fast MA longer than the slow one inverts every rule")

    def test_defaults_do_not_lengthen_warmup(self):
        """Adding ATR/ADX/Bollinger must not cost extra warm-up bars.

        At the default windows the binding constraint is still the slow MA. If
        this breaks, every symbol loses usable history to indicators nothing
        asked for.
        """

        self.assertEqual(
            indicators.required_bars(indicators.DEFAULT_PARAMS),
            config.MA_SLOW,
        )

    def test_required_bars_respects_adx_double_smoothing(self):
        params = dict(indicators.DEFAULT_PARAMS)
        params.update({"ma_slow": 10, "macd_slow": 5, "macd_signal": 3,
                       "volume_ma": 5, "rsi_window": 5,
                       "atr_window": 5, "adx_window": 30, "bb_window": 5})

        self.assertEqual(indicators.required_bars(params), 60)


class TestEmptyInput(unittest.TestCase):

    def test_empty_frame_returns_empty(self):
        self.assertTrue(indicators.calculate_indicators(pd.DataFrame()).empty)

    def test_none_returns_empty(self):
        self.assertTrue(indicators.calculate_indicators(None).empty)


if __name__ == "__main__":
    unittest.main()
