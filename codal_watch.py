"""Watch Codal's market-wide feed and fetch new filings as they appear.

    python codal_watch.py                      one poll (scheduled every 2 hours)
    python codal_watch.py --since 1405/05/01   catch up: page back to a date
    python codal_watch.py --rewind 1405/08/04  re-sweep from a day (dry run;
                                               --apply saves it)
    python codal_watch.py --report             the marks, the sweep, the retry
                                               queue, dead letters and stalls

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

A poll that cannot be sure of what it read claims nothing and reads it
again next time, so what is left to watch for is a stall, and a stall is
made visible: a poll that does not advance is counted, and after
STALL_POLLS of them in a row the poll's log and --report say so, with the
reason (a feed that keeps refusing, or pages that keep coming back short --
as a page size other than PAGE_SIZE would make every one). A letter whose
handler keeps failing goes behind the others in watch_retry and, after
RETRY_LIMIT tries, is kept with its last error as a dead letter that
--report lists.

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
from datetime import datetime, timedelta, timezone

import codal
import codal_announcements
import config
import database
import fundamental_db
import jalali
import persian
import settings

# Statements, monthly reports, and since 2026-11-06 the announcements a
# manual trader acts on (codal_announcements.KINDS).
FEEDS = ("ن-10", "ن-30", "ن-31") + codal_announcements.CODES
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
# How far past a mark a pass reads before it takes the mark as reached. The
# feed is not strictly in PublishDateTime order -- the 11-06 catch-up log
# shows inversions of 4 to 30 seconds -- so a letter stamped just after the
# mark can sit below letters stamped before it, on a page that a pass
# stopping at the first stamp at or before the mark never read (the review,
# 2026-11-11). Letters already seen cost nothing but their share of a page:
# about one page more, at most, outside a deadline-day burst.
MARK_MARGIN_SECONDS = 120
# A letter whose fetch keeps failing -- transiently, or by raising -- is
# retried from watch_retry, behind the others, up to this many tries in all
# (about 16 hours of two-hourly polls). Then it is given up: marked seen with
# that outcome and kept, with its last error, in the watch state's dead
# letters for --report. ن-10 and ن-30 still have the backfill and
# codal_monthly behind them.
RETRY_PER_POLL = 30
RETRY_LIMIT = 8
# Polls in a row that did not advance before the watch calls itself stalled.
STALL_POLLS = 3


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_table():
    fundamental_db.create_database()
    conn = fundamental_db._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_seen (
        tracing_no TEXT PRIMARY KEY, letter_code TEXT, ins_code TEXT,
        published_at TEXT, outcome TEXT, seen_at TEXT)""")
    # The complete-through mark and an unfinished sweep (scan), the largest
    # feed Total seen, the stall count and the dead letters (notes); and the
    # letters whose fetch failed, kept whole so a later poll retries them
    # without paging back to find them.
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_state (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_retry (
        tracing_no TEXT PRIMARY KEY, letter TEXT, attempts INTEGER,
        last_tried TEXT, error TEXT)""")
    # A queue made before 2026-11-11 has no error column.
    if "error" not in {r[1] for r in conn.execute(
            "PRAGMA table_info(watch_retry)")}:
        conn.execute("ALTER TABLE watch_retry ADD COLUMN error TEXT")
    conn.commit()
    conn.close()


def _values():
    """watch_state as {key: value}; {} before the table exists."""

    conn = fundamental_db._connect()
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM watch_state").fetchall())
    except sqlite3.OperationalError:
        rows = {}
    conn.close()
    return rows


def _save_values(values):
    conn = fundamental_db._connect()
    now = _now()
    conn.executemany("INSERT OR REPLACE INTO watch_state VALUES (?, ?, ?)",
                     [(key, value, now) for key, value in values.items()])
    conn.commit()
    conn.close()


def _json(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


# A stamp as the feed carries it once its digits are folded. Anything else
# -- missing, empty, another shape -- is undated here: the letter is still
# taken and handled, but it never moves a mark, ends a pass or places a
# floor, because the marks are compared as strings and only this shape
# orders correctly as one.
_STAMP = re.compile(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}")


def _dated(stamp):
    return isinstance(stamp, str) and bool(_STAMP.fullmatch(stamp))


def load_state():
    """{'through': stamp or None, 'sweep': dict or None}; see scan().

    With nothing saved yet the mark starts at the newest letter seen, which
    is where the first-seen stop would have stopped -- the gap that rule
    left below it is for rewind(), not for a silent default.
    """

    rows = _values()
    if "through" not in rows:
        conn = fundamental_db._connect()
        try:
            stamps = [r[0] for r in conn.execute(
                "SELECT DISTINCT published_at FROM watch_seen")]
        except sqlite3.OperationalError:
            stamps = []
        conn.close()
        rows["through"] = _later(*(s for s in stamps if _dated(s)))

    sweep = _json(rows.get("sweep"), {}) or None
    return {"through": rows.get("through") or None, "sweep": sweep}


def save_state(state):
    _save_values({
        "through": state.get("through") or "",
        "sweep": json.dumps(state["sweep"], ensure_ascii=False)
        if state.get("sweep") else "",
    })


def notes():
    """What the watch keeps beside its marks.

      total  the largest Total the feed has answered with, or None. The
             market-wide feed only grows, so it is a floor for every later
             answer (_read).
      stall  {'polls', 'since', 'reason'} while polls in a row have not
             advanced (scan), else {}.
      dead   letters given up after RETRY_LIMIT tries, oldest first:
             {'tracing_no', 'attempts', 'error', 'at', 'letter'}.
    """

    rows = _values()
    try:
        total = int(rows.get("total") or "")
    except ValueError:
        total = None
    return {"total": total, "stall": _json(rows.get("stall"), {}),
            "dead": _json(rows.get("dead"), [])}


def _queued():
    """{tracing_no: (letter, attempts)} waiting for a retry, least recently
    tried first -- so a letter that failed waits behind the others."""

    conn = fundamental_db._connect()
    try:
        rows = conn.execute("SELECT tracing_no, letter, attempts FROM watch_retry"
                            " ORDER BY last_tried, rowid").fetchall()
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


def _queue(letter, attempts, error=None):
    # Microseconds: letters queued within one second must still keep the
    # order they failed in, or a failed one could sort ahead of the rest.
    tried = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    conn = fundamental_db._connect()
    conn.execute("INSERT OR REPLACE INTO watch_retry (tracing_no, letter, "
                 "attempts, last_tried, error) VALUES (?, ?, ?, ?, ?)",
                 (letter["tracing_no"], json.dumps(letter, ensure_ascii=False),
                  attempts, tried, error))
    conn.commit()
    conn.close()


def _unqueue(tracing_no):
    conn = fundamental_db._connect()
    conn.execute("DELETE FROM watch_retry WHERE tracing_no = ?", (tracing_no,))
    conn.commit()
    conn.close()


def _bury(letter, attempts, error):
    """Keep a letter given up in the dead letters, with its last error."""

    dead = notes()["dead"]
    dead.append({"tracing_no": letter["tracing_no"], "attempts": attempts,
                 "error": (error or "")[:200], "at": _now(), "letter": letter})
    _save_values({"dead": json.dumps(dead, ensure_ascii=False)})


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
    """One page of the whole market's feed, every letter type: (letters, total).

    `total` is the answer's Total, the number of letters in the whole feed,
    or None if it did not say -- what tells the feed's real end from an
    answer that came back empty or cut short (_read).

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
    letters = [dict(codal._letter_row(raw, raw.get("Symbol") or ""),
                    symbol=raw.get("Symbol") or "")
               for raw in payload.get("Letters") or []]
    try:
        total = int(payload.get("Total"))
    except (TypeError, ValueError):
        total = None
    return letters, (total if total is not None and total >= 0 else None)


def collect(client, seen, since=None, pace=None, codes=FEEDS):
    """New letters of the watched types, newest first, stopping at known ground.

    Without `since`, paging stops at the first page that holds a watched
    letter already seen; pages of other letter types alone do not stop it.
    With `since` (a Jalali date), paging continues until letters are older
    than that date -- the catch-up mode. A routine poll uses scan() instead.
    """

    return _collect(client, seen, since, pace, codes)[0]


def _collect(client, seen, since=None, pace=None, codes=FEEDS, feed=None):
    """collect(), and the newest stamp read if paging ended where it should
    -- known ground, `since`, or the end of the feed -- rather than on a
    failed page or the page cap (None then)."""

    fresh = []
    page = 1
    limit = None if since else MAX_PAGES_POLL
    newest, complete = None, False
    feed = {} if feed is None else feed

    while limit is None or page <= limit:
        letters = _read(client, page, pace, feed)
        if letters is None:
            break                       # a failed page claims nothing
        if not letters:
            complete = True             # the end of the feed, by its Total
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

    if complete and _cut_short(feed) is not None:
        complete = False
    return fresh, (newest if complete else None)


def _catch_up(client, seen, since, pace, feed=None):
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

    fresh, newest = _collect(client, seen, since=since, pace=pace, feed=feed)

    if newest and state["through"] == mark:
        return fresh, {"through": _later(mark, newest), "sweep": None}
    return fresh, None


def _read(client, page, pace, feed=None):
    """One page of the feed, whole: its letters, [] past the feed's end, or
    None if the read failed.

    A page holding fewer than PAGE_SIZE letters is the end of the feed only
    where the answer's Total puts the end; anywhere else an empty or short
    page is a failed read, and it claims nothing. Found 2026-11-11 by the
    review: an empty page was taken for the end of the feed, and a short
    one for a whole page, so an HTTP-200 answer that came back empty or cut
    short -- which looks exactly like the end -- moved the complete-through
    mark to the top and skipped the rest for good: with 3% such answers a
    simulated recovery sweep lost 127-492 of ~2,450 letters, from the
    bottom of its hole up, while the log said "complete through". The real
    end is thousands of pages back and is never reached in practice.

    Which Total places the end (found by the second review, 2026-11-11):
      - none that the answer itself contradicts: fewer letters in the feed
        than this page and the pages before it hold is no Total at all.
        Five letters with "Total: 0" on page 1 had passed for a whole page
        (nothing read before it to say otherwise), the next page was read
        as contiguous, and the 15 letters between were never read;
      - the largest one known: read in this pass, or stored from earlier
        polls (feed['stored'], notes) -- the market-wide feed only grows,
        so a degraded answer whose small Total agrees with itself (five
        letters, "Total: 5") cannot move the end up to it either. Nor
        can it on the first poll, with nothing stored: the pages are judged
        again, before the marks move, by the largest Total the whole pass
        read (_cut_short).
    Without any Total, only a full page counts.

    And a Total below one read earlier in the same pass is an answer from
    an older copy of the feed -- a lagging replica, a cache, or a letter
    withdrawn meanwhile -- whose page holds letters from further down than
    its number says: the letters between it and the page before are
    skipped. It is a failed read too. In the second review's simulation a
    page served as it was one poll earlier lost 8-341 letters; the second
    read of a pass's last page (_confirm) alone still left 3-29 lost to
    such pages in mid-pass. Only within a pass: a letter withdrawn since an
    earlier poll must not blind the watch until the feed outgrows the
    stored Total.

    `feed` is the pass's record of what the feed said: 'stored' (given),
    'read' (the largest Total read in this pass), 'whole' ({page: letters}
    accepted) and 'failed' (what ended the pass, if a read did).
    """

    feed = {} if feed is None else feed
    try:
        letters, total = _page(client, page)
    except Exception as exc:                                    # noqa: BLE001
        if pace:
            pace(codal.classify(exc) == codal.KIND_TRANSIENT)
        return _failed(feed, "feed page %d failed: %s" % (page, exc))
    if total is not None and total < (page - 1) * PAGE_SIZE + len(letters):
        total = None
    if total is not None and total < (feed.get("read") or 0):
        if pace:
            pace(True)
        return _failed(feed, "feed page %d failed: an older copy of the feed "
                             "(Total %d, below the %d read before it in this "
                             "pass)" % (page, total, feed["read"]))
    if total is not None:
        feed["read"] = max(total, feed.get("read") or 0)
    known = [t for t in (feed.get("read"), feed.get("stored")) if t is not None]
    total = max(known) if known else None
    due = PAGE_SIZE if total is None else max(
        0, min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE))
    if len(letters) < due:
        if pace:
            pace(True)
        return _failed(feed, "feed page %d failed: %d letters where %d were "
                             "due (Total %s)" % (page, len(letters), due, total))
    if pace:
        pace(False)
    feed.setdefault("whole", {})[page] = len(letters)
    return letters


def _failed(feed, message):
    print("watch: %s" % message, flush=True)
    feed["failed"] = message
    return None


def _cut_short(feed):
    """The first page this pass took as whole that the largest Total known
    now says was short, or None. A page 1 of five letters with "Total: 5"
    is whole when it is read; page 2's real Total says it was not."""

    known = [t for t in (feed.get("read"), feed.get("stored")) if t is not None]
    if not known:
        return None
    total = max(known)
    for number, count in sorted((feed.get("whole") or {}).items()):
        if count < max(0, min(PAGE_SIZE, total - (number - 1) * PAGE_SIZE)):
            _failed(feed, "feed page %d held %d letters, short of what the "
                          "Total read later in the pass (%d) puts there; the "
                          "marks stay this poll" % (number, count, total))
            return number
    return None


def _confirm(client, number, letters, pace, feed, take):
    """Read the page a pass ends on a second time, before the marks move on it.

    The page that joins new ground to ground already covered is the one a
    stale answer does the most harm on. An answer served from an older copy
    of the feed -- a lagging replica, a cache -- holds letters from further
    down than its page number says, so the letters between it and the page
    above are skipped; on the last page they are claimed with the rest when
    the mark moves. In the second review's simulation (2026-11-11) a page
    served as it was one poll earlier ended a sweep and claimed 8 to 341
    letters of the hole. _read refuses such an answer by its Total; this
    check holds without one.

    One more request, CATCH_UP_GAP_SECONDS later. It holds if the second
    answer is read whole, shares letters with the first and shows no
    watched letter the pass has not taken: letters published in between
    push the page down by letters the pass read above it, which is no
    difference. Otherwise the marks stay this poll, and the next one reads
    the page again; whatever the second answer showed is taken all the same.
    """

    time.sleep(CATCH_UP_GAP_SECONDS)
    again = _read(client, number, pace, feed)
    if again is None:
        return False
    first = {letter.get("tracing_no") for letter in letters}
    missed = take(again)
    if missed:
        why = "%d watched letters the first read had skipped" % missed
    elif not any(letter.get("tracing_no") in first for letter in again):
        why = "no letter in common with the first read"
    else:
        return True
    _failed(feed, "feed page %d read differently a second time (%s); the "
                  "marks stay this poll" % (number, why))
    return False


def _stamps(letters):
    return [l["published_at"] for l in letters if _dated(l.get("published_at"))]


def _later(*stamps):
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def _earliest(*stamps):
    stamps = [s for s in stamps if s]
    return min(stamps) if stamps else None


def _before(stamp, seconds):
    """`stamp` moved `seconds` earlier, in the same shape; an undated stamp
    is returned as it is."""

    moment = jalali.parse(stamp) if _dated(stamp) else None
    if moment is None:
        return stamp
    moment -= timedelta(seconds=seconds)
    day = jalali.today_jalali(moment.date())
    if not day:
        return stamp
    return "%04d/%02d/%02d %s" % (day + (moment.strftime("%H:%M:%S"),))


def scan(client, seen, state, pace=None, codes=FEEDS, sweep_pages=None,
         feed=None):
    """New letters of the watched types down to the mark, newest first.

    Returns (fresh, state). Save `state` only once every letter in `fresh`
    has been handled or queued for retry: an interrupted run then claims
    nothing it did not cover, and the next poll reads the same ground again.

      through  every watched letter published at or before it is handled.
               The top of the feed is read down to it, MARK_MARGIN_SECONDS
               past it (MAX_PAGES_POLL pages at most), and it moves to the
               newest letter only when the poll got all the way down.
      sweep    a backlog deeper than that: {top, top_at, floor, floor_at,
               floor_index}, the stretch from the top letter of the last
               poll down to its floor letter having been covered. The next
               poll reads the new letters above `top`, then jumps to the
               floor letter's page -- where it sits now, shifted by the
               letters published since -- and walks on toward `through`,
               sweep_pages a poll at the catch-up's pace.

    The jump re-reads one page at most, the floor's own, and finds the
    floor letter on it. It used to land a page earlier, re-reading two
    pages a poll where this said one (found 2026-11-11). A page without
    the floor letter on it (letters gone from the feed shift it) steps
    back a page -- where the old jump landed -- and on while a page is all
    older than the floor, so nothing between is skipped; a sweep saved
    before the floor letter was kept jumps there directly.

    Every unseen watched letter on a page read is taken, whatever its date:
    the marks decide how far to read, not what to keep. A page that fails
    (_read: an error, or an empty or short answer short of the feed's end)
    ends the reading where it is, and the marks claim nothing past the
    pages read whole before it. The page a pass ends on below page 1 is
    read twice before the marks move on it (_confirm), and no mark moves
    if a page taken as whole turns out short by a Total read after it
    (_cut_short). An undated letter is taken, but moves no mark: it
    neither ends a pass nor places a floor.

    `feed` is the pass's record (_read); scan adds 'advanced', whether the
    pass moved the marks or covered new ground -- what poll() counts stalls
    by.
    """

    feed = {} if feed is None else feed
    fresh, after = _scan(client, seen, state, pace, codes, sweep_pages, feed)
    if after != state and _cut_short(feed) is not None:
        feed["advanced"] = False
        return fresh, state
    return fresh, after


def _scan(client, seen, state, pace, codes, sweep_pages, feed):
    through, sweep = state.get("through"), state.get("sweep")
    sweep_pages = SWEEP_PAGES if sweep_pages is None else sweep_pages
    feed["advanced"] = False
    fresh, taken = [], set()

    def take(letters):
        found = 0
        for letter in letters:
            number = letter.get("tracing_no")
            if (letter.get("letter_code") in codes and number
                    and number not in seen and number not in taken):
                taken.add(number)
                fresh.append(letter)
                found += 1
        return found

    # ---- the top of the feed, down to ground already covered ------------
    ground = sweep["top_at"] if sweep else through
    stop = _before(ground, MARK_MARGIN_SECONDS)
    above, reached, page, letters = [], False, 0, None
    while page < MAX_PAGES_POLL:
        letters = _read(client, page + 1, pace, feed)
        if letters is None:
            # The marks stay as they were, a sweep under way included: the
            # letters read above the failed page are taken (and handled and
            # marked seen), so the next poll reads the top again for the
            # price of a few pages. This used to start a fresh sweep from
            # here, which threw away the old one's progress and re-read its
            # whole range (found 2026-11-11).
            return fresh, state
        page += 1
        if not letters:
            reached = True                  # the end of the feed, by its Total
            break
        above.extend(letters)
        take(letters)
        stamps = _stamps(letters)
        if ground is None or (stamps and min(stamps) <= stop):
            reached = True
            break

    if not above:
        feed["advanced"] = True
        return fresh, state                         # an empty feed: no news

    # Page 1 joins nothing: an old copy of it is only old news, read again
    # next poll. Any later page that ends the read joins the new letters
    # to covered ground.
    if reached and page > 1 and letters and not _confirm(
            client, page, letters, pace, feed, take):
        return fresh, state

    stamps = _stamps(above)
    top = {"top": above[0].get("tracing_no"),
           "top_at": _later(*stamps) or ground}

    if not reached:
        # The new letters alone outran the page cap (about 17 hours of
        # feed): what was read is covered and the rest, down to the mark,
        # is a backlog. A sweep already under way restarts from here (its
        # stretch is now cut off from this one): pages are re-read, nothing
        # is skipped.
        feed["advanced"] = True
        return fresh, {"through": through, "sweep": dict(
            top, floor=above[-1].get("tracing_no"),
            floor_at=_earliest(*stamps) or top["top_at"],
            floor_index=len(above) - 1)}

    if not sweep:
        feed["advanced"] = True
        return fresh, {"through": _later(through, top["top_at"]),
                       "sweep": None}

    # ---- the sweep, from where the last poll stopped ----------------------
    shift = next((i for i, l in enumerate(above)
                  if l.get("tracing_no") == sweep.get("top")), None)
    if shift is None:
        shift = sum(1 for l in above
                    if (l.get("published_at") or "") > (sweep["top_at"] or ""))
    floor, floor_at = sweep.get("floor") or None, sweep.get("floor_at")
    start = floor_index = sweep["floor_index"] + shift
    # The floor letter's own page; without a floor letter to find there (a
    # sweep saved before 2026-11-11), the page above it, as it always was.
    number = max(page + 1, floor_index // PAGE_SIZE + (1 if floor else 0))
    checked = number == page + 1                    # contiguous with the top
    stepped, done, requests = not floor, False, 0
    stop = _before(through, MARK_MARGIN_SECONDS)

    while requests < sweep_pages:
        time.sleep(CATCH_UP_GAP_SECONDS)
        letters = _read(client, number, pace, feed)
        requests += 1
        if letters is None:
            break                   # the floor stays at the last whole page
        stamps = _stamps(letters)
        if not checked:
            anchored = bool(floor) and any(
                l.get("tracing_no") == floor for l in letters)
            if number > page + 1 and not anchored and (
                    not stepped or not letters or (
                        stamps and floor_at and max(stamps) < floor_at)):
                number -= 1
                stepped = True
                continue
            checked = True
        take(letters)
        if not letters or through is None or (stamps and min(stamps) <= stop):
            # The mark (or the feed's end, by its Total) is reached -- on a
            # page that must hold before the mark moves over it.
            done = not letters or _confirm(client, number, letters, pace,
                                           feed, take)
            break
        floor = letters[-1].get("tracing_no")
        floor_at = _earliest(floor_at, *stamps)
        floor_index = (number - 1) * PAGE_SIZE + len(letters) - 1
        number += 1

    if done:
        feed["advanced"] = True
        return fresh, {"through": _later(through, top["top_at"]),
                       "sweep": None}
    feed["advanced"] = floor_index > start
    return fresh, {"through": through, "sweep": dict(
        top, floor=floor, floor_at=floor_at, floor_index=floor_index)}


class Transient(Exception):
    """A handler's "try again next poll", with Codal's reason for the queue."""


class NoRequest(str):
    """A handler's outcome reached without a request to Codal.

    _process does not pace it and reports its refusal as None, as for a
    letter outside the universe: an "already stored" between two refusals
    reset both refusal counts, and a poll in a throttle window went on
    sending downloads into it (the portfolio review, 2026-11-11).
    """


def handle_statement(client, letter, inst):
    import codal_sync

    if str(letter["tracing_no"]) in database.codal_filing_tracing_numbers():
        return NoRequest("already stored")
    if not letter.get("excel_url"):
        return NoRequest("no excel")

    record, error, kind = codal_sync.parse_letter(client, letter)
    if error:
        if kind == codal.KIND_TRANSIENT:
            raise Transient(error)          # _process queues it, with why
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
        return NoRequest("already stored")
    if not letter.get("excel_url"):
        return NoRequest("no excel")

    got = client.fetch_sheets(letter["excel_url"], archive_key=letter["tracing_no"])
    code = str(inst["ins_code"])
    if not got.ok:
        if got.transient:
            raise Transient(got.error or "transient")
        codal_monthly._fail(code, letter, got.error or "unreadable")
        return "unreadable"
    record = monthly_reports.parse(got.data, letter["title"])
    if not record:
        codal_monthly._fail(code, letter, "no sales, purchase or income table")
        return "unreadable"
    codal_monthly.store(code, letter, record)
    codal_monthly._status(code, letters=None)
    return "stored"


def handle_portfolio(client, letter, inst):
    """An investment company's monthly portfolio report (ن-۳۱) -> NAV.

    Not watched until 2026-11-11, when 130 of 141 stored portfolios were
    still Tir's while the index had risen 72.5% (codal_portfolio.py says
    how). Only a report newer than the one held is downloaded; a company
    filing its sales report under this code parses to no portfolio and is
    marked so, not retried.
    """

    import codal_portfolio

    code = str(inst["ins_code"])
    if not letter.get("excel_url"):
        return NoRequest("no excel")
    if str(letter["tracing_no"]) in database.known_tracing_numbers(code):
        return NoRequest("already stored")
    got = codal_portfolio.fetch_newest(client, code, [letter],
                                       newest_held=codal_portfolio.held_period(code))
    if got["transient"]:
        raise Transient("portfolio download refused")
    if not got["requested"]:
        return NoRequest("not newer than the portfolio held")
    if got["stored"]:
        return "stored"
    if got["error"]:
        return "unreadable: %s" % got["error"][:80]
    return got["outcome"]


HANDLERS = {"ن-10": handle_statement, "ن-30": handle_monthly,
            "ن-31": handle_portfolio}
for _code in codal_announcements.CODES:
    HANDLERS[_code] = codal_announcements.handle

# Handlers that put a request on Codal for every letter, and so are paced.
# An announcement is a row from the feed itself, except an annual AGM's
# decisions, whose page is fetched for the dividend.
NETWORK_CODES = ("ن-10", "ن-30", "ن-31", persian.normalize(
    codal_announcements.ANNUAL_AGM_DECISIONS))


def _process(client, letter, universe, pace, totals, verbose, attempts=0,
             defer=False):
    """Hand one letter to its handler; mark it seen, or queue it for retry.

    Returns (outcome, refused): the outcome, None when the letter was
    queued; and whether the try failed transiently -- a refusal, what the
    stop rules in poll() count. With `defer` the letter is queued untried,
    its tries unchanged.

    A handler that raises is a failed try like a transient one. Until
    2026-11-11 the exception left the poll and the letter stayed at the
    head of watch_retry -- tried first, so it failed first -- and every
    later poll died on it before reading the feed: one poison letter
    stopped the watch (HTTP-500 filings at the head of the backfill's
    queue once stalled its campaign the same way). A letter that fails goes
    behind the others, and after RETRY_LIMIT tries it is given up: marked
    seen with that outcome and kept, with its last error, in the dead
    letters --report lists. Nothing is dropped silently.
    """

    inst = universe.get(persian.normalize(letter.get("symbol") or ""))
    if not inst:
        _mark(letter, None, "outside universe")
        _unqueue(letter["tracing_no"])
        totals["outside_universe"] += 1
        # None, not False: no request was made, so this letter says nothing
        # about whether Codal is refusing. False would reset the callers'
        # refusal count while the Pacer's own count stood, and refused,
        # outside, refused, outside, refused slept 15+30+60.. minutes inside a
        # poll holding the watch lock (2026-11-11: 751 of ~1,712 ن-10 letters
        # in watch_seen are outside the universe, so that order is common).
        return "outside universe", None

    if defer:
        _queue(letter, attempts, "not tried: Codal refused the letters "
                                 "before it in the same poll")
        totals["retry_later"] += 1
        return None, False

    networked = letter["letter_code"] in NETWORK_CODES
    error = None
    try:
        if letter["letter_code"] in ("ن-10", "ن-30", "ن-31"):
            # codal_letters is the statement/monthly/portfolio ledger the
            # per-company paths read; announcements live in their own table.
            database.save_codal_letters(str(inst["ins_code"]), [letter])
        outcome = HANDLERS[letter["letter_code"]](client, letter, inst)
        refused = outcome is None
        if refused:
            error = "transient failure"
    except Transient as exc:
        outcome, refused, error = None, True, str(exc)[:200]
    except Exception as exc:                                    # noqa: BLE001
        outcome = None
        refused = codal.classify(exc) == codal.KIND_TRANSIENT
        error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    if isinstance(outcome, NoRequest):
        # No request, so nothing to pace and nothing said about refusals.
        _mark(letter, str(inst["ins_code"]), str(outcome))
        _unqueue(letter["tracing_no"])
        return str(outcome), None
    if networked:
        pace(refused)

    if outcome is None:
        attempts += 1
        if attempts < RETRY_LIMIT:
            _queue(letter, attempts, error)
            totals["retry_later"] += 1
            return None, refused
        outcome = "gave up after %d %sfailures" % (
            attempts, "transient " if refused else "")
        _bury(letter, attempts, error)
        totals["given_up"] += 1
        if verbose:
            print("  ! %s %s: %s -- %s" % (letter.get("symbol"),
                                           letter.get("tracing_no"),
                                           outcome, error), flush=True)
    _mark(letter, str(inst["ins_code"]), outcome)
    _unqueue(letter["tracing_no"])
    if outcome.startswith("stored"):
        totals["stored"] += 1
        if verbose:
            print("  + %s %s" % (letter.get("symbol"),
                                 (letter.get("title") or "")[:70]), flush=True)
    elif outcome.startswith("unreadable"):
        totals["unreadable"] += 1
    return outcome, False


def _newest_portfolio_per_company(fresh, universe, totals):
    """`fresh` with only each company's newest ن-31 letter kept.

    Newest by the period in the title, then by publication, so a
    same-period correction wins over its original. The rest are saved to
    codal_letters (the per-company paths still see them) and marked seen.
    """

    import financials

    best = {}
    for letter in fresh:
        if letter["letter_code"] != "ن-31":
            continue
        key = persian.normalize(letter.get("symbol") or "")
        rank = (str(financials.period_end(letter.get("title") or "") or ""),
                str(letter.get("published_at") or ""))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, letter["tracing_no"])
    keep = {tracing for _rank, tracing in best.values()}
    out = []
    for letter in fresh:
        if letter["letter_code"] != "ن-31" or letter["tracing_no"] in keep:
            out.append(letter)
            continue
        inst = universe.get(persian.normalize(letter.get("symbol") or ""))
        code = str(inst["ins_code"]) if inst else None
        if code:
            database.save_codal_letters(code, [letter])
        _mark(letter, code, "superseded by a newer portfolio in this poll")
        _unqueue(letter["tracing_no"])
        totals["new"] += 1
    return out


def poll(since=None, verbose=True, client=None):
    import codal_monthly

    _ensure_table()
    client = client or codal.CodalClient()
    pace = codal_monthly.Pacer("watch", verbose)
    universe = _universe()
    totals = {"new": 0, "stored": 0, "outside_universe": 0, "unreadable": 0,
              "retry_later": 0, "retried": 0, "given_up": 0}
    kept = notes()

    # What an earlier poll could not finish, from the queue: paging back to
    # find it again is what used to lose it. Two refusals in a row and the
    # rest wait for the next poll -- a third would set the Pacer sleeping
    # 15-240 minutes inside a poll the backfill campaign is waiting on.
    refused = 0
    for letter, attempts in list(_queued().values())[:RETRY_PER_POLL]:
        totals["retried"] += 1
        refusal = _process(client, letter, universe, pace, totals, verbose,
                           attempts)[1]
        if refusal:
            refused += 1
            if refused >= 2:
                break
        elif refusal is not None and letter["letter_code"] in NETWORK_CODES:
            refused = 0             # only a request that got through resets
    # ...and the Pacer forgets them. It is shared with the scan, so a pass
    # that ended on two refusals left it one short of its 15-minute wait,
    # and a refused feed page 1 (18 in the watch's log by 2026-11-11, every
    # one a timeout or a 429) set it sleeping inside the poll after all.
    # Its batch count stands: that paces the requests, not the refusals.
    pace.refused = 0

    # A queued letter is not new: it waits its turn rather than costing a
    # second request in the same poll.
    seen = _seen() | set(_queued())
    # Every letter in watch_seen came from this feed, so their count is a
    # floor for its Total even before one is stored. Without it the first
    # poll after a state loss (the cold start) trusted a degraded backend's
    # small, self-consistent Total and could claim the whole hole: _cut_short
    # only helps when a later page in the same pass carries the real Total.
    feed = {"stored": max(kept["total"] or 0, len(_seen())) or None}
    if since:
        fresh, state = _catch_up(client, seen, since, pace, feed)
    else:
        fresh, state = scan(client, seen, load_state(), pace=pace, feed=feed)
    known = [t for t in (feed.get("read"), kept["total"]) if t is not None]
    largest = max(known) if known else None
    if verbose:
        for letter_code in ("ن-10", "ن-30", "ن-31"):
            print("watch: %s -- %d new letters" % (letter_code, sum(
                1 for l in fresh if l["letter_code"] == letter_code)), flush=True)
        print("watch: announcements -- %d new letters" % sum(
            1 for l in fresh if l["letter_code"] in codal_announcements.CODES),
            flush=True)
        # Once a poll, so a feed that stops saying how long it is -- or
        # says something else -- shows in the log before it shows as a
        # stall (2026-11-11).
        print("watch: feed Total %s (largest seen %s)" % (
            feed.get("read") if feed.get("read") is not None
            else "none read this poll", largest), flush=True)

    # Oldest first, so an interrupted catch-up has stored a contiguous
    # stretch rather than a scatter. The retry pass's rule holds here too:
    # two networked letters refused in a row and the rest are queued untried
    # for the next poll, so the Pacer never waits 15-240 minutes inside a
    # poll the campaign holds the watch lock for -- six refused new letters
    # slept 3 h 45 min (the second review, 2026-11-11), a sleep that does
    # not even run on across a laptop's suspend. Letters that need no
    # request go on being handled.
    # One portfolio report per company per poll: NAV wants the newest, and a
    # --since catch-up oldest first downloaded every intermediate month (05,
    # 06, 07 for each of ~135 holders: ~400 downloads where ~135 do -- the
    # portfolio review, 2026-11-11). The others are listed in the ledger and
    # marked seen, unread.
    fresh = _newest_portfolio_per_company(fresh, universe, totals)

    pace.refused = 0
    refused = 0
    for letter in reversed(fresh):
        totals["new"] += 1
        networked = letter["letter_code"] in NETWORK_CODES
        if networked and refused >= 2:
            _process(client, letter, universe, pace, totals, verbose,
                     defer=True)
            continue
        refusal = _process(client, letter, universe, pace, totals, verbose)[1]
        if networked and refusal is not None:
            refused = refused + 1 if refusal else 0

    # Only now: every letter the marks claim is handled or queued.
    if state is not None:
        save_state(state)

    values = {}
    if largest is not None and largest != kept["total"]:
        values["total"] = str(largest)
    stall = kept["stall"]
    if not since:
        # A routine poll that did not advance: the reason is kept, and a
        # third in a row is called a stall (the poll's log and --report).
        stall = {} if feed.get("advanced") else {
            "polls": int(stall.get("polls") or 0) + 1,
            "since": stall.get("since") or _now(),
            "reason": feed.get("failed") or "the sweep's floor did not move"}
        if stall != kept["stall"]:
            values["stall"] = json.dumps(stall, ensure_ascii=False) \
                if stall else ""
    if values:
        _save_values(values)

    if totals["stored"]:
        fundamental_db.sync_from_market_db()

    if verbose:
        print("watch: %(new)d new, %(stored)d stored, %(outside_universe)d "
              "outside the universe, %(unreadable)d unreadable, "
              "%(retry_later)d left for the next poll (%(retried)d retried), "
              "%(given_up)d given up" % totals, flush=True)
        if state is not None:
            print("watch: complete through %s%s" % (
                state.get("through"),
                "; sweeping back, reached %s" % state["sweep"]["floor_at"]
                if state.get("sweep") else ""), flush=True)
        if int(stall.get("polls") or 0) >= STALL_POLLS:
            print("watch: STALLED -- %d polls in a row have not advanced, "
                  "since %s: %s" % (stall["polls"], stall.get("since"),
                                    stall.get("reason")), flush=True)
    return totals


def rewind(day, apply=False, verbose=True):
    """Move the complete-through mark back to the start of `day` (Jalali).

    The repair for the ground the first-seen stop lost: the routine polls
    then sweep back over it, SWEEP_PAGES a poll, and take every watched
    letter there they have not seen; a seen one costs only its share of a
    page. The mark only ever moves back. Dry run unless `apply`; idempotent.

    Nothing is saved unless the mark really moves back. It used to be saved
    with the sweep cleared every time, so running the same --apply again
    during the recovery -- or naming a later day -- threw the sweep's
    progress away and the next ~20 polls read the same ~400 pages again
    (found 2026-11-11). A sweep under way is kept when the mark does move:
    everything between its top and its floor is still covered, and it
    carries on down to the new mark.
    """

    if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", day or ""):
        raise ValueError("rewind wants a Jalali day like 1405/08/04, got %r"
                         % (day,))

    state = load_state()
    mark = "%s 00:00:00" % day
    changed = state["through"] is None or mark < state["through"]
    if not changed:
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
        if changed:
            print("watch: complete through %s -> %s%s" % (
                state["through"], mark, "" if apply else
                "  (dry run, nothing written; --apply saves it)"), flush=True)
        else:
            print("watch: complete through %s, at or before %s already -- "
                  "nothing changes" % (state["through"], day), flush=True)
        if state.get("sweep"):
            print("  the sweep under way (reached %s) is kept%s"
                  % (state["sweep"].get("floor_at"),
                     ", and carries on down to the new mark" if changed
                     else ""), flush=True)
        if pages is not None and changed:
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

    if apply and changed:
        _ensure_table()
        save_state({"through": mark, "sweep": state.get("sweep")})

    return {"through": mark, "previous": state["through"], "days": days,
            "pages": pages, "unseen": len(unseen), "unstored": len(unstored),
            "changed": changed, "applied": bool(apply and changed)}


def _days_between(first):
    """Jalali days from `first` up to today, as 'YYYY/MM/DD' strings."""

    from datetime import date

    day = date(*jalali.to_gregorian(*first.split("/")))
    out = []
    while day <= date.today():
        out.append("%04d/%02d/%02d" % jalali.today_jalali(day))
        day += timedelta(days=1)
    return out


def report():
    """Where the watch stands. It only reads: nothing is created or saved."""

    conn = fundamental_db._connect()
    try:
        rows = conn.execute("""SELECT letter_code, outcome, COUNT(*), MAX(seen_at)
                               FROM watch_seen GROUP BY 1, 2
                               ORDER BY 1, 3 DESC""").fetchall()
    except sqlite3.OperationalError:
        rows = []
    queued, head = 0, []
    try:
        queued = conn.execute("SELECT COUNT(*) FROM watch_retry").fetchone()[0]
        # A queue no poll has migrated yet has no error column (_ensure_table).
        columns = {r[1] for r in conn.execute("PRAGMA table_info(watch_retry)")}
        head = conn.execute(
            "SELECT tracing_no, attempts, last_tried, %s FROM watch_retry "
            "ORDER BY last_tried, rowid LIMIT 5"
            % ("error" if "error" in columns else "NULL")).fetchall()
    except sqlite3.OperationalError:
        pass
    conn.close()
    for row in rows:
        print("  %s  %-40s %5d  last %s" % row)

    state, kept = load_state(), notes()
    print("  complete through %s" % state["through"])
    sweep = state.get("sweep")
    if sweep:
        print("  sweeping back: covered from %s (top letter %s) down to %s "
              "(floor letter %s, about page %d as of the last poll)" % (
                  sweep.get("top_at"), sweep.get("top"), sweep.get("floor_at"),
                  sweep.get("floor") or "?",
                  int(sweep.get("floor_index") or 0) // PAGE_SIZE + 1))
    else:
        print("  no sweep under way")
    print("  largest feed Total seen: %s" % (
        kept["total"] if kept["total"] is not None else "none yet"))
    print("  %d letters waiting for a retry%s" % (
        queued, ", least recently tried first:" if head else ""))
    for number, attempts, tried, error in head:
        print("    %s  %s tries, last %s  %s" % (number, attempts, tried,
                                                 (error or "")[:80]))
    if kept["dead"]:
        print("  %d dead letters, given up after %d tries (still in "
              "watch_seen as 'gave up ...'):" % (len(kept["dead"]),
                                                 RETRY_LIMIT))
        for entry in kept["dead"][-20:]:
            letter = entry.get("letter") or {}
            print("    %s %s %s %s  %s tries, last error: %s" % (
                entry.get("tracing_no"), letter.get("letter_code"),
                letter.get("symbol"), letter.get("published_at"),
                entry.get("attempts"), entry.get("error")))
    else:
        print("  no dead letters")
    stall = kept["stall"]
    if int(stall.get("polls") or 0) >= STALL_POLLS:
        print("  STALLED: %d polls in a row have not advanced, since %s. "
              "Last reason: %s" % (stall["polls"], stall.get("since"),
                                   stall.get("reason")))
    elif stall:
        print("  the last %d poll(s) did not advance: %s" % (
            int(stall.get("polls") or 0), stall.get("reason")))


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
