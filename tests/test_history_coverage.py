"""database.history_coverage: the strip's "bars ... to <date>" is the equities'.

2026-11-23: seven gold funds carried that day's bar (gold.py fetches them on
its own) and no equity did; MAX(date) over the whole table made the panel's
footer read "to 2026-11-23" over a market that stopped at 11-22.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


class NewestEquitySessionTests(unittest.TestCase):

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

    def store(self, instruments, bars):
        """instruments: (code, is_equity); bars: (code, date)."""

        conn = database._connect()
        conn.executemany(
            "INSERT INTO instruments (ins_code, symbol_fa, kind, is_equity) "
            "VALUES (?, ?, ?, ?)",
            [(code, "s" + code, "equity" if equity else "fund", equity)
             for code, equity in instruments])
        conn.executemany(
            "INSERT INTO price_history (ins_code, date, close, volume) "
            "VALUES (?, ?, 100.0, 10.0)", bars)
        conn.commit()
        conn.close()

    def test_a_funds_newer_bar_does_not_date_the_market(self):
        self.store([("1", 1), ("2", 1), ("9", 0)],
                   [("1", "2026-11-21"), ("1", "2026-11-22"),
                    ("2", "2026-11-22"),
                    ("9", "2026-11-22"), ("9", "2026-11-23")])

        coverage = database.history_coverage()

        self.assertEqual(coverage["last_date"], "2026-11-22")
        # The count and the first date are still the whole table's.
        self.assertEqual(coverage["bars"], 5)
        self.assertEqual(coverage["first_date"], "2026-11-21")

    def test_a_session_only_a_few_names_have_is_not_the_newest(self):
        """19:13 on 2026-11-22: four names off the market watch had the
        session's bar, some 750 did not."""

        codes = [str(n) for n in range(1, 11)]
        bars = [(code, "2026-11-18") for code in codes]
        bars.append(("1", "2026-11-22"))
        self.store([(code, 1) for code in codes], bars)

        self.assertEqual(database.history_coverage()["last_date"], "2026-11-18")

    def test_a_session_most_names_have_is(self):
        codes = [str(n) for n in range(1, 11)]
        bars = [(code, "2026-11-18") for code in codes]
        bars += [(code, "2026-11-22") for code in codes[:6]]
        self.store([(code, 1) for code in codes], bars)

        self.assertEqual(database.history_coverage()["last_date"], "2026-11-22")

    def test_without_equities_the_whole_table_still_answers(self):
        """A store whose universe is not loaded yet keeps the old reading
        rather than an empty strip."""

        self.store([("9", 0)], [("9", "2026-11-23")])

        self.assertEqual(database.history_coverage()["last_date"], "2026-11-23")

    def test_an_empty_table_is_empty(self):
        self.assertEqual(database.history_coverage(),
                         {"bars": 0, "first_date": None, "last_date": None})


if __name__ == "__main__":
    unittest.main()
