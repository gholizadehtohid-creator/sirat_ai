"""Tests for the did-the-market-trade detector.

The property under test is one sentence: a published bar must never be
mistaken for a session that happened. That is not a hypothetical. TSETMC
published a full bar for every listed equity on each of the fifty sessions
between 2026-02-28 and 2026-05-18, while the exchange traded nothing, and
every freshness check in this project read those bars and reported the data
current.

So the fixtures below are built the way the feed actually behaves on a closed
day -- rows present, prices carried forward, volume zero -- rather than the
way a closed day is usually imagined, with rows absent.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import market_session                                           # noqa: E402


def build(dates, names=3, dead=(), flow_dates=None):
    """A throwaway DB shaped like the real one.

    `dead` dates get a bar for every name at zero volume -- the closed-market
    shape. Flow rows are written only for dates in `flow_dates`, defaulting to
    the dates that actually traded, because the exchange publishes
    حقیقی/حقوقی from a session's completed trades.
    """

    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)

    conn = sqlite3.connect(path)

    conn.execute("""CREATE TABLE instruments
                    (ins_code TEXT PRIMARY KEY, is_equity INTEGER)""")
    conn.execute("""CREATE TABLE price_history
                    (ins_code TEXT, date TEXT, volume REAL)""")
    conn.execute("""CREATE TABLE client_type_history
                    (ins_code TEXT, date TEXT)""")

    codes = ["c%d" % i for i in range(names)]

    for code in codes:
        conn.execute("INSERT INTO instruments VALUES (?, 1)", (code,))

    if flow_dates is None:
        flow_dates = [d for d in dates if d not in dead]

    for date in dates:
        for code in codes:
            volume = 0.0 if date in dead else 1000.0
            conn.execute("INSERT INTO price_history VALUES (?, ?, ?)",
                         (code, date, volume))

        if date in flow_dates:
            for code in codes:
                conn.execute("INSERT INTO client_type_history VALUES (?, ?)",
                             (code, date))

    conn.commit()
    conn.close()

    return path


class ClosedMarketTests(unittest.TestCase):
    """A bar is not evidence that a session happened."""

    def setUp(self):
        self.path = build(
            dates=["2026-02-26", "2026-02-27", "2026-02-28",
                   "2026-03-01", "2026-03-02", "2026-03-03"],
            dead=("2026-02-28", "2026-03-01", "2026-03-02"))

    def tearDown(self):
        os.unlink(self.path)

    def test_dead_dates_carry_bars_and_are_still_dead(self):
        """The failure mode: rows present, volume zero, nothing traded."""

        counts = dict((d, (listed, traded))
                      for d, listed, traded
                      in market_session.traded_counts(db=self.path))

        self.assertEqual(counts["2026-02-28"], (3, 0),
                         "a closed session still publishes a full market")

        self.assertEqual(
            market_session.dead_dates(db=self.path),
            {"2026-02-28", "2026-03-01", "2026-03-02"})

    def test_live_calendar_excludes_them(self):
        """What a research panel must build its calendar from."""

        self.assertEqual(
            market_session.live_calendar(db=self.path),
            ["2026-02-26", "2026-02-27", "2026-03-03"])

    def test_closures_collapse_into_runs(self):
        """A holiday and a shutdown differ only by length, so report length."""

        self.assertEqual(market_session.closures(db=self.path),
                         [("2026-02-28", "2026-03-02", 3)])

    def test_last_live_session_skips_the_frozen_tail(self):
        """The question every staleness check should have been asking."""

        self.assertEqual(market_session.last_live_session(db=self.path),
                         "2026-03-03")

    def test_flow_is_silent_on_closed_dates(self):
        """The corroborating witness, and the only table that told the truth."""

        self.assertEqual(market_session.flow_dates(db=self.path),
                         {"2026-02-26", "2026-02-27", "2026-03-03"})


class BoundaryTests(unittest.TestCase):

    def test_a_single_traded_name_is_a_session(self):
        """The 2026-05-19 reopening traded 402 of 751. Nothing traded 1..401.

        The floor sits where it separates nothing from something. A threshold
        tuned between those -- "a real session needs 100 names" -- would be a
        free parameter chosen against a boundary this market has never shown.
        """

        path = build(dates=["2026-05-18", "2026-05-19"], names=3,
                     dead=("2026-05-18",))

        conn = sqlite3.connect(path)
        conn.execute("UPDATE price_history SET volume = 0 "
                     "WHERE date = '2026-05-19' AND ins_code != 'c0'")
        conn.commit()
        conn.close()

        try:
            self.assertEqual(market_session.live_calendar(db=path),
                             ["2026-05-19"])
        finally:
            os.unlink(path)

    def test_no_bars_at_all_is_not_a_closure(self):
        """An empty database has no evidence either way, and says so."""

        path = build(dates=[], names=0)

        try:
            self.assertEqual(market_session.traded_counts(db=path), [])
            self.assertEqual(market_session.closures(db=path), [])
            self.assertIsNone(market_session.last_live_session(db=path))
        finally:
            os.unlink(path)

    def test_non_equities_do_not_vouch_for_a_session(self):
        """The same mistake database.newest_equity_bar() was fixed for.

        Gold funds carry bars on their own schedule. One fund trading does not
        make it a session for the 818 equities every scanner reads.
        """

        path = build(dates=["2026-03-02"], names=2, dead=("2026-03-02",))

        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO instruments VALUES ('gold1', 0)")
        conn.execute("INSERT INTO price_history VALUES "
                     "('gold1', '2026-03-02', 50000.0)")
        conn.commit()
        conn.close()

        try:
            self.assertEqual(market_session.dead_dates(db=path),
                             {"2026-03-02"})
        finally:
            os.unlink(path)


# 10:30 Tehran on Wednesday 2026-10-28, a session that traded.
SESSION_NOW = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

# One row of GetMarketWatch exactly as the live feed served it on Friday
# 2026-10-30, trimmed of the order book. The two facts that matter: qtj holds
# Wednesday's volume two days later, and qTotTran5J is 0.0 even though the name
# traded a million shares.
RECORDED_ROW = {
    "lva": "سكرما", "insCode": "15472396110662150", "qtj": 1001465.0,
    "pdv": 97420.0, "pMax": 97420.0, "pMin": 91760, "dEven": 0,
    "hEven": 122706, "qTotTran5J": 0.0, "zTotTran": 0.0,
}

# GetMarketOverview/1 from the same call.
RECORDED_OVERVIEW = {
    "marketActivityDEven": 20261028, "marketActivityHEven": 180327,
    "marketState": "F", "marketStateTitle": "بسته",
}


def overview(date=20261028, state="S"):
    return {"marketActivityDEven": date, "marketState": state}


class LiveStateTests(unittest.TestCase):
    """is_trading_now() reads the tape, and has three answers rather than two."""

    class FakeProvider:
        base_url = "https://example.invalid"

        def __init__(self, rows=None, error=None, market=None,
                     overview_error=None):
            self.rows = rows
            self.error = error
            self.market = market if market is not None else overview()
            self.overview_error = overview_error

        def _get(self, url, timeout=None):
            if "GetMarketOverview" in url:
                if self.overview_error:
                    raise self.overview_error

                payload = {"marketOverview": self.market}
            else:
                if self.error:
                    raise self.error

                payload = {"marketwatch": self.rows}

            class Response:
                @staticmethod
                def json():
                    return payload

            return Response()

    def ask(self, rows, **kwargs):
        return market_session.is_trading_now(
            provider=self.FakeProvider(rows, **kwargs), now=SESSION_NOW)

    def test_turnover_on_the_tape_means_trading(self):
        rows = [{"qtj": 1000, "hEven": 100530},
                {"qtj": 0, "hEven": 0}]

        state = self.ask(rows)

        self.assertTrue(state["trading"])
        self.assertEqual(state["traded"], 1)
        self.assertEqual(state["last_trade"], 100530)
        self.assertEqual(state["session_date"], 20261028)

    def test_state_n_after_the_session_is_not_trading(self):
        """Seen 14:30-15:04 on 2026-10-31: the day's date, turnover, "N"."""

        rows = [{"qtj": 1000, "hEven": 122959}]

        state = market_session.is_trading_now(
            provider=self.FakeProvider(rows, market=overview(state="N")),
            now=SESSION_NOW)

        self.assertFalse(state["trading"])

    def test_the_recorded_friday_payload_is_closed(self):
        """The real feed, two days after the session it still describes.

        Before this test existed the detector read qTotTran5J, which is 0.0
        on every row, and called every live session closed. Reading qtj
        alone would have called this Friday open instead.
        """

        friday = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)

        state = market_session.is_trading_now(
            provider=self.FakeProvider([RECORDED_ROW],
                                       market=RECORDED_OVERVIEW),
            now=friday)

        self.assertFalse(state["trading"])
        self.assertEqual(state["traded"], 1)

    def test_qtj_counts_even_when_qtottran5j_reads_zero(self):
        """The field mix of the real feed, on the day it was live."""

        state = self.ask([RECORDED_ROW])

        self.assertTrue(state["trading"])
        self.assertEqual(state["traded"], 1)

    def test_yesterdays_volume_is_not_todays_session(self):
        """A pre-open morning: the tape still shows the last session."""

        state = self.ask([RECORDED_ROW], market=overview(date=20261027))

        self.assertFalse(state["trading"])
        self.assertIn("left over", state["reason"])

    def test_the_exchange_saying_shut_is_believed(self):
        """After the close on a session day: today's date, state F."""

        state = self.ask([RECORDED_ROW], market=overview(state="F"))

        self.assertFalse(state["trading"])

    def test_a_full_market_at_zero_turnover_is_closed(self):
        """What every day of the eleven-week closure looked like live."""

        rows = [{"qtj": 0, "hEven": 172118} for _ in range(900)]

        state = self.ask(rows)

        self.assertFalse(state["trading"])
        self.assertEqual(state["traded"], 0)

    def test_an_unreadable_feed_is_neither_open_nor_closed(self):
        """None, not False.

        A caller that reads an unreachable exchange as closed resumes
        cheerfully when the network returns mid-session; one that reads it as
        open sends orders into a market it cannot see. The third answer is
        the only honest one.
        """

        state = self.ask(None, error=OSError("no route to host"))

        self.assertIsNone(state["trading"])
        self.assertIn("unreadable", state["reason"])
        # Unknown, not "0 symbols trading" (the full test, 2026-11-12).
        self.assertIsNone(state["traded"])

    def test_an_unreadable_overview_is_also_unknown(self):
        """Without a date the tape cannot be placed in time."""

        state = self.ask([RECORDED_ROW],
                         overview_error=OSError("no route to host"))

        self.assertIsNone(state["trading"])
        self.assertIn("overview", state["reason"])
        self.assertIsNone(state["traded"])

    def test_an_empty_payload_is_also_unknown(self):
        self.assertIsNone(self.ask([])["trading"])

    def test_the_other_volume_field_is_a_fallback(self):
        """A row with no qtj at all falls back to qTotTran5J."""

        self.assertTrue(self.ask([{"qTotTran5J": 500, "hEven": 93000}])
                        ["trading"])

    def test_an_unparseable_volume_does_not_crash_the_read(self):
        rows = [{"qtj": "n/a", "hEven": "bad"},
                {"qtj": 10, "hEven": 90000}]

        state = self.ask(rows)

        self.assertTrue(state["trading"])
        self.assertEqual(state["traded"], 1)

    def test_tehran_time_is_utc_plus_three_thirty(self):
        local = market_session.tehran_now(SESSION_NOW)

        self.assertEqual((local.hour, local.minute), (10, 30))


if __name__ == "__main__":
    unittest.main()
