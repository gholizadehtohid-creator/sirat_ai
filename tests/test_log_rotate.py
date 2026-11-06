"""log_rotate: a task log past its limit becomes .1; nothing ever fails a task."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import log_rotate  # noqa: E402


class LogRotateTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "task.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, path, size):
        with open(path, "wb") as handle:
            handle.write(b"x" * size)

    def test_a_small_log_is_left_alone(self):
        self.write(self.log, 10)
        self.assertFalse(log_rotate.rotate(self.log, max_bytes=50))
        self.assertTrue(os.path.exists(self.log))

    def test_a_large_log_becomes_the_previous_one(self):
        self.write(self.log + ".1", 5)               # an older previous
        self.write(self.log, 100)
        self.assertTrue(log_rotate.rotate(self.log, max_bytes=50))
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(os.path.getsize(self.log + ".1"), 100)

    def test_a_missing_log_is_not_an_error(self):
        self.assertFalse(log_rotate.rotate(self.log, max_bytes=50))
        self.assertEqual(log_rotate.main([self.log]), 0)


if __name__ == "__main__":
    unittest.main()
