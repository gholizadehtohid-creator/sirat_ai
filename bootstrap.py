"""Acquire whatever data is missing or stale, without being asked.

The panel used to assume someone had already run seed_import.py with the right
flags in the right order. That assumption fails in exactly the situation this
project is used in: the machine is behind a TUN-mode VPN, TSETMC is only
reachable through the physical NIC, and whether any given import succeeded
depends on network state that changes between runs. The result was a panel
showing empty tables and a message telling the user to go and run a command.

So the panel now fetches its own data. On startup it works out what is absent
or out of date and downloads it in the background, in dependency order, while
the UI stays responsive and reports progress.

Three rules this follows, because "fetch automatically" must not become
"fetch endlessly" or "invent something":

  ORDER.     The universe must exist before histories can be fetched for it,
             and sectors before sectors can be aggregated. Steps run in
             dependency order and a failed prerequisite skips its dependants
             rather than failing loudly on every one.

  BACK OFF.  A step that fails is not retried for RETRY_COOLDOWN. Without
             that, an unreachable network turns into a request flood -- and
             the one thing worse than no data is being rate-limited into
             having no data.

  NEVER INVENT.  A step that cannot fetch leaves the table as it was and says
             so. Nothing here writes a placeholder, and nothing marks data as
             real that has not passed authenticity.verify().

    python bootstrap.py            # fetch what is missing, in the foreground
    python bootstrap.py --status   # report what is missing, fetch nothing
    python bootstrap.py --force    # refetch everything regardless of age
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import config
import database
import settings

ROOT = os.path.dirname(os.path.abspath(__file__))

# Where the cross-process files below live. The suite points it at a scratch
# directory (tests/__init__.py) so a test never writes into the live tree.
RUNTIME_DIR = os.environ.get("SIRAT_RUNTIME_DIR") or os.path.join(ROOT, "data")

# Touched whenever any process -- the panel or the evening task -- stores a
# component that changed. The panel's cache key reads it (data_stamp), so data
# the evening task delivered is not answered from before it.
STAMP_PATH = os.path.join(RUNTIME_DIR, "bootstrap_stamp")

# Held while a process fetches, so the panel and the evening task never fetch
# the same ~1,600 per-symbol requests at once. The holder touches it every
# LOCK_HEARTBEAT; one untouched for LOCK_FRESH is a dead holder's and ignored.
# No PID probe: os.kill(pid, 0) TERMINATES the process on Windows.
RUN_LOCK_PATH = os.path.join(RUNTIME_DIR, "bootstrap_run.lock")
LOCK_HEARTBEAT = 60.0
LOCK_FRESH = timedelta(minutes=5)


# The minimum gap between two fetch attempts of the same component. This is
# NOT how old the data may be -- that is decided per component by `behind`
# below -- it is the floor on how often we are willing to go back to TSETMC.
MAX_AGE = {
    # The instrument list changes when companies list or delist: rarely.
    "universe": timedelta(days=7),
    # Intraday figures. Worth refreshing within a trading day.
    "snapshot": timedelta(hours=6),
    # Daily bars: at most twice a day, and only if a session is missing.
    "history": timedelta(hours=12),
    # Published index values, same cadence as bars.
    "indices": timedelta(hours=12),
    # Sector membership effectively never changes.
    "sectors": timedelta(days=30),
    # حقیقی/حقوقی gains a row per session, like bars -- but it costs one
    # request per symbol rather than one for the market, so it is the most
    # expensive recurring fetch here and is not attempted twice a day.
    "flow": timedelta(hours=20),
    # Codal filings. Companies report quarterly, so there is nothing new to
    # find most days; codal_sync does its own per-company pacing and this is
    # only the floor on how often the batch runs at all.
    "codal": timedelta(hours=6),
}

# Wall-clock ceiling for the steps that are one or two requests. A read
# timeout bounds each wait for the next byte, not a whole response, so a
# server that trickles -- or a Retry-After the session obeys -- can hold a
# request for hours: on 2026-11-04 the indices step (two requests, normally
# under a second each) ran 19,453 s before failing, and history and flow
# waited behind it. Past the ceiling the step is recorded as failed and the
# run moves on; the stuck request is left to finish in a daemon thread.
# History, flow, sectors and codal are hundreds of requests with their own
# per-request pacing and legitimately take an hour or more, so no ceiling.
STEP_DEADLINE_SECONDS = {
    "universe": 15 * 60,
    "snapshot": 15 * 60,
    "indices": 15 * 60,
}

# Tehran Stock Exchange trades Saturday to Wednesday.
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}          # Sat, Sun, Mon, Tue, Wed

# The session closes at 12:30 Tehran time; TSETMC's closing prices, index
# values and client-type figures are final by about 14:00 (local clock).
DATA_READY = (14, 0)


def _ready_today(now=None):
    """True once today's session figures are final, on a trading weekday."""

    now = now or datetime.now()
    return (now.weekday() in TRADING_WEEKDAYS
            and (now.hour, now.minute) >= DATA_READY)


def _ready_moment():
    """Today's DATA_READY as an aware instant, comparable with _now()."""

    local = datetime.now().astimezone()
    return local.replace(hour=DATA_READY[0], minute=DATA_READY[1],
                         second=0, microsecond=0)


def sessions_behind(newest, include_today=False):
    """Completed trading sessions we are missing since `newest`.

    Daily bars are stamped at midnight, so a bar for the last completed
    session is always "a day or two old" by wall clock -- judging staleness on
    age alone would refetch all 818 instruments on every single startup and
    still never be satisfied. What actually matters is whether a session has
    closed that we do not hold.

    Today counts only when the caller says it has happened: `include_today`.
    Excluding it unconditionally (until 2026-11-10) meant the 19:00 evening
    run never fetched the session that had closed six hours earlier -- it
    only ever caught up to yesterday -- so the forward prediction log wrote
    each session a day late, after the next session had traded, which is the
    one thing its admissibility rule refuses: every entry from 11-07 on was
    unusable. The callers decide what "happened" means: the index after
    DATA_READY, and the bars and flow only once the index shows the session
    (a holiday then costs two index requests, not 1,600 per-symbol ones).
    """

    if newest is None:
        return None

    today = datetime.now().date()
    day = newest.date() if hasattr(newest, "date") else newest

    missed = 0

    if include_today and day < today:
        missed += 1

    day = day + timedelta(days=1)

    while day < today:
        if day.weekday() in TRADING_WEEKDAYS:
            missed += 1
        day += timedelta(days=1)

    return missed


def _index_has_today():
    """Whether an index bar for today is stored: the session happened.

    The index trades every session and nothing else does reliably, which is
    why backtesting/pit.py builds its calendar from it too.
    """

    coverage = database.index_coverage()
    today = datetime.now().date()
    for spec in config.INDICES.values():
        stamp = _parse((coverage.get(spec["insCode"]) or {}).get("last_date"))
        if stamp is not None and stamp.date() >= today:
            return True
    return False

# The components that gain one row per trading session.
SESSION_COMPONENTS = ("indices", "history", "flow")

# A fetch of these made before today's figures were final cannot have found
# them, however recent. The snapshot joined the session components here on
# 2026-11-24: the panel opened at 11:25 kept that mid-session market watch --
# its prices, its P/Es -- for the rest of the day, because six hours had not
# passed. One request after the close replaces it with the final one.
AFTER_CLOSE = SESSION_COMPONENTS + ("snapshot",)

# What the panel fetches by itself, at startup and from the keeper: all but
# Codal. A Codal batch is minutes of paced requests that nothing on the
# session's cards waits for, and it ran first thing on every panel start
# (its coverage is always "behind"); the evening task, the refresh button
# and codal_sync's own schedule still run it.
AUTOMATIC = ("universe", "snapshot", "indices", "history", "sectors", "flow")

# What the keeper checks every KEEPER_INTERVAL while the panel runs.
KEEPER_COMPONENTS = ("snapshot",) + SESSION_COMPONENTS
KEEPER_INTERVAL = 600.0

# What a fresh fetch of each component changes the answer for: today's index
# bar says a session happened; new bars say which names owe flow.
DOWNSTREAM = {"indices": ("history", "flow"), "history": ("flow",)}

# A failed step waits this long before being attempted again.
RETRY_COOLDOWN = timedelta(minutes=10)

# Bars requested per instrument on an automatic history fetch.
HISTORY_BARS = 300

# What each step is called in the panel. Kept here rather than in the page so
# the label and the thing it names cannot drift apart.
STEP_LABELS = {
    "universe": "فهرست نمادها — instrument universe",
    "snapshot": "تصویر بازار — market snapshot",
    "indices": "سری شاخص‌ها — index series",
    "history": "سابقه قیمت — price history",
    "sectors": "صنایع — sector membership",
    "flow": "حقیقی/حقوقی — money flow",
    "codal": "صورت‌های مالی کدال — Codal filings",
}

# Roughly how much of a full run each step accounts for. A progress bar that
# gave the universe (one request) the same width as history (hundreds) would
# sit at 20% for the whole download and then jump. These weights exist only to
# make the bar move at a rate that matches the wait.
STEP_WEIGHT = {
    "universe": 1,
    "snapshot": 2,
    "indices": 1,
    "history": 12,
    "sectors": 10,
    "flow": 10,
    "codal": 6,
}

# A step is over -- whatever the outcome -- once it reports one of these.
SETTLED = {"fetched", "failed", "kept", "skipped", "cooling-off"}

# status() runs every probe, and three of them walk the whole price_history
# table: the equities with bars (instrument_counts, read by the universe and
# history probes), the newest equity bar, and the newest session an equity
# traded in (the flow probe). About 5 s on the live file (2026-11-11) -- far
# too expensive to recompute for a panel polling twice a second. Cache it
# briefly: the numbers it reports change on the timescale of a completed
# fetch, not of a poll.
STATUS_TTL = 5.0


_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "current": None,
    "planned": [],
    "steps": {},
    "item": None,
    "log": [],
}

_lock = threading.Lock()
_thread = None

_last_failure = {}

_status_lock = threading.Lock()
_status_cache = {"at": None, "value": None}
# The probe in flight, if any: a page load's five loaders each ran their own
# 2 s probe at once (the full test, 2026-11-12). Late callers wait for it.
_status_inflight = {"event": None}


def _now():
    return datetime.now(timezone.utc)


def _note(message):
    with _lock:
        _state["log"].append({
            "at": _now().isoformat(timespec="seconds"),
            "message": message,
        })
        _state["log"] = _state["log"][-60:]


def _set_item(done, total, label=None):
    """Report progress *within* the running step.

    `total` may be None when a step cannot know its size in advance -- the
    panel draws an indeterminate bar for that rather than inventing a
    denominator.
    """

    with _lock:
        _state["item"] = {"done": done, "total": total, "label": label}


def _parse(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# ==============================
# WHAT IS MISSING
# ==============================

def _age_behind(stamp, limit):
    """Behind if the stored data is older than `limit`."""

    if stamp is None:
        return True

    return (_now() - stamp) > limit


def _universe_state():
    counts = database.instrument_counts()
    rows = database.load_instruments(equities_only=False, limit=1)
    updated = _parse(rows[0]["updated_at"]) if rows else None

    return {
        "present": counts["total"] > 0,
        "detail": f"{counts['total']} instruments, {counts['equities']} equities",
        "updated_at": updated,
        "behind": _age_behind(updated, MAX_AGE["universe"]),
    }


def _snapshot_state():
    info = database.snapshot_info()
    captured = _parse(info["captured_at"])

    # Taken before today's session was final: the day's closing prices are
    # out, and this is the morning's market watch (AFTER_CLOSE).
    before_close = (captured is not None and _ready_today()
                    and captured < _ready_moment())

    return {
        "present": info["rows"] > 0,
        "detail": f"{info['rows']} rows"
                  + (", taken before today's close" if before_close else ""),
        "updated_at": captured,
        "behind": _age_behind(captured, MAX_AGE["snapshot"]) or before_close,
    }


def _history_state():
    counts = database.instrument_counts()
    covered = counts["with_history"]
    equities = counts["equities"] or 0

    # Recency is the newest bar we hold, not when we last fetched: a fetch that
    # returned nothing new must not read as fresh.
    #
    # OVER EQUITIES ONLY. This used market_turnover(), which sums every row in
    # price_history -- and gold.py fetches the bullion funds on its own
    # schedule. On 2026-08-24 those funds carried bars to 08-23 while every
    # single equity stopped at 08-19, so this reported "newest 2026-08-23,
    # behind: False" and the auto-fetcher never refetched. The panel told the
    # user the market data was current while every scanner, the screen and the
    # basket were reading a three-session-old market. Non-equity instruments
    # cannot vouch for equity freshness, and the query now says so.
    newest = database.newest_equity_bar()

    missed = sessions_behind(newest,
                             include_today=_ready_today() and _index_has_today())

    detail = f"{covered}/{equities} equities have bars"

    if newest is not None:
        detail += f", newest {newest.date()}"
        if missed:
            detail += f" ({missed} session(s) behind)"

    # The newest bar of ANY equity says nothing about the rest, the flaw fixed
    # for flow on 2026-11-10: update_history reports success when anything
    # was stored, so a run whose fetch failed for most names read "newest
    # <today>, current" and the names that failed waited for the next
    # session. The newest session's coverage decides as well.
    session, traded, held = _history_session_coverage(newest)
    thin = bool(traded) and held < traded * HISTORY_SESSION_COVERAGE
    if thin:
        detail += (f"; session {session}: {held}/{traded} names that traded "
                   f"the session before have its bar")

    return {
        "present": covered > 0 and covered >= equities * 0.9,
        "detail": detail,
        "updated_at": newest.replace(tzinfo=timezone.utc) if newest else None,
        "behind": bool(missed) or thin,
    }


# Share of the equities that traded in the session before the newest one
# which must hold a bar for the newest before the bars count as current.
# Measured over every session from 2025-01 to 2026-11-07: 1.0000; 0.994 at
# worst since, from names wrongly marked off the market watch.
HISTORY_SESSION_COVERAGE = 0.97


def _history_session_coverage(newest=None):
    """(newest equity session, names that traded the session before it,
    names among those holding a bar for it).

    "The session before" is read off the index calendar, not price_history:
    a MAX over two million bars costs ~2 s on every probe, the index lookup
    milliseconds. A session before it with no trades -- the 2026 closure --
    gives no names, and nothing to judge.
    """

    if newest is None:
        newest = database.newest_equity_bar()
    if newest is None:
        return None, 0, 0

    session = newest.strftime("%Y-%m-%d")
    tedpix = config.INDICES["TEDPIX"]["insCode"]

    conn = database._connect()
    try:
        previous = conn.execute(
            "SELECT MAX(date) FROM index_history WHERE ins_code = ? "
            "AND date < ?", (tedpix, session)).fetchone()[0]
        if not previous:
            return session, 0, 0
        traded, held = conn.execute("""
            SELECT COUNT(p.ins_code), COUNT(b.ins_code)
            FROM instruments i
            JOIN price_history p
              ON p.ins_code = i.ins_code AND p.date = ? AND p.volume > 0
            LEFT JOIN price_history b
              ON b.ins_code = i.ins_code AND b.date = ?
            WHERE i.is_equity = 1
        """, (str(previous)[:10], session)).fetchone()
    finally:
        conn.close()
    return session, int(traded or 0), int(held or 0)


def _indices_state():
    coverage = database.index_coverage()

    wanted = [spec["insCode"] for spec in config.INDICES.values()]
    have = [code for code in wanted if code in coverage]

    # The STALEST index decides. Taking the newest let one current index hide
    # another: the equal-weight series sat at 2026-10-31 while the status
    # read "newest 2026-11-04", current, nothing to fetch (found 2026-11-06).
    oldest = None
    for code in have:
        stamp = _parse(coverage[code]["last_date"])
        if stamp and (oldest is None or stamp < oldest):
            oldest = stamp

    # Two requests: worth asking after every close, holiday or not.
    missed = sessions_behind(oldest, include_today=_ready_today())

    detail = f"{len(have)}/{len(wanted)} indices stored"

    if oldest is not None:
        detail += f", all through {oldest.date()}"
        if missed:
            detail += f" ({missed} session(s) behind)"

    return {
        "present": len(have) == len(wanted),
        "detail": detail,
        "updated_at": oldest,
        "behind": bool(missed),
    }


def _sectors_state():
    coverage = database.sector_coverage()
    equities = coverage["equities"] or 0
    missing = equities - coverage["with_sector"]

    covered = (
        equities > 0 and coverage["with_sector"] >= equities * 0.9
    )

    detail = (f"{coverage['with_sector']}/{equities} equities have a "
              f"sector, {coverage['sectors']} sectors")
    if missing > 0:
        detail += f"; {missing} without one"

    return {
        "present": covered,
        "detail": detail,
        # Sector membership carries no timestamp of its own, and changes on the
        # timescale of a company listing -- so a listing without one is what
        # makes this behind. Not falling under 90%: a new company or two a
        # month never did, and so never got a sector (2026-11-11). The step
        # asks only the names without one (import_sectors, missing_only), and
        # MAX_AGE paces it: one request per new listing, at most monthly.
        "updated_at": None,
        "behind": missing > 0,
    }


def _flow_state():
    coverage = database.client_type_coverage()

    equities = coverage["equities"] or 0
    covered = coverage["with_flow"]

    newest = _parse(coverage["last_date"])
    missed = sessions_behind(newest,
                             include_today=_ready_today() and _index_has_today())

    detail = f"{covered}/{equities} equities have flow data"

    if newest is not None:
        detail += f", newest {newest.date()}"
        if missed:
            detail += f" ({missed} session(s) behind)"

    # The newest flow row of ANY name said nothing about the rest: on
    # 2026-11-10 154 of 652 names that traded had that session's flow, the
    # newest date read "2026-11-10", nothing was owed, and the other ~500
    # were never fetched (11-09 stood at 355). The latest traded session's
    # coverage decides as well.
    session, traded, flowed = _flow_session_coverage()
    thin = bool(traded) and flowed < traded * FLOW_SESSION_COVERAGE
    if thin:
        detail += f"; session {session}: {flowed}/{traded} traded names have flow"

    return {
        "present": equities > 0 and covered >= equities * 0.9,
        "detail": detail,
        "updated_at": newest,
        # Same rule as price history: staleness is a missing session, never a
        # wall-clock age. The newest row is stamped with the session's own
        # date, so it is always "a day old" the morning after a close.
        "behind": bool(missed) or thin,
    }


# Share of the names that traded in the newest session which must have that
# session's flow before the flow counts as current. Every session from
# 2026-06 to 11-04 had 100%; 0.9 then let 11-07..11-10 through at 93.9%,
# 93.1%, 91.3% and 90.8% (592/652), the names wrongly marked off the market
# watch going without flow for a week (seed_import._still_absent).
FLOW_SESSION_COVERAGE = 0.97


def _flow_session_coverage():
    """(newest equity session, names that traded in it, names with its flow)."""

    conn = database._connect()
    try:
        # Each equity's newest traded bar off the (ins_code, date) key, then
        # the newest of those: 0.09 s instead of 1.6 s for the join over every
        # bar, on a probe nearly every panel response pays (2026-11-11).
        session = conn.execute("""
            SELECT MAX((SELECT p.date FROM price_history p
                        WHERE p.ins_code = i.ins_code AND p.volume > 0
                        ORDER BY p.date DESC LIMIT 1))
            FROM instruments i WHERE i.is_equity = 1
        """).fetchone()[0]
        if not session:
            return None, 0, 0
        traded, flowed = conn.execute("""
            SELECT COUNT(*), COUNT(f.ins_code)
            FROM price_history p
            JOIN instruments i ON i.ins_code = p.ins_code
            LEFT JOIN client_type_history f
              ON f.ins_code = p.ins_code AND f.date = p.date
            WHERE i.is_equity = 1 AND p.volume > 0 AND p.date = ?
        """, (session,)).fetchone()
    finally:
        conn.close()
    return session, int(traded or 0), int(flowed or 0)


def _codal_state():
    coverage = database.codal_coverage()

    equities = coverage["equities"] or 0
    covered = coverage["with_financials"]

    # Deliberately a low bar. Codal fills in over days by design -- the whole
    # universe is ~800 MB of filings and CODAL_MAX_FETCH_PER_RUN caps each run
    # -- so "present" here means the fundamental view has enough to be useful,
    # not that every company has been downloaded. Demanding full coverage
    # would leave this step permanently red and permanently refetching.
    target = max(1, int(equities * 0.25)) if equities else 1

    detail = (f"{covered}/{equities} equities have filings, "
              f"{coverage['periods']} periods, {coverage['checked']} checked")

    return {
        "present": covered >= target,
        "detail": detail,
        "updated_at": _parse(coverage["updated_at"]),
        # Never "behind" on age: a company that filed nothing new has nothing
        # new to fetch, and codal_sync paces each company itself. Progress
        # toward coverage is what this step is for.
        "behind": covered < equities,
    }


# Order matters: each step's prerequisites come before it.
STEPS = [
    ("universe", _universe_state, ()),
    ("snapshot", _snapshot_state, ()),
    ("indices", _indices_state, ()),
    ("history", _history_state, ("universe",)),
    ("sectors", _sectors_state, ("universe",)),
    ("flow", _flow_state, ("universe",)),
    # Last on purpose: it is the slowest step and the only one nothing else
    # depends on, so everything that gates a scan is already stored before
    # this starts filling in fundamentals.
    ("codal", _codal_state, ("universe",)),
]


# A history step that DEFERRED the market's session -- the history endpoint
# had not published it yet, so the run stored nothing for it -- used to
# count as a fetch and held the retry off for MAX_AGE: on 2026-11-11 the
# 19:00 run deferred 11-11 and nothing tried again for 12 hours, the basket
# was built on 11-10 bars and the day's forward entry could not be written
# (the review panel). A deferred session component is retried after this.
DEFERRED_RETRY = timedelta(minutes=45)
_DEFERRED = {}

# Per component, from 2026-11-24. Asking for the index again is two
# requests, and a deferred bar update costs the overview, the market watch
# and at most seed_import.HISTORY_CANARY history requests -- so those two
# are asked again after 15 minutes, not 45: the published series showed up
# to 45 minutes late on the panel. Flow keeps 45: a partial flow session is
# re-asked name by name, some 700 requests a try.
DEFERRED_RETRY_BY = {
    "indices": timedelta(minutes=15),
    "history": timedelta(minutes=15),
}


def _deferred_retry(name):
    """How long after a deferred fetch `name` is attempted again."""

    return DEFERRED_RETRY_BY.get(name, DEFERRED_RETRY)

# The scheduled run is one bootstrap an evening, and status() is asked again
# only when the panel happens to be open: the 45-minute retry never ran on
# the scheduled path, and a deferred session was lost to the forward log
# (the full test, 2026-11-12). The command line now waits and retries the
# session components itself, at most this many times and never past the
# local cut-off -- the evening's later steps wait for it, which is the point:
# they read the session.
#
# The flow (حقیقی/حقوقی) of a session is published later in the evening than
# its bars: on 11-22 the 20:09 retry found the bars but flow for 36 of 748
# traded names, the loop stopped because only history counted, and the
# session was lost to the forward log (which needs 90% of them); 11-16's
# flow was complete by 21:34. A thin flow for the newest session is a
# deferral too, and the cut-off sits late enough to catch it.
SCHEDULED_RETRIES = 3
SCHEDULED_RETRY_UNTIL = (22, 30)

# The index series is published hours after the close, like the bars: at
# 15:30 on 10-31 and at 15:46 on 2026-11-23 it did not carry the day, at
# 19:00 on 11-09 and 11-11 it did. An index fetch that brings no bar for
# today used to be recorded "fetched" all the same, and that held the next
# attempt off for MAX_AGE: on 2026-11-23 the panel was opened at 15:46, and
# the 19:00 scheduled run read "indices ... (1 session(s) behind)", found
# the attempt recent, printed "nothing to fetch" and exited 0 -- no bars, no
# flow, no forward entry for the session (the review, 2026-11-23). An index
# still behind after its own fetch is a deferral (_left_behind), retried
# after DEFERRED_RETRY: two requests an attempt, never closer together than
# that, and none once the series is current.
#
# The scheduled run also WAITS for it (retry_deferred), but only before this
# hour: later than this an index without today's bar means the market did
# not trade -- a holiday on a trading weekday -- and waiting three retries
# for it would hold the evening's other steps back two hours for nothing.
INDEX_WAIT_UNTIL = (18, 0)


def _deferred():
    """Which session step is still waiting on the exchange, or None."""

    return ("indices" if _DEFERRED.get("indices")
            else "history" if _DEFERRED.get("history")
            else "flow" if _DEFERRED.get("flow") else None)


def _left_behind(name):
    """True when the index series is still a session behind after its fetch.

    Only the index: asking it again is two requests. Bars and flow decide
    their own deferral in their fetchers (a canary, the session's coverage),
    because "still behind" for them on a holiday would mean asking for every
    name again -- some 1,800 requests -- each time the gate opened.
    """

    if name != "indices":
        return False
    try:
        return bool(_indices_state().get("behind"))
    except Exception:                                           # noqa: BLE001
        return False


def retry_deferred(wait=None, retries=None, until=None, now=None, sleep=None):
    """Retry the session components while the history or flow step defers.

    Returns the number of retries made. The wait is checked against the wall
    clock in short slices, so a laptop that suspends mid-wait resumes on
    time rather than sleeping the whole interval after it wakes (the
    11-10 stall).
    """

    wait = DEFERRED_RETRY if wait is None else wait
    retries = SCHEDULED_RETRIES if retries is None else retries
    until = until or SCHEDULED_RETRY_UNTIL
    now = now or datetime.now
    sleep = sleep or time.sleep
    made = 0
    while made < retries and _deferred():
        waiting = _deferred()
        due = now() + wait
        if (due.hour, due.minute) > until or due.date() != now().date():
            print("deferred: the session's %s is still unpublished; the next "
                  "try would pass %02d:%02d, so it is left to tomorrow's run"
                  % (waiting, until[0], until[1]), flush=True)
            break
        print("deferred: the exchange has not published the session's %s; "
              "trying again at %s" % (waiting, due.strftime("%H:%M")), flush=True)
        while now() < due:
            sleep(min(30.0, max(0.0, (due - now()).total_seconds())))
        made += 1
        # Only a step that defers again keeps the loop going; one that is not
        # planned (nothing behind any more) ends it.
        _DEFERRED["indices"] = False
        _DEFERRED["history"] = False
        _DEFERRED["flow"] = False
        outcome = ensure(only=SESSION_COMPONENTS)
        for name, result in (outcome or {}).items():
            print("  retry %d: %-10s %s  %s" % (made, name, result.get("action"),
                                                result.get("detail", "")), flush=True)
    return made


def _mark_fetched(name):
    """Record a successful fetch, so a retry is paced by attempts not by age.

    A session component whose run deferred the session is recorded as
    "deferred", which status() retries after DEFERRED_RETRY -- and so is an
    index fetch that left the series a session behind (INDEX_WAIT_UNTIL).
    """

    deferred = name in SESSION_COMPONENTS and (
        bool(_deferred()) or _left_behind(name))
    database.set_provenance(
        f"bootstrap:{name}", "deferred" if deferred else "fetched",
        f"automatic fetch at {_now().isoformat(timespec='seconds')}"
        + (" -- the session was deferred, retried shortly" if deferred else "")
    )


def _last_fetch(name):
    record = database.get_provenance(f"bootstrap:{name}")
    return _parse(record.get("checked_at")) if record else None


def _last_fetch_deferred(name):
    record = database.get_provenance(f"bootstrap:{name}")
    return bool(record) and record.get("status") == "deferred"


def status(fresh=False, max_age=None):
    """What is stored, what is missing, and what is stale — fetching nothing.

    Cached for STATUS_TTL seconds, or `max_age` where the caller can use an
    older reading. Pass fresh=True where the answer decides what to
    download, so a fetch is never planned from a stale reading. A run of
    this module clears the cache when it ends, so its own fetches are never
    hidden by it.
    """

    ttl = STATUS_TTL if max_age is None else max_age
    if not fresh:
        with _status_lock:
            cached, at = _status_cache["value"], _status_cache["at"]
            if cached is not None and (time.monotonic() - at) < ttl:
                return cached
            running = _status_inflight["event"]
            if running is None:
                running = _status_inflight["event"] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            running.wait(timeout=120)
            with _status_lock:
                if _status_cache["value"] is not None:
                    return _status_cache["value"]
            return _probe_all()
        try:
            report = _probe_all()
            with _status_lock:
                _status_cache["value"] = report
                _status_cache["at"] = time.monotonic()
            return report
        finally:
            with _status_lock:
                _status_inflight["event"] = None
            running.set()

    report = _probe_all()

    with _status_lock:
        _status_cache["value"] = report
        _status_cache["at"] = time.monotonic()

    return report


def _probe_all():
    """Probe every component. The uncached half of status()."""

    report = {}

    for name, probe, requires in STEPS:

        try:
            state = probe()
        except Exception as exc:
            state = {"present": False, "detail": f"{type(exc).__name__}: {exc}",
                     "updated_at": None, "behind": True}

        age = None
        if state["updated_at"]:
            age = _now() - state["updated_at"]

        # Missing data is always fetched. Data that is merely behind is fetched
        # only if we have not already tried recently -- otherwise a market
        # holiday, where no new session exists to find, would have us refetch
        # the whole universe on a loop, each attempt correctly returning
        # nothing new and each leaving the component still "behind".
        fetched_at = _last_fetch(name)

        wait = _deferred_retry(name) if _last_fetch_deferred(name) else MAX_AGE[name]

        attempted_recently = (
            fetched_at is not None and (_now() - fetched_at) < wait
        )

        # A fetch made before today's figures were final cannot have found
        # them: the panel opened at 10:00 would otherwise make the 19:00 run
        # skip the session that closed in between.
        if (attempted_recently and name in AFTER_CLOSE
                and _ready_today() and fetched_at < _ready_moment()):
            attempted_recently = False

        stale = (not state["present"]) or (
            state.get("behind", False) and not attempted_recently
        )

        # When a component that is behind may be asked for again. The panel
        # showed "everything is current" for one held back by a recent
        # attempt (2026-11-23); it now says the session is incomplete and
        # when the next try is due.
        next_try = None
        if state.get("behind", False) and attempted_recently:
            next_try = (fetched_at + wait).isoformat(timespec="seconds")

        cooling = _last_failure.get(name)
        cooldown_left = None
        if cooling:
            remaining = RETRY_COOLDOWN - (_now() - cooling)
            if remaining.total_seconds() > 0:
                cooldown_left = int(remaining.total_seconds())

        report[name] = {
            "present": state["present"],
            "detail": state["detail"],
            "updated_at": (state["updated_at"].isoformat(timespec="seconds")
                           if state["updated_at"] else None),
            "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
            "behind": state.get("behind", False),
            # One row per trading session: for these, behind means a session
            # is missing or incomplete (sectors and codal are "behind" while
            # coverage grows, which is no one's emergency).
            "session": name in SESSION_COMPONENTS,
            "last_fetch": (fetched_at.isoformat(timespec="seconds")
                           if fetched_at else None),
            "next_try": next_try,
            "needs_fetch": stale,
            "requires": list(requires),
            "retry_in_seconds": cooldown_left,
        }

    return report


# ==============================
# FETCHING
# ==============================

# Each fetcher takes the callback the panel's progress bar is fed from:
#   on_item(done, total, label)   -- total None means "size unknown"

def _fetch_universe(on_item):
    import seed_import
    return seed_import.import_universe(verbose=False, progress=on_item) is not None


def _fetch_snapshot(on_item):
    import seed_import
    written, plan = seed_import.import_snapshot(
        verbose=False, progress=on_item, return_plan=True)
    if written:
        _note_board_moves(plan)
    return bool(written)


def _note_board_moves(plan=None):
    """Say how many board moves wait for review, while any do.

    The snapshot step adds new companies and holds every board move for
    `seed_import.py --repair-universe --apply` (reconcile_universe). Held
    silently -- the step reports only what it wrote -- a moved company
    stayed on its dead code, without bars, flow or filings for the live
    listing, and nothing on the panel said a review was waiting (review of
    2026-11-11). So every run that includes the snapshot step says so: from
    `plan` when the step has just fetched, otherwise from the stored watch,
    which costs a read (~1.3 s on the live file) and no request.
    """

    import seed_import

    try:
        if plan is None:
            plan = seed_import.reconcile_universe(apply=False, verbose=False,
                                                  retire=False)
        waiting = len({str(new["ins_code"])
                       for _old, new in plan.get("awaiting") or ()})
    except Exception as exc:                                    # noqa: BLE001
        _note("board-move check failed: %s: %s"
              % (type(exc).__name__, str(exc)[:160]))
        return

    if waiting:
        _note(f"تصویر بازار: تغییر تابلوی {waiting} نماد در انتظار بازبینی "
              "است و تا آن زمان کد قدیمی‌شان دنبال می‌شود — "
              "python seed_import.py --repair-universe --apply")


def _fetch_indices(on_item):
    import seed_import
    ok = bool(seed_import.import_indices(verbose=False, progress=on_item))
    # Today's figures should exist and the series does not carry them: the
    # exchange has not published yet (INDEX_WAIT_UNTIL). The scheduled run
    # waits for it; past the hour it is taken for a day without trading.
    try:
        now = datetime.now()
        waiting = bool(ok and _ready_today() and not _index_has_today()
                       and (now.hour, now.minute) < INDEX_WAIT_UNTIL)
    except Exception:                                           # noqa: BLE001
        waiting = False
    _DEFERRED["indices"] = waiting
    if waiting:
        _note("indices: today's index is not published yet; retried")
    return ok


def _fetch_history(on_item):
    # Incremental: the stored series is kept and only the sessions it lacks
    # are fetched. A full HISTORY_BARS pull is now only for a name with no
    # bars at all. `python seed_import.py --history` still does the full pull.
    import seed_import
    stats = seed_import.update_history(
        full_count=HISTORY_BARS, verbose=False, progress=on_item
    )
    for message in stats.get("notes") or []:
        _note(message)
    _DEFERRED["history"] = bool(stats.get("deferred"))
    return bool(stats.get("ok"))


def _fetch_sectors(on_item):
    # Only the equities without a sector: a full refetch is ~900 requests for
    # membership that does not change (`seed_import.py --sectors` still does
    # it, as does the panel's sector button). A run that got no live answer
    # returns 0, and so is a failure retried after RETRY_COOLDOWN -- at most
    # seed_import.SECTOR_CANARY requests an attempt -- not a fetch that
    # holds a new listing off for MAX_AGE.
    import seed_import
    return bool(seed_import.import_sectors(verbose=False, progress=on_item,
                                           missing_only=True))


def _fetch_flow(on_item):
    import seed_import
    ok = bool(seed_import.import_client_type(verbose=False, progress=on_item,
                                             incremental=True))
    # The newest session's flow arrives in the evening after its bars; until
    # most traded names have it, the step has deferred that session.
    try:
        session, traded, flowed = _flow_session_coverage()
    except Exception:                                           # noqa: BLE001
        session, traded, flowed = None, 0, 0
    _DEFERRED["flow"] = bool(ok and traded and
                             flowed < traded * FLOW_SESSION_COVERAGE)
    if _DEFERRED["flow"]:
        _note("flow: session %s has %d of %d traded names so far -- the rest "
              "is published later; retried" % (session, flowed, traded))
    elif ok:
        # The funds' flow, by kind (fund_flows.py, 2026-11-26): its own
        # table, at most every 6 h, once the session's equity flow is in.
        # Never raises, and never fails the equity step.
        try:
            import fund_flows
            _note(fund_flows.update())
        except Exception as exc:                                # noqa: BLE001
            _note("fund flows failed to run: %s: %s" % (type(exc).__name__,
                                                        str(exc)[:160]))
    return ok


def _fetch_codal(on_item):
    """One bounded Codal batch.

    Returns True when the batch completed, even if it stored nothing: with
    quarterly reporting, "checked forty companies and none had filed" is a
    successful run, and calling it a failure would put the step into the retry
    cooldown for no reason.
    """

    import codal_sync

    result = codal_sync.run(progress=on_item)

    return result.get("checked", 0) > 0 or result.get("planned", 0) == 0


FETCHERS = {
    "universe": _fetch_universe,
    "snapshot": _fetch_snapshot,
    "indices": _fetch_indices,
    "history": _fetch_history,
    "sectors": _fetch_sectors,
    "flow": _fetch_flow,
    "codal": _fetch_codal,
}


class StepTimeout(Exception):
    pass


def _run_step(name, fetcher, on_item):
    """fetcher(on_item), cut off at STEP_DEADLINE_SECONDS[name] if it has one."""

    deadline = STEP_DEADLINE_SECONDS.get(name)
    if not deadline:
        return fetcher(on_item)

    outcome = {}

    def target():
        try:
            outcome["ok"] = fetcher(on_item)
        except Exception as exc:                                # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=target, name=f"bootstrap-{name}",
                              daemon=True)
    worker.start()
    worker.join(deadline)

    if worker.is_alive():
        raise StepTimeout(f"no answer within {deadline // 60} min; "
                          f"left running in the background")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("ok")


def ensure(force=False, only=None):
    """Fetch every missing or stale component, in dependency order.

    Returns a per-step result dict. Never raises: a step that fails is
    recorded and the rest continue, because a missing snapshot should not
    cost the user their price history too.
    """

    # The other process -- the evening task, or the panel -- is fetching: its
    # data reaches this one through the store (and data_stamp), and fetching
    # the same sessions alongside it would double the requests.
    holder = _lock_holder()
    if holder is not None:
        _note("another process (PID %s, since %s) is fetching; this run "
              "leaves it to that one" % (holder.get("pid"),
                                         _clock(holder.get("started_at"))))
        with _lock:
            _state["planned"] = []
        return {}

    report = status(fresh=True)

    # What this run intends to download, decided before the first request so
    # the panel can show "step 2 of 4" rather than an open-ended list.
    planned = [
        name for name, _probe, _requires in STEPS
        if (not only or name in only)
        and (force or not report[name]["present"] or report[name]["needs_fetch"])
    ]

    with _lock:
        _state["planned"] = planned

    if not planned:
        return _fetch_planned(report, planned, force, only)

    holder = _acquire_run_lock()
    if holder is not None:
        _note("another process (PID %s) started fetching first; this run "
              "leaves it to that one" % holder.get("pid"))
        with _lock:
            _state["planned"] = []
        return {}
    try:
        return _fetch_planned(report, planned, force, only)
    finally:
        _release_run_lock()


def _fetch_planned(report, planned, force, only):
    """The body of ensure(): fetch what `report` says, in order."""

    results = {}
    satisfied = set()

    def record(name, outcome):
        results[name] = outcome
        with _lock:
            _state["steps"][name] = dict(outcome)

    for name, _probe, requires in STEPS:

        if only and name not in only:
            continue

        state = report[name]

        if state["present"] and not state["needs_fetch"] and not force:
            record(name, {"action": "kept", "detail": state["detail"]})
            satisfied.add(name)
            continue

        missing_prereq = [
            r for r in requires
            if r not in satisfied and not report[r]["present"]
        ]

        if missing_prereq:
            record(name, {
                "action": "skipped",
                "detail": f"needs {', '.join(missing_prereq)} first",
            })
            continue

        if state["retry_in_seconds"] and not force:
            record(name, {
                "action": "cooling-off",
                "detail": (f"last attempt failed; retrying in "
                           f"{state['retry_in_seconds']}s"),
            })
            continue

        with _lock:
            _state["current"] = name
            _state["item"] = None
            _state["steps"][name] = {
                "action": "fetching",
                "detail": state["detail"],
                "started_at": _now().isoformat(timespec="seconds"),
            }

        _note(f"fetching {name} ({state['detail']})")

        started = time.monotonic()

        try:
            ok = _run_step(name, FETCHERS[name], _set_item)
            error = None
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {str(exc)[:200]}"

        elapsed = round(time.monotonic() - started, 1)

        # The step is over; a leftover item count would keep the bar reading
        # "312 of 818" while the next step runs.
        with _lock:
            _state["item"] = None

        if ok:
            _last_failure.pop(name, None)
            satisfied.add(name)
            _mark_fetched(name)
            # Today's index bar is what tells the bar and flow probes that a
            # session happened today, and new bars are what tell the flow
            # import which names are owed; both were read before this fetch
            # stored anything. So look again, and a downstream component left
            # behind is fetched in THIS run even if it was attempted recently:
            # that attempt worked from the older data (2026-11-10: flow tried
            # at 21:33 against 11-09 bars, then "recent" when 11-10 landed).
            if name in DOWNSTREAM:
                report = status(fresh=True)
                for later in DOWNSTREAM[name]:
                    if later not in report:
                        continue
                    if report[later]["behind"] and not report[later]["retry_in_seconds"]:
                        report[later]["needs_fetch"] = True
                with _lock:
                    _state["planned"] = [
                        n for n, _p, _r in STEPS
                        if (not only or n in only)
                        and (force or n in planned or not report[n]["present"]
                             or report[n]["needs_fetch"])
                    ]
            # The component's own probe is the honest post-fetch detail: a
            # fetch that "succeeded" but stored 3 of 818 histories should not
            # read as simply done.
            after = _step_state(name)
            # The panel's cache key moves HERE, per step, not when the whole
            # run ends (2026-11-24): the index landed in seconds and then
            # waited behind ten minutes of flow and Codal before any view
            # showed it. Only a step that changed what is stored moves it --
            # a deferred retry every 15 minutes that found nothing new must
            # not recompute every cached scan each time.
            if (after.get("detail"), _iso(after.get("updated_at"))) != (
                    state["detail"], state["updated_at"]):
                _bump_stamp(name)
            record(name, {
                "action": "fetched",
                "seconds": elapsed,
                "detail": after.get("detail"),
            })
            _note(f"{name}: fetched in {elapsed}s")
        else:
            _last_failure[name] = _now()
            record(name, {
                "action": "failed",
                "seconds": elapsed,
                "detail": error or "the provider returned nothing",
            })
            _note(f"{name}: FAILED after {elapsed}s — "
                  f"{error or 'provider returned nothing'}")

    with _lock:
        _state["current"] = None
        _state["item"] = None

    # The DB moved under the cache during the run.
    with _status_lock:
        _status_cache["at"] = None
        _status_cache["value"] = None

    # A held board move waits for a person, so every run with the snapshot
    # step says so -- not only the one whose snapshot found it, which has
    # said so already (_fetch_snapshot).
    if (results.get("snapshot") or {}).get("action") not in (None, "fetched"):
        _note_board_moves()

    _forward_log()
    _outlook_inputs()
    _outlook_forward_log()

    return results


def _outlook_inputs():
    """Refresh what the forward fundamental analysis reads besides filings.

    The dollar series (fx.py, at most every 12 h: two requests to tgju.org)
    and the outlook's measured error and dollar slopes (outlook_calibration,
    rebuilt weekly from the stored filings, ~5 s). Added 2026-11-23 with the
    dollar scenarios; here, on the trigger the rest of the data uses, rather
    than as a new scheduled step. Each returns a message and never raises,
    and this wrapper still guards the imports: bookkeeping must never take
    down a fetch that succeeded.
    """

    # commodities.py (2026-11-26): the ounce, oil and the base metals, nine
    # paced requests to the same host at most every 12 h.
    for name in ("fx", "commodities", "outlook_calibration"):
        try:
            module = __import__(name)
            _note(module.ensure() if name == "outlook_calibration" else module.refresh())
        except Exception as exc:                                # noqa: BLE001
            _note("%s failed to run: %s: %s" % (name, type(exc).__name__,
                                                 str(exc)[:160]))


def _forward_log():
    """Append today's forward prediction, now that the day's data is in.

    The forward prediction log (examples/forward_prediction_log.py) is how
    the reserved out-of-sample window R-FORWARD-2027 becomes a PROSPECTIVE
    test: a prediction written this evening is provably older than
    tomorrow's outcome. An evening the log misses is an admissible forward
    prediction lost permanently -- which is why it runs here, on the same
    trigger as the data itself, instead of relying on someone remembering.

    log_once() is append-once per session, takes an OS lock against a
    concurrent writer, and returns a message instead of raising; and this
    wrapper still guards the import, because a fetch that succeeded must
    never be taken down by the bookkeeping that follows it.
    """

    try:
        from examples.forward_prediction_log import log_once

        _note("forward log: %s" % log_once(verbose=False))
    except Exception as exc:                                    # noqa: BLE001
        _note("forward log failed to run: %s: %s"
              % (type(exc).__name__, str(exc)[:160]))


def _outlook_forward_log():
    """Write down today's forward profit estimates, once per Tehran date.

    outlook_forward_log.py logs every ranked company's estimate and range,
    hash-chained, under the rules fixed in advance in
    backtest_output/OUTLOOK_FORWARD_PROTOCOL.json, so forward_scorecard.py
    can later score them against statements published after them. It runs
    after _outlook_inputs() so the day's calibration is the one logged; it
    makes no network request, is a no-op once today is logged, and returns a
    message instead of raising -- and this wrapper still guards the import:
    a data run that succeeded must never be taken down by this bookkeeping.
    """

    try:
        import outlook_forward_log

        _note(outlook_forward_log.log_once(verbose=False))
    except Exception as exc:                                    # noqa: BLE001
        _note("outlook forward log failed to run: %s: %s"
              % (type(exc).__name__, str(exc)[:160]))


def _step_state(name):
    """Re-probe one component after its fetch: its detail and newest date."""

    probe = dict((n, p) for n, p, _r in STEPS)[name]

    try:
        return probe()
    except Exception as exc:                                    # noqa: BLE001
        return {"detail": f"{type(exc).__name__}: {exc}", "updated_at": None}


def _step_detail(name):
    """Re-probe one component, for the line shown under a finished step."""

    return _step_state(name).get("detail")


def _iso(stamp):
    return stamp.isoformat(timespec="seconds") if stamp else None


def _clock(stamp):
    """An ISO stamp as local HH:MM, for a note."""

    moment = _parse(stamp)
    return moment.astimezone().strftime("%H:%M") if moment else "?"


# ==============================
# ACROSS PROCESSES: THE DATA STAMP AND THE RUN LOCK
# ==============================

def _bump_stamp(name=None):
    """Say that stored data changed: in this process and, via STAMP_PATH, in
    every other one (the evening task's fetches reach the panel's cache)."""

    stamp = _now().isoformat(timespec="microseconds")
    with _lock:
        _state["data_stamp"] = stamp
    try:
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        with open(STAMP_PATH, "w", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (stamp, name or ""))
    except OSError:
        pass
    try:
        import today_provisional

        today_provisional.forget_official()
    except Exception:                                           # noqa: BLE001
        pass


def data_stamp():
    """A token that moves whenever any process stored a changed component.

    The panel's cache key (app.data_version). It was finished_at(), which
    moved only when this process's whole run ended -- so an index fetched in
    seconds waited behind the flow and Codal steps, and what the evening task
    stored never moved it at all (2026-11-24). Cheap: a dict lookup and one
    stat() of a file.
    """

    # Not finished_at(): the keeper ends a run every time it retries a
    # deferred session, and a run that stored nothing new must leave every
    # cached scan where it is.
    try:
        shared = os.stat(STAMP_PATH).st_mtime_ns
    except OSError:
        shared = None
    with _lock:
        own = _state.get("data_stamp")
    return "%s|%s" % (own or "-", shared or "-")


def _lock_holder():
    """Who holds the run lock, if it is another live process; else None."""

    try:
        touched = os.stat(RUN_LOCK_PATH).st_mtime
    except OSError:
        return None
    if time.time() - touched > LOCK_FRESH.total_seconds():
        return None                     # its holder stopped beating: gone
    try:
        with open(RUN_LOCK_PATH, encoding="utf-8") as handle:
            holder = json.load(handle)
    except (OSError, ValueError):
        holder = {}
    if holder.get("pid") == os.getpid():
        return None
    return holder


_run_lock = {"beat": None, "owned": False}


def _acquire_run_lock():
    """Take the run lock. None if taken (or unusable), else who holds it."""

    record = json.dumps({"pid": os.getpid(),
                         "started_at": _now().isoformat(timespec="seconds"),
                         "by": os.path.basename(sys.argv[0] if sys.argv else "python")})
    for _attempt in range(2):
        try:
            handle = os.open(RUN_LOCK_PATH,
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _lock_holder()
            if holder is not None:
                return holder
            try:                        # a dead holder's, or our own leftover
                os.remove(RUN_LOCK_PATH)
            except OSError:
                pass
            continue
        except OSError:
            return None                 # no lock to be had; fetch unlocked
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(record)
        stop = threading.Event()

        def beat():
            while not stop.wait(LOCK_HEARTBEAT):
                try:
                    os.utime(RUN_LOCK_PATH)
                except OSError:
                    pass

        threading.Thread(target=beat, name="bootstrap-lock",
                         daemon=True).start()
        _run_lock.update(beat=stop, owned=True)
        return None
    return None


def _release_run_lock():
    if not _run_lock["owned"]:
        return
    if _run_lock["beat"] is not None:
        _run_lock["beat"].set()
    _run_lock.update(beat=None, owned=False)
    try:
        with open(RUN_LOCK_PATH, encoding="utf-8") as handle:
            mine = json.load(handle).get("pid") == os.getpid()
    except (OSError, ValueError):
        mine = False
    if mine:
        try:
            os.remove(RUN_LOCK_PATH)
        except OSError:
            pass


def wait_for_other_run(limit=timedelta(hours=3), sleep=None):
    """Block while another process holds the run lock, up to `limit`.

    For the evening task: started while the panel is fetching, it waits for
    that run's data rather than fetching it a second time, then runs its own
    steps against the result. Returns True if it waited.
    """

    sleep = sleep or time.sleep
    holder = _lock_holder()
    if holder is None:
        return False
    print("another process (PID %s, since %s) is fetching; waiting for it"
          % (holder.get("pid"), _clock(holder.get("started_at"))), flush=True)
    give_up = time.time() + limit.total_seconds()
    while _lock_holder() is not None and time.time() < give_up:
        sleep(15.0)
    return True


# ==============================
# THE KEEPER
# ==============================

_keeper = {"thread": None, "stop": None, "last": None, "next": None,
           "outcome": None}


def keep_fresh_once():
    """One pass of the keeper. Returns what it did, for the log and tests.

    The panel used to fetch only when a page asked, and a deferred session
    was retried only if a page happened to ask after the retry was due: on
    2026-11-24 the 15:16 attempt found no index for the day and nothing asked
    again until the evening. While the panel runs, this asks every
    KEEPER_INTERVAL -- through the same status() gates, so nothing is
    fetched more often than before, only without waiting for a reader.
    """

    done = {"provisional": None, "started": []}

    try:
        import today_provisional

        reading = today_provisional.refresh_if_due()
        if reading is not None:
            done["provisional"] = reading.get("session") or reading.get("error")
            _note("today, provisionally (%s): %s" % (
                reading.get("session") or "-",
                "stored" if not reading.get("error") else reading["error"]))
    except Exception as exc:                                    # noqa: BLE001
        _note("provisional figures failed: %s: %s"
              % (type(exc).__name__, str(exc)[:160]))

    if is_running() or _lock_holder() is not None:
        return done

    report = status(fresh=True)
    wanted = [name for name in KEEPER_COMPONENTS
              if (report.get(name) or {}).get("needs_fetch")]
    if wanted and start_background(only=wanted):
        done["started"] = wanted
    return done


def start_keeper(interval=None, first_delay=20.0):
    """Run keep_fresh_once() every `interval` seconds in a daemon thread.

    The wait is sliced against the wall clock, so a laptop that suspends
    mid-wait checks again soon after it wakes (the 11-10 stall).
    """

    interval = KEEPER_INTERVAL if interval is None else interval
    if _keeper["thread"] is not None and _keeper["thread"].is_alive():
        return False
    stop = threading.Event()

    def loop():
        due = time.time() + first_delay
        while not stop.is_set():
            _keeper["next"] = datetime.fromtimestamp(
                due, timezone.utc).isoformat(timespec="seconds")
            while time.time() < due and not stop.is_set():
                stop.wait(min(30.0, max(0.5, due - time.time())))
            if stop.is_set():
                break
            _keeper["last"] = _now().isoformat(timespec="seconds")
            try:
                _keeper["outcome"] = keep_fresh_once()
            except Exception as exc:                            # noqa: BLE001
                _keeper["outcome"] = {"error": "%s: %s" % (
                    type(exc).__name__, str(exc)[:160])}
            due = time.time() + interval

    thread = threading.Thread(target=loop, name="sirat-keeper", daemon=True)
    _keeper.update(thread=thread, stop=stop)
    thread.start()
    return True


def stop_keeper():
    if _keeper["stop"] is not None:
        _keeper["stop"].set()


def keeper_state():
    thread = _keeper["thread"]
    return {"running": bool(thread is not None and thread.is_alive()),
            "last_check": _keeper["last"], "next_check": _keeper["next"],
            "interval_seconds": KEEPER_INTERVAL}


def run(force=False, only=None):
    """ensure(), guarded so only one run happens at a time."""

    with _lock:
        if _state["running"]:
            return None
        _state["running"] = True
        _state["started_at"] = _now().isoformat(timespec="seconds")
        # None while a run is in flight -- progress() and the command line
        # read it so -- but the run before it is remembered: finished_at().
        if _state["finished_at"]:
            _state["last_finished_at"] = _state["finished_at"]
        _state["finished_at"] = None
        _state["steps"] = {}
        _state["planned"] = []
        _state["item"] = None
        _state["current"] = None

    try:
        results = ensure(force=force, only=only)
    finally:
        with _lock:
            _state["running"] = False
            _state["finished_at"] = _now().isoformat(timespec="seconds")

    return results


def start_background(force=False, only=None):
    """Kick off a run in a daemon thread. Returns True if one started."""

    global _thread

    with _lock:
        if _state["running"]:
            return False

    _thread = threading.Thread(
        target=run,
        kwargs={"force": force, "only": only},
        name="sirat-bootstrap",
        daemon=True,
    )
    _thread.start()

    return True


def is_running():
    """Whether a fetch is in flight. Cheap — touches no database."""

    with _lock:
        return _state["running"]


def finished_at():
    """When the last run finished, or None. Cheap — touches no database.

    The panel's cache key (app.data_version). It used to read progress(),
    which calls status() and re-probes the whole database (~5 s) whenever its
    5-second cache has gone cold -- on most requests, since the panel polls
    only every 20 s -- so the key cost ten times the reads it was memoising.

    WHILE A RUN IS IN FLIGHT this is still the run before it. It used to be
    None from the moment a fetch started, so the panel's key became "-" in
    mid-fetch: every cached scan was orphaned and recomputed against a store
    half-written, the opposite of what app.data_version promises ("the
    cached answer is at least a consistent one"). The key now moves once,
    when the new run ends (2026-11-23).
    """

    with _lock:
        return _state["finished_at"] or _state.get("last_finished_at")


def progress():
    """Current run state, for the panel to poll."""

    with _lock:
        snapshot = {
            "running": _state["running"],
            "started_at": _state["started_at"],
            "finished_at": _state["finished_at"],
            "current": _state["current"],
            "planned": list(_state["planned"]),
            "steps": {k: dict(v) for k, v in _state["steps"].items()},
            "item": dict(_state["item"]) if _state["item"] else None,
            "log": list(_state["log"][-20:]),
        }

    snapshot["status"] = status()
    snapshot["order"] = [name for name, _probe, _requires in STEPS]
    snapshot["labels"] = dict(STEP_LABELS)
    snapshot["overall"] = _overall(snapshot)

    # For the status strip (2026-11-24): the token the page reloads its views
    # on, the keeper's schedule, another process's fetch, and today's
    # provisional figures while the official series lacks them.
    snapshot["data_stamp"] = data_stamp()
    snapshot["keeper"] = keeper_state()
    holder = _lock_holder()
    snapshot["other_run"] = ({"pid": holder.get("pid"),
                              "started_at": holder.get("started_at"),
                              "by": holder.get("by")} if holder else None)
    try:
        import today_provisional

        snapshot["provisional"] = today_provisional.current()
        snapshot["official"] = today_provisional.official_dates()
    except Exception:                                           # noqa: BLE001
        snapshot["provisional"] = None
        snapshot["official"] = None

    return snapshot


def _overall(snapshot):
    """Where the run has got to: which step, and how far through the whole.

    The percentage is weighted by STEP_WEIGHT and, inside the running step, by
    its item count. It is an estimate of *work*, not of time -- a stalled
    request against an unreachable host will hold the bar still, which is the
    honest reading.
    """

    planned = snapshot["planned"]
    current = snapshot["current"]

    done = [n for n in planned
            if (snapshot["steps"].get(n) or {}).get("action") in SETTLED]

    if not planned:
        # Either nothing needed fetching, or no run has happened yet.
        finished = bool(snapshot["finished_at"]) or not snapshot["running"]
        return {
            "percent": 100.0 if finished else 0.0,
            "step": None,
            "step_label": None,
            "step_number": 0,
            "steps_total": 0,
            "done": len(done),
            "item": None,
            "elapsed_seconds": _elapsed(snapshot),
            "phase": "idle" if finished else "starting",
        }

    total_weight = sum(STEP_WEIGHT.get(n, 1) for n in planned) or 1

    done_weight = sum(STEP_WEIGHT.get(n, 1) for n in done)

    item = snapshot["item"]

    # Credit the running step for the share of its items already fetched.
    if current in planned and current not in done and item and item.get("total"):
        fraction = min(1.0, max(0.0, item["done"] / item["total"]))
        done_weight += STEP_WEIGHT.get(current, 1) * fraction

    percent = round(min(100.0, done_weight / total_weight * 100), 1)

    if not snapshot["running"]:
        percent = 100.0

    number = planned.index(current) + 1 if current in planned else len(done)

    return {
        "percent": percent,
        "step": current,
        "step_label": STEP_LABELS.get(current) if current else None,
        "step_number": number,
        "steps_total": len(planned),
        "done": len(done),
        "item": item,
        "elapsed_seconds": _elapsed(snapshot),
        "phase": "running" if snapshot["running"] else "finished",
    }


def _elapsed(snapshot):
    """Seconds since the run started, or its total once finished."""

    started = _parse(snapshot["started_at"])

    if started is None:
        return None

    end = _parse(snapshot["finished_at"]) if not snapshot["running"] else _now()

    if end is None:
        return None

    return int((end - started).total_seconds())


if __name__ == "__main__":

    import sys

    force = "--force" in sys.argv

    print("=" * 74)
    print("SIRAT AI - DATA BOOTSTRAP")
    print("=" * 74)

    report = status()

    print(f"\n{'COMPONENT':<12}{'PRESENT':<9}{'AGE':>8}  DETAIL")
    print("-" * 74)
    for name, state in report.items():
        age = f"{state['age_hours']}h" if state["age_hours"] is not None else "-"
        flag = "yes" if state["present"] else "NO"
        need = "  <- will fetch" if state["needs_fetch"] else ""
        print(f"{name:<12}{flag:<9}{age:>8}  {state['detail']}{need}")

    if "--status" in sys.argv:
        raise SystemExit(0)

    if not any(s["needs_fetch"] for s in report.values()) and not force:
        # "Everything is present and current" was printed over a table that
        # said "(1 session(s) behind)" (2026-11-23): behind but held back by
        # a recent attempt is not current, and the log now says which.
        held = [name for name, s in report.items()
                if s.get("session") and s["behind"]]
        if held:
            print("\nNothing to fetch now -- %s behind, tried recently; "
                  "next try %s." % (", ".join(held), min(
                      report[n].get("next_try") or "soon" for n in held)))
        else:
            print("\nEverything is present and current — nothing to fetch.")
        raise SystemExit(0)

    print(f"\nprovider: {settings.PROVIDER}   chain: {settings.PROVIDER_CHAIN}")
    print()

    # The panel may be fetching the same sessions right now (its keeper, or
    # a page): wait for it, then run against what it stored.
    wait_for_other_run()

    # Same progress the panel draws, rendered on one rewritten line: a fetch of
    # 800+ histories should not look like a hung process from the console
    # either.
    start_background(force=force)

    # Redirected to a file (the scheduled tasks), "\r" does not rewrite a
    # line, it appends one: twice a second for a two-hour history fetch, and
    # task_evening.log grew to 530 KB of progress bars. A file gets one line
    # per step instead.
    interactive = sys.stdout.isatty()
    last_step = None

    while True:

        snapshot = progress()
        overall = snapshot["overall"]

        if not snapshot["running"] and snapshot["finished_at"]:
            break

        step = overall["step"] or "starting"
        item = overall["item"]
        count = (f" {item['done']}/{item['total']}"
                 if item and item.get("total") else "")

        if interactive:
            filled = int(overall["percent"] / 4)
            bar = "#" * filled + "." * (25 - filled)

            sys.stdout.write(
                f"\r  [{bar}] {overall['percent']:5.1f}%  "
                f"step {overall['step_number']}/{overall['steps_total']}  "
                f"{step}{count}      "
            )
            sys.stdout.flush()
        elif overall["step_number"] != last_step:
            last_step = overall["step_number"]
            print(f"  step {overall['step_number']}/{overall['steps_total']}  "
                  f"{step}  ({datetime.now():%H:%M:%S})", flush=True)

        time.sleep(0.5)

    if interactive:
        sys.stdout.write("\r" + " " * 78 + "\r")

    results = progress()["steps"]

    print("\n" + "=" * 74)
    for name, outcome in (results or {}).items():
        detail = outcome.get("detail", "")
        seconds = outcome.get("seconds")
        timing = f" ({seconds}s)" if seconds else ""
        print(f"  {name:<12}{outcome['action']}{timing}  {detail}")

    # What each step had to say -- a deferral, a canary, a partial market
    # watch. The panel shows these; a scheduled run used to drop them, so
    # its log never said why a step stored nothing (the review panel).
    notes = [entry["message"] for entry in list(_state["log"])]
    if notes:
        print()
        print("notes:")
        for message in notes[-20:]:
            print("  " + message)

    failures = [n for n, o in (results or {}).items() if o["action"] == "failed"]

    if "--no-retry" not in sys.argv:
        retry_deferred()

    if failures:
        print(f"\nFailed: {', '.join(failures)}")
        print("Check connectivity with: run.bat probe")

    raise SystemExit(1 if failures else 0)
