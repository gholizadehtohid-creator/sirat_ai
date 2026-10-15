"""Did the market trade? -- asked of the data, never of the calendar.

WHY THIS EXISTS
---------------
Between 2026-02-28 and 2026-05-18 the Tehran equity market traded nothing at
all: fifty consecutive sessions, eleven weeks. TSETMC published a complete bar
for every listed instrument on every one of those days, carrying the previous
close forward at zero volume, and froze both indices at a single value.

Nothing in this project noticed. `bootstrap._history_state()` asks
`price_history` for its newest bar, got one every day, and reported the data
current. The panel ranked names on frozen prices and sized positions in a
market where no position could be taken. MARKET_REGIME_AUDIT.md has the
measurements.

The lesson is narrow and it is the whole of this module:

    A BAR IS NOT EVIDENCE THAT A SESSION HAPPENED.

Three tables answer "did the market trade on date D" and only one of them
tells the truth:

    price_history        a full, plausible, fictional bar for every name
    index_history        a flat line no index ever printed
    client_type_history  silence -- حقیقی/حقوقی is published per session, so
                         no session means no rows at all

This module asks the two honest questions instead: did any equity record
turnover, and did the exchange publish a flow row. Either one answers it; both
agreeing is the normal case.

WHAT IT DELIBERATELY DOES NOT DO
---------------------------------
It holds no holiday calendar and no session clock. Iran's trading calendar
moves with the religious year, closures are announced rather than scheduled,
and the longest one on record here lasted eleven weeks -- none of which a
hardcoded table survives. Every answer here is an observation about rows that
exist, so a closure nobody announced is detected the same way as one that was.

It also never guesses forward. `is_trading_now()` reports on the data in hand;
a market that opens in five minutes is correctly reported as not trading.

USAGE
    python market_session.py              what the stored data says
    python market_session.py --since 2025-01-01
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import config

DB = config.DATABASE_NAME

# Sat..Wed. Kept only to describe which calendar days COULD have been a
# session; it never decides whether one was. bootstrap.py owns the same set
# for its own staleness arithmetic.
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}

# A session with fewer than this many equities showing turnover is not a
# session this project can screen on. The 2026-05-19 reopening traded 402 of
# 751 names and is unambiguously a session; the closed days traded exactly 0.
# There is no observed case anywhere between 0 and 402, so the floor is placed
# where it separates nothing from something rather than tuned to a boundary
# that has never been seen.
MIN_TRADED_NAMES = 1


def _connect(db=DB):
    return sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"),
                           uri=True)


def traded_counts(since=None, until=None, db=DB):
    """Equities with turnover, per calendar date that carries any bar at all.

    Returns [(date, listed, traded)] ordered by date. `listed` counts the bars
    TSETMC published; `traded` counts the ones that represent a trade. On a
    closed session the first is a full market and the second is zero, and the
    gap between them is the entire point of this module.
    """

    clauses = ["i.is_equity = 1"]
    params = []

    if since:
        clauses.append("p.date >= ?")
        params.append(since)

    if until:
        clauses.append("p.date <= ?")
        params.append(until)

    sql = """
        SELECT p.date,
               COUNT(*) AS listed,
               SUM(CASE WHEN p.volume > 0 THEN 1 ELSE 0 END) AS traded
        FROM price_history p
        JOIN instruments i ON i.ins_code = p.ins_code
        WHERE %s
        GROUP BY p.date
        ORDER BY p.date
    """ % " AND ".join(clauses)

    conn = _connect(db)

    try:
        return [(d, int(listed or 0), int(traded or 0))
                for d, listed, traded in conn.execute(sql, params)]
    finally:
        conn.close()


def flow_dates(since=None, until=None, db=DB):
    """Dates the exchange published حقیقی/حقوقی for -- the honest witness.

    Flow is computed from a session's completed trades, so a date with flow
    rows had a session. The converse is weaker: this project's own collector
    can be behind, so an absent date means "not observed here", never "no
    session". Used to corroborate turnover, never to overrule it.
    """

    clauses = []
    params = []

    if since:
        clauses.append("date >= ?")
        params.append(since)

    if until:
        clauses.append("date <= ?")
        params.append(until)

    sql = "SELECT DISTINCT date FROM client_type_history"

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    conn = _connect(db)

    try:
        return {row[0] for row in conn.execute(sql, params)}
    finally:
        conn.close()


def dead_dates(since=None, until=None, db=DB):
    """Calendar dates carrying bars that no equity actually traded on.

    This is the set every historical panel must drop from its calendar. A
    forward target that lands here reads a frozen bar and concludes the name
    was quietly available, which is the one direction that flatters a
    liquidity signal -- see MARKET_REGIME_AUDIT.md section 5.
    """

    return {date for date, _listed, traded in traded_counts(since, until, db)
            if traded < MIN_TRADED_NAMES}


def live_calendar(since=None, until=None, db=DB):
    """The sessions that happened, in order. The calendar panels should use."""

    return [date for date, _listed, traded in traded_counts(since, until, db)
            if traded >= MIN_TRADED_NAMES]


def closures(since=None, until=None, db=DB):
    """Dead dates collapsed into runs: [(first, last, sessions)].

    A run rather than a list because the distinction that matters
    operationally is between a holiday and a shutdown, and only the length
    tells them apart. Every retry and cool-off policy in this project is
    tuned in hours; the run that actually happened was 1,800 of them long.
    """

    runs = []
    current = None

    for date, _listed, traded in traded_counts(since, until, db):
        if traded < MIN_TRADED_NAMES:
            if current is None:
                current = [date, date, 1]
            else:
                current[1] = date
                current[2] += 1
        elif current is not None:
            runs.append(tuple(current))
            current = None

    if current is not None:
        runs.append(tuple(current))

    return runs


def last_live_session(db=DB):
    """The newest date an equity actually traded on, or None."""

    for date, _listed, traded in reversed(traded_counts(db=db)):
        if traded >= MIN_TRADED_NAMES:
            return date

    return None


def is_trading_now(provider=None, min_names=MIN_TRADED_NAMES):
    """Is a session running RIGHT NOW? Observed from the live tape.

    Asks the market-watch feed how many instruments report turnover today and
    what the newest trade stamp is. Returns a dict rather than a bool because
    the three outcomes a caller must separate -- trading, not trading, and
    could not tell -- are not two.

        {"trading": True/False/None, "traded": int, "last_trade": int|None,
         "reason": str}

    `trading=None` means the feed could not be read. A caller that treats an
    unreachable exchange as a closed one will happily resume when the network
    comes back mid-session; one that treats it as open will send orders into
    a market it cannot see. Neither is right, so neither is chosen here.
    """

    if provider is None:
        from providers.tsetmc_cdn import TsetmcCdnProvider

        provider = TsetmcCdnProvider()

    url = (provider.base_url + "/api/ClosingPrice/GetMarketWatch"
           "?market=0&paperTypes[0]=1&showTraded=false&withBestLimits=false")

    try:
        payload = provider._get(url, timeout=(10.0, 45.0)).json()
    except Exception as exc:                                    # noqa: BLE001
        return {"trading": None, "traded": 0, "last_trade": None,
                "reason": "feed unreadable: %s: %s"
                          % (type(exc).__name__, str(exc)[:120])}

    rows = (payload.get("marketwatch") or payload.get("marketWatch") or [])

    if not rows:
        return {"trading": None, "traded": 0, "last_trade": None,
                "reason": "market watch returned no rows"}

    traded = 0
    newest = 0

    for item in rows:
        # qTotTran5J is share volume; qtj is the same figure under the older
        # name. A row with volume is a row that traded today -- the feed
        # resets these at the start of each session.
        volume = item.get("qTotTran5J")

        if volume is None:
            volume = item.get("qtj")

        try:
            if float(volume or 0) > 0:
                traded += 1
        except (TypeError, ValueError):
            pass

        try:
            stamp = int(item.get("hEven") or 0)
        except (TypeError, ValueError):
            stamp = 0

        newest = max(newest, stamp)

    return {
        "trading": traded >= min_names,
        "traded": traded,
        "last_trade": newest or None,
        "reason": "%d of %d instruments show turnover today" % (traded,
                                                                len(rows)),
    }


def report(since=None, db=DB):
    """What the stored data says about which sessions were real."""

    counts = traded_counts(since=since, db=db)

    if not counts:
        print("no equity bars stored%s"
              % (" since %s" % since if since else ""))
        return 0

    dead = [row for row in counts if row[2] < MIN_TRADED_NAMES]

    print("calendar dates with bars : %d   (%s .. %s)"
          % (len(counts), counts[0][0], counts[-1][0]))
    print("sessions that traded     : %d" % (len(counts) - len(dead)))
    print("dates the market was shut: %d  (%.1f%% of the calendar)"
          % (len(dead), 100.0 * len(dead) / len(counts)))

    runs = closures(since=since, db=db)

    if runs:
        print()
        print("closures -- bars published, nothing traded:")

        for first, last, sessions in runs:
            print("   %s .. %s   %3d session%s"
                  % (first, last, sessions, "" if sessions == 1 else "s"))

    flow = flow_dates(since=since, db=db)

    if flow:
        disagree = [d for d, _l, t in counts
                    if (t >= MIN_TRADED_NAMES) != (d in flow)]

        print()
        print("corroboration: %d dates where turnover and flow publication "
              "disagree" % len(disagree))

        for date in disagree[:10]:
            traded = dict((d, t) for d, _l, t in counts)[date]
            print("   %s  traded=%d  flow_published=%s"
                  % (date, traded, date in flow))

        if len(disagree) > 10:
            print("   ... and %d more" % (len(disagree) - 10))

    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default=None,
                        help="earliest date to report on (YYYY-MM-DD)")
    parser.add_argument("--now", action="store_true",
                        help="ask the live feed whether a session is running")
    parser.add_argument("--db", default=DB)

    args = parser.parse_args(argv)

    if args.now:
        state = is_trading_now()
        print("trading   : %s" % state["trading"])
        print("traded    : %d" % state["traded"])
        print("last trade: %s" % state["last_trade"])
        print("reason    : %s" % state["reason"])
        return 0

    return report(since=args.since, db=args.db)


if __name__ == "__main__":
    sys.exit(main())
