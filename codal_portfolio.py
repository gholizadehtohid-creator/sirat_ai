"""Investment companies' monthly portfolio reports (ن-۳۱): catch up, stay current.

WHY THIS EXISTS
---------------
P/NAV -- what an investment company, a holding or an insurer is valued on --
needs the company's portfolio at market: its ن-۳۱ monthly report. On
2026-11-11 the user asked why every NAV dated from the end of Tir. 130 of
the 141 stored portfolios were 1405/04/31, while TEDPIX had risen 72.5%
since, because:

  * ن-۳۱ was fetched only by codal_sync's per-company pass (one filing per
    company per run), and that pass, at 25 companies a run, had last
    reached 742 of 827 companies in August;
  * the two-hourly feed watch (codal_watch) followed ن-۱۰ and ن-۳۰ but not
    ن-۳۱, so no new portfolio ever arrived on its own.

This module is the catch-up, in the shape codal_monthly uses for ن-۳۰: one
Symbol + LetterCode search per company returns its newest portfolio
reports, and only the newest one not yet held is downloaded -- NAV is a
position at a date, so an older month answers a question nobody asks. The
watch now handles ن-۳۱ too (codal_watch.handle_portfolio, through
fetch_newest here), so after this pass the reports arrive as published.

Reviewed adversarially before its first run (2026-11-11): searches and
downloads are paced apart (the two hosts are penalised apart), three refused
downloads in a row end the pass rather than renew the block, a cool-off that
would outlast --hours ends it too, corrections of a held month are fetched,
and nothing older than the newest few letters is ever downloaded.

    python codal_portfolio.py                 # a bounded pass (1.5 h, 120 companies)
    python codal_portfolio.py --hours 3 --limit 300   # longer, by hand
    python codal_portfolio.py --report        # what is held, how old
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import codal
import config
import database
import persian

LETTER_CODE = "ن-31"
CHECK_HOURS = 20                 # a filer is asked again after this
NEVER_FILED_HOURS = 24 * 7       # no ن-۳۱, or none with a portfolio: weekly
# Only this many of a company's NEWEST letters are looked at, read ones
# included: walking back further downloaded ever older reports that cannot
# give today's NAV.
PER_COMPANY = 3
# With no portfolio held, a report more than this many months before the
# last closed month is not today's NAV and is never stored as it.
FLOOR_MONTHS = 2
# Downloads refused in a row before the pass stops: the download host is
# penalised apart from the search host, and a search that succeeds must not
# reset the count.
DOWNLOAD_REFUSALS_STOP = 3
# The default bounds, the ones the evening gives codal_monthly: an uncapped
# first pass is ~250 searches, twice what got the search host to block the
# watch on 2026-11-05.
DEFAULT_HOURS = 1.5
DEFAULT_LIMIT = 120

# What main() returns when the throttle or the clock ended the pass with
# companies left (codal_monthly.CUT_SHORT_EXIT). It returned 0 always: on
# 2026-11-19 the pass ended "0 checked, 0 stored, 4 failed" under a 429 and
# the evening log could not tell that from a pass that found nothing new.
CUT_SHORT_EXIT = 3


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_tables():
    database.create_database()
    conn = database._connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codal_portfolio_status (
            ins_code TEXT PRIMARY KEY,
            checked_at TEXT,
            newest_period TEXT,
            letters INTEGER,
            error TEXT,
            outcome TEXT
        )""")
    conn.commit()
    conn.close()


def _status_rows():
    conn = database._connect()
    rows = {str(r[0]): (r[1], r[2], r[3], r[4]) for r in conn.execute(
        "SELECT ins_code, checked_at, newest_period, letters, outcome "
        "FROM codal_portfolio_status")}
    conn.close()
    return rows


def _set_status(ins_code, letters=None, error=None, outcome=None):
    newest = (database.load_codal_portfolio_latest([ins_code])
              .get(str(ins_code)) or {}).get("period_end")
    conn = database._connect()
    conn.execute("INSERT OR REPLACE INTO codal_portfolio_status "
                 "(ins_code, checked_at, newest_period, letters, error, outcome) "
                 "VALUES (?,?,?,?,?,?)",
                 (str(ins_code), _now(), newest, letters, error, outcome))
    conn.commit()
    conn.close()


def _months_back(period, months):
    """'YYYY/MM' `months` before `period` ('YYYY/MM')."""

    import jalali
    y, m = (int(x) for x in str(period).split("/")[:2])
    return "%04d/%02d" % jalali.add_months(y, m, -months)


def want_period(today=None):
    """The newest month a portfolio report can exist for: last closed month."""

    import codal_monthly
    return "%04d/%02d" % codal_monthly.last_closed_month(today)


def held_period(ins_code):
    """The newest portfolio month held for one company, or None."""

    row = database.load_codal_portfolio_latest([ins_code]).get(str(ins_code))
    return (row or {}).get("period_end")


def due_companies(today=None, now=None):
    """The companies whose newest portfolio is behind, most promising first.

    Candidates: every equity that has filed a portfolio before, and every
    equity in an industry valued on its assets (config.
    EARNINGS_MULTIPLE_NOT_MEANINGFUL). Those already holding last month's
    are skipped, as are those asked within CHECK_HOURS (NEVER_FILED_HOURS
    when the last search found no ن-۳۱, or none that held a portfolio -- a
    bank files none). Companies that have filed before come first, the
    oldest portfolio first.
    """

    _ensure_tables()
    want = want_period(today)
    now = now or datetime.now(timezone.utc)
    held = database.load_codal_portfolio_latest()
    status = _status_rows()

    due = []
    for inst in database.load_instruments(equities_only=True):
        code = str(inst["ins_code"])
        sector = (inst.get("sector_name") or "").strip()
        newest = (held.get(code) or {}).get("period_end")
        if not newest and sector not in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            continue
        if newest and str(newest)[:7] >= want:
            continue
        checked, _period, letters, outcome = status.get(
            code, (None, None, None, None))
        if checked:
            try:
                age = (now - datetime.fromisoformat(checked)).total_seconds() / 3600
            except ValueError:
                age = None
            limit = (NEVER_FILED_HOURS
                     if letters == 0 or outcome == "no portfolio"
                     else CHECK_HOURS)
            if age is not None and age < limit:
                continue
        due.append((0 if newest else 1, str(newest or ""), inst))

    due.sort(key=lambda item: (item[0], item[1]))
    return [inst for _filer, _newest, inst in due]


def search(client, symbol):
    """(letters, kind, error): the company's newest ن-۳۱ letters, newest first."""

    params = dict(client.SEARCH_DEFAULTS)
    params.update({"Symbol": symbol, "PageNumber": "1",
                   "LetterCode": LETTER_CODE})
    try:
        payload = client._get(config.CODAL_SEARCH_URL, params=params).json()
    except Exception as exc:                                    # noqa: BLE001
        return None, codal.classify(exc), "%s: %s" % (type(exc).__name__,
                                                      str(exc)[:120])
    letters = [codal._letter_row(raw, symbol)
               for raw in payload.get("Letters") or []]
    letters.sort(key=lambda l: str(l.get("published_at") or ""), reverse=True)
    return letters, None, None


def fetch_newest(client, ins_code, letters, newest_held=None, pace=None,
                 want=None):
    """Download and store the newest unread portfolio among `letters`.

    `letters` newest first. Returns {"stored", "transient", "requested",
    "error", "outcome"}. Looked at: only the PER_COMPANY newest letters with
    a body, read ones counted. Skipped without a request: a letter already
    read; one for a month older than the one held (walking on, since a late
    correction of an old month can sit ahead of a newer report); the
    original of the month held -- its correction («اصلاحیه») IS downloaded,
    and save_codal_portfolio keeps whichever release is newer; with nothing
    held, a report more than FLOOR_MONTHS before the last closed month.
    Stops at the first stored portfolio or the first refusal.
    """

    import codal_sync
    import financials

    out = {"stored": 0, "transient": False, "requested": 0, "error": None,
           "outcome": "nothing newer"}
    known = database.known_tracing_numbers(ins_code)
    floor = None if newest_held else _months_back(want or want_period(),
                                                  FLOOR_MONTHS)
    window = [l for l in letters if l.get("excel_url")][:PER_COMPANY]

    candidates = []
    for letter in window:
        if str(letter.get("tracing_no")) in known:
            continue
        title = letter.get("title") or ""
        period = financials.period_end(title)
        if not period:
            continue
        if newest_held:
            if str(period) < str(newest_held):
                continue
            correction = "اصلاح" in persian.normalize(title)
            if str(period) == str(newest_held) and not correction:
                continue
        elif floor and str(period)[:7] < floor:
            continue
        candidates.append((str(period), str(letter.get("published_at") or ""),
                           letter))
    # The newest MONTH first, its latest release first: a correction of the
    # month held, published after the next month's report, must not be
    # fetched ahead of that report.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

    for _period, _published, letter in candidates:
        record, error, kind = codal_sync.parse_portfolio_letter(client, letter)
        out["requested"] += 1
        transient = kind == codal.KIND_TRANSIENT
        if pace:
            pace(transient)
        if transient:
            out.update(transient=True, outcome="refused")
            return out
        database.mark_letter_parsed(letter["tracing_no"])
        if error:
            out["error"] = str(error)[:200]
            out["outcome"] = "unreadable"
            continue
        if record:
            record["published_at"] = letter.get("published_at")
            out["stored"] = database.save_codal_portfolio(ins_code, record)
            out["outcome"] = ("stored" if out["stored"]
                              else "an older release than the one held")
            return out
        out["outcome"] = "no portfolio"
    return out


def run(hours=DEFAULT_HOURS, limit=DEFAULT_LIMIT, verbose=True, client=None):
    """One paced pass over the companies whose portfolio is behind."""

    import codal_monthly
    import codal_sync

    _ensure_tables()
    client = client or codal.CodalClient()
    end = time.time() + hours * 3600 if hours else None
    want = want_period()

    companies = due_companies()
    if limit:
        companies = companies[:int(limit)]

    if verbose:
        print("portfolio: %d companies due (want %s)" % (len(companies), want),
              flush=True)

    totals = {"checked": 0, "stored": 0, "none": 0, "failed": 0,
              "downloads": 0, "cut_short": None}
    # Searches and downloads paced apart: the two hosts are penalised apart,
    # and a good search reset the count a refused download had started.
    pace = codal_monthly.Pacer("portfolio", verbose, deadline=end)
    downloads = codal_monthly.Pacer("portfolio-download", verbose,
                                    deadline=end,
                                    stop_after=DOWNLOAD_REFUSALS_STOP)
    refused_in_a_row = 0

    for index, inst in enumerate(companies):
        if (end and time.time() > end) or pace.stop or downloads.stop:
            # Which of the two it was: the log said "time is up" for both.
            throttled = pace.stop is True or downloads.stop is True
            totals["cut_short"] = "%s, %d companies left" % (
                "throttled" if throttled else "time is up",
                len(companies) - index)
            if verbose:
                print("portfolio: %s" % totals["cut_short"], flush=True)
            break

        code = str(inst["ins_code"])
        # Read now, not at the start: the watch may have stored this month
        # while the pass was paced or cooling off.
        held = held_period(code)
        if held and str(held)[:7] >= want:
            continue

        symbol = codal_sync.codal_symbol(inst)
        letters, kind, error = search(client, symbol)
        pace(kind == codal.KIND_TRANSIENT)

        if letters is None:
            totals["failed"] += 1
            if kind != codal.KIND_TRANSIENT:
                _set_status(code, error=error, outcome="search failed")
            continue

        totals["checked"] += 1
        database.save_codal_letters(code, letters)
        got = fetch_newest(client, code, letters, newest_held=held,
                           pace=downloads, want=want)
        totals["downloads"] += got["requested"]

        if got["transient"]:
            # Left without a status row, so the next pass asks again.
            totals["failed"] += 1
            refused_in_a_row += 1
            if refused_in_a_row >= DOWNLOAD_REFUSALS_STOP:
                totals["cut_short"] = (
                    "the download host refused %d in a row, %d companies "
                    "left" % (refused_in_a_row, len(companies) - index))
                if verbose:
                    print("portfolio: the download host refused %d in a row -- "
                          "stopping until the next pass" % refused_in_a_row,
                          flush=True)
                break
            continue
        if got["requested"]:
            refused_in_a_row = 0

        if got["stored"]:
            totals["stored"] += 1
        elif letters:
            totals["none"] += 1
        _set_status(code, letters=len(letters), error=got["error"],
                    outcome=got["outcome"])

        if verbose and totals["checked"] % 20 == 0:
            print("portfolio: %(checked)d checked, %(stored)d stored, "
                  "%(none)d nothing newer, %(failed)d failed, "
                  "%(downloads)d downloads" % totals, flush=True)

    if verbose:
        print("portfolio: done -- %(checked)d checked, %(stored)d stored, "
              "%(none)d nothing newer, %(failed)d failed, %(downloads)d "
              "downloads" % totals, flush=True)
    return totals


def reparse_archived(verbose=True, only_empty=True):
    """Re-read, from the raw archive, every stored portfolio that read as
    empty -- no listed cost, value or gain under holdings. No request.

    For the reports parsed before nav learnt to sum the holdings when Codal's
    export leaves the «جمع» rows at zero (2026-11-11). A report without an
    archived copy (parsed before 2026-11-05) is counted and left: the next
    newer report replaces it.
    """

    import gzip
    import os

    import nav

    conn = database._connect()
    rows = conn.execute(
        "SELECT ins_code, period_end, tracing_no, title FROM codal_portfolio "
        + ("" if not only_empty else
           "WHERE COALESCE(listed_cost, 0) = 0 AND COALESCE(listed_market, 0) = 0 "
           "AND COALESCE(listed_unrealised, 0) = 0 AND COALESCE(holdings, 0) > 0")
    ).fetchall()
    conn.close()

    out = {"empty": len(rows), "repaired": 0, "no_archive": 0, "still_empty": 0}
    for code, period, tracing, title in rows:
        path = os.path.join(codal.ARCHIVE_DIR, str(tracing)[-2:],
                            "%s.gz" % tracing)
        if not tracing or not os.path.exists(path):
            out["no_archive"] += 1
            continue
        with gzip.open(path, "rb") as handle:
            body = handle.read()
        text = body.decode("utf-8", errors="replace") \
            if body[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" else body
        try:
            sheets = codal.split_sheets(text) if isinstance(text, str) \
                else codal.split_sheets_xls(text)
        except Exception:                                       # noqa: BLE001
            sheets = None
        portfolio = nav.extract_portfolio(sheets) if sheets else None
        if not portfolio or not portfolio.get("listed_market"):
            out["still_empty"] += 1
            continue
        record = dict(portfolio, period_end=period, tracing_no=tracing,
                      title=title)
        if database.save_codal_portfolio(code, record):
            out["repaired"] += 1
    if verbose:
        print(("portfolio: %(empty)d read as empty" if only_empty else
               "portfolio: %(empty)d re-read") +
              " -- %(repaired)d rewritten from the archive, %(no_archive)d "
              "without an archived copy, %(still_empty)d still empty" % out,
              flush=True)
    return out


def _archived_sheets(tracing):
    """The sheets of an archived filing, or None (no copy, or unreadable)."""

    import gzip
    import os

    if not tracing:
        return None
    path = os.path.join(codal.ARCHIVE_DIR, str(tracing)[-2:], "%s.gz" % tracing)
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rb") as handle:
        body = handle.read()
    try:
        if body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return codal.split_sheets_xls(body)
        return codal.split_sheets(body.decode("utf-8", errors="replace"))
    except Exception:                                           # noqa: BLE001
        return None


def backfill_rows(verbose=True, only_missing=True):
    """Read each stored portfolio's holdings from the raw archive. No request.

    The rows are kept from 2026-11-25 (nav.extract_holdings) and value a
    portfolio at today's prices (portfolio_value.py); the portfolios stored
    before that have their totals only. Every newest portfolio, and any
    other the archive holds, gets its rows here.
    """

    import nav

    database.create_database()
    conn = database._connect()
    stored = conn.execute(
        "SELECT p.ins_code, p.period_end, p.tracing_no FROM codal_portfolio p"
        + (" WHERE NOT EXISTS (SELECT 1 FROM codal_portfolio_rows r WHERE "
           "r.ins_code = p.ins_code AND r.period_end = p.period_end)"
           if only_missing else "")).fetchall()
    conn.close()

    out = {"portfolios": len(stored), "filled": 0, "rows": 0,
           "no_archive": 0, "no_rows": 0}
    for code, period, tracing in stored:
        sheets = _archived_sheets(tracing)
        if sheets is None:
            out["no_archive"] += 1
            continue
        rows = nav.extract_holdings(sheets)
        if not rows:
            out["no_rows"] += 1
            continue
        out["rows"] += database.save_portfolio_rows(code, period, rows)
        out["filled"] += 1
    if verbose:
        print("portfolio rows: %(portfolios)d portfolios, %(filled)d filled "
              "with %(rows)d holdings from the archive, %(no_archive)d without "
              "an archived copy, %(no_rows)d with no holding rows" % out,
              flush=True)
    return out


def report():
    """How old the held portfolios are."""

    held = database.load_codal_portfolio_latest()
    by_period = {}
    for row in held.values():
        key = str(row.get("period_end") or "")[:7]
        by_period[key] = by_period.get(key, 0) + 1
    print("portfolios held: %d companies" % len(held))
    for period, count in sorted(by_period.items(), reverse=True)[:8]:
        print("  %s  %d" % (period, count))
    print("want: %s" % want_period())
    return by_period


def main(argv=None):
    import codal_monthly

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--reparse-archived", action="store_true",
                        help="re-read empty-looking portfolios from the raw "
                             "archive (no request)")
    parser.add_argument("--rows", action="store_true",
                        help="read the stored portfolios' holdings from the "
                             "raw archive (no request)")
    parser.add_argument("--all", action="store_true",
                        help="with --reparse-archived: every archived "
                             "portfolio, not only the empty-looking ones")
    args = parser.parse_args(argv)

    if args.report:
        report()
        return 0
    if args.reparse_archived:
        reparse_archived(only_empty=not args.all)
        report()
        return 0
    if args.rows:
        backfill_rows(only_missing=not args.all)
        return 0

    lock = codal_monthly._single_instance("codal_portfolio.lock")
    if lock is None:
        print("portfolio: another pass is running; nothing to do")
        return 0
    try:
        totals = run(hours=args.hours, limit=args.limit)
    finally:
        lock.close()
    report()
    return CUT_SHORT_EXIT if totals.get("cut_short") else 0


if __name__ == "__main__":
    sys.exit(main())
