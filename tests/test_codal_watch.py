"""codal_watch: page back as far as needed, and never lose a letter."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_watch as w  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402


def letter(n, day, code="ن-10"):
    return {"tracing_no": str(n), "published_at": "1405/08/%02d 10:00:00" % day,
            "title": "t", "excel_url": "u", "symbol": "s", "letter_code": code}


# Newest first, 3 letters a page: 9, 8, 7 | 6, 5, 4 | 3, 2, 1
PAGES = {1: [letter(9, 14), letter(8, 14), letter(7, 13)],
         2: [letter(6, 12), letter(5, 11), letter(4, 10)],
         3: [letter(3, 9), letter(2, 8), letter(1, 7)]}


class CollectTests(unittest.TestCase):

    def run_collect(self, seen, since=None, pages=PAGES):
        calls = []

        def page(_client, n):
            calls.append(n)
            return pages.get(n, [])

        with mock.patch.object(w, "_page", side_effect=page), \
                mock.patch.object(w.time, "sleep"):
            fresh = w.collect(None, seen, since=since)
        return [l["tracing_no"] for l in fresh], calls

    def test_a_poll_stops_at_the_first_page_with_a_known_letter(self):
        fresh, calls = self.run_collect(seen={"7"})
        self.assertEqual(fresh, ["9", "8"])
        self.assertEqual(calls, [1])

    def test_a_poll_pages_back_when_a_whole_page_is_new(self):
        fresh, calls = self.run_collect(seen={"5"})
        self.assertEqual(fresh, ["9", "8", "7", "6", "4"])
        self.assertEqual(calls, [1, 2])

    def test_catch_up_pages_to_the_date_not_to_the_first_known(self):
        fresh, calls = self.run_collect(seen={"8"}, since="1405/08/09")
        self.assertEqual(fresh, ["9", "7", "6", "5", "4", "3"])
        self.assertEqual(calls, [1, 2, 3])

    def test_the_feed_is_unfiltered_so_other_letter_types_are_dropped(self):
        # The market-wide feed carries every letter type. Only the watched
        # codes (codal_watch.FEEDS) are kept, and a seen letter of another type (here an
        # audit-committee notice, ن-41) is not "known ground" for the watch.
        pages = {1: [letter(9, 14, "ن-41"), letter(8, 14, "ن-30"),
                     letter(7, 13, "ن-41")],
                 2: [letter(6, 12), letter(5, 11), letter(4, 10, "ن-41")]}
        fresh, calls = self.run_collect(seen={"7", "5"}, pages=pages)
        self.assertEqual(fresh, ["8", "6"])
        self.assertEqual(calls, [1, 2])


def announcement(n, minute):
    """A watched letter of a kind whose handler is faked here (ن-20)."""

    return {"tracing_no": str(n), "letter_code": "ن-20", "title": "t%d" % n,
            "symbol": "s", "published_at": "1405/08/10 10:%02d:00" % minute}


def pages_of(letters, size=3):
    """{page: letters}, newest first, `size` to a page."""

    return {i // size + 1: letters[i:i + size]
            for i in range(0, len(letters), size)}


class Feed:
    """A market feed that grows at the top between polls."""

    def __init__(self, count):
        self.letters = [announcement(n, n) for n in range(count, 0, -1)]
        self.calls = []
        self.fail = set()

    def publish(self, *numbers):
        self.letters[:0] = [announcement(n, n) for n in sorted(numbers,
                                                               reverse=True)]

    def page(self, _client, number):
        self.calls.append(number)
        if number in self.fail:
            raise ConnectionError("dropped")
        return pages_of(self.letters).get(number, [])


class PollTests(unittest.TestCase):
    """Whole polls against a fake feed, a fake handler and a temp store."""

    def setUp(self):
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        self.previous = fundamental_db.set_path(self.store)
        self.handled = []
        self.transient = set()

        def handler(_client, letter, _inst):
            self.handled.append(letter["tracing_no"])
            return None if letter["tracing_no"] in self.transient else "stored"

        self.patches = [
            mock.patch.object(w, "HANDLERS", {"ن-20": handler}),
            mock.patch.object(w, "_universe", return_value={"s": {"ins_code": "1"}}),
            mock.patch.object(w.time, "sleep"),
            mock.patch("codal_monthly.Pacer", return_value=lambda transient: None),
            mock.patch.object(w.fundamental_db, "sync_from_market_db"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        fundamental_db.set_path(self.previous)
        if os.path.exists(self.store):
            os.unlink(self.store)

    def poll(self, feed):
        with mock.patch.object(w, "_page", side_effect=feed.page):
            return w.poll(verbose=False, client=object())

    def seen(self):
        return w._seen()

    def test_a_transient_failure_is_retried_after_new_letters_arrive(self):
        # Letter 1 fails transiently; two letters later push it to page 2,
        # below the first page holding a seen letter -- where the old poll
        # stopped, so it was never asked for again.
        feed = Feed(3)
        self.transient = {"1"}
        self.poll(feed)
        self.assertNotIn("1", self.seen())

        self.transient = set()
        feed.publish(4, 5)
        self.poll(feed)

        self.assertIn("1", self.seen())
        self.assertEqual(self.handled.count("1"), 2)

    def test_the_retry_queue_stops_at_a_second_refusal(self):
        # Codal still refusing: a third retry would only set the Pacer
        # sleeping inside the poll. The rest wait for the next poll.
        feed = Feed(3)
        self.transient = {"1", "2", "3"}
        self.poll(feed)
        self.assertEqual(len(self.handled), 3)

        self.poll(feed)

        self.assertEqual(len(self.handled), 5)
        self.assertEqual(set(w._queued()), {"1", "2", "3"})

    def test_a_letter_that_keeps_failing_is_given_up(self):
        feed = Feed(1)
        self.transient = {"1"}
        with mock.patch.object(w, "RETRY_LIMIT", 2):
            self.poll(feed)
            self.poll(feed)

        conn = fundamental_db._connect()
        outcome = conn.execute("SELECT outcome FROM watch_seen "
                               "WHERE tracing_no = '1'").fetchone()[0]
        conn.close()
        self.assertEqual(outcome, "gave up after 2 transient failures")
        self.assertEqual(w._queued(), {})

    def test_a_failed_page_leaves_its_letters_for_the_next_poll(self):
        feed = Feed(3)
        self.poll(feed)                     # 1-3 handled, the mark at 3
        feed.publish(*range(4, 13))         # nine more: pages 1-3
        feed.fail = {2}
        self.poll(feed)                     # page 2 drops: only 10-12 handled
        self.assertNotIn("8", self.seen())

        feed.fail = set()
        feed.publish(13)
        self.poll(feed)                     # stopped at seen 11-12 before

        self.assertTrue({str(n) for n in range(1, 14)} <= self.seen())
        self.assertIsNone(w.load_state()["sweep"])

    def test_a_backlog_deeper_than_the_page_cap_is_swept_over_polls(self):
        feed = Feed(3)
        self.poll(feed)                     # the mark at letter 3
        feed.publish(*range(4, 34))         # thirty new letters: ten pages

        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            for extra in range(34, 60):
                feed.publish(extra)         # one more each poll
                feed.calls = []
                self.poll(feed)
                self.assertLessEqual(len(feed.calls), 2 + 3)
                if w.load_state()["sweep"] is None:
                    break

        self.assertTrue({str(n) for n in range(1, extra + 1)} <= self.seen())
        self.assertEqual(sorted(self.handled, key=int),
                         [str(n) for n in range(1, extra + 1)],
                         "every letter handled exactly once")
        self.assertEqual(w.load_state()["through"],
                         "1405/08/10 10:%02d:00" % extra)

    def test_an_interrupted_catch_up_is_finished_by_the_routine_polls(self):
        # 2026-11-06: a poll had seen the newest letters; a catch-up then
        # handled its range oldest first and was cut off part way, and the
        # next polls stopped at the seen letters above the unhandled ones.
        feed = Feed(9)
        self.poll(feed)                     # 7-9 handled, the mark at 9

        def cut_off(_client, letter, _inst):
            if letter["tracing_no"] == "4":
                raise KeyboardInterrupt
            self.handled.append(letter["tracing_no"])
            return "stored"

        with mock.patch.object(w, "HANDLERS", {"ن-20": cut_off}):
            with self.assertRaises(KeyboardInterrupt):
                with mock.patch.object(w, "_page", side_effect=feed.page):
                    w.poll(since="1405/08/10", verbose=False, client=object())
        self.assertEqual(self.seen(), {"1", "2", "3", "7", "8", "9"})

        self.poll(feed)

        self.assertTrue({"4", "5", "6"} <= self.seen())

    def test_a_catch_up_that_gets_all_the_way_down_raises_the_mark(self):
        feed = Feed(9)
        self.poll(feed)
        with mock.patch.object(w, "_page", side_effect=feed.page):
            w.poll(since="1405/08/10", verbose=False, client=object())

        self.assertEqual(self.seen(), {str(n) for n in range(1, 10)})
        self.assertEqual(w.load_state(), {"through": "1405/08/10 10:09:00",
                                          "sweep": None})


class ScanTests(unittest.TestCase):

    def scan(self, feed, state, seen=()):
        with mock.patch.object(w, "_page", side_effect=feed.page), \
                mock.patch.object(w.time, "sleep"):
            fresh, state = w.scan(None, set(seen), state)
        return [l["tracing_no"] for l in fresh], state

    def test_it_pages_to_the_mark_not_to_the_first_seen_letter(self):
        feed = Feed(9)
        # Letter 8 was seen by a catch-up, on page 1; the mark is letter 4,
        # on page 2. The first-seen stop read page 1 only.
        fresh, state = self.scan(feed, {"through": "1405/08/10 10:04:00",
                                        "sweep": None}, seen={"8"})
        self.assertEqual(fresh, ["9", "7", "6", "5", "4"])
        self.assertEqual(feed.calls, [1, 2])
        self.assertEqual(state, {"through": "1405/08/10 10:09:00",
                                 "sweep": None})

    def test_a_jump_that_lands_below_the_floor_steps_back(self):
        feed = Feed(12)
        # The floor is letter 10 (index 2), but its index is recorded as 11:
        # the jump lands on page 4, below it, and must come back up.
        state = {"through": "1405/08/10 10:00:00",
                 "sweep": {"top": "12", "top_at": "1405/08/10 10:12:00",
                           "floor_at": "1405/08/10 10:10:00",
                           "floor_index": 11}}
        fresh, _state = self.scan(feed, state)
        self.assertEqual(feed.calls[:3], [1, 3, 2])
        self.assertTrue({"9", "8", "7", "6", "5", "4"} <= set(fresh))


class RewindTests(unittest.TestCase):

    def setUp(self):
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.store)
        database.create_database()
        w._ensure_table()
        w._mark(announcement(1, 1), "1", "stored")

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.store):
            if os.path.exists(path):
                os.unlink(path)

    def test_a_dry_run_reports_and_saves_nothing(self):
        result = w.rewind("1405/08/04", verbose=False)
        self.assertEqual(result["through"], "1405/08/04 00:00:00")
        conn = fundamental_db._connect()
        saved = conn.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0]
        conn.close()
        self.assertEqual(saved, 0)

    def test_apply_moves_the_mark_back_and_never_forward(self):
        w.rewind("1405/08/04", apply=True, verbose=False)
        self.assertEqual(w.load_state(), {"through": "1405/08/04 00:00:00",
                                          "sweep": None})
        w.rewind("1405/08/09", apply=True, verbose=False)
        self.assertEqual(w.load_state()["through"], "1405/08/04 00:00:00")


if __name__ == "__main__":
    unittest.main()
