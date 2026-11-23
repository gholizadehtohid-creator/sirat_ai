"""health.py: what ran, what did not, from facts already stored.

Every test builds its own logs, stores and clocks: nothing here reads the
live tree, today's date or the network.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import health  # noqa: E402

NOW = datetime(2026, 11, 23, 21, 0)              # a Monday evening
EVENING = health.TASKS[0]
SESSION = health.TASKS[1]
WATCH = health.TASKS[2]

FINISHED = """===== Mon 11/23/2026 19:00:02.02 =====
news: 21 sources ok, 2 failed, 367 new articles
step news: exit 0
step bootstrap: exit 0
step backup: exit 0
===== done Mon 11/23/2026 20:42:10.11: 0 step(s) failed =====
"""

THROTTLED = """===== Mon 11/23/2026 19:00:02.02 =====
step bootstrap: exit 0
monthly: throttled; a 30m cool-off would pass the time limit -- stopping
step codal-monthly: exit 3
step codal-portfolio: exit 3
step dividends: exit 0
===== done Mon 11/23/2026 20:42:10.11: 2 step(s) failed =====
"""

KILLED = """===== Sun 11/22/2026 19:13:49.39 =====
step bootstrap: exit 0
monthly: 120 companies due
monthly: throttled; waiting 15m
"""


class ParseRunTests(unittest.TestCase):

    def test_a_finished_run(self):
        run = health.parse_run(FINISHED)
        self.assertEqual(run["start"], datetime(2026, 11, 23, 19, 0, 2))
        self.assertEqual(run["done"], datetime(2026, 11, 23, 20, 42, 10))
        self.assertEqual(run["failed_steps"], [])

    def test_the_failed_steps_of_the_newest_run_only(self):
        older = THROTTLED.replace("11/23", "11/22")
        run = health.parse_run(older + FINISHED)
        self.assertEqual(run["failed_steps"], [])
        self.assertEqual(health.parse_run(THROTTLED)["failed_steps"],
                         ["codal-monthly", "codal-portfolio"])

    def test_a_done_line_belongs_to_the_run_before_a_newer_start(self):
        run = health.parse_run(FINISHED + KILLED.replace("11/22", "11/24"))
        self.assertIsNone(run["done"])
        self.assertEqual(run["start"].day, 24)

    def test_a_morning_hour_is_space_padded(self):
        run = health.parse_run("===== Mon 11/02/2026  7:44:32.58 =====\n")
        self.assertEqual(run["start"], datetime(2026, 11, 2, 7, 44, 32))

    def test_bootstraps_banner_is_not_a_marker(self):
        self.assertIsNone(health.parse_run("=" * 74 + "\n")["start"])

    def test_the_done_number(self):
        self.assertEqual(health._done_code("2 step(s) failed"), 2)
        self.assertEqual(health._done_code("codal-watch exit 0"), 0)
        self.assertEqual(health._done_code("monitor exit 3221225786"),
                         3221225786)


class TaskItemTests(unittest.TestCase):

    def item(self, spec, text, now=NOW, mtime=None, heartbeat=None):
        return health.task_item(spec, text, mtime or now, now,
                                heartbeat=heartbeat)

    def test_a_finished_evening_is_ok(self):
        item = self.item(EVENING, FINISHED)
        self.assertEqual((item["key"], item["level"]), ("task:evening", "ok"))

    def test_failed_steps_warn_and_are_named(self):
        item = self.item(EVENING, THROTTLED)
        self.assertEqual(item["level"], "warn")
        self.assertIn("codal-monthly", item["text"])
        self.assertIn("2", item["text"])

    def test_a_run_with_no_done_line_past_its_window_was_killed(self):
        item = self.item(EVENING, KILLED, mtime=datetime(2026, 11, 22, 20, 39))
        self.assertEqual(item["level"], "bad")
        self.assertIn("SIRAT-evening", item["action"])

    def test_a_run_inside_its_window_is_running(self):
        now = datetime(2026, 11, 22, 19, 40)
        item = self.item(EVENING, KILLED, now=now,
                         mtime=now - timedelta(minutes=10))
        self.assertEqual(item["level"], "ok")

    def test_a_log_quiet_for_too_long_is_a_dead_run_even_inside_the_window(self):
        now = datetime(2026, 11, 22, 22, 30)
        item = self.item(EVENING, KILLED, now=now,
                         mtime=datetime(2026, 11, 22, 20, 39))
        self.assertEqual(item["level"], "bad")

    def test_a_killed_watch_poll_only_warns(self):
        text = "===== Mon 11/23/2026 17:52:11.64 =====\nwatch: throttled; waiting 60m\n"
        self.assertEqual(self.item(WATCH, text)["level"], "warn")

    def test_a_task_that_has_not_started_for_a_day_warns_then_is_bad(self):
        text = ("===== Sun 11/22/2026 19:52:11.70 =====\n"
                "===== done Sun 11/22/2026 19:55:03.36: codal-watch exit 0 =====\n")
        self.assertEqual(self.item(WATCH, text, now=datetime(2026, 11, 22, 21, 0))["level"], "ok")
        self.assertEqual(self.item(WATCH, text, now=datetime(2026, 11, 23, 3, 0))["level"], "warn")
        self.assertEqual(self.item(WATCH, text, now=datetime(2026, 11, 24, 3, 0))["level"], "bad")

    def test_a_non_zero_exit_warns(self):
        text = ("===== Mon 11/23/2026 19:52:11.70 =====\n"
                "===== done Mon 11/23/2026 19:55:03.36: codal-watch exit 1 =====\n")
        self.assertEqual(self.item(WATCH, text)["level"], "warn")

    def test_an_empty_log_is_unknown_not_an_alarm(self):
        self.assertEqual(self.item(EVENING, "")["level"], "unknown")

    def test_a_session_run_ends_at_the_monitors_hard_stop(self):
        text = "===== Mon 11/23/2026 12:13:00.53 =====\ncapture\n"
        running = self.item(SESSION, text, now=datetime(2026, 11, 23, 13, 0))
        self.assertEqual(running["level"], "ok")
        over = self.item(SESSION, text, now=datetime(2026, 11, 23, 18, 30))
        self.assertEqual(over["level"], "bad")

    def test_a_silent_monitor_during_the_session_is_dead(self):
        """12:13 on 2026-11-23: started, captured once, killed 20 s later."""
        text = "===== Mon 11/23/2026  9:00:00.53 =====\ncapture\n"
        now = datetime(2026, 11, 23, 11, 0)
        alive = self.item(SESSION, text, now=now,
                          heartbeat=now - timedelta(minutes=1))
        dead = self.item(SESSION, text, now=now,
                         heartbeat=now - timedelta(minutes=40))
        self.assertEqual((alive["level"], dead["level"]), ("ok", "bad"))


class SessionStartTests(unittest.TestCase):

    def test_an_open_market_with_no_monitor_today_is_bad(self):
        text = "===== Sun 11/22/2026  8:40:01.00 =====\n"
        item = health.session_start_item(text, datetime(2026, 11, 23, 10, 0))
        self.assertEqual(item["level"], "bad")

    def test_a_monitor_started_today_or_a_shut_market_says_nothing(self):
        today = "===== Mon 11/23/2026  8:40:01.00 =====\n"
        self.assertIsNone(health.session_start_item(
            today, datetime(2026, 11, 23, 10, 0)))
        self.assertIsNone(health.session_start_item(
            "", datetime(2026, 11, 23, 19, 0)))
        self.assertIsNone(health.session_start_item(
            "", datetime(2026, 11, 26, 10, 0)))          # a Thursday


class SessionsItemTests(unittest.TestCase):
    DAYS = ["2026-11-23", "2026-11-22", "2026-11-21", "2026-11-18",
            "2026-11-17", "2026-11-16", "2026-11-15", "2026-11-14",
            "2026-11-11", "2026-11-10"]

    def test_the_newest_session_without_a_capture_is_bad(self):
        item = health.sessions_item(self.DAYS, {"2026-11-11": 22,
                                                "2026-11-21": 1})
        self.assertEqual(item["level"], "bad")
        self.assertIn("8", item["text"])                 # eight with none

    def test_old_misses_are_counted_but_do_not_alarm(self):
        captures = {d: 20 for d in self.DAYS[:3]}
        item = health.sessions_item(self.DAYS, captures)
        self.assertEqual(item["level"], "ok")
        self.assertIn("7", item["text"])

    def test_a_thin_recent_session_warns(self):
        captures = {d: 20 for d in self.DAYS}
        captures["2026-11-22"] = 1
        self.assertEqual(health.sessions_item(self.DAYS, captures)["level"],
                         "warn")

    def test_all_captured_is_ok_and_no_calendar_is_unknown(self):
        self.assertEqual(health.sessions_item(
            self.DAYS, {d: 12 for d in self.DAYS})["level"], "ok")
        self.assertEqual(health.sessions_item([], {})["level"], "unknown")


class ForwardTests(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sirat-health-")
        self.path = os.path.join(self.folder, "forward.jsonl")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def write(self, entries):
        with open(self.path, "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def test_the_newest_entries_are_read_from_the_tail(self):
        self.write([{"session": "2026-11-%02d" % d, "names": ["x"] * 50}
                    for d in range(1, 21)]
                   + [{"session": "2026-11-21", "missed": "superseded"}])
        records = health.forward_records(self.path, limit=4000)
        self.assertEqual(records[-1], {"session": "2026-11-21",
                                       "missed": "superseded"})
        # The tail began inside an entry; that half line is not an entry.
        self.assertTrue(all(r["session"] > "2026-11-05" for r in records))
        self.assertLessEqual(len(records), health.SESSIONS)

    def test_a_missing_log_is_unknown(self):
        self.assertEqual(health.forward_records(self.path + ".absent"), [])
        self.assertEqual(health.forward_item([])["level"], "unknown")

    def test_the_newest_entry_missed_is_bad(self):
        records = [{"session": "2026-11-18", "missed": None},
                   {"session": "2026-11-21", "missed": "x"},
                   {"session": "2026-11-22", "missed": "y"}]
        item = health.forward_item(records, "2026-11-22")
        self.assertEqual(item["level"], "bad")
        self.assertIn("2", item["text"])

    def test_a_session_not_yet_written_warns(self):
        records = [{"session": "2026-11-22", "missed": None}]
        item = health.forward_item(records, "2026-11-23")
        self.assertEqual(item["level"], "warn")

    def test_old_misses_are_counted_but_do_not_alarm(self):
        records = ([{"session": "2026-11-10", "missed": "x"}]
                   + [{"session": "2026-11-%02d" % d, "missed": None}
                      for d in (11, 14, 15, 16)])
        item = health.forward_item(records, "2026-11-16")
        self.assertEqual(item["level"], "ok")
        self.assertIn("1", item["text"])


class BackupItemTests(unittest.TestCase):

    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="sirat-health-bk-")

    def tearDown(self):
        shutil.rmtree(self.dest, ignore_errors=True)

    def night(self, name, taken):
        os.makedirs(os.path.join(self.dest, name))
        with open(os.path.join(self.dest, name, "MANIFEST.json"), "w") as handle:
            json.dump({"taken_at": taken, "files": []}, handle)

    def test_no_backup_is_bad(self):
        self.assertEqual(health.backup_item(NOW, self.dest)["level"], "bad")

    def test_last_nights_backup_is_ok_and_an_old_one_is_not(self):
        self.night("2026-11-23", "2026-11-23T19:03:13")
        self.assertEqual(health.backup_item(NOW, self.dest)["level"], "ok")
        self.assertEqual(health.backup_item(
            NOW + timedelta(hours=40), self.dest)["level"], "warn")
        self.assertEqual(health.backup_item(
            NOW + timedelta(days=5), self.dest)["level"], "bad")


class SmallItemTests(unittest.TestCase):

    def test_power(self):
        self.assertEqual(health.power_item(None)["level"], "unknown")
        self.assertEqual(health.power_item(
            {"on_battery": False, "percent": 100})["level"], "ok")
        on = health.power_item({"on_battery": True, "percent": 93})
        self.assertEqual(on["level"], "warn")
        self.assertIn("93", on["text"])
        self.assertEqual(health.power_item(
            {"on_battery": True, "percent": 12})["level"], "bad")

    def test_the_power_reading_never_raises(self):
        with mock.patch.object(health.os, "name", "posix"):
            self.assertIsNone(health.power_status())

    def status(self, **over):
        base = {"present": True, "behind": False, "needs_fetch": False}
        report = {name: dict(base) for name in
                  ("universe", "snapshot", "indices", "history", "sectors",
                   "flow", "codal")}
        for name, change in over.items():
            report[name].update(change)
        return report

    def test_data_behind(self):
        self.assertEqual(health.data_item(self.status())["level"], "ok")
        # Sectors and codal are "behind" while coverage grows: no alarm.
        self.assertEqual(health.data_item(self.status(
            codal={"behind": True}, sectors={"behind": True}))["level"], "ok")
        held = health.data_item(self.status(indices={"behind": True}))
        self.assertEqual(held["level"], "warn")
        due = health.data_item(self.status(
            flow={"behind": True, "needs_fetch": True}))
        self.assertEqual(due["level"], "warn")
        self.assertNotEqual(held["action"], due["action"])
        self.assertEqual(health.data_item(self.status(
            history={"present": False}))["level"], "bad")

    def test_the_watchs_notes(self):
        self.assertEqual(health.watch_item({}, NOW)["level"], "ok")
        stalled = health.watch_item(
            {"stall": {"polls": 4, "since": "x", "reason": "429"}}, NOW)
        self.assertEqual(stalled["level"], "bad")
        dead = [{"at": "2026-11-23T14:22:15+00:00"},
                {"at": "2026-10-01T10:00:00+00:00"}, {"at": None}]
        recent = health.watch_item({"dead": dead}, NOW)
        self.assertEqual(recent["level"], "warn")
        self.assertIn("1", recent["text"])
        self.assertEqual(health.watch_item({"dead": dead[1:]}, NOW)["level"],
                         "ok")

    def test_the_history_backlog(self):
        self.assertEqual(health.history_item(0, 0, False)["level"], "ok")
        # The phase never got its turn (every evening to 2026-11-23).
        self.assertEqual(health.history_item(4016, 629, False)["level"], "warn")
        self.assertEqual(health.history_item(4016, 629, (0, 0))["level"], "warn")
        moving = health.history_item(3716, 610, (300, 287))
        self.assertEqual(moving["level"], "ok")
        self.assertIn("3716", moving["text"])
        self.assertIn("300", moving["text"])
        # No finished run in the log's tail: the number, and no alarm.
        self.assertEqual(health.history_item(4016, 629, None)["level"], "ok")

    def test_the_history_phase_is_read_off_the_newest_finished_run(self):
        ran = FINISHED.replace(
            "step backup: exit 0",
            "monthly history: 4016 company-months known and missing in 629 "
            "companies; this run takes at most 300\n"
            "monthly history: 300 downloaded, 287 stored\nstep backup: exit 0")
        self.assertEqual(health.history_progress(ran), (300, 287))
        self.assertIs(health.history_progress(FINISHED), False)
        self.assertIs(health.history_progress(THROTTLED), False)
        # A run still going says nothing yet; the one before it does.
        self.assertIsNone(health.history_progress(KILLED))
        self.assertEqual(health.history_progress(
            ran + KILLED.replace("11/22", "11/24")), (300, 287))


class ReportTests(unittest.TestCase):

    def test_the_tail_is_all_that_is_read(self):
        folder = tempfile.mkdtemp(prefix="sirat-health-")
        try:
            path = os.path.join(folder, "task.log")
            with open(path, "wb") as handle:
                handle.write(b"x" * 5000 + b"\nthe end\n")
            text, mtime = health._tail(path, limit=100)
            self.assertEqual(len(text), 100)
            self.assertTrue(text.endswith("the end\n"))
            self.assertIsNotNone(mtime)
            self.assertEqual(health._tail(path + ".absent"), ("", None))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_every_probe_failing_is_still_an_answer(self):
        boom = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.multiple(
                health, market_facts=boom, tasks=boom, power_status=boom,
                forward_records=boom, backup_item=boom, history_facts=boom,
                data_item=boom, watch_item=boom, sessions_item=boom), \
                mock.patch.dict(health._slow, clear=True):
            result = health.build(NOW)
        self.assertEqual(result["level"], "ok")
        self.assertTrue(result["items"])
        self.assertTrue(all(i["level"] == "unknown" for i in result["items"]))
        json.dumps(result)

    def test_the_worst_item_is_the_reports_level(self):
        def one(level):
            return [health._item("x", level, "t", "a")]

        with mock.patch.object(health, "_guard", side_effect=[
                [], one("ok"), one("warn"), one("bad"), one("unknown"),
                [], [], [], []]):
            result = health.build(NOW)
        self.assertEqual((result["level"], result["attention"]), ("bad", 2))

    def test_the_report_is_cached_and_a_broken_build_is_still_an_answer(self):
        saved = dict(health._cache)
        try:
            health._cache.update({"at": None, "value": None})
            with mock.patch.object(health, "build",
                                   return_value={"level": "ok"}) as build:
                health.report(fresh=True)
                health.report()
                self.assertEqual(build.call_count, 1)
            with mock.patch.object(health, "build",
                                   side_effect=RuntimeError("boom")):
                result = health.report(fresh=True)
            self.assertEqual(result["items"], [])
            self.assertEqual(result["level"], "ok")
        finally:
            health._cache.clear()
            health._cache.update(saved)


class RouteTests(unittest.TestCase):
    """GET /api/health: the report as it is, and never a 500."""

    def get(self, url="/api/health"):
        import app
        return app.app.test_client().get(url)

    def test_the_route_answers_with_the_report(self):
        answer = {"level": "warn", "attention": 1, "summary": "s",
                  "items": [{"key": "power", "level": "warn", "text": "t",
                             "action": "a"}]}
        with mock.patch.object(health, "report", return_value=answer) as report:
            response = self.get()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), answer)
            report.assert_called_with(fresh=False)
            self.get("/api/health?refresh=1")
            report.assert_called_with(fresh=True)

    def test_a_report_that_raises_is_still_an_answer(self):
        with mock.patch.object(health, "report",
                               side_effect=RuntimeError("boom")):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body["level"], body["items"]), ("ok", []))

    def test_the_page_has_the_card_and_the_script_fills_it(self):
        """The three shared files each hold their part of the card."""

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def text(*parts):
            with open(os.path.join(root, *parts), encoding="utf-8") as handle:
                return handle.read()

        page = text("templates", "dashboard.html")
        script = text("static", "js", "panel.js")
        styles = text("static", "css", "panel.css")
        for ident in ("healthCard", "healthSummary", "healthList"):
            self.assertIn('id="%s"' % ident, page)
            self.assertIn('$("%s")' % ident, script)
        self.assertIn('getJSON("/api/health")', script)
        self.assertIn(".health-card", styles)
        # The strip no longer calls a waiting session "current".
        self.assertIn("s.session && s.behind && !s.needs_fetch", script)


if __name__ == "__main__":
    unittest.main()
