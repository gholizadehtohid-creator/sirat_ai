"""Census of every instrument TSETMC's search will admit to -- dead ones included.

WHY THIS EXISTS
----------------
The instrument list this project fetches is TODAY'S list. A company delisted
in 1400 is absent exactly where its absence flatters a backtest, and no test
on the stored panel can detect that, because the rows were never fetched.
RESEARCH_OPTIONS.md Option 5 asked whether the past is even recoverable; the
2026-08-23 probe answered yes: TSETMC's search returns inactive instruments
(observed flag: `lastDate` 0 = inactive, 1 = active) and
`GetClosingPriceDailyList` still serves a delisted name's full history --
4,099 bars for آکنتور, three years after its last trade.

HOW ENUMERATION WORKS, AND WHY IT IS COMPLETE
-----------------------------------------------
There is no "list everything" endpoint. `GetInstrumentSearch/<text>` matches
<text> as a SUBSTRING of the symbol or name and returns at most 40 hits --
and a capped result hides the inactive names behind the active ones, so a
capped query is a question that was not answered.

So the sweep is breadth-first over query strings: every single letter first;
any query that returns exactly the cap is expanded with one more letter on
the right, and so on until nothing is capped. That right-extension is
COMPLETE under substring matching: any instrument containing the substring
c1..ck also contains its prefix c1..cj, so either some prefix's query came
back under the cap (and the instrument is already in that complete answer)
or every prefix was capped and the walk reached c1..ck itself.

WHAT IT DELIBERATELY DOES NOT TOUCH
-------------------------------------
`instruments` and `price_history`. Inserting dead names there would silently
change every panel later rebuilt from those tables -- the E21 flow panel and
the Alpha Core Tier-2 fixture both read `price_history` wholesale -- and
whether history should include the dead is a decision for a preregistered
experiment, not a side effect of acquisition. Everything lands in three
`census_*` tables that nothing else reads.

Coverage is a LOWER BOUND. The probe found «بیمه توسعه» absent from search
under every folding tried, so some fully-removed names may be purged
server-side. Absence from this census must never be read as never-listed.

USAGE
    python delisted_sweep.py                 discover, then fetch bars
    python delisted_sweep.py --discover-only
    python delisted_sweep.py --fetch-only
    python delisted_sweep.py --report
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import database
from providers.tsetmc_cdn import TsetmcCdnProvider

# The observed result cap. A query returning exactly this many hits is
# treated as truncated and expanded, never trusted.
SEARCH_CAP = 40

# One letter appended per expansion. آ is folded to ا by the server (probed),
# and the Arabic forms ك/ي match their Persian twins, so one alphabet is
# enough. Digits appear in symbols (خودرو4) but always beside letters, and a
# substring query of the letters alone finds those rows.
ALPHABET = tuple("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")

# Politeness gap between requests. TSETMC tolerates far more than codal, but
# this sweep shares the host with the app's own daily fetch.
REQUEST_DELAY = 0.35

HISTORY_BARS = 8000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def say(message):
    print(message, flush=True)


# ==============================
# SCHEMA
# ==============================

def ensure_tables():
    conn = database._connect()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS census_queries (
        query       TEXT PRIMARY KEY,
        hits        INTEGER,
        capped      INTEGER,
        searched_at TEXT,
        error       TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS census_instruments (
        ins_code           TEXT PRIMARY KEY,
        symbol             TEXT,
        name               TEXT,
        flow               INTEGER,
        flow_title         TEXT,
        active             INTEGER,
        in_todays_universe INTEGER,
        discovered_via     TEXT,
        first_seen         TEXT,
        bars               INTEGER,
        bar_first          TEXT,
        bar_last           TEXT,
        fetched_at         TEXT,
        fetch_error        TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS census_price_history (
        ins_code TEXT NOT NULL,
        date     TEXT NOT NULL,
        open     REAL,
        high     REAL,
        low      REAL,
        close    REAL,
        volume   REAL,
        value    REAL,
        trades   REAL,
        PRIMARY KEY (ins_code, date)
    )
    """)

    conn.commit()
    conn.close()


# ==============================
# DISCOVERY
# ==============================

def _pending_queries(conn):
    """Seed letters never searched, plus unexpanded children of capped ones."""

    done = {row[0] for row in
            conn.execute("SELECT query FROM census_queries "
                         "WHERE error IS NULL").fetchall()}

    capped = [row[0] for row in
              conn.execute("SELECT query FROM census_queries "
                           "WHERE capped = 1 AND error IS NULL").fetchall()]

    queue = [letter for letter in ALPHABET if letter not in done]

    for query in capped:
        queue.extend(query + letter for letter in ALPHABET
                     if (query + letter) not in done)

    # Failed queries are retried after everything new, oldest failure first.
    failed = [row[0] for row in
              conn.execute("SELECT query FROM census_queries "
                           "WHERE error IS NOT NULL "
                           "ORDER BY searched_at").fetchall()]

    queue.extend(q for q in failed if q not in queue)

    return queue


def _store_hits(conn, query, hits, universe):
    for hit in hits:

        code = str(hit.get("insCode") or "").strip()

        if not code:
            continue

        conn.execute("""
        INSERT INTO census_instruments
            (ins_code, symbol, name, flow, flow_title, active,
             in_todays_universe, discovered_via, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ins_code) DO UPDATE SET
            symbol = excluded.symbol,
            name = excluded.name,
            flow = excluded.flow,
            flow_title = excluded.flow_title,
            active = excluded.active,
            in_todays_universe = excluded.in_todays_universe
        """, (code, hit.get("lVal18AFC"), hit.get("lVal30"),
              hit.get("flow"), hit.get("flowTitle"),
              1 if hit.get("lastDate") else 0,
              1 if code in universe else 0,
              query, _now()))


def discover(provider=None, max_queries=None, delay=REQUEST_DELAY,
             verbose=True):
    """Walk the query tree until nothing is capped. Resumable, never raises."""

    ensure_tables()

    provider = provider or TsetmcCdnProvider()

    conn = database._connect()

    universe = {str(row[0]) for row in
                conn.execute("SELECT ins_code FROM instruments").fetchall()}

    searched = found_capped = failed = 0
    consecutive_failures = 0

    while True:

        queue = _pending_queries(conn)

        if not queue:
            break

        for query in queue:

            if max_queries and searched >= max_queries:
                conn.commit()
                conn.close()
                if verbose:
                    say("discover: stopping at the %d-query budget"
                        % max_queries)
                return {"searched": searched, "capped": found_capped,
                        "failed": failed, "finished": False}

            url = "%s/api/Instrument/GetInstrumentSearch/%s" % (
                provider.base_url, query)

            try:
                hits = provider._get(url).json().get("instrumentSearch") or []
                error = None
            except Exception as exc:                            # noqa: BLE001
                hits = []
                error = "%s: %s" % (type(exc).__name__, str(exc)[:140])

            searched += 1

            if error:
                failed += 1
                consecutive_failures += 1
            else:
                consecutive_failures = 0
                _store_hits(conn, query, hits, universe)

            capped = 1 if len(hits) >= SEARCH_CAP else 0
            found_capped += capped

            conn.execute("""
            INSERT INTO census_queries (query, hits, capped, searched_at, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (query) DO UPDATE SET
                hits = excluded.hits, capped = excluded.capped,
                searched_at = excluded.searched_at, error = excluded.error
            """, (query, len(hits), capped, _now(), error))

            conn.commit()

            if verbose and searched % 50 == 0:
                total, inactive = conn.execute(
                    "SELECT COUNT(*), SUM(active = 0) "
                    "FROM census_instruments").fetchone()
                say("  discover: %d queries, %d instruments (%d inactive)"
                    % (searched, total or 0, inactive or 0))

            if consecutive_failures >= 5:
                conn.close()
                if verbose:
                    say("discover: 5 consecutive failures -- the route or "
                        "the host is refusing; stopping rather than looping")
                return {"searched": searched, "capped": found_capped,
                        "failed": failed, "finished": False}

            time.sleep(delay)

    conn.close()

    if verbose:
        say("discover: complete -- %d queries this run, %d failed"
            % (searched, failed))

    return {"searched": searched, "capped": found_capped,
            "failed": failed, "finished": True}


# ==============================
# BARS FOR THE DEAD
# ==============================

def fetch(provider=None, limit=None, delay=REQUEST_DELAY, retry=False,
          verbose=True):
    """Full daily history for every inactive instrument, once each."""

    ensure_tables()

    provider = provider or TsetmcCdnProvider()

    conn = database._connect()

    where = "active = 0 AND fetched_at IS NULL"

    if not retry:
        where += " AND fetch_error IS NULL"

    rows = conn.execute(
        "SELECT ins_code, symbol FROM census_instruments "
        "WHERE %s ORDER BY ins_code" % where).fetchall()

    if limit:
        rows = rows[:int(limit)]

    if verbose:
        say("fetch: %d inactive instruments without bars" % len(rows))

    stored = failed = 0

    for number, (code, symbol) in enumerate(rows, start=1):

        result = provider.get_history(code, count=HISTORY_BARS)

        if not result.ok:
            failed += 1
            conn.execute(
                "UPDATE census_instruments SET fetch_error = ? "
                "WHERE ins_code = ?", (str(result.error)[:200], code))
            conn.commit()
            time.sleep(delay)
            continue

        frame = result.data

        conn.executemany("""
        INSERT OR REPLACE INTO census_price_history
            (ins_code, date, open, high, low, close, volume, value, trades)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(code, row.date.strftime("%Y-%m-%d"), row.open, row.high,
               row.low, row.close, row.volume, row.value, row.trades)
              for row in frame.itertuples()])

        conn.execute("""
        UPDATE census_instruments
        SET bars = ?, bar_first = ?, bar_last = ?, fetched_at = ?,
            fetch_error = NULL
        WHERE ins_code = ?
        """, (len(frame), frame["date"].min().strftime("%Y-%m-%d"),
              frame["date"].max().strftime("%Y-%m-%d"), _now(), code))

        conn.commit()

        stored += 1

        if verbose and number % 25 == 0:
            say("  fetch: %d/%d done (%d stored, %d failed)"
                % (number, len(rows), stored, failed))

        time.sleep(delay)

    conn.close()

    if verbose:
        say("fetch: %d histories stored, %d failed" % (stored, failed))

    return {"attempted": len(rows), "stored": stored, "failed": failed}


# ==============================
# REPORT
# ==============================

def report():
    ensure_tables()

    conn = database._connect()

    queries, capped, query_errors = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(capped), 0), "
        "COUNT(error) FROM census_queries").fetchone()

    unexpanded = conn.execute("""
        SELECT COUNT(*) FROM census_queries q
        WHERE q.capped = 1 AND q.error IS NULL
          AND EXISTS (SELECT 1 FROM census_queries LIMIT 1)
          AND (SELECT COUNT(*) FROM census_queries c
               WHERE c.query LIKE q.query || '_') < ?
    """, (len(ALPHABET),)).fetchone()[0]

    total, inactive, off_universe = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(active = 0), 0), "
        "COALESCE(SUM(active = 1 AND in_todays_universe = 0), 0) "
        "FROM census_instruments").fetchone()

    with_bars, bars = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(bars), 0) FROM census_instruments "
        "WHERE fetched_at IS NOT NULL").fetchone()

    span = conn.execute(
        "SELECT MIN(bar_first), MAX(bar_last) FROM census_instruments "
        "WHERE fetched_at IS NOT NULL").fetchone()

    by_last_year = conn.execute("""
        SELECT substr(bar_last, 1, 4) y, COUNT(*)
        FROM census_instruments
        WHERE active = 0 AND bar_last IS NOT NULL
        GROUP BY y ORDER BY y
    """).fetchall()

    conn.close()

    say("=" * 70)
    say("TSETMC INSTRUMENT CENSUS")
    say("=" * 70)
    say("  queries searched      %d  (%d capped, %d unexpanded, %d errors)"
        % (queries, capped, unexpanded, query_errors))
    say("  instruments seen      %d" % total)
    say("  inactive (delisted)   %d" % inactive)
    say("  active but NOT in today's instrument table: %d" % off_universe)
    say("  dead histories stored %d  (%d bars, %s .. %s)"
        % (with_bars, bars, span[0], span[1]))

    if by_last_year:
        say("")
        say("  inactive instruments by final trading year:")
        for year, count in by_last_year:
            say("     %s  %d" % (year, count))

    say("=" * 70)

    return {"queries": queries, "instruments": total, "inactive": inactive,
            "with_bars": with_bars}


# ==============================
# CLI
# ==============================

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])

    parser.add_argument("--discover-only", action="store_true",
                        dest="discover_only")
    parser.add_argument("--fetch-only", action="store_true", dest="fetch_only")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--max-queries", type=int, default=None,
                        dest="max_queries")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the histories fetched")
    parser.add_argument("--retry", action="store_true",
                        help="retry instruments whose history fetch failed")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY)
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.report:
        report()
        return 0

    verbose = not args.quiet

    provider = TsetmcCdnProvider()

    if not args.fetch_only:
        discover(provider, max_queries=args.max_queries, delay=args.delay,
                 verbose=verbose)

    if not args.discover_only:
        fetch(provider, limit=args.limit, delay=args.delay, retry=args.retry,
              verbose=verbose)

    if verbose:
        report()

    return 0


if __name__ == "__main__":
    sys.exit(main())
