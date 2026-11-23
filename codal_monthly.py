"""Collect Codal monthly activity reports (ن-۳۰) into the fundamental store.

    python codal_monthly.py                 one paced pass over every company
    python codal_monthly.py --hours 2       stop after two hours
    python codal_monthly.py --per-company 12   twelve months, searched
    python codal_monthly.py --history-months 0   skip the history phase
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

# ---- the monthly history (2026-11-23) --------------------------------------
# The user asked for the forward analysis to be made more accurate. Its
# error is in the margin, the cost side (outlook_calibration), and a year of
# monthly reports is what shows where sales and purchases are heading -- but
# only the newest one or two months were stored (1,183 reports for 610
# companies): the due pass downloads `per_company` of the letters it finds,
# and once a company is current it is never searched again.
#
# The letters of the missing months are mostly already known: every search
# the due pass made saved a company's twenty newest ن-۳۰ letters to
# codal_letters (7,046 for 656 companies). So the history phase SEARCHES
# NOTHING -- search.codal.ir is the host that throttles (codal-monthly
# memory) -- and only downloads those letters' documents, at most
# HISTORY_LIMIT per run, through the same pacer, after the due companies,
# inside the same --hours. Fifteen months: the last three against the same
# three a year before. On 2026-11-23 that was ~4,000 documents: about
# fourteen nights of the evening task's existing step.
HISTORY_MONTHS = 15
HISTORY_LIMIT = 300


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


def history_due(months=HISTORY_MONTHS, today=None, have=None):
    """[(ins_code, [letter, ...])] of known letters for months not yet stored.

    The window is the `months` closed months up to the last one. One letter
    per company-month, the newest published (a correction wins); companies
    with the most missing months first, so a run that stops early has
    filled the emptiest histories. Nothing here asks codal.ir anything.
    """

    if not months:
        return []
    _ensure_tables()
    have = have if have is not None else _known()
    last = last_closed_month(today)
    first = jalali.add_months(last[0], last[1], -(int(months) - 1))
    lo, hi = "%04d/%02d" % first, "%04d/%02d" % last

    conn = fundamental_db._connect()
    stored = {(r[0], r[1][:7]) for r in conn.execute(
        "SELECT ins_code, period_end FROM monthly_reports")}
    conn.close()

    conn = database._connect()
    try:
        rows = conn.execute("""
            SELECT tracing_no, ins_code, symbol, title, published_at, excel_url
            FROM codal_letters WHERE letter_code = ? AND excel_url IS NOT NULL
            ORDER BY published_at DESC
        """, (LETTER_CODE,)).fetchall()
    except Exception:                                           # noqa: BLE001
        rows = []
    finally:
        conn.close()

    equities = {str(r["ins_code"]) for r in database.load_instruments(equities_only=True)}
    wanted = {}
    for tracing, code, symbol, title, published, url in rows:
        code = str(code)
        if code not in equities or tracing in have or not url:
            continue
        period = monthly_reports.period_end_of(title)
        if not period:
            continue
        month = "%04d/%02d" % period[:2]
        if not lo <= month <= hi or (code, month) in stored:
            continue
        wanted.setdefault(code, {}).setdefault(month, {
            "tracing_no": tracing, "symbol": symbol, "title": title,
            "published_at": published, "excel_url": url})
    out = [(code, [by_month[m] for m in sorted(by_month, reverse=True)])
           for code, by_month in wanted.items()]
    out.sort(key=lambda pair: (-len(pair[1]), pair[0]))
    return out


def _fetch_one(client, pace, code, letter, have, totals):
    """Download, parse and store one letter. False when the host refused."""

    got = client.fetch_sheets(letter["excel_url"], archive_key=letter["tracing_no"])
    pace(got.transient if not got.ok else False)
    if not got.ok:
        if got.transient:
            totals["failed"] += 1
            return False
        _fail(code, letter, got.error or "unreadable")
        totals["unreadable"] += 1
        return True
    record = monthly_reports.parse(got.data, letter["title"])
    if record:
        store(code, letter, record)
        have.add(letter["tracing_no"])
        totals["stored"] += 1
    else:
        _fail(code, letter, "no sales, purchase or income table")
        totals["unreadable"] += 1
    return True


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

    def __init__(self, label, verbose=True, deadline=None, stop_after=None):
        self.label, self.verbose = label, verbose
        # Refusals in a row that end the pass outright, before any cool-off:
        # the portfolio pass slept 15 minutes on its third refused download
        # and then stopped anyway (the full test, 2026-11-12). None: cool off.
        self.stop_after = stop_after
        self.in_batch = 0
        self.refused = 0
        # A wall-clock end (time.time()): a cool-off that would run past it
        # sets `stop` instead of sleeping, so --hours 1.5 cannot become five
        # hours holding the lock (the portfolio review, 2026-11-11). Wall
        # clock, not monotonic, so a laptop's suspend counts as time gone.
        self.deadline = deadline
        self.stop = False

    def __call__(self, transient):
        self.in_batch += 1
        if transient:
            self.refused += 1
        else:
            self.refused = 0
        if self.stop_after and self.refused >= self.stop_after:
            if self.verbose:
                print("%s: %d refused in a row -- stopping, not waiting"
                      % (self.label, self.refused), flush=True)
            self.stop = True
            return
        if self.refused >= 3:
            wait = min(15 * 2 ** (self.refused - 3), 240) * 60
            if self.deadline is not None and time.time() + wait > self.deadline:
                if self.verbose:
                    print("%s: throttled; a %dm cool-off would pass the time "
                          "limit -- stopping" % (self.label, wait // 60), flush=True)
                self.stop = True
                return
            if self.verbose:
                print("%s: throttled; waiting %dm" % (self.label, wait // 60),
                      flush=True)
            time.sleep(wait)
            self.in_batch = 0
        elif self.in_batch >= BATCH:
            time.sleep(PAUSE_SECONDS)
            self.in_batch = 0


def run(hours=None, per_company=2, limit=None, verbose=True, client=None,
        history_months=HISTORY_MONTHS, history_limit=HISTORY_LIMIT):
    """One paced pass over the companies that are due, then the history."""

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
    pace = Pacer("monthly", verbose,
                 deadline=time.time() + hours * 3600 if hours else None)

    for inst in companies:
        if (deadline and time.monotonic() > deadline) or pace.stop:
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
            if not _fetch_one(client, pace, code, letter, have, totals):
                break

        _status(code, letters=len(letters))

        if verbose and totals["checked"] % 50 == 0:
            print("monthly: %(checked)d checked, %(stored)d stored, "
                  "%(unreadable)d unreadable, %(failed)d failed" % totals,
                  flush=True)

    if verbose:
        print("monthly: done -- %(checked)d checked, %(stored)d stored, "
              "%(unreadable)d unreadable, %(failed)d failed" % totals, flush=True)

    # ---- the history: known letters of missing months, no searches --------
    totals["history_stored"] = 0
    if history_months and history_limit and not pace.stop and \
            not (deadline and time.monotonic() > deadline):
        due = history_due(history_months, have=have)
        if verbose:
            print("monthly history: %d company-months known and missing in %d "
                  "companies; this run takes at most %d"
                  % (sum(len(v) for _c, v in due), len(due), history_limit),
                  flush=True)
        before = totals["stored"]
        taken = 0
        for code, letters in due:
            stop = False
            for letter in letters:
                if taken >= history_limit or pace.stop or \
                        (deadline and time.monotonic() > deadline):
                    stop = True
                    break
                taken += 1
                if not _fetch_one(client, pace, code, letter, have, totals):
                    break                   # refused: the pacer decides
            if stop:
                break
        totals["history_stored"] = totals["stored"] - before
        if verbose:
            print("monthly history: %d downloaded, %d stored"
                  % (taken, totals["history_stored"]), flush=True)
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
    parser.add_argument("--history-months", type=int, default=HISTORY_MONTHS,
                        help="closed months of history to fill from known "
                             "letters (0: none)")
    parser.add_argument("--history-limit", type=int, default=HISTORY_LIMIT,
                        help="history documents downloaded per run at most")
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

    run(hours=args.hours, per_company=args.per_company, limit=args.limit,
        history_months=args.history_months, history_limit=args.history_limit)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
