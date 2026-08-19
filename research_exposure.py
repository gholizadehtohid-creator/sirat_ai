"""Why is SIRAT-AI V2 80% in cash? A controlled experiment, not a change.

THE QUESTION
------------
V2 returned 0.04% while the index returned 171.8%, holding 2.11 of 10 slots and
80.7% cash. Two explanations fit that, and they call for opposite responses:

    too conservative   the picks are fine, the book never owns enough of them
    no alpha           the picks are not good, and owning more of them is worse

This module separates them. It changes NO factor, NO threshold, NO stop and NO
risk budget. It changes only how much of the book the same ranked list is
allowed to occupy, and then reports what that did.

WHAT IT MEASURES
----------------
    funnel      where the 818 names go on a decision date, stage by stage
    supply      how many BUY candidates exist, and what they would weigh
    stress      the same ranking at four exposure ceilings
    regime      the market read as a selector vs as a brake

    python research_exposure.py --stage funnel
    python research_exposure.py --stage stress
    python research_exposure.py --stage regime
"""

import argparse
import json
import os
import sys
import time

import pandas as pd

import backtest_config
import config
import factor_engine
from backtesting import engine as engine_module
from backtesting.pit import PointInTimeData
from backtesting.swing_strategy import SwingStrategy


OUTPUT = os.path.join(backtest_config.OUTPUT_DIR, "research_exposure.json")


# Which gate a decision died at, read off the reason factor_engine.decide()
# recorded. Ordered: decide() returns on the FIRST rule that fires, so the first
# matching key is the binding one.
GATES = (
    ("regime_cash_only", "ورود جدید را مجاز نمی‌کند"),
    ("no_score", "قابل محاسبه نیست"),
    ("no_score", "پوشش"),
    ("technical_gate", "دروازه"),
    ("risk_floor", "امتیاز ریسک"),
    ("fundamental_critical", "هشدار بنیادی"),
    ("below_buy_band", "زیر حد خرید"),
    ("below_hold_band", "زیر حد نگهداری"),
    ("regime_entry_floor", "حداقل"),
)


def classify(signal):
    """The stage one candidate stopped at."""

    action = signal.get("action")

    if action == factor_engine.BUY:
        return "buy"

    reasons = signal.get("reasons") or []

    text = " | ".join(str(r) for r in reasons)

    for name, needle in GATES:
        if needle in text:
            return name

    return "other_%s" % (action or "none")


def write(payload):
    os.makedirs(backtest_config.OUTPUT_DIR, exist_ok=True)
    existing = {}
    if os.path.exists(OUTPUT):
        try:
            with open(OUTPUT, encoding="utf-8") as handle:
                existing = json.load(handle)
        except ValueError:
            existing = {}
    existing.update(payload)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, ensure_ascii=False, indent=1, default=str)


# ==============================
# TASK 1 — THE FUNNEL
# ==============================

def funnel(cfg, data, verbose=True, limit=None):
    """Where the universe goes on each decision date, with an empty book.

    The book is empty on purpose. Handing decide() the real evolving portfolio
    would mix "the strategy found nothing" with "the book had no room", and the
    whole question here is which of those is happening.
    """

    dates = data.rebalance_dates(backtest_config.DEFAULT_START,
                                 data.calendar()[-1], cfg.rebalance_sessions)

    # PointInTimeData.instruments() does not read cfg.universe_limit, so the cap
    # has to reach the strategy directly or a "quick" run is a full one.
    strategy = SwingStrategy(cfg, data, verbose=False, limit=limit)

    equity = cfg.initial_capital

    per_date = []

    totals = {}

    started = time.time()

    for number, date in enumerate(dates, start=1):

        result = strategy.decide(date, positions=[], equity=equity)

        signals = result["signals"]

        counts = {}

        for signal in signals:
            key = classify(signal)
            counts[key] = counts.get(key, 0) + 1

        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value

        buys = result["buys"]

        accepted = result["book"]["accepted"]

        # What the accepted names would weigh, and what ALL the buys would
        # weigh if the book had let every one of them in at its risk-sized
        # value. The gap between these two is the allocator's contribution.
        wanted_value = sum((s.get("position_value") or 0.0) for s in buys)
        taken_value = sum((s.get("allocated_value") or 0.0) for s in accepted)

        rejected = {}

        for row in result["book"]["rejected"]:
            key = row.get("reason_key") or "?"
            rejected[key] = rejected.get(key, 0) + 1

        per_date.append({
            "date": str(date.date()),
            "regime": (result["regime"] or {}).get("regime"),
            "trading_mode": (result["regime"] or {}).get("trading_mode"),
            "entry_floor": (result["regime"] or {}).get("entry_floor"),
            "scored": len(signals),
            "gates": counts,
            "buys": len(buys),
            "accepted": len(accepted),
            "rejected_by": rejected,
            "wanted_pct_of_equity": round(wanted_value / equity * 100, 1),
            "taken_pct_of_equity": round(taken_value / equity * 100, 1),
        })

        if verbose:
            print("  %2d/%d  %s  %-10s scored %3d  buys %3d  taken %2d  "
                  "wanted %5.1f%%  taken %5.1f%%  %.0fs"
                  % (number, len(dates), date.date(),
                     per_date[-1]["trading_mode"], len(signals), len(buys),
                     len(accepted), per_date[-1]["wanted_pct_of_equity"],
                     per_date[-1]["taken_pct_of_equity"],
                     time.time() - started), flush=True)

    scored_total = sum(row["scored"] for row in per_date)

    summary = {
        "dates": len(per_date),
        "universe": len(data.instruments()),
        "scored_total": scored_total,
        "scored_per_date": round(scored_total / max(1, len(per_date)), 1),
        "gates_total": totals,
        "buys_total": sum(row["buys"] for row in per_date),
        "buys_per_date": round(sum(row["buys"] for row in per_date)
                               / max(1, len(per_date)), 1),
        "accepted_total": sum(row["accepted"] for row in per_date),
        "accepted_per_date": round(sum(row["accepted"] for row in per_date)
                                   / max(1, len(per_date)), 1),
        "mean_wanted_pct": round(sum(row["wanted_pct_of_equity"]
                                     for row in per_date)
                                 / max(1, len(per_date)), 1),
        "mean_taken_pct": round(sum(row["taken_pct_of_equity"]
                                    for row in per_date)
                                / max(1, len(per_date)), 1),
        "dates_with_no_buy": sum(1 for row in per_date if not row["buys"]),
    }

    write({"funnel": {"summary": summary, "per_date": per_date}})

    return summary


# ==============================
# TASK 2 — EXPOSURE STRESS
# ==============================

VARIANTS = {
    "baseline": None,
    "exposure_50": 50.0,
    "exposure_75": 75.0,
    "exposure_100": 100.0,
}


def stress(cfg, data, verbose=True):
    """The same ranking at four exposure ceilings.

    The ONLY thing that moves is the portfolio policy's exposure rules --
    target_invested_pct, the cash floor, and whether the budget is spread
    across candidates or taken greedily in rank order. Ranking, stops, targets
    and the 1% risk budget per trade are untouched, so a difference between
    columns is a difference in how much of the same list was owned.
    """

    end = data.calendar()[-1]

    rows = {}

    for name, floor in VARIANTS.items():

        started = time.time()

        result = engine_module.run(
            backtest_config.DEFAULT_START, end, cfg=cfg, data=data,
            verbose=False, engine="v2", use_cache=False,
            exposure_floor_pct=floor,
        )

        rows[name] = _profile(result)

        if verbose:
            print("  %-14s return %8s%%  DD %7s%%  cash %5s%%  positions %4s  "
                  "trades %3s  %.0fs"
                  % (name, rows[name]["total_return_pct"],
                     rows[name]["max_drawdown_pct"],
                     rows[name]["average_cash_pct"],
                     rows[name]["average_positions"],
                     rows[name]["trades"], time.time() - started), flush=True)

        write({"stress": rows})

    return rows


def _profile(result):

    metrics = result.metrics or {}
    trades = metrics.get("trades") or {}
    exposure = metrics.get("exposure") or {}
    drawdown = metrics.get("drawdown") or {}
    diagnostics = result.diagnostics or {}

    turnover = None

    if not result.curve.empty:
        bought = sum((t.get("entry_price") or 0) * (t.get("shares") or 0)
                     for t in (result.trades or []))
        average_equity = float(result.curve["portfolio_value"].mean())
        years = len(result.curve) / float(config.TRADING_DAYS_PER_YEAR)
        if average_equity and years:
            turnover = round(bought / average_equity / years, 2)

    return {
        "available": result.available,
        "total_return_pct": metrics.get("total_return_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "volatility_pct": metrics.get("volatility_pct"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "trades": trades.get("trades"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "profit_factor": trades.get("profit_factor"),
        "average_win_pct": trades.get("average_win_pct"),
        "average_loss_pct": trades.get("average_loss_pct"),
        "turnover_per_year": turnover,
        "average_positions": exposure.get("average_positions"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "average_exposure_pct": (None if exposure.get("average_cash_pct") is None
                                 else round(100.0 - exposure["average_cash_pct"], 2)),
        "sessions_fully_in_cash": exposure.get("sessions_fully_in_cash"),
        "commission": diagnostics.get("commission_paid"),
        "tax": diagnostics.get("tax_paid"),
        "benchmark_pct": (result.benchmarks or {}).get(
            "tedpix", {}).get("return_pct"),
    }


# ==============================
# TASK 6 — THE REGIME, AS SELECTOR VS AS BRAKE
# ==============================

def regime_test(cfg, data, verbose=True):
    """Regime on and off, at a FIXED exposure, so only its selection role moves.

    The regime cannot reorder names -- its Swing Score weight is 0 -- so the only
    ways it can touch a result are the entry floor, the position multiplier and
    the CASH_ONLY veto. All three are exposure. Running it off at the same
    exposure ceiling therefore isolates whether the market read is worth anything
    beyond braking.
    """

    end = data.calendar()[-1]

    rows = {}

    for name, disabled in (("regime_on", False), ("regime_off", True)):

        started = time.time()

        result = engine_module.run(
            backtest_config.DEFAULT_START, end, cfg=cfg, data=data,
            verbose=False, engine="v2", use_cache=False,
            disable_regime=disabled,
        )

        rows[name] = _profile(result)

        if verbose:
            print("  %-12s return %8s%%  DD %7s%%  cash %5s%%  trades %3s  %.0fs"
                  % (name, rows[name]["total_return_pct"],
                     rows[name]["max_drawdown_pct"],
                     rows[name]["average_cash_pct"],
                     rows[name]["trades"], time.time() - started), flush=True)

        write({"regime": rows})

    return rows


# ==============================
# DRIVER
# ==============================

def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Separate the exposure problem from the alpha problem.")

    parser.add_argument("--stage", nargs="*",
                        default=["funnel", "stress", "regime"],
                        choices=["funnel", "stress", "regime"])
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)

    cfg = backtest_config.load()

    if args.limit:
        cfg = cfg.replace(universe_limit=args.limit)

    data = PointInTimeData(cfg, verbose=False)

    print("=" * 78)
    print("SIRAT-AI V2 — EXPOSURE RESEARCH")
    print("=" * 78)

    if "funnel" in args.stage:
        print("\n[1] FUNNEL — where the universe goes, empty book", flush=True)
        summary = funnel(cfg, data, limit=args.limit)
        print("\n  %s" % json.dumps(summary, ensure_ascii=False), flush=True)

    if "stress" in args.stage:
        print("\n[2] EXPOSURE STRESS", flush=True)
        stress(cfg, data)

    if "regime" in args.stage:
        print("\n[3] REGIME ON / OFF", flush=True)
        regime_test(cfg, data)

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
