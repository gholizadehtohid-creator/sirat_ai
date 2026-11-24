"""Treasury-bill (اخزا) history: every series' daily bars, and the yield the
market offered at the start of each month.

WHY THIS EXISTS (2026-11-24)
-----------------------------
E27 and E28 -- the only accepted results in the repository -- measured equity
and gold against a fixed-income sleeve assumed to earn 20% a year. On the day
the owner's research report was written, treasury bills paid 39-44%. E34 re-tests
both results against what bills actually yielded; this module supplies that
series and nothing else. It computes no verdict.

WHERE THE BARS COME FROM
-------------------------
* Matured series: `census_instruments` / `census_price_history`, written by
  delisted_sweep.py (TSETMC keeps serving a dead instrument's whole history).
  109 bill series are there, 92 of them matured with bars from 2015 on.
* Live series: the census holds no bars for them (it fetched only the dead),
  so `refresh()` asks TSETMC for each live series' daily list -- one request
  per series, paced, about twenty in all.

Both land in two tables of their own, `bill_series` and `bill_bars`; nothing
else in the project reads them, and the census tables are only read.

WHAT A YIELD IS HERE
---------------------
The bills are zero-coupon at 1,000,000 rials. A bill's annual yield on a day
is `opportunities.bill_yield(close, maturity, day + 1)` -- the same arithmetic
the live card uses, settling T+1. `close` is TSETMC's pClosing, the day's
volume-weighted price, used only on a day the series actually traded.

The month-start yield of month M is the MEDIAN of the yields of every eligible
(series, session) quote in the last `TAIL_SESSIONS` sessions before M begins,
where eligible means: traded that day, `MIN_DAYS`..`MAX_DAYS` days to
maturity, and a yield inside `SANE_YIELD` (outside it is a misprint, counted).
If that tail holds no quote the window widens to `WIDE_TAIL_SESSIONS`; if it
is still empty the previous month's yield is carried forward and the month is
counted as carried. These constants are frozen in E34's preregistration --
change them only under a new experiment id.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "sirat_market.db")

FACE_VALUE = 1_000_000
MIN_DAYS = 90                  # the live card's rule: shorter bills price weeks
MAX_DAYS = 400                 # a year and a bit: the bill a saver would buy
SANE_YIELD = (0.05, 1.00)      # 5%..100% a year; outside is a misprint
TAIL_SESSIONS = 5
WIDE_TAIL_SESSIONS = 20
REQUEST_DELAY = 1.0            # seconds between TSETMC requests
HISTORY_COUNT = 8000           # every bar TSETMC holds (delisted_sweep uses the same)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bill_series (
        ins_code   TEXT PRIMARY KEY,
        symbol     TEXT,
        name       TEXT,
        maturity   TEXT,
        active     INTEGER,
        source     TEXT,
        bars       INTEGER,
        fetched_at TEXT,
        error      TEXT)""",
    """CREATE TABLE IF NOT EXISTS bill_bars (
        ins_code TEXT NOT NULL,
        date     TEXT NOT NULL,
        close    REAL,
        volume   REAL,
        value    REAL,
        PRIMARY KEY (ins_code, date))""",
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(readonly=False):
    if readonly:
        return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"),
                               uri=True, timeout=30)
    conn = sqlite3.connect(DB, timeout=30)
    for statement in SCHEMA:
        conn.execute(statement)
    return conn


def is_bill(symbol, name):
    """A treasury bill, not an option written on one (طاخزا / ضاخزا)."""

    name = (name or "").strip()
    return ("خزانه" in name and not name.startswith("اختيار")
            and not name.startswith("اختیار"))


def maturity(name):
    import opportunities
    return opportunities.maturity_of(name)


# ---- gathering ---------------------------------------------------------------

def _from_census(conn):
    """Register every census bill; copy the bars of those the census holds."""

    rows = conn.execute(
        "SELECT ins_code, symbol, name, active, bars FROM census_instruments "
        "WHERE symbol LIKE '%اخزا%' OR name LIKE '%خزانه%'").fetchall()
    added = copied = 0
    for code, symbol, name, active, bars in rows:
        if not is_bill(symbol, name):
            continue
        due = maturity(name)
        if due is None:
            continue
        conn.execute(
            "INSERT INTO bill_series (ins_code, symbol, name, maturity, active, "
            "source) VALUES (?, ?, ?, ?, ?, 'census') ON CONFLICT(ins_code) DO "
            "UPDATE SET symbol=excluded.symbol, name=excluded.name, "
            "maturity=excluded.maturity",
            (str(code), symbol, name, due.isoformat(), int(active or 0)))
        added += 1
        if bars:
            cur = conn.execute(
                "INSERT OR IGNORE INTO bill_bars (ins_code, date, close, volume, "
                "value) SELECT ins_code, substr(date, 1, 10), close, volume, "
                "value FROM census_price_history WHERE ins_code = ?",
                (str(code),))
            copied += cur.rowcount
            conn.execute(
                "UPDATE bill_series SET bars = (SELECT COUNT(*) FROM bill_bars "
                "WHERE ins_code = ?), fetched_at = COALESCE(fetched_at, ?) "
                "WHERE ins_code = ?", (str(code), _now(), str(code)))
    return added, copied


def _live_series():
    """Every live bill TSETMC's search returns: {ins_code: {symbol, name}}."""

    import opportunities
    session = opportunities._session()
    return opportunities.discover_bills(session)


def _fetch_bars(provider, code):
    result = provider.get_history(code, count=HISTORY_COUNT)
    frame = getattr(result, "data", None)
    if not getattr(result, "ok", False) or frame is None or frame.empty:
        raise RuntimeError(getattr(result, "error", None) or "no bars")
    out = []
    for row in frame.itertuples(index=False):
        when = getattr(row, "date", None)
        if when is None or str(when) == "NaT":
            continue
        out.append((str(code), when.strftime("%Y-%m-%d"),
                    _num(getattr(row, "close", None)),
                    _num(getattr(row, "volume", None)),
                    _num(getattr(row, "value", None))))
    return out


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def refresh(max_requests=60, verbose=True):
    """Census bills in; then the live series' bars from TSETMC, paced.

    A live series is fetched whole once and then again only when its stored
    bars are more than a day old; it is cheap (about twenty series) and E34
    needs nothing after 2026-08-18, so a failure here is reported, not retried.
    """

    from providers.tsetmc_cdn import TsetmcCdnProvider

    conn = _connect()
    try:
        added, copied = _from_census(conn)
        conn.commit()
        if verbose:
            print("census: %d bill series, %d bars copied" % (added, copied))

        live = _live_series()
        for code, info in live.items():
            due = maturity(info.get("name"))
            if due is None:
                continue
            conn.execute(
                "INSERT INTO bill_series (ins_code, symbol, name, maturity, "
                "active, source) VALUES (?, ?, ?, ?, 1, 'search') ON "
                "CONFLICT(ins_code) DO UPDATE SET active = 1",
                (str(code), info.get("symbol"), info.get("name"),
                 due.isoformat()))
        conn.commit()

        due_fetch = [r[0] for r in conn.execute(
            "SELECT ins_code FROM bill_series WHERE active = 1 AND "
            "(fetched_at IS NULL OR fetched_at < ?) ORDER BY maturity",
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))]

        provider = TsetmcCdnProvider()
        fetched = failed = 0
        for code in due_fetch[:max_requests]:
            try:
                rows = _fetch_bars(provider, code)
                conn.executemany(
                    "INSERT OR REPLACE INTO bill_bars (ins_code, date, close, "
                    "volume, value) VALUES (?, ?, ?, ?, ?)", rows)
                conn.execute(
                    "UPDATE bill_series SET bars = ?, fetched_at = ?, error = "
                    "NULL WHERE ins_code = ?", (len(rows), _now(), code))
                fetched += 1
            except Exception as exc:                             # noqa: BLE001
                conn.execute(
                    "UPDATE bill_series SET error = ?, fetched_at = ? WHERE "
                    "ins_code = ?", (str(exc)[:200], _now(), code))
                failed += 1
            conn.commit()
            time.sleep(REQUEST_DELAY)
        if verbose:
            print("live: %d series found, %d fetched, %d failed"
                  % (len(live), fetched, failed))
        return {"census_series": added, "live_series": len(live),
                "fetched": fetched, "failed": failed}
    finally:
        conn.close()


# ---- the series ---------------------------------------------------------------

def quotes(conn=None):
    """Every eligible (date, ins_code, yield) quote, ascending by date.

    Returns (rows, rejected): `rejected` lists the (date, ins_code, yield)
    of traded quotes whose yield fell outside SANE_YIELD.
    """

    import opportunities

    own = conn is None
    conn = conn or _connect(readonly=True)
    try:
        series = {code: date.fromisoformat(due) for code, due in conn.execute(
            "SELECT ins_code, maturity FROM bill_series WHERE maturity IS NOT "
            "NULL")}
        bars = conn.execute(
            "SELECT ins_code, date, close, volume, value FROM bill_bars "
            "ORDER BY date").fetchall()
    finally:
        if own:
            conn.close()

    rows, rejected = [], []
    for code, day, close, volume, value in bars:
        due = series.get(code)
        if due is None or not close or not (volume or 0) > 0:
            continue
        today = date.fromisoformat(day[:10])
        left = (due - today).days
        if not MIN_DAYS <= left <= MAX_DAYS:
            continue
        y = opportunities.bill_yield(close, due, today + timedelta(days=1))
        if y is None:
            continue
        if not SANE_YIELD[0] <= y <= SANE_YIELD[1]:
            rejected.append((today, code, y))
            continue
        rows.append((today, code, y))
    return rows, rejected


def _median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    middle = n // 2
    return values[middle] if n % 2 else (values[middle - 1] + values[middle]) / 2


def month_start_yields(sessions, start, end, conn=None):
    """{(year, month): record} for EVERY calendar month in start..end.

    `sessions` is the market calendar (dates of TEDPIX sessions). The pool for
    month M is the last TAIL_SESSIONS sessions before M begins (else the last
    WIDE_TAIL_SESSIONS) -- the tail of M-1 in a normal month. A month whose
    previous calendar month had no session at all (the exchange closed)
    carries the previous month's yield, labelled 'carried'; the arithmetic is
    the same either way, because the sessions before it are the ones its
    predecessor already used. Each record: yield (annual, decimal), quotes,
    basis ('tail' / 'wide' / 'carried'), from, to (the pool's session dates;
    `to` always precedes the month).

    Returns (records, rejected) -- `rejected` as quotes() returns it.
    """

    import bisect

    rows, rejected = quotes(conn)
    by_day = {}
    for day, _code, y in rows:
        by_day.setdefault(day, []).append(y)

    sessions = sorted({d if isinstance(d, date) else d.date() for d in sessions})
    months_with_sessions = {(d.year, d.month) for d in sessions}

    out = {}
    previous = None
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        before = sessions[:bisect.bisect_left(sessions, first)]
        prior_month = (y, m - 1) if m > 1 else (y - 1, 12)
        record = None
        if prior_month in months_with_sessions or previous is None:
            for basis, width in (("tail", TAIL_SESSIONS),
                                 ("wide", WIDE_TAIL_SESSIONS)):
                tail = before[-width:]
                pool = [q for d in tail for q in by_day.get(d, ())]
                if pool:
                    record = {"yield": _median(pool), "quotes": len(pool),
                              "basis": basis, "from": str(tail[0]),
                              "to": str(tail[-1])}
                    break
        if record is None and previous is not None:
            record = dict(previous, basis="carried", quotes=0)
        out[(y, m)] = record
        if record is not None:
            previous = record
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out, rejected


def coverage(conn=None):
    """Availability only -- counts of series and bars per year, no yields.

    This is what may be looked at before a preregistration is frozen.
    """

    own = conn is None
    conn = conn or _connect(readonly=True)
    try:
        series = conn.execute("SELECT COUNT(*), SUM(active) FROM bill_series"
                              ).fetchone()
        years = conn.execute(
            "SELECT substr(date, 1, 4), COUNT(*), COUNT(DISTINCT ins_code) "
            "FROM bill_bars WHERE volume > 0 GROUP BY 1 ORDER BY 1").fetchall()
    finally:
        if own:
            conn.close()
    return {"series": series[0], "active": series[1] or 0,
            "traded_bars_by_year": {y: {"bars": n, "series": s}
                                    for y, n, s in years}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--refresh", action="store_true",
                        help="copy census bills and fetch live series' bars")
    parser.add_argument("--coverage", action="store_true",
                        help="print availability (no yields)")
    args = parser.parse_args(argv)
    if args.refresh:
        print(json.dumps(refresh(), ensure_ascii=False))
    if args.coverage or not args.refresh:
        print(json.dumps(coverage(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
