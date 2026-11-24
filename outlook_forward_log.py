"""The outlook's forward estimates, written down before their outcome exists.

WHY THIS EXISTS
---------------
fundamental_outlook puts a profit estimate for the current fiscal year on
every ranked company, and since 2026-11-23 a range around it measured on its
own history (outlook_calibration). Measured on history means measured on
years the estimator's author had already seen. The one test that cannot have
been tuned is the one whose outcome did not exist when the estimate was
made -- so this module writes the estimates down, once a day, in a
hash-chained file, and forward_scorecard.py scores them against the first
12-month statement each company later publishes.

The rules -- what is logged, how an entry resolves, the metrics, the naive
benchmark, the minimum N and the retirement rule -- were fixed BEFORE the
first entry, in backtest_output/OUTLOOK_FORWARD_PROTOCOL.json. Every entry
carries that file's sha256, and the writer refuses to append once the file
has changed: a changed protocol is a new log, not an edit of this one.

WHAT IT IS NOT
--------------
Not the E21 forward log (examples/forward_prediction_log.py), and it claims
none of R-FORWARD-2027: that reservation is the E21 log's. Nothing here reads
or writes forward_predictions.jsonl.

NO NETWORK
----------
fundamental_outlook.screen() reads the local stores only (verified
2026-11-24 with every socket connect blocked: 0 attempts in 18 s). This
module adds one read-only query of the fundamental store.

    python outlook_forward_log.py            append today's entry (once a day)
    python outlook_forward_log.py --status   verify the chain, show the head
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(ROOT, "backtest_output")
LOG_PATH = os.path.join(OUTPUT_DIR, "outlook_forward_log.jsonl")
PROTOCOL_PATH = os.path.join(OUTPUT_DIR, "OUTLOOK_FORWARD_PROTOCOL.json")

GENESIS = "OUTLOOK-FORWARD-v1"
SPEC = "ofl-v1"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
INNER_NOMINAL = 0.50
OUTER_NOMINAL = 0.80
RATIO_KEYS = ("q10", "q25", "q50", "q75", "q90")


def say(message):
    print(message, flush=True)


# ==============================
# THE PROTOCOL AND THE CHAIN
# ==============================

def protocol_sha256(path=None):
    """sha256 of the protocol file's bytes, or None when it is missing.

    CRLF is read as LF first: with core.autocrlf the same committed file is
    LF in one checkout and CRLF in another, and a line-ending rewrite is not
    a change of the rules. Nothing else is normalised."""

    try:
        with open(path or PROTOCOL_PATH, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def load_protocol(path=None):
    with open(path or PROTOCOL_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def canonical(entry):
    return json.dumps(entry, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def entry_hash(prev_hash, entry):
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256((prev_hash + canonical(body)).encode("utf-8")).hexdigest()


def read_log(path=None):
    """Every entry, oldest first; [] when the file does not exist."""

    path = path or LOG_PATH
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verify_chain(rows, genesis=GENESIS):
    prev = genesis
    for number, row in enumerate(rows, start=1):
        if row.get("entry_hash") != entry_hash(prev, row):
            return {"intact": False, "entries": len(rows), "broken_at": number,
                    "date": row.get("date"), "head": None}
        prev = row["entry_hash"]
    return {"intact": True, "entries": len(rows), "broken_at": None,
            "head": prev if rows else None}


def append_entry(entry, path=None):
    """Chain `entry` onto the log and write it. Returns its hash."""

    path = path or LOG_PATH
    rows = read_log(path)
    prev = rows[-1]["entry_hash"] if rows else GENESIS
    entry = dict(entry)
    entry["entry_hash"] = entry_hash(prev, entry)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(canonical(entry) + "\n")
    return entry["entry_hash"]


class _WriterLock(object):
    """One appender at a time, or the chain forks. An OS byte-range lock,
    so it dies with its process (the E21 log's pattern). Where msvcrt is
    absent the lock is a no-op: the panel runs on Windows."""

    def __init__(self, path):
        self.path = path + ".lock"
        self.handle = None

    def __enter__(self):
        try:
            import msvcrt
        except ImportError:                                     # not Windows
            return self
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.handle = open(self.path, "a")
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            self.handle = None
            return None
        return self

    def __exit__(self, *_exc):
        if self.handle is not None:
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()


# ==============================
# TIME
# ==============================

def tehran(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(TEHRAN)


def jalali_stamp(moment):
    """A datetime as Tehran-local Jalali 'YYYY/MM/DD HH:MM:SS' -- the format
    of codal_filings.published_at, so admissibility is a string compare."""

    import jalali

    local = tehran(moment)
    jy, jm, jd = jalali.today_jalali(local.date())
    return "%04d/%02d/%02d %02d:%02d:%02d" % (jy, jm, jd, local.hour,
                                             local.minute, local.second)


# ==============================
# ONE NAME
# ==============================

def target_fiscal_year(result):
    """'YYYY/MM' of the fiscal year the estimate is for, or None."""

    import fundamental_outlook as fo

    latest = result.get("latest") or {}
    method = result.get("method")
    if method == "monthly_sales":
        fy = (result.get("monthly") or {}).get("fiscal_year")
        fy = tuple(fy) if fy else None
    elif method == "next_year_trend":
        fy = fo.fiscal_year_end(latest.get("period_end"), 12)
        fy = (fy[0] + 1, fy[1]) if fy else None
    elif method == "year_completed":
        fy = fo.fiscal_year_end(latest.get("period_end"), latest.get("months"))
    else:
        fy = None
    if not fy:
        return None
    return "%04d/%02d" % (int(fy[0]), int(fy[1]))


def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _int(value):
    value = _f(value)
    return None if value is None else int(round(value))


def _round(value, digits):
    value = _f(value)
    return None if value is None else round(value, digits)


def _owners_profit(items):
    """fundamental_outlook._profit: owners' share where filed, else total."""

    owners = _f((items or {}).get("net_profit_owners"))
    return owners if owners is not None else _f((items or {}).get("net_profit"))


def last_annuals(ins_codes, db_path=None):
    """{ins_code: [(period_end, consolidated, owners' profit)]} of every
    stored 12-month statement, newest first, read-only."""

    if not ins_codes:
        return {}
    if db_path is None:
        import fundamental_db
        db_path = fundamental_db.path()
    if not os.path.exists(db_path):
        return {}
    uri = "file:%s?mode=ro" % os.path.abspath(db_path).replace("\\", "/")
    conn = sqlite3.connect(uri, uri=True)
    out = {}
    try:
        codes = sorted({str(c) for c in ins_codes})
        for start in range(0, len(codes), 400):
            chunk = codes[start:start + 400]
            rows = conn.execute(
                "SELECT ins_code, period_end, consolidated, items FROM metrics "
                "WHERE period_months = 12 AND ins_code IN (%s)"
                % ",".join("?" * len(chunk)), chunk).fetchall()
            for code, end, cons, items in rows:
                try:
                    items = json.loads(items) if items else {}
                except ValueError:
                    continue
                out.setdefault(str(code), []).append(
                    (str(end), bool(cons), _owners_profit(items)))
    finally:
        conn.close()
    for rows in out.values():
        rows.sort(key=lambda r: r[0], reverse=True)
    return out


def naive_base(annuals, fy, consolidated):
    """The last reported annual profit before the targeted fiscal year:
    the newest 12-month statement whose year ends before `fy`, on the
    estimate's basis when one exists, else the newest on either."""

    before = [r for r in annuals or () if r[0][:7] < fy]
    same = [r for r in before if r[1] == consolidated]
    pick, substituted = (same[0], False) if same else \
        ((before[0], True) if before else (None, False))
    if pick is None:
        return {"last_annual_profit": None, "period_end": None,
                "consolidated": None, "basis_substituted": False}
    return {"last_annual_profit": _int(pick[2]), "period_end": pick[0],
            "consolidated": pick[1], "basis_substituted": substituted}


def nominal(rng, which, default):
    """A band's nominal coverage. q25..q75 of actual/estimate covers 0.50
    and q10..q90 0.80 by construction; a range that states its own NOMINAL
    coverage (a key with "nominal" in it) is taken at its word. An empirical
    coverage field is never read as a nominal one."""

    for name in ("nominal_" + which, "nominal_coverage_" + which):
        value = _f((rng or {}).get(name))
        if value is not None and 0 < value < 1:
            return value
    # outlook_calibration's leave-one-year-out tally states its nominal
    # inside it (2026-11-24): {"covered", "total", "nominal", "rate", ...}.
    tally = (rng or {}).get("coverage" if which == "inner" else "coverage80")
    value = _f(tally.get("nominal")) if isinstance(tally, dict) else None
    if value is not None and 0 < value < 1:
        return value
    return default


def record(result, annuals):
    """One logged name, or None when the estimate has no range or year."""

    forward = result.get("forward") or {}
    rng = forward.get("range")
    central = _f(forward.get("profit"))
    if not rng or central is None or central <= 0:
        return None
    if any(_f(rng.get(k)) is None for k in RATIO_KEYS):
        return None
    fy = target_fiscal_year(result)
    if not fy:
        return None
    latest = result.get("latest") or {}
    consolidated = bool(latest.get("consolidated"))
    valuation = result.get("valuation") or {}
    shares = _f(valuation.get("shares"))
    ratios = {k: round(_f(rng[k]), 4) for k in RATIO_KEYS}
    profit = {"central": _int(central),
              "low": _int(central * ratios["q25"]),
              "high": _int(central * ratios["q75"]),
              "low80": _int(central * ratios["q10"]),
              "high80": _int(central * ratios["q90"])}

    def eps(value):
        return round(value / shares, 2) if shares and value is not None else None

    naive = naive_base(annuals, fy, consolidated)
    naive["ttm_profit"] = _int((result.get("ttm") or {}).get("profit"))
    return {
        "ins_code": str(result.get("ins_code")),
        "symbol": result.get("symbol"),
        "fy": fy,
        "method": result.get("method"),
        "months_known": rng.get("months_known"),
        "series_basis": result.get("basis"),
        "consolidated": consolidated,
        "latest_period": {"end": latest.get("period_end"),
                          "months": latest.get("months")},
        "profit": profit,
        "eps": {"central": eps(central), "low": eps(central * ratios["q25"]),
                "high": eps(central * ratios["q75"])},
        "ratios": ratios,
        "band": {"key": rng.get("key"), "n": rng.get("n"),
                 "basis": rng.get("basis"), "source": rng.get("source"),
                 "years": rng.get("years"),
                 "nominal_inner": nominal(rng, "inner", INNER_NOMINAL),
                 "nominal_outer": nominal(rng, "outer", OUTER_NOMINAL)},
        "naive": naive,
        "price": {"price": _f(valuation.get("price")), "shares": _int(shares),
                  "pe_forward": _round(valuation.get("pe_forward"), 2)},
    }


# ==============================
# ONE ENTRY
# ==============================

def build_entry(screen_result, annuals, now=None, protocol_hash=None,
                market_session=None):
    """The day's entry from one screen() result. Pure: no I/O."""

    moment = now or datetime.now(timezone.utc)
    ranked = screen_result.get("ranked") or []
    names, without_range, without_year = [], 0, 0
    for result in ranked:
        rec = record(result, annuals.get(str(result.get("ins_code"))))
        if rec is None:
            if not (result.get("forward") or {}).get("range"):
                without_range += 1
            else:
                without_year += 1
            continue
        names.append(rec)
    names.sort(key=lambda r: r["ins_code"])
    return {
        "spec": SPEC,
        "date": tehran(moment).date().isoformat(),
        "logged_at": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "logged_at_jalali": jalali_stamp(moment),
        "protocol_sha256": protocol_hash,
        "market_session": market_session,
        "calibration_built_at": (screen_result.get("calibration") or {}).get("built_at"),
        "calibration_source": (screen_result.get("calibration") or {}).get("source"),
        "population": {
            "ranked": screen_result.get("count", len(ranked)),
            "logged": len(names),
            "ranked_without_range": without_range,
            "ranked_without_fiscal_year": without_year,
            "financials": screen_result.get("financials_count"),
            "excluded": screen_result.get("excluded"),
        },
        "names": names,
    }


def _market_session():
    try:
        import database
        newest = database.newest_equity_bar()
        return None if newest is None else str(newest.date())
    except Exception:                                           # noqa: BLE001
        return None


def _default_screen():
    import fundamental_outlook
    return fundamental_outlook.screen(limit=None)


def _suite_running():
    """True while the test suite runs (tests/__init__.py is loaded).

    Several suites drive bootstrap.ensure() and patch its other post-run
    hooks by name; this one is newer than they are. Without this check
    such a test would run the 20-second screen against the live stores and
    could append to the LIVE log -- a record that must hold only real days.
    """

    return getattr(sys.modules.get("tests"), "_SCRATCH", None) is not None


def log_once(verbose=False, now=None, log_path=None, protocol_path=None,
             screen_fn=None, annuals_fn=None, session_fn=None):
    """Append today's entry, at most once per Tehran date. Never raises.

    Returns a short outcome string. Cheap when today is already logged: the
    screen (~20 s over 800 companies) runs only on the call that writes.
    """

    log_path = log_path or LOG_PATH

    def out(message):
        if verbose:
            say(message)
        return message

    if os.path.abspath(log_path) == os.path.abspath(LOG_PATH) and _suite_running():
        return out("outlook forward log: the test suite never writes the live log")

    try:
        with _WriterLock(log_path) as lock:
            if lock is None:
                return out("outlook forward log: another writer holds the "
                           "log; skipping this attempt")
            digest = protocol_sha256(protocol_path)
            if digest is None:
                return out("outlook forward log: protocol file missing; "
                           "refusing to log without the rules")
            rows = read_log(log_path)
            chain = verify_chain(rows)
            if not chain["intact"]:
                return out("outlook forward log: REFUSING to append to a broken "
                           "chain (entry %s)" % chain["broken_at"])
            if rows and rows[0].get("protocol_sha256") != digest:
                return out("outlook forward log: REFUSING -- the protocol "
                           "changed since entry 1 (%s.. -> %s..); a changed "
                           "protocol is a new log"
                           % (str(rows[0].get("protocol_sha256"))[:12], digest[:12]))
            moment = now or datetime.now(timezone.utc)
            today = tehran(moment).date().isoformat()
            if any(r.get("date") == today for r in rows):
                return out("outlook forward log: %s already logged" % today)

            screen = (screen_fn or _default_screen)()
            codes = [str(r.get("ins_code")) for r in screen.get("ranked") or ()]
            annuals = (annuals_fn or last_annuals)(codes)
            session = (session_fn or _market_session)()
            entry = build_entry(screen, annuals, now=moment,
                                protocol_hash=digest, market_session=session)
            if not entry["names"]:
                return out("outlook forward log: no ranked name carries a "
                           "range today (%d ranked); nothing logged"
                           % entry["population"]["ranked"])
            head = append_entry(entry, log_path)
            return out("outlook forward log: logged %s, %d names, head %s"
                       % (today, len(entry["names"]), head[:16]))
    except Exception as exc:                                    # noqa: BLE001
        # Bookkeeping after a data run: it must never take the run down.
        return out("outlook forward log failed: %s: %s"
                   % (type(exc).__name__, str(exc)[:160]))


def status(log_path=None, protocol_path=None):
    rows = read_log(log_path)
    chain = verify_chain(rows)
    digest = protocol_sha256(protocol_path)
    return {"entries": len(rows), "chain": chain,
            "first_date": rows[0].get("date") if rows else None,
            "last_date": rows[-1].get("date") if rows else None,
            "names_last_entry": len(rows[-1].get("names") or ()) if rows else 0,
            "protocol_sha256": digest,
            "protocol_matches": (not rows) or rows[0].get("protocol_sha256") == digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.status:
        s = status()
        say("entries : %d (%s .. %s), %d names in the newest"
            % (s["entries"], s["first_date"], s["last_date"], s["names_last_entry"]))
        say("chain   : %s" % ("intact, head %s" % (s["chain"]["head"] or "-")[:16]
                              if s["chain"]["intact"]
                              else "BROKEN at entry %s" % s["chain"]["broken_at"]))
        say("protocol: %s (%s)" % ((s["protocol_sha256"] or "MISSING")[:16],
                                   "matches entry 1" if s["protocol_matches"]
                                   else "DIFFERS from entry 1"))
        return 0 if s["chain"]["intact"] and s["protocol_matches"] else 1
    outcome = log_once(verbose=True)
    return 0 if (": logged " in outcome or "already logged" in outcome) else 1


if __name__ == "__main__":
    sys.exit(main(None))
