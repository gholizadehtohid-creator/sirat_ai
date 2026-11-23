"""app's cache: bounded, and one failure per computation (2026-11-23).

The software review of that day found two things test_panel_cache.py did
not cover. Nothing ever left the cache except through invalidate(), so a
panel left running kept every generation of every scan. And when the caller
that owned a computation failed, each caller waiting on it ran the producer
again itself: four waiting tabs, four full scans -- or four times the ~100
TSETMC requests of /api/opportunities, at the moment the host was refusing.
"""

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


class WatchedFlight(app._Flight):
    """A flight that says how many callers have reached its wait.

    The tests below release the owner only once every other caller is known
    to be waiting. A sleep would do most of the time; a caller that arrived
    after the owner had finished would start a second computation of its own
    and the test would fail for a reason that is not the one under test.
    """

    __slots__ = ("arrived",)

    def __init__(self):
        super().__init__()
        self.arrived = []
        real = self.done.wait

        def wait(timeout=None):
            self.arrived.append(1)
            return real(timeout)

        self.done.wait = wait


class OneOutcomePerFlightTests(unittest.TestCase):

    CALLERS = 4

    def setUp(self):
        app.invalidate()
        self.gate = threading.Event()
        self.runs = []
        self.outcomes = []

    def tearDown(self):
        self.gate.set()
        app.invalidate()

    def call(self, producer):
        try:
            self.outcomes.append(("value", app.memoise("k", producer)))
        except Exception as exc:                                # noqa: BLE001
            self.outcomes.append((type(exc).__name__, str(exc)))

    def race(self, producer):
        """CALLERS concurrent memoise() calls on one key, the owner held
        until the rest are waiting on it."""

        with mock.patch.object(app, "_Flight", WatchedFlight):
            threads = [threading.Thread(target=self.call, args=(producer,))
                       for _ in range(self.CALLERS)]
            for thread in threads:
                thread.start()
            self.assertTrue(wait_for(
                lambda: len(getattr(app._inflight.get("k"), "arrived", ()))
                == self.CALLERS - 1), "the callers never queued on one flight")
            self.gate.set()
            for thread in threads:
                thread.join(5)

    def broken(self):
        self.runs.append(1)
        self.gate.wait(5)
        raise RuntimeError("provider down")

    def test_waiters_get_the_owners_error_and_run_nothing(self):
        self.race(self.broken)

        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.outcomes,
                         [("RuntimeError", "provider down")] * self.CALLERS)

    def test_a_failure_leaves_nothing_behind(self):
        self.race(self.broken)

        self.assertNotIn("k", app._inflight)
        with app._cache_lock:
            self.assertNotIn("k", app._cache)

    def test_the_next_request_is_the_retry(self):
        self.race(self.broken)

        self.assertEqual(app.memoise("k", lambda: {"value": "back"}),
                         {"value": "back"})

    def test_waiters_get_the_owners_answer_not_a_second_look_at_the_cache(self):
        """A waiter read the cache after the owner finished. An invalidate()
        landing in between -- any import button -- left it nothing to read,
        and it ran the producer again."""

        def slow():
            self.runs.append(1)
            self.gate.wait(5)
            return {"value": len(self.runs)}

        def emptied(_now):
            app._cache.clear()              # called with the lock held

        with mock.patch.object(app, "_evict", side_effect=emptied):
            self.race(slow)

        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.outcomes,
                         [("value", {"value": 1})] * self.CALLERS)

    def test_an_interrupted_owner_does_not_hand_its_waiters_none(self):
        def interrupted():
            self.runs.append(1)
            self.gate.wait(5)
            raise KeyboardInterrupt()

        def owner_or_waiter():
            try:
                self.call(interrupted)
            except KeyboardInterrupt:
                self.outcomes.append(("KeyboardInterrupt", ""))

        with mock.patch.object(app, "_Flight", WatchedFlight):
            threads = [threading.Thread(target=owner_or_waiter)
                       for _ in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(wait_for(
                lambda: len(getattr(app._inflight.get("k"), "arrived", ())) == 1))
            self.gate.set()
            for thread in threads:
                thread.join(5)

        self.assertEqual(len(self.runs), 1)
        self.assertEqual(sorted(kind for kind, _ in self.outcomes),
                         ["KeyboardInterrupt", "RuntimeError"])


class BoundTests(unittest.TestCase):

    def setUp(self):
        app.invalidate()

    def tearDown(self):
        app.invalidate()

    def store(self, key):
        return app.memoise(key, lambda: {"key": key})

    def keys(self):
        with app._cache_lock:
            return set(app._cache)

    def mark(self, key, **fields):
        with app._cache_lock:
            app._cache[key].update(fields)

    def test_an_entry_past_the_grace_goes_when_an_answer_is_stored(self):
        self.store("old")
        self.mark("old", at=time.time() - app.CACHE_STALE_GRACE - 1)

        self.store("new")

        self.assertEqual(self.keys(), {"new"})

    def test_a_stale_entry_inside_the_grace_stays(self):
        """It is what stale-while-refresh serves; the bound must not take it."""

        self.store("stale")
        self.mark("stale", at=time.time() - app.CACHE_TTL - 1)

        self.store("new")

        self.assertEqual(self.keys(), {"stale", "new"})

    def test_past_the_count_the_least_recently_served_go_first(self):
        for key in ("a", "b", "c"):
            self.store(key)
        self.mark("a", used=300.0)
        self.mark("b", used=100.0)
        self.mark("c", used=200.0)

        with mock.patch.object(app, "CACHE_MAX_ENTRIES", 3):
            self.store("d")
            self.assertEqual(self.keys(), {"a", "c", "d"})
            self.store("e")
            self.assertEqual(self.keys(), {"a", "d", "e"})

    def test_serving_an_entry_is_what_counts_as_use(self):
        self.store("a")
        self.mark("a", used=0.0)

        self.store("a")                     # a fresh hit: served, not computed

        with app._cache_lock:
            self.assertGreater(app._cache["a"]["used"], 0.0)

    def test_the_answer_just_stored_is_never_the_one_dropped(self):
        with mock.patch.object(app, "CACHE_MAX_ENTRIES", 1):
            self.store("a")
            self.store("b")

        self.assertEqual(self.keys(), {"b"})

    def test_an_entry_with_no_use_recorded_is_ordered_by_its_age(self):
        for key in ("a", "b"):
            self.store(key)
        now = time.time()
        with app._cache_lock:
            app._cache["a"] = {"at": now - 60, "value": 1}
            app._cache["b"] = {"at": now - 30, "value": 2}

        with mock.patch.object(app, "CACHE_MAX_ENTRIES", 2):
            self.store("c")

        self.assertEqual(self.keys(), {"b", "c"})

    def test_the_default_bound_holds_a_working_session(self):
        """About twenty keys per data version: the count must not be so low
        that one page load evicts its own first answers."""

        self.assertGreaterEqual(app.CACHE_MAX_ENTRIES, 100)
        for n in range(60):
            self.store("k%d" % n)
        self.assertEqual(len(self.keys()), 60)


if __name__ == "__main__":
    unittest.main()
