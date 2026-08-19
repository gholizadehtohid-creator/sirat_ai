"""CI entry point. The build is red unless governance holds.

WHAT CI IS FOR HERE
-------------------
Not "do the tests pass". A research platform can have a green test suite and
still be unable to tell a good idea from a bad one. These checks ask the
question that actually matters: does the platform still reject what it already
knows to be false, and can it still prove where its numbers came from?

    1  test suite                every existing test
    2  Alpha Core fixture        the known failure must be rediscovered
    3  reproducibility           git, code hash, dataset fingerprint
    4  universe scheduler        is the survivorship clock still running
    5  platform KPI              at which stage are ideas rejected

Checks 3 and 4 are reported as WARN rather than FAIL when they are unmet: they
describe the environment, not a regression, and a red build for a missing cron
teaches people to ignore red builds. Check 2 is always fatal.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

FAIL, WARN, OK = "FAIL", "WARN", "OK"


def _line(status, name, detail=""):
    print("  [%-4s] %-28s %s" % (status, name, detail))


def check_tests():
    out = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests",
         "-p", "test_*.py"],
        cwd=ROOT, capture_output=True, text=True)

    tail = (out.stderr or out.stdout).strip().splitlines()

    summary = next((l for l in reversed(tail) if l.startswith("Ran ")), "?")

    if out.returncode == 0:
        _line(OK, "test suite", summary)
        return True

    _line(FAIL, "test suite", summary)
    for line in tail[-12:]:
        print("         %s" % line)
    return False


def check_alpha_core():
    from sirat.fixtures import alpha_core_regression as fixture

    try:
        ok, results = fixture.run()
    except fixture.FixtureUnavailable as exc:
        _line(FAIL, "alpha core fixture", str(exc))
        return False

    failed = [r for r in results if r["status"] != "PASS"]

    if ok:
        _line(OK, "alpha core fixture",
              "%d checks, every known failure rediscovered" % len(results))
        return True

    _line(FAIL, "alpha core fixture", "%d of %d checks failed"
          % (len(failed), len(results)))

    for row in failed:
        print("         %-24s observed %s vs %s"
              % (row["check"], row.get("observed"), row.get("reference")))
        if row.get("direction") == "IMPROVED":
            print("         ^ moved in the IMPROVED direction. Alpha Core has")
            print("           not got better; a constraint stopped being")
            print("           measured. Explain before merging.")
        if row.get("note"):
            print("         %s" % row["note"])

    return False


def check_reproducibility():
    from sirat.experiments import provenance

    report = provenance.reproducibility_report()

    if report["reproducible"]:
        _line(OK, "reproducibility", "commit %s" % report["commit"][:12])
        return True

    _line(WARN, "reproducibility", report["reason"])
    print("         remedy: %s" % report["remedy"])
    print("         blocks: %s" % ", ".join(report["blocks"]))
    return True          # environment, not regression


def check_universe_scheduler():
    from sirat.universe import verify_scheduler

    try:
        health = verify_scheduler()
    except Exception as exc:                       # noqa: BLE001
        _line(WARN, "universe scheduler", "unavailable: %s" % exc)
        return True

    if health["healthy"]:
        _line(OK, "universe scheduler",
              "%s, %d instruments, %d days old"
              % (health["snapshot_id"], health["instruments"],
                 health["days_since"]))
        return True

    _line(WARN, "universe scheduler", health["reason"])
    print("         every week not snapshotted is permanently unrecoverable")
    return True


def check_kpi():
    from sirat.experiments import kpi as platform_kpi

    try:
        kpi = platform_kpi()
    except Exception as exc:                       # noqa: BLE001
        _line(WARN, "platform KPI", "unavailable: %s" % exc)
        return True

    if kpi["rate"] is None:
        _line(WARN, "platform KPI", "no rejections recorded yet")
        return True

    status = OK if kpi["meets_target"] else WARN

    _line(status, "platform KPI",
          "%.0f%% of %d rejections at stages 1-3 (target %.0f%%)"
          % (kpi["rate"] * 100, kpi["rejections"], kpi["target"] * 100))

    if not kpi["meets_target"]:
        print("         rejections are happening too late: %s"
              % kpi["by_stage"])

    return True


def main():
    print("=" * 74)
    print("SIRAT-AI CI — governance gate")
    print("=" * 74)

    results = [
        check_tests(),
        check_alpha_core(),
        check_reproducibility(),
        check_universe_scheduler(),
        check_kpi(),
    ]

    ok = all(results)

    print("=" * 74)
    print("BUILD: %s" % ("GREEN" if ok else "RED"))
    print("=" * 74)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
