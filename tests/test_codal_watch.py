"""codal_watch.collect: page back only as far as needed."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_watch as w  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
