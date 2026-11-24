"""E35 (successor of E34): do E27 and E28 survive real treasury-bill yields?

Implements backtest_output/E35_preregistration.json exactly; read that first.

    python examples/real_bills_study.py --register   freeze the record (once)
    python examples/real_bills_study.py --run        compute, write, decide

--register goes through the Experiment Manager BEFORE anything is computed:
the preregistration is hashed into sirat_research.db and the window is
recorded. --run refuses to start unless that registration exists and its
frozen parameters still match the file on disk. It then writes
backtest_output/E35_results.json, records Stage 1 (data integrity, measured
here) and closes the experiment as the preregistration's decision rule says.
"""

import argparse
import json
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for path in (ROOT, os.path.join(ROOT, "examples")):
    if path not in sys.path:
        sys.path.insert(0, path)

import backtest_config                                          # noqa: E402
import bill_history                                             # noqa: E402
import config                                                   # noqa: E402
from allocation_study import (HOLDING_DRAG, memmel, norm_sf,  # noqa: E402
                              per_session, series)
from gold_sleeve_study import gold_sleeve                       # noqa: E402

# E34 ended at Stage 1 on a data-layer defect (every pre-1400 bill maturity
# read a century late); E35 asks the same questions on corrected maturities.
# E34's frozen record is untouched; this file now runs E35 by default.
EXPERIMENTS = {
    "E34": "E34-real-bills",
    "E35": "E35-real-bills",
}
EXPERIMENT_ID = EXPERIMENTS["E35"]
PREREG = os.path.join(backtest_config.OUTPUT_DIR, "E35_preregistration.json")
OUT = os.path.join(backtest_config.OUTPUT_DIR, "E35_results.json")

START = date(2017, 10, 1)
END = date(2026, 7, 31)
REBALANCE = 60
COST = config.LIVE_FUND_ROUND_TRIP_PCT / 100.0       # 0.7512% round trip
FUND_RATE = 0.20
BLOCK_MONTHS = 6
RESAMPLES = 10_000
SEED = 20261124
FAMILY_ALPHA = 0.05
SESSION_BLOCK = 20
CARRIED_MAX = 0.10
REJECTED_MAX = 0.05
NEWEY_WEST_LAG = 6

BOOKS = {
    "equity_100": {"e": 1.0},
    "eq70_gold30": {"e": 0.7, "g": 0.3},
    "eq70_bills30": {"e": 0.7, "b": 0.3},
    "eq70_gold15_bills15": {"e": 0.7, "g": 0.15, "b": 0.15},
}


# ---- the grid: every session, plus every calendar month-end --------------------

def grid(calendar):
    """Sessions in the window plus each month's last calendar day.

    A month-end that is not a session carries no market move; it exists so
    that the bill accrual of a month is credited to that month even when the
    exchange was closed (the prereg: closed months earn the bill accrual and
    zero on market legs).
    """

    sessions = [d for d in calendar if START <= d.date() <= END]
    ends = pd.date_range(pd.Timestamp(START), pd.Timestamp(END), freq="ME")
    points = sorted(set(sessions) | set(ends))
    return pd.DatetimeIndex(points), set(sessions)


def legs(calendar, index, sleeve, yields, fund_rate=None):
    """Per-grid-point returns of equity, gold and the fixed-income leg."""

    points, sessions = grid(calendar)
    previous_session = calendar[calendar < pd.Timestamp(START)][-1]

    closes = index.reindex(points.union([previous_session])).ffill()
    eq = closes.pct_change().reindex(points).fillna(0.0)
    drag = per_session(HOLDING_DRAG)
    is_session = np.array([p in sessions for p in points])
    equity = np.where(is_session, (1 + eq.to_numpy()) * (1 - drag) - 1, 0.0)

    gold = sleeve.reindex(points).fillna(0.0).to_numpy()
    gold = np.where(is_session, gold, 0.0)

    fixed = []
    before = pd.Timestamp(START) - pd.Timedelta(days=1)
    for p in points:
        gap = (p - before).days
        rate = fund_rate if fund_rate is not None else \
            yields[(p.year, p.month)]["yield"]
        fixed.append((1 + rate) ** (gap / 365.0) - 1)
        before = p

    return pd.DataFrame({"e": equity, "g": gold, "b": np.array(fixed)},
                        index=points), is_session


def book(frame, is_session, weights):
    """Daily returns of a book rebalanced to `weights` every REBALANCE sessions."""

    columns = list(weights)
    target = np.array([weights[c] for c in columns], dtype=float)
    held = target.copy()
    out = []
    count = 0
    for row, session in zip(frame[columns].to_numpy(), is_session):
        cost = 0.0
        if session:
            count += 1
            if count > 1 and (count - 1) % REBALANCE == 0:
                cost = np.abs(held - target).sum() * COST / 2
                held = target.copy()
        grown = held * (1 + row)
        value = grown.sum()
        out.append(value * (1 - cost) - 1)
        held = grown / value
    return pd.Series(out, index=frame.index)


def monthly(daily):
    return (1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1


def session_blocks(daily, is_session):
    """E28's 20-session blocks; a month-end point joins the block of the
    session before it. The last partial block is dropped."""

    counter = np.cumsum(is_session)
    label = np.maximum(counter - 1, 0) // SESSION_BLOCK
    grouped = (1 + daily).groupby(label).prod() - 1
    full = int(counter[-1] // SESSION_BLOCK)
    return grouped.iloc[:full].to_numpy()


# ---- statistics -------------------------------------------------------------------

def sharpe(x):
    sd = np.std(x, ddof=1)
    return float(np.mean(x) / sd) if sd > 0 else float("nan")


def bootstrap_indices(n, rng):
    blocks = math.ceil(n / BLOCK_MONTHS)
    starts = rng.integers(0, n, size=blocks)
    idx = (starts[:, None] + np.arange(BLOCK_MONTHS)[None, :]) % n
    return idx.ravel()[:n]


def bootstrap_p(statistic, arrays, two_sided=False):
    """Circular block bootstrap p, as the preregistration defines it."""

    observed = statistic(*arrays)
    rng = np.random.default_rng(SEED)
    n = len(arrays[0])
    hits = 0
    draws = []
    for _ in range(RESAMPLES):
        idx = bootstrap_indices(n, rng)
        d = statistic(*(a[idx] for a in arrays))
        draws.append(d)
        if two_sided:
            hits += abs(d - observed) >= abs(observed)
        else:
            hits += (d - observed) >= observed
    draws = np.array(draws)
    return {"observed": round(float(observed), 6),
            "p": round((1 + hits) / (1 + RESAMPLES), 5),
            "ci90": [round(float(np.quantile(draws, 0.05)), 6),
                     round(float(np.quantile(draws, 0.95)), 6)]}


def newey_west_t(x, lag=NEWEY_WEST_LAG):
    x = np.asarray(x, dtype=float)
    n = len(x)
    e = x - x.mean()
    gamma0 = np.dot(e, e) / n
    s = gamma0
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        s += 2 * w * np.dot(e[k:], e[:-k]) / n
    se = math.sqrt(s / n)
    t = x.mean() / se if se > 0 else float("nan")
    return {"t": round(float(t), 3), "p_one_sided": round(norm_sf(t), 5)}


def holm(pvalues, alpha=FAMILY_ALPHA):
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m = len(order)
    out = {}
    still = True
    for i, key in enumerate(order):
        threshold = alpha / (m - i)
        reject = still and pvalues[key] <= threshold
        if not reject:
            still = False
        out[key] = {"p": pvalues[key], "holm_threshold": round(threshold, 5),
                    "null_rejected": bool(reject)}
    return out


def _finite(value, digits):
    return round(float(value), digits) if math.isfinite(value) else None


def describe(daily, is_session, fixed):
    wealth = (1 + daily).cumprod()
    days = (daily.index[-1] - daily.index[0]).days + 1
    years = days / 365.25
    m = monthly(daily).to_numpy()
    mb = monthly(fixed).to_numpy()
    ex = m - mb
    return {
        "total_pct": round((wealth.iloc[-1] - 1) * 100, 1),
        "cagr_pct": round((wealth.iloc[-1] ** (1 / years) - 1) * 100, 2),
        "monthly_vol_pct": round(float(np.std(m, ddof=1)) * 100, 2),
        "sharpe_monthly_vs_bills": _finite(sharpe(ex), 4),
        "sharpe_annualised_vs_bills": _finite(sharpe(ex) * math.sqrt(12), 3),
        "max_drawdown_pct": round(float((wealth / wealth.cummax() - 1).min())
                                  * 100, 2),
    }


# ---- registration --------------------------------------------------------------

def _prereg():
    with open(PREREG, encoding="utf-8") as fh:
        return json.load(fh)


def _manager_prereg(doc):
    return {
        "parameters": doc,
        "validation_gates": doc["stage_1_checks"],
        "expected_metrics": {
            "months": "about 106 (2017-10..2026-07)",
            "statistic": doc["statistic"],
        },
        "rejection_thresholds": {
            "holm_family_alpha": FAMILY_ALPHA,
            "carried_months_max_share": CARRIED_MAX,
            "rejected_quotes_max_share": REJECTED_MAX,
        },
        "seed": SEED,
    }


def register():
    from sirat.experiments.manager import GovernanceError, Manager

    doc = _prereg()
    try:
        manager = Manager.register(
            experiment_id=EXPERIMENT_ID,
            hypothesis=" | ".join("%s: %s" % kv for kv in
                                  doc["hypotheses"].items()),
            strategy_version="allocation-real-bills-v1",
            researcher=doc["researcher"],
            notes=("Re-test of E27 H1 and E28 H1 against the month-start yield "
                   "of real treasury bills. " + doc.get("why_a_successor", "") +
                   " " + doc["written_before"] +
                   " The working tree also carries unrelated, uncommitted work "
                   "by other implementers on 2026-11-24 (corporate actions, "
                   "point-in-time statements, forward scorecard), so git_dirty "
                   "is expected; this experiment's own files are committed."),
            expected_failure=" | ".join(doc["expected_failure"]),
            acceptance_criteria=doc["test"]["family"] + " " +
            doc["test"]["accept"],
            preregistration=_manager_prereg(doc),
        )
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1
    manager.touch_window(START.isoformat(), END.isoformat(), "evaluation")
    record = manager.record()
    print("registered %s; prereg hash %s; next stage %s"
          % (EXPERIMENT_ID, record["provenance"].get("prereg_hash"),
             manager.next_stage()))
    return 0


def _registered():
    from sirat.experiments.manager import Manager
    try:
        manager = Manager.load(EXPERIMENT_ID)
        frozen = manager.preregistration()
    except Exception as exc:                                    # noqa: BLE001
        raise SystemExit("%s is not registered (%s). Run --register first."
                         % (EXPERIMENT_ID, exc))
    if frozen["parameters"] != _prereg():
        raise SystemExit("the preregistration on disk differs from the frozen "
                         "record; register a new id instead of editing it.")
    return manager


# ---- the run ---------------------------------------------------------------------

def run(record_decision=True):
    manager = _registered() if record_decision else None

    index = series("TEDPIX")
    calendar = index.index
    sleeve, first_gold, dropped, funds_at_end = gold_sleeve(calendar)

    yields, rejected = bill_history.month_start_yields(
        [d.date() for d in calendar], START, END)

    points, sessions = grid(calendar)
    window_sessions = sorted(sessions)

    # ---- stage 1, measured --------------------------------------------------
    months = list(yields)
    missing = [k for k, r in yields.items() if r is None]
    carried = [k for k, r in yields.items() if r and r["basis"] == "carried"]
    pit_ok = all(r is None or date.fromisoformat(r["to"]) < date(k[0], k[1], 1)
                 for k, r in yields.items())
    rows, _ = bill_history.quotes()
    in_window = [q for q in rows if START <= q[0] <= END]
    rejected_window = [q for q in rejected if START <= q[0] <= END]
    traded = len(in_window) + len(rejected_window)
    with bill_history._connect(readonly=True) as conn:
        matured = {c for c, due in conn.execute(
            "SELECT ins_code, maturity FROM bill_series")
            if due and date.fromisoformat(due) < date(2026, 11, 24)}
    pool_codes = {q[1] for q in in_window}
    gold_present = sleeve.reindex(pd.DatetimeIndex(window_sessions)).notna()
    gold_ok = bool(first_gold is not None
                   and first_gold.date() < START
                   and gold_present.mean() >= 0.99)
    tedpix_months = {(d.year, d.month) for d in window_sessions}
    closed_months = [k for k in months if k not in tedpix_months]

    if missing:
        stage1 = {"point_in_time": pit_ok, "survivorship": bool(pool_codes & matured),
                  "universe_valid": gold_ok, "missing_data": False,
                  "price_sanity": False}
        result = {"stage_1": stage1, "missing_months": missing,
                  "verdict": "NO RESULT: a month has no bill yield"}
        _write(result)
        if manager:
            _close(manager, stage1, result)
        return result

    frame, is_session = legs(calendar, index, sleeve, yields)
    daily = {name: book(frame, is_session, w) for name, w in BOOKS.items()}
    fixed = frame["b"]
    m_fixed = monthly(fixed).to_numpy()
    m = {name: monthly(d).to_numpy() for name, d in daily.items()}
    excess = {name: v - m_fixed for name, v in m.items()}
    worst_month = max(float(np.max(np.abs(v))) for v in m.values())

    stage1 = {
        "point_in_time": pit_ok,
        "survivorship": bool(pool_codes & matured),
        "universe_valid": gold_ok and bool(len(window_sessions)),
        "missing_data": len(carried) <= CARRIED_MAX * len(months),
        "price_sanity": (traded > 0 and len(rejected_window) <= REJECTED_MAX
                         * traded and worst_month < 1.0),
    }

    # ---- the three tests ------------------------------------------------------
    def sharpe_diff(a, b):
        return sharpe(a) - sharpe(b)

    h1 = bootstrap_p(sharpe_diff, (excess["eq70_gold30"],
                                   excess["eq70_bills30"]))
    h2 = bootstrap_p(lambda x: float(np.mean(x)), (excess["equity_100"],))
    h3 = bootstrap_p(sharpe_diff, (excess["eq70_gold15_bills15"],
                                   excess["eq70_gold30"]), two_sided=True)
    family = holm({"H1_gold_vs_real_bills": h1["p"],
                   "H2_equity_vs_real_bills": h2["p"],
                   "H3_three_part_vs_gold": h3["p"]})

    # ---- reported, not tested -------------------------------------------------
    z1, p1m, sa, sb, rho = memmel(pd.Series(excess["eq70_gold30"]),
                                  pd.Series(excess["eq70_bills30"]))
    blocks = {name: session_blocks(d, is_session) for name, d in daily.items()}
    b_fixed = session_blocks(fixed, is_session)
    bz1 = memmel(pd.Series(blocks["eq70_gold30"] - b_fixed),
                 pd.Series(blocks["eq70_bills30"] - b_fixed))

    frame20, _ = legs(calendar, index, sleeve, yields, fund_rate=FUND_RATE)
    daily20 = {name: book(frame20, is_session, w) for name, w in BOOKS.items()}
    m20_fixed = monthly(frame20["b"]).to_numpy()
    ex20 = {name: monthly(d).to_numpy() - m20_fixed for name, d in daily20.items()}
    fund20 = {
        "H1_like": bootstrap_p(sharpe_diff, (ex20["eq70_gold30"],
                                             ex20["eq70_bills30"])),
        "H2_like": bootstrap_p(lambda x: float(np.mean(x)), (ex20["equity_100"],)),
        "books": {name: describe(d, is_session, frame20["b"])
                  for name, d in daily20.items()},
    }

    bill_rows = [{"month": "%d-%02d" % k, "yield_pct": round(r["yield"] * 100, 2),
                  "quotes": r["quotes"], "basis": r["basis"]}
                 for k, r in sorted(yields.items())]
    bill_years = {}
    for k, r in yields.items():
        bill_years.setdefault(k[0], []).append(r["yield"])

    result = {
        "preregistration": "backtest_output/%s" % os.path.basename(PREREG),
        "window_months": ["%d-%02d" % months[0], "%d-%02d" % months[-1]],
        "months": len(months),
        "sessions": len(window_sessions),
        "closed_months": ["%d-%02d" % k for k in closed_months],
        "gold_first_session": str(first_gold.date()),
        "gold_funds_at_end": funds_at_end,
        "gold_returns_dropped": dropped,
        "gold_sessions_without_value": int((~gold_present).sum()),
        "bills": {
            "carried_months": len(carried),
            "quotes_in_window": len(in_window),
            "rejected_quotes_in_window": len(rejected_window),
            "series_in_pool": len(pool_codes),
            "matured_series_in_pool": len(pool_codes & matured),
            "mean_yield_by_year_pct": {str(y): round(float(np.mean(v)) * 100, 2)
                                       for y, v in sorted(bill_years.items())},
            "cagr_pct": describe(fixed, is_session, fixed)["cagr_pct"],
            "series": bill_rows,
        },
        "books": {name: describe(d, is_session, fixed)
                  for name, d in daily.items()},
        "stage_1": stage1,
        "tests": {
            "H1_gold_vs_real_bills": dict(h1, statistic="monthly Sharpe(eq70_gold30) - Sharpe(eq70_bills30)", sided="one"),
            "H2_equity_vs_real_bills": dict(h2, statistic="mean monthly excess of equity_100 over bills", sided="one",
                                            mean_annualised_pct=round(((1 + h2["observed"]) ** 12 - 1) * 100, 2)),
            "H3_three_part_vs_gold": dict(h3, statistic="monthly Sharpe(eq70_gold15_bills15) - Sharpe(eq70_gold30)", sided="two"),
            "holm": family,
        },
        "reported_not_tested": {
            "memmel_monthly_H1": {"z": round(z1, 3), "p_one_sided": round(p1m, 5),
                                  "rho": round(rho, 3)},
            "memmel_20_session_blocks_H1": {"z": round(bz1[0], 3),
                                            "p_one_sided": round(bz1[1], 5)},
            "newey_west_H2": newey_west_t(excess["equity_100"]),
            "corr_equity_gold_monthly": round(float(np.corrcoef(
                m["equity_100"], monthly(frame["g"]).to_numpy())[0, 1]), 3),
            "same_window_against_20pct_fund": fund20,
        },
    }

    if not all(stage1.values()):
        result["verdict"] = {"stage_1": "FAIL", "note": "the experiment says nothing"}
    else:
        verdict = {}
        for key in ("H1_gold_vs_real_bills", "H2_equity_vs_real_bills",
                    "H3_three_part_vs_gold"):
            verdict[key] = ("ACCEPT" if family[key]["null_rejected"]
                            else "NOT ACCEPTED")
        verdict["family"] = "allocation; Holm at %.2f over H1-H3" % FAMILY_ALPHA
        result["verdict"] = verdict

    _write(result)
    if manager:
        _close(manager, stage1, result)
    return result


def _write(result):
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


def _close(manager, stage1, result):
    from sirat.experiments import manager as mgr

    detail = {k: result.get(k) for k in ("bills", "months", "closed_months",
                                         "gold_returns_dropped")}
    detail["bills"] = {k: v for k, v in (detail.get("bills") or {}).items()
                       if k != "series"}
    manager.run_stage(1, stage1, detail=detail)
    if not all(stage1.values()):
        out = manager.decide(mgr.REJECT, "Stage 1 failed: %s. The preregistration "
                             "says the experiment then says nothing." %
                             ", ".join(k for k, v in stage1.items() if not v),
                             by="SIRAT-AI Research (%s script)" % EXPERIMENT_ID)
    else:
        v = result["verdict"]
        out = manager.decide(
            mgr.ARCHIVE,
            "Allocation re-test, closed as the preregistration's decision rule "
            "says (ARCHIVE: not a signal for the stage pipeline). Verdicts: "
            "H1 gold vs real bills %s (p %.4f); H2 equity vs real bills %s "
            "(p %.4f); H3 three-part vs gold %s (p %.4f); Holm at 0.05 within "
            "the experiment, family 'allocation'. Results: %s."
            % (v["H1_gold_vs_real_bills"],
               result["tests"]["H1_gold_vs_real_bills"]["p"],
               v["H2_equity_vs_real_bills"],
               result["tests"]["H2_equity_vs_real_bills"]["p"],
               v["H3_three_part_vs_gold"],
               result["tests"]["H3_three_part_vs_gold"]["p"],
               os.path.relpath(OUT, ROOT)),
            by="SIRAT-AI Research (%s script)" % EXPERIMENT_ID)
    print("decision:", json.dumps(out, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.register:
        return register()
    if args.run:
        result = run()
        printable = dict(result)
        if "bills" in printable:
            printable["bills"] = {k: v for k, v in printable["bills"].items()
                                  if k != "series"}
        print(json.dumps(printable, ensure_ascii=False, indent=1))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
