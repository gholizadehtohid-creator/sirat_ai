"""Where the public's money is going: individuals' (حقیقی) net buying of the
exchange-traded funds, by kind -- fixed income, equity, mixed, gold,
leveraged, real estate.

WHY THIS EXISTS
---------------
From the owner's walk through BourseView (2026-11-26): its macro data reads
"ورود حقیقی به صندوق‌های درآمد ثابت / طلا" -- whether money is leaving shares
for bonds or gold, or coming back. The bot read individuals' flow into
shares only (moneyflow.py), and no fund's flow was stored at all.

SOURCE AND STORE
----------------
TSETMC's per-instrument client-type history (the same request the equity
flow uses, GetClientTypeHistory: a fund's whole daily history, rial values
included), for every fund in the instruments table whose kind its name tells
(category()). Probed 2026-11-26: «اعتماد», a fixed-income fund, 2,756 days.

The rows go to their OWN table, fund_flow_history, never client_type_history:
that one is read wholesale as "the market's" retail flow (moneyflow, the
panel's market card), and a fund's flow summed into it would be counted as
money entering shares. It is the gold-fund bars lesson (the market turnover
that read funds as equities, 2026-08-24) applied before the fact.

update() runs after the equity flow in bootstrap's flow step, at most every
MIN_HOURS: one request per fund (about 160), through the provider's own
pacing, as the equity flow does for its ~870.

WHAT IT IS NOT
--------------
A description of where the public's money went, not a timing signal: the
bot's own test of individuals' flow as a predictor of the market (E21, E22)
found nothing it could trade.
"""

import time
from datetime import datetime, timezone

import database

MIN_HOURS = 6
SESSIONS = 60
WINDOWS = (1, 5, 20)

# (key, Persian label), in the order shown
CATEGORIES = (("fixed", "درآمد ثابت"), ("equity", "سهامی"), ("mixed", "مختلط"),
              ("gold", "طلا"), ("leveraged", "اهرمی"), ("real_estate", "املاک"),
              ("commodity", "کالایی"))
LABELS = dict(CATEGORIES)

FIELDS = database.CLIENT_TYPE_FIELDS


def category(name):
    """The kind of fund a name says, or None (opportunities.fund_sleeve
    first, then the kinds it leaves out)."""

    import opportunities

    name = (name or "").strip()
    sleeve = opportunities.fund_sleeve(name)
    if sleeve == "gold":
        return "gold"
    if sleeve == "fixed":
        return "fixed"
    if "اهرمی" in name:
        return "leveraged"
    if "مختلط" in name or name.endswith("-م"):
        return "mixed"
    if sleeve in ("equity", "equity_active") or "بخشی" in name:
        return "equity"
    if "املاک" in name or "مستغلات" in name:
        return "real_estate"
    if "کالا" in name or "زعفران" in name:
        return "commodity"
    return None


def ensure_table():
    conn = database._connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS fund_flow_history (
            ins_code TEXT NOT NULL, date TEXT NOT NULL, %s,
            PRIMARY KEY (ins_code, date))""" % ", ".join("%s REAL" % f for f in FIELDS))
        conn.execute("""CREATE TABLE IF NOT EXISTS fund_flow_runs (
            ran_at TEXT PRIMARY KEY, funds INTEGER, stored INTEGER, failed INTEGER)""")
        conn.commit()
    finally:
        conn.close()


def funds():
    """{ins_code: {"symbol", "name", "category"}} of the funds whose kind is told."""

    out = {}
    for inst in database.load_instruments(equities_only=False):
        if (inst.get("kind") or "") != "fund":
            continue
        kind = category(inst.get("name_fa"))
        if kind:
            out[str(inst["ins_code"])] = {"symbol": inst.get("symbol_fa"),
                                          "name": inst.get("name_fa"), "category": kind}
    return out


def save(ins_code, frame):
    """Upsert one fund's rows; the number written."""

    if frame is None or frame.empty:
        return 0
    import pandas as pd
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame = frame.dropna(subset=["date"])
    rows = [[str(ins_code), d] + [None if pd.isna(v) else float(v) for v in values]
            for d, *values in frame[["date"] + FIELDS].itertuples(index=False)]
    if not rows:
        return 0
    conn = database._connect()
    try:
        conn.executemany("INSERT OR REPLACE INTO fund_flow_history VALUES (%s)"
                         % ", ".join("?" * (2 + len(FIELDS))), rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def last_run():
    ensure_table()
    conn = database._connect()
    try:
        row = conn.execute("SELECT MAX(ran_at) FROM fund_flow_runs").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def update(provider=None, force=False, now=None):
    """Fetch every classified fund's flow history, at most every MIN_HOURS.
    A message, never an exception."""

    try:
        ensure_table()
        now = now or datetime.now(timezone.utc)
        before = last_run()
        if before and not force:
            age = (now - datetime.fromisoformat(before)).total_seconds() / 3600.0
            if age < MIN_HOURS:
                return "fund flows: current (%.1f h old)" % age
        codes = list(funds())
        if not codes:
            return "fund flows: no fund in the instruments table"
        if provider is None:
            from providers.tsetmc_cdn import TsetmcCdnProvider
            provider = TsetmcCdnProvider()
        results = provider.get_client_type_histories(codes)
        stored = failed = 0
        for code, result in (results or {}).items():
            if result is not None and result.ok:
                stored += save(code, result.data)
            else:
                failed += 1
        conn = database._connect()
        try:
            conn.execute("INSERT OR REPLACE INTO fund_flow_runs VALUES (?,?,?,?)",
                         (now.isoformat(timespec="seconds"), len(codes), stored, failed))
            conn.commit()
        finally:
            conn.close()
        return "fund flows: %d funds asked, %d rows stored, %d failed" % (
            len(codes), stored, failed)
    except Exception as exc:                                    # noqa: BLE001
        return "fund flows: failed (%s: %s)" % (type(exc).__name__, str(exc)[:160])


def summary(sessions=SESSIONS, kinds=None):
    """Individuals' net buying per fund kind: the newest session, the last 5
    and 20, and the daily series of the last `sessions`. None with nothing
    stored."""

    ensure_table()
    kinds = kinds if kinds is not None else funds()
    if not kinds:
        return None
    conn = database._connect()
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM fund_flow_history ORDER BY date DESC LIMIT ?",
            (int(sessions),))]
        if not dates:
            return None
        rows = conn.execute(
            "SELECT ins_code, date, individual_buy_value, individual_sell_value, "
            "institutional_buy_value FROM fund_flow_history WHERE date >= ?",
            (min(dates),)).fetchall()
    finally:
        conn.close()
    dates = sorted(dates)
    net = {k: {d: 0.0 for d in dates} for k, _ in CATEGORIES}
    traded = {k: {d: 0.0 for d in dates} for k, _ in CATEGORIES}
    seen = {k: set() for k, _ in CATEGORIES}
    for code, day, ibuy, isell, nbuy in rows:
        info = kinds.get(str(code))
        if not info or day not in net[info["category"]]:
            continue
        k = info["category"]
        net[k][day] += (ibuy or 0.0) - (isell or 0.0)
        traded[k][day] += (ibuy or 0.0) + (nbuy or 0.0)
        seen[k].add(str(code))
    out = []
    for key, label in CATEGORIES:
        if not seen[key]:
            continue
        series = [net[key][d] for d in dates]
        item = {"key": key, "label": label, "funds": len(seen[key]),
                "series": [{"date": d, "net": round(net[key][d])} for d in dates]}
        for w in WINDOWS:
            item["net_%d" % w] = round(sum(series[-w:]))
            item["traded_%d" % w] = round(sum(traded[key][d] for d in dates[-w:]))
        out.append(item)
    return {"as_of": dates[-1], "sessions": len(dates), "categories": out,
            "funds_classified": len(kinds), "last_run": last_run()}


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    print(update(force="--force" in argv))
    s = summary()
    for c in (s or {}).get("categories", []):
        print("  %-11s %3d funds  last %8.0f bn  5d %9.0f bn  20d %9.0f bn" % (
            c["key"], c["funds"], c["net_1"] / 1e9, c["net_5"] / 1e9, c["net_20"] / 1e9))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
