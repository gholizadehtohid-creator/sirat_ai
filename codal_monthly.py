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

EXIT CODE: 0 when the pass ran to its end, CUT_SHORT_EXIT (3) when the
throttle or the time limit stopped it with work left -- the evening task's
"step codal-monthly: exit N" line then says so.
"""

import argparse
import json
import os
import re
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

# ---- the history gets its turn (2026-11-23, the review) --------------------
# It never ran. The due pass goes first, and it is always full -- 758
# companies were due on 1405/09/02, two days after a month closed, against
# --limit 120 -- and always throttled: in all eight evening runs from 11-09
# to 11-23 search.codal.ir answered 429 and the pass sat out 15- and
# 30-minute cool-offs until --hours was gone (60 lines of 429, not one
# "monthly history" line). So while history is pending the due pass may use
# at most this share of --hours. Sixty per cent of the evening's 1.5 h is
# 54 minutes: unthrottled, 120 companies take about 20 (two requests each,
# thirty a batch, a minute between batches), and the 36 minutes left hold
# the history's 300 downloads at the same pace. The feed watch is what
# brings new reports in; the due pass only fills its gaps.
#
# The history starts with its own pacer whatever stopped the due pass: it
# asks only the download host, which codal.ir penalises apart from the
# search host (codal-api-behaviour, measured 2026-08-23), and three
# refusals in a row still end it.
DUE_SHARE = 0.6

# What main() returns when the throttle or the clock ended the pass with
# work left. It returned 0 always: on 2026-11-23 the step stopped at 92 of
# 120 companies with 20 failed and no history, and the evening log read
# "step codal-monthly: exit 0".
CUT_SHORT_EXIT = 3

# ---- a letter that is not a report (2026-11-23) ----------------------------
# codal.py reads a body too small to be a statement as a throttle page, and
# rightly: a transient failure is asked for again. But some letters ARE
# that small on every request -- 1,583 bytes for thirteen 1405/07 reports
# the watch had tried two to eight times each, 16,922 for both of فاراک's
# -- and nothing here counted the tries. In the history that was a loop: 15
# of the first 38 companies were headed by such a letter, each refusal
# ended that company's turn, and its older months would never have
# downloaded. A small answer is now counted, and at SMALL_BODY_TRIES the
# letter is settled as unreadable (monthly_failures; deleting its row there
# and in monthly_attempts re-opens it).
#
# Counted only when it cannot be the firewall: the request before it and
# the request after it both succeeded (a throttle refuses in runs), the
# size is the same as last time, and at most once a day -- so three counts
# are three different days.
SMALL_BODY_TRIES = 3
_SMALL_BODY = re.compile(r"returned only (\d+) bytes")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Stores whose tables this process has already made sure of. load() calls
# _ensure_tables for every company, and the outlook screen reads some 800:
# a connection and a five-statement script each, a third of the 2,398
# connections one screen opened (the review, 2026-11-23). Keyed by path --
# the tests move the store -- and redone if the file has gone.
_ENSURED = set()


def _ensure_tables():
    target = fundamental_db.path()
    if target in _ENSURED and os.path.exists(target):
        return
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
    CREATE TABLE IF NOT EXISTS monthly_attempts (
        tracing_no   TEXT PRIMARY KEY,
        ins_code     TEXT,
        attempts     INTEGER NOT NULL DEFAULT 0,
        bytes        INTEGER,
        last_tried   TEXT
    );
    """)
    conn.commit()
    conn.close()
    _ENSURED.add(target)


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


def _small_body(error):
    """The byte count in codal's "too small to be a statement", or None."""

    match = _SMALL_BODY.search(error or "")
    return int(match.group(1)) if match else None


def _count_small(code, letter, size):
    """One more small answer for a letter. True when that settles it."""

    tracing = letter["tracing_no"]
    today = _now()[:10]
    conn = fundamental_db._connect()
    row = conn.execute(
        "SELECT attempts, bytes, last_tried FROM monthly_attempts "
        "WHERE tracing_no = ?", (tracing,)).fetchone()
    if row and row[1] == size:
        # The same answer again; a second one the same day adds nothing.
        attempts = row[0] + (0 if str(row[2] or "")[:10] == today else 1)
    else:
        attempts = 1                    # the first, or not a constant answer
    conn.execute("INSERT OR REPLACE INTO monthly_attempts VALUES (?,?,?,?,?)",
                 (tracing, str(code), attempts, size, _now()))
    conn.commit()
    conn.close()
    if attempts < SMALL_BODY_TRIES:
        return False
    _fail(code, letter, "settled: the same %d-byte answer on %d days -- not a "
                        "report document" % (size, attempts))
    return True


class _SmallAnswers:
    """Counts "too small" answers that cannot be the firewall's.

    A throttle refuses in runs, so only an answer between two successes is
    counted: ok() confirms the one held, anything else drops it.
    """

    def __init__(self, have, totals, verbose=False):
        self.have, self.totals, self.verbose = have, totals, verbose
        self.held = None
        self.clear = True               # the request before succeeded

    def ok(self):
        held, self.held, self.clear = self.held, None, True
        if held and _count_small(*held):
            _code, letter, size = held
            self.have.add(letter["tracing_no"])
            self.totals["unreadable"] += 1
            self.totals["settled"] = self.totals.get("settled", 0) + 1
            if self.verbose:
                print("monthly: %s %s settled as unreadable -- %d bytes on "
                      "%d days" % (letter.get("symbol") or "",
                                   letter["tracing_no"], size, SMALL_BODY_TRIES),
                      flush=True)

    def small(self, code, letter, size):
        self.held = (code, letter, size) if self.clear and not self.held else None
        self.clear = False

    def refused(self):
        self.held, self.clear = None, False

    def finish(self):
        """The run ended on the answer held: nothing after it was refused,
        so it stands. Without this the last letter of a run -- or the only
        one left, once the rest of a history is filled -- was never counted
        and would be asked for every night for good."""

        self.ok()


def _fetch_one(client, pace, code, letter, have, totals, small=None):
    """Download, parse and store one letter. False when the host refused.

    A "too small" answer is not the host refusing but, as far as can be
    told, this letter: it is handed to `small` and the caller carries on
    with the company's next letter (the pacer still counts it, so three in
    a row cool off as before).
    """

    got = client.fetch_sheets(letter["excel_url"], archive_key=letter["tracing_no"])
    pace(got.transient if not got.ok else False)
    if not got.ok:
        if got.transient:
            totals["failed"] += 1
            size = _small_body(got.error)
            if small is not None and size is not None:
                small.small(code, letter, size)
                return True
            if small is not None:
                small.refused()
            return False
        if small is not None:
            small.ok()
        _fail(code, letter, got.error or "unreadable")
        totals["unreadable"] += 1
        return True
    if small is not None:
        small.ok()
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
    # The wall clock throughout, as the pacer's deadline always was: a
    # laptop's suspend counts as time gone.
    started = time.time()
    end = started + hours * 3600 if hours else None

    companies = due_companies()
    if limit:
        companies = companies[:int(limit)]
    have = _known()

    history_on = bool(history_months and history_limit)
    history_waiting = history_on and bool(history_due(history_months, have=have))
    # The due pass's share of the time, while history waits (DUE_SHARE).
    due_end = end
    if end and history_waiting:
        due_end = started + hours * 3600 * DUE_SHARE

    if verbose:
        print("monthly: %d companies due" % len(companies), flush=True)

    totals = {"checked": 0, "stored": 0, "unreadable": 0, "failed": 0,
              "cut_short": None, "left": 0}
    small = _SmallAnswers(have, totals, verbose)
    pace = Pacer("monthly", verbose, deadline=due_end)
    cut = []

    for index, inst in enumerate(companies):
        if (due_end and time.time() > due_end) or pace.stop:
            totals["left"] = len(companies) - index
            cut.append("the due pass: %s, %d companies left" % (
                "throttled" if pace.stop else
                "its share of the time is up" if due_end != end else
                "time is up", totals["left"]))
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
            if not _fetch_one(client, pace, code, letter, have, totals, small):
                break

        _status(code, letters=len(letters))

        if verbose and totals["checked"] % 50 == 0:
            print("monthly: %(checked)d checked, %(stored)d stored, "
                  "%(unreadable)d unreadable, %(failed)d failed" % totals,
                  flush=True)
    else:
        # Every company was reached, but the last of them were refused: the
        # pacer gave up, and those are still owed.
        if pace.stop is True:
            cut.append("the due pass: throttled at its last companies")

    if verbose:
        print("monthly: done -- %(checked)d checked, %(stored)d stored, "
              "%(unreadable)d unreadable, %(failed)d failed" % totals, flush=True)

    # ---- the history: known letters of missing months, no searches --------
    totals["history_stored"] = 0
    if history_on and not (end and time.time() > end):
        due = history_due(history_months, have=have)
        known = sum(len(v) for _c, v in due)
        if verbose:
            print("monthly history: %d company-months known and missing in %d "
                  "companies; this run takes at most %d"
                  % (known, len(due), history_limit), flush=True)
        # Its own pacer: what the search host refused the due pass says
        # nothing about the download host (DUE_SHARE).
        pace = Pacer("monthly history", verbose, deadline=end)
        before = totals["stored"]
        taken = 0
        stopped = None
        for code, letters in due:
            for letter in letters:
                if taken >= history_limit:
                    stopped = "limit"
                elif pace.stop is True:
                    stopped = "throttled"
                elif end and time.time() > end:
                    stopped = "time is up"
                if stopped:
                    break
                taken += 1
                if not _fetch_one(client, pace, code, letter, have, totals,
                                  small):
                    break                   # refused: the pacer decides
            if stopped:
                break
        if stopped is None and pace.stop is True:
            stopped = "throttled"
        totals["history_stored"] = totals["stored"] - before
        totals["history_downloaded"] = taken
        if verbose:
            print("monthly history: %d downloaded, %d stored"
                  % (taken, totals["history_stored"]), flush=True)
        remaining = min(history_limit, known) - taken
        if stopped in ("throttled", "time is up") and remaining > 0:
            cut.append("the history: %s, %d of this run's letters left"
                       % (stopped, remaining))
    elif history_waiting:
        cut.append("the history: time is up before it began")

    small.finish()

    if cut:
        totals["cut_short"] = "; ".join(cut)
        if verbose:
            print("monthly: cut short -- %s" % totals["cut_short"], flush=True)
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


def load_many(ins_codes=None, limit=13):
    """{ins_code: load(ins_code, limit)} for many companies, one query.

    The outlook's ranking opened a connection per company for this: 5 of its
    26 s (2026-11-25). Same order and the same rule as load() -- one report
    per period, the most recently published -- and only the kept payloads
    are decoded.
    """

    _ensure_tables()
    wanted = None if ins_codes is None else {str(c) for c in ins_codes}
    conn = fundamental_db._connect()
    try:
        rows = conn.execute("""
            SELECT ins_code, period_end, published_at, title, payload
            FROM monthly_reports
            ORDER BY ins_code, period_end DESC, published_at DESC
        """).fetchall()
    finally:
        conn.close()

    out, seen = {}, set()
    for code, period_end, published, title, payload in rows:
        code = str(code)
        if wanted is not None and code not in wanted:
            continue
        kept = out.setdefault(code, [])
        if len(kept) >= limit or (code, period_end) in seen:
            continue
        seen.add((code, period_end))
        record = json.loads(payload)
        record.update(published_at=published, title=title)
        kept.append(record)
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

    totals = run(hours=args.hours, per_company=args.per_company,
                 limit=args.limit, history_months=args.history_months,
                 history_limit=args.history_limit)
    report()
    return CUT_SHORT_EXIT if totals.get("cut_short") else 0


if __name__ == "__main__":
    sys.exit(main())
