"""A close at the top of the daily band blocks a new entry (2026-11-11).

9 of the basket's 10 names had closed +2.8% to +3.0% on a ±3% band; a plan
priced at that close waits in the next morning's buy queue.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import swing_advisor  # noqa: E402


def bars(last_close, last_high):
    return pd.DataFrame({"close": [100.0, 100.0, last_close],
                         "high": [101.0, 101.0, last_high],
                         "low": [99.0, 99.0, 99.5],
                         "volume": [1e6, 1e6, 1e6]})


class BandTopTests(unittest.TestCase):

    def test_a_close_at_the_band_top_blocks(self):
        reason = swing_advisor._entry_blocker(bars(102.9, 102.9), band_pct=3.0)
        self.assertIn("سقف دامنه", reason or "")

    def test_a_close_below_the_high_does_not(self):
        self.assertIsNone(swing_advisor._entry_blocker(bars(102.9, 104.0),
                                                       band_pct=3.0))

    def test_a_wider_band_does_not(self):
        self.assertIsNone(swing_advisor._entry_blocker(bars(102.9, 102.9),
                                                       band_pct=5.0))

    def test_without_a_band_nothing_changes(self):
        # The backtests pass no band: their behaviour must not move.
        self.assertIsNone(swing_advisor._entry_blocker(bars(102.9, 102.9)))


if __name__ == "__main__":
    unittest.main()
