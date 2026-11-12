"""codal_backfill.campaign: the search host's back-off is its own.

The full test, 2026-11-12: a round that downloaded filings (excel.codal.ir)
reset the throttle streak, so a refused discover (search.codal.ir, the host
the two-hourly watch shares) was retried every 15 minutes all day and the
watch stalled for ten hours.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_backfill as cb  # noqa: E402


class Clock:

    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def sleep_until(self, until, deadline=float("inf")):
        self.t = max(self.t, min(until, deadline))


class SearchBackoffTests(unittest.TestCase):

    def run_campaign(self, hours, discover_throttled=True):
        clock = Clock()
        discovers = []

        def discover(*_a, **_k):
            discovers.append(clock.t)
            return {"letters": 0, "throttled": discover_throttled}

        fetched = {"stored": 2, "unparseable": 0, "failed": 0, "pending": 5,
                   "throttled": False}
        with mock.patch.object(cb.time, "time", side_effect=clock.time), \
                mock.patch.object(cb, "_sleep_until", side_effect=clock.sleep_until), \
                mock.patch.object(cb, "remaining_to_discover", return_value=50), \
                mock.patch.object(cb, "discover", side_effect=discover), \
                mock.patch.object(cb, "fetch", return_value=dict(fetched)), \
                mock.patch.object(cb, "_watch_due"), \
                mock.patch.object(cb, "_sync_store"), \
                mock.patch.object(cb.codal, "CodalClient"):
            start = clock.t
            cb.campaign("1394/01/01", None, hours=hours, verbose=False)
        return [round((t - start) / 60) for t in discovers]

    def test_a_throttled_search_backs_off_though_downloads_succeed(self):
        minutes = self.run_campaign(hours=8)
        gaps = [b - a for a, b in zip(minutes, minutes[1:])]
        # 15, 30, 60, 120, 240 -- not a discover every 15 minutes.
        self.assertEqual(gaps[:5], [15, 30, 60, 120, 240])
        self.assertLessEqual(len(minutes), 6)

    def test_an_open_search_is_asked_every_round(self):
        minutes = self.run_campaign(hours=0.1, discover_throttled=False)
        self.assertGreater(len(minutes), 4)


if __name__ == "__main__":
    unittest.main()
