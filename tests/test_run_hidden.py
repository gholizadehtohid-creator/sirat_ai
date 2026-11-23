"""run_hidden.py: the tasks run with no window, and End still ends them."""

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import run_hidden

PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
SYNCHRONIZE = 0x00100000


def _exited_within(pid, seconds):
    """True once process `pid` has exited (or is gone), waiting up to `seconds`."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "Windows task launcher")
class RunHiddenTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat-hidden-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def bat(self, body, name="task_test.bat"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("@echo off\r\n" + body.replace("\n", "\r\n"))
        return path

    def test_only_an_existing_bat_is_run(self):
        self.assertIsNone(run_hidden.resolve("no_such_task.bat"))
        self.assertIsNone(run_hidden.resolve("run_hidden.py"))
        self.assertEqual(run_hidden.resolve("task_evening.bat"),
                         os.path.join(run_hidden.ROOT, "task_evening.bat"))
        with mock.patch.object(run_hidden, "run") as run:
            self.assertEqual(run_hidden.main([], contained=False), 2)
            self.assertEqual(run_hidden.main(["a.bat", "b.bat"],
                                             contained=False), 2)
            self.assertEqual(run_hidden.main(["x.txt"], contained=False), 2)
        run.assert_not_called()

    def test_the_console_has_no_window(self):
        with mock.patch.object(run_hidden.subprocess, "call",
                               return_value=0) as call:
            run_hidden.run("C:\\x\\task_evening.bat")
        flags = call.call_args.kwargs["creationflags"]
        self.assertTrue(flags & run_hidden.CREATE_NO_WINDOW)
        self.assertEqual(call.call_args.args[0],
                         ["cmd.exe", "/c", "C:\\x\\task_evening.bat"])
        self.assertEqual(call.call_args.kwargs["cwd"], run_hidden.ROOT)

    def test_the_bats_exit_code_is_the_runs(self):
        marker = os.path.join(self.dir, "ran.txt")
        bat = self.bat('echo ran> "%s"\nexit /b 7\n' % marker)
        self.assertEqual(run_hidden.main([bat], contained=False), 7)
        self.assertTrue(os.path.isfile(marker))

    @unittest.skipUnless(os.path.isfile(PYTHONW), "no pythonw beside python")
    def test_ending_the_launcher_ends_the_whole_run(self):
        pidfile = os.path.join(self.dir, "child.pid")
        child = ("import os, time; open(r'%s', 'w').write(str(os.getpid())); "
                 "time.sleep(120)" % pidfile)
        bat = self.bat('"%s" -c "%s"\nexit /b 0\n' % (sys.executable, child))
        launcher = subprocess.Popen(
            [PYTHONW, os.path.join(run_hidden.ROOT, "run_hidden.py"), bat])
        try:
            deadline = time.monotonic() + 30
            while not os.path.isfile(pidfile) and time.monotonic() < deadline:
                time.sleep(0.1)
            time.sleep(0.2)
            with open(pidfile, encoding="utf-8") as handle:
                pid = int(handle.read())
            self.assertFalse(_exited_within(pid, 0.5))   # running under it
            launcher.terminate()                          # what End does
            launcher.wait(10)
            self.assertTrue(_exited_within(pid, 10),
                            "the bat's python outlived its launcher")
        finally:
            if launcher.poll() is None:
                launcher.kill()


if __name__ == "__main__":
    unittest.main()
