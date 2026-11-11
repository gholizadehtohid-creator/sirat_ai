"""codal_watch: page back as far as needed, and never lose a letter."""

import contextlib
import io
import json
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

    def read(self, letters, total, number=5, feed=None):
        with mock.patch.object(w, "_page", return_value=(letters, total)), \
                mock.patch.object(w, "PAGE_SIZE", 3):
            return w._read(None, number, None, {} if feed is None else feed)

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

    # ---- which Total places the end (the second review, 2026-11-11) -------

    def test_a_total_its_own_letters_contradict_is_no_total(self):
        # One letter on page 1 and "Total: 0" passed for a whole page.
        one = [letter(1, 10)]
        self.assertIsNone(self.read(one, 0, number=1))
        full = [letter(n, 10) for n in (3, 2, 1)]
        self.assertEqual(self.read(full, 0, number=1), full)

    def test_the_largest_total_read_in_the_pass_places_the_end(self):
        feed = {}
        full = [letter(n, 10) for n in (3, 2, 1)]
        self.assertEqual(self.read(full, 30, number=1, feed=feed), full)
        # Page 5 empty, "Total: 12": consistent with itself, but the pass
        # has read a feed of 30 letters, so page 5 is due 3 of them.
        self.assertIsNone(self.read([], 12, feed=feed))
        self.assertEqual(feed["read"], 30)

    def test_a_stored_total_is_a_floor_for_every_answer(self):
        self.assertIsNone(self.read([], 12, feed={"stored": 30}))
        self.assertEqual(self.read([], 12, feed={"stored": 12}), [])

    def test_an_answer_from_an_older_copy_of_the_feed_is_a_failed_read(self):
        # Its page holds letters from further down than its number says.
        feed = {}
        full = [letter(n, 10) for n in (3, 2, 1)]
        self.assertEqual(self.read(full, 30, number=1, feed=feed), full)
        self.assertIsNone(self.read(full, 29, number=2, feed=feed))
        self.assertEqual(self.read(full, 31, number=2, feed=feed), full)
        # Only within the pass: a stored Total is not the copy this was.
        self.assertEqual(self.read(full, 29, number=2, feed={"stored": 30}),
                         full)


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
    which looks just like the end of the feed but for its Total. `totals`
    gives a page's answer another Total than the feed's real one, `stale`
    answers a page once with what it is given -- an older copy of the
    feed, say -- and `cut` answers every page as if the feed held only its
    newest `cut` letters, Total and all.
    """

    def __init__(self, count, code=None):
        self.code = code or (lambda n: "ن-20")
        self.letters = [self.make(n) for n in range(count, 0, -1)]
        self.calls = []
        self.fail = set()
        self.empty = set()
        self.short = set()
        self.totals = {}
        self.stale = {}
        self.cut = None

    def make(self, n):
        return dict(announcement(n, n), letter_code=self.code(n))

    def publish(self, *numbers):
        self.letters[:0] = [self.make(n) for n in sorted(numbers, reverse=True)]

    def page(self, _client, number):
        self.calls.append(number)
        if number in self.fail:
            raise ConnectionError("dropped")
        if number in self.stale:
            return self.stale.pop(number)
        if self.cut is not None:
            return pages_of(self.letters[:self.cut]).get(number, []), self.cut
        letters = pages_of(self.letters).get(number, [])
        if number in self.empty:
            letters = []
        elif number in self.short:
            letters = letters[:1]
        return letters, self.totals.get(number, len(self.letters))


class PollTests(unittest.TestCase):
    """Whole polls against a fake feed, a fake handler and a temp store."""

    def setUp(self):
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.previous = fundamental_db.set_path(self.store)
        self.handled = []
        self.transient = set()

        def handler(_client, letter, _inst):
            self.handled.append(letter["tracing_no"])
            return None if letter["tracing_no"] in self.transient else "stored"

        self.handler = handler
        self.patches = [
            mock.patch.object(w, "HANDLERS", {"ن-20": handler}),
            mock.patch.object(w, "_universe", return_value={"s": {"ins_code": "1"}}),
            mock.patch.object(w.time, "sleep"),
            mock.patch("codal_monthly.Pacer", return_value=lambda transient: None),
            mock.patch.object(w.fundamental_db, "sync_from_market_db"),
            mock.patch.object(w, "PAGE_SIZE", 3),
            # A statement or monthly report goes into the market database's
            # letter ledger before its handler runs: never the real one.
            mock.patch.object(database, "DATABASE_NAME", self.market),
            mock.patch.object(w.database, "save_codal_letters"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        fundamental_db.set_path(self.previous)
        for path in (self.store, self.market):
            if os.path.exists(path):
                os.unlink(path)

    def poll(self, feed, verbose=False):
        with mock.patch.object(w, "_page", side_effect=feed.page):
            return w.poll(verbose=verbose, client=object())

    def seen(self):
        return w._seen()

    def marked_at(self, through):
        """A store whose mark was saved before any Total was (as live's was)."""

        w._ensure_table()
        w.save_state({"through": through, "sweep": None})

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
        self.assertEqual([d["tracing_no"] for d in w.notes()["dead"]], ["1"])

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
                # The top, the sweep, and the page each of them ends on
                # read a second time (_confirm).
                self.assertLessEqual(len(feed.calls), 2 + 3 + 2)
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

    # ---- the second review (2026-11-11) ---------------------------------
    # Which Total a page is judged by.

    def test_a_degraded_page_1_whose_total_is_0_claims_nothing(self):
        # Page 1 came back with one letter of three and "Total: 0", and no
        # Total had been stored yet (live's first poll after the change):
        # it passed for a whole page, page 2 was read as contiguous, and
        # 11 and 10 were never read while the mark moved to 12.
        feed = Feed(12)
        self.marked_at("1405/08/10 10:03:00")
        for done in feed.letters[9:]:
            w._mark(done, "1", "stored")
        feed.short, feed.totals = {1}, {1: 0}
        self.poll(feed)

        self.assertEqual(w.load_state(), {"through": "1405/08/10 10:03:00",
                                          "sweep": None})
        self.assertNotIn("11", self.seen())

        feed.short, feed.totals = set(), {}
        self.poll(feed)
        self.assertTrue({str(n) for n in range(1, 13)} <= self.seen())

    def test_a_degraded_page_1_whose_small_total_agrees_with_itself(self):
        # One letter and "Total: 1" contradicts nothing on the page itself,
        # and no Total was stored yet; page 2's real Total, read later in
        # the same pass, says page 1 was short.
        feed = Feed(12)
        self.marked_at("1405/08/10 10:03:00")
        for done in feed.letters[9:]:
            w._mark(done, "1", "stored")
        feed.short, feed.totals = {1}, {1: 1}
        self.poll(feed)

        self.assertEqual(w.load_state(), {"through": "1405/08/10 10:03:00",
                                          "sweep": None})
        self.assertNotIn("11", self.seen())

        feed.short, feed.totals = set(), {}
        self.poll(feed)
        self.assertTrue({str(n) for n in range(1, 13)} <= self.seen())

    def test_a_feed_cut_short_with_its_own_small_total_claims_nothing(self):
        # Every page answered as if the feed held four letters, Total 4 on
        # each: nothing in the pass contradicts it. The Total an earlier
        # poll stored does -- the market-wide feed only grows.
        feed = Feed(12)
        self.marked_at("1405/08/10 10:00:00")
        self.poll(feed)                     # 1-12 handled, Total 12 stored
        feed.publish(*range(13, 25))
        feed.cut = 4
        self.poll(feed)

        self.assertEqual(w.load_state(), {"through": "1405/08/10 10:12:00",
                                          "sweep": None})
        self.assertNotIn("20", self.seen())

        feed.cut = None
        self.poll(feed)
        self.assertTrue({str(n) for n in range(1, 25)} <= self.seen())

    def test_a_catch_up_whose_page_1_turns_out_short_keeps_the_mark_low(self):
        feed = Feed(9)
        self.marked_at("1405/08/10 10:09:00")       # no Total stored yet
        feed.short, feed.totals = {1}, {1: 1}
        with mock.patch.object(w, "_page", side_effect=feed.page):
            w.poll(since="1405/08/10", verbose=False, client=object())

        self.assertEqual(w.load_state()["through"], "1405/08/10 00:00:00")
        self.assertNotIn("8", self.seen())

        feed.short, feed.totals = set(), {}
        self.poll(feed)
        self.assertEqual(self.seen(), {str(n) for n in range(1, 10)})

    def test_an_empty_first_sweep_page_with_total_0_keeps_the_floor(self):
        feed = Feed(3)
        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            before = self.backlog_and_sweep(feed)
            feed.empty, feed.totals = {3}, {3: 0}
            self.poll(feed)

            self.assertEqual(w.load_state(), before)

            feed.empty, feed.totals = set(), {}
            self.sweep_to_the_end(feed)

        self.assertTrue({str(n) for n in range(1, 34)} <= self.seen())

    def test_the_largest_total_is_kept_from_poll_to_poll(self):
        feed = Feed(3)
        self.poll(feed)
        self.assertEqual(w.notes()["total"], 3)
        feed.publish(4, 5)
        self.poll(feed)
        self.assertEqual(w.notes()["total"], 5)
        feed.totals = {1: 4}                # an older answer: kept as is
        self.poll(feed)
        self.assertEqual(w.notes()["total"], 5)

    # Refusals among new letters: the retry pass's rule.

    def refusing(self):
        tried = []

        def refuse(_client, letter, _inst):
            tried.append(letter["tracing_no"])
            return None                     # the document host refuses

        return tried, {"ن-10": refuse, "ن-20": self.handler}

    def test_refused_new_letters_are_queued_untried_after_two(self):
        # Six refused new letters slept 15+30+60+120 minutes inside a poll
        # the campaign holds the watch lock for (the second review).
        feed = Feed(5, code=lambda n: "ن-20" if n == 5 else "ن-10")
        self.marked_at("1405/08/10 10:00:00")
        tried, handlers = self.refusing()
        with mock.patch.object(w, "HANDLERS", handlers), \
                mock.patch("codal_monthly.Pacer", PACER), \
                mock.patch.object(w.time, "sleep") as slept:
            self.poll(feed)

        self.assertEqual(tried, ["1", "2"])
        self.assertEqual({n: tries for n, (_l, tries) in w._queued().items()},
                         {"1": 1, "2": 1, "3": 0, "4": 0})
        self.assertIn("5", self.seen())     # needs no request: handled
        self.assertFalse([c for c in slept.call_args_list if c.args[0] >= 900])
        # Every letter handled or queued, so the mark moves.
        self.assertEqual(w.load_state()["through"], "1405/08/10 10:05:00")

    def test_a_failed_sweep_page_and_two_refusals_do_not_sleep(self):
        feed = Feed(3, code=lambda n: "ن-10" if n in (34, 35) else "ن-20")
        tried, handlers = self.refusing()
        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            self.backlog_and_sweep(feed)
            feed.publish(34, 35)
            feed.fail = {3}                 # the sweep's first new page
            with mock.patch.object(w, "HANDLERS", handlers), \
                    mock.patch("codal_monthly.Pacer", PACER), \
                    mock.patch.object(w.time, "sleep") as slept:
                self.poll(feed)

        self.assertEqual(tried, ["34", "35"])
        self.assertFalse([c for c in slept.call_args_list if c.args[0] >= 900])

    # A stall shows.

    def test_a_sweep_that_cannot_advance_is_called_stalled(self):
        feed = Feed(3)
        with mock.patch.object(w, "MAX_PAGES_POLL", 2), \
                mock.patch.object(w, "SWEEP_PAGES", 3):
            self.backlog_and_sweep(feed)
            feed.short = {3}                # e.g. Codal changed its page size
            for _ in range(w.STALL_POLLS):
                self.poll(feed)

            stall = w.notes()["stall"]
            self.assertEqual(stall["polls"], w.STALL_POLLS)
            self.assertIn("feed page 3 failed", stall["reason"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                w.report()
            self.assertIn("STALLED", out.getvalue())

            feed.short = set()
            self.poll(feed)

        self.assertEqual(w.notes()["stall"], {})

    def test_the_feed_total_is_logged_once_a_poll(self):
        feed = Feed(3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.poll(feed, verbose=True)
        lines = [l for l in out.getvalue().splitlines()
                 if l.startswith("watch: feed Total")]
        self.assertEqual(lines, ["watch: feed Total 3 (largest seen 3)"])

    def test_the_report_shows_the_marks_the_total_and_the_queue(self):
        feed = Feed(3)
        with mock.patch.object(w, "MAX_PAGES_POLL", 2):
            self.backlog_and_sweep(feed)
        w._queue(announcement(99, 1), 2, "HTTPError: 500 Server Error")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w.report()
        text = out.getvalue()
        self.assertIn("complete through 1405/08/10 10:03:00", text)
        self.assertIn("covered from 1405/08/10 10:33:00 (top letter 33) "
                      "down to 1405/08/10 10:28:00 (floor letter 28", text)
        self.assertIn("largest feed Total seen: 33", text)
        self.assertIn("1 letters waiting for a retry", text)
        self.assertIn("HTTPError: 500 Server Error", text)

    # Answers from an older copy of the feed, and the page a pass ends on.

    def stale_page_2(self, total):
        """1-9 handled, 10-15 published, and page 2 answered once as it was
        before them: 6, 5, 4 -- below the mark, so the read ends there and
        12, 11, 10 are skipped."""

        feed = Feed(9)
        self.marked_at("1405/08/10 10:00:00")
        self.poll(feed)                     # 1-9 handled, the mark at 9
        old = list(feed.letters)
        feed.publish(*range(10, 16))
        feed.stale = {2: (pages_of(old)[2], total(old))}
        return feed

    def test_a_page_from_an_older_copy_of_the_feed_claims_nothing(self):
        feed = self.stale_page_2(len)       # its own, older Total: 9
        self.poll(feed)

        self.assertEqual(w.load_state()["through"], "1405/08/10 10:09:00")
        self.assertNotIn("12", self.seen())

        self.poll(feed)
        self.assertEqual(w.load_state()["through"], "1405/08/10 10:15:00")
        self.assertTrue({str(n) for n in range(1, 16)} <= self.seen())

    def test_a_stale_last_page_without_a_total_is_caught_by_a_second_read(self):
        feed = self.stale_page_2(lambda old: None)
        self.poll(feed)

        self.assertEqual(w.load_state()["through"], "1405/08/10 10:09:00")
        self.assertTrue({"10", "11", "12"} <= self.seen())  # the second read's

        self.poll(feed)
        self.assertEqual(w.load_state()["through"], "1405/08/10 10:15:00")
        self.assertTrue({str(n) for n in range(1, 16)} <= self.seen())

    # A letter that keeps failing.

    def test_a_handler_that_raises_does_not_stop_the_poll(self):
        # A poison letter at the head of watch_retry used to end every poll
        # before the feed was read.
        feed = Feed(3)

        def handler(_client, letter, _inst):
            self.handled.append(letter["tracing_no"])
            if letter["tracing_no"] == "1":
                raise ValueError("no such sheet")
            return "stored"

        w._ensure_table()
        for n in (1, 2, 3):
            w._queue(announcement(n, n), 1)
        with mock.patch.object(w, "HANDLERS", {"ن-20": handler}):
            self.poll(feed)

        self.assertEqual(self.handled, ["1", "2", "3"])
        self.assertEqual(feed.calls[:1], [1])           # the feed was read
        self.assertEqual(w._queued()["1"][1], 2)
        conn = fundamental_db._connect()
        error = conn.execute("SELECT error FROM watch_retry WHERE "
                             "tracing_no = '1'").fetchone()[0]
        conn.close()
        self.assertEqual(error, "ValueError: no such sheet")

    def test_a_failed_letter_waits_behind_the_others(self):
        feed = Feed(0)
        self.transient = {"1", "2"}
        w._ensure_table()
        for n in (1, 2, 3, 4):
            w._queue(announcement(n, n), 1)
        self.poll(feed)                     # 1, 2 refused: the rest wait
        self.poll(feed)

        self.assertEqual(self.handled, ["1", "2", "3", "4", "1", "2"])

    def test_a_queue_made_before_the_error_column_is_read_and_kept(self):
        # Live's watch_retry was made by the code before this one.
        conn = fundamental_db._connect()
        conn.execute("CREATE TABLE watch_retry (tracing_no TEXT PRIMARY KEY, "
                     "letter TEXT, attempts INTEGER, last_tried TEXT)")
        conn.execute("INSERT INTO watch_retry VALUES ('7', ?, 3, "
                     "'2026-11-11T00:00:00+00:00')",
                     (json.dumps(announcement(7, 7)),))
        conn.commit()
        conn.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w.report()                      # reads only: nothing migrated
        self.assertIn("1 letters waiting for a retry", out.getvalue())

        w._ensure_table()

        conn = fundamental_db._connect()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(watch_retry)")]
        conn.close()
        self.assertIn("error", columns)
        self.assertEqual(w._queued()["7"][1], 3)

    def test_a_letter_given_up_is_kept_as_a_dead_letter(self):
        feed = Feed(1)

        def handler(_client, letter, _inst):
            raise ValueError("no such sheet")

        self.marked_at("1405/08/10 10:00:00")
        with mock.patch.object(w, "HANDLERS", {"ن-20": handler}), \
                mock.patch.object(w, "RETRY_LIMIT", 3):
            for _ in range(3):
                self.poll(feed)

        self.assertEqual(w._queued(), {})
        dead = w.notes()["dead"]
        self.assertEqual([(d["tracing_no"], d["attempts"], d["error"])
                          for d in dead],
                         [("1", 3, "ValueError: no such sheet")])
        conn = fundamental_db._connect()
        outcome = conn.execute("SELECT outcome FROM watch_seen "
                               "WHERE tracing_no = '1'").fetchone()[0]
        conn.close()
        self.assertEqual(outcome, "gave up after 3 failures")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w.report()
        self.assertIn("1 dead letters", out.getvalue())
        self.assertIn("ValueError: no such sheet", out.getvalue())

    # Stamps that are no stamps.

    def test_a_malformed_stamp_never_moves_the_mark(self):
        # "1405/8/10" sorts after every real stamp: as the mark it would
        # have every later poll stop on page 1.
        feed = Feed(3)
        self.poll(feed)
        odd = dict(announcement(4, 4), published_at="1405/8/10 11:00:00")
        feed.letters.insert(0, odd)
        self.poll(feed)

        self.assertIn("4", self.seen())
        self.assertEqual(w.load_state()["through"], "1405/08/10 10:03:00")


class ScanTests(unittest.TestCase):

    def scan(self, feed, state, seen=()):
        with mock.patch.object(w, "_page", side_effect=feed.page), \
                mock.patch.object(w, "PAGE_SIZE", 3), \
                mock.patch.object(w.time, "sleep"):
            fresh, state = w.scan(None, set(seen), state)
        return [l["tracing_no"] for l in fresh], state

    def test_it_pages_to_the_mark_not_to_the_first_seen_letter(self):
        feed = Feed(9)
        # Letter 8 was seen by a catch-up, on page 1; the mark is letter 6,
        # on page 2, and the margin past it reaches 4 on the same page. The
        # first-seen stop read page 1 only. Page 2 ends the read below page
        # 1, so it is read twice.
        fresh, state = self.scan(feed, {"through": "1405/08/10 10:06:00",
                                        "sweep": None}, seen={"8"})
        self.assertEqual(fresh, ["9", "7", "6", "5", "4"])
        self.assertEqual(feed.calls, [1, 2, 2])
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

    # ---- the second review (2026-11-11) ---------------------------------

    def test_a_sweep_page_claiming_the_end_by_a_small_total_claims_nothing(self):
        # Page 3 empty, "Total: 6" -- the end of a feed of six letters, by
        # itself; but page 1 of the same pass said fifteen.
        feed = Feed(15)
        feed.empty, feed.totals = {3}, {3: 6}
        state = {"through": "1405/08/10 10:00:00",
                 "sweep": {"top": "15", "top_at": "1405/08/10 10:15:00",
                           "floor": "10", "floor_at": "1405/08/10 10:10:00",
                           "floor_index": 5}}
        _fresh, after = self.scan(feed, state, seen={"15", "14", "13", "12",
                                                     "11", "10"})
        self.assertEqual(after, state)

    def test_the_jump_re_reads_only_the_floors_own_page(self):
        feed = Feed(21)
        # Letters 21-10 (pages 1-4) are covered; the floor letter, 10, is
        # the last of page 4. The old jump read page 3 as well.
        state = {"through": "1405/08/10 10:00:00",
                 "sweep": {"top": "21", "top_at": "1405/08/10 10:21:00",
                           "floor": "10", "floor_at": "1405/08/10 10:10:00",
                           "floor_index": 11}}
        fresh, after = self.scan(feed, state,
                                 seen={str(n) for n in range(10, 22)})
        self.assertEqual(feed.calls[:3], [1, 4, 5])
        self.assertEqual(fresh, [str(n) for n in range(9, 0, -1)])
        self.assertIsNone(after["sweep"])

    def test_a_jump_without_the_floor_letter_on_its_page_steps_back(self):
        feed = Feed(12)
        state = {"through": "1405/08/10 10:00:00",
                 "sweep": {"top": "12", "top_at": "1405/08/10 10:12:00",
                           "floor": "10", "floor_at": "1405/08/10 10:10:00",
                           "floor_index": 11}}
        fresh, _state = self.scan(feed, state)
        self.assertEqual(feed.calls[:4], [1, 4, 3, 2])
        self.assertTrue({"9", "8", "7", "6", "5", "4"} <= set(fresh))

    def test_a_letter_inverted_just_past_the_mark_is_still_read(self):
        # Not strictly in stamp order (4-30 s inversions in the 11-06 log):
        # 99, stamped after the mark, sits below letters stamped before it.
        # The read stopped on page 2, its first stamp at or before the mark.
        def at(n, clock):
            return dict(announcement(n, 0), published_at="1405/08/10 " + clock)

        feed = Feed(0)
        feed.letters = [at(9, "10:09:00"), at(8, "10:08:00"), at(7, "10:07:00"),
                        at(6, "10:06:00"), at(5, "10:05:30"), at(4, "10:05:10"),
                        at(99, "10:06:20"), at(3, "10:03:00"), at(2, "10:02:00"),
                        at(1, "10:01:00")]
        fresh, state = self.scan(feed, {"through": "1405/08/10 10:06:00",
                                        "sweep": None},
                                 seen={"6", "3", "2", "1"})
        self.assertIn("99", fresh)
        self.assertEqual(state["through"], "1405/08/10 10:09:00")

    SWEPT = {"top": "12", "top_at": "1405/08/10 10:12:00", "floor": "7",
             "floor_at": "1405/08/10 10:07:00", "floor_index": 5}

    def test_a_stale_last_page_of_a_sweep_does_not_move_the_mark(self):
        feed = Feed(12)
        # Page 3 answered from an older copy of the feed (Total 9): 3, 2, 1
        # -- the mark's page -- in place of 6, 5, 4. Read once, it ended the
        # sweep and claimed 6, 5 and 4.
        feed.stale = {3: (pages_of(feed.letters)[4], 9)}
        state = {"through": "1405/08/10 10:03:00", "sweep": self.SWEPT}
        covered = {str(n) for n in range(7, 13)}
        fresh, after = self.scan(feed, state, seen=covered)

        self.assertEqual(after, state)
        self.assertNotIn("6", fresh)

        fresh, after = self.scan(feed, after, seen=covered)
        self.assertTrue({"6", "5", "4"} <= set(fresh))
        self.assertEqual(after, {"through": "1405/08/10 10:12:00",
                                 "sweep": None})

    def test_a_page_from_an_older_copy_in_mid_sweep_is_a_failed_read(self):
        feed = Feed(15)
        # Page 3 as it was three letters ago (Total 12): 6, 5, 4. Taken for
        # page 3, it skipped 9, 8 and 7 for good, the sweep walking on.
        feed.stale = {3: (pages_of(feed.letters)[4], 12)}
        state = {"through": "1405/08/10 10:01:00",
                 "sweep": {"top": "15", "top_at": "1405/08/10 10:15:00",
                           "floor": "10", "floor_at": "1405/08/10 10:10:00",
                           "floor_index": 5}}
        covered = {str(n) for n in range(10, 16)}
        fresh, after = self.scan(feed, state, seen=covered)

        self.assertEqual(after, state)
        fresh, after = self.scan(feed, after, seen=covered)
        self.assertTrue({"9", "8", "7"} <= set(fresh))

    def test_a_last_page_the_second_read_shows_letters_missing_from(self):
        # No Total to tell the copies apart. The first read of page 3 was
        # one letter behind (5, 4, 3) and reached the mark; the second
        # (6, 5, 4) holds 6, published after the mark and never taken.
        feed = Feed(12)
        feed.stale = {3: (feed.letters[7:10], None)}
        state = {"through": "1405/08/10 10:05:00", "sweep": self.SWEPT}
        handled = {str(n) for n in range(7, 13)} | {"5", "4", "3", "2", "1"}
        fresh, after = self.scan(feed, state, seen=handled)

        self.assertEqual(fresh, ["6"])
        self.assertEqual(after, state)

    def test_a_last_page_the_second_read_shares_no_letter_with(self):
        # The first read of page 3 came from three letters back (3, 2, 1);
        # every letter is handled, so only the overlap tells.
        feed = Feed(12)
        feed.stale = {3: (pages_of(feed.letters)[4], None)}
        state = {"through": "1405/08/10 10:03:00", "sweep": self.SWEPT}
        fresh, after = self.scan(feed, state,
                                 seen={str(n) for n in range(1, 13)})

        self.assertEqual(fresh, [])
        self.assertEqual(after, state)

    def test_undated_letters_neither_end_corrupt_nor_stall_a_sweep(self):
        feed = Feed(12)
        for undated in feed.letters[6:9]:   # 6, 5, 4: page 3
            undated["published_at"] = None
        # A floor saved without a stamp (an undated page) no longer raises.
        state = {"through": "1405/08/10 10:03:00",
                 "sweep": {"top": "12", "top_at": "1405/08/10 10:12:00",
                           "floor": "10", "floor_at": None,
                           "floor_index": 2}}
        with mock.patch.object(w, "SWEEP_PAGES", 2):
            fresh, after = self.scan(feed, state, seen={"12", "11", "10"})

        # Pages 2 and 3 read: the undated page neither ended the sweep nor
        # stopped it; the floor is the last letter read, at the last stamp.
        self.assertEqual(fresh, ["9", "8", "7", "6", "5", "4"])
        self.assertEqual(after["sweep"]["floor"], "4")
        self.assertEqual(after["sweep"]["floor_at"], "1405/08/10 10:07:00")
        self.assertEqual(after["through"], "1405/08/10 10:03:00")

        fresh, after = self.scan(feed, after, seen={str(n) for n in range(4, 13)})
        self.assertEqual(fresh, ["3", "2", "1"])
        self.assertIsNone(after["sweep"])


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


class StampTests(unittest.TestCase):

    def test_a_mark_moves_back_across_a_month_and_a_year(self):
        self.assertEqual(w._before("1405/08/01 00:01:00", 120),
                         "1405/07/30 23:59:00")
        self.assertEqual(w._before("1405/01/01 00:00:30", 120),
                         "1404/12/29 23:58:30")
        self.assertEqual(w._before("1405/8/1 9:00:00", 120), "1405/8/1 9:00:00")
        self.assertIsNone(w._before(None, 120))


if __name__ == "__main__":
    unittest.main()
