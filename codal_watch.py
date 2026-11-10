"""Watch Codal's market-wide feed and fetch new filings as they appear.

    python codal_watch.py                      one poll (scheduled every 2 hours)
    python codal_watch.py --since 1405/05/01   catch up: page back to a date
    python codal_watch.py --rewind 1405/08/04  re-sweep from a day (dry run;
                                               --apply saves it)
    python codal_watch.py --report

WHY A FEED AND NOT A SWEEP
--------------------------
A statement published today used to wait for codal_sync to reach its company
-- 25 companies a run, so up to a month (found 2026-11-05). Codal's search
without a symbol is the whole market's announcement feed, newest first, and
every hit carries its LetterCode. The poll keeps two of them:

  ن-10  financial statements      -> codal_filings / codal_financials,
                                     then the fundamental store
  ن-30  monthly activity reports  -> monthly_reports

The feed is read UNFILTERED and the letter codes are picked out here. The
search accepts a `LetterCode` parameter, but a market-wide filtered query
is one Codal's server cannot answer: from the evening of 2026-11-05 every
one ran past a 20-second read timeout (25 s with curl), while the same
feed unfiltered answered in 0.3 s and a filtered query for one symbol in
8 s. It was not a block on us -- the host served everything else. Four
and a half hours of polls found nothing because of it.

A poll pages back to a COMPLETE-THROUGH mark: every watched letter published
at or before it has been handled. So a quiet two hours cost one ~16 KB
request. Every document goes through the raw archive (codal.ARCHIVE_DIR),
and a filing is never fetched twice.

Until 2026-11-11 a poll stopped at the first page holding a letter it had
already seen, taking everything older as handled. Four things break that,
and each left letters unhandled for good -- no announcement seen from
1405/08/05 to 08/13, and 36 companies' 1405/07/30 monthly report listed but
never stored: a letter whose fetch failed transiently, left for "the next
poll" that stops above it; a feed page that failed and ended the poll; the
page cap; and a run interrupted after its oldest letters (the 11-06
catch-up stopped at 08/04 18:16, below a poll that had already seen 08/14).
Now the mark moves only when a poll has covered everything down to it, a
transient failure waits in watch_retry, and a backlog deeper than the page
cap is swept SWEEP_PAGES a poll from where the last one stopped (scan).

Companies outside today's equity universe are recorded as seen and skipped.
The per-company paths stay as safety nets: codal_monthly.py nightly, and
codal_sync from the panel.
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

import codal
import codal_announcements
import config
import database
import fundamental_db
import persian
import settings

# Statements, monthly reports, and since 2026-11-06 the announcements a
# manual trader acts on (codal_announcements.KINDS).
FEEDS = ("ن-10", "ن-30") + codal_announcements.CODES
# 200 letters of every type. A quiet day fills ~3 pages, but polls are two
# hours apart and a month-end deadline day publishes several times that.
MAX_PAGES_POLL = 10
CATCH_UP_GAP_SECONDS = 10     # between pages of a --since catch-up or a sweep
FEED_READ_TIMEOUT = 60        # seconds; see _page
# Deeper pages a poll walks, at the catch-up's pace, while a backlog is
# being swept: ~4 minutes every two hours, against the 333-page catch-up
# that ended on a timeout (2026-11-06).
SWEEP_PAGES = 20
PAGE_SIZE = 20                # letters a feed page carries (the sweep's arithmetic)
FEED_PAGES_PER_DAY = 14       # measured 2026-11-06: 333 pages reached back 23 days
# A letter whose fetch keeps failing transiently is retried from watch_retry
# this many polls (about a day), then given up; ن-10 and ن-30 still have the
# backfill and codal_monthly behind them.
RETRY_PER_POLL = 30
RETRY_LIMIT = 12


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_table():
    fundamental_db.create_database()
    conn = fundamental_db._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_seen (
        tracing_no TEXT PRIMARY KEY, letter_code TEXT, ins_code TEXT,
        published_at TEXT, outcome TEXT, seen_at TEXT)""")
    # The complete-through mark and an unfinished sweep (scan), and the
    # letters whose fetch failed transiently, kept whole so a later poll
    # retries them without paging back to find them.
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_state (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_retry (
        tracing_no TEXT PRIMARY KEY, letter TEXT, attempts INTEGER,
        last_tried TEXT)""")
    conn.commit()
    conn.close()


def load_state():
    """{'through': stamp or None, 'sweep': dict or None}; see scan().

    With nothing saved yet the mark starts at the newest letter seen, which
    is where the first-seen stop would have stopped -- the gap that rule
    left below it is for rewind(), not for a silent default.
    """

    conn = fundamental_db._connect()
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM watch_state").fetchall())
    except sqlite3.OperationalError:            # before the table exists
        rows = {}
    if "through" not in rows:
        try:
            rows["through"] = conn.execute(
                "SELECT MAX(published_at) FROM watch_seen").fetchone()[0]
        except sqlite3.OperationalError:
            rows["through"] = None
    conn.close()

    sweep = None
    if rows.get("sweep"):
        try:
            sweep = json.loads(rows["sweep"])
        except ValueError:
            sweep = None
    return {"through": rows.get("through") or None, "sweep": sweep}


def save_state(state):
    conn = fundamental_db._connect()
    now = _now()
    conn.executemany("INSERT OR REPLACE INTO watch_state VALUES (?, ?, ?)", [
        ("through", state.get("through") or "", now),
        ("sweep", json.dumps(state["sweep"], ensure_ascii=False)
         if state.get("sweep") else "", now),
    ])
    conn.commit()
    conn.close()


def _queued():
    """{tracing_no: (letter, attempts)} waiting for a retry, least recent first."""

    conn = fundamental_db._connect()
    try:
        rows = conn.execute("SELECT tracing_no, letter, attempts FROM watch_retry"
                            " ORDER BY last_tried").fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    out = {}
    for number, text, attempts in rows:
        try:
            out[number] = (json.loads(text), int(attempts or 0))
        except (TypeError, ValueError):
            continue
    return out


def _queue(letter, attempts):
    conn = fundamental_db._connect()
    conn.execute("INSERT OR REPLACE INTO watch_retry VALUES (?, ?, ?, ?)",
                 (letter["tracing_no"], json.dumps(letter, ensure_ascii=False),
                  attempts, _now()))
    conn.commit()
    conn.close()


def _unqueue(tracing_no):
    conn = fundamental_db._connect()
    conn.execute("DELETE FROM watch_retry WHERE tracing_no = ?", (tracing_no,))
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


def _page(client, page):
    """One page of the whole market's feed, every letter type.

    Only PageNumber and search -- not client.SEARCH_DEFAULTS. Those sixteen
    parameters are what a company search sends, and market-wide they put
    the query on a path Codal's backend answers slowly or not at all: on
    2026-11-05 the full set (and `Mains=true` alone) timed out at 25 s,
    while the bare form returned the same letters, the same Total, in
    0.3-2 s on most tries. Even the bare form spiked to 10-30 s that
    night, hence the longer read timeout.
    """

    params = {"PageNumber": str(page), "search": "true"}
    payload = client._get(config.CODAL_SEARCH_URL, params=params,
                          timeout=(settings.TIMEOUT_CONNECT,
                                   FEED_READ_TIMEOUT)).json()
    return [dict(codal._letter_row(raw, raw.get("Symbol") or ""),
                 symbol=raw.get("Symbol") or "")
            for raw in payload.get("Letters") or []]


def collect(client, seen, since=None, pace=None, codes=FEEDS):
    """New letters of the watched types, newest first, stopping at known ground.

    Without `since`, paging stops at the first page that holds a watched
    letter already seen; pages of other letter types alone do not stop it.
    With `since` (a Jalali date), paging continues until letters are older
    than that date -- the catch-up mode. A routine poll uses scan() instead.
    """

    return _collect(client, seen, since, pace, codes)[0]


def _collect(client, seen, since=None, pace=None, codes=FEEDS):
    """collect(), and the newest stamp read if paging ended where it should
    -- known ground, `since`, or the end of the feed -- rather than on a
    failed page or the page cap (None then)."""

    fresh = []
    page = 1
    limit = None if since else MAX_PAGES_POLL
    newest, complete = None, False

    while limit is None or page <= limit:
        try:
            letters = _page(client, page)
        except Exception as exc:                                # noqa: BLE001
            if pace:
                pace(codal.classify(exc) == codal.KIND_TRANSIENT)
            print("watch: feed page %d failed: %s" % (page, exc), flush=True)
            break
        if pace:
            pace(False)
        if not letters:
            complete = True
            break
        newest = _later(newest, *_stamps(letters))

        hit_known = False
        for letter in letters:
            if letter.get("letter_code") not in codes:
                continue
            if letter["tracing_no"] in seen:
                hit_known = True
                continue
            if since and (letter.get("published_at") or "")[:10] < since:
                hit_known = True
                continue
            fresh.append(letter)

        if hit_known and not (since and
                              (letters[-1].get("published_at") or "")[:10] >= since):
            complete = True
            break
        page += 1
        # A catch-up walks a hundred pages or more of the search host, the
        # same host the routine polls need. Run back to back it earned
        # a 429 at page 34 and blocked the polls for half an hour
        # (2026-11-05), so it goes at a walking pace.
        if since:
            time.sleep(CATCH_UP_GAP_SECONDS)

    return fresh, (newest if complete else None)


def _catch_up(client, seen, since, pace):
    """collect(since=...) that cannot leave a hole: (fresh, state or None).

    The mark is lowered to `since` and saved BEFORE anything is fetched. On
    2026-11-06 a catch-up collected 332 pages, handled them oldest first and
    was cut off at 08/04 18:16, below letters a routine poll had already
    seen -- so 08/04-08/14 stayed unhandled. Now an interrupted catch-up
    leaves the routine polls a mark below its range, and they sweep the
    rest. Only a catch-up that got all the way down raises it again.
    """

    if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", since or ""):
        raise ValueError("--since wants a Jalali day like 1405/08/04, got %r"
                         % (since,))

    state = load_state()
    mark = "%s 00:00:00" % since
    if state["through"] is None or mark < state["through"]:
        state = {"through": mark, "sweep": None}
        save_state(state)

    fresh, newest = _collect(client, seen, since=since, pace=pace)

    if newest and state["through"] == mark:
        return fresh, {"through": _later(mark, newest), "sweep": None}
    return fresh, None


def _read(client, page, pace):
    """One page of the feed, or None if the request failed."""

    try:
        letters = _page(client, page)
    except Exception as exc:                                    # noqa: BLE001
        if pace:
            pace(codal.classify(exc) == codal.KIND_TRANSIENT)
        print("watch: feed page %d failed: %s" % (page, exc), flush=True)
        return None
    if pace:
        pace(False)
    return letters


def _stamps(letters):
    return [l["published_at"] for l in letters if l.get("published_at")]


def _later(*stamps):
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def scan(client, seen, state, pace=None, codes=FEEDS, sweep_pages=None):
    """New letters of the watched types down to the mark, newest first.

    Returns (fresh, state). Save `state` only once every letter in `fresh`
    has been handled or queued for retry: an interrupted run then claims
    nothing it did not cover, and the next poll reads the same ground again.

      through  every watched letter published at or before it is handled.
               The top of the feed is read down to it (MAX_PAGES_POLL pages
               at most) and it moves to the newest letter only when the
               poll got all the way down.
      sweep    a backlog deeper than that: {top, top_at, floor_at,
               floor_index}, the stretch from the top letter of the last
               poll down to its floor having been covered. The next poll
               reads the new letters above `top`, then jumps to the floor's
               page -- where it sits now, shifted by the letters published
               since, one page early -- and walks on toward `through`,
               sweep_pages a poll at the catch-up's pace. A jump that lands
               below the floor steps back, so nothing between is skipped.

    Every unseen watched letter on a page read is taken, whatever its date:
    the marks decide how far to read, not what to keep.
    """

    through, sweep = state.get("through"), state.get("sweep")
    sweep_pages = SWEEP_PAGES if sweep_pages is None else sweep_pages
    fresh, taken = [], set()

    def take(letters):
        for letter in letters:
            number = letter.get("tracing_no")
            if (letter.get("letter_code") in codes and number
                    and number not in seen and number not in taken):
                taken.add(number)
                fresh.append(letter)

    # ---- the top of the feed, down to ground already covered ------------
    ground = sweep["top_at"] if sweep else through
    above, reached, page, size = [], False, 0, PAGE_SIZE
    while page < MAX_PAGES_POLL:
        letters = _read(client, page + 1, pace)
        if letters is None:
            break
        page += 1
        if not letters:
            reached = True                          # the end of the feed
            break
        if page == 1:
            size = len(letters)
        above.extend(letters)
        take(letters)
        stamps = _stamps(letters)
        if ground is None or (stamps and min(stamps) <= ground):
            reached = True
            break

    if not above:
        return fresh, state                         # page 1 failed: no news

    stamps = _stamps(above)
    top = {"top": above[0].get("tracing_no"),
           "top_at": _later(*stamps) or ground}

    if not reached:
        # The new letters alone outran the page cap, or a page failed: what
        # was read is covered and the rest, down to the mark, is a backlog.
        # A sweep already under way restarts from here (its stretch is now
        # cut off from this one): pages are re-read, nothing is skipped.
        return fresh, {"through": through, "sweep": dict(
            top, floor_at=min(stamps) if stamps else top["top_at"],
            floor_index=len(above) - 1)}

    if not sweep:
        return fresh, {"through": _later(through, top["top_at"]),
                       "sweep": None}

    # ---- the sweep, from where the last poll stopped ----------------------
    shift = next((i for i, l in enumerate(above)
                  if l.get("tracing_no") == sweep.get("top")), None)
    if shift is None:
        shift = sum(1 for l in above
                    if (l.get("published_at") or "") > (sweep["top_at"] or ""))
    floor_at = sweep["floor_at"]
    floor_index = sweep["floor_index"] + shift
    number = max(page + 1, floor_index // size)     # one page of overlap
    checked = number == page + 1                    # contiguous with the top
    done, requests = False, 0

    while requests < sweep_pages:
        time.sleep(CATCH_UP_GAP_SECONDS)
        letters = _read(client, number, pace)
        requests += 1
        if letters is None:
            break
        stamps = _stamps(letters)
        if not checked:
            # Landed below the floor (or past the end): step back, or the
            # letters in between would never be read.
            if number > page + 1 and (not letters or (
                    stamps and max(stamps) < floor_at)):
                number -= 1
                continue
            checked = True
        if not letters:
            done = True                             # the end of the feed
            break
        take(letters)
        if stamps:
            floor_at = min(floor_at, min(stamps))
        floor_index = (number - 1) * size + len(letters) - 1
        if through is None or (stamps and min(stamps) <= through):
            done = True
            break
        number += 1

    if done:
        return fresh, {"through": _later(through, top["top_at"]),
                       "sweep": None}
    return fresh, {"through": through, "sweep": dict(
        top, floor_at=floor_at, floor_index=floor_index)}


def handle_statement(client, letter, inst):
    import codal_sync

    if str(letter["tracing_no"]) in database.codal_filing_tracing_numbers():
        return "already stored"
    if not letter.get("excel_url"):
        return "no excel"

    record, error, kind = codal_sync.parse_letter(client, letter)
    if error:
        if kind == codal.KIND_TRANSIENT:
            return None                      # queued in watch_retry by poll()
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
for _code in codal_announcements.CODES:
    HANDLERS[_code] = codal_announcements.handle

# Handlers that put a request on Codal for every letter, and so are paced.
# An announcement is a row from the feed itself, except an annual AGM's
# decisions, whose page is fetched for the dividend.
NETWORK_CODES = ("ن-10", "ن-30", persian.normalize(
    codal_announcements.ANNUAL_AGM_DECISIONS))


def _process(client, letter, universe, pace, totals, verbose, attempts=0):
    """Hand one letter to its handler; mark it seen, or queue it for retry.

    Returns the outcome, None when the letter was queued.
    """

    inst = universe.get(persian.normalize(letter.get("symbol") or ""))
    if not inst:
        _mark(letter, None, "outside universe")
        _unqueue(letter["tracing_no"])
        totals["outside_universe"] += 1
        return "outside universe"

    networked = letter["letter_code"] in NETWORK_CODES
    if letter["letter_code"] in ("ن-10", "ن-30"):
        # codal_letters is the statement/monthly ledger the per-company
        # paths read; announcements live in their own table.
        database.save_codal_letters(str(inst["ins_code"]), [letter])
    outcome = HANDLERS[letter["letter_code"]](client, letter, inst)
    if networked:
        pace(outcome is None)

    if outcome is None:
        attempts += 1
        if attempts < RETRY_LIMIT:
            _queue(letter, attempts)
            totals["retry_later"] += 1
            return None
        outcome = "gave up after %d transient failures" % attempts
    _mark(letter, str(inst["ins_code"]), outcome)
    _unqueue(letter["tracing_no"])
    if outcome.startswith("stored"):
        totals["stored"] += 1
        if verbose:
            print("  + %s %s" % (letter.get("symbol"), letter["title"][:70]),
                  flush=True)
    elif outcome.startswith("unreadable"):
        totals["unreadable"] += 1
    return outcome


def poll(since=None, verbose=True, client=None):
    import codal_monthly

    _ensure_table()
    client = client or codal.CodalClient()
    pace = codal_monthly.Pacer("watch", verbose)
    universe = _universe()
    totals = {"new": 0, "stored": 0, "outside_universe": 0, "unreadable": 0,
              "retry_later": 0, "retried": 0}

    # What an earlier poll could not finish, from the queue: paging back to
    # find it again is what used to lose it. Two refusals in a row and the
    # rest wait for the next poll -- a third would set the Pacer sleeping
    # 15-240 minutes inside a poll the backfill campaign is waiting on.
    refused = 0
    for letter, attempts in list(_queued().values())[:RETRY_PER_POLL]:
        totals["retried"] += 1
        if _process(client, letter, universe, pace, totals, verbose,
                    attempts) is None:
            refused += 1
            if refused >= 2:
                break
        else:
            refused = 0

    # A queued letter is not new: it waits its turn rather than costing a
    # second request in the same poll.
    seen = _seen() | set(_queued())
    if since:
        fresh, state = _catch_up(client, seen, since, pace)
    else:
        fresh, state = scan(client, seen, load_state(), pace=pace)
    if verbose:
        for letter_code in ("ن-10", "ن-30"):
            print("watch: %s -- %d new letters" % (letter_code, sum(
                1 for l in fresh if l["letter_code"] == letter_code)), flush=True)
        print("watch: announcements -- %d new letters" % sum(
            1 for l in fresh if l["letter_code"] in codal_announcements.CODES),
            flush=True)

    # Oldest first, so an interrupted catch-up has stored a contiguous
    # stretch rather than a scatter.
    for letter in reversed(fresh):
        totals["new"] += 1
        _process(client, letter, universe, pace, totals, verbose)

    # Only now: every letter the marks claim is handled or queued.
    if state is not None:
        save_state(state)

    if totals["stored"]:
        fundamental_db.sync_from_market_db()

    if verbose:
        print("watch: %(new)d new, %(stored)d stored, %(outside_universe)d "
              "outside the universe, %(unreadable)d unreadable, "
              "%(retry_later)d left for the next poll (%(retried)d retried)"
              % totals, flush=True)
        if state is not None:
            print("watch: complete through %s%s" % (
                state.get("through"),
                "; sweeping back, reached %s" % state["sweep"]["floor_at"]
                if state.get("sweep") else ""), flush=True)
    return totals


def rewind(day, apply=False, verbose=True):
    """Move the complete-through mark back to the start of `day` (Jalali).

    The repair for the ground the first-seen stop lost: the routine polls
    then sweep back over it, SWEEP_PAGES a poll, and take every watched
    letter there they have not seen; a seen one costs only its share of a
    page. The mark only ever moves back. Dry run unless `apply`; idempotent.
    """

    if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", day or ""):
        raise ValueError("rewind wants a Jalali day like 1405/08/04, got %r"
                         % (day,))

    state = load_state()
    mark = "%s 00:00:00" % day
    if state["through"] and state["through"] < mark:
        mark = state["through"]                 # never forward: that skips

    # What is known to be waiting there: statements and monthly reports the
    # per-company paths listed and nothing has stored. Announcements are
    # only in the feed, so the per-day count of those seen shows their hole.
    conn = fundamental_db._connect()
    seen = {r[0] for r in conn.execute("SELECT tracing_no FROM watch_seen")}
    try:
        settled = {r[0] for r in conn.execute(
            "SELECT tracing_no FROM monthly_reports UNION "
            "SELECT tracing_no FROM monthly_failures")}
    except sqlite3.OperationalError:
        settled = set()
    per_day = dict(conn.execute(
        "SELECT substr(published_at, 1, 10), COUNT(*) FROM watch_seen "
        "WHERE published_at > ? AND letter_code NOT IN ('ن-10', 'ن-30') "
        "GROUP BY 1", (mark,)).fetchall())
    conn.close()

    universe = {str(i["ins_code"]) for i in _universe().values()}
    mconn = database._connect()
    letters = mconn.execute(
        "SELECT tracing_no, ins_code, symbol, letter_code, title, published_at "
        "FROM codal_letters WHERE letter_code IN ('ن-10', 'ن-30') "
        "AND published_at > ? ORDER BY published_at", (mark,)).fetchall()
    settled |= {r[0] for r in mconn.execute(
        "SELECT tracing_no FROM codal_filings WHERE published_at > ?", (mark,))}
    try:
        settled |= {r[0] for r in mconn.execute(
            "SELECT tracing_no FROM codal_backfill_failures")}
    except sqlite3.OperationalError:
        pass
    mconn.close()

    unseen = [l for l in letters if str(l[1]) in universe and l[0] not in seen]
    unstored = [l for l in unseen if l[0] not in settled]

    days = len(_days_between(mark[:10])) or None
    pages = days * FEED_PAGES_PER_DAY if days else None

    if verbose:
        print("watch: complete through %s -> %s%s" % (
            state["through"], mark, "" if apply else
            "  (dry run, nothing written; --apply saves it)"), flush=True)
        if state.get("sweep"):
            print("  a sweep under way (reached %s) is restarted"
                  % state["sweep"].get("floor_at"), flush=True)
        if pages is not None:
            print("  %d days of feed, ~%d pages: ~%d polls at %d pages a poll"
                  % (days, pages, -(-pages // SWEEP_PAGES), SWEEP_PAGES),
                  flush=True)
        print("  statements/monthly reports listed there, never seen by the "
              "watch: %d, of which stored by no path: %d"
              % (len(unseen), len(unstored)), flush=True)
        for row in unstored[:8]:
            print("    %s %s %s %s" % (row[5], row[3], row[2],
                                       (row[4] or "")[:60]), flush=True)
        empty = [d for d in _days_between(mark[:10]) if d not in per_day]
        if empty:
            print("  days with no announcement seen (Fridays and holidays "
                  "have none anyway): %s" % ", ".join(empty), flush=True)

    if apply:
        _ensure_table()
        save_state({"through": mark, "sweep": None})

    return {"through": mark, "previous": state["through"], "days": days,
            "pages": pages, "unseen": len(unseen), "unstored": len(unstored),
            "applied": bool(apply)}


def _days_between(first):
    """Jalali days from `first` up to today, as 'YYYY/MM/DD' strings."""

    from datetime import date, timedelta

    import jalali

    day = date(*jalali.to_gregorian(*first.split("/")))
    out = []
    while day <= date.today():
        out.append("%04d/%02d/%02d" % jalali.today_jalali(day))
        day += timedelta(days=1)
    return out


def report():
    _ensure_table()
    conn = fundamental_db._connect()
    for row in conn.execute("""SELECT letter_code, outcome, COUNT(*), MAX(seen_at)
                               FROM watch_seen GROUP BY 1, 2 ORDER BY 1, 3 DESC"""):
        print("  %s  %-40s %5d  last %s" % row)
    queued = conn.execute("SELECT COUNT(*) FROM watch_retry").fetchone()[0]
    conn.close()
    state = load_state()
    print("  complete through %s%s; %d letters waiting for a retry" % (
        state["through"], "; sweeping back, reached %s" % state["sweep"].get(
            "floor_at") if state.get("sweep") else "", queued))


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
    parser.add_argument("--rewind", metavar="YYYY/MM/DD",
                        help="move the complete-through mark back to this "
                             "Jalali day, so polls sweep it again (dry run)")
    parser.add_argument("--apply", action="store_true",
                        help="with --rewind: save the new mark")
    args = parser.parse_args(argv)

    if args.report:
        report()
        return 0

    if args.rewind and not args.apply:
        rewind(args.rewind)
        return 0

    import time
    import codal_monthly

    # A routine poll that finds the lock taken just leaves -- the next one is
    # two hours away. A catch-up must not: it waits its turn (up to an
    # hour), or a poll that happened to be running would skip the backlog.
    # Nor a rewind: a poll finishing after it would save its own mark over it.
    lock = codal_monthly._single_instance("codal_watch.lock")
    waited = 0
    while lock is None and (args.since or args.rewind) and waited < 3600:
        time.sleep(30)
        waited += 30
        lock = codal_monthly._single_instance("codal_watch.lock")
    if lock is None:
        print("watch: another poll is running")
        return 0

    if args.rewind:
        rewind(args.rewind, apply=True)
        return 0

    poll(since=args.since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
