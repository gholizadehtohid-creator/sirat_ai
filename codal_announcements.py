"""Codal announcements a manual trader acts on, and the dividend per share.

    python codal_announcements.py --report       what is stored, by kind

The quarter-hour -- now two-hourly -- feed watch (codal_watch.py) already
reads every letter Codal publishes and kept only statements (ن-10) and
monthly reports (ن-30). The same feed carries the letters that move a
price on the day: an AGM's dividend, a capital increase, a halt, a
material disclosure. This module is what the watch hands those to.

THE KINDS (letter codes surveyed from ~800 letters of the live feed,
2026-11-06; normalised digits, as persian.normalize stores them)
---------------------------------------------------------------------
  agm        AGM notices and decisions, dividend payment schedules
  capital    capital-increase proposals, decisions, subscriptions, registration
  material   material disclosures, rumour and price-swing clarifications,
             investor conferences
  halt       symbol halts and suspensions issued by the exchange
  board      board / CEO changes

DIVIDEND PER SHARE
------------------
ن-52 is the annual AGM's decisions. Its page (codal.ir/Reports/Decision.aspx)
is a server-rendered form whose rows carry a hidden PropertyName cell --
`DividendPerShare`, `ListedCapital` -- and the value in an input's value=
attribute, e.g. فولاد FY1404: DPS 55 rials, capital 1,935,000,000 (million
rials). The machine name is what is matched, never the Persian label.
An amendment (اصلاحیه) is published later and replaces the original for the
same fiscal year.
"""

import argparse
import re
import sys
from datetime import datetime, timezone

import codal
import fundamental_db
import persian

KINDS = {
    "agm": ("ن-50", "ن-51", "ن-52", "ن-53", "ن-54", "ن-55", "ن-58",
            "ن-13", "ن-85"),
    "capital": ("ن-56", "ن-57", "ن-60", "ن-61", "ن-62", "ن-65", "ن-66",
                "ن-67", "ن-71"),
    "material": ("ن-20", "ن-21", "ن-22", "ن-23"),
    "halt": ("س-4", "س-5", "س-7", "س-12", "ف ب-13"),
    "board": ("ن-45",),
}

KIND_LABELS = {
    "agm": "مجمع و سود",
    "capital": "افزایش سرمایه",
    "material": "اطلاعات بااهمیت",
    "halt": "توقف و تعلیق نماد",
    "board": "تغییر مدیریت",
}

ANNUAL_AGM_DECISIONS = "ن-52"

KIND_OF = {persian.normalize(code): kind
           for kind, codes in KINDS.items() for code in codes}
CODES = tuple(KIND_OF)

CODAL_SITE = "https://codal.ir"

_FISCAL_END = re.compile(r"منتهی به\s*(\d{4}/\d{2}/\d{2})")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind_of(letter_code):
    return KIND_OF.get(persian.normalize(letter_code or ""))


def ensure_tables():
    fundamental_db.create_database()
    conn = fundamental_db._connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS announcements (
            tracing_no TEXT PRIMARY KEY, ins_code TEXT, symbol TEXT,
            letter_code TEXT, kind TEXT, title TEXT, published_at TEXT,
            url TEXT, seen_at TEXT);
        CREATE INDEX IF NOT EXISTS announcements_published
            ON announcements (published_at);
        CREATE TABLE IF NOT EXISTS dividends (
            ins_code TEXT, fiscal_year_end TEXT, dps REAL, capital REAL,
            tracing_no TEXT, published_at TEXT, stored_at TEXT,
            PRIMARY KEY (ins_code, fiscal_year_end));
    """)
    conn.commit()
    conn.close()


# ==============================
# PARSING
# ==============================

def _property(html, name):
    """The value= of the input in the row whose PropertyName cell is `name`."""

    match = re.search(r'PropertyName">\s*%s\s*</td>.*?<input[^>]*?value="([^"]*)"'
                      % re.escape(name), html, re.S)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_dividend(html):
    """{'dps', 'capital'} from an annual-AGM decisions page, or None."""

    dps = _property(html, "DividendPerShare")
    if dps is None:
        return None
    return {"dps": dps, "capital": _property(html, "ListedCapital")}


def fiscal_year_end(title):
    match = _FISCAL_END.search(persian.normalize(title or ""))
    return match.group(1) if match else None


# ==============================
# STORE
# ==============================

def _letter_url(letter):
    url = letter.get("url") or ""
    return CODAL_SITE + url if url.startswith("/") else url


def store_announcement(letter, ins_code, kind):
    conn = fundamental_db._connect()
    conn.execute("INSERT OR REPLACE INTO announcements VALUES (?,?,?,?,?,?,?,?,?)",
                 (letter["tracing_no"], ins_code, letter.get("symbol"),
                  persian.normalize(letter.get("letter_code") or ""), kind,
                  letter.get("title"), letter.get("published_at"),
                  _letter_url(letter), _now()))
    conn.commit()
    conn.close()


def store_dividend(ins_code, fiscal_end, parsed, letter):
    """Keep the latest-published decision per fiscal year (amendments win)."""

    conn = fundamental_db._connect()
    row = conn.execute("SELECT published_at FROM dividends WHERE ins_code=? "
                       "AND fiscal_year_end=?", (ins_code, fiscal_end)).fetchone()
    published = letter.get("published_at") or ""
    if row is None or (row[0] or "") <= published:
        conn.execute("INSERT OR REPLACE INTO dividends VALUES (?,?,?,?,?,?,?)",
                     (ins_code, fiscal_end, parsed["dps"], parsed.get("capital"),
                      letter["tracing_no"], published, _now()))
        conn.commit()
    conn.close()


def fetch_dividend(client, letter):
    """Parse DPS from the letter's page; archived so it is fetched once."""

    key = "agm-%s" % letter["tracing_no"]
    body = codal.archive_read(key)
    if body is None:
        url = _letter_url(letter)
        if not url:
            return None, "no page"
        try:
            body = client._get(url).content
        except Exception as exc:                                # noqa: BLE001
            return None, "%s: %s" % (type(exc).__name__, str(exc)[:80])
        if b"DividendPerShare" in body:
            codal.archive_write(key, body)
    parsed = parse_dividend(body.decode("utf-8", errors="replace"))
    return parsed, None if parsed else "no DividendPerShare row"


def handle(client, letter, inst):
    """codal_watch handler: store the announcement, and the DPS if it has one.

    Returns the watch outcome; None means "try again next poll" (a transient
    failure fetching an AGM page).
    """

    ensure_tables()
    code = str(inst["ins_code"])
    kind = kind_of(letter.get("letter_code"))
    store_announcement(letter, code, kind)

    if persian.normalize(letter.get("letter_code") or "") != \
            persian.normalize(ANNUAL_AGM_DECISIONS):
        return "stored"

    fiscal_end = fiscal_year_end(letter.get("title"))
    parsed, error = fetch_dividend(client, letter)
    if parsed and fiscal_end:
        store_dividend(code, fiscal_end, parsed, letter)
        return "stored (DPS %.0f)" % parsed["dps"]
    if error and ("Timeout" in error or "Connection" in error or "429" in error):
        return None
    return "stored (no DPS: %s)" % (error or "no fiscal year in title")


# ==============================
# READ SIDE
# ==============================

def recent(days=3, kind=None, ins_code=None, limit=200):
    """Newest announcements first, within `days` (Jalali publication dates)."""

    import sqlite3
    from datetime import date, timedelta

    import jalali

    ensure_tables()
    sql = "SELECT * FROM announcements"
    where, args = [], []
    if days:
        cutoff = jalali.today_jalali(date.today() - timedelta(days=days))
        where.append("published_at >= ?")
        args.append("%04d/%02d/%02d" % cutoff)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if ins_code:
        where.append("ins_code = ?")
        args.append(str(ins_code))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY published_at DESC LIMIT ?"
    args.append(limit)

    conn = fundamental_db._connect()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    for row in rows:
        row["kind_label"] = KIND_LABELS.get(row["kind"], row["kind"])
    return rows


def latest_dividends():
    """{ins_code: newest fiscal year's {'fiscal_year_end', 'dps', ...}}."""

    ensure_tables()
    conn = fundamental_db._connect()
    out = {}
    for code, fy, dps, capital, tracing, published in conn.execute(
            "SELECT ins_code, fiscal_year_end, dps, capital, tracing_no, "
            "published_at FROM dividends ORDER BY fiscal_year_end"):
        out[code] = {"fiscal_year_end": fy, "dps": dps, "capital": capital,
                     "tracing_no": tracing, "published_at": published}
    conn.close()
    return out


def report():
    ensure_tables()
    conn = fundamental_db._connect()
    for kind, n, newest in conn.execute(
            "SELECT kind, COUNT(*), MAX(published_at) FROM announcements "
            "GROUP BY kind ORDER BY 2 DESC"):
        print("  %-10s %6d  newest %s" % (kind, n, newest))
    n, companies = conn.execute("SELECT COUNT(*), COUNT(DISTINCT ins_code) "
                                "FROM dividends").fetchone()
    print("  dividends  %6d rows, %d companies" % (n, companies))
    conn.close()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true")
    parser.parse_args(argv)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
