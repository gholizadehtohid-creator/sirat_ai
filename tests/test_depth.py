"""Tests for the order-book depth collector.

The thing worth testing here is not the arithmetic -- there is barely any. It
is that a change in the feed STOPS the collector instead of quietly writing
nulls, and that nothing in the module can destroy history.

Both properties exist because of specific damage already done:

    `market_snapshot.state` was read from a key named `cs` that this endpoint
    has never returned. 587 of 587 rows were NULL for as long as the column
    existed and nothing anywhere objected.

    `save_market_snapshot()` runs DELETE-then-INSERT, so every pull destroys
    the previous one. That is why there is no order-book history to research.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import depth                                                    # noqa: E402


def row(ins_code="123", **overrides):
    """One marketwatch row carrying every key the collector declares."""

    item = {
        "insCode": ins_code, "lva": "نماد", "pdv": 4370.0, "pcl": 4360.0,
        "py": 4250.0, "pf": 4340.0, "pmx": 4370.0, "pmn": 4250.0,
        "qtj": 63993400.0, "qtc": 279082704650.0, "ztt": 1387.0,
        "bv": 1.0, "ztd": 75000000000.0, "pMax": 4370.0, "pMin": 4130.0,
        "hEven": 114301, "csv": "39 ",
        "blDs": [{"n": level, "pmd": 4370.0 - level, "qmd": 1000.0 * level,
                  "zmd": level, "pmo": 4390.0 + level, "qmo": 500.0 * level,
                  "zmo": level} for level in range(1, 6)],
    }
    item.update(overrides)
    return item


class PayloadContract(unittest.TestCase):
    """A missing key is a bug in this module, not a missing value."""

    def test_a_missing_top_level_key_raises(self):
        broken = row()
        del broken["pMax"]

        with self.assertRaises(depth.PayloadChanged) as caught:
            depth.parse([broken], "t0")

        self.assertIn("pMax", str(caught.exception))

    def test_a_missing_depth_key_raises(self):
        broken = row()
        del broken["blDs"][0]["qmo"]

        with self.assertRaises(depth.PayloadChanged):
            depth.parse([broken], "t0")

    def test_the_exact_bug_this_module_was_written_after(self):
        """`cs` for the instrument state. It never existed in this feed.

        Under `.get()` that produced 587 NULLs and no complaint. Under
        `_require` it is a stopped collector and a name in the message.
        """

        with self.assertRaises(depth.PayloadChanged) as caught:
            depth._require(row(), "cs", "marketwatch row")

        self.assertIn("cs", str(caught.exception))

    def test_a_null_VALUE_is_kept_because_that_is_real_data(self):
        """An empty ask side means the queue ate it. That is the signal."""

        empty = row()
        empty["blDs"][0]["qmo"] = None

        _, levels = depth.parse([empty], "t0")

        self.assertIsNone(levels[0][7])

    def test_every_declared_field_reaches_the_record(self):
        snapshots, levels = depth.parse([row()], "t0")

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(snapshots[0]), 18)
        self.assertEqual(len(levels), 5)
        self.assertEqual(len(levels[0]), 9)

    def test_the_real_band_is_stored_not_estimated(self):
        """E14 had to estimate the band from a cross-sectional percentile."""

        snapshots, _ = depth.parse([row(pMax=5000.0, pMin=4000.0)], "t0")

        self.assertEqual(snapshots[0][14], 5000.0)
        self.assertEqual(snapshots[0][15], 4000.0)


class HistoryIsNeverDestroyed(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_depth_")
        self.db = os.path.join(self.dir, "market.db")

    def _count(self):
        conn = sqlite3.connect(self.db)
        try:
            return (conn.execute(
                        "SELECT COUNT(*) FROM depth_snapshot").fetchone()[0],
                    conn.execute(
                        "SELECT COUNT(*) FROM depth_levels").fetchone()[0])
        finally:
            conn.close()

    def test_a_second_capture_adds_rather_than_replaces(self):
        for stamp in ("t0", "t1", "t2"):
            snapshots, levels = depth.parse([row()], stamp)
            depth.store(snapshots, levels, db=self.db)

        snaps, lvls = self._count()

        self.assertEqual(snaps, 3)
        self.assertEqual(lvls, 15)

    def test_re_storing_the_same_capture_is_idempotent(self):
        snapshots, levels = depth.parse([row()], "t0")

        depth.store(snapshots, levels, db=self.db)
        depth.store(snapshots, levels, db=self.db)

        self.assertEqual(self._count(), (1, 5))

    def test_a_repeat_cannot_overwrite_what_was_recorded(self):
        """INSERT OR IGNORE, never REPLACE. A recorded capture is history."""

        first, levels = depth.parse([row(qtj=1.0)], "t0")
        depth.store(first, levels, db=self.db)

        second, levels2 = depth.parse([row(qtj=999.0)], "t0")
        depth.store(second, levels2, db=self.db)

        conn = sqlite3.connect(self.db)
        try:
            kept = conn.execute(
                "SELECT volume FROM depth_snapshot").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(kept, 1.0)

    def test_the_module_contains_no_delete(self):
        """market_snapshot's DELETE-then-INSERT is why there is no history."""

        with open(os.path.join(ROOT, "depth.py"), encoding="utf-8") as handle:
            source = handle.read()

        for statement in ("DELETE FROM", "DROP TABLE", "INSERT OR REPLACE"):
            self.assertNotIn(statement, source.upper().replace("`", ""),
                             "%s in a module whose only job is to accumulate"
                             % statement)


class SchemaTests(unittest.TestCase):

    def test_state_is_absent_because_it_has_no_source(self):
        """Not an oversight.

        GetMarketWatch does not carry the instrument state under any key --
        `csv` is the sector code, `vc` is near-unique per instrument. Adding a
        column with no source is how `market_snapshot.state` came to be 100%
        null. If state is needed, get_instrument_info must be verified first.
        """

        conn = sqlite3.connect(":memory:")
        try:
            depth.ensure_tables(conn)
            columns = [r[1] for r in
                       conn.execute("PRAGMA table_info(depth_snapshot)")]
        finally:
            conn.close()

        self.assertNotIn("state", columns)
        self.assertIn("limit_up", columns)
        self.assertIn("last_trade_hhmmss", columns)



class SchedulingTests(unittest.TestCase):
    """A collector that runs once is not a collector.

    depth.py was written because every session without it loses order-book
    state permanently. That argument is only true if the thing actually runs
    again tomorrow, so `--if-due` and the emitted schtasks line are part of the
    feature rather than convenience around it.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat_sched_")
        self.db = os.path.join(self.dir, "market.db")

    def test_an_empty_database_is_due(self):
        """Absence of history means a capture is owed, not that it is not."""

        owed, why = depth.due(db=self.db)

        self.assertTrue(owed)
        self.assertIn("has ever been taken", why)

    def test_a_missing_file_is_due(self):
        owed, _ = depth.due(db=os.path.join(self.dir, "nothing.db"))

        self.assertTrue(owed)

    def test_a_fresh_capture_is_not_due(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        snapshots, levels = depth.parse(
            [row()], now.isoformat(timespec="seconds"))
        depth.store(snapshots, levels, db=self.db)

        owed, why = depth.due(db=self.db, now=now + timedelta(hours=3))

        self.assertFalse(owed)
        self.assertIn("floor", why)

    def test_a_missed_day_catches_up_rather_than_waiting_a_cycle(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        snapshots, levels = depth.parse(
            [row()], now.isoformat(timespec="seconds"))
        depth.store(snapshots, levels, db=self.db)

        owed, _ = depth.due(db=self.db, now=now + timedelta(hours=25))

        self.assertTrue(owed)

    def test_an_unreadable_timestamp_is_due_not_silently_skipped(self):
        snapshots, levels = depth.parse([row()], "not-a-timestamp")
        depth.store(snapshots, levels, db=self.db)

        owed, why = depth.due(db=self.db)

        self.assertTrue(owed)
        self.assertIn("unreadable", why)

    def test_the_schedule_covers_the_tehran_trading_week(self):
        command = depth.schedule_command()

        for day in ("SAT", "SUN", "MON", "TUE", "WED"):
            self.assertIn(day, command)

        self.assertNotIn("THU", command)
        self.assertNotIn("FRI", command)

    def test_the_scheduled_job_runs_if_due_not_a_bare_capture(self):
        """A daily task that captures unconditionally double-counts a day."""

        self.assertIn("--if-due", depth.schedule_command())

    def test_installing_is_not_something_this_module_does(self):
        """It prints the command. Registering a task changes the machine."""

        source = open(os.path.join(ROOT, "depth.py"), encoding="utf-8").read()

        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main()
