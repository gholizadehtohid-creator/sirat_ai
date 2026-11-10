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
    the old code retired -- not an equity, nothing keyed by it touched.

    ONLY ON EVIDENCE. An old code still listed or trading, or a symbol held by
    another company, is held back rather than guessed at.
"""

import os
import sys
import tempfile
import unittest

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


class ReconcileTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

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
        self.assertIsNone(self.instrument("999"))

        command("--apply")
        self.assertEqual(self.instrument("999")["is_equity"], 1)
        self.assertEqual(self.instrument("100")["kind"], universe.SUPERSEDED)

        again = command("--apply")
        self.assertIn("0 main shares to add, 0 board-moved codes to retire",
                      again)

    # ---- the snapshot step ---------------------------------------------------

    def snapshot_with(self, provenance):
        frame = self.watch.copy()

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

    def test_a_live_snapshot_brings_the_universe_up_to_date(self):
        self.assertEqual(self.snapshot_with(seed_import.LIVE), len(self.watch))

        self.assertEqual(self.instrument("555")["is_equity"], 1)
        self.assertEqual(self.instrument("100")["kind"], universe.SUPERSEDED)

    def test_a_stored_copy_is_not_a_snapshot(self):
        """2026-11-10 21:04: DNS down, the 11-09 watch re-stamped as new."""

        self.assertIsNone(self.snapshot_with(SEED))

        self.assertEqual(database.snapshot_info()["rows"], 0)
        self.assertIsNone(self.instrument("555"))


if __name__ == "__main__":
    unittest.main()
