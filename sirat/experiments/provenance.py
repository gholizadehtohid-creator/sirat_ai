"""Immutable provenance for one experiment.

WHAT A RESULT MUST CARRY TO BE A RESULT
---------------------------------------
git commit · code hash · config hash · dataset snapshot · universe snapshot ·
execution timestamp. Anything missing one of these is a number, not a finding.

GIT IS NOT INSTALLED ON THIS MACHINE, AND THAT IS RECORDED RATHER THAN HIDDEN
----------------------------------------------------------------------------
`git` is absent from PATH and there is no repository. Every provenance record
produced here therefore carries git_commit = "UNAVAILABLE" and
reproducible = False, and `Manager.decide()` refuses to PROMOTE a
non-reproducible experiment. The platform degrades loudly: research can proceed,
promotion cannot. The moment git exists the same code starts recording real
commits with no further change.

WHY CODE HASH AS WELL AS GIT COMMIT
-----------------------------------
A commit identifies what was committed. It says nothing about the working tree
that actually ran, and fifteen experiments in this project were run from dirty
trees. The code hash is taken over the source files as they were on disk at
execution, so a result produced from uncommitted edits cannot masquerade as the
commit it sat on top of.
"""

import hashlib
import json
import os
import subprocess
import sys

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

UNAVAILABLE = "UNAVAILABLE"

# Source that materially decides a result. Notebooks, outputs and caches are
# excluded on purpose: hashing them would make every run look like a code
# change and the hash would stop meaning anything.
CODE_ROOTS = ("sirat", "backtesting")

CODE_FILES = ("config.py", "backtest_config.py", "research_alpha_ic.py",
              "portfolio_engine.py", "factor_engine.py", "performance.py")


def git_commit():
    """The HEAD commit, or UNAVAILABLE. Never raises."""

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return UNAVAILABLE

    if out.returncode != 0:
        return UNAVAILABLE

    return out.stdout.strip() or UNAVAILABLE


def git_dirty():
    """True if the working tree differs from HEAD. None when git is absent."""

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None

    if out.returncode != 0:
        return None

    return bool(out.stdout.strip())


def code_hash():
    """SHA256 over the source that decides a result, as it is on disk."""

    digest = hashlib.sha256()

    paths = []

    for folder in CODE_ROOTS:
        base = os.path.join(ROOT, folder)
        if not os.path.isdir(base):
            continue
        for here, _, names in os.walk(base):
            if "__pycache__" in here:
                continue
            paths.extend(os.path.join(here, n) for n in names
                         if n.endswith(".py"))

    paths.extend(os.path.join(ROOT, n) for n in CODE_FILES
                 if os.path.exists(os.path.join(ROOT, n)))

    # Sorted, so the hash describes the content and not the walk order.
    for path in sorted(paths):
        digest.update(os.path.relpath(path, ROOT).replace("\\", "/").encode())
        with open(path, "rb") as handle:
            digest.update(handle.read())

    return digest.hexdigest()


def config_hash(config):
    """Stable hash of a configuration mapping.

    sort_keys, because a dict that serialises in a different order is the same
    configuration and must not read as a different one.
    """

    payload = json.dumps(config, sort_keys=True, ensure_ascii=False,
                         default=str)

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def content_hash(payload):
    """Hash of any JSON-serialisable object. Used to freeze preregistrations."""

    return config_hash(payload)


def dataset_snapshot():
    """Identity of the market data, reusing the PIT layer's own fingerprint.

    `pit.fingerprint()` covers bar count, newest bar, flow rows and filing
    rows. It does NOT cover which instruments exist, which is why the universe
    snapshot is recorded separately rather than folded in here.
    """

    try:
        from backtesting.pit import PointInTimeData
        return PointInTimeData(verbose=False).fingerprint()
    except Exception as exc:                       # noqa: BLE001
        return "%s:%s" % (UNAVAILABLE, type(exc).__name__)


def capture(config=None, universe_snapshot_id=None):
    """The full provenance block for one experiment."""

    commit = git_commit()
    dirty = git_dirty()

    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "code_hash": code_hash(),
        "config_hash": config_hash(config or {}),
        "dataset_snapshot": dataset_snapshot(),
        "universe_snapshot": universe_snapshot_id or UNAVAILABLE,
        "executed_at": pd.Timestamp.now().isoformat(),
        "python": sys.version.split()[0],
        # A result is reproducible only if the code that produced it can be
        # recovered. A dirty tree is as unrecoverable as a missing repository.
        "reproducible": bool(commit != UNAVAILABLE and dirty is False),
    }


def reproducibility_report():
    """Human-readable answer to 'can anything here be re-derived?'"""

    commit = git_commit()
    dirty = git_dirty()

    if commit == UNAVAILABLE:
        return {
            "reproducible": False,
            "reason": "git is not available: no repository, or git is not "
                      "installed on this machine",
            "remedy": "install git, `git init`, commit the research code",
            "blocks": ["PROMOTE decisions", "regression fixture provenance"],
        }

    if dirty:
        return {
            "reproducible": False,
            "reason": "the working tree has uncommitted changes, so the code "
                      "that ran cannot be recovered from the commit",
            "remedy": "commit or stash before registering an experiment",
            "blocks": ["PROMOTE decisions"],
        }

    return {"reproducible": True, "commit": commit}
