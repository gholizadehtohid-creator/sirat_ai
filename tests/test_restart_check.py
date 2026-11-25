"""restart_check: the panel says when its code changed under it (2026-11-25).

That day every PDF failed: the panel had loaded fundamental_outlook.py before
an update and read stock_report.py, lazily, after it -- half old code, half
new. These pin what counts as a change and that the page is told.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import restart_check  # noqa: E402


class Tree(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sirat-restart-")
        self.saved = dict(restart_check._start)
        self.write("app.py", "a = 1\n")
        self.write("pkg/__init__.py", "")
        self.write("pkg/mod.py", "b = 2\n")
        self.write("scripts/tool.py", "c = 3\n")          # not a package
        self.write("tests/test_x.py", "d = 4\n")          # tests aside
        os.makedirs(os.path.join(self.root, "static"))
        self.write("static/app.js", "x\n")
        restart_check.mark_started(self.root)

    def tearDown(self):
        restart_check._start.clear()
        restart_check._start.update(self.saved)
        restart_check._cache.update(at=0.0, value=None)
        shutil.rmtree(self.root, True)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        later = time.time() + 2
        os.utime(path, (later, later))

    def test_nothing_changed_is_no_restart(self):
        self.assertFalse(restart_check.state(force=True)["restart_needed"])

    def test_a_changed_module_asks_for_a_restart(self):
        self.write("pkg/mod.py", "b = 3\n")
        state = restart_check.state(force=True)
        self.assertTrue(state["restart_needed"])
        self.assertEqual(state["files"][0]["path"], "pkg/mod.py")
        self.assertEqual(state["files"][0]["how"], "changed")

    def test_a_touched_but_identical_file_does_not(self):
        self.write("app.py", "a = 1\n")                  # same content, new mtime
        self.assertFalse(restart_check.state(force=True)["restart_needed"])

    def test_added_and_removed_modules_count(self):
        self.write("new_module.py", "z = 1\n")
        os.remove(os.path.join(self.root, "pkg", "mod.py"))
        hows = {f["path"]: f["how"] for f in restart_check.state(force=True)["files"]}
        self.assertEqual(hows, {"new_module.py": "added", "pkg/mod.py": "removed"})

    def test_tests_and_loose_scripts_are_not_the_panels_code(self):
        self.write("tests/test_x.py", "d = 5\n")
        self.write("scripts/tool.py", "c = 4\n")
        self.assertFalse(restart_check.state(force=True)["restart_needed"])

    def test_a_page_file_moves_the_assets_stamp_only(self):
        before = restart_check.state(force=True)["assets"]
        self.write("static/app.js", "y\n")
        state = restart_check.state(force=True)
        self.assertNotEqual(state["assets"], before)
        self.assertFalse(state["restart_needed"])


class NotStartedTests(unittest.TestCase):

    def test_a_process_that_never_marked_its_start_never_asks(self):
        saved = dict(restart_check._start)
        try:
            restart_check._start.update(files=None)
            self.assertEqual(restart_check.changed(), [])
        finally:
            restart_check._start.update(saved)


class WiringTests(unittest.TestCase):

    def test_the_status_poll_carries_it_and_main_marks_the_start(self):
        import inspect

        import app

        self.assertIn("restart_check.state()", inspect.getsource(app.api_bootstrap_status))
        self.assertIn("restart_check.mark_started()", inspect.getsource(app.main))

    def test_the_page_shows_it(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "templates", "dashboard.html"), encoding="utf-8") as fh:
            self.assertIn('id="restartBanner"', fh.read())
        with open(os.path.join(root, "static", "js", "panel.js"), encoding="utf-8") as fh:
            self.assertIn("renderRestart(p.restart)", fh.read())


if __name__ == "__main__":
    unittest.main()
