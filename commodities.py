"""World commodity prices: gold and silver, Brent and the OPEC basket, and
the London base metals -- descriptive context for the market summary and for
the companies whose sales follow them.

WHY THIS EXISTS
---------------
The owner went through BourseView with the bot on 2026-11-26 and asked for
what it shows that the bot does not. Its market page carries the gold ounce,
oil and the base metals beside the index; the bot had only the dollar
(fx.py). A copper, zinc or aluminium producer's year moves with the metal's
dollar price, and a refiner's or a petrochemical's with oil.

SOURCE
------
The same tgju.org table endpoint fx.py reads (api.tgju.org, an Iranian host
on the physical NIC): one GET per series returns its whole daily history,
rows [open, low, high, close, change, change%, gregorian, jalali]. Prices are
in dollars per the unit named in SERIES. Probed 2026-11-26: the ounce since
1358, Brent since 1384, copper (COMEX, dollars a pound) since 1367, LME
aluminium, zinc, lead and nickel (dollars a tonne) since 1386-1393. The keys
were read from tgju's own pages: "basemetal-aluminum", not "-aluminium".

data/commodities.json is rewritten whole. refresh() runs from bootstrap's
post-run hook beside fx.refresh(), at most every REFRESH_HOURS, pausing
PAUSE_SECONDS between requests; it never raises, and a series that fails keeps
the rows it had.

WHAT IT IS NOT
--------------
No forecast and no signal. The bot has not tested whether a metal's move says
anything about the share's next return, and it is shown as what it is: the
price the company sells into, and how far it has moved.
"""

import bisect
import json
import os
import time
from datetime import datetime, timezone

import fx
import jalali

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "data", "commodities.json")
REFRESH_HOURS = 12
PAUSE_SECONDS = 1.5
TIMEOUT = 30
# A series whose newest close is older than this many days is said to be.
STALE_DAYS = 10

# name: (tgju key, Persian label, unit)
SERIES = {
    "gold_ounce": ("ons", "انس طلا", "دلار"),
    "silver": ("silver", "انس نقره", "دلار"),
    "brent": ("oil_brent", "نفت برنت", "دلار/بشکه"),
    "opec": ("oil_opec", "سبد نفتی اوپک", "دلار/بشکه"),
    "copper": ("basemetal-copper", "مس", "دلار/پوند"),
    "aluminum": ("basemetal-aluminum", "آلومینیوم", "دلار/تن"),
    "zinc": ("basemetal-zinc", "روی", "دلار/تن"),
    "lead": ("basemetal-lead", "سرب", "دلار/تن"),
    "nickel": ("basemetal-nickel", "نیکل", "دلار/تن"),
}
ORDER = list(SERIES)

# Which commodity a company sells into, from its exchange sector and its
# name. Only where the link is direct: a steel maker is not a copper story,
# and iron ore and steel have no series here.
SECTOR_COMMODITIES = {
    "فراورده هاي نفتي، كك و سوخت هسته اي": ("brent",),
    "استخراج نفت گاز و خدمات جنبي جز اکتشاف": ("brent",),
    "محصولات شيميايي": ("brent",),
}
NAME_COMMODITIES = (
    ("مس", ("copper",)),
    # The exchange writes them without the madda: «الومینیومایران» (فایرا),
    # «الومینای ایران», «الومینیم پارس», «الومتک», «الومراد».
    ("آلوم", ("aluminum",)), ("الوم", ("aluminum",)),
    ("سرب", ("lead", "zinc")), ("روي", ("zinc", "lead")),
    ("روی", ("zinc", "lead")), ("فرآوري مواد معدني", ("zinc", "lead")),
    ("طلا", ("gold_ounce",)),
)
METAL_SECTORS = ("فلزات اساسي", "استخراج کانه هاي فلزي", "استخراج ساير معادن")

_CACHE = {"mtime": None, "data": None}


def fetch(session=None, previous=None, pause=PAUSE_SECONDS):
    """Every series from tgju, or the rows already held for one that fails.
    Returns the stored record; raises only when nothing at all came back."""

    from providers import transport

    session = session or transport.build_session(host=fx.HOST)
    held = ((previous or {}).get("series")) or {}
    record = {"source": "tgju.org",
              "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "series": {}, "failed": {}}
    got = 0
    for i, (name, (key, _label, _unit)) in enumerate(SERIES.items()):
        if i and pause:
            time.sleep(pause)
        try:
            response = session.get(fx.URL % key, timeout=TIMEOUT)
            response.raise_for_status()
            rows = fx.parse(response.json())
            if len(rows) < 100:
                raise ValueError("only %d rows" % len(rows))
            record["series"][name] = rows
            got += 1
        except Exception as exc:                                # noqa: BLE001
            record["failed"][name] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            if held.get(name):
                record["series"][name] = held[name]
    if not got:
        raise RuntimeError("no commodity series answered: %s" % record["failed"])
    _write(record)
    return record


def _write(record, path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    os.replace(partial, path)
    _CACHE["mtime"] = None


def load(path=None):
    """The stored record, or None. Cached by mtime."""

    path = path or PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _CACHE["mtime"] == mtime and path == PATH:
        return _CACHE["data"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if path == PATH:
        _CACHE.update(mtime=mtime, data=data)
    return data


def age_hours(record=None, now=None):
    record = record if record is not None else load()
    try:
        then = datetime.fromisoformat(record["fetched_at"])
    except (TypeError, KeyError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    return (now - then).total_seconds() / 3600.0


def refresh(max_age_hours=REFRESH_HOURS, session=None):
    """Fetch when the stored series are older than `max_age_hours`. A
    message, never an exception."""

    record = load()
    age = age_hours(record)
    if age is not None and age < max_age_hours:
        return "commodities: current (%.1f h old)" % age
    try:
        record = fetch(session=session, previous=record)
    except Exception as exc:                                    # noqa: BLE001
        return "commodities: fetch failed, kept the stored series (%s: %s)" % (
            type(exc).__name__, str(exc)[:160])
    failed = record.get("failed") or {}
    return "commodities: %d series fetched%s" % (
        len(SERIES) - len(failed),
        "; kept the stored rows of %s" % ", ".join(sorted(failed)) if failed else "")


def _close_on_or_before(rows, day):
    i = bisect.bisect_right([r[0] for r in rows], day)
    return rows[i - 1][2] if i else None


def _days_before(gregorian, days):
    from datetime import date, timedelta
    y, m, d = (int(p) for p in gregorian.split("-"))
    return (date(y, m, d) - timedelta(days=days)).isoformat()


def summary(record=None, today=None):
    """{"as_of", "age_hours", "items": [{name, label, unit, date, close,
    change_1w, change_1m, change_1y, stale}]} in ORDER, or None."""

    record = record if record is not None else load()
    if not record:
        return None
    today = today or datetime.now(timezone.utc).date().isoformat()
    items = []
    for name in ORDER:
        rows = ((record.get("series") or {}).get(name)) or []
        if not rows:
            continue
        greg, jal, close = rows[-1]
        changes = {}
        for label, days in (("change_1w", 7), ("change_1m", 30), ("change_1y", 365)):
            then = _close_on_or_before(rows, _days_before(greg, days))
            changes[label] = close / then - 1 if then else None
        stale_days = (datetime.fromisoformat(today) - datetime.fromisoformat(greg)).days
        key, label, unit = SERIES[name]
        items.append(dict(name=name, label=label, unit=unit, date=jal, close=close,
                          stale=stale_days > STALE_DAYS, **changes))
    if not items:
        return None
    return {"as_of": max(i["date"] for i in items), "age_hours": age_hours(record),
            "source": record.get("source"), "items": items}


def for_company(name, sector):
    """The commodity names a company's sales follow, or ()."""

    name, sector = (name or "").strip(), (sector or "").strip()
    found = list(SECTOR_COMMODITIES.get(sector, ()))
    # Names only inside the metal and mining sectors: «مس» is also the start
    # of «مسکن», and a housing developer is not a copper story.
    if sector in METAL_SECTORS:
        for word, names in NAME_COMMODITIES:
            if word in name:
                found.extend(names)
                break
    out = []
    for n in found:
        if n not in out:
            out.append(n)
    return tuple(out)


def company_lines(name, sector, record=None):
    """Persian lines on the commodities a company's sales follow."""

    names = for_company(name, sector)
    if not names:
        return []
    s = summary(record)
    if not s:
        return []
    by = {i["name"]: i for i in s["items"]}
    lines = []
    for n in names:
        item = by.get(n)
        if not item:
            continue
        parts = []
        for key, label in (("change_1m", "یک ماه"), ("change_1y", "یک سال")):
            if item[key] is not None:
                parts.append("%s %+.0f٪" % (label, item[key] * 100))
        lines.append("قیمت جهانی %s: %s %s (%s)%s — منبع tgju؛ توصیف است، نه پیش‌بینی" % (
            item["label"], format(round(item["close"], 2), ","), item["unit"],
            item["date"], "؛ " + "، ".join(parts) if parts else ""))
    return lines


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    print(refresh(max_age_hours=0 if "--force" in argv else REFRESH_HOURS))
    s = summary()
    for item in (s or {}).get("items", []):
        print("  %-10s %s %12s %s  1w %s  1m %s  1y %s" % (
            item["name"], item["date"], format(round(item["close"], 2), ","), item["unit"],
            *("%+.1f%%" % (item[k] * 100) if item[k] is not None else "-"
              for k in ("change_1w", "change_1m", "change_1y"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
