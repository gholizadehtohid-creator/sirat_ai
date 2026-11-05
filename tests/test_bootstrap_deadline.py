"""bootstrap._run_step: a one-request step cannot hold the run for hours."""

import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap  # noqa: E402


class DeadlineTests(unittest.TestCase):

    def test_a_stuck_step_is_cut_off_at_its_ceiling(self):
        release = threading.Event()
        try:
            with mock.patch.dict(bootstrap.STEP_DEADLINE_SECONDS, {"indices": 0.2}):
                with self.assertRaises(bootstrap.StepTimeout):
                    bootstrap._run_step("indices", lambda _on: release.wait(5), None)
        finally:
            release.set()

    def test_a_step_inside_its_ceiling_returns_its_result(self):
        self.assertTrue(bootstrap._run_step("indices", lambda _on: True, None))

    def test_a_steps_exception_still_reaches_the_caller(self):
        def boom(_on):
            raise ValueError("bad")
        with self.assertRaises(ValueError):
            bootstrap._run_step("indices", boom, None)

    def test_steps_without_a_ceiling_run_inline(self):
        seen = []
        bootstrap._run_step("history",
                            lambda _on: seen.append(threading.current_thread()), None)
        self.assertIs(seen[0], threading.current_thread())


if __name__ == "__main__":
    unittest.main()
