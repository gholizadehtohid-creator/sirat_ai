"""What ran, what did not, and what to do about it -- one card in the panel.

    python health.py            print the report (reads only)

WHY (2026-11-23, the review the user asked to be applied)
---------------------------------------------------------
Nothing told the owner when a night's run failed or the laptop was off. Over
the fourteen evenings 11-09..11-22 five had no run at all and two reached
their last step; of eleven sessions one has a usable order-book capture;
five of the last ten forward-log entries are "missed" -- and the panel's
strip said the data was present and current throughout. The only record was
logs nobody reads and a LastTaskResult nobody queries.

WHAT IT READS
-------------
Facts already stored, and nothing else: the tail of each scheduled task's
log (its start marker, its "===== done" line, its "step ...: exit N"
lines), the index calendar against the order-book captures, the forward
log's newest entries, the newest backup's manifest, the power source, what
bootstrap.status() calls behind, the feed watch's own notes, and the
monthly-history backlog. No collector is started, no request leaves the
machine, and Task Scheduler is not asked anything: a page load must not
spawn schtasks or PowerShell.

Every item is {key, level, text, action}: level ok / warn / bad (or
unknown when it could not be read), one Persian sentence, and what to do.
report() never raises -- a probe that fails becomes an "unknown" item --
and is cached for CACHE_SECONDS, the slow parts for longer.

RECENT FAILURES ALARM, OLD ONES ARE COUNTED. A session without a capture
cannot be repaired, so a level taken from "any of the last ten" would stay
red for two weeks and be ignored by the third day. The level comes from the
newest session (bad) and the newest three (warn); the sentence still gives
the count over ten.
"""

import ctypes
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(ROOT, "logs")
FORWARD_LOG = os.path.join(ROOT, "backtest_output", "forward_predictions.jsonl")

OK, WARN, BAD, UNKNOWN = "ok", "warn", "bad", "unknown"
_RANK = {UNKNOWN: 0, OK: 0, WARN: 1, BAD: 2}

# No log is read whole: the evening log is rotated at 2 MB and a campaign
# day writes a thousand lines. 256 KB holds any one run's lines.
TAIL_BYTES = 256 * 1024
FORWARD_TAIL_BYTES = 1024 * 1024   # an entry is ~40 KB (750 names); ten fit
CACHE_SECONDS = 60
SLOW_CACHE_SECONDS = 15 * 60

SESSIONS = 10                  # how far back the counts look
RECENT = 3                     # how far back a miss still warns
THIN_CAPTURES = 5              # a full session has 10-28 (2026-10-31..11-11)

# Tehran Stock Exchange trades Saturday to Wednesday (bootstrap.TRADING_WEEKDAYS).
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}

# The four scheduled tasks. `run_hours`: how long a run may still be going
# before a missing "done" line means it was killed (the evening: retries to
# 22:30, then 1.5 h + 0.75 h + 0.5 h of Codal; the watch: its PT50M limit;
# the campaign: --hours 23.5). `quiet_minutes`: the longest a live run
# leaves its log untouched (a 45-minute deferred retry; a 240-minute
# campaign cool-off) -- quieter than that without a done line is a dead run
# even inside the window. `warn_hours` / `bad_hours`: how long without any
# start before the task counts as not running at all.
TASKS = (
    {"key": "evening", "log": "task_evening.log", "label": "کار شبانه",
     "task": "SIRAT-evening", "run_hours": 8, "quiet_minutes": 75,
     "warn_hours": 26, "bad_hours": 50, "killed": BAD},
    {"key": "session", "log": "task_session.log", "label": "پایش جلسه",
     "task": "SIRAT-session", "run_hours": 9.5, "quiet_minutes": None,
     "warn_hours": None, "bad_hours": None, "killed": BAD},
    {"key": "watch", "log": "task_watch.log", "label": "پایش اطلاعیه‌های کدال",
     "task": "SIRAT-codal-watch", "run_hours": 1, "quiet_minutes": 55,
     "warn_hours": 5, "bad_hours": 26, "killed": WARN},
    {"key": "backfill", "log": "task_backfill.log", "label": "کارزار کدال",
     "task": "SIRAT-codal-backfill", "run_hours": 24, "quiet_minutes": 250,
     "warn_hours": 26, "bad_hours": 50, "killed": WARN},
)

# "===== Mon 11/23/2026 19:00:02.02 =====" -- cmd's %date% %time% on this
# machine; an hour below ten is space-padded. A locale that writes the date
# another way simply does not match, and the task reads "unknown".
_START = re.compile(
    r"^===== \w{3} (\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")
_DONE = re.compile(
    r"^===== done \w{3} (\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})"
    r"[^:]*:\s*(.*?)\s*=====")
_STEP = re.compile(r"^step (\S+): exit (-?\d+)")
_NUMBER = re.compile(r"-?\d+")

_cache_lock = threading.Lock()
_cache = {"at": None, "value": None}
_slow = {}


def _item(key, level, text, action=""):
    return {"key": key, "level": level, "text": text, "action": action}


# ==============================
# TIME, IN THE PANEL'S WORDS
# ==============================

def _jalali(day):
    try:
        import jalali
        _jy, jm, jd = jalali.today_jalali(day)
        return "%02d/%02d" % (jm, jd)
    except Exception:                                           # noqa: BLE001
        return day.strftime("%m-%d")


def _when(stamp, now):
    """«امروز ساعت 19:00», «دیروز ساعت ...», or the Jalali month/day."""

    clock = stamp.strftime("%H:%M")
    days = (now.date() - stamp.date()).days
    if days == 0:
        return "امروز ساعت %s" % clock
    if days == 1:
        return "دیروز ساعت %s" % clock
    return "%s ساعت %s" % (_jalali(stamp), clock)


def _day(text):
    """A stored 'YYYY-MM-DD...' as the Jalali month/day."""

    try:
        return _jalali(datetime.strptime(str(text)[:10], "%Y-%m-%d"))
    except ValueError:
        return str(text)[:10]


# ==============================
# THE SCHEDULED TASKS' LOGS
# ==============================

def _tail(path, limit=TAIL_BYTES):
    """(the file's last `limit` bytes as text, its mtime); ('', None) if
    it cannot be read. Opened for reading only, which a task appending to
    the same log with >> is not disturbed by."""

    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            data = handle.read()
        return (data.decode("utf-8", errors="replace"),
                datetime.fromtimestamp(os.path.getmtime(path)))
    except OSError:
        return "", None


def _stamp(match):
    month, day, year, hour, minute, second = (int(match.group(i))
                                              for i in range(1, 7))
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def parse_run(text):
    """The newest run in a task log's tail.

    {'start', 'done', 'done_text', 'failed_steps'}: the newest start marker,
    the done line after it (None: the run has not finished, or was killed)
    and the steps of that run whose exit code was not 0.
    """

    start = done = done_text = None
    failed = []
    for line in text.splitlines():
        match = _DONE.match(line)
        if match:
            done, done_text = _stamp(match), match.group(7)
            continue
        match = _START.match(line)
        if match:
            start, done, done_text, failed = _stamp(match), None, None, []
            continue
        match = _STEP.match(line)
        if match and match.group(2) != "0":
            failed.append(match.group(1))
    return {"start": start, "done": done, "done_text": done_text,
            "failed_steps": failed}


def _done_code(text):
    """The number a done line reports: failed steps, or the exit code."""

    found = _NUMBER.findall(text or "")
    try:
        return int(found[-1] if "exit" in (text or "") else found[0])
    except (IndexError, ValueError):
        return 0


def task_item(spec, text, mtime, now, heartbeat=None):
    """One scheduled task, from its log's tail.

    `heartbeat`: for the session watch, when the monitor last wrote a tick
    (monitor_ticks) -- its log can be silent for hours while it captures.
    """

    key, label = "task:" + spec["key"], spec["label"]
    rerun = ("لپ‌تاپ را روشن و به برق وصل نگه دارید؛ برای اجرای دوباره: "
             "Task Scheduler ← %s ← Run." % spec["task"])

    if not text.strip():
        return _item(key, UNKNOWN, "%s: گزارشی برای خواندن نبود." % label)

    run = parse_run(text)
    start, done = run["start"], run["done"]

    if done and (start is None or done >= start):
        last = start or done
        hours = (now - last).total_seconds() / 3600
        if spec["bad_hours"] and hours > spec["bad_hours"]:
            return _item(key, BAD, "%s از %s اجرا نشده است."
                         % (label, _when(last, now)), rerun)
        if spec["warn_hours"] and hours > spec["warn_hours"]:
            return _item(key, WARN, "%s از %s اجرا نشده است."
                         % (label, _when(last, now)), rerun)
        code = _done_code(run["done_text"])
        if code and spec["key"] == "evening":
            steps = "، ".join(run["failed_steps"]) or "نام گام‌ها در گزارش"
            return _item(
                key, WARN,
                "%s %s تمام شد، ولی %d گام ناموفق بود: %s."
                % (label, _when(done, now), code, steps),
                "جزئیات در logs\\%s. گام‌های کدال که به محدودیت کدال (429) "
                "یا سقف زمان می‌خورند با کد 3 تمام می‌شوند و شب بعد ادامه "
                "می‌یابند." % spec["log"])
        if code:
            return _item(key, WARN, "%s %s با کد خطای %d تمام شد."
                         % (label, _when(done, now), code),
                         "جزئیات در logs\\%s." % spec["log"])
        return _item(key, OK, "%s: آخرین اجرا %s کامل شد."
                     % (label, _when(done, now)))

    if start is None:
        # The run began before the tail: only its liveness can be judged.
        quiet = spec["quiet_minutes"] or 60
        if mtime and now - mtime <= timedelta(minutes=quiet):
            return _item(key, OK, "%s در حال اجراست." % label)
        return _item(key, UNKNOWN, "%s: وضعیت آخرین اجرا روشن نیست." % label)

    # Started, and no done line after it: still going, or killed.
    alive = now - start <= timedelta(hours=spec["run_hours"])
    if alive and spec["key"] == "session":
        # monitor.HARD_STOP ends a session run at 18:00 whatever the feed says.
        alive = now.date() == start.date() and (now.hour, now.minute) < (18, 10)
    if alive and spec["quiet_minutes"] and mtime:
        alive = now - mtime <= timedelta(minutes=spec["quiet_minutes"])
    if alive and heartbeat is not None and _session_hours(now):
        alive = now - heartbeat <= timedelta(minutes=10)

    if alive:
        return _item(key, OK, "%s از %s در حال اجراست."
                     % (label, _when(start, now)))
    return _item(
        key, spec["killed"],
        "%s: اجرای %s بدون خط پایان قطع شده است (خاموشی، خواب، یا "
        "بسته‌شدن)." % (label, _when(start, now)), rerun)


def _session_hours(now):
    return (now.weekday() in TRADING_WEEKDAYS
            and (9, 5) <= (now.hour, now.minute) < (12, 30))


def session_start_item(text, now):
    """During a session with no monitor started today: the one moment a
    missed start can still be repaired by hand."""

    if not _session_hours(now) or (now.hour, now.minute) < (9, 15):
        return None
    start = parse_run(text)["start"]
    if start is not None and start.date() == now.date():
        return None
    return _item(
        "task:session-today", BAD,
        "بازار باز است و پایش جلسه‌ی امروز شروع نشده است.",
        "همین حالا: Task Scheduler ← SIRAT-session ← Run. تابلوی سفارش و "
        "جریان لحظه‌ای جلسه بعداً قابل بازیابی نیست.")


def tasks(now, logs=None, heartbeat=None):
    logs = logs or LOGS
    items = []
    for spec in TASKS:
        text, mtime = _tail(os.path.join(logs, spec["log"]))
        beat = heartbeat if spec["key"] == "session" else None
        items.append(task_item(spec, text, mtime, now, heartbeat=beat))
        if spec["key"] == "session":
            extra = session_start_item(text, now)
            if extra:
                items.append(extra)
    return items


# ==============================
# THE STORES
# ==============================

def _read_only(path):
    """A connection that cannot write, or None if the store is not there."""

    if not path or not os.path.isfile(path):
        return None
    uri = "file:%s?mode=ro" % quote(os.path.abspath(path).replace("\\", "/"),
                                    safe="/:")
    return sqlite3.connect(uri, uri=True, timeout=3)


def _market():
    import database
    return _read_only(database.DATABASE_NAME)


def market_facts():
    """(newest sessions, captures per day, the monitor's last tick).

    The sessions are the index calendar's, newest first: the index trades
    every session and nothing else does reliably (backtesting/pit.py).
    """

    import config
    conn = _market()
    if conn is None:
        return [], {}, None
    try:
        tedpix = config.INDICES["TEDPIX"]["insCode"]
        sessions = [str(r[0])[:10] for r in conn.execute(
            "SELECT date FROM index_history WHERE ins_code = ? "
            "ORDER BY date DESC LIMIT ?", (tedpix, SESSIONS))]
        captures, tick = {}, None
        if sessions:
            try:
                captures = dict(conn.execute(
                    "SELECT substr(captured_at, 1, 10), COUNT(*) "
                    "FROM depth_manifest WHERE captured_at >= ? GROUP BY 1",
                    (sessions[-1],)))
            except sqlite3.Error:
                captures = {}
        try:
            newest = conn.execute(
                "SELECT MAX(observed_at) FROM monitor_ticks").fetchone()[0]
            if newest:
                tick = (datetime.fromisoformat(newest)
                        .astimezone().replace(tzinfo=None))
        except (sqlite3.Error, ValueError):
            tick = None
        return sessions, captures, tick
    finally:
        conn.close()


def sessions_item(sessions, captures):
    """Sessions of the last ten without an intraday capture."""

    key = "sessions"
    if not sessions:
        return _item(key, UNKNOWN, "تقویم جلسه‌ها (شاخص) خوانده نشد.")
    count = lambda day: int(captures.get(day) or 0)             # noqa: E731
    missing = [d for d in sessions if count(d) == 0]
    thin = [d for d in sessions if 0 < count(d) < THIN_CAPTURES]
    full = [d for d in sessions if count(d) >= THIN_CAPTURES]

    if not missing and not thin:
        return _item(key, OK, "هر %d جلسه‌ی آخر ثبت لحظه‌ای دارد."
                     % len(sessions))

    text = "از %d جلسه‌ی آخر، %d جلسه ثبت لحظه‌ای (تابلوی سفارش) ندارد" % (
        len(sessions), len(missing))
    if thin:
        text += " و %d جلسه ناقص است" % len(thin)
    text += ("؛ آخرین جلسه‌ی کامل %s." % _day(full[0]) if full
             else "؛ هیچ‌کدام کامل نیست.")

    recent = sessions[:RECENT]
    if count(sessions[0]) == 0:
        level = BAD
    elif any(count(d) < THIN_CAPTURES for d in recent):
        level = WARN
    else:
        level = OK
    return _item(
        key, level, text,
        "پایش جلسه ساعت 08:40 شروع می‌شود؛ لپ‌تاپ باید پیش از آن روشن و به "
        "برق وصل باشد. داده‌ی لحظه‌ای جلسه‌ی ازدست‌رفته بازیابی نمی‌شود.")


def forward_records(path=None, limit=FORWARD_TAIL_BYTES):
    """The forward log's newest entries, oldest first: {'session', 'missed'}."""

    text, _mtime = _tail(path or FORWARD_LOG, limit)
    lines = text.splitlines()
    try:
        whole = os.path.getsize(path or FORWARD_LOG) <= limit
    except OSError:
        whole = True
    if not whole:
        lines = lines[1:]               # the tail began inside an entry
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("session"):
            records.append({"session": str(entry["session"])[:10],
                            "missed": entry.get("missed")})
    return records[-SESSIONS:]


def forward_item(records, newest_session=None):
    """Forward-log entries of the last ten that were missed."""

    key = "forward"
    action = ("ورودی هر جلسه باید همان شب، پیش از بازشدن بازار فردا نوشته "
              "شود؛ کار شبانه این کار را می‌کند و ورودی ازدست‌رفته جبران "
              "نمی‌شود.")
    if not records:
        return _item(key, UNKNOWN, "دفتر پیش‌بینی آینده‌نگر خوانده نشد.")

    missed = [r for r in records if r.get("missed")]
    newest = records[-1]
    pending = bool(newest_session) and newest["session"] < newest_session

    if not missed and not pending:
        return _item(key, OK, "هر %d ورودی آخر دفتر پیش‌بینی نوشته شده است."
                     % len(records))

    parts = []
    if missed:
        parts.append("از %d ورودی آخر دفتر پیش‌بینی، %d مورد ازدست‌رفته است"
                     % (len(records), len(missed)))
    if pending:
        parts.append("ورودی جلسه‌ی %s هنوز نوشته نشده است"
                     % _day(newest_session))
    if newest.get("missed"):
        level = BAD
    elif pending or any(r.get("missed") for r in records[-RECENT:]):
        level = WARN
    else:
        level = OK
    return _item(key, level, "؛ ".join(parts) + ".", action)


def backup_item(now, dest=None):
    """The newest backup's age."""

    import backup
    key = "backup"
    action = ("کار شبانه هر شب پشتیبان می‌گیرد (python backup.py). پشتیبان "
              "روی همین دیسک است؛ یک نسخه روی دیسک دیگر نگه دارید.")
    dest = dest or backup.DEST
    done = backup.nights(dest)
    if not done:
        return _item(key, BAD, "هیچ پشتیبانی پیدا نشد.", action)
    taken = None
    try:
        with open(os.path.join(dest, done[-1], backup.MANIFEST),
                  encoding="utf-8") as handle:
            taken = datetime.fromisoformat(json.load(handle)["taken_at"])
    except (OSError, ValueError, KeyError, TypeError):
        taken = None
    if taken is None:
        return _item(key, WARN, "پشتیبان %s خوانده نشد." % done[-1], action)
    hours = (now - taken).total_seconds() / 3600
    text = "آخرین پشتیبان %s گرفته شده است." % _when(taken, now)
    if hours > 72:
        return _item(key, BAD, text, action)
    if hours > 30:
        return _item(key, WARN, text, action)
    return _item(key, OK, text)


# ==============================
# THE MACHINE
# ==============================

class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong)]


def power_status():
    """{'on_battery', 'percent'} from GetSystemPowerStatus; None off Windows
    or when the system cannot say."""

    if os.name != "nt":
        return None
    try:
        status = _PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    if status.ACLineStatus not in (0, 1):
        return None
    percent = status.BatteryLifePercent
    return {"on_battery": status.ACLineStatus == 0,
            "percent": None if percent > 100 else int(percent)}


def power_item(status):
    key = "power"
    if status is None:
        return _item(key, UNKNOWN, "منبع برق خوانده نشد.")
    if not status["on_battery"]:
        return _item(key, OK, "لپ‌تاپ به برق وصل است.")
    percent = status.get("percent")
    text = ("لپ‌تاپ روی باتری است" + (" (%d٪)." % percent
                                       if percent is not None else "."))
    return _item(
        key, BAD if percent is not None and percent < 25 else WARN, text,
        "شارژر را وصل کنید: روی باتری، کارهای زمان‌بندی‌شده لپ‌تاپ را بیدار "
        "نمی‌کنند و با خالی‌شدن باتری (2026-11-22، ساعت 20:40) قطع می‌شوند.")


# ==============================
# WHAT THE COLLECTORS SAY
# ==============================

def data_item(status):
    """Session components bootstrap.status() calls behind, or absent."""

    import bootstrap
    key = "data"
    name = lambda n: bootstrap.STEP_LABELS.get(n, n).split("—")[0].strip()  # noqa: E731
    absent = [n for n, s in status.items() if not s.get("present")]
    behind = [n for n, s in status.items()
              if s.get("present") and s.get("behind")
              and n in bootstrap.SESSION_COMPONENTS]
    if absent:
        return _item(key, BAD, "هرگز دریافت نشده: %s."
                     % "، ".join(name(n) for n in absent),
                     "در پنل «دریافت داده‌های ناقص یا کهنه» را بزنید.")
    if not behind:
        return _item(key, OK, "شاخص، قیمت و حقیقی/حقوقی تا آخرین جلسه کامل است.")
    text = "داده‌ی آخرین جلسه کامل نیست: %s." % "، ".join(name(n) for n in behind)
    waiting = [n for n in behind if not status[n].get("needs_fetch")]
    if len(waiting) == len(behind):
        return _item(key, WARN, text,
                     "بورس هنوز آن را کامل منتشر نکرده و تلاش قبلی تازه است؛ "
                     "تلاش بعدی خودکار است (کار شبانه تا 22:30 دوباره می‌پرسد).")
    return _item(key, WARN, text,
                 "در پنل «دریافت داده‌های ناقص یا کهنه» را بزنید.")


def watch_item(notes, now):
    """The feed watch's own notes: a stall, and letters it gave up on."""

    key = "watch-notes"
    stall = notes.get("stall") or {}
    if stall.get("polls"):
        return _item(
            key, BAD,
            "پایش اطلاعیه‌های کدال %s نوبت پیاپی جلو نرفته است: %s"
            % (stall.get("polls"), str(stall.get("reason") or "")[:120]),
            "معمولاً محدودیت کدال یا قطع شبکه است؛ اگر تا فردا ادامه داشت "
            "logs\\task_watch.log را ببینید.")
    recent = 0
    for letter in notes.get("dead") or []:
        try:
            at = datetime.fromisoformat(letter.get("at"))
            if at.tzinfo is not None:
                at = at.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if now - at <= timedelta(days=7):
            recent += 1
    if recent:
        return _item(
            key, WARN,
            "%d اطلاعیه‌ی کدال در 7 روز گذشته پس از 8 تلاش کنار گذاشته شد."
            % recent,
            "python codal_watch.py --report فهرست و دلیل هر کدام را نشان "
            "می‌دهد.")
    return _item(key, OK, "پایش اطلاعیه‌های کدال جلو می‌رود.")


def history_facts():
    """(company-months still to download, companies). Local reads only:
    codal_monthly.history_due asks codal.ir nothing."""

    import codal_monthly
    due = codal_monthly.history_due()
    return sum(len(letters) for _code, letters in due), len(due)


_HISTORY_RAN = re.compile(r"^monthly history: (\d+) downloaded, (\d+) stored")


def history_progress(text):
    """What the history phase did in the newest FINISHED evening run.

    (downloaded, stored) from codal_monthly's own line; False when that run
    ended without the phase having run at all (the due pass ate the time:
    every evening to 2026-11-23); None when the tail holds no finished run.
    Not read off the store: the due pass walks a due company's older months
    too, two letters a night, so "old months stored lately" proves nothing
    about this phase (105 such reports in three days it never ran).
    """

    found = current = None
    for line in text.splitlines():
        if _DONE.match(line):
            found = current if current is not None else False
            continue
        if _START.match(line):
            current = None
            continue
        match = _HISTORY_RAN.match(line)
        if match:
            current = (int(match.group(1)), int(match.group(2)))
    return found


def history_item(months, companies, progress=None):
    key = "monthly-history"
    if not months:
        return _item(key, OK, "تاریخچه‌ی گزارش‌های ماهانه کامل است.")
    text = ("تاریخچه‌ی گزارش‌های ماهانه: %d گزارش از %d شرکت هنوز دانلود "
            "نشده" % (months, companies))
    action = ("گام codal-monthly کار شبانه آن را پر می‌کند (حداکثر 300 در هر "
              "شب)؛ اگر هر شب با کد 3 تمام می‌شود، کدال آن را محدود کرده است.")
    if progress is None:
        return _item(key, OK, text + ".")
    if progress is False:
        return _item(key, WARN, text + " و در آخرین کار شبانه نوبت به آن "
                                       "نرسید.", action)
    if not progress[0]:
        return _item(key, WARN, text + " و در آخرین کار شبانه چیزی از آن "
                                       "دانلود نشد.", action)
    return _item(key, OK, text + "؛ در آخرین کار شبانه %d گزارش دانلود شد."
                 % progress[0])


# ==============================
# THE REPORT
# ==============================

def _slowly(name, producer, seconds=SLOW_CACHE_SECONDS):
    """producer(), at most once per `seconds`."""

    hit = _slow.get(name)
    if hit and time.monotonic() - hit[0] < seconds:
        return hit[1]
    value = producer()
    _slow[name] = (time.monotonic(), value)
    return value


def _guard(key, label, probe):
    """probe() -> item or list of items; a failure is an "unknown" item."""

    try:
        found = probe()
    except Exception as exc:                                    # noqa: BLE001
        return [_item(key, UNKNOWN, "%s بررسی نشد (%s)."
                      % (label, type(exc).__name__))]
    if found is None:
        return []
    return found if isinstance(found, list) else [found]


def build(now=None):
    """Every probe, uncached. Never raises."""

    now = now or datetime.now()
    items = []

    facts = {}

    def market():
        facts["sessions"], facts["captures"], facts["tick"] = market_facts()

    _guard("sessions", "", market)

    items += _guard("task", "کارهای زمان‌بندی‌شده",
                    lambda: tasks(now, heartbeat=facts.get("tick")))
    items += _guard("power", "منبع برق", lambda: power_item(power_status()))

    def data():
        import bootstrap
        return data_item(bootstrap.status(max_age=120))

    items += _guard("data", "داده‌ی جلسه", data)
    items += _guard("sessions", "ثبت لحظه‌ای جلسه‌ها", lambda: sessions_item(
        facts.get("sessions") or [], facts.get("captures") or {}))
    items += _guard("forward", "دفتر پیش‌بینی", lambda: forward_item(
        forward_records(), (facts.get("sessions") or [None])[0]))
    items += _guard("backup", "پشتیبان", lambda: backup_item(now))

    def watch():
        import codal_watch
        return watch_item(codal_watch.notes(), now)

    items += _guard("watch-notes", "پایش کدال", watch)
    def history():
        months, companies = _slowly("history", history_facts)
        text, _mtime = _tail(os.path.join(LOGS, "task_evening.log"))
        return history_item(months, companies, history_progress(text))

    items += _guard("monthly-history", "تاریخچه‌ی ماهانه", history)

    worst = max((_RANK.get(i["level"], 0) for i in items), default=0)
    level = {2: BAD, 1: WARN}.get(worst, OK)
    attention = [i for i in items if i["level"] in (WARN, BAD)]
    summary = ("کارهای زمان‌بندی‌شده اجرا شده‌اند و چیزی نیاز به رسیدگی ندارد."
               if not attention else
               "%d مورد نیاز به رسیدگی دارد." % len(attention))
    return {"generated_at": now.isoformat(timespec="seconds"),
            "level": level, "summary": summary,
            "attention": len(attention), "items": items}


def report(fresh=False):
    """build(), cached for CACHE_SECONDS. What /api/health answers with."""

    with _cache_lock:
        at, value = _cache["at"], _cache["value"]
        if (not fresh and value is not None
                and time.monotonic() - at < CACHE_SECONDS):
            return value
        try:
            value = build()
        except Exception as exc:                                # noqa: BLE001
            value = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                     "level": OK, "attention": 0, "items": [],
                     "summary": "وضعیت سامانه خوانده نشد (%s)."
                                % type(exc).__name__}
        _cache["at"], _cache["value"] = time.monotonic(), value
        return value


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    result = build()
    print("%s  %s" % (result["level"].upper(), result["summary"]))
    for item in result["items"]:
        print("  [%-7s] %-18s %s" % (item["level"], item["key"], item["text"]))
        if item["action"] and item["level"] in (WARN, BAD):
            print("  %s -> %s" % (" " * 28, item["action"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
