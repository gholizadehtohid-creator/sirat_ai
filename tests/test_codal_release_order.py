"""The newest-published release of a period is the one kept, in every store.

A company files a period more than once -- unaudited, then audited; an
original, then its correction (اصلاحیه). Found 2026-11-11: codal_financials
kept whichever release was WRITTEN last, and the backfill, the watch and the
daily sync all write an older release after a newer one in ordinary
operation. 531 periods held a superseded release, 74 of them a company's
latest (ملت 1405/06/31 held 1614602, not its correction 1619771).
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal  # noqa: E402
import codal_backfill  # noqa: E402
import codal_sync  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402

ORIGINAL = ("اطلاعات و صورتهای مالی میاندورهای دوره 6 ماهه منتهی به 1405/06/31 "
            "(حسابرسی نشده)")
CORRECTION = ORIGINAL + "(اصلاحیه)"


def record(tracing, title, profit):
    return {"tracing_no": tracing, "period_end": "1405/06/31",
            "period_months": 6, "title": title, "audited": False,
            "consolidated": False, "items": {"net_profit": profit},
            "ratios": {}}


class Stores(unittest.TestCase):
    """A temporary market database and fundamental store."""

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

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.store):
            if os.path.exists(path):
                os.unlink(path)

    def filing(self, tracing, title, profit, published):
        """What the backfill and the watch do with one parsed filing."""

        row = record(tracing, title, profit)
        database.save_codal_filing("M", row, published_at=published)
        return database.save_codal_financials("M", row)

    def held(self):
        return database.load_codal_financials("M")[0]


class FinancialsTests(Stores):

    def test_an_older_release_written_last_does_not_replace_the_newer(self):
        # The watch stored the correction; a later pass reached the original.
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")
        written = self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")

        self.assertEqual(written, 0)
        self.assertEqual(self.held()["tracing_no"], "1619771")
        self.assertEqual(self.held()["items"]["net_profit"], 120.0)

    def test_a_correction_still_replaces_its_original(self):
        self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")

        self.assertEqual(self.held()["tracing_no"], "1619771")

    def test_a_reparse_of_the_held_release_still_updates_it(self):
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")
        self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")
        self.filing("1619771", CORRECTION, 121.0, "1405/08/05 11:12:49")

        self.assertEqual(self.held()["items"]["net_profit"], 121.0)

    def test_the_letter_index_dates_a_release_codal_filings_lacks(self):
        # A ن-۳۱ portfolio filing is never in codal_filings; its letter is.
        database.save_codal_letters("M", [
            {"tracing_no": "1592027", "letter_code": "ن-31",
             "title": "گزارش فعالیت ماهانه (اصلاحیه)",
             "published_at": "1405/06/07 12:21:26"},
            {"tracing_no": "1590183", "letter_code": "ن-31",
             "title": "گزارش فعالیت ماهانه",
             "published_at": "1405/06/04 18:04:29"}])

        def portfolio(tracing, market):
            return database.save_codal_portfolio("M", {
                "period_end": "1405/05/31", "tracing_no": tracing,
                "title": "t", "listed_market": market})

        portfolio("1592027", 650.0)
        self.assertEqual(portfolio("1590183", 640.0), 0)

        held = database.load_codal_portfolio_latest(["M"])["M"]
        self.assertEqual((held["tracing_no"], held["listed_market"]),
                         ("1592027", 650.0))


class SyncTests(Stores):

    def sync(self, letters):
        client = mock.Mock()
        client.search_letters.return_value = codal.CodalResult(data=letters)

        def parse(_client, letter):
            title = letter["title"]
            return (record(letter["tracing_no"], title,
                           120.0 if "اصلاحیه" in title else 100.0),
                    None, codal.KIND_OK)

        with mock.patch.object(codal_sync, "parse_letter", side_effect=parse):
            return codal_sync.sync_company(
                client, {"ins_code": "M", "symbol_fa": "وبملت"})

    def letter(self, tracing, title, published):
        return {"tracing_no": tracing, "letter_code": "ن-10", "title": title,
                "published_at": published, "excel_url": "x"}

    def test_the_panels_sync_records_the_filing_itself(self):
        self.sync([self.letter("1614602", ORIGINAL, "1405/07/29 16:42:34")])

        filings = database.load_codal_filings("M")
        self.assertEqual([(f["tracing_no"], f["published_at"]) for f in filings],
                         [("1614602", "1405/07/29 16:42:34")])

    def test_two_releases_in_one_sync_leave_the_newer_in_place(self):
        # The search lists newest first, and the sync takes the two newest.
        self.sync([self.letter("1619771", CORRECTION, "1405/08/05 11:12:49"),
                   self.letter("1614602", ORIGINAL, "1405/07/29 16:42:34")])

        self.assertEqual(self.held()["tracing_no"], "1619771")


class StoreSyncTests(Stores):

    def test_a_reparsed_older_release_does_not_displace_the_newer(self):
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")
        self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")
        fundamental_db.sync_from_market_db()

        # codal_backfill.reparse_latest re-reads the original for new items:
        # codal_filings gets them and a later updated_at.
        conn = database._connect()
        conn.execute("UPDATE codal_filings SET items = ?, updated_at = ? "
                     "WHERE tracing_no = '1614602'",
                     ('{"net_profit": 100.0, "ppe": 5.0}', "2099-01-01T00:00:00"))
        conn.commit()
        conn.close()
        result = fundamental_db.sync_from_market_db()

        self.assertEqual(result["superseded"], 1)
        held = fundamental_db.load_metrics("M")[0]
        self.assertEqual((held["tracing_no"], held["items"]["net_profit"]),
                         ("1619771", 120.0))


class RepairTests(Stores):

    def setUp(self):
        super().setUp()
        self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")
        # The damage as the old upsert left it: the original over its
        # correction.
        conn = database._connect()
        conn.execute("UPDATE codal_financials SET tracing_no = '1614602', "
                     "title = ?, items = '{\"net_profit\": 100.0}'",
                     (ORIGINAL,))
        conn.commit()
        conn.close()

    def test_a_dry_run_reports_and_writes_nothing(self):
        result = codal_backfill.repair_superseded(verbose=False)

        self.assertEqual((result["superseded"], result["latest_period"],
                          result["written"]), (1, 1, 0))
        self.assertEqual(self.held()["tracing_no"], "1614602")

    def test_apply_puts_the_newest_release_back_once(self):
        result = codal_backfill.repair_superseded(apply=True, verbose=False)
        again = codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual(result["written"], 1)
        self.assertEqual(self.held()["tracing_no"], "1619771")
        self.assertEqual(self.held()["items"]["net_profit"], 120.0)
        self.assertEqual((again["superseded"], again["written"]), (0, 0))


FULLER = {"net_profit": 120.0, "ppe": 6.0}

# Enough of a statement document for codal.parse_body to split into sheets.
DOCUMENT = ("<h2>صورت سود و زیان</h2><table><tr><td>درآمدهای عملیاتی</td>"
            "<td>100</td></tr></table>").encode("utf-8")


class ThinnerRepairTests(Stores):
    """The newest release's stored parse can be thinner than the held one's:
    129 of the 531 on 2026-11-11, most parsed in August before the extractor
    learned ppe, capex, finance_cost and the rest."""

    def setUp(self):
        super().setUp()
        self.archive = tempfile.mkdtemp()
        self.patch = mock.patch.object(codal, "ARCHIVE_DIR", self.archive)
        self.patch.start()
        self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")
        # The original over its correction, as re-parsed in November: with
        # a ppe line the correction's August parse lacks.
        conn = database._connect()
        conn.execute("UPDATE codal_financials SET tracing_no = '1614602', "
                     "title = ?, items = ?",
                     (ORIGINAL, '{"net_profit": 100.0, "ppe": 5.0}'))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.archive, ignore_errors=True)
        super().tearDown()

    def filing_items(self, tracing_no):
        return {f["tracing_no"]: f for f in
                database.load_codal_filings("M")}[tracing_no]["items"]

    def test_the_dry_run_names_what_would_be_lost_and_what_is_not_archived(self):
        result = codal_backfill.repair_superseded(verbose=False)

        self.assertEqual((result["losing_items"], result["items_lost"]),
                         (1, {"ppe": 1}))
        self.assertEqual((result["reparsed"], result["still_losing_items"],
                          result["not_archived"]), (0, 1, ["1619771"]))
        self.assertEqual(self.held()["tracing_no"], "1614602")

    def test_an_archived_document_is_re_parsed_offline_and_kept_fuller(self):
        codal.archive_write("1619771", DOCUMENT)
        parsed = []

        def parse(client, letter):
            parsed.append(letter["tracing_no"])
            got = client.fetch_sheets(None, archive_key=letter["tracing_no"])
            self.assertTrue(got.ok, got.error)         # read from the archive
            with self.assertRaises(RuntimeError):      # and never the network
                client._get("https://codal.ir/")
            return (dict(record(letter["tracing_no"], letter["title"], 120.0),
                         items=dict(FULLER)), None, codal.KIND_OK)

        with mock.patch.object(codal_backfill.codal_sync, "parse_letter",
                               side_effect=parse):
            dry = codal_backfill.repair_superseded(verbose=False)
            self.assertEqual(self.filing_items("1619771"), {"net_profit": 120.0})
            applied = codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual(parsed, ["1619771", "1619771"])
        self.assertEqual((dry["losing_items"], dry["reparsed"],
                          dry["still_losing_items"], dry["not_archived"]),
                         (1, 1, 0, []))
        self.assertEqual(applied["written"], 1)
        self.assertEqual((self.held()["tracing_no"], self.held()["items"]),
                         ("1619771", FULLER))
        self.assertEqual(self.filing_items("1619771"), FULLER)


if __name__ == "__main__":
    unittest.main()
