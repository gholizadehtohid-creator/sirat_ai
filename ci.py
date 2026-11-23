"""The build. One command, and it is the one that decides whether work ships.

    python ci.py              the fast build -- unit suite + fixture tier 1
    python ci.py --full       adds fixture tier 2 (rebuilds the panel from the
                              424 MB market database)
    python ci.py --json       machine-readable, for a runner

WHAT A RED BUILD MEANS HERE
----------------------------
Three different things, and the report says which:

  TESTS       something in the platform broke
  FIXTURE     the platform can no longer rediscover Alpha Core's failures, and
              is therefore not entitled to judge anything else
  GOVERNANCE  the record itself is in a state that should not ship -- a
              universe snapshot months overdue, or a KPI showing that
              rejections have drifted back to the expensive stages

The third is a warning rather than a failure, and that distinction is
deliberate. A build that goes red because the KPI slipped would get the KPI
removed. A build that reports it every time gets it looked at.

REPRODUCIBILITY IS REPORTED, NOT ENFORCED, AND ONLY FOR NOW
------------------------------------------------------------
git is not installed on this machine, so every build currently reports
`reproducible: False`. Failing the build on that would mean nothing could ever
be built until someone installs software, which is not this script's decision
to force. What IS enforced, in `Manager.decide()`, is that nothing can be
PROMOTED while it holds. Research proceeds; capital does not move.
"""

import argparse
import json
import os
import sys
import time
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run_tests(verbosity=1):
    """The whole unit suite, discovered the way the project already runs it."""

    loader = unittest.TestLoader()

    suite = loader.discover(start_dir=os.path.join(ROOT, "tests"),
                            top_level_dir=ROOT)

    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stdout)

    started = time.time()

    result = runner.run(suite)

    return {
        "stage": "TESTS",
        "run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "seconds": round(time.time() - started, 2),
        "passed": result.wasSuccessful(),
        "detail": [str(t) for t, _ in result.failures + result.errors],
    }


def run_fixture(full=False):
    """Alpha Core must still fail, for all the reasons it failed before."""

    from sirat.fixtures import alpha_core

    started = time.time()

    try:
        report = alpha_core.run(full=full, verbose=True)
    except SystemExit as exc:
        return {"stage": "FIXTURE", "passed": False, "seconds": 0.0,
                "detail": [str(exc)]}

    return {
        "stage": "FIXTURE",
        "tier": report["tier"],
        "checks": len(report["checks"]),
        "failed": report["failed"],
        "seconds": round(time.time() - started, 2),
        "passed": report["passed"],
        "measurements": report["measurements"],
    }


# The seven preregistrations that predate the coverage check below. Their
# retroactive registration is written and rehearsed
# (examples/register_retroactive_e26_e33.py --dry-run: ledger 17 -> 24, chain
# intact), but appending to the permanent ledger is the owner's step and was
# not taken on 2026-11-23. Until it is, these seven WARN on every build, by
# name; any OTHER preregistration outside the record fails it. Run the script
# with --apply, then empty this tuple: a name left here after its row exists
# is only noise, and a name added here to quiet a new experiment defeats the
# check.
PENDING_RETROACTIVE = (
    "E26_preregistration.json", "E27_preregistration.json",
    "E28_preregistration.json", "E30_preregistration.json",
    "E31_preregistration.json", "E32_preregistration.json",
    "E33_preregistration.json",
)


def preregistration_coverage(directory=None, registered=None):
    """Preregistration files with no experiment behind them in the record.

    WHY (2026-11-23): E26-E33 were preregistered as
    backtest_output/E??_preregistration.json, run as standalone scripts and
    never registered with the Experiment Manager. For three weeks the only two
    "accepted" results in the repository sat outside the ledger while this
    build stayed green, because it verified the chain it had and never asked
    what was missing from it. A preregistration file is a claim that an
    experiment exists; this checks that the record knows it does.

    Matching is on the `experiment_id` the file itself states. A file that
    cannot be read, or names no experiment, counts as outside: the check must
    not be passable by writing a file it cannot parse.

    `registered` is injectable so the test needs no research database.
    """

    import re

    directory = directory or os.path.join(ROOT, "backtest_output")

    if registered is None:
        from sirat.experiments import manager

        registered = {row["experiment_id"] for row in manager.registry()}

    try:
        names = sorted(n for n in os.listdir(directory)
                       if re.match(r"E\d+.*_preregistration\.json$", n))
    except OSError:
        names = []

    outside = []

    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                experiment_id = json.load(fh).get("experiment_id")
        except (OSError, ValueError, AttributeError):
            outside.append({"file": name, "experiment_id": None,
                            "reason": "unreadable, or not a JSON object"})
            continue

        if not experiment_id:
            outside.append({"file": name, "experiment_id": None,
                            "reason": "states no experiment_id"})
        elif experiment_id not in registered:
            outside.append({"file": name, "experiment_id": experiment_id,
                            "reason": "no such experiment in the record"})

    return {"files": len(names), "outside": outside}


def run_governance():
    """The state of the record. Reported; two conditions are fatal."""

    from sirat.experiments import ledger, manager, universe

    report = manager.kpi()

    snapshot = universe.due()

    chain = ledger.verify()

    coverage = preregistration_coverage()

    warnings = []

    fatal = []

    if not chain["intact"]:
        warnings.append(
            "THE RESEARCH LEDGER IS BROKEN at entry %s (%s): %s"
            % (chain.get("broken_at"), chain.get("experiment_id"),
               chain.get("reason")))
        fatal.append("ledger broken at entry %s" % chain.get("broken_at"))

    if coverage["outside"]:
        warnings.append(
            "%d OF %d PREREGISTRATION FILES ARE OUTSIDE THE RECORD: %s. An "
            "experiment the Experiment Manager has never heard of has no "
            "frozen thresholds, no provenance and no ledger entry -- "
            "register it before anything is concluded from it."
            % (len(coverage["outside"]), coverage["files"],
               "; ".join("%s (%s)" % (o["file"], o["reason"])
                         for o in coverage["outside"])))
        pending = [o["file"] for o in coverage["outside"]
                   if o["file"] in PENDING_RETROACTIVE]
        unknown = [o["file"] for o in coverage["outside"]
                   if o["file"] not in PENDING_RETROACTIVE]
        if pending:
            warnings.append(
                "%d of them await the owner's retroactive registration "
                "(python examples/register_retroactive_e26_e33.py --apply) "
                "and do not fail the build: %s"
                % (len(pending), ", ".join(pending)))
        if unknown:
            fatal.append("outside the record: %s" % ", ".join(unknown))

    if snapshot["due"]:
        warnings.append(
            "universe snapshot overdue (%s). Survivorship is the one defect "
            "only elapsed time can fix -- every week of delay is a week "
            "permanently unrecoverable."
            % snapshot["reason"])

    if not report["reproducibility"]["reproducible"]:
        warnings.append(
            "results are not reproducible: %s. PROMOTE decisions are blocked "
            "until this is fixed." % report["reproducibility"]["reason"])

    if report["meets_target"] is False:
        warnings.append(
            "only %.0f%% of rejections happened at stages 1-3, against a %.0f%% "
            "target. Rejections are drifting back toward the expensive stages, "
            "which is what a backtester does."
            % (report["rate"] * 100, report["target"] * 100))

    return {
        "stage": "GOVERNANCE",
        "kpi": report,
        "snapshot": snapshot,
        "ledger": chain,
        "preregistrations": coverage,
        "warnings": warnings,
        # What the last line of the build prints beside a FAIL.
        "detail": fatal,
        # The KPI and the snapshot age are management signals, and a management
        # signal wired to a red build becomes a deleted signal -- so they warn.
        #
        # A BROKEN LEDGER IS DIFFERENT AND DOES FAIL THE BUILD. It is not a
        # signal about how research is going; it means the record of what was
        # decided has been altered, and nothing built on top of it can be
        # trusted until that is explained.
        #
        # SO DOES AN EXPERIMENT OUTSIDE THE RECORD (2026-11-23). It is the same
        # kind of fault seen from the other side: not a record that was
        # altered, but a result that was never in it. As a warning it would
        # have been printed on every build since E26 and read on none.
        "passed": not fatal,
    }


def _print_governance(report):
    print()
    print("=" * 74)
    print("GOVERNANCE")
    print("=" * 74)

    kpi = report["kpi"]

    if kpi["rejections"]:
        print("  cheap rejection rate   %.0f%% of %d  (target %.0f%%)"
              % (kpi["rate"] * 100, kpi["rejections"], kpi["target"] * 100))
    else:
        print("  cheap rejection rate   undefined -- no rejections recorded")

    print("  reproducible           %s"
          % kpi["reproducibility"]["reproducible"])
    print("  universe snapshot      %s"
          % (report["snapshot"]["latest"] or "NEVER TAKEN"))
    print("  research ledger        %d entries, chain %s"
          % (report["ledger"]["entries"],
             "intact" if report["ledger"]["intact"] else "BROKEN"))

    coverage = report.get("preregistrations")

    if coverage is not None:
        print("  preregistrations       %d files, %s"
              % (coverage["files"],
                 "all in the record" if not coverage["outside"]
                 else "%d OUTSIDE THE RECORD" % len(coverage["outside"])))

    for warning in report["warnings"]:
        print("\n  WARNING: %s" % warning)

    print("=" * 74)


def main(argv=None):
    parser = argparse.ArgumentParser(description="The SIRAT-AI build.")
    parser.add_argument("--full", action="store_true",
                        help="rebuild the fixture panel from sirat_market.db")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-tests", action="store_true",
                        help="fixture and governance only")
    parser.add_argument("-v", "--verbose", action="count", default=1)

    args = parser.parse_args(argv)

    stages = []

    if not args.skip_tests:
        if not args.json:
            print("=" * 74)
            print("UNIT SUITE")
            print("=" * 74)
        stages.append(run_tests(verbosity=0 if args.json else args.verbose))

    stages.append(run_fixture(full=args.full))

    governance = run_governance()

    stages.append(governance)

    passed = all(s["passed"] for s in stages)

    if args.json:
        print(json.dumps({"passed": passed, "stages": stages}, indent=2,
                         default=str))
        return 0 if passed else 1

    _print_governance(governance)

    print()
    print("=" * 74)

    for stage in stages:
        print("  [%s] %-12s %s"
              % ("PASS" if stage["passed"] else "FAIL", stage["stage"],
                 ", ".join(str(x) for x in (stage.get("failed")
                                            or stage.get("detail") or []))[:60]))

    print("=" * 74)
    print("  BUILD %s" % ("GREEN" if passed else "RED"))
    print("=" * 74)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
