"""Codal figures as they were known on a date: every release, not the last word.

    python pit_fundamentals.py                 the store's statistics
    python pit_fundamentals.py --code <ins>    one company's revisions

WHY THIS EXISTS
---------------
`metrics` (fundamental_db) keeps one row per company-period: the most
recently published release. Everything the panel reads is built on it, and
for "what does the company say today" that is right. For any question about
the past it is look-ahead: on 2026-11-24, 7,838 of 16,060 company-periods
had more than one release (the unaudited statement, the audited one,
amendments; separate and consolidated), and where a period had several
releases on one basis its net profit moved by more than 1% between the
first and the last in 40% of them (a median 8% when it moved). The
outlook's error band (outlook_calibration) was measured on those last
words, for its inputs and for the "actual" it scored against, as if every
decision had been taken at the period's END -- before the statement was
even published. The owner approved, the same day, storing the figures
point-in-time and re-measuring the band on them.

fundamental_db.sync_versions() mirrors every release from codal_filings
into `metric_versions`, with a sortable Gregorian `published_iso`. This
module reads it:

  as_of(ins_code, when, lag_days=0, consolidated=None)
      per period, the release `metrics` would have held on (when - lag_days):
      load_metrics' shape, newest period first, so it can be handed to
      fundamental_outlook.analyse(rows=...) as it is;
  first_release(ins_code, period_end=None, period_months=None, consolidated=None)
      the first release that carried figures;
  revisions(ins_code)
      each period's chain of releases and how the net profit moved;
  statistics()
      publication lags, revision sizes, how many periods mix bases.

THE RULES, AND WHY
------------------
* "Known on D" means published_iso <= D. A Codal stamp carries its clock;
  one without a clock is the end of its day (jalali.parse), so a same-day
  decision never sees it.
* Among the releases known on D, the one held is chosen as `metrics`
  chooses (fundamental_db.sync_from_market_db): in publication order each
  release replaces the held one, unless its parse lacks a statement the
  held parse has (database.statements_lost) -- that is a failed read of the
  statement, not the company's word on it. So as_of(code, today) is what
  `metrics` holds, and any day before it is what `metrics` WOULD have held.
* `consolidated=None` lets the bases compete as `metrics` does (the newest
  wins whatever its basis); True/False keeps one basis, which is what a
  like-for-like series needs: 7.8% of company-periods carry both, and a
  holding company's separate statement is a different business from its
  group's (fundamental_outlook.choose_basis).
* The first release is the earliest one whose parse carries the income
  statement (revenue or net profit). A parse without it is a failed read,
  not a release of the year's figures. And it is the first release IN THIS
  STORE: where the collector never fetched the original letter, the date is
  later than the market's -- the store's lag statistics show where.
"""

import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote

import jalali

# A first and a latest net profit further apart than this are a material
# revision: what the per-stock report marks beside a statement.
MATERIAL_REVISION = 0.05
# Net profit is said to have changed at all past this (rounding, a
# re-typed figure).
CHANGED = 0.01

_COLUMNS = ("tracing_no", "ins_code", "period_end", "period_months",
            "consolidated", "audited", "published_at", "published_iso",
            "title", "items", "ratios")


# ---- reading the store -------------------------------------------------------

def _loads(text):
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _record(row):
    rec = dict(zip(_COLUMNS, row))
    rec["ins_code"] = str(rec["ins_code"])
    rec["period_months"] = int(rec["period_months"] or 12)
    rec["consolidated"] = None if rec["consolidated"] is None else bool(rec["consolidated"])
    rec["audited"] = None if rec["audited"] is None else bool(rec["audited"])
    rec["items"] = _loads(rec["items"])
    rec["ratios"] = _loads(rec["ratios"])
    return rec


def load_versions(ins_code=None):
    """{ins_code: [release, ...]} oldest publication first; {} without a
    store or a metric_versions table. Never creates either: a read-only
    connection, so a reader cannot write by accident.

    A release whose publication stamp did not parse is left out: it cannot
    be placed in time, and a point-in-time read must not guess."""

    import fundamental_db

    target = fundamental_db.path()
    if not os.path.isfile(target):
        return {}
    uri = "file:%s?mode=ro" % quote(os.path.abspath(target).replace("\\", "/"),
                                    safe="/:")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.DatabaseError:
        return {}
    query = ("SELECT %s FROM metric_versions WHERE published_iso IS NOT NULL"
             % ", ".join(_COLUMNS))
    params = []
    if ins_code is not None:
        query += " AND ins_code = ?"
        params.append(str(ins_code))
    query += " ORDER BY ins_code, published_iso, tracing_no"
    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.DatabaseError:            # no table yet: not synced
        return {}
    finally:
        conn.close()
    out = defaultdict(list)
    for row in rows:
        rec = _record(row)
        out[rec["ins_code"]].append(rec)
    return dict(out)


def _versions_for(ins_code, versions):
    if versions is not None:
        return versions
    return load_versions(ins_code).get(str(ins_code), [])


# ---- time --------------------------------------------------------------------

def cutoff(when, lag_days=0):
    """The ISO stamp a release must not be later than: `when` less
    `lag_days`. `when` is a datetime, a date, an ISO string ('2021-04-19',
    '2021-04-19T12:00:00') or a Jalali one ('1400/01/30', with or without a
    clock). A day given without a clock means its end (23:59:59)."""

    if isinstance(when, datetime):
        stamp = when
    elif isinstance(when, date):
        stamp = datetime(when.year, when.month, when.day, 23, 59, 59)
    else:
        text = str(when or "").strip()
        if "/" in text:
            stamp = jalali.parse(text)
        else:
            try:
                stamp = datetime.fromisoformat(text)
            except ValueError:
                stamp = None
            if stamp is not None and len(text) <= 10:
                stamp = stamp.replace(hour=23, minute=59, second=59)
        if stamp is None:
            raise ValueError("not a date: %r" % (when,))
    if stamp.tzinfo is not None:
        stamp = stamp.replace(tzinfo=None)
    stamp = stamp - timedelta(days=float(lag_days or 0))
    return stamp.isoformat(timespec="seconds")


def parse_iso(stamp):
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


# ---- choosing among releases --------------------------------------------------

def _figure(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def has_income(items):
    """Does the parse carry the income statement (revenue or net profit)?"""

    items = items or {}
    return _figure(items.get("revenue")) or _figure(items.get("net_profit"))


def net_profit(items):
    """The owners' net profit where the parse has it, else the total: the
    figure the outlook scores (fundamental_outlook._profit)."""

    items = items or {}
    for key in ("net_profit_owners", "net_profit"):
        value = items.get(key)
        if _figure(value):
            return float(value)
    return None


def winner(releases):
    """The release `metrics` holds out of `releases` (publication order):
    each replaces the held one unless its parse lacks a statement the held
    parse has. None for none."""

    import database

    held = None
    for rec in sorted(releases, key=lambda r: (r["published_iso"] or "",
                                               str(r["tracing_no"]))):
        if held is None or not database.statements_lost(held["items"], rec["items"]):
            held = rec
    return held


def earliest(releases):
    """The first release that carries the income statement; the first of
    all when none does."""

    ordered = sorted(releases, key=lambda r: (r["published_iso"] or "",
                                              str(r["tracing_no"])))
    for rec in ordered:
        if has_income(rec["items"]):
            return rec
    return ordered[0] if ordered else None


def _by_period(releases, consolidated=None):
    groups = defaultdict(list)
    for rec in releases:
        if consolidated is not None and rec["consolidated"] != bool(consolidated):
            continue
        groups[(rec["period_end"], rec["period_months"])].append(rec)
    return groups


def _newest_first(records):
    return sorted(records, key=lambda r: (r["period_end"], r["period_months"]),
                  reverse=True)


# ---- the public reads ------------------------------------------------------------

def periods_as_of(releases, stamp, consolidated=None):
    """as_of() over releases already in memory; `stamp` an ISO cutoff."""

    known = [r for r in releases if r["published_iso"] and r["published_iso"] <= stamp]
    chosen = [winner(group) for group in _by_period(known, consolidated).values()]
    return _newest_first([c for c in chosen if c])


def as_of(ins_code, when, lag_days=0, consolidated=None, versions=None):
    """Per period, the release that was known on (when - lag_days), chosen
    as `metrics` chooses; newest period first, in load_metrics' shape (plus
    `published_iso`), ready for fundamental_outlook.analyse(rows=...).

    `versions` (the company's releases, as load_versions gives them) spares
    the store read when a caller asks many dates of one company."""

    return periods_as_of(_versions_for(ins_code, versions),
                         cutoff(when, lag_days), consolidated)


def first_release(ins_code, period_end=None, period_months=None,
                  consolidated=None, versions=None):
    """The first release of a period that carried figures (see earliest()).

    With `period_end` (and `period_months`, default 12) the one record or
    None; without, every period's first release, newest period first."""

    groups = _by_period(_versions_for(ins_code, versions), consolidated)
    if period_end is not None:
        months = int(period_months or 12)
        return earliest(groups.get((str(period_end), months), []))
    return _newest_first([earliest(g) for g in groups.values() if g])


def revisions(ins_code, versions=None):
    """Every period with more than one release ON ONE BASIS, newest first:

        {period_end, period_months, consolidated, count,
         releases: [{tracing_no, published_at, audited, net_profit}],
         first_net_profit, latest_net_profit, change}

    `change` is latest / first - 1 of the net profit (None when either is
    missing or the first is zero). `latest` is the release `metrics` holds
    for that basis. A period whose bases differ is two series, never one
    revision."""

    out = []
    releases = _versions_for(ins_code, versions)
    for basis in (False, True):
        for (end, months), group in _by_period(releases, basis).items():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda r: (r["published_iso"], str(r["tracing_no"])))
            first, latest = earliest(ordered), winner(ordered)
            a = net_profit(first["items"]) if first else None
            b = net_profit(latest["items"]) if latest else None
            out.append({
                "period_end": end, "period_months": months,
                "consolidated": basis, "count": len(ordered),
                "releases": [{"tracing_no": r["tracing_no"],
                              "published_at": r["published_at"],
                              "audited": r["audited"],
                              "net_profit": net_profit(r["items"])}
                             for r in ordered],
                "first_tracing_no": first["tracing_no"] if first else None,
                "first_published_at": first["published_at"] if first else None,
                "latest_tracing_no": latest["tracing_no"] if latest else None,
                "first_net_profit": a, "latest_net_profit": b,
                "change": (b / a - 1) if a not in (None, 0) and b is not None else None,
            })
    out.sort(key=lambda r: (r["period_end"], r["period_months"]), reverse=True)
    return out


def revision_of(ins_code, period_end, period_months, consolidated, items,
                versions=None, threshold=MATERIAL_REVISION):
    """How the figure held now (`items`, the `metrics` row) differs from the
    period's first release on the same basis, or None when it does not
    materially (|change| <= threshold) or nothing earlier is stored.

        {first_net_profit, latest_net_profit, change, first_published_at,
         first_audited, releases}
    """

    groups = _by_period(_versions_for(ins_code, versions), consolidated)
    group = groups.get((str(period_end), int(period_months or 12)), [])
    if len(group) < 2:
        return None
    first = earliest(group)
    a, b = net_profit(first["items"]) if first else None, net_profit(items)
    if a in (None, 0) or b is None:
        return None
    change = b / a - 1
    if abs(change) <= threshold:
        return None
    return {"first_net_profit": a, "latest_net_profit": b, "change": change,
            "first_published_at": first["published_at"],
            "first_audited": first["audited"], "releases": len(group)}


# ---- the store, measured ------------------------------------------------------------

def _quantiles(values, qs=(0.10, 0.25, 0.50, 0.75, 0.90)):
    values = sorted(values)
    if not values:
        return {}
    out = {}
    for q in qs:
        pos = (len(values) - 1) * q
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        v = values[lo] + (values[hi] - values[lo]) * (pos - lo)
        out["q%02d" % int(q * 100)] = round(v, 4)
    return out


def statistics(versions_by_code=None):
    """What the store says about timing and revision.

      lags        days from period end to the first release, per period
                  length (3/6/9/12): quantiles, and how many came later
                  than 60/90/120 days;
      revisions   per period length, the periods with more than one
                  release on one basis, how many moved the net profit by
                  more than 1% (CHANGED) and 5% (MATERIAL_REVISION) between
                  the first release and the latest, and the size of the move;
      mixed       company-periods with releases on both bases.
    """

    versions_by_code = load_versions() if versions_by_code is None else versions_by_code
    lags = defaultdict(list)
    revs = defaultdict(lambda: {"periods": 0, "with_profit": 0, "changed": 0,
                                "material": 0, "sizes": [], "up": 0, "down": 0})
    periods = mixed = releases = 0
    for code, recs in versions_by_code.items():
        releases += len(recs)
        groups = _by_period(recs)
        for (end, months), group in groups.items():
            periods += 1
            if len({bool(r["consolidated"]) for r in group}) > 1:
                mixed += 1
            first = earliest(group)
            start = jalali.parse(end, default_time=(0, 0, 0))
            when = parse_iso(first["published_iso"]) if first else None
            if start and when:
                lags[months].append((when - start).total_seconds() / 86400.0)
        for basis in (False, True):
            for (end, months), group in _by_period(recs, basis).items():
                if len(group) < 2:
                    continue
                rec = revs[months]
                rec["periods"] += 1
                a = net_profit(earliest(group)["items"])
                b = net_profit(winner(group)["items"])
                if a in (None, 0) or b is None:
                    continue
                rec["with_profit"] += 1
                size = abs(b / a - 1)
                if size > CHANGED:
                    rec["changed"] += 1
                    rec["sizes"].append(size)
                    rec["up" if b > a else "down"] += 1
                if size > MATERIAL_REVISION:
                    rec["material"] += 1

    lag_out = {}
    for months in sorted(lags):
        values = lags[months]
        lag_out[str(months)] = dict(
            _quantiles(values), n=len(values),
            over_60=sum(1 for v in values if v > 60),
            over_90=sum(1 for v in values if v > 90),
            over_120=sum(1 for v in values if v > 120),
            negative=sum(1 for v in values if v < 0))
    rev_out = {}
    total = {"periods": 0, "with_profit": 0, "changed": 0, "material": 0, "sizes": []}
    for months in sorted(revs):
        rec = revs[months]
        for key in ("periods", "with_profit", "changed", "material"):
            total[key] += rec[key]
        total["sizes"].extend(rec["sizes"])
        rev_out[str(months)] = {
            "periods": rec["periods"], "with_profit": rec["with_profit"],
            "changed": rec["changed"], "material": rec["material"],
            "up": rec["up"], "down": rec["down"],
            "changed_share": (round(rec["changed"] / rec["with_profit"], 4)
                              if rec["with_profit"] else None),
            "size_when_changed": _quantiles(rec["sizes"], (0.25, 0.50, 0.75, 0.90))}
    rev_out["all"] = {
        "periods": total["periods"], "with_profit": total["with_profit"],
        "changed": total["changed"], "material": total["material"],
        "changed_share": (round(total["changed"] / total["with_profit"], 4)
                          if total["with_profit"] else None),
        "material_share": (round(total["material"] / total["with_profit"], 4)
                           if total["with_profit"] else None),
        "size_when_changed": _quantiles(total["sizes"], (0.25, 0.50, 0.75, 0.90))}
    return {"releases": releases, "companies": len(versions_by_code),
            "periods": periods, "mixed": mixed,
            "mixed_share": round(mixed / periods, 4) if periods else None,
            "lags": lag_out, "revisions": rev_out}


def main(argv=None):
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--code", default=None, help="one company's revisions")
    args = parser.parse_args(argv)

    if args.code:
        def bn(value):
            return "—" if value is None else "%.0f" % (value / 1e9)

        print("net profit in billion rials, first release -> latest:")
        for rev in revisions(args.code):
            print("%s %2dm %s  %d releases  first %s  latest %s  change %s" % (
                rev["period_end"], rev["period_months"],
                "cons" if rev["consolidated"] else "sep ", rev["count"],
                bn(rev["first_net_profit"]), bn(rev["latest_net_profit"]),
                "—" if rev["change"] is None else "%+.1f%%" % (rev["change"] * 100)))
        return 0

    s = statistics()
    print("releases %d, companies %d, company-periods %d; both bases in %d (%.1f%%)"
          % (s["releases"], s["companies"], s["periods"], s["mixed"],
             100 * (s["mixed_share"] or 0)))
    print("\ndays from period end to the first release:")
    for months, q in s["lags"].items():
        if q["n"] < 20:
            continue
        print("  %2sm n=%5d  10%% %4.0f  25%% %4.0f  median %4.0f  75%% %4.0f  "
              "90%% %4.0f   >60d %4d  >90d %4d  >120d %4d"
              % (months, q["n"], q["q10"], q["q25"], q["q50"], q["q75"], q["q90"],
                 q["over_60"], q["over_90"], q["over_120"]))
    print("\nnet profit, first release -> latest (periods with >1 release on one basis):")
    for months, r in s["revisions"].items():
        if r["with_profit"] < 20:
            continue
        size = r["size_when_changed"]
        print("  %3s  n=%5d  moved >1%%: %5d (%4.1f%%)  >5%%: %5d  size when moved: "
              "25%% %.1f%%  median %.1f%%  75%% %.1f%%  90%% %.1f%%"
              % (months, r["with_profit"], r["changed"], 100 * (r["changed_share"] or 0),
                 r["material"], 100 * size.get("q25", 0), 100 * size.get("q50", 0),
                 100 * size.get("q75", 0), 100 * size.get("q90", 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
