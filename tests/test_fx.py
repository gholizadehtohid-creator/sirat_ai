"""fx: the dollar series -- parsing, lookups, and a refresh that never raises."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fx  # noqa: E402


def row(close, greg, jal):
    # tgju's shape: open, low, high, close, change, change%, gregorian, jalali
    return [close, close, close, close, "<span>0</span>", "-", greg, jal]


PAYLOAD = {"data": [
    row("1,950,000", "2026/11/22", "1405/09/01"),
    row("1,940,000", "2026/11/21", "1405/08/30"),
    row("19,400", "2026/11/19", "1405/08/28"),          # a typing error
    row("1,930,000", "2026/11/18", "1405/08/27"),
    row("not a price", "2026/11/17", "1405/08/26"),
    row("1,000,000", "2025/11/22", "1404/09/01"),
]}


class ParseTests(unittest.TestCase):

    def test_rows_are_dated_priced_and_oldest_first(self):
        rows = fx.parse(PAYLOAD)
        self.assertEqual(rows[0], ("2025-11-22", "1404/09/01", 1_000_000.0))
        self.assertEqual(rows[-1], ("2026-11-22", "1405/09/01", 1_950_000.0))

    def test_a_close_far_from_both_neighbours_is_dropped(self):
        closes = [r[2] for r in fx.parse(PAYLOAD)]
        self.assertNotIn(19_400.0, closes)
        self.assertNotIn(None, closes)
        self.assertEqual(len(closes), 4)


class LookupTests(unittest.TestCase):

    def setUp(self):
        rows = fx.parse(PAYLOAD)
        self.record = {"fetched_at": datetime.now(timezone.utc).isoformat(),
                       "series": {"nima": rows, "free": [(d, j, c * 1.4) for d, j, c in rows]}}

    def test_rate_on_takes_the_close_on_or_before_the_day(self):
        # 1405/08/29 is 2026-11-20: the 19th was a typing error, so the 18th
        self.assertEqual(fx.rate_on("1405/08/29", "nima", self.record), 1_930_000.0)
        self.assertEqual(fx.rate_on("1405/08/30", "nima", self.record), 1_940_000.0)
        self.assertIsNone(fx.rate_on("1390/01/01", "nima", self.record))

    def test_average_over_a_window(self):
        avg = fx.average("1405/08/27", "1405/09/01", "nima", self.record)
        self.assertAlmostEqual(avg, (1_930_000 + 1_940_000 + 1_950_000) / 3)

    def test_summary_names_both_rates_their_gap_and_the_year(self):
        s = fx.summary(self.record)
        self.assertEqual(s["as_of"], "1405/09/01")
        self.assertAlmostEqual(s["gap"], 0.4)
        self.assertAlmostEqual(s["rates"]["nima"]["change_1y"], 0.95)


class RefreshTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        self.patch = mock.patch.object(fx, "PATH", self.path)
        self.patch.start()
        fx._CACHE.update(mtime=None, data=None)

    def tearDown(self):
        self.patch.stop()
        fx._CACHE.update(mtime=None, data=None)
        for p in (self.path, self.path + ".partial"):
            if os.path.exists(p):
                os.unlink(p)

    def test_a_fresh_copy_is_not_refetched(self):
        with open(self.path, "w", encoding="utf-8") as h:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(),
                       "series": {}}, h)
        with mock.patch.object(fx, "fetch", side_effect=AssertionError("fetched")):
            self.assertIn("fresh", fx.refresh())

    def test_a_failed_fetch_keeps_the_old_copy_and_says_so(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with open(self.path, "w", encoding="utf-8") as h:
            json.dump({"fetched_at": old, "series": {"nima": [["2026-11-01", "1405/08/10", 1.0]]}}, h)
        with mock.patch.object(fx, "fetch", side_effect=OSError("offline")):
            message = fx.refresh()
        self.assertIn("failed", message)
        self.assertEqual(fx.series("nima"), [("2026-11-01", "1405/08/10", 1.0)])

    def test_fetch_writes_both_series_whole(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [row("%d" % (1000 + i), "2026/%02d/%02d" % (1 + i // 28, 1 + i % 28),
                                     "1405/01/01") for i in range(150)]}

        class Session:
            def get(self, url, timeout=None):
                return Response()

        record = fx.fetch(session=Session())
        self.assertEqual(set(record["series"]), {"free", "nima"})
        self.assertEqual(len(fx.load()["series"]["nima"]), 150)


if __name__ == "__main__":
    unittest.main()
