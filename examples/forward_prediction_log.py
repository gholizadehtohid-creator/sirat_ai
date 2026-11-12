"""The forward prediction log: turn a year of waiting into a year of evidence.

WHY THIS EXISTS
----------------
Every historical holdout is spent (E19, E21) and the only out-of-sample window
this programme will ever have again is R-FORWARD-2027 (2026-08-19..2027-08-19),
which accrues one session at a time. A window evaluated retrospectively still
leans on trust that nothing was chosen after the fact. A window whose
predictions were WRITTEN DOWN BEFORE EACH OUTCOME EXISTED does not: the
timestamp does the work that trust used to.

So this script, run once per session after the day's data lands, computes the
scores that are already frozen on the platform's record -- nothing new, no
parameter this file owns -- and appends them to a hash-chained log. When the
reservation matures, a successor experiment that claims R-FORWARD-2027 can
evaluate a year of predictions each of which is provably older than its
outcome.

WHAT IS DECLARED, HERE AND NOW, SO IT CANNOT DRIFT LATER
---------------------------------------------------------
PRIMARY score  : `composite_e21` -- E21's archived four-component composite,
                 signs +1/+1/+1/+1 exactly as preregistered there (prereg
                 2ce1d49dd90ebd46). Its wrong-signed inst_net_ratio stays
                 wrong-signed: the archived claim is what gets tested, not an
                 improved cousin of it.
SECONDARY      : `indiv_power_solo` (E21's strongest single carrier) and
                 `inst_net_supply_e24` (inst_net_ratio at -1, the corrected
                 sign E24 put on record, prereg 87b995fae3e862c7). Diagnostics.
                 Any future evaluation gates on the PRIMARY alone; promoting a
                 secondary after seeing forward results would be selection,
                 and this paragraph is the record that says so.

ADMISSIBILITY IS A TIMESTAMP RULE, NOT A PROMISE
-------------------------------------------------
Every entry carries `computed_at` (UTC). At evaluation time an entry is
admissible only if `computed_at` precedes the outcome session. A prediction
logged late -- after its outcome session has traded -- is not deleted or
hidden; it simply fails the rule and drops out. The log stays mechanical and
the evaluator stays honest.

THIS LOG IS NOT AN EXPERIMENT
------------------------------
It spends no window (writing predictions is not evaluating them), issues no
verdict, and touches no experiment record. R-FORWARD-2027 remains unclaimed
until a registered successor names it.

USAGE
    python examples/forward_prediction_log.py            append today's entry
    python examples/forward_prediction_log.py --status   verify the chain
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
import experiment_14_queue_artifact as e14                      # noqa: E402
from examples import e21_panel                                  # noqa: E402

DB = os.path.join(ROOT, "sirat_market.db")

LOG_PATH = os.path.join(backtest_config.OUTPUT_DIR,
                        "forward_predictions.jsonl")

GENESIS = "R-FORWARD-2027"

# Sessions of trailing history the detector needs: vol_ratio compares volume
# to its own trailing median, and E14's annotate owns that arithmetic.
LOOKBACK_SESSIONS = 70

SPEC_VERSION = "fpl-v1"

COMPOSITE = ("inst_net_ratio", "indiv_power", "closed_at_high", "vol_ratio")


def say(message):
    print(message, flush=True)


# ==============================
# THE CHAIN
# ==============================

def _canonical(entry):
    return json.dumps(entry, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _entry_hash(prev_hash, entry):
    payload = (prev_hash + _canonical(entry)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_log():
    if not os.path.exists(LOG_PATH):
        return []

    rows = []

    with open(LOG_PATH, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    return rows


def verify_chain(rows):
    prev = GENESIS

    for number, row in enumerate(rows, start=1):
        entry = {k: v for k, v in row.items() if k != "entry_hash"}
        expected = _entry_hash(prev, entry)
        if row.get("entry_hash") != expected:
            return {"intact": False, "broken_at": number,
                    "session": row.get("session")}
        prev = row["entry_hash"]

    return {"intact": True, "entries": len(rows),
            "head": prev if rows else None}


def append_entry(entry):
    rows = read_log()

    prev = rows[-1]["entry_hash"] if rows else GENESIS

    entry = dict(entry)
    entry["entry_hash"] = _entry_hash(prev, {k: v for k, v in entry.items()
                                             if k != "entry_hash"})

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(_canonical(entry) + "\n")

    return entry["entry_hash"]


# ==============================
# THE SCORES -- frozen elsewhere, computed here
# ==============================

# A session is loggable only when BOTH tables carry a real cross-section.
# Measured 2026-08-23: flow lands in one bulk call (636 names same evening)
# while bars arrive per-instrument and can run days behind -- the raw
# MAX(date) intersection picked a session with 636 flow rows and ONE bar.
MIN_WIDTH = 100

# Two write-time rules (the review panel, 2026-11-11). Ten of the first
# twenty entries were written after their outcome session had opened -- the
# 19:00 run logs the newest COMPLETE session, usually yesterday's, and the
# panel's fetch path logged during market hours -- and three were frozen on a
# partial flow table (154 of ~750 names on 11-10). An entry is now written
# only before the next trading day's pre-open, and only when the flow table
# covers COVERAGE_MIN of the equities that traded that session. A session
# that can no longer be logged in time gets a `missed` record instead, so
# the log says what it lacks rather than holding a late prediction.
COVERAGE_MIN = 0.9
TEHRAN = timezone(timedelta(hours=3, minutes=30))
PRE_OPEN = (8, 45)                       # Tehran; the pre-open session starts
TRADING_WEEKDAYS = (5, 6, 0, 1, 2)       # Saturday .. Wednesday
AUDIT_PATH = os.path.join(backtest_config.OUTPUT_DIR,
                          "forward_predictions_audit.json")


def too_late(conn, session, now=None):
    """Why writing `session` now would follow its outcome, or None.

    Late when a later session is already stored, or when any trading
    weekday after the session has reached its pre-open by `now` (a holiday
    on a weekday makes this conservative, never permissive).
    """

    later = conn.execute("SELECT MIN(date) FROM price_history WHERE date > ?",
                         (session,)).fetchone()[0]
    if later:
        return "the next session (%s) is already stored" % later
    now = (now or datetime.now(timezone.utc)).astimezone(TEHRAN)
    day = date.fromisoformat(str(session)[:10]) + timedelta(days=1)
    while day <= now.date():
        if day.weekday() in TRADING_WEEKDAYS and \
                (day < now.date() or (now.hour, now.minute) >= PRE_OPEN):
            return "trading day %s had opened (pre-open %02d:%02d Tehran)" % (
                day, PRE_OPEN[0], PRE_OPEN[1])
        day += timedelta(days=1)
    return None


# R-FORWARD-2027's first session; a calendar date from here on with no
# entry at all is a gap the audit names.
FORWARD_START = "2026-08-19"


def calendar(conn, after=None, before=None):
    """Trading sessions by the index's own calendar: price_history is no
    calendar on a broken day (11-11 held 2 bars, both gold funds)."""

    import config
    sql = "SELECT DISTINCT date FROM index_history WHERE ins_code = ?"
    args = [str(config.INDICES["TEDPIX"]["insCode"])]
    if after:
        sql += " AND date > ?"
        args.append(str(after)[:10])
    if before:
        sql += " AND date < ?"
        args.append(str(before)[:10])
    try:
        return [str(d)[:10] for (d,) in conn.execute(sql + " ORDER BY date", args)]
    except sqlite3.Error:
        return []


def skipped_sessions(conn, rows, session):
    """Sessions before `session` that the log passed over without a record.

    From the newest session the log has a record for (logged or missed);
    the older gaps, from before this rule, are the audit's. Each is already
    too late: `session`, a later one, is stored.
    """

    seen = {str(r["session"])[:10] for r in rows if r.get("spec") == SPEC_VERSION}
    if not seen:
        return []
    anchor = max(seen)
    return [d for d in calendar(conn, after=anchor, before=session) if d not in seen]


def coverage(conn, session):
    """(equities that traded the session, of them with a flow row)."""

    traded = conn.execute(
        "SELECT COUNT(*) FROM price_history p JOIN instruments i "
        "ON i.ins_code = p.ins_code WHERE p.date = ? AND p.volume > 0 "
        "AND i.is_equity = 1", (session,)).fetchone()[0]
    flowed = conn.execute(
        "SELECT COUNT(*) FROM client_type_history c JOIN instruments i "
        "ON i.ins_code = c.ins_code JOIN price_history p "
        "ON p.ins_code = c.ins_code AND p.date = c.date "
        "WHERE c.date = ? AND p.volume > 0 AND i.is_equity = 1",
        (session,)).fetchone()[0]
    return traded, flowed


def latest_common_session(conn):
    price = dict(conn.execute(
        "SELECT date, COUNT(*) FROM price_history "
        "WHERE date >= date('now', '-30 day') GROUP BY date "
        "HAVING COUNT(*) >= ?", (MIN_WIDTH,)).fetchall())

    flow = dict(conn.execute(
        "SELECT date, COUNT(*) FROM client_type_history "
        "WHERE date >= date('now', '-30 day') GROUP BY date "
        "HAVING COUNT(*) >= ?", (MIN_WIDTH,)).fetchall())

    common = set(price) & set(flow)

    return max(common) if common else None


def build_cross_section(conn, session):
    """One session's four features, through the SAME code E21's panel used."""

    window_start = conn.execute(
        "SELECT MIN(date) FROM (SELECT DISTINCT date FROM price_history "
        "WHERE date <= ? ORDER BY date DESC LIMIT ?)",
        (session, LOOKBACK_SESSIONS)).fetchone()[0]

    bars = pd.read_sql_query(
        "SELECT ins_code, date, high, low, close, volume, value "
        "FROM price_history WHERE date BETWEEN ? AND ? "
        "ORDER BY ins_code, date",
        conn, params=(window_start, session))

    if bars.empty:
        return None

    bars = e14.annotate(bars)

    today = bars[bars["date"] == session][
        ["ins_code", "closed_at_high", "vol_ratio", "locked_up",
         "unbuyable"]].copy()

    flow = pd.read_sql_query(
        "SELECT ins_code, date, "
        "       individual_buy_value, individual_sell_value, "
        "       institutional_buy_value, institutional_sell_value, "
        "       individual_buy_count, individual_sell_count "
        "FROM client_type_history WHERE date = ?",
        conn, params=(session,))

    features = e21_panel.flow_features(flow)

    frame = today.merge(features[["ins_code", "inst_net_ratio",
                                  "indiv_power"]],
                        on="ins_code", how="left")

    frame["closed_at_high"] = frame["closed_at_high"].astype(float)

    return frame[frame[list(COMPOSITE)].notna().all(axis=1)].reset_index(
        drop=True)


def score(frame):
    """The three declared scores over one session's cross-section."""

    ranks = {name: frame[name].rank(pct=True) for name in COMPOSITE}

    composite = sum(ranks[name] for name in COMPOSITE) / float(len(COMPOSITE))

    return {
        "composite_e21": composite,
        "indiv_power_solo": ranks["indiv_power"],
        "inst_net_supply_e24": 1.0 - ranks["inst_net_ratio"],
    }


# ==============================
# MAIN
# ==============================

def status():
    rows = read_log()
    chain = verify_chain(rows)

    if not rows:
        say("empty log (genesis %s)" % GENESIS)
        return 0

    say("entries : %d" % len(rows))
    say("chain   : %s" % ("intact, head %s" % chain["head"][:16]
                          if chain["intact"]
                          else "BROKEN at entry %s (session %s)"
                          % (chain["broken_at"], chain["session"])))
    say("sessions: %s .. %s" % (rows[0]["session"], rows[-1]["session"]))

    return 0 if chain["intact"] else 1


class _WriterLock(object):
    """One appender at a time, or the chain can fork.

    Two processes reading the same head and appending concurrently would
    both chain onto the same prev_hash, and the second line breaks
    verification forever. The lock is an OS byte-range lock, so it dies
    with its process -- a crash cannot leave the log unwritable.
    """

    def __init__(self, path):
        self.path = path + ".lock"
        self.handle = None

    def __enter__(self):
        import msvcrt

        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        self.handle = open(self.path, "a")

        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            self.handle = None
            return None

        return self

    def __exit__(self, *_exc):
        import msvcrt

        if self.handle is not None:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()


def log_once(verbose=True):
    """Append the latest loggable session, exactly once. Never raises.

    Returns a short outcome string. Safe to call after every acquisition
    run: when the session is already logged, or no session is wide enough
    in BOTH tables yet, it says so and does nothing -- the expensive
    cross-section build only happens on the one call per day that writes.
    """

    def out(message):
        if verbose:
            say(message)
        return message

    try:
        with _WriterLock(LOG_PATH) as lock:

            if lock is None:
                return out("another writer holds the forward log; "
                           "skipping this attempt")

            rows = read_log()

            chain = verify_chain(rows)

            if not chain["intact"]:
                return out("REFUSING to append to a broken chain (entry %s)."
                           " The log must be explained, not extended."
                           % chain["broken_at"])

            conn = sqlite3.connect(
                "file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)

            try:
                session = latest_common_session(conn)

                if not session:
                    return out("no session holds >= %d names in BOTH prices "
                               "and flow; nothing to log" % MIN_WIDTH)

                if any(r["session"] == session and r["spec"] == SPEC_VERSION
                       for r in rows):
                    return out("session %s already logged; the log is "
                               "append-once per session" % session)

                # A session the log passed over -- deferred, then superseded
                # by a later one before any run could log it -- was left with
                # no trace, and the audit said "missed: 0" over 40 such
                # sessions (the full test, 2026-11-12).
                for skipped in skipped_sessions(conn, rows, session):
                    append_entry({
                        "spec": SPEC_VERSION, "session": skipped,
                        "missed": "superseded: a later session (%s) became "
                                  "loggable first -- data incomplete, or no "
                                  "run in time" % session,
                        "computed_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds")})
                    if verbose:
                        say("session %s MISSED (superseded by %s); recorded"
                            % (skipped, session))

                late = too_late(conn, session)
                if late:
                    head = append_entry({
                        "spec": SPEC_VERSION, "session": str(session),
                        "missed": late,
                        "computed_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds")})
                    return out("session %s MISSED (%s); recorded as missed, "
                               "head %s" % (session, late, head[:16]))

                traded, flowed = coverage(conn, session)
                if traded and flowed < COVERAGE_MIN * traded:
                    return out("session %s: flow covers %d of %d traded "
                               "equities (< %.0f%%); waiting for the rest"
                               % (session, flowed, traded, COVERAGE_MIN * 100))

                frame = build_cross_section(conn, session)
            finally:
                conn.close()

            if frame is None or frame.empty:
                return out("session %s has no complete cross-section; "
                           "nothing to log" % session)

            scores = score(frame)

            names = frame["ins_code"].astype(str).tolist()

            entry = {
                "spec": SPEC_VERSION,
                "session": str(session),
                "predicts": "next trading session",
                "computed_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "n_names": len(names),
                "traded_equities": traded,
                "flow_rows": flowed,
                "locked_up_today": int(frame["locked_up"].astype(bool).sum()),
                "scores": {
                    key: {name: round(float(value), 6)
                          for name, value in zip(names, series)}
                    for key, series in scores.items()
                },
            }

            head = append_entry(entry)

            return out("logged session %s: %d names, head %s"
                       % (session, len(names), head[:16]))

    except Exception as exc:                                    # noqa: BLE001
        # The log is an evening convenience wired into the app's fetch path;
        # it must never take the fetch down with it.
        return out("forward log failed: %s: %s"
                   % (type(exc).__name__, str(exc)[:160]))


def audit(write=True):
    """Each entry's admissibility and width, recomputed from the stores.

    The chain cannot be edited, so what the evaluator must know about the
    entries written before the write-time rules -- which were late, which
    were frozen on a partial flow table -- is kept beside it, recomputable
    by anyone from the same database.
    """

    rows = read_log()
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    report = []
    try:
        # Calendar sessions with no record at all -- not ok, not inadmissible,
        # not missed: absent. The evaluator's denominator needs them. The
        # newest calendar session may still be logged, so it is not one.
        logged = {str(r["session"])[:10] for r in rows}
        sessions = calendar(conn, after=_day_before(FORWARD_START))
        gaps = [d for d in sessions[:-1] if d not in logged]
        for r in rows:
            item = {"session": r["session"], "computed_at": r.get("computed_at")}
            if r.get("missed"):
                item.update(status="missed", reason=r["missed"])
                report.append(item)
                continue
            nxt = conn.execute("SELECT MIN(date) FROM price_history "
                               "WHERE date > ?", (r["session"],)).fetchone()[0]
            if nxt:
                opens = datetime.fromisoformat(
                    "%sT%02d:%02d:00" % (nxt[:10], PRE_OPEN[0], PRE_OPEN[1])
                ).replace(tzinfo=TEHRAN)
                stamp = datetime.fromisoformat(r["computed_at"])
                item["outcome_session"] = nxt[:10]
                item["admissible"] = stamp < opens
            else:
                item["outcome_session"] = None
                item["admissible"] = None          # outcome not yet traded
            traded, _flowed = coverage(conn, r["session"])
            item["n_names"] = r.get("n_names")
            item["traded_equities_now"] = traded
            # The complete cross-section also needs a 70-session window, so
            # it runs a little under the traded count even on a full day.
            item["width_ok"] = bool(traded) and \
                (r.get("n_names") or 0) >= AUDIT_WIDTH * traded
            item["status"] = ("pending" if item["admissible"] is None
                              else "ok" if item["admissible"] and item["width_ok"]
                              else "inadmissible")
            report.append(item)
    finally:
        conn.close()
    summary = {
        "entries": len(report),
        "ok": sum(1 for i in report if i["status"] == "ok"),
        "inadmissible": sum(1 for i in report if i["status"] == "inadmissible"),
        "missed": sum(1 for i in report if i["status"] == "missed"),
        "pending": sum(1 for i in report if i["status"] == "pending"),
        "gaps": len(gaps),
        "gap_sessions": gaps,
        "rule": "admissible: computed_at before the outcome session's pre-open "
                "(%02d:%02d Tehran); width_ok: n_names at least %.0f%% of the "
                "equities that traded" % (PRE_OPEN[0], PRE_OPEN[1],
                                          AUDIT_WIDTH * 100),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries_detail": report}
    if write:
        with open(AUDIT_PATH, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=1)
    return summary


AUDIT_WIDTH = 0.75


def _day_before(day):
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--audit", action="store_true",
                        help="recompute each entry's admissibility and width")

    args = parser.parse_args(argv)

    if args.status:
        return status()

    if args.audit:
        summary = audit()
        say("audit: %d entries -- %d ok, %d inadmissible, %d missed, %d pending"
            % (summary["entries"], summary["ok"], summary["inadmissible"],
               summary["missed"], summary["pending"]))
        return 0

    outcome = log_once(verbose=True)

    return 0 if ("logged session" in outcome
                 or "already logged" in outcome) else 1


if __name__ == "__main__":
    sys.exit(main(None))
