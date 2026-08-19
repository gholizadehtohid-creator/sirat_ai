"""Tests for universe snapshots -- the one defect only elapsed time can fix.

Every other problem in this repository can be fixed whenever someone gets to it.
A universe snapshot not taken this week is a week of the instrument list that is
gone: there is no archive to fetch it from later.

Every test builds its own tiny market database, so nothing here depends on the
424 MB file being present and nothing writes into the real research record.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sirat.experiments import universe


def market_db(directory, instruments):
    """A stand-in for sirat_market.db carrying only what a snapshot reads."""

    path = os.path.join(directory, "market.db")

    conn = sqlite3.connect(path)

    try:
        conn.execute(
            "CREATE TABLE instruments ("
            "ins_code TEXT PRIMARY KEY, symbol_fa TEXT, symbol_raw TEXT, "
            "name_fa TEXT, isin TEXT, kind TEXT, board TEXT, "
            "is_equity INTEGER, sector_code TEXT, sector_name TEXT, "
            "updated_at TEXT)")

        conn.executemany(
            "INSERT INTO instruments (ins_code, symbol_fa, name_fa, isin, "
            "kind, board, is_equity, sector_code, sector_name, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            instruments)

        conn.commit()
    finally:
        conn.close()

    return path


def rows(count=5, updated="2026-08-18"):
    return [("%d" % (1000 + i), "SYM%d" % i, "name %d" % i, "IR%08d" % i,
             "equity", "bourse", 1, "27", "sector", updated)
            for i in range(count)]


class Snapshots(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_uni_")
        self.research = os.path.join(self.dir, "research.db")
        self.market = market_db(self.dir, rows())

    def test_capture_records_the_list(self):
        snapshot = universe.capture(market_db=self.market, path=self.research)

        self.assertTrue(snapshot["created"])
        self.assertEqual(snapshot["instrument_count"], 5)
        self.assertEqual(snapshot["equities"], 5)

    def test_an_unchanged_list_does_not_create_a_second_snapshot(self):
        first = universe.capture(market_db=self.market, path=self.research,
                                 as_of="2026-08-18")
        second = universe.capture(market_db=self.market, path=self.research,
                                  as_of="2026-08-18")

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(universe.history(path=self.research)), 1)

    def test_a_changed_list_creates_a_new_snapshot(self):
        universe.capture(market_db=self.market, path=self.research,
                         as_of="2026-08-18")

        conn = sqlite3.connect(self.market)
        try:
            conn.execute(
                "INSERT INTO instruments (ins_code, symbol_fa, is_equity) "
                "VALUES ('9999', 'NEW', 1)")
            conn.commit()
        finally:
            conn.close()

        second = universe.capture(market_db=self.market, path=self.research,
                                  as_of="2026-08-18")

        self.assertTrue(second["created"])
        self.assertEqual(second["instrument_count"], 6)
        self.assertEqual(len(universe.history(path=self.research)), 2)

    def test_updated_at_alone_does_not_change_the_hash(self):
        """A refresh that touched every row changed nothing about the universe.

        If `updated_at` fed the hash, every routine data refresh would mint a
        new snapshot and the record would fill with identical universes -- and
        the property that makes a daily schedule affordable would be gone.
        """

        first = universe.capture(market_db=self.market, path=self.research,
                                 as_of="2026-08-18")

        conn = sqlite3.connect(self.market)
        try:
            conn.execute("UPDATE instruments SET updated_at = '2026-08-19'")
            conn.commit()
        finally:
            conn.close()

        second = universe.capture(market_db=self.market, path=self.research,
                                  as_of="2026-08-18")

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertFalse(second["created"])

    def test_restore_returns_the_universe_as_it_stood(self):
        snapshot = universe.capture(market_db=self.market, path=self.research)

        conn = sqlite3.connect(self.market)
        try:
            conn.execute("DELETE FROM instruments WHERE ins_code = '1000'")
            conn.commit()
        finally:
            conn.close()

        restored = universe.restore(snapshot["snapshot_id"],
                                    path=self.research)

        self.assertEqual(len(restored), 5)
        self.assertIn("1000", set(restored["ins_code"]))

    def test_an_altered_snapshot_is_refused_rather_than_returned(self):
        snapshot = universe.capture(market_db=self.market, path=self.research)

        conn = sqlite3.connect(self.research)
        try:
            conn.execute(
                "UPDATE universe_snapshots SET payload = '[]' "
                "WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(universe.UniverseUnavailable):
            universe.restore(snapshot["snapshot_id"], path=self.research)

    def test_a_missing_market_database_raises(self):
        with self.assertRaises(universe.UniverseUnavailable):
            universe.capture(market_db=os.path.join(self.dir, "nope.db"),
                             path=self.research)

    def test_restoring_an_unknown_snapshot_raises(self):
        with self.assertRaises(universe.UniverseUnavailable):
            universe.restore("u19700101-deadbeef", path=self.research)


class Scheduling(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_sched_")
        self.research = os.path.join(self.dir, "research.db")
        self.market = market_db(self.dir, rows())

    def test_never_taken_is_due(self):
        status = universe.due(path=self.research)

        self.assertTrue(status["due"])
        self.assertIsNone(status["latest"])
        self.assertIn("no universe snapshot", status["reason"])

    def test_a_fresh_snapshot_is_not_due(self):
        universe.capture(market_db=self.market, path=self.research)

        self.assertFalse(universe.due(path=self.research)["due"])

    def test_an_old_snapshot_is_due(self):
        universe.capture(market_db=self.market, path=self.research,
                         as_of="2026-08-01")

        status = universe.due(path=self.research,
                              now=pd.Timestamp("2026-08-19"))

        self.assertTrue(status["due"])
        self.assertGreater(status["age_days"], 7)

    def test_run_if_due_captures_when_owed(self):
        result = universe.run_if_due(market_db=self.market, path=self.research)

        self.assertIsNotNone(result["captured"])
        self.assertTrue(result["captured"]["created"])

    def test_run_if_due_is_cheap_when_nothing_is_owed(self):
        universe.run_if_due(market_db=self.market, path=self.research)

        second = universe.run_if_due(market_db=self.market,
                                     path=self.research)

        self.assertIsNone(second["captured"])
        self.assertFalse(second["due"])

    def test_run_if_due_reports_a_failure_instead_of_raising(self):
        """A scheduled job that raises is a scheduled job that gets disabled."""

        result = universe.run_if_due(
            market_db=os.path.join(self.dir, "absent.db"),
            path=self.research)

        self.assertIsNone(result["captured"])
        self.assertIn("error", result)

    def test_an_unrestorable_snapshot_counts_as_no_snapshot(self):
        """The worst state of all: a row that keeps the schedule quiet.

        A snapshot written by some other code path, or corrupted, will not
        restore. If `due()` trusted the timestamp alone it would report the
        universe as frozen and recoverable when it is neither, and the schedule
        would never fire again.
        """

        snapshot = universe.capture(market_db=self.market, path=self.research)

        conn = sqlite3.connect(self.research)
        try:
            conn.execute(
                "UPDATE universe_snapshots SET payload = '[]' "
                "WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.commit()
        finally:
            conn.close()

        status = universe.due(path=self.research)

        self.assertTrue(status["due"])
        self.assertFalse(status["restorable"])
        self.assertIn("cannot be restored", status["reason"])

    def test_the_schedule_command_is_emitted_not_executed(self):
        command = universe.schedule_command()

        self.assertIn("schtasks /Create", command)
        self.assertIn("universe.py", command)


class Governance(unittest.TestCase):
    """A snapshot is what makes the Stage 1 survivorship check answerable."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_gov_")
        self.research = os.path.join(self.dir, "research.db")
        self.market = market_db(self.dir, rows())

    def test_a_snapshot_id_satisfies_the_stage_one_survivorship_check(self):
        from sirat.experiments import gates
        from tests.test_experiment_gates import THRESHOLDS, panel

        snapshot = universe.capture(market_db=self.market, path=self.research)

        without = gates.data_integrity(panel(), THRESHOLDS,
                                       universe_snapshot=None)
        with_it = gates.data_integrity(
            panel(), THRESHOLDS, universe_snapshot=snapshot["snapshot_id"])

        self.assertFalse(without.metrics["survivorship"])
        self.assertTrue(with_it.metrics["survivorship"])


if __name__ == "__main__":
    unittest.main()
