"""Is the v2 basket's stock selection better than chance?

Runs the v2 engine over a window, takes every trade it made, then rebuilds the
same book many times with each name swapped for a random stock that a buyer
could have bought on the same day: liquid over the previous 20 sessions
(config.MIN_DAILY_VALUE), traded that day, and not locked (a day with no range
is a limit-lock). Entry day, holding period and weight are kept; only the
choice of stock changes.

Both the real book and the random ones are priced by the SAME simple model --
buy at the entry day's open, sell at the exit day's close, round trip deducted
-- so the difference between them is the selection and nothing else. That is
also why the real book's number here differs from the engine's own P&L, which
fills stops at the stop price and models queues.

    python examples/basket_random_null.py --start 2026-01-01 --end 2026-11-04
    python examples/basket_random_null.py ... --hold 20     same picks, no stop

`--hold N` replaces each exit with "N sessions later": it separates a bad pick
from a good pick stopped out early.

Measured 2026-11-05 on the v2 book before E26 (500 draws): 20th percentile on
2024-09..2025-12 and 2nd on 2026-01..2026-11 with the engine's exits; 55th and
24th held 20 sessions. A percentile is the share of random books the real one
beat -- 50 is chance.

What it does not model: the sector cap (random names ignore it) and delisted
companies (the universe is today's listings, which flatters every book).
"""

import argparse
import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
import config                                                   # noqa: E402
from backtesting import engine as engine_module                 # noqa: E402
from backtesting.pit import PointInTimeData                     # noqa: E402

OUT = os.path.join(backtest_config.OUTPUT_DIR, "basket_null")
SEED = 20261105


def engine_trades(start, end, cfg, data, label, rerun=False):
    """The v2 engine's trades for the window, cached under `label`."""

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "v2_%s_%s_%s.pkl"
                        % (label, start.date(), end.date()))

    if os.path.exists(path) and not rerun:
        with open(path, "rb") as fh:
            return pickle.load(fh)

    started = time.time()
    result = engine_module.run(start, end, cfg=cfg, data=data, verbose=False,
                               engine="v2", use_cache=False)
    print("v2 engine: %.0fs, %d trades" % (time.time() - started,
                                           len(result.trades)))

    saved = {"trades": result.trades, "open": result.open_positions,
             "metrics": result.metrics, "benchmarks": result.benchmarks,
             "diagnostics": result.diagnostics}

    with open(path, "wb") as fh:
        pickle.dump(saved, fh)

    return saved


def panels(data, start):
    calendar = data.calendar()
    calendar = calendar[calendar >= start - pd.Timedelta(days=60)]

    frames = {}
    for code in data.instruments():
        bars = data.bars(code)
        if bars is not None and not bars.empty:
            frames[str(code)] = bars.set_index("date")

    def pivot(column):
        return pd.DataFrame({c: f[column] for c, f in frames.items()
                             if column in f.columns}).reindex(calendar)

    out = {c: pivot(c) for c in ("open", "close", "high", "low", "volume",
                                 "value")}
    out["liquid"] = out["value"].fillna(0).rolling(20, min_periods=10).mean()
    out["close_ff"] = out["close"].ffill()

    return calendar, out


def random_null(saved, data, cfg, start, end, draws=500, hold=None,
                seed=SEED):

    calendar, p = panels(data, start)
    sessions = calendar[calendar <= end]
    cost = cfg.round_trip_pct() / 100.0

    def trade_return(code, d0, d1):
        if code not in p["open"].columns:
            return np.nan
        entry = p["open"].at[d0, code]
        exit_ = p["close_ff"].at[d1, code]
        if not np.isfinite(entry) or not np.isfinite(exit_) or entry <= 0:
            return np.nan
        return exit_ / entry - 1.0 - cost

    def buyable(day):
        prev = calendar[calendar.get_loc(day) - 1]
        ok = ((p["liquid"].loc[prev] >= config.MIN_DAILY_VALUE)
              & (p["volume"].loc[day] > 0) & p["open"].loc[day].notna()
              & (p["high"].loc[day] > p["low"].loc[day]))
        return ok[ok].index.to_numpy()

    rows = []
    for trade in list(saved["trades"]) + list(saved["open"]):
        d0 = pd.Timestamp(trade["entry_date"])
        d1 = pd.Timestamp(trade.get("exit_date") or sessions[-1])
        if d0 not in sessions or d1 not in sessions:
            continue
        if hold:
            d1 = sessions[min(sessions.get_loc(d0) + hold, len(sessions) - 1)]
        rows.append({"code": str(trade["ins_code"]), "d0": d0, "d1": d1,
                     "weight": float(trade["shares"] * trade["entry_price"])})

    book = pd.DataFrame(rows)

    if book.empty:
        return None

    book["real"] = [trade_return(r.code, r.d0, r.d1) for r in book.itertuples()]
    book = book[book["real"].notna()].reset_index(drop=True)

    weights = book["weight"].to_numpy(float)
    weights = weights / weights.sum()
    real = float(np.dot(weights, book["real"]))

    pools = {d: buyable(d) for d in book["d0"].unique()}
    rng = np.random.default_rng(seed)
    null = []

    for _ in range(draws):
        picks = []
        for r in book.itertuples():
            pool = pools[r.d0]
            value = np.nan
            for _attempt in range(5):
                if len(pool) == 0:
                    break
                value = trade_return(pool[rng.integers(len(pool))], r.d0, r.d1)
                if np.isfinite(value):
                    break
            picks.append(value)
        picks = np.array(picks)
        ok = np.isfinite(picks)
        null.append(float(np.dot(weights[ok], picks[ok]) / weights[ok].sum()))

    null = np.array(null)

    return {
        "trades": len(book),
        "hold": hold or "engine exits",
        "real_weighted_trade_return_pct": round(real * 100, 3),
        "real_hit_rate_pct": round(float((book["real"] > 0).mean() * 100), 1),
        "null_mean_pct": round(float(null.mean() * 100), 3),
        "null_p5_p50_p95_pct": [round(float(np.percentile(null, q) * 100), 3)
                                for q in (5, 50, 95)],
        "percentile_of_real": round(float((null < real).mean() * 100), 1),
        "draws": draws,
        "seed": seed,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--draws", type=int, default=500)
    parser.add_argument("--hold", type=int, default=None,
                        help="hold every pick this many sessions, no stop")
    parser.add_argument("--label", default="current",
                        help="names the cached engine run (e.g. E26)")
    parser.add_argument("--rerun", action="store_true",
                        help="ignore a cached engine run")
    # Overrides for an arm other than the shipped one -- E26's baseline is
    # --v2-stop-atr 2 --v2-min-stop 3 --v2-rebalance 20.
    parser.add_argument("--v2-stop-atr", type=float, default=None)
    parser.add_argument("--v2-min-stop", type=float, default=None)
    parser.add_argument("--v2-rebalance", type=int, default=None)
    args = parser.parse_args(argv)

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    cfg = backtest_config.load()

    if args.v2_stop_atr is not None:
        config.V2_STOP_ATR_MULTIPLE = args.v2_stop_atr
    if args.v2_min_stop is not None:
        config.V2_MIN_STOP_PCT = args.v2_min_stop
    if args.v2_rebalance is not None:
        cfg = cfg.replace(v2_rebalance_sessions=args.v2_rebalance)
    data = PointInTimeData(cfg, verbose=False)

    saved = engine_trades(start, end, cfg, data, args.label, rerun=args.rerun)
    summary = random_null(saved, data, cfg, start, end, draws=args.draws,
                          hold=args.hold)

    benchmarks = saved.get("benchmarks") or {}
    report = {
        "window": [str(start.date()), str(end.date())],
        "label": args.label,
        "arm": {"v2_stop_atr": config.V2_STOP_ATR_MULTIPLE,
                "v2_min_stop_pct": config.V2_MIN_STOP_PCT,
                "v2_rebalance": cfg.v2_rebalance_sessions,
                # engine.run() turns the credit on for v2 from this switch;
                # cfg.credit_idle_cash is the global default and stays off.
                "credit_idle_cash": bool(cfg.credit_idle_cash
                                         or cfg.v2_credit_idle_cash)},
        "strategy_return_pct": benchmarks.get("strategy_return_pct"),
        "benchmarks_pct": {k: v.get("return_pct") for k, v in benchmarks.items()
                           if isinstance(v, dict)},
        "exposure": (saved.get("metrics") or {}).get("exposure"),
        "selection": summary,
    }

    path = os.path.join(OUT, "null_%s_%s_%s%s.json" % (
        args.label, start.date(), end.date(),
        "_hold%d" % args.hold if args.hold else ""))

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
