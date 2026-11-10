"""fundamentals.from_history: the screen's price-path metrics.

Two defects, both measured on the live database on 2026-11-11:

  * The four price-path metrics -- volatility_60, return_60d, headroom_pct,
    range_position -- were computed on raw bars, so a capital increase inside
    the window read as a crash. 57 of 697 live equities carried one inside the
    61-bar window and 55 changed Stability band; تپسی read 175.8% volatility
    and -72.6% over 60 sessions against 6.4% and +83.2% adjusted.
  * Volatility was annualised with sqrt(250) against the configured 240.
"""

import math
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import fundamentals  # noqa: E402
from backtest import back_adjust  # noqa: E402

AS_OF = "2026-11-10"


def bars(n=200, seed=5, split_at=None):
    rng = np.random.default_rng(seed)
    moves = np.clip(rng.normal(0.0005, 0.015, n), -0.03, 0.03)
    close = 10_000.0 * np.cumprod(1.0 + moves)
    if split_at is not None:
        close[split_at:] *= 0.1          # a 1:10 split, unmarked in the feed
    return pd.DataFrame({
        "date": pd.bdate_range(end=AS_OF, periods=n),
        "open": close,
        "high": close * (1 + rng.uniform(0.002, 0.02, n)),
        "low": close * (1 - rng.uniform(0.002, 0.02, n)),
        "close": close,
        "volume": 1_000_000.0,
        "value": 2e10,
    })


def by_hand(frame):
    """The four price-path metrics, computed directly on `frame`."""

    closes, last = frame["close"], float(frame["close"].iloc[-1])
    window = closes.tail(250)
    ceiling = frame["high"].iloc[-(config.HEADROOM_WINDOW + 1):-1].max()
    return {
        "volatility_60": (closes.pct_change().tail(60).dropna().std()
                          * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100),
        "return_60d": round((last - closes.iloc[-61]) / closes.iloc[-61] * 100, 2),
        "headroom_pct": round((ceiling - last) / last * 100, 2),
        "range_position": round((last - window.min())
                                / (window.max() - window.min()), 4),
    }


class PricePathTests(unittest.TestCase):

    def test_a_split_inside_the_window_is_not_read_as_a_crash(self):
        raw = bars(split_at=185)            # fifteen sessions before the end
        adjusted, events = back_adjust(raw)
        self.assertEqual(len(events), 1)

        got = fundamentals.from_history(raw, as_of=AS_OF)
        want = by_hand(adjusted)

        self.assertAlmostEqual(got["volatility_60"], want["volatility_60"],
                               places=9)
        for key in ("return_60d", "headroom_pct", "range_position"):
            self.assertEqual(got[key], want[key], key)

        # What the raw bars said instead: the split as a 90% loss.
        shipped = by_hand(raw)
        self.assertLess(shipped["return_60d"], -80)
        self.assertGreater(shipped["volatility_60"], 5 * got["volatility_60"])

        # The newest bar is the anchor, so the price the screen quotes is today's.
        self.assertEqual(got["close"], float(raw["close"].iloc[-1]))

    def test_a_series_with_no_gap_reads_exactly_as_raw(self):
        raw = bars()
        self.assertEqual(back_adjust(raw)[1], [])

        got = fundamentals.from_history(raw, as_of=AS_OF)
        want = by_hand(raw)

        for key in ("return_60d", "headroom_pct", "range_position"):
            self.assertEqual(got[key], want[key], key)

    def test_volatility_is_annualised_with_the_configured_session_count(self):
        raw = bars()

        got = fundamentals.from_history(raw, as_of=AS_OF)["volatility_60"]
        daily = raw["close"].pct_change().tail(60).dropna().std()

        self.assertAlmostEqual(
            got, daily * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100, places=9)

    def test_liquidity_is_what_actually_traded(self):
        """Without a value column, turnover is volume x the RAW close: the
        sessions before a split really did change hands at the old price."""

        raw = bars(split_at=185).drop(columns=["value"])

        got = fundamentals.from_history(raw, as_of=AS_OF)["avg_value_20"]

        self.assertEqual(got, float((raw["volume"] * raw["close"]).tail(20).mean()))


if __name__ == "__main__":
    unittest.main()
