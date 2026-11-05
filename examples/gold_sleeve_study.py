"""E28: does a gold sleeve improve on E27's equity + fixed-income allocation?

Implements backtest_output/E28_preregistration.json exactly; read that first.

    python examples/gold_sleeve_study.py
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for path in (ROOT, os.path.join(ROOT, "examples")):
    if path not in sys.path:
        sys.path.insert(0, path)

import backtest_config                                          # noqa: E402
import database                                                 # noqa: E402
import gold                                                     # noqa: E402
from allocation_study import (FUND_RATE, HOLDING_DRAG, ROUND_TRIP,  # noqa: E402
                              blocks, memmel, per_session, series, stats_of,
                              t_sf)

MIN_HISTORY = 60
BAD_RETURN = 0.20
REBALANCE = 60
ALPHA = 0.025
END = "2026-11-04"

OUT = os.path.join(backtest_config.OUTPUT_DIR, "E28_results.json")


def gold_sleeve(calendar):
    closes = {}
    for code in gold.gold_fund_codes():
        frame = database.load_history_by_inscode(code)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        closes[code] = (frame.drop_duplicates("date").set_index("date")["close"]
                        .astype(float))

    panel = pd.DataFrame(closes).reindex(calendar)
    returns = panel.pct_change(fill_method=None)

    bad = returns.abs() > BAD_RETURN
    dropped = int(bad.sum().sum())
    returns = returns.mask(bad)

    seasoned = panel.notna().cumsum() >= MIN_HISTORY
    returns = returns.where(seasoned)

    sleeve = returns.mean(axis=1)            # NaN where no fund qualifies
    first = sleeve.first_valid_index()

    return sleeve, first, dropped, int(seasoned.iloc[-1].sum())


def static_mix(legs, weights, every=REBALANCE):
    """Daily returns of a book rebalanced to `weights` every `every` sessions."""

    frame = pd.DataFrame(legs)
    target = np.array(weights, dtype=float)
    held = target.copy()
    out = []

    for i, row in enumerate(frame.to_numpy()):
        cost = 0.0
        if i > 0 and i % every == 0:
            cost = np.abs(held - target).sum() * ROUND_TRIP / 2
            held = target.copy()
        grown = held * (1 + row)
        value = grown.sum()
        out.append(value * (1 - cost) - 1)
        held = grown / value

    return pd.Series(out, index=frame.index)


def run():
    index = series("TEDPIX")
    calendar = index.index

    sleeve, first, dropped, funds_at_end = gold_sleeve(calendar)

    window = (calendar >= first) & (calendar <= pd.Timestamp(END))
    dates = calendar[window]

    equity = ((1 + index.pct_change()[window].fillna(0.0))
              * (1 - per_session(HOLDING_DRAG)) - 1)
    gold_r = sleeve[window].fillna(0.0)
    fund = pd.Series(per_session(FUND_RATE), index=dates)

    books = {
        "equity_100": equity,
        "gold_100": gold_r,
        "eq70_gold30": static_mix({"e": equity, "g": gold_r}, (0.7, 0.3)),
        "eq70_fund30": static_mix({"e": equity, "f": fund}, (0.7, 0.3)),
        "eq50_gold50": static_mix({"e": equity, "g": gold_r}, (0.5, 0.5)),
        "eq50_fund50": static_mix({"e": equity, "f": fund}, (0.5, 0.5)),
    }

    m = {k: blocks(v) for k, v in books.items()}
    m_fund = blocks(fund)

    z1, p1, sr_gold, sr_fund, rho = memmel(m["eq70_gold30"] - m_fund,
                                           m["eq70_fund30"] - m_fund)

    diff = m["gold_100"] - m_fund
    t2 = diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))
    p2 = t_sf(t2, len(diff) - 1)

    out = {
        "preregistration": "backtest_output/E28_preregistration.json",
        "range": [str(dates[0].date()), str(dates[-1].date())],
        "sessions": int(len(dates)),
        "blocks": int(len(m_fund)),
        "gold_funds_in_sleeve_at_end": funds_at_end,
        "returns_dropped_as_errors": dropped,
        "corr_equity_gold_blocks": round(float(np.corrcoef(
            m["equity_100"], m["gold_100"])[0, 1]), 3),
        "books": {k: stats_of(v, fund) for k, v in books.items()},
        "fund_20pct": stats_of(fund, fund),
        "H1_test": {"z_memmel": round(z1, 3), "p_one_sided": round(p1, 5),
                    "monthly_sharpe_eq70_gold30": round(sr_gold, 4),
                    "monthly_sharpe_eq70_fund30": round(sr_fund, 4),
                    "rho": round(rho, 3)},
        "H2_test": {"t": round(float(t2), 3), "p_one_sided": round(p2, 5),
                    "mean_block_excess_pct": round(float(diff.mean()) * 100, 3)},
    }

    out["verdict"] = {
        "H1_gold_as_defence": "ACCEPT" if p1 < ALPHA else "REJECT",
        "H2_gold_alone": "ACCEPT" if p2 < ALPHA else "REJECT",
        "alpha_per_hypothesis": ALPHA,
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    run()
