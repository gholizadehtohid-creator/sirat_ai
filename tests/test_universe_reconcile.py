"""The universe follows the live market watch, not only a stale list.

Until 2026-11-11 the instrument list came from a reference file on GitHub
that nothing checked against the exchange. The 2026-11-09 watch listed 98
traded main shares it lacked -- 81 new companies that never got a bar, and
17 that had changed board, which re-issues the ISIN and the insCode, so the
universe followed a dead code (فاسمین: bars end 2026-06-10, the live code
traded 123.6M shares that day). These tests pin the reconciliation:

    ADD WHAT THE EXCHANGE LISTS. A main share on the watch that the universe
    lacks becomes an equity; block-trade boards, funds and derivatives do not.

    RE-POINT, DO NOT MIGRATE. A board-moved symbol's live code is added and
    the old code retired -- not an equity, nothing keyed by it touched. What
    the live readers ask by code -- statements, NAV, dividends, the sector --
    is COPIED to the live code; the old rows stay as they were.

    ONLY ON EVIDENCE. An old code still listed or trading, or a symbol held by
    another company, is held back rather than guessed at.

    ONLY ON REVIEW. The snapshot step adds new companies and nothing else; a
    retirement is made by `seed_import.py --repair-universe --apply`, which
    prints its undo.

    HISTORY KEEPS THE OLD LISTING. The point-in-time and backtest universes
    still load a retired code, so a past date sees what traded then.

    ONE BAD LINE IS ONE LINE. A watch row with no ISIN (None, or NaN in a
    pandas 3 frame) skips itself, not the reconciliation.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import database                                                 # noqa: E402
import seed_import                                              # noqa: E402
import universe                                                 # noqa: E402
from providers.base import SEED, ProviderResult                 # noqa: E402

NEWEST = "2026-11-09"


def bars(dates, volume=1000.0, close=100.0):
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "open": close, "high": close,
        "low": close, "close": close, "volume": volume,
        "value": volume * close, "trades": 3.0})


def watch(*rows):
    """Market-watch lines as the provider emits them (Arabic spelling)."""

    return pd.DataFrame([
        {"ins_code": code, "symbol_fa": symbol, "name_fa": name,
         "isin": isin, "close": 100.0, "volume": 5000.0}
        for code, symbol, name, isin in rows])


class WatchRecordTests(unittest.TestCase):

    def test_a_main_share_becomes_an_equity_record(self):
        record = universe.watch_record("555", "آكو", "كاشي آكو", "IRO3AKOZ0001")

        self.assertEqual(record["kind"], "equity")
        self.assertEqual(record["board"], "farabourse")
        self.assertEqual(record["symbol_fa"], "اکو")        # folded for joins
        self.assertEqual(record["symbol_raw"], "آکو")       # Codal's spelling

    def test_everything_else_is_not(self):
        for isin in ("IRO3AKOZ0003",       # block-trade board, ends in 3
                     "IRT1AKOZ0001",       # fund
                     "IRO9AKOZ0001",       # option
                     "IRR3AKOZ0001",       # rights
                     "", None):
            self.assertIsNone(universe.watch_record("555", "x", "x", isin), isin)

    def test_company_code_survives_a_board_move(self):
        self.assertEqual(universe.company_code("IRO1KSIM0001"),
                         universe.company_code("IRO7KSIM0001"))
        self.assertIsNone(universe.company_code("nonsense"))

    def test_a_missing_field_is_no_record_not_an_error(self):
        """None or NaN -- pandas 3 stores a missing str as float NaN."""

        nan = float("nan")

        for isin in (None, nan):
            self.assertIsNone(universe.watch_record("556", "x", "y", isin))
        for symbol in (None, nan, ""):
            self.assertIsNone(universe.watch_record("555", symbol, "y",
                                                    "IRO3AKOZ0001"))
        record = universe.watch_record("555", "آكو", nan, "IRO3AKOZ0001")
        self.assertEqual(record["name_fa"], "")

        self.assertEqual(universe.exchange_spelling(nan), "")
        self.assertEqual(universe.exchange_spelling(None), "")
        self.assertIsNone(universe.company_code(nan))

    def test_the_kind_is_spelled_the_same_in_both_modules(self):
        self.assertEqual(database.SUPERSEDED_KIND, universe.SUPERSEDED)


class ReconcileTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        # The fundamental store a retirement copies into: a temp path, absent
        # until a test creates it. Through the variable, which wins over any
        # module-level path (fundamental_db.path).
        self.store_dir = tempfile.mkdtemp()
        self.store = os.path.join(self.store_dir, "fundamentals.db")
        self.env = mock.patch.dict(os.environ,
                                   {"SIRAT_FUNDAMENTAL_DB": self.store})
        self.env.start()

        database.save_instruments([
            # Moved to another board: last traded in February.
            {"ins_code": "100", "symbol_fa": "فاسمین", "symbol_raw": "فاسمین",
             "isin": "IRO1KSIM0001", "kind": "equity", "board": "bourse",
             "is_equity": 1},
            {"ins_code": "200", "symbol_fa": "وبملت", "symbol_raw": "وبملت",
             "isin": "IRO1BMLT0001", "kind": "equity", "board": "bourse",
             "is_equity": 1},
            # Same symbol on the watch under a new code, but still trading.
            {"ins_code": "300", "symbol_fa": "شستا", "symbol_raw": "شستا",
             "isin": "IRO1TAMN0001", "kind": "equity", "board": "bourse",
             "is_equity": 1},
            # Same symbol on the watch, a different company code.
            {"ins_code": "400", "symbol_fa": "خساپا", "symbol_raw": "خساپا",
             "isin": "IRO1SIPA0001", "kind": "equity", "board": "bourse",
             "is_equity": 1},
        ])

        dead = bars(["2026-02-18", "2026-06-10"])
        dead.loc[1, "volume"] = 0.0
        database.save_history("100", dead)
        for code in ("200", "300", "400"):
            database.save_history(code, bars(["2026-11-08", NEWEST]))

        self.watch = watch(
            ("999", "فاسمين", "كالسيمين", "IRO7KSIM0001"),    # board move
            ("200", "وبملت", "بانك ملت", "IRO1BMLT0001"),     # known
            ("555", "آكو", "كاشي آكو", "IRO3AKOZ0001"),        # new company
            ("556", "آكو3", "كاشي آكو", "IRO3AKOZ0003"),       # block board
            ("557", "طلا", "صندوق طلا", "IRT1ZARF0001"),       # fund
            ("777", "شستا", "تامين", "IRO3TAMN0001"),          # old still live
            ("888", "خساپا", "ديگري", "IRO3OTHR0001"),         # other company
        )

    def tearDown(self):
        import shutil

        self.env.stop()
        shutil.rmtree(self.store_dir, ignore_errors=True)
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def instrument(self, code):
        return next((r for r in database.load_instruments()
                     if r["ins_code"] == code), None)

    def test_the_dry_run_plans_and_writes_nothing(self):
        plan = seed_import.reconcile_universe(self.watch, verbose=False)

        self.assertEqual(sorted(r["ins_code"] for r in plan["added"]),
                         ["555", "999"])
        self.assertEqual([(old["ins_code"], new["ins_code"])
                          for old, new in plan["retired"]], [("100", "999")])
        self.assertEqual(sorted(r["ins_code"] for r, _ in plan["held"]),
                         ["777", "888"])
        self.assertFalse(plan["applied"])
        self.assertIsNone(self.instrument("999"))
        self.assertEqual(self.instrument("100")["is_equity"], 1)

    def test_apply_re_points_and_touches_nothing_keyed_by_the_old_code(self):
        before = database.load_history_by_inscode("100")

        plan = seed_import.reconcile_universe(self.watch, apply=True,
                                              verbose=False)

        self.assertTrue(plan["applied"])
        live = self.instrument("999")
        self.assertEqual((live["is_equity"], live["symbol_fa"], live["isin"]),
                         (1, "فاسمین", "IRO7KSIM0001"))
        self.assertEqual(self.instrument("555")["symbol_raw"], "آکو")

        old = self.instrument("100")
        self.assertEqual((old["is_equity"], old["kind"]),
                         (0, universe.SUPERSEDED))
        pd.testing.assert_frame_equal(
            database.load_history_by_inscode("100"), before)

        equities = {r["ins_code"]
                    for r in database.load_instruments(equities_only=True)}
        self.assertEqual(equities, {"200", "300", "400", "555", "999"})

    def test_a_second_run_has_nothing_to_do(self):
        seed_import.reconcile_universe(self.watch, apply=True, verbose=False)
        plan = seed_import.reconcile_universe(self.watch, apply=True,
                                              verbose=False)

        self.assertEqual((plan["added"], plan["retired"], plan["applied"]),
                         ([], [], False))

    def test_a_symbol_resolves_to_the_live_code(self):
        """Lookups take the first row per symbol; it was the dead one."""

        seed_import.reconcile_universe(self.watch, apply=True, verbose=False)

        first = next(r for r in database.load_instruments()
                     if r["symbol_fa"] == "فاسمین")
        self.assertEqual(first["ins_code"], "999")

    def test_an_interrupted_apply_is_finished_by_the_next_run(self):
        """The live row stored, the old one not yet retired."""

        database.save_instruments([universe.watch_record(
            "999", "فاسمين", "كالسيمين", "IRO7KSIM0001")])

        plan = seed_import.reconcile_universe(self.watch, apply=True,
                                              verbose=False)

        self.assertEqual(plan["added"][0]["ins_code"], "555")
        self.assertEqual([(old["ins_code"], new["ins_code"])
                          for old, new in plan["retired"]], [("100", "999")])
        self.assertEqual(self.instrument("100")["kind"], universe.SUPERSEDED)

    def test_the_weekly_list_does_not_bring_a_retired_code_back(self):
        seed_import.reconcile_universe(self.watch, apply=True, verbose=False)

        stale = universe.parse_reference_list({
            "فاسمین": {"index": "100", "code": "IRO1KSIM0001",
                       "name": "کالسیمین"},
            "وبملت": {"index": "200", "code": "IRO1BMLT0001",
                      "name": "بانک ملت"},
        })

        saved = universe.fetch_reference_list
        universe.fetch_reference_list = lambda url=None: stale
        try:
            seed_import.import_universe(verbose=False)
        finally:
            universe.fetch_reference_list = saved

        self.assertEqual(self.instrument("100")["is_equity"], 0)
        self.assertEqual(self.instrument("999")["is_equity"], 1)

    def test_the_repair_command_is_a_dry_run_unless_applied(self):
        """python seed_import.py --repair-universe [--apply], stored watch."""

        import contextlib
        import io

        database.save_snapshot(self.watch)

        def command(*flags):
            saved = sys.argv
            sys.argv = ["seed_import.py", "--repair-universe", *flags]
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    self.assertEqual(seed_import.main(), 0)
            finally:
                sys.argv = saved
            return out.getvalue()

        dry = command()
        self.assertIn("2 main shares to add, 1 board-moved codes to retire",
                      dry)
        self.assertIn("Dry run", dry)
        self.assertIn(seed_import.UNDO_RETIREMENT, dry)
        self.assertIsNone(self.instrument("999"))

        applied = command("--apply")
        self.assertIn(seed_import.UNDO_RETIREMENT, applied)
        self.assertEqual(self.instrument("999")["is_equity"], 1)
        self.assertEqual(self.instrument("100")["kind"], universe.SUPERSEDED)

        again = command("--apply")
        self.assertIn("0 main shares to add, 0 board-moved codes to retire",
                      again)

        # The undo it prints is the undo: run it, and the old code is back.
        conn = database._connect()
        conn.execute(seed_import.UNDO_RETIREMENT)
        conn.commit()
        conn.close()
        self.assertEqual((self.instrument("100")["is_equity"],
                          self.instrument("100")["kind"]), (1, "equity"))

    def test_the_undo_is_in_the_command_docstring(self):
        self.assertIn(seed_import.UNDO_RETIREMENT,
                      " ".join(seed_import.__doc__.split()))

    # ---- the snapshot step ---------------------------------------------------

    def snapshot_with(self, provenance, frame=None):
        frame = self.watch.copy() if frame is None else frame

        class Provider:
            name = "fake"

            def get_market_snapshot(self):
                result = ProviderResult.success(frame, "fake")
                result.provenance = provenance
                return result

        saved = seed_import.get_provider
        seed_import.get_provider = Provider
        try:
            return seed_import.import_snapshot(verbose=False)
        finally:
            seed_import.get_provider = saved

    def test_a_live_snapshot_adds_new_companies_and_retires_nothing(self):
        """ADD-ONLY: the first evening snapshot would have retired all 17."""

        self.assertEqual(self.snapshot_with(seed_import.LIVE), len(self.watch))

        self.assertEqual(self.instrument("555")["is_equity"], 1)
        # The board move waits for --repair-universe --apply, live code and
        # retirement together: never two equities for one company.
        self.assertEqual((self.instrument("100")["is_equity"],
                          self.instrument("100")["kind"]), (1, "equity"))
        self.assertIsNone(self.instrument("999"))

        plan = seed_import.reconcile_universe(self.watch, verbose=False,
                                              retire=False)
        self.assertEqual(plan["retired"], [])
        self.assertIn("999", {r["ins_code"] for r, _ in plan["held"]})

    def test_a_watch_line_without_an_isin_skips_only_itself(self):
        """The CDN emits None for a missing insID; pandas 3 reads it NaN.

        Until 2026-11-11 that one line raised AttributeError, and the snapshot
        step's reconciliation added nothing -- the new share beside it
        included -- on every snapshot.
        """

        frame = pd.concat([self.watch, watch(
            ("560", "بی‌نام", "بدون شناسه", None),
            ("561", "نان", "نامعلوم", float("nan")))], ignore_index=True)
        self.assertTrue(frame["isin"].isna().sum() >= 2)

        self.assertEqual(self.snapshot_with(seed_import.LIVE, frame),
                         len(frame))

        self.assertEqual(self.instrument("555")["is_equity"], 1)
        self.assertIsNone(self.instrument("560"))
        self.assertIsNone(self.instrument("561"))

    def test_a_line_that_raises_is_skipped_and_counted(self):
        real = universe.watch_record

        def flaky(ins_code, *args):
            if str(ins_code) == "200":
                raise ValueError("a line no parser foresaw")
            return real(ins_code, *args)

        with mock.patch.object(universe, "watch_record", side_effect=flaky):
            plan = seed_import.reconcile_universe(self.watch, verbose=False)

        self.assertEqual(plan["unreadable"], 1)
        self.assertEqual(sorted(r["ins_code"] for r in plan["added"]),
                         ["555", "999"])

    # ---- what a retirement carries to the live code ----------------------------

    def fill_old_code(self):
        """Statements, NAV and a dividend parsed under the old code 100."""

        import codal_announcements
        import fundamental_db

        statement = {"title": "صورت مالی", "audited": True,
                     "consolidated": False, "items": {"revenue": 10.0},
                     "ratios": {"eps": 5.0}}
        database.save_codal_financials("100", dict(
            statement, period_end="1404-12-29", period_months=12,
            tracing_no="T1"))
        # A NULL key column: a primary key lets it in twice; the copy may not.
        database.save_codal_financials("100", dict(
            statement, period_end="1403-12-29", period_months=None,
            tracing_no="T0"))
        database.save_codal_filing("100", dict(
            statement, period_end="1404-12-29", period_months=12,
            tracing_no="T1", published_at="2026-04-01T10:00:00"))
        database.save_codal_portfolio("100", {
            "period_end": "1405-06-31", "tracing_no": "P1",
            "listed_market": 7.0})

        fundamental_db.create_database()
        fundamental_db.save_metrics(dict(
            statement, ins_code="100", period_end="1404-12-29",
            period_months=12, tracing_no="T1"))
        codal_announcements.ensure_tables()
        conn = fundamental_db._connect()
        conn.execute("INSERT INTO dividends (ins_code, fiscal_year_end, dps) "
                     "VALUES ('100', '1404-12-29', 300.0)")
        conn.commit()
        conn.close()

    def test_a_retirement_copies_what_live_readers_ask_by_code(self):
        """codal_sync moves the letters to the live code, parsed; the figures
        parsed from them stayed on the old one, which no live reader asks."""

        import codal_announcements
        import fundamental_db

        self.fill_old_code()
        expected = {"codal_financials": 2, "codal_portfolio": 1,
                    "metrics": 1, "dividends": 1}

        dry = seed_import.reconcile_universe(self.watch, verbose=False)
        self.assertEqual({k: dry["copies"].get(k) for k in expected},
                         expected)
        self.assertEqual(dry["copies"]["codal_filings (stays)"], 1)
        self.assertEqual(database.load_codal_financials("999"), [])

        plan = seed_import.reconcile_universe(self.watch, apply=True,
                                              verbose=False)

        self.assertEqual({k: plan["copies"].get(k) for k in expected},
                         expected)
        self.assertEqual(len(database.load_codal_financials("999")), 2)
        self.assertEqual(len(database.load_codal_financials("100")), 2)
        self.assertIn("999", database.load_codal_portfolio_latest())
        self.assertEqual(len(fundamental_db.load_metrics("999")), 1)
        self.assertEqual(len(fundamental_db.load_metrics("100")), 1)
        self.assertEqual(codal_announcements.latest_dividends()["999"]["dps"],
                         300.0)
        # One row per filing: it stays with the code it was filed under.
        self.assertEqual([r["ins_code"] for r in database.load_codal_filings()],
                         ["100"])

        # Idempotent, the NULL period_months included.
        again = seed_import._carry_over(plan["retired"], write=True)
        self.assertEqual({k: again.get(k) for k in expected},
                         dict.fromkeys(expected, 0))
        self.assertEqual(len(database.load_codal_financials("999")), 2)

    def test_a_copy_never_overwrites_what_the_live_code_holds(self):
        self.fill_old_code()
        database.save_codal_financials("999", {
            "period_end": "1404-12-29", "period_months": 12,
            "tracing_no": "T9", "title": "the live code's own",
            "items": {"revenue": 99.0}, "ratios": {}})

        plan = seed_import.reconcile_universe(self.watch, apply=True,
                                              verbose=False)

        self.assertEqual(plan["copies"]["codal_financials"], 1)
        rows = {r["period_end"]: r for r in
                database.load_codal_financials("999")}
        self.assertEqual(rows["1404-12-29"]["tracing_no"], "T9")

    def test_the_sector_goes_with_the_company(self):
        """A board move keeps the industry; the new code had none."""

        conn = database._connect()
        conn.execute("UPDATE instruments SET sector_code = '27', "
                     "sector_name = 'فلزات اساسی' WHERE ins_code = '100'")
        conn.commit()
        conn.close()

        dry = seed_import.reconcile_universe(self.watch, verbose=False)
        self.assertEqual(dry["copies"]["sector"], 1)

        seed_import.reconcile_universe(self.watch, apply=True, verbose=False)

        live = self.instrument("999")
        self.assertEqual((live["sector_code"], live["sector_name"]),
                         ("27", "فلزات اساسی"))
        self.assertEqual(self.instrument("100")["sector_name"], "فلزات اساسی")
        self.assertIsNone(self.instrument("555")["sector_name"])

    def test_a_live_code_keeps_the_sector_the_exchange_gave_it(self):
        conn = database._connect()
        conn.execute("UPDATE instruments SET sector_code = '27', "
                     "sector_name = 'فلزات اساسی' WHERE ins_code = '100'")
        conn.commit()
        conn.close()
        record = universe.watch_record("999", "فاسمين", "كالسيمين",
                                       "IRO7KSIM0001")
        record.update(sector_code="54", sector_name="کانی غیرفلزی")
        database.save_instruments([record])

        seed_import.reconcile_universe(self.watch, apply=True, verbose=False)

        self.assertEqual(self.instrument("999")["sector_code"], "54")

    def test_a_stored_copy_is_not_a_snapshot(self):
        """2026-11-10 21:04: DNS down, the 11-09 watch re-stamped as new."""

        self.assertIsNone(self.snapshot_with(SEED))

        self.assertEqual(database.snapshot_info()["rows"], 0)
        self.assertIsNone(self.instrument("555"))


class HistoricalUniverseTests(unittest.TestCase):
    """A retired listing stays in every universe that reads the past.

    Retiring 17 codes took 13,191 traded bars inside the 2021-06..2026-10
    research window out of every date, not only the dates after each move:
    registered experiments and market_valuation --rebuild no longer
    reproduced (review of 2026-11-11).
    """

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        database.save_instruments([
            {"ins_code": "999", "symbol_fa": "فاسمین", "isin": "IRO7KSIM0001",
             "kind": "equity", "board": "farabourse", "is_equity": 1},
            {"ins_code": "100", "symbol_fa": "فاسمین", "isin": "IRO1KSIM0001",
             "kind": "equity", "board": "bourse", "is_equity": 1},
            {"ins_code": "557", "symbol_fa": "طلا", "isin": "IRT1ZARF0001",
             "kind": "fund", "is_equity": 0},
        ])
        seed_import._retire_instruments(["100"])

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def test_a_live_reader_sees_the_live_code_only(self):
        codes = {r["ins_code"]
                 for r in database.load_instruments(equities_only=True)}
        self.assertEqual(codes, {"999"})

    def test_a_historical_reader_sees_the_retired_listing_too(self):
        codes = {r["ins_code"] for r in database.load_instruments(
            equities_only=True, with_superseded=True)}
        self.assertEqual(codes, {"100", "999"})

    def test_the_point_in_time_universe_keeps_it(self):
        from backtesting.pit import PointInTimeData

        self.assertEqual(set(PointInTimeData(verbose=False).instruments()),
                         {"100", "999"})

    def test_the_combined_backtest_scans_it(self):
        import backtest_combined

        seen = []

        def bars_of(code, *args, **kwargs):
            seen.append(str(code))
            return None

        with mock.patch.object(backtest_combined.database,
                               "load_history_by_inscode", side_effect=bars_of):
            backtest_combined.collect(verbose=False)

        self.assertEqual(sorted(seen), ["100", "999"])

    def test_the_portfolio_backtest_scores_it(self):
        import backtest_portfolio

        database.save_index_history(backtest_portfolio.TEDPIX, pd.DataFrame({
            "date": pd.bdate_range("2025-06-01", periods=5), "close": 1.0,
            "low": 1.0, "high": 1.0}))

        scored = []

        def score_panel(instruments, dates, *args, **kwargs):
            scored.extend(str(r["ins_code"]) for r in instruments)
            return {}

        with mock.patch.object(backtest_portfolio, "score_panel",
                               side_effect=score_panel), \
                mock.patch.object(backtest_portfolio, "simulate",
                                  return_value=(pd.DataFrame(), [])), \
                mock.patch("builtins.print"):
            backtest_portfolio.run(start="2025-06-01", end="2025-06-30",
                                   rebalance=1, verbose=False)

        self.assertEqual(sorted(scored), ["100", "999"])


class MissingSectorTests(unittest.TestCase):
    """A new listing costs one instrument-info request, not ~900."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        database.save_instruments([
            {"ins_code": "1", "symbol_fa": "الف", "kind": "equity",
             "is_equity": 1, "sector_code": "27", "sector_name": "فلزات"},
            {"ins_code": "2", "symbol_fa": "ب", "kind": "equity",
             "is_equity": 1},
            {"ins_code": "3", "symbol_fa": "پ", "kind": "fund",
             "is_equity": 0},
        ])

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def run_import(self, **kwargs):
        asked = []

        class Provider:
            name = "fake"

            def get_instrument_infos(self, codes, progress=None):
                asked.extend(codes)
                return {c: ProviderResult.success(
                    {"ins_code": c, "sector_code": "44",
                     "sector_name": "دارویی"}, "fake") for c in codes}

        with mock.patch.object(seed_import, "get_provider", Provider):
            result = seed_import.import_sectors(verbose=False, **kwargs)
        return asked, result

    def test_only_the_equities_without_a_sector_are_asked(self):
        asked, result = self.run_import(missing_only=True)

        self.assertEqual(asked, ["2"])
        self.assertEqual(result, 2)
        self.assertEqual(database.sector_coverage()["with_sector"], 2)

    def test_nothing_missing_is_no_request_and_no_failure(self):
        self.run_import(missing_only=True)
        asked, result = self.run_import(missing_only=True)

        self.assertEqual(asked, [])
        self.assertTrue(result)

    def test_the_full_import_still_asks_everyone(self):
        asked, _ = self.run_import()

        self.assertEqual(sorted(asked), ["1", "2"])


if __name__ == "__main__":
    unittest.main()
