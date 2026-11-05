"""database.halted_codes: who is left out of the analyses as halted."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


class HaltedTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        conn = database._connect()
        bars = [
            # trades normally up to the market's newest session
            ("live", "2026-11-04", 1000), ("live", "2026-11-03", 900),
            # traded 20 days ago, then zero-volume bars: halted, but recently
            ("short", "2026-10-15", 500), ("short", "2026-11-04", 0),
            # last real trade two months ago, frozen bars since
            ("long", "2026-09-01", 700), ("long", "2026-11-04", 0),
            # bars, but not one with volume
            ("never", "2026-11-04", 0),
        ]
        for code, day, volume in bars:
            conn.execute("INSERT INTO price_history (ins_code, date, close, "
                         "volume) VALUES (?, ?, 1, ?)", (code, day, volume))
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def test_only_symbols_without_a_traded_session_for_a_month(self):
        halted = database.halted_codes(days=30)
        self.assertEqual(halted, {"long": "2026-09-01", "never": None})

    def test_the_clock_is_the_market_not_the_wall(self):
        # 2026-11-04 is the newest bar: nothing is halted for being "today"-old.
        self.assertNotIn("live", database.halted_codes(days=1))

    def test_a_symbol_with_no_bars_is_unknown_not_halted(self):
        self.assertNotIn("absent", database.halted_codes(days=30))


if __name__ == "__main__":
    unittest.main()
