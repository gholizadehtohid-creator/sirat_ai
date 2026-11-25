"""bootstrap: what reaches the panel, and how soon (2026-11-24).

The owner: "the panel is sometimes a day or two behind, and I have to wait at
least half an hour for it to show current data". What these pin:

  - the cache key moves when a STEP stores changed data, not when the whole
    run (flow, Codal) ends -- and not at all for a retry that found nothing;
  - a deferred index or bar update is asked again after 15 minutes, flow
    after 45;
  - the morning's market watch is replaced once after the close;
  - the panel and the evening task never fetch at once (the run lock);
  - the keeper asks by itself, through the same gates, while the panel runs;
  - the panel's own runs leave Codal to the evening task.
"""

import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap  # noqa: E402


def state(behind=False, detail="d", updated=None, present=True):
    return {"present": present, "detail": detail, "updated_at": updated,
            "behind": behind}


class Saved(unittest.TestCase):
    """Run with bootstrap's module state restored and no lock left over."""

    def setUp(self):
        self.saved = {k: (dict(v) if isinstance(v, dict) else
                          list(v) if isinstance(v, list) else v)
                      for k, v in bootstrap._state.items()}
        for path in (bootstrap.RUN_LOCK_PATH, bootstrap.STAMP_PATH):
            if os.path.exists(path):
                os.remove(path)

    def tearDown(self):
        bootstrap._release_run_lock()
        bootstrap._state.clear()
        bootstrap._state.update(self.saved)
        for path in (bootstrap.RUN_LOCK_PATH, bootstrap.STAMP_PATH):
            if os.path.exists(path):
                os.remove(path)


class TheSuiteIsolationTests(unittest.TestCase):

    def test_the_lock_and_stamp_are_not_in_the_live_tree(self):
        live = os.path.normcase(os.path.join(bootstrap.ROOT, "data")) + os.sep
        for path in (bootstrap.RUN_LOCK_PATH, bootstrap.STAMP_PATH):
            self.assertFalse(os.path.normcase(path).startswith(live), path)


class PacingTests(unittest.TestCase):

    def test_the_index_and_bars_are_asked_again_after_fifteen_minutes(self):
        self.assertEqual(bootstrap._deferred_retry("indices"),
                         timedelta(minutes=15))
        self.assertEqual(bootstrap._deferred_retry("history"),
                         timedelta(minutes=15))

    def test_flow_keeps_forty_five_minutes(self):
        """A partial flow session is re-asked name by name."""
        self.assertEqual(bootstrap._deferred_retry("flow"),
                         bootstrap.DEFERRED_RETRY)
        self.assertEqual(bootstrap.DEFERRED_RETRY, timedelta(minutes=45))

    def test_a_deferred_history_update_is_due_after_fifteen_minutes(self):
        tried = datetime.now(timezone.utc) - timedelta(minutes=16)
        with mock.patch.object(bootstrap, "STEPS",
                               [("history", lambda: state(True), ())]), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=tried), \
                mock.patch.object(bootstrap, "_last_fetch_deferred",
                                  return_value=True), \
                mock.patch.object(bootstrap, "_ready_today", return_value=False), \
                mock.patch.dict(bootstrap._last_failure, clear=True):
            self.assertTrue(bootstrap._probe_all()["history"]["needs_fetch"])


class SnapshotAfterTheCloseTests(unittest.TestCase):

    def setUp(self):
        # The clock is this afternoon, whatever hour the suite runs at. On
        # the real clock a "14:05" snapshot passed the six-hour snapshot
        # limit at 20:05, and the test failed in every evening run (found
        # 2026-11-25).
        self.now = datetime.now().astimezone().replace(
            hour=15, minute=0, second=0, microsecond=0)

    def at(self, hour, minute):
        return self.now.replace(hour=hour, minute=minute)

    def snapshot(self, captured, ready=True):
        moment = self.at(14, 0)
        with mock.patch.object(bootstrap.database, "snapshot_info",
                               return_value={"rows": 900,
                                             "captured_at": captured.isoformat()}), \
                mock.patch.object(bootstrap, "_ready_today", return_value=ready), \
                mock.patch.object(bootstrap, "_ready_moment", return_value=moment), \
                mock.patch.object(bootstrap, "_now",
                                  return_value=self.now.astimezone(timezone.utc)):
            return bootstrap._snapshot_state(), moment

    def test_the_mornings_market_watch_is_behind_after_the_close(self):
        got, _ = self.snapshot(self.at(11, 25))
        self.assertTrue(got["behind"])
        self.assertIn("before today's close", got["detail"])

    def test_one_taken_after_the_close_is_current(self):
        got, _ = self.snapshot(self.at(14, 5))
        self.assertFalse(got["behind"])

    def test_a_snapshot_past_the_age_limit_is_behind_all_the_same(self):
        got, _ = self.snapshot(self.now - bootstrap.MAX_AGE["snapshot"]
                               - timedelta(minutes=1), ready=False)
        self.assertTrue(got["behind"])

    def test_not_on_a_day_without_a_session(self):
        got, _ = self.snapshot(self.now - timedelta(hours=1), ready=False)
        self.assertFalse(got["behind"])

    def test_a_recent_attempt_before_the_close_does_not_hold_it(self):
        tried = datetime.now(timezone.utc) - timedelta(hours=1)
        ready = datetime.now(timezone.utc) - timedelta(minutes=5)
        with mock.patch.object(bootstrap, "STEPS",
                               [("snapshot", lambda: state(True), ())]), \
                mock.patch.object(bootstrap, "_last_fetch", return_value=tried), \
                mock.patch.object(bootstrap, "_last_fetch_deferred",
                                  return_value=False), \
                mock.patch.object(bootstrap, "_ready_today", return_value=True), \
                mock.patch.object(bootstrap, "_ready_moment", return_value=ready), \
                mock.patch.dict(bootstrap._last_failure, clear=True):
            self.assertTrue(bootstrap._probe_all()["snapshot"]["needs_fetch"])


class StampTests(Saved):
    """The cache key moves per delivered step, and only on a change."""

    def run_steps(self, before, after):
        """ensure() over one 'indices' step whose probe goes before -> after.

        `before` is what status() reported; the probe answers `after`."""
        probes = iter([after] * 4)
        report = {"indices": {"present": True, "detail": before["detail"],
                              "updated_at": bootstrap._iso(before["updated_at"]),
                              "needs_fetch": True, "retry_in_seconds": None,
                              "behind": True}}
        with mock.patch.object(bootstrap, "STEPS",
                               [("indices", lambda: next(probes), ())]), \
                mock.patch.object(bootstrap, "status", return_value=report), \
                mock.patch.dict(bootstrap.FETCHERS,
                                {"indices": lambda on_item: True}), \
                mock.patch.object(bootstrap, "_mark_fetched"), \
                mock.patch.object(bootstrap, "DOWNSTREAM", {}), \
                mock.patch.object(bootstrap, "_forward_log"), \
                mock.patch.object(bootstrap, "_outlook_inputs"), \
                mock.patch.object(bootstrap, "_outlook_forward_log"), \
                mock.patch.object(bootstrap, "_note"):
            return bootstrap.ensure(only=("indices",))

    def test_a_step_that_stored_a_new_session_moves_the_key(self):
        key = bootstrap.data_stamp()
        old = datetime(2026, 11, 23, tzinfo=timezone.utc)
        new = datetime(2026, 11, 24, tzinfo=timezone.utc)
        result = self.run_steps(state(True, "through 11-23", old),
                                state(False, "through 11-24", new))
        self.assertEqual(result["indices"]["action"], "fetched")
        self.assertNotEqual(bootstrap.data_stamp(), key)
        self.assertTrue(os.path.exists(bootstrap.STAMP_PATH))

    def test_a_retry_that_found_nothing_leaves_every_cached_scan(self):
        key = bootstrap.data_stamp()
        old = datetime(2026, 11, 23, tzinfo=timezone.utc)
        self.run_steps(state(True, "through 11-23", old),
                       state(True, "through 11-23", old))
        self.assertEqual(bootstrap.data_stamp(), key)

    def test_another_processs_delivery_moves_the_key_too(self):
        """The evening task stores; the panel's key reads the shared file."""
        key = bootstrap.data_stamp()
        with open(bootstrap.STAMP_PATH, "w") as handle:
            handle.write("from the evening task\n")
        later = time.time() + 5
        os.utime(bootstrap.STAMP_PATH, (later, later))
        self.assertNotEqual(bootstrap.data_stamp(), key)

    def test_the_lock_is_released_after_the_run(self):
        old = datetime(2026, 11, 23, tzinfo=timezone.utc)
        self.run_steps(state(True, "a", old), state(True, "a", old))
        self.assertFalse(os.path.exists(bootstrap.RUN_LOCK_PATH))


class RunLockTests(Saved):

    def foreign(self, age_seconds=0, pid=None):
        with open(bootstrap.RUN_LOCK_PATH, "w", encoding="utf-8") as handle:
            json.dump({"pid": pid or os.getpid() + 1,
                       "started_at": "2026-11-24T15:30:00+00:00",
                       "by": "bootstrap.py"}, handle)
        then = time.time() - age_seconds
        os.utime(bootstrap.RUN_LOCK_PATH, (then, then))

    def test_another_processs_fresh_lock_is_seen(self):
        self.foreign(age_seconds=30)
        self.assertEqual(bootstrap._lock_holder()["by"], "bootstrap.py")

    def test_a_lock_nobody_touches_is_a_dead_holders(self):
        self.foreign(age_seconds=bootstrap.LOCK_FRESH.total_seconds() + 60)
        self.assertIsNone(bootstrap._lock_holder())
        self.assertIsNone(bootstrap._acquire_run_lock())    # taken over
        self.assertEqual(bootstrap._lock_holder(), None)    # ours now
        with open(bootstrap.RUN_LOCK_PATH, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["pid"], os.getpid())

    def test_our_own_lock_is_not_another_process(self):
        self.foreign(pid=os.getpid())
        self.assertIsNone(bootstrap._lock_holder())

    def test_a_run_leaves_the_fetch_to_the_process_fetching(self):
        self.foreign(age_seconds=10)
        fetched = []
        with mock.patch.dict(bootstrap.FETCHERS,
                             {n: (lambda on_item, n=n: fetched.append(n))
                              for n in bootstrap.FETCHERS}), \
                mock.patch.object(bootstrap, "status") as probe, \
                mock.patch.object(bootstrap, "_note"):
            self.assertEqual(bootstrap.ensure(), {})
        self.assertEqual(fetched, [])
        probe.assert_not_called()

    def test_the_evening_task_waits_for_the_panels_fetch(self):
        self.foreign(age_seconds=10)
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            os.remove(bootstrap.RUN_LOCK_PATH)          # the panel finished

        with mock.patch("builtins.print"):
            self.assertTrue(bootstrap.wait_for_other_run(sleep=sleep))
        self.assertEqual(len(waits), 1)

    def test_no_wait_when_nobody_fetches(self):
        self.assertFalse(bootstrap.wait_for_other_run(sleep=self.fail))


class KeeperTests(Saved):

    def report(self, **needs):
        return {name: {"needs_fetch": needs.get(name, False)}
                for name, _p, _r in bootstrap.STEPS}

    def keep(self, report, running=False):
        started = []
        with mock.patch.object(bootstrap, "status", return_value=report), \
                mock.patch.object(bootstrap, "is_running", return_value=running), \
                mock.patch.object(bootstrap, "start_background",
                                  side_effect=lambda only=None, force=False:
                                  started.append(list(only)) or True), \
                mock.patch("today_provisional.refresh_if_due",
                           return_value=None) as provisional:
            done = bootstrap.keep_fresh_once()
        return done, started, provisional

    def test_it_fetches_the_session_components_that_need_it(self):
        done, started, provisional = self.keep(self.report(
            indices=True, flow=True, codal=True, sectors=True))
        self.assertEqual(started, [["indices", "flow"]])
        self.assertEqual(done["started"], ["indices", "flow"])
        provisional.assert_called_once()

    def test_nothing_due_is_nothing_fetched(self):
        _done, started, _p = self.keep(self.report())
        self.assertEqual(started, [])

    def test_it_never_starts_a_second_run(self):
        _done, started, provisional = self.keep(
            self.report(indices=True), running=True)
        self.assertEqual(started, [])
        provisional.assert_called_once()   # the provisional read still runs

    def test_the_keeper_runs_and_stops(self):
        with mock.patch.object(bootstrap, "keep_fresh_once",
                               return_value={}) as once:
            self.assertTrue(bootstrap.start_keeper(interval=3600,
                                                   first_delay=0))
            self.assertFalse(bootstrap.start_keeper())       # one at a time
            deadline = time.time() + 5
            while not once.called and time.time() < deadline:
                time.sleep(0.05)
            bootstrap.stop_keeper()
            bootstrap._keeper["thread"].join(5)
        self.assertTrue(once.called)
        self.assertFalse(bootstrap.keeper_state()["running"])


class TheAutomaticRunTests(unittest.TestCase):

    def test_the_panel_leaves_codal_to_the_evening_task(self):
        self.assertNotIn("codal", bootstrap.AUTOMATIC)
        self.assertEqual(set(bootstrap.AUTOMATIC) | {"codal"},
                         {name for name, _p, _r in bootstrap.STEPS})

    def test_the_panel_starts_its_keeper_and_skips_codal(self):
        import inspect

        import app

        source = inspect.getsource(app.main)
        self.assertIn("bootstrap.start_keeper()", source)
        self.assertIn("only=bootstrap.AUTOMATIC", source)

    def test_the_cache_key_is_the_data_stamp(self):
        import inspect

        import app

        self.assertIn("bootstrap.data_stamp()", inspect.getsource(app.data_version))


if __name__ == "__main__":
    unittest.main()
