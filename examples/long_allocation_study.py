"""E36: the allocation questions over fifteen years -- about two market cycles.

Implements backtest_output/E36_preregistration.json exactly; read that first.

    python examples/long_allocation_study.py --register   freeze it (once)
    python examples/long_allocation_study.py --run        compute, write, decide

The arithmetic is E35's (examples/real_bills_study.py): the grid of sessions
and month-ends, the 60-session rebalance, the monthly excess, the bootstrap
and Holm. What differs is declared in the preregistration and set here: the
window (2011-08..2026-07), the block length (12 months), the seed, and the
two legs spliced at 2017-10 -- the Emami coin before E28's gold funds, lease
bonds before E35's treasury bills (long_history.py).
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
import long_history                                             # noqa: E402
import real_bills_study as base                                 # noqa: E402
from allocation_study import memmel, series                     # noqa: E402
from gold_sleeve_study import gold_sleeve                       # noqa: E402

EXPERIMENT_ID = "E36-allocation-15y"
PREREG = os.path.join(backtest_config.OUTPUT_DIR, "E36_preregistration.json")
OUT = os.path.join(backtest_config.OUTPUT_DIR, "E36_results.json")

START = date(2011, 8, 1)
END = date(2026, 7, 31)
SPLICE = date(2017, 10, 1)
CYCLE_SPLIT = date(2018, 10, 1)      # after the dollar-terms trough of 1397/07
BLOCK_MONTHS = 12
E35_BLOCK_MONTHS = 6
SEED = 20261125
NEWEY_WEST_LAG = 12
COIN_STALE_DAYS = 7
COVERED_SHARE = 0.99


def _configure_base(block_months=BLOCK_MONTHS):
    """E35's functions read their window, block and seed from module level;
    E36's values are set there before any of them runs."""

    base.START, base.END = START, END
    base.BLOCK_MONTHS = block_months
    base.SEED = SEED
    base.NEWEY_WEST_LAG = NEWEY_WEST_LAG


def coin_returns(calendar):
    """Per-session coin returns, and the share of sessions whose last coin
    print is at most COIN_STALE_DAYS old (both over the coin era)."""

    rows = long_history.coin_series()
    coin = pd.Series({pd.Timestamp(iso): float(p) for iso, _j, p in rows}).sort_index()
    on_sessions = coin.reindex(calendar.union(coin.index)).ffill().reindex(calendar)
    last_print = pd.Series(coin.index, index=coin.index).reindex(
        calendar.union(coin.index)).ffill().reindex(calendar)
    era = (calendar >= pd.Timestamp(START)) & (calendar < pd.Timestamp(SPLICE))
    age = (calendar.to_series() - last_print).dt.days
    fresh = float((age[era] <= COIN_STALE_DAYS).mean()) if era.any() else 0.0
    return on_sessions.pct_change(fill_method=None), fresh


def gold_leg(calendar):
    sleeve, first_gold, dropped, funds_at_end = gold_sleeve(calendar)
    coin_r, coin_fresh = coin_returns(calendar)
    splice = pd.Timestamp(SPLICE)
    gold = pd.concat([coin_r[calendar < splice], sleeve[calendar >= splice]])
    fund_era = calendar[(calendar >= splice) & (calendar <= pd.Timestamp(END))]
    fund_present = float(sleeve.reindex(fund_era).notna().mean())
    return gold, {"coin_fresh_share": round(coin_fresh, 4),
                  "fund_sessions_with_value_share": round(fund_present, 4),
                  "gold_fund_first_session": str(first_gold.date()),
                  "gold_funds_at_end": funds_at_end,
                  "gold_fund_returns_dropped": dropped}


def fixed_income(calendar):
    sessions = [d.date() for d in calendar]
    lease, lease_rejected = bill_history.month_start_yields(
        sessions, START, date(2017, 9, 30),
        quotes_fn=long_history.lease_quotes)
    bills, bill_rejected = bill_history.month_start_yields(sessions, SPLICE, END)
    yields = dict(lease)
    yields.update(bills)
    lease_rows, _ = long_history.lease_quotes()
    bill_rows, _ = bill_history.quotes()
    in_window = ([q for q in lease_rows if START <= q[0] < SPLICE] +
                 [q for q in bill_rows if SPLICE <= q[0] <= END])
    rejected = ([q for q in lease_rejected if START <= q[0] < SPLICE] +
                [q for q in bill_rejected if SPLICE <= q[0] <= END])
    return yields, in_window, rejected


def _slice_describe(daily, fixed, start, end):
    mask = (daily.index >= pd.Timestamp(start)) & (daily.index <= pd.Timestamp(end))
    return base.describe(daily[mask], None, fixed[mask])


# ---- registration ------------------------------------------------------------------

def _prereg():
    with open(PREREG, encoding="utf-8") as fh:
        return json.load(fh)


def _manager_prereg(doc):
    return {
        "parameters": doc,
        "validation_gates": doc["stage_1_checks"],
        "expected_metrics": {"months": "180 (2011-08..2026-07)",
                             "statistic": doc["statistic"]},
        "rejection_thresholds": {"holm_family_alpha": base.FAMILY_ALPHA,
                                 "carried_months_max_share": base.CARRIED_MAX,
                                 "rejected_quotes_max_share": base.REJECTED_MAX,
                                 "coverage_min_share": COVERED_SHARE},
        "seed": SEED,
    }


def register():
    from sirat.experiments.manager import GovernanceError, Manager

    doc = _prereg()
    try:
        manager = Manager.register(
            experiment_id=EXPERIMENT_ID,
            hypothesis=" | ".join("%s: %s" % kv for kv in doc["hypotheses"].items()),
            strategy_version="allocation-15y-v1",
            researcher=doc["researcher"],
            notes=(doc["question"] + " " + doc["written_before"] +
                   " The working tree carries uncommitted, unrelated work of "
                   "2026-11-24 (corporate actions), so git_dirty is expected; "
                   "this experiment's own files are committed."),
            expected_failure=" | ".join(doc["expected_failure"]),
            acceptance_criteria=doc["test"]["family"] + " " + doc["test"]["accept"],
            preregistration=_manager_prereg(doc),
        )
    except GovernanceError as exc:
        print("REFUSED: %s" % exc)
        return 1
    manager.touch_window(START.isoformat(), END.isoformat(), "evaluation")
    print("registered %s; prereg hash %s; next stage %s"
          % (EXPERIMENT_ID, manager.record()["provenance"].get("prereg_hash"),
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
    _configure_base()

    index = series("TEDPIX")
    calendar = index.index
    gold, gold_info = gold_leg(calendar)
    yields, quotes_in, rejected = fixed_income(calendar)

    points, sessions = base.grid(calendar)
    months = list(yields)
    missing = [k for k, r in yields.items() if r is None]
    carried = [k for k, r in yields.items() if r and r["basis"] == "carried"]
    pit_ok = all(r is None or date.fromisoformat(r["to"]) < date(k[0], k[1], 1)
                 for k, r in yields.items())
    with bill_history._connect(readonly=True) as conn:
        matured_bills = {c for c, due in conn.execute(
            "SELECT ins_code, maturity FROM bill_series")
            if due and date.fromisoformat(due) < date(2026, 11, 24)}
    lease_codes = {q[1] for q in quotes_in if q[0] < SPLICE}
    pool_codes = {q[1] for q in quotes_in}
    traded = len(quotes_in) + len(rejected)

    def stage_one(worst_month):
        return {
            "point_in_time": pit_ok,
            # Every lease bond of 2011-2017 has matured; bills as in E35.
            "survivorship": bool(lease_codes) and bool(pool_codes & matured_bills),
            "universe_valid": (gold_info["coin_fresh_share"] >= COVERED_SHARE and
                               gold_info["fund_sessions_with_value_share"] >= COVERED_SHARE),
            "missing_data": not missing and len(carried) <= base.CARRIED_MAX * len(months),
            "price_sanity": (traded > 0 and len(rejected) <= base.REJECTED_MAX * traded
                             and worst_month < 1.0),
        }

    if missing:
        stage1 = stage_one(0.0)
        result = {"stage_1": stage1, "missing_months": ["%d-%02d" % k for k in missing],
                  "verdict": {"stage_1": "FAIL", "note": "a month has no yield"}}
        _write(result)
        if manager:
            _close(manager, stage1, result)
        return result

    frame, is_session = base.legs(calendar, index, gold, yields)
    daily = {name: base.book(frame, is_session, w) for name, w in base.BOOKS.items()}
    fixed = frame["b"]
    m_fixed = base.monthly(fixed).to_numpy()
    m = {name: base.monthly(d).to_numpy() for name, d in daily.items()}
    excess = {name: v - m_fixed for name, v in m.items()}
    stage1 = stage_one(max(float(np.max(np.abs(v))) for v in m.values()))

    def sharpe_diff(a, b):
        return base.sharpe(a) - base.sharpe(b)

    def mean(x):
        return float(np.mean(x))

    def three(block_months):
        _configure_base(block_months)
        return {
            "H1_gold_vs_fixed_income": base.bootstrap_p(
                sharpe_diff, (excess["eq70_gold30"], excess["eq70_bills30"])),
            "H2_equity_vs_fixed_income": base.bootstrap_p(
                mean, (excess["equity_100"],)),
            "H3_three_part_vs_gold": base.bootstrap_p(
                sharpe_diff, (excess["eq70_gold15_bills15"], excess["eq70_gold30"]),
                two_sided=True),
        }

    tests = three(BLOCK_MONTHS)
    with_e35_blocks = three(E35_BLOCK_MONTHS)
    _configure_base(BLOCK_MONTHS)
    family = base.holm({k: v["p"] for k, v in tests.items()})

    z1, p1m, _sa, _sb, rho = memmel(pd.Series(excess["eq70_gold30"]),
                                    pd.Series(excess["eq70_bills30"]))

    def periods(label_start, label_end):
        return {name: _slice_describe(d, fixed, label_start, label_end)
                for name, d in daily.items()}

    by_year = {}
    for k, r in yields.items():
        by_year.setdefault(k[0], []).append(r["yield"])

    result = {
        "preregistration": "backtest_output/E36_preregistration.json",
        "window_months": ["%d-%02d" % months[0], "%d-%02d" % months[-1]],
        "months": len(months),
        "sessions": len(sessions),
        "gold": gold_info,
        "fixed_income": {
            "carried_months": len(carried),
            "quotes_in_window": len(quotes_in),
            "rejected_quotes_in_window": len(rejected),
            "lease_series_used": len(lease_codes),
            "bill_series_used": len(pool_codes - lease_codes),
            "mean_yield_by_year_pct": {str(y): round(float(np.mean(v)) * 100, 2)
                                       for y, v in sorted(by_year.items())},
            "cagr_pct": base.describe(fixed, None, fixed)["cagr_pct"],
            "series": [{"month": "%d-%02d" % k, "yield_pct": round(r["yield"] * 100, 2),
                        "quotes": r["quotes"], "basis": r["basis"],
                        "source": "lease" if date(k[0], k[1], 1) < SPLICE else "bills"}
                       for k, r in sorted(yields.items())],
        },
        "books": {name: base.describe(d, None, fixed) for name, d in daily.items()},
        "cycles": {
            "2011-08..2018-09": periods(START, date(2018, 9, 30)),
            "2018-10..2026-07": periods(CYCLE_SPLIT, END),
        },
        "eras": {
            "2011-08..2017-09 (coin, lease bonds)": periods(START, date(2017, 9, 30)),
            "2017-10..2026-07 (E35's window)": periods(SPLICE, END),
        },
        "stage_1": stage1,
        "tests": dict({k: dict(v, sided="two" if k.startswith("H3") else "one")
                       for k, v in tests.items()}, holm=family),
        "reported_not_tested": {
            "with_e35_6_month_blocks": {k: v["p"] for k, v in with_e35_blocks.items()},
            "memmel_monthly_H1": {"z": round(z1, 3), "p_one_sided": round(p1m, 5),
                                  "rho": round(rho, 3)},
            "newey_west_H2_lag12": base.newey_west_t(excess["equity_100"],
                                                     lag=NEWEY_WEST_LAG),
            "mean_annualised_equity_excess_pct": round(
                ((1 + float(np.mean(excess["equity_100"]))) ** 12 - 1) * 100, 2),
        },
    }

    if not all(stage1.values()):
        result["verdict"] = {"stage_1": "FAIL", "note": "the experiment says nothing"}
    else:
        result["verdict"] = {k: "ACCEPT" if family[k]["null_rejected"] else "NOT ACCEPTED"
                             for k in tests}
        result["verdict"]["family"] = "allocation; Holm at 0.05 over H1-H3, 12-month blocks"

    _write(result)
    if manager:
        _close(manager, stage1, result)
    return result


def _write(result):
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


def _close(manager, stage1, result):
    from sirat.experiments import manager as mgr

    detail = {"fixed_income": {k: v for k, v in (result.get("fixed_income") or {}).items()
                               if k != "series"},
              "gold": result.get("gold"), "months": result.get("months")}
    manager.run_stage(1, stage1, detail=detail)
    by = "SIRAT-AI Research (%s script)" % EXPERIMENT_ID
    if not all(stage1.values()):
        out = manager.decide(mgr.REJECT, "Stage 1 failed: %s. The preregistration "
                             "says the experiment then says nothing." %
                             ", ".join(k for k, v in stage1.items() if not v), by=by)
    else:
        v, t = result["verdict"], result["tests"]
        out = manager.decide(
            mgr.ARCHIVE,
            "Allocation over fifteen years (2011-08..2026-07), closed as the "
            "preregistration's decision rule says (ARCHIVE). H1 gold vs fixed "
            "income %s (p %.4f); H2 equity vs fixed income %s (p %.4f); H3 "
            "three-part vs gold %s (p %.4f); Holm at 0.05, 12-month blocks. "
            "Results: backtest_output/E36_results.json."
            % (v["H1_gold_vs_fixed_income"], t["H1_gold_vs_fixed_income"]["p"],
               v["H2_equity_vs_fixed_income"], t["H2_equity_vs_fixed_income"]["p"],
               v["H3_three_part_vs_gold"], t["H3_three_part_vs_gold"]["p"]), by=by)
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
        if "fixed_income" in printable:
            printable["fixed_income"] = {k: v for k, v in printable["fixed_income"].items()
                                         if k != "series"}
        print(json.dumps(printable, ensure_ascii=False, indent=1))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
