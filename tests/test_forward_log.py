"""The forward prediction log's write-time rules (review panel, 2026-11-11).

Ten of its first twenty entries were written after their outcome session had
opened, and three on a partial flow table; nothing tested the logger.
"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "examples"))

import forward_prediction_log as fpl  # noqa: E402


def store(sessions, traded=10, flowed=10):
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE instruments (ins_code TEXT, is_equity INTEGER);
        CREATE TABLE price_history (ins_code TEXT, date TEXT, volume REAL);
        CREATE TABLE client_type_history (ins_code TEXT, date TEXT);
    """)
    for i in range(traded):
        conn.execute("INSERT INTO instruments VALUES (?, 1)", ("c%d" % i,))
    for day in sessions:
        for i in range(traded):
            conn.execute("INSERT INTO price_history VALUES (?, ?, 100)",
                         ("c%d" % i, day))
        for i in range(flowed):
            conn.execute("INSERT INTO client_type_history VALUES (?, ?)",
                         ("c%d" % i, day))
    return conn


def tehran(text):
    return datetime.fromisoformat(text).replace(tzinfo=fpl.TEHRAN).astimezone(
        timezone.utc)


class TooLateTests(unittest.TestCase):

    def test_the_evening_after_the_session_is_in_time(self):
        conn = store(["2026-11-11"])                 # a Wednesday
        self.assertIsNone(fpl.too_late(conn, "2026-11-11",
                                       now=tehran("2026-11-11T19:30:00")))

    def test_a_thursday_or_friday_run_is_still_in_time(self):
        conn = store(["2026-11-11"])
        self.assertIsNone(fpl.too_late(conn, "2026-11-11",
                                       now=tehran("2026-11-13T22:00:00")))

    def test_saturday_after_the_pre_open_is_too_late(self):
        conn = store(["2026-11-11"])
        self.assertIsNotNone(fpl.too_late(conn, "2026-11-11",
                                          now=tehran("2026-11-14T09:10:00")))
        self.assertIsNone(fpl.too_late(conn, "2026-11-11",
                                       now=tehran("2026-11-14T08:30:00")))

    def test_a_stored_later_session_is_too_late_whatever_the_clock(self):
        conn = store(["2026-11-10", "2026-11-11"])
        self.assertIn("2026-11-11", fpl.too_late(
            conn, "2026-11-10", now=tehran("2026-11-10T20:00:00")))


class CoverageTests(unittest.TestCase):

    def test_flow_rows_are_counted_against_the_equities_that_traded(self):
        conn = store(["2026-11-10"], traded=10, flowed=2)
        self.assertEqual(fpl.coverage(conn, "2026-11-10"), (10, 2))


if __name__ == "__main__":
    unittest.main()
