"""Delete every price bar whose provenance is not verified-real.

The user's instruction is absolute: simulated data must never be used. Rather
than trusting a label, this re-runs the authenticity check and wipes the price
tables unless they pass -- so the only way data survives here is by being
demonstrably genuine.

    python purge_simulated.py            # check, and say what would go
    python purge_simulated.py --yes      # check, and purge if convicted
    python purge_simulated.py --force    # wipe price data unconditionally

Only a SIMULATED verdict -- a failed DISCRIMINATING test, the holiday
calendar -- purges, and only with --yes after the counts are printed. An
UNVERIFIED verdict (a structural test real data can also fail) never does
(the full test, 2026-11-12).

This is only as good as the verdict it acts on, and that verdict has been
wrong before: a miscalibrated Nowruz window once had this delete bars fetched
live from cdn.tsetmc.com. Run `python test_authenticity.py` -- which asserts
that the checker still convicts a fabricated calendar and clears a real one --
before trusting a SIMULATED verdict enough to act on it here.
"""

import sqlite3
import sys

import authenticity
from database import DATABASE_NAME, SIMULATED, create_database, set_provenance


def purge(force=False, confirmed=False):

    create_database()

    if not force:
        result = authenticity.verify(record=True)
        print(f"authenticity verdict: {result['status'].upper()}")
        print(f"  {result['summary']}")
        convicted = result["status"] == SIMULATED and any(
            c["discriminating"] and c["passed"] is False
            for c in result.get("checks") or ())
        if not convicted:
            print("\nNot convicted by a discriminating test — nothing purged.")
            return 0

    conn = sqlite3.connect(DATABASE_NAME)

    counts = {}
    for table in ("market_data", "price_history", "market_snapshot"):
        try:
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = 0

    if not (force or confirmed):
        conn.close()
        print("\nWould delete:")
        for table, n in counts.items():
            print(f"  {table:<18} {n} rows")
        print("\nNothing deleted. Back up the database, run "
              "`python test_authenticity.py`, and rerun with --yes to purge.")
        return 0

    for table in ("market_data", "price_history", "market_snapshot"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass

    # The cache log must go too, or market_data.py will believe the deleted
    # bars are still fresh and skip re-fetching them.
    try:
        conn.execute("DELETE FROM fetch_log")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

    total = sum(counts.values())

    print("\nPurged:")
    for table, n in counts.items():
        print(f"  {table:<18} {n} rows")

    set_provenance(
        "price_history", "empty",
        "All non-verified price data purged; awaiting a real fetch."
    )

    print(f"\n{total} rows deleted. Instruments and universe were kept.")
    print("Now fetch real data:  python seed_import.py --snapshot --history 200")

    return total


if __name__ == "__main__":
    purge(force="--force" in sys.argv, confirmed="--yes" in sys.argv)
