"""The newest-published release of a period is the one kept, in every store.

A company files a period more than once -- unaudited, then audited; an
original, then its correction (اصلاحیه). Found 2026-11-11: codal_financials
kept whichever release was WRITTEN last, and the backfill, the watch and the
daily sync all write an older release after a newer one in ordinary
operation. 531 periods held a superseded release, 74 of them a company's
latest (ملت 1405/06/31 held 1614602, not its correction 1619771).
"""

import json
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
import financials  # noqa: E402
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

    def reparse_as(self, **changes):
        """The archived newest release, re-parsed into `changes`."""

        codal.archive_write("1619771", DOCUMENT)

        def parse(_client, letter):
            return (dict(record(letter["tracing_no"], letter["title"], 120.0),
                         **changes), None, codal.KIND_OK)

        return mock.patch.object(codal_backfill.codal_sync, "parse_letter",
                                 side_effect=parse)

    def stored_filing(self, tracing_no):
        return {f["tracing_no"]: f for f in
                database.load_codal_filings("M")}[tracing_no]

    def test_a_re_parse_that_lands_on_another_period_is_not_used(self):
        # Written, it would move the filing to that period in codal_filings
        # (keyed by tracing number) and add a row there in codal_financials.
        with self.reparse_as(period_end="1405/03/31", items=dict(FULLER)):
            dry = codal_backfill.repair_superseded(verbose=False)
            codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual(dry["reparsed"], 0)
        self.assertEqual(
            [(tracing, why) for tracing, why, _keys in dry["not_reparsed"]],
            [("1619771", "re-parsed as another period (1405/03/31, 6m)")])
        self.assertEqual((self.stored_filing("1619771")["period_end"],
                          self.stored_filing("1619771")["items"]),
                         ("1405/06/31", {"net_profit": 120.0}))
        self.assertEqual([(r["period_end"], r["tracing_no"]) for r in
                          database.load_codal_financials("M")],
                         [("1405/06/31", "1619771")])

    def test_a_re_parse_that_finds_nothing_more_is_not_counted_or_written(self):
        conn = database._connect()
        conn.execute("UPDATE codal_filings SET updated_at = ? "
                     "WHERE tracing_no = '1619771'", ("2026-01-01T00:00:00",))
        conn.commit()
        conn.close()

        with self.reparse_as():                 # exactly the stored items
            dry = codal_backfill.repair_superseded(verbose=False)
            applied = codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual((dry["reparsed"], dry["still_losing_items"]), (0, 1))
        self.assertEqual(dry["not_reparsed"], [
            ("1619771", "today's extractor finds no more in the document",
             ["ppe"])])
        self.assertEqual(applied["written"], 1)
        self.assertEqual(self.stored_filing("1619771")["updated_at"],
                         "2026-01-01T00:00:00")


# Enough of each statement for financials' provenance flags to count it.
COMPLETE = {"revenue": 900.0, "net_profit": 100.0, "total_assets": 5000.0,
            "operating_cash_flow": 80.0}


class StatementGuardTests(Stores):
    """A newer release whose parse lacks a statement the held parse has is a
    failed read, not the company's word: 472469 (no balance sheet or cash
    flow statement) and 1212323 (no income statement) on 2026-11-11."""

    def setUp(self):
        super().setUp()
        # Never the live raw archive: 1619771 is in it, and a re-parse of the
        # real ملت correction would decide the outcome (third review).
        archive = tempfile.mkdtemp()
        patch = mock.patch.object(codal, "ARCHIVE_DIR", archive)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, archive, True)

    def save(self, tracing, title, items, published):
        row = dict(record(tracing, title, None), items=items)
        database.save_codal_filing("M", row, published_at=published)
        return database.save_codal_financials("M", row)

    def test_a_newer_release_missing_a_statement_does_not_replace_it(self):
        self.save("1614602", ORIGINAL, dict(COMPLETE), "1405/07/29 16:42:34")
        written = self.save("1619771", CORRECTION,
                            {"revenue": 950.0, "net_profit": 120.0},
                            "1405/08/05 11:12:49")

        self.assertEqual(written, 0)
        self.assertEqual((self.held()["tracing_no"], self.held()["items"]),
                         ("1614602", COMPLETE))

    def test_a_newer_release_with_each_statement_replaces_a_fuller_parse(self):
        self.save("1614602", ORIGINAL, dict(COMPLETE, ppe=7.0),
                  "1405/07/29 16:42:34")
        written = self.save("1619771", CORRECTION, dict(COMPLETE, net_profit=120.0),
                            "1405/08/05 11:12:49")

        self.assertEqual(written, 1)
        self.assertEqual(self.held()["tracing_no"], "1619771")

    def test_a_re_parse_of_the_held_release_still_may(self):
        self.save("1619771", CORRECTION, dict(COMPLETE), "1405/08/05 11:12:49")
        written = self.save("1619771", CORRECTION, {"net_profit": 121.0},
                            "1405/08/05 11:12:49")

        self.assertEqual(written, 1)
        self.assertEqual(self.held()["items"], {"net_profit": 121.0})

    def test_the_statements_are_financials_provenance_flags(self):
        flags = {"income statement": "has_income",
                 "balance sheet": "has_balance",
                 "cash flow statement": "has_cashflow"}
        self.assertEqual([name for name, _keys in database.STATEMENT_ITEMS],
                         list(flags))
        for key in financials.summarise({}):
            ratios = financials.ratios({key: 1.0})
            self.assertEqual(
                {name for name, flag in flags.items() if ratios[flag]},
                {name for name, keys in database.STATEMENT_ITEMS if key in keys},
                key)

    def test_the_repair_keeps_the_held_row_and_names_the_release(self):
        # The damage as --repair-superseded finds it: an older release held,
        # the newest one's parse without the balance sheet or cash flow.
        self.save("1614602", ORIGINAL, dict(COMPLETE), "1405/07/29 16:42:34")
        conn = database._connect()
        conn.execute("INSERT INTO codal_filings (tracing_no, ins_code, "
                     "period_end, period_months, published_at, title, items, "
                     "ratios) VALUES ('1619771', 'M', '1405/06/31', 6, "
                     "'1405/08/05 11:12:49', ?, ?, '{}')",
                     (CORRECTION, '{"revenue": 950.0, "net_profit": 120.0}'))
        conn.commit()
        conn.close()

        dry = codal_backfill.repair_superseded(verbose=False)
        applied = codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual(dry["kept_unparsed"], [
            ("1619771", "1614602", ["balance sheet", "cash flow statement"])])
        self.assertEqual((applied["superseded"], applied["written"]), (1, 0))
        self.assertEqual(self.held()["tracing_no"], "1614602")


# The 1404-era income statement's basic EPS, as 1516177 files it: the figure
# on "سود (زیان) پایه هر سهم", the "خالص هر سهم" row left empty.
EPS_STATEMENT = (
    "<h2>صورت سود و زیان</h2><table>"
    "<tr><td>سود(زیان) خالص</td><td>30,978,478</td><td>14,453,701</td></tr>"
    "<tr><td>سود(زیان) پایه هر سهم:</td><td></td><td></td></tr>"
    "<tr><td>عملیاتی (ریال)</td><td>27,113</td><td>12,957</td></tr>"
    "<tr><td>سود (زیان) پایه هر سهم</td><td>28,428</td><td>13,232</td></tr>"
    "<tr><td>سود (زیان) خالص هر سهم– ریال</td><td></td><td></td></tr>"
    "</table>")


class ArchivedEpsTests(Stores):
    """Today's extractor on 1516177, the one archived release of the six
    that re-parses fuller: its EPS label, not its owners' lines."""

    def extract(self, html):
        return financials.extract(codal.split_sheets(html))

    def test_basic_eps_written_with_its_loss_is_read(self):
        self.assertEqual(self.extract(EPS_STATEMENT)["eps"], 28428.0)

    def test_the_net_eps_row_still_wins_where_it_has_a_figure(self):
        html = EPS_STATEMENT.replace(
            "خالص هر سهم– ریال</td><td></td><td></td>",
            "خالص هر سهم– ریال</td><td>28,400</td><td>13,200</td>")

        self.assertEqual(self.extract(html)["eps"], 28400.0)

    def test_the_repair_re_parses_the_archived_release_for_its_eps(self):
        archive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, archive, ignore_errors=True)
        patch = mock.patch.object(codal, "ARCHIVE_DIR", archive)
        patch.start()
        self.addCleanup(patch.stop)
        self.filing("1614602", ORIGINAL, 100.0, "1405/07/29 16:42:34")
        self.filing("1619771", CORRECTION, 120.0, "1405/08/05 11:12:49")
        conn = database._connect()
        conn.execute("UPDATE codal_financials SET tracing_no = '1614602', "
                     "title = ?, items = ?",
                     (ORIGINAL, '{"net_profit": 100.0, "eps": 28151.0}'))
        conn.commit()
        conn.close()
        codal.archive_write("1619771", EPS_STATEMENT.encode("utf-8"))

        dry = codal_backfill.repair_superseded(verbose=False)
        codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual((dry["losing_items"], dry["reparsed"],
                          dry["still_losing_items"]), (1, 1, 0))
        self.assertEqual(self.held()["tracing_no"], "1619771")
        self.assertEqual(self.held()["items"]["eps"], 28428.0)
        eps = {f["tracing_no"]: f["items"].get("eps")
               for f in database.load_codal_filings("M")}
        self.assertEqual(eps["1619771"], 28428.0)


class OrderIndependentGuardTests(Stores):
    """The statement rule must not depend on write order (third review,
    2026-11-11): the daily sync writes the newest filing first, and a thin
    newest release written first used to keep its period for good."""

    def setUp(self):
        super().setUp()
        archive = tempfile.mkdtemp()
        patch = mock.patch.object(codal, "ARCHIVE_DIR", archive)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, archive, True)

    def save(self, tracing, title, items, published):
        row = dict(record(tracing, title, None), items=items)
        database.save_codal_filing("M", row, published_at=published)
        return database.save_codal_financials("M", row)

    THIN = {"revenue": 950.0, "net_profit": 120.0}     # no balance sheet, no cash flow

    def test_a_thin_newest_written_first_gives_way_to_the_complete_original(self):
        self.save("1619771", CORRECTION, dict(self.THIN), "1405/08/05 11:12:49")
        written = self.save("1614602", ORIGINAL, dict(COMPLETE),
                            "1405/07/29 16:42:34")

        self.assertEqual(written, 1)
        self.assertEqual(self.held()["tracing_no"], "1614602")

    def test_the_store_makes_the_same_choice_in_either_order(self):
        for order in ((("1619771", CORRECTION, self.THIN, "1405/08/05 11:12:49"),
                       ("1614602", ORIGINAL, COMPLETE, "1405/07/29 16:42:34")),):
            for tracing, title, items, published in order:
                self.save(tracing, title, dict(items), published)
        result = fundamental_db.sync_from_market_db()

        held = fundamental_db.load_metrics("M")[0]
        self.assertEqual(held["tracing_no"], "1614602")
        self.assertEqual(result["thin"], 1)

    def plant_thin_newest(self):
        """The damage as live held it: the newest release, its parse without
        two statements, in both stores; the complete original on file."""

        conn = database._connect()
        for tracing, title, items, published in (
                ("1614602", ORIGINAL, COMPLETE, "1405/07/29 16:42:34"),
                ("1619771", CORRECTION, self.THIN, "1405/08/05 11:12:49")):
            conn.execute(
                "INSERT INTO codal_filings (tracing_no, ins_code, period_end, "
                "period_months, published_at, title, items, ratios) VALUES "
                "(?, 'M', '1405/06/31', 6, ?, ?, ?, '{}')",
                (tracing, published, title, json.dumps(items)))
        conn.execute(
            "INSERT INTO codal_financials (ins_code, period_end, period_months, "
            "tracing_no, title, items, ratios) VALUES ('M', '1405/06/31', 6, "
            "'1619771', ?, ?, '{}')", (CORRECTION, json.dumps(self.THIN)))
        conn.commit()
        conn.close()
        fundamental_db.save_metrics({
            "ins_code": "M", "period_end": "1405/06/31", "period_months": 6,
            "tracing_no": "1619771", "title": CORRECTION,
            "published_at": "1405/08/05 11:12:49", "items": dict(self.THIN),
            "ratios": {}})

    def test_the_repair_restores_a_held_thin_newest_release_once(self):
        self.plant_thin_newest()

        dry = codal_backfill.repair_superseded(verbose=False)
        self.assertEqual(dry["held_unparsed"], [("1619771", "1614602")])
        self.assertEqual(dry["held_unparsed_store"], [("1619771", "1614602")])
        self.assertEqual(self.held()["tracing_no"], "1619771")    # dry: no write

        applied = codal_backfill.repair_superseded(apply=True, verbose=False)
        self.assertEqual((applied["restored"], applied["restored_store"]), (1, 1))
        self.assertEqual(self.held()["items"], COMPLETE)
        self.assertEqual(fundamental_db.load_metrics("M")[0]["tracing_no"],
                         "1614602")

        again = codal_backfill.repair_superseded(verbose=False)
        self.assertEqual((again["held_unparsed"], again["held_unparsed_store"]),
                         ([], []))

    def test_a_live_code_is_compared_with_its_predecessors_releases(self):
        # A board move: codal_filings stays on the retired code M while
        # --repair-universe copied codal_financials to the live code L.
        self.save("1614602", ORIGINAL, dict(COMPLETE), "1405/07/29 16:42:34")
        self.save("1619771", CORRECTION, dict(COMPLETE, net_profit=120.0),
                  "1405/08/05 11:12:49")
        conn = database._connect()
        conn.execute(
            "INSERT INTO codal_financials (ins_code, period_end, period_months, "
            "tracing_no, title, items, ratios) VALUES ('L', '1405/06/31', 6, "
            "'1614602', ?, ?, '{}')", (ORIGINAL, json.dumps(COMPLETE)))
        conn.commit()
        conn.close()

        with mock.patch.object(codal_backfill, "_predecessors",
                               return_value={"L": ["M"]}):
            pairs = codal_backfill.superseded_periods()
            codal_backfill.repair_superseded(apply=True, verbose=False)

        self.assertEqual([(h["ins_code"], b["ins_code"], b["tracing_no"])
                          for h, b in pairs], [("L", "L", "1619771")])
        self.assertEqual(database.load_codal_financials("L")[0]["tracing_no"],
                         "1619771")


if __name__ == "__main__":
    unittest.main()
