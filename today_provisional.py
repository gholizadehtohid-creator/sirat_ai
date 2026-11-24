"""Today's session, provisionally, until the exchange publishes it officially.

TSETMC publishes a session's daily series -- the index history, the bars,
the per-symbol حقیقی/حقوقی -- hours after the 12:30 close, in the evening
(18:00-20:00, sometimes later). Until it does, every card showed the session
before, and a panel opened after a missed evening was "a day or two behind"
until the catch-up landed half an hour later (the owner, 2026-11-24).

Two of the day's figures do not have to wait for that:

  the indices   GetMarketOverview -- one request -- carries both indices'
                final values and changes as soon as the session closes, dated
                by marketActivityDEven (probed 2026-11-24 at 18:18: 20261124,
                شاخص کل 8,591,498.89, +201,684.82, while index_history still
                ended on 11-23).
  money flow    the session task (monitor.py) records every name's
                حقیقی/حقوقی VOLUMES into client_type_live through the session,
                last at 14:20. Volume x price estimates the rials. With no
                capture of the session, one GetClientTypeAll request gives the
                same volumes.

What this module promises:

  DISPLAY ONLY. Nothing here is written to index_history, price_history or
      client_type_history: the official series stays the exchange's own. The
      figures live in data/today_provisional.json, are shown only while the
      official series lacks their date, and no analysis, test, forward log
      or scan reads them.
  LABELLED. Every view shows them as «موقت تا انتشار رسمی», and the flow as
      an ESTIMATE: volume x price is not the exchange's rial figure. The price
      is the post-close snapshot's closing price when there is one for the
      session, otherwise the previous official close (daily limits keep that
      within a few percent per name).
  BOUNDED. Refreshed at most twice a session day (after the close, then
      after the session task's last capture), only on a trading weekday,
      and never once the official flow carries the day: two requests a day.
"""

import json
import os
import sqlite3
import threading
from datetime import date, datetime, time as dtime, timedelta, timezone

import config

ROOT = os.path.dirname(os.path.abspath(__file__))

# The suite points this at a scratch directory (tests/__init__.py), so a
# test never writes into the live tree.
RUNTIME_DIR = os.environ.get("SIRAT_RUNTIME_DIR") or os.path.join(ROOT, "data")
PATH = os.path.join(RUNTIME_DIR, "today_provisional.json")

DB = config.DATABASE_NAME

# Iran keeps standard time all year (market_session.TEHRAN).
TEHRAN = timezone(timedelta(hours=3, minutes=30))
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}          # Sat .. Wed

# The session closes at 12:30; the overview's closing figures settle within
# minutes. The first refresh of a session day waits until this.
FIRST_REFRESH = dtime(12, 40)

# The session task's last capture is at 14:20, and bootstrap refetches the
# market snapshot after DATA_READY (14:00), so a refresh after this has final
# volumes and final closing prices. The second and last refresh of the day.
SECOND_REFRESH = dtime(14, 30)

# A capture of client_type_live taken at or after the close carries the
# day's final volumes; one taken earlier is the session so far.
CLOSE = dtime(12, 30)

# How long a reading of the official series' last dates is reused. current()
# runs on the panel's status poll; these are two indexed MAX() queries.
OFFICIAL_TTL = 60.0

_lock = threading.Lock()
_cache = {"mtime": None, "data": None, "official": None, "official_at": 0.0}


# ==============================
# CLOCK AND STORE
# ==============================

def tehran_now(now=None):
    """Aware Tehran time. `now` may be aware, or naive local (as bootstrap's
    clock is: this laptop's wall clock is Tehran's)."""

    if now is None:
        return datetime.now(TEHRAN)
    if now.tzinfo is None:
        return now.replace(tzinfo=TEHRAN)
    return now.astimezone(TEHRAN)


def _tehran(stamp):
    """An ISO timestamp from the store, as aware Tehran time, or None."""

    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TEHRAN)


def _connect(db=None):
    path = str(db or DB).replace("\\", "/")
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)


def load(path=None):
    """The stored provisional reading, or None. Re-read only when it changes."""

    path = path or PATH
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    with _lock:
        if _cache["mtime"] == mtime and path == PATH:
            return _cache["data"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if path == PATH:
        with _lock:
            _cache["mtime"], _cache["data"] = mtime, data
    return data


def _save(payload, path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    os.replace(temp, path)


# ==============================
# THE OFFICIAL SERIES
# ==============================

def official_dates(db=None, fresh=False):
    """Last date of the official index series and of the official flow.

    {"indices": "2026-11-23", "flow": "2026-11-23"}: the index date is the
    OLDER of the two indices (both must carry a day for it to be shown
    officially), the flow date the newest session in client_type_history.
    """

    import time as _time

    if not fresh and db is None:
        with _lock:
            if (_cache["official"] is not None
                    and _time.monotonic() - _cache["official_at"] < OFFICIAL_TTL):
                return dict(_cache["official"])

    codes = [spec["insCode"] for spec in config.INDICES.values()]
    found = {"indices": None, "flow": None}
    try:
        conn = _connect(db)
        try:
            lasts = []
            for code in codes:
                row = conn.execute("SELECT MAX(date) FROM index_history "
                                   "WHERE ins_code = ?", (code,)).fetchone()
                lasts.append(str(row[0])[:10] if row and row[0] else None)
            found["indices"] = (None if None in lasts or not lasts
                                else min(lasts))
            row = conn.execute(
                "SELECT MAX(date) FROM client_type_history").fetchone()
            found["flow"] = str(row[0])[:10] if row and row[0] else None
        finally:
            conn.close()
    except sqlite3.Error:
        return found

    if db is None:
        with _lock:
            _cache["official"] = dict(found)
            _cache["official_at"] = _time.monotonic()
    return found


def forget_official():
    """Drop the cached official dates: a fetch has just changed them."""

    with _lock:
        _cache["official"] = None


# ==============================
# READING THE EXCHANGE
# ==============================

def _provider(provider=None):
    if provider is not None and hasattr(provider, "_get"):
        return provider
    from providers.tsetmc_cdn import TsetmcCdnProvider

    return TsetmcCdnProvider()


def read_overview(provider=None):
    """The market overview, parsed: one request. Raises if unreadable."""

    provider = _provider(provider)
    overview = provider._get(
        provider.base_url + "/api/MarketData/GetMarketOverview/1",
        timeout=(10.0, 30.0)).json()["marketOverview"]
    return parse_overview(overview)


def parse_overview(overview):
    """The day's index figures from a GetMarketOverview payload."""

    raw = int(overview.get("marketActivityDEven") or 0)
    if raw < 19000101:
        raise ValueError("the overview carries no session date")
    session = date(raw // 10000, raw // 100 % 100, raw % 100)

    def index(value_key, change_key):
        value = overview.get(value_key)
        change = overview.get(change_key)
        if not value:
            return None
        value, change = float(value), float(change or 0.0)
        base = value - change
        return {"value": round(value, 2), "change": round(change, 2),
                "change_pct": round(change / base * 100.0, 2) if base else None}

    return {
        "session": session.isoformat(),
        "market_state": overview.get("marketState"),
        "market_state_title": overview.get("marketStateTitle"),
        "indices": {
            "TEDPIX": index("indexLastValue", "indexChange"),
            "TEDPIX_EW": index("indexEqualWeightedLastValue",
                               "indexEqualWeightedChange"),
        },
        "trades": overview.get("marketActivityZTotTran"),
        "traded_value": overview.get("marketActivityQTotCap"),
    }


# ==============================
# THE FLOW ESTIMATE
# ==============================

def _day_bounds_utc(day):
    """The UTC ISO strings bounding Tehran's `day`, for the stored stamps."""

    start = datetime.combine(day, dtime(0, 0), TEHRAN).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S+00:00"
    return start.strftime(fmt), end.strftime(fmt)


def live_volumes(day, codes, db=None):
    """The session's last client_type_live capture for `codes`.

    Returns (rows by code, captured_at ISO) or ({}, None).
    """

    start, end = _day_bounds_utc(day)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT MAX(captured_at) FROM client_type_live "
            "WHERE captured_at >= ? AND captured_at < ?", (start, end)).fetchone()
        captured = row[0] if row else None
        if not captured:
            return {}, None
        wanted = set(codes)
        rows = {}
        for (code, ib, isell, nb, nsell) in conn.execute(
                "SELECT ins_code, individual_buy_volume, individual_sell_volume, "
                "institutional_buy_volume, institutional_sell_volume "
                "FROM client_type_live WHERE captured_at = ?", (captured,)):
            if str(code) in wanted:
                rows[str(code)] = (ib, isell, nb, nsell)
    except sqlite3.Error:
        return {}, None
    finally:
        conn.close()
    return rows, captured


def bulk_volumes(codes, provider=None):
    """The same volumes from one GetClientTypeAll request, or {}."""

    try:
        result = _provider(provider).get_client_type_all()
    except Exception:                                           # noqa: BLE001
        return {}
    if not getattr(result, "ok", False) or result.data is None:
        return {}
    wanted = set(codes)
    rows = {}
    for record in result.data.to_dict("records"):
        code = str(record.get("ins_code") or "")
        if code in wanted:
            rows[code] = (record.get("individual_buy_volume"),
                          record.get("individual_sell_volume"),
                          record.get("institutional_buy_volume"),
                          record.get("institutional_sell_volume"))
    return rows


def session_prices(day, codes, db=None):
    """A price per code for valuing the session's volumes, and its source.

    The market snapshot's closing price (the day's volume-weighted price)
    when the snapshot was taken on `day` after the close; otherwise the last
    official close before `day`.
    """

    wanted = list(dict.fromkeys(str(c) for c in codes))
    prices = {}
    source = None
    conn = _connect(db)
    try:
        try:
            snap = conn.execute("SELECT MAX(captured_at) FROM market_snapshot"
                                ).fetchone()
            taken = _tehran(snap[0]) if snap else None
        except sqlite3.Error:
            taken = None
        if taken is not None and taken.date() == day and taken.time() >= CLOSE:
            for code, close in conn.execute(
                    "SELECT ins_code, close FROM market_snapshot"):
                if close:
                    prices[str(code)] = float(close)
            source = "snapshot"
        else:
            since = (day - timedelta(days=30)).isoformat()
            for code, close in conn.execute(
                    "SELECT p.ins_code, p.close FROM price_history p JOIN ("
                    " SELECT ins_code, MAX(date) AS d FROM price_history"
                    " WHERE date >= ? AND date < ? GROUP BY ins_code) m"
                    " ON p.ins_code = m.ins_code AND p.date = m.d",
                    (since, day.isoformat())):
                if close:
                    prices[str(code)] = float(close)
            source = "previous_close"
    except sqlite3.Error:
        return {}, None
    finally:
        conn.close()
    keep = set(wanted)
    return {c: p for c, p in prices.items() if c in keep}, source


def estimate_flow(volumes, prices):
    """Net individual rials and the session's breadth, from volumes x price.

    The shape of one moneyflow.market_daily session, so the panel can draw it
    beside them: net_individual, turnover, symbols, inflow_count, net_pct,
    breadth_pct. Names with no price are left out and counted.
    """

    net = turnover = 0.0
    symbols = inflow = unpriced = 0
    for code, (ib, isell, nb, nsell) in volumes.items():
        ib, isell, nb = float(ib or 0.0), float(isell or 0.0), float(nb or 0.0)
        if ib + isell + nb <= 0:
            continue                    # did not trade
        price = prices.get(code)
        if not price:
            unpriced += 1
            continue
        symbols += 1
        net += (ib - isell) * price
        turnover += (ib + nb) * price
        if ib > isell:
            inflow += 1
    if not symbols:
        return None
    return {
        "net_individual": round(net, 0),
        "turnover": round(turnover, 0),
        "symbols": symbols,
        "inflow_count": inflow,
        "unpriced": unpriced,
        "net_pct": round(net / turnover * 100.0, 2) if turnover else None,
        "breadth_pct": round(inflow / symbols * 100.0, 1),
    }


def _equity_codes():
    import database

    return [str(r["ins_code"])
            for r in database.load_instruments(equities_only=True)]


def flow_for(day, codes=None, provider=None, db=None, now=None, allow_bulk=True):
    """The session's estimated flow, with where its inputs came from."""

    codes = codes if codes is not None else _equity_codes()
    volumes, captured = live_volumes(day, codes, db=db)
    taken = _tehran(captured)
    final = taken is not None and taken.time() >= CLOSE
    source = "live_capture"

    # No capture of the session, or only one from before the close: the
    # bulk feed still holds the session's totals -- only on the session's
    # own day, before the next session resets them.
    if not final and allow_bulk and tehran_now(now).date() == day:
        bulk = bulk_volumes(codes, provider=provider)
        if bulk:
            volumes, taken, final = bulk, tehran_now(now), True
            source = "client_type_all"

    if not volumes:
        return {"error": "no record of the session's حقیقی/حقوقی volumes"}

    prices, price_source = session_prices(day, codes, db=db)
    flow = estimate_flow(volumes, prices)
    if flow is None:
        return {"error": "no traded name could be priced"}
    flow.update({
        "volumes_from": source,
        "volumes_at": taken.isoformat(timespec="seconds") if taken else None,
        "complete": bool(final),
        "price_from": price_source,
    })
    return flow


# ==============================
# REFRESH AND READ
# ==============================

def refresh(provider=None, codes=None, db=None, now=None, path=None):
    """Read the overview, estimate the flow, store both. Never raises."""

    moment = tehran_now(now)
    try:
        overview = read_overview(provider)
    except Exception as exc:                                    # noqa: BLE001
        return {"error": "overview unreadable: %s: %s"
                         % (type(exc).__name__, str(exc)[:160])}

    session = date.fromisoformat(overview["session"])
    try:
        flow = flow_for(session, codes=codes, provider=provider, db=db,
                        now=moment)
    except Exception as exc:                                    # noqa: BLE001
        flow = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}

    payload = dict(overview)
    payload.update({
        "fetched_at": moment.isoformat(timespec="seconds"),
        "flow": flow,
        "note": "provisional: display only, never stored as the official "
                "series; the flow is an estimate (volume x price)",
    })
    try:
        _save(payload, path)
    except OSError as exc:
        payload["error"] = "not stored: %s" % exc
    return payload


def due(now=None, stored=None, official=None):
    """Whether a refresh is owed now, and why not if it is not.

    Two refreshes a trading day at most: one after FIRST_REFRESH, one after
    SECOND_REFRESH, each only if none was made since; none once the official
    flow -- the last series the exchange publishes -- carries today.
    """

    moment = tehran_now(now)
    today = moment.date()
    if today.weekday() not in TRADING_WEEKDAYS:
        return False, "not a trading weekday"
    if moment.time() < FIRST_REFRESH:
        return False, "before the close settles"

    official = official if official is not None else official_dates()
    if (official.get("flow") or "") >= today.isoformat():
        return False, "the official series carries today"

    stored = stored if stored is not None else load()
    last = _tehran((stored or {}).get("fetched_at"))
    if last is None or last.date() != today or last.time() < FIRST_REFRESH:
        return True, "no reading since the close"
    if moment.time() >= SECOND_REFRESH and last.time() < SECOND_REFRESH:
        return True, "the final capture is in"
    return False, "read at %s" % last.strftime("%H:%M")


def refresh_if_due(provider=None, now=None):
    """refresh() when due(); returns the reading or None. Never raises."""

    try:
        owed, _why = due(now=now)
    except Exception:                                           # noqa: BLE001
        return None
    return refresh(provider=provider, now=now) if owed else None


def current(stored=None, official=None):
    """What the panel may show: the parts of the reading the official series
    lacks, or None.

        {"session": "2026-11-24", "fetched_at": ..., "market_state": "N",
         "indices": {"TEDPIX": {...}, "TEDPIX_EW": {...}} or None,
         "flow": {...} or None,
         "official": {"indices": "2026-11-23", "flow": "2026-11-23"}}
    """

    stored = stored if stored is not None else load()
    if not stored or not stored.get("session"):
        return None
    official = official if official is not None else official_dates()
    session = stored["session"]

    indices = stored.get("indices")
    if (official.get("indices") or "") >= session or not indices:
        indices = None
    flow = stored.get("flow")
    if ((official.get("flow") or "") >= session or not flow
            or flow.get("error")):
        flow = None

    if indices is None and flow is None:
        return None

    return {
        "session": session,
        "fetched_at": stored.get("fetched_at"),
        "market_state": stored.get("market_state"),
        "market_state_title": stored.get("market_state_title"),
        "indices": indices,
        "flow": flow,
        "official": official,
    }


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--refresh" in sys.argv:
        print(json.dumps(refresh(), ensure_ascii=False, indent=1))
    else:
        print("due:", due())
        print(json.dumps(current(), ensure_ascii=False, indent=1))
