"""Command line for the research record.

    python -m sirat.experiments.cli status              the platform KPI
    python -m sirat.experiments.cli registry            every experiment
    python -m sirat.experiments.cli show E16-...        one experiment in full
    python -m sirat.experiments.cli register --file p.json
    python -m sirat.experiments.cli snapshot            freeze the universe
    python -m sirat.experiments.cli windows             the holdout ledger

WHY REGISTRATION TAKES A FILE AND NOT FLAGS
--------------------------------------------
Eleven required fields, four of them structured, one of them a hypothesis that
should take a paragraph. A flag-based form of this would be unusable and would
push people toward the shortest thing that passes the parser, which is exactly
the failure the required fields exist to prevent. A JSON file is a thing that
can be written carefully, reviewed by a second person, and committed next to the
result.
"""

import argparse
import json
import sys

from sirat.experiments import manager as mgr
from sirat.experiments import stages, universe


def _print_kpi(report):
    print("=" * 70)
    print("PLATFORM KPI -- at which stage are bad ideas rejected")
    print("=" * 70)

    if report["rejections"] == 0:
        print("  no rejections recorded yet.")
        print("  The KPI is undefined rather than perfect: a platform with no")
        print("  rejections has not been asked a question.")
    else:
        print("  rejections          %d" % report["rejections"])
        print("  at stages 1-3       %d" % report["cheap"])
        print("  cheap rejection     %.1f%%   (target %.0f%%)"
              % (report["rate"] * 100, report["target"] * 100))
        print("  meets target        %s" % report["meets_target"])
        print()
        for stage in stages.ORDER:
            count = report["by_stage"].get(stage, 0)
            marker = "  <- cheap" if stage in stages.CHEAP_STAGES else ""
            print("    stage %d  %-28s %d%s"
                  % (stage, stages.NAMES[stage], count, marker))

    print()

    repro = report["reproducibility"]

    print("  reproducible        %s" % repro["reproducible"])

    if not repro["reproducible"]:
        print("    reason            %s" % repro["reason"])
        print("    remedy            %s" % repro["remedy"])
        print("    blocks            %s" % ", ".join(repro["blocks"]))

    print("=" * 70)


def _print_registry(rows):
    if not rows:
        print("  the research record is empty.")
        return

    print("  %-28s %-10s %-8s %s" % ("experiment", "status", "stage", "hypothesis"))
    print("  " + "-" * 96)

    for row in rows:
        stage = row["rejected_at_stage"]
        print("  %-28s %-10s %-8s %s"
              % (row["experiment_id"][:28],
                 (row["decision"] or row["status"])[:10],
                 ("s%d" % stage) if stage else "-",
                 (row["hypothesis"] or "")[:60]))


def _print_experiment(record):
    print("=" * 74)
    print(record["experiment_id"])
    print("=" * 74)
    print("  hypothesis      %s" % record["hypothesis"])
    print("  strategy        %s" % record["strategy_version"])
    print("  researcher      %s" % record["researcher"])
    print("  registered      %s" % record["registered_at"][:19])
    print("  status          %s" % record["status"])

    print("\n  PROVENANCE")
    for key, value in sorted(record["provenance"].items()):
        print("    %-20s %s" % (key, value))

    prereg = record["preregistration"]
    print("\n  PREREGISTRATION  frozen %s  hash %s"
          % (prereg["frozen_at"][:19], prereg["content_hash"][:16]))
    print("    seed               %s" % prereg["seed"])
    for key in ("parameters", "validation_gates", "expected_metrics",
                "rejection_thresholds"):
        print("    %-18s %s" % (key, json.dumps(prereg[key],
                                                ensure_ascii=False)[:110]))

    print("\n  STAGES")
    if not record["stages"]:
        print("    none run")
    for entry in record["stages"]:
        print("    [%s] %d %-28s %s"
              % (entry["status"], entry["stage"], entry["stage_name"],
                 entry["detail"]))

    decision = record["decision"]
    print("\n  DECISION")
    if decision is None:
        print("    open")
    else:
        print("    %s at stage %s by %s"
              % (decision["decision"], decision["rejected_at_stage"],
                 decision["decided_by"]))
        print("    %s" % decision["rationale"])

    print("=" * 74)


def _register_from_file(path):
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)

    required = ("experiment_id", "hypothesis", "strategy_version",
                "researcher", "notes", "expected_failure",
                "acceptance_criteria", "preregistration")

    missing = [k for k in required if k not in spec]

    if missing:
        raise SystemExit("the proposal is missing: %s" % ", ".join(missing))

    handle = mgr.Manager.register(
        experiment_id=spec["experiment_id"],
        hypothesis=spec["hypothesis"],
        strategy_version=spec["strategy_version"],
        researcher=spec["researcher"],
        notes=spec["notes"],
        expected_failure=spec["expected_failure"],
        acceptance_criteria=spec["acceptance_criteria"],
        preregistration=spec["preregistration"],
        config=spec.get("config"),
        universe_snapshot=spec.get("universe_snapshot"),
    )

    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sirat.experiments.cli",
        description="The research record: registration, stages, decisions.")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="the platform KPI and reproducibility state")
    sub.add_parser("registry", help="every registered experiment")
    sub.add_parser("windows", help="the holdout ledger")

    show = sub.add_parser("show", help="one experiment in full")
    show.add_argument("experiment_id")
    show.add_argument("--json", action="store_true")

    register = sub.add_parser("register", help="register from a proposal file")
    register.add_argument("--file", required=True)

    snapshot = sub.add_parser("snapshot", help="freeze the market universe")
    snapshot.add_argument("--if-due", action="store_true")

    args = parser.parse_args(argv)

    if args.command in (None, "status"):
        _print_kpi(mgr.kpi())
        return 0

    if args.command == "registry":
        _print_registry(mgr.registry())
        return 0

    if args.command == "show":
        record = mgr.Manager.load(args.experiment_id).record()
        if args.json:
            print(json.dumps(record, ensure_ascii=False, indent=2,
                             default=str))
        else:
            _print_experiment(record)
        return 0

    if args.command == "register":
        handle = _register_from_file(args.file)
        print("registered %s" % handle.experiment_id)
        print("  prereg hash   %s" % handle.record()["provenance"]["prereg_hash"])
        print("  reproducible  %s"
              % handle.record()["provenance"]["reproducible"])
        print("  next stage    %s" % handle.next_stage())
        return 0

    if args.command == "snapshot":
        result = (universe.run_if_due() if args.if_due
                  else dict(universe.capture(), due=True))
        payload = result.get("captured") if args.if_due else result
        if payload is None:
            print("not due: %s" % result.get("reason"))
            return 0
        print("%s %s -- %d instruments"
              % ("captured" if payload.get("created") else "unchanged",
                 payload["snapshot_id"], payload["instrument_count"]))
        return 0

    if args.command == "windows":
        from sirat.experiments import store
        store.initialise(None)
        conn = store.connect(None)
        try:
            rows = conn.execute(
                "SELECT * FROM windows_touched ORDER BY touched_at").fetchall()
        finally:
            conn.close()
        if not rows:
            print("  no window has been evaluated. The holdout is intact.")
        for row in rows:
            print("  %-28s %s .. %s  %-12s %s"
                  % (row["experiment_id"], row["window_start"],
                     row["window_end"], row["purpose"],
                     row["touched_at"][:19]))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except mgr.GovernanceError as exc:
        print("REFUSED: %s" % exc)
        sys.exit(2)
