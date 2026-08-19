"""Universe snapshots: freeze the list, because only elapsed time can fix it.

THE DEFECT, STATED PRECISELY
----------------------------
`instruments` in `sirat_market.db` is the list of things listed TODAY. It has no
`valid_from` and no `valid_to`; a refresh overwrites it in place. A company
delisted in 1403 is not in it, was never in it as far as this database is
concerned, and therefore never appears in any historical window -- and companies
leave this market after collapsing, not after doubling.

`backtesting/survivorship.py` says this in its own docstring and is honest about
the consequence: it can produce a LOWER BOUND on the bias and nothing more,
because it can only see the names that are still on the list.

WHY THIS IS THE MOST TIME-CRITICAL ITEM IN THE PROJECT
-------------------------------------------------------
Every other defect in this repository can be fixed whenever someone gets to it.
This one cannot. A snapshot not taken this week is a week of the universe's
history that is permanently unrecoverable -- there is no archive to fetch it
from later. The measured attrition of 1.7%/decade is a floor for exactly this
reason.

Starting costs an afternoon. Each week of delay costs a week that no amount of
later effort recovers.

WHAT A SNAPSHOT IS
------------------
The instrument list as it stood, content-hashed, stored in the append-only
research database rather than the market database. Two snapshots with the same
content share a hash and the second is a no-op, so a daily schedule that mostly
sees no change costs almost nothing and still proves the list did not move.

The snapshot_id is `u<YYYYMMDD>-<first 8 of the content hash>`: sortable by
date, and distinguishable when the list changes twice in one day.
"""

import argparse
import os
import sqlite3
import sys

import pandas as pd

from sirat.experiments import provenance, store


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

MARKET_DB = os.path.join(ROOT, "sirat_market.db")

# How stale a snapshot may be before the platform calls it overdue. Weekly is
# the schedule the plan committed to; the check is in days so a run that slips
# by an afternoon does not read as a miss.
MAX_AGE_DAYS = 7

# What is frozen. Deliberately NOT everything in the row: `updated_at` changes
# on every refresh whether or not anything about the instrument changed, and
# including it would give every snapshot a fresh hash and destroy the property
# that makes an unchanged list cheap.
COLUMNS = ("ins_code", "symbol_fa", "name_fa", "isin", "kind", "board",
           "is_equity", "sector_code", "sector_name")


class UniverseUnavailable(Exception):
    """The market database could not be read."""


def read_universe(market_db=None):
    """The instrument list as it stands right now."""

    path = market_db or MARKET_DB

    if not os.path.exists(path):
        raise UniverseUnavailable(
            "no market database at %s; there is no universe to freeze" % path)

    conn = sqlite3.connect(path)

    try:
        frame = pd.read_sql_query(
            "SELECT %s FROM instruments ORDER BY ins_code" % ", ".join(COLUMNS),
            conn)
    except Exception as exc:                                   # noqa: BLE001
        raise UniverseUnavailable("could not read instruments: %s" % exc)
    finally:
        conn.close()

    frame["ins_code"] = frame["ins_code"].astype(str)

    return frame


def _payload(frame):
    """The snapshot's content, as a list of plain rows.

    Sorted by ins_code and with columns in a fixed order, so the hash describes
    the universe rather than the order SQLite happened to return.
    """

    return [
        {column: (None if pd.isna(row[column]) else row[column])
         for column in COLUMNS}
        for _, row in frame.sort_values("ins_code").iterrows()
    ]


def capture(market_db=None, path=None, as_of=None):
    """Freeze the universe. Idempotent when the list has not moved.

    Returns the snapshot record, with `created` saying whether this call was the
    one that wrote it.
    """

    store.initialise(path)

    frame = read_universe(market_db)

    payload = _payload(frame)

    content = provenance.content_hash(payload)

    stamp = pd.Timestamp(as_of) if as_of else pd.Timestamp.now()

    snapshot_id = "u%s-%s" % (stamp.strftime("%Y%m%d"), content[:8])

    existing = get(snapshot_id, path=path)

    if existing is not None:
        return dict(existing, created=False, equities=int(
            frame["is_equity"].fillna(0).astype(int).sum()))

    store.insert_universe_snapshot(snapshot_id, len(payload), content, payload,
                                   path=path, taken_at=stamp)

    return {
        "snapshot_id": snapshot_id,
        "taken_at": stamp.isoformat(),
        "instrument_count": len(payload),
        "equities": int(frame["is_equity"].fillna(0).astype(int).sum()),
        "content_hash": content,
        "created": True,
    }


def get(snapshot_id, path=None):
    """One snapshot's header. Use `restore()` for the instrument list itself."""

    store.initialise(path)

    conn = store.connect(path)

    try:
        row = conn.execute(
            "SELECT snapshot_id, taken_at, instrument_count, content_hash "
            "FROM universe_snapshots WHERE snapshot_id = ?",
            (snapshot_id,)).fetchone()
    finally:
        conn.close()

    return dict(row) if row else None


def restore(snapshot_id, path=None):
    """The universe as it stood, as a DataFrame -- the recoverability promise.

    Verifies the content hash on the way out. A snapshot that no longer hashes
    to its recorded value is not a snapshot of anything, and returning it
    silently would make every experiment that cited it unverifiable.
    """

    import json

    store.initialise(path)

    conn = store.connect(path)

    try:
        row = conn.execute(
            "SELECT * FROM universe_snapshots WHERE snapshot_id = ?",
            (snapshot_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        raise UniverseUnavailable("no snapshot %r" % snapshot_id)

    payload = json.loads(row["payload"])

    if provenance.content_hash(payload) != row["content_hash"]:
        raise UniverseUnavailable(
            "snapshot %r does not hash to its recorded value; the stored "
            "universe has been altered" % snapshot_id)

    return pd.DataFrame(payload, columns=list(COLUMNS))


def latest(path=None):
    store.initialise(path)
    return store.latest_universe_snapshot(path=path)


def history(path=None, limit=50):
    store.initialise(path)

    conn = store.connect(path)

    try:
        rows = conn.execute(
            "SELECT snapshot_id, taken_at, instrument_count, content_hash "
            "FROM universe_snapshots ORDER BY taken_at DESC LIMIT ?",
            (int(limit),)).fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows]


def due(max_age_days=MAX_AGE_DAYS, path=None, now=None):
    """Is a snapshot overdue, and by how much.

    A snapshot that cannot be RESTORED does not count as a snapshot. That is
    not pedantry: an unverifiable row is worse than an empty table, because the
    empty table makes the schedule fire and the bad row makes it stay quiet
    while nothing is actually recoverable. It is the same absence-read-as-
    approval failure that let four null execution counters pass for "no queues
    were ever hit", wearing different clothes.
    """

    current = latest(path=path)

    stamp = pd.Timestamp(now) if now else pd.Timestamp.now()

    if current is None:
        return {"due": True, "age_days": None, "latest": None,
                "restorable": None,
                "reason": "no universe snapshot has ever been taken"}

    try:
        restore(current["snapshot_id"], path=path)
        restorable = True
    except UniverseUnavailable as exc:
        return {
            "due": True,
            "age_days": None,
            "max_age_days": max_age_days,
            "latest": current["snapshot_id"],
            "restorable": False,
            "reason": ("the most recent snapshot (%s) cannot be restored: %s. "
                       "It is treated as absent."
                       % (current["snapshot_id"], exc)),
        }

    age = (stamp - pd.Timestamp(current["taken_at"])).total_seconds() / 86400.0

    return {
        "due": bool(age >= max_age_days),
        "age_days": round(age, 2),
        "max_age_days": max_age_days,
        "latest": current["snapshot_id"],
        "restorable": restorable,
        "reason": ("%.1f days since %s" % (age, current["snapshot_id"])),
    }


def run_if_due(max_age_days=MAX_AGE_DAYS, market_db=None, path=None):
    """The scheduler's whole body.

    Any entry point can call this -- the web app's bootstrap, a backtest run, a
    scheduled task. It is cheap when nothing is owed and it is the reason a
    missed cron does not become a missed month.
    """

    status = due(max_age_days=max_age_days, path=path)

    if not status["due"]:
        return dict(status, captured=None)

    try:
        snapshot = capture(market_db=market_db, path=path)
    except UniverseUnavailable as exc:
        return dict(status, captured=None, error=str(exc))

    return dict(status, captured=snapshot)


# ==============================
# SCHEDULING ON THIS MACHINE
# ==============================

TASK_NAME = "SIRAT-AI universe snapshot"


def schedule_command(python=None, weekly_day="MON", at="18:30"):
    """The Windows Task Scheduler command that installs the weekly job.

    Emitted rather than executed. Registering a scheduled task changes the
    machine, and a research module does not get to do that without being asked
    -- but leaving the operator to compose the command by hand is how the job
    ends up never being installed.
    """

    python = python or sys.executable

    return (
        'schtasks /Create /TN "%s" /SC WEEKLY /D %s /ST %s /F '
        '/TR "\\"%s\\" \\"%s\\" --capture"'
        % (TASK_NAME, weekly_day, at, python,
           os.path.join(ROOT, "sirat", "experiments", "universe.py"))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Freeze the market universe so research stays recoverable.")

    parser.add_argument("--capture", action="store_true",
                        help="take a snapshot now")
    parser.add_argument("--if-due", action="store_true",
                        help="take one only if the last is older than --max-age")
    parser.add_argument("--max-age", type=float, default=MAX_AGE_DAYS)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--restore", metavar="SNAPSHOT_ID")
    parser.add_argument("--install-schedule", action="store_true",
                        help="print the schtasks command; installs nothing")

    args = parser.parse_args(argv)

    if args.install_schedule:
        print("Run this in an elevated prompt to install the weekly job:\n")
        print("  " + schedule_command())
        return 0

    if args.restore:
        frame = restore(args.restore)
        print(frame.to_string(index=False, max_rows=40))
        print("\n%d instruments" % len(frame))
        return 0

    if args.history:
        for row in history():
            print("  %-22s %s  %6d instruments  %s"
                  % (row["snapshot_id"], row["taken_at"][:19],
                     row["instrument_count"], row["content_hash"][:12]))
        return 0

    if args.capture or args.if_due:
        result = (run_if_due(max_age_days=args.max_age) if args.if_due
                  else dict(capture(), due=True))

        snapshot = result.get("captured") if args.if_due else result

        if snapshot is None:
            print("not due: %s" % result.get("reason"))
            if result.get("error"):
                print("  and it could not have run anyway: %s"
                      % result["error"])
                return 1
            return 0

        print("%s  %s  %d instruments (%d equities)"
              % ("captured" if snapshot.get("created") else "unchanged",
                 snapshot["snapshot_id"], snapshot["instrument_count"],
                 snapshot.get("equities", -1)))
        return 0

    status = due(max_age_days=args.max_age)

    for key in ("due", "age_days", "max_age_days", "latest", "reason"):
        print("  %-14s %s" % (key, status.get(key)))

    if status["due"]:
        print("\n  Survivorship is the one defect only elapsed time can fix.")
        print("  Run:  python -m sirat.experiments.universe --capture")

    return 1 if status["due"] else 0


if __name__ == "__main__":
    sys.exit(main())
