"""Watch Codal's market-wide feed and fetch new filings as they appear.

    python codal_watch.py                      one poll (scheduled every 15 min)
    python codal_watch.py --since 1405/05/01   catch up: page back to a date
    python codal_watch.py --report

WHY A FEED AND NOT A SWEEP
--------------------------
A statement published today used to wait for codal_sync to reach its company
-- 25 companies a run, so up to a month (found 2026-11-05). Codal's search
without a symbol is the whole market's announcement feed, newest first, and
it takes the same `LetterCode` filter as a company search. So one request
per letter type sees everything published since the last poll:

  ن-10  financial statements      -> codal_filings / codal_financials,
                                     then the fundamental store
  ن-30  monthly activity reports  -> monthly_reports

A poll pages back only until it reaches a letter it has already seen, so a
quiet quarter-hour costs two ~16 KB requests. Every document goes through
the raw archive (codal.ARCHIVE_DIR), and a filing is never fetched twice.

Companies outside today's equity universe are recorded as seen and skipped.
The per-company paths stay as safety nets: codal_monthly.py nightly, and
codal_sync from the panel.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import codal
import config
import database
import fundamental_db
import persian

FEEDS = ("ن-10", "ن-30")
MAX_PAGES_POLL = 5            # 100 letters per type per poll is ample
CATCH_UP_GAP_SECONDS = 10     # between pages of a --since catch-up


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_table():
    fundamental_db.create_database()
    conn = fundamental_db._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_seen (
        tracing_no TEXT PRIMARY KEY, letter_code TEXT, ins_code TEXT,
        published_at TEXT, outcome TEXT, seen_at TEXT)""")
    conn.commit()
    conn.close()


def _seen():
    conn = fundamental_db._connect()
    out = {r[0] for r in conn.execute("SELECT tracing_no FROM watch_seen")}
    conn.close()
    return out


def _mark(letter, ins_code, outcome):
    conn = fundamental_db._connect()
    conn.execute("INSERT OR REPLACE INTO watch_seen VALUES (?,?,?,?,?,?)",
                 (letter["tracing_no"], letter.get("letter_code"), ins_code,
                  letter.get("published_at"), outcome, _now()))
    conn.commit()
    conn.close()


def _universe():
    """{normalised symbol: instrument} for today's equities."""

    out = {}
    for inst in database.load_instruments(equities_only=True):
        for key in (inst.get("symbol_raw"), inst.get("symbol_fa")):
            if key:
                out[persian.normalize(key)] = inst
    return out


def _page(client, letter_code, page):
    params = dict(client.SEARCH_DEFAULTS)
    params.update({"PageNumber": str(page), "LetterCode": letter_code})
    payload = client._get(config.CODAL_SEARCH_URL, params=params).json()
    return [dict(codal._letter_row(raw, raw.get("Symbol") or ""),
                 symbol=raw.get("Symbol") or "")
            for raw in payload.get("Letters") or []]


def collect(client, letter_code, seen, since=None, pace=None):
    """New letters of one type, newest first, stopping at known ground.

    Without `since`, paging stops at the first page that holds a letter
    already seen. With it (a Jalali date), paging continues until letters
    are older than that date -- the catch-up mode.
    """

    fresh = []
    page = 1
    limit = None if since else MAX_PAGES_POLL

    while limit is None or page <= limit:
        try:
            letters = _page(client, letter_code, page)
        except Exception as exc:                                # noqa: BLE001
            if pace:
                pace(codal.classify(exc) == codal.KIND_TRANSIENT)
            print("watch: %s page %d failed: %s" % (letter_code, page, exc),
                  flush=True)
            break
        if pace:
            pace(False)
        if not letters:
            break

        hit_known = False
        for letter in letters:
            if letter["tracing_no"] in seen:
                hit_known = True
                continue
            if since and (letter.get("published_at") or "")[:10] < since:
                hit_known = True
                continue
            fresh.append(letter)

        if hit_known and not (since and
                              (letters[-1].get("published_at") or "")[:10] >= since):
            break
        page += 1
        # A catch-up walks a hundred pages or more of the search host, the
        # same host the quarter-hour polls need. Run back to back it earned
        # a 429 at page 34 and blocked the polls for half an hour
        # (2026-11-05), so it goes at a walking pace.
        if since:
            time.sleep(CATCH_UP_GAP_SECONDS)

    return fresh


def handle_statement(client, letter, inst):
    import codal_sync

    if str(letter["tracing_no"]) in database.codal_filing_tracing_numbers():
        return "already stored"
    if not letter.get("excel_url"):
        return "no excel"

    record, error, kind = codal_sync.parse_letter(client, letter)
    if error:
        if kind == codal.KIND_TRANSIENT:
            return None                      # not marked seen: retried next poll
        database.mark_letter_parsed(letter["tracing_no"])
        return "unreadable: %s" % error[:80]

    code = str(inst["ins_code"])
    record["tracing_no"] = letter["tracing_no"]
    database.save_codal_filing(code, record, published_at=letter.get("published_at"))
    database.save_codal_financials(code, record)
    database.mark_letter_parsed(letter["tracing_no"])
    return "stored"


def handle_monthly(client, letter, inst):
    import codal_monthly
    import monthly_reports

    codal_monthly._ensure_tables()
    if letter["tracing_no"] in codal_monthly._known():
        return "already stored"
    if not letter.get("excel_url"):
        return "no excel"

    got = client.fetch_sheets(letter["excel_url"], archive_key=letter["tracing_no"])
    code = str(inst["ins_code"])
    if not got.ok:
        if got.transient:
            return None
        codal_monthly._fail(code, letter, got.error or "unreadable")
        return "unreadable"
    record = monthly_reports.parse(got.data, letter["title"])
    if not record:
        codal_monthly._fail(code, letter, "no sales, purchase or income table")
        return "unreadable"
    codal_monthly.store(code, letter, record)
    codal_monthly._status(code, letters=None)
    return "stored"


HANDLERS = {"ن-10": handle_statement, "ن-30": handle_monthly}


def poll(since=None, verbose=True, client=None):
    import codal_monthly

    _ensure_table()
    client = client or codal.CodalClient()
    pace = codal_monthly.Pacer("watch", verbose)
    seen = _seen()
    universe = _universe()
    totals = {"new": 0, "stored": 0, "outside_universe": 0, "unreadable": 0,
              "retry_later": 0}

    for letter_code in FEEDS:
        fresh = collect(client, letter_code, seen, since=since, pace=pace)
        if verbose:
            print("watch: %s -- %d new letters" % (letter_code, len(fresh)),
                  flush=True)

        # Oldest first, so an interrupted catch-up has stored a contiguous
        # stretch rather than a scatter.
        for letter in reversed(fresh):
            totals["new"] += 1
            letter["letter_code"] = letter_code
            inst = universe.get(persian.normalize(letter.get("symbol") or ""))
            if not inst:
                _mark(letter, None, "outside universe")
                totals["outside_universe"] += 1
                continue

            database.save_codal_letters(str(inst["ins_code"]), [letter])
            outcome = HANDLERS[letter_code](client, letter, inst)
            pace(outcome is None)

            if outcome is None:
                totals["retry_later"] += 1
                continue
            _mark(letter, str(inst["ins_code"]), outcome)
            if outcome == "stored":
                totals["stored"] += 1
                if verbose:
                    print("  + %s %s" % (letter.get("symbol"), letter["title"][:70]),
                          flush=True)
            elif outcome.startswith("unreadable"):
                totals["unreadable"] += 1

    if totals["stored"]:
        fundamental_db.sync_from_market_db()

    if verbose:
        print("watch: %(new)d new, %(stored)d stored, %(outside_universe)d "
              "outside the universe, %(unreadable)d unreadable, "
              "%(retry_later)d left for the next poll" % totals, flush=True)
    return totals


def report():
    _ensure_table()
    conn = fundamental_db._connect()
    for row in conn.execute("""SELECT letter_code, outcome, COUNT(*), MAX(seen_at)
                               FROM watch_seen GROUP BY 1, 2 ORDER BY 1, 3 DESC"""):
        print("  %s  %-40s %5d  last %s" % row)
    conn.close()


def main(argv=None):
    # Scheduled runs write to a cp1252 log on this machine; a Persian symbol
    # in a print crashed the first one (2026-11-05). The batch file sets
    # PYTHONIOENCODING too -- this covers anyone running it without.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="catch up back to this Jalali date")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    if args.report:
        report()
        return 0

    import time
    import codal_monthly

    # A routine poll that finds the lock taken just leaves -- the next one is
    # a quarter-hour away. A catch-up must not: it waits its turn (up to an
    # hour), or a poll that happened to be running would skip the backlog.
    lock = codal_monthly._single_instance("codal_watch.lock")
    waited = 0
    while lock is None and args.since and waited < 3600:
        time.sleep(30)
        waited += 30
        lock = codal_monthly._single_instance("codal_watch.lock")
    if lock is None:
        print("watch: another poll is running")
        return 0

    poll(since=args.since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
