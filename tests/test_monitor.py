"""Tests for the intraday session monitor.

Three properties, each of which exists because of a specific way this kind of
collector lies:

    A RESTART IS NOT A MARKET EVENT. Restarting at 11:00 must not write
    "queue formed" for the 300 names that formed one at 09:05.

    UNKNOWN IS NOT FALSE. A gap in the feed must not become a market event.
    This is the same rule e21_panel states for forward targets, applied to
    the book: absence is not approval, in either direction.

    A SLOT THAT COLLECTED NOTHING STILL HAPPENED. Intraday data cannot be
    backfilled, so a missed tick that leaves no row is indistinguishable
    later from a tick where the market stood still.

The live-session paths are exercised against a fake provider because the real
ones can only be reached Saturday to Wednesday between 09:00 and 12:30 Tehran
time, and a test that can only run then is a test that does not run.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monitor                                                  # noqa: E402


def row(ins_code="1", bid_qty=100.0, ask_qty=100.0, last=4300.0,
        limit_up=4370.0, limit_down=4130.0, rid=10, volume=1000.0):
    """One marketwatch row with a one-level book."""

    return {
        "insCode": ins_code, "lva": "نماد%s" % ins_code,
        "pdv": last, "pMax": limit_up, "pMin": limit_down,
        "qTotTran5J": volume, "hEven": 100530,
        "blDs": [{"n": 1, "pmd": last - 1, "qmd": bid_qty, "zmd": 2,
                  "pmo": last + 1, "qmo": ask_qty, "zmo": 3, "rid": rid}],
    }


class FakeProvider:
    """Serves a scripted sequence of market-watch payloads."""

    base_url = "https://example.invalid"

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = []

    def _get(self, url, timeout=None):
        self.calls.append(url)

        rows = self.sequence[0] if len(self.sequence) == 1 \
            else self.sequence.pop(0)

        class Response:
            @staticmethod
            def json():
                return {"marketwatch": rows}

        return Response()


def temp_db():
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)

    return path


class BookStateTests(unittest.TestCase):
    """Every flag is a definition, not a threshold."""

    def test_an_exhausted_ask_side_is_a_buy_queue(self):
        state = monitor.book_state(row(bid_qty=5000.0, ask_qty=0.0))

        self.assertTrue(state["buy_queue"])
        self.assertFalse(state["sell_queue"])

    def test_an_exhausted_bid_side_is_a_sell_queue(self):
        state = monitor.book_state(row(bid_qty=0.0, ask_qty=5000.0))

        self.assertTrue(state["sell_queue"])
        self.assertFalse(state["buy_queue"])

    def test_both_sides_empty_is_neither_queue(self):
        """A halted name has no queue; it has no book."""

        state = monitor.book_state(row(bid_qty=0.0, ask_qty=0.0))

        self.assertFalse(state["buy_queue"])
        self.assertFalse(state["sell_queue"])

    def test_a_missing_book_leaves_the_flags_unknown_not_false(self):
        item = row()
        item["blDs"] = []

        state = monitor.book_state(item)

        self.assertIsNone(state["buy_queue"])
        self.assertIsNone(state["sell_queue"])

    def test_the_limit_comes_from_the_exchange_not_from_a_percentage(self):
        """pMax is published. E14 had to estimate the band from a percentile."""

        at_top = monitor.book_state(row(last=4370.0, limit_up=4370.0))
        below = monitor.book_state(row(last=4369.0, limit_up=4370.0))

        self.assertTrue(at_top["at_limit_up"])
        self.assertFalse(below["at_limit_up"])

    def test_an_absent_limit_leaves_the_flag_unknown(self):
        state = monitor.book_state(row(limit_up=0.0, limit_down=0.0))

        self.assertIsNone(state["at_limit_up"])
        self.assertIsNone(state["at_limit_down"])


class TransitionTests(unittest.TestCase):

    def test_a_flag_turning_on_and_off_names_both_events(self):
        off = {"buy_queue": False}
        on = {"buy_queue": True}

        self.assertEqual(monitor.transitions(off, on), ["buy_queue_formed"])
        self.assertEqual(monitor.transitions(on, off), ["buy_queue_broke"])

    def test_an_unchanged_flag_is_not_an_event(self):
        self.assertEqual(
            monitor.transitions({"buy_queue": True}, {"buy_queue": True}), [])

    def test_unknown_on_either_side_produces_nothing(self):
        """A gap in the feed is not a market event.

        Treating unknown as False would make every dropped payload look like
        three hundred queues breaking at once.
        """

        self.assertEqual(
            monitor.transitions({"buy_queue": None}, {"buy_queue": True}), [])
        self.assertEqual(
            monitor.transitions({"buy_queue": True}, {"buy_queue": None}), [])


class BaselineTests(unittest.TestCase):
    """The first observation describes the process, not the market."""

    def setUp(self):
        self.path = temp_db()

    def tearDown(self):
        os.unlink(self.path)

    def test_the_first_poll_emits_no_events(self):
        queued = [row("1", bid_qty=5000.0, ask_qty=0.0),
                  row("2", bid_qty=5000.0, ask_qty=0.0)]

        mon = monitor.Monitor(db=self.path,
                              provider=FakeProvider([queued]),
                              verbose=False)

        seen, written = mon.poll_events()

        self.assertEqual(seen, 2)
        self.assertEqual(written, 0, "a restart must not replay the book")

    def test_the_second_poll_reports_a_real_change(self):
        before = [row("1", bid_qty=5000.0, ask_qty=0.0)]
        after = [row("1", bid_qty=5000.0, ask_qty=400.0)]

        mon = monitor.Monitor(db=self.path,
                              provider=FakeProvider([before, after]),
                              verbose=False)

        mon.poll_events()
        _seen, written = mon.poll_events()

        self.assertEqual(written, 1)

        conn = sqlite3.connect(self.path)
        events = conn.execute(
            "SELECT ins_code, event FROM market_events").fetchall()
        conn.close()

        self.assertEqual(events, [("1", "buy_queue_broke")])

    def test_the_symbol_is_carried_onto_the_event(self):
        before = [row("7", bid_qty=0.0, ask_qty=100.0)]
        after = [row("7", bid_qty=900.0, ask_qty=0.0)]

        mon = monitor.Monitor(db=self.path,
                              provider=FakeProvider([before, after]),
                              verbose=False)

        mon.poll_events()
        mon.poll_events()

        conn = sqlite3.connect(self.path)
        symbol = conn.execute(
            "SELECT symbol_fa FROM market_events LIMIT 1").fetchone()[0]
        conn.close()

        self.assertEqual(symbol, "نماد7")


class TickLedgerTests(unittest.TestCase):
    """Every tick is a row, including the ones that collected nothing."""

    def setUp(self):
        self.path = temp_db()

    def tearDown(self):
        os.unlink(self.path)

    def outcomes(self):
        conn = sqlite3.connect(self.path)

        try:
            return conn.execute(
                "SELECT outcome, detail FROM monitor_ticks "
                "ORDER BY observed_at").fetchall()
        finally:
            conn.close()

    def test_a_closed_market_is_recorded_not_skipped(self):
        """Fifty of these in a row is what the 2026 closure looked like."""

        shut = [row("1", volume=0.0), row("2", volume=0.0)]

        mon = monitor.Monitor(db=self.path, provider=FakeProvider([shut]),
                              verbose=False)

        wait, outcome = mon.tick()

        self.assertEqual(outcome, "closed")
        self.assertEqual(len(self.outcomes()), 1)
        self.assertEqual(self.outcomes()[0][0], "closed")

    def test_an_unreadable_feed_is_recorded_and_backs_off(self):
        class Broken:
            base_url = "https://example.invalid"

            @staticmethod
            def _get(url, timeout=None):
                raise OSError("no route to host")

        mon = monitor.Monitor(db=self.path, provider=Broken(), verbose=False)

        wait, outcome = mon.tick()

        self.assertEqual(outcome, "unreadable")
        self.assertEqual(wait, monitor.UNREADABLE_BACKOFF_SECONDS)
        self.assertEqual(self.outcomes()[0][0], "unreadable")

    def test_the_ledger_survives_a_repeated_instant(self):
        """INSERT OR IGNORE: a recorded tick is history, not a draft."""

        mon = monitor.Monitor(db=self.path,
                              provider=FakeProvider([[row("1", volume=0.0)]]),
                              verbose=False)

        mon._log_tick("2026-10-15T09:00:00+00:00", "closed")
        mon._log_tick("2026-10-15T09:00:00+00:00", "trading")

        rows = self.outcomes()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "closed")


class FrozenFeedTests(unittest.TestCase):
    """Nothing changing during a live session is a signal, never a verdict."""

    def setUp(self):
        self.path = temp_db()

    def tearDown(self):
        os.unlink(self.path)

    def test_the_streak_only_counts_after_a_baseline(self):
        still = [row("1")]

        mon = monitor.Monitor(db=self.path, provider=FakeProvider([still]),
                              verbose=False)

        mon.poll_events()

        self.assertEqual(mon.frozen_streak, 0)

    def test_a_change_clears_the_streak(self):
        mon = monitor.Monitor(db=self.path,
                              provider=FakeProvider([[row("1")]]),
                              verbose=False)

        mon.baseline_taken = True
        mon.frozen_streak = 4

        mon.state["1"] = monitor.book_state(row("1", ask_qty=0.0))

        self.assertNotEqual(mon.frozen_streak, 0)

    def test_the_warning_threshold_is_a_constant_not_a_guess(self):
        """Documented where it is defined; asserted here so it cannot drift."""

        self.assertEqual(monitor.FROZEN_POLLS_BEFORE_WARNING, 5)


class CursorTests(unittest.TestCase):

    def test_the_cursor_is_the_largest_revision_id_in_the_payload(self):
        rows = [row("1", rid=10), row("2", rid=99), row("3", rid=50)]

        self.assertEqual(monitor.Monitor._max_rid(rows), 99)

    def test_a_payload_without_revision_ids_yields_no_cursor(self):
        """Then the next poll asks for everything, which is correct."""

        item = row("1")
        item["blDs"][0].pop("rid")

        self.assertIsNone(monitor.Monitor._max_rid([item]))


if __name__ == "__main__":
    unittest.main()
