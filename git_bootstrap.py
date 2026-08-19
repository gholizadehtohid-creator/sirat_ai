"""Week 1, item 1: put this repository under version control.

WHY THIS IS A SCRIPT AND NOT A COMMAND SOMEONE RUNS BY HAND
-----------------------------------------------------------
Fifteen experiments were produced by code that cannot be recovered. Every
provenance block in this repository reads "UNAVAILABLE" and every result is
therefore a number rather than a finding. That is not a documentation problem,
it is the reason `Manager.decide()` refuses to PROMOTE anything today.

The remedy has to be repeatable and it has to be checkable, so it is code:

    python git_bootstrap.py --check      report, change nothing
    python git_bootstrap.py              init, add, commit, tag v2.0-archive

GIT IS NOT INSTALLED ON THIS MACHINE
------------------------------------
Verified: `git` is absent from PATH and there is no `.git` directory. This
script therefore ends in one of two ways and never in between:

    git present  -> the repository exists and provenance starts recording real
                    commits, with no other change anywhere in the codebase
    git absent   -> exit code 2, the install instruction, and nothing pretended

It does not install git. Installing software on someone's machine is not a
thing a research script gets to decide.

WHY THE TAG MATTERS AS MUCH AS THE COMMIT
------------------------------------------
The Alpha Core regression fixture asserts that this platform can still re-derive
E13/E14/E15's numbers. Those numbers were produced by the code as it stood on
2026-08-18. Without a tag naming that state, "reproduce E14" has no referent --
the fixture would be comparing against a moving target and its green would mean
nothing.
"""

import os
import subprocess
import sys


ROOT = os.path.dirname(os.path.abspath(__file__))

ARCHIVE_TAG = "v2.0-archive"

FIRST_COMMIT = """Phase 1 archive: fifteen experiments, complete causal account.

The state of SIRAT-AI at the close of Phase 1, committed as the referent for
the Alpha Core regression fixture.

  Exposure engine      RETIRED    E4  -- 1 of 8 gates out of sample
  Alpha Core v1        REJECTED   E11 -- 3.3rd percentile, recoverable -10.8%
  Signal mechanism     ARTIFACT   E14 -- 3.03x queue exposure
  Execution rescue     FAILED     E15 -- the filter removes artifact and alpha

The forward holdout was never burned. Alpha Core was never modified.
"""


def have_git():
    try:
        out = subprocess.run(["git", "--version"], cwd=ROOT,
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _git(*args, check=True):
    out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                         text=True, timeout=300)
    if check and out.returncode != 0:
        raise RuntimeError("git %s failed: %s"
                           % (" ".join(args), out.stderr.strip()))
    return out


def state():
    """What is true right now. Never raises."""

    version = have_git()

    report = {
        "git_installed": version is not None,
        "git_version": version,
        "repository": os.path.isdir(os.path.join(ROOT, ".git")),
        "gitignore": os.path.exists(os.path.join(ROOT, ".gitignore")),
        "commit": None,
        "tagged": False,
        "reproducible": False,
    }

    if not report["git_installed"] or not report["repository"]:
        return report

    head = _git("rev-parse", "HEAD", check=False)

    if head.returncode == 0:
        report["commit"] = head.stdout.strip()

    tags = _git("tag", "--list", ARCHIVE_TAG, check=False)
    report["tagged"] = bool(tags.returncode == 0 and tags.stdout.strip())

    dirty = _git("status", "--porcelain", check=False)
    report["dirty"] = bool(dirty.returncode == 0 and dirty.stdout.strip())

    report["reproducible"] = bool(report["commit"] and not report["dirty"])

    return report


INSTALL_HELP = """
git is not installed on this machine.

  winget install --id Git.Git -e --source winget

or download from https://git-scm.com/download/win, then reopen the terminal
(the installer edits PATH and an already-open shell will not see it) and run:

  python git_bootstrap.py

UNTIL THEN, and this is deliberate rather than a workaround:

  * every provenance record carries git_commit = "UNAVAILABLE"
  * every experiment is stored with reproducible = 0
  * Manager.decide() REFUSES a PROMOTE decision

Research can proceed. Promotion cannot. Nothing is silently degraded.
"""


def bootstrap(verbose=True):
    """init, configure, commit, tag. Idempotent -- safe to re-run."""

    def say(message):
        if verbose:
            print(message)

    version = have_git()

    if version is None:
        say(INSTALL_HELP)
        return 2

    say("git found: %s" % version)

    if not os.path.exists(os.path.join(ROOT, ".gitignore")):
        say("FAIL: .gitignore is missing. Refusing to commit -- the first "
            "`git add .` would put a 424 MB database in the history "
            "permanently.")
        return 3

    if not os.path.isdir(os.path.join(ROOT, ".git")):
        _git("init")
        say("initialised repository")
    else:
        say("repository already exists")

    # Only set identity if the machine has none. Overwriting a configured
    # identity would silently misattribute someone else's commits.
    if _git("config", "user.email", check=False).returncode != 0:
        _git("config", "user.email", "farnamdrilling@gmail.com")
        _git("config", "user.name", "SIRAT-AI Research")
        say("set repository-local commit identity")

    _git("add", "-A")

    staged = _git("diff", "--cached", "--name-only", check=False)

    if staged.stdout.strip():
        _git("commit", "-m", FIRST_COMMIT)
        say("committed %d paths" % len(staged.stdout.strip().splitlines()))
    else:
        say("nothing to commit; tree already clean")

    existing = _git("tag", "--list", ARCHIVE_TAG, check=False)

    if not existing.stdout.strip():
        _git("tag", "-a", ARCHIVE_TAG, "-m",
             "Phase 1 archive -- the referent for the Alpha Core fixture")
        say("tagged %s" % ARCHIVE_TAG)
    else:
        say("tag %s already present -- left alone" % ARCHIVE_TAG)

    now = state()

    say("")
    say("HEAD          %s" % now["commit"])
    say("reproducible  %s" % now["reproducible"])

    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if "--check" in argv:
        now = state()
        for key in sorted(now):
            print("  %-16s %s" % (key, now[key]))
        return 0 if now["reproducible"] else 1

    return bootstrap()


if __name__ == "__main__":
    sys.exit(main())
