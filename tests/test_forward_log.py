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


def with_index(conn, sessions):
    import config
    conn.execute("CREATE TABLE index_history (ins_code TEXT, date TEXT)")
    for day in sessions:
        conn.execute("INSERT INTO index_history VALUES (?, ?)",
                     (config.INDICES["TEDPIX"]["insCode"], day))
    return conn


class SkippedSessionTests(unittest.TestCase):
    """The full test, 2026-11-12: a session passed over left no record, and
    the audit said "missed: 0" over 40 of them."""

    def test_sessions_between_the_last_record_and_this_one_are_named(self):
        conn = with_index(store(["2026-11-10", "2026-11-14"]),
                          ["2026-11-10", "2026-11-11", "2026-11-14"])
        rows = [{"spec": fpl.SPEC_VERSION, "session": "2026-11-10"}]
        self.assertEqual(fpl.skipped_sessions(conn, rows, "2026-11-14"),
                         ["2026-11-11"])

    def test_a_session_already_recorded_is_not_named_again(self):
        conn = with_index(store(["2026-11-10"]),
                          ["2026-11-10", "2026-11-11", "2026-11-14"])
        rows = [{"spec": fpl.SPEC_VERSION, "session": "2026-11-10"},
                {"spec": fpl.SPEC_VERSION, "session": "2026-11-11", "missed": "x"}]
        self.assertEqual(fpl.skipped_sessions(conn, rows, "2026-11-14"), [])

    def test_an_empty_log_names_nothing(self):
        conn = with_index(store([]), ["2026-11-10", "2026-11-11"])
        self.assertEqual(fpl.skipped_sessions(conn, [], "2026-11-11"), [])

    def test_log_once_records_the_skipped_session_before_the_new_one(self):
        import tempfile
        from unittest import mock
        folder = tempfile.mkdtemp()
        log = os.path.join(folder, "log.jsonl")
        db = os.path.join(folder, "m.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE instruments (ins_code TEXT, is_equity INTEGER);
            CREATE TABLE price_history (ins_code TEXT, date TEXT, volume REAL);
            CREATE TABLE client_type_history (ins_code TEXT, date TEXT);
        """)
        with_index(conn, ["2026-11-10", "2026-11-11", "2026-11-14"])
        conn.commit()
        conn.close()
        with mock.patch.object(fpl, "LOG_PATH", log), \
                mock.patch.object(fpl, "DB", db), \
                mock.patch.object(fpl, "latest_common_session", return_value="2026-11-14"), \
                mock.patch.object(fpl, "too_late", return_value="test: late"):
            fpl.append_entry({"spec": fpl.SPEC_VERSION, "session": "2026-11-10",
                              "computed_at": "2026-11-10T16:00:00+00:00"})
            fpl.log_once(verbose=False)
            rows = fpl.read_log()
        self.assertEqual([(r["session"], bool(r.get("missed"))) for r in rows],
                         [("2026-11-10", False), ("2026-11-11", True),
                          ("2026-11-14", True)])
        self.assertIn("superseded", rows[1]["missed"])
        self.assertTrue(fpl.verify_chain(rows)["intact"])


if __name__ == "__main__":
    unittest.main()
