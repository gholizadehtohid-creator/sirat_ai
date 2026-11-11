"""SQLite persistence for market data.

Two problems the previous version had:
  * save_market_data() appended unconditionally, so re-running the scanner
    duplicated every bar.
  * load_market_data() interpolated the symbol into the SQL string.

Both are fixed here with a UNIQUE(symbol, date) upsert and bound parameters.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from config import DATABASE_NAME


PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


# How long a statement waits for a lock before giving up. The default is 5s,
# which is shorter than a single bulk write of 818 histories takes.
LOCK_TIMEOUT_SECONDS = 30.0

_wal_checked = False


def _ensure_wal(conn):
    """Put the database in WAL mode, once per process.

    This is what lets the panel stay usable while data is downloading. In the
    default rollback-journal mode a writer takes an EXCLUSIVE lock on the whole
    file, so every read the page issues -- the scan, the screen, the sector and
    index views -- fails with "database is locked" for as long as the import
    runs, and the user gets a page of empty tables while the fetch is working
    perfectly. In WAL mode readers and the single writer proceed concurrently.

    The mode is stored in the database file, so this survives restarts; the
    call is repeated only until it is known to have taken.
    """

    global _wal_checked

    if _wal_checked:
        return

    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        # A read-only volume or a network share cannot do WAL. Losing the
        # concurrency is bad; refusing to open the database would be worse.
        pass

    _wal_checked = True


def _connect():
    conn = sqlite3.connect(DATABASE_NAME, timeout=LOCK_TIMEOUT_SECONDS)
    conn.execute("PRAGMA foreign_keys = ON")
    # Per-connection, so it cannot be set once: with WAL this is the documented
    # safe pairing, and it is what makes an 818-instrument import bearable.
    conn.execute("PRAGMA synchronous = NORMAL")
    _ensure_wal(conn)
    return conn


_schema_ready = set()


def create_database():
    """Create the schema, migrating the old unconstrained table if present.

    Once per database file per process. Sixty-odd readers call this before
    every query, and each call re-ran every CREATE ... IF NOT EXISTS and the
    column migration: 1,701 times in one v2 scan of the market, a tenth of
    its time (2026-11-06). The file must still exist for the shortcut to
    hold, so a test that deletes and recreates its database gets a schema.
    """

    if DATABASE_NAME in _schema_ready and os.path.exists(DATABASE_NAME):
        return

    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS market_data (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol  TEXT    NOT NULL,
        date    TEXT    NOT NULL,
        open    REAL,
        high    REAL,
        low     REAL,
        close   REAL,
        volume  INTEGER,
        UNIQUE (symbol, date)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fetch_log (
        symbol      TEXT PRIMARY KEY,
        fetched_at  TEXT NOT NULL,
        source      TEXT NOT NULL,
        bars        INTEGER
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_symbol_date "
        "ON market_data (symbol, date)"
    )

    # ---- v2: full-market universe, snapshots, fundamentals, screen runs ----

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS instruments (
        ins_code    TEXT PRIMARY KEY,
        symbol_fa   TEXT NOT NULL,
        symbol_raw  TEXT,
        name_fa     TEXT,
        isin        TEXT,
        kind        TEXT,
        board       TEXT,
        is_equity   INTEGER DEFAULT 0,
        sector_code TEXT,
        sector_name TEXT,
        updated_at  TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_instruments_symbol "
        "ON instruments (symbol_fa)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_instruments_equity "
        "ON instruments (is_equity)"
    )

    # Daily bars keyed by insCode -- the v2 universe has no latin symbol.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        ins_code TEXT NOT NULL,
        date     TEXT NOT NULL,
        open     REAL,
        high     REAL,
        low      REAL,
        close    REAL,
        volume   REAL,
        value    REAL,
        trades   REAL,
        PRIMARY KEY (ins_code, date)
    )
    """)

    # One row per instrument per snapshot pull.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS market_snapshot (
        ins_code     TEXT PRIMARY KEY,
        symbol_fa    TEXT,
        name_fa      TEXT,
        isin         TEXT,
        pe           REAL,
        close        REAL,
        last         REAL,
        prev_close   REAL,
        open         REAL,
        high         REAL,
        low          REAL,
        volume       REAL,
        value        REAL,
        trades       REAL,
        eps          REAL,
        base_volume  REAL,
        total_shares REAL,
        limit_up     REAL,
        limit_down   REAL,
        state        TEXT,
        captured_at  TEXT
    )
    """)

    # Index values. Deliberately not price_history: an index has no open and
    # no volume, and giving it those columns would invite code to read a zero
    # out of them as if it were a traded figure.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS index_history (
        ins_code TEXT NOT NULL,
        date     TEXT NOT NULL,
        close    REAL,
        low      REAL,
        high     REAL,
        PRIMARY KEY (ins_code, date)
    )
    """)

    # Who was on each side of every session: individuals (حقیقی) against legal
    # entities (حقوقی). The count columns are stored alongside the values
    # because the per-participant average is the whole point -- a billion rials
    # from one buyer and a billion from a thousand are different events, and a
    # table holding only the values cannot distinguish them afterwards.
    # When a name the market watch no longer lists was last asked for its
    # history. Without it, every delisted equity costs a request on every
    # run for a series that never moves (seed_import.ABSENT_RECHECK_DAYS).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history_checked (
        ins_code    TEXT PRIMARY KEY,
        checked_on  TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS client_type_history (
        ins_code                    TEXT NOT NULL,
        date                        TEXT NOT NULL,
        individual_buy_value        REAL,
        individual_sell_value       REAL,
        institutional_buy_value     REAL,
        institutional_sell_value    REAL,
        individual_buy_volume       REAL,
        individual_sell_volume      REAL,
        institutional_buy_volume    REAL,
        institutional_sell_volume   REAL,
        individual_buy_count        REAL,
        individual_sell_count       REAL,
        institutional_buy_count     REAL,
        institutional_sell_count    REAL,
        PRIMARY KEY (ins_code, date)
    )
    """)

    # ---- Codal: filings, parsed financials, and per-symbol sync state ------
    #
    # The announcement index. Cheap to refresh (one ~16KB search per symbol)
    # and it is what makes the expensive half incremental: a filing whose
    # tracing number is already here has already been downloaded, so the
    # megabyte fetch is skipped. Also the store for the letter types that are
    # never parsed -- material announcements, capital increases -- which are
    # worth surfacing as headlines even without their figures.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS codal_letters (
        tracing_no  TEXT PRIMARY KEY,
        ins_code    TEXT,
        symbol      TEXT,
        letter_code TEXT,
        title       TEXT,
        published_at TEXT,
        url         TEXT,
        excel_url   TEXT,
        has_excel   INTEGER DEFAULT 0,
        parsed      INTEGER DEFAULT 0,
        seen_at     TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_codal_letters_symbol "
        "ON codal_letters (ins_code, letter_code)"
    )

    # One row per company per reporting period. Keyed on the period rather than
    # the filing, because a company files the same period twice -- unaudited
    # first, audited weeks later -- and the audited figures should replace the
    # earlier ones rather than sit beside them as a second period.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS codal_financials (
        ins_code      TEXT NOT NULL,
        period_end    TEXT NOT NULL,
        period_months INTEGER,
        tracing_no    TEXT,
        title         TEXT,
        audited       INTEGER,
        consolidated  INTEGER,
        items         TEXT,
        ratios        TEXT,
        updated_at    TEXT,
        PRIMARY KEY (ins_code, period_end, period_months)
    )
    """)

    # ONE ROW PER FILING, keyed on the tracing number rather than the period.
    #
    # codal_financials above answers "what are this company's latest figures",
    # which is the live panel's question, and collapsing the unaudited and
    # audited versions of one period into a single row is the right answer to
    # it. A backtest asks a different question -- "what had this company
    # published by 1403/09/14" -- and that collapse destroys the answer: the
    # unaudited release of FY1402 was public in خرداد and the audited one in
    # تیر, and a store that keeps only the second one moves the first release
    # forward by weeks. Every filing is therefore also written here, with its
    # own publication date, and nothing is ever overwritten by a later filing
    # for the same period.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS codal_filings (
        tracing_no    TEXT PRIMARY KEY,
        ins_code      TEXT NOT NULL,
        period_end    TEXT NOT NULL,
        period_months INTEGER,
        published_at  TEXT,
        title         TEXT,
        audited       INTEGER,
        consolidated  INTEGER,
        items         TEXT,
        ratios        TEXT,
        updated_at    TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_codal_filings_company "
        "ON codal_filings (ins_code, published_at)"
    )

    # Portfolio position of an investment company, from letter ن-۳۱. Separate
    # from codal_financials because it answers a different question: the
    # statements say what the company earned, this says what it owns and what
    # the market pays for it. Keyed on the period for the same reason -- a
    # corrected monthly report replaces the original rather than duplicating it.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS codal_portfolio (
        ins_code          TEXT NOT NULL,
        period_end        TEXT NOT NULL,
        tracing_no        TEXT,
        title             TEXT,
        listed_cost       REAL,
        listed_market     REAL,
        listed_unrealised REAL,
        unlisted_cost     REAL,
        holdings          INTEGER,
        updated_at        TEXT,
        PRIMARY KEY (ins_code, period_end)
    )
    """)

    # The incremental engine's memory: when each symbol was last checked,
    # last successfully fetched, and what went wrong if anything. Without this
    # every run would re-search all 818 symbols and re-download whatever it
    # could reach, which is the failure mode the whole design exists to avoid.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS codal_status (
        ins_code     TEXT PRIMARY KEY,
        symbol       TEXT,
        last_checked TEXT,
        last_fetched TEXT,
        letters      INTEGER DEFAULT 0,
        periods      INTEGER DEFAULT 0,
        newest_period TEXT,
        error        TEXT,
        -- Why it failed, not just that it did. A rate limit is worth retrying
        -- in minutes; a symbol Codal has never heard of is not worth retrying
        -- today at all, and one column is the difference between those.
        error_kind   TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fundamentals (
        ins_code    TEXT PRIMARY KEY,
        payload     TEXT NOT NULL,
        source      TEXT,
        updated_at  TEXT
    )
    """)

    # Provenance of the cached bars themselves. Without this, simulated data
    # and exchange data are indistinguishable once they are in the table --
    # which has already happened once in this project's history.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS data_provenance (
        scope       TEXT PRIMARY KEY,
        status      TEXT NOT NULL,
        detail      TEXT,
        checked_at  TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS screen_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        payload     TEXT
    )
    """)

    # ---- v3: paper trading ------------------------------------------------
    #
    # A paper run is a real portfolio in every respect except that no order is
    # sent. It is stored rather than printed because its only value is the
    # record it accumulates: a paper account whose history is lost each time
    # the process exits proves nothing about anything.
    #
    # Kept separate from the backtest tables on purpose. A backtest can be
    # re-run from scratch at any time; a paper trade cannot -- it was raised at
    # a moment with the data available at that moment, and overwriting it
    # afterwards with today's better data is exactly the look-ahead the
    # backtester spends its whole design avoiding.

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_positions (
        account      TEXT NOT NULL,
        ins_code     TEXT NOT NULL,
        symbol       TEXT,
        shares       REAL,
        entry_price  REAL,
        entry_date   TEXT,
        stop_price   REAL,
        target_price REAL,
        peak_price   REAL,
        atr_unit     REAL,
        opened_at    TEXT,
        PRIMARY KEY (account, ins_code)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        account      TEXT NOT NULL,
        ins_code     TEXT,
        symbol       TEXT,
        signal       TEXT,
        entry_date   TEXT,
        entry_price  REAL,
        shares       REAL,
        stop_price   REAL,
        target_price REAL,
        exit_date    TEXT,
        exit_price   REAL,
        pnl          REAL,
        pnl_pct      REAL,
        holding_days INTEGER,
        reason       TEXT,
        recorded_at  TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_trades_account "
        "ON paper_trades (account, entry_date)"
    )

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_equity (
        account          TEXT NOT NULL,
        date             TEXT NOT NULL,
        cash             REAL,
        holdings_value   REAL,
        portfolio_value  REAL,
        positions        INTEGER,
        note             TEXT,
        PRIMARY KEY (account, date)
    )
    """)

    # Orders raised but not yet filled. This table is what makes paper trading
    # obey the same T+1 rule as the backtest: a decision taken from today's
    # close cannot be filled today, so it has to survive until tomorrow's
    # session exists. Without it, a paper run restarted between sessions would
    # either lose the intent or fill it at a price it could not have got.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_orders (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        account       TEXT NOT NULL,
        ins_code      TEXT NOT NULL,
        symbol        TEXT,
        side          TEXT NOT NULL,
        shares        REAL,
        filled_shares REAL DEFAULT 0,
        average_price REAL,
        created_date  TEXT,
        eligible_from TEXT,
        expires_on    TEXT,
        status        TEXT,
        reason        TEXT,
        stop_price    REAL,
        target_price  REAL,
        atr_unit      REAL,
        entry_close   REAL,
        recorded_at   TEXT
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_orders_open "
        "ON paper_orders (account, status)"
    )

    conn.commit()

    _migrate_legacy_table(conn)
    _add_missing_columns(conn)

    conn.close()

    _schema_ready.add(DATABASE_NAME)


def _add_missing_columns(conn):
    """Add columns introduced after a table was first created.

    SQLite has no "ADD COLUMN IF NOT EXISTS", and a database created by an
    earlier build silently lacks the newer fields -- which surfaces later as a
    missing key rather than as a schema error.
    """

    wanted = {
        "market_snapshot": [
            ("isin", "TEXT"),
            ("pe", "REAL"),
        ],
        "instruments": [
            ("market", "TEXT"),
        ],
        "codal_status": [
            ("error_kind", "TEXT"),
        ],
    }

    for table, columns in wanted.items():

        try:
            existing = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
        except Exception:
            continue

        if not existing:
            continue

        for name, sql_type in columns:
            if name not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                except Exception:
                    pass

    conn.commit()


def _migrate_legacy_table(conn):
    """Older builds created market_data without UNIQUE(symbol, date).

    Detect that shape, rebuild the table with the constraint and copy the
    rows across, keeping the most recent row for each duplicated bar.
    """

    cursor = conn.cursor()

    indexes = cursor.execute(
        "PRAGMA index_list(market_data)"
    ).fetchall()

    has_unique = any(row[2] for row in indexes)

    if has_unique:
        return

    print("Migrating market_data to UNIQUE(symbol, date) schema...")

    cursor.execute("ALTER TABLE market_data RENAME TO market_data_legacy")

    cursor.execute("""
    CREATE TABLE market_data (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol  TEXT    NOT NULL,
        date    TEXT    NOT NULL,
        open    REAL,
        high    REAL,
        low     REAL,
        close   REAL,
        volume  INTEGER,
        UNIQUE (symbol, date)
    )
    """)

    cursor.execute("""
    INSERT INTO market_data (symbol, date, open, high, low, close, volume)
    SELECT symbol, date, open, high, low, close, volume
    FROM market_data_legacy
    WHERE id IN (
        SELECT MAX(id) FROM market_data_legacy GROUP BY symbol, date
    )
    """)

    moved = cursor.rowcount

    cursor.execute("DROP TABLE market_data_legacy")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_symbol_date "
        "ON market_data (symbol, date)"
    )

    conn.commit()

    print(f"Migration complete: {moved} unique bars preserved.")


def save_market_data(symbol, df, source="tsetmc"):
    """Upsert a price frame. Re-running never duplicates a bar."""

    if df is None or df.empty:
        return 0

    frame = df.copy()

    frame["date"] = pd.to_datetime(
        frame["date"],
        errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    frame = frame.dropna(subset=["date", "close"])

    for column in PRICE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None

    rows = [
        (
            symbol,
            row["date"],
            _clean(row["open"]),
            _clean(row["high"]),
            _clean(row["low"]),
            _clean(row["close"]),
            _clean(row["volume"])
        )
        for _, row in frame.iterrows()
    ]

    conn = _connect()

    conn.executemany("""
    INSERT INTO market_data (symbol, date, open, high, low, close, volume)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (symbol, date) DO UPDATE SET
        open   = excluded.open,
        high   = excluded.high,
        low    = excluded.low,
        close  = excluded.close,
        volume = excluded.volume
    """, rows)

    conn.execute("""
    INSERT INTO fetch_log (symbol, fetched_at, source, bars)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (symbol) DO UPDATE SET
        fetched_at = excluded.fetched_at,
        source     = excluded.source,
        bars       = excluded.bars
    """, (
        symbol,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source,
        len(rows)
    ))

    conn.commit()
    conn.close()

    return len(rows)


def _clean(value):
    """SQLite cannot bind numpy scalars or NaN."""

    if value is None or pd.isna(value):
        return None

    return float(value)


def load_market_data(symbol, limit=None):
    """Read cached bars for one symbol, oldest first."""

    conn = _connect()

    query = """
    SELECT date, open, high, low, close, volume
    FROM market_data
    WHERE symbol = ?
    ORDER BY date ASC
    """

    params = [symbol]

    if limit:
        # Take the newest `limit` bars, then restore chronological order.
        query = """
        SELECT * FROM (
            SELECT date, open, high, low, close, volume
            FROM market_data
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT ?
        ) ORDER BY date ASC
        """
        params.append(limit)

    df = pd.read_sql(query, conn, params=params)

    conn.close()

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


def get_fetch_info(symbol):
    """Return the cache record for a symbol, or None if never fetched."""

    conn = _connect()

    row = conn.execute(
        "SELECT symbol, fetched_at, source, bars FROM fetch_log WHERE symbol = ?",
        (symbol,)
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return {
        "symbol": row[0],
        "fetched_at": row[1],
        "source": row[2],
        "bars": row[3]
    }


def cache_age_hours(symbol):
    """Hours since the last successful fetch, or None if never fetched."""

    info = get_fetch_info(symbol)

    if info is None:
        return None

    try:
        fetched = datetime.fromisoformat(info["fetched_at"])
    except ValueError:
        return None

    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)

    delta = datetime.now(timezone.utc) - fetched

    return delta.total_seconds() / 3600


# ==============================
# V2: UNIVERSE / SNAPSHOT / SCREENING
# ==============================

def save_instruments(records):
    """Upsert instrument rows. Returns the number written."""

    if not records:
        return 0

    create_database()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = [
        (
            r["ins_code"], r["symbol_fa"], r.get("symbol_raw"), r.get("name_fa"),
            r.get("isin"), r.get("kind"), r.get("board"),
            1 if r.get("is_equity") else 0,
            r.get("sector_code"), r.get("sector_name"), now
        )
        for r in records
    ]

    conn = _connect()

    conn.executemany("""
    INSERT INTO instruments
        (ins_code, symbol_fa, symbol_raw, name_fa, isin, kind, board,
         is_equity, sector_code, sector_name, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (ins_code) DO UPDATE SET
        symbol_fa   = excluded.symbol_fa,
        symbol_raw  = excluded.symbol_raw,
        name_fa     = excluded.name_fa,
        isin        = excluded.isin,
        kind        = excluded.kind,
        board       = excluded.board,
        is_equity   = excluded.is_equity,
        -- sector arrives from a different call; never overwrite with NULL
        sector_code = COALESCE(excluded.sector_code, instruments.sector_code),
        sector_name = COALESCE(excluded.sector_name, instruments.sector_name),
        updated_at  = excluded.updated_at
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


# universe.SUPERSEDED: the kind of a listing a board move retired. Spelled
# here too so this module imports nothing above it; the tests hold the two
# equal.
SUPERSEDED_KIND = "superseded"


def load_instruments(equities_only=False, limit=None, with_superseded=False):
    """Instrument rows as dicts.

    `with_superseded`, with `equities_only`: also the listings a board move
    retired (SUPERSEDED_KIND). For a HISTORICAL universe -- the point-in-time
    data, the backtests -- which must see a past date as it traded. As first
    written (2026-11-11) a retirement (is_equity 0) took the old listing out
    of every date, not only those after the move: the 17 codes it found hold
    13,191 traded bars inside the 2021-06..2026-10 research window, and no
    registered experiment would have reproduced. Its trading ends at the
    move, and the halt and staleness filters drop it after that exactly as
    they drop any halted name. Live readers -- scans, fetch loops, the panel
    -- leave it out: the live code is the company now.
    """

    create_database()

    conn = _connect()

    query = """
    SELECT ins_code, symbol_fa, symbol_raw, name_fa, isin, kind, board,
           is_equity, sector_code, sector_name, updated_at
    FROM instruments
    """

    params = []

    if equities_only and with_superseded:
        query += " WHERE is_equity = 1 OR kind = ?"
        params.append(SUPERSEDED_KIND)
    elif equities_only:
        query += " WHERE is_equity = 1"

    # The equity row first among rows sharing a symbol: a board move leaves
    # the retired code beside the live one (seed_import.reconcile_universe),
    # and the symbol lookups -- market_data, technical, holdings -- take the
    # first row they meet. Without this they met the older, dead one.
    query += " ORDER BY symbol_fa, is_equity DESC"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    conn.close()

    keys = [
        "ins_code", "symbol_fa", "symbol_raw", "name_fa", "isin", "kind",
        "board", "is_equity", "sector_code", "sector_name", "updated_at"
    ]

    return [dict(zip(keys, row)) for row in rows]


def superseded_listings():
    """{retired ins_code: {"successor": ins_code or None, "until": date}}.

    A board move leaves one company under two codes: the listing it retired
    (SUPERSEDED_KIND), whose trading ends at the move, and the live one. A
    reader that loads both -- a historical universe, a per-session sum --
    must count the company once on any date, and this says where one ends.

    Paired by the rule the repair itself used (seed_import.reconcile_
    universe): the same symbol and the same ISIN company code. `successor`
    is the company's live code, None unless exactly one live code matches.
    `until` ('YYYY-MM-DD') is the first session after the retired code's
    first bar on which the company has a bar under another of its codes --
    the live one, or after two moves the later retired one. Before it the
    retired code is the company. From it on the company trades elsewhere,
    and the retired code's own bars there are frozen zero-volume prints
    (فاسمین's ran 71 sessions past its last trade) beside the live code's
    real ones. None while no other code of the company has a bar -- true of
    all 17 retired on 2026-11-11, whose live codes had none yet.

    A reading at date D drops the retired code once D reaches `until`: once
    another code of the company has a bar on or before D, which is known on
    D. Until this existed a retired code stayed in point-in-time universes
    for as long as its frozen bars kept it fresh, or up to config.HALT_
    EXCLUDE_DAYS past its last trade, beside the live code (found
    2026-11-11).

    Empty, after one query, when nothing has been retired.
    """

    # The repair's own pairing. universe sits above this module (see
    # SUPERSEDED_KIND), so it is imported where it is used; it imports
    # nothing that imports this module.
    import universe

    create_database()

    conn = _connect()

    try:
        retired = [(str(c), s, i) for c, s, i in conn.execute(
            "SELECT ins_code, symbol_fa, isin FROM instruments "
            "WHERE kind = ?", (SUPERSEDED_KIND,))]

        if not retired:
            return {}

        symbols = sorted({s for _, s, _ in retired if s})
        live = [(str(c), s, i) for c, s, i in conn.execute(
            "SELECT ins_code, symbol_fa, isin FROM instruments "
            "WHERE is_equity = 1 AND symbol_fa IN (%s)"
            % ",".join("?" for _ in symbols), symbols)] if symbols else []

        def company(symbol, isin):
            code = universe.company_code(isin)
            return (symbol, code) if symbol and code else None

        groups = {}
        for code, symbol, isin in live + retired:
            key = company(symbol, isin)
            if key is not None:
                groups.setdefault(key, []).append(code)

        out = {}

        for code, symbol, isin in retired:

            others = [c for c in groups.get(company(symbol, isin), ())
                      if c != code]
            heirs = [c for c, s, i in live if c in others]

            first = conn.execute(
                "SELECT MIN(date) FROM price_history WHERE ins_code = ?",
                (code,)).fetchone()[0]

            # Only the codes that came AFTER this one: a predecessor's
            # frozen zero-volume tail, running past this code's first
            # session, is not the company trading elsewhere, and it cut a
            # middle listing at its own second session (third review).
            until = None
            if first and others:
                starts = [conn.execute(
                    "SELECT MIN(date) FROM price_history WHERE ins_code = ?",
                    (other,)).fetchone()[0] for other in others]
                later = [s for s in starts if s and s > first]
                until = min(later) if later else None

            out[code] = {
                "successor": heirs[0] if len(heirs) == 1 else None,
                "until": str(until)[:10] if until else None,
            }
    finally:
        conn.close()

    return out


def instrument_counts():
    """Universe coverage for the panel."""

    create_database()

    conn = _connect()

    total = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]

    equities = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE is_equity = 1"
    ).fetchone()[0]

    by_board = conn.execute("""
        SELECT COALESCE(board, 'unknown'), COUNT(*)
        FROM instruments WHERE is_equity = 1 GROUP BY board
    """).fetchall()

    by_kind = conn.execute(
        "SELECT COALESCE(kind, 'unknown'), COUNT(*) FROM instruments GROUP BY kind"
    ).fetchall()

    # Equities only, so the figure is comparable with `equities` beside it.
    # Counting every ins_code in price_history made the panel print
    # "832/818 equities have bars" -- a coverage ratio above 100%, because the
    # gold funds have bars and are not equities. A count that can exceed its own
    # denominator is not a coverage figure.
    with_history = conn.execute("""
        SELECT COUNT(DISTINCT p.ins_code)
        FROM price_history p
        JOIN instruments i ON i.ins_code = p.ins_code
        WHERE i.is_equity = 1
    """).fetchone()[0]

    conn.close()

    return {
        "total": total,
        "equities": equities,
        "by_board": dict(by_board),
        "by_kind": dict(by_kind),
        "with_history": with_history,
    }


def save_history(ins_code, df):
    """Upsert daily bars keyed by insCode."""

    if df is None or df.empty:
        return 0

    create_database()

    frame = df.copy()

    frame["date"] = pd.to_datetime(
        frame["date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    frame = frame.dropna(subset=["date", "close"])

    for column in ["open", "high", "low", "close", "volume", "value", "trades"]:
        if column not in frame.columns:
            frame[column] = None

    rows = [
        (
            str(ins_code), row["date"],
            _clean(row["open"]), _clean(row["high"]), _clean(row["low"]),
            _clean(row["close"]), _clean(row["volume"]),
            _clean(row["value"]), _clean(row["trades"])
        )
        for _, row in frame.iterrows()
    ]

    conn = _connect()

    conn.executemany("""
    INSERT INTO price_history
        (ins_code, date, open, high, low, close, volume, value, trades)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (ins_code, date) DO UPDATE SET
        open = excluded.open, high = excluded.high, low = excluded.low,
        close = excluded.close, volume = excluded.volume,
        value = excluded.value, trades = excluded.trades
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


def load_history_by_inscode(ins_code, limit=None, before=None):
    """Cached bars for one insCode, oldest first.

    `before` keeps only sessions up to that date, and it belongs in SQL rather
    than in the caller: a point-in-time sweep asks for the same instrument at
    hundreds of dates, and loading the full 3,000-bar series each time to slice
    off the last 120 reads about 25x the data it uses. With both arguments the
    result is the newest `limit` bars on or before `before`, which is the
    window a historical reading actually wants.
    """

    create_database()

    conn = _connect()

    clause = "WHERE ins_code = ?"
    params = [str(ins_code)]

    if before is not None:
        clause += " AND date <= ?"
        params.append(str(pd.Timestamp(before).date()))

    if limit:
        query = f"""
        SELECT * FROM (
            SELECT date, open, high, low, close, volume, value, trades
            FROM price_history {clause}
            ORDER BY date DESC LIMIT ?
        ) ORDER BY date ASC
        """
        params.append(limit)
    else:
        query = f"""
        SELECT date, open, high, low, close, volume, value, trades
        FROM price_history {clause} ORDER BY date ASC
        """

    df = pd.read_sql(query, conn, params=params)

    conn.close()

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


def inscodes_with_history():
    create_database()
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT ins_code FROM price_history"
    ).fetchall()
    conn.close()
    return {str(r[0]) for r in rows}


def backfill_history_from_legacy(symbol_to_inscode):
    """Copy legacy market_data rows into the insCode-keyed price_history.

    v1 keyed bars by a latin symbol ("FOLAD"); v2 keys everything by insCode,
    because the full universe has no latin symbols at all. This carries the
    existing cache forward so the technical stage still has data to work with.
    """

    create_database()

    conn = _connect()

    moved = 0

    for symbol, ins_code in symbol_to_inscode.items():

        rows = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM market_data WHERE symbol = ? ORDER BY date
        """, (symbol,)).fetchall()

        if not rows:
            continue

        conn.executemany("""
        INSERT INTO price_history
            (ins_code, date, open, high, low, close, volume, value, trades)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        ON CONFLICT (ins_code, date) DO UPDATE SET
            open = excluded.open, high = excluded.high, low = excluded.low,
            close = excluded.close, volume = excluded.volume
        """, [(str(ins_code), *row) for row in rows])

        moved += len(rows)

    conn.commit()
    conn.close()

    return moved


# ==============================
# INDICES
# ==============================

def save_index_history(ins_code, df):
    """Upsert one index's published series."""

    if df is None or df.empty:
        return 0

    create_database()

    frame = df.copy()

    frame["date"] = pd.to_datetime(
        frame["date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    frame = frame.dropna(subset=["date", "close"])

    for column in ("low", "high"):
        if column not in frame.columns:
            frame[column] = None

    rows = [
        (str(ins_code), row["date"], _clean(row["close"]),
         _clean(row["low"]), _clean(row["high"]))
        for _, row in frame.iterrows()
    ]

    conn = _connect()

    conn.executemany("""
    INSERT INTO index_history (ins_code, date, close, low, high)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (ins_code, date) DO UPDATE SET
        close = excluded.close, low = excluded.low, high = excluded.high
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


def load_index_history(ins_code, limit=None):
    """One index's series, oldest first."""

    create_database()

    conn = _connect()

    if limit:
        query = """
        SELECT * FROM (
            SELECT date, close, low, high FROM index_history
            WHERE ins_code = ? ORDER BY date DESC LIMIT ?
        ) ORDER BY date ASC
        """
        params = [str(ins_code), limit]
    else:
        query = """
        SELECT date, close, low, high FROM index_history
        WHERE ins_code = ? ORDER BY date ASC
        """
        params = [str(ins_code)]

    df = pd.read_sql(query, conn, params=params)

    conn.close()

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


def index_coverage():
    """Bars and date range per stored index, for the status view."""

    create_database()

    conn = _connect()

    rows = conn.execute("""
        SELECT ins_code, COUNT(*), MIN(date), MAX(date)
        FROM index_history GROUP BY ins_code
    """).fetchall()

    conn.close()

    return {
        str(r[0]): {"bars": r[1], "first_date": r[2], "last_date": r[3]}
        for r in rows
    }


# ==============================
# CODAL
# ==============================

def save_codal_letters(ins_code, letters):
    """Upsert the announcement index for one company.

    `parsed` is deliberately preserved on conflict: re-running the cheap
    search must not forget that a filing's figures were already downloaded,
    or every refresh would re-fetch every statement it can see.
    """

    if not letters:
        return 0

    create_database()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = [
        (
            str(letter.get("tracing_no") or ""), str(ins_code),
            letter.get("symbol"), letter.get("letter_code"),
            letter.get("title"), letter.get("published_at"),
            letter.get("url"), letter.get("excel_url"),
            1 if letter.get("has_excel") else 0, now,
        )
        for letter in letters
        if letter.get("tracing_no")
    ]

    if not rows:
        return 0

    conn = _connect()

    conn.executemany("""
    INSERT INTO codal_letters
        (tracing_no, ins_code, symbol, letter_code, title, published_at,
         url, excel_url, has_excel, seen_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (tracing_no) DO UPDATE SET
        ins_code = excluded.ins_code, symbol = excluded.symbol,
        letter_code = excluded.letter_code, title = excluded.title,
        published_at = excluded.published_at, url = excluded.url,
        excel_url = excluded.excel_url, has_excel = excluded.has_excel,
        seen_at = excluded.seen_at
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


def mark_letter_parsed(tracing_no, parsed=True):
    create_database()
    conn = _connect()
    conn.execute(
        "UPDATE codal_letters SET parsed = ? WHERE tracing_no = ?",
        (1 if parsed else 0, str(tracing_no))
    )
    conn.commit()
    conn.close()


def load_codal_letters(ins_code=None, letter_code=None, limit=50):
    """Stored announcements, newest first."""

    create_database()

    where, params = [], []

    if ins_code:
        where.append("ins_code = ?")
        params.append(str(ins_code))

    if letter_code:
        where.append("letter_code = ?")
        params.append(letter_code)

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    conn = _connect()

    rows = conn.execute(f"""
        SELECT tracing_no, ins_code, symbol, letter_code, title,
               published_at, url, excel_url, has_excel, parsed
        FROM codal_letters {clause}
        ORDER BY published_at DESC LIMIT ?
    """, params + [limit]).fetchall()

    conn.close()

    keys = ["tracing_no", "ins_code", "symbol", "letter_code", "title",
            "published_at", "url", "excel_url", "has_excel", "parsed"]

    return [dict(zip(keys, row)) for row in rows]


def known_tracing_numbers(ins_code):
    """Tracing numbers already parsed for one company.

    The whole incremental design in one query: a filing in this set has been
    downloaded and does not need fetching again.
    """

    create_database()
    conn = _connect()
    rows = conn.execute(
        "SELECT tracing_no FROM codal_letters WHERE ins_code = ? AND parsed = 1",
        (str(ins_code),)
    ).fetchall()
    conn.close()
    return {str(r[0]) for r in rows}


# A letter's publication stamp, from the per-filing table or else the letter
# index (every path lists a letter there before parsing it). Codal's own
# "1405/08/05 11:12:49" form, which sorts as a string: Jalali, zero-padded,
# fixed-width (codal_backfill._day says why that is safe here and only here).
_PUBLISHED = ("COALESCE("
              "NULLIF((SELECT published_at FROM codal_filings "
              "WHERE tracing_no = {t}), ''), "
              "NULLIF((SELECT published_at FROM codal_letters "
              "WHERE tracing_no = {t}), ''))")


def _published_at(conn, tracing_no, given=None):
    """When a filing was published: `given` if stated, else what is on file."""

    if given:
        return str(given)

    if not tracing_no:
        return None

    row = conn.execute("SELECT " + _PUBLISHED.format(t="?"),
                       (str(tracing_no), str(tracing_no))).fetchone()

    return row[0] if row else None


def _not_older(table):
    """An upsert's DO UPDATE condition: the incoming release (its stamp bound
    twice as ?) replaces the held one unless it was published EARLIER.

    The same letter always may (a re-parse), and so may anything undated on
    either side -- nothing proves it older, and refusing it would freeze the
    row on a missing stamp.
    """

    held = _PUBLISHED.format(t="%s.tracing_no" % table)

    return ("excluded.tracing_no IS %s.tracing_no OR ? IS NULL OR %s IS NULL "
            "OR ? >= %s" % (table, held, held))


# What a parse must carry to stand for each statement: financials.ratios'
# provenance flags -- has_income (revenue or net profit), has_balance (total
# assets), has_cashflow (operating cash flow). Spelled here too so this
# module imports nothing above it; the tests hold the two equal.
STATEMENT_ITEMS = (
    ("income statement", ("revenue", "net_profit")),
    ("balance sheet", ("total_assets",)),
    ("cash flow statement", ("operating_cash_flow",)),
)


def _figure(value):
    """A number a parse found: finite, as financials._finite reads one."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return False

    return number == number and abs(number) != float("inf")


def statements_lost(held, incoming):
    """The statements one parse's items (`held`) carry and another's lack."""

    held = held if isinstance(held, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}

    return [name for name, keys in STATEMENT_ITEMS
            if any(_figure(held.get(key)) for key in keys)
            and not any(_figure(incoming.get(key)) for key in keys)]


def _statements_lost_sql(held, incoming):
    """statements_lost() over two stored `items` blobs, for an upsert's WHERE."""

    return 1 if statements_lost(_loads(held), _loads(incoming)) else 0


def save_codal_financials(ins_code, record):
    """Upsert one company-period of parsed financials.

    The newest-PUBLISHED release of a period wins, whatever order the
    releases are written in. Found 2026-11-11: the upsert always replaced, so
    the release written last won, and that is often the older one -- the
    backfill walks history oldest-first after the watch has stored the
    current filing, a watch catch-up reached the original a day after its
    correction, and the daily sync writes its two newest filings newest
    first. 531 periods held a superseded release, 74 of them a company's
    latest (ملت 1405/06/31 held 1614602, not the correction 1619771), while
    the fundamental store, which already ranked by publication, held the
    right one.

    Nor does a newer release replace the held one when its parse lacks a
    statement the held parse has (STATEMENT_ITEMS): that is a failed read
    of the statement, not the company's word on it, and newest-wins alone
    let it through. Found 2026-11-11 in the --repair-superseded dry run:
    472469's parse has no balance sheet or cash flow statement, 1212323's
    no income statement, and the releases held for those periods have all
    three. codal_filings keeps every release as parsed; the repair names
    these for a targeted re-parse. A re-parse of the held letter itself
    always may.

    And the choice does not depend on write order (third review,
    2026-11-11): an OLDER release replaces a held newer one whose parse lacks
    a statement the older has. Otherwise the thin newest release, written
    first -- the daily sync writes newest first -- kept its period and the
    complete original was refused as older. fundamental_db.sync_from_market_db
    makes the same choice. Returns 1 if the row was written, 0 if it held a
    newer release or a statement the incoming parse lacks.
    """

    if not record or not record.get("period_end"):
        return 0

    create_database()

    conn = _connect()
    conn.create_function("statements_lost", 2, _statements_lost_sql,
                         deterministic=True)

    tracing_no = record.get("tracing_no")
    tracing_no = None if tracing_no is None else str(tracing_no)
    published = _published_at(conn, tracing_no, record.get("published_at"))

    cursor = conn.execute("""
    INSERT INTO codal_financials
        (ins_code, period_end, period_months, tracing_no, title, audited,
         consolidated, items, ratios, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (ins_code, period_end, period_months) DO UPDATE SET
        tracing_no = excluded.tracing_no, title = excluded.title,
        audited = excluded.audited, consolidated = excluded.consolidated,
        items = excluded.items, ratios = excluded.ratios,
        updated_at = excluded.updated_at
    WHERE excluded.tracing_no IS codal_financials.tracing_no
       OR (NOT statements_lost(codal_financials.items, excluded.items)
           AND ((%s)
                OR statements_lost(excluded.items, codal_financials.items)))
    """ % _not_older("codal_financials"), (
        str(ins_code), record["period_end"], record.get("period_months"),
        tracing_no, record.get("title"),
        _flag(record.get("audited")), 1 if record.get("consolidated") else 0,
        json.dumps(record.get("items") or {}, ensure_ascii=False),
        json.dumps(record.get("ratios") or {}, ensure_ascii=False),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        published, published,
    ))

    written = cursor.rowcount

    conn.commit()
    conn.close()

    return 1 if written else 0


def save_codal_filing(ins_code, record, published_at=None):
    """Upsert one FILING, keyed on its tracing number.

    The point-in-time twin of save_codal_financials(). Both are written for
    every filing parsed: the period-keyed table serves the live panel, this one
    serves any question of the form "what was public on date D". They cannot be
    merged, because the merge is exactly what loses the first release date of a
    period that was later refiled.
    """

    if not record or not record.get("period_end") or not record.get("tracing_no"):
        return 0

    create_database()

    conn = _connect()

    conn.execute("""
    INSERT INTO codal_filings
        (tracing_no, ins_code, period_end, period_months, published_at, title,
         audited, consolidated, items, ratios, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (tracing_no) DO UPDATE SET
        ins_code = excluded.ins_code, period_end = excluded.period_end,
        period_months = excluded.period_months,
        published_at = COALESCE(excluded.published_at, codal_filings.published_at),
        title = excluded.title, audited = excluded.audited,
        consolidated = excluded.consolidated, items = excluded.items,
        ratios = excluded.ratios, updated_at = excluded.updated_at
    """, (
        str(record["tracing_no"]), str(ins_code), record["period_end"],
        record.get("period_months"), published_at or record.get("published_at"),
        record.get("title"), _flag(record.get("audited")),
        1 if record.get("consolidated") else 0,
        json.dumps(record.get("items") or {}, ensure_ascii=False),
        json.dumps(record.get("ratios") or {}, ensure_ascii=False),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))

    conn.commit()
    conn.close()

    return 1


def load_codal_filings(ins_code=None):
    """Every stored filing, oldest publication first, as plain dicts.

    Returns the whole table in one query when no company is named. The
    backtester wants exactly that: 818 separate queries inside a symbol loop
    was the difference between a second and a minute in the equivalent join
    over codal_financials.
    """

    create_database()

    conn = _connect()

    query = """
        SELECT tracing_no, ins_code, period_end, period_months, published_at,
               title, audited, consolidated, items, ratios, updated_at
        FROM codal_filings
    """

    params = []

    if ins_code:
        query += " WHERE ins_code = ?"
        params.append(str(ins_code))

    query += " ORDER BY ins_code, published_at"

    rows = conn.execute(query, params).fetchall()

    conn.close()

    return [
        {
            "tracing_no": row[0],
            "ins_code": row[1],
            "period_end": row[2],
            "period_months": row[3],
            "published_at": row[4],
            "title": row[5],
            "audited": None if row[6] is None else bool(row[6]),
            "consolidated": bool(row[7]),
            "items": _loads(row[8]),
            "ratios": _loads(row[9]),
            "updated_at": row[10],
        }
        for row in rows
    ]


def codal_filing_tracing_numbers():
    """Tracing numbers already parsed into codal_filings, as a set.

    The backfill's resume point. One query rather than one per company.
    """

    create_database()

    conn = _connect()

    rows = conn.execute("SELECT tracing_no FROM codal_filings").fetchall()

    conn.close()

    return {str(row[0]) for row in rows}


def _flag(value):
    """Tri-state to SQLite. None stays None -- "not stated" is not "false"."""

    if value is None:
        return None

    return 1 if value else 0


def load_codal_financials(ins_code, limit=None):
    """Stored periods for one company, newest period first."""

    create_database()

    conn = _connect()

    query = """
        SELECT period_end, period_months, tracing_no, title, audited,
               consolidated, items, ratios, updated_at
        FROM codal_financials WHERE ins_code = ?
        ORDER BY period_end DESC, period_months DESC
    """
    params = [str(ins_code)]

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    conn.close()

    out = []

    for row in rows:
        out.append({
            "period_end": row[0],
            "period_months": row[1],
            "tracing_no": row[2],
            "title": row[3],
            "audited": None if row[4] is None else bool(row[4]),
            "consolidated": bool(row[5]),
            "items": _loads(row[6]),
            "ratios": _loads(row[7]),
            "updated_at": row[8],
        })

    return out


def save_codal_portfolio(ins_code, record):
    """Upsert one company-period of portfolio totals from letter ن-۳۱.

    A correction replaces the original, never the reverse (see
    save_codal_financials): codal_sync parses one unparsed ن-۳۱ per run,
    newest first, so the run after a correction is parsed reaches the
    original of the same month. Returns 1 if written, 0 if refused.
    """

    if not record or not record.get("period_end"):
        return 0

    create_database()

    conn = _connect()

    tracing_no = record.get("tracing_no")
    tracing_no = None if tracing_no is None else str(tracing_no)
    published = _published_at(conn, tracing_no, record.get("published_at"))

    cursor = conn.execute("""
    INSERT INTO codal_portfolio
        (ins_code, period_end, tracing_no, title, listed_cost, listed_market,
         listed_unrealised, unlisted_cost, holdings, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (ins_code, period_end) DO UPDATE SET
        tracing_no = excluded.tracing_no, title = excluded.title,
        listed_cost = excluded.listed_cost,
        listed_market = excluded.listed_market,
        listed_unrealised = excluded.listed_unrealised,
        unlisted_cost = excluded.unlisted_cost,
        holdings = excluded.holdings, updated_at = excluded.updated_at
    WHERE %s
    """ % _not_older("codal_portfolio"), (
        str(ins_code), record["period_end"], tracing_no,
        record.get("title"), record.get("listed_cost"),
        record.get("listed_market"), record.get("listed_unrealised"),
        record.get("unlisted_cost"), record.get("holdings"),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        published, published,
    ))

    written = cursor.rowcount

    conn.commit()
    conn.close()

    return 1 if written else 0


def load_codal_portfolio_latest(ins_codes=None):
    """Newest portfolio filing per company, keyed by insCode.

    One query rather than one per symbol, for the same reason as
    load_codal_latest: the scan already touches hundreds of instruments.
    """

    create_database()

    conn = _connect()

    if ins_codes:
        codes = [str(c) for c in ins_codes]
        holes = ", ".join(["?"] * len(codes))
        where, params = f"WHERE ins_code IN ({holes})", codes
    else:
        where, params = "", []

    rows = conn.execute(f"""
        SELECT ins_code, period_end, tracing_no, title, listed_cost,
               listed_market, listed_unrealised, unlisted_cost, holdings,
               updated_at
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY ins_code ORDER BY period_end DESC
            ) AS rn
            FROM codal_portfolio {where}
        ) WHERE rn = 1
    """, params).fetchall()

    conn.close()

    out = {}

    for row in rows:
        out[str(row[0])] = {
            "period_end": row[1],
            "tracing_no": row[2],
            "title": row[3],
            "listed_cost": row[4],
            "listed_market": row[5],
            "listed_unrealised": row[6],
            "unlisted_cost": row[7],
            "holdings": row[8],
            "updated_at": row[9],
        }

    return out


def load_codal_latest(ins_codes=None):
    """The most recent stored period for many companies, in one query.

    A per-symbol call in a loop meant a connection per instrument on a scan
    that already touches hundreds; this keeps the fundamental join to one
    round trip. "Most recent" is by period end, then by the longer period --
    an annual filing outranks a same-dated interim one because it carries the
    audited full-year figures.
    """

    create_database()

    conn = _connect()

    if ins_codes:
        codes = [str(c) for c in ins_codes]
        holes = ", ".join(["?"] * len(codes))
        where = f"WHERE ins_code IN ({holes})"
        params = codes
    else:
        where, params = "", []

    rows = conn.execute(f"""
        SELECT ins_code, period_end, period_months, title, audited,
               consolidated, items, ratios, updated_at
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY ins_code
                ORDER BY period_end DESC, period_months DESC,
                         COALESCE(audited, 0) DESC
            ) AS rank
            FROM codal_financials {where}
        ) WHERE rank = 1
    """, params).fetchall()

    conn.close()

    out = {}

    for row in rows:
        out[str(row[0])] = {
            "period_end": row[1],
            "period_months": row[2],
            "title": row[3],
            "audited": None if row[4] is None else bool(row[4]),
            "consolidated": bool(row[5]),
            "items": _loads(row[6]),
            "ratios": _loads(row[7]),
            "updated_at": row[8],
        }

    return out


def _loads(text):
    try:
        return json.loads(text) if text else {}
    except (ValueError, TypeError):
        return {}


def set_codal_status(ins_code, symbol=None, checked=False, fetched=False,
                     letters=None, periods=None, newest_period=None,
                     error=None, error_kind=None):
    """Record what happened to one company on this pass."""

    create_database()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = _connect()

    existing = conn.execute(
        "SELECT last_checked, last_fetched, letters, periods, newest_period "
        "FROM codal_status WHERE ins_code = ?", (str(ins_code),)
    ).fetchone()

    previous = existing or (None, None, 0, 0, None)

    conn.execute("""
    INSERT INTO codal_status
        (ins_code, symbol, last_checked, last_fetched, letters, periods,
         newest_period, error, error_kind)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (ins_code) DO UPDATE SET
        symbol = excluded.symbol, last_checked = excluded.last_checked,
        last_fetched = excluded.last_fetched, letters = excluded.letters,
        periods = excluded.periods, newest_period = excluded.newest_period,
        error = excluded.error, error_kind = excluded.error_kind
    """, (
        str(ins_code), symbol,
        now if checked else previous[0],
        now if fetched else previous[1],
        previous[2] if letters is None else letters,
        previous[3] if periods is None else periods,
        newest_period or previous[4],
        error, error_kind,
    ))

    conn.commit()
    conn.close()


_STATUS_KEYS = ["ins_code", "symbol", "last_checked", "last_fetched",
                "letters", "periods", "newest_period", "error", "error_kind"]

_STATUS_COLUMNS = ", ".join(_STATUS_KEYS)


def load_codal_status(ins_codes=None):
    create_database()

    conn = _connect()

    if ins_codes:
        codes = [str(c) for c in ins_codes]
        holes = ", ".join(["?"] * len(codes))
        rows = conn.execute(
            f"SELECT {_STATUS_COLUMNS} FROM codal_status "
            f"WHERE ins_code IN ({holes})", codes
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_STATUS_COLUMNS} FROM codal_status"
        ).fetchall()

    conn.close()

    return {str(row[0]): dict(zip(_STATUS_KEYS, row)) for row in rows}


def codal_coverage():
    """How many equities carry filed financials, and how fresh they are."""

    create_database()

    conn = _connect()

    companies, periods = conn.execute(
        "SELECT COUNT(DISTINCT ins_code), COUNT(*) FROM codal_financials"
    ).fetchone()

    letters = conn.execute("SELECT COUNT(*) FROM codal_letters").fetchone()[0]

    checked = conn.execute(
        "SELECT COUNT(*) FROM codal_status WHERE last_checked IS NOT NULL"
    ).fetchone()[0]

    newest = conn.execute(
        "SELECT MAX(updated_at) FROM codal_financials"
    ).fetchone()[0]

    equities = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE is_equity = 1"
    ).fetchone()[0]

    conn.close()

    return {
        "with_financials": companies or 0,
        "periods": periods or 0,
        "letters": letters or 0,
        "checked": checked or 0,
        "equities": equities or 0,
        "updated_at": newest,
    }


# ==============================
# CLIENT TYPE (حقیقی / حقوقی)
# ==============================

# Column order is fixed here rather than taken from the frame, so a provider
# that reorders its output cannot silently write buy counts into sell columns.
CLIENT_TYPE_FIELDS = [
    "individual_buy_value", "individual_sell_value",
    "institutional_buy_value", "institutional_sell_value",
    "individual_buy_volume", "individual_sell_volume",
    "institutional_buy_volume", "institutional_sell_volume",
    "individual_buy_count", "individual_sell_count",
    "institutional_buy_count", "institutional_sell_count",
]


def save_client_type_history(ins_code, df):
    """Upsert one instrument's daily حقیقی/حقوقی split."""

    if df is None or df.empty:
        return 0

    create_database()

    frame = df.copy()

    frame["date"] = pd.to_datetime(
        frame["date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    frame = frame.dropna(subset=["date"])

    for column in CLIENT_TYPE_FIELDS:
        if column not in frame.columns:
            frame[column] = None

    rows = [
        (str(ins_code), row["date"],
         *[_clean(row[column]) for column in CLIENT_TYPE_FIELDS])
        for _, row in frame.iterrows()
    ]

    if not rows:
        return 0

    columns = ", ".join(CLIENT_TYPE_FIELDS)
    placeholders = ", ".join(["?"] * (len(CLIENT_TYPE_FIELDS) + 2))
    updates = ", ".join(f"{c} = excluded.{c}" for c in CLIENT_TYPE_FIELDS)

    conn = _connect()

    conn.executemany(f"""
    INSERT INTO client_type_history (ins_code, date, {columns})
    VALUES ({placeholders})
    ON CONFLICT (ins_code, date) DO UPDATE SET {updates}
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


def load_client_type_history(ins_code, limit=None):
    """One instrument's client-type series, oldest first."""

    create_database()

    columns = ", ".join(CLIENT_TYPE_FIELDS)

    conn = _connect()

    if limit:
        query = f"""
        SELECT * FROM (
            SELECT date, {columns} FROM client_type_history
            WHERE ins_code = ? ORDER BY date DESC LIMIT ?
        ) ORDER BY date ASC
        """
        params = [str(ins_code), limit]
    else:
        query = f"""
        SELECT date, {columns} FROM client_type_history
        WHERE ins_code = ? ORDER BY date ASC
        """
        params = [str(ins_code)]

    df = pd.read_sql(query, conn, params=params)

    conn.close()

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


def load_client_type_tail(ins_codes, sessions):
    """The most recent `sessions` rows for each of many instruments.

    The panel's flow view reads several hundred symbols at once. Doing that one
    load_client_type_history() call at a time meant a connection and a query
    per symbol; this is one query, with the per-symbol cut applied in SQL so a
    long-listed name does not drag its whole history through pandas.
    """

    create_database()

    codes = [str(c) for c in ins_codes]

    if not codes or sessions <= 0:
        return {}

    columns = ", ".join(CLIENT_TYPE_FIELDS)
    holes = ", ".join(["?"] * len(codes))

    conn = _connect()

    df = pd.read_sql(f"""
        SELECT ins_code, date, {columns} FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY ins_code ORDER BY date DESC
            ) AS rank
            FROM client_type_history
            WHERE ins_code IN ({holes})
        ) WHERE rank <= ?
        ORDER BY ins_code, date ASC
    """, conn, params=codes + [sessions])

    conn.close()

    if df.empty:
        return {}

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return {
        str(code): group.drop(columns=["ins_code"]).reset_index(drop=True)
        for code, group in df.groupby("ins_code")
    }


def halted_codes(days=None):
    """{ins_code: last traded date} for symbols halted longer than `days`.

    "Traded" means a bar with volume: a halted symbol keeps getting bars at
    its frozen close with zero volume, so the newest bar's date says nothing.
    The clock is the market's newest session, not today's date -- a holiday
    or a late fetch must not halt the whole market. A symbol with bars but
    none traded is halted (date None); one with no bars at all is unknown
    and left out of this map, so missing history is never mistaken for a
    halt.
    """

    from datetime import date

    if days is None:
        from config import HALT_EXCLUDE_DAYS as days

    create_database()
    conn = _connect()
    newest = conn.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    rows = conn.execute("""
        SELECT ins_code, MAX(CASE WHEN volume > 0 THEN date END)
        FROM price_history GROUP BY ins_code
    """).fetchall()
    conn.close()

    if not newest:
        return {}

    market = date.fromisoformat(str(newest)[:10])
    out = {}
    for code, last in rows:
        if not last:
            out[str(code)] = None
        elif (market - date.fromisoformat(str(last)[:10])).days > days:
            out[str(code)] = str(last)[:10]
    return out


def load_history_tail(ins_codes, sessions):
    """The most recent `sessions` price bars for each of many instruments.

    One query for a whole-market technical scan, mirroring
    `load_client_type_tail`: the per-symbol cut is applied in SQL so a
    long-listed name does not drag its entire history through pandas. Returns
    {ins_code: DataFrame(oldest first)} -- the shape calculate_indicators wants.
    """

    create_database()

    codes = [str(c) for c in ins_codes]

    if not codes or sessions <= 0:
        return {}

    conn = _connect()

    holes = ", ".join(["?"] * len(codes))

    df = pd.read_sql(f"""
        SELECT ins_code, date, open, high, low, close, volume, value, trades
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY ins_code ORDER BY date DESC
            ) AS rank
            FROM price_history
            WHERE ins_code IN ({holes})
        ) WHERE rank <= ?
        ORDER BY ins_code, date ASC
    """, conn, params=codes + [sessions])

    conn.close()

    if df.empty:
        return {}

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return {
        str(code): group.drop(columns=["ins_code", "rank"], errors="ignore")
                        .reset_index(drop=True)
        for code, group in df.groupby("ins_code")
    }


def inscodes_with_client_type():
    create_database()
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT ins_code FROM client_type_history"
    ).fetchall()
    conn.close()
    return {str(r[0]) for r in rows}


def client_type_coverage():
    """How many equities carry a flow series, and how recent it is."""

    create_database()

    conn = _connect()

    covered, sessions, newest = conn.execute("""
        SELECT COUNT(DISTINCT ins_code), COUNT(*), MAX(date)
        FROM client_type_history
    """).fetchone()

    equities = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE is_equity = 1"
    ).fetchone()[0]

    conn.close()

    return {
        "with_flow": covered or 0,
        "equities": equities or 0,
        "rows": sessions or 0,
        "last_date": newest,
    }


def newest_bars(ins_codes=None):
    """{ins_code: (newest date 'YYYY-MM-DD', close on that date)}.

    What an incremental update needs to know per instrument: how far its own
    series reaches, and the price the next session's `py` must agree with.
    Instruments with no bars are simply absent.
    """

    create_database()

    conn = _connect()

    rows = conn.execute("""
        SELECT p.ins_code, p.date, p.close
        FROM price_history p
        JOIN (SELECT ins_code, MAX(date) AS date
              FROM price_history GROUP BY ins_code) n
          ON n.ins_code = p.ins_code AND n.date = p.date
    """).fetchall()

    conn.close()

    wanted = None if ins_codes is None else {str(c) for c in ins_codes}

    return {
        str(code): (date, close)
        for code, date, close in rows
        if wanted is None or str(code) in wanted
    }


def history_checked_dates():
    """{ins_code: 'YYYY-MM-DD'} -- when each unlisted name was last read."""

    create_database()

    conn = _connect()
    rows = conn.execute(
        "SELECT ins_code, checked_on FROM history_checked").fetchall()
    conn.close()

    return {str(code): day for code, day in rows}


def mark_history_checked(ins_codes, day):
    create_database()

    conn = _connect()
    conn.executemany(
        "INSERT OR REPLACE INTO history_checked (ins_code, checked_on) "
        "VALUES (?, ?)", [(str(c), day) for c in ins_codes])
    conn.commit()
    conn.close()


def newest_flow_owed(ins_codes=None):
    """Equities whose flow series ends before their newest TRADED bar.

    Flow rows exist only for sessions a name traded (the provider drops
    zero-turnover rows), so a name whose last trade is already in its flow
    series has nothing new to fetch -- it was the whole equity universe, one
    request each, on every run. Names with no flow at all are owed too.

    Run it after the price history is brought up to date: the bars are what
    say a session happened for that name.
    """

    create_database()

    conn = _connect()

    rows = conn.execute("""
        SELECT p.ins_code, p.traded, f.flowed
        FROM (SELECT ins_code, MAX(date) AS traded
              FROM price_history WHERE volume > 0 GROUP BY ins_code) p
        LEFT JOIN (SELECT ins_code, MAX(date) AS flowed
                   FROM client_type_history GROUP BY ins_code) f
          ON f.ins_code = p.ins_code
    """).fetchall()

    conn.close()

    wanted = None if ins_codes is None else {str(c) for c in ins_codes}

    owed = {str(code) for code, traded, flowed in rows
            if flowed is None or flowed < traded}

    if wanted is None:
        return owed

    # A name with bars but no traded one (19 on 2026-11-05: listed at 1000,
    # never dealt) has no flow to fetch. Only a name with no bars at all is
    # unknown -- the history step has not reached it -- and is asked.
    conn = _connect()
    known = {str(code) for (code,) in conn.execute(
        "SELECT DISTINCT ins_code FROM price_history")}
    conn.close()

    return {c for c in wanted if c in owed or c not in known}


def newest_equity_bar():
    """The newest stored bar ACROSS EQUITIES ONLY, as a datetime, or None.

    Deliberately not `MAX(date) FROM price_history`. That table also holds the
    gold funds, which gold.py fetches on its own schedule, and on 2026-08-24 it
    held fund bars three sessions newer than any equity bar. Anything asking
    "is the market data current" and answering from the whole table gets a yes
    on the strength of instruments the equity scanners never read.
    """

    create_database()

    conn = _connect()

    row = conn.execute("""
        SELECT MAX(p.date)
        FROM price_history p
        JOIN instruments i ON i.ins_code = p.ins_code
        WHERE i.is_equity = 1
    """).fetchone()

    conn.close()

    if not row or not row[0]:
        return None

    stamp = pd.to_datetime(row[0], errors="coerce")

    return None if pd.isna(stamp) else stamp.to_pydatetime()


def market_turnover(limit=None):
    """Total rials traded per session, summed across stored EQUITIES.

    This is the only volume series an index can honestly be given. The exchange
    publishes no volume for an index, so a technical read on one either drops
    the volume component or states plainly where its volume came from -- and
    market-wide turnover is the figure a trader would use anyway.

    Equities only, for the reason newest_equity_bar() gives. This summed every
    row of price_history, and that table also holds the gold funds: through
    2025-26 they were a fifth to two-thirds of a month's total, and from
    2026-03-15 to 05-18, when no equity traded at all, they were ALL of it.
    Their flows follow bullion and the dollar, not the stock market, yet they
    drove the regime's turnover reading -- on 2026-11-10 the ten-session ratio
    read 0.734 (CONTRACTING), which demoted a unanimous BULL read from
    AGGRESSIVE to NORMAL, where the equities alone read 0.798 (STEADY) -- and
    they were attached as the "volume" of TEDPIX, an index none of them is in.
    `instruments` counts equity bars only, which is what the coverage filter
    in indices.py means by a well-covered session.

    A listing a board move retired (SUPERSEDED_KIND) still counts: its bars
    are that company's trading on the dates it traded, and a series the
    backtests read (swing_backtest, swing_strategy) must not lose them for
    every past session at the move. It stops trading there and is no longer
    fetched, so a later session gains nothing from it -- see
    load_instruments(with_superseded).
    """

    create_database()

    conn = _connect()

    # CROSS JOIN fixes the order: the ~1,400 instruments first, then each
    # one's bars by key. Given the OR, the planner otherwise walked all two
    # million bars and looked each one's instrument up -- 3.7 s against
    # 2.9 s on the live file (2026-11-11).
    query = """
        SELECT p.date, SUM(COALESCE(p.value, 0)), COUNT(*)
        FROM instruments i
        CROSS JOIN price_history p ON p.ins_code = i.ins_code
        WHERE i.is_equity = 1 OR i.kind = ?
        GROUP BY p.date ORDER BY p.date
    """

    rows = conn.execute(query, (SUPERSEDED_KIND,)).fetchall()

    conn.close()

    df = pd.DataFrame(rows, columns=["date", "turnover", "instruments"])

    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    if limit:
        df = df.tail(limit)

    return df.reset_index(drop=True)


# ==============================
# SECTORS
# ==============================

def save_instrument_sectors(records):
    """Attach sector code/name (and board/market) to stored instruments.

    The universe list on GitHub carries no sector at all, so this is the only
    route to one: TSETMC's per-instrument info call, one request per symbol.
    """

    if not records:
        return 0

    create_database()

    rows = [
        (
            r.get("sector_code"), r.get("sector_name"),
            r.get("board"), r.get("market"),
            str(r["ins_code"]),
        )
        for r in records
        if r.get("ins_code")
    ]

    conn = _connect()

    conn.executemany("""
    UPDATE instruments SET
        sector_code = COALESCE(?, sector_code),
        sector_name = COALESCE(?, sector_name),
        board       = COALESCE(?, board),
        market      = COALESCE(?, market)
    WHERE ins_code = ?
    """, rows)

    written = conn.total_changes

    conn.commit()
    conn.close()

    return written


def save_fundamentals(records, source="tsetmc-cdn"):
    """Store the full per-instrument info payload as JSON.

    Sector membership goes to `instruments` because it identifies the company,
    but the rest of the instrument-info response -- the exchange's own sector
    P/E, free float, shares outstanding, the 52-week range -- is measurement,
    not identity, and it changes shape as TSETMC's schema drifts. Keeping it as
    a payload means a new field costs no migration, and the sector view can use
    the exchange's published sector P/E instead of inferring one.
    """

    if not records:
        return 0

    create_database()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = [
        (str(r["ins_code"]), json.dumps(r, ensure_ascii=False), source, now)
        for r in records
        if r.get("ins_code")
    ]

    conn = _connect()

    conn.executemany("""
    INSERT INTO fundamentals (ins_code, payload, source, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (ins_code) DO UPDATE SET
        payload    = excluded.payload,
        source     = excluded.source,
        updated_at = excluded.updated_at
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


def load_fundamentals():
    """ins_code -> the stored instrument-info payload."""

    create_database()

    conn = _connect()

    rows = conn.execute("SELECT ins_code, payload FROM fundamentals").fetchall()

    conn.close()

    out = {}

    for ins_code, payload in rows:
        try:
            out[str(ins_code)] = json.loads(payload)
        except (TypeError, ValueError):
            continue

    return out


def sector_coverage():
    """How much of the equity universe has a sector attached."""

    create_database()

    conn = _connect()

    total = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE is_equity = 1"
    ).fetchone()[0]

    known = conn.execute(
        "SELECT COUNT(*) FROM instruments "
        "WHERE is_equity = 1 AND sector_name IS NOT NULL"
    ).fetchone()[0]

    sectors = conn.execute(
        "SELECT COUNT(DISTINCT sector_name) FROM instruments "
        "WHERE is_equity = 1 AND sector_name IS NOT NULL"
    ).fetchone()[0]

    conn.close()

    return {"equities": total, "with_sector": known, "sectors": sectors}


def load_sector_members():
    """Equities grouped by sector name, sector-less ones excluded."""

    create_database()

    conn = _connect()

    rows = conn.execute("""
        SELECT sector_code, sector_name, ins_code, symbol_fa, name_fa, board
        FROM instruments
        WHERE is_equity = 1 AND sector_name IS NOT NULL AND sector_name <> ''
        ORDER BY sector_name, symbol_fa
    """).fetchall()

    conn.close()

    grouped = {}

    for sector_code, sector_name, ins_code, symbol_fa, name_fa, board in rows:
        grouped.setdefault(sector_name, {
            "sector_code": (sector_code or "").strip(),
            "sector_name": sector_name,
            "members": [],
        })["members"].append({
            "ins_code": str(ins_code),
            "symbol_fa": symbol_fa,
            "name_fa": name_fa,
            "board": board,
        })

    return grouped


def traded_sessions():
    """{ins_code: sessions it traded on (volume > 0)} over price_history.

    How long a name has really traded, which its first stored bar does not
    say: most listings begin with a run of zero-volume bars. About 1 s.
    """

    conn = _connect()
    try:
        rows = conn.execute("SELECT ins_code, COUNT(*) FROM price_history "
                            "WHERE volume > 0 GROUP BY ins_code").fetchall()
    finally:
        conn.close()
    return {str(code): count for code, count in rows}


def save_snapshot(df):
    """Replace the stored whole-market snapshot."""

    if df is None or df.empty:
        return 0

    create_database()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    columns = [
        "ins_code", "symbol_fa", "name_fa", "isin", "close", "last",
        "prev_close", "open", "high", "low", "volume", "value", "trades",
        "eps", "pe", "base_volume", "total_shares", "limit_up", "limit_down",
        "state"
    ]

    rows = []

    for _, row in df.iterrows():
        values = []
        for column in columns:
            value = row.get(column)
            if column in ("ins_code", "symbol_fa", "name_fa", "isin", "state"):
                values.append(None if value is None else str(value))
            else:
                values.append(_clean(value))
        values.append(now)
        rows.append(tuple(values))

    conn = _connect()

    # A pre-open watch (before 09:00) carries today's price band and state
    # but no trading at all: every volume and value is 0. Replacing the last
    # session's snapshot with it wiped the traded values the fund list, the
    # allocation's suggested funds and the bill check read, so "امروز چه
    # کنم؟" came back empty every morning -- exactly when a manual trader
    # plans the day (found 2026-11-11: 2,506 rows captured 07:00, none
    # traded). Such a watch keeps each name's last-session volume, value and
    # trades; prices, band and state are the new watch's.
    volume_at = columns.index("volume")
    if rows and not any((r[volume_at] or 0) > 0 for r in rows):
        held = {str(r[0]): r[1:] for r in conn.execute(
            "SELECT ins_code, volume, value, trades FROM market_snapshot")}
        value_at, trades_at = columns.index("value"), columns.index("trades")
        merged = []
        for r in rows:
            last = held.get(str(r[0]))
            if last and (last[0] or 0) > 0:
                r = list(r)
                r[volume_at], r[value_at], r[trades_at] = last
                r = tuple(r)
            merged.append(r)
        rows = merged

    conn.execute("DELETE FROM market_snapshot")

    conn.executemany(f"""
    INSERT INTO market_snapshot ({', '.join(columns)}, captured_at)
    VALUES ({', '.join('?' * (len(columns) + 1))})
    """, rows)

    conn.commit()
    conn.close()

    return len(rows)


def load_snapshot():
    create_database()
    conn = _connect()
    df = pd.read_sql("SELECT * FROM market_snapshot", conn)
    conn.close()
    return df


def snapshot_info():
    create_database()
    conn = _connect()
    row = conn.execute(
        "SELECT COUNT(*), MAX(captured_at) FROM market_snapshot"
    ).fetchone()
    conn.close()
    return {"rows": row[0] or 0, "captured_at": row[1]}


VERIFIED_REAL = "verified-real"
SIMULATED = "simulated"
UNVERIFIED = "unverified"


def set_provenance(scope, status, detail=None):
    """Record where a body of cached data came from."""

    create_database()

    conn = _connect()

    conn.execute("""
    INSERT INTO data_provenance (scope, status, detail, checked_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (scope) DO UPDATE SET
        status = excluded.status,
        detail = excluded.detail,
        checked_at = excluded.checked_at
    """, (
        scope, status, detail,
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    ))

    conn.commit()
    conn.close()


def get_provenance(scope="price_history"):
    """Provenance record, defaulting to 'unverified' when nothing is known.

    Defaulting to unverified rather than to real is deliberate: unlabelled
    data must never be presented as exchange data.
    """

    create_database()

    conn = _connect()

    row = conn.execute(
        "SELECT scope, status, detail, checked_at FROM data_provenance WHERE scope = ?",
        (scope,)
    ).fetchone()

    conn.close()

    if not row:
        return {
            "scope": scope,
            "status": UNVERIFIED,
            "detail": "No provenance recorded — treat these bars as unverified.",
            "checked_at": None,
        }

    return {
        "scope": row[0], "status": row[1],
        "detail": row[2], "checked_at": row[3]
    }


def save_screen_run(payload):
    """Persist a completed screening run; returns its id."""

    create_database()

    conn = _connect()

    cursor = conn.execute(
        "INSERT INTO screen_runs (started_at, finished_at, payload) VALUES (?, ?, ?)",
        (
            payload.get("started_at"),
            payload.get("finished_at"),
            json.dumps(payload, ensure_ascii=False)
        )
    )

    run_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return run_id


def load_latest_screen_run():
    create_database()
    conn = _connect()
    row = conn.execute(
        "SELECT payload FROM screen_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        return None

    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


# ==============================
# PAPER TRADING
# ==============================
#
# A paper run is a real portfolio in every respect except that no order is
# sent. These functions exist because printing it is not enough: a paper
# account whose history dies with the process proves nothing about anything,
# and the whole value of paper trading is the record it accumulates.
#
# Nothing here ever rewrites a past row with better data. A position opened on
# Sunday was opened on what was knowable on Sunday, and improving it on Tuesday
# is exactly the look-ahead the backtester spends its design avoiding.


def save_paper_position(account, position):
    """Upsert one open paper position."""

    create_database()

    conn = _connect()

    conn.execute("""
    INSERT INTO paper_positions
        (account, ins_code, symbol, shares, entry_price, entry_date,
         stop_price, target_price, peak_price, atr_unit, opened_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (account, ins_code) DO UPDATE SET
        symbol = excluded.symbol, shares = excluded.shares,
        entry_price = excluded.entry_price, entry_date = excluded.entry_date,
        stop_price = excluded.stop_price, target_price = excluded.target_price,
        peak_price = excluded.peak_price, atr_unit = excluded.atr_unit
    """, (
        account, str(position["ins_code"]), position.get("symbol"),
        position.get("shares"), position.get("entry_price"),
        str(position.get("entry_date")), position.get("stop_price"),
        position.get("target_price"), position.get("peak_price"),
        position.get("atr_unit"),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))

    conn.commit()
    conn.close()

    return 1


def load_paper_positions(account):
    create_database()

    conn = _connect()

    rows = conn.execute("""
        SELECT ins_code, symbol, shares, entry_price, entry_date, stop_price,
               target_price, peak_price, atr_unit, opened_at
        FROM paper_positions WHERE account = ? ORDER BY entry_date
    """, (account,)).fetchall()

    conn.close()

    return [
        {
            "ins_code": row[0], "symbol": row[1], "shares": row[2],
            "entry_price": row[3], "entry_date": row[4], "stop_price": row[5],
            "target_price": row[6], "peak_price": row[7], "atr_unit": row[8],
            "opened_at": row[9],
        }
        for row in rows
    ]


def delete_paper_position(account, ins_code):
    create_database()

    conn = _connect()
    conn.execute("DELETE FROM paper_positions WHERE account = ? AND ins_code = ?",
                 (account, str(ins_code)))
    conn.commit()
    conn.close()


def record_paper_trade(account, trade):
    """Append one paper trade. Appends only -- rows are never updated."""

    create_database()

    conn = _connect()

    conn.execute("""
    INSERT INTO paper_trades
        (account, ins_code, symbol, signal, entry_date, entry_price, shares,
         stop_price, target_price, exit_date, exit_price, pnl, pnl_pct,
         holding_days, reason, recorded_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        account, str(trade.get("ins_code")), trade.get("symbol"),
        trade.get("signal"), str(trade.get("entry_date")),
        trade.get("entry_price"), trade.get("shares"), trade.get("stop_price"),
        trade.get("target_price"),
        str(trade.get("exit_date")) if trade.get("exit_date") else None,
        trade.get("exit_price"), trade.get("pnl"), trade.get("pnl_pct"),
        trade.get("holding_days"), trade.get("reason"),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))

    conn.commit()
    conn.close()

    return 1


def load_paper_trades(account, limit=None):
    create_database()

    conn = _connect()

    query = """
        SELECT id, ins_code, symbol, signal, entry_date, entry_price, shares,
               stop_price, target_price, exit_date, exit_price, pnl, pnl_pct,
               holding_days, reason, recorded_at
        FROM paper_trades WHERE account = ? ORDER BY entry_date, id
    """

    params = [account]

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    conn.close()

    keys = ("id", "ins_code", "symbol", "signal", "entry_date", "entry_price",
            "shares", "stop_price", "target_price", "exit_date", "exit_price",
            "pnl", "pnl_pct", "holding_days", "reason", "recorded_at")

    return [dict(zip(keys, row)) for row in rows]


def close_paper_trade(account, ins_code, entry_date, exit_date, exit_price,
                      pnl, pnl_pct, holding_days, reason):
    """Fill in the exit half of an open paper trade row."""

    create_database()

    conn = _connect()

    conn.execute("""
        UPDATE paper_trades
        SET exit_date = ?, exit_price = ?, pnl = ?, pnl_pct = ?,
            holding_days = ?, reason = ?
        WHERE account = ? AND ins_code = ? AND entry_date = ?
          AND exit_date IS NULL
    """, (str(exit_date), exit_price, pnl, pnl_pct, holding_days, reason,
          account, str(ins_code), str(entry_date)))

    conn.commit()
    conn.close()


def save_paper_equity(account, date, cash, holdings_value, portfolio_value,
                      positions, note=None):
    create_database()

    conn = _connect()

    conn.execute("""
    INSERT INTO paper_equity
        (account, date, cash, holdings_value, portfolio_value, positions, note)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (account, date) DO UPDATE SET
        cash = excluded.cash, holdings_value = excluded.holdings_value,
        portfolio_value = excluded.portfolio_value,
        positions = excluded.positions, note = excluded.note
    """, (account, str(date), cash, holdings_value, portfolio_value,
          positions, note))

    conn.commit()
    conn.close()


def load_paper_equity(account):
    create_database()

    conn = _connect()

    rows = conn.execute("""
        SELECT date, cash, holdings_value, portfolio_value, positions, note
        FROM paper_equity WHERE account = ? ORDER BY date
    """, (account,)).fetchall()

    conn.close()

    return [
        {"date": r[0], "cash": r[1], "holdings_value": r[2],
         "portfolio_value": r[3], "positions": r[4], "note": r[5]}
        for r in rows
    ]


def save_paper_order(account, order):
    """Insert a pending order, or update one that already has an id."""

    create_database()

    conn = _connect()

    if order.get("id"):
        conn.execute("""
            UPDATE paper_orders
            SET filled_shares = ?, average_price = ?, status = ?
            WHERE id = ?
        """, (order.get("filled_shares", 0), order.get("average_price"),
              order.get("status", "open"), order["id"]))
        new_id = order["id"]
    else:
        cursor = conn.execute("""
        INSERT INTO paper_orders
            (account, ins_code, symbol, side, shares, filled_shares,
             average_price, created_date, eligible_from, expires_on, status,
             reason, stop_price, target_price, atr_unit, entry_close,
             recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            account, str(order["ins_code"]), order.get("symbol"),
            order["side"], order.get("shares"), order.get("filled_shares", 0),
            order.get("average_price"), str(order.get("created_date")),
            str(order.get("eligible_from")), str(order.get("expires_on")),
            order.get("status", "open"), order.get("reason"),
            order.get("stop_price"), order.get("target_price"),
            order.get("atr_unit"), order.get("entry_close"),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))
        new_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return new_id


def load_paper_orders(account, status="open"):
    create_database()

    conn = _connect()

    query = """
        SELECT id, ins_code, symbol, side, shares, filled_shares,
               average_price, created_date, eligible_from, expires_on, status,
               reason, stop_price, target_price, atr_unit, entry_close
        FROM paper_orders WHERE account = ?
    """

    params = [account]

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY id"

    rows = conn.execute(query, params).fetchall()

    conn.close()

    keys = ("id", "ins_code", "symbol", "side", "shares", "filled_shares",
            "average_price", "created_date", "eligible_from", "expires_on",
            "status", "reason", "stop_price", "target_price", "atr_unit",
            "entry_close")

    return [dict(zip(keys, row)) for row in rows]


def paper_accounts():
    create_database()

    conn = _connect()

    rows = conn.execute(
        "SELECT DISTINCT account FROM paper_equity "
        "UNION SELECT DISTINCT account FROM paper_trades").fetchall()

    conn.close()

    return sorted(row[0] for row in rows if row[0])


def database_summary():
    """Per-symbol coverage, used by the admin panel status strip."""

    if not os.path.exists(DATABASE_NAME):
        return []

    create_database()

    conn = _connect()

    rows = conn.execute("""
    SELECT m.symbol,
           COUNT(*),
           MIN(m.date),
           MAX(m.date),
           f.fetched_at,
           f.source
    FROM market_data m
    LEFT JOIN fetch_log f ON f.symbol = m.symbol
    GROUP BY m.symbol
    ORDER BY m.symbol
    """).fetchall()

    conn.close()

    return [
        {
            "symbol": r[0],
            "bars": r[1],
            "first_date": r[2],
            "last_date": r[3],
            "fetched_at": r[4],
            "source": r[5] or "unknown"
        }
        for r in rows
    ]


def history_coverage():
    """Bars and date span of price_history, the table every analysis reads.

    The status strip's numbers. database_summary() above counts the legacy
    market_data table -- five hand-picked symbols, newest bar 2026-08-09 --
    and the strip printed its 1,505 bars as the size of the database. One
    aggregate over the whole table, ~0.5 s on ~2 million rows, so the panel
    memoises it rather than paying it per page load.
    """

    if not os.path.exists(DATABASE_NAME):
        return {"bars": 0, "first_date": None, "last_date": None}

    create_database()

    conn = _connect()

    bars, first, last = conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM price_history"
    ).fetchone()

    conn.close()

    return {"bars": bars or 0, "first_date": first, "last_date": last}
