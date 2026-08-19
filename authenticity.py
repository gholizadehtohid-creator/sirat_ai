"""Test whether a cached price series is genuine Tehran Stock Exchange data.

This module exists because of a real incident in this project: a simulated
series was written into the database alongside exchange data and was, for a
while, mistaken for real. Two properties are easy for a simulation to get
right and one is hard:

  EASY  the trading week -- Saturday to Wednesday, closed Thursday/Friday.
  EASY  the price band -- daily moves clipped at the دامنه نوسان limit.
  HARD  the holiday calendar -- the TSE closes ~2 weeks for Nowruz and for
        roughly 25 public holidays a year whose Gregorian dates move.

Passing the first two proves very little. The Nowruz closure is the
discriminating test, and it is the one the simulated series failed.

    python authenticity.py
"""

import datetime
import sqlite3
from collections import Counter

import config
from database import DATABASE_NAME, SIMULATED, UNVERIFIED, VERIFIED_REAL, set_provenance

# Nowruz: the TSE closes for 1-4 Farvardin, NOT for the fortnight the holiday
# is popularly associated with. It reopens around 5 Farvardin (~25 March) and
# then closes again for 12-13 Farvardin (~1-2 April).
#
# This was calibrated wrongly once, and the mistake was expensive: the window
# used to run to 2 April, so the perfectly ordinary sessions of 25-31 March
# were read as trading through a closure, and 244,662 bars fetched live from
# cdn.tsetmc.com were pronounced simulated. Because the Gregorian date of
# 1 Farvardin drifts between 20 and 21 March, the days the exchange is shut in
# EVERY year are 21-23 March -- verified against the 1404 and 1405 calendars
# reconstructed from the exchange's own data.
NOWRUZ_START = (3, 21)
NOWRUZ_END = (3, 23)

# Non-weekend closure gaps per year. Measured on genuine exchange data across
# 818 instruments: 11.1/year. A Sat-Wed generator with no holiday calendar
# produces almost none, so the floor sits between the two, near the simulation
# end -- the test is here to catch a fabrication, not to fail a real calendar
# that happened to have a quiet year.
MIN_HOLIDAY_GAPS_PER_YEAR = 6

# Consecutive closed days merge into one gap (Nowruz's four days are a single
# gap), and any holiday falling on Thursday or Friday is invisible behind the
# weekend. That is why ~25 public holidays a year show up as ~11 gaps.
OBSERVED_GAPS_PER_YEAR = 11.1

# A date counts as a session only if this share of the best-covered day's
# instruments have a bar on it.
SESSION_COVERAGE_RATIO = 0.25

# The two price tables key their rows differently: v1 by a latin symbol, v2 by
# insCode. Everything here is per-series, so it only needs to know which column
# separates one series from the next.
KEY_COLUMNS = {"market_data": "symbol", "price_history": "ins_code"}

# Tables to test, most authoritative first. price_history is the v2 store the
# whole-market screen reads; market_data is the legacy five-symbol cache.
TABLE_PRIORITY = ("price_history", "market_data")

# The band check reads every bar of the series it samples. Over a whole-market
# table that is a quarter of a million rows for a statistic that converges long
# before then, so it samples this many series.
BAND_SAMPLE_SERIES = 60


def _key_column(table):
    return KEY_COLUMNS.get(table, "symbol")


def _row_count(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _pick_table(conn, preferred=None):
    """The populated price table to judge.

    Choosing by row count rather than by a hardcoded name matters: a verdict
    silently rendered on an empty table reads as "unverified" and looks like a
    data problem, when the real bars were simply in the other table.
    """

    if preferred:
        return preferred, _row_count(conn, preferred)

    for table in TABLE_PRIORITY:
        rows = _row_count(conn, table)
        if rows:
            return table, rows

    return TABLE_PRIORITY[0], 0


def _sessions(conn, table="market_data"):
    """The exchange calendar implied by the cached bars, plus how it was cut.

    A date is a session when a real share of the stored instruments have a bar
    on it. Taking every distinct date instead reconstructs the calendar from
    whatever happened to be listed at the time -- and in a whole-market table
    the oldest dates come from a handful of long-delisted instruments whose
    thin, intermittent trading is indistinguishable from an exchange that shut
    for weeks on end. Judging a holiday calendar on that is judging noise: the
    same 818-instrument table reads as 11.4 closures/year across its full
    25-year span and 11.1 across the 15 months that are actually covered, and
    only the second number describes the Tehran Stock Exchange.
    """

    key = _key_column(table)

    rows = conn.execute(
        f"SELECT date, COUNT(DISTINCT {key}) FROM {table} "
        f"GROUP BY date ORDER BY date"
    ).fetchall()

    dated = []

    for value, count in rows:
        try:
            dated.append((datetime.date.fromisoformat(str(value)[:10]), count))
        except ValueError:
            continue

    if not dated:
        return [], {"dates": 0, "kept": 0, "min_instruments": 0}

    # Reference against the best-covered day, not a percentile of all days.
    # Most dates in a whole-market table are old and thin, so any percentile
    # is dragged down by exactly the dates that need excluding -- the 90th
    # here sat at 32 instruments and let 2008 back in.
    best = max(count for _, count in dated)

    floor = max(1, int(best * SESSION_COVERAGE_RATIO))

    kept = [day for day, count in dated if count >= floor]

    return kept, {
        "dates": len(dated),
        "kept": len(kept),
        "min_instruments": floor,
        "best_day_instruments": best,
    }


def check_trading_week(days):
    """Sat-Wed only. Easy to fake, but a failure here is decisive."""

    counts = Counter(d.strftime("%a") for d in days)
    weekend = counts.get("Thu", 0) + counts.get("Fri", 0)
    share = weekend / len(days) if days else 1.0

    return {
        "name": "Trading week (Sat-Wed)",
        "passed": share < 0.02,
        "detail": (f"{weekend} of {len(days)} sessions fall on Thu/Fri "
                   f"({share*100:.1f}%)"),
        "discriminating": False,
    }


def check_price_band(conn, table="market_data"):
    """Daily moves should cluster inside the band."""

    key = _key_column(table)

    series_ids = [
        row[0] for row in conn.execute(
            f"SELECT {key} FROM {table} GROUP BY {key} "
            f"ORDER BY COUNT(*) DESC LIMIT {BAND_SAMPLE_SERIES}"
        )
    ]

    if not series_ids:
        return {"name": "Price band", "passed": False,
                "detail": "no series to test", "discriminating": False}

    placeholders = ",".join("?" * len(series_ids))

    rows = conn.execute(
        f"SELECT {key}, date, close FROM {table} "
        f"WHERE {key} IN ({placeholders}) ORDER BY {key}, date",
        series_ids
    ).fetchall()

    by_symbol = {}
    for symbol, date, close in rows:
        by_symbol.setdefault(symbol, []).append((date, close))

    moves = []
    for series in by_symbol.values():
        series.sort()
        for i in range(1, len(series)):
            prev = series[i - 1][1]
            current = series[i][1]
            # A halted session reports a zero close; comparing against it
            # invents a -100% move followed by a +infinite one.
            if prev and prev > 0 and current and current > 0:
                moves.append((current - prev) / prev * 100)

    if not moves:
        return {"name": "Price band", "passed": False,
                "detail": "no moves to test", "discriminating": False}

    band = config.PRICE_BAND_PCT
    inside = sum(1 for m in moves if abs(m) <= band + 0.6)
    share = inside / len(moves)

    return {
        "name": f"Price band (+/-{band}%)",
        "passed": share > 0.95,
        "detail": f"{share*100:.1f}% of {len(moves)} daily moves sit inside the band",
        "discriminating": False,
    }


def check_nowruz(days):
    """THE discriminating test: is the market closed for Nowruz?"""

    if not days:
        return {"name": "Nowruz closure", "passed": False,
                "detail": "no sessions", "discriminating": True}

    years = sorted({d.year for d in days})

    findings = []
    failed = False

    for year in years:

        start = datetime.date(year, *NOWRUZ_START)
        end = datetime.date(year, *NOWRUZ_END)

        # Only judge a year whose Nowruz window the data actually spans.
        if not (min(days) <= start and end <= max(days)):
            continue

        during = [d for d in days if start <= d <= end]

        # Weekends inside the window would be closed anyway; count weekdays.
        trading_weekdays = [d for d in during if d.strftime("%a") not in ("Thu", "Fri")]

        # In some years the whole core window lands on Thursday/Friday, and a
        # closed market proves nothing there -- it would have been closed for
        # the weekend regardless. Say so rather than banking a free pass.
        span = (end - start).days + 1
        eligible = [
            start + datetime.timedelta(days=offset)
            for offset in range(span)
            if (start + datetime.timedelta(days=offset)).strftime("%a")
            not in ("Thu", "Fri")
        ]

        if not eligible:
            findings.append(f"{year}: Nowruz fell entirely on a weekend — no signal")
            continue

        if trading_weekdays:
            failed = True
            findings.append(
                f"{year}: {len(trading_weekdays)} sessions during the Nowruz "
                f"closure ({', '.join(str(d) for d in trading_weekdays[:6])}"
                f"{'...' if len(trading_weekdays) > 6 else ''})"
            )
        else:
            findings.append(f"{year}: correctly closed through Nowruz")

    if not findings:
        return {
            "name": "Nowruz closure",
            "passed": None,
            "detail": "the data does not span a full Nowruz window",
            "discriminating": True,
        }

    return {
        "name": "Nowruz closure",
        "passed": not failed,
        "detail": "; ".join(findings),
        "discriminating": True,
    }


def check_holiday_density(days):
    """Iran has ~25 public holidays a year; a Sat-Wed generator has none."""

    if len(days) < 2:
        return {"name": "Holiday density", "passed": False,
                "detail": "not enough sessions", "discriminating": True}

    # Under a year of sessions, one long Nowruz gap swings the annualised rate
    # by several points. Report it, do not convict on it.
    span_days = (days[-1] - days[0]).days

    extra = 0
    for i in range(1, len(days)):
        gap = (days[i] - days[i - 1]).days
        previous = days[i - 1].strftime("%a")
        # Wed -> Sat is the ordinary weekend. Anything longer is a closure.
        ordinary = (previous == "Wed" and gap == 3)
        if gap >= 3 and not ordinary:
            extra += 1

    span_years = span_days / 365.25
    per_year = extra / span_years if span_years > 0 else 0

    detail = (f"{extra} non-weekend closures over {span_years:.2f} years "
              f"= {per_year:.1f}/year "
              f"(measured on real exchange data: {OBSERVED_GAPS_PER_YEAR}/year; "
              f"a generated Sat-Wed calendar has ~0)")

    if span_years < 1.0:
        return {
            "name": "Holiday density",
            "passed": None,
            "detail": detail + " — too short a span to annualise",
            "discriminating": True,
        }

    return {
        "name": "Holiday density",
        "passed": per_year >= MIN_HOLIDAY_GAPS_PER_YEAR,
        "detail": detail,
        "discriminating": True,
    }


def verify(table=None, record=True):
    """Run every check and return a verdict.

    With no table given, whichever price table holds bars is judged.
    """

    conn = sqlite3.connect(DATABASE_NAME)

    try:
        table, rows = _pick_table(conn, table)

        days, coverage = _sessions(conn, table) if rows else ([], {})

        if not days:
            return {"status": UNVERIFIED, "checks": [], "table": table,
                    "coverage": coverage,
                    "summary": f"{table} is empty"}

        checks = [
            check_trading_week(days),
            check_price_band(conn, table),
            check_nowruz(days),
            check_holiday_density(days),
        ]

    finally:
        conn.close()

    hard_failures = [
        c for c in checks if c["discriminating"] and c["passed"] is False
    ]
    soft_failures = [
        c for c in checks if not c["discriminating"] and c["passed"] is False
    ]

    if hard_failures:
        status = SIMULATED
        summary = (
            "FAILS a discriminating test — this series is simulated, not "
            "exchange data: " + "; ".join(c["name"] for c in hard_failures)
        )
    elif soft_failures:
        status = SIMULATED
        summary = ("fails a basic structural test: "
                   + "; ".join(c["name"] for c in soft_failures))
    elif any(c["passed"] is None for c in checks):
        status = UNVERIFIED
        summary = "passes what could be tested, but not every test could run"
    else:
        status = VERIFIED_REAL
        summary = "passes every test including the holiday calendar"

    if record:
        set_provenance("price_history", status, summary)

    return {"status": status, "checks": checks, "summary": summary,
            "table": table, "bars": rows, "sessions": len(days),
            "coverage": coverage,
            "range": f"{days[0]} -> {days[-1]}"}


if __name__ == "__main__":

    result = verify()

    print("=" * 74)
    print("PRICE DATA AUTHENTICITY CHECK")
    print("=" * 74)
    coverage = result.get("coverage") or {}

    print(f"table: {result.get('table')}   bars: {result.get('bars')}")
    print(f"sessions: {result.get('sessions')}   range: {result.get('range')}")

    if coverage.get("dates"):
        print(f"calendar: {coverage['kept']} of {coverage['dates']} dates had "
              f"at least {coverage['min_instruments']} instruments trading "
              f"(best day: {coverage.get('best_day_instruments')})")

    print()

    for check in result["checks"]:
        mark = {True: "PASS", False: "FAIL", None: "N/A "}[check["passed"]]
        star = " *" if check["discriminating"] else "  "
        print(f"  [{mark}]{star} {check['name']}")
        print(f"          {check['detail']}")

    print()
    print("  * = discriminating test (hard for a simulation to reproduce)")
    print()
    print("=" * 74)
    print(f"VERDICT: {result['status'].upper()}")
    print(f"  {result['summary']}")
    print("=" * 74)

    if result["status"] == SIMULATED:
        print()
        print("These bars are NOT exchange data. Any score, backtest or P&L")
        print("computed from them describes a simulation and says nothing")
        print("about the real market.")
