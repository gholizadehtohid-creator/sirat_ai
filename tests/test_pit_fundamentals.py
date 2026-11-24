"""pit_fundamentals and fundamental_db.sync_versions: Codal figures as they
were known on a date -- every release kept, the one known on D chosen as
`metrics` chooses, and the store's own statistics."""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import fundamental_db  # noqa: E402
import pit_fundamentals as pit  # noqa: E402

B = 1e9
FULL = {"revenue": 100 * B, "net_profit": 20 * B, "total_assets": 500 * B,
        "operating_cash_flow": 30 * B}


def release(tracing, end, months, published, profit, cons=False, audited=False,
            items=None):
    """One release as load_versions() gives it."""

    body = dict(FULL if items is None else items)
    if profit is not None and items is None:
        body["net_profit"] = profit
    return {"tracing_no": tracing, "ins_code": "C", "period_end": end,
            "period_months": months, "consolidated": cons, "audited": audited,
            "published_at": published,
            "published_iso": fundamental_db.published_iso(published),
            "title": "t", "items": body, "ratios": {}}


class StampTests(unittest.TestCase):

    def test_codal_stamps_become_sortable_gregorian(self):
        self.assertEqual(fundamental_db.published_iso("1400/01/30 12:18:18"),
                         "2021-04-19T12:18:18")
        # no clock: the end of the day, never its start
        self.assertEqual(fundamental_db.published_iso("1400/01/30"),
                         "2021-04-19T23:59:59")
        self.assertIsNone(fundamental_db.published_iso("not a date"))

    def test_a_cutoff_from_every_spelling_of_a_day(self):
        self.assertEqual(pit.cutoff("1400/01/30"), "2021-04-19T23:59:59")
        self.assertEqual(pit.cutoff("2021-04-19"), "2021-04-19T23:59:59")
        self.assertEqual(pit.cutoff(date(2021, 4, 19)), "2021-04-19T23:59:59")
        self.assertEqual(pit.cutoff(datetime(2021, 4, 19, 10, 0)), "2021-04-19T10:00:00")
        self.assertEqual(pit.cutoff("2021-04-19", lag_days=30), "2021-03-20T23:59:59")
        with self.assertRaises(ValueError):
            pit.cutoff("soon")


class AsOfTests(unittest.TestCase):

    def setUp(self):
        self.versions = [
            release("1", "1403/12/30", 12, "1404/01/25 10:00:00", 20 * B),
            release("2", "1403/12/30", 12, "1404/04/10 10:00:00", 26 * B, audited=True),
            release("3", "1404/06/31", 6, "1404/07/28 10:00:00", 12 * B),
        ]

    def test_each_day_sees_only_what_was_published_by_then(self):
        self.assertEqual(pit.as_of("C", "1404/01/24", versions=self.versions), [])
        first = pit.as_of("C", "1404/02/01", versions=self.versions)
        self.assertEqual([r["tracing_no"] for r in first], ["1"])
        later = pit.as_of("C", "1404/08/01", versions=self.versions)
        # newest period first, the annual's audited release by then
        self.assertEqual([r["tracing_no"] for r in later], ["3", "2"])
        self.assertEqual(later[1]["items"]["net_profit"], 26 * B)

    def test_a_lag_moves_the_cutoff_back(self):
        rows = pit.as_of("C", "1404/04/20", lag_days=30, versions=self.versions)
        self.assertEqual([r["tracing_no"] for r in rows], ["1"])

    def test_a_later_parse_without_a_statement_does_not_replace_the_held_one(self):
        # what metrics does (sync_from_market_db): a failed read of the income
        # statement is not the company's word on it
        thin = release("4", "1403/12/30", 12, "1404/05/01 10:00:00", None,
                       items={"total_assets": 1.0, "operating_cash_flow": 1.0})
        rows = pit.as_of("C", "1404/06/01", versions=self.versions + [thin])
        self.assertEqual(rows[0]["tracing_no"], "2")

    def test_bases_are_kept_apart_on_request(self):
        cons = release("5", "1403/12/30", 12, "1404/05/01 10:00:00", 40 * B, cons=True)
        both = self.versions + [cons]
        self.assertEqual(pit.as_of("C", "1404/06/01", versions=both)[0]["tracing_no"], "5")
        self.assertEqual(pit.as_of("C", "1404/06/01", consolidated=False,
                                   versions=both)[0]["tracing_no"], "2")
        self.assertEqual(pit.as_of("C", "1404/06/01", consolidated=True,
                                   versions=both)[0]["tracing_no"], "5")

    def test_the_first_release_is_the_first_with_figures(self):
        thin_first = release("0", "1403/12/30", 12, "1404/01/20 10:00:00", None,
                             items={"total_assets": 1.0})
        first = pit.first_release("C", "1403/12/30", 12,
                                  versions=[thin_first] + self.versions)
        self.assertEqual(first["tracing_no"], "1")
        every = pit.first_release("C", versions=self.versions)
        self.assertEqual([r["tracing_no"] for r in every], ["3", "1"])

    def test_revisions_are_one_basis_at_a_time(self):
        cons = release("5", "1403/12/30", 12, "1404/05/01 10:00:00", 40 * B, cons=True)
        revs = pit.revisions("C", versions=self.versions + [cons])
        self.assertEqual(len(revs), 1)                 # the consolidated one is alone
        rev = revs[0]
        self.assertEqual((rev["count"], rev["first_tracing_no"], rev["latest_tracing_no"]),
                         (2, "1", "2"))
        self.assertAlmostEqual(rev["change"], 0.3)

    def test_only_a_material_revision_is_reported(self):
        latest = {"net_profit": 26 * B}
        rev = pit.revision_of("C", "1403/12/30", 12, False, latest, versions=self.versions)
        self.assertAlmostEqual(rev["first_net_profit"], 20 * B)
        self.assertAlmostEqual(rev["change"], 0.3)
        # within MATERIAL_REVISION of the first: nothing to mark
        self.assertIsNone(pit.revision_of("C", "1403/12/30", 12, False,
                                          {"net_profit": 20.5 * B}, versions=self.versions))
        # a period with one release has no revision
        self.assertIsNone(pit.revision_of("C", "1404/06/31", 6, False,
                                          {"net_profit": 99 * B}, versions=self.versions))

    def test_statistics_count_lags_revisions_and_mixed_bases(self):
        cons = release("5", "1403/12/30", 12, "1404/05/01 10:00:00", 40 * B, cons=True)
        s = pit.statistics({"C": self.versions + [cons]})
        self.assertEqual((s["periods"], s["mixed"]), (2, 1))
        # 1403/12/30 -> 1404/01/25 is 25 days; 1404/06/31 -> 1404/07/28 is 27
        self.assertAlmostEqual(s["lags"]["12"]["q50"], 25 + 10 / 24, places=2)
        self.assertEqual(s["revisions"]["12"]["changed"], 1)
        self.assertEqual(s["revisions"]["all"]["material"], 1)


class VersionStoreTests(unittest.TestCase):
    """sync_versions on a temporary market database and fundamental store."""

    def setUp(self):
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.store)
        self.env = os.environ.pop("SIRAT_FUNDAMENTAL_DB", None)
        database.create_database()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        if self.env is not None:
            os.environ["SIRAT_FUNDAMENTAL_DB"] = self.env
        for path in (self.market, self.store):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    try:
                        os.unlink(path + suffix)
                    except OSError:
                        pass

    def filing(self, tracing, profit, published, audited=False):
        row = {"tracing_no": tracing, "period_end": "1404/12/29", "period_months": 12,
               "title": "t", "audited": audited, "consolidated": False,
               "items": dict(FULL, net_profit=profit), "ratios": {}}
        database.save_codal_filing("C", row, published_at=published)

    def test_every_release_is_kept_beside_the_period_winner(self):
        self.filing("100", 20 * B, "1405/01/25 10:00:00")
        self.filing("200", 26 * B, "1405/04/10 10:00:00", audited=True)
        result = fundamental_db.sync_from_market_db()

        self.assertEqual(result["versions"]["new"], 2)
        held = fundamental_db.load_metrics("C")
        self.assertEqual([r["tracing_no"] for r in held], ["200"])
        releases = pit.load_versions("C")["C"]
        self.assertEqual([r["tracing_no"] for r in releases], ["100", "200"])
        self.assertEqual(releases[0]["published_iso"], "2026-04-14T10:00:00")
        # the store's winner and the point-in-time read of today agree
        self.assertEqual(pit.as_of("C", "2099-01-01")[0]["tracing_no"], "200")
        self.assertEqual(pit.as_of("C", "1405/02/01")[0]["items"]["net_profit"], 20 * B)

    def test_the_sync_is_incremental_follows_a_reparse_and_a_purge(self):
        self.filing("100", 20 * B, "1405/01/25 10:00:00")
        self.filing("200", 26 * B, "1405/04/10 10:00:00")
        fundamental_db.sync_versions()
        self.assertEqual(fundamental_db.sync_versions()["written"], 0)

        conn = database._connect()
        conn.execute("UPDATE codal_filings SET items = ?, updated_at = ? "
                     "WHERE tracing_no = '100'",
                     ('{"net_profit": 21000000000.0}', "2099-01-01T00:00:00"))
        conn.execute("DELETE FROM codal_filings WHERE tracing_no = '200'")
        conn.commit()
        conn.close()
        result = fundamental_db.sync_versions()

        self.assertEqual((result["written"], result["new"], result["removed"]), (1, 0, 1))
        releases = pit.load_versions("C")["C"]
        self.assertEqual([r["tracing_no"] for r in releases], ["100"])
        self.assertEqual(releases[0]["items"]["net_profit"], 21 * B)

    def test_an_empty_source_deletes_nothing(self):
        self.filing("100", 20 * B, "1405/01/25 10:00:00")
        fundamental_db.sync_versions()
        self.assertEqual(fundamental_db.sync_versions(rows=[])["removed"], 0)
        self.assertEqual(len(pit.load_versions("C")["C"]), 1)

    def test_no_store_or_no_table_reads_as_empty_and_creates_nothing(self):
        self.assertEqual(pit.load_versions(), {})
        self.assertFalse(os.path.exists(self.store))
        fundamental_db.create_database()                # a store without the table
        self.assertEqual(pit.load_versions("C"), {})
        self.assertEqual(pit.as_of("C", "1405/01/01"), [])


if __name__ == "__main__":
    unittest.main()
