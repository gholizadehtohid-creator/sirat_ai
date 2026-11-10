"""E30: would trading the fundamental outlook's top picks have paid?

Preregistered in backtest_output/E30_preregistration.json before the first
run. Every rule below is that file's; nothing here is a parameter to tune.

Each decision session D, every 21 sessions: rebuild the outlook's ranking from
the filings that were public by D's close (backtesting/pit.py), price it at
D's printed close, buy the ten highest forward earnings yields at the close of
D+1 and hold them to the next decision's D+1. Compared with holding every
eligible company (the control that isolates the ranking), TEDPIX, bills, and
1,000 random ten-name books drawn from the same list.

    python examples/outlook_backtest.py            # the preregistered run
    python examples/outlook_backtest.py --smoke    # eligibility counts only
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backtest_config  # noqa: E402
import config  # noqa: E402
import fundamental_outlook as fo  # noqa: E402
import jalali  # noqa: E402
from backtesting.execution import BUY_QUEUE, ExecutionModel  # noqa: E402
from backtesting.pit import PointInTimeData  # noqa: E402

START, END = "2021-06-01", "2026-10-31"
EVERY = 21
TOP = 10
MIN_VALUE = 5e9                     # rials of 20-session average traded value
MAX_FLAGS = 1
ROUND_TRIP = 1.039 / 100.0
BILLS = 0.30
NULL_DRAWS = 1000
SEED = 30
SENSITIVITY_TOPS = (5, 20)
SENSITIVITY_EVERY = 3               # every third decision: a 63-session cadence

# The bill-yield flag is off: there is no historical bill series (prereg).
NO_HURDLE = {"rate_pct": 0.0, "as_of": "-", "source": "E30: disabled"}

RESULTS = os.path.join(ROOT, "backtest_output", "E30_results.json")


def _jalali(ts):
    return jalali.today_jalali(ts.date())


def _gregorian(period_end):
    try:
        jy, jm, jd = (int(x) for x in str(period_end).split("/")[:3])
        return datetime(*jalali.to_gregorian(jy, jm, jd))
    except (TypeError, ValueError):
        return None


def _factor(data, code, when):
    """Back-adjusted close over printed close at `when`: 1 after the last
    capital increase, the cumulative ratio before it."""

    frame = data.bars(code)
    i = data.position(code, when)
    if frame is None or i < 0:
        return None
    raw = float(frame["raw_close"].iloc[i] or 0)
    return float(frame["close"].iloc[i]) / raw if raw > 0 else None


def _shares(data, code, newest, decision):
    """Capital/par of the newest filing, carried across later bonus issues."""

    capital = fo._f((newest.get("items") or {}).get("capital"))
    if not capital:
        return None
    shares = capital / fo.PAR_VALUE
    then = _gregorian(newest.get("period_end"))
    f_then = _factor(data, code, then) if then else None
    f_now = _factor(data, code, decision)
    if f_then and f_now:
        shares *= f_now / f_then
    return shares


def _rows(history):
    return [{"period_end": r["period_end"], "period_months": r["period_months"],
             "items": r.get("items") or {}, "ratios": r.get("ratios") or {},
             "published_at": r.get("published_at"), "audited": r.get("audited"),
             "consolidated": r.get("consolidated")} for r in reversed(history)]


def ranked_on(data, decision):
    """[(earnings yield %, code)] of the eligible companies, best first."""

    out = []
    for code, inst in data.instruments().items():
        if inst["sector"] in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            continue
        if data.bar_on(code, decision) is None:
            continue
        if (data.average_value(code, decision, sessions=20) or 0) < MIN_VALUE:
            continue
        history = data.fundamental_history(code, decision, periods=12)
        if not history:
            continue
        newest = max(history, key=lambda r: (str(r["period_end"]),
                                             r["period_months"] or 0))
        result = fo.analyse(
            code, price=data.valuation_price(code, decision), rows=_rows(history),
            today=_jalali(decision), monthly_rows=[], dividend=None,
            shares=_shares(data, code, newest, decision), sector=inst["sector"],
            portfolio=None, opportunity=NO_HURDLE)
        if not result or result["kind"] != fo.OPERATING:
            continue
        age = result["latest"]["age_months"]
        if age is not None and age > fo.STALE_MONTHS:
            continue
        ey = result["valuation"]["earnings_yield_forward_pct"]
        if ey is None or result["method"] == "annualised" or ey <= 0:
            continue
        if fo.suspect_reason(result) or len(result["flags"]) > MAX_FLAGS:
            continue
        out.append((ey, code))
    out.sort(reverse=True)
    return out


_POSITION = {}


def _index(sessions, day):
    if not _POSITION:
        _POSITION.update({s: i for i, s in enumerate(sessions)})
    return _POSITION[day]


def entry(data, execution, sessions, code, decision):
    """Adjusted close at D+1, or None when it cannot be bought then."""

    i = _index(sessions, decision)
    if i + 1 >= len(sessions):
        return None
    bar = data.bar_on(code, sessions[i + 1])
    if bar is None:
        return None
    before = data.bar_on(code, decision)
    state = execution.queue_state(bar, before["close"] if before is not None
                                  else None, data.median_volume(code, decision))
    return None if state == BUY_QUEUE else float(bar["close"])


def mark(data, code, when):
    """Adjusted close on or before `when` (the last trade of a halted name)."""

    frame = data.history_until(code, when, bars=1)
    return None if frame is None or frame.empty else float(frame["close"].iloc[-1])


def holding_return(data, code, start_price, exit_session):
    end = mark(data, code, exit_session)
    return end / start_price - 1.0 if end and start_price else None


def period_returns(data, execution, sessions, decisions, ranked):
    """Per decision: every eligible, buyable name's return to the next exit.

    Shared by the strategy, the control and the null, so they differ only in
    which names they hold.
    """

    periods = []
    for k in range(len(decisions) - 1):
        d, nxt = decisions[k], decisions[k + 1]
        j = _index(sessions, nxt) + 1
        if j >= len(sessions):
            break
        exit_session = sessions[j]
        names = []
        for ey, code in ranked[d]:
            price = entry(data, execution, sessions, code, d)
            if price is None:
                continue
            r = holding_return(data, code, price, exit_session)
            if r is not None and math.isfinite(r):
                names.append((ey, code, r))
        start = sessions[_index(sessions, d) + 1]
        periods.append({"decision": d, "start": start, "exit": exit_session,
                        "names": names})
    return periods


def book(periods, top=None):
    """Gross and net per-period returns of the top-N (or all) names."""

    held_before = set()
    gross, net, counts = [], [], []
    for p in periods:
        chosen = p["names"][:top] if top else p["names"]
        if not chosen:
            gross.append(0.0)
            net.append(0.0)
            counts.append(0)
            held_before = set()
            continue
        g = float(np.mean([r for _, _, r in chosen]))
        codes = {c for _, c, _ in chosen}
        changed = 1.0 - len(codes & held_before) / float(len(codes))
        gross.append(g)
        net.append((1 + g) * (1 - changed * ROUND_TRIP) - 1)
        counts.append(len(chosen))
        held_before = codes
    return gross, net, counts


def cumulative(returns):
    return float(np.prod([1 + r for r in returns]) - 1)


def annualised(returns, periods):
    days = (periods[-1]["exit"] - periods[0]["start"]).days
    total = np.prod([1 + r for r in returns])
    return float(total ** (365.0 / days) - 1) if days > 0 and total > 0 else None


def newey_west_t(x, lags=2):
    x = np.asarray(x, dtype=float)
    n = len(x)
    mean = x.mean()
    e = x - mean
    var = (e @ e) / n
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1.0)
        var += 2 * w * (e[lag:] @ e[:-lag]) / n
    se = math.sqrt(var / n) if var > 0 else float("nan")
    t = mean / se if se and se == se else float("nan")
    # One-sided p from the normal: n is ~60, close enough to Student's t.
    p = 0.5 * math.erfc(t / math.sqrt(2)) if t == t else float("nan")
    return mean, t, p


def tedpix(data, periods):
    frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)
    series = dict(zip(frame["date"], frame["close"].astype(float)))
    out = []
    for p in periods:
        a, b = series.get(p["start"]), series.get(p["exit"])
        out.append(b / a - 1 if a and b else 0.0)
    return out


def bills(periods):
    return [(1 + BILLS) ** ((p["exit"] - p["start"]).days / 365.0) - 1
            for p in periods]


def random_null(periods, top, draws, seed):
    rng = random.Random(seed)
    totals = []
    for _ in range(draws):
        total = 1.0
        for p in periods:
            pool = p["names"]
            if not pool:
                continue
            pick = rng.sample(pool, min(top, len(pool)))
            total *= 1 + float(np.mean([r for _, _, r in pick]))
        totals.append(total - 1)
    return totals


def quintiles(periods):
    """Mean per-period return of each fifth of the ranking, best first."""

    sums = [[] for _ in range(5)]
    for p in periods:
        names = p["names"]
        if len(names) < 10:
            continue
        size = len(names) / 5.0
        for q in range(5):
            part = names[int(q * size):int((q + 1) * size)]
            sums[q].append(float(np.mean([r for _, _, r in part])))
    return [round(float(np.mean(s)) * 100, 2) if s else None for s in sums]


def evaluate(periods, top):
    gross, net, counts = book(periods, top)
    control_gross, control_net, control_counts = book(periods)
    excess = [math.log1p(a) - math.log1p(b) for a, b in zip(net, control_net)]
    half = len(excess) // 2
    mean, t, p = newey_west_t(excess)
    return {
        "top": top, "periods": len(periods),
        "strategy": {"cumulative_net_pct": round(cumulative(net) * 100, 1),
                     "annualised_net_pct": round((annualised(net, periods) or 0) * 100, 1),
                     "cumulative_gross_pct": round(cumulative(gross) * 100, 1),
                     "mean_names": round(float(np.mean(counts)), 1)},
        "control_eligible_ew": {"cumulative_net_pct": round(cumulative(control_net) * 100, 1),
                                "annualised_net_pct": round((annualised(control_net, periods) or 0) * 100, 1),
                                "mean_names": round(float(np.mean(control_counts)), 1)},
        "excess_log_per_period_pct": round(mean * 100, 3),
        "newey_west_t": round(t, 2), "p_one_sided": round(p, 4),
        "first_half_mean_excess_pct": round(float(np.mean(excess[:half])) * 100, 3),
        "second_half_mean_excess_pct": round(float(np.mean(excess[half:])) * 100, 3),
        "beat_control_share_of_periods": round(float(np.mean([e > 0 for e in excess])), 3),
        "gross": gross, "net": net, "control_net": control_net,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true",
                        help="print eligibility counts on three dates and exit")
    args = parser.parse_args(argv)

    data = PointInTimeData(backtest_config.BacktestConfig(), verbose=False)
    execution = ExecutionModel(backtest_config.BacktestConfig())
    sessions = data.sessions()
    decisions = data.rebalance_dates(START, END, EVERY)

    if args.smoke:
        for d in (decisions[0], decisions[len(decisions) // 2], decisions[-1]):
            print(d.date(), "eligible:", len(ranked_on(data, d)), flush=True)
        return 0

    ranked = {}
    for n, d in enumerate(decisions, 1):
        ranked[d] = ranked_on(data, d)
        if n % 10 == 0:
            print("  ranked %d/%d (%s: %d eligible)" % (n, len(decisions),
                                                         d.date(), len(ranked[d])),
                  flush=True)

    periods = period_returns(data, execution, sessions, decisions, ranked)

    main_result = evaluate(periods, TOP)
    null = random_null(periods, TOP, NULL_DRAWS, SEED)
    strategy_gross = cumulative(main_result["gross"])
    percentile = 100.0 * sum(1 for x in null if x < strategy_gross) / len(null)
    index_returns = tedpix(data, periods)
    bill_returns = bills(periods)

    accept = (main_result["p_one_sided"] < 0.05 and percentile >= 90
              and main_result["first_half_mean_excess_pct"] > 0
              and main_result["second_half_mean_excess_pct"] > 0)

    sensitivity = {"top_%d" % n: {k: v for k, v in evaluate(periods, n).items()
                                  if k not in ("gross", "net", "control_net")}
                   for n in SENSITIVITY_TOPS}
    slow = decisions[::SENSITIVITY_EVERY]
    slow_periods = period_returns(data, execution, sessions, slow, ranked)
    sensitivity["every_63_sessions"] = {
        k: v for k, v in evaluate(slow_periods, TOP).items()
        if k not in ("gross", "net", "control_net")}

    per_period = [{"decision": str(p["decision"].date()), "exit": str(p["exit"].date()),
                   "eligible": len(p["names"]),
                   "top": [data.instruments()[c]["symbol"] for _, c, _ in p["names"][:TOP]],
                   "strategy_net_pct": round(s * 100, 2),
                   "control_net_pct": round(c * 100, 2),
                   "tedpix_pct": round(t * 100, 2)}
                  for p, s, c, t in zip(periods, main_result["net"],
                                        main_result["control_net"], index_returns)]

    result = {
        "experiment_id": "E30-outlook-forward-yield",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "window": [str(periods[0]["start"].date()), str(periods[-1]["exit"].date())],
        "decision": "ACCEPT" if accept else "REJECT",
        "primary": {k: v for k, v in main_result.items()
                    if k not in ("gross", "net", "control_net")},
        "random_null": {"draws": NULL_DRAWS, "strategy_gross_pct": round(strategy_gross * 100, 1),
                        "percentile": round(percentile, 1),
                        "null_median_pct": round(float(np.median(null)) * 100, 1),
                        "null_p90_pct": round(float(np.percentile(null, 90)) * 100, 1)},
        "benchmarks": {
            "tedpix_cumulative_pct": round(cumulative(index_returns) * 100, 1),
            "tedpix_annualised_pct": round((annualised(index_returns, periods) or 0) * 100, 1),
            "bills_30pct_cumulative_pct": round(cumulative(bill_returns) * 100, 1)},
        "quintiles_mean_period_return_pct_best_first": quintiles(periods),
        "sensitivity_not_decisive": sensitivity,
        "pit_blocked_future_reads": data.blocked_future_reads,
        "per_period": per_period,
    }

    with open(RESULTS, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=str)

    print(json.dumps({k: v for k, v in result.items() if k != "per_period"},
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
