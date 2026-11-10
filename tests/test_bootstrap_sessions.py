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

    def history_state(self, at, index_has_today):
        with frozen(at), \
                mock.patch.object(bootstrap, "_index_has_today",
                                  return_value=index_has_today), \
                mock.patch.object(bootstrap.database, "instrument_counts",
                                  return_value={"with_history": 818, "equities": 818,
                                                "total": 1394}), \
                mock.patch.object(bootstrap.database, "newest_equity_bar",
                                  return_value=datetime(2026, 11, 9)):
            return bootstrap._history_state()

    def test_bars_are_behind_this_evening_once_the_index_shows_the_session(self):
        self.assertTrue(self.history_state(TUESDAY, index_has_today=True)["behind"])

    def test_a_holiday_costs_no_bar_fetch(self):
        """No index bar today: no session, so 818 symbol requests are spared."""
        self.assertFalse(self.history_state(TUESDAY, index_has_today=False)["behind"])

    def test_during_the_session_nothing_is_behind(self):
        self.assertFalse(self.history_state(TUESDAY_MORNING, index_has_today=True)["behind"])


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


if __name__ == "__main__":
    unittest.main()
