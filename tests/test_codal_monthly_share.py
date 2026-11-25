"""codal_monthly / codal_portfolio: the history gets its turn, a letter that
is not a report is settled, and a pass that was throttled out says so.

The review, 2026-11-23: the monthly history never ran (the due pass ate
--hours under 429s on all eight evenings), 15 of the first 38 companies in
it were headed by a letter that answers 1,583 bytes on every request, and
both collectors exited 0 whatever had happened.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal  # noqa: E402
import codal_monthly  # noqa: E402
import codal_portfolio  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402

TITLE = "گزارش فعالیت ماهانه دوره 1 ماهه منتهی به %s"
SMALL = ("filing returned only 1583 bytes — too small to be a statement, "
         "treating as throttled")
# What codal.parse_body says of the empty workbook, named by its content.
EMPTY = ("filing returned only 1583 bytes — %s an empty workbook, not a block "
         "page; left pending until it repeats" % codal.EXPORTER_EMPTY)


class Clock:
    """codal_monthly.time: a wall clock the test moves."""

    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def answer(ok=True, transient=False, error=None):
    return mock.Mock(ok=ok, data={"s": []}, transient=transient, error=error)


class Store(unittest.TestCase):
    """A temporary fundamental store and a market store holding the letters
    of two companies: "1" has 1405/07 stored and 06 and 05 known, "2" has
    07 known."""

    def setUp(self):
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.previous = fundamental_db.set_path(self.store)
        conn = sqlite3.connect(self.market)
        conn.execute("""CREATE TABLE codal_letters (tracing_no TEXT PRIMARY KEY,
            ins_code TEXT, symbol TEXT, letter_code TEXT, title TEXT,
            published_at TEXT, url TEXT, excel_url TEXT, has_excel INTEGER,
            parsed INTEGER, seen_at TEXT)""")
        letters = [("t1", "1", TITLE % "1405/07/30", "1405/08/05"),
                   ("t2", "1", TITLE % "1405/06/31", "1405/07/05"),
                   ("t3", "1", TITLE % "1405/05/31", "1405/06/05"),
                   ("u1", "2", TITLE % "1405/07/30", "1405/08/05")]
        conn.executemany(
            "INSERT INTO codal_letters VALUES (?,?,?,?,?,?,?,?,1,0,'')",
            [(t, c, "s" + c, "ن-30", title, pub, "u", "x/" + t)
             for t, c, title, pub in letters])
        conn.commit()
        conn.close()
        self.clock = Clock()
        self.patches = [
            mock.patch.object(database, "DATABASE_NAME", self.market),
            mock.patch.object(database, "load_instruments",
                              return_value=[{"ins_code": "1"}, {"ins_code": "2"}]),
            mock.patch.object(database, "save_codal_letters"),
            mock.patch.object(codal_monthly, "last_closed_month",
                              return_value=(1405, 8)),
            mock.patch.object(codal_monthly.monthly_reports, "parse",
                              side_effect=lambda _d, title: {
                                  "period_end": title[-10:], "template": "m"}),
            mock.patch.object(codal_monthly, "time", self.clock),
            mock.patch("codal_sync.codal_symbol",
                       side_effect=lambda inst: "s" + str(inst["ins_code"])),
        ]
        for p in self.patches:
            p.start()
        codal_monthly._ensure_tables()
        codal_monthly.store("1", {"tracing_no": "t1", "published_at": "1405/08/05",
                                  "title": TITLE % "1405/07/30"},
                            {"period_end": "1405/07/30", "template": "m"})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        fundamental_db.set_path(self.previous)
        for path in (self.store, self.market):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    try:
                        os.unlink(path + suffix)
                    except OSError:
                        pass

    def rows(self, sql):
        conn = sqlite3.connect(self.store)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def client(self, small=(), refused=(), empty=()):
        """A client whose downloads answer by tracing number."""

        asked = []

        def fetch(url, archive_key=None):
            asked.append(archive_key)
            if archive_key in empty:
                return answer(ok=False, transient=True, error=EMPTY)
            if archive_key in small:
                return answer(ok=False, transient=True, error=SMALL)
            if archive_key in refused:
                return answer(ok=False, transient=True, error="429 Client Error")
            return answer()

        made = mock.Mock()
        made.fetch_sheets.side_effect = fetch
        return made, asked


class EnsureTablesTests(Store):

    def test_the_schema_is_checked_once_per_process_per_store(self):
        """load() runs per company: ~800 connections a screen for nothing."""

        with mock.patch.object(fundamental_db, "create_database",
                               wraps=fundamental_db.create_database) as create:
            for _ in range(5):
                codal_monthly._ensure_tables()
                codal_monthly.load("1")
            self.assertEqual(create.call_count, 0)        # setUp made it

            handle, other = tempfile.mkstemp(suffix=".db")
            os.close(handle)
            os.unlink(other)
            fundamental_db.set_path(other)
            try:
                codal_monthly._ensure_tables()
                codal_monthly._ensure_tables()
                self.assertEqual(create.call_count, 1)    # a new store: once
            finally:
                fundamental_db.set_path(self.store)
                for suffix in ("", "-wal", "-shm"):
                    if os.path.exists(other + suffix):
                        os.unlink(other + suffix)

    def test_a_store_that_was_deleted_is_made_again(self):
        os.unlink(self.store)
        codal_monthly._ensure_tables()
        self.assertEqual(codal_monthly.load("1"), [])


class SmallAnswerTests(Store):

    def test_the_byte_count_is_read_from_codals_message(self):
        self.assertEqual(codal_monthly._small_body(SMALL), 1583)
        self.assertIsNone(codal_monthly._small_body("429 Client Error"))
        self.assertIsNone(codal_monthly._small_body(None))

    def history(self, day, **answers):
        client, asked = self.client(**answers)
        with mock.patch.object(codal_monthly, "due_companies", return_value=[]), \
                mock.patch.object(codal_monthly, "_now",
                                  return_value="2026-11-%02dT20:00:00+00:00" % day):
            totals = codal_monthly.run(verbose=False, client=client)
        return totals, asked

    def test_a_small_letter_no_longer_ends_its_companys_turn(self):
        """آبین, 2026-11-23: fourteen older months behind one such letter."""

        totals, asked = self.history(23, small={"t2"})

        self.assertEqual(asked, ["t2", "t3", "u1"])
        self.assertEqual(totals["history_stored"], 2)
        self.assertEqual(self.rows("SELECT attempts, bytes FROM monthly_attempts "
                                   "WHERE tracing_no = 't2'"), [(1, 1583)])

    def test_three_days_of_the_same_answer_settle_the_letter(self):
        # A fresh letter to succeed after it each night, so the count stands.
        for day, tracing in ((23, "t3"), (24, "u1")):
            with mock.patch.object(
                    codal_monthly, "history_due",
                    return_value=[("1", [self.letter("t2"), self.letter(tracing)])]):
                self.history(day, small={"t2"})
        self.assertEqual(self.rows("SELECT attempts FROM monthly_attempts"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM monthly_failures"), [(0,)])

        with mock.patch.object(codal_monthly, "history_due",
                               return_value=[("1", [self.letter("t2")])]):
            totals, _asked = self.history(25, small={"t2"})

        reason = self.rows("SELECT reason FROM monthly_failures "
                           "WHERE tracing_no = 't2'")[0][0]
        self.assertIn("1583", reason)
        self.assertEqual(totals["settled"], 1)
        # Settled: the next run does not ask for it.
        self.assertIn("t2", codal_monthly._known())

    def letter(self, tracing):
        return {"tracing_no": tracing, "symbol": "s1", "excel_url": "x/" + tracing,
                "title": TITLE % "1405/06/31", "published_at": "1405/07/05"}

    def test_a_second_answer_the_same_day_adds_nothing(self):
        for _ in range(3):
            with mock.patch.object(
                    codal_monthly, "history_due",
                    return_value=[("1", [self.letter("t2")])]):
                self.history(23, small={"t2"})
        self.assertEqual(self.rows("SELECT attempts FROM monthly_attempts"), [(1,)])

    def test_a_different_size_starts_the_count_again(self):
        letter = self.letter("t2")
        with mock.patch.object(codal_monthly, "_now",
                               return_value="2026-11-23T20:00:00+00:00"):
            codal_monthly._count_small("1", letter, 1583)
        with mock.patch.object(codal_monthly, "_now",
                               return_value="2026-11-24T20:00:00+00:00"):
            codal_monthly._count_small("1", letter, 3035)
        self.assertEqual(self.rows("SELECT attempts, bytes FROM monthly_attempts"),
                         [(1, 3035)])

    def test_a_small_answer_inside_a_run_of_refusals_is_the_firewall(self):
        """A throttle refuses in runs: the answer before or after it was
        refused too, and nothing is counted."""

        totals = {"checked": 0, "stored": 0, "unreadable": 0, "failed": 0}
        small = codal_monthly._SmallAnswers(set(), totals)
        small.small("1", self.letter("t2"), 1583)
        small.refused()                     # the next request was refused
        small.ok()
        small.refused()
        small.small("1", self.letter("t2"), 1583)   # the one before was refused
        small.ok()
        small.small("1", self.letter("t2"), 1583)
        small.small("1", self.letter("t3"), 1583)   # two in a row: neither
        small.finish()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM monthly_attempts"), [(0,)])

    def test_any_other_refusal_still_ends_the_companys_turn(self):
        totals, asked = self.history(23, refused={"t2"})
        self.assertEqual(asked, ["t2", "u1"])       # t3 waits for the pacer
        self.assertEqual(self.rows("SELECT COUNT(*) FROM monthly_attempts"), [(0,)])


class EmptyExportTests(Store):
    """2026-11-25: "4 downloaded, 0 stored" two nights running. The history
    began with the companies that had every month missing; Codal exports
    theirs as an empty workbook (تملت); three in a row were three refusals,
    and the pacer's 15 minutes were the rest of the night."""

    def history(self, day, due=None, **answers):
        client, asked = self.client(**answers)
        patches = [mock.patch.object(codal_monthly, "due_companies", return_value=[]),
                   mock.patch.object(codal_monthly, "_now",
                                     return_value="2026-11-%02dT20:00:00+00:00" % day)]
        if due is not None:
            patches.append(mock.patch.object(codal_monthly, "history_due",
                                             return_value=due))
        for p in patches:
            p.start()
        try:
            totals = codal_monthly.run(hours=0.1, verbose=False, client=client)
        finally:
            for p in patches:
                p.stop()
        return totals, asked

    def letters(self, count):
        return [(str(n), [{"tracing_no": "h%d" % n, "symbol": "s",
                           "excel_url": "x/h%d" % n, "title": TITLE % "1405/05/31",
                           "published_at": "1405/06/05"}]) for n in range(count)]

    def test_a_run_of_empty_exports_does_not_stop_the_night(self):
        totals, asked = self.history(25, due=self.letters(6),
                                     empty={"h0", "h1", "h2", "h3"})
        self.assertEqual(asked, ["h%d" % n for n in range(6)])
        self.assertEqual(totals["history_stored"], 2)
        self.assertEqual(totals["empty"], 4)
        self.assertEqual(totals["failed"], 0)
        self.assertIsNone(totals["cut_short"])

    def test_it_is_counted_without_a_success_beside_it(self):
        """Named by its content, it cannot be the firewall's answer."""

        self.history(25, due=self.letters(3), empty={"h0", "h1", "h2"})
        self.assertEqual(sorted(self.rows(
            "SELECT tracing_no, attempts, bytes FROM monthly_attempts")),
            [("h0", 1, 1583), ("h1", 1, 1583), ("h2", 1, 1583)])

    def test_the_same_empty_export_on_three_days_settles_the_letter(self):
        for day in (25, 25, 26):                # twice on the 25th: one count
            self.history(day, due=self.letters(1), empty={"h0"})
        self.assertEqual(self.rows("SELECT attempts FROM monthly_attempts"), [(2,)])
        totals, _asked = self.history(27, due=self.letters(1), empty={"h0"})
        self.assertEqual(totals["settled"], 1)
        self.assertIn("h0", codal_monthly._known())

    def test_a_throttle_page_of_that_size_is_still_a_refusal(self):
        """Without the exporter's name on it, a small body is what it was."""

        totals, asked = self.history(25, due=self.letters(6),
                                     small={"h%d" % n for n in range(6)})
        self.assertEqual(asked, ["h0", "h1", "h2"])
        self.assertIn("throttled", totals["cut_short"])

    def test_companies_with_a_report_stored_are_asked_first(self):
        conn = sqlite3.connect(self.market)
        conn.executemany(
            "INSERT INTO codal_letters VALUES (?,?,?,?,?,?,?,?,1,0,'')",
            [("u%d" % n, "2", "s2", "ن-30", TITLE % ("1405/%02d/30" % (8 - n)),
              "1405/%02d/05" % (9 - n), "u", "x/u%d" % n) for n in range(2, 6)])
        conn.commit()
        conn.close()
        due = codal_monthly.history_due(15, today=(1405, 9, 2))
        # "2" has five months missing and nothing stored; "1" has two missing
        # and one stored: the one whose reports are known to parse goes first.
        self.assertEqual([(code, len(letters)) for code, letters in due],
                         [("1", 2), ("2", 5)])


class ShareTests(Store):

    def companies(self, count):
        return [{"ins_code": str(n)} for n in range(10, 10 + count)]

    def test_the_due_pass_stops_at_its_share_and_the_history_runs(self):
        """Half an hour a company: the third would start at minute 60 of 90,
        past the due pass's 54."""

        client, asked = self.client()

        def search(_client, _symbol):
            self.clock.now += 1800
            return [], None, None

        with mock.patch.object(codal_monthly, "due_companies",
                               return_value=self.companies(3)), \
                mock.patch.object(codal_monthly, "search",
                                  side_effect=search) as searched:
            totals = codal_monthly.run(hours=1.5, verbose=False, client=client)

        self.assertEqual(searched.call_count, 2)
        self.assertEqual(totals["left"], 1)
        self.assertIn("share", totals["cut_short"])
        self.assertEqual(asked, ["t2", "t3", "u1"])
        self.assertEqual(totals["history_stored"], 3)

    def test_with_no_history_waiting_the_due_pass_has_all_the_time(self):
        client, _asked = self.client()

        def search(_client, _symbol):
            self.clock.now += 1800
            return [], None, None

        with mock.patch.object(codal_monthly, "due_companies",
                               return_value=self.companies(3)), \
                mock.patch.object(codal_monthly, "search",
                                  side_effect=search) as searched:
            totals = codal_monthly.run(hours=1.5, verbose=False, client=client,
                                       history_months=0)

        self.assertEqual(searched.call_count, 3)
        self.assertIsNone(totals["cut_short"])

    def test_a_throttled_due_pass_no_longer_costs_the_history_its_night(self):
        """Every evening to 2026-11-23: 429s, cool-offs, no history."""

        client, asked = self.client()
        refuse = mock.Mock(return_value=(None, codal.KIND_TRANSIENT, "429"))

        with mock.patch.object(codal_monthly, "due_companies",
                               return_value=self.companies(40)), \
                mock.patch.object(codal_monthly, "search", refuse):
            totals = codal_monthly.run(hours=1.5, verbose=False, client=client)

        self.assertIn("throttled", totals["cut_short"])
        self.assertGreater(totals["left"], 0)
        # The cool-offs stayed inside the due pass's share of the time...
        self.assertLessEqual(self.clock.now - 1_000_000.0,
                             1.5 * 3600 * codal_monthly.DUE_SHARE)
        # ...and the history was downloaded all the same.
        self.assertEqual(asked, ["t2", "t3", "u1"])
        self.assertEqual(totals["history_stored"], 3)

    def test_a_history_stopped_by_the_download_host_is_cut_short(self):
        """Three refusals in a row and a cool-off that would pass the time
        limit: the pacer stops, with letters of this run still to fetch."""

        due = [(str(n), [{"tracing_no": "h%d" % n, "symbol": "s",
                          "excel_url": "x/h%d" % n, "title": TITLE % "1405/05/31",
                          "published_at": "1405/06/05"}]) for n in range(6)]
        client, asked = self.client(refused={"h%d" % n for n in range(6)})
        with mock.patch.object(codal_monthly, "due_companies", return_value=[]), \
                mock.patch.object(codal_monthly, "history_due", return_value=due):
            totals = codal_monthly.run(hours=0.1, verbose=False, client=client)
        self.assertEqual(asked, ["h0", "h1", "h2"])
        self.assertIn("the history: throttled, 3 of this run's letters left",
                      totals["cut_short"])

    def test_a_refusal_or_two_at_the_end_of_the_list_is_not_a_throttle(self):
        client, asked = self.client(refused={"t2", "u1"})
        with mock.patch.object(codal_monthly, "due_companies", return_value=[]):
            totals = codal_monthly.run(hours=1.5, verbose=False, client=client)
        self.assertEqual(asked, ["t2", "u1"])
        self.assertIsNone(totals["cut_short"])

    def test_a_run_that_reaches_its_limit_is_not_cut_short(self):
        client, asked = self.client()
        with mock.patch.object(codal_monthly, "due_companies", return_value=[]):
            totals = codal_monthly.run(hours=1.5, verbose=False, client=client,
                                       history_limit=2)
        self.assertEqual(len(asked), 2)
        self.assertIsNone(totals["cut_short"])


class ExitCodeTests(unittest.TestCase):

    def monthly(self, totals):
        with mock.patch.object(codal_monthly, "_single_instance",
                               return_value=object()), \
                mock.patch.object(codal_monthly, "run", return_value=totals), \
                mock.patch.object(codal_monthly, "report"):
            return codal_monthly.main(["--hours", "1.5", "--limit", "120"])

    def test_monthly_says_when_it_was_cut_short(self):
        self.assertEqual(self.monthly({"cut_short": None}), 0)
        self.assertEqual(
            self.monthly({"cut_short": "the due pass: throttled, 28 companies left"}),
            codal_monthly.CUT_SHORT_EXIT)
        self.assertEqual(codal_monthly.CUT_SHORT_EXIT, 3)

    def portfolio(self, totals):
        lock = mock.Mock()
        with mock.patch.object(codal_monthly, "_single_instance",
                               return_value=lock), \
                mock.patch.object(codal_portfolio, "run", return_value=totals), \
                mock.patch.object(codal_portfolio, "report"):
            return codal_portfolio.main(["--hours", "0.75", "--limit", "60"])

    def test_portfolio_says_when_it_was_cut_short(self):
        self.assertEqual(self.portfolio({"cut_short": None}), 0)
        self.assertEqual(self.portfolio({"cut_short": "throttled, 56 companies left"}),
                         codal_portfolio.CUT_SHORT_EXIT)
        self.assertEqual(codal_portfolio.CUT_SHORT_EXIT,
                         codal_monthly.CUT_SHORT_EXIT)


class PortfolioCutShortTests(unittest.TestCase):

    def run_pass(self, companies, search, fetch, clock=None, hours=None):
        patches = [
            mock.patch.object(codal_portfolio, "_ensure_tables"),
            mock.patch.object(codal_portfolio, "due_companies",
                              return_value=companies),
            mock.patch.object(codal_portfolio, "held_period",
                              return_value="1405/04/31"),
            mock.patch.object(codal_portfolio, "want_period",
                              return_value="1405/08"),
            mock.patch.object(codal_portfolio, "search", side_effect=search),
            mock.patch.object(codal_portfolio, "_set_status"),
            mock.patch.object(codal_portfolio.database, "save_codal_letters"),
            mock.patch.object(codal_portfolio, "fetch_newest", side_effect=fetch),
            mock.patch("codal_sync.codal_symbol",
                       side_effect=lambda i: i["ins_code"]),
        ]
        if clock is not None:
            patches += [mock.patch.object(codal_portfolio, "time", clock),
                        mock.patch.object(codal_monthly, "time", clock)]
        for p in patches:
            p.start()
        try:
            return codal_portfolio.run(hours=hours, limit=None, verbose=False,
                                       client=object())
        finally:
            for p in patches:
                p.stop()

    COMPANIES = [{"ins_code": str(n)} for n in range(6)]
    FOUND = ([{"tracing_no": "30"}], None, None)

    def test_a_pass_that_reached_every_company_is_not_cut_short(self):
        totals = self.run_pass(
            self.COMPANIES, lambda _c, _s: self.FOUND,
            lambda *a, **k: {"stored": 1, "transient": False, "requested": 1,
                             "error": None, "outcome": "stored"})
        self.assertIsNone(totals["cut_short"])
        self.assertEqual(totals["stored"], 6)

    def test_three_refused_downloads_cut_it_short(self):
        totals = self.run_pass(
            self.COMPANIES, lambda _c, _s: self.FOUND,
            lambda *a, **k: {"stored": 0, "transient": True, "requested": 1,
                             "error": None, "outcome": "refused"})
        self.assertIn("refused", totals["cut_short"])

    def test_a_throttled_search_host_cuts_it_short(self):
        """2026-11-19: "0 checked, 0 stored, 4 failed" and exit 0."""

        clock = Clock()
        totals = self.run_pass(
            self.COMPANIES,
            lambda _c, _s: (None, codal.KIND_TRANSIENT, "429"),
            lambda *a, **k: self.fail("no download after a refused search"),
            clock=clock, hours=0.2)
        self.assertIn("throttled", totals["cut_short"])
        self.assertEqual(totals["checked"], 0)

    def test_running_out_of_time_cuts_it_short(self):
        clock = Clock()

        def search(_c, _s):
            clock.now += 1800
            return self.FOUND

        totals = self.run_pass(
            self.COMPANIES, search,
            lambda *a, **k: {"stored": 1, "transient": False, "requested": 1,
                             "error": None, "outcome": "stored"},
            clock=clock, hours=0.75)
        self.assertIn("time is up", totals["cut_short"])
        self.assertIn("4 companies left", totals["cut_short"])


if __name__ == "__main__":
    unittest.main()
