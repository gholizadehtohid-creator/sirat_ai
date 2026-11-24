"""The scorecard of the two forward logs: what each may and may not say yet.

WHY THIS EXISTS
---------------
The programme keeps two logs of predictions written before their outcomes:

  1. backtest_output/forward_predictions.jsonl -- E21's lock-prediction
     scores, the evidence base of the RESERVED window R-FORWARD-2027
     (2026-08-19..2027-08-19). Scoring those predictions against what
     happened before the reservation matures would spend the programme's
     only clean out-of-sample window on a look. So for this log the
     scorecard computes ONLY what needs no outcome: sessions expected and
     logged, timely and late, missed (counted as missing, never imputed),
     thin entries and the chain. score_e21_outcomes() raises before
     2027-08-19, and after it says that a registered successor experiment
     must do the scoring -- this module never will.

  2. backtest_output/outlook_forward_log.jsonl -- the fundamental outlook's
     forward profit estimates and their range (outlook_forward_log.py). That
     log CAN be scored now: each company-year resolves on the first 12-month
     statement the company publishes, and the rules for that -- metrics,
     naive benchmark, minimum N, retirement -- were fixed in advance in
     backtest_output/OUTLOOK_FORWARD_PROTOCOL.json, whose parameters this
     module reads rather than restates.

status() is what the panel reads on page load: cached, never raises.

    python forward_scorecard.py          both scorecards
    python forward_scorecard.py --json   the same, as JSON
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(ROOT, "backtest_output")
MARKET_DB = os.path.join(ROOT, "sirat_market.db")
E21_LOG = os.path.join(OUTPUT_DIR, "forward_predictions.jsonl")

TEHRAN = timezone(timedelta(hours=3, minutes=30))

# R-FORWARD-2027, as reserved in sirat_research.db windows_reserved and
# named in examples/forward_prediction_log.py.
RESERVATION_ID = "R-FORWARD-2027"
RESERVATION_START = "2026-08-19"
RESERVATION_END = "2027-08-19"
E21_GENESIS = "R-FORWARD-2027"
E21_SPEC = "fpl-v1"
E21_PRE_OPEN = (8, 45)                 # Tehran; as forward_prediction_log
E21_WIDTH_OK = 0.75                    # as forward_prediction_log.AUDIT_WIDTH
TRADING_WEEKDAYS = (5, 6, 0, 1, 2)     # Saturday .. Wednesday


class ReservationNotMatured(RuntimeError):
    """Outcome scoring of the E21 forward log was asked for before
    R-FORWARD-2027 matured: it would spend the window on a look."""


def _today_tehran(today=None):
    if today is not None:
        return today if isinstance(today, date) and not isinstance(today, datetime) \
            else today.astimezone(TEHRAN).date()
    return datetime.now(timezone.utc).astimezone(TEHRAN).date()


def score_e21_outcomes(today=None, **_ignored):
    """Refuses, always. Before the reservation matures it raises
    ReservationNotMatured; after, scoring belongs to a registered successor
    experiment that claims R-FORWARD-2027 through the Experiment Manager,
    so it raises NotImplementedError naming that requirement."""

    day = _today_tehran(today)
    if day < date.fromisoformat(RESERVATION_END):
        raise ReservationNotMatured(
            "%s runs %s..%s; outcome scoring of the E21 forward log before "
            "%s would spend the programme's only clean out-of-sample window"
            % (RESERVATION_ID, RESERVATION_START, RESERVATION_END,
               RESERVATION_END))
    raise NotImplementedError(
        "%s has matured: its evaluation is a registered successor experiment "
        "that claims the reservation (sirat.experiments), not this scorecard"
        % RESERVATION_ID)


# ==============================
# E21: INTEGRITY ONLY
# ==============================

def _canonical(entry):
    return json.dumps(entry, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verify_e21_chain(rows):
    """forward_prediction_log.verify_chain, restated so the panel need not
    import the E21 panel code (pandas, E14); tests hold the two equal."""

    prev = E21_GENESIS
    for number, row in enumerate(rows, start=1):
        body = {k: v for k, v in row.items() if k != "entry_hash"}
        expected = hashlib.sha256((prev + _canonical(body)).encode("utf-8")).hexdigest()
        if row.get("entry_hash") != expected:
            return {"intact": False, "entries": len(rows), "broken_at": number,
                    "session": row.get("session"), "head": None}
        prev = row["entry_hash"]
    return {"intact": True, "entries": len(rows), "broken_at": None,
            "head": prev if rows else None}


def _ro(path):
    uri = "file:%s?mode=ro" % os.path.abspath(path).replace("\\", "/")
    return sqlite3.connect(uri, uri=True)


def _calendar(conn, start):
    import config
    code = str(config.INDICES["TEDPIX"]["insCode"])
    try:
        return [str(d)[:10] for (d,) in conn.execute(
            "SELECT DISTINCT date FROM index_history WHERE ins_code = ? AND "
            "date >= ? ORDER BY date", (code, start))]
    except sqlite3.Error:
        return []


def _next_trading_weekday(day):
    nxt = day + timedelta(days=1)
    while nxt.weekday() not in TRADING_WEEKDAYS:
        nxt += timedelta(days=1)
    return nxt


def _pre_open(day):
    return datetime(day.year, day.month, day.day, E21_PRE_OPEN[0],
                    E21_PRE_OPEN[1], tzinfo=TEHRAN)


def _traded_counts(conn, sessions):
    out = {}
    sessions = sorted(set(sessions))
    for start in range(0, len(sessions), 400):
        chunk = sessions[start:start + 400]
        try:
            rows = conn.execute(
                "SELECT p.date, COUNT(*) FROM price_history p JOIN instruments i "
                "ON i.ins_code = p.ins_code WHERE p.date IN (%s) AND p.volume > 0 "
                "AND i.is_equity = 1 GROUP BY p.date" % ",".join("?" * len(chunk)),
                chunk).fetchall()
        except sqlite3.Error:
            rows = []
        out.update({str(d)[:10]: n for d, n in rows})
    return out


def e21_integrity(log_path=None, db_path=None, now=None):
    """What the E21 forward log holds, judged without one outcome.

    The calendar is TEDPIX's own (index_history); the outcome session of a
    session is the next calendar session, used only for WHEN it opened. A
    logged entry is timely when computed_at precedes that session's pre-open
    (08:45 Tehran); when the next session is not stored yet, the next
    trading weekday's pre-open is the deadline (conservative on a holiday).
    A session is due once a later calendar session exists or the next trading
    weekday's pre-open has passed; a due session with no record is absent.
    """

    log_path = log_path or E21_LOG
    db_path = db_path or MARKET_DB
    moment = (now or datetime.now(timezone.utc)).astimezone(TEHRAN)
    rows = _read_jsonl(log_path)
    chain = verify_e21_chain(rows)

    conn = _ro(db_path)
    try:
        calendar = _calendar(conn, RESERVATION_START)
        by_session = {}
        for r in rows:
            if r.get("spec", E21_SPEC) != E21_SPEC:
                continue
            by_session.setdefault(str(r.get("session"))[:10], []).append(r)
        traded = _traded_counts(conn, [s for s, rs in by_session.items()
                                       if any(not x.get("missed") for x in rs)])
    finally:
        conn.close()

    cal_set = set(calendar)
    due = []
    for i, s in enumerate(calendar):
        if i + 1 < len(calendar) or s in by_session:
            due.append(s)
        elif moment >= _pre_open(_next_trading_weekday(date.fromisoformat(s))):
            due.append(s)

    detail, counts = [], {"timely": 0, "late": 0, "missed_recorded": 0,
                          "absent": 0, "thin": 0, "clean": 0}
    for s in due:
        records = by_session.get(s) or []
        logged = [r for r in records if not r.get("missed")]
        if not records:
            counts["absent"] += 1
            detail.append({"session": s, "status": "absent"})
            continue
        if not logged:
            counts["missed_recorded"] += 1
            detail.append({"session": s, "status": "missed",
                           "reason": records[0].get("missed")})
            continue
        r = logged[0]
        i = calendar.index(s)
        outcome = calendar[i + 1] if i + 1 < len(calendar) else None
        deadline_day = date.fromisoformat(outcome) if outcome else \
            _next_trading_weekday(date.fromisoformat(s))
        try:
            stamp = datetime.fromisoformat(r["computed_at"])
            timely = stamp < _pre_open(deadline_day)
        except (KeyError, TypeError, ValueError):
            timely = False
        n_traded = traded.get(s)
        thin = bool(n_traded) and (r.get("n_names") or 0) < E21_WIDTH_OK * n_traded
        counts["timely" if timely else "late"] += 1
        counts["thin"] += int(thin)
        counts["clean"] += int(timely and not thin)
        detail.append({"session": s, "status": "timely" if timely else "late",
                       "outcome_session": outcome, "computed_at": r.get("computed_at"),
                       "n_names": r.get("n_names"), "traded_equities": n_traded,
                       "thin": thin})

    off_calendar = sorted(s for s in by_session if s not in cal_set)
    duplicates = sorted(s for s, rs in by_session.items() if len(rs) > 1)
    start = date.fromisoformat(RESERVATION_START)
    end = date.fromisoformat(RESERVATION_END)
    expected = len(due)
    return {
        "log": os.path.relpath(log_path, ROOT) if log_path.startswith(ROOT) else log_path,
        "reservation": {"id": RESERVATION_ID, "start": RESERVATION_START,
                        "end": RESERVATION_END,
                        "matured": moment.date() >= end,
                        "days_elapsed": max(0, (min(moment.date(), end) - start).days),
                        "days_total": (end - start).days},
        "chain": chain,
        "sessions_expected": expected,
        "logged": counts["timely"] + counts["late"],
        "timely": counts["timely"],
        "late": counts["late"],
        "missed_recorded": counts["missed_recorded"],
        "absent": counts["absent"],
        "thin": counts["thin"],
        "clean": counts["clean"],
        "clean_share": round(counts["clean"] / expected, 4) if expected else None,
        "first_session": calendar[0] if calendar else None,
        "last_due_session": due[-1] if due else None,
        "absent_sessions": [d["session"] for d in detail if d["status"] == "absent"],
        "late_sessions": [d["session"] for d in detail if d["status"] == "late"],
        "thin_sessions": [d["session"] for d in detail if d.get("thin")],
        "missed_sessions": [d["session"] for d in detail if d["status"] == "missed"],
        "off_calendar_records": off_calendar,
        "duplicate_sessions": duplicates,
        "rules": {
            "timely": "computed_at before the outcome session's pre-open "
                      "(%02d:%02d Tehran)" % E21_PRE_OPEN,
            "thin": "n_names below %.0f%% of the equities that traded the "
                    "session" % (E21_WIDTH_OK * 100),
            "clean": "timely and not thin",
            "missing": "absent and missed sessions are counted as missing, "
                       "never imputed",
        },
        "outcome_scoring": "locked until %s (%s): scoring these predictions "
                           "against outcomes before then would spend the "
                           "window" % (RESERVATION_END, RESERVATION_ID),
        "detail": detail,
    }


# ==============================
# OUTLOOK: SCORED BY THE PROTOCOL
# ==============================

def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _owners_profit(items):
    owners = _f((items or {}).get("net_profit_owners"))
    return owners if owners is not None else _f((items or {}).get("net_profit"))


def _log_binom_pmf(k, n, p):
    if p <= 0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def _binom_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.exp(_log_binom_pmf(i, n, p)) for i in range(k + 1)))


def clopper_pearson(x, n, level=0.95):
    """Exact (Clopper-Pearson) binomial interval for x successes in n.

    The lower end solves P(X >= x | p) = alpha/2, the upper P(X <= x | p) =
    alpha/2; both by bisection on the binomial CDF, which falls in p."""

    if not n:
        return None
    alpha = 1 - level

    def falling(target, fn):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if fn(mid) > target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    lower = 0.0 if x <= 0 else falling(1 - alpha / 2,
                                       lambda p: _binom_cdf(x - 1, n, p))
    upper = 1.0 if x >= n else falling(alpha / 2, lambda p: _binom_cdf(x, n, p))
    return [round(lower, 4), round(upper, 4)]


def _pinball(y, quantiles, taus, scale):
    total = 0.0
    for tau, q in zip(taus, quantiles):
        u = y - q
        total += u * (tau - (1.0 if u < 0 else 0.0))
    return total / len(taus) / scale


def _jalali_to_date(stamp):
    import jalali
    try:
        jy, jm, jd = (int(p) for p in str(stamp)[:10].split("/"))
        return date(*jalali.to_gregorian(jy, jm, jd))
    except (TypeError, ValueError):
        return None


def _months_since_fy(fy, today):
    import jalali
    ty, tm, _td = jalali.today_jalali(today)
    y, m = (int(p) for p in fy.split("/"))
    return (ty * 12 + tm) - (y * 12 + m)


def _annual_filings(conn, codes):
    """{(ins_code, 'YYYY/MM', consolidated): first published row}."""

    out = {}
    codes = sorted(set(codes))
    for start in range(0, len(codes), 400):
        chunk = codes[start:start + 400]
        try:
            rows = conn.execute(
                "SELECT ins_code, period_end, consolidated, published_at, "
                "tracing_no, items FROM codal_filings WHERE period_months = 12 "
                "AND published_at IS NOT NULL AND ins_code IN (%s)"
                % ",".join("?" * len(chunk)), chunk).fetchall()
        except sqlite3.Error:
            rows = []
        for code, end, cons, published, tracing, items in rows:
            key = (str(code), str(end)[:7], bool(cons))
            held = out.get(key)
            if held is None or str(published) < held["published_at"]:
                try:
                    parsed = json.loads(items) if items else {}
                except ValueError:
                    parsed = {}
                out[key] = {"published_at": str(published), "tracing_no": tracing,
                            "period_end": end, "profit": _owners_profit(parsed)}
    return out


def _resolving(rec, filings, months_since, deadline):
    """(filing, substituted) for one logged record, or (None, False)."""

    same = filings.get((rec["ins_code"], rec["fy"], bool(rec["consolidated"])))
    if same:
        return same, False
    if rec.get("series_basis") == "mixed" and months_since >= deadline:
        other = filings.get((rec["ins_code"], rec["fy"], not rec["consolidated"]))
        if other:
            return other, True
    return None, False


def _score_unit(rec, filing, params):
    taus = params["quantiles"]
    keys = ["q%02d" % round(t * 100) for t in taus]
    ratios = rec["ratios"]
    central = _f(rec["profit"]["central"])
    y = filing["profit"]
    out = {"ins_code": rec["ins_code"], "symbol": rec.get("symbol"),
           "fy": rec["fy"], "months_known": rec.get("months_known"),
           "logged_on": rec["_date"], "resolved_on": filing["published_at"],
           "tracing_no": filing["tracing_no"], "actual": y, "central": central}
    if y is None or central is None:
        out["scorable"] = False
        return out
    quant = [central * ratios[k] for k in keys]
    lo_in, hi_in = (central * ratios[params["inner_band"][0]],
                    central * ratios[params["inner_band"][1]])
    lo_out, hi_out = (central * ratios[params["outer_band"][0]],
                      central * ratios[params["outer_band"][1]])
    naive = rec.get("naive") or {}
    a_last = _f(naive.get("last_annual_profit"))
    ttm = _f(naive.get("ttm_profit"))
    scale = max(abs(a_last or 0.0), abs(ttm or 0.0)) or None
    out.update(scorable=True,
               inner_hit=lo_in <= y <= hi_in, outer_hit=lo_out <= y <= hi_out,
               nominal_inner=(rec.get("band") or {}).get("nominal_inner"),
               abs_error=abs(y / central - 1) if central else None)
    if scale:
        out["pinball"] = _pinball(y, quant, taus, scale)
        mid = ratios.get("q50")
        if a_last is not None and a_last > 0 and mid and mid > 0:
            out["pinball_naive"] = _pinball(
                y, [a_last * ratios[k] / mid for k in keys], taus, scale)
        if ttm is not None and ttm > 0 and mid and mid > 0:
            out["pinball_naive_ttm"] = _pinball(
                y, [ttm * ratios[k] / mid for k in keys], taus, scale)
    if a_last is not None:
        predicted = (central > a_last) - (central < a_last)
        actual = (y > a_last) - (y < a_last)
        out["direction_hit"] = bool(predicted) and predicted == actual
        out["rose"] = actual > 0
    return out


def aggregate(units, params):
    """The protocol's metrics over a list of scored units."""

    units = [u for u in units if u.get("scorable")]
    n = len(units)
    level = params.get("ci_level", 0.95)
    out = {"n": n}
    if not n:
        return out
    x_in = sum(1 for u in units if u["inner_hit"])
    x_out = sum(1 for u in units if u["outer_hit"])
    out["coverage_inner"] = {"hits": x_in, "n": n, "rate": round(x_in / n, 4),
                             "ci95": clopper_pearson(x_in, n, level),
                             "nominal": params["inner_nominal"]}
    out["coverage_outer"] = {"hits": x_out, "n": n, "rate": round(x_out / n, 4),
                             "ci95": clopper_pearson(x_out, n, level),
                             "nominal": params["outer_nominal"]}
    for key, label in (("pinball_naive", "skill"), ("pinball_naive_ttm", "skill_vs_ttm")):
        both = [u for u in units if "pinball" in u and key in u]
        denom = sum(u[key] for u in both)
        out[label] = {"n": len(both),
                      "value": round(1 - sum(u["pinball"] for u in both) / denom, 4)
                      if both and denom > 0 else None}
    scored = [u for u in units if "pinball" in u]
    out["pinball_mean"] = round(sum(u["pinball"] for u in scored) / len(scored), 6) \
        if scored else None
    directed = [u for u in units if "direction_hit" in u]
    if directed:
        hits = sum(1 for u in directed if u["direction_hit"])
        rose = sum(1 for u in directed if u["rose"])
        out["direction"] = {"hits": hits, "n": len(directed),
                            "rate": round(hits / len(directed), 4),
                            "ci95": clopper_pearson(hits, len(directed), level),
                            "share_rose": round(rose / len(directed), 4)}
    errors = sorted(u["abs_error"] for u in units if u.get("abs_error") is not None)
    if errors:
        mid = len(errors) // 2
        out["median_abs_error"] = round(errors[mid] if len(errors) % 2 else
                                        (errors[mid - 1] + errors[mid]) / 2, 4)
    return out


def _retire_reasons(metrics, params):
    reasons = []
    skill = (metrics.get("skill") or {}).get("value")
    if skill is not None and skill <= params["retire_if_skill_at_most"]:
        reasons.append("skill %.3f <= %.1f vs the naive" % (
            skill, params["retire_if_skill_at_most"]))
    cov = metrics.get("coverage_inner")
    if params.get("retire_if_inner_nominal_outside_ci") and cov and cov["ci95"]:
        lo, hi = cov["ci95"]
        if not lo <= cov["nominal"] <= hi:
            reasons.append("inner coverage %.2f, 95%% interval %.2f-%.2f excludes "
                           "the nominal %.2f" % (cov["rate"], lo, hi, cov["nominal"]))
    return reasons


def _window(units, until, months):
    try:
        since = until.replace(year=until.year - months // 12)
    except ValueError:                                      # 29 February
        since = until.replace(year=until.year - months // 12, day=28)
    return [u for u in units if u.get("_resolved_date") and
            since < u["_resolved_date"] <= until]


def outlook_scorecard(today=None, log_path=None, protocol_path=None,
                      db_path=None):
    """The outlook forward log scored as its protocol says."""

    import outlook_forward_log as ofl

    log_path = log_path or ofl.LOG_PATH
    protocol_path = protocol_path or ofl.PROTOCOL_PATH
    db_path = db_path or MARKET_DB
    day = _today_tehran(today)
    protocol = ofl.load_protocol(protocol_path)
    params = protocol["parameters"]
    digest = ofl.protocol_sha256(protocol_path)
    rows = ofl.read_log(log_path)
    chain = ofl.verify_chain(rows, genesis=params.get("genesis", ofl.GENESIS))

    other_protocol = 0
    by_unit = {}
    for row in rows:
        if row.get("protocol_sha256") != digest:
            other_protocol += 1
            continue
        for rec in row.get("names") or ():
            rec = dict(rec, _at=row.get("logged_at_jalali") or "", _date=row.get("date"))
            by_unit.setdefault((rec["ins_code"], rec["fy"]), []).append(rec)

    conn = _ro(db_path)
    try:
        filings = _annual_filings(conn, [k[0] for k in by_unit])
    finally:
        conn.close()

    deadline = params["resolution_deadline_months"]
    resolved, diagnostic = [], []
    states = {"pending": 0, "expired": 0, "outcome_public_at_log": 0,
              "basis_substituted": 0, "unscorable": 0}
    expired_units = []
    for (code, fy), records in sorted(by_unit.items()):
        months_since = _months_since_fy(fy, day)
        chosen = None
        seen_states = set()
        for rec in records:
            filing, substituted = _resolving(rec, filings, months_since, deadline)
            if filing is None:
                if months_since < deadline:
                    # Its statement may still come: the unit waits for it.
                    seen_states.add("pending")
                    break
                # Past the deadline this record can never resolve; the next
                # one (another basis) may.
                seen_states.add("expired")
                continue
            if rec["_at"] < filing["published_at"]:
                chosen = (rec, filing, substituted)
                break
            seen_states.add("outcome_public_at_log")
        if chosen is None:
            state = "pending" if "pending" in seen_states else \
                "expired" if "expired" in seen_states else "outcome_public_at_log"
            states[state] += 1
            if state == "expired":
                expired_units.append({"ins_code": code, "fy": fy,
                                      "symbol": records[0].get("symbol")})
            continue
        rec, filing, substituted = chosen
        unit = _score_unit(rec, filing, params)
        unit["basis_substituted"] = substituted
        unit["_resolved_date"] = _jalali_to_date(filing["published_at"])
        states["basis_substituted"] += int(substituted)
        if not unit["scorable"]:
            states["unscorable"] += 1
        resolved.append(unit)
        # Diagnostic: the first admissible record of each months-known state.
        first_by_months = {}
        for r in records:
            if r["_at"] < filing["published_at"] and                     bool(r["consolidated"]) == bool(rec["consolidated"]):
                first_by_months.setdefault(r.get("months_known"), r)
        for r in first_by_months.values():
            diagnostic.append(_score_unit(r, filing, params))

    # The verdict, at every date a unit resolved and today; sticky retirement.
    min_n = params["min_resolved_n"]
    window_months = params["rolling_window_months"]
    retired_since, retire_reasons = None, []
    dates = sorted({u["_resolved_date"] for u in resolved
                    if u.get("_resolved_date") and u["_resolved_date"] <= day})
    for when in dates + [day]:
        window = [u for u in _window(resolved, when, window_months) if u.get("scorable")]
        if len(window) < min_n:
            continue
        reasons = _retire_reasons(aggregate(window, params), params)
        if reasons and params.get("retirement_sticky", True):
            retired_since, retire_reasons = when.isoformat(), reasons
            break

    current = [u for u in _window(resolved, day, window_months) if u.get("scorable")]
    metrics_window = aggregate(current, params)
    if retired_since:
        state = "retired"
    elif len(current) >= min_n:
        state = "active"
    else:
        state = "collecting"

    by_months = {}
    for u in diagnostic:
        by_months.setdefault(str(u.get("months_known")), []).append(u)
    labels = protocol.get("label_fa") or {}
    if state == "collecting":
        label = labels.get("collecting", "") % (len(current), min_n) \
            if "%d" in labels.get("collecting", "") else labels.get("collecting")
    elif state == "active":
        label = labels.get("active", "") % len(current) \
            if "%d" in labels.get("active", "") else labels.get("active")
    else:
        label = labels.get("retired")

    for u in resolved:
        u.pop("_resolved_date", None)
    return {
        "protocol": {"id": protocol.get("protocol_id"), "sha256": digest,
                     "path": os.path.relpath(protocol_path, ROOT)
                     if protocol_path.startswith(ROOT) else protocol_path},
        "log": {"entries": len(rows), "chain": chain,
                "first_date": rows[0].get("date") if rows else None,
                "last_date": rows[-1].get("date") if rows else None,
                "names_last_entry": len(rows[-1].get("names") or ()) if rows else 0,
                "entries_under_other_protocol": other_protocol},
        "as_of": day.isoformat(),
        "state": state,
        "label_fa": label,
        "min_n": min_n,
        "company_years": len(by_unit),
        "resolved": len(resolved),
        "resolved_in_window": len(current),
        "window_months": window_months,
        "states": states,
        "expired": expired_units,
        "retired_since": retired_since,
        "retire_reasons": retire_reasons,
        "metrics_window": metrics_window,
        "metrics_all": aggregate(resolved, params),
        "diagnostic_by_months_known": {k: aggregate(v, params)
                                       for k, v in sorted(by_months.items())},
        "units": resolved,
    }


# ==============================
# STATUS: WHAT THE PANEL READS
# ==============================

STATUS_TTL = 600.0
_CACHE = {"key": None, "at": None, "value": None}
_CACHE_LOCK = threading.Lock()


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def status(today=None, log_path=None, protocol_path=None, db_path=None,
           use_cache=True):
    """{state: 'collecting'|'active'|'retired', label_fa, ...}. Never raises.

    The outlook range's standing under its protocol, cached for STATUS_TTL
    seconds or until the log changes. On any failure the state is
    'unknown' with the error, so the panel can say so instead of breaking.
    """

    import outlook_forward_log as ofl

    log_path = log_path or ofl.LOG_PATH
    key = (log_path, protocol_path, db_path, _mtime(log_path),
           _mtime(protocol_path or ofl.PROTOCOL_PATH), str(_today_tehran(today)))
    now = time.monotonic()
    with _CACHE_LOCK:
        if use_cache and _CACHE["key"] == key and _CACHE["at"] is not None and \
                now - _CACHE["at"] < STATUS_TTL:
            return _CACHE["value"]
    try:
        card = outlook_scorecard(today=today, log_path=log_path,
                                 protocol_path=protocol_path, db_path=db_path)
        m = card["metrics_window"]
        value = {
            "state": card["state"],
            "label_fa": card["label_fa"],
            "report_only": card["state"] == "retired",
            "min_n": card["min_n"],
            "resolved": card["resolved"],
            "resolved_in_window": card["resolved_in_window"],
            "company_years": card["company_years"],
            "pending": card["states"]["pending"],
            "expired": card["states"]["expired"],
            "outcome_public_at_log": card["states"]["outcome_public_at_log"],
            "entries": card["log"]["entries"],
            "first_date": card["log"]["first_date"],
            "last_date": card["log"]["last_date"],
            "names_last_entry": card["log"]["names_last_entry"],
            "chain_intact": card["log"]["chain"]["intact"],
            "protocol_sha256": card["protocol"]["sha256"],
            "coverage_inner": m.get("coverage_inner"),
            "skill": (m.get("skill") or {}).get("value"),
            "median_abs_error": m.get("median_abs_error"),
            "retired_since": card["retired_since"],
            "retire_reasons": card["retire_reasons"],
            "as_of": card["as_of"],
            "error": None,
        }
    except Exception as exc:                                    # noqa: BLE001
        value = {"state": "unknown", "label_fa": "وضعیت آزمون پیش‌رو خوانده نشد",
                 "report_only": False, "error": "%s: %s" % (type(exc).__name__,
                                                            str(exc)[:200])}
    with _CACHE_LOCK:
        _CACHE.update(key=key, at=now, value=value)
    return value


def e21_status(log_path=None, db_path=None, use_cache=True):
    """e21_integrity() without the per-session detail, cached like status().
    Never raises."""

    key = ("e21", log_path, db_path, _mtime(log_path or E21_LOG),
           str(_today_tehran()))
    now = time.monotonic()
    with _CACHE_LOCK:
        held = _CACHE.get(key)
        if use_cache and held and now - held[0] < STATUS_TTL:
            return held[1]
    try:
        value = {k: v for k, v in e21_integrity(log_path, db_path).items()
                 if k != "detail"}
        value["error"] = None
    except Exception as exc:                                    # noqa: BLE001
        value = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    e21 = e21_integrity()
    try:
        outlook = outlook_scorecard()
    except Exception as exc:                                    # noqa: BLE001
        outlook = {"error": "%s: %s" % (type(exc).__name__, exc)}
    if args.json:
        e21.pop("detail", None)
        if "units" in outlook:
            outlook = dict(outlook, units=len(outlook["units"]))
        print(json.dumps({"e21": e21, "outlook": outlook}, ensure_ascii=False,
                         indent=1, default=str))
        return 0
    print("E21 FORWARD LOG (%s) -- integrity only; outcomes locked until %s"
          % (RESERVATION_ID, RESERVATION_END))
    print("  chain     : %s" % ("intact, %d entries" % e21["chain"]["entries"]
                                if e21["chain"]["intact"] else
                                "BROKEN at entry %s" % e21["chain"]["broken_at"]))
    print("  sessions  : %d expected (%s .. %s)" % (
        e21["sessions_expected"], e21["first_session"], e21["last_due_session"]))
    print("  logged    : %d  (timely %d, late %d)" % (e21["logged"], e21["timely"],
                                                     e21["late"]))
    print("  missing   : %d recorded missed + %d absent (never imputed)"
          % (e21["missed_recorded"], e21["absent"]))
    print("  thin      : %d;  clean (timely, not thin): %d = %s of expected"
          % (e21["thin"], e21["clean"],
             "%.1f%%" % (e21["clean_share"] * 100) if e21["clean_share"] is not None
             else "-"))
    print()
    if "error" in outlook:
        print("OUTLOOK FORWARD LOG: %s" % outlook["error"])
        return 1
    print("OUTLOOK FORWARD LOG (%s, protocol %s..)" % (
        outlook["protocol"]["id"], (outlook["protocol"]["sha256"] or "-")[:12]))
    print("  entries   : %d (%s .. %s), chain %s" % (
        outlook["log"]["entries"], outlook["log"]["first_date"],
        outlook["log"]["last_date"],
        "intact" if outlook["log"]["chain"]["intact"] else "BROKEN"))
    print("  units     : %d company-years; resolved %d (%d in the %d-month window); "
          "pending %d, expired %d, outcome public at log %d"
          % (outlook["company_years"], outlook["resolved"],
             outlook["resolved_in_window"], outlook["window_months"],
             outlook["states"]["pending"], outlook["states"]["expired"],
             outlook["states"]["outcome_public_at_log"]))
    print("  state     : %s -- %s" % (outlook["state"], outlook["label_fa"]))
    m = outlook["metrics_window"]
    if m.get("n"):
        cov = m["coverage_inner"]
        print("  coverage  : inner %.2f (95%% %s, nominal %.2f); skill vs naive %s"
              % (cov["rate"], cov["ci95"], cov["nominal"],
                 (m.get("skill") or {}).get("value")))
    else:
        print("  metrics   : insufficient N (%d of %d resolved)"
              % (outlook["resolved_in_window"], outlook["min_n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(None))
