"""Watch the session while it happens. Record what changed; judge nothing.

WHY THIS EXISTS
---------------
Everything this project knows about the market, it learned after the close.
The evening job fetches daily bars and end-of-day flow, and that is the whole
of its contact with a trading session. Two consequences:

    The order book is never seen while it matters. 310 names closed with
    their ask side exhausted on 2026-10-14; not one of them was observed
    forming. depth.py can capture the book, but it had captured twice in its
    life when this module was written.

    Flow arrives too late to act on. E24 rejected inst_net_ratio at Stage 2
    on exactly this: "a feasibility filter has to survive the lag between
    computing a signal after the close and acting on it". A h=1 lift of
    2.407x decaying to 1.290x by h=3 is useless overnight. It is not
    obviously useless at 10:30.

Both gaps closed for the price of two HTTP calls per tick, measured
2026-10-15 on the live feed:

    GetMarketWatch?withBestLimits=true     1,038 instruments + 5-level books
                                           996 KB, 0.9 s
    GetClientTypeAll                       2,286 instruments' حقیقی/حقوقی
                                           519 KB, 0.5 s
    GetMarketWatch&refID=<cursor>          only what changed since the cursor
                                           644 bytes, 0.1 s

THE ONE RULE
------------
    THIS MODULE OBSERVES. IT DOES NOT SIGNAL.

No score, no ranking, no BUY, nothing that reaches factor_engine,
swing_advisor or the point-in-time firewall. It writes rows and it stops. The
reason is not modesty: a monitor that also judged would be choosing its
thresholds while watching the outcome, which is the single failure this
platform exists to refuse. Anything derived from this data belongs in a
preregistered experiment, and the data has to exist first.

So the events recorded here are DEFINITIONS, not thresholds. "The ask side at
level one is empty while the bid side is not" is an observation about the
book with no free parameter in it. "Volume is 2.5x its average" is a
parameter, and there is none of that here.

TWO CADENCES, AND THE ARITHMETIC THAT CHOSE THEM
-------------------------------------------------
A full capture is 5,856 rows. Over a 3.5-hour session and 240 sessions a
year:

    every  1 min    295.1 M rows/yr     -- would bury this database
    every  5 min     59.0 M rows/yr
    every 15 min     19.7 M rows/yr     -- ten times price_history. Fine.
    every 30 min      9.8 M rows/yr

So the full capture runs every 15 minutes by default and the operator can
change it knowing what it costs. Between captures the delta cursor is polled
every 60 seconds, which is 644 bytes when nothing has changed, and only
EVENTS are written from it. Events are rare and tiny: a name's queue forming
is one row.

That is what "watch continuously" can honestly mean on this storage budget --
minute-resolution awareness of transitions, quarter-hour resolution of the
whole book.

WHAT STOPS IT RUNNING ON A DEAD MARKET
---------------------------------------
market_session.is_trading_now(), which counts instruments reporting turnover
on a session the exchange itself dates today, rather than reading a clock. The exchange was shut for eleven weeks this year
while publishing a complete, frozen market every day (MARKET_REGIME_AUDIT.md),
so a monitor that trusted the calendar would have captured 50 identical
books and called it history. An unreadable feed returns None and is treated
as neither open nor closed: the loop waits and retries, and records nothing.

A RESTART IS NOT A MARKET EVENT
--------------------------------
The first poll after startup establishes a baseline and emits nothing. Without
that rule, restarting at 11:00 would write "queue formed" for all 300 names
that already had one at 09:05, and the event log would describe the process
rather than the market.

USAGE
    python monitor.py                  watch until the session ends
    python monitor.py --once           one tick, then exit
    python monitor.py --status         what has been collected
    python monitor.py --capture-minutes 30 --poll-seconds 120
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone

import config
import depth
import market_session

DB = config.DATABASE_NAME

# See the module docstring for the row arithmetic behind these.
CAPTURE_MINUTES = 15.0
POLL_SECONDS = 60.0

# How long to wait before asking again when the feed could not be read at all.
# Deliberately longer than the poll interval: a network that just refused a
# request is not more likely to accept one a second later, and this project's
# transport already re-probes its route on a connection error.
UNREADABLE_BACKOFF_SECONDS = 120.0

# Consecutive ticks reporting "not trading" before the loop gives up for the
# day. Three rather than one because the turnover counter is zero in the
# moments before the first trade prints, and a monitor that exits at 08:59
# has missed the session it was started for.
CLOSED_TICKS_BEFORE_EXIT = 3

# ...and closed readings taken before this Tehran time do not count towards
# that exit at all. The scheduled task starts at 08:40 and polls every 60 s,
# so three closed readings used to arrive by 08:43 and the monitor quit before
# every open it was started for -- the 2026-10-18 log shows exactly that. This
# clock never decides that a session IS running; only market_session does.
# It only stops a pre-open silence from being mistaken for a closed day.
EXIT_NOT_BEFORE = (9, 15)

# Consecutive polls returning NOTHING CHANGED, during a session established
# as live, before the feed is called frozen. The delta cursor makes this
# nearly free to detect: an unchanging market answers 644 bytes.
#
# It is a warning and never a verdict. A real market can be motionless --
# measured on 2026-10-15, a closed Thursday, three calls two seconds apart
# returned byte-identical payloads. Only the combination of "live session"
# and "nothing moved for this long" means anything, and even then the right
# response is to record it, not to invent a reading.
FROZEN_POLLS_BEFORE_WARNING = 5

SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS market_events (
    observed_at TEXT NOT NULL,
    ins_code    TEXT NOT NULL,
    symbol_fa   TEXT,
    event       TEXT NOT NULL,
    -- The reading that justified the row, so an event can be checked later
    -- without reconstructing the book it came from.
    last        REAL,
    limit_up    REAL,
    limit_down  REAL,
    bid_qty     REAL,
    ask_qty     REAL,
    PRIMARY KEY (observed_at, ins_code, event)
)
"""

SCHEMA_TICKS = """
CREATE TABLE IF NOT EXISTS monitor_ticks (
    observed_at TEXT PRIMARY KEY,
    -- trading | closed | unreadable | error | frozen
    outcome     TEXT NOT NULL,
    changed     INTEGER,
    events      INTEGER,
    captured_at TEXT,
    -- state_hash of the last full capture, market only, instant removed.
    -- Two captures agreeing during a live session is the frozen-feed signal.
    state_hash  TEXT,
    detail      TEXT
)
"""

SCHEMA_FLOW = """
CREATE TABLE IF NOT EXISTS client_type_live (
    ins_code    TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    individual_buy_volume     REAL,
    individual_sell_volume    REAL,
    institutional_buy_volume  REAL,
    institutional_sell_volume REAL,
    individual_buy_count      REAL,
    individual_sell_count     REAL,
    institutional_buy_count   REAL,
    institutional_sell_count  REAL,
    PRIMARY KEY (ins_code, captured_at)
)
"""


def _utf8_stdout():
    """Persian through a pipe. See depth._utf8_stdout for what this prevents."""

    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# SetThreadExecutionState flags. CONTINUOUS keeps the request in force until
# it is cleared; DISPLAY is included because on a Modern Standby laptop the
# machine drops into standby when the screen goes off, and SYSTEM alone does
# not stop that.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def _keep_awake(on):
    """Ask Windows not to sleep while a session is being watched.

    On 2026-10-31 the machine entered Modern Standby at 09:48:55 Tehran
    (Kernel-Power 506) and woke at 11:48 (507): two hours of the session with
    no tick, no capture and no log line, because a sleeping process cannot
    record that it is asleep. Returns True if the request was made. A no-op
    off Windows, and a closed lid still wins.
    """

    if sys.platform != "win32":
        return False

    try:
        import ctypes

        flags = (_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
                 if on else _ES_CONTINUOUS)

        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:                                           # noqa: BLE001
        return False


def ensure_schema(db=DB):
    conn = sqlite3.connect(db)

    try:
        conn.execute(SCHEMA_EVENTS)
        conn.execute(SCHEMA_TICKS)
        conn.execute(SCHEMA_FLOW)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_at "
                     "ON market_events(observed_at)")
        conn.commit()
    finally:
        conn.close()


# ==============================
# READING THE BOOK
# ==============================
# Every state below is a property of the published book with no threshold in
# it. `locked_up` is the exchange's own limit_up compared against its own last
# price -- not an inference from a 12% move, which is what E14 had to do
# before pMax was collected.

def book_state(row):
    """The observable state of one instrument, as a small tuple of flags.

    Returns a dict. Values are None where the payload cannot answer, never
    False: "there is no ask side" and "we could not see the ask side" are
    different claims and only one of them is an event.
    """

    levels = row.get("blDs") or []

    first = None

    for level in levels:
        if level.get("n") in (1, "1"):
            first = level
            break

    if first is None and levels:
        first = levels[0]

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    bid_qty = _num(first.get("qmd")) if first else None
    ask_qty = _num(first.get("qmo")) if first else None

    last = _num(row.get("pdv"))

    if last is None:
        last = _num(row.get("pDrCotVal"))

    up = _num(row.get("pMax"))
    down = _num(row.get("pMin"))

    state = {
        "last": last,
        "limit_up": up,
        "limit_down": down,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "buy_queue": None,
        "sell_queue": None,
        "at_limit_up": None,
        "at_limit_down": None,
    }

    if bid_qty is not None and ask_qty is not None:
        # صف خرید: demand standing, nothing offered against it.
        state["buy_queue"] = ask_qty == 0 and bid_qty > 0
        state["sell_queue"] = bid_qty == 0 and ask_qty > 0

    if last is not None and up is not None and up > 0:
        state["at_limit_up"] = last >= up

    if last is not None and down is not None and down > 0:
        state["at_limit_down"] = last <= down

    return state


# Each entry is (flag, event when it turns on, event when it turns off).
TRANSITIONS = (
    ("buy_queue", "buy_queue_formed", "buy_queue_broke"),
    ("sell_queue", "sell_queue_formed", "sell_queue_broke"),
    ("at_limit_up", "reached_limit_up", "left_limit_up"),
    ("at_limit_down", "reached_limit_down", "left_limit_down"),
)


def transitions(previous, current):
    """Events implied by a flag changing between two observations.

    A flag that was unknown, or is unknown now, produces nothing. Treating
    unknown as False would turn every gap in the feed into a market event,
    and the event log would describe the connection rather than the book.
    """

    events = []

    for flag, turned_on, turned_off in TRANSITIONS:
        was = previous.get(flag)
        now = current.get(flag)

        if was is None or now is None or was == now:
            continue

        events.append(turned_on if now else turned_off)

    return events


# ==============================
# THE LOOP
# ==============================

class Monitor:
    """One session's watch. Holds the last seen state per instrument."""

    def __init__(self, db=DB, provider=None, capture_minutes=CAPTURE_MINUTES,
                 poll_seconds=POLL_SECONDS, verbose=True):
        if provider is None:
            from providers.tsetmc_cdn import TsetmcCdnProvider

            provider = TsetmcCdnProvider()

        self.db = db
        self.provider = provider
        self.capture_minutes = float(capture_minutes)
        self.poll_seconds = float(poll_seconds)
        self.verbose = verbose

        self.state = {}
        self.symbols = {}
        self.cursor = None
        self.baseline_taken = False
        self.last_capture = None
        self.frozen_streak = 0

        self.ticks = 0
        self.events_written = 0
        self.captures = 0

        ensure_schema(db)

    # ---- plumbing ----------------------------------------------------

    def say(self, message):
        if self.verbose:
            print(message, flush=True)

    def _market_watch(self, with_books=True, ref_id=None):
        url = (self.provider.base_url + "/api/ClosingPrice/GetMarketWatch"
               "?market=0&paperTypes[0]=1&showTraded=false"
               "&withBestLimits=%s" % ("true" if with_books else "false"))

        if ref_id is not None:
            url += "&refID=%d" % ref_id

        payload = self.provider._get(url, timeout=(10.0, 60.0)).json()

        return (payload.get("marketwatch")
                or payload.get("marketWatch")
                or [])

    @staticmethod
    def _max_rid(rows):
        """The delta cursor: the largest order-book revision id in a payload.

        Verified on the live feed -- passing this back as refID returns only
        instruments whose book has moved since. hEven is NOT a cursor; a
        non-zero value there answers HTTP 500.
        """

        best = None

        for row in rows:
            for level in (row.get("blDs") or []):
                rid = level.get("rid")

                if isinstance(rid, int) and (best is None or rid > best):
                    best = rid

        return best

    # ---- the two things a tick can do --------------------------------

    def poll_events(self):
        """Cheap tick: read what changed, write transitions, store nothing else."""

        rows = self._market_watch(with_books=True, ref_id=self.cursor)

        cursor = self._max_rid(rows)

        if cursor is not None:
            self.cursor = cursor

        observed_at = _now()
        events = []

        for row in rows:
            ins_code = str(row.get("insCode") or "")

            if not ins_code:
                continue

            symbol = (row.get("lva") or "").strip()

            if symbol:
                self.symbols[ins_code] = symbol

            current = book_state(row)
            previous = self.state.get(ins_code)

            self.state[ins_code] = current

            if previous is None or not self.baseline_taken:
                # First sighting. A restart is not a market event.
                continue

            for event in transitions(previous, current):
                events.append((observed_at, ins_code,
                               self.symbols.get(ins_code), event,
                               current["last"], current["limit_up"],
                               current["limit_down"], current["bid_qty"],
                               current["ask_qty"]))

        if not self.baseline_taken and rows:
            self.baseline_taken = True
            self.say("  baseline: %d instruments, no events emitted"
                     % len(self.state))

        written = self._store_events(events)

        self.events_written += written

        return len(rows), written

    def _log_tick(self, observed_at, outcome, changed=None, events=None,
                  captured_at=None, state_hash=None, detail=None):
        """Every tick becomes a row, including the ones that did nothing.

        A missed slot that leaves no trace is indistinguishable later from a
        slot where the market stood still, and intraday data cannot be
        backfilled to settle the question. The same reasoning depth.py gives
        for never inventing a value: an absence has to be recorded as an
        absence.
        """

        conn = sqlite3.connect(self.db)

        try:
            conn.execute(
                "INSERT OR IGNORE INTO monitor_ticks VALUES (?,?,?,?,?,?,?)",
                (observed_at, outcome, changed, events, captured_at,
                 state_hash, detail))
            conn.commit()
        finally:
            conn.close()

    def _store_events(self, events):
        if not events:
            return 0

        conn = sqlite3.connect(self.db)

        try:
            # INSERT OR IGNORE: an event already recorded for this instant is
            # history, and the same discipline depth.py stores captures under.
            conn.executemany(
                "INSERT OR IGNORE INTO market_events VALUES (?,?,?,?,?,?,?,?,?)",
                events)
            conn.commit()

            return conn.total_changes
        finally:
            conn.close()

    def capture(self):
        """Expensive tick: the full book, plus whole-market flow.

        depth.collect() is called, not reimplemented. It already parses the
        payload with required-key discipline, hashes the capture and writes an
        immutable manifest row; a second parser here would be a second thing
        to keep correct.
        """

        result = depth.collect(db=self.db, verbose=False)

        if not isinstance(result, dict):
            result = {}

        captured_at = result.get("captured_at") or _now()

        flow_rows = self._capture_flow(captured_at)

        self.captures += 1
        self.last_capture = time.monotonic()

        return captured_at, result.get("state_hash"), flow_rows

    def _capture_flow(self, captured_at):
        result = self.provider.get_client_type_all()

        if not result.ok:
            self.say("  flow snapshot failed: %s" % result.error)
            return 0

        frame = result.data

        rows = [
            (str(row.ins_code), captured_at,
             row.individual_buy_volume, row.individual_sell_volume,
             row.institutional_buy_volume, row.institutional_sell_volume,
             row.individual_buy_count, row.individual_sell_count,
             row.institutional_buy_count, row.institutional_sell_count)
            for row in frame.itertuples(index=False)
        ]

        conn = sqlite3.connect(self.db)

        try:
            conn.executemany(
                "INSERT OR IGNORE INTO client_type_live "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()

            return conn.total_changes
        finally:
            conn.close()

    # ---- the loop itself ---------------------------------------------

    def tick(self):
        """One iteration. Returns (seconds to wait, outcome).

        Every path through here writes a row to monitor_ticks, including the
        ones that collected nothing. A slot that leaves no trace cannot be
        told apart later from a slot where the market stood still.
        """

        observed_at = _now()

        live = market_session.is_trading_now(provider=self.provider)

        if live["trading"] is None:
            self.say("%s  feed unreadable -- %s" % (observed_at,
                                                    live["reason"]))
            self._log_tick(observed_at, "unreadable", detail=live["reason"])

            return UNREADABLE_BACKOFF_SECONDS, "unreadable"

        if not live["trading"]:
            self.say("%s  not trading -- %s" % (observed_at, live["reason"]))
            self._log_tick(observed_at, "closed", detail=live["reason"])

            return self.poll_seconds, "closed"

        self.ticks += 1

        due = (self.last_capture is None
               or (time.monotonic() - self.last_capture)
               >= self.capture_minutes * 60.0)

        captured_at = None
        state = None

        if due:
            captured_at, state, flow_rows = self.capture()
            self.say("%s  capture %s  (+%d flow rows)  state %s"
                     % (observed_at, captured_at, flow_rows,
                        (state or "?")[:12]))

        seen, written = self.poll_events()

        # An empty delta during a LIVE session is the frozen-feed signature.
        # The cursor makes it almost free to notice: nothing changed answers
        # 644 bytes. Only recorded, never acted on -- see the constant.
        if self.baseline_taken and seen == 0:
            self.frozen_streak += 1
        else:
            self.frozen_streak = 0

        outcome = "trading"

        if self.frozen_streak >= FROZEN_POLLS_BEFORE_WARNING:
            outcome = "frozen"
            self.say("%s  WARNING: %d consecutive polls with nothing "
                     "changed, during a session reading as live"
                     % (observed_at, self.frozen_streak))

        self.say("%s  %d changed, %d event%s   [session: %d captures, "
                 "%d events]"
                 % (observed_at, seen, written, "" if written == 1 else "s",
                    self.captures, self.events_written))

        self._log_tick(observed_at, outcome, changed=seen, events=written,
                       captured_at=captured_at, state_hash=state,
                       detail=live["reason"])

        return self.poll_seconds, outcome

    def run(self, once=False, max_ticks=None, sleeper=time.sleep,
            clock=None):
        held = _keep_awake(True)

        try:
            return self._run(once, max_ticks, sleeper, clock)
        finally:
            if held:
                _keep_awake(False)

    def _run(self, once, max_ticks, sleeper, clock):
        closed_streak = 0
        clock = clock or market_session.tehran_now

        while True:
            try:
                wait, outcome = self.tick()
            except Exception as exc:                            # noqa: BLE001
                # A monitor that dies on one bad response has stopped
                # monitoring, which is the failure it exists to prevent.
                self.say("%s  tick failed: %s: %s"
                         % (_now(), type(exc).__name__, str(exc)[:160]))
                wait, outcome = UNREADABLE_BACKOFF_SECONDS, "error"

            if outcome == "closed":
                local = clock()

                if (local.hour, local.minute) >= EXIT_NOT_BEFORE:
                    closed_streak += 1

                if closed_streak >= CLOSED_TICKS_BEFORE_EXIT:
                    self.say("session is not running; stopping after %d "
                             "consecutive closed readings" % closed_streak)
                    return 0
            else:
                closed_streak = 0

            if once:
                return 0

            if max_ticks is not None and self.ticks >= max_ticks:
                return 0

            sleeper(wait)


# ==============================
# THE READ SIDE -- what the panel asks for
# ==============================
# Everything here reads the tables the loop above writes. It never fetches: a
# dashboard load must not put a request on TSETMC, and the loop is at most one
# poll behind anyway.
#
# THE AGE IS PART OF THE ANSWER, NOT A FOOTNOTE. This panel is the one place in
# the project most likely to be mistaken for a live terminal, and the monitor
# only runs during a session. Opened on a Thursday it is showing Wednesday's
# close; opened while the scheduled task is broken it could be showing last
# month. So every payload carries the capture instant and its age in minutes,
# and the caller is expected to render them.


def _ro(db=DB):
    return sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"),
                           uri=True)


def _age_minutes(stamp):
    if not stamp:
        return None

    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None

    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)

    return round((datetime.now(timezone.utc) - then).total_seconds() / 60.0, 1)


def latest_capture(db=DB):
    conn = _ro(db)

    try:
        row = conn.execute(
            "SELECT MAX(captured_at) FROM depth_snapshot").fetchone()

        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def queues(captured_at=None, limit=25, db=DB):
    """Names locked at the last capture, deepest queue first.

    A queue is read off the book, not inferred: the ask side at level one is
    empty while the bid side is not, which is صف خرید, and the mirror is صف
    فروش. `bid_qty` is the demand standing behind the lock -- the number E14
    measured a 3.03x queue-exposure ratio without ever seeing a rial of.

    Ordered by size because size is a magnitude. Nothing here ranks names by
    how good they are; this module does not have an opinion about that.
    """

    captured_at = captured_at or latest_capture(db)

    if not captured_at:
        return {"captured_at": None, "age_minutes": None,
                "buy": [], "sell": []}

    conn = _ro(db)

    sql = """
        SELECT s.ins_code, s.symbol_fa, s.last, s.prev_close,
               s.limit_up, s.limit_down, s.value, s.trades,
               l.bid_qty, l.bid_orders, l.ask_qty, l.ask_orders
        FROM depth_levels l
        JOIN depth_snapshot s
          ON s.ins_code = l.ins_code AND s.captured_at = l.captured_at
        JOIN instruments i ON i.ins_code = s.ins_code
        WHERE l.captured_at = ? AND l.level = 1 AND i.is_equity = 1
          AND %s
        ORDER BY %s DESC
        LIMIT ?
    """

    def fetch(side):
        if side == "buy":
            where = "(l.ask_qty IS NULL OR l.ask_qty = 0) AND l.bid_qty > 0"
            order = "l.bid_qty"
        else:
            where = "(l.bid_qty IS NULL OR l.bid_qty = 0) AND l.ask_qty > 0"
            order = "l.ask_qty"

        rows = conn.execute(sql % (where, order),
                            (captured_at, limit)).fetchall()

        out = []

        for (ins_code, symbol, last, prev, up, down, value, trades,
             bid_qty, bid_orders, ask_qty, ask_orders) in rows:
            qty = bid_qty if side == "buy" else ask_qty
            orders = bid_orders if side == "buy" else ask_orders

            out.append({
                "ins_code": ins_code,
                "symbol": symbol,
                "last": last,
                "prev_close": prev,
                "change_pct": (round((last - prev) / prev * 100, 2)
                               if last and prev else None),
                "limit_up": up,
                "limit_down": down,
                "queue_shares": qty,
                "queue_orders": orders,
                # Rials standing in the queue, which is what "how big is this
                # صف" means to someone judging whether it will clear.
                "queue_value": (round(qty * last) if qty and last else None),
                "value": value,
                "trades": trades,
            })

        return out

    try:
        return {"captured_at": captured_at,
                "age_minutes": _age_minutes(captured_at),
                "buy": fetch("buy"),
                "sell": fetch("sell")}
    finally:
        conn.close()


def queue_counts(captured_at=None, db=DB):
    """How many equities are locked each way at the last capture."""

    captured_at = captured_at or latest_capture(db)

    if not captured_at:
        return {"buy": 0, "sell": 0, "instruments": 0}

    conn = _ro(db)

    try:
        buy, sell, total = conn.execute("""
            SELECT
              SUM(CASE WHEN (l.ask_qty IS NULL OR l.ask_qty = 0)
                        AND l.bid_qty > 0 THEN 1 ELSE 0 END),
              SUM(CASE WHEN (l.bid_qty IS NULL OR l.bid_qty = 0)
                        AND l.ask_qty > 0 THEN 1 ELSE 0 END),
              COUNT(*)
            FROM depth_levels l
            JOIN instruments i ON i.ins_code = l.ins_code
            WHERE l.captured_at = ? AND l.level = 1 AND i.is_equity = 1
        """, (captured_at,)).fetchone()

        return {"buy": int(buy or 0), "sell": int(sell or 0),
                "instruments": int(total or 0)}
    except sqlite3.OperationalError:
        return {"buy": 0, "sell": 0, "instruments": 0}
    finally:
        conn.close()


def recent_events(limit=40, db=DB):
    """The last transitions the loop saw, newest first.

    Empty is the normal state outside a session and must not be dressed up:
    the loop only writes when the market moves, so nothing here means either
    nothing moved or nothing was watching, and the tick ledger is what tells
    those apart.
    """

    conn = _ro(db)

    try:
        rows = conn.execute("""
            SELECT observed_at, ins_code, symbol_fa, event,
                   last, limit_up, limit_down, bid_qty, ask_qty
            FROM market_events
            ORDER BY observed_at DESC, ins_code
            LIMIT ?
        """, (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    return [{"observed_at": r[0], "ins_code": r[1], "symbol": r[2],
             "event": r[3], "last": r[4], "limit_up": r[5],
             "limit_down": r[6], "bid_qty": r[7], "ask_qty": r[8],
             "age_minutes": _age_minutes(r[0])}
            for r in rows]


def live_flow(limit=25, min_participants=20, db=DB):
    """Intraday قدرت خریدار from the newest flow snapshot.

    Per-capita retail buying over per-capita retail selling, computed from
    volumes and counts because that is what the bulk endpoint publishes. It
    matches the value-based figure at correlation 0.99999999 over 57,649
    stored rows -- both legs of one instrument trade at nearly one price
    inside a session, so price cancels in the ratio.

    `min_participants` is a FLOOR ON EVIDENCE, not a filter on outcome: a
    ratio built from two buyers and one seller is arithmetic, not a reading.
    moneyflow.py makes the same point about daily ratios reaching the millions
    on a session where four people sold.
    """

    conn = _ro(db)

    try:
        stamp = conn.execute(
            "SELECT MAX(captured_at) FROM client_type_live").fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return {"captured_at": None, "age_minutes": None, "names": [],
                "universe": 0}

    stamp = stamp[0] if stamp else None

    if not stamp:
        conn.close()
        return {"captured_at": None, "age_minutes": None, "names": [],
                "universe": 0}

    try:
        rows = conn.execute("""
            SELECT f.ins_code, i.symbol_fa,
                   f.individual_buy_volume, f.individual_sell_volume,
                   f.individual_buy_count, f.individual_sell_count
            FROM client_type_live f
            JOIN instruments i ON i.ins_code = f.ins_code
            WHERE f.captured_at = ? AND i.is_equity = 1
              AND f.individual_buy_count >= ?
              AND f.individual_sell_count >= ?
              AND f.individual_sell_volume > 0
              AND f.individual_buy_volume > 0
        """, (stamp, min_participants, min_participants)).fetchall()
    finally:
        conn.close()

    names = []

    for ins_code, symbol, buy_vol, sell_vol, buy_cnt, sell_cnt in rows:
        power = (buy_vol / buy_cnt) / (sell_vol / sell_cnt)

        names.append({
            "ins_code": ins_code,
            "symbol": symbol,
            "buyer_power": round(power, 3),
            "buyers": int(buy_cnt or 0),
            "sellers": int(sell_cnt or 0),
            # One fact, signed once: every share حقیقی bought net was sold
            # net by حقوقی. moneyflow.py refuses to report both as separate
            # evidence and so does this.
            "net_retail_shares": round((buy_vol or 0) - (sell_vol or 0)),
        })

    names.sort(key=lambda row: row["buyer_power"], reverse=True)

    return {"captured_at": stamp, "age_minutes": _age_minutes(stamp),
            "names": names[:limit], "universe": len(names)}


def last_tick(db=DB):
    """The loop's own most recent record of itself.

    What separates "the market stood still" from "nothing was watching".
    """

    conn = _ro(db)

    try:
        row = conn.execute("""
            SELECT observed_at, outcome, changed, events, detail
            FROM monitor_ticks ORDER BY observed_at DESC LIMIT 1
        """).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

    if not row:
        return None

    return {"observed_at": row[0], "outcome": row[1], "changed": row[2],
            "events": row[3], "detail": row[4],
            "age_minutes": _age_minutes(row[0])}


def status(db=DB):
    conn = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"),
                           uri=True)

    try:
        try:
            events, first, last = conn.execute(
                "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) "
                "FROM market_events").fetchone()
        except sqlite3.OperationalError:
            print("nothing collected yet -- run `python monitor.py`")
            return 0

        print("events        : %d   (%s .. %s)" % (events or 0, first, last))

        try:
            snaps, ffirst, flast = conn.execute(
                "SELECT COUNT(*), MIN(captured_at), MAX(captured_at) "
                "FROM client_type_live").fetchone()
            print("flow rows     : %d   (%s .. %s)"
                  % (snaps or 0, ffirst, flast))
        except sqlite3.OperationalError:
            print("flow rows     : none")

        if events:
            print()
            print("by kind:")

            for kind, count in conn.execute(
                    "SELECT event, COUNT(*) FROM market_events "
                    "GROUP BY event ORDER BY COUNT(*) DESC"):
                print("   %-22s %6d" % (kind, count))

        return 0
    finally:
        conn.close()


def main(argv=None):
    _utf8_stdout()

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true",
                        help="one tick, then exit")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--capture-minutes", type=float,
                        default=CAPTURE_MINUTES,
                        help="full book + flow capture interval "
                             "(default %(default)s; see the module docstring "
                             "for what each setting costs in rows)")
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS,
                        help="event poll interval (default %(default)s)")
    parser.add_argument("--max-ticks", type=int, default=None)
    parser.add_argument("--db", default=DB)

    args = parser.parse_args(argv)

    if args.status:
        return status(db=args.db)

    monitor = Monitor(db=args.db,
                      capture_minutes=args.capture_minutes,
                      poll_seconds=args.poll_seconds)

    return monitor.run(once=args.once, max_ticks=args.max_ticks)


if __name__ == "__main__":
    sys.exit(main())
