"""Codal announcements a manual trader acts on, and the dividend per share.

    python codal_announcements.py --report       what is stored, by kind
    python codal_announcements.py --backfill-dps [--limit N]
                                                 last annual AGM's DPS, one
                                                 search per equity lacking it
    python codal_announcements.py --repair-stale-dps [--apply]
                                                 re-open companies holding an
                                                 older year's DPS (dry run)

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


def _fetch_dividend(client, letter):
    """(parsed, error, kind) for one annual-AGM page; kind is codal's KIND_*.

    Only a decision form without the row is a settled "no DPS". A reply
    that is not the form at all -- no PropertyName cell anywhere, as in a
    firewall or throttle page served with HTTP 200 -- and a 5xx are
    transient. Both used to settle: غچین's 1613971 was stored as "no
    DividendPerShare row" at 10:07Z on 2026-11-06, and the same page gave
    DPS 130 at 15:24Z. A genuine form carries ~20 PropertyName rows (all
    695 archived pages, 110-330 KB).
    """

    key = "agm-%s" % letter["tracing_no"]
    body = codal.archive_read(key)
    if body is None:
        url = _letter_url(letter)
        if not url:
            return None, "no page", codal.KIND_BAD
        try:
            body = client._get(url).content
        except Exception as exc:                                # noqa: BLE001
            return (None, "%s: %s" % (type(exc).__name__, str(exc)[:80]),
                    codal.classify(exc))
        if b"DividendPerShare" in body:
            codal.archive_write(key, body)
    html = body.decode("utf-8", errors="replace")
    parsed = parse_dividend(html)
    if parsed:
        return parsed, None, codal.KIND_OK
    if "PropertyName" not in html:
        return (None, "not a decision page (%d bytes)" % len(body),
                codal.KIND_TRANSIENT)
    return None, "no DividendPerShare row", codal.KIND_BAD


def fetch_dividend(client, letter):
    """Parse DPS from the letter's page; archived so it is fetched once."""

    parsed, error, _kind = _fetch_dividend(client, letter)
    return parsed, error


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
    parsed, error, kind = _fetch_dividend(client, letter)
    if parsed and fiscal_end:
        store_dividend(code, fiscal_end, parsed, letter)
        return "stored (DPS %.0f)" % parsed["dps"]
    # By codal.classify, not by sniffing the message: "Timeout",
    # "Connection" and "429" let a 500 through as settled.
    if kind == codal.KIND_TRANSIENT:
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


# ==============================
# DIVIDEND BACKFILL -- one search per company
# ==============================
# The feed watch only sees AGMs from the day it started. Walking the feed
# back to the AGM season is the wrong tool: the market feed carries ~14
# pages a day, so 333 pages reached back 23 days (2026-11-06) and the
# season (Farvardin-Mordad) is ~3,000 pages away. One Symbol + LetterCode
# search per company returns its annual-AGM letters directly.

BACKFILL_GAP_SECONDS = 8          # between companies; the search host throttles


def _agm_letters(client, symbol):
    import config

    params = dict(client.SEARCH_DEFAULTS)
    params.update({"Symbol": symbol, "LetterCode": ANNUAL_AGM_DECISIONS,
                   "PageNumber": "1"})
    payload = client._get(config.CODAL_SEARCH_URL, params=params).json()
    return [dict(codal._letter_row(raw, symbol), symbol=symbol,
                 letter_code=ANNUAL_AGM_DECISIONS)
            for raw in payload.get("Letters") or []]


def backfill_dividends(limit=None, verbose=True, client=None, hours=6.0):
    """The newest annual AGM's DPS for every equity that has none stored.

    Newest-published letter first: for its fiscal year it is the amendment
    if there is one. A refused search skips that company (the next run
    retries it) and is paced by codal_monthly.Pacer: three refusals in a
    row wait 15 minutes, doubling, because retrying inside Codal's throttle
    window renews it. The first version stopped at the first refusal and
    covered 5 companies on a day the search host dropped connections.
    """

    import time

    import codal_monthly
    import database

    ensure_tables()
    client = client or codal.CodalClient()
    pace = codal_monthly.Pacer("dps", verbose)
    deadline = time.monotonic() + hours * 3600
    have = latest_dividends()
    halted = database.halted_codes()
    todo = [r for r in database.load_instruments(equities_only=True)
            if str(r["ins_code"]) not in have and str(r["ins_code"]) not in halted]
    if limit:
        todo = todo[:limit]

    stored = searched = refused = 0
    stopped = False
    for inst in todo:
        if time.monotonic() > deadline:
            stopped = True
            break
        symbol = inst.get("symbol_raw") or inst.get("symbol_fa")
        if not symbol:
            continue
        if searched:
            time.sleep(BACKFILL_GAP_SECONDS)
        searched += 1
        try:
            letters = _agm_letters(client, symbol)
        except Exception as exc:                                # noqa: BLE001
            refused += 1
            if verbose:
                print("dps: search refused at %s (%s)" % (
                    symbol, type(exc).__name__), flush=True)
            pace(True)
            continue
        pace(False)
        # The newest FISCAL YEAR's letters only (an amendment, then the
        # original), not simply the newest-published letter. Falling
        # through to an older year after a "no DPS", or taking an older
        # year's late amendment (دفرا: FY1403's, published after FY1404's
        # decision), stored that year's DPS as the company's latest -- and
        # a company with a DPS is never searched again: 17 showed a
        # dividend one to three years old next to a newer decision on file
        # (2026-11-11). With none for the newest year it stays due.
        newest = max((fiscal_year_end(letter.get("title")) or ""
                      for letter in letters), default="")
        for letter in letters:
            if not letter.get("tracing_no"):
                continue
            if (fiscal_year_end(letter.get("title")) or "") != newest:
                continue
            outcome = handle(client, letter, inst)
            if outcome is None:                 # the page itself was refused
                refused += 1
                pace(True)
                break
            if outcome.startswith("stored (DPS"):
                stored += 1
                if verbose:
                    print("  + %s %s" % (symbol, outcome), flush=True)
                break                       # newest fiscal year is enough
    if verbose:
        print("dps: %d companies searched, %d dividends stored, %d refused"
              % (searched, stored, refused), flush=True)
    return {"searched": searched, "stored": stored, "refused": refused,
            "stopped": stopped}


def stale_dividends():
    """{ins_code: (newest decision's fiscal year, [stored rows])} for every
    company whose stored DPS is for an older fiscal year than an annual-AGM
    decision on file -- the older-year fall-through backfill_dividends no
    longer does. Decisions on file: the announcements table, and the letter
    index the per-company sync fills (sirat_market.db)."""

    import database

    ensure_tables()
    code = persian.normalize(ANNUAL_AGM_DECISIONS)
    conn = fundamental_db._connect()
    titles = conn.execute("SELECT ins_code, title FROM announcements "
                          "WHERE letter_code = ?", (code,)).fetchall()
    stored = conn.execute("SELECT ins_code, fiscal_year_end, dps, tracing_no "
                          "FROM dividends").fetchall()
    conn.close()

    database.create_database()
    mconn = database._connect()
    titles += mconn.execute("SELECT ins_code, title FROM codal_letters "
                            "WHERE letter_code = ?", (code,)).fetchall()
    mconn.close()

    decided = {}
    for ins_code, title in titles:
        year = fiscal_year_end(title)
        if year and year > decided.get(str(ins_code), ""):
            decided[str(ins_code)] = year

    rows = {}
    for row in stored:
        rows.setdefault(str(row[0]), []).append(row)

    return {ins_code: (decided[ins_code], held)
            for ins_code, held in rows.items()
            if ins_code in decided
            and max(r[1] or "" for r in held) < decided[ins_code]}


def reopen_stale_dividends(apply=False, verbose=True):
    """Make the stale_dividends() companies due again. Dry run unless `apply`.

    A company with any DPS stored is never searched again, so the older
    year's rows are deleted; the next `--backfill-dps` run then reads only
    the newest year's decision. Idempotent.
    """

    import database

    stale = stale_dividends()
    names = {str(r["ins_code"]): r.get("symbol_fa")
             for r in database.load_instruments(equities_only=False)}
    rows = sum(len(held) for _year, held in stale.values())

    if verbose:
        print("dps: %d companies hold an older fiscal year's DPS than an "
              "annual-AGM decision on file (%d rows)%s"
              % (len(stale), rows, "" if apply else " -- dry run, nothing "
                 "written; --apply deletes them"), flush=True)
        for ins_code, (year, held) in sorted(stale.items()):
            newest_held = max(held, key=lambda r: r[1] or "")
            print("  %-10s %s  stored FY %s DPS %s  | newest decision FY %s"
                  % (names.get(ins_code) or "?", ins_code, newest_held[1],
                     newest_held[2], year), flush=True)

    if apply and stale:
        conn = fundamental_db._connect()
        for ins_code, (year, _held) in stale.items():
            conn.execute("DELETE FROM dividends WHERE ins_code = ? AND "
                         "fiscal_year_end < ?", (ins_code, year))
        conn.commit()
        conn.close()
        if verbose:
            print("dps: deleted; now run  python codal_announcements.py "
                  "--backfill-dps", flush=True)

    return {"companies": len(stale), "rows": rows, "applied": bool(apply)}


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
    parser.add_argument("--backfill-dps", action="store_true",
                        help="search each equity's last annual AGM for its DPS")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--repair-stale-dps", action="store_true",
                        help="list companies whose DPS is for an older fiscal "
                             "year than an AGM decision on file (dry run)")
    parser.add_argument("--apply", action="store_true",
                        help="with --repair-stale-dps: delete those rows so "
                             "the next --backfill-dps reads the newest year")
    args = parser.parse_args(argv)
    if args.repair_stale_dps:
        reopen_stale_dividends(apply=args.apply)
        return 0
    if args.backfill_dps:
        backfill_dividends(limit=args.limit, hours=args.hours)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
