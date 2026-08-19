"""Guards for the portfolio backtest.

The first version of this module bought at the close of the same session whose
client-type figures selected the name -- figures published after that close.
It was worth 41 percentage points of invented return in both windows tested,
and nothing failed: the equity curve, the trade list and the benchmark
comparison were all internally consistent. Only the implausibility of the
result gave it away.

These tests exist so the next one is caught by something more reliable than
suspicion.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_portfolio as bp
import config


def frame(closes, opens=None, start="2025-01-01"):
    n = len(closes)
    dates = pd.bdate_range(start, periods=n)
    opens = opens or closes
    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": [c * 1.02 for c in closes],
        "low": [c * 0.98 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


class TestNextOpen(unittest.TestCase):
    """The tradeable price for a decision is never the same session's close."""

    def test_returns_the_following_session_open(self):
        prices = frame([100.0, 110.0, 120.0, 130.0],
                       opens=[101.0, 111.0, 121.0, 131.0])

        # A decision made on bar 1 fills on bar 2's open.
        price = bp._next_open(prices, prices["date"].iloc[1])

        self.assertEqual(price, 121.0)

    def test_never_returns_the_signal_bar_itself(self):
        prices = frame([100.0, 110.0, 120.0])

        for i in range(len(prices) - 1):
            price = bp._next_open(prices, prices["date"].iloc[i])
            self.assertNotEqual(
                price, prices["close"].iloc[i],
                "filled at the signal bar's own close — this is the look-ahead"
            )

    def test_no_next_session_returns_none(self):
        prices = frame([100.0, 110.0])

        self.assertIsNone(bp._next_open(prices, prices["date"].iloc[-1]))

    def test_a_date_between_sessions_takes_the_next_one(self):
        prices = frame([100.0, 110.0, 120.0],
                       opens=[100.0, 110.0, 120.0])

        gap = prices["date"].iloc[0] + pd.Timedelta(hours=12)

        self.assertEqual(bp._next_open(prices, gap), 110.0)

    def test_falls_back_to_close_when_open_is_missing(self):
        prices = frame([100.0, 110.0, 120.0])
        prices["open"] = [None, None, None]

        self.assertEqual(bp._next_open(prices, prices["date"].iloc[0]), 110.0)

    def test_empty_frame_is_not_a_crash(self):
        self.assertIsNone(bp._next_open(None, pd.Timestamp("2025-01-01")))
        self.assertIsNone(
            bp._next_open(pd.DataFrame(columns=["date"]),
                          pd.Timestamp("2025-01-01"))
        )


class TestSelection(unittest.TestCase):

    def readings(self, spec):
        """spec = [(code, sector, technical, flow)]"""
        return {
            code: {"symbol": code, "sector": sector, "technical": tech,
                   "flow": flow, "close": 1000.0, "atr": 30.0}
            for code, sector, tech, flow in spec
        }

    def test_gate_then_flow_ranks_by_flow_not_technical(self):
        readings = self.readings([
            ("A", "s1", 95, 40),
            ("B", "s2", 71, 90),
        ])

        picked = bp.select(readings, size=1, max_per_sector=5,
                           mode="gate_then_flow")

        self.assertEqual(picked[0][0], "B",
                         "flow must decide the order, not the technical score")

    def test_names_without_flow_are_skipped_in_flow_mode(self):
        readings = self.readings([("A", "s1", 95, None)])

        picked = bp.select(readings, size=5, max_per_sector=5,
                           mode="gate_then_flow")

        self.assertEqual(picked, [])

    def test_the_sector_cap_binds(self):
        readings = self.readings([
            ("A", "same", 90, 99),
            ("B", "same", 90, 98),
            ("C", "same", 90, 97),
            ("D", "other", 90, 10),
        ])

        picked = bp.select(readings, size=4, max_per_sector=2,
                           mode="gate_then_flow")

        sectors = [r["sector"] for _, r in picked]

        self.assertEqual(sectors.count("same"), 2)
        self.assertIn("other", sectors)

    def test_size_is_respected(self):
        readings = self.readings([
            (str(i), "s%d" % i, 90, 50 + i) for i in range(20)
        ])

        picked = bp.select(readings, size=7, max_per_sector=5,
                           mode="gate_then_flow")

        self.assertEqual(len(picked), 7)

    def test_blend_mode_ranks_by_technical(self):
        readings = self.readings([
            ("A", "s1", 95, 10),
            ("B", "s2", 71, 99),
        ])

        picked = bp.select(readings, size=1, max_per_sector=5, mode="blend")

        self.assertEqual(picked[0][0], "A")


class TestSizing(unittest.TestCase):

    def reading(self, close=1000.0, atr=25.0):
        return {"symbol": "X", "sector": "s", "technical": 80, "flow": 80,
                "close": close, "atr": atr}

    def test_a_stop_out_costs_about_the_risk_budget(self):
        equity = 1_000_000_000
        risk_pct = 1.0

        shares, stop = bp._size_position(
            self.reading(), equity, risk_pct, max_position_pct=100
        )

        loss = shares * (1000.0 - stop)

        self.assertAlmostEqual(loss / equity * 100, risk_pct, delta=0.05)

    def test_a_more_volatile_name_gets_fewer_shares(self):
        quiet = bp._size_position(self.reading(atr=10.0), 1e9, 1.0, 100)[0]
        wild = bp._size_position(self.reading(atr=50.0), 1e9, 1.0, 100)[0]

        self.assertLess(wild, quiet)

    def test_the_position_cap_binds(self):
        equity = 1_000_000_000

        shares, _ = bp._size_position(
            self.reading(atr=1.0), equity, 1.0, max_position_pct=5.0
        )

        self.assertLessEqual(shares * 1000.0, equity * 0.05 + 1)

    def test_a_missing_atr_still_sizes(self):
        shares, stop = bp._size_position(
            {"close": 1000.0, "atr": None}, 1e9, 1.0, 100
        )

        self.assertGreater(shares, 0)
        self.assertIsNotNone(stop)

    def test_a_zero_price_is_refused(self):
        shares, stop = bp._size_position({"close": 0, "atr": 10}, 1e9, 1.0, 100)

        self.assertEqual(shares, 0)


if __name__ == "__main__":
    unittest.main()
