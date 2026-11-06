"""app.memoise: stale-while-refresh, and one computation per key at a time."""

import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


def wait_for(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class PanelCacheTests(unittest.TestCase):

    def setUp(self):
        app.invalidate()
        self.calls = 0

    def producer(self):
        self.calls += 1
        return {"value": self.calls}

    def age(self, key, seconds):
        with app._cache_lock:
            app._cache[key]["at"] = time.time() - seconds

    def test_a_stale_entry_is_served_at_once_and_refreshed_behind(self):
        app.memoise("k", self.producer)
        self.age("k", app.CACHE_TTL + 1)

        self.assertEqual(app.memoise("k", self.producer)["value"], 1)
        self.assertTrue(wait_for(lambda: self.calls == 2))
        self.assertTrue(wait_for(
            lambda: app.memoise("k", self.producer)["value"] == 2))

    def test_past_the_grace_the_caller_waits_for_a_fresh_answer(self):
        app.memoise("k", self.producer)
        self.age("k", app.CACHE_STALE_GRACE + 1)
        self.assertEqual(app.memoise("k", self.producer)["value"], 2)

    def test_concurrent_callers_share_one_computation(self):
        gate = threading.Event()
        runs = []

        def slow():
            runs.append(1)
            gate.wait(5)
            return {"value": len(runs)}

        results = []
        threads = [threading.Thread(target=lambda: results.append(
            app.memoise("slow", slow))) for _ in range(4)]
        for t in threads:
            t.start()
        self.assertTrue(wait_for(lambda: "slow" in app._inflight))
        time.sleep(0.05)
        gate.set()
        for t in threads:
            t.join(5)

        self.assertEqual(len(runs), 1)
        self.assertEqual([r["value"] for r in results], [1, 1, 1, 1])

    def test_a_failed_background_refresh_keeps_the_old_answer(self):
        app.memoise("k", self.producer)
        self.age("k", app.CACHE_TTL + 1)

        def broken():
            raise RuntimeError("provider down")

        with mock.patch.object(app.log, "warning") as warned:
            self.assertEqual(app.memoise("k", broken)["value"], 1)
            self.assertTrue(wait_for(lambda: warned.called))
        self.assertNotIn("k", app._inflight)
        with app._cache_lock:
            self.assertEqual(app._cache["k"]["value"]["value"], 1)


if __name__ == "__main__":
    unittest.main()
