"""Keep the scheduled tasks' logs bounded: one current file, one previous.

    python log_rotate.py logs\\task_evening.log [more.log ...]

The task .bat files append with `>>`, so nothing ever trimmed them --
sirat.log has a RotatingFileHandler, these had nothing (task_evening.log
reached 530 KB, task_backfill.log grows ~150 KB a day). Each task calls
this on its own log before its first write: past MAX_BYTES the file
becomes <name>.1 (replacing the previous .1) and the task starts a new one.

Never fails a task. A log another process still holds open cannot be
renamed on Windows; it is left for the next run.
"""

import os
import sys

MAX_BYTES = 2 * 1024 * 1024


def rotate(path, max_bytes=MAX_BYTES):
    """Rename `path` to `path`.1 if it is over `max_bytes`. True if rotated."""

    try:
        if os.path.getsize(path) <= max_bytes:
            return False
        os.replace(path, path + ".1")
        return True
    except OSError:
        return False


def main(argv=None):
    for path in (sys.argv[1:] if argv is None else argv):
        if rotate(path):
            print("log_rotate: %s -> %s.1" % (path, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
