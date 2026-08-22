"""Set aside the next out-of-sample window, before anyone reaches it.

WHY THIS RUNS TODAY AND NOT WHEN SOMEBODY NEEDS IT
----------------------------------------------------
Every holdout in the archive is spent. E19 took 2023-03-01..2024-09-01 and E21
took 2024-09-02..2026-08-18, and market data ends 2026-08-18. E20 could make no
out-of-sample claim at all for exactly this reason, and E22 and E23 inherited
the same silence.

Nothing objected while that happened. `windows_touched` records what has been
SPENT; it had no way to record what must not be. Spending was permitted, and
afterwards there was simply nothing left.

So the next window is reserved now, while it is still empty and nobody wants
it. A window that only becomes protected once somebody remembers it is not
protected -- the moment it is worth protecting is the moment there is a result
waiting on the other side of it.

WHAT A RESERVATION IS, AND WHAT IT IS NOT
-------------------------------------------
It is NOT a lock. The window can be spent, because that is what a holdout is
for. What it requires is that the spending experiment NAMES the reservation:

    manager.touch_window(start, end, "holdout", claim="R-FORWARD-2027")

That converts "I did not realise this was the holdout" into a deliberate act
with an author and a date. The control is not prevention; it is that nobody
can spend this by accident.

USAGE
    python examples/reserve_forward_holdout.py
    python examples/reserve_forward_holdout.py --status
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments import store                             # noqa: E402
from sirat.experiments.manager import Manager, GovernanceError  # noqa: E402

RESERVATION_ID = "R-FORWARD-2027"

# The day after the last bar in price_history. Everything before this has been
# read by somebody, so nothing before this can be an honest holdout again.
START = "2026-08-19"

# A year: roughly 240 sessions, which is the sample size the roadmap's ML gate
# asks for and comfortably more than any Stage 2 measurement needs. Ending it
# rather than leaving it open matters -- an unbounded reservation is one nobody
# can ever satisfy, and a control that can never be satisfied gets removed.
END = "2027-08-19"

REASON = (
    "The only unspent out-of-sample window this programme has. E19 spent "
    "2023-03-01..2024-09-01 and E21 spent 2024-09-02..2026-08-18; data ends "
    "2026-08-18, so every other window has been read. It accrues on its own "
    "and cannot be manufactured. Spend it on a hypothesis worth a year of "
    "waiting, and name this reservation when you do."
)


def show():
    held = store.reservations()

    if not held:
        print("no reservations")
        return 1

    print("%-18s %-12s %-12s %-10s %s"
          % ("reservation", "start", "end", "purpose", "claimed by"))
    print("-" * 74)

    for row in held:
        print("%-18s %-12s %-12s %-10s %s"
              % (row["reservation_id"], row["window_start"], row["window_end"],
                 row["purpose"], row["claimed_by"] or "-- unclaimed --"))

    print()

    for row in held:
        if row["claimed_by"]:
            continue
        print("%s, set aside by %s on %s:"
              % (row["reservation_id"], row["reserved_by"],
                 row["reserved_at"][:10]))
        print("   %s" % row["reason"])

    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true")

    args = parser.parse_args(argv)

    if args.status:
        return show()

    existing = [r for r in store.reservations()
                if r["reservation_id"] == RESERVATION_ID]

    if existing:
        print("%s already reserved. Reservations are not re-made."
              % RESERVATION_ID)
        return show()

    try:
        Manager.reserve_window(
            RESERVATION_ID, START, END, "holdout",
            reserved_by="SIRAT-AI Research", reason=REASON)
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1

    print("reserved %s .. %s as %s" % (START, END, RESERVATION_ID))
    print()

    return show()


if __name__ == "__main__":
    sys.exit(main())
