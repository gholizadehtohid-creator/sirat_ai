"""The two tools that delete in bulk ask before they do (2026-11-23).

`purge_simulated.py --force` wiped the price tables at once -- 2,010,804
bars on the day of the review -- and `codal_backfill.py --purge-subsidiaries`
deleted by one rule over a title with no way to see first what it matched.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import authenticity  # noqa: E402
import codal_backfill  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402
import purge_simulated  # noqa: E402

TABLES = ("market_data", "price_history", "market_snapshot", "fetch_log")
OWN = ("اطلاعات و صورتهای مالی میاندورهای دوره 6 ماهه منتهی به 1405/06/31 "
       "(حسابرسی نشده)")
CLUB = ("اطلاعات و صورتهای مالی میاندورهای دوره 6 ماهه منتهی به 1405/06/31 "
        "(حسابرسی شده) (شرکت باشگاه فرهنگی ورزشی سپاهان نوین)")


class ForceTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        conn = sqlite3.connect(self.path)
        for table in TABLES:
            conn.execute("CREATE TABLE %s (x INTEGER)" % table)
            conn.executemany("INSERT INTO %s VALUES (?)" % table,
                             [(n,) for n in range(4)])
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.path)

    def rows(self):
        conn = sqlite3.connect(self.path)
        try:
            return {table: conn.execute("SELECT COUNT(*) FROM %s" % table
                                        ).fetchone()[0] for table in TABLES}
        finally:
            conn.close()

    def purge(self, **flags):
        printed = []
        with mock.patch.object(purge_simulated, "create_database"), \
                mock.patch.object(purge_simulated, "set_provenance") as mark, \
                mock.patch.object(purge_simulated, "DATABASE_NAME", self.path), \
                mock.patch.object(authenticity, "verify") as verify, \
                mock.patch("builtins.print",
                           side_effect=lambda *a, **k: printed.append(
                               " ".join(str(x) for x in a))):
            result = purge_simulated.purge(**flags)
        return result, "\n".join(printed), verify, mark

    def test_force_alone_deletes_nothing_and_prints_the_counts(self):
        result, said, verify, mark = self.purge(force=True)

        self.assertEqual(self.rows(), dict.fromkeys(TABLES, 4))
        self.assertEqual(result, 0)
        self.assertIn("price_history", said)
        self.assertIn("4 rows", said)
        self.assertIn("--force --yes", said)
        mark.assert_not_called()                # the provenance is not reset
        verify.assert_not_called()              # --force still skips the check

    def test_force_with_yes_wipes_the_price_tables(self):
        result, _said, verify, mark = self.purge(force=True, confirmed=True)

        self.assertEqual(self.rows(), dict.fromkeys(TABLES, 0))
        self.assertEqual(result, 12)            # the three price tables' rows
        verify.assert_not_called()
        mark.assert_called_once()

    def test_yes_alone_still_needs_a_conviction(self):
        """--yes without --force runs the check, and an unconvicted store
        keeps its rows (the full test, 2026-11-12)."""

        with mock.patch.object(purge_simulated, "create_database"), \
                mock.patch.object(purge_simulated, "set_provenance"), \
                mock.patch.object(purge_simulated, "DATABASE_NAME", self.path), \
                mock.patch.object(authenticity, "verify", return_value={
                    "status": authenticity.UNVERIFIED, "summary": "t",
                    "checks": []}), \
                mock.patch("builtins.print"):
            purge_simulated.purge(confirmed=True)
        self.assertEqual(self.rows(), dict.fromkeys(TABLES, 4))


class DryRunTests(unittest.TestCase):

    def setUp(self):
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.store)
        database.create_database()

        def filing(tracing, title, revenue, published):
            record = {"tracing_no": tracing, "period_end": "1405/06/31",
                      "period_months": 6, "title": title,
                      "items": {"revenue": revenue}, "ratios": {}}
            database.save_codal_filing("P", record, published_at=published)
            database.save_codal_financials("P", record)

        filing("own", OWN, 1000.0, "1405/07/30 10:00:00")
        filing("club", CLUB, 3.0, "1405/08/18 13:00:00")
        fundamental_db.sync_from_market_db()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.store):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    try:
                        os.unlink(path + suffix)
                    except OSError:
                        pass

    def held(self):
        conn = database._connect()
        try:
            return (sorted(r[0] for r in conn.execute(
                        "SELECT tracing_no FROM codal_filings")),
                    conn.execute("SELECT tracing_no FROM codal_financials"
                                 ).fetchall())
        finally:
            conn.close()

    def test_a_dry_run_lists_what_would_go_and_changes_nothing(self):
        before = self.held()
        revenue = fundamental_db.load_metrics("P", limit=5)[0]["items"]["revenue"]

        result = codal_backfill.purge_subsidiaries(verbose=False, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual((result["would_remove"], result["periods"],
                          result["removed"]), (1, 1, 0))
        self.assertEqual([row["tracing_no"] for row in result["listed"]],
                         ["club"])
        self.assertEqual(result["metrics_periods"], 1)
        self.assertEqual(self.held(), before)
        self.assertEqual(
            fundamental_db.load_metrics("P", limit=5)[0]["items"]["revenue"],
            revenue)
        self.assertNotIn("club", codal_backfill.settled_failures())

    def test_the_real_purge_removes_what_the_dry_run_listed(self):
        listed = codal_backfill.purge_subsidiaries(verbose=False, dry_run=True)
        done = codal_backfill.purge_subsidiaries(verbose=False)
        self.assertEqual(done["removed"], listed["would_remove"])
        after = codal_backfill.purge_subsidiaries(verbose=False, dry_run=True)
        self.assertEqual(after["would_remove"], 0)

    def test_a_dry_run_prints_each_statement_and_says_nothing_changed(self):
        printed = []
        with mock.patch("builtins.print",
                        side_effect=lambda *a, **k: printed.append(str(a[0]))):
            codal_backfill.purge_subsidiaries(verbose=True, dry_run=True)
        text = "\n".join(printed)
        self.assertIn("would remove club", text)
        self.assertIn("Nothing was changed", text)

    def test_a_dry_run_on_a_missing_store_creates_no_file(self):
        absent = self.market + ".absent"
        with mock.patch.object(database, "DATABASE_NAME", absent):
            result = codal_backfill.purge_subsidiaries(verbose=False,
                                                       dry_run=True)
        self.assertEqual(result["would_remove"], 0)
        self.assertFalse(os.path.exists(absent))

    def test_the_flag_reaches_the_purge(self):
        with mock.patch.object(codal_backfill, "purge_subsidiaries") as purge:
            codal_backfill.main(["--purge-subsidiaries", "--dry-run", "--quiet"])
            self.assertTrue(purge.call_args[1]["dry_run"])
            codal_backfill.main(["--purge-subsidiaries", "--quiet"])
            self.assertFalse(purge.call_args[1]["dry_run"])


if __name__ == "__main__":
    unittest.main()
