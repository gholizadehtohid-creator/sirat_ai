"""Order-book depth and the real price band, collected forward.

WHY THIS EXISTS, AND WHY IT IS URGENT RATHER THAN INTERESTING
--------------------------------------------------------------
`RESEARCH_ROADMAP.md` names microstructure the only unblocked workstream and
says its data cannot be backfilled. Both are true. What was not noticed is that
TSETMC serves the data today and this platform explicitly declines it:

    providers/tsetmc_cdn.py:509     ...&withBestLimits=false

One parameter. The response carries five levels of bid and ask -- price,
quantity and order count on each side -- for every instrument.

Three things are being thrown away every single day:

    blDs          five levels of depth. QUEUE SIZE, which E14 never had: it
                  measured a 3.03x queue-exposure ratio without ever seeing a
                  rial of the demand standing behind a صف
    pMax / pMin   the REAL دامنه نوسان. E14 had to estimate the band from the
                  99.5th percentile of each session's cross-section, with a
                  note that the estimate is "never the detector". It did not
                  have to -- the exchange publishes the number
    hEven         the time of the last trade, which turns staleness detection
                  from an inference about volume into an observation

`market_snapshot` is keyed on `ins_code` alone and `save_market_snapshot()`
runs DELETE-then-INSERT, so each pull destroys the previous one. Every day this
is not running is a day of order-book history permanently lost. Nothing here
can be recovered later by trying harder.

A MISSING KEY IS A BUG, NOT A MISSING VALUE
--------------------------------------------
`item.get("cs")` returns None when the key is absent and None when the value is
null, and this codebase has now been bitten by that three times:

    client_type counts   wrong names -> silent zeros
    blDs                 guessed `qTitMeOf` -> every level read as empty
    market_snapshot      `cs` for the instrument state -> 587 of 587 rows NULL,
                         for as long as the column has existed. GetMarketWatch
                         does not carry state at all; the column never had a
                         source

So `_require` raises on an absent key. A field this module declares must exist
in the payload, and if the exchange renames one the collector stops rather than
quietly writing a column of nulls that a later experiment reads as "no queues
were ever seen".

USAGE
    python depth.py            collect one snapshot
    python depth.py --status   what has been collected so far
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

import config

DB = config.DATABASE_NAME

URL = ("https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch"
       "?market=0&paperTypes[0]=1&showTraded=false&withBestLimits=true")

LEVELS = 5

TIMEOUT = 60


class PayloadChanged(Exception):
    """A key this module depends on is not in the response.

    Deliberately fatal. The alternative is a column of nulls that reads exactly
    like "the market had nothing to report".
    """


def _require(item, key, where):
    if key not in item:
        raise PayloadChanged(
            "%s: expected key %r, got %s. The feed changed shape; fix the "
            "mapping rather than letting this become a null column."
            % (where, key, sorted(item)[:12]))

    return item[key]


def _num(value):
    if value is None or value == "":
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if number == number and abs(number) != float("inf") else None


# ==============================
# SCHEMA
# ==============================

def ensure_tables(conn):
    """Append-only history. No DELETE anywhere in this module, on purpose."""

    conn.execute("""
    CREATE TABLE IF NOT EXISTS depth_snapshot (
        ins_code        TEXT NOT NULL,
        captured_at     TEXT NOT NULL,
        symbol_fa       TEXT,
        last            REAL,
        close           REAL,
        prev_close      REAL,
        open            REAL,
        high            REAL,
        low             REAL,
        volume          REAL,
        value           REAL,
        trades          REAL,
        base_volume     REAL,
        total_shares    REAL,
        -- the real band, published by the exchange. E14 estimated this.
        limit_up        REAL,
        limit_down      REAL,
        -- HHMMSS of the last trade. A name whose hEven has not moved since
        -- the previous capture did not trade, which is an observation rather
        -- than an inference from volume.
        last_trade_hhmmss INTEGER,
        sector_code     TEXT,
        PRIMARY KEY (ins_code, captured_at)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS depth_levels (
        ins_code     TEXT NOT NULL,
        captured_at  TEXT NOT NULL,
        level        INTEGER NOT NULL,
        bid_price    REAL,
        bid_qty      REAL,
        bid_orders   REAL,
        ask_price    REAL,
        ask_qty      REAL,
        ask_orders   REAL,
        PRIMARY KEY (ins_code, captured_at, level)
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_depth_snapshot_time
        ON depth_snapshot (captured_at)
    """)

    conn.commit()


# ==============================
# FETCH
# ==============================

def fetch(session=None):
    """One market-wide pull, with depth. Returns the raw rows."""

    if session is None:
        from providers import transport
        # build_session applies the source-IP binding, which is what lets this
        # reach TSETMC while a VPN owns the default route.
        session = transport.build_session(host="cdn.tsetmc.com")

    response = session.get(URL, timeout=TIMEOUT)
    response.raise_for_status()

    payload = response.json()

    rows = payload.get("marketwatch") or payload.get("marketWatch")

    if not rows:
        raise PayloadChanged(
            "no marketwatch rows; top-level keys were %s" % sorted(payload))

    return rows


def parse(rows, captured_at):
    """Rows -> (snapshot records, level records). Raises if a key is gone."""

    snapshots = []
    levels = []

    for item in rows:
        ins_code = str(_require(item, "insCode", "marketwatch row"))

        snapshots.append((
            ins_code, captured_at,
            (_require(item, "lva", ins_code) or "").strip(),
            _num(_require(item, "pdv", ins_code)),
            _num(_require(item, "pcl", ins_code)),
            _num(_require(item, "py", ins_code)),
            _num(_require(item, "pf", ins_code)),
            _num(_require(item, "pmx", ins_code)),
            _num(_require(item, "pmn", ins_code)),
            _num(_require(item, "qtj", ins_code)),
            _num(_require(item, "qtc", ins_code)),
            _num(_require(item, "ztt", ins_code)),
            _num(_require(item, "bv", ins_code)),
            _num(_require(item, "ztd", ins_code)),
            _num(_require(item, "pMax", ins_code)),
            _num(_require(item, "pMin", ins_code)),
            int(_num(_require(item, "hEven", ins_code)) or 0),
            str(_require(item, "csv", ins_code) or "").strip() or None,
        ))

        book = _require(item, "blDs", ins_code) or []

        for entry in book:
            levels.append((
                ins_code, captured_at,
                int(_num(_require(entry, "n", ins_code + " blDs")) or 0),
                _num(_require(entry, "pmd", ins_code + " blDs")),
                _num(_require(entry, "qmd", ins_code + " blDs")),
                _num(_require(entry, "zmd", ins_code + " blDs")),
                _num(_require(entry, "pmo", ins_code + " blDs")),
                _num(_require(entry, "qmo", ins_code + " blDs")),
                _num(_require(entry, "zmo", ins_code + " blDs")),
            ))

    return snapshots, levels


# ==============================
# STORE
# ==============================

def store(snapshots, levels, db=DB):
    conn = sqlite3.connect(db)

    try:
        ensure_tables(conn)

        # INSERT OR IGNORE, never REPLACE: a capture already recorded for this
        # instant is history, and history is not rewritten here.
        conn.executemany(
            "INSERT OR IGNORE INTO depth_snapshot VALUES (%s)"
            % ",".join("?" * 18), snapshots)

        conn.executemany(
            "INSERT OR IGNORE INTO depth_levels VALUES (%s)"
            % ",".join("?" * 9), levels)

        conn.commit()

        return conn.total_changes
    finally:
        conn.close()


def collect(db=DB, session=None, verbose=True):
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = fetch(session=session)

    snapshots, levels = parse(rows, captured_at)

    written = store(snapshots, levels, db=db)

    if verbose:
        print("captured %s" % captured_at)
        print("  instruments : %d" % len(snapshots))
        print("  depth levels: %d" % len(levels))
        print("  rows written: %d" % written)

    return {"captured_at": captured_at, "instruments": len(snapshots),
            "levels": len(levels), "written": written}


# ==============================
# STATUS
# ==============================

def status(db=DB):
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)

    try:
        try:
            captures, first, last = conn.execute(
                "SELECT COUNT(DISTINCT captured_at), MIN(captured_at), "
                "MAX(captured_at) FROM depth_snapshot").fetchone()
        except sqlite3.OperationalError:
            print("no depth history yet -- run `python depth.py`")
            return 1

        snaps = conn.execute(
            "SELECT COUNT(*) FROM depth_snapshot").fetchone()[0]
        lvls = conn.execute(
            "SELECT COUNT(*) FROM depth_levels").fetchone()[0]

        print("captures      : %d   (%s .. %s)" % (captures, first, last))
        print("snapshot rows : %d" % snaps)
        print("level rows    : %d" % lvls)

        if not captures:
            return 0

        # What the depth buys, stated as a number rather than a promise.
        queued = conn.execute("""
            SELECT COUNT(*) FROM depth_levels
            WHERE captured_at = ? AND level = 1
              AND (ask_qty IS NULL OR ask_qty = 0) AND bid_qty > 0
        """, (last,)).fetchone()[0]

        biggest = conn.execute("""
            SELECT s.symbol_fa, l.bid_qty, l.bid_orders
            FROM depth_levels l JOIN depth_snapshot s
              ON s.ins_code = l.ins_code AND s.captured_at = l.captured_at
            WHERE l.captured_at = ? AND l.level = 1
            ORDER BY l.bid_qty DESC LIMIT 3
        """, (last,)).fetchall()

        print()
        print("latest capture: %d instruments with the ask side exhausted at "
              "level 1" % queued)
        for symbol, qty, orders in biggest:
            print("   %-12s %18.0f shares bid, %.0f orders"
                  % (symbol, qty or 0, orders or 0))

        return 0
    finally:
        conn.close()


TASK_NAME = "SIRAT-AI depth capture"

# A daily job that fires 20 hours after the last capture will not double up,
# and a job that missed yesterday still catches up today rather than waiting a
# full cycle to forgive itself.
MAX_AGE_HOURS = 20.0


def last_capture(db=DB):
    if not os.path.exists(db):
        return None

    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)

    try:
        row = conn.execute(
            "SELECT MAX(captured_at) FROM depth_snapshot").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

    return row[0] if row else None


def due(db=DB, max_age_hours=MAX_AGE_HOURS, now=None):
    """Is a capture owed? Absence of history means yes, not no."""

    stamp = last_capture(db=db)

    if not stamp:
        return True, "no capture has ever been taken"

    now = now or datetime.now(timezone.utc)

    try:
        age = (now - datetime.fromisoformat(stamp)).total_seconds() / 3600.0
    except ValueError:
        return True, "last capture timestamp %r is unreadable" % stamp

    if age >= max_age_hours:
        return True, "last capture was %.1f hours ago" % age

    return False, "last capture was %.1f hours ago, under the %.0f-hour floor" \
                  % (age, max_age_hours)


def schedule_command(python=None, at="13:00"):
    """The Windows Task Scheduler command that installs the daily job.

    Emitted, never executed. Registering a scheduled task changes the machine
    and this module does not get to do that unasked -- but leaving the operator
    to compose it by hand is how the job ends up never installed, and an
    uninstalled collector is the state this file was written to end.

    Saturday through Wednesday because that is the Tehran trading week, and
    13:00 because the market closes at 12:30: the capture then holds the
    closing book, including whatever queue survived the session. The schema is
    keyed on `captured_at`, so adding intraday captures later needs no change
    here beyond a second scheduled entry.
    """

    python = python or sys.executable

    return (
        'schtasks /Create /TN "%s" /SC WEEKLY /D SAT,SUN,MON,TUE,WED '
        '/ST %s /F /TR "\\"%s\\" \\"%s\\" --if-due"'
        % (TASK_NAME, at, python, os.path.abspath(__file__))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--if-due", action="store_true", dest="if_due",
                        help="capture only if the last one is older than "
                             "--max-age-hours; what the scheduled job runs")
    parser.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    parser.add_argument("--install-schedule", action="store_true",
                        help="print the schtasks command; installs nothing")
    parser.add_argument("--db", default=DB)

    args = parser.parse_args(argv)

    if args.install_schedule:
        print("Run this in an elevated prompt to install the daily job:\n")
        print("  " + schedule_command())
        print("\nNothing has been installed. Until it is, this collector runs")
        print("only when someone remembers -- and every session it misses is")
        print("order-book state that cannot be recovered later.")
        return 0

    if args.status:
        return status(db=args.db)

    if args.if_due:
        owed, why = due(db=args.db, max_age_hours=args.max_age_hours)
        if not owed:
            print("skipping: %s" % why)
            return 0
        print("capturing: %s" % why)

    try:
        collect(db=args.db)
    except PayloadChanged as exc:
        print("PAYLOAD CHANGED: %s" % exc)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
