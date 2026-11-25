"""The dollar's rial rate: the free market and the exchange centre (NIMA).

WHY THIS EXISTS
---------------
The user asked (2026-11-23) for dollar scenarios in the forward fundamental
analysis: what a company's year looks like if the dollar moves. Nothing in
the store held a dollar series (the review panel of 2026-11-11 named the gap).
Two rates matter and they differ by a third:

  free   the open-market rate (price_dollar_rl): what the public sees, and
         what imports outside the official channel pay.
  nima   the exchange centre's selling rate (nima_sell_usd): what exporters
         are paid for their dollars, and what commodity-exchange prices are
         struck on. The rate a petrochemical or steel maker's revenue tracks.

On 1405/09/01 they were 2,673,750 and 1,944,082 rials.

SOURCE
------
tgju.org's public table endpoint: one GET returns a series' whole daily
history (4,010 rows of the free rate since 1390, 1,896 of NIMA since 1397),
newest first, each row [open, low, high, close, change, change%, gregorian,
jalali]. Prices are rials, as strings with thousands separators. Reached on
the physical NIC like TSETMC (an Iranian host).

The file is data/fx_rates.json, rewritten whole, never appended: a refetch is
two requests and the series is small. refresh() is called by bootstrap after
each run and by the stock report before it is built, at most every
REFRESH_HOURS; it never raises -- a failed fetch keeps the stored series and
says how old it is.
"""

import bisect
import json
import os
import time
from datetime import datetime, timezone

import jalali

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "data", "fx_rates.json")
HOST = "api.tgju.org"
URL = "https://api.tgju.org/v1/market/indicator/summary-table-data/%s"
SERIES = {"free": "price_dollar_rl", "nima": "nima_sell_usd"}
LABELS = {"free": "دلار بازار آزاد", "nima": "دلار مرکز مبادله (نیما)"}
REFRESH_HOURS = 12
TIMEOUT = 30
# A day's close this far from the median of the days around it is a typing
# error in the source,
# not a market move: the rate's largest one-day move since 1390 was ~18%.
MAX_DAY_MOVE = 0.5

_CACHE = {"mtime": None, "data": None}


def _number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse(payload):
    """[(gregorian 'YYYY-MM-DD', jalali 'YYYY/MM/DD', close)], oldest first.

    Rows that do not read as a date and a positive price are dropped; so is
    a close more than MAX_DAY_MOVE from its neighbour on both sides.
    """

    rows = []
    for row in (payload or {}).get("data") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            continue
        close = _number(row[3])
        greg = str(row[6]).replace("/", "-").strip()
        jal = str(row[7]).strip()
        if not close or close <= 0 or len(greg) != 10 or len(jal) < 8:
            continue
        rows.append((greg, jal, close))
    rows.sort()
    # one row per day, the last one seen
    by_day = {}
    for row in rows:
        by_day[row[0]] = row
    rows = [by_day[d] for d in sorted(by_day)]
    # Against the median of a five-day window around it (itself included),
    # not each neighbour alone: next to a typing error, a true close would
    # otherwise be "far from both" and go with it.
    clean = []
    for i, row in enumerate(rows):
        around = sorted(r[2] for r in rows[max(i - 2, 0):i + 3])
        if len(around) >= 3:
            mid = len(around) // 2
            median = (around[mid] if len(around) % 2
                      else (around[mid - 1] + around[mid]) / 2.0)
            if median > 0 and abs(row[2] / median - 1) > MAX_DAY_MOVE:
                continue
        clean.append(row)
    return clean


def fetch(session=None):
    """Both series from the source, or raise. Returns the stored record."""

    from providers import transport

    session = session or transport.build_session(host=HOST)
    record = {"source": "tgju.org",
              "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "series": {}}
    for name, key in SERIES.items():
        response = session.get(URL % key, timeout=TIMEOUT)
        response.raise_for_status()
        rows = parse(response.json())
        if len(rows) < 100:
            raise ValueError("%s: only %d rows" % (key, len(rows)))
        record["series"][name] = rows
    _write(record)
    return record


def _write(record, path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"                 # swapped in whole
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    os.replace(partial, path)


def load(path=None):
    """The stored record, or None. Cached by the file's mtime."""

    path = path or PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if path == PATH and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    for name, rows in (data.get("series") or {}).items():
        data["series"][name] = [tuple(r) for r in rows]
    if path == PATH:
        _CACHE.update(mtime=mtime, data=data)
    return data


def age_hours(record=None, now=None):
    record = record if record is not None else load()
    try:
        fetched = datetime.fromisoformat(record["fetched_at"])
    except (TypeError, KeyError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    return (now - fetched).total_seconds() / 3600


def refresh(max_age_hours=REFRESH_HOURS, session=None):
    """Refetch when the stored series is older than `max_age_hours`.

    Returns a short message; never raises. A failed fetch leaves the stored
    series as it was.
    """

    age = age_hours()
    if age is not None and age < max_age_hours:
        return "dollar rates: fresh (%.1f h old)" % age
    started = time.monotonic()
    try:
        record = fetch(session=session)
    except Exception as exc:                                    # noqa: BLE001
        return "dollar rates: fetch failed (%s: %s); kept %s" % (
            type(exc).__name__, str(exc)[:120],
            "a %.0f h old copy" % age if age is not None else "nothing")
    return "dollar rates: %s in %.1f s" % (
        ", ".join("%s %d days" % (k, len(v)) for k, v in record["series"].items()),
        time.monotonic() - started)


def series(name="nima", record=None):
    record = record if record is not None else load()
    return list(((record or {}).get("series") or {}).get(name) or [])


def _gregorian(jalali_date):
    try:
        jy, jm, jd = (int(p) for p in str(jalali_date).split("/")[:3])
    except (TypeError, ValueError):
        return None
    return "%04d-%02d-%02d" % jalali.to_gregorian(jy, jm, max(jd, 1))


def rate_on(jalali_date, name="nima", record=None):
    """The close on or before a Jalali date, or None."""

    rows = series(name, record)
    day = _gregorian(jalali_date)
    if not rows or not day:
        return None
    i = bisect.bisect_right([r[0] for r in rows], day)
    return rows[i - 1][2] if i else None


def average(jalali_from, jalali_to, name="nima", record=None):
    """Mean close over [from, to] (Jalali dates), or None."""

    rows = series(name, record)
    a, b = _gregorian(jalali_from), _gregorian(jalali_to)
    if not rows or not a or not b:
        return None
    days = [r[0] for r in rows]
    values = [r[2] for r in rows[bisect.bisect_left(days, a):bisect.bisect_right(days, b)]]
    return sum(values) / len(values) if values else None


def latest(record=None):
    """{name: (jalali date, close)} of each series' newest day."""

    record = record if record is not None else load()
    out = {}
    for name in SERIES:
        rows = series(name, record)
        if rows:
            out[name] = (rows[-1][1], rows[-1][2])
    return out


def summary(record=None):
    """Today's two rates, their gap and the year's move, for the report."""

    record = record if record is not None else load()
    now = latest(record)
    if not now:
        return None
    out = {"as_of": None, "age_hours": age_hours(record), "rates": {}}
    for name, (day, close) in now.items():
        out["as_of"] = max(out["as_of"] or day, day)
        jy, jm, jd = (int(p) for p in day.split("/"))
        year_ago = rate_on("%04d/%02d/%02d" % (jy - 1, jm, jd), name, record)
        # The week and the month too (2026-11-26, the panel's commodities
        # card shows the dollar beside the metals), by Gregorian days back.
        rows = series(name, record)
        days = [r[0] for r in rows]

        def back(n):
            from datetime import date, timedelta
            y, m, d = (int(p) for p in rows[-1][0].split("-"))
            i = bisect.bisect_right(days, (date(y, m, d) - timedelta(days=n)).isoformat())
            return rows[i - 1][2] if i else None

        week, month = back(7), back(30)
        out["rates"][name] = {
            "label": LABELS[name], "date": day, "close": close,
            "change_1w": close / week - 1 if week else None,
            "change_1m": close / month - 1 if month else None,
            "change_1y": close / year_ago - 1 if year_ago else None}
    free, nima = (out["rates"].get(k, {}).get("close") for k in ("free", "nima"))
    out["gap"] = free / nima - 1 if free and nima else None
    return out


def main(argv=None):
    import sys
    print(refresh(max_age_hours=0 if "--force" in (argv or sys.argv[1:]) else REFRESH_HOURS))
    s = summary()
    if s:
        for name, r in s["rates"].items():
            print("  %s %s: %s rials" % (name, r["date"], format(int(r["close"]), ",")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
