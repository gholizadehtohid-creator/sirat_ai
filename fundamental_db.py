"""The fundamental store: a database of its own, deliberately off the trading path.

WHY A SECOND DATABASE FILE AT ALL
---------------------------------
`sirat_market.db` is 400+ MB of price history and is rewritten constantly: every
scan appends bars, snapshots and provenance rows. Filed financial statements have
the opposite lifecycle -- they arrive once, in a weekly batch, and are then read
thousands of times without changing. Putting the two in one file has three costs
that only show up under load:

  1. SQLite takes a database-wide writer lock. A three-hour Codal campaign and a
     market scan therefore block each other, which is exactly why the collector
     has to be a separate process on a separate file rather than a stage of the
     scan.
  2. The price database is disposable -- it can be rebuilt from TSETMC in an
     afternoon. Years of parsed filings cannot: codal.ir rate-limits, and the
     historical letters are the one asset in this project that is expensive to
     reacquire. Storing the disposable and the irreplaceable in the same file
     means every price-side repair risks the irreplaceable half.
  3. A weekly job that fails halfway must leave the trading data untouched by
     construction, not by care.

THE THREE STAGES, AND WHY THEY ARE THREE TABLES
-----------------------------------------------
    raw_filings          the collector's output: a letter was downloaded
        |
        v
    metrics              the processor's output: it PARSED into usable figures
        |
        +--> processing_failures     it did not, and here is why

The distinction between stage 1 and stage 2 is the one that cost this project a
six-hour run that stored nothing. "We attempted this filing" and "this filing
yielded numbers" are different facts, and a single `parsed` flag can only hold
one of them. A letter recorded in `raw_filings` and absent from `metrics` is not
an anomaly to be explained away -- it is the normal case for the roughly one
filing in five whose Excel layout this project cannot read, and
`processing_failures` is where that is stated instead of inferred.

A NON-TRANSIENT FAILURE SETTLES A FILING. That is what stops the queue looping:
`settled_failures()` is subtracted from the work list, so a letter that will
never parse is attempted once per reason, not once per run.

WHAT THE TRADING PATH IS ALLOWED TO DO
--------------------------------------
Exactly one thing: `load_latest_metrics()`. One indexed query, no network, no
parsing, no codal.ir. If this file is missing entirely the callers get empty
dicts and the fundamental factor drops out of the blend -- which is the correct
degradation, because "we have not collected its statements" is a fact about us,
not about the company.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Module-level rather than a config constant so a test can point it at a temp
# file. Nothing here creates the file at import time; only an explicit write or
# create_database() does.
DATABASE_NAME = os.path.join(BASE_DIR, "sirat_fundamentals.db")


# The fundamental picture is refreshed weekly. Quarterly statements do not move
# faster than that, and a daily poll of codal.ir buys nothing but rate limits.
REFRESH_INTERVAL_DAYS = 7

# Scope names for the refresh log. Split because discovery and parsing fail for
# unrelated reasons and a single "last run" timestamp hides which one stalled.
SCOPE_COLLECT = "collect"
SCOPE_PROCESS = "process"

STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"


# Failure reasons worth trying again. Everything not listed here is treated as
# settled: the filing is excluded from the queue until someone changes the
# parser, which is the only thing that could change the outcome.
TRANSIENT_REASONS = frozenset({
    "network",
    "timeout",
    "rate_limited",
    "server_error",
    "aborted",
})


# Scalars lifted out of the ratios blob into their own columns. The JSON stays
# authoritative; these exist so coverage and screening queries are SQL rather
# than a full-table deserialise.
METRIC_COLUMNS = (
    "eps", "bvps",
    "roe", "roa", "net_margin", "gross_margin", "operating_margin",
    "revenue_growth", "profit_growth", "eps_growth",
    "pe", "pb", "ps",
    "debt_ratio", "debt_to_equity", "current_ratio",
    "operating_cash_flow", "ocf_margin", "earnings_quality",
)


# ==============================
# PLUMBING
# ==============================

def path():
    """The file this module reads and writes. Honours an env override."""

    return os.environ.get("SIRAT_FUNDAMENTAL_DB") or DATABASE_NAME


def set_path(value):
    """Point the store at another file. Returns the previous path.

    Exists for tests and for a one-off migration; production reads path().
    """

    global DATABASE_NAME

    previous = DATABASE_NAME
    DATABASE_NAME = value

    return previous


def _connect():

    target = path()

    directory = os.path.dirname(os.path.abspath(target))

    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(target, timeout=30)

    # WAL so the weekly writer never blocks a reader mid-scan. Best-effort:
    # a store on a network share may refuse, and a refused journal mode is not
    # a reason to fail a read.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass

    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _dumps(value):
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _loads(text):
    if not text:
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


_schema_ready = set()


def create_database():
    """Create the three stages and the refresh log. Idempotent.

    Once per store file per process, like database.create_database: every
    load_metrics ran the whole schema script, 1,510 times in one /api/outlook
    (2026-11-11). The file must still exist for the shortcut to hold, so a
    test that deletes and recreates its store gets a schema.
    """

    target = path()
    if target in _schema_ready and os.path.exists(target):
        return

    conn = _connect()

    cursor = conn.cursor()

    # ---- stage 1: what the collector downloaded --------------------------
    #
    # Keyed on the tracing number, which is Codal's own identifier for a
    # letter. Never keyed on the period: the unaudited and audited releases of
    # one fiscal year are two letters published weeks apart, and collapsing
    # them would move the first release forward in time -- the same look-ahead
    # that codal_filings in the market database exists to avoid.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raw_filings (
        tracing_no    TEXT PRIMARY KEY,
        ins_code      TEXT NOT NULL,
        symbol        TEXT,
        letter_code   TEXT,
        title         TEXT,
        period_end    TEXT,
        period_months INTEGER,
        published_at  TEXT,
        audited       INTEGER,
        consolidated  INTEGER,
        payload       TEXT,
        source        TEXT,
        fetched_at    TEXT,
        processed_at  TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_company "
        "ON raw_filings (ins_code, published_at)"
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_unprocessed "
        "ON raw_filings (processed_at)"
    )

    # ---- stage 2: what the processor could actually compute ---------------
    columns = ",\n        ".join("%s REAL" % name for name in METRIC_COLUMNS)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS metrics (
        ins_code      TEXT NOT NULL,
        period_end    TEXT NOT NULL,
        period_months INTEGER,
        tracing_no    TEXT,
        symbol        TEXT,
        title         TEXT,
        published_at  TEXT,
        audited       INTEGER,
        consolidated  INTEGER,
        items         TEXT,
        ratios        TEXT,
        source        TEXT,
        updated_at    TEXT,
        %s,
        PRIMARY KEY (ins_code, period_end, period_months)
    )
    """ % columns)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_metrics_published "
        "ON metrics (ins_code, published_at)"
    )

    # ---- the failure log -------------------------------------------------
    #
    # `attempts` and the two timestamps are what make a repeated failure
    # legible: one attempt is a filing nobody has looked at twice, and forty
    # attempts on the same reason is a queue eating itself.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS processing_failures (
        tracing_no    TEXT PRIMARY KEY,
        ins_code      TEXT,
        symbol        TEXT,
        stage         TEXT,
        reason        TEXT NOT NULL,
        detail        TEXT,
        transient     INTEGER NOT NULL DEFAULT 0,
        attempts      INTEGER NOT NULL DEFAULT 1,
        first_seen    TEXT,
        last_seen     TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_failures_reason "
        "ON processing_failures (reason)"
    )

    # ---- the weekly cadence ----------------------------------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS refresh_log (
        scope        TEXT NOT NULL,
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        status       TEXT,
        companies    INTEGER,
        filings      INTEGER,
        failures     INTEGER,
        detail       TEXT,
        PRIMARY KEY (scope, started_at)
    )
    """)

    conn.commit()
    conn.close()

    _schema_ready.add(target)


def exists():
    """Is there a store on disk yet? Never creates one to answer."""

    return os.path.isfile(path())


# ==============================
# STAGE 1 -- THE COLLECTOR
# ==============================

def save_raw_filing(record):
    """Record one downloaded letter. Returns True when a row was written.

    An existing tracing number is refreshed rather than duplicated, but
    `processed_at` is deliberately NOT cleared: re-downloading a letter does not
    un-parse it, and clearing it would put every already-processed filing back
    in the queue on the next collector run.
    """

    if not record or not record.get("tracing_no"):
        return False

    create_database()

    conn = _connect()

    conn.execute("""
        INSERT INTO raw_filings
            (tracing_no, ins_code, symbol, letter_code, title, period_end,
             period_months, published_at, audited, consolidated, payload,
             source, fetched_at, processed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
        ON CONFLICT(tracing_no) DO UPDATE SET
            ins_code      = excluded.ins_code,
            symbol        = COALESCE(excluded.symbol, raw_filings.symbol),
            letter_code   = COALESCE(excluded.letter_code, raw_filings.letter_code),
            title         = COALESCE(excluded.title, raw_filings.title),
            period_end    = COALESCE(excluded.period_end, raw_filings.period_end),
            period_months = COALESCE(excluded.period_months, raw_filings.period_months),
            published_at  = COALESCE(excluded.published_at, raw_filings.published_at),
            audited       = COALESCE(excluded.audited, raw_filings.audited),
            consolidated  = COALESCE(excluded.consolidated, raw_filings.consolidated),
            payload       = COALESCE(excluded.payload, raw_filings.payload),
            source        = COALESCE(excluded.source, raw_filings.source),
            fetched_at    = excluded.fetched_at
    """, (
        str(record["tracing_no"]),
        str(record.get("ins_code") or ""),
        record.get("symbol"),
        record.get("letter_code"),
        record.get("title"),
        record.get("period_end"),
        record.get("period_months"),
        record.get("published_at"),
        None if record.get("audited") is None else int(bool(record["audited"])),
        None if record.get("consolidated") is None else int(bool(record["consolidated"])),
        _dumps(record.get("payload")),
        record.get("source") or "codal",
        _now(),
    ))

    conn.commit()
    conn.close()

    return True


def load_raw_filings(ins_code=None, unprocessed_only=False, limit=None,
                     exclude_settled=True):
    """Letters in the store, oldest publication first.

    `exclude_settled` drops the ones a non-transient failure has already
    settled. That is the loop guard: without it, a filing that cannot be parsed
    is handed back to the processor on every single run, forever, and the queue
    never drains.
    """

    if not exists():
        return []

    create_database()

    conn = _connect()

    query = [
        "SELECT tracing_no, ins_code, symbol, letter_code, title, period_end,",
        "       period_months, published_at, audited, consolidated, payload,",
        "       source, fetched_at, processed_at",
        "FROM raw_filings WHERE 1=1",
    ]

    params = []

    if ins_code is not None:
        query.append("AND ins_code = ?")
        params.append(str(ins_code))

    if unprocessed_only:
        query.append("AND processed_at IS NULL")

    if exclude_settled:
        query.append(
            "AND tracing_no NOT IN "
            "(SELECT tracing_no FROM processing_failures WHERE transient = 0)"
        )

    query.append("ORDER BY published_at IS NULL, published_at, tracing_no")

    if limit:
        query.append("LIMIT ?")
        params.append(int(limit))

    rows = conn.execute("\n".join(query), params).fetchall()

    conn.close()

    return [
        {
            "tracing_no": row[0],
            "ins_code": row[1],
            "symbol": row[2],
            "letter_code": row[3],
            "title": row[4],
            "period_end": row[5],
            "period_months": row[6],
            "published_at": row[7],
            "audited": None if row[8] is None else bool(row[8]),
            "consolidated": None if row[9] is None else bool(row[9]),
            "payload": _loads(row[10]),
            "source": row[11],
            "fetched_at": row[12],
            "processed_at": row[13],
        }
        for row in rows
    ]


def mark_processed(tracing_no, when=None):
    """Stamp a letter as having been through the processor.

    Says nothing about the outcome -- that is what `metrics` and
    `processing_failures` are for. Conflating the two is the bug this schema is
    shaped to prevent.
    """

    if not tracing_no:
        return False

    create_database()

    conn = _connect()

    cursor = conn.execute(
        "UPDATE raw_filings SET processed_at = ? WHERE tracing_no = ?",
        (when or _now(), str(tracing_no))
    )

    conn.commit()

    changed = cursor.rowcount

    conn.close()

    return bool(changed)


# ==============================
# STAGE 2 -- THE PROCESSOR'S OUTPUT
# ==============================

def save_metrics(record):
    """Upsert one company-period of computed figures. The SUCCESS path.

    Refuses a record with no ratios and no items: an empty row in `metrics`
    would be counted as coverage by every downstream report, which is worse than
    the absence it is standing in for.
    """

    if not record or not record.get("ins_code") or not record.get("period_end"):
        return False

    ratios = record.get("ratios") or {}
    items = record.get("items") or {}

    if not ratios and not items:
        return False

    create_database()

    scalars = [_finite(ratios.get(name)) for name in METRIC_COLUMNS]

    names = ", ".join(METRIC_COLUMNS)
    placeholders = ", ".join("?" for _ in METRIC_COLUMNS)
    updates = ", ".join("%s = excluded.%s" % (n, n) for n in METRIC_COLUMNS)

    conn = _connect()

    conn.execute("""
        INSERT INTO metrics
            (ins_code, period_end, period_months, tracing_no, symbol, title,
             published_at, audited, consolidated, items, ratios, source,
             updated_at, %s)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,%s)
        ON CONFLICT(ins_code, period_end, period_months) DO UPDATE SET
            tracing_no   = COALESCE(excluded.tracing_no, metrics.tracing_no),
            symbol       = COALESCE(excluded.symbol, metrics.symbol),
            title        = COALESCE(excluded.title, metrics.title),
            published_at = COALESCE(excluded.published_at, metrics.published_at),
            audited      = COALESCE(excluded.audited, metrics.audited),
            consolidated = COALESCE(excluded.consolidated, metrics.consolidated),
            items        = excluded.items,
            ratios       = excluded.ratios,
            source       = COALESCE(excluded.source, metrics.source),
            updated_at   = excluded.updated_at,
            %s
    """ % (names, placeholders, updates), tuple([
        str(record["ins_code"]),
        str(record["period_end"]),
        record.get("period_months"),
        record.get("tracing_no"),
        record.get("symbol"),
        record.get("title"),
        record.get("published_at"),
        None if record.get("audited") is None else int(bool(record["audited"])),
        None if record.get("consolidated") is None else int(bool(record["consolidated"])),
        _dumps(items),
        _dumps(ratios),
        record.get("source") or "codal",
        _now(),
    ] + scalars))

    conn.commit()
    conn.close()

    # A filing that produced figures is settled in the other direction too: an
    # earlier transient failure on it is no longer true.
    clear_failure(record.get("tracing_no"))

    return True


def load_metrics(ins_code, limit=None, before=None):
    """Stored periods for one company, newest period first.

    `before` filters on PUBLICATION time, not on period end -- the only cut that
    means "what was knowable then". A filter on period_end would admit a
    statement for a quarter that had ended but not yet been filed, which is the
    classic fundamental look-ahead.
    """

    if not exists():
        return []

    create_database()

    conn = _connect()

    query = [
        "SELECT ins_code, period_end, period_months, tracing_no, symbol, title,",
        "       published_at, audited, consolidated, items, ratios, source,",
        "       updated_at",
        "FROM metrics WHERE ins_code = ?",
    ]

    params = [str(ins_code)]

    if before is not None:
        query.append("AND published_at IS NOT NULL AND published_at < ?")
        params.append(str(before))

    query.append("ORDER BY period_end DESC, period_months DESC")

    if limit:
        query.append("LIMIT ?")
        params.append(int(limit))

    rows = conn.execute("\n".join(query), params).fetchall()

    conn.close()

    return [_metric_row(row) for row in rows]


def _metric_row(row):
    return {
        "ins_code": row[0],
        "period_end": row[1],
        "period_months": row[2],
        "tracing_no": row[3],
        "symbol": row[4],
        "title": row[5],
        "published_at": row[6],
        "audited": None if row[7] is None else bool(row[7]),
        "consolidated": None if row[8] is None else bool(row[8]),
        "items": _loads(row[9]),
        "ratios": _loads(row[10]),
        "source": row[11],
        "updated_at": row[12],
    }


def load_latest_metrics(ins_codes=None):
    """{ins_code: newest record} -- the only call the trading path should make.

    One query for the whole basket. The newest period per company is chosen in
    SQL rather than by loading every period and sorting in Python, because on a
    universe of ~800 names that is the difference between milliseconds and
    seconds on the critical path.
    """

    if not exists():
        return {}

    create_database()

    conn = _connect()

    query = [
        "SELECT m.ins_code, m.period_end, m.period_months, m.tracing_no,",
        "       m.symbol, m.title, m.published_at, m.audited, m.consolidated,",
        "       m.items, m.ratios, m.source, m.updated_at",
        "FROM metrics m",
        "JOIN (SELECT ins_code, MAX(period_end) AS newest",
        "      FROM metrics GROUP BY ins_code) latest",
        "  ON m.ins_code = latest.ins_code AND m.period_end = latest.newest",
    ]

    params = []

    codes = [str(c) for c in (ins_codes or [])]

    if codes:
        query.append("WHERE m.ins_code IN (%s)"
                     % ",".join("?" for _ in codes))
        params.extend(codes)

    # Two rows can share the newest period_end when a company files both a
    # 6-month and a 12-month statement for it. The longer period wins, since it
    # is the one whose figures are annual rather than partial.
    query.append("ORDER BY m.ins_code, m.period_months DESC")

    rows = conn.execute("\n".join(query), params).fetchall()

    conn.close()

    out = {}

    for row in rows:
        record = _metric_row(row)
        out.setdefault(record["ins_code"], record)

    return out


def companies():
    """Every ins_code with at least one usable period."""

    if not exists():
        return []

    create_database()

    conn = _connect()

    rows = conn.execute(
        "SELECT DISTINCT ins_code FROM metrics ORDER BY ins_code"
    ).fetchall()

    conn.close()

    return [row[0] for row in rows]


# ==============================
# THE FAILURE LOG
# ==============================

def record_failure(tracing_no, ins_code=None, reason="unknown", detail=None,
                   stage="process", symbol=None, transient=None):
    """Log why a filing did not become figures.

    `transient` defaults to whether the reason is in TRANSIENT_REASONS, so a
    caller that classifies its errors gets the loop guard for free and one that
    does not still gets a settled row for an unrecognised reason. Erring toward
    settled is deliberate: a filing wrongly retired shows up as missing
    coverage, while a filing wrongly retried shows up as a run that never ends.
    """

    if not tracing_no:
        return False

    reason = str(reason or "unknown")

    if transient is None:
        transient = reason in TRANSIENT_REASONS

    create_database()

    conn = _connect()

    conn.execute("""
        INSERT INTO processing_failures
            (tracing_no, ins_code, symbol, stage, reason, detail, transient,
             attempts, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,1,?,?)
        ON CONFLICT(tracing_no) DO UPDATE SET
            ins_code  = COALESCE(excluded.ins_code, processing_failures.ins_code),
            symbol    = COALESCE(excluded.symbol, processing_failures.symbol),
            stage     = excluded.stage,
            reason    = excluded.reason,
            detail    = excluded.detail,
            transient = excluded.transient,
            attempts  = processing_failures.attempts + 1,
            last_seen = excluded.last_seen
    """, (
        str(tracing_no), None if ins_code is None else str(ins_code), symbol,
        stage, reason, detail, int(bool(transient)), _now(), _now(),
    ))

    conn.commit()
    conn.close()

    return True


def clear_failure(tracing_no):
    """Forget a failure, because the filing has since produced figures."""

    if not tracing_no or not exists():
        return False

    conn = _connect()

    try:
        cursor = conn.execute(
            "DELETE FROM processing_failures WHERE tracing_no = ?",
            (str(tracing_no),)
        )
        conn.commit()
        changed = cursor.rowcount
    except sqlite3.DatabaseError:
        changed = 0
    finally:
        conn.close()

    return bool(changed)


def settled_failures():
    """Tracing numbers no further attempt can change. Subtract from any queue."""

    if not exists():
        return set()

    create_database()

    conn = _connect()

    rows = conn.execute(
        "SELECT tracing_no FROM processing_failures WHERE transient = 0"
    ).fetchall()

    conn.close()

    return {row[0] for row in rows}


def failure_summary():
    """Failures grouped by reason, worst first, plus the retryable count."""

    if not exists():
        return {"total": 0, "settled": 0, "transient": 0, "by_reason": []}

    create_database()

    conn = _connect()

    rows = conn.execute(
        "SELECT reason, COUNT(*), SUM(attempts), MAX(transient) "
        "FROM processing_failures GROUP BY reason ORDER BY COUNT(*) DESC"
    ).fetchall()

    total, settled = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN transient = 0 THEN 1 ELSE 0 END) "
        "FROM processing_failures"
    ).fetchone()

    conn.close()

    total = total or 0
    settled = settled or 0

    return {
        "total": total,
        "settled": settled,
        "transient": total - settled,
        "by_reason": [
            {
                "reason": row[0],
                "count": row[1],
                "attempts": row[2],
                "transient": bool(row[3]),
            }
            for row in rows
        ],
    }


# ==============================
# THE WEEKLY CADENCE
# ==============================

def start_refresh(scope=SCOPE_PROCESS):
    """Open a refresh row and return its key (the started_at stamp)."""

    create_database()

    started = _now()

    conn = _connect()

    conn.execute(
        "INSERT OR REPLACE INTO refresh_log "
        "(scope, started_at, finished_at, status, companies, filings, "
        " failures, detail) VALUES (?,?,NULL,NULL,NULL,NULL,NULL,NULL)",
        (scope, started)
    )

    conn.commit()
    conn.close()

    return started


def finish_refresh(scope, started_at, status=STATUS_OK, companies=None,
                   filings=None, failures=None, detail=None):
    """Close a refresh row. An unclosed row is what `needs_refresh` reads as a
    crashed run, so this must be called even on the failure path."""

    if not started_at:
        return False

    create_database()

    conn = _connect()

    conn.execute(
        "UPDATE refresh_log SET finished_at = ?, status = ?, companies = ?, "
        "filings = ?, failures = ?, detail = ? "
        "WHERE scope = ? AND started_at = ?",
        (_now(), status, companies, filings, failures, detail, scope, started_at)
    )

    conn.commit()
    conn.close()

    return True


def last_refresh(scope=SCOPE_PROCESS):
    """The newest COMPLETED refresh for a scope, or None."""

    if not exists():
        return None

    create_database()

    conn = _connect()

    row = conn.execute(
        "SELECT scope, started_at, finished_at, status, companies, filings, "
        "failures, detail FROM refresh_log "
        "WHERE scope = ? AND finished_at IS NOT NULL "
        "ORDER BY finished_at DESC LIMIT 1",
        (scope,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    return {
        "scope": row[0],
        "started_at": row[1],
        "finished_at": row[2],
        "status": row[3],
        "companies": row[4],
        "filings": row[5],
        "failures": row[6],
        "detail": row[7],
    }


def needs_refresh(scope=SCOPE_PROCESS, days=None, now=None):
    """Is the fundamental picture older than the weekly cadence allows?

    Returns (bool, reason). The reason is the part callers should print: "never
    run", "last run failed" and "eight days old" are three different situations
    and only one of them is routine.
    """

    days = REFRESH_INTERVAL_DAYS if days is None else days

    record = last_refresh(scope)

    if record is None:
        return True, "never run: no %s pass has ever completed" % scope

    if record.get("status") == STATUS_FAILED:
        return True, ("the last %s run finished as FAILED (%s)"
                      % (scope, record.get("detail") or "no detail"))

    stamp = record.get("finished_at")

    try:
        finished = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return True, "the last %s run has an unreadable timestamp %r" % (
            scope, stamp)

    reference = now or datetime.now()

    age = reference - finished

    if age >= timedelta(days=days):
        return True, ("the last %s run finished %.1f days ago (cadence is %d)"
                      % (scope, age.total_seconds() / 86400.0, days))

    return False, ("the last %s run finished %.1f days ago"
                   % (scope, age.total_seconds() / 86400.0))


# ==============================
# REPORTING
# ==============================

def coverage():
    """One dict describing what this store holds and what it failed on.

    `parse_rate` is the number that matters and the one a `parsed` flag cannot
    produce: filings that yielded figures, over filings that were attempted.
    """

    if not exists():
        return {
            "exists": False,
            "path": path(),
            "raw_filings": 0,
            "processed": 0,
            "companies_with_metrics": 0,
            "periods": 0,
            "failures": {"total": 0, "settled": 0, "transient": 0,
                         "by_reason": []},
            "parse_rate": None,
            "last_collect": None,
            "last_process": None,
        }

    create_database()

    conn = _connect()

    raw, processed = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN processed_at IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM raw_filings"
    ).fetchone()

    periods, with_metrics = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ins_code) FROM metrics"
    ).fetchone()

    conn.close()

    raw = raw or 0
    processed = processed or 0

    failures = failure_summary()

    # Attempted = processed. Of those, the ones that produced a row minus the
    # ones that produced a failure row; a filing can do neither only if the
    # processor crashed between the two writes.
    attempted = processed

    return {
        "exists": True,
        "path": path(),
        "raw_filings": raw,
        "processed": processed,
        "unprocessed": raw - processed,
        "companies_with_metrics": with_metrics or 0,
        "periods": periods or 0,
        "failures": failures,
        "parse_rate": (round((attempted - failures["total"]) / float(attempted), 4)
                       if attempted else None),
        "last_collect": last_refresh(SCOPE_COLLECT),
        "last_process": last_refresh(SCOPE_PROCESS),
    }


def sync_from_market_db(verbose=False):
    """Carry only the filings this store has not seen yet. Cheap; run often.

    Found 2026-11-05: this store was last written on 2026-08-14 while the
    collectors kept filling sirat_market.db, so the analysis read a
    three-month-old snapshot. The one-off import below was never scheduled.
    This is the incremental form, called after every collection round.

    A filing is imported once (the `imported_letters` ledger). Within a
    company-period the most recently PUBLISHED letter wins -- the backfill
    delivers old letters after new ones, and an unaudited 1398 release
    arriving today must not overwrite its audited successor.
    """

    import database

    create_database()

    conn = _connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS imported_letters (
        tracing_no TEXT PRIMARY KEY, imported_at TEXT)""")
    seen = {row[0]: row[1] or "" for row in conn.execute(
        "SELECT tracing_no, imported_at FROM imported_letters")}
    current = {(r[0], r[1], r[2]): (r[3] or "", r[4]) for r in conn.execute(
        "SELECT ins_code, period_end, period_months, published_at, tracing_no "
        "FROM metrics")}
    conn.close()

    held_items = {}

    def _held(key):
        """The stored parse of a period, read only when a choice needs it."""
        if key not in held_items:
            c = _connect()
            found = c.execute(
                "SELECT items FROM metrics WHERE ins_code = ? AND "
                "period_end = ? AND period_months IS ?", key).fetchone()
            c.close()
            try:
                held_items[key] = json.loads(found[0]) if found and found[0] else {}
            except (TypeError, ValueError):
                held_items[key] = {}
        return held_items[key]

    # New, or re-parsed since it was carried (codal_backfill.reparse_latest
    # re-reads a filing for line items added after its first parse).
    source = database.load_codal_filings()
    fresh = [row for row in source
             if row.get("tracing_no")
             and (str(row["tracing_no"]) not in seen
                  or (row.get("updated_at") or "") > seen[str(row["tracing_no"])])]

    imported = superseded = thin = empty = 0
    done = []

    for row in sorted(fresh, key=lambda r: r.get("published_at") or ""):
        key = (str(row.get("ins_code")), str(row.get("period_end")),
               row.get("period_months"))
        published = row.get("published_at") or ""
        items = row.get("items") or {}
        held = current.get(key)
        tracing = None if row.get("tracing_no") is None else str(row["tracing_no"])
        other = held is not None and held[1] != tracing

        # No exemption for a letter already carried. Re-parsing the release
        # the store holds passes anyway (same stamp); re-parsing an OLDER one
        # (codal_backfill --reparse-latest reads each company's newest
        # filings, which can include a superseded release of a period) is
        # exactly what must not displace the newer. The exemption allowed
        # that: --reparse-latest 4 would have put 20 older releases back,
        # 8 would have put 64 (2026-11-11).
        #
        # The same choice as database.save_codal_financials, so metrics and
        # codal_financials hold the same release (2026-11-11): a release whose
        # parse lacks a statement the stored one has is a failed read, not
        # the company's word, and does not replace it; an OLDER release may
        # replace a stored one that lacks a statement it has. Newest-wins
        # alone made the outcome depend on write order: 103 periods held a
        # newest release with no income statement (or balance sheet, or cash
        # flow) beside an older complete one, فوکا's latest 1405/06/31 among
        # them.
        lost = database.statements_lost(_held(key), items) if other else []
        gained = database.statements_lost(items, _held(key)) if other else []
        if lost:
            thin += 1
        elif other and held[0] > published and not gained:
            superseded += 1
        elif save_metrics({
            "ins_code": row.get("ins_code"),
            "period_end": row.get("period_end"),
            "period_months": row.get("period_months"),
            "tracing_no": row.get("tracing_no"),
            "title": row.get("title"),
            "published_at": published or None,
            "audited": row.get("audited"),
            "consolidated": row.get("consolidated"),
            "items": items,
            "ratios": row.get("ratios") or {},
            "source": "market-db",
        }):
            imported += 1
            current[key] = (published, tracing)
            held_items[key] = items
        else:
            empty += 1

        # The SOURCE row's own timestamp, so the next comparison is between
        # two values in one clock and one format (this module's _now() is
        # local time, the market database's is UTC).
        done.append((str(row["tracing_no"]), row.get("updated_at") or ""))

    if done:
        conn = _connect()
        conn.executemany(
            "INSERT OR REPLACE INTO imported_letters VALUES (?, ?)", done)
        conn.commit()
        conn.close()

    result = {"new": len(fresh), "imported": imported,
              "superseded": superseded, "thin": thin, "empty": empty}

    # Every release as well, not only the period's winner: the point-in-time
    # store (pit_fundamentals). A failure there is reported, never allowed
    # to undo or block the import above.
    try:
        result["versions"] = sync_versions(source)
    except (sqlite3.DatabaseError, OSError) as exc:
        result["versions"] = {"error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160])}

    if verbose:
        print("fundamental store: %(new)d new letters, %(imported)d imported, "
              "%(superseded)d older than what is stored, %(thin)d lacking a "
              "statement the stored release has, %(empty)d empty" % result)
        print("versions: %s" % (result["versions"],))

    return result


# ==============================
# EVERY RELEASE, AS PUBLISHED (2026-11-24)
# ==============================
#
# `metrics` keeps ONE row per company-period, the most recently published
# release, and everything downstream reads it. That is right for "what does
# the company say today" and wrong for "what could anyone have known on
# date D": 7,838 of 16,060 company-periods have more than one release
# (unaudited, audited, amended; separate and consolidated), and in 40% of
# the same-basis ones the net profit moved by more than 1% between the
# first release and the last. The outlook's error band was measured on the
# last word for both its inputs and its "actual" -- look-ahead twice.
#
# metric_versions mirrors codal_filings (the market database's per-letter
# table) here, beside `metrics`, one row per tracing number, with a sortable
# Gregorian publication stamp. Read through pit_fundamentals. Created by the
# sync, not by create_database(), like imported_letters: a reader never
# needs to write, and a read-only store (the test suite's view of the live
# one) never meets a CREATE it cannot run.

VERSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_versions (
    tracing_no        TEXT PRIMARY KEY,
    ins_code          TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    period_months     INTEGER,
    consolidated      INTEGER,
    audited           INTEGER,
    published_at      TEXT,
    published_iso     TEXT,
    title             TEXT,
    items             TEXT,
    ratios            TEXT,
    source_updated_at TEXT,
    synced_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_versions_period
    ON metric_versions (ins_code, period_end, period_months, published_iso);
CREATE INDEX IF NOT EXISTS idx_versions_published
    ON metric_versions (published_iso);
"""


def published_iso(published_at):
    """Codal's Jalali stamp ('1400/01/30 12:18:18') as a sortable Gregorian
    'YYYY-MM-DDTHH:MM:SS', or None. A stamp without a clock is the END of
    its day (jalali.parse), so a same-day decision cannot claim to have
    seen it."""

    import jalali

    stamp = jalali.parse(published_at)
    return stamp.isoformat(timespec="seconds") if stamp else None


def sync_versions(rows=None):
    """Mirror codal_filings into metric_versions. Incremental; cheap.

    `rows` is the WHOLE codal_filings table as database.load_codal_filings()
    returns it (sync_from_market_db passes the copy it already read); a
    subset would read as deletions. A release is written when its tracing
    number is new or its source row was re-parsed since (updated_at newer),
    and a tracing number no longer in codal_filings is dropped: the
    subsidiary purge (codal_backfill.purge_subsidiaries) deletes letters
    there, and another company's statement must not linger here either.
    An empty source deletes nothing -- an unreadable table is not a purge.

    Returns {written, new, removed, undated}: `undated` counts releases
    whose published_at did not parse (stored, but invisible to as_of).
    """

    import database

    if rows is None:
        rows = database.load_codal_filings()

    conn = _connect()
    try:
        conn.executescript(VERSIONS_SCHEMA)
        held = {str(t): (stamp or "") for t, stamp in conn.execute(
            "SELECT tracing_no, source_updated_at FROM metric_versions")}

        now = _now()
        batch = []
        source_ids = set()
        undated = 0
        for row in rows:
            tracing = row.get("tracing_no")
            if tracing is None or not row.get("period_end"):
                continue
            tracing = str(tracing)
            source_ids.add(tracing)
            stamp = row.get("updated_at") or ""
            if tracing in held and held[tracing] >= stamp:
                continue
            iso = published_iso(row.get("published_at"))
            if iso is None:
                undated += 1
            batch.append((
                tracing, str(row.get("ins_code")), str(row["period_end"]),
                row.get("period_months"),
                None if row.get("consolidated") is None
                else int(bool(row["consolidated"])),
                None if row.get("audited") is None else int(bool(row["audited"])),
                row.get("published_at"), iso, row.get("title"),
                _dumps(row.get("items") or {}), _dumps(row.get("ratios") or {}),
                stamp, now))

        if batch:
            conn.executemany("""
                INSERT INTO metric_versions
                    (tracing_no, ins_code, period_end, period_months,
                     consolidated, audited, published_at, published_iso, title,
                     items, ratios, source_updated_at, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tracing_no) DO UPDATE SET
                    ins_code = excluded.ins_code,
                    period_end = excluded.period_end,
                    period_months = excluded.period_months,
                    consolidated = excluded.consolidated,
                    audited = excluded.audited,
                    published_at = COALESCE(excluded.published_at,
                                            metric_versions.published_at),
                    published_iso = COALESCE(excluded.published_iso,
                                             metric_versions.published_iso),
                    title = excluded.title, items = excluded.items,
                    ratios = excluded.ratios,
                    source_updated_at = excluded.source_updated_at,
                    synced_at = excluded.synced_at
            """, batch)

        gone = sorted(set(held) - source_ids) if source_ids else []
        if gone:
            conn.executemany("DELETE FROM metric_versions WHERE tracing_no = ?",
                             [(t,) for t in gone])
        conn.commit()
    finally:
        conn.close()

    return {"written": len(batch),
            "new": sum(1 for b in batch if b[0] not in held),
            "removed": len(gone), "undated": undated}


def import_from_market_db(limit=None, verbose=True):
    """Seed this store from the filings already parsed into sirat_market.db.

    Migration, not duplication: everything `codal_filings` holds was expensive
    to acquire and there is no reason to download it twice. Reads the
    per-letter table rather than `codal_financials`, because that one collapses
    the unaudited and audited releases of a period into a single row and loses
    the publication date the point-in-time contract runs on.
    """

    import database

    create_database()

    rows = database.load_codal_filings()

    if limit:
        rows = rows[:int(limit)]

    imported = 0
    skipped = 0

    for row in rows:

        record = {
            "ins_code": row.get("ins_code"),
            "period_end": row.get("period_end"),
            "period_months": row.get("period_months"),
            "tracing_no": row.get("tracing_no"),
            "title": row.get("title"),
            "published_at": row.get("published_at"),
            "audited": row.get("audited"),
            "consolidated": row.get("consolidated"),
            "items": row.get("items") or {},
            "ratios": row.get("ratios") or {},
            "source": "market-db",
        }

        if save_metrics(record):
            imported += 1
        else:
            skipped += 1

    if verbose:
        print("imported %d filings into %s (%d skipped as empty)"
              % (imported, path(), skipped))

    return {"imported": imported, "skipped": skipped, "seen": len(rows)}


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(
        description="The fundamental store: coverage, failures, cadence.")

    parser.add_argument("--create", action="store_true",
                        help="create the store and exit")
    parser.add_argument("--import-market-db", action="store_true",
                        help="seed from filings already parsed in sirat_market.db")
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    if args.create:
        create_database()
        print("created %s" % path())
        raise SystemExit(0)

    if args.import_market_db:
        import_from_market_db(limit=args.limit)

    report = coverage()

    print("=" * 74)
    print("SIRAT AI — پایگاه داده بنیادی / FUNDAMENTAL STORE")
    print("=" * 74)

    if not report["exists"]:
        print("\n  no store yet at %s" % report["path"])
        print("  create it with:  python fundamental_db.py --create")
        raise SystemExit(0)

    print("\n  file              %s" % report["path"])
    print("  raw filings       %d  (%d processed, %d waiting)"
          % (report["raw_filings"], report["processed"], report["unprocessed"]))
    print("  usable periods    %d across %d companies"
          % (report["periods"], report["companies_with_metrics"]))

    if report["parse_rate"] is not None:
        print("  parse rate        %.1f%%" % (report["parse_rate"] * 100))

    failures = report["failures"]

    print("\n  failures          %d  (%d settled, %d retryable)"
          % (failures["total"], failures["settled"], failures["transient"]))

    for row in failures["by_reason"][:10]:
        print("    %-28s %5d  (%s)"
              % (row["reason"], row["count"],
                 "retryable" if row["transient"] else "settled"))

    for scope in (SCOPE_COLLECT, SCOPE_PROCESS):
        stale, reason = needs_refresh(scope)
        print("\n  %-8s %s" % (scope, "DUE" if stale else "fresh"))
        print("           %s" % reason)
