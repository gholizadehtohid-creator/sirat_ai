"""codal_watch: page back as far as needed, and never lose a letter."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_monthly  # noqa: E402
import codal_watch as w  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402

# The real one, for the tests that mean the Pacer's own arithmetic; the
# others replace it with a no-op.
PACER = codal_monthly.Pacer


def letter(n, day, code="ن-10"):
    return {"tracing_no": str(n), "published_at": "1405/08/%02d 10:00:00" % day,
            "title": "t", "excel_url": "u", "symbol": "s", "letter_code": code}


# Newest first, 3 letters a page (w.PAGE_SIZE is patched to match):
# 9, 8, 7 | 6, 5, 4 | 3, 2, 1
PAGES = {1: [letter(9, 14), letter(8, 14), letter(7, 13)],
         2: [letter(6, 12), letter(5, 11), letter(4, 10)],
         3: [letter(3, 9), letter(2, 8), letter(1, 7)]}


class CollectTests(unittest.TestCase):

    def run_collect(self, seen, since=None, pages=PAGES):
        calls = []
        total = sum(len(letters) for letters in pages.values())

        def page(_client, n):
            calls.append(n)
            return pages.get(n, []), total

        with mock.patch.object(w, "_page", side_effect=page), \
                mock.patch.object(w, "PAGE_SIZE", 3), \
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

    def test_the_end_of_the_feed_is_where_its_total_says(self):
        # Past the last page the feed answers empty, and that is the end.
        fresh, calls = self.run_collect(seen=set(), since="1405/08/01")
        self.assertEqual(len(fresh), 9)
        self.assertEqual(calls, [1, 2, 3, 4])


class PageTests(unittest.TestCase):
    """A feed page comes with the feed's Total, which places its end."""

    def page(self, payload):
        client = mock.Mock()
        client._get.return_value.json.return_value = payload
        return w._page(client, 1)

    def test_the_answer_carries_the_feeds_total(self):
        letters, total = self.page({"Total": 3005, "Letters": [
            {"TracingNo": 7, "Symbol": "x", "LetterCode": "ن-۱۰"}]})
        self.assertEqual((letters[0]["tracing_no"], total), ("7", 3005))

    def test_an_answer_without_a_total_says_so(self):
        self.assertEqual(self.page({"Letters": None}), ([], None))
        self.assertEqual(self.page({"Total": "n/a", "Letters": []}), ([], None))

    def read(self, letters, total, number=5):
        with mock.patch.object(w, "_page", return_value=(letters, total)), \
                mock.patch.object(w, "PAGE_SIZE", 3):
            return w._read(None, number, None, {})

    def test_without_a_total_only_a_full_page_counts(self):
        full = [letter(n, 10) for n in (3, 2, 1)]
        self.assertEqual(self.read(full, None), full)
        self.assertIsNone(self.read(full[:2], None))
        self.assertIsNone(self.read([], None))

    def test_the_last_page_may_be_short_and_past_it_empty(self):
        rest = [letter(n, 10) for n in (2, 1)]
        self.assertEqual(self.read(rest, 14), rest)      # 12 before page 5
        self.assertEqual(self.read([], 12), [])
        self.assertIsNone(self.read([], 13))


def announcement(n, minute):
    """A watched letter of a kind whose handler is faked here (ن-20)."""

    return {"tracing_no": str(n), "letter_code": "ن-20", "title": "t%d" % n,
            "symbol": "s", "published_at": "1405/08/10 10:%02d:00" % minute}


def pages_of(letters, size=3):
    """{page: letters}, newest first, `size` to a page."""

    return {i // size + 1: letters[i:i + size]
            for i in range(0, len(letters), size)}


class Feed:
    """A market feed that grows at the top between polls.

    A page in `fail` raises; one in `empty` answers HTTP 200 with no letters
    and one in `short` with only its first letter -- an answer cut short,
    which looks just like the end of the feed but for its Total.
    """

    def __init__(self, count):
        self.letters = [announcement(n, n) for n in range(count, 0, -1)]
        self.calls = []
        self.fail = set()
        self.empty = set()
        self.short = set()

    def publish(self, *numbers):
        self.letters[:0] = [announcement(n, n) for n in sorted(numbers,
                                                               reverse=True)]

    def page(self, _client, number):
        self.calls.append(number)
        if number in self.fail:
            raise ConnectionError("dropped")
        letters = pages_of(self.letters).get(number, [])
        if number in self.empty:
            letters = []
        elif number in self.short:
            letters = letters[:1]
        return letters, len(self.letters)


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
            mock.patch.object(w, "PAGE_SIZE", 3),
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

    # ---- an answer that came back empty or cut short (2026-11-11) --------
    # HTTP 200 and well-formed, so nothing raises: only the feed's Total
    # tells it from the real end, which is thousands of pages back.

    def backlog_and_sweep(self, feed):
        """Thirty letters behind a two-page cap: a sweep under way from 28."""

        self.poll(feed)                     # 1-3 handled, the mark at 3
        feed.publish(*range(4, 34))         # ten pages of new letters
        self.poll(feed)                     # pages 1-2 (33-28); the rest swept
        state = w.load_state()
        self.assertEqual(state["sweep"]["floor_at"], "1405/08/10 10:28:00")
        return state

    def sweep_to_the_end(self, feed):
        for _ in range(30):
            self.poll(feed)
            if w.load_state()["sweep"] is None:
                return
        self.fail("the sweep never finished")

    def test_an_empty_page_in_a_backlog_claims_nothing(self):
        feed = Feed(3)
        self.poll(feed)
        mark = w.load_state()["through"]
        feed.publish(*range(4, 13))         # nine more: pages 1-3
        feed.empty = {2}
        self.poll(feed)                     # read as the end: the mark at 12

        self.assertEqual(w.load_state(), {"through": mark, "sweep": None})
        self.assertNotIn("8", self.seen())

        feed.empty = set()
        self.poll(feed)
        self.assertTrue({str(n) for n in range(1, 13)} <= self.seen())

    def test_a_short_page_short_of_the_end_claims_nothing(self):
        feed = Feed(3)
        self.poll(feed)
        mark = w.load_state()["through"]
        feed.publish(*range(4, 13))
        feed.short = {2}                    # 9 of 9, 8, 7: 8 and 7 skipped
        self.poll(feed)

        self.assertEqual(w.load_state(), {"through": mark, "sweep": None})

        feed.short = set()
        self.poll(feed)
        self.assertTrue({str(n) for n in range(1, 13)} <= self.seen())

    def test_an_empty_page_in_a_sweep_keeps_its_floor(self):
        feed = Feed(3)
        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            before = self.backlog_and_sweep(feed)
            feed.empty = {3}                # the first page below the floor
            self.poll(feed)                 # read as the end: the sweep done

            self.assertEqual(w.load_state(), before)
            self.assertNotIn("27", self.seen())

            feed.empty = set()
            self.sweep_to_the_end(feed)

        self.assertTrue({str(n) for n in range(1, 34)} <= self.seen())

    def test_a_short_page_in_a_sweep_keeps_its_floor(self):
        feed = Feed(3)
        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            before = self.backlog_and_sweep(feed)
            feed.short = {3}
            self.poll(feed)

            self.assertEqual(w.load_state(), before)

            feed.short = set()
            self.sweep_to_the_end(feed)

        self.assertTrue({str(n) for n in range(1, 34)} <= self.seen())

    def test_an_empty_page_does_not_finish_a_catch_up(self):
        feed = Feed(9)
        self.poll(feed)                     # 7-9 handled, the mark at 9
        feed.empty = {2}
        with mock.patch.object(w, "_page", side_effect=feed.page):
            w.poll(since="1405/08/10", verbose=False, client=object())

        self.assertEqual(w.load_state()["through"], "1405/08/10 00:00:00")
        self.assertNotIn("5", self.seen())

        feed.empty = set()
        self.poll(feed)
        self.assertEqual(self.seen(), {str(n) for n in range(1, 10)})

    def test_a_failed_page_at_the_top_keeps_the_sweep_under_way(self):
        # It used to start a fresh sweep from the top, which re-read the
        # whole stretch the old one had covered.
        feed = Feed(3)
        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            self.backlog_and_sweep(feed)
            self.poll(feed)                 # the sweep moves on a page
            before = w.load_state()
            feed.publish(34, 35, 36, 37)    # page 1 is new; page 2 is needed
            feed.fail = {2}
            self.poll(feed)

            self.assertEqual(w.load_state(), before)
            self.assertTrue({"35", "36", "37"} <= self.seen())

            feed.fail = set()
            self.sweep_to_the_end(feed)

        self.assertTrue({str(n) for n in range(1, 38)} <= self.seen())

    def test_a_refused_page_1_after_refused_retries_does_not_sleep(self):
        # The retry pass stops at its second refusal so that the Pacer never
        # waits inside a poll; but the scan shares the Pacer, and a refused
        # feed page 1 was then its third refusal: 15 minutes asleep.
        import requests

        refused = []

        def handler(_client, letter, _inst):
            refused.append(letter["tracing_no"])
            return None                     # the document host still refuses

        def page(_client, _number):
            raise requests.HTTPError("429 Client Error",
                                     response=mock.Mock(status_code=429))

        w._ensure_table()
        for n in (1, 2, 3):
            w._queue(dict(announcement(n, n), letter_code="ن-52"), 1)

        with mock.patch.object(w, "HANDLERS", {"ن-52": handler}), \
                mock.patch("codal_monthly.Pacer", PACER), \
                mock.patch.object(w.time, "sleep") as slept, \
                mock.patch.object(w, "_page", side_effect=page):
            w.poll(verbose=False, client=object())

        self.assertEqual(len(refused), 2)
        self.assertEqual(slept.call_args_list, [])


class ScanTests(unittest.TestCase):

    def scan(self, feed, state, seen=()):
        with mock.patch.object(w, "_page", side_effect=feed.page), \
                mock.patch.object(w, "PAGE_SIZE", 3), \
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

    SWEEP = {"top": "9", "top_at": "1405/08/19 10:00:00",
             "floor_at": "1405/08/12 10:00:00", "floor_index": 400}

    def test_running_it_again_keeps_the_sweep_under_way(self):
        # A second --apply during the recovery used to clear the sweep, and
        # the next ~20 polls read the same ~400 pages again.
        w.save_state({"through": "1405/08/04 00:00:00", "sweep": self.SWEEP})

        again = w.rewind("1405/08/04", apply=True, verbose=False)
        later = w.rewind("1405/08/09", apply=True, verbose=False)

        self.assertEqual(w.load_state(), {"through": "1405/08/04 00:00:00",
                                          "sweep": self.SWEEP})
        self.assertEqual([again["changed"], again["applied"],
                          later["changed"], later["applied"]],
                         [False, False, False, False])

    def test_an_earlier_day_keeps_the_sweep_going_down_to_it(self):
        w.save_state({"through": "1405/08/04 00:00:00", "sweep": self.SWEEP})

        result = w.rewind("1405/07/22", apply=True, verbose=False)

        self.assertTrue(result["applied"])
        self.assertEqual(w.load_state(), {"through": "1405/07/22 00:00:00",
                                          "sweep": self.SWEEP})


if __name__ == "__main__":
    unittest.main()
