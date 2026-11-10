"""E32: would buying the best forward-looking fundamentals have paid?

Preregistered in backtest_output/E32_preregistration.json before the first
run. Eligibility, execution, costs and dates are E30's, imported from
examples/outlook_backtest.py; only the ranking differs -- the quality and
direction of the business, with no price in it.

    python examples/quality_backtest.py            # the preregistered run
    python examples/quality_backtest.py --smoke    # eligibility counts only
"""

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import backtest_config  # noqa: E402
import config  # noqa: E402
import fundamental_outlook as fo  # noqa: E402
import outlook_backtest as base  # noqa: E402
from backtesting.execution import ExecutionModel  # noqa: E402
from backtesting.pit import PointInTimeData  # noqa: E402

ALPHA = 0.05 / 3
COMPONENTS = ("forward_profit_growth", "sales_growth", "margin_change",
              "cash_conversion")
RESULTS = os.path.join(os.path.dirname(HERE), "backtest_output", "E32_results.json")


def components(result):
    ttm = result["ttm"]["profit"]
    fwd = result["forward"]["profit"]
    margins = result["margins"]
    return {
        "forward_profit_growth": fwd / ttm - 1 if ttm and ttm > 0 and fwd is not None
        else None,
        "sales_growth": result["growth"]["revenue_pct"],
        "margin_change": (margins["net_pct"] - margins["net_prev_pct"])
        if margins["net_pct"] is not None and margins["net_prev_pct"] is not None
        else None,
        "cash_conversion": result["cash"]["cash_conversion"],
    }


def eligible_on(data, decision):
    """{code: components} for E30's eligible set on `decision`."""

    out = {}
    for code, inst in data.instruments().items():
        if inst["sector"] in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            continue
        if data.bar_on(code, decision) is None:
            continue
        if (data.average_value(code, decision, sessions=20) or 0) < base.MIN_VALUE:
            continue
        history = data.fundamental_history(code, decision, periods=12)
        if not history:
            continue
        newest = max(history, key=lambda r: (str(r["period_end"]),
                                             r["period_months"] or 0))
        result = fo.analyse(
            code, price=data.valuation_price(code, decision),
            rows=base._rows(history), today=base._jalali(decision),
            monthly_rows=[], dividend=None,
            shares=base._shares(data, code, newest, decision),
            sector=inst["sector"], portfolio=None, opportunity=base.NO_HURDLE)
        if not result or result["kind"] != fo.OPERATING:
            continue
        age = result["latest"]["age_months"]
        if age is not None and age > fo.STALE_MONTHS:
            continue
        ey = result["valuation"]["earnings_yield_forward_pct"]
        if ey is None or result["method"] == "annualised" or ey <= 0:
            continue
        if fo.suspect_reason(result) or len(result["flags"]) > base.MAX_FLAGS:
            continue
        out[code] = components(result)
    return out


def ranks(values):
    """Percentile rank 0..1 of each present value; missing -> 0.5."""

    present = sorted((v, c) for c, v in values.items() if v is not None)
    n = len(present)
    out = {c: 0.5 for c in values}
    for i, (_, c) in enumerate(present):
        out[c] = i / (n - 1) if n > 1 else 0.5
    return out


def ranking(table, keys=COMPONENTS):
    """[(score, code)] best first: the mean of the components' ranks."""

    per = [ranks({c: comp[k] for c, comp in table.items()}) for k in keys]
    scored = [(float(np.mean([r[c] for r in per])), c) for c in table]
    scored.sort(reverse=True)
    return scored


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)

    data = PointInTimeData(backtest_config.BacktestConfig(), verbose=False)
    execution = ExecutionModel(backtest_config.BacktestConfig())
    sessions = data.sessions()
    decisions = data.rebalance_dates(base.START, base.END, base.EVERY)

    if args.smoke:
        for d in (decisions[0], decisions[len(decisions) // 2], decisions[-1]):
            table = eligible_on(data, d)
            filled = {k: sum(1 for v in table.values() if v[k] is not None)
                      for k in COMPONENTS}
            print(d.date(), "eligible", len(table), "components present", filled,
                  flush=True)
        return 0

    tables = {}
    for n, d in enumerate(decisions, 1):
        tables[d] = eligible_on(data, d)
        if n % 10 == 0:
            print("  %d/%d" % (n, len(decisions)), flush=True)

    def run(keys, top):
        ranked = {d: ranking(t, keys) for d, t in tables.items()}
        periods = base.period_returns(data, execution, sessions, decisions, ranked)
        return periods, base.evaluate(periods, top)

    periods, primary = run(COMPONENTS, base.TOP)
    null = base.random_null(periods, base.TOP, base.NULL_DRAWS, base.SEED)
    gross = base.cumulative(primary["gross"])
    percentile = 100.0 * sum(1 for x in null if x < gross) / len(null)

    accept = (primary["p_one_sided"] < ALPHA and percentile >= 90
              and primary["first_half_mean_excess_pct"] > 0
              and primary["second_half_mean_excess_pct"] > 0)

    strip = lambda r: {k: v for k, v in r.items()  # noqa: E731
                       if k not in ("gross", "net", "control_net")}
    diagnostics = {"top_20": strip(run(COMPONENTS, 20)[1])}
    for key in COMPONENTS:
        diagnostics[key + "_alone"] = strip(run((key,), base.TOP)[1])

    index_returns = base.tedpix(data, periods)
    result = {
        "experiment_id": "E32-forward-fundamental-quality",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "alpha": round(ALPHA, 4),
        "decision": "ACCEPT" if accept else "REJECT",
        "primary": strip(primary),
        "random_null": {"strategy_gross_pct": round(gross * 100, 1),
                        "percentile": round(percentile, 1),
                        "null_median_pct": round(float(np.median(null)) * 100, 1)},
        "tedpix_annualised_pct": round((base.annualised(index_returns, periods) or 0) * 100, 1),
        "quintiles_mean_period_return_pct_best_first": base.quintiles(periods),
        "diagnostics_not_decisive": diagnostics,
        "pit_blocked_future_reads": data.blocked_future_reads,
    }
    with open(RESULTS, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
