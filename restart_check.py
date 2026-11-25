"""Has the code changed under the running panel? (2026-11-25)

The panel imports much of its code only when first asked -- the PDF report
at the first report request -- so a panel started before an update and
asked afterwards runs half old code and half new. On 2026-11-25 every PDF
failed with "module 'fundamental_outlook' has no attribute 'attach_forward'":
stock_report.py was read new, fundamental_outlook.py had been loaded old.
Templates and static files are read on every request, so a stale panel also
serves new pages to old Python, and a feature looks broken when it is only
not loaded (that has cost debugging sessions before: app.port_owner).

So the panel records its Python files when it starts -- the project's own
modules and packages, not tests or example scripts -- and the page asks,
with its status poll, whether any has changed since. A file only touched
(a checkout that rewrote it unchanged) is compared by content and does not
count. The page also learns the newest template or static file, so it can
say "reload this page" when only the page itself is old.

    restart_check.mark_started()   # once, when the panel starts
    restart_check.state()          # {"restart_needed", "files", ...}
"""

import hashlib
import os
import threading
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))

# Folders that hold no code the panel runs.
SKIP_DIRS = {"tests", "examples", "__pycache__", "data", "logs", "reports",
             "backtest_output", "graphify-out", "node_modules", "venv"}

ASSET_DIRS = ("static", "templates")

CHECK_TTL = 15.0
SHOWN_FILES = 5

_lock = threading.Lock()
_start = {"at": None, "files": None, "root": ROOT}
_cache = {"at": 0.0, "value": None}


def _digest(path):
    hasher = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            hasher.update(block)
    return hasher.hexdigest()


def python_files(root=ROOT):
    """{relative path: (mtime_ns, size)} for the panel's own Python: the
    root's modules and every package below it (a folder with __init__.py),
    tests and examples aside."""

    out = {}

    def walk(folder):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if entry.is_file() and name.endswith(".py"):
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                out[os.path.relpath(entry.path, root)] = (stat.st_mtime_ns,
                                                          stat.st_size)
            elif (entry.is_dir() and name not in SKIP_DIRS
                  and not name.startswith(".")
                  and os.path.exists(os.path.join(entry.path, "__init__.py"))):
                walk(entry.path)

    walk(root)
    return out


def mark_started(root=ROOT):
    """Record the code as it is now: the panel's starting point."""

    files = {}
    for path, (mtime, size) in python_files(root).items():
        try:
            files[path] = (mtime, size, _digest(os.path.join(root, path)))
        except OSError:
            continue
    with _lock:
        _start.update(at=time.time(), files=files, root=root)
        _cache.update(at=0.0, value=None)


def changed():
    """[(path, mtime_ns, how)] for every file that differs from the start,
    newest first; how is "changed", "added" or "removed"."""

    with _lock:
        start, root = _start["files"], _start["root"]
    if start is None:
        return []
    now = python_files(root)
    out = []
    for path, (mtime, size) in now.items():
        before = start.get(path)
        if before is None:
            out.append((path, mtime, "added"))
            continue
        if (mtime, size) == before[:2]:
            continue
        try:
            if _digest(os.path.join(root, path)) == before[2]:
                continue                    # touched, not changed
        except OSError:
            continue
        out.append((path, mtime, "changed"))
    for path, before in start.items():
        if path not in now:
            out.append((path, before[0], "removed"))
    out.sort(key=lambda item: -item[1])
    return out


def assets_stamp(root=ROOT):
    """The newest template or static file's mtime, as a string: what a page
    compares with the one it was loaded under."""

    newest = 0
    for folder in ASSET_DIRS:
        for dirpath, _dirs, files in os.walk(os.path.join(root, folder)):
            for name in files:
                try:
                    newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime_ns)
                except OSError:
                    continue
    return str(newest)


def _iso(ns_or_seconds, ns=True):
    seconds = ns_or_seconds / 1e9 if ns else ns_or_seconds
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def state(force=False):
    """What the page shows: whether the panel must be restarted, and why.

    {"restart_needed": bool, "count": n, "files": [{"path", "at", "how"}],
     "started_at": iso, "assets": stamp}. Cached for CHECK_TTL seconds: the
    page polls every 20 s, and a check stats ~250 files.
    """

    with _lock:
        cached, at = _cache["value"], _cache["at"]
    if not force and cached is not None and time.monotonic() - at < CHECK_TTL:
        return cached
    diff = changed()
    with _lock:
        started = _start["at"]
        root = _start["root"]
    value = {
        "restart_needed": bool(diff),
        "count": len(diff),
        "files": [{"path": path.replace(os.sep, "/"), "at": _iso(mtime), "how": how}
                  for path, mtime, how in diff[:SHOWN_FILES]],
        "started_at": _iso(started, ns=False) if started else None,
        "assets": assets_stamp(root),
    }
    with _lock:
        _cache.update(at=time.monotonic(), value=value)
    return value
