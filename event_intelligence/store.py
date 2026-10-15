"""Persistence for the intelligence report -- deliberately its own tables.

WHY SEPARATE TABLES, AND WHY HERE INSTEAD OF database.create_database()
----------------------------------------------------------------------
News is a report, never a signal (see the package docstring). Keeping its two
tables out of the core schema is the physical expression of that rule: nothing
in the trading path (`market_data`, `price_history`, `fundamentals`, the paper
book) can join to news, because news lives in tables the core schema does not
know about. The separation is the safeguard.

The connection helper `database._connect()` is reused (codal_backfill.py already
does), so we inherit the same WAL / busy-timeout settings, but the DDL is owned
here and created lazily by ensure_schema() -- no edit to database.py.

WHAT IS STORED
--------------
- news_sources: one row per feed, with its last fetch outcome (health).
- news_articles: one row per article, keyed by a stable `uid` so re-fetching the
  same feed updates rather than duplicates. Classification (categories, markets,
  tone, salience) is stored alongside so the report can query by market, and an
  `event_key` groups near-duplicate stories from different outlets into one
  event. `published_at` is UTC-ISO or NULL -- an undated item is never given a
  guessed time.
"""

import hashlib
import sqlite3

import database


TAXONOMY_VERSION = "1.0"


def ensure_schema():
    """Create the two intelligence tables if absent. Cheap; safe to call often."""

    conn = database._connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS news_sources (
        name            TEXT PRIMARY KEY,
        url             TEXT NOT NULL,
        tier            INTEGER,
        lang            TEXT,
        kind            TEXT,
        markets_hint    TEXT,
        last_fetched_at TEXT,
        last_status     TEXT,
        last_items      INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS news_articles (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        uid           TEXT NOT NULL UNIQUE,
        source        TEXT NOT NULL,
        tier          INTEGER,
        lang          TEXT,
        title         TEXT NOT NULL,
        summary       TEXT,
        link          TEXT,
        published_at  TEXT,
        published_raw TEXT,
        fetched_at    TEXT NOT NULL,
        categories    TEXT,
        markets       TEXT,
        tone          TEXT,
        salience      REAL,
        event_key     TEXT,
        taxonomy_ver  TEXT,
        is_economic   INTEGER DEFAULT 0,
        is_analysis   INTEGER DEFAULT 0,
        is_price_forecast INTEGER DEFAULT 0
    )
    """)

    # Migration for a table created before these columns existed. ALTER ADD
    # COLUMN is a no-op-if-present only via try/except -- SQLite has no
    # "ADD COLUMN IF NOT EXISTS".
    for col in ("is_economic", "is_analysis", "is_price_forecast"):
        try:
            cur.execute(
                f"ALTER TABLE news_articles ADD COLUMN {col} INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass   # already there

    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_pub "
                "ON news_articles (published_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_source "
                "ON news_articles (source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_event "
                "ON news_articles (event_key)")

    conn.commit()
    conn.close()


def make_uid(source, guid, link, title):
    """A stable identity for an article, so a re-fetch updates in place.

    Prefer the feed's own guid, then the link, then a hash of source+title.
    Everything is folded into one sha1 so the column is fixed-width and the
    UNIQUE constraint does the deduplication of an identical re-fetch for free.
    """

    basis = (guid or link or title or "").strip()
    raw = f"{source}\x1f{basis}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()


def upsert_articles(rows):
    """Insert or update classified articles. `rows` are dicts. Returns counts."""

    if not rows:
        return {"inserted": 0, "updated": 0}

    ensure_schema()
    conn = database._connect()
    cur = conn.cursor()

    inserted = updated = 0
    for r in rows:
        existed = cur.execute(
            "SELECT 1 FROM news_articles WHERE uid = ?", (r["uid"],)
        ).fetchone()

        cur.execute("""
        INSERT INTO news_articles (
            uid, source, tier, lang, title, summary, link,
            published_at, published_raw, fetched_at,
            categories, markets, tone, salience, event_key, taxonomy_ver,
            is_economic, is_analysis, is_price_forecast
        ) VALUES (
            :uid, :source, :tier, :lang, :title, :summary, :link,
            :published_at, :published_raw, :fetched_at,
            :categories, :markets, :tone, :salience, :event_key, :taxonomy_ver,
            :is_economic, :is_analysis, :is_price_forecast
        )
        ON CONFLICT (uid) DO UPDATE SET
            title         = excluded.title,
            summary       = excluded.summary,
            link          = excluded.link,
            published_at  = excluded.published_at,
            published_raw = excluded.published_raw,
            categories    = excluded.categories,
            markets       = excluded.markets,
            tone          = excluded.tone,
            salience      = excluded.salience,
            event_key     = excluded.event_key,
            taxonomy_ver  = excluded.taxonomy_ver,
            is_economic   = excluded.is_economic,
            is_analysis   = excluded.is_analysis,
            is_price_forecast = excluded.is_price_forecast
        """, r)

        if existed:
            updated += 1
        else:
            inserted += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "updated": updated}


def record_source_health(name, url, tier, lang, kind, markets_hint,
                         status, items, fetched_at):
    """Store the outcome of one feed fetch so the report can show source health."""

    ensure_schema()
    conn = database._connect()
    conn.execute("""
    INSERT INTO news_sources (
        name, url, tier, lang, kind, markets_hint,
        last_fetched_at, last_status, last_items
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (name) DO UPDATE SET
        url = excluded.url,
        tier = excluded.tier,
        lang = excluded.lang,
        kind = excluded.kind,
        markets_hint = excluded.markets_hint,
        last_fetched_at = excluded.last_fetched_at,
        last_status = excluded.last_status,
        last_items = excluded.last_items
    """, (name, url, tier, lang, kind, markets_hint,
          fetched_at, status, items))
    conn.commit()
    conn.close()


def load_articles(since_iso=None, limit=2000):
    """Read stored articles, newest first. `since_iso` bounds by published_at.

    Undated rows (published_at IS NULL) are always included -- excluding them by
    a date filter would silently drop real news whose only fault is a missing
    timestamp. They sort last.
    """

    ensure_schema()
    conn = database._connect()
    conn.row_factory = sqlite3.Row

    if since_iso:
        cur = conn.execute("""
            SELECT * FROM news_articles
            WHERE published_at IS NULL OR published_at >= ?
            ORDER BY (published_at IS NULL), published_at DESC
            LIMIT ?
        """, (since_iso, limit))
    else:
        cur = conn.execute("""
            SELECT * FROM news_articles
            ORDER BY (published_at IS NULL), published_at DESC
            LIMIT ?
        """, (limit,))

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def source_health():
    """All known sources with their last fetch outcome."""

    ensure_schema()
    conn = database._connect()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM news_sources ORDER BY tier, name").fetchall()]
    conn.close()
    return rows


def counts():
    """Quick totals for status displays."""

    ensure_schema()
    conn = database._connect()
    total = conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
    latest = conn.execute(
        "SELECT MAX(fetched_at) FROM news_articles").fetchone()[0]
    conn.close()
    return {"articles": total, "last_fetch": latest}
