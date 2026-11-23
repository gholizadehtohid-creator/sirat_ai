"""Markup in a symbol or a name is refused at the door, not stored.

The security review, 2026-11-23: a symbol is the one string the panel prints
on every card, and three of its tables wrote it into the page as it came.
The instrument list has two doors -- the reference list, a third party's
file on GitHub that is re-read every week, and the market watch -- and
neither looked at what a symbol was made of: an entry spelled
<svg/onload=alert(1)> with an equity's ISIN was stored as an equity, sorted
ahead of every Persian symbol, and would have run in the panel. These tests
pin the door:

    MARKUP AND CONTROL CHARACTERS ARE REFUSED. < > " ' & or a control
    character in the symbol, the name or the ISIN: the entry is left out,
    counted and logged -- by its insCode, never by its text.

    IT IS NOT AN ALPHABET. Persian and Arabic letters, digits, ZWNJ, spaces,
    dots, dashes, parentheses and Latin letters all pass, and so does the tab
    a real fund's name carries on the watch (folded to a space, as before).

    ONE BAD LINE IS ONE LINE. The entries beside a refused one are stored.
"""

import contextlib
import io
import os
import shutil
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

HOSTILE = "<svg/onload=alert(1)>"
ZWNJ = "‌"

# What real tickers and names look like, the odd ones included.
REAL = {
    "وبملت": {"index": "200", "code": "IRO1BMLT0001", "name": "بانک ملت"},
    # a block-trade board: the symbol ends in a digit
    "شکربن3": {"index": "201", "code": "IRO3SHKB0003", "name": "شکربن"},
    # a rights issue: the symbol ends in ح
    "فولادح": {"index": "202", "code": "IRR1FOLD0101",
               "name": "ح . فولاد مبارکه اصفهان"},
    # Arabic yeh and kaf, as the exchange spells them
    "آكو": {"index": "203", "code": "IRO3AKOZ0001", "name": "كاشي آكو"},
    # a zero-width non-joiner inside the symbol and the name
    "های" + ZWNJ + "وب": {"index": "204", "code": "IRO1HWEB0001",
                           "name": "داده" + ZWNJ + "گستر عصر نوین"},
    # dots, a dash, parentheses, Latin letters, a slash, a percent sign
    "ETF-طلا": {"index": "205", "code": "IRT1ZARF0001",
                "name": "ص.س. طلا (ETF) 20% - د/ب"},
    # the tab one fund's name carries on the 2026-11-23 watch
    "نمك": {"index": "206", "code": "IRT3NMKF0001",
            "name": "صندوق س.بخشي گستره فيروزه3\t-ب"},
}


def watch(*rows):
    return pd.DataFrame([
        {"ins_code": code, "symbol_fa": symbol, "name_fa": name,
         "isin": isin, "close": 100.0, "volume": 5000.0}
        for code, symbol, name, isin in rows])


class UnsafeTextTests(unittest.TestCase):

    def test_markup_and_control_characters_are_named(self):
        for ch in "<>\"'&":
            self.assertIn("markup", universe.unsafe_text("فولاد" + ch), ch)
        for ch in ("\x00", "\x1b", "\x7f", "\n", "\t"):
            self.assertIn("control", universe.unsafe_text("فولاد" + ch),
                          repr(ch))
        # any one of several values
        self.assertIsNotNone(universe.unsafe_text("فولاد", None, "a<b"))

    def test_the_reason_never_repeats_the_text(self):
        why = universe.unsafe_text(HOSTILE)
        self.assertNotIn("svg", why)
        self.assertNotIn("alert", why)

    def test_it_is_not_an_alphabet(self):
        for text in ("فولاد", "شکربن3", "رانفور2", "فولادح", "آكو",
                     "كاشي آكو", "های" + ZWNJ + "وب", "ETF-طلا",
                     "ص.س. طلا (ETF) 20% - د/ب", "اخزا۴۰۴", "IRO1FOLD0001",
                     "«سرمایه‌گذاری»، شرکت؛ سهامی عام", ""):
            self.assertIsNone(universe.unsafe_text(text), text)

    def test_a_missing_value_is_not_text(self):
        self.assertIsNone(universe.unsafe_text(None, float("nan"), 5))


class ReferenceListDoorTests(unittest.TestCase):

    def test_a_hostile_symbol_is_left_out_counted_and_logged(self):
        payload = dict(REAL)
        payload[HOSTILE] = {"index": "111", "code": "IRO1ZZZZ0001",
                            "name": "x"}
        refused = []

        with self.assertLogs("universe", level="WARNING") as logs:
            records = universe.parse_reference_list(payload, refused=refused)

        self.assertEqual(refused, ["111"])
        self.assertEqual(sorted(r["ins_code"] for r in records),
                         sorted(m["index"] for m in REAL.values()))
        text = "\n".join(logs.output)
        self.assertIn("1 entries refused", text)
        self.assertIn("111", text)
        self.assertNotIn("svg", text)             # the code, never the text

    def test_a_hostile_name_or_isin_is_refused_too(self):
        cases = {
            "name": {"index": "112", "code": "IRO1FOLD0001",
                     "name": "<img src=x onerror=alert(2)>"},
            "isin": {"index": "113", "code": "\"><script>", "name": "x"},
            "ampersand": {"index": "114", "code": "IRO1FOLD0001",
                          "name": "a &lt; b"},
            "quote": {"index": "115", "code": "IRO1FOLD0001",
                      "name": "x' onmouseover='y"},
        }

        for label, meta in cases.items():
            with self.assertLogs("universe", level="WARNING"):
                records = universe.parse_reference_list({"فولاد": meta})
            self.assertEqual(records, [], label)

    def test_a_control_character_in_the_symbol_is_refused(self):
        for ch in ("\x00", "\x1b", "\x7f"):
            with self.assertLogs("universe", level="WARNING"):
                records = universe.parse_reference_list({
                    "فولاد" + ch: {"index": "116", "code": "IRO1FOLD0001",
                                   "name": "فولاد"}})
            self.assertEqual(records, [], repr(ch))

    def test_real_symbols_and_names_all_pass_and_nothing_is_logged(self):
        refused = []

        with self.assertNoLogs("universe", level="WARNING"):
            records = universe.parse_reference_list(REAL, refused=refused)

        self.assertEqual(refused, [])
        self.assertEqual(len(records), len(REAL))
        by_code = {r["ins_code"]: r for r in records}
        self.assertEqual(by_code["201"]["symbol_fa"], "شکربن3")
        self.assertEqual(by_code["204"]["symbol_raw"], "های" + ZWNJ + "وب")
        # The tab is folded to a space, as it always was -- not a refusal.
        self.assertEqual(by_code["206"]["name_fa"],
                         "صندوق س.بخشی گستره فیروزه3 -ب")

    def test_many_refusals_are_one_line_with_three_examples(self):
        payload = {"%s%d" % (HOSTILE, i): {"index": str(500 + i),
                                           "code": "IRO1ZZZZ0001", "name": "x"}
                   for i in range(5)}

        with self.assertLogs("universe", level="WARNING") as logs:
            records = universe.parse_reference_list(payload)   # no list given

        self.assertEqual(records, [])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("5 entries refused", logs.output[0])
        self.assertIn("500, 501, 502, ...", logs.output[0])
        self.assertNotIn("503", logs.output[0])


class WatchDoorTests(unittest.TestCase):

    def test_a_hostile_main_share_line_raises_unsafe_text(self):
        for symbol, name in ((HOSTILE, "x"),
                             ("خوب", "<img src=x onerror=alert(2)>"),
                             ("خوب\x1b[2J", "x")):
            with self.assertRaises(universe.UnsafeText) as caught:
                universe.watch_record("666", symbol, name, "IRO3EVIL0001")
            message = str(caught.exception)
            self.assertIn("666", message)
            self.assertNotIn("svg", message)
            self.assertNotIn("onerror", message)

    def test_unsafe_text_is_a_value_error(self):
        """So a caller that only knows "a line that raises is skipped" still
        skips it (seed_import.reconcile_universe's broad except)."""

        self.assertTrue(issubclass(universe.UnsafeText, ValueError))

    def test_a_hostile_line_that_is_no_main_share_is_just_not_a_record(self):
        """A fund or an option is never stored from the watch, so there is
        nothing to refuse and nothing to count."""

        for isin in ("IRT1ZARF0001", "IRO9AKOZ0001", "IRO3AKOZ0003", ""):
            self.assertIsNone(
                universe.watch_record("557", HOSTILE, HOSTILE, isin), isin)

    def test_real_lines_still_become_records(self):
        record = universe.watch_record("555", "آكو", "كاشي آكو", "IRO3AKOZ0001")
        self.assertEqual(record["symbol_fa"], "اکو")

        # a digit in the symbol, a ZWNJ, a tab inside the name
        record = universe.watch_record(
            "558", "های" + ZWNJ + "وب2", "داده" + ZWNJ + "گستر\tعصر نوین",
            "IRO1HWEB0001")
        self.assertEqual(record["symbol_fa"], "هایوب2")
        self.assertEqual(record["name_fa"], "دادهگستر عصر نوین")

        record = universe.watch_record("559", "فولادح", "ح . فولاد (مبارکه)",
                                       "IRO1FOLD0001")
        self.assertEqual(record["kind"], "equity")


class StoredInstrumentsTests(unittest.TestCase):
    """Through to the table: what is refused is not there afterwards."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        self.store_dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "SIRAT_FUNDAMENTAL_DB": os.path.join(self.store_dir, "f.db")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.store_dir, ignore_errors=True)
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def stored(self):
        return {str(r["ins_code"]): r for r in database.load_instruments()}

    def test_the_weekly_list_stores_everything_but_the_hostile_entry(self):
        payload = dict(REAL)
        payload[HOSTILE] = {"index": "111", "code": "IRO1ZZZZ0001",
                            "name": "x"}

        with mock.patch.object(
                universe, "fetch_reference_list",
                side_effect=lambda url=None:
                universe.parse_reference_list(payload)), \
                self.assertLogs("universe", level="WARNING"):
            stats = seed_import.import_universe(verbose=False)

        self.assertEqual(stats["total"], len(REAL))
        stored = self.stored()
        self.assertNotIn("111", stored)
        self.assertEqual(sorted(stored),
                         sorted(m["index"] for m in REAL.values()))
        for row in stored.values():
            for field in ("symbol_fa", "symbol_raw", "name_fa"):
                self.assertNotIn("<", row.get(field) or "")

    def test_the_watch_adds_the_real_line_and_counts_the_refused_ones(self):
        lines = watch(
            ("555", "آكو", "كاشي آكو", "IRO3AKOZ0001"),               # real
            ("666", HOSTILE, "x", "IRO3EVIL0001"),                    # symbol
            ("667", "خوب", "<img src=x onerror=1>", "IRO3GOOD0001"),  # name
            ("557", HOSTILE, "صندوق", "IRT1ZARF0001"),                # a fund
        )

        with self.assertLogs("seed_import", level="WARNING") as logs, \
                contextlib.redirect_stdout(io.StringIO()):
            plan = seed_import.reconcile_universe(lines, apply=True,
                                                  verbose=False)

        self.assertEqual(plan["refused"], 2)
        self.assertEqual(plan["unreadable"], 0)
        self.assertEqual([r["ins_code"] for r in plan["added"]], ["555"])
        self.assertEqual(sorted(self.stored()), ["555"])
        self.assertIn("2 main-share lines refused", logs.output[0])
        self.assertNotIn("svg", "\n".join(logs.output))

    def test_the_printed_report_says_how_many_were_refused(self):
        lines = watch(("555", "آكو", "كاشي آكو", "IRO3AKOZ0001"),
                      ("666", HOSTILE, "x", "IRO3EVIL0001"))
        out = io.StringIO()

        with self.assertLogs("seed_import", level="WARNING"), \
                contextlib.redirect_stdout(out):
            seed_import.reconcile_universe(lines, apply=False, verbose=True)

        self.assertIn("1 watch lines were refused", out.getvalue())
        self.assertNotIn("svg", out.getvalue())

    def test_a_clean_watch_refuses_nothing_and_logs_nothing(self):
        lines = watch(("555", "آكو", "كاشي آكو", "IRO3AKOZ0001"))

        with self.assertNoLogs("seed_import", level="WARNING"):
            plan = seed_import.reconcile_universe(lines, apply=False,
                                                  verbose=False)

        self.assertEqual(plan["refused"], 0)


if __name__ == "__main__":
    unittest.main()
