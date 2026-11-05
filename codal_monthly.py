"""Collect Codal monthly activity reports (ن-۳۰) into the fundamental store.

    python codal_monthly.py                 one paced pass over every company
    python codal_monthly.py --hours 2       stop after two hours
    python codal_monthly.py --per-company 12   twelve months of history
    python codal_monthly.py --report        what is stored

INCREMENTAL, THREE WAYS
-----------------------
  1. A company is searched only when a report can be due: its newest stored
     report ends before the last completed month, and it has not been
     checked in the last CHECK_HOURS. Once a company has filed for a month,
     it is not searched again until the next month closes.
  2. The search asks Codal for ن-۳۰ letters only (`LetterCode`), so one
     ~16 KB request lists a company's twenty newest reports.
  3. A report already stored (or settled as unreadable) is never downloaded
     again, and every document downloaded is kept in the raw archive
     (codal.ARCHIVE_DIR), so a parser change re-reads disk, not codal.ir.

PACING: codal.ir's firewall throttles sustained access and renews the block
when retried inside it (see codal_backfill's campaign). Batches of BATCH
requests, a one-minute pause between them, and an escalating cool-off from
15 to 240 minutes after a refused batch.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import codal
import config
import database
import fundamental_db
import jalali
import monthly_reports

LETTER_CODE = "ن-30"
CHECK_HOURS = 20
NEVER_FILED_HOURS = 24 * 7
BATCH = 30
PAUSE_SECONDS = 60


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_tables():
    fundamental_db.create_database()
    conn = fundamental_db._connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS monthly_reports (
        tracing_no   TEXT PRIMARY KEY,
        ins_code     TEXT NOT NULL,
        period_end   TEXT NOT NULL,
        published_at TEXT,
        title        TEXT,
        template     TEXT,
        payload      TEXT,
        parsed_at    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_monthly_company
        ON monthly_reports (ins_code, period_end);
    CREATE TABLE IF NOT EXISTS monthly_status (
        ins_code     TEXT PRIMARY KEY,
        checked_at   TEXT,
        newest_period TEXT,
        letters      INTEGER,
        error        TEXT
    );
    CREATE TABLE IF NOT EXISTS monthly_failures (
        tracing_no   TEXT PRIMARY KEY,
        ins_code     TEXT,
        reason       TEXT,
        tried_at     TEXT
    );
    """)
    conn.commit()
    conn.close()


def last_closed_month(today=None):
    y, m, _d = today or jalali.today_jalali()
    return jalali.add_months(y, m, -1)


def due_companies(today=None, now=None):
    """Equities a new monthly report can be waiting for, oldest check first."""

    _ensure_tables()
    want = "%04d/%02d" % last_closed_month(today)
    now = now or datetime.now(timezone.utc)

    conn = fundamental_db._connect()
    status = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT ins_code, checked_at, newest_period, letters FROM monthly_status")}
    conn.close()

    due = []
    for inst in database.load_instruments(equities_only=True):
        code = str(inst["ins_code"])
        checked, newest, letters = status.get(code, (None, None, None))
        if newest and newest[:7] >= want:
            continue
        if checked:
            try:
                age = (now - datetime.fromisoformat(checked)).total_seconds() / 3600
            except ValueError:
                age = None
            # A company that has never filed one (investment companies file
            # ن-۳۱ instead) is asked weekly, not nightly.
            limit = NEVER_FILED_HOURS if letters == 0 else CHECK_HOURS
            if age is not None and age < limit:
                continue
        due.append((checked or "", inst))

    due.sort(key=lambda pair: pair[0])
    return [inst for _checked, inst in due]


def _known():
    conn = fundamental_db._connect()
    have = {r[0] for r in conn.execute("SELECT tracing_no FROM monthly_reports")}
    have |= {r[0] for r in conn.execute("SELECT tracing_no FROM monthly_failures")}
    conn.close()
    return have


def search(client, symbol):
    """The company's newest ن-۳۰ letters, or a CodalResult error."""

    params = dict(client.SEARCH_DEFAULTS)
    params.update({"Symbol": symbol, "PageNumber": "1",
                   "LetterCode": LETTER_CODE})
    try:
        payload = client._get(config.CODAL_SEARCH_URL, params=params).json()
    except Exception as exc:                                    # noqa: BLE001
        return None, codal.classify(exc), "%s: %s" % (type(exc).__name__,
                                                      str(exc)[:120])
    # The same row shape codal_sync stores, digits already normalised.
    letters = [codal._letter_row(raw, symbol)
               for raw in payload.get("Letters") or []]
    return letters, None, None


def store(ins_code, letter, record):
    conn = fundamental_db._connect()
    conn.execute("""
        INSERT OR REPLACE INTO monthly_reports
        (tracing_no, ins_code, period_end, published_at, title, template,
         payload, parsed_at) VALUES (?,?,?,?,?,?,?,?)
    """, (letter["tracing_no"], str(ins_code), record["period_end"],
          letter.get("published_at"), letter.get("title"), record.get("template"),
          json.dumps(record, ensure_ascii=False), _now()))
    conn.commit()
    conn.close()


def _fail(ins_code, letter, reason):
    conn = fundamental_db._connect()
    conn.execute("INSERT OR REPLACE INTO monthly_failures VALUES (?,?,?,?)",
                 (letter["tracing_no"], str(ins_code), reason[:200], _now()))
    conn.commit()
    conn.close()


def _status(ins_code, letters=None, error=None):
    conn = fundamental_db._connect()
    newest = conn.execute(
        "SELECT MAX(period_end) FROM monthly_reports WHERE ins_code = ?",
        (str(ins_code),)).fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO monthly_status VALUES (?,?,?,?,?)",
                 (str(ins_code), _now(), newest, letters, error))
    conn.commit()
    conn.close()


class Pacer:
    """Batches of BATCH requests with a pause, and an escalating cool-off.

    Three refusals in a row wait 15 minutes, each further one doubles it up
    to 240: retrying inside codal.ir's throttle window renews the block.
    """

    def __init__(self, label, verbose=True):
        self.label, self.verbose = label, verbose
        self.in_batch = 0
        self.refused = 0

    def __call__(self, transient):
        self.in_batch += 1
        if transient:
            self.refused += 1
        else:
            self.refused = 0
        if self.refused >= 3:
            wait = min(15 * 2 ** (self.refused - 3), 240) * 60
            if self.verbose:
                print("%s: throttled; waiting %dm" % (self.label, wait // 60),
                      flush=True)
            time.sleep(wait)
            self.in_batch = 0
        elif self.in_batch >= BATCH:
            time.sleep(PAUSE_SECONDS)
            self.in_batch = 0


def run(hours=None, per_company=2, limit=None, verbose=True, client=None):
    """One paced pass over the companies that are due."""

    import codal_sync

    _ensure_tables()
    client = client or codal.CodalClient()
    deadline = time.monotonic() + hours * 3600 if hours else None

    companies = due_companies()
    if limit:
        companies = companies[:int(limit)]
    have = _known()

    if verbose:
        print("monthly: %d companies due" % len(companies), flush=True)

    totals = {"checked": 0, "stored": 0, "unreadable": 0, "failed": 0}
    pace = Pacer("monthly", verbose)

    for inst in companies:
        if deadline and time.monotonic() > deadline:
            break

        code = str(inst["ins_code"])
        symbol = codal_sync.codal_symbol(inst)
        letters, kind, error = search(client, symbol)
        pace(kind == codal.KIND_TRANSIENT)

        if letters is None:
            totals["failed"] += 1
            if kind != codal.KIND_TRANSIENT:
                _status(code, error=error)
            continue

        totals["checked"] += 1
        database.save_codal_letters(code, letters)
        fresh = [l for l in letters if l["tracing_no"] not in have and l["excel_url"]]

        for letter in fresh[:per_company]:
            got = client.fetch_sheets(letter["excel_url"],
                                      archive_key=letter["tracing_no"])
            pace(got.transient if not got.ok else False)
            if not got.ok:
                if got.transient:
                    totals["failed"] += 1
                    break
                _fail(code, letter, got.error or "unreadable")
                totals["unreadable"] += 1
                continue
            record = monthly_reports.parse(got.data, letter["title"])
            if record:
                store(code, letter, record)
                have.add(letter["tracing_no"])
                totals["stored"] += 1
            else:
                _fail(code, letter, "no sales, purchase or income table")
                totals["unreadable"] += 1

        _status(code, letters=len(letters))

        if verbose and totals["checked"] % 50 == 0:
            print("monthly: %(checked)d checked, %(stored)d stored, "
                  "%(unreadable)d unreadable, %(failed)d failed" % totals,
                  flush=True)

    if verbose:
        print("monthly: done -- %(checked)d checked, %(stored)d stored, "
              "%(unreadable)d unreadable, %(failed)d failed" % totals, flush=True)
    return totals


def load(ins_code, limit=13):
    """Stored reports for one company, newest period first, one per period.

    A correction (اصلاحیه) is a later letter for the same month; the most
    recently published one wins.
    """

    _ensure_tables()
    conn = fundamental_db._connect()
    rows = conn.execute("""
        SELECT period_end, published_at, title, payload FROM monthly_reports
        WHERE ins_code = ? ORDER BY period_end DESC, published_at DESC
    """, (str(ins_code),)).fetchall()
    conn.close()

    out, seen = [], set()
    for period_end, published, title, payload in rows:
        if period_end in seen:
            continue
        seen.add(period_end)
        record = json.loads(payload)
        record.update(published_at=published, title=title)
        out.append(record)
        if len(out) >= limit:
            break
    return out


def report():
    _ensure_tables()
    conn = fundamental_db._connect()
    n, companies = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ins_code) FROM monthly_reports").fetchone()
    newest = conn.execute(
        "SELECT substr(period_end,1,7), COUNT(DISTINCT ins_code) FROM monthly_reports "
        "GROUP BY 1 ORDER BY 1 DESC LIMIT 4").fetchall()
    failures = conn.execute("SELECT COUNT(*) FROM monthly_failures").fetchone()[0]
    conn.close()
    print("monthly reports stored: %d, companies: %d, unreadable: %d"
          % (n, companies, failures))
    for month, count in newest:
        print("  %s  %d companies" % (month, count))


def _single_instance(name="codal_monthly.lock"):
    """An OS lock held for the life of the process, or None if taken."""

    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs",
                        name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+")
    try:
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
    except OSError:
        return None
    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--per-company", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    if args.report:
        report()
        return 0

    # One collector at a time: the evening task and an overnight campaign can
    # both start one, and two would search the same companies twice and
    # double the load on codal.ir's firewall.
    lock = _single_instance()
    if lock is None:
        print("monthly: another collector is running; nothing to do")
        return 0

    run(hours=args.hours, per_company=args.per_company, limit=args.limit)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
