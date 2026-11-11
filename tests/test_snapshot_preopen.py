"""A pre-open market watch must not wipe the last session's trading.

Found 2026-11-11: the panel's 07:00 snapshot (every volume 0) replaced the
11-10 one, so the fund list, the allocation's funds and the bill check -- all
filtered on traded value -- came back empty every morning.
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


def watch(volume, value, close, limit_up):
    return pd.DataFrame([{"ins_code": "1", "symbol_fa": "a", "close": close,
                          "last": close, "prev_close": 100.0, "volume": volume,
                          "value": value, "trades": 5 if volume else 0,
                          "limit_up": limit_up, "limit_down": 90.0}])


class PreOpenSnapshotTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def stored(self):
        conn = database._connect()
        row = conn.execute("SELECT volume, value, trades, close, limit_up "
                           "FROM market_snapshot WHERE ins_code = '1'").fetchone()
        conn.close()
        return row

    def test_a_pre_open_watch_keeps_the_last_sessions_trading(self):
        database.save_snapshot(watch(1000.0, 2e9, 103.0, 103.0))
        database.save_snapshot(watch(0.0, 0.0, 103.0, 106.0))

        volume, value, trades, close, limit_up = self.stored()
        self.assertEqual((volume, value, trades), (1000.0, 2e9, 5))
        self.assertEqual((close, limit_up), (103.0, 106.0))   # the new watch's

    def test_a_traded_watch_replaces_everything(self):
        database.save_snapshot(watch(1000.0, 2e9, 103.0, 103.0))
        database.save_snapshot(watch(500.0, 1e9, 104.0, 106.0))

        self.assertEqual(self.stored()[:2], (500.0, 1e9))


if __name__ == "__main__":
    unittest.main()
