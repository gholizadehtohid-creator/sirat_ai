"""pipeline.run_scan's technical stage reads back-adjusted bars.

The v1 screen's Stage 3 handed stored price_history straight to
calculate_indicators. Stored bars are raw, so a capital increase inside the
window read as a crash -- RSI near zero, MAs above the price, an ATR the size
of the adjustment -- and that ATR went on to size the stop through
_risk_inputs. Run on a throwaway database, no network: the provider is stubbed
and the snapshot refresh is off.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import pipeline  # noqa: E402
from backtest import back_adjust  # noqa: E402
from indicators import calculate_indicators  # noqa: E402
from ranking import rank_stocks  # noqa: E402


def bars(n, seed, split_at=None):
    """n sessions ending today -- the screen's stale gate reads the wall clock."""

    rng = np.random.default_rng(seed)
    moves = np.clip(rng.normal(0.0005, 0.015, n), -0.03, 0.03)
    close = 10_000.0 * np.cumprod(1.0 + moves)
    if split_at is not None:
        close[split_at:] *= 0.1
    return pd.DataFrame({
        "date": pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n),
        "open": close,
        "high": close * (1 + rng.uniform(0.002, 0.02, n)),
        "low": close * (1 - rng.uniform(0.002, 0.02, n)),
        "close": close,
        "volume": 1_000_000.0,
        "value": 2e10,
        "trades": 500.0,
    })


class TechnicalStageTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        conn = database._connect()
        conn.execute("INSERT INTO instruments (ins_code, symbol_fa, name_fa, kind, "
                     "is_equity) VALUES ('split', 'Tsplit', 'split', 'equity', 1)")
        for row in bars(240, 11, split_at=225).itertuples(index=False):
            conn.execute(
                "INSERT INTO price_history (ins_code, date, open, high, low, close, "
                "volume, value, trades) VALUES ('split',?,?,?,?,?,?,?,?)",
                (row.date.strftime("%Y-%m-%d"), row.open, row.high, row.low,
                 row.close, row.volume, row.value, row.trades))
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def test_the_screen_scores_and_sizes_on_adjusted_bars(self):
        stub = types.SimpleNamespace(name="stub")
        with mock.patch.object(pipeline, "get_provider", lambda: stub):
            # approve_score=0: the row reaches Stage 3 whatever its
            # fundamental score, so only the technical stage is under test.
            report = pipeline.run_scan(approve_score=0)

        row = next(r for r in report["final"] if r["ins_code"] == "split")

        raw = database.load_history_by_inscode("split", limit=300)
        adjusted, events = back_adjust(raw)
        self.assertEqual(len(events), 1)

        want = calculate_indicators(adjusted)
        self.assertEqual(row["technical"]["rsi"], rank_stocks(want)["rsi"])
        self.assertEqual(row["technical"]["score"], rank_stocks(want)["score"])
        self.assertEqual(row["metrics"]["atr_pct"],
                         round(float(want["ATR_PCT"].iloc[-1]), 4))

        # The stop the basket would size from was the split, not the market.
        shipped = calculate_indicators(raw)
        self.assertGreater(float(shipped["ATR_PCT"].iloc[-1]),
                           10 * row["metrics"]["atr_pct"])


if __name__ == "__main__":
    unittest.main()
