"""bootstrap: an index fetch that brought no bar for today is a deferral.

2026-11-23: the panel was opened at 15:46, before the exchange had published
the day's index. The fetch "succeeded", was recorded "fetched", and held the
next attempt off for twelve hours -- so the 19:00 scheduled run printed
"nothing to fetch" under a table that said "1 session(s) behind", and the
session's bars, flow and forward entry were not collected that evening.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap  # noqa: E402

MONDAY_1546 = datetime(2026, 11, 23, 15, 46)     # a trading day, unpublished
MONDAY_1900 = datetime(2026, 11, 23, 19, 0)      # the scheduled run


def frozen(at):
    """bootstrap.datetime with now() pinned to the naive local `at`."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return at.replace(tzinfo=timezone.utc).astimezone(tz)
            return at

    return mock.patch.object(bootstrap, "datetime", Clock)


def state(behind):
    return {"present": True, "detail": "", "updated_at": None,
            "behind": behind}


class TheMarkTests(unittest.TestCase):

    def mark(self, name, behind):
        calls = []
        with mock.patch.object(
                bootstrap.database, "set_provenance",
                side_effect=lambda scope, status, detail=None:
                calls.append((scope, status))), \
                mock.patch.object(bootstrap, "_indices_state",
                                  return_value=state(behind)), \
                mock.patch.dict(bootstrap._DEFERRED, clear=True):
            bootstrap._mark_fetched(name)
        return calls

    def test_an_index_still_behind_after_its_fetch_is_deferred(self):
        self.assertEqual(self.mark("indices", behind=True),
                         [("bootstrap:indices", "deferred")])

    def test_an_index_that_caught_up_is_fetched(self):
        self.assertEqual(self.mark("indices", behind=False),
                         [("bootstrap:indices", "fetched")])

    def test_only_the_index_is_judged_by_its_own_probe(self):
        """Bars and flow still behind would mean ~1,800 requests a retry on
        a holiday; their fetchers decide their own deferral."""
        with mock.patch.object(bootstrap, "_history_state") as history, \
                mock.patch.object(bootstrap, "_flow_state") as flow:
            self.assertEqual(self.mark("history", behind=True),
                             [("bootstrap:history", "fetched")])
            self.assertEqual(self.mark("flow", behind=True),
                             [("bootstrap:flow", "fetched")])
        history.assert_not_called()
        flow.assert_not_called()

    def test_a_probe_that_raises_does_not_take_the_run_down(self):
        with mock.patch.object(bootstrap.database, "set_provenance") as put, \
                mock.patch.object(bootstrap, "_indices_state",
                                  side_effect=RuntimeError("locked")), \
                mock.patch.dict(bootstrap._DEFERRED, clear=True):
            bootstrap._mark_fetched("indices")
        self.assertEqual(put.call_args[0][1], "fetched")


class TheHoldTests(unittest.TestCase):
    """What the 19:00 run reads after a 15:46 attempt."""

    def probe(self, deferred, minutes_ago):
        tried = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        with mock.patch.object(bootstrap, "STEPS",
                               [("indices", lambda: state(True), ())]), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=tried), \
                mock.patch.object(bootstrap, "_last_fetch_deferred",
                                  return_value=deferred), \
                mock.patch.object(bootstrap, "_ready_today", return_value=False), \
                mock.patch.dict(bootstrap._last_failure, clear=True):
            return bootstrap._probe_all()["indices"], tried

    def test_a_deferred_index_is_asked_again_that_evening(self):
        report, _tried = self.probe(deferred=True, minutes_ago=194)
        self.assertTrue(report["needs_fetch"])
        self.assertIsNone(report["next_try"])

    def test_the_old_mark_held_it_for_twelve_hours(self):
        """What happened on 2026-11-23, kept as the contrast."""
        report, tried = self.probe(deferred=False, minutes_ago=194)
        self.assertFalse(report["needs_fetch"])
        self.assertTrue(report["behind"])
        self.assertEqual(report["next_try"], (
            tried + bootstrap.MAX_AGE["indices"]).isoformat(timespec="seconds"))

    def test_a_deferred_index_is_not_asked_again_within_the_gap(self):
        """Bounded: never two attempts closer than its deferred retry (15
        minutes for the index since 2026-11-24: two requests a try)."""
        report, tried = self.probe(deferred=True, minutes_ago=10)
        self.assertFalse(report["needs_fetch"])
        self.assertEqual(report["next_try"], (
            tried + bootstrap._deferred_retry("indices")
        ).isoformat(timespec="seconds"))
        self.assertEqual(bootstrap._deferred_retry("indices"),
                         timedelta(minutes=15))

    def test_the_report_says_which_components_are_per_session(self):
        report, _tried = self.probe(deferred=True, minutes_ago=10)
        self.assertTrue(report["session"])
        with mock.patch.object(bootstrap, "STEPS",
                               [("codal", lambda: state(True), ())]), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=None):
            self.assertFalse(bootstrap._probe_all()["codal"]["session"])

    def test_a_current_index_has_no_next_try_and_no_fetch(self):
        """It stops once the component is complete."""
        with mock.patch.object(bootstrap, "STEPS",
                               [("indices", lambda: state(False), ())]), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=None):
            report = bootstrap._probe_all()["indices"]
        self.assertFalse(report["needs_fetch"])
        self.assertIsNone(report["next_try"])


class TheWaitTests(unittest.TestCase):
    """The scheduled run waits for an unpublished index, but not on a
    holiday evening."""

    def fetch(self, at, has_today, ok=True):
        import seed_import
        with frozen(at), \
                mock.patch.object(seed_import, "import_indices",
                                  return_value=2 if ok else 0), \
                mock.patch.object(bootstrap, "_index_has_today",
                                  return_value=has_today), \
                mock.patch.object(bootstrap, "_note"), \
                mock.patch.dict(bootstrap._DEFERRED, clear=True):
            result = bootstrap._fetch_indices(lambda *a: None)
            return result, bootstrap._DEFERRED.get("indices")

    def test_an_afternoon_fetch_without_todays_bar_waits(self):
        self.assertEqual(self.fetch(MONDAY_1546, has_today=False), (True, True))

    def test_an_evening_without_todays_bar_is_taken_for_a_holiday(self):
        self.assertEqual(self.fetch(MONDAY_1900, has_today=False), (True, False))

    def test_todays_bar_ends_the_wait(self):
        self.assertEqual(self.fetch(MONDAY_1546, has_today=True), (True, False))

    def test_a_failed_fetch_is_a_failure_not_a_deferral(self):
        self.assertEqual(self.fetch(MONDAY_1546, has_today=False, ok=False),
                         (False, False))

    def test_no_wait_before_the_days_figures_are_due(self):
        morning = datetime(2026, 11, 23, 10, 0)
        self.assertEqual(self.fetch(morning, has_today=False), (True, False))

    def test_the_retry_loop_runs_for_a_waiting_index(self):
        clock = [MONDAY_1546]
        left = [False]
        calls = []

        def ensure(only=None):
            calls.append(only)
            bootstrap._DEFERRED["indices"] = left.pop(0)
            return {"indices": {"action": "fetched", "detail": "x"}}

        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)

        with mock.patch.dict(bootstrap._DEFERRED, {"indices": True}, clear=True), \
                mock.patch.object(bootstrap, "ensure", side_effect=ensure), \
                mock.patch("builtins.print"):
            made = bootstrap.retry_deferred(now=lambda: clock[0], sleep=sleep)
        self.assertEqual((made, calls), (1, [bootstrap.SESSION_COMPONENTS]))
        self.assertEqual(clock[0], MONDAY_1546 + bootstrap.DEFERRED_RETRY)


class DataVersionTests(unittest.TestCase):
    """finished_at() is the panel's cache key: it must not move when a fetch
    STARTS, only when it has delivered."""

    def test_the_key_holds_through_a_run_and_moves_at_its_end(self):
        seen = {}

        def ensure(force=False, only=None):
            seen["key"] = bootstrap.finished_at()
            seen["progress"] = bootstrap._state["finished_at"]
            return {}

        saved = dict(bootstrap._state)
        try:
            bootstrap._state["running"] = False
            bootstrap._state["finished_at"] = "2026-11-23T12:16:49+00:00"
            with mock.patch.object(bootstrap, "ensure", side_effect=ensure):
                bootstrap.run()
            after = bootstrap.finished_at()
        finally:
            bootstrap._state.clear()
            bootstrap._state.update(saved)

        self.assertEqual(seen["key"], "2026-11-23T12:16:49+00:00")
        # progress() and the command line still read "in flight".
        self.assertIsNone(seen["progress"])
        self.assertNotEqual(after, "2026-11-23T12:16:49+00:00")

    def test_a_first_run_has_no_earlier_key(self):
        saved = dict(bootstrap._state)
        try:
            bootstrap._state["finished_at"] = None
            bootstrap._state.pop("last_finished_at", None)
            self.assertIsNone(bootstrap.finished_at())
        finally:
            bootstrap._state.clear()
            bootstrap._state.update(saved)


if __name__ == "__main__":
    unittest.main()
