"""Test package for SIRAT-AI.

WHY THIS FILE EXISTS
--------------------
Without it `tests` is a namespace package, and `unittest.discover(start_dir,
top_level_dir=ROOT)` refuses a start directory it cannot import -- which is why
the suite previously had to be run as `python -m unittest discover -s tests`
with no top-level directory, importing each file as a top-level module.

That worked, and it had one real cost: a test that imported a helper from a
sibling (`from tests.test_experiment_gates import panel`) got a SECOND copy of
that module rather than the one already loaded. Two copies of a module with
module-level state is a class of test bug that is very hard to see and very easy
to write.

With this file, `ci.py` discovers the suite from the project root, every test
module is imported once under one name, and both invocations still work:

    python ci.py
    python -m unittest discover -s tests -t .
"""


# ---- the live stores are read-only to the suite (2026-11-11) -----------------
# The databases live beside the code, so a test that forgets to patch a path
# writes to the user's real data whenever the suite runs from the live tree.
# That happened: TestCooldownEscalation rewrote the live "codal:cooldown" row on
# every CI run, which also erases a cooldown Codal imposed. From here on, while
# the suite runs, a connection to one of the tree's own stores opens it
# read-only, so such a test fails loudly instead; a store that does not exist is
# redirected to a temporary file rather than created in the tree. Every other
# path -- the suite's own temporary databases -- is untouched.

import atexit as _atexit
import os as _os
import shutil as _shutil
import sqlite3 as _sqlite3
import tempfile as _tempfile
from urllib.parse import quote as _quote, unquote as _unquote

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_STORES = {_os.path.normcase(_os.path.join(_ROOT, name)) for name in
           ("sirat_market.db", "sirat_fundamentals.db", "sirat_research.db")}
_DATA = _os.path.normcase(_os.path.join(_ROOT, "data")) + _os.sep
_SCRATCH = _tempfile.mkdtemp(prefix="sirat-tests-")
_atexit.register(_shutil.rmtree, _SCRATCH, True)
_real_connect = getattr(_sqlite3.connect, "_sirat_real", _sqlite3.connect)


def _live_store(path):
    """True if `path` is one of this tree's own databases."""

    full = _os.path.normcase(_os.path.abspath(path))
    return full in _STORES or (full.startswith(_DATA) and full.endswith(".db"))


def _guarded_connect(database, *args, **kwargs):
    uri = kwargs.get("uri", False)
    target = database
    if isinstance(target, (bytes, _os.PathLike)):
        target = _os.fsdecode(target)
    if not isinstance(target, str) or target == ":memory:" or not target:
        return _real_connect(database, *args, **kwargs)

    if uri and target.startswith("file:"):
        path, _sep, query = target[5:].partition("?")
        if path and _live_store(_unquote(path)) and "mode=ro" not in query:
            query = "&".join(p for p in query.split("&")
                             if p and not p.startswith("mode=")) or ""
            target = "file:%s?%s" % (path, ("mode=ro&" + query).rstrip("&"))
            return _real_connect(target, *args, **kwargs)
        return _real_connect(database, *args, **kwargs)

    if not _live_store(target):
        return _real_connect(database, *args, **kwargs)

    if not _os.path.exists(target):
        stray = _os.path.join(_SCRATCH, _os.path.basename(target))
        return _real_connect(stray, *args, **kwargs)

    kwargs["uri"] = True
    readonly = "file:%s?mode=ro" % _quote(
        _os.path.abspath(target).replace("\\", "/"), safe="/:")
    return _real_connect(readonly, *args, **kwargs)


_guarded_connect._sirat_real = _real_connect
_sqlite3.connect = _guarded_connect
