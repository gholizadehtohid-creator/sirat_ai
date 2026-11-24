"""Corporate actions from the exchange itself: dividends, capital increases,
and a total-return price basis built on them.

    python corporate_actions.py --fetch [--max N] [--stale-days 7] [--dry-run]
                                 the exchange's lists, two requests per equity
    python corporate_actions.py --reclassify
                                 rebuild the events from what is stored
                                 (no network): after a bars refresh
    python corporate_actions.py --report       what is stored
    python corporate_actions.py --measure      the measurements below, again
    python corporate_actions.py --audit        backtest_output/total_return_audit.json

This is opt-in code. Nothing that already reads prices -- backtest.back_
adjust, backtesting/pit.py, the registered experiments, the Alpha Core
fixture -- calls it, and it changes none of them.

WHY
---
TEDPIX is a price-and-dividend index. This bot keeps one dividend per
company (its last AGM, from Codal), and back_adjust finds the rest from the
shape of the raw series: a gap past 12% on a traded bar, any move on an
untraded one, a known DPS at the reopening. A multi-year comparison with
TEDPIX therefore rested on a heuristic. The exchange publishes its own
adjustment record, and that is what this module stores.

THE TWO LISTS (cdn.tsetmc.com, one request each per instrument)
----------------------------------------------------------------
  ClosingPrice/GetPriceAdjustList/{insCode}
      {"priceAdjust": [{"dEven", "pClosing", "pClosingNotAdjusted"}, ...]}
      newest first; pClosing is the price after the event, NotAdjusted the
      last price before it. Two rows can share a date (فملی 2011-07-05:
      15241 -> 12991 -> 4911, a dividend and an increase on one day).
  Instrument/GetInstrumentShareChange/{insCode}
      {"instrumentShareChange": [{"dEven", "numberOfShareOld",
                                  "numberOfShareNew"}, ...]}

WHAT WAS MEASURED (2026-11-24, all 909 active equities; --measure prints
it again, and backtest_output/total_return_audit.json keeps it under
"method_measurements")
-----------------------------------------------------------------------------
1. THE PRICE LIST STOPPED CARRYING CAPITAL INCREASES IN MID-2020. Increases
   it carries (rows matched to a share change): 121 in 2019, 9 in 2020,
   then 0-1 a year 2021-2026 -- while the share-change list holds 196-299 a
   year from 2020 on (فولاد 2021-05-24 x1.40 ... 2026-03-30 x1.29: none in
   the price list, and the raw bars drop on exactly those dates, 3359 ->
   2604 = 1/1.29 on an untraded bar). The price list alone would leave every
   recent increase in the series as a loss, so increases it lacks are
   rebuilt from the share-change list (source 'share_change', below).

2. THE DATE CONVENTION CHANGED WITH IT, between 2020-06-21 and 2020-06-22.
   Up to then dEven is the FIRST session on the new basis: the raw close of
   the session before it equals pClosingNotAdjusted (4,303 rows; 161
   otherwise). From then on dEven is the LAST session on the old basis --
   the meeting day, the symbol halted for it: the raw close ON dEven equals
   pClosingNotAdjusted (3,778 rows; 182 otherwise, 181 of them in 2020) and the
   next session opens on the new basis, as an untraded bar whose close
   equals pClosing in 2,522 of 2,625 cases (فولاد 2026-08-03 2604, 08-05
   2549 = DPS 55). The ex session is untraded in 75% of events (6,293 of
   8,420). pClosingNotAdjusted equals the close of the last session WITH
   volume before the ex session in 79% (6,450 of 8,167): the rest are names
   whose frozen close had already been re-priced. "Equal" is to within
   0.1% (20945 listed vs 20940 stored). The ex date is therefore read per
   event off the bars (`convention` 'last_cum' / 'first_ex'); with no bar
   to read, CONVENTION_SWITCH decides ('assumed').

3. A SHARE CHANGE IS DATED AT THE RE-PRICING. Of 2,328 increases of x1.25
   or more (where a re-pricing is unmistakable), searched 60 sessions back
   and 20 ahead: 2,195 re-priced on the share change's own session, 2,218
   within +/-2 sessions, 37 scattered from -59 to +20 (coincidental drops),
   73 not found. SHARE_CHANGE_WINDOW is +/-2 sessions, nearest first.

4. CODAL AGREES. 640 exchange dividends have a Codal DPS (sirat_
   fundamentals.db `dividends`) published within CODAL_MATCH_DAYS; 621
   (97.0%) agree within 1 rial or 1%. Codal's decision is published a
   median 2 days after the exchange's dEven (p5 -3, p95 +13). 14 of the 19
   disagreements are a share change between year-end and AGM whose ratio is
   exactly codal/exchange (حبندر 2930 vs 733, x4.0): Codal quotes DPS on the
   old share count, the exchange on the new -- the exchange's is the one
   the price moved by.

5. THE LISTS ARE NOT CLEAN.
   * Crowded rows: 36 instruments carry rows that are not corporate actions
     -- نبورس 730 rows 2016-2020, کبورس 533, اکالا/نکالا/تکالا ~105 each
     (one a session, each "adjusting" to the next day's price), and pairs
     of a wrong adjustment and its reversal days apart (بسویچ). A row with
     CROWDED_ROWS or more dates within CROWDED_DAYS is kind 'suspect' and
     is not applied (1,723 rows, none after 2020-06-22).
   * Reversed share changes: 406 rows (203 pairs) are a registration and
     its reversal within CORRECTION_DAYS (product 1 to 2%): kind
     'share_correction'. In 192 pairs the exchange also re-priced the
     frozen close to the wrong count and back (وبیمه 2021-01-16 x2.149);
     those spikes are taken out ('reversed_pair_reprice'), nothing else.
   * Missing events: some increases are in neither list (شمواد 2023-12-06,
     frozen close 141,050 -> 3,050, no share change; شتهران twice). 88 of
     563 names in the audit have such a move. exchange_adjust leaves them
     unless fill_untraded=True.
   * 257 price-list rows have factor >= 1 and no share change (kind
     'other'; capital reductions, no-op rows, reversals), all before 2021.

HOW AN INCREASE MISSING FROM THE PRICE LIST IS PRICED
-----------------------------------------------------
A rights issue is subscribed at the 1,000-rial nominal value; a bonus issue
(retained earnings, reserves) costs nothing. With r = new/old shares and P
the last close on the old basis (net of a price-list event on the same
session), the exchange's new base B lies in
    [P / r, (P + 1000 (r - 1)) / r]         bonus ... all-cash rights
and the total-return factor is B / P (rights valued at their theoretical
price, the usual total-return convention). Neither list gives the mix. Of
the 1,848 increases rebuilt:
  * 1,627 'observed_reprice': the ex session is untraded and its close IS
    the exchange's base -- factor exact. It must fit the interval to within
    ADJUST_MATCH_TOLERANCE: measured, 1,620 of 1,646 untraded share-change
    sessions do (1,123 strictly, 497 within 2%: price rounding).
  * 168 'observed_open': the ex session traded; B = its first trade,
    clamped into the interval, whose width (factor_low..factor_high, stored)
    bounds the error: median 7.4%, p95 80% (low prices). A reopening after
    a halt is accepted up to REOPEN_AFTER_HALT above the rights bound (see
    there for the measured first-trade gaps).
  * 53 'assumed': no bar on one side of the date; bonus factor 1/r.
  * 24 'capital_unpriced': bars exist but nothing re-priced within the
    window (a public offering at market price, سلب حق تقدم): factor 1.
An increase the price list DOES carry is matched to its share change (dEven
or ex date within ADJUST_MATCH_DAYS, factor inside the interval, and the
share change's own session not re-priced separately -- ومعلم 2022-07-11
DPS 250 then x1.385 on 07-12 is a dividend and an increase) and keeps the
exchange's factor: kind 'capital', source 'adjust_list' (985 rows). Every
other price-list row with factor < 1 is a dividend, dps =
pClosingNotAdjusted - pClosing (7,542 rows).

THE TOTAL-RETURN BASIS
----------------------
exchange_adjust() multiplies every price before an event's ex-date by the
event's factor, anchoring at the newest bar exactly as back_adjust does.
Dividends are thereby reinvested at the ex-date base price. Volume is left
alone. It reads no bar past the frame it is given.

WHAT IT CHANGES (the audit, 2021-06-01..2026-08-18; descriptive, no verdict
on any experiment): against today's back_adjust the gap is small -- median
+0.38 pp/yr per name, +1.3 pp/yr equal-weight buy-and-hold, over the 475
names whose lists are complete -- because back_adjust already takes out
every dividend the exchange re-priced on a frozen close. Against the
back_adjust in force before a5c810a (2026-11-11) it is median +5.2 pp/yr,
+7.1 equal-weight. The dividends alone add a median 4.6%/yr.
"""

import argparse
import bisect
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import database
import jalali
import settings


HOST = "cdn.tsetmc.com"
ADJUST_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetPriceAdjustList/%s"
SHARE_URL = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentShareChange/%s"
ADJUST_KEY = "priceAdjust"
SHARE_KEY = "instrumentShareChange"

# Between any two requests of a run, on top of the response time.
REQUEST_GAP_SECONDS = 0.6
MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_CAP_SECONDS = 60.0
REFUSED_STATUS = (403, 429)
STALE_DAYS = 7

# Rights are subscribed at the nominal value.
NOMINAL_RIALS = 1000.0

# A close of 10 rials or less is a placeholder, not a price (backtest.py).
PLACEHOLDER_RIALS = 10.0

# Two prices are "the same" to within 0.1%: 20945 listed against a stored
# 20940 (فولاد 2020-08-05) is the widest disagreement measured.
SAME_PRICE_TOLERANCE = 0.001

# Before this date the price list dates an event at its first ex session,
# from it on at its last cum session (see the module docstring). Only used
# when no bar shows which.
CONVENTION_SWITCH = "2020-06-21"

# An increase the price list carries is the same event as a share change on
# a date this close; its factor must lie inside the bonus..rights interval
# to within the rounding of a listed price.
ADJUST_MATCH_DAYS = 7
ADJUST_MATCH_TOLERANCE = 0.02

# How far the first trade on the new basis can sit from the base price and
# still be read as the re-pricing: the daily band (+/-5% for most of the
# period, wider at some reopenings) with room to spare.
REOPEN_TOLERANCE = 0.10

# The first trade after a halt can sit much further above the base: measured
# over 276 exact re-prices (an untraded bar whose close is the base), the
# first traded open after them was within +/-5.5% of the base in 77% of
# cases and above +21.6% in 5% (median +3%). A reopening after a halt is
# therefore accepted up to this far above the rights bound.
REOPEN_AFTER_HALT = 0.25

# Sessions either side of a share change's date searched for its re-pricing
# (nearest first). Chosen from the measured lag distribution: see the
# module docstring and --measure.
SHARE_CHANGE_WINDOW = (2, 2)

# The research window; the audit never reads past AUDIT_END (2026-08-19..
# 2027-08-19 is the reserved forward window).
AUDIT_START = "2021-06-01"
AUDIT_END = "2026-08-18"
AUDIT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backtest_output", "total_return_audit.json")

KIND_LABELS = {
    "dividend": "سود نقدی",
    "capital": "افزایش سرمایه",
    "capital_unpriced": "افزایش سرمایه بدون تعدیل قیمت",
    "other": "تعدیل دیگر",
    "suspect": "ردیف مشکوک فهرست تعدیل",
    "share_correction": "اصلاح ثبت تعداد سهام",
}


class FetchError(Exception):
    """One request failed; the run goes on."""


class Refused(FetchError):
    """The host refused (403/429): the run stops at once."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =============================================================================
# STORAGE
# =============================================================================

_ready = set()


def ensure_tables():
    """Create this module's tables in sirat_market.db, once per file."""

    database.create_database()
    if database.DATABASE_NAME in _ready and \
            os.path.exists(database.DATABASE_NAME):
        return
    conn = database._connect()
    try:
        conn.executescript("""
        -- The exchange's price-adjustment list as served (one row per event;
        -- seq orders rows sharing a date, oldest first).
        CREATE TABLE IF NOT EXISTS price_adjustments (
            ins_code     TEXT NOT NULL,
            date         TEXT NOT NULL,
            seq          INTEGER NOT NULL DEFAULT 0,
            adjusted     REAL,
            not_adjusted REAL,
            fetched_at   TEXT,
            PRIMARY KEY (ins_code, date, seq));
        -- The exchange's share-count changes as served.
        CREATE TABLE IF NOT EXISTS share_changes (
            ins_code   TEXT NOT NULL,
            date       TEXT NOT NULL,
            seq        INTEGER NOT NULL DEFAULT 0,
            shares_old REAL,
            shares_new REAL,
            ratio      REAL,
            fetched_at TEXT,
            PRIMARY KEY (ins_code, date, seq));
        -- The events derived from the two (classify()): what
        -- exchange_adjust() applies. Rebuilt per instrument.
        CREATE TABLE IF NOT EXISTS corporate_actions (
            ins_code     TEXT NOT NULL,
            date         TEXT NOT NULL,
            seq          INTEGER NOT NULL DEFAULT 0,
            source       TEXT NOT NULL,
            kind         TEXT,
            ex_date      TEXT,
            ex_observed  INTEGER DEFAULT 0,
            convention   TEXT,
            adjusted     REAL,
            not_adjusted REAL,
            factor       REAL,
            factor_low   REAL,
            factor_high  REAL,
            dps          REAL,
            share_ratio  REAL,
            matched_share_change TEXT,
            fetched_at   TEXT,
            PRIMARY KEY (ins_code, date, source, seq));
        CREATE INDEX IF NOT EXISTS corporate_actions_ex
            ON corporate_actions (ex_date);
        CREATE TABLE IF NOT EXISTS corporate_actions_status (
            ins_code     TEXT PRIMARY KEY,
            symbol       TEXT,
            fetched_at   TEXT,
            attempted_at TEXT,
            adjust_rows  INTEGER,
            share_rows   INTEGER,
            events       INTEGER,
            error        TEXT);
        """)
        conn.commit()
    finally:
        conn.close()
    _ready.add(database.DATABASE_NAME)


EVENT_COLUMNS = ("ins_code", "date", "seq", "source", "kind", "ex_date",
                 "ex_observed", "convention", "adjusted", "not_adjusted",
                 "factor", "factor_low", "factor_high", "dps", "share_ratio",
                 "matched_share_change", "fetched_at")


def store(ins_code, adjust_rows, share_rows, events, symbol=None, now=None):
    """Replace one instrument's lists and events; record the fetch."""

    ensure_tables()
    now = now or _now()
    code = str(ins_code)
    conn = database._connect()
    try:
        conn.execute("DELETE FROM price_adjustments WHERE ins_code = ?",
                     (code,))
        conn.execute("DELETE FROM share_changes WHERE ins_code = ?", (code,))
        conn.executemany(
            "INSERT INTO price_adjustments (ins_code, date, seq, adjusted, "
            "not_adjusted, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(code, r["date"], r["seq"], r["adjusted"], r["not_adjusted"],
              now) for r in adjust_rows])
        conn.executemany(
            "INSERT INTO share_changes (ins_code, date, seq, shares_old, "
            "shares_new, ratio, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(code, r["date"], r["seq"], r["shares_old"], r["shares_new"],
              r["ratio"], now) for r in share_rows])
        _write_events(conn, code, events, now)
        conn.execute("""
            INSERT INTO corporate_actions_status
                (ins_code, symbol, fetched_at, attempted_at, adjust_rows,
                 share_rows, events, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT (ins_code) DO UPDATE SET
                symbol = COALESCE(excluded.symbol, symbol),
                fetched_at = excluded.fetched_at,
                attempted_at = excluded.attempted_at,
                adjust_rows = excluded.adjust_rows,
                share_rows = excluded.share_rows,
                events = excluded.events, error = NULL
        """, (code, symbol, now, now, len(adjust_rows), len(share_rows),
              len(events)))
        conn.commit()
    finally:
        conn.close()


def _write_events(conn, code, events, now):
    conn.execute("DELETE FROM corporate_actions WHERE ins_code = ?", (code,))
    conn.executemany(
        "INSERT INTO corporate_actions (%s) VALUES (%s)"
        % (", ".join(EVENT_COLUMNS), ", ".join("?" for _ in EVENT_COLUMNS)),
        [tuple(now if c == "fetched_at" else
               (code if c == "ins_code" else e.get(c))
               for c in EVENT_COLUMNS) for e in events])


def record_failure(ins_code, error, symbol=None, now=None):
    """Note a failed fetch. Never raises: a store too busy to take the note
    must not end the run (the instrument is simply due again next time)."""

    try:
        ensure_tables()
        conn = database._connect()
        try:
            conn.execute("""
                INSERT INTO corporate_actions_status
                    (ins_code, symbol, attempted_at, error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (ins_code) DO UPDATE SET
                    symbol = COALESCE(excluded.symbol, symbol),
                    attempted_at = excluded.attempted_at,
                    error = excluded.error
            """, (str(ins_code), symbol, now or _now(), str(error)[:300]))
            conn.commit()
        finally:
            conn.close()
    except Exception:                                           # noqa: BLE001
        return False
    return True


def load_raw(ins_code):
    """(adjust_rows, share_rows) as stored, oldest first."""

    ensure_tables()
    conn = database._connect()
    try:
        adjust = [dict(zip(("date", "seq", "adjusted", "not_adjusted"), r))
                  for r in conn.execute(
                      "SELECT date, seq, adjusted, not_adjusted FROM "
                      "price_adjustments WHERE ins_code = ? ORDER BY date, "
                      "seq", (str(ins_code),))]
        share = [dict(zip(("date", "seq", "shares_old", "shares_new",
                           "ratio"), r))
                 for r in conn.execute(
                     "SELECT date, seq, shares_old, shares_new, ratio FROM "
                     "share_changes WHERE ins_code = ? ORDER BY date, seq",
                     (str(ins_code),))]
    finally:
        conn.close()
    return adjust, share


def events(ins_code):
    """Every stored event for one instrument, oldest first.

    Each: {date, date_jalali, ex_date, ex_date_jalali, ex_observed, kind,
    kind_fa, source, convention, factor, factor_low, factor_high, adjusted,
    not_adjusted, dps, share_ratio, matched_share_change, seq}. `date` is the
    exchange's own (dEven), `ex_date` the first session on the new basis;
    prices before ex_date are scaled by `factor` on the total-return basis.
    """

    ensure_tables()
    conn = database._connect()
    try:
        rows = conn.execute(
            "SELECT %s FROM corporate_actions WHERE ins_code = ? "
            "ORDER BY date, source, seq" % ", ".join(EVENT_COLUMNS),
            (str(ins_code),)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        event = dict(zip(EVENT_COLUMNS, row))
        event.pop("ins_code")
        event.pop("fetched_at")
        event["ex_observed"] = bool(event["ex_observed"])
        event["date_jalali"] = to_jalali(event["date"])
        event["ex_date_jalali"] = to_jalali(event["ex_date"])
        event["kind_fa"] = KIND_LABELS.get(event["kind"], event["kind"])
        out.append(event)
    return out


_stored_events = events          # exchange_adjust's argument shadows the name


def status(ins_codes=None):
    """{ins_code: status row} for the fetch."""

    ensure_tables()
    conn = database._connect()
    try:
        rows = conn.execute(
            "SELECT ins_code, symbol, fetched_at, attempted_at, adjust_rows, "
            "share_rows, events, error FROM corporate_actions_status"
        ).fetchall()
    finally:
        conn.close()
    keys = ("ins_code", "symbol", "fetched_at", "attempted_at",
            "adjust_rows", "share_rows", "events", "error")
    wanted = None if ins_codes is None else {str(c) for c in ins_codes}
    return {r[0]: dict(zip(keys, r)) for r in rows
            if wanted is None or r[0] in wanted}


# =============================================================================
# DATES
# =============================================================================

def iso_from_deven(value):
    """20260803 -> '2026-08-03'; None for anything else."""

    try:
        text = "%08d" % int(value)
        return date(int(text[:4]), int(text[4:6]), int(text[6:])).isoformat()
    except (TypeError, ValueError):
        return None


def to_jalali(iso):
    """'2026-08-03' -> '1405/05/12'; None when unparseable."""

    if not iso:
        return None
    try:
        day = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    found = jalali.today_jalali(day)
    return "%04d/%02d/%02d" % found if found else None


def _days(a, b):
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _plus_days(iso, days):
    return (date.fromisoformat(iso[:10]) + timedelta(days=days)).isoformat()


# =============================================================================
# PARSING
# =============================================================================

def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_adjust(rows):
    """priceAdjust rows -> [{date, seq, adjusted, not_adjusted}], oldest
    first. Rows sharing a date keep their order as events: the list is
    newest first, so the later row in it is the earlier event."""

    parsed = []
    for position, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        day = iso_from_deven(row.get("dEven"))
        after = _number(row.get("pClosing"))
        before = _number(row.get("pClosingNotAdjusted"))
        if not day or not after or not before or after <= 0 or before <= 0:
            continue
        parsed.append((day, -position, after, before))
    parsed.sort()
    out, seen = [], {}
    for day, _order, after, before in parsed:
        seq = seen.get(day, 0)
        seen[day] = seq + 1
        out.append({"date": day, "seq": seq, "adjusted": after,
                    "not_adjusted": before})
    return out


def parse_shares(rows):
    """instrumentShareChange rows -> [{date, seq, shares_old, shares_new,
    ratio}], oldest first."""

    parsed = []
    for position, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        day = iso_from_deven(row.get("dEven"))
        old = _number(row.get("numberOfShareOld"))
        new = _number(row.get("numberOfShareNew"))
        if not day or not old or not new or old <= 0 or new <= 0:
            continue
        parsed.append((day, -position, old, new))
    parsed.sort()
    out, seen = [], {}
    for day, _order, old, new in parsed:
        seq = seen.get(day, 0)
        seen[day] = seq + 1
        out.append({"date": day, "seq": seq, "shares_old": old,
                    "shares_new": new, "ratio": new / old})
    return out


# =============================================================================
# CLASSIFICATION
# =============================================================================

class Bars:
    """The raw bars classify() reads: ISO dates, open, close, volume."""

    def __init__(self, frame=None):
        self.dates, self.open, self.close, self.volume = [], [], [], []
        if frame is None or len(frame) == 0:
            return
        import pandas as pd
        self.dates = [str(d)[:10] for d in
                      pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")]
        self.close = [_number(v) or 0.0 for v in frame["close"]]
        self.open = ([_number(v) or 0.0 for v in frame["open"]]
                     if "open" in frame.columns else list(self.close))
        self.volume = ([_number(v) or 0.0 for v in frame["volume"]]
                       if "volume" in frame.columns else [1.0] * len(self))

    def __len__(self):
        return len(self.dates)

    def at_or_before(self, iso):
        """Index of the last bar dated on or before `iso`, or -1."""
        return bisect.bisect_right(self.dates, iso) - 1

    def first_from(self, iso):
        """Index of the first bar dated on or after `iso` (may be len)."""
        return bisect.bisect_left(self.dates, iso)

    def traded(self, i):
        return self.volume[i] > 0


def _same(a, b):
    return bool(a) and bool(b) and \
        abs(a - b) <= max(0.5, SAME_PRICE_TOLERANCE * abs(b))


def locate_adjustment(bars, day, not_adjusted):
    """(ex_index, convention) for one price-list row.

    'last_cum': the bar ON `day` closes at not_adjusted, so the event starts
    at the next bar. 'first_ex': the bar before `day` does, and `day` is the
    first on the new basis. 'assumed': no bar says; the era decides.
    ex_index may equal len(bars): the event is after the newest bar.
    """

    n = len(bars)
    i = bars.at_or_before(day)
    if i >= 0:
        if _same(bars.close[i], not_adjusted):
            return i + 1, "last_cum"
        if bars.dates[i] == day and i >= 1 and \
                _same(bars.close[i - 1], not_adjusted):
            return i, "first_ex"
    if day >= CONVENTION_SWITCH:
        return i + 1, "assumed"
    return bars.first_from(day), "assumed"


def _ex_date(bars, index, day, convention):
    """(ex_date, observed): the first bar on the new basis, or, past the
    newest bar, the date that convention implies."""

    if 1 <= index < len(bars):
        return bars.dates[index], True
    if convention == "first_ex" or (convention == "assumed"
                                    and day < CONVENTION_SWITCH):
        return day, False
    return _plus_days(day, 1), False


def capital_bounds(ratio, price):
    """(low, high) total-return factor of an increase of `ratio` at
    `price`: bonus (P/r) to all-cash rights at the nominal value."""

    if not ratio or ratio <= 0:
        return None, None
    low = 1.0 / ratio
    if ratio <= 1.0 or not price or price <= PLACEHOLDER_RIALS:
        return low, low
    high = (price + NOMINAL_RIALS * (ratio - 1.0)) / (ratio * price)
    return low, max(low, high)


def find_reprice(bars, day, ratio, window=SHARE_CHANGE_WINDOW,
                 prior_factor=None):
    """The bar that re-prices a share change, nearest to its date first.

    Returns (index, lag_sessions, price_before, base_observed) or None.
    price_before is the last close on the old basis, net of any price-list
    event on the same bar (`prior_factor`: {index: factor}); base_observed
    the untraded close or the first trade.
    """

    n = len(bars)
    anchor = bars.first_from(day)
    if n < 2 or anchor < 1 or anchor >= n:
        return None              # no bar on one side of the date
    before, after = window
    order = sorted(range(anchor - before, anchor + after + 1),
                   key=lambda e: (abs(e - anchor), e < anchor))
    for e in order:
        if e < 1 or e >= n:
            continue
        price = bars.close[e - 1]
        if not price or price <= PLACEHOLDER_RIALS:
            continue
        if prior_factor and e in prior_factor:
            price *= prior_factor[e]
        traded = bars.traded(e)
        seen = bars.close[e] if not traded else (bars.open[e]
                                                 or bars.close[e])
        if not seen or seen <= PLACEHOLDER_RIALS:
            continue
        low, high = capital_bounds(ratio, price)
        q = seen / price
        if not traded:
            # The exchange's own re-price: inside the interval up to a tick.
            down = up = ADJUST_MATCH_TOLERANCE
        else:
            down = REOPEN_TOLERANCE
            up = (REOPEN_AFTER_HALT if not bars.traded(e - 1)
                  else REOPEN_TOLERANCE)
        if low * (1 - down) <= q <= high * (1 + up):
            return e, e - anchor, price, seen
    return None


# A company has a dividend a year and an increase or two. Rows this crowded
# are not corporate actions: اکالا's list holds 107 rows from 2019-01-30 to
# 2021-07-07, one per session, each "adjusting" to the next day's price.
CROWDED_ROWS = 4
CROWDED_DAYS = 30


def _crowded(rows, day):
    """Do CROWDED_ROWS or more distinct dates lie within CROWDED_DAYS of
    `day`? (A dividend beside an increase is two; فملی 2016-07-12/19.)"""

    near = {r["date"] for r in rows if abs(_days(day, r["date"]))
            <= CROWDED_DAYS}
    return len(near) >= CROWDED_ROWS


# A share change undone by the next within this many days.
CORRECTION_DAYS = 15
CORRECTION_TOLERANCE = 0.02


def _corrections(share_rows):
    """[(a, b)]: share changes reversed by the next one."""

    pairs, taken = [], set()
    rows = sorted(share_rows, key=lambda r: (r["date"], r["seq"]))
    for a, b in zip(rows, rows[1:]):
        if (a["date"], a["seq"]) in taken:
            continue
        if _days(a["date"], b["date"]) <= CORRECTION_DAYS \
                and abs(a["ratio"] * b["ratio"] - 1.0) <= CORRECTION_TOLERANCE:
            pairs.append((a, b))
            taken.add((a["date"], a["seq"]))
            taken.add((b["date"], b["seq"]))
    return pairs


def _correction_events(a, b, bars):
    """The two events of a reversed pair. Factor 1 each, unless the
    exchange re-priced the frozen close to the wrong count and back (وبیمه
    2021-01-16: x2.149 on an untraded bar, back three sessions later): then
    the bars in between are scaled back to the price around them -- the
    first event's factor is that jump, the second's its inverse, so nothing
    outside the pair moves."""

    def event(share, factor, ex_date, observed, convention):
        return {"date": share["date"], "seq": share["seq"],
                "source": "share_change", "kind": "share_correction",
                "ex_date": ex_date, "ex_observed": int(observed),
                "convention": convention, "adjusted": None,
                "not_adjusted": None, "factor": factor, "factor_low": None,
                "factor_high": None, "dps": None,
                "share_ratio": share["ratio"],
                "matched_share_change": share["date"]}

    first, second = bars.first_from(a["date"]), bars.first_from(b["date"])
    if 1 <= first < second < len(bars) and \
            bars.close[first - 1] > PLACEHOLDER_RIALS and \
            not any(bars.traded(i) for i in range(first, second)):
        jump = bars.close[first] / bars.close[first - 1]
        if abs(jump - 1.0) > 0.005:
            return [event(a, jump, bars.dates[first], True,
                          "reversed_pair_reprice"),
                    event(b, 1.0 / jump, bars.dates[second], True,
                          "reversed_pair_reprice")]
    return [event(a, 1.0, a["date"], False, "reversed_pair"),
            event(b, 1.0, b["date"], False, "reversed_pair")]


def classify(adjust_rows, share_rows, bars, window=SHARE_CHANGE_WINDOW):
    """The events of one instrument from its two lists and its raw bars.

    Pure: no I/O. `bars` is a Bars. Returns event dicts (EVENT_COLUMNS less
    ins_code/fetched_at), oldest first.
    """

    out = []
    adjust = []
    for row in adjust_rows:
        index, convention = locate_adjustment(bars, row["date"],
                                              row["not_adjusted"])
        ex_date, observed = _ex_date(bars, index, row["date"], convention)
        factor = row["adjusted"] / row["not_adjusted"]
        adjust.append(dict(row, index=index, convention=convention,
                           ex_date=ex_date, observed=observed,
                           factor=factor, match=None,
                           suspect=_crowded(adjust_rows, row["date"])))

    # A registration and its reversal a few days later (x0.78 then x1.282,
    # x0.24 then x4.167: the product is 1) change nothing.
    corrections = set()
    for a, b in _corrections(share_rows):
        out.extend(_correction_events(a, b, bars))
        corrections.update({(a["date"], a["seq"]), (b["date"], b["seq"])})

    # Increases the price list carries (2019 and before).
    unmatched = []
    for share in share_rows:
        if (share["date"], share["seq"]) in corrections:
            continue
        best = None
        for event in adjust:
            if event["match"] is not None or event["suspect"]:
                continue
            gap = abs(_days(share["date"], event["date"]))
            gap = min(gap, abs(_days(share["date"], event["ex_date"])))
            if gap > ADJUST_MATCH_DAYS:
                continue
            low, high = capital_bounds(share["ratio"], event["not_adjusted"])
            if low is None:
                continue
            if not (low * (1 - ADJUST_MATCH_TOLERANCE) <= event["factor"]
                    <= high * (1 + ADJUST_MATCH_TOLERANCE)):
                continue
            # A frozen close the exchange re-priced by more than this row
            # says the row is only part of that day's events -- a dividend
            # beside an increase the list does not carry -- not the increase.
            i = event["index"]
            if event["observed"] and not bars.traded(i) \
                    and bars.close[i - 1] > PLACEHOLDER_RIALS:
                seen = bars.close[i] / bars.close[i - 1]
                if abs(seen / event["factor"] - 1.0) > ADJUST_MATCH_TOLERANCE:
                    continue
            # The share change's own session re-priced separately: the row
            # is the AGM's dividend and the increase came after it (ومعلم
            # 2022-07-11 DPS 250, then x1.385 on 07-12; وبانک, داتام, کچاد).
            own = bars.first_from(share["date"])
            if 1 <= own < len(bars) and own != i and \
                    not bars.traded(own) and \
                    bars.close[own - 1] > PLACEHOLDER_RIALS and \
                    abs(bars.close[own] / bars.close[own - 1] - 1.0) > 0.005:
                continue
            if best is None or gap < best[0]:
                best = (gap, event)
        if best is None:
            unmatched.append(share)
        else:
            best[1]["match"] = share

    prior = {}
    for event in adjust:
        factor = event["factor"]
        share = event["match"]
        low = high = None
        if event["suspect"]:
            kind = "suspect"
        elif share is not None:
            kind = "capital"
            low, high = capital_bounds(share["ratio"], event["not_adjusted"])
        elif factor < 1.0:
            kind = "dividend"
        else:
            kind = "other"
        if event["observed"] and kind != "suspect":
            prior[event["index"]] = prior.get(event["index"], 1.0) * factor
        out.append({
            "date": event["date"], "seq": event["seq"],
            "source": "adjust_list", "kind": kind,
            "ex_date": event["ex_date"], "ex_observed": int(event["observed"]),
            "convention": event["convention"],
            "adjusted": event["adjusted"],
            "not_adjusted": event["not_adjusted"],
            "factor": factor, "factor_low": low, "factor_high": high,
            "dps": (round(event["not_adjusted"] - event["adjusted"], 4)
                    if kind == "dividend" else None),
            "share_ratio": share["ratio"] if share else None,
            "matched_share_change": share["date"] if share else None,
        })

    # Increases it does not carry: rebuilt from the share change and bars.
    for share in unmatched:
        ratio = share["ratio"]
        found = find_reprice(bars, share["date"], ratio, window, prior)
        event = {"date": share["date"], "seq": share["seq"],
                 "source": "share_change", "share_ratio": ratio,
                 "matched_share_change": share["date"], "dps": None}
        if found is not None:
            index, _lag, price, seen = found
            low, high = capital_bounds(ratio, price)
            if not bars.traded(index):
                base, convention = seen, "observed_reprice"
            else:
                base = min(max(seen, price * low), price * high)
                convention = "observed_open"
            event.update(kind="capital", ex_date=bars.dates[index],
                         ex_observed=1, convention=convention,
                         adjusted=base, not_adjusted=price,
                         factor=base / price, factor_low=low,
                         factor_high=high)
        else:
            anchor = bars.first_from(share["date"])
            if 1 <= anchor < len(bars):
                event.update(kind="capital_unpriced",
                             ex_date=share["date"], ex_observed=0,
                             convention="no_reprice", adjusted=None,
                             not_adjusted=None, factor=1.0,
                             factor_low=None, factor_high=None)
            else:
                low, _high = capital_bounds(ratio, None)
                event.update(kind="capital", ex_date=share["date"],
                             ex_observed=0, convention="assumed",
                             adjusted=None, not_adjusted=None, factor=low,
                             factor_low=low, factor_high=None)
        out.append(event)

    out.sort(key=lambda e: (e["date"], e["source"], e["seq"]))
    return out


def _load_bars(ins_code):
    return Bars(database.load_history_by_inscode(str(ins_code)))


def reclassify(ins_codes=None, verbose=True):
    """Rebuild corporate_actions from the stored lists (no network).

    Returns the number of instruments rebuilt.
    """

    ensure_tables()
    codes = (sorted(c for c, s in status().items() if s.get("fetched_at"))
             if ins_codes is None else [str(c) for c in ins_codes])
    for count, code in enumerate(codes, 1):
        adjust, share = load_raw(code)
        found = classify(adjust, share, _load_bars(code))
        conn = database._connect()
        try:
            _write_events(conn, code, found, _now())
            conn.execute("UPDATE corporate_actions_status SET events = ? "
                         "WHERE ins_code = ?", (len(found), code))
            conn.commit()
        finally:
            conn.close()
        if verbose and count % 100 == 0:
            print("  reclassified %d/%d" % (count, len(codes)), flush=True)
    return len(codes)


# =============================================================================
# FETCH
# =============================================================================

def universe():
    """Active equities: is_equity = 1 (funds, bonds, options, rights and
    retired listings excluded -- universe.classify by ISIN)."""

    return [{"ins_code": str(r["ins_code"]), "symbol": r["symbol_fa"]}
            for r in database.load_instruments(equities_only=True)]


def due(stale_days=STALE_DAYS, now=None, instruments=None):
    """The instruments a run should fetch, never-fetched first, then the
    oldest fetch; a fetch younger than `stale_days` is skipped."""

    now = now or datetime.now(timezone.utc)
    instruments = universe() if instruments is None else instruments
    known = status()
    queue = []
    for inst in instruments:
        row = known.get(inst["ins_code"]) or {}
        fetched = row.get("fetched_at")
        if fetched:
            try:
                age = now - datetime.fromisoformat(fetched)
            except ValueError:
                age = None
            if age is not None and age < timedelta(days=stale_days):
                continue
        queue.append((fetched is not None, fetched or "", inst["symbol"] or "",
                      inst))
    queue.sort(key=lambda t: t[:3])
    return [t[3] for t in queue]


def _get_rows(session, url, key):
    response = session.get(url, timeout=(settings.TIMEOUT_CONNECT,
                                         settings.TIMEOUT_READ))
    code = response.status_code
    if code in REFUSED_STATUS:
        raise Refused("HTTP %d" % code)
    if code != 200:
        raise FetchError("HTTP %d" % code)
    try:
        payload = response.json()
    except ValueError:
        raise FetchError("not JSON (%d bytes)" % len(response.content or b""))
    if not isinstance(payload, dict) or key not in payload:
        raise FetchError("no %s in the response" % key)
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        raise FetchError("%s is not a list" % key)
    return rows


class _Pacer:
    """At least `gap` seconds from the END of one request to the start of
    the next; after failures, a doubling pause (capped)."""

    def __init__(self, gap, sleep=time.sleep, clock=time.monotonic):
        self.gap = gap
        self.sleep = sleep
        self.clock = clock
        self.last = None

    def wait(self, failures=0):
        spacing = self.gap if not failures else min(
            BACKOFF_CAP_SECONDS, self.gap * (2 ** failures) * 2)
        if self.last is not None:
            left = spacing - (self.clock() - self.last)
            if left > 0:
                self.sleep(left)

    def done(self):
        self.last = self.clock()


def _paced(pacer, failures, session, url, key):
    pacer.wait(failures)
    try:
        return _get_rows(session, url, key)
    finally:
        pacer.done()


def fetch_one(session, ins_code, pacer, failures=0):
    """(adjust_rows, share_rows) for one instrument; raises FetchError."""

    adjust = parse_adjust(_paced(pacer, failures, session,
                                 ADJUST_URL % ins_code, ADJUST_KEY))
    shares = parse_shares(_paced(pacer, failures, session,
                                 SHARE_URL % ins_code, SHARE_KEY))
    return adjust, shares


def fetch(max_count=None, stale_days=STALE_DAYS, dry_run=False, session=None,
          gap=REQUEST_GAP_SECONDS, verbose=True, sleep=time.sleep,
          instruments=None):
    """Fetch both lists for every due equity, one at a time.

    Sequential, one session, >= `gap` s between requests. Stops the run at
    once on a 403/429, and after MAX_CONSECUTIVE_FAILURES failures in a row
    (with a doubling pause between them). Resumable: what was fetched within
    `stale_days` is skipped. Returns a summary dict.
    """

    queue = due(stale_days, instruments=instruments)
    if max_count is not None:
        queue = queue[:max_count]
    summary = {"due": len(queue), "attempted": 0, "fetched": 0, "failed": 0,
               "events": 0, "stopped": None, "errors": [],
               "started_at": _now(), "dry_run": bool(dry_run)}
    if dry_run:
        summary["would_fetch"] = [i["symbol"] for i in queue[:50]]
        summary["requests"] = 2 * len(queue)
        return summary
    if not queue:
        return summary

    if session is None:
        from providers import transport
        # Transport retries the 5xx codes only: a 403/429 retried at once
        # inside the throttle window renews it (codal-api-behaviour).
        session = transport.build_session(host=HOST,
                                          retry_status=(500, 502, 503, 504))
    pacer = _Pacer(gap, sleep=sleep)
    failures = 0
    started = time.monotonic()
    for inst in queue:
        code, symbol = inst["ins_code"], inst["symbol"]
        summary["attempted"] += 1
        try:
            adjust, shares = fetch_one(session, code, pacer, failures)
        except Refused as exc:
            record_failure(code, "refused: %s" % exc, symbol)
            summary["failed"] += 1
            summary["errors"].append("%s: refused %s" % (symbol, exc))
            summary["stopped"] = "refused (%s) at %s" % (exc, symbol)
            break
        except Exception as exc:                                # noqa: BLE001
            failures += 1
            why = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            record_failure(code, why, symbol)
            summary["failed"] += 1
            summary["errors"].append("%s: %s" % (symbol, why))
            if verbose:
                print("  ! %s %s" % (symbol, why), flush=True)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                summary["stopped"] = ("%d consecutive failures" % failures)
                break
            continue
        try:
            found = classify(adjust, shares, _load_bars(code))
            store(code, adjust, shares, found, symbol=symbol)
        except Exception as exc:                                # noqa: BLE001
            # The store, not the host (a lock held past its timeout): the
            # lists were had; the next run fetches them again.
            failures += 1
            why = "store: %s: %s" % (type(exc).__name__, str(exc)[:160])
            summary["failed"] += 1
            summary["errors"].append("%s: %s" % (symbol, why))
            if verbose:
                print("  ! %s %s" % (symbol, why), flush=True)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                summary["stopped"] = ("%d consecutive failures" % failures)
                break
            continue
        failures = 0
        summary["fetched"] += 1
        summary["events"] += len(found)
        if verbose and summary["fetched"] % 25 == 0:
            print("  %d/%d fetched (%s) %.0fs" % (
                summary["fetched"], len(queue), symbol,
                time.monotonic() - started), flush=True)
    summary["elapsed_s"] = round(time.monotonic() - started, 1)
    summary["finished_at"] = _now()
    summary["errors"] = summary["errors"][:50]
    return summary


# =============================================================================
# THE TOTAL-RETURN BASIS
# =============================================================================

def exchange_adjust(frame, ins_code=None, events=None, fill_untraded=False):
    """A copy of a bars frame on the exchange's total-return basis.

    `frame` has date/open/high/low/close[/volume] as database.load_history_
    by_inscode returns it. Every price before an event's ex_date is
    multiplied by the event's factor (dividends reinvested; increases at the
    exchange's base). `events` defaults to events(ins_code). Volume is left
    alone. The applied events are in result.attrs["exchange_events"].

    `fill_untraded` (opt-in, off by default): the lists are not complete --
    measured 2026-11-24, see UNEXPLAINED in the audit: شمواد's frozen close
    went 141,050 -> 3,050 on 2023-12-06 with no share change listed. With
    it, a move the events leave on a bar with NO trade (or onto a name's
    first traded bar from its pre-listing placeholder) is taken out as well,
    as back_adjust's rule 2 does: a frozen close moves only when the
    exchange re-prices it. Moves on traded bars are never touched. The
    filled moves are in result.attrs["filled"].
    """

    if frame is None:
        return None
    out = frame.copy()
    if len(out) == 0:
        out.attrs["exchange_events"] = []
        out.attrs["filled"] = []
        return out
    if events is None:
        if ins_code is None:
            raise ValueError("exchange_adjust needs ins_code or events")
        events = _stored_events(ins_code)

    import numpy as np
    import pandas as pd

    dates = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d").to_numpy()
    multiplier = np.ones(len(out))
    applied = []
    for event in events:
        factor = _number(event.get("factor"))
        when = event.get("ex_date")
        if not factor or factor <= 0 or abs(factor - 1.0) < 1e-12 \
                or not when or event.get("kind") == "suspect":
            continue
        mask = dates < str(when)[:10]
        if not mask.any():
            continue
        multiplier[mask] *= factor
        applied.append({"ex_date": when, "kind": event.get("kind"),
                        "factor": factor, "source": event.get("source")})
    filled = []
    if fill_untraded and "volume" in out.columns:
        multiplier, filled = _fill_untraded(out, dates, multiplier)
    for column in ("open", "high", "low", "close"):
        if column in out.columns:
            out[column] = out[column].astype(float).to_numpy() * multiplier
    out.attrs["exchange_events"] = applied
    out.attrs["filled"] = filled
    return out


# A residual move on an untraded bar smaller than this is rounding.
UNTRADED_NOISE = 0.005


def _fill_untraded(frame, dates, multiplier):
    """exchange_adjust's fill_untraded: (multiplier, filled moves)."""

    import numpy as np

    closes = frame["close"].astype(float).to_numpy()
    volumes = frame["volume"].astype(float).fillna(0).to_numpy()
    traded = np.nonzero(volumes > 0)[0]
    first_trade = int(traded[0]) if len(traded) else None
    adjusted = closes * multiplier
    extra = np.ones(len(closes))
    filled = []
    running = 1.0
    # Backwards, so one factor accumulates every later fill.
    for i in range(len(closes) - 1, 0, -1):
        before, now = adjusted[i - 1], adjusted[i]
        if closes[i - 1] > PLACEHOLDER_RIALS and closes[i] > \
                PLACEHOLDER_RIALS and before > 0:
            ratio = now / before
            frozen = not volumes[i] > 0 or (first_trade is not None
                                            and i <= first_trade)
            if frozen and abs(ratio - 1.0) > UNTRADED_NOISE:
                running *= ratio
                filled.append({"date": str(dates[i]),
                               "ratio": round(float(ratio), 6)})
        extra[i - 1] = running
    return multiplier * extra, filled[::-1]


def total_return_index(ins_code, start=None, end=None, base=100.0):
    """DataFrame [date, close, tr_close, tr_index] for one instrument.

    close is the raw close, tr_close the close on the exchange's total-
    return basis (exchange_adjust), tr_index tr_close rebased to `base` at
    the first bar of [start, end]. Empty when there are no bars.
    """

    import pandas as pd

    bars = database.load_history_by_inscode(str(ins_code), before=end)
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["date", "close", "tr_close", "tr_index"])
    adjusted = exchange_adjust(bars, ins_code=ins_code)
    frame = pd.DataFrame({"date": bars["date"], "close": bars["close"],
                          "tr_close": adjusted["close"]})
    if start is not None:
        frame = frame[frame["date"] >= pd.Timestamp(start)]
    frame = frame.reset_index(drop=True)
    if frame.empty:
        frame["tr_index"] = []
        return frame
    first = float(frame["tr_close"].iloc[0])
    frame["tr_index"] = (frame["tr_close"] / first * base) if first else None
    return frame


def dividend_history(ins_code, codal=True):
    """The exchange's dividends for one instrument, newest first.

    Each: {date, date_jalali (the exchange's dEven: the AGM session since
    2020), ex_date, ex_date_jalali, dps, price_before, price_after,
    yield_pct, convention, codal_dps, codal_fiscal_year_end,
    codal_tracing_no, codal_published_at}. The codal_* fields are the
    matching row of sirat_fundamentals.db `dividends` when there is one
    (match_codal_dividend), else None.
    """

    found = [e for e in events(ins_code) if e["kind"] == "dividend"]
    stored = _codal_dividends(ins_code) if codal else []
    out = []
    for e in reversed(found):
        before, after = e["not_adjusted"], e["adjusted"]
        row = {"date": e["date"], "date_jalali": e["date_jalali"],
               "ex_date": e["ex_date"], "ex_date_jalali": e["ex_date_jalali"],
               "dps": e["dps"], "price_before": before, "price_after": after,
               "yield_pct": (round(e["dps"] / before * 100, 3)
                             if before else None),
               "convention": e["convention"],
               "codal_dps": None, "codal_fiscal_year_end": None,
               "codal_tracing_no": None, "codal_published_at": None}
        match = match_codal_dividend(e["date"], stored)
        if match:
            row.update(codal_dps=match["dps"],
                       codal_fiscal_year_end=match["fiscal_year_end"],
                       codal_tracing_no=match["tracing_no"],
                       codal_published_at=match["published_at"])
        out.append(row)
    return out


# A Codal AGM decision is published around the meeting; the exchange dates a
# dividend at the meeting session (2020 on). Measured: see --measure
# ("codal_agreement.offset_days").
CODAL_MATCH_DAYS = (-20, 45)


def _codal_dividends(ins_code=None):
    """Stored Codal dividends with the publication date in Gregorian."""

    import fundamental_db

    try:
        conn = fundamental_db._connect()
        try:
            query = ("SELECT ins_code, fiscal_year_end, dps, tracing_no, "
                     "published_at FROM dividends")
            params = ()
            if ins_code is not None:
                query += " WHERE ins_code = ?"
                params = (str(ins_code),)
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
    except Exception:                                           # noqa: BLE001
        return []
    out = []
    for code, fiscal, dps, tracing, published in rows:
        day = jalali.parse_date(published)
        out.append({"ins_code": str(code), "fiscal_year_end": fiscal,
                    "dps": dps, "tracing_no": tracing,
                    "published_at": published,
                    "published_iso": day.isoformat() if day else None})
    return out


def match_codal_dividend(day, stored):
    """The Codal row published nearest `day` within CODAL_MATCH_DAYS."""

    best = None
    for row in stored:
        if not row.get("published_iso"):
            continue
        offset = _days(day, row["published_iso"])
        if CODAL_MATCH_DAYS[0] <= offset <= CODAL_MATCH_DAYS[1]:
            if best is None or abs(offset) < abs(best[0]):
                best = (offset, row)
    return best[1] if best else None


def dps_agrees(exchange, codal):
    """Within 1 rial or 1% of Codal's figure."""

    if exchange is None or codal is None:
        return False
    return abs(exchange - codal) <= max(1.0, 0.01 * abs(codal))


# =============================================================================
# MEASUREMENTS AND THE AUDIT
# =============================================================================

def _percentiles(values, points=(5, 25, 50, 75, 95)):
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for p in points:
        k = (len(ordered) - 1) * p / 100.0
        lo, hi = int(math.floor(k)), int(math.ceil(k))
        out["p%d" % p] = round(ordered[lo] + (ordered[hi] - ordered[lo])
                               * (k - lo), 4)
    return out


def measure(verbose=True):
    """The measurements the module docstring cites, over every fetched
    instrument: date convention, the price list's capital increases by
    year, the share-change lag, how the rebuilt increases were priced, and
    agreement with Codal's DPS."""

    ensure_tables()
    codes = sorted(c for c, s in status().items() if s.get("fetched_at"))
    convention = {}
    exact_next = [0, 0]
    previous_traded = [0, 0]
    ex_traded = [0, 0]
    capital_by_year = {}
    lags = []
    priced = {}
    widths = []
    codal_rows = {}
    for row in _codal_dividends():
        codal_rows.setdefault(row["ins_code"], []).append(row)
    agreement = {"pairs": 0, "agree": 0, "offsets": [], "disagree": []}

    for code in codes:
        adjust, shares = load_raw(code)
        bars = _load_bars(code)
        for row in adjust:
            year = row["date"][:4]
            index, conv = locate_adjustment(bars, row["date"],
                                            row["not_adjusted"])
            on_day = bars.at_or_before(row["date"])
            has_bar = on_day >= 0 and bars.dates[on_day] == row["date"]
            if not has_bar and conv == "assumed":
                conv = "no_bar"
            convention.setdefault(year, {}).setdefault(conv, 0)
            convention[year][conv] += 1
            if conv == "last_cum" and index < len(bars) and \
                    not bars.traded(index):
                exact_next[1] += 1
                if _same(bars.close[index], row["adjusted"]):
                    exact_next[0] += 1
            if 1 <= index < len(bars) and conv in ("last_cum", "first_ex"):
                ex_traded[1] += 1
                ex_traded[0] += int(bars.traded(index))
                t = index - 1
                while t >= 0 and not bars.traded(t):
                    t -= 1
                if t >= 0:
                    previous_traded[1] += 1
                    previous_traded[0] += int(_same(bars.close[t],
                                                    row["not_adjusted"]))
        classified = classify(adjust, shares, bars)
        for event in classified:
            year = event["date"][:4]
            slot = capital_by_year.setdefault(year, {
                "adjust_capital": 0, "adjust_dividend": 0, "adjust_other": 0,
                "adjust_suspect": 0, "share_rebuilt": 0,
                "share_unpriced": 0, "share_correction": 0})
            if event["source"] == "adjust_list":
                slot["adjust_" + event["kind"]] += 1
            else:
                slot[{"capital_unpriced": "share_unpriced",
                      "share_correction": "share_correction"}.get(
                          event["kind"], "share_rebuilt")] += 1
                priced[event["convention"]] = \
                    priced.get(event["convention"], 0) + 1
                if event.get("factor_low") and event.get("factor_high"):
                    widths.append(event["factor_high"] / event["factor_low"]
                                  - 1.0)
        # The lag of large increases, searched wide.
        for share in shares:
            if share["ratio"] < 1.25 or share["date"] < "2014-03-01":
                continue
            found = find_reprice(bars, share["date"], share["ratio"],
                                 window=(60, 20))
            lags.append(None if found is None else found[1])
        # Codal agreement.
        stored = codal_rows.get(code) or []
        if stored:
            for event in classified:
                if event["kind"] != "dividend":
                    continue
                match = match_codal_dividend(event["date"], stored)
                if not match or match["dps"] is None:
                    continue
                agreement["pairs"] += 1
                agreement["offsets"].append(_days(event["date"],
                                                  match["published_iso"]))
                if dps_agrees(event["dps"], match["dps"]):
                    agreement["agree"] += 1
                elif len(agreement["disagree"]) < 25:
                    agreement["disagree"].append(
                        {"ins_code": code, "date": event["date"],
                         "exchange_dps": event["dps"],
                         "codal_dps": match["dps"],
                         "codal_tracing_no": match["tracing_no"]})

    found_lags = [l for l in lags if l is not None]
    lag_counts = {}
    for lag in found_lags:
        lag_counts[str(lag)] = lag_counts.get(str(lag), 0) + 1
    offsets = agreement.pop("offsets")
    result = {
        "instruments": len(codes),
        "date_convention_by_year": dict(sorted(convention.items())),
        "date_convention_by_era": {
            era: {conv: sum(counts.get(conv, 0) for year, counts
                            in convention.items() if pick(year))
                  for conv in ("last_cum", "first_ex", "assumed", "no_bar")}
            for era, pick in (("before_2020", lambda y: y < "2020"),
                              ("2020_on", lambda y: y >= "2020"))},
        "last_cum_next_bar_untraded_equals_adjusted": {
            "equal": exact_next[0], "of": exact_next[1]},
        "not_adjusted_equals_last_traded_close": {
            "equal": previous_traded[0], "of": previous_traded[1],
            "note": "the close of the last session WITH volume before the "
                    "ex session; the last bar itself matches by "
                    "construction of last_cum/first_ex"},
        "ex_session_traded": {"traded": ex_traded[0], "of": ex_traded[1]},
        "events_by_year": dict(sorted(capital_by_year.items())),
        "share_change_lag": {
            "ratio_at_least": 1.25, "searched_sessions": [-60, 20],
            "share_changes": len(lags), "repriced": len(found_lags),
            "not_found": len(lags) - len(found_lags),
            "lag_0": sum(1 for l in found_lags if l == 0),
            "within_1": sum(1 for l in found_lags if abs(l) <= 1),
            "within_5": sum(1 for l in found_lags if abs(l) <= 5),
            "counts": dict(sorted(lag_counts.items(),
                                  key=lambda kv: int(kv[0]))),
        },
        "rebuilt_increases_by_method": priced,
        "rebuilt_interval_width": _percentiles(widths),
        "codal_agreement": dict(
            agreement,
            rate=(round(agreement["agree"] / agreement["pairs"], 4)
                  if agreement["pairs"] else None),
            rule="exchange DPS within 1 rial or 1% of Codal's",
            match_window_days=list(CODAL_MATCH_DAYS),
            offset_days=_percentiles(offsets),
            offset_note="Codal publication date minus the exchange's dEven",
        ),
    }
    if verbose:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def _cagr(first, last, days):
    if not first or not last or first <= 0 or last <= 0 or days <= 0:
        return None
    return ((last / first) ** (365.25 / days) - 1.0) * 100.0


def audit(path=AUDIT_PATH, verbose=True, start=AUDIT_START, end=AUDIT_END,
          measurements=None):
    """Descriptive: per-name annualised return on back_adjust's basis vs the
    exchange's, over [start, end]. NOT a test; issues no verdict about any
    experiment. Writes `path` and returns the summary.

    Three bases per name, all from the same raw bars (nothing past `end`):
      back_adjust      backtest.back_adjust(bars), its defaults, as
                       backtesting/pit.py calls it
      exchange         exchange_adjust(bars, events): the lists only
      exchange_filled  the same with fill_untraded=True
    A name is covered when its lists were fetched, it has >= 250 bars in
    the window, and its first TRADED bar is within 10 days of `start` and
    its last bar within 10 days of `end` (a pre-listing placeholder run is
    not a start). "Lists complete" names are those the fill finds nothing
    to do for: every move of a frozen close is an event the lists carry.
    """

    import numpy as np
    import pandas as pd
    from backtest import back_adjust

    ensure_tables()
    known = status()
    symbols = {i["ins_code"]: i["symbol"] for i in universe()}
    first_allowed = pd.Timestamp(start) + pd.Timedelta(days=10)
    last_required = pd.Timestamp(end) - pd.Timedelta(days=10)
    rows = []
    skipped = {"not_fetched": 0, "short_coverage": 0, "placeholder": 0}
    for code, symbol in sorted(symbols.items(), key=lambda kv: kv[1] or ""):
        state = known.get(code) or {}
        if not state.get("fetched_at"):
            skipped["not_fetched"] += 1
            continue
        bars = database.load_history_by_inscode(code, before=end)
        if bars is None or bars.empty:
            skipped["short_coverage"] += 1
            continue
        bars = bars[bars["date"] >= pd.Timestamp(start)]
        traded = bars.index[bars["volume"].fillna(0) > 0]
        if len(traded) == 0:
            skipped["short_coverage"] += 1
            continue
        bars = bars.loc[traded[0]:].reset_index(drop=True)
        if len(bars) < 250 or bars["date"].iloc[0] > first_allowed or \
                bars["date"].iloc[-1] < last_required:
            skipped["short_coverage"] += 1
            continue
        if float(bars["close"].min()) <= PLACEHOLDER_RIALS:
            skipped["placeholder"] += 1
            continue
        evs = [e for e in events(code) if e.get("ex_date")
               and e["ex_date"] <= end]
        bases = {"back_adjust": back_adjust(bars)[0],
                 "exchange": exchange_adjust(bars, events=evs),
                 "exchange_filled": exchange_adjust(bars, events=evs,
                                                    fill_untraded=True)}
        days = (bars["date"].iloc[-1] - bars["date"].iloc[0]).days
        growth = {k: float(v["close"].iloc[-1]) / float(v["close"].iloc[0])
                  for k, v in bases.items()}
        cagr = {k: _cagr(1.0, g, days) for k, g in growth.items()}
        if any(v is None for v in cagr.values()):
            skipped["placeholder"] += 1
            continue
        first_day = bars["date"].iloc[0].strftime("%Y-%m-%d")
        inside = [e for e in evs if first_day < e["ex_date"] <= end]
        dividend_growth = 1.0
        for e in inside:
            if e["kind"] == "dividend" and e.get("factor"):
                dividend_growth /= e["factor"]
        applied = bases["exchange"].attrs.get("exchange_events", [])
        ret_ex = bases["exchange_filled"]["close"].astype(float).pct_change()
        rows.append({
            "ins_code": code, "symbol": symbol, "first_bar": first_day,
            "back_adjust_pct_yr": round(cagr["back_adjust"], 3),
            "exchange_pct_yr": round(cagr["exchange"], 3),
            "exchange_filled_pct_yr": round(cagr["exchange_filled"], 3),
            "gap_pct_yr": round(cagr["exchange"] - cagr["back_adjust"], 3),
            "gap_filled_pct_yr": round(cagr["exchange_filled"]
                                       - cagr["back_adjust"], 3),
            "growth": {k: round(g, 6) for k, g in growth.items()},
            "dividend_contribution_pct_yr": round(
                (dividend_growth ** (365.25 / days) - 1.0) * 100.0, 3),
            "dividends": sum(1 for e in applied if e["kind"] == "dividend"),
            "increases": sum(1 for e in applied if e["kind"] == "capital"),
            "approximate_increases": sum(
                1 for e in inside if e["kind"] == "capital"
                and e.get("convention") in ("observed_open", "assumed")),
            "unpriced_share_changes": sum(
                1 for e in inside if e["kind"] == "capital_unpriced"),
            "unexplained_untraded_moves": len(
                bases["exchange_filled"].attrs.get("filled", [])),
            "traded_moves_over_12pct": int((ret_ex.abs() > 0.12).sum()),
        })

    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25

    def describe(subset, key, note):
        values = [r[key] for r in subset]
        if not values:
            return {"names": 0, "note": note}
        basis = {"gap_pct_yr": "exchange",
                 "gap_filled_pct_yr": "exchange_filled"}[key]
        ew = {k: (float(np.mean([r["growth"][k] for r in subset]))
                  ** (1.0 / years) - 1.0) * 100.0
              for k in ("back_adjust", basis)}
        return {
            "names": len(values),
            "median": round(float(np.median(values)), 3),
            "mean": round(float(np.mean(values)), 3),
            "percentiles": _percentiles(values),
            "names_over_plus_1": sum(1 for v in values if v > 1.0),
            "names_below_minus_1": sum(1 for v in values if v < -1.0),
            "equal_weight_buy_and_hold_pct_yr": {
                "back_adjust": round(ew["back_adjust"], 3),
                basis: round(ew[basis], 3),
                "gap": round(ew[basis] - ew["back_adjust"], 3)},
            "note": note,
        }

    complete = [r for r in rows if r["unexplained_untraded_moves"] == 0]
    exact = [r for r in complete if r["approximate_increases"] == 0
             and r["unpriced_share_changes"] == 0]
    ordered = sorted(rows, key=lambda r: r["gap_filled_pct_yr"])
    dividend_part = [r["dividend_contribution_pct_yr"] for r in rows]
    summary = {
        "label": "DESCRIPTIVE -- not a test, no verdict on any experiment",
        "note": ("Annualised return per name over the window on the "
                 "heuristic basis every registered experiment reads "
                 "(backtest.back_adjust with its defaults, as "
                 "backtesting/pit.py calls it) and on the exchange's own "
                 "lists (corporate_actions.exchange_adjust). gap = exchange "
                 "minus back_adjust, in percentage points a year; positive "
                 "means the heuristic understates the total return. It says "
                 "how far a multi-year comparison with TEDPIX (a price-and-"
                 "dividend index) moves with the basis; it says nothing "
                 "about the verdicts of E26 or E30-E33, which compared "
                 "strategies with controls on one basis."),
        "window": {"start": start, "end": end,
                   "reserved_forward_window_not_read": "2026-08-19.."},
        "generated_at": _now(),
        "names": len(rows),
        "skipped": skipped,
        "coverage_rule": ("fetched lists; >= 250 bars; first TRADED bar "
                          "within 10 days of start, last bar within 10 days "
                          "of end; no close at or below %d rials"
                          % PLACEHOLDER_RIALS),
        "headline_lists_complete": describe(
            complete, "gap_pct_yr",
            "names whose every frozen-close move is an event the lists "
            "carry (fill_untraded finds nothing): the exchange basis needs "
            "no heuristic here"),
        "lists_complete_exact_factors": describe(
            exact, "gap_pct_yr",
            "of those, names whose increases all have exact factors (no "
            "first-trade estimate, no unpriced share change)"),
        "all_names_filled": describe(
            rows, "gap_filled_pct_yr",
            "every covered name, exchange basis with fill_untraded=True "
            "(unexplained frozen-close moves taken out as back_adjust does)"),
        "all_names_lists_only": describe(
            rows, "gap_pct_yr",
            "every covered name, lists only: names with events the lists "
            "miss carry them as returns, so the tails here are list gaps"),
        "lists_incomplete_names": len(rows) - len(complete),
        "dividend_contribution_pct_yr": {
            "median": (round(float(np.median(dividend_part)), 3)
                       if dividend_part else None),
            "mean": (round(float(np.mean(dividend_part)), 3)
                     if dividend_part else None),
            "note": ("what the exchange's dividends alone add per year "
                     "(product of 1/factor, annualised): the most a "
                     "price-only basis could miss; back_adjust already "
                     "takes out those re-priced on untraded bars")},
        "largest_positive_gaps_filled": ordered[::-1][:10],
        "largest_negative_gaps_filled": ordered[:10],
        "method_measurements": measurements,
        "per_name": rows,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=1)
    if verbose:
        brief = {k: summary[k] for k in (
            "label", "window", "names", "skipped", "lists_incomplete_names",
            "headline_lists_complete", "lists_complete_exact_factors",
            "all_names_filled", "all_names_lists_only",
            "dividend_contribution_pct_yr")}
        for block in brief.values():
            if isinstance(block, dict):
                block.pop("percentiles", None)
        print(json.dumps(brief, ensure_ascii=False, indent=1))
        print("written: %s" % path)
    return summary


def report():
    ensure_tables()
    conn = database._connect()
    try:
        n, fetched, failed = conn.execute(
            "SELECT COUNT(*), SUM(fetched_at IS NOT NULL), "
            "SUM(error IS NOT NULL) FROM corporate_actions_status"
        ).fetchone()
        print("  status: %d instruments, %d fetched, %d with an error"
              % (n or 0, fetched or 0, failed or 0))
        for source, kind, count, first, last in conn.execute(
                "SELECT source, kind, COUNT(*), MIN(date), MAX(date) FROM "
                "corporate_actions GROUP BY source, kind ORDER BY 1, 2"):
            print("  %-12s %-17s %6d  %s .. %s"
                  % (source, kind, count, first, last))
        for row in conn.execute(
                "SELECT symbol, error FROM corporate_actions_status WHERE "
                "error IS NOT NULL LIMIT 10"):
            print("  ! %s: %s" % row)
    finally:
        conn.close()
    print("  universe: %d active equities" % len(universe()))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--max", type=int)
    parser.add_argument("--stale-days", type=float, default=STALE_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gap", type=float, default=REQUEST_GAP_SECONDS)
    parser.add_argument("--reclassify", action="store_true")
    parser.add_argument("--measure", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    if args.fetch or args.dry_run:
        summary = fetch(max_count=args.max, stale_days=args.stale_days,
                        dry_run=args.dry_run,
                        gap=max(0.5, args.gap))
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.reclassify:
        print("reclassified %d" % reclassify())
    measured = None
    if args.measure:
        measured = measure()
    if args.audit:
        audit(measurements=measured or measure(verbose=False))
    if args.report or not any((args.fetch, args.dry_run, args.reclassify,
                               args.measure, args.audit)):
        report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
