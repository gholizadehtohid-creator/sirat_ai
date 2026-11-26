"""A company's real exposure to the dollar, read from its own filings: the
currency position and the imported raw materials its board reports every
period in the management report (گزارش تفسیری) beside the statements.

WHY THIS EXISTS
---------------
The owner's walk through BourseView (2026-11-26) showed a currency page per
company -- currency assets and liabilities, receipts and payments -- and the
dollar scenarios in fundamental_outlook rest on a regression of sales on the
NIMA rate whose R^2 is 0.02-0.15 in every industry (outlook_calibration). The
owner approved reading the real exposure instead, if Codal carries it.

It does, in the file the bot already downloads: Codal's statement export
holds five pages of «خلاصه اطلاعات گزارش تفسیری» after the statements, and
in them, per currency and for three columns (the last fiscal year, the
filing's own period, the board's estimate for the rest of the year):
  منابع ارزی طی دوره        currency received in the period (mostly exports)
  مصارف ارزی طی دوره        currency paid (mostly imports)
  داراییهای ارزی پایان دوره  currency held or owed TO the company at the end
  بدهی های ارزی پایان دوره   currency the company owes at the end
each as a rate, a currency amount and a rial amount (million rials); and the
raw materials bought and used, «مواد اولیه داخلی» and «مواد اولیه وارداتی».
Present in about 60% of 1400-1405 filings (manufacturers; not banks).

Read from data/codal_raw, the archive of every downloaded filing: no request
to Codal. update() parses the archived filings not yet read, from bootstrap's
post-run hook.

WHAT IT GIVES
-------------
  receipts_share   currency received in the period / the period's revenue:
                   roughly the export share (receipts include any currency
                   loan drawn, so it is an upper bound)
  payments_share   currency paid / revenue
  net_position     currency assets less liabilities at the period's end, in
                   rials: what a move in the rate revalues at once
  imported_share   imported raw materials used / all raw materials used
"""

import gzip
import os
import re
from datetime import datetime, timezone

import codal
import fundamental_db

REPORT = "گزارش تفسیری"
ROWS = (("منابع", "receipts"), ("مصارف", "payments"),
        ("دارایی", "assets"), ("بدهی", "liabilities"))
MILLION = 1_000_000.0
FIRST_PERIOD = "1399/01/01"
_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _num(value):
    """A figure as written in the report: Persian digits, thousands commas,
    parentheses for a negative. None for blank or not a number."""

    text = _text(value).translate(_FA).replace(",", "").replace("٬", "")
    if not text or text.lower() == "nan":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("−", "-")
    try:
        number = float(text)
    except ValueError:
        return None
    return -abs(number) if negative else number


def _period(label):
    """(end 'YYYY/MM/DD', months, estimate) from a column header such as
    «دوره ۶ ماهه منتهي به ۱۴۰۵/۰۶/۳۱» or «برآورد دوره ...», or None."""

    text = _text(label).translate(_FA)
    found = _DATE.search(text)
    if not found:
        return None
    end = "%s/%02d/%02d" % (found.group(1), int(found.group(2)), int(found.group(3)))
    estimate = "برآورد" in text
    months = 12
    m = re.search(r"(\d{1,2})\s*ماهه", text)
    if m:
        months = int(m.group(1))
    return end, months, estimate


def report_frames(text):
    """The management report's tables, in order, from a filing's HTML."""

    try:
        sheets = codal.split_sheets(text)
    except Exception:                                           # noqa: BLE001
        return []
    frames = []
    for name, tables in (sheets or {}).items():
        if REPORT in name:
            frames.extend(t for t in tables if hasattr(t, "columns"))
    return frames


def _first_column(frame):
    return [_text(v) for v in frame.iloc[:, 0].tolist()]


def currency_table(frames):
    """{(end, months, estimate): {"receipts", "payments", "assets",
    "liabilities"}} in rials, summed over currencies, or {}."""

    for frame in frames:
        first = _first_column(frame)
        if not any("ارزی" in x and ("منابع" in x or "دارایی" in x) for x in first):
            continue
        columns = list(frame.columns)
        groups = {}
        for i, col in enumerate(columns):
            levels = col if isinstance(col, tuple) else (col,)
            period = next((p for p in map(_period, levels) if p), None)
            if period and any("ريالي" in _text(l) or "ریالی" in _text(l) for l in levels):
                groups[period] = i
        if not groups:
            continue
        out = {p: {"receipts": 0.0, "payments": 0.0, "assets": 0.0, "liabilities": 0.0}
               for p in groups}
        seen = False
        for row in range(len(frame)):
            label = first[row]
            key = next((k for word, k in ROWS if label.startswith(word)), None)
            if not key:
                continue
            for period, col in groups.items():
                value = _num(frame.iat[row, col])
                if value is not None:
                    out[period][key] += abs(value) * MILLION
                    seen = True
        if seen:
            return out
    return {}


def materials_table(frames):
    """{(end, months, estimate): {"domestic", "imported"}}: the raw materials
    USED in the period (rials), from «جمع مواد اولیه داخلی / وارداتی»."""

    out = {}
    for frame in frames:
        first = _first_column(frame)
        if not any("جمع مواد اولیه" in x for x in first):
            continue
        columns = list(frame.columns)
        period = None
        used = None
        for i, col in enumerate(columns):
            levels = col if isinstance(col, tuple) else (col,)
            period = period or next((p for p in map(_period, levels) if p), None)
            texts = [_text(l) for l in levels]
            if used is None and any(t == "مصرف" for t in texts) and \
                    any("مبلغ" in t for t in texts):
                used = i
        if period is None or used is None:
            continue
        row_of = {k: i for i, k in enumerate(first)}
        domestic = imported = None
        for label, row in row_of.items():
            if label.startswith("جمع مواد اولیه داخلی"):
                domestic = _num(frame.iat[row, used])
            elif label.startswith("جمع مواد اولیه وارداتی"):
                imported = _num(frame.iat[row, used])
        if domestic is not None or imported is not None:
            out[period] = {"domestic": abs(domestic or 0.0) * MILLION,
                           "imported": abs(imported or 0.0) * MILLION}
    return out


def read(text, period_end, period_months):
    """The filing's own period, from its management report: a dict, or None
    when the report carries neither table."""

    frames = report_frames(text)
    if not frames:
        return None
    currency = currency_table(frames)
    materials = materials_table(frames)

    def own(table):
        for (end, months, estimate), values in table.items():
            if not estimate and end[:7] == str(period_end)[:7] and \
                    int(months) == int(period_months):
                return values
        return None

    def estimate(table):
        for (end, months, est), values in table.items():
            if est:
                return dict(values, end=end, months=months)
        return None

    cur, mat = own(currency), own(materials)
    if cur is None and mat is None:
        return None
    return {"currency": cur, "materials": mat,
            "currency_estimate": estimate(currency)}


# ---- the store --------------------------------------------------------------

def ensure_table():
    conn = fundamental_db._connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS fx_exposure (
            tracing_no TEXT PRIMARY KEY, ins_code TEXT NOT NULL,
            period_end TEXT, period_months INTEGER, published_at TEXT,
            receipts REAL, payments REAL, assets REAL, liabilities REAL,
            materials_domestic REAL, materials_imported REAL,
            est_end TEXT, est_months INTEGER, est_receipts REAL, est_payments REAL,
            found INTEGER, parsed_at TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fx_exposure_company "
                     "ON fx_exposure (ins_code, period_end)")
        conn.commit()
    finally:
        conn.close()


def _archived(tracing_no):
    path = codal._archive_path(tracing_no)
    if not os.path.exists(path):
        return None
    try:
        body = gzip.open(path).read()
    except OSError:
        return None
    if body[:8] == codal.XLS_MAGIC:
        return None                          # the old .xls exports carry no report
    return body.decode("utf-8", "replace")


def update(limit=None, verbose=False):
    """Read the management reports of the archived filings not yet read.
    Local only. A message, never an exception."""

    import config
    import sqlite3
    try:
        ensure_table()
        conn = fundamental_db._connect()
        try:
            done = {r[0] for r in conn.execute("SELECT tracing_no FROM fx_exposure")}
        finally:
            conn.close()
        market = sqlite3.connect(config.DATABASE_NAME, timeout=30)
        try:
            filings = market.execute(
                "SELECT tracing_no, ins_code, period_end, period_months, published_at "
                "FROM codal_filings ORDER BY published_at DESC").fetchall()
        finally:
            market.close()
        # The management report summary entered Codal's export around 1399;
        # nothing before it carries one (a 600-filing sample, 2026-11-26).
        todo = [f for f in filings if str(f[0]) not in done
                and str(f[2] or "") >= FIRST_PERIOD]
        if limit:
            todo = todo[:limit]
        rows, found = [], 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for tracing, code, end, months, published in todo:
            text = _archived(tracing)
            if text is None:
                continue
            got = read(text, end, months or 12) if REPORT in text else None
            cur = (got or {}).get("currency") or {}
            mat = (got or {}).get("materials") or {}
            est = (got or {}).get("currency_estimate") or {}
            found += 1 if got else 0
            rows.append((str(tracing), str(code), end, months, published,
                         cur.get("receipts"), cur.get("payments"), cur.get("assets"),
                         cur.get("liabilities"), mat.get("domestic"), mat.get("imported"),
                         est.get("end"), est.get("months"), est.get("receipts"),
                         est.get("payments"), 1 if got else 0, now))
        if rows:
            conn = fundamental_db._connect()
            try:
                conn.executemany("INSERT OR REPLACE INTO fx_exposure VALUES (%s)"
                                 % ",".join("?" * 17), rows)
                conn.commit()
            finally:
                conn.close()
        return "fx exposure: %d archived filings read, %d with a management report" % (
            len(rows), found)
    except Exception as exc:                                    # noqa: BLE001
        return "fx exposure: failed (%s: %s)" % (type(exc).__name__, str(exc)[:160])


def latest(ins_code):
    """The newest period read for a company, or None."""

    ensure_table()
    conn = fundamental_db._connect()
    try:
        row = conn.execute(
            "SELECT period_end, period_months, receipts, payments, assets, liabilities, "
            "materials_domestic, materials_imported, est_end, est_months, est_receipts, "
            "est_payments, tracing_no FROM fx_exposure WHERE ins_code = ? AND found = 1 "
            "AND (receipts IS NOT NULL OR materials_imported IS NOT NULL) "
            "ORDER BY period_end DESC, period_months DESC LIMIT 1", (str(ins_code),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    keys = ("period_end", "period_months", "receipts", "payments", "assets", "liabilities",
            "materials_domestic", "materials_imported", "est_end", "est_months",
            "est_receipts", "est_payments", "tracing_no")
    return dict(zip(keys, row))


def exposure(ins_code, revenue_for=None, shares=None):
    """latest() turned into shares of the period's revenue and a per-share
    revaluation for a 10% move. `revenue_for(period_end, months)` gives the
    statement's revenue of that period (rials)."""

    row = latest(ins_code)
    if not row:
        return None
    revenue = revenue_for(row["period_end"], row["period_months"]) if revenue_for else None
    out = dict(row)
    out["receipts_share"] = (row["receipts"] / revenue
                             if row["receipts"] is not None and revenue and revenue > 0 else None)
    out["payments_share"] = (row["payments"] / revenue
                             if row["payments"] is not None and revenue and revenue > 0 else None)
    out["net_position"] = (row["assets"] - row["liabilities"]
                           if row["assets"] is not None and row["liabilities"] is not None
                           else None)
    used = (row["materials_domestic"] or 0.0) + (row["materials_imported"] or 0.0)
    out["imported_share"] = (row["materials_imported"] / used
                             if row["materials_imported"] is not None and used > 0 else None)
    out["per_share_10pct"] = (out["net_position"] * 0.10 / shares
                              if out["net_position"] is not None and shares else None)
    return out


def statement_revenue(ins_code, versions=None):
    """revenue_for(period_end, months) for exposure(): the period's revenue
    on the separate (parent's) statement the management report speaks for,
    else on whichever basis is stored. Reads the store's releases once."""

    import pit_fundamentals
    if versions is None:
        versions = pit_fundamentals.load_versions(ins_code).get(str(ins_code), [])

    def revenue_for(period_end, months):
        same = [r for r in versions if str(r.get("period_end"))[:7] == str(period_end)[:7]
                and int(r.get("period_months") or 12) == int(months)]
        for basis in (False, True, None):
            pool = [r for r in same if basis is None or r.get("consolidated") == basis]
            held = pit_fundamentals.winner(pool) if pool else None
            value = (held or {}).get("items", {}).get("revenue")
            if value:
                return float(value)
        return None

    return revenue_for


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    limit = None
    for a in argv:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
    print(update(limit=limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
