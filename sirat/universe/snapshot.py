"""Freeze the tradable universe, weekly, forever.

WHY THIS IS THE MOST URGENT FILE IN THE REPOSITORY
--------------------------------------------------
Survivorship is the one defect that no amount of cleverness fixes later. Every
result the programme produced is inflated by an unmeasured amount because the
universe is today's listings: a name that rose and then delisted is simply
absent, and the measured 1.7%/decade attrition is a lower bound, since names
delisted before the database existed were never fetched at all.

The remedy is not analysis. It is arriving on time, every week, and writing
down what was listed. **Each week not taken is a week permanently
unrecoverable** -- which is why this ships before the microstructure module,
before the gates, before anything interesting.

WHAT A SNAPSHOT IS
------------------
The full instrument list with the fields that decide eligibility, hashed by
content. Two snapshots with the same membership share a content hash even if
taken on different days, so a run can assert "the universe did not move"
cheaply. The snapshot_id carries the date, so recovery is by date.
"""

import hashlib
import os
import sqlite3

import pandas as pd

from ..experiments import store


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

MARKET_DB = os.path.join(ROOT, "sirat_market.db")

# Fields that decide whether an instrument is eligible. Anything else is
# description and would make the content hash churn for no reason.
FIELDS = ("ins_code", "symbol_fa", "isin", "kind", "board", "is_equity",
          "sector_code", "sector_name", "market")

STALE_AFTER_DAYS = 8       # a weekly cadence, with one day of slack


def _rows(market_db=None):
    conn = sqlite3.connect(market_db or MARKET_DB)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT %s FROM instruments ORDER BY ins_code" % ", ".join(FIELDS))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def _content_hash(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update("|".join(str(row.get(f, "")) for f in FIELDS).encode())
    return digest.hexdigest()


def take_snapshot(market_db=None, db_path=None, as_of=None):
    """Freeze the universe. Idempotent within a day."""

    rows = _rows(market_db)

    if not rows:
        raise RuntimeError(
            "the instruments table is empty; refusing to record an empty "
            "universe snapshot, which would later read as a real one")

    stamp = pd.Timestamp(as_of) if as_of else pd.Timestamp.now()

    snapshot_id = "UNIV-%s" % stamp.strftime("%Y%m%d")

    content = _content_hash(rows)

    store.insert_universe_snapshot(snapshot_id, len(rows), content, rows,
                                   db_path)

    return {"snapshot_id": snapshot_id, "instruments": len(rows),
            "content_hash": content}


def latest(db_path=None):
    return store.latest_universe_snapshot(db_path)


def verify_scheduler(db_path=None, now=None):
    """Is the weekly cadence actually being kept?

    Returns a status rather than raising: a research run should be able to say
    "the universe record is stale" in its provenance instead of dying.
    """

    row = latest(db_path)

    now = pd.Timestamp(now) if now else pd.Timestamp.now()

    if row is None:
        return {
            "healthy": False,
            "reason": "no universe snapshot has ever been taken",
            "remedy": "run `python -m sirat.universe.snapshot` and schedule it "
                      "weekly",
            "days_since": None,
        }

    days = (now - pd.Timestamp(row["taken_at"])).days

    return {
        "healthy": bool(days <= STALE_AFTER_DAYS),
        "snapshot_id": row["snapshot_id"],
        "days_since": days,
        "stale_after_days": STALE_AFTER_DAYS,
        "instruments": row["instrument_count"],
        "reason": None if days <= STALE_AFTER_DAYS
        else "the last snapshot is %d days old" % days,
    }


def snapshot_report(db_path=None):
    conn = store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT snapshot_id, taken_at, instrument_count, content_hash "
            "FROM universe_snapshots ORDER BY taken_at").fetchall()
    finally:
        conn.close()

    rows = [dict(r) for r in rows]

    changes = sum(1 for a, b in zip(rows, rows[1:])
                  if a["content_hash"] != b["content_hash"])

    return {"snapshots": len(rows), "membership_changes": changes,
            "first": rows[0] if rows else None,
            "last": rows[-1] if rows else None}


def main():
    store.initialise()
    got = take_snapshot()
    health = verify_scheduler()
    print("universe snapshot %s: %d instruments, hash %s"
          % (got["snapshot_id"], got["instruments"], got["content_hash"][:16]))
    print("scheduler healthy: %s" % health["healthy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
