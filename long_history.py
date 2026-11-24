"""Fifteen-year inputs for the allocation test (E36): the gold coin before
gold funds existed, and Islamic lease bonds (اوراق اجاره) before treasury
bills did.

WHY (2026-11-24)
-----------------
The owner pointed out that Tehran's market moves in cycles -- several years
up, three or four down -- and asked that tests cover fifteen years. Measured
in dollars, TEDPIX bottomed in 1391/07, 1397/07 and 1404/06 and peaked in
1392/10 and 1399/05: a cycle of six to seven years, so fifteen years hold
about two. E35 covered 2017-10..2026-07, roughly one. Two of its three legs
did not exist before then:

* GOLD. The first gold fund (طلا) started trading 1396/03 (2017-06). Before
  it, the gold an Iranian saver actually bought was the Emami coin. tgju.org
  serves its daily price from 1389 (series `sekee`), read with fx.parse --
  the same reader and the same outlier rule as the dollar series. The coin
  carries a premium over its gold content that moves on its own; for a
  holder that premium was part of the return, so it is kept, and declared.
* FIXED INCOME. Treasury bills started trading 1394/06 (2015-09) and are
  continuous in E35's window from 2017-10. Before them the exchange listed
  lease bonds (اجاره) with fixed coupons -- most of them 20% a year, paid
  quarterly -- from 1389/12 (2011-03). Their bars are in the census tables
  (delisted_sweep.py). A quote's yield is the current yield compounded at
  its coupon frequency, (1 + c/k * F/P)^k - 1, F the face the print is
  quoted against (100 for the early series, 1,000,000 rials later): the
  name states the coupon and the frequency but not always the maturity, so
  a yield to maturity cannot be computed for every series. Near par the two
  differ by little; that is declared, not hidden. Prices are as the exchange
  printed them, which for these bonds include accrued coupon -- a further
  error of up to a quarter's coupon either way, declared.

Nothing here computes a return or a test statistic. `coverage()` reports
availability only, which is what may be looked at before E36's
preregistration is frozen.
"""

import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

import fx

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "sirat_market.db")
COIN_PATH = os.path.join(ROOT, "data", "coin_sekee.json")
COIN_SERIES = "sekee"

FACE_VALUE = 1_000_000
PRICE_BAND = (0.8, 1.2)            # of face; outside is not a bond near par
SANE_YIELD = (0.05, 1.00)
DEFAULT_FREQUENCY = 4              # "سه ماهه": quarterly, the common case

FREQUENCY_WORDS = (("ماهانه", 12), ("یک ماهه", 12), ("سه ماهه", 4),
                   ("شش ماهه", 2), ("6 ماهه", 2), ("3 ماهه", 4))

LEASE_WORDS = ("اجاره", "مرابحه", "صکوک", "منفعت", "سلف")
COUPON = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*درصد")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text):
    return str(text or "").replace("ي", "ی").replace("ك", "ک")


# ---- the coin ------------------------------------------------------------------

def refresh_coin(session=None, path=None):
    """One request to tgju for the coin's whole daily history."""

    from providers import transport

    session = session or transport.build_session(host=fx.HOST)
    response = session.get(fx.URL % COIN_SERIES, timeout=(10, fx.TIMEOUT))
    response.raise_for_status()
    rows = fx.parse(response.json())
    if not rows:
        raise RuntimeError("tgju returned no coin rows")
    record = {"source": "tgju.org", "series_name": COIN_SERIES,
              "fetched_at": _now(), "series": rows}
    path = path or COIN_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    os.replace(partial, path)
    return {"rows": len(rows), "first": rows[0][0], "last": rows[-1][0]}


def coin_series(path=None):
    """[(iso date, jalali, price)] oldest first, or []."""

    try:
        with open(path or COIN_PATH, encoding="utf-8") as handle:
            return [tuple(r) for r in json.load(handle).get("series") or []]
    except (OSError, ValueError):
        return []


# ---- the lease bonds -------------------------------------------------------------

def is_lease(symbol, name):
    name = _norm(name)
    if name.startswith("اختیار") or "اختیار" in name[:8]:
        return False
    return any(w in name for w in LEASE_WORDS)


def coupon_of(name):
    """Annual coupon as a decimal from the name ("... 20 درصد"), or None."""

    match = COUPON.search(_norm(name))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value / 100.0 if 5 <= value <= 60 else None


def frequency_of(name):
    name = _norm(name)
    for word, k in FREQUENCY_WORDS:
        if word in name:
            return k
    return DEFAULT_FREQUENCY


def lease_series(conn):
    out = {}
    for code, symbol, name in conn.execute(
            "SELECT ins_code, symbol, name FROM census_instruments WHERE bars > 0"):
        if not is_lease(symbol, name):
            continue
        coupon = coupon_of(name)
        if coupon is None:
            continue
        out[str(code)] = {"symbol": symbol, "name": name, "coupon": coupon,
                          "frequency": frequency_of(name)}
    return out


def face_of(close):
    """The face a print is quoted against, or None if it is near neither.

    The early series (1389-1393) print per 100 of face -- ماهان, ساما1,
    صاميد and ماهان2 all trade at 100.0-101.95 -- and the later ones in
    rials of a 1,000,000 face. A print within PRICE_BAND of either is read
    against it; anything else is not a bond trading near par.
    """

    for face in (100.0, float(FACE_VALUE)):
        if PRICE_BAND[0] * face <= close <= PRICE_BAND[1] * face:
            return face
    return None


def lease_quotes(conn=None):
    """Every eligible (date, ins_code, yield) quote, ascending, and the
    rejected ones (yield outside SANE_YIELD) as quotes() returns them in
    bill_history -- so bill_history.month_start_yields can take either."""

    own = conn is None
    conn = conn or sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"),
                                   uri=True, timeout=30)
    try:
        series = lease_series(conn)
        rows, rejected = [], []
        for code, info in series.items():
            for day, close, volume in conn.execute(
                    "SELECT substr(date, 1, 10), close, volume FROM "
                    "census_price_history WHERE ins_code = ? ORDER BY date",
                    (code,)):
                if not close or not (volume or 0) > 0:
                    continue
                face = face_of(close)
                if face is None:
                    continue
                k = info["frequency"]
                y = (1 + info["coupon"] / k * face / close) ** k - 1
                when = date.fromisoformat(day)
                if not SANE_YIELD[0] <= y <= SANE_YIELD[1]:
                    rejected.append((when, code, y))
                    continue
                rows.append((when, code, y))
        rows.sort()
        return rows, rejected
    finally:
        if own:
            conn.close()


# ---- availability only -----------------------------------------------------------

def coverage(start=date(2011, 8, 1), end=date(2017, 9, 30)):
    """Counts per year -- no yield, no return. Safe before a preregistration."""

    coin = coin_series()
    coin_years = {}
    for iso, _j, _p in coin:
        if start.isoformat() <= iso <= end.isoformat():
            coin_years[iso[:4]] = coin_years.get(iso[:4], 0) + 1
    rows, rejected = lease_quotes()
    lease_years, codes = {}, {}
    for when, code, _y in rows:
        if start <= when <= end:
            key = str(when.year)
            lease_years[key] = lease_years.get(key, 0) + 1
            codes.setdefault(key, set()).add(code)
    months_without = []
    y, m = start.year, start.month
    have = {(w.year, w.month) for w, _c, _y in rows if start <= w <= end}
    while (y, m) <= (end.year, end.month):
        if (y, m) not in have:
            months_without.append("%d-%02d" % (y, m))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return {"coin_days_by_year": coin_years,
            "coin_first": coin[0][0] if coin else None,
            "lease_quotes_by_year": lease_years,
            "lease_series_by_year": {k: len(v) for k, v in codes.items()},
            "lease_rejected": len([r for r in rejected if start <= r[0] <= end]),
            "months_without_a_lease_quote": months_without}


if __name__ == "__main__":
    print(json.dumps(coverage(), ensure_ascii=False, indent=1))
