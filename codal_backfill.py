"""Historical Codal backfill: reconstruct what each company had filed, and when.

WHY THE DAILY SYNC CANNOT DO THIS
---------------------------------
codal_sync.py keeps the mirror CURRENT. It asks Codal "what is new" once a day
and downloads at most two statements per company, which is exactly right for a
panel that wants today's figures and exactly useless for a backtest that wants
1403's. Run against a cold database it converges on the present and never walks
backwards, because nothing in it ever asks for a filing older than the newest
one it already holds.

The measured consequence, in this database: 89% of stored filings first became
public in 1405. A backtest starting 1403/06 therefore had a point-in-time
fundamental reading for 12.7% of its decisions, and the fundamental variant was
not measuring a fundamental model -- it was measuring whichever companies
happened to have filed inside the last few months.

WHAT MAKES THE BACKFILL CHEAP ENOUGH TO RUN
-------------------------------------------
Codal's search accepts `LetterCode`, and filtering to ن-۱۰ changes the
arithmetic completely. Unfiltered, one page of 20 announcements covers about
two MONTHS of a company's filings -- a company files monthly sales reports,
board reports, dividend schedules and meeting decisions, and the statements are
a twentieth of the traffic. Filtered, one page of 20 statements covers about
five YEARS. Verified against فولاد: page 1 unfiltered reaches 1405/04, page 1
filtered reaches 1403/04.

So the discovery half of this job is roughly one request per company, not
thirty.

WHAT IT STILL COSTS
-------------------
The download half is unavoidable: a statement is 0.5-1 MB and there is no
endpoint that returns figures without the document. At Codal's tolerated rate
that is a few seconds each, and a universe-wide backfill is measured in hours.
The queue is therefore ordered so that an interrupted run is still worth
having: every company's OLDEST in-window filing is fetched before any company's
second one, because a filing stays the point-in-time answer until it is
superseded -- so the first pass is what lifts coverage across the whole window,
and every pass after it only improves freshness.

NOTHING HERE INVENTS A DATE
---------------------------
A filing is stored with the publication timestamp Codal reports and no other.
Where that timestamp is missing the filing is stored without one and
backtesting/pit.py refuses it under the default policy, which is the same rule
the rest of the project already applies.
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone

import codal
import codal_sync
import config
import database
import jalali


# Statement filings only. The monthly sales report (ن-۳۰) carries a revenue
# line and nothing else, so it cannot produce a fundamental score, and it is
# five times as numerous -- including it would multiply the download bill to buy
# a single field.
STATEMENT_CODE = "ن-10"

# Codal returns 20 rows per page whatever `Length` says.
PAGE_SIZE = 20

# Stop paging a company once the page's oldest row predates the window. One
# extra page beyond the boundary is requested deliberately: publication order
# and period order are not the same, and a late-filed old statement can sit
# below a boundary-crossing row.
MAX_PAGES = 8


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _day(stamp):
    """The date half of a Jalali timestamp: '1403/03/27 22:30:59' -> '1403/03/27'.

    Compared as a string, which is safe here and only here: Jalali dates in
    this format are zero-padded and fixed-width, so lexical order is
    chronological order. Nothing else in this project compares dates that way.
    """

    return (str(stamp or "")).strip()[:10]


# ==============================
# STATE
# ==============================

def _ensure_state_table():
    """Where the discovery half remembers how far back it has already looked."""

    conn = database._connect()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS codal_backfill_state (
        ins_code    TEXT PRIMARY KEY,
        symbol      TEXT,
        searched_to TEXT,
        pages       INTEGER,
        letters     INTEGER,
        error       TEXT,
        updated_at  TEXT
    )
    """)

    # FILINGS THIS TOOL HAS TRIED AND CANNOT READ.
    #
    # Not the same thing as codal_letters.parsed, and conflating them cost a
    # six-hour run that stored nothing. `parsed` means "some version of this
    # project once attempted this", which includes attempts made before
    # codal_filings existed and attempts that met a firewall page -- so
    # pending() deliberately ignores it, in order to retry those.
    #
    # The consequence, unnoticed until a campaign ran overnight: a filing that
    # genuinely will not parse is marked parsed, is still absent from
    # codal_filings, and so comes back to the head of the queue on the next
    # round. Twenty-two consecutive rounds fetched the same seven documents,
    # failed on them identically, and never reached anything behind them.
    #
    # This table is the distinction that was missing: a NON-TRANSIENT failure
    # recorded here settles that filing, while a legacy `parsed` flag settles
    # nothing.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS codal_backfill_failures (
        tracing_no TEXT PRIMARY KEY,
        ins_code   TEXT,
        reason     TEXT,
        attempts   INTEGER DEFAULT 1,
        last_tried TEXT
    )
    """)

    # FILINGS THAT FAILED IN A WAY WORTH RETRYING -- AND THAT STILL MUST NOT
    # HOLD THE HEAD OF THE QUEUE.
    #
    # A transient failure settles nothing, rightly: a 429 or a timeout says
    # nothing about the document. But some documents answer HTTP 500 every
    # time -- Codal's exporter fails on them -- and a 500 is classed transient.
    # Observed 2026-11-06: the same five filings at the head of the queue
    # answered 500 for thirteen hours, every round read "5 of 5 refused", the
    # campaign backed off to four hours at a time as if throttled, and a
    # filing further down downloaded in under a second. Anything recorded here
    # is retried only after every untried filing.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS codal_backfill_retries (
        tracing_no TEXT PRIMARY KEY,
        ins_code   TEXT,
        last_error TEXT,
        attempts   INTEGER DEFAULT 1,
        last_tried TEXT
    )
    """)

    # FILINGS CODAL'S EXPORTER ANSWERS EMPTY (2026-11-25): the days each one
    # has answered the same empty document (codal.exporter_empty), so the
    # third day settles it and no day asks twice. See EMPTY_EXPORT_SETTLE_DAYS.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS codal_backfill_empty (
        tracing_no TEXT PRIMARY KEY,
        ins_code   TEXT,
        bytes      INTEGER,
        days       INTEGER DEFAULT 1,
        last_day   TEXT
    )
    """)

    conn.commit()
    conn.close()


def record_retry(tracing_no, ins_code, error):
    """Note a transient failure, so the filing goes behind the untried ones."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    conn.execute("""
    INSERT INTO codal_backfill_retries
        (tracing_no, ins_code, last_error, attempts, last_tried)
    VALUES (?, ?, ?, 1, ?)
    ON CONFLICT (tracing_no) DO UPDATE SET
        last_error = excluded.last_error,
        attempts = codal_backfill_retries.attempts + 1,
        last_tried = excluded.last_tried
    """, (str(tracing_no), str(ins_code), str(error)[:200], _now()))

    attempts = conn.execute(
        "SELECT attempts FROM codal_backfill_retries WHERE tracing_no = ?",
        (str(tracing_no),)).fetchone()[0]

    conn.commit()
    conn.close()

    return int(attempts or 0)


# A document Codal's exporter answers 500 for this many times is settled as
# unreadable. Going to the back was not enough on its own: by 2026-11-08 the
# queue WAS the back -- 47 of 48 left had answered 500 twice -- so every round
# fetched five of them and backed off four hours as if throttled, holding up
# any new filing behind them. Only a server error settles; a 429, a timeout or
# a dropped connection says nothing about the document and never does.
SERVER_ERROR_SETTLE_ATTEMPTS = 3


def is_server_error(error):
    return bool(re.search(r"\b50\d Server Error", str(error or "")))


def retried_filings():
    """Tracing number -> transient failures so far."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    rows = conn.execute(
        "SELECT tracing_no, attempts FROM codal_backfill_retries").fetchall()

    conn.close()

    return {str(row[0]): int(row[1] or 0) for row in rows}


# A filing Codal's exporter answers EMPTY (codal.exporter_empty) on this many
# different days, the same number of bytes each time, is settled as
# unreadable. By 2026-11-25 the queue was nothing but seven of them: each had
# answered the same 13,185 or 1,450 bytes 31 times, every round read "5 of 5
# refused" and backed off up to four hours, and the campaign never reached
# its finished state, where it watches the feed. Content, not size, says it
# is the exporter and not a throttle -- a block page carries neither Codal's
# export template nor its workbook shell -- and days, not attempts, are
# counted, so a single bad hour at Codal cannot settle anything. Such an
# answer is asked for once a day (pending() skips it until the next) and is
# not counted as a refusal: it is the host answering, not declining.
EMPTY_EXPORT_SETTLE_DAYS = 3

_EMPTY_BYTES = re.compile(r"returned only (\d+) bytes")


def empty_export_size(error):
    """The byte count of an empty-export answer (codal.EXPORTER_EMPTY), or
    None for any other error -- a throttle page's "too small" included."""

    text = str(error or "")
    if codal.EXPORTER_EMPTY not in text:
        return None
    match = _EMPTY_BYTES.search(text)
    return int(match.group(1)) if match else None


def _today():
    return _now()[:10]


def record_empty_export(tracing_no, ins_code, size):
    """One more empty answer for a filing; returns the days counted so far.

    The same size on a later day adds a day; the same day adds nothing; a
    different size starts again at one -- a document that changes is not the
    constant answer this settles.
    """

    database.create_database()
    _ensure_state_table()

    today = _today()
    conn = database._connect()
    row = conn.execute(
        "SELECT bytes, days, last_day FROM codal_backfill_empty "
        "WHERE tracing_no = ?", (str(tracing_no),)).fetchone()
    if row and row[0] == size:
        days = int(row[1] or 0) + (0 if row[2] == today else 1)
    else:
        days = 1
    conn.execute("INSERT OR REPLACE INTO codal_backfill_empty VALUES (?,?,?,?,?)",
                 (str(tracing_no), str(ins_code), int(size), days, today))
    conn.commit()
    conn.close()
    return days


def empty_exports_today():
    """Tracing numbers already answered empty today: not asked again until
    tomorrow."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()
    rows = conn.execute(
        "SELECT tracing_no FROM codal_backfill_empty WHERE last_day = ?",
        (_today(),)).fetchall()
    conn.close()
    return {str(row[0]) for row in rows}


SUBSIDIARY_REASON ="subsidiary's statement (not the issuer's own)"


def purge_subsidiaries(verbose=True, dry_run=False):
    """Take out every stored subsidiary statement, and put the parent back.

    codal_sync.parse_letter refuses them from now on; this removes the ones
    stored before it did (182 on 2026-11-10). Each is deleted from
    codal_filings and settled so no queue fetches it again. Where it had
    taken the period's place in codal_financials or in the fundamental
    store's metrics, the parent's own newest filing for that period goes back
    in -- or the period is left empty, which is honest, rather than another
    company's. Idempotent.

    `dry_run` (--dry-run, 2026-11-23): list what would be removed and
    change nothing. The purge decides by one rule over a title
    (financials.subsidiary_of) and deleted at once; a rule that had come
    to match too much would have shown only in what was gone.
    """

    import financials
    import fundamental_db

    if dry_run:
        return _purge_subsidiaries_preview(verbose)

    database.create_database()
    _ensure_state_table()

    conn = database._connect()
    rows = conn.execute(
        "SELECT tracing_no, ins_code, period_end, period_months, title "
        "FROM codal_filings").fetchall()
    bad = [r for r in rows if financials.subsidiary_of(r[4])]
    bad_ids = {str(r[0]) for r in bad}
    keys = {(str(r[1]), r[2], r[3]) for r in bad}

    for tracing_no, ins_code, _, _, _ in bad:
        conn.execute("DELETE FROM codal_filings WHERE tracing_no = ?",
                     (str(tracing_no),))
    conn.commit()

    # The parent's own newest filing for each period it had displaced.
    restore = {}
    for ins_code, period_end, months in keys:
        own = conn.execute(
            "SELECT tracing_no FROM codal_filings WHERE ins_code = ? AND "
            "period_end = ? AND period_months IS ? ORDER BY published_at DESC "
            "LIMIT 1", (ins_code, period_end, months)).fetchone()
        restore[(ins_code, period_end, months)] = str(own[0]) if own else None

    # A subsidiary row in the period table whose filing never reached
    # codal_filings: nothing to restore from, so it simply goes.
    for tracing_no, title in conn.execute(
            "SELECT tracing_no, title FROM codal_financials").fetchall():
        if financials.subsidiary_of(title) and str(tracing_no) not in bad_ids:
            conn.execute("DELETE FROM codal_financials WHERE tracing_no = ?",
                         (tracing_no,))
    conn.commit()

    replaced = 0
    for (ins_code, period_end, months), own in restore.items():
        held = conn.execute(
            "SELECT tracing_no FROM codal_financials WHERE ins_code = ? AND "
            "period_end = ? AND period_months IS ?",
            (ins_code, period_end, months)).fetchone()
        if not held or str(held[0]) not in bad_ids:
            continue
        conn.execute(
            "DELETE FROM codal_financials WHERE ins_code = ? AND period_end = ? "
            "AND period_months IS ?", (ins_code, period_end, months))
        replaced += 1
    conn.commit()
    conn.close()

    for row in database.load_codal_filings():
        key = (str(row.get("ins_code")), row.get("period_end"),
               row.get("period_months"))
        if restore.get(key) == str(row.get("tracing_no")):
            database.save_codal_financials(row["ins_code"], row)

    for tracing_no, ins_code, _, _, _ in bad:
        record_failure(tracing_no, ins_code, SUBSIDIARY_REASON)
        database.mark_letter_parsed(tracing_no)

    # The fundamental store: drop the borrowed periods, and forget that the
    # parent's own letter was imported, so the next sync carries it back in.
    fundamental_db.create_database()
    fconn = fundamental_db._connect()
    fconn.execute("CREATE TABLE IF NOT EXISTS imported_letters ("
                  "tracing_no TEXT PRIMARY KEY, imported_at TEXT)")
    # Every period a subsidiary held here, by key: the market-db sweep above
    # and any row this store took from elsewhere.
    borrowed = {(str(i), p, m) for t, i, p, m, title in fconn.execute(
        "SELECT tracing_no, ins_code, period_end, period_months, title "
        "FROM metrics").fetchall()
        if str(t) in bad_ids or financials.subsidiary_of(title)}
    dropped = 0
    for ins_code, period_end, months in borrowed:
        dropped += fconn.execute(
            "DELETE FROM metrics WHERE ins_code = ? AND period_end = ? AND "
            "period_months IS ?", (ins_code, period_end, months)).rowcount
    # Released from the ledger: the subsidiaries (harmless, they are gone
    # from the source) and the issuer's own letters for those periods, which
    # the next sync then carries back in, newest publication last.
    mconn = database._connect()
    for ins_code, period_end, months in borrowed:
        for (own,) in mconn.execute(
                "SELECT tracing_no FROM codal_filings WHERE ins_code = ? AND "
                "period_end = ? AND period_months IS ?",
                (ins_code, period_end, months)).fetchall():
            fconn.execute("DELETE FROM imported_letters WHERE tracing_no = ?",
                          (str(own),))
    mconn.close()
    for tracing_no in bad_ids:
        fconn.execute("DELETE FROM imported_letters WHERE tracing_no = ?",
                      (tracing_no,))
    fconn.commit()
    fconn.close()

    synced = fundamental_db.sync_from_market_db() if borrowed else None

    result = {"removed": len(bad), "periods": len(keys),
              "restored": sum(1 for v in restore.values() if v),
              "financials_replaced": replaced, "metrics_dropped": dropped,
              "synced": synced}

    if verbose:
        print("purge: %(removed)d subsidiary statements removed from "
              "%(periods)d company-periods; %(restored)d periods have the "
              "issuer's own filing back, %(metrics_dropped)d dropped from the "
              "fundamental store" % result, flush=True)

    return result


def _purge_subsidiaries_preview(verbose=True):
    """What purge_subsidiaries would remove. Reads only; creates nothing."""

    import sqlite3

    import financials
    import fundamental_db

    result = {"dry_run": True, "removed": 0, "would_remove": 0, "periods": 0,
              "financials_rows": 0, "metrics_periods": 0, "listed": []}

    if os.path.exists(database.DATABASE_NAME):
        conn = database._connect()
        try:
            rows = conn.execute(
                "SELECT tracing_no, ins_code, period_end, period_months, "
                "title FROM codal_filings").fetchall()
            bad = [r for r in rows if financials.subsidiary_of(r[4])]
            bad_ids = {str(r[0]) for r in bad}
            result["would_remove"] = len(bad)
            result["periods"] = len({(str(r[1]), r[2], r[3]) for r in bad})
            result["listed"] = [
                {"tracing_no": str(r[0]), "ins_code": str(r[1]),
                 "period_end": r[2], "period_months": r[3], "title": r[4]}
                for r in bad]
            # Subsidiary rows in the period table whose filing never reached
            # codal_filings: the purge deletes those as well.
            result["financials_rows"] = sum(
                1 for tracing_no, title in conn.execute(
                    "SELECT tracing_no, title FROM codal_financials")
                if financials.subsidiary_of(title)
                and str(tracing_no) not in bad_ids)
        except sqlite3.Error:
            bad_ids = set()
        finally:
            conn.close()
    else:
        bad_ids = set()

    store = fundamental_db.path()
    if os.path.exists(store):
        fconn = fundamental_db._connect()
        try:
            result["metrics_periods"] = len({
                (str(i), p, m) for t, i, p, m, title in fconn.execute(
                    "SELECT tracing_no, ins_code, period_end, period_months, "
                    "title FROM metrics")
                if str(t) in bad_ids or financials.subsidiary_of(title)})
        except sqlite3.Error:
            pass
        finally:
            fconn.close()

    if verbose:
        for row in result["listed"]:
            print("  would remove %(tracing_no)s  %(ins_code)s  "
                  "%(period_end)s (%(period_months)s m)  %(title)s" % row,
                  flush=True)
        print("purge (dry run): %(would_remove)d subsidiary statements in "
              "%(periods)d company-periods would be removed, "
              "%(financials_rows)d more rows from codal_financials, and "
              "%(metrics_periods)d periods dropped from the fundamental "
              "store. Nothing was changed; back up (python backup.py) and "
              "run without --dry-run to apply." % result, flush=True)

    return result


def _predecessors():
    """{live ins_code: [retired ins_codes]} for board moves.

    codal_filings stays keyed by the code a filing was published under, while
    seed_import --repair-universe copies codal_financials to the live code, so
    a live code's period must be compared with its predecessors' releases too
    (2026-11-11: the copy ran before --repair-superseded, which then saw only
    the retired codes' rows).
    """

    out = {}
    try:
        listings = database.superseded_listings()
    except Exception:                                   # noqa: BLE001
        return out
    for retired, info in listings.items():
        if info.get("successor"):
            out.setdefault(str(info["successor"]), []).append(str(retired))
    return out


def superseded_periods():
    """codal_financials rows holding an older release than codal_filings has.

    [(held row, newest filing)] -- the damage the always-replace upsert did
    before save_codal_financials compared publication times (2026-11-11). A
    row whose own release cannot be dated is left alone: nothing proves it
    older, and it may be a filing that never reached codal_filings.
    """

    import financials

    database.create_database()

    conn = database._connect()

    held = conn.execute("""
        SELECT f.ins_code, f.period_end, f.period_months, f.tracing_no,
               f.title, COALESCE(NULLIF(g.published_at, ''),
                                 NULLIF(l.published_at, '')), f.items
        FROM codal_financials f
        LEFT JOIN codal_filings g ON g.tracing_no = f.tracing_no
        LEFT JOIN codal_letters l ON l.tracing_no = f.tracing_no
    """).fetchall()

    filings = conn.execute("""
        SELECT tracing_no, ins_code, period_end, period_months, published_at,
               title, audited, consolidated, items, ratios
        FROM codal_filings
        WHERE published_at IS NOT NULL AND published_at <> ''
    """).fetchall()

    conn.close()

    newest = {}

    for row in filings:
        if financials.subsidiary_of(row[5]):
            continue
        key = (str(row[1]), row[2], row[3])
        if key not in newest or (row[4], str(row[0])) > (newest[key][4],
                                                          str(newest[key][0])):
            newest[key] = row

    out = []
    before = _predecessors()

    for ins_code, period_end, months, tracing_no, title, published, items in held:
        found = [newest.get((code, period_end, months)) for code in
                 [str(ins_code)] + before.get(str(ins_code), [])]
        found = [row for row in found if row is not None]
        best = max(found, key=lambda r: (r[4], str(r[0]))) if found else None
        if (best is None or not published or str(best[0]) == str(tracing_no)
                or best[4] <= published):
            continue
        out.append(({"ins_code": str(ins_code), "period_end": period_end,
                     "period_months": months, "tracing_no": str(tracing_no),
                     "title": title, "published_at": published,
                     "items": database._loads(items)},
                    {"ins_code": str(ins_code), "period_end": best[2],
                     "period_months": best[3], "tracing_no": str(best[0]),
                     "published_at": best[4], "title": best[5],
                     "audited": None if best[6] is None else bool(best[6]),
                     "consolidated": bool(best[7]),
                     "items": database._loads(best[8]),
                     "ratios": database._loads(best[9])}))

    return out


def _filings_by_period():
    """Every dated release in codal_filings, by (ins_code, period_end, months);
    a subsidiary's statement filed under the parent's symbol left out."""

    import financials

    database.create_database()
    conn = database._connect()
    rows = conn.execute("""
        SELECT tracing_no, ins_code, period_end, period_months, published_at,
               title, audited, consolidated, items, ratios
        FROM codal_filings
        WHERE published_at IS NOT NULL AND published_at <> ''
    """).fetchall()
    conn.close()

    out = {}
    for row in rows:
        if financials.subsidiary_of(row[5]):
            continue
        out.setdefault((str(row[1]), row[2], row[3]), []).append(row)
    return out


def _release(row):
    return {"ins_code": str(row[1]), "period_end": row[2],
            "period_months": row[3], "tracing_no": str(row[0]),
            "published_at": row[4], "title": row[5],
            "audited": None if row[6] is None else bool(row[6]),
            "consolidated": bool(row[7]),
            "items": database._loads(row[8]),
            "ratios": database._loads(row[9])}


def _held_rows(store):
    """The release each period holds now: codal_financials ("market") or the
    fundamental store's metrics ("store")."""

    table = "codal_financials" if store == "market" else "metrics"
    if store == "market":
        conn = database._connect()
    else:
        import fundamental_db
        if not os.path.isfile(fundamental_db.path()):
            return []                   # a dry run creates no store
        conn = fundamental_db._connect()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                            "AND name = ?", (table,)).fetchone():
            return []
        rows = conn.execute(
            "SELECT ins_code, period_end, period_months, tracing_no, title, "
            "items FROM %s" % table).fetchall()
    finally:
        conn.close()
    return [{"ins_code": str(r[0]), "period_end": r[1], "period_months": r[2],
             "tracing_no": None if r[3] is None else str(r[3]),
             "title": r[4], "items": database._loads(r[5])} for r in rows]


def unparsed_held_periods(held_rows, releases):
    """[(held, better)]: periods whose HELD parse lacks a statement another
    release of the period carries, with the newest release that lacks none
    of the held parse's statements and has one it lacks.

    The other half of the statement rule (third review, 2026-11-11):
    superseded_periods() only sees rows holding an OLDER release, so a row
    holding the newest release whose parse failed was invisible -- 103 on
    live data, فوکا's latest period 1405/06/31 among them (correction
    1613577 exported with no income-statement table; original 1611372 has
    it), and the panel showed that period with no revenue, profit or EPS.
    """

    out = []
    before = _predecessors()
    for held in held_rows:
        found = []
        candidates = []
        for code in [held["ins_code"]] + before.get(held["ins_code"], []):
            candidates += releases.get(
                (code, held["period_end"], held["period_months"]), [])
        for row in candidates:
            if str(row[0]) == held["tracing_no"]:
                continue
            items = database._loads(row[8])
            if database.statements_lost(held["items"], items):
                continue                        # it lacks one the held has
            if not database.statements_lost(items, held["items"]):
                continue                        # it adds nothing
            found.append(row)
        if found:
            best = max(found, key=lambda r: (r[4] or "", str(r[0])))
            out.append((held, dict(_release(best), ins_code=held["ins_code"])))
    return out


NOT_ARCHIVED = "not in the raw archive"


class _ArchiveOnly(codal.CodalClient):
    """codal_sync.parse_letter's client for a re-parse that must not reach
    Codal: a document comes from the raw archive or not at all."""

    def __init__(self):                 # no session, no limiter: no route
        pass

    def _get(self, *args, **kwargs):
        raise RuntimeError("archive only: no request is made")

    def fetch_sheets(self, excel_url, archive_key=None):
        body = codal.archive_read(archive_key) if archive_key else None
        if body is None:
            return codal.CodalResult(error=NOT_ARCHIVED, kind=codal.KIND_BAD)
        return self.parse_body(body)


def _present(items):
    """The line items a parse carries a figure for."""

    return {key for key, value in (items or {}).items() if value is not None}


def _reparse_from_archive(best):
    """`best` (a codal_filings row) re-parsed with today's extractor from the
    raw archive: (record, None) if that finds items the stored parse lacks,
    else (None, why not). Never downloads."""

    if not codal.archive_has(best["tracing_no"]):
        return None, NOT_ARCHIVED

    record, error, _kind = codal_sync.parse_letter(_ArchiveOnly(), {
        "tracing_no": best["tracing_no"], "title": best["title"]})

    if error:
        return None, error

    if (record["period_end"], record["period_months"]) != \
            (best["period_end"], best["period_months"]):
        return None, "re-parsed as another period (%s, %sm)" % (
            record["period_end"], record["period_months"])

    # Only a fuller parse replaces the stored one; an equal one would only
    # touch codal_filings for nothing.
    if not _present(record["items"]) > _present(best["items"]):
        return None, "today's extractor finds no more in the document"

    record.update(tracing_no=best["tracing_no"],
                  published_at=best["published_at"])
    return record, None


def repair_superseded(apply=False, verbose=True):
    """Put each period's newest release back in codal_financials.

    Dry run unless `apply`. Every release is in codal_filings with its
    figures, so nothing is downloaded; the fundamental store already holds
    the newest release. Idempotent: a repaired row holds the newest release
    and is not listed again.

    The newest release's stored parse can be thinner than the held one's.
    Found 2026-11-11 by the review: of the 531, 129 newest releases lack
    items the held release's parse carries -- 123 are not in the raw
    archive, 122 of them parsed in August, before the extractor learned
    finance_cost, pretax_profit, ppe, capex and the rest; --reparse-latest
    only re-reads a company's two newest filings, so nothing would restore
    them. The dry run counts those rows and their items. A document the
    archive holds is re-parsed from it with today's extractor, offline,
    and a fuller parse is written to codal_filings too, so the fundamental
    store carries it (on 2026-11-11, of the 6 archived ones only 1516177
    gains -- its EPS, once financials read "سود (زیان) پایه هر سهم"; the
    other five carry no owners' lines as Codal exported them: separate
    statements, or a consolidated heading with no table under it). The
    rest are listed by tracing number for a targeted re-parse, which
    needs the network and is not done here. All are written: the newest
    release is the right one, line items or not (and none of the 129 was
    a company's latest period) --

    -- except where the newest release's parse lacks a whole statement the
    held one has (database.statements_lost): that is a failed read, not a
    thinner one, and writing it would replace a usable row. 472469 has no
    balance sheet or cash flow statement, 1212323 no income statement
    (2026-11-11). Those are kept, reported as "newest release unparsed"
    for a targeted re-parse, and save_codal_financials refuses them on any
    path.
    """

    stale = superseded_periods()

    # What each row would lose, before and after the archive re-parse.
    lost_before, lost_after = {}, {}
    thinner_before = thinner_after = 0
    reparsed, unarchived, unparsed, plan, kept = 0, [], [], [], []

    for held, best in stale:
        lost = _present(held["items"]) - _present(best["items"])
        record = None
        if lost:
            thinner_before += 1
            for key in lost:
                lost_before[key] = lost_before.get(key, 0) + 1
            record, why = _reparse_from_archive(best)
            if record is not None:
                reparsed += 1
                lost = _present(held["items"]) - _present(record["items"])
            elif why == NOT_ARCHIVED:
                unarchived.append(best["tracing_no"])
            else:
                unparsed.append((best["tracing_no"], why, sorted(lost)))
        missing = database.statements_lost(
            held["items"], (best if record is None else record)["items"])
        if missing:
            kept.append((best["tracing_no"], held["tracing_no"], missing))
            continue
        if lost:
            thinner_after += 1
            for key in lost:
                lost_after[key] = lost_after.get(key, 0) + 1
        plan.append((held, best, record))

    latest = {}
    conn = database._connect()
    for ins_code, period_end, months in conn.execute(
            "SELECT ins_code, period_end, period_months FROM codal_financials"):
        key = (period_end or "", months or 0)
        if key > latest.get(str(ins_code), ("", 0)):
            latest[str(ins_code)] = key
    names = {str(r[0]): r[1] for r in conn.execute(
        "SELECT ins_code, symbol_fa FROM instruments")}
    conn.close()

    def kind(held, best):
        if "اصلاحیه" in (best["title"] or ""):
            return "original -> its correction"
        if "حسابرسی شده" in (best["title"] or "") and \
                "حسابرسی نشده" in (held["title"] or ""):
            return "unaudited -> audited"
        if ("تلفیقی" in (best["title"] or "")) != \
                ("تلفیقی" in (held["title"] or "")):
            return "the other basis (consolidated/separate)"
        return "a later release"

    kinds, on_latest = {}, []
    for held, best in stale:
        name = kind(held, best)
        kinds[name] = kinds.get(name, 0) + 1
        if latest.get(held["ins_code"]) == (held["period_end"] or "",
                                            held["period_months"] or 0):
            on_latest.append((held, best))

    # Rows holding a release whose parse lacks a statement another release
    # has (the newest included), in both stores. A period the plan above
    # already rewrites is left to it.
    planned = {(h["ins_code"], h["period_end"], h["period_months"])
               for h, _b, _r in plan}
    releases = _filings_by_period()
    restore = [(h, b) for h, b in unparsed_held_periods(
        _held_rows("market"), releases)
        if (h["ins_code"], h["period_end"], h["period_months"]) not in planned]
    restore_store = unparsed_held_periods(_held_rows("store"), releases)

    written = restored = restored_store = 0
    if apply:
        for _held, best in restore:
            restored += database.save_codal_financials(best["ins_code"], best)
        if restore_store:
            import fundamental_db
            for _held, best in restore_store:
                if fundamental_db.save_metrics(dict(best, source="market-db")):
                    restored_store += 1
        for _held, best, record in plan:
            if record is not None:
                # The fuller parse goes to codal_filings too, so the
                # fundamental store's next sync carries it.
                database.save_codal_filing(best["ins_code"], record,
                                           published_at=best["published_at"])
            written += database.save_codal_financials(
                best["ins_code"], best if record is None else record)
        if reparsed:
            _sync_store(verbose)

    result = {"superseded": len(stale), "latest_period": len(on_latest),
              "by_kind": kinds, "written": written, "applied": bool(apply),
              "losing_items": thinner_before, "items_lost": lost_before,
              "reparsed": reparsed, "still_losing_items": thinner_after,
              "items_still_lost": lost_after, "not_archived": unarchived,
              "not_reparsed": unparsed, "kept_unparsed": kept,
              "held_unparsed": [(h["tracing_no"], b["tracing_no"])
                                for h, b in restore],
              "held_unparsed_store": [(h["tracing_no"], b["tracing_no"])
                                      for h, b in restore_store],
              "restored": restored, "restored_store": restored_store}

    if verbose:
        # Kept rows hold an older release on purpose (the newest one's parse
        # lacks a statement), so the headline says how many are really owed.
        print("repair: %d periods in codal_financials hold an older release "
              "than codal_filings has (%d on purpose: the newest release's "
              "parse lacks a statement); %d are the company's latest period%s"
              % (len(stale), len(kept), len(on_latest), "" if apply else
                 " -- dry run, nothing written (--apply rewrites %d of them)"
                 % len(plan)), flush=True)
        for name, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print("    %4d  %s" % (count, name), flush=True)
        examples = on_latest + [pair for pair in stale if pair not in on_latest]
        for held, best in examples[:8]:
            print("  %-8s %s %sm  held %s (%s)  newest %s (%s)"
                  % (names.get(held["ins_code"]) or held["ins_code"],
                     held["period_end"], held["period_months"],
                     held["tracing_no"], held["published_at"],
                     best["tracing_no"], best["published_at"]), flush=True)

        def by_rows(counts):
            return ", ".join("%s %d" % kv for kv in sorted(
                counts.items(), key=lambda kv: (-kv[1], kv[0])))

        print("repair: %d of them would lose line items the held release's "
              "parse carries (rows per item: %s)"
              % (thinner_before, by_rows(lost_before) or "-"), flush=True)
        print("  re-parsed from the raw archive with today's extractor, "
              "offline, and fuller: %d; written still losing items: %d; "
              "kept, the newest release unparsed: %d"
              % (reparsed, thinner_after, len(kept)), flush=True)
        for tracing_no, why, keys in unparsed:
            print("  archived, kept as stored -- %s: %s (%s)"
                  % (why, tracing_no, ", ".join(keys)), flush=True)
        if unarchived:
            print("  not in the raw archive (%d; a targeted re-parse needs "
                  "the network): %s" % (len(unarchived), " ".join(unarchived)),
                  flush=True)
        for newest, held_no, missing in kept:
            print("  newest release unparsed, kept: %s has no %s, which the "
                  "held %s has -- not written; it needs a targeted re-parse"
                  % (newest, " and no ".join(missing), held_no), flush=True)
        on_latest_held = [pair for pair in restore if latest.get(
            pair[0]["ins_code"]) == (pair[0]["period_end"] or "",
                                     pair[0]["period_months"] or 0)]
        print("repair: %d periods in codal_financials and %d in the "
              "fundamental store hold a release whose parse lacks a statement "
              "another release of the period has (%d are the company's latest "
              "period)%s" % (len(restore), len(restore_store),
                             len(on_latest_held), "" if apply else
                             " -- --apply puts the complete release back"),
              flush=True)
        for held, best in (on_latest_held + [p for p in restore
                                             if p not in on_latest_held])[:8]:
            print("  %-8s %s %sm  held %s lacks the %s; %s has it"
                  % (names.get(held["ins_code"]) or held["ins_code"],
                     held["period_end"], held["period_months"],
                     held["tracing_no"], " and the ".join(
                         database.statements_lost(best["items"], held["items"])),
                     best["tracing_no"]), flush=True)
        if apply:
            print("repair: %d rows rewritten; restored %d in codal_financials "
                  "and %d in the fundamental store"
                  % (written, restored, restored_store), flush=True)

    return result


def record_failure(tracing_no, ins_code, reason):
    """Settle one filing as unreadable, so the queue moves past it."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    conn.execute("""
    INSERT INTO codal_backfill_failures
        (tracing_no, ins_code, reason, attempts, last_tried)
    VALUES (?, ?, ?, 1, ?)
    ON CONFLICT (tracing_no) DO UPDATE SET
        reason = excluded.reason,
        attempts = codal_backfill_failures.attempts + 1,
        last_tried = excluded.last_tried
    """, (str(tracing_no), str(ins_code), str(reason)[:200], _now()))

    conn.commit()
    conn.close()


def settled_failures():
    """Tracing numbers this tool has tried and cannot read."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    rows = conn.execute(
        "SELECT tracing_no FROM codal_backfill_failures").fetchall()

    conn.close()

    return {str(row[0]) for row in rows}


def failure_summary():
    """What was unreadable, and why. Counts by reason, for the report."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    rows = conn.execute(
        "SELECT reason, COUNT(*) FROM codal_backfill_failures "
        "GROUP BY reason ORDER BY COUNT(*) DESC").fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM codal_backfill_failures").fetchone()[0]

    conn.close()

    return {"total": total,
            "by_reason": [{"reason": r, "count": c} for r, c in rows]}


def load_state():
    """{ins_code: {searched_to, ...}} -- one row per company already searched."""

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    rows = conn.execute(
        "SELECT ins_code, symbol, searched_to, pages, letters, error, updated_at "
        "FROM codal_backfill_state"
    ).fetchall()

    conn.close()

    return {
        str(row[0]): {
            "symbol": row[1], "searched_to": row[2], "pages": row[3],
            "letters": row[4], "error": row[5], "updated_at": row[6],
        }
        for row in rows
    }


def save_state(ins_code, symbol=None, searched_to=None, pages=0, letters=0,
               error=None):

    database.create_database()
    _ensure_state_table()

    conn = database._connect()

    conn.execute("""
    INSERT INTO codal_backfill_state
        (ins_code, symbol, searched_to, pages, letters, error, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (ins_code) DO UPDATE SET
        symbol = excluded.symbol, searched_to = excluded.searched_to,
        pages = excluded.pages, letters = excluded.letters,
        error = excluded.error, updated_at = excluded.updated_at
    """, (str(ins_code), symbol, searched_to, int(pages or 0),
          int(letters or 0), error, _now()))

    conn.commit()
    conn.close()


# ==============================
# DISCOVERY  (cheap: ~1 request per company)
# ==============================

def search_statements(client, symbol, since, max_pages=MAX_PAGES):
    """Every ن-۱۰ filing for one symbol back to `since`. (letters, oldest, error).

    `oldest` is the oldest publication date actually seen, which is what the
    state table records: a company that has only ever filed three statements is
    fully discovered after one page, and re-searching it every run would spend
    the budget proving the same negative.
    """

    letters = []
    oldest = None

    for page in range(1, max(1, max_pages) + 1):

        params = dict(codal.CodalClient.SEARCH_DEFAULTS)
        params["Symbol"] = symbol
        params["PageNumber"] = str(page)
        params["LetterCode"] = STATEMENT_CODE

        try:
            payload = client._get(config.CODAL_SEARCH_URL, params=params).json()
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, str(exc)[:140])
            return letters, oldest, (error, codal.classify(exc))

        batch = payload.get("Letters") or []

        if not batch:
            break

        rows = [codal._letter_row(item, symbol) for item in batch]

        letters.extend(rows)

        oldest = _day(rows[-1].get("published_at")) or oldest

        # Reached past the window, or Codal has no more pages for this symbol.
        if (oldest and since and oldest < since) or len(batch) < PAGE_SIZE:
            break

    return letters, oldest, None


def remaining_to_discover(since, refresh=False):
    """Companies not yet searched back to `since`."""

    instruments = database.load_instruments(equities_only=True)

    state = load_state()

    out = []

    for row in instruments:

        code = str(row["ins_code"])

        known = state.get(code)

        if known and not refresh:

            reached = known.get("searched_to")

            if reached and reached <= since:
                continue

            # Exhausted: Codal returned every statement it has for this company
            # and none of them reach the window. Searching again cannot find
            # what does not exist.
            if (known.get("pages") and known["pages"] < MAX_PAGES
                    and not known.get("error")):
                continue

        out.append(row)

    return out


def discover(since, limit=None, verbose=True, client=None, refresh=False):
    """Fill codal_letters with every statement filing back to `since`.

    Returns a summary. Never raises: a company Codal will not answer for costs
    that company, not the run.
    """

    client = client or codal.CodalClient()

    queue = remaining_to_discover(since, refresh=refresh)

    outstanding = len(queue)

    if limit:
        queue = queue[:int(limit)]

    if verbose:
        print("discover: %d companies still to search back to %s (taking %d)"
              % (outstanding, since, len(queue)), flush=True)

    found = failed = 0
    consecutive = 0
    throttled = False

    for number, instrument in enumerate(queue, start=1):

        symbol = codal_sync.codal_symbol(instrument)
        code = str(instrument["ins_code"])

        letters, oldest, error = search_statements(client, symbol, since)

        if letters:
            database.save_codal_letters(code, letters)
            found += len(letters)

        save_state(code, symbol=symbol, searched_to=oldest,
                   pages=max(1, -(-len(letters) // PAGE_SIZE)),
                   letters=len(letters),
                   error=error[0] if error else None)

        if error:
            failed += 1
            consecutive = consecutive + 1 if error[1] == codal.KIND_TRANSIENT else 0
        else:
            consecutive = 0

        if verbose and number % 25 == 0:
            print("  discover %d/%d  letters=%d failed=%d"
                  % (number, len(queue), found, failed), flush=True)

        if consecutive >= config.CODAL_ABORT_AFTER_FAILURES:
            throttled = True
            if verbose:
                print("  pausing: %d consecutive transient failures -- "
                      "Codal is throttling or unreachable" % consecutive,
                      flush=True)
            break

    if verbose:
        print("discover: %d letters stored, %d companies failed"
              % (found, failed), flush=True)

    return {"searched": len(queue), "letters": found, "failed": failed,
            "throttled": throttled,
            "outstanding": len(remaining_to_discover(since, refresh=False))}


# ==============================
# SEED FROM WHAT IS ALREADY PARSED
# ==============================

def seed_from_financials(verbose=True):
    """Carry every already-parsed filing into the point-in-time table.

    codal_financials holds 1,480 filings this project downloaded over months of
    daily syncs. Re-downloading them to populate codal_filings would cost hours
    against a source that throttles, to obtain figures already on disk. The one
    thing the period-keyed table cannot supply is which filings were LOST to
    its own primary key -- where an unaudited and an audited release of the same
    period collapsed into one row, only the surviving row can be seeded, and the
    other has to come from a download.

    Publication dates come from the letter index, which is where they have
    always lived. A filing whose letter is missing is seeded without one, and
    the point-in-time layer then refuses it under the standard policy rather
    than guessing.
    """

    import financials

    database.create_database()

    conn = database._connect()

    rows = conn.execute("""
        SELECT f.ins_code, f.period_end, f.period_months, f.tracing_no,
               f.title, f.audited, f.consolidated, f.items, f.ratios,
               l.published_at
        FROM codal_financials f
        LEFT JOIN codal_letters l ON f.tracing_no = l.tracing_no
        WHERE f.tracing_no IS NOT NULL AND f.tracing_no <> ''
    """).fetchall()

    conn.close()

    have = database.codal_filing_tracing_numbers()

    seeded = dated = 0

    for (ins_code, period_end, period_months, tracing_no, title, audited,
         consolidated, items, ratios, published_at) in rows:

        if str(tracing_no) in have:
            continue

        # Never carry a subsidiary's statement back in (purge_subsidiaries).
        if financials.subsidiary_of(title):
            continue

        database.save_codal_filing(ins_code, {
            "tracing_no": tracing_no,
            "period_end": period_end,
            "period_months": period_months,
            "title": title,
            "audited": None if audited is None else bool(audited),
            "consolidated": bool(consolidated),
            "items": database._loads(items),
            "ratios": database._loads(ratios),
        }, published_at=published_at)

        seeded += 1

        if published_at:
            dated += 1

    if verbose:
        print("seed: %d filings carried over from codal_financials (%d dated)"
              % (seeded, dated), flush=True)

    return {"seeded": seeded, "dated": dated, "candidates": len(rows)}


# ==============================
# THE DOWNLOAD QUEUE
# ==============================

def companies_covered_before(day):
    """Companies that already hold a parsed filing published before `day`.

    The set that decides which downloads are worth anything: a company in it
    has point-in-time coverage across the whole window already, because a
    filing stays the newest-as-of answer until something supersedes it.
    """

    database.create_database()

    conn = database._connect()

    rows = conn.execute(
        "SELECT DISTINCT ins_code FROM codal_filings "
        "WHERE published_at IS NOT NULL AND substr(published_at, 1, 10) < ?",
        (day,)
    ).fetchall()

    conn.close()

    return {str(row[0]) for row in rows}


def pending(since, until, per_company=None, priority_date=None):
    """Statement filings to download, ordered so an interrupted run still helps.

    The ordering is the only interesting part, and it is worth more than the
    downloading. This job will not finish -- codal.ir answers roughly 40
    requests per burst and the full queue is thousands -- so the question is
    not "how do we fetch everything" but "which few hundred filings buy the
    most coverage".

    Two orderings, and the second is dramatically better:

      RANK WITHIN COMPANY (the default). Every company's oldest in-window
      filing, then every company's second-oldest. A first pass lifts coverage
      across the whole universe; later passes only improve freshness.

      COVERAGE FIRST (`priority_date`, normally the backtest's start). A filing
      stays the point-in-time answer until it is superseded, so ONE filing
      published before the window starts gives a company 100% coverage of that
      window. This ordering puts, at the front of the queue, exactly one filing
      for each company that does not yet have one -- the NEWEST one that still
      predates the window, so the coverage it buys is also the freshest
      available.

      Measured on this database: 305 such downloads take point-in-time decision
      coverage from 58.8% to 96.0%. The same request budget spent in rank order
      buys a fraction of that, because most of it goes to companies that are
      already covered.
    """

    database.create_database()

    conn = database._connect()

    rows = conn.execute("""
        SELECT l.ins_code, l.tracing_no, l.published_at, l.excel_url, l.title
        FROM codal_letters l
        WHERE l.letter_code = ?
          AND l.excel_url IS NOT NULL AND l.excel_url <> ''
          AND l.published_at IS NOT NULL
        ORDER BY l.ins_code, l.published_at
    """, (STATEMENT_CODE,)).fetchall()

    conn.close()

    have = database.codal_filing_tracing_numbers()

    # Settled by THIS tool, on evidence, rather than by a legacy flag.
    settled = settled_failures()

    # Answered empty by Codal's exporter today: asked again tomorrow, not
    # every round (EMPTY_EXPORT_SETTLE_DAYS).
    settled = settled | empty_exports_today()

    covered = companies_covered_before(priority_date) if priority_date else set()

    by_company = {}

    for ins_code, tracing_no, published_at, excel_url, title in rows:

        day = _day(published_at)

        if since and day < since:
            continue

        if until and day > until:
            continue

        # Note what is NOT filtered here: the `parsed` flag. A letter is
        # skipped when its figures are in codal_filings, or when THIS tool has
        # tried it and found it unreadable.
        #
        # `parsed` means "some version of this project once attempted this",
        # which includes attempts made before codal_filings existed and
        # attempts that met a firewall page. Filtering on it would make both of
        # those permanent. Filtering on nothing, however, makes an unreadable
        # filing immortal -- it returns to the head of the queue every round --
        # which is why the failure table exists.
        if str(tracing_no) in have or str(tracing_no) in settled:
            continue

        by_company.setdefault(str(ins_code), []).append({
            "ins_code": str(ins_code),
            "tracing_no": str(tracing_no),
            "published_at": published_at,
            "excel_url": excel_url,
            "title": title,
        })

    work = []

    for code, items in by_company.items():

        items.sort(key=lambda item: _day(item["published_at"]))

        # ---- the coverage-first pick ------------------------------------
        if priority_date and code not in covered:

            before = [i for i in items
                      if _day(i["published_at"]) < priority_date]

            if before:
                pick = before[-1]           # newest that still predates it
                pick["rank"] = 0
                pick["priority"] = 0
                work.append(pick)
                items = [i for i in items if i is not pick]

        capped = items[:int(per_company)] if per_company else items

        for rank, item in enumerate(capped):
            item.setdefault("rank", rank)
            item.setdefault("priority", 1)
            work.append(item)

    # Retried filings last, the least-tried of them first: one document Codal
    # cannot export must not stand in front of thousands it can.
    retried = retried_filings()

    work.sort(key=lambda item: (retried.get(item["tracing_no"], 0),
                                item.get("priority", 1), item["rank"],
                                _day(item["published_at"])))

    return work


# ==============================
# FETCH
# ==============================

def fetch(since, until, limit=None, per_company=None, verbose=True,
          client=None, deadline=None, priority_date=None):
    """Download and parse the pending statements. Returns a summary.

    `deadline` is a monotonic-clock cutoff, so a caller can say "spend an hour
    on this" and get a partial backfill with a stated boundary rather than an
    open-ended run.
    """

    client = client or codal.CodalClient()

    queue = pending(since, until, per_company=per_company,
                    priority_date=priority_date)

    urgent = sum(1 for item in queue if item.get("priority") == 0)

    if limit:
        queue = queue[:int(limit)]

    if verbose:
        print("fetch: %d statements pending between %s and %s%s"
              % (len(queue), since, until,
                 " (%d of them buy a company its first pre-window filing)"
                 % urgent if urgent else ""), flush=True)

    stored = failed = skipped = empties = 0
    consecutive = 0
    throttled = False
    started = time.monotonic()

    # Counted by reason rather than as one number. "8 unparseable" is not an
    # actionable fact; "8 filings whose income statement uses a template the
    # extractor does not know" is, and so is "8 firewall pages".
    reasons = {}

    for number, item in enumerate(queue, start=1):

        if deadline is not None and time.monotonic() - started > deadline:
            if verbose:
                print("  stopping at the %ds budget with %d of %d done"
                      % (deadline, number - 1, len(queue)), flush=True)
            break

        letter = {
            "tracing_no": item["tracing_no"],
            "excel_url": item["excel_url"],
            "title": item["title"],
        }

        try:
            record, error, kind = codal_sync.parse_letter(client, letter)
        except Exception as exc:                      # the contract is no raise
            record, error, kind = None, "%s: %s" % (
                type(exc).__name__, str(exc)[:140]), codal.KIND_BAD

        if error:

            size = (empty_export_size(error)
                    if kind == codal.KIND_TRANSIENT else None)

            if size is not None:
                # Codal's exporter answered, with nothing in it: counted by
                # day, and neither a refusal nor a streak (the 2026-11-25
                # queue of seven, EMPTY_EXPORT_SETTLE_DAYS).
                consecutive = 0
                days = record_empty_export(item["tracing_no"], item["ins_code"],
                                           size)
                if days < EMPTY_EXPORT_SETTLE_DAYS:
                    empties += 1
                    continue
                error = ("empty statement export: Codal's exporter answered "
                         "%d bytes, the same on %d days" % (size, days))
                kind = codal.KIND_BAD

            attempts = (record_retry(item["tracing_no"], item["ins_code"], error)
                        if kind == codal.KIND_TRANSIENT else 0)

            if (is_server_error(error)
                    and attempts >= SERVER_ERROR_SETTLE_ATTEMPTS):
                # Falls through to the unreadable branch below: counted,
                # settled, and not taken for a throttle.
                error = "server error 500 (Codal cannot export it)"
                kind = codal.KIND_BAD

            if kind == codal.KIND_TRANSIENT:
                failed += 1
                consecutive += 1
                if consecutive >= config.CODAL_ABORT_AFTER_FAILURES:
                    throttled = True
                    if verbose:
                        # The reason, so a run of server errors on a few
                        # documents is not read for hours as a throttle.
                        print("  pausing: %d consecutive transient failures"
                              " (last: %s)" % (consecutive, str(error)[:120]),
                              flush=True)
                    break
                continue

            # A filing that genuinely will not parse. Marked so a megabyte is
            # not re-downloaded on every run to fail the same way, and counted
            # separately from a network failure because they mean opposite
            # things about whether to try again.
            skipped += 1
            consecutive = 0

            key = _reason_key(error)
            reasons[key] = reasons.get(key, 0) + 1

            # Both are needed. `mark_letter_parsed` keeps the daily sync from
            # re-downloading it; the failure row keeps THIS queue from doing
            # so, and only the second one actually moves the queue forward.
            record_failure(item["tracing_no"], item["ins_code"], key)

            database.mark_letter_parsed(item["tracing_no"])
            continue

        consecutive = 0

        if record:
            record["tracing_no"] = item["tracing_no"]
            database.save_codal_filing(item["ins_code"], record,
                                       published_at=item["published_at"])
            database.save_codal_financials(item["ins_code"], record)
            stored += 1

        database.mark_letter_parsed(item["tracing_no"])

        if verbose and number % 25 == 0:
            rate = (time.monotonic() - started) / number
            print("  fetch %d/%d  stored=%d unparseable=%d failed=%d  "
                  "%.1fs/filing, ~%.0f min left"
                  % (number, len(queue), stored, skipped, failed, rate,
                     rate * (len(queue) - number) / 60.0), flush=True)

    if verbose:
        print("fetch: %d stored, %d unparseable, %d transient failures%s"
              % (stored, skipped, failed,
                 ", %d answered empty by Codal's exporter (asked again "
                 "tomorrow)" % empties if empties else ""), flush=True)
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print("    %3d  %s" % (count, reason), flush=True)

    return {"pending": len(queue), "stored": stored, "unparseable": skipped,
            "failed": failed, "empty_exports": empties,
            "throttled": throttled, "reasons": reasons}


def _reason_key(error):
    """Collapse a per-filing error into the class of problem it belongs to."""

    text = str(error or "")

    if "no statements in filing" in text:
        return "no income or balance sheet the extractor could locate"

    if "zero sheets" in text:
        return "document has tables but no statement headings"

    if "no period end in title" in text:
        return "title carries no period end"

    if "no table at all" in text:
        return "not a statement document (firewall or error page)"

    # Kept as its own key so these can be re-tried in one query if Codal ever
    # starts filling them.
    if "empty statement export" in text:
        return "empty statement export (Codal holds no tables for it)"

    if "subsidiary's statement" in text:
        return SUBSIDIARY_REASON

    return text[:70] or "unknown"


# The line items fundamental_outlook.py needs, added 2026-11-05. A stored
# filing parsed before then has none of them.
OUTLOOK_ITEMS = ("ppe", "receivables", "borrowings_short", "capex")


def reparse_latest(per_company=2, deadline=None, verbose=True, client=None):
    """Re-read each company's newest filings parsed before the new items.

    Goes through the raw archive, so a document already there costs nothing;
    otherwise it is downloaded once and archived. Stops on the same transient
    streak as fetch(), so a throttled host is waited out, not hammered.
    """

    client = client or codal.CodalClient()

    conn = database._connect()
    rows = conn.execute("""
        SELECT f.ins_code, f.tracing_no, f.published_at, f.items,
               l.excel_url, l.title
        FROM codal_filings f
        JOIN codal_letters l ON l.tracing_no = f.tracing_no
        WHERE l.excel_url IS NOT NULL AND l.excel_url <> ''
        ORDER BY f.ins_code, f.published_at DESC
    """).fetchall()
    conn.close()

    queue, taken = [], {}
    for ins_code, tracing_no, published_at, items, excel_url, title in rows:
        if taken.get(ins_code, 0) >= per_company:
            continue
        taken[ins_code] = taken.get(ins_code, 0) + 1
        if any('"%s"' % key in (items or "") for key in OUTLOOK_ITEMS):
            continue
        queue.append({"ins_code": ins_code, "tracing_no": tracing_no,
                      "published_at": published_at, "excel_url": excel_url,
                      "title": title})

    if verbose:
        print("reparse: %d filings lack the outlook items" % len(queue),
              flush=True)

    started = time.monotonic()
    done = failed = consecutive = 0

    for item in queue:
        if deadline is not None and time.monotonic() - started > deadline:
            break

        record, error, kind = codal_sync.parse_letter(client, item)

        if error:
            failed += 1
            if kind == codal.KIND_TRANSIENT:
                consecutive += 1
                if consecutive >= config.CODAL_ABORT_AFTER_FAILURES:
                    if verbose:
                        print("reparse: pausing 30m after %d transient failures"
                              % consecutive, flush=True)
                    time.sleep(1800)
                    consecutive = 0
            continue

        consecutive = 0
        record["tracing_no"] = item["tracing_no"]
        database.save_codal_filing(item["ins_code"], record,
                                   published_at=item["published_at"])
        database.save_codal_financials(item["ins_code"], record)
        done += 1

        if verbose and done % 100 == 0:
            print("reparse: %d done, %d failed" % (done, failed), flush=True)
            _sync_store()

    _sync_store(verbose)

    if verbose:
        print("reparse: %d re-read, %d failed" % (done, failed), flush=True)

    return {"queued": len(queue), "done": done, "failed": failed}


# New statements and monthly reports are picked up from Codal's market-wide
# feed (codal_watch.py) every WATCH_SECONDS while a campaign runs. The
# campaign is the one process scheduled ~23.5 hours a day, so it carries the
# watch; the separate SIRAT-codal-watch task (task_watch.bat) shares the
# same lock, so the two never overlap. Two hours, as the scheduled task:
# the user found a quarter-hour more often than needed (2026-11-06).
WATCH_SECONDS = 2 * 60 * 60
_last_watch = [0.0]


def _watch_due(verbose=False):
    """Poll the feed if WATCH_SECONDS have passed. Never raises."""

    if time.monotonic() - _last_watch[0] < WATCH_SECONDS:
        return
    _last_watch[0] = time.monotonic()

    try:
        import codal_monthly
        import codal_watch
        lock = codal_monthly._single_instance("codal_watch.lock")
        if lock is None:
            return                      # a catch-up or another poll is running
        try:
            codal_watch.poll(verbose=verbose)
        finally:
            lock.close()
    except Exception as exc:                                    # noqa: BLE001
        if verbose:
            print("  feed watch failed: %s" % exc, flush=True)


def _sync_store(verbose=False):
    """New filings -> the fundamental store the analysis reads. Never raises."""

    try:
        import fundamental_db
        fundamental_db.sync_from_market_db(verbose=verbose)
    except Exception as exc:                                    # noqa: BLE001
        if verbose:
            print("  fundamental store sync failed: %s" % exc, flush=True)


# ==============================
# A CAMPAIGN: HOURS, NOT ONE BATCH
# ==============================
#
# Codal throttles on SUSTAINED access, not on rate: about 40-50 requests in
# quick succession earns a 429 whatever the gap between them, and -- this is
# the part that is not guessable -- retrying inside the rolling window RENEWS
# the throttle rather than clearing it. A run that simply keeps trying makes
# itself permanently rate-limited.
#
# So the shape that actually converges is a batch, a real pause, another batch.
# Both halves of the job are interleaved, because discovery is what creates
# work for the download half and starving it would leave the queue empty.

# A pause of the campaign, in short steps against the wall clock.
#
# One time.sleep(4 h) on a laptop does not end four hours later: the wait does
# not advance while the machine sleeps. Observed 2026-11-10: the 11-09 run
# (23.5 h budget, due out at 08:57) began a four-hour pause at 07:00, the
# laptop slept through most of the day, and at 21:30 it was still waiting --
# so Task Scheduler refused that morning's 09:00 run as already running.
# Stepping a minute at a time against time.time() ends the pause, and the
# run, when the clock on the wall says so.
SLEEP_STEP_SECONDS = 60


def _sleep_until(until, deadline=float("inf")):
    end = min(until, deadline)
    while True:
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(left, SLEEP_STEP_SECONDS))


def campaign(since, until, hours=None, batch=30, cool_minutes=15,
             per_company=None, verbose=True, delay=None, priority_date=None):
    """Alternate discovery and download for a wall-clock budget.

    Returns what was actually achieved. It is expected to stop short: this is a
    job measured in hours against a source that answers roughly 40 requests at
    a time, and a partial backfill honestly reported is the deliverable, not a
    failure of the run.
    """

    # The wall clock, not the monotonic one: see _sleep_until.
    deadline = time.time() + (hours * 3600 if hours else float("inf"))

    client = codal.CodalClient(delay=delay) if delay else codal.CodalClient()

    totals = {"letters": 0, "stored": 0, "unparseable": 0, "pauses": 0,
              "rounds": 0}

    # Rounds in a row that achieved NOTHING against a throttle. The wait
    # doubles on each one, because retrying inside Codal's rolling window
    # RENEWS the penalty rather than outlasting it -- observed 2026-08-23 as
    # three consecutive rounds of "5 of 5 refused" at a fixed 15m pause, and
    # observed before that as a panel that kept itself permanently 429'd.
    # One productive round clears it.
    refused_rounds = 0

    # The search host's own back-off. Discover shares search.codal.ir with
    # the two-hourly watch, codal_monthly and codal_portfolio; a round that
    # downloaded filings (another host) reset the streak above, so a
    # throttled discover was retried every 15 minutes all day and the watch
    # stalled for ten hours (11-11, the full test). Discover now waits out
    # its own doubling cool-off while downloads go on.
    search_refused = 0
    search_resume = 0.0

    while time.time() < deadline:

        totals["rounds"] += 1

        left = remaining_to_discover(since)

        paused = False
        letters_this_round = 0

        if left and time.time() >= search_resume:
            found = discover(since, limit=batch, verbose=verbose,
                             client=client)
            totals["letters"] += found["letters"]
            letters_this_round = found["letters"]
            if found["throttled"]:
                search_refused += 1
                pause = min(cool_minutes * 2 ** (search_refused - 1), 240) * 60
                search_resume = time.time() + pause
                if verbose:
                    print("campaign: the search host is throttled (streak %d); "
                          "discovery resumes in %dm, downloads go on"
                          % (search_refused, pause // 60), flush=True)
            else:
                search_refused = 0

        if time.time() >= deadline:
            break

        got = fetch(since, until, limit=batch, per_company=per_company,
                    verbose=verbose, client=client,
                    priority_date=priority_date,
                    deadline=max(0, deadline - time.time()))

        totals["stored"] += got["stored"]
        totals["unparseable"] += got["unparseable"]

        # A ROUND CAN BE THROTTLED WITHOUT EVER FAILING FIVE TIMES IN A ROW.
        #
        # The consecutive-failure guard catches an outage. It does not catch
        # the state Codal actually puts a bulk reader into, which is
        # intermittent refusal: two filings arrive, one is refused, two more
        # arrive. Nothing hits five in a row, the campaign concludes it is
        # fine, waits its one minute and goes again -- and retrying inside the
        # rolling window is what RENEWS the throttle rather than clearing it.
        # Observed directly: rounds degrading from 27 stored to 1 while the
        # consecutive counter never once reached five.
        #
        # So the rate is what decides. A quarter of a round refused means back
        # off properly.
        attempted = got["stored"] + got["unparseable"] + got["failed"]

        if attempted and got["failed"] / float(attempted) > 0.25:
            paused = True
            if verbose:
                print("campaign: %d of %d refused -- backing off"
                      % (got["failed"], attempted), flush=True)

        paused = paused or got["throttled"]

        _watch_due(verbose)

        if not left and got["pending"] == 0:
            # The history is complete, but the campaign is the process that
            # runs 23.5 hours a day -- so it keeps watching the feed for new
            # filings every WATCH_SECONDS until its hours are spent.
            if verbose:
                print("campaign: nothing left to discover or download; "
                      "watching the feed until the deadline", flush=True)
            while time.time() + WATCH_SECONDS < deadline:
                _sleep_until(time.time() + WATCH_SECONDS, deadline)
                _watch_due(verbose)
            break

        if time.time() >= deadline:
            break

        productive = bool(got["stored"] or got["unparseable"]
                          or letters_this_round)

        if paused and not productive:
            refused_rounds += 1
        elif productive:
            refused_rounds = 0

        # 15m, 30m, 60m, 120m, 240m cap. cool_minutes stays the knob; the
        # doubling is what stops a refused campaign from renewing the very
        # throttle it is waiting out.
        if paused:
            wait = min(cool_minutes * (2 ** max(0, refused_rounds - 1)),
                       240) * 60
        else:
            wait = 60
        # Nothing to download while discovery waits: sleep to its resume
        # rather than spinning a round a minute.
        if left and got["pending"] == 0 and time.time() + wait < search_resume:
            wait = int(search_resume - time.time()) + 1

        totals["pauses"] += 1

        # The analysis reads sirat_fundamentals.db, not this database; carry
        # each round's new filings across while the campaign waits anyway.
        if got["stored"]:
            _sync_store(verbose)

        if verbose:
            print("campaign: round %d done (%d letters, %d filings so far); "
                  "waiting %dm before the next batch%s"
                  % (totals["rounds"], totals["letters"], totals["stored"],
                     wait // 60,
                     " -- throttled (streak %d)" % refused_rounds
                     if paused else ""),
                  flush=True)

        if time.time() + wait > deadline:
            break

        _sleep_until(time.time() + wait, deadline)

    if verbose:
        print("campaign: %d rounds, %d letters discovered, %d filings stored"
              % (totals["rounds"], totals["letters"], totals["stored"]),
              flush=True)

    return totals


# ==============================
# COVERAGE REPORT
# ==============================

def coverage_report():
    """What the store now holds, by year and by company. Counts, not claims."""

    database.create_database()

    conn = database._connect()

    total, companies = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ins_code) FROM codal_filings"
    ).fetchone()

    by_year = conn.execute("""
        SELECT substr(published_at, 1, 4) AS year, COUNT(*),
               COUNT(DISTINCT ins_code)
        FROM codal_filings
        WHERE published_at IS NOT NULL
        GROUP BY year ORDER BY year
    """).fetchall()

    dated = conn.execute(
        "SELECT COUNT(*) FROM codal_filings WHERE published_at IS NOT NULL"
    ).fetchone()[0]

    per_company = conn.execute("""
        SELECT n, COUNT(*) FROM (
            SELECT ins_code, COUNT(*) AS n FROM codal_filings GROUP BY ins_code
        ) GROUP BY n ORDER BY n
    """).fetchall()

    equities = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE is_equity = 1"
    ).fetchone()[0]

    conn.close()

    return {
        "filings": total,
        "companies": companies,
        "equities": equities,
        "company_share": round(companies / float(equities), 4) if equities else None,
        "dated": dated,
        "undated": total - dated,
        "by_year": [{"year": y, "filings": n, "companies": c}
                    for y, n, c in by_year],
        "filings_per_company": [{"filings": n, "companies": c}
                                for n, c in per_company],
    }


def print_coverage():

    report = coverage_report()

    print()
    print("=" * 70)
    print("CODAL POINT-IN-TIME STORE")
    print("=" * 70)
    print("  filings stored        %s" % report["filings"])
    print("  companies covered     %s of %s equities (%.1f%%)"
          % (report["companies"], report["equities"],
             (report["company_share"] or 0) * 100))
    print("  with a publication date %s   without %s"
          % (report["dated"], report["undated"]))

    print()
    print("  by publication year (Jalali)")
    for row in report["by_year"]:
        print("     %-6s %6d filings   %4d companies"
              % (row["year"], row["filings"], row["companies"]))

    print()
    print("  filings per company")
    for row in report["filings_per_company"]:
        print("     %3d filings  %4d companies" % (row["filings"], row["companies"]))

    return report


# ==============================
# CLI
# ==============================

def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Backfill historical Codal statements for the backtester"
    )

    # 1394 is the fundamental panel's target depth, not the daily sync's
    # horizon. The old default of 1402/06/01 silently capped every run at the
    # sync window -- which is how 23,827 discovered statements were counted as
    # "median 3 filings" (RESEARCH_OPTIONS.md section 7): the pipeline's output
    # was measured and reported as the size of the input. The PIT gate needs 24
    # quarters of depth; 1394 leaves room in front of any window that gate can
    # accept.
    parser.add_argument("--from", dest="since", default="1394/01/01",
                        help="oldest publication date to keep (Jalali)")
    parser.add_argument("--to", dest="until", default=None,
                        help="newest publication date to keep (Jalali)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of statements downloaded")
    parser.add_argument("--per-company", type=int, default=None,
                        dest="per_company",
                        help="cap the statements taken per company")
    parser.add_argument("--priority-date", default=None, dest="priority_date",
                        help="Jalali date (the backtest's start) whose "
                             "coverage to buy first: one filing per company "
                             "that has none published before it")
    parser.add_argument("--minutes", type=float, default=None,
                        help="spend at most this long downloading")
    parser.add_argument("--hours", type=float, default=None,
                        help="run a paced campaign for this many hours")
    parser.add_argument("--batch", type=int, default=30,
                        help="requests per burst before pausing (campaign)")
    parser.add_argument("--cool-minutes", type=float, default=15,
                        dest="cool_minutes",
                        help="pause after a throttled burst (campaign)")
    parser.add_argument("--discover-only", action="store_true",
                        dest="discover_only",
                        help="search for filings but download none")
    parser.add_argument("--fetch-only", action="store_true", dest="fetch_only",
                        help="download what is already discovered")
    parser.add_argument("--report", action="store_true",
                        help="print the coverage report and exit")
    parser.add_argument("--seed", action="store_true",
                        help="carry already-parsed filings into codal_filings "
                             "and exit")
    parser.add_argument("--reparse-latest", type=int, default=None,
                        metavar="N",
                        help="re-read each company's N newest stored filings "
                             "that predate the outlook line items, then exit")
    parser.add_argument("--purge-subsidiaries", action="store_true",
                        help="remove stored subsidiary statements, restore "
                             "the issuer's own periods, and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --purge-subsidiaries: list what would be "
                             "removed and change nothing")
    parser.add_argument("--repair-superseded", action="store_true",
                        help="list codal_financials periods holding an older "
                             "release than codal_filings has, and exit "
                             "(dry run; --apply rewrites them)")
    parser.add_argument("--apply", action="store_true",
                        help="with --repair-superseded: write the repair")
    parser.add_argument("--refresh", action="store_true",
                        help="re-search companies already searched")
    parser.add_argument("--delay", type=float, default=None,
                        help="seconds between requests (default: config's %.1f)"
                             % config.CODAL_REQUEST_DELAY)
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.report:
        print_coverage()
        return 0

    verbose = not args.quiet

    if args.purge_subsidiaries:
        purge_subsidiaries(verbose=verbose, dry_run=args.dry_run)
        return 0

    if args.repair_superseded:
        repair_superseded(apply=args.apply, verbose=verbose)
        return 0

    if args.seed:
        seed_from_financials(verbose=verbose)
        if verbose:
            print_coverage()
        return 0

    # Always free, always first: nothing below should download a filing whose
    # figures are already on disk.
    seed_from_financials(verbose=verbose)

    until = args.until

    if until is None:
        today = jalali.today_jalali()
        until = "%04d/%02d/%02d" % today if today else "1499/12/29"

    client = codal.CodalClient(delay=args.delay) if args.delay else None

    if args.reparse_latest:
        reparse_latest(per_company=args.reparse_latest, verbose=verbose,
                       client=client,
                       deadline=(args.minutes * 60 if args.minutes else None))
        return 0

    if args.hours:
        campaign(args.since, until, hours=args.hours, batch=args.batch,
                 cool_minutes=args.cool_minutes,
                 per_company=args.per_company, verbose=verbose,
                 delay=args.delay, priority_date=args.priority_date)
        if verbose:
            print_coverage()
        return 0

    if not args.fetch_only:
        discover(args.since, limit=args.limit, verbose=verbose,
                 refresh=args.refresh, client=client)

    if not args.discover_only:
        fetch(args.since, until, limit=args.limit,
              per_company=args.per_company, verbose=verbose, client=client,
              priority_date=args.priority_date,
              deadline=(args.minutes * 60 if args.minutes else None))

    if verbose:
        print_coverage()

    return 0


if __name__ == "__main__":
    sys.exit(main())
