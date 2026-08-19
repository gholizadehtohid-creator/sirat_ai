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
