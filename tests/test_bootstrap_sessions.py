"""bootstrap: the session that closed this afternoon is fetched this evening.

Until 2026-11-10 sessions_behind() excluded today outright, so the 19:00 run
only ever caught up to yesterday and the forward prediction log wrote each
session after the next one had traded -- inadmissible by its own rule.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap  # noqa: E402

TUESDAY = datetime(2026, 11, 10, 19, 0)          # a trading day, evening
TUESDAY_MORNING = datetime(2026, 11, 10, 10, 0)  # session still open
THURSDAY = datetime(2026, 11, 12, 19, 0)         # weekend


def frozen(at):
    """bootstrap.datetime with now() pinned to the naive local `at`."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return at.replace(tzinfo=timezone.utc).astimezone(tz)
            return at

    return mock.patch.object(bootstrap, "datetime", Clock)


class SessionsBehindTests(unittest.TestCase):

    def test_yesterday_held_and_today_closed_is_one_behind(self):
        with frozen(TUESDAY):
            self.assertEqual(bootstrap.sessions_behind(date(2026, 11, 9),
                                                       include_today=True), 1)

    def test_today_is_not_counted_unless_the_caller_says_it_happened(self):
        with frozen(TUESDAY):
            self.assertEqual(bootstrap.sessions_behind(date(2026, 11, 9)), 0)

    def test_holding_today_is_never_behind(self):
        with frozen(TUESDAY):
            self.assertEqual(bootstrap.sessions_behind(date(2026, 11, 10),
                                                       include_today=True), 0)

    def test_older_gaps_still_count_trading_days_only(self):
        # Sat 11-07 held; Sun, Mon missing; Tue (today) closed.
        with frozen(TUESDAY):
            self.assertEqual(bootstrap.sessions_behind(date(2026, 11, 7),
                                                       include_today=True), 3)


class ReadyTests(unittest.TestCase):

    def test_ready_after_the_figures_are_final_on_a_trading_day(self):
        self.assertTrue(bootstrap._ready_today(TUESDAY))
        self.assertFalse(bootstrap._ready_today(TUESDAY_MORNING))
        self.assertFalse(bootstrap._ready_today(THURSDAY))


class ProbeTests(unittest.TestCase):

    def history_state(self, at, index_has_today, coverage=(None, 0, 0)):
        with frozen(at), \
                mock.patch.object(bootstrap, "_index_has_today",
                                  return_value=index_has_today), \
                mock.patch.object(bootstrap.database, "instrument_counts",
                                  return_value={"with_history": 818, "equities": 818,
                                                "total": 1394}), \
                mock.patch.object(bootstrap.database, "newest_equity_bar",
                                  return_value=datetime(2026, 11, 9)), \
                mock.patch.object(bootstrap, "_history_session_coverage",
                                  return_value=coverage):
            return bootstrap._history_state()

    def test_bars_are_behind_this_evening_once_the_index_shows_the_session(self):
        self.assertTrue(self.history_state(TUESDAY, index_has_today=True)["behind"])

    def test_a_holiday_costs_no_bar_fetch(self):
        """No index bar today: no session, so 818 symbol requests are spared."""
        self.assertFalse(self.history_state(TUESDAY, index_has_today=False)["behind"])

    def test_during_the_session_nothing_is_behind(self):
        self.assertFalse(self.history_state(TUESDAY_MORNING, index_has_today=True)["behind"])


class HistoryCoverageTests(unittest.TestCase):
    """One name's newest bar does not vouch for the other six hundred."""

    history_state = ProbeTests.history_state

    def test_a_thinly_covered_session_is_behind(self):
        """The session's bars stored for 154 of 656 names: not current."""
        state = self.history_state(TUESDAY_MORNING, index_has_today=False,
                                   coverage=("2026-11-09", 656, 154))
        self.assertTrue(state["behind"])
        self.assertIn("154/656", state["detail"])

    def test_a_fully_covered_session_is_current(self):
        state = self.history_state(TUESDAY_MORNING, index_has_today=False,
                                   coverage=("2026-11-09", 656, 655))
        self.assertFalse(state["behind"])

    def test_coverage_is_read_from_the_bars(self):
        """Of the names that traded the session before, which hold the newest."""

        import tempfile

        import pandas as pd

        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        saved = bootstrap.database.DATABASE_NAME
        bootstrap.database.DATABASE_NAME = path
        try:
            database = bootstrap.database
            database.create_database()
            conn = database._connect()
            conn.executemany(
                "INSERT INTO instruments (ins_code, symbol_fa, is_equity) "
                "VALUES (?, ?, ?)",
                [("1", "a", 1), ("2", "b", 1), ("3", "c", 1), ("4", "d", 1),
                 ("9", "fund", 0)])
            conn.commit()
            conn.close()
            tedpix = bootstrap.config.INDICES["TEDPIX"]["insCode"]
            database.save_index_history(tedpix, pd.DataFrame({
                "date": pd.to_datetime(["2026-11-08", "2026-11-09"]),
                "close": 1.0, "low": 1.0, "high": 1.0}))

            def bar(code, day, volume):
                database.save_history(code, pd.DataFrame({
                    "date": pd.to_datetime([day]), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": volume}))

            for code in ("1", "2", "3"):          # traded the session before
                bar(code, "2026-11-08", 100.0)
            bar("4", "2026-11-08", 0.0)           # halted: not counted
            bar("9", "2026-11-08", 100.0)         # not an equity
            bar("1", "2026-11-09", 100.0)         # only one fetch landed
            bar("9", "2026-11-09", 100.0)

            self.assertEqual(bootstrap._history_session_coverage(),
                             ("2026-11-09", 3, 1))
        finally:
            bootstrap.database.DATABASE_NAME = saved
            os.unlink(path)


class FlowCoverageTests(unittest.TestCase):
    """One name's newest flow row does not vouch for the other five hundred."""

    def flow_state(self, traded, flowed):
        coverage = {"equities": 818, "with_flow": 780, "last_date": "2026-11-10"}
        with frozen(TUESDAY), \
                mock.patch.object(bootstrap, "_index_has_today", return_value=True), \
                mock.patch.object(bootstrap.database, "client_type_coverage",
                                  return_value=coverage), \
                mock.patch.object(bootstrap, "_flow_session_coverage",
                                  return_value=("2026-11-10", traded, flowed)):
            return bootstrap._flow_state()

    def test_a_thinly_covered_session_is_behind(self):
        state = self.flow_state(652, 154)
        self.assertTrue(state["behind"])
        self.assertIn("154/652", state["detail"])

    def test_a_fully_covered_session_is_current(self):
        self.assertFalse(self.flow_state(652, 640)["behind"])

    def test_a_session_missing_sixty_traded_names_is_behind(self):
        """11-10: 592 of 652 -- through a 90% gate, after 100% since June."""
        self.assertTrue(self.flow_state(652, 592)["behind"])


class SectorStateTests(unittest.TestCase):
    """A listing without a sector is behind, whatever the coverage.

    Under 90% was the only trigger, and then the step re-asked all ~900
    equities: the 98 names added on 2026-11-11 would have cost ~900
    requests, and a company or two listed in a month never got a sector.
    """

    def state(self, with_sector, equities=899):
        coverage = {"equities": equities, "with_sector": with_sector,
                    "sectors": 40}
        with mock.patch.object(bootstrap.database, "sector_coverage",
                               return_value=coverage):
            return bootstrap._sectors_state()

    def test_one_new_listing_without_a_sector_is_behind(self):
        state = self.state(898)
        self.assertTrue(state["present"])
        self.assertTrue(state["behind"])
        self.assertIn("1 without one", state["detail"])

    def test_full_coverage_is_current(self):
        self.assertFalse(self.state(899)["behind"])

    def test_the_step_asks_only_the_names_without_one(self):
        import seed_import

        with mock.patch.object(seed_import, "import_sectors",
                               return_value=818) as call:
            self.assertTrue(bootstrap._fetch_sectors(lambda *a: None))

        self.assertTrue(call.call_args.kwargs.get("missing_only"))

    def test_the_month_still_paces_it(self):
        """Behind, but asked within MAX_AGE: not fetched again."""

        state = {"present": True, "detail": "", "updated_at": None,
                 "behind": True}
        now = TUESDAY.replace(tzinfo=timezone.utc)
        with mock.patch.object(bootstrap, "STEPS",
                               [("sectors", lambda: state, ())]), \
                mock.patch.object(bootstrap, "_now", return_value=now), \
                mock.patch.object(bootstrap, "_last_fetch",
                                  return_value=now - timedelta(days=3)):
            self.assertFalse(bootstrap._probe_all()["sectors"]["needs_fetch"])


class AttemptTests(unittest.TestCase):
    """A fetch from before today's close does not excuse the evening run."""

    def probe(self, fetched_local):
        state = {"present": True, "detail": "", "updated_at": None, "behind": True}
        fetched = fetched_local.replace(tzinfo=timezone.utc)
        with frozen(TUESDAY), \
                mock.patch.object(bootstrap, "STEPS",
                                  [("indices", lambda: state, ())]), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=fetched), \
                mock.patch.object(bootstrap, "_now",
                                  return_value=TUESDAY.replace(tzinfo=timezone.utc)), \
                mock.patch.object(bootstrap, "_ready_moment",
                                  return_value=datetime(2026, 11, 10, 14, 0,
                                                        tzinfo=timezone.utc)):
            return bootstrap._probe_all()["indices"]["needs_fetch"]

    def test_a_morning_fetch_is_not_recent_after_the_close(self):
        self.assertTrue(self.probe(TUESDAY_MORNING))

    def test_an_evening_fetch_is_recent(self):
        self.assertFalse(self.probe(TUESDAY - timedelta(minutes=30)))


class EnsureTests(unittest.TestCase):

    def test_the_index_landing_puts_history_into_the_same_run(self):
        """Read before the index was fetched, history looked current; the run
        must look again once today's index bar is stored."""

        stored = {"index_today": False}
        fetched = []

        def indices_state():
            return {"present": True, "detail": "", "updated_at": None,
                    "behind": not stored["index_today"]}

        def history_state():
            return {"present": True, "detail": "", "updated_at": None,
                    "behind": stored["index_today"]}

        def fetch_indices(_on):
            stored["index_today"] = True
            fetched.append("indices")
            return True

        def fetch_history(_on):
            fetched.append("history")
            return True

        steps = [("indices", indices_state, ()), ("history", history_state, ())]
        with mock.patch.object(bootstrap, "STEPS", steps), \
                mock.patch.dict(bootstrap.FETCHERS, {"indices": fetch_indices,
                                                     "history": fetch_history}), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=None), \
                mock.patch.object(bootstrap, "_mark_fetched"), \
                mock.patch.object(bootstrap, "_step_detail", return_value=""), \
                mock.patch.object(bootstrap, "_forward_log"):
            bootstrap.ensure()

        self.assertEqual(fetched, ["indices", "history"])

    def test_new_bars_override_a_recent_flow_attempt(self):
        """Flow tried at 21:33 against yesterday's bars stays owed once
        today's bars land at 23:31 -- the earlier attempt does not excuse it."""

        stored = {"bars_today": False}
        fetched = []

        def history_state():
            return {"present": True, "detail": "", "updated_at": None,
                    "behind": not stored["bars_today"]}

        def flow_state():
            return {"present": True, "detail": "", "updated_at": None,
                    "behind": stored["bars_today"]}

        def fetch_history(_on):
            stored["bars_today"] = True
            fetched.append("history")
            return True

        def fetch_flow(_on):
            fetched.append("flow")
            return True

        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        steps = [("history", history_state, ()), ("flow", flow_state, ())]
        with mock.patch.object(bootstrap, "STEPS", steps), \
                mock.patch.dict(bootstrap.FETCHERS, {"history": fetch_history,
                                                     "flow": fetch_flow}), \
                mock.patch.object(bootstrap, "_last_fetch",
                                  side_effect=lambda name: None if name == "history" else recent), \
                mock.patch.object(bootstrap, "_mark_fetched"), \
                mock.patch.object(bootstrap, "_step_detail", return_value=""), \
                mock.patch.object(bootstrap, "_forward_log"):
            bootstrap.ensure()

        self.assertEqual(fetched, ["history", "flow"])


if __name__ == "__main__":
    unittest.main()


class DeferredSessionRetryTests(unittest.TestCase):
    """A deferred session is retried in minutes, not after MAX_AGE (2026-11-11)."""

    def test_a_deferred_history_marks_the_session_components_deferred(self):
        from unittest import mock
        import bootstrap
        calls = []
        with mock.patch.object(bootstrap.database, "set_provenance",
                               side_effect=lambda scope, status, detail=None: calls.append((scope, status))), \
                mock.patch.dict(bootstrap._DEFERRED, {"history": True}):
            bootstrap._mark_fetched("history")
            bootstrap._mark_fetched("flow")
            bootstrap._mark_fetched("codal")
        self.assertEqual(calls, [("bootstrap:history", "deferred"),
                                 ("bootstrap:flow", "deferred"),
                                 ("bootstrap:codal", "fetched")])
        self.assertLess(bootstrap.DEFERRED_RETRY, bootstrap.MAX_AGE["history"])


class ScheduledRetryTests(unittest.TestCase):
    """The full test, 2026-11-12: the 45-minute retry ran only when the panel
    was open, so a deferred evening lost its session."""

    def setUp(self):
        self.clock = [datetime(2026, 11, 10, 19, 10)]
        self.slept = []

    def now(self):
        return self.clock[0]

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.clock[0] += timedelta(seconds=seconds)

    def run_retry(self, still_deferred):
        calls = []

        def ensure(only=None):
            calls.append(only)
            bootstrap._DEFERRED["history"] = still_deferred.pop(0)
            return {"history": {"action": "fetched", "detail": "x"}}

        bootstrap._DEFERRED["history"] = True
        with mock.patch.object(bootstrap, "ensure", side_effect=ensure), \
                mock.patch("builtins.print"):
            made = bootstrap.retry_deferred(now=self.now, sleep=self.sleep)
        bootstrap._DEFERRED.clear()
        return made, calls

    def test_a_deferred_session_is_retried_until_it_lands(self):
        made, calls = self.run_retry([False])
        self.assertEqual(made, 1)
        self.assertEqual(calls, [bootstrap.SESSION_COMPONENTS])
        # The wait is taken in short slices against the wall clock.
        self.assertTrue(all(s <= 30 for s in self.slept))
        self.assertEqual(self.clock[0], datetime(2026, 11, 10, 19, 55))

    def test_retries_are_capped(self):
        made, calls = self.run_retry([True, True, True, True])
        self.assertEqual(made, bootstrap.SCHEDULED_RETRIES)

    def test_no_retry_past_the_cut_off(self):
        self.clock[0] = datetime(2026, 11, 10, 21, 0)
        made, calls = self.run_retry([True])
        self.assertEqual((made, calls), (0, []))

    def test_nothing_deferred_means_no_wait(self):
        bootstrap._DEFERRED.clear()
        self.assertEqual(bootstrap.retry_deferred(now=self.now, sleep=self.sleep), 0)
        self.assertEqual(self.slept, [])
