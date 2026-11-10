"""E31: does a cheap, sound laggard whose retail selling has stopped beat the market?

Preregistered in backtest_output/E31_preregistration.json before the first
run. Eligibility, execution, costs and dates are E30's, imported from
examples/outlook_backtest.py so the two cannot drift apart; this file adds the
three conditions and the decision.

    python examples/laggard_backtest.py            # the preregistered run
    python examples/laggard_backtest.py --smoke    # basket sizes only
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import backtest_config  # noqa: E402
import outlook_backtest as base  # noqa: E402
from backtesting.execution import ExecutionModel  # noqa: E402
from backtesting.pit import PointInTimeData  # noqa: E402

LOOKBACK = 60            # sessions of price and of retail flow
RECENT = 5               # the last sessions in which selling must have stopped
MIN_NAMES = 3
CAP = 10
RESULTS = os.path.join(os.path.dirname(HERE), "backtest_output", "E31_results.json")


def _trailing_return(frame):
    if frame is None or len(frame) < LOOKBACK + 1:
        return None
    a, b = float(frame["close"].iloc[0]), float(frame["close"].iloc[-1])
    return b / a - 1 if a > 0 else None


def _net_share(rows):
    if rows is None or rows.empty:
        return None
    turnover = float(rows["individual_buy_value"].sum()
                     + rows["institutional_buy_value"].sum())
    if turnover <= 0:
        return None
    return float(rows["individual_buy_value"].sum()
                 - rows["individual_sell_value"].sum()) / turnover


def _third(values, low=True):
    ordered = sorted(values)
    return ordered[max(0, len(ordered) // 3 - 1)] if low else None


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    return None if not n else (ordered[n // 2] if n % 2
                               else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0)


def conditions(data, decision, ranked, index_return):
    """{code: {"F", "L", "S"}} for every eligible company on `decision`."""

    eys = {code: ey for ey, code in ranked}
    rets, early, late = {}, {}, {}
    for code in eys:
        rets[code] = _trailing_return(
            data.history_until(code, decision, bars=LOOKBACK + 1))
        flow = data.flow_until(code, decision, sessions=LOOKBACK)
        if flow is not None and len(flow) >= LOOKBACK:
            early[code] = _net_share(flow.iloc[:-RECENT])
            late[code] = _net_share(flow.iloc[-RECENT:])

    ey_median = _median(list(eys.values()))
    ret_cut = _third([v for v in rets.values() if v is not None])
    early_cut = _median([v for v in early.values() if v is not None])
    late_median = _median([v for v in late.values() if v is not None])

    out = {}
    for code, ey in eys.items():
        r, e, l = rets.get(code), early.get(code), late.get(code)
        out[code] = {
            "F": ey_median is not None and ey >= ey_median,
            "L": (r is not None and ret_cut is not None and r <= ret_cut
                  and index_return is not None and r < index_return),
            "S": (e is not None and l is not None and early_cut is not None
                  and late_median is not None and e <= early_cut
                  and l >= late_median),
        }
    return out


def index_trailing(data, decision):
    frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)
    upto = frame[frame["date"] <= decision]
    if len(upto) < LOOKBACK + 1:
        return None
    a, b = float(upto["close"].iloc[-LOOKBACK - 1]), float(upto["close"].iloc[-1])
    return b / a - 1


def run_book(periods, chooser, min_names=MIN_NAMES):
    """Per-period gross/net of the chosen names; the control when too few."""

    held = set()
    gross, net, real = [], [], []
    for p in periods:
        wanted = chooser(p)
        names = p["names"]
        chosen = [x for x in names if wanted is None or x[1] in wanted]
        is_real = wanted is not None and len(chosen) >= min_names
        if wanted is not None and not is_real:
            chosen = names
        chosen = chosen[:CAP] if is_real else chosen
        if not chosen:
            gross.append(0.0)
            net.append(0.0)
            real.append(False)
            held = set()
            continue
        g = float(np.mean([r for _, _, r in chosen]))
        codes = {c for _, c, _ in chosen}
        changed = 1.0 - len(codes & held) / float(len(codes))
        gross.append(g)
        net.append((1 + g) * (1 - changed * base.ROUND_TRIP) - 1)
        real.append(is_real)
        held = codes
    return gross, net, real


def compare(periods, chooser, label):
    gross, net, real = run_book(periods, chooser)
    _, control, _ = run_book(periods, lambda p: None)
    excess = [math.log1p(a) - math.log1p(b) for a, b in zip(net, control)]
    half = len(excess) // 2
    mean, t, p = base.newey_west_t(excess)
    return {
        "label": label, "real_basket_periods": int(sum(real)),
        "cumulative_net_pct": round(base.cumulative(net) * 100, 1),
        "annualised_net_pct": round((base.annualised(net, periods) or 0) * 100, 1),
        "control_cumulative_net_pct": round(base.cumulative(control) * 100, 1),
        "control_annualised_net_pct": round((base.annualised(control, periods) or 0) * 100, 1),
        "excess_log_per_period_pct": round(mean * 100, 3),
        "newey_west_t": round(t, 2), "p_one_sided": round(p, 4),
        "first_half_mean_excess_pct": round(float(np.mean(excess[:half])) * 100, 3),
        "second_half_mean_excess_pct": round(float(np.mean(excess[half:])) * 100, 3),
        "_gross": gross, "_real": real,
    }


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
            flags = conditions(data, d, base.ranked_on(data, d), index_trailing(data, d))
            print(d.date(), "eligible", len(flags), "F+L+S",
                  sum(1 for f in flags.values() if f["F"] and f["L"] and f["S"]),
                  flush=True)
        return 0

    ranked, flags = {}, {}
    for n, d in enumerate(decisions, 1):
        ranked[d] = base.ranked_on(data, d)
        flags[d] = conditions(data, d, ranked[d], index_trailing(data, d))
        if n % 10 == 0:
            print("  %d/%d" % (n, len(decisions)), flush=True)

    periods = base.period_returns(data, execution, sessions, decisions, ranked)

    def pick(*keys):
        return lambda p: {c for c, f in flags[p["decision"]].items()
                          if all(f[k] for k in keys)}

    primary = compare(periods, pick("F", "L", "S"), "F+L+S")

    # Null: in each real-basket period, random baskets of the strategy's size.
    sizes = []
    for p, real in zip(periods, primary["_real"]):
        wanted = pick("F", "L", "S")(p)
        sizes.append(min(CAP, len([x for x in p["names"] if x[1] in wanted]))
                     if real else 0)
    rng = random.Random(base.SEED)
    strategy_gross = float(np.prod([1 + g for g, real in
                                    zip(primary["_gross"], primary["_real"]) if real]) - 1)
    null = []
    for _ in range(base.NULL_DRAWS):
        total = 1.0
        for p, size in zip(periods, sizes):
            if size:
                pick_ = rng.sample(p["names"], min(size, len(p["names"])))
                total *= 1 + float(np.mean([r for _, _, r in pick_]))
        null.append(total - 1)
    percentile = 100.0 * sum(1 for x in null if x < strategy_gross) / len(null)

    accept = (primary["real_basket_periods"] >= 24 and primary["p_one_sided"] < 0.05
              and percentile >= 90 and primary["first_half_mean_excess_pct"] > 0
              and primary["second_half_mean_excess_pct"] > 0)

    diagnostics = [compare(periods, pick(*k), "+".join(k))
                   for k in (("L",), ("L", "F"), ("L", "S"), ("F",))]

    clean = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}  # noqa: E731
    index_returns = base.tedpix(data, periods)
    result = {
        "experiment_id": "E31-laggard-quality-flow-turn",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "decision": "ACCEPT" if accept else "REJECT",
        "primary": clean(primary),
        "random_null": {"strategy_gross_real_periods_pct": round(strategy_gross * 100, 1),
                        "percentile": round(percentile, 1),
                        "null_median_pct": round(float(np.median(null)) * 100, 1)},
        "tedpix_annualised_pct": round((base.annualised(index_returns, periods) or 0) * 100, 1),
        "diagnostics_not_decisive": [clean(r) for r in diagnostics],
        "basket_sizes": sizes,
        "pit_blocked_future_reads": data.blocked_future_reads,
    }
    with open(RESULTS, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
