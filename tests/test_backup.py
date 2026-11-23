"""backup.py: the nightly copy of the stores that cannot be refetched."""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import backup


def _store(path, wal=False, rows=3):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    return conn


class BackupTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sirat-bk-root-")
        self.dest = tempfile.mkdtemp(prefix="sirat-bk-dest-")
        # A WAL store with a writer still connected, as the Codal campaign
        # is every night, and its newest rows still in the -wal file.
        self.live = _store(os.path.join(self.root, "sirat_market.db"), wal=True)
        self.live.execute("PRAGMA wal_autocheckpoint=0")
        self.live.execute("INSERT INTO t VALUES (99)")
        self.live.commit()
        _store(os.path.join(self.root, "sirat_research.db")).close()
        os.makedirs(os.path.join(self.root, "backtest_output"))
        with open(os.path.join(self.root, "backtest_output",
                               "forward_predictions.jsonl"), "w") as handle:
            handle.write('{"session": "2026-11-22"}\n')

    def tearDown(self):
        self.live.close()
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.dest, ignore_errors=True)

    def night(self, day):
        return backup.run(root=self.root, dest=self.dest,
                          now=datetime(2026, 11, day, 19, 30))

    def test_a_night_holds_consistent_single_file_copies(self):
        summary = self.night(23)
        folder = os.path.join(self.dest, "2026-11-23")
        self.assertEqual(summary["folder"], folder)
        copy = os.path.join(folder, "sirat_market.db")
        conn = sqlite3.connect(copy)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0],
                             "delete")
            # The row that was only in the -wal file is in the copy.
            self.assertEqual(conn.execute("SELECT MAX(x) FROM t").fetchone()[0],
                             99)
        finally:
            conn.close()
        self.assertFalse(os.path.exists(copy + "-wal"))
        with open(os.path.join(folder, backup.MANIFEST), encoding="utf-8") as h:
            manifest = json.load(h)
        names = {entry["name"] for entry in manifest["files"]}
        self.assertIn("backtest_output/forward_predictions.jsonl", names)
        self.assertIn("sirat_research.db", names)
        entry = next(e for e in manifest["files"] if e["name"] == "sirat_market.db")
        self.assertEqual(entry["sha256"], backup._sha256(copy))
        self.assertEqual(entry["quick_check"], "ok")
        # Absent stores and files are named, not invented.
        self.assertNotIn("sirat_fundamentals.db", names)
        self.assertIn("data/bill_quotes.json", manifest["skipped"])

    def test_only_the_newest_nights_are_kept_and_nothing_else_is_touched(self):
        os.makedirs(os.path.join(self.dest, "notes"))
        os.makedirs(os.path.join(self.dest, "2026-01-01"))     # no manifest
        for day in (18, 19, 20, 21):
            self.night(day)
        self.assertEqual(backup.nights(self.dest), ["2026-11-18", "2026-11-19",
                                                    "2026-11-20", "2026-11-21"])
        summary = backup.run(root=self.root, dest=self.dest, keep=2,
                             now=datetime(2026, 11, 22, 19, 30))
        self.assertEqual(summary["removed"],
                         ["2026-11-18", "2026-11-19", "2026-11-20"])
        self.assertEqual(backup.nights(self.dest), ["2026-11-21", "2026-11-22"])
        self.assertTrue(os.path.isdir(os.path.join(self.dest, "notes")))
        self.assertTrue(os.path.isdir(os.path.join(self.dest, "2026-01-01")))

    def test_a_killed_runs_leftover_is_cleared_by_the_next(self):
        os.makedirs(os.path.join(self.dest, "2026-11-20.tmp"))
        self.night(21)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "2026-11-20.tmp")))

    def test_a_copy_that_fails_its_check_costs_no_older_night(self):
        for day in (20, 21):
            self.night(day)
        with mock.patch.object(backup, "_quick_check",
                               return_value="*** page 7 never used"):
            with self.assertRaises(backup.BackupFailed):
                backup.run(root=self.root, dest=self.dest, keep=1,
                           now=datetime(2026, 11, 22, 19, 30))
        self.assertEqual(backup.nights(self.dest), ["2026-11-20", "2026-11-21"])
        self.assertFalse(os.path.exists(os.path.join(self.dest, "2026-11-22")))

    def test_too_little_space_stops_before_copying(self):
        with mock.patch.object(backup.shutil, "disk_usage",
                               return_value=mock.Mock(free=10)):
            with self.assertRaises(backup.BackupFailed):
                self.night(23)
        self.assertEqual(backup.nights(self.dest), [])

    def test_the_cli_reports_a_failure_by_its_exit_code(self):
        with mock.patch.object(backup, "run",
                               side_effect=backup.BackupFailed("disk full")):
            with mock.patch("sys.stdout"):
                self.assertEqual(backup.main(["--dest", self.dest]), 1)

    def test_the_default_destination_is_outside_the_project(self):
        self.assertFalse(os.path.normcase(backup.DEST).startswith(
            os.path.normcase(backup.ROOT) + os.sep))


if __name__ == "__main__":
    unittest.main()
