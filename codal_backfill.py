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

    conn.commit()
    conn.close()


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

    work.sort(key=lambda item: (item.get("priority", 1), item["rank"],
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

    stored = failed = skipped = 0
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

            if kind == codal.KIND_TRANSIENT:
                failed += 1
                consecutive += 1
                if consecutive >= config.CODAL_ABORT_AFTER_FAILURES:
                    throttled = True
                    if verbose:
                        print("  pausing: %d consecutive transient failures"
                              % consecutive, flush=True)
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
        print("fetch: %d stored, %d unparseable, %d transient failures"
              % (stored, skipped, failed), flush=True)
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print("    %3d  %s" % (count, reason), flush=True)

    return {"pending": len(queue), "stored": stored, "unparseable": skipped,
            "failed": failed, "throttled": throttled, "reasons": reasons}


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

    return text[:70] or "unknown"


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

def campaign(since, until, hours=None, batch=30, cool_minutes=15,
             per_company=None, verbose=True, delay=None, priority_date=None):
    """Alternate discovery and download for a wall-clock budget.

    Returns what was actually achieved. It is expected to stop short: this is a
    job measured in hours against a source that answers roughly 40 requests at
    a time, and a partial backfill honestly reported is the deliverable, not a
    failure of the run.
    """

    deadline = time.monotonic() + (hours * 3600 if hours else float("inf"))

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

    while time.monotonic() < deadline:

        totals["rounds"] += 1

        left = remaining_to_discover(since)

        paused = False
        letters_this_round = 0

        if left:
            found = discover(since, limit=batch, verbose=verbose,
                             client=client)
            totals["letters"] += found["letters"]
            letters_this_round = found["letters"]
            paused = paused or found["throttled"]

        if time.monotonic() >= deadline:
            break

        got = fetch(since, until, limit=batch, per_company=per_company,
                    verbose=verbose, client=client,
                    priority_date=priority_date,
                    deadline=max(0, deadline - time.monotonic()))

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

        if not left and got["pending"] == 0:
            if verbose:
                print("campaign: nothing left to discover or download",
                      flush=True)
            break

        if time.monotonic() >= deadline:
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

        totals["pauses"] += 1

        if verbose:
            print("campaign: round %d done (%d letters, %d filings so far); "
                  "waiting %dm before the next batch%s"
                  % (totals["rounds"], totals["letters"], totals["stored"],
                     wait // 60,
                     " -- throttled (streak %d)" % refused_rounds
                     if paused else ""),
                  flush=True)

        if time.monotonic() + wait > deadline:
            break

        time.sleep(wait)

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
