"""codal_announcements: what the watch keeps, and the dividend per share."""

import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_announcements as a  # noqa: E402
import fundamental_db  # noqa: E402

# The shape of codal.ir/Reports/Decision.aspx, trimmed: a hidden
# PropertyName cell, the Persian label, and the value in an input.
PAGE = """
<tr><td class="Hidden PropertyName">AllocatedProfit</td>
<td><span>سود قابل تخصیص</span></td><td><input name="x" type="text" value="900" class="Hidden"></td></tr>
<tr><td class="Hidden PropertyName">DividendPerShare</td>
<td class="DescriptionColumn"><span>سود نقدی هر سهم (ریال)</span></td>
<td class="GridItem"><div></div><input name="y" type="text" value="55" class="Hidden">
<span class="spanFormattedValue">۵۵</span></td></tr>
<tr class="ComputationalRow"><td class="Hidden PropertyName">ListedCapital</td>
<td><span>سرمایه</span></td><td><div></div><input name="z" type="text" value="1935000000"></td></tr>
"""


# The decision form with its rows but no DividendPerShare among them: a
# settled "no DPS", unlike a reply that is not the form at all.
FORM_WITHOUT_DPS = "\n".join(line for line in PAGE.splitlines()
                             if "DividendPerShare" not in line)

# What the firewall serves with HTTP 200 instead of the page.
NOT_THE_FORM = ("<html><head><title>Request Rejected</title></head><body>The "
                "requested URL was rejected.</body></html>")

OLDER_YEAR = "تصمیمات مجمع عمومی عادی سالیانه دوره ۱۲ ماهه منتهی به ۱۴۰۳/۱۲/۳۰"

# وامیر's newest ن-52 (2026-11-11): a transitional 3 months after its fiscal
# year end moved, settled "no DividendPerShare row".
SHORT_YEAR = "تصمیمات مجمع عمومی عادی سالیانه دوره ۳ ماهه منتهی به ۱۴۰۵/۰۳/۳۱"

# کیبیسی's: the meeting adjourned; nothing was decided.
RECESS = ("تصمیمات مجمع عمومی عادی سالیانه دوره ۱۲ ماهه منتهی به "
          "۱۴۰۵/۱۲/۲۹(اعلام تنفس)")

# A recess of the year the decision that follows it settles: 3 of the 5
# recess notices on file share their fiscal year with one (2026-11-11).
RECESS_SAME_YEAR = ("تصمیمات مجمع عمومی عادی سالیانه دوره ۱۲ ماهه منتهی به "
                    "۱۴۰۴/۱۲/۲۹(اعلام تنفس)")


def letter(tracing, published, code="ن-52",
           title="تصمیمات مجمع عمومی عادی سالیانه دوره ۱۲ ماهه منتهی به ۱۴۰۴/۱۲/۲۹"):
    return {"tracing_no": tracing, "letter_code": code, "title": title,
            "published_at": published, "symbol": "فولاد",
            "url": "/Reports/Decision.aspx?LetterSerial=%s" % tracing}


class ParseTests(unittest.TestCase):

    def test_dps_and_capital_come_from_the_machine_names(self):
        self.assertEqual(a.parse_dividend(PAGE),
                         {"dps": 55.0, "capital": 1935000000.0})

    def test_a_page_without_the_row_has_no_dividend(self):
        self.assertIsNone(a.parse_dividend("<table><tr><td>x</td></tr></table>"))

    def test_fiscal_year_from_a_persian_title(self):
        self.assertEqual(a.fiscal_year_end(
            "تصمیمات مجمع ... منتهی به  ۱۴۰۴/۱۲/۲۹(اصلاحیه)"), "1404/12/29")

    def test_kinds_match_either_digit_form(self):
        self.assertEqual(a.kind_of("ن-۵۲"), "agm")
        self.assertEqual(a.kind_of("ن-60"), "capital")
        self.assertEqual(a.kind_of("ف ب-13"), "halt")
        self.assertIsNone(a.kind_of("ن-10"))


class StoreTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.previous = fundamental_db.set_path(self.path)
        a.ensure_tables()

    def tearDown(self):
        fundamental_db.set_path(self.previous)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def handle(self, item, page=PAGE):
        with mock.patch.object(a.codal, "archive_read", return_value=None), \
                mock.patch.object(a.codal, "archive_write"):
            client = mock.Mock()
            client._get.return_value.content = page.encode("utf-8")
            return a.handle(client, item, {"ins_code": "123"})

    def test_an_annual_agm_stores_the_announcement_and_the_dps(self):
        self.assertEqual(self.handle(letter("1", "1405/04/29 12:00:00")),
                         "stored (DPS 55)")
        self.assertEqual(a.latest_dividends()["123"]["dps"], 55.0)
        self.assertEqual(a.recent(days=None)[0]["kind"], "agm")

    def test_a_recess_notice_stores_no_dps_and_asks_for_no_page(self):
        # The watch path stored a recess notice's DPS as the year's dividend
        # (1372609: 380); only the backfill skipped them (2026-11-11).
        with mock.patch.object(a.codal, "archive_read", return_value=None),                 mock.patch.object(a.codal, "archive_write"):
            client = mock.Mock()
            client._get.return_value.content = PAGE.encode("utf-8")
            outcome = a.handle(client, letter("9", "1405/04/29 12:00:00",
                                              title=RECESS_SAME_YEAR),
                               {"ins_code": "123"})

        self.assertTrue(outcome.startswith("stored (recess notice"))
        self.assertEqual(client._get.call_count, 0)
        self.assertNotIn("123", a.latest_dividends())

    def test_an_amendment_replaces_the_original(self):
        self.handle(letter("1", "1405/04/29 12:00:00"))
        self.handle(letter("2", "1405/05/25 14:00:00"),
                    page=PAGE.replace('value="55"', 'value="60"'))
        self.assertEqual(a.latest_dividends()["123"]["dps"], 60.0)

    def test_an_older_letter_does_not_overwrite_a_newer_one(self):
        self.handle(letter("2", "1405/05/25 14:00:00"),
                    page=PAGE.replace('value="55"', 'value="60"'))
        self.handle(letter("1", "1405/04/29 12:00:00"))
        self.assertEqual(a.latest_dividends()["123"]["dps"], 60.0)

    def test_other_kinds_are_stored_without_a_request(self):
        client = mock.Mock()
        outcome = a.handle(client, letter("3", "1405/08/14 10:00:00", code="ن-20",
                                          title="افشای اطلاعات بااهمیت"),
                           {"ins_code": "123"})
        self.assertEqual(outcome, "stored")
        client._get.assert_not_called()
        self.assertEqual(a.recent(days=None, kind="material")[0]["tracing_no"], "3")

    def test_a_transient_failure_is_left_for_the_next_poll(self):
        client = mock.Mock()
        client._get.side_effect = TimeoutError("read timed out")
        with mock.patch.object(a.codal, "archive_read", return_value=None):
            self.assertIsNone(a.handle(client, letter("4", "1405/05/01 10:00:00"),
                                       {"ins_code": "123"}))

    def test_a_reply_that_is_not_the_form_is_left_for_the_next_poll(self):
        # غچین's 1613971 was settled "no DividendPerShare row" at 10:07Z on
        # 2026-11-06; the same page gave DPS 130 at 15:24Z.
        self.assertIsNone(self.handle(letter("5", "1405/08/04 14:58:27"),
                                      page=NOT_THE_FORM))
        self.assertNotIn("123", a.latest_dividends())

    def test_a_server_error_is_left_for_the_next_poll(self):
        import requests

        error = requests.HTTPError("500 Server Error: Internal Server Error",
                                   response=mock.Mock(status_code=500))
        client = mock.Mock()
        client._get.side_effect = error
        with mock.patch.object(a.codal, "archive_read", return_value=None):
            self.assertIsNone(a.handle(client, letter("6", "1405/05/01 10:00:00"),
                                       {"ins_code": "123"}))

    def test_the_form_without_the_row_is_a_settled_no_dps(self):
        self.assertEqual(self.handle(letter("7", "1405/05/01 10:00:00"),
                                     page=FORM_WITHOUT_DPS),
                         "stored (no DPS: no DividendPerShare row)")


LATE = "تصمیمات مجمع عمومی عادی به طور فوق العاده دوره ۱۲ ماهه منتهی به ۱۴۰۴/۱۲/۲۹"
LATE_MIDYEAR = ("تصمیمات مجمع عمومی عادی به طور فوق العاده دوره ۱۲ ماهه منتهی به "
                "۱۴۰۵/۱۲/۲۹")


class LateAgmTests(unittest.TestCase):
    """ن-55: an ordinary meeting held out of schedule. For a 12-month year
    that had ended, it is that year's late annual AGM and its page carries
    the DPS (سخواف 1639336, read by hand 2026-11-23); 76 equities had one
    newer than their stored DPS (the full test, 2026-11-12)."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.previous = fundamental_db.set_path(self.path)
        a.ensure_tables()

    def tearDown(self):
        fundamental_db.set_path(self.previous)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def handle(self, item, page=PAGE):
        with mock.patch.object(a.codal, "archive_read", return_value=None), \
                mock.patch.object(a.codal, "archive_write"):
            client = mock.Mock()
            client._get.return_value.content = page.encode("utf-8")
            return a.handle(client, item, {"ins_code": "123"}), client

    def test_which_letters_decide_a_years_dividend(self):
        self.assertTrue(a.is_annual_decision("ن-52", letter("1", "x")["title"]))
        self.assertFalse(a.is_annual_decision("ن-52", RECESS))
        self.assertTrue(a.is_annual_decision("ن-55", LATE, "1405/08/28 14:22:40"))
        self.assertFalse(a.is_annual_decision("ن-55", LATE_MIDYEAR, "1405/08/26"))
        self.assertFalse(a.is_annual_decision("ن-55", LATE, None))
        self.assertFalse(a.is_annual_decision("ن-20", LATE, "1405/08/28"))

    def test_a_late_agm_for_an_ended_year_is_read(self):
        outcome, client = self.handle(letter("10", "1405/08/28 14:22:40",
                                             code="ن-55", title=LATE))
        self.assertEqual(outcome, "stored (DPS 55)")
        self.assertEqual(a.latest_dividends()["123"]["fiscal_year_end"], "1404/12/29")

    def test_a_mid_year_meeting_asks_for_no_page(self):
        outcome, client = self.handle(letter("11", "1405/08/26 10:00:00",
                                             code="ن-55", title=LATE_MIDYEAR))
        self.assertTrue(outcome.startswith("stored (no year-end decision"))
        client._get.assert_not_called()
        self.assertNotIn("123", a.latest_dividends())

    def test_the_annual_decision_keeps_its_year_against_a_later_late_one(self):
        self.handle(letter("1", "1405/04/29 12:00:00"))                  # ن-52: 55
        self.handle(letter("12", "1405/08/28 14:22:40", code="ن-55", title=LATE),
                    page=PAGE.replace('value="55"', 'value="0"'))
        self.assertEqual(a.latest_dividends()["123"]["dps"], 55.0)

    def test_the_annual_decision_displaces_a_late_one_read_first(self):
        self.handle(letter("12", "1405/08/28 14:22:40", code="ن-55", title=LATE),
                    page=PAGE.replace('value="55"', 'value="0"'))
        self.handle(letter("1", "1405/04/29 12:00:00"))
        self.assertEqual(a.latest_dividends()["123"]["dps"], 55.0)


class BackfillTests(unittest.TestCase):

    def setUp(self):
        import database

        self.database = database
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.path)
        database.create_database()
        a.ensure_tables()

    def tearDown(self):
        self.database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.path):
            if os.path.exists(path):
                os.unlink(path)

    def run_backfill(self, pages, letters):
        """`pages`: served in request order, or {tracing_no: page}; a page
        that is an exception is raised instead."""

        instruments = [{"ins_code": "1", "symbol_raw": "x"},
                       {"ins_code": "2", "symbol_raw": "y"}]
        self.client = client = mock.Mock()

        def get(url, **_kw):
            if isinstance(pages, dict):
                page = pages[url.rsplit("=", 1)[-1]]
            else:
                page = pages.pop(0)
            if isinstance(page, Exception):
                raise page
            return mock.Mock(content=page.encode("utf-8"))

        client._get.side_effect = get
        with mock.patch.object(a, "_agm_letters", side_effect=letters), \
                mock.patch("time.sleep"), \
                mock.patch("database.load_instruments", return_value=instruments), \
                mock.patch("database.halted_codes", return_value={}), \
                mock.patch.object(a.codal, "archive_read", return_value=None), \
                mock.patch.object(a.codal, "archive_write"):
            return a.backfill_dividends(verbose=False, client=client)

    def stored_years(self, ins_code):
        conn = fundamental_db._connect()
        years = {r[0]: r[1] for r in conn.execute(
            "SELECT fiscal_year_end, dps FROM dividends WHERE ins_code = ?",
            (ins_code,))}
        conn.close()
        return years

    def marks(self):
        """dividend_reread: {ins_code: (fiscal year read down from, due)}."""

        conn = fundamental_db._connect()
        marks = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT ins_code, fiscal_year_end, due FROM dividend_reread")}
        conn.close()
        return marks

    def test_a_letter_without_dps_falls_through_to_the_next(self):
        # Two letters for one fiscal year -- an amendment without the row,
        # then the original. (The page was "<table>...x...</table>", which
        # is not the decision form at all and is now retried, not settled.)
        result = self.run_backfill(
            [FORM_WITHOUT_DPS, PAGE],
            [[letter("1", "1405/05/01 10:00:00"), letter("2", "1404/05/01 10:00:00")],
             []])
        self.assertEqual(result["stored"], 1)
        self.assertEqual(a.latest_dividends()["1"]["dps"], 55.0)

    def test_an_older_year_never_stands_in_for_the_newest(self):
        result = self.run_backfill(
            [FORM_WITHOUT_DPS, PAGE],
            [[letter("1", "1405/05/01 10:00:00"),
              letter("2", "1404/05/01 10:00:00", title=OLDER_YEAR)], []])
        self.assertEqual(result["stored"], 0)
        self.assertNotIn("1", a.latest_dividends())

    def test_a_late_amendment_of_an_older_year_does_not_stand_in(self):
        # دفرا: FY1403's amendment was published (1405/08/10) after the
        # FY1404 decision (1405/04/01); the newest-published letter won.
        result = self.run_backfill(
            [PAGE],
            [[letter("1", "1405/08/10 16:57:25", title=OLDER_YEAR + "(اصلاحیه)"),
              letter("2", "1405/04/01 16:07:12")], []])
        self.assertEqual(result["stored"], 1)
        self.assertEqual(a.latest_dividends()["1"]["fiscal_year_end"],
                         "1404/12/29")

    def test_an_indexed_late_agm_supplies_the_newer_year(self):
        """The search (ن-52 only) finds FY1403; a ن-55 for the ended FY1404
        is already in the index. No second search is made for it."""
        a.store_announcement(letter("55", "1405/08/28 14:22:40", code="ن-55",
                                    title=LATE), "1", "agm")
        searches = []

        def letters(_client, symbol):
            searches.append(symbol)
            return [letter("3", "1404/05/01 10:00:00", title=OLDER_YEAR)]                 if symbol == "x" else []

        result = self.run_backfill({"55": PAGE, "3": PAGE}, letters)
        self.assertEqual(result["stored"], 1)
        self.assertEqual(a.latest_dividends()["1"]["fiscal_year_end"], "1404/12/29")
        self.assertEqual(searches, ["x", "y"])

    def test_a_reply_that_is_not_the_form_leaves_the_company_due(self):
        result = self.run_backfill(
            [NOT_THE_FORM, PAGE],
            [[letter("1", "1405/05/01 10:00:00"), letter("2", "1404/05/01 10:00:00")],
             []])
        self.assertEqual((result["stored"], result["refused"]), (0, 1))
        self.assertNotIn("1", a.latest_dividends())

    def test_a_refused_search_skips_that_company_and_goes_on(self):
        calls = []

        def letters(client, symbol):
            calls.append(symbol)
            if symbol == "x":
                raise ConnectionError("dropped")
            return [letter("2", "1405/05/01 10:00:00")]

        result = self.run_backfill([PAGE], letters)
        self.assertEqual(calls, ["x", "y"])
        self.assertEqual(result, {"searched": 2, "stored": 1, "refused": 1,
                                  "stopped": False})
        self.assertNotIn("1", a.latest_dividends())
        self.assertIn("2", a.latest_dividends())

    # ---- which decision is the newest (2026-11-11) -----------------------

    def test_a_short_newest_year_without_dps_gives_way_to_the_12_month_one(self):
        result = self.run_backfill(
            {"1": FORM_WITHOUT_DPS, "2": PAGE},
            [[letter("1", "1405/08/04 16:17:29", title=SHORT_YEAR),
              letter("2", "1405/05/06 18:04:16"),
              letter("3", "1404/05/05 18:23:28", title=OLDER_YEAR)], []])

        self.assertEqual(result["stored"], 1)
        self.assertEqual(self.stored_years("1"), {"1404/12/29": 55.0})

    def test_a_recess_notice_is_not_read_as_the_newest_decision(self):
        result = self.run_backfill(
            {"1": FORM_WITHOUT_DPS, "2": PAGE},
            [[letter("1", "1405/04/20 10:00:00", title=RECESS),
              letter("2", "1405/04/01 16:07:12")], []])

        self.assertEqual(result["stored"], 1)
        self.assertEqual(self.stored_years("1"), {"1404/12/29": 55.0})
        self.assertEqual(self.client._get.call_count, 1)

    def test_a_recess_notice_of_the_year_being_read_is_not_read_either(self):
        # decision_years() leaves recess notices out of the years; this is
        # the letter choice within a year. Read after its decision turned
        # out to carry no DPS, the recess page is one more request, and its
        # form can hold a DividendPerShare row. It happened: the 2026-11-06
        # backfill read decision 1386657 (no DPS), then recess notice
        # 1372609 of the same FY1403, and stored its 380 as
        # 18027801615184692's DPS (18:32:31, :33, :35 UTC).
        result = self.run_backfill(
            {"1": FORM_WITHOUT_DPS, "2": PAGE},
            [[letter("1", "1405/05/06 18:04:16"),
              letter("2", "1405/04/20 10:00:00", title=RECESS_SAME_YEAR)],
             []])

        self.assertEqual(result["stored"], 0)
        self.assertEqual(self.client._get.call_count, 1)
        self.assertEqual(self.stored_years("1"), {})

    # ---- a DPS older than the newest decision on file ---------------------

    def hold_an_older_year(self):
        """Company 1 holds FY1403's DPS; its FY1404 decision is on file."""

        a.store_dividend("1", "1403/12/30", {"dps": 40.0},
                         letter("10", "1404/05/01 10:00:00", title=OLDER_YEAR))
        a.store_announcement(letter("11", "1405/05/01 10:00:00"), "1", "agm")

    def test_a_company_holding_an_older_year_is_searched_again(self):
        # It used to be skipped for good: it had a DPS. Its row stays.
        self.hold_an_older_year()
        result = self.run_backfill(
            {"11": PAGE},
            [[letter("11", "1405/05/01 10:00:00"),
              letter("10", "1404/05/01 10:00:00", title=OLDER_YEAR)], []])

        self.assertEqual(result["stored"], 1)
        self.assertEqual(self.stored_years("1"),
                         {"1403/12/30": 40.0, "1404/12/29": 55.0})
        self.assertEqual(a.latest_dividends()["1"]["dps"], 55.0)

    def test_a_decision_read_without_dps_is_not_read_again_every_run(self):
        self.hold_an_older_year()
        searched = []

        def letters(_client, symbol):
            searched.append(symbol)
            return [letter("11", "1405/05/01 10:00:00")] if symbol == "x" else []

        self.run_backfill({"11": FORM_WITHOUT_DPS}, letters)
        self.run_backfill({"11": FORM_WITHOUT_DPS}, letters)

        self.assertEqual(searched.count("x"), 1)
        self.assertEqual(self.stored_years("1"), {"1403/12/30": 40.0})

    def test_a_refused_decision_page_is_not_recorded_as_read(self):
        # Recorded as read after one refused page, a stale company would not
        # be searched again until a newer decision appeared.
        import requests

        self.hold_an_older_year()
        searched = []

        def letters(_client, symbol):
            searched.append(symbol)
            return [letter("11", "1405/05/01 10:00:00")] if symbol == "x" else []

        refused = requests.HTTPError("500 Server Error: Internal Server Error",
                                     response=mock.Mock(status_code=500))
        result = self.run_backfill({"11": refused}, letters)

        self.assertEqual((result["stored"], result["refused"]), (0, 1))
        self.assertNotIn("1", self.marks())
        self.assertIn("1", a.stale_dividends())

        self.run_backfill({"11": PAGE}, letters)

        self.assertEqual(searched.count("x"), 2)
        self.assertEqual(self.stored_years("1"),
                         {"1403/12/30": 40.0, "1404/12/29": 55.0})


class ReopenTests(unittest.TestCase):
    """The repair for the companies the older-year fall-through left."""

    def setUp(self):
        import database

        self.database = database
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.path)
        database.create_database()
        a.ensure_tables()

        stale = letter("10", "1404/05/01 10:00:00", title=OLDER_YEAR)
        a.store_dividend("1", "1403/12/30", {"dps": 40.0}, stale)
        a.store_announcement(letter("11", "1405/05/01 10:00:00"), "1", "agm")
        current = letter("12", "1405/05/01 10:00:00")
        a.store_dividend("2", "1404/12/29", {"dps": 55.0}, current)
        a.store_announcement(current, "2", "agm")

    def tearDown(self):
        self.database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.path):
            if os.path.exists(path):
                os.unlink(path)

    def rows(self):
        conn = fundamental_db._connect()
        rows = conn.execute("SELECT ins_code, fiscal_year_end, dps "
                            "FROM dividends ORDER BY 1, 2").fetchall()
        marks = conn.execute("SELECT ins_code, fiscal_year_end, due "
                             "FROM dividend_reread ORDER BY 1").fetchall()
        conn.close()
        return rows, marks

    def test_a_dry_run_lists_and_writes_nothing(self):
        before = self.rows()
        result = a.reopen_stale_dividends(verbose=False)

        self.assertEqual((result["companies"], result["due"]), (1, 1))
        self.assertEqual(self.rows(), before)

    def test_apply_marks_the_stale_company_due_and_deletes_nothing(self):
        # Deleting the older year's row left a company whose newest decision
        # carries no DPS (وامیر) with no DPS at all.
        a.reopen_stale_dividends(apply=True, verbose=False)
        a.reopen_stale_dividends(apply=True, verbose=False)

        rows, marks = self.rows()
        self.assertEqual(rows, [("1", "1403/12/30", 40.0),
                                ("2", "1404/12/29", 55.0)])
        self.assertEqual(marks, [("1", "1404/12/29", 1)])
        self.assertEqual(a.latest_dividends()["1"]["dps"], 40.0)

    def test_a_mark_has_a_company_read_already_read_again(self):
        a._read_through("1", "1404/12/29")      # read: no DPS on FY1404's
        self.assertNotIn("1", a.stale_dividends())
        self.assertIn("1", a.stale_dividends(include_read=True))

        result = a.reopen_stale_dividends(apply=True, verbose=False)

        self.assertEqual((result["companies"], result["due"]), (1, 0))
        self.assertIn("1", a.stale_dividends())

    def test_a_recess_notice_is_no_newer_decision(self):
        a.store_announcement(letter("13", "1405/06/01 10:00:00", title=RECESS),
                             "2", "agm")
        self.assertNotIn("2", a.stale_dividends(include_read=True))

    def test_a_dps_taken_from_a_recess_notice_is_due_again(self):
        # Right year, wrong letter: the year rule cannot see it.
        recess = letter("15", "1405/04/01 10:00:00", title=RECESS_SAME_YEAR)
        a.store_announcement(recess, "3", "agm")
        a.store_dividend("3", "1404/12/29", {"dps": 380.0}, recess)
        a.store_announcement(letter("16", "1405/05/11 10:00:00"), "3", "agm")

        self.assertEqual(a.stale_dividends()["3"][0], "1404/12/29")
        a._read_through("3", "1404/12/29")     # read; the decision had none
        self.assertNotIn("3", a.stale_dividends())

    def test_a_short_year_read_without_dps_leaves_the_12_month_year(self):
        # Company 2 holds FY1404's DPS; its newest decision is a 3-month
        # year. Unread, it is due; read without a DPS, FY1404 stands.
        a.store_announcement(letter("14", "1405/08/04 16:17:29",
                                    title=SHORT_YEAR), "2", "agm")
        self.assertEqual(a.stale_dividends()["2"][0], "1405/03/31")

        a._read_through("2", "1405/03/31")

        self.assertNotIn("2", a.stale_dividends(include_read=True))


# The store as the live one was on 2026-11-11: announcements and dividends,
# no dividend_reread (which ensure_tables() creates since the DPS marks).
BEFORE_MARKS = """
    CREATE TABLE announcements (
        tracing_no TEXT PRIMARY KEY, ins_code TEXT, symbol TEXT,
        letter_code TEXT, kind TEXT, title TEXT, published_at TEXT,
        url TEXT, seen_at TEXT);
    CREATE TABLE dividends (
        ins_code TEXT, fiscal_year_end TEXT, dps REAL, capital REAL,
        tracing_no TEXT, published_at TEXT, stored_at TEXT,
        PRIMARY KEY (ins_code, fiscal_year_end));
"""


class DryRunTests(unittest.TestCase):
    """--repair-stale-dps without --apply writes nothing. It created the
    dividend_reread table in the live store, and through a read-only
    connection it failed: "attempt to write a readonly database"."""

    def setUp(self):
        import database

        self.database = database
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.path)
        database.create_database()
        fundamental_db.create_database()
        conn = fundamental_db._connect()
        conn.executescript(BEFORE_MARKS)
        conn.close()
        a.store_dividend("1", "1403/12/30", {"dps": 40.0},
                         letter("10", "1404/05/01 10:00:00", title=OLDER_YEAR))
        a.store_announcement(letter("11", "1405/05/01 10:00:00"), "1", "agm")

    def tearDown(self):
        self.database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.path):
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except OSError:                 # absent, or held open
                    pass

    def store(self):
        """Every schema object of the store, and every table's row count."""

        conn = sqlite3.connect(self.path)
        schema = conn.execute("SELECT type, name, sql FROM sqlite_master "
                              "ORDER BY type, name").fetchall()
        counts = {name: conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()
                  for kind, name, _sql in schema if kind == "table"}
        conn.close()
        return schema, counts

    def test_the_dry_run_reads_a_store_opened_read_only(self):
        store = pathlib.Path(self.path).as_uri() + "?mode=ro"
        market = pathlib.Path(self.market).as_uri() + "?mode=ro"

        with mock.patch.object(fundamental_db, "_connect",
                               lambda: sqlite3.connect(store, uri=True)), \
                mock.patch.object(self.database, "_connect",
                                  lambda: sqlite3.connect(market, uri=True)):
            result = a.reopen_stale_dividends(verbose=False)

        self.assertEqual(result, {"companies": 1, "due": 1, "applied": False})

    def test_the_dry_run_leaves_a_store_without_the_marks_table_as_it_was(self):
        before = self.store()

        result = a.reopen_stale_dividends(verbose=False)

        self.assertEqual((result["companies"], result["due"]), (1, 1))
        self.assertEqual(self.store(), before)
        self.assertNotIn("dividend_reread", [name for _k, name, _s in before[0]])

    def test_no_store_at_all_holds_nothing_and_is_not_created(self):
        absent = self.path + ".absent.db"
        self.addCleanup(lambda: [os.unlink(absent + suffix) for suffix
                                 in ("", "-wal", "-shm")
                                 if os.path.exists(absent + suffix)])
        fundamental_db.set_path(absent)

        self.assertEqual(a.stale_dividends(), {})
        self.assertEqual(a.reopen_stale_dividends(verbose=False)["companies"], 0)
        self.assertFalse(os.path.exists(absent))

    def test_apply_creates_the_table_it_writes_to(self):
        a.reopen_stale_dividends(apply=True, verbose=False)

        conn = fundamental_db._connect()
        marks = conn.execute("SELECT ins_code, fiscal_year_end, due "
                             "FROM dividend_reread").fetchall()
        conn.close()
        self.assertEqual(marks, [("1", "1404/12/29", 1)])


if __name__ == "__main__":
    unittest.main()
