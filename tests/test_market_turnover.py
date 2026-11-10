"""database.market_turnover: the stock market's turnover, not the gold funds'.

The table it sums also holds the bullion funds gold.py fetches. From 2025 they
were a fifth to two-thirds of a month's total, and their flows follow gold and
the dollar -- so the regime's turnover reading, and the "volume" attached to
TEDPIX, were partly a reading of the gold market.
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import database  # noqa: E402
import regime_engine  # noqa: E402


class MarketTurnoverTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        database.save_instruments([
            {"ins_code": "eq1", "symbol_fa": "الف", "kind": "equity",
             "is_equity": True},
            {"ins_code": "eq2", "symbol_fa": "ب", "kind": "equity",
             "is_equity": True},
            {"ins_code": "gold", "symbol_fa": "طلا", "kind": "fund",
             "is_equity": False},
        ])

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def _bars(self, code, values, start="2026-06-01"):
        dates = pd.bdate_range(start, periods=len(values))
        database.save_history(code, pd.DataFrame({
            "date": dates, "close": 1000.0, "volume": 10.0, "value": values,
        }))
        return dates

    def test_only_equities_are_summed(self):
        self._bars("eq1", [100.0, 100.0, 100.0])
        self._bars("eq2", [50.0, 60.0, 70.0])
        self._bars("gold", [1000.0, 5000.0, 9000.0])

        frame = database.market_turnover()

        self.assertEqual(frame["turnover"].tolist(), [150.0, 160.0, 170.0])
        self.assertEqual(frame["instruments"].tolist(), [2, 2, 2],
                         "coverage is counted in equity bars")

    def test_a_session_only_the_funds_reached_is_not_a_market_session(self):
        # gold.py fetches on its own schedule: on 2026-08-24 the funds held
        # bars three sessions newer than any equity. Those sessions carry no
        # stock-market turnover at all, so they are not in the series.
        equity_dates = self._bars("eq1", [100.0] * 3)
        self._bars("gold", [1000.0] * 6)

        frame = database.market_turnover()

        self.assertEqual(list(frame["date"]), list(equity_dates))

    def test_a_gold_fund_surge_does_not_move_the_regime_turnover_state(self):
        # Equities trade the same every session. The funds were busy over the
        # baseline and quiet in the recent window -- the shape of autumn 2026,
        # when they turned a steady stock market into a CONTRACTING one.
        sessions = config.REGIME_TURNOVER_BASELINE
        recent = config.REGIME_TURNOVER_WINDOW
        self._bars("eq1", [100.0] * sessions)
        self._bars("gold", [300.0] * (sessions - recent) + [0.0] * recent)

        state = regime_engine.turnover_state(database.market_turnover())

        self.assertEqual(state["state"], regime_engine.STEADY)
        self.assertAlmostEqual(state["ratio"], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
