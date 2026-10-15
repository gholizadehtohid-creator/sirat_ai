"""The signal log -- what makes this scanner falsifiable instead of memorable.

WHY EVERY SCAN IS WRITTEN DOWN
-------------------------------
A watchlist nobody records is unfalsifiable by construction. The names that
went up get remembered, the ones that did not get forgotten, and after a year
the scanner has a reputation and no evidence. This project has been here
before: every historical holdout it owns is already spent (E19, E21), and the
only out-of-sample window it will ever have again is R-FORWARD-2027, accruing
one session at a time.

So each run appends two things:

    a RUN row     with the funnel -- universe, liquid, past the MFI gate, past
                  the range gate, and how many finally qualified. An empty scan
                  is written too. "Nothing qualified today" is a real
                  observation, and a log that only records the days something
                  did is a log that cannot measure a hit rate.

    a SIGNAL row  per candidate, carrying its score, every input that produced
                  it, and the full payload as JSON.

Both carry `computed_at` in UTC. That timestamp is the entire point, and it
follows examples/forward_prediction_log.py's rule exactly: at evaluation time an
entry is admissible only if it was computed BEFORE the session whose outcome is
being measured. A row written late is not deleted or hidden -- it simply fails
the rule and drops out.

APPEND-ONLY. NOTHING IS EVER REWRITTEN.
----------------------------------------
Re-running the scanner writes a new run, never an update to an old one. This is
the same rule database.py applies to paper trades and for the same reason: a
signal raised on Sunday was raised on what was knowable on Sunday, and
improving it on Tuesday with Tuesday's data is precisely the look-ahead the
backtester spends its whole design avoiding.

OWN TABLES, NOT database.create_database()
-------------------------------------------
Same pattern as event_intelligence/store.py: the connection helper is reused so
WAL and busy-timeout settings are inherited, but the DDL is owned here and
created lazily. database.py is not edited for a scanner.
"""

import json
import sqlite3
from datetime import datetime, timezone

import database


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema():
    """Create the two tables if absent. Cheap; safe to call on every scan."""

    conn = database._connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS smart_money_runs (
        run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_at   TEXT NOT NULL,
        session_date  TEXT,
        universe      INTEGER,
        liquid        INTEGER,
        passed_mfi    INTEGER,
        passed_range  INTEGER,
        scored        INTEGER,
        qualified     INTEGER,
        params        TEXT,
        funnel        TEXT
    )
    """)

    # PRIMARY KEY (run_id, ins_code): one row per name per run. Not
    # (ins_code, session_date) -- two scans of the same session are two
    # observations, and collapsing them would hide a scanner that gives
    # different answers at 10am and 4pm.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS smart_money_signals (
        run_id            INTEGER NOT NULL,
        ins_code          TEXT NOT NULL,
        symbol            TEXT,
        name              TEXT,
        sector            TEXT,
        rank              INTEGER,
        score             INTEGER,
        status            TEXT,
        coverage          REAL,
        mfi               REAL,
        mfi_change        REAL,
        divergence        INTEGER,
        pe                REAL,
        sector_pe         REAL,
        market_cap        REAL,
        size_band         TEXT,
        net_pct           REAL,
        smart_money_ratio REAL,
        code_to_code      INTEGER,
        volume_ratio      REAL,
        position_pct      REAL,
        support_price     REAL,
        support_distance  REAL,
        close             REAL,
        payload           TEXT,
        PRIMARY KEY (run_id, ins_code)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_sm_signals_code "
                "ON smart_money_signals (ins_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sm_runs_date "
                "ON smart_money_runs (session_date)")

    conn.commit()
    conn.close()


def record(report):
    """Append one scan. Returns the run_id, or None if it could not be stored.

    Storage failure never fails a scan: the panel showing a list is worth more
    than the log being complete, and the caller is told which happened via the
    returned id rather than by an exception it would have to catch around the
    whole scan.
    """

    ensure_schema()

    computed_at = report.get("computed_at") or _now()
    counts = report.get("counts") or {}

    conn = database._connect()

    try:
        cur = conn.execute("""
            INSERT INTO smart_money_runs
                (computed_at, session_date, universe, liquid, passed_mfi,
                 passed_range, scored, qualified, params, funnel)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            computed_at,
            report.get("session_date"),
            counts.get("universe"),
            counts.get("liquid"),
            counts.get("passed_mfi"),
            counts.get("passed_range"),
            counts.get("scored"),
            counts.get("qualified"),
            json.dumps(report.get("params") or {}, ensure_ascii=False),
            json.dumps(counts, ensure_ascii=False),
        ))

        run_id = cur.lastrowid

        rows = []

        for row in report.get("top") or []:
            support = row.get("support") or {}
            rows.append((
                run_id,
                str(row.get("ins_code")),
                row.get("symbol"),
                row.get("name"),
                row.get("sector"),
                row.get("rank"),
                row.get("score"),
                row.get("status"),
                row.get("coverage"),
                row.get("mfi"),
                row.get("mfi_change"),
                1 if row.get("divergence") else 0,
                row.get("pe"),
                row.get("sector_pe"),
                row.get("market_cap"),
                row.get("size_band"),
                row.get("net_pct"),
                row.get("smart_money_ratio"),
                1 if row.get("code_to_code") else 0,
                row.get("volume_ratio"),
                row.get("position_pct"),
                support.get("price"),
                support.get("distance_pct"),
                row.get("close"),
                json.dumps(row, ensure_ascii=False, default=str),
            ))

        if rows:
            # Plain INSERT, not OR REPLACE. run_id is fresh for every scan so a
            # conflict is impossible here -- and if one ever became possible,
            # failing loudly is the behaviour this log's append-only promise
            # requires. OR REPLACE would quietly overwrite a past prediction.
            conn.executemany("""
                INSERT INTO smart_money_signals
                    (run_id, ins_code, symbol, name, sector, rank, score,
                     status, coverage, mfi, mfi_change, divergence, pe,
                     sector_pe, market_cap, size_band, net_pct,
                     smart_money_ratio, code_to_code, volume_ratio,
                     position_pct, support_price, support_distance, close,
                     payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?)
            """, rows)

        conn.commit()

        return run_id

    except sqlite3.Error:
        conn.rollback()
        return None

    finally:
        conn.close()


def recent_signals(limit=50):
    """The most recent signals across all runs, newest first."""

    ensure_schema()

    conn = database._connect()

    rows = conn.execute("""
        SELECT r.run_id, r.computed_at, r.session_date, s.symbol, s.name,
               s.sector, s.rank, s.score, s.status, s.mfi, s.net_pct,
               s.smart_money_ratio, s.position_pct, s.close
        FROM smart_money_signals s
        JOIN smart_money_runs r ON r.run_id = s.run_id
        ORDER BY r.run_id DESC, s.rank ASC
        LIMIT ?
    """, (int(limit),)).fetchall()

    conn.close()

    keys = ("run_id", "computed_at", "session_date", "symbol", "name",
            "sector", "rank", "score", "status", "mfi", "net_pct",
            "smart_money_ratio", "position_pct", "close")

    return [dict(zip(keys, row)) for row in rows]


def signal_history(ins_code, limit=None):
    """Every signal ever raised for one name, oldest first.

    The shape an evaluation wants: one name, its scores over time, each with
    the timestamp that decides whether it is admissible against a given
    outcome session.
    """

    ensure_schema()

    conn = database._connect()

    sql = """
        SELECT r.computed_at, r.session_date, s.rank, s.score, s.status,
               s.mfi, s.net_pct, s.smart_money_ratio, s.position_pct, s.close,
               s.payload
        FROM smart_money_signals s
        JOIN smart_money_runs r ON r.run_id = s.run_id
        WHERE s.ins_code = ?
        ORDER BY r.computed_at ASC
    """

    params = [str(ins_code)]

    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()

    conn.close()

    keys = ("computed_at", "session_date", "rank", "score", "status", "mfi",
            "net_pct", "smart_money_ratio", "position_pct", "close",
            "payload")

    return [dict(zip(keys, row)) for row in rows]


def runs(limit=30):
    """Past runs with their funnels, newest first. An empty run is a row too."""

    ensure_schema()

    conn = database._connect()

    rows = conn.execute("""
        SELECT run_id, computed_at, session_date, universe, liquid,
               passed_mfi, passed_range, scored, qualified
        FROM smart_money_runs
        ORDER BY run_id DESC
        LIMIT ?
    """, (int(limit),)).fetchall()

    conn.close()

    keys = ("run_id", "computed_at", "session_date", "universe", "liquid",
            "passed_mfi", "passed_range", "scored", "qualified")

    return [dict(zip(keys, row)) for row in rows]
