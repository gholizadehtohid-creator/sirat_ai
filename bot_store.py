"""The Bale bot's own store: users, their holdings, the outbox, the log, and
the settings (token included).

WHY A STORE OF ITS OWN (2026-11-24)
------------------------------------
The owner gives the bot to other people. Their identities, their lists of
holdings and the bot's token are not market data and must never reach the
public repository: everything here lives in data/bale_bot.db and
data/bale_bot.json, both git-ignored (data/bale_bot*). The token is never
returned by any function that feeds the admin page -- only whether it is set
and its last four characters.

WHO MAY DO WHAT
----------------
A user who sends /start is `pending` until the admin approves them
(`approved`) or refuses (`blocked`). An approved user with role `admin`
also receives the join requests and may approve from the chat. The admin
page (/admin/bot, served only on 127.0.0.1) writes here; the bot thread
reads here and sends what the page queued in the outbox, so the page never
needs the token or the network.

Every function takes an optional `path` so the test suite never opens the
live store (tests/__init__.py makes the tree's data/*.db read-only).
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "data", "bale_bot.db")
CONFIG_PATH = os.path.join(ROOT, "data", "bale_bot.json")

PENDING, APPROVED, BLOCKED = "pending", "approved", "blocked"
STATUSES = (PENDING, APPROVED, BLOCKED)
USER, ADMIN = "user", "admin"
ROLES = (USER, ADMIN)

DEFAULT_CONFIG = {
    "token": "",
    "enabled": False,
    "api_base": "https://tapi.bale.ai",
    "summary_time": "19:30",        # Tehran; after the evening data run
    "alerts_time": "20:00",
    "daily_quota": 60,               # requests a day for an approved user
    "pdf_quota": 5,                  # PDF reports a day
    "max_holdings": 40,
}

TEHRAN = timezone(timedelta(hours=3, minutes=30))

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users (
        chat_id      INTEGER PRIMARY KEY,
        user_id      INTEGER,
        username     TEXT,
        first_name   TEXT,
        last_name    TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        role         TEXT NOT NULL DEFAULT 'user',
        created_at   TEXT,
        approved_at  TEXT,
        last_seen    TEXT,
        state        TEXT,
        daily_quota  INTEGER,
        pdf_quota    INTEGER,
        summary_on   INTEGER NOT NULL DEFAULT 1,
        alerts_on    INTEGER NOT NULL DEFAULT 1,
        note         TEXT)""",
    """CREATE TABLE IF NOT EXISTS holdings (
        chat_id   INTEGER NOT NULL,
        line_no   INTEGER NOT NULL,
        line      TEXT NOT NULL,
        PRIMARY KEY (chat_id, line_no))""",
    """CREATE TABLE IF NOT EXISTS usage (
        chat_id  INTEGER NOT NULL,
        day      TEXT NOT NULL,
        kind     TEXT NOT NULL,
        count    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, day, kind))""",
    """CREATE TABLE IF NOT EXISTS events (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        at       TEXT NOT NULL,
        chat_id  INTEGER,
        kind     TEXT NOT NULL,
        ok       INTEGER NOT NULL,
        ms       INTEGER,
        detail   TEXT)""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id    INTEGER NOT NULL,
        text       TEXT NOT NULL,
        markup     TEXT,
        created_at TEXT NOT NULL,
        sent_at    TEXT,
        error      TEXT,
        batch      TEXT)""",
    """CREATE TABLE IF NOT EXISTS sent_alerts (
        chat_id  INTEGER NOT NULL,
        key      TEXT NOT NULL,
        sent_at  TEXT NOT NULL,
        PRIMARY KEY (chat_id, key))""",
    """CREATE TABLE IF NOT EXISTS jobs (
        name     TEXT PRIMARY KEY,
        last_day TEXT,
        last_at  TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_outbox_unsent ON outbox (sent_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_at ON events (at)",
)

_LOCK = threading.Lock()


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tehran_now():
    return datetime.now(TEHRAN)


def today(now=None):
    return (now or tehran_now()).strftime("%Y-%m-%d")


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def initialise(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for statement in SCHEMA:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()


# ---- settings ------------------------------------------------------------------

def load_config(path=None):
    out = dict(DEFAULT_CONFIG)
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            out.update(json.load(fh) or {})
    except (OSError, ValueError):
        pass
    return out


def save_config(changes, path=None):
    """Merge `changes` into the stored settings; the token only if given."""

    path = path or CONFIG_PATH
    current = load_config(path)
    for key, value in (changes or {}).items():
        if key not in DEFAULT_CONFIG:
            continue
        if key == "token" and not str(value or "").strip():
            continue                       # an empty field keeps the token
        current[key] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as fh:
        json.dump(current, fh, ensure_ascii=False, indent=1)
    os.replace(partial, path)
    return public_config(current)


def public_config(config=None):
    """The settings as the admin page may see them: never the token."""

    config = dict(config if config is not None else load_config())
    token = str(config.pop("token", "") or "")
    config["token_set"] = bool(token)
    config["token_hint"] = ("…" + token[-4:]) if len(token) >= 8 else ""
    return config


# ---- users ------------------------------------------------------------------------

def get_user(chat_id, path=None):
    conn = connect(path)
    try:
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?",
                           (int(chat_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def touch_user(chat_id, sender=None, path=None):
    """Create the user as pending if new; refresh their names and last_seen.

    Returns (user, created).
    """

    sender = sender or {}
    stamp = now_utc()
    with _LOCK:
        conn = connect(path)
        try:
            row = conn.execute("SELECT chat_id FROM users WHERE chat_id = ?",
                               (int(chat_id),)).fetchone()
            created = row is None
            if created:
                conn.execute(
                    "INSERT INTO users (chat_id, user_id, username, first_name, "
                    "last_name, status, role, created_at, last_seen) VALUES "
                    "(?, ?, ?, ?, ?, 'pending', 'user', ?, ?)",
                    (int(chat_id), sender.get("id"), sender.get("username"),
                     sender.get("first_name"), sender.get("last_name"), stamp,
                     stamp))
            else:
                conn.execute(
                    "UPDATE users SET last_seen = ?, username = COALESCE(?, "
                    "username), first_name = COALESCE(?, first_name), last_name "
                    "= COALESCE(?, last_name) WHERE chat_id = ?",
                    (stamp, sender.get("username"), sender.get("first_name"),
                     sender.get("last_name"), int(chat_id)))
            conn.commit()
        finally:
            conn.close()
    return get_user(chat_id, path), created


def set_status(chat_id, status, path=None):
    if status not in STATUSES:
        raise ValueError("unknown status %r" % status)
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute(
                "UPDATE users SET status = ?, approved_at = CASE WHEN ? = "
                "'approved' THEN ? ELSE approved_at END WHERE chat_id = ?",
                (status, status, now_utc(), int(chat_id)))
            conn.commit()
        finally:
            conn.close()
    return get_user(chat_id, path)


def set_role(chat_id, role, path=None):
    if role not in ROLES:
        raise ValueError("unknown role %r" % role)
    return _update(chat_id, {"role": role}, path)


def set_fields(chat_id, fields, path=None):
    allowed = {"daily_quota", "pdf_quota", "note", "summary_on", "alerts_on",
               "state"}
    return _update(chat_id, {k: v for k, v in fields.items() if k in allowed},
                   path)


def _update(chat_id, fields, path=None):
    if not fields:
        return get_user(chat_id, path)
    with _LOCK:
        conn = connect(path)
        try:
            sets = ", ".join("%s = ?" % k for k in fields)
            conn.execute("UPDATE users SET %s WHERE chat_id = ?" % sets,
                         list(fields.values()) + [int(chat_id)])
            conn.commit()
        finally:
            conn.close()
    return get_user(chat_id, path)


def delete_user(chat_id, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            for table in ("users", "holdings", "usage", "sent_alerts"):
                conn.execute("DELETE FROM %s WHERE chat_id = ?" % table,
                             (int(chat_id),))
            conn.commit()
        finally:
            conn.close()


def list_users(status=None, path=None):
    conn = connect(path)
    try:
        sql = "SELECT * FROM users"
        args = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' " \
               "THEN 1 ELSE 2 END, created_at DESC"
        rows = [dict(r) for r in conn.execute(sql, args)]
        day = today()
        counts = {}
        for r in conn.execute("SELECT chat_id, kind, count FROM usage WHERE "
                              "day = ?", (day,)):
            counts.setdefault(r["chat_id"], {})[r["kind"]] = r["count"]
        holdings = {r["chat_id"]: r["n"] for r in conn.execute(
            "SELECT chat_id, COUNT(*) AS n FROM holdings GROUP BY chat_id")}
    finally:
        conn.close()
    for row in rows:
        row["today"] = counts.get(row["chat_id"], {})
        row["holdings"] = holdings.get(row["chat_id"], 0)
    return rows


def admins(path=None):
    return [u for u in list_users(APPROVED, path) if u["role"] == ADMIN]


# ---- holdings ---------------------------------------------------------------------

def get_holdings(chat_id, path=None):
    conn = connect(path)
    try:
        return [r["line"] for r in conn.execute(
            "SELECT line FROM holdings WHERE chat_id = ? ORDER BY line_no",
            (int(chat_id),))]
    finally:
        conn.close()


def set_holdings(chat_id, lines, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute("DELETE FROM holdings WHERE chat_id = ?", (int(chat_id),))
            conn.executemany(
                "INSERT INTO holdings (chat_id, line_no, line) VALUES (?, ?, ?)",
                [(int(chat_id), i, str(line)[:120]) for i, line in
                 enumerate(lines)])
            conn.commit()
        finally:
            conn.close()


# ---- quotas and the log -----------------------------------------------------------

def count_use(chat_id, kind, path=None):
    """Add one use of `kind` today; return today's count after it."""

    day = today()
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute(
                "INSERT INTO usage (chat_id, day, kind, count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(chat_id, day, kind) DO UPDATE SET count = count + 1",
                (int(chat_id), day, kind))
            conn.commit()
            return conn.execute(
                "SELECT count FROM usage WHERE chat_id = ? AND day = ? AND kind = ?",
                (int(chat_id), day, kind)).fetchone()[0]
        finally:
            conn.close()


def used_today(chat_id, kind, path=None):
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT count FROM usage WHERE chat_id = ? AND day = ? AND kind = ?",
            (int(chat_id), today(), kind)).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def log_event(chat_id, kind, ok=True, ms=None, detail=None, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute(
                "INSERT INTO events (at, chat_id, kind, ok, ms, detail) VALUES "
                "(?, ?, ?, ?, ?, ?)",
                (now_utc(), chat_id, kind, 1 if ok else 0, ms,
                 (str(detail)[:300] if detail else None)))
            conn.commit()
        finally:
            conn.close()


def stats(path=None):
    conn = connect(path)
    try:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM users GROUP BY status")}
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        kinds = [dict(r) for r in conn.execute(
            "SELECT kind, COUNT(*) AS n, SUM(1 - ok) AS failed FROM events "
            "WHERE at >= ? GROUP BY kind ORDER BY n DESC", (since,))]
        errors = [dict(r) for r in conn.execute(
            "SELECT at, chat_id, kind, detail FROM events WHERE ok = 0 "
            "ORDER BY id DESC LIMIT 10")]
        outbox = conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL AND error IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"users": by_status, "last_24h": kinds, "recent_errors": errors,
            "outbox_waiting": outbox}


# ---- the outbox: what the admin page asks the bot to send ---------------------

def enqueue(chat_id, text, markup=None, batch=None, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute(
                "INSERT INTO outbox (chat_id, text, markup, created_at, batch) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(chat_id), str(text)[:3900],
                 json.dumps(markup, ensure_ascii=False) if markup else None,
                 now_utc(), batch))
            conn.commit()
        finally:
            conn.close()


def pending_outbox(limit=20, path=None):
    conn = connect(path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM outbox WHERE sent_at IS NULL AND error IS NULL "
            "ORDER BY id LIMIT ?", (limit,))]
    finally:
        conn.close()
    for row in rows:
        row["markup"] = json.loads(row["markup"]) if row.get("markup") else None
    return rows


def mark_sent(outbox_id, error=None, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            if error:
                conn.execute("UPDATE outbox SET error = ? WHERE id = ?",
                             (str(error)[:300], int(outbox_id)))
            else:
                conn.execute("UPDATE outbox SET sent_at = ? WHERE id = ?",
                             (now_utc(), int(outbox_id)))
            conn.commit()
        finally:
            conn.close()


def broadcast(text, audience="approved", path=None):
    """Queue `text` for every approved user (or every admin); return count."""

    users = list_users(APPROVED, path)
    if audience == "admins":
        users = [u for u in users if u["role"] == ADMIN]
    batch = "broadcast-" + now_utc()
    for user in users:
        enqueue(user["chat_id"], text, batch=batch, path=path)
    return len(users)


# ---- alerts and jobs --------------------------------------------------------------

def alert_sent(chat_id, key, path=None):
    conn = connect(path)
    try:
        return conn.execute(
            "SELECT 1 FROM sent_alerts WHERE chat_id = ? AND key = ?",
            (int(chat_id), key)).fetchone() is not None
    finally:
        conn.close()


def mark_alert(chat_id, key, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sent_alerts (chat_id, key, sent_at) VALUES "
                "(?, ?, ?)", (int(chat_id), key, now_utc()))
            conn.commit()
        finally:
            conn.close()


def job_ran(name, day, path=None):
    conn = connect(path)
    try:
        row = conn.execute("SELECT last_day FROM jobs WHERE name = ?",
                           (name,)).fetchone()
        return bool(row and row[0] == day)
    finally:
        conn.close()


def mark_job(name, day, path=None):
    with _LOCK:
        conn = connect(path)
        try:
            conn.execute(
                "INSERT INTO jobs (name, last_day, last_at) VALUES (?, ?, ?) ON "
                "CONFLICT(name) DO UPDATE SET last_day = excluded.last_day, "
                "last_at = excluded.last_at", (name, day, now_utc()))
            conn.commit()
        finally:
            conn.close()
