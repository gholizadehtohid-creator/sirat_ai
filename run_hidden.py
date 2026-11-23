"""Run a task_*.bat with no console window. What Task Scheduler starts.

    pythonw.exe C:\\SIRAT-AI\\run_hidden.py task_evening.bat

WHY (2026-11-23, at the user's request)
---------------------------------------
The tasks were .bat actions, so Task Scheduler opened each in a visible
console window, and a closed window kills its run with 0xC000013A. It kept
happening: the evening run at 19:05 on 2026-11-11, and at 12:13 on
2026-11-23 the backfill, the session watch and the feed watch together,
seconds after the laptop woke and their windows appeared at once.

pythonw has no console. The bat runs in a console created without a window
(CREATE_NO_WINDOW), so there is nothing to close; its exit code -- the
evening task's count of failed steps -- is passed through as the task's
result. The bats write their own logs, so nothing visible is lost.

STOPPING A RUN
--------------
Task Scheduler -> the task -> End, or the task's time limit. Either ends
this process, and with it the whole run: this process puts itself in a job
object that kills everything in it when the last handle closes, and the
bat, its python steps and anything they start inherit the job.
"""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))

CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def contain():
    """Put this process in a kill-on-close job; children inherit it.

    Returns the job handle (keep it: closing it ends the run), or None if
    the job could not be set up -- the run then goes ahead uncontained,
    which is how it ran before.
    """

    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE,
                                                  wintypes.HANDLE)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (kernel32.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits), ctypes.sizeof(limits))
            and kernel32.AssignProcessToJobObject(
                job, kernel32.GetCurrentProcess())):
        return job
    kernel32.CloseHandle(job)
    return None


def resolve(name):
    """The bat to run: a .bat that exists, relative to the project."""

    path = os.path.abspath(os.path.join(ROOT, name))
    if not path.lower().endswith(".bat") or not os.path.isfile(path):
        return None
    return path


def run(bat):
    """Run `bat` in a windowless console; its exit code."""

    # pythonw has no standard handles to pass on; the bats redirect what
    # matters to their logs, and the rest went to a window nobody kept.
    return subprocess.call(
        ["cmd.exe", "/c", bat], cwd=ROOT, creationflags=CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def main(argv=None, contained=True):
    argv = sys.argv[1:] if argv is None else argv
    bat = resolve(argv[0]) if len(argv) == 1 else None
    if bat is None:
        return 2
    job = contain() if contained else None   # noqa: F841 -- held to the end
    return run(bat)


if __name__ == "__main__":
    sys.exit(main())
