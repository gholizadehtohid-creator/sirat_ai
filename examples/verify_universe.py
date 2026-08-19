"""Phase 6 acceptance: prove the universe snapshot system is trustworthy.

FIVE PROPERTIES, EACH DEMONSTRATED RATHER THAN ASSERTED
--------------------------------------------------------
  1. the live snapshot restores to a real instrument list
  2. its payload hashes to the value recorded when it was frozen
  3. its constituents are immutable -- a rewritten payload is detected
  4. an experiment can cite it, and Stage 1 accepts it as the survivorship
     answer only because it verifies
  5. the weekly scheduler verifies restorability, so a corrupt snapshot can
     never keep the schedule quiet

The failure test is the point. Property 5 is the one that was actually broken:
`due()` originally read only a timestamp, so the pre-existing UNIV-20260818 row
-- which does not restore -- reported the universe as fresh and frozen when it
was neither, and the schedule would never have fired again.

This script writes nothing to the live record. The corruption test operates on
a copy.
"""

import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd                                                 # noqa: E402

from sirat.experiments import gates, provenance, stages, store      # noqa: E402
from sirat.experiments import universe                              # noqa: E402


def say(message=""):
    print(message, flush=True)


def rule(title):
    say()
    say("=" * 74)
    say(title)
    say("=" * 74)


def main():
    results = []

    rule("PHASE 6 -- UNIVERSE SNAPSHOT ACCEPTANCE")

    live = universe.latest()

    if live is None:
        say("  no snapshot exists. Run: python -m sirat.experiments.universe "
            "--capture")
        return 1

    snapshot_id = live["snapshot_id"]

    say("  snapshot        %s" % snapshot_id)
    say("  taken at        %s" % live["taken_at"][:19])
    say("  instruments     %d" % live["instrument_count"])
    say("  recorded hash   %s" % live["content_hash"])

    # ---- 1. restore -------------------------------------------------------
    rule("1. THE SNAPSHOT RESTORES")

    frame = universe.restore(snapshot_id)

    say("  restored %d rows, %d columns" % (len(frame), len(frame.columns)))
    say("  columns  %s" % ", ".join(frame.columns))
    say()
    say(frame.head(4).to_string(index=False, max_colwidth=18))

    passed = bool(len(frame) == live["instrument_count"] and len(frame))

    results.append(("snapshot restores to a real instrument list", passed))

    # ---- 2. hash ----------------------------------------------------------
    rule("2. THE PAYLOAD HASHES TO ITS RECORDED VALUE")

    conn = store.connect(None)
    try:
        row = conn.execute(
            "SELECT payload, content_hash FROM universe_snapshots "
            "WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    finally:
        conn.close()

    import json

    recomputed = provenance.content_hash(json.loads(row["payload"]))

    say("  recorded    %s" % row["content_hash"])
    say("  recomputed  %s" % recomputed)
    say("  match       %s" % (recomputed == row["content_hash"]))

    results.append(("payload hash matches the recorded hash",
                    recomputed == row["content_hash"]))

    # ---- 3. immutability, on a COPY --------------------------------------
    rule("3. CONSTITUENTS ARE IMMUTABLE -- the failure test")

    workdir = tempfile.mkdtemp(prefix="sirat_corrupt_")

    copy = os.path.join(workdir, "research.db")

    shutil.copy2(store.DB_PATH, copy)

    say("  working on a copy: %s" % copy)
    say("  removing one constituent from the stored payload ...")

    payload = json.loads(row["payload"])

    tampered = payload[1:]                       # drop one instrument

    conn = sqlite3.connect(copy)
    try:
        conn.execute("UPDATE universe_snapshots SET payload = ? "
                     "WHERE snapshot_id = ?",
                     (json.dumps(tampered, ensure_ascii=False, sort_keys=True,
                                 default=str), snapshot_id))
        conn.commit()
    finally:
        conn.close()

    say("  payload now holds %d instruments instead of %d"
        % (len(tampered), len(payload)))
    say()

    refused = False

    try:
        universe.restore(snapshot_id, path=copy)
        say("  *** RESTORED ANYWAY -- the integrity check is broken ***")
    except universe.UniverseUnavailable as exc:
        refused = True
        say("  REFUSED: %s" % exc)

    results.append(("a rewritten payload is refused, not returned", refused))

    # ---- 4. an experiment can cite it ------------------------------------
    rule("4. RESEARCH CAN CITE THE SNAPSHOT, AND ONLY A VALID ONE COUNTS")

    thresholds = {"max_missing_pct": 5.0, "min_names_per_date": 2,
                  "max_stale_pct": 30.0}

    panel = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-19"] * 60),
        "ins_code": ["S%02d" % i for i in range(60)],
        "score": range(60),
        "fwd_1": [0.1] * 60,
        "stale": [False] * 60,
    })

    with_valid = gates.data_integrity(panel, thresholds,
                                      universe_snapshot=snapshot_id)

    without = gates.data_integrity(panel, thresholds, universe_snapshot=None)

    unavailable = gates.data_integrity(panel, thresholds,
                                       universe_snapshot="UNAVAILABLE")

    say("  citing %s          survivorship = %s"
        % (snapshot_id, with_valid.metrics["survivorship"]))
    say("  citing nothing                     survivorship = %s"
        % without.metrics["survivorship"])
    say("  citing 'UNAVAILABLE'               survivorship = %s"
        % unavailable.metrics["survivorship"])

    results.append((
        "Stage 1 accepts a cited snapshot and refuses a missing one",
        bool(with_valid.metrics["survivorship"]
             and not without.metrics["survivorship"]
             and not unavailable.metrics["survivorship"])))

    # ---- 5. the scheduler verifies restorability -------------------------
    rule("5. THE SCHEDULER VERIFIES, IT DOES NOT TRUST A TIMESTAMP")

    live_status = universe.due()

    say("  live record:")
    for key in ("due", "age_days", "latest", "restorable", "reason"):
        say("    %-12s %s" % (key, live_status.get(key)))

    corrupt_status = universe.due(path=copy)

    say()
    say("  the corrupted copy, whose timestamp is identical:")
    for key in ("due", "age_days", "latest", "restorable", "reason"):
        say("    %-12s %s" % (key, corrupt_status.get(key)))

    correct = bool(corrupt_status["due"]
                   and corrupt_status["restorable"] is False
                   and live_status["restorable"] is True)

    results.append((
        "a corrupt snapshot is treated as absent and the schedule fires",
        correct))

    # ---- 6. duplicate prevention -----------------------------------------
    rule("6. DUPLICATE PREVENTION")

    say("  Capturing the same universe again, twice, on the same day ...")

    copy2 = os.path.join(workdir, "dup.db")

    first = universe.capture(path=copy2)
    second = universe.capture(path=copy2)

    say("    first    %s  created=%s" % (first["snapshot_id"],
                                         first["created"]))
    say("    second   %s  created=%s" % (second["snapshot_id"],
                                         second["created"]))

    rows = universe.history(path=copy2)

    say("    rows on record: %d" % len(rows))
    say()
    say("  An unchanged universe hashes to the same id, so the second call is")
    say("  a no-op. This is what makes a daily schedule affordable: a quiet")
    say("  week costs nothing and still proves the list did not move.")

    no_duplicate = bool(first["created"] and not second["created"]
                        and len(rows) == 1
                        and first["snapshot_id"] == second["snapshot_id"])

    results.append(("an unchanged universe does not create a second snapshot",
                    no_duplicate))

    # ---- 7. missing snapshot blocks promotion ----------------------------
    rule("7. REMOVING THE SNAPSHOT BLOCKS PROMOTION")

    from sirat.experiments import manager as mgr
    from sirat.experiments import provenance as prov

    promo_db = os.path.join(workdir, "promote.db")

    frozen = universe.capture(path=promo_db)

    saved_commit, saved_dirty = prov.git_commit, prov.git_dirty

    prov.git_commit, prov.git_dirty = (lambda: "0" * 40), (lambda: False)

    try:
        handle = mgr.Manager.register(
            experiment_id="E-PROMOTION-EVIDENCE",
            hypothesis="A candidate that passes every gate.",
            strategy_version="v3.0",
            researcher="phase 6 acceptance",
            notes="Exists to show that promotion re-verifies its evidence.",
            expected_failure=["any gate fails"],
            acceptance_criteria={"promotion": "six stages AND live evidence"},
            preregistration={
                "parameters": {"horizon": 20},
                "validation_gates": {"all": "six stages"},
                "expected_metrics": {"ic": 0.03},
                "rejection_thresholds": {"min_ic": 0.02},
                "seed": 20260819,
            },
            config={"demo": True},
            universe_snapshot=frozen["snapshot_id"],
            path=promo_db)
    finally:
        prov.git_commit, prov.git_dirty = saved_commit, saved_dirty

    for stage in stages.ORDER:
        handle.run_stage(stage,
                         {c: True for c in stages.REQUIRED_CHECKS[stage]},
                         detail={"note": "acceptance demonstration"})

    say("  registered %s citing %s"
        % (handle.experiment_id, frozen["snapshot_id"]))
    say("  all six stages PASS")
    say()
    say("  Now the snapshot it cited is destroyed ...")

    conn = sqlite3.connect(promo_db)
    try:
        conn.executescript("PRAGMA writable_schema = ON;")
        conn.execute("UPDATE universe_snapshots SET payload = '[]' "
                     "WHERE snapshot_id = ?", (frozen["snapshot_id"],))
        conn.commit()
    finally:
        conn.close()

    blocked = False

    try:
        handle.decide(mgr.PROMOTE, "every gate passed",
                      "phase 6 acceptance", regression_check=lambda: None)
        say("  *** PROMOTED ANYWAY -- the evidence check is broken ***")
    except mgr.EvidenceMissing as exc:
        blocked = True
        say("  REFUSED: %s" % exc)

    results.append(("promotion is blocked when the cited snapshot is gone",
                    blocked))

    # ---- the pre-existing bad row ----------------------------------------
    rule("THE ROW THAT PROVED THIS MATTERS")

    history = universe.history()

    say("  %d snapshots on record:" % len(history))

    for entry in history:
        try:
            universe.restore(entry["snapshot_id"])
            state = "restores"
        except universe.UniverseUnavailable:
            state = "DOES NOT RESTORE -- treated as absent"
        say("    %-22s %s  %5d instruments  %s"
            % (entry["snapshot_id"], entry["taken_at"][:19],
               entry["instrument_count"], state))

    say()
    say("  UNIV-20260818 was written by an earlier partial implementation and")
    say("  does not restore. Before `due()` verified restorability it was the")
    say("  most recent row, so the platform reported the universe as frozen")
    say("  and recoverable when it was neither.")

    # ---- verdict ----------------------------------------------------------
    rule("PHASE 6 VERDICT")

    for label, passed in results:
        say("  [%s] %s" % ("PASS" if passed else "FAIL", label))

    everything = all(passed for _, passed in results)

    say()
    say("  %s" % ("UNIVERSE SNAPSHOT SYSTEM ACCEPTED" if everything
                  else "PHASE 6 FAILED"))

    shutil.rmtree(workdir, ignore_errors=True)

    return 0 if everything else 1


if __name__ == "__main__":
    sys.exit(main())
