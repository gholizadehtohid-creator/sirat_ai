"""Tests for the incremental price-history update.

The stored series is kept and only what it lacks is fetched. The properties
that matter, each a way an incremental collector quietly corrupts a series:

    A ROW THE WATCH CANNOT DATE IS NOT USED. The market watch has no date, so
    a row is taken only when it traded and its `py` equals the close we hold
    for the session before.

    ONE DISAGREEMENT REJECTS THE WATCH. A sample is re-read from the history
    endpoint; if any name disagrees, every name goes per-symbol.

    A GAP IS FETCHED, NOT PAPERED OVER. A name more than one session behind
    never gets its newest bar from the watch, which would leave a hole.
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config                                                   # noqa: E402
import database                                                 # noqa: E402
import seed_import                                              # noqa: E402
from providers.base import ProviderResult                       # noqa: E402

SESSION = "2026-10-28"
PREVIOUS = "2026-10-27"
TEDPIX = config.INDICES["TEDPIX"]["insCode"]


def bars(dates, close=1000.0, volume=5000.0):
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "open": close, "high": close,
        "low": close, "close": close, "volume": volume,
        "value": volume * close, "trades": 10.0,
    })


class FakeProvider:
    base_url = "https://example.invalid"

    def __init__(self, watch, history, state="F", session=20261028):
        self.watch = watch            # {code: dict of snapshot fields}
        self.history = history        # {code: frame}
        self.state = state
        self.session = session
        self.requests = []            # (codes, count)

    def _get(self, url, timeout=None):
        payload = {"marketOverview": {"marketActivityDEven": self.session,
                                      "marketState": self.state}}

        class Response:
            @staticmethod
            def json():
                return payload

        return Response()

    def get_market_snapshot(self):
        frame = pd.DataFrame([dict(ins_code=c, **row)
                              for c, row in self.watch.items()])
        return ProviderResult.success(frame, "fake")

    def get_histories(self, codes, count=300, progress=None):
        self.requests.append((sorted(codes), count))
        return {c: (ProviderResult.success(self.history[c].tail(count), "fake")
                    if c in self.history
                    else ProviderResult.failure("fake", "none"))
                for c in codes}

    def get_index_history(self, ins_code):
        return ProviderResult.failure("fake", "not needed")


def watch_row(close=1010.0, py=1000.0, volume=7000.0):
    return {"open": 1000.0, "high": 1015.0, "low": 995.0, "close": close,
            "prev_close": py, "volume": volume, "value": volume * close,
            "trades": 12.0}


class IncrementalHistoryTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        conn = database._connect()
        for code in ("1", "2", "3"):
            conn.execute("INSERT INTO instruments (ins_code, symbol_fa, "
                         "is_equity) VALUES (?, ?, 1)", (code, "n" + code))
        conn.commit()
        conn.close()

        database.save_index_history(TEDPIX, pd.DataFrame({
            "date": pd.to_datetime(["2026-10-26", PREVIOUS, SESSION]),
            "close": [1.0, 2.0, 3.0], "low": [1.0, 2.0, 3.0],
            "high": [1.0, 2.0, 3.0]}))

        for code in ("1", "2", "3"):
            database.save_history(code, bars(["2026-10-26", PREVIOUS]))

        self.saved_provider = seed_import.get_provider

    def tearDown(self):
        seed_import.get_provider = self.saved_provider
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def run_with(self, provider):
        seed_import.get_provider = lambda: provider
        return seed_import.update_history(full_count=300, verbose=False)

    def full_history(self, close=1010.0, volume=7000.0):
        frame = bars(["2026-10-26", PREVIOUS, SESSION])
        frame.loc[2, "close"] = close
        frame.loc[2, "volume"] = volume
        return frame

    def newest(self, code):
        return database.newest_bars([code])[code]

    def test_one_session_behind_comes_from_the_watch(self):
        provider = FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: self.full_history() for c in ("1", "2", "3")})

        stats = self.run_with(provider)

        self.assertEqual(stats["watch"], 3)
        self.assertEqual(stats["tail"], 0)
        # Only the verification sample touched the history endpoint.
        self.assertEqual([count for _, count in provider.requests], [3])
        self.assertEqual(self.newest("1"), (SESSION, 1010.0))

    def test_an_ex_date_row_goes_to_the_history_endpoint(self):
        """py re-based by the exchange: the watch row is not used."""

        watch = {c: watch_row() for c in ("1", "2", "3")}
        watch["2"] = watch_row(close=505.0, py=500.0)

        provider = FakeProvider(
            watch=watch,
            history={c: self.full_history() for c in ("1", "2", "3")})

        stats = self.run_with(provider)

        self.assertEqual(stats["watch"], 2)
        self.assertEqual(stats["tail"], 1)

    def test_an_untraded_row_is_not_dated_by_the_watch(self):
        watch = {c: watch_row() for c in ("1", "2", "3")}
        watch["3"] = watch_row(volume=0.0)

        stats = self.run_with(FakeProvider(
            watch=watch,
            history={c: self.full_history() for c in ("1", "2", "3")}))

        self.assertEqual(stats["watch"], 2)
        self.assertEqual(stats["tail"], 1)

    def test_one_disagreement_rejects_the_watch(self):
        history = {c: self.full_history() for c in ("1", "2", "3")}
        history["1"] = self.full_history(volume=9999.0)

        stats = self.run_with(FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history=history))

        self.assertEqual(stats["watch"], 0)
        self.assertEqual(stats["tail"], 3)
        self.assertTrue(any("REJECTED" in n for n in stats["notes"]))

    def test_a_session_still_open_is_fetched_per_symbol(self):
        provider = FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: self.full_history() for c in ("1", "2", "3")},
            state="S")

        stats = self.run_with(provider)

        self.assertEqual(stats["watch"], 0)
        self.assertEqual(stats["tail"], 3)
        self.assertTrue(all(count < 300 for _, count in provider.requests))

    def test_an_unclosed_sessions_bar_is_not_stored(self):
        """A draft bar would make its name read as current forever."""

        import market_session

        today = market_session.tehran_now().strftime("%Y-%m-%d")
        frame = bars(["2026-10-26", PREVIOUS, today])

        provider = FakeProvider(watch={}, history={c: frame
                                                   for c in ("1", "2", "3")},
                                state="S", session=int(today.replace("-", "")))

        self.run_with(provider)

        self.assertEqual(self.newest("1")[0], PREVIOUS)

    def test_a_name_two_sessions_behind_is_not_patched_from_the_watch(self):
        database.save_history("4", bars(["2026-10-26"]))
        conn = database._connect()
        conn.execute("INSERT INTO instruments (ins_code, symbol_fa, "
                     "is_equity) VALUES ('4', 'n4', 1)")
        conn.commit()
        conn.close()

        history = {c: self.full_history() for c in ("1", "2", "3", "4")}
        stats = self.run_with(FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3", "4")},
            history=history))

        self.assertEqual(stats["watch"], 3)
        self.assertEqual(stats["tail"], 1)

    def test_a_current_store_costs_no_history_request(self):
        for code in ("1", "2", "3"):
            database.save_history(code, self.full_history())

        provider = FakeProvider(watch={}, history={})

        stats = self.run_with(provider)

        self.assertEqual(stats["current"], 3)
        self.assertEqual(provider.requests, [])
        self.assertTrue(stats["ok"])

    def test_a_name_with_no_bars_gets_the_full_series(self):
        conn = database._connect()
        conn.execute("INSERT INTO instruments (ins_code, symbol_fa, "
                     "is_equity) VALUES ('9', 'n9', 1)")
        conn.commit()
        conn.close()

        history = {c: self.full_history() for c in ("1", "2", "3", "9")}
        provider = FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")}, history=history)

        stats = self.run_with(provider)

        self.assertEqual(stats["full"], 1)
        self.assertIn((["9"], 300), provider.requests)

    def test_the_stored_series_is_kept(self):
        self.run_with(FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: self.full_history() for c in ("1", "2", "3")}))

        frame = database.load_history_by_inscode("1")

        self.assertEqual(len(frame), 3)

    def test_state_n_is_a_closed_session(self):
        """After 14:30 the overview says "N", not "F" (seen 2026-10-31)."""

        stats = self.run_with(FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: self.full_history() for c in ("1", "2", "3")},
            state="N"))

        self.assertEqual(stats["watch"], 3)

    def test_a_chain_provider_still_reads_the_overview(self):
        """The default provider is a chain, which has no raw _get."""

        inner = FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: self.full_history() for c in ("1", "2", "3")})

        class Chain:
            providers = [inner]
            get_market_snapshot = inner.get_market_snapshot
            get_histories = inner.get_histories
            get_index_history = inner.get_index_history

        stats = self.run_with(Chain())

        self.assertEqual(stats["session"], SESSION)
        self.assertEqual(stats["watch"], 3)

    def test_a_lagging_history_endpoint_defers_instead_of_refetching(self):
        """Right after the close the endpoint still ends a session early."""

        provider = FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: bars(["2026-10-26", PREVIOUS])
                     for c in ("1", "2", "3")})

        stats = self.run_with(provider)

        self.assertEqual(stats["deferred"], 3)
        self.assertEqual((stats["watch"], stats["tail"]), (0, 0))
        self.assertEqual(len(provider.requests), 1)     # the sample only
        self.assertTrue(stats["ok"])
        self.assertEqual(self.newest("1")[0], PREVIOUS)


    def test_an_index_one_session_short_still_dates_the_watch(self):
        """The index lags the close too; its newest date is the one before."""

        conn = database._connect()
        conn.execute("DELETE FROM index_history WHERE date LIKE ?",
                     (SESSION + "%",))
        conn.commit()
        conn.close()

        stats = self.run_with(FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")},
            history={c: self.full_history() for c in ("1", "2", "3")}))

        self.assertEqual(stats["watch"], 3)

    def add_delisted(self, code, newest="2026-09-01"):
        conn = database._connect()
        conn.execute("INSERT INTO instruments (ins_code, symbol_fa, "
                     "is_equity) VALUES (?, ?, 1)", (code, "n" + code))
        conn.commit()
        conn.close()
        database.save_history(code, bars(["2026-08-31", newest]))

    def test_an_unlisted_name_is_read_once_then_left_for_a_week(self):
        """Delisted names were one wasted request each, on every run."""

        from datetime import date, timedelta

        self.add_delisted("7")
        self.add_delisted("8")
        database.mark_history_checked(
            ["8"], (date.today() - timedelta(days=2)).isoformat())

        history = {c: self.full_history() for c in ("1", "2", "3")}
        history["7"] = history["8"] = bars(["2026-08-31", "2026-09-01"])

        saved = seed_import.WATCH_MIN_ROWS
        seed_import.WATCH_MIN_ROWS = 1
        try:
            provider = FakeProvider(
                watch={c: watch_row() for c in ("1", "2", "3")},
                history=history)
            stats = self.run_with(provider)
        finally:
            seed_import.WATCH_MIN_ROWS = saved

        fetched = {c for codes, _ in provider.requests for c in codes}

        self.assertIn("7", fetched)          # never checked: read
        self.assertNotIn("8", fetched)       # checked two days ago: skipped
        self.assertEqual(stats["absent"], 1)
        self.assertTrue(stats["ok"])

        checked = database.history_checked_dates()
        self.assertEqual(checked["7"], date.today().isoformat())
        self.assertNotIn("1", checked)       # listed names are never marked

    def test_a_short_watch_proves_nothing_about_absence(self):
        """A partial answer must not make every missing name 'delisted'."""

        from datetime import date

        self.add_delisted("8")
        database.mark_history_checked(["8"], date.today().isoformat())

        history = {c: self.full_history() for c in ("1", "2", "3")}
        history["8"] = bars(["2026-08-31", "2026-09-01"])

        provider = FakeProvider(
            watch={c: watch_row() for c in ("1", "2", "3")}, history=history)
        self.run_with(provider)                    # 3 rows < WATCH_MIN_ROWS

        fetched = {c for codes, _ in provider.requests for c in codes}
        self.assertIn("8", fetched)


class FlowOwedTests(unittest.TestCase):
    """Flow is fetched only for names that traded since their series ends."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def flow(self, code, dates):
        database.save_client_type_history(code, pd.DataFrame({
            "date": pd.to_datetime(dates),
            "individual_buy_value": 1.0, "institutional_buy_value": 1.0}))

    def test_owed_names(self):
        database.save_history("current", bars([PREVIOUS, SESSION]))
        self.flow("current", [PREVIOUS, SESSION])

        database.save_history("behind", bars([PREVIOUS, SESSION]))
        self.flow("behind", [PREVIOUS])

        database.save_history("never", bars([SESSION]))

        # Its newest bar did not trade, so there is no flow row to have.
        frame = bars([PREVIOUS, SESSION])
        frame.loc[1, "volume"] = 0.0
        database.save_history("halted", frame)
        self.flow("halted", [PREVIOUS])

        # Listed at a price, never dealt: no traded bar, no flow to have.
        frame = bars([SESSION])
        frame["volume"] = 0.0
        database.save_history("untraded", frame)

        owed = database.newest_flow_owed(
            ["current", "behind", "never", "halted", "untraded", "unknown"])

        self.assertEqual(owed, {"behind", "never", "unknown"})

    def test_nothing_owed_costs_no_request_and_is_not_a_failure(self):
        database.save_history("1", bars([SESSION]))
        self.flow("1", [SESSION])

        class NoCalls:
            name = "fake"

            def get_client_type_histories(self, codes, progress=None):
                raise AssertionError("nothing was owed")

        saved = seed_import.get_provider
        seed_import.get_provider = NoCalls
        try:
            result = seed_import.import_client_type(
                ins_codes=["1"], verbose=False, incremental=True)
        finally:
            seed_import.get_provider = saved

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
