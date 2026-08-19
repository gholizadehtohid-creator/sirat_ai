"""Metrics. Built on performance.py rather than beside it.

WHY NOT A SECOND SHARPE
-----------------------
performance.py already computes Sharpe, Sortino, Calmar, drawdown, alpha and
beta, and it does the one thing that matters on this market: it subtracts a 23%
risk-free rate before dividing. A second implementation here would eventually
disagree with it by a rounding rule, and the two numbers would be reported side
by side under different names by two different reports.

So this module computes only what performance.py does not: average drawdown as
distinct from maximum, downside volatility as a standalone figure, per-calendar-
year returns, trade-level statistics in rials as well as percent, and holding
periods.

A NOTE ON PERCENT VERSUS RIALS
------------------------------
Trade returns are reported both ways and they answer different questions. The
percentage says whether the decisions were good. The rial figure says whether
the account grew, and it can be negative while the percentage is positive -- a
strategy whose winners are small positions and whose losers are large ones does
exactly that.
"""

import math

import numpy as np
import pandas as pd

import config
import performance


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def equity_frame(curve):
    """The equity curve with the derived columns a report needs.

    Columns: cash, portfolio_value, daily_return, cumulative_return, drawdown.
    """

    if curve is None or curve.empty:
        return pd.DataFrame()

    frame = curve.copy()

    values = frame["portfolio_value"].astype(float)

    frame["daily_return"] = values.pct_change()

    first = float(values.iloc[0]) or 1.0

    frame["cumulative_return"] = values / first - 1

    peak = values.cummax()

    frame["drawdown"] = (values - peak) / peak

    return frame


def drawdown_episodes(values):
    """Every underwater stretch, with its depth and length.

    Maximum drawdown is one number about one day. A strategy that spends most
    of its life 15% underwater and once touched 30% is a different proposition
    from one that ran flat and fell 30% in a week, and only the average and the
    count separate them.
    """

    series = pd.Series(values, dtype=float).dropna()

    if len(series) < 2:
        return []

    peak = series.cummax()

    drawdown = (series - peak) / peak

    episodes = []

    start = None
    trough = 0.0

    for index, value in drawdown.items():

        if value < 0:
            if start is None:
                start = index
                trough = value
            else:
                trough = min(trough, value)
        elif start is not None:
            episodes.append({
                "start": str(start),
                "end": str(index),
                "depth_pct": round(trough * 100, 2),
            })
            start, trough = None, 0.0

    if start is not None:
        episodes.append({
            "start": str(start),
            "end": str(drawdown.index[-1]),
            "depth_pct": round(trough * 100, 2),
            "recovered": False,
        })

    return episodes


def downside_volatility(returns, periods=None):
    """Annualised deviation of returns below the risk-free hurdle."""

    periods = periods or config.TRADING_DAYS_PER_YEAR

    series = pd.Series(returns, dtype=float).dropna()

    if len(series) < 2:
        return None

    hurdle = performance.daily_risk_free()

    shortfall = (series - hurdle).clip(upper=0)

    return float(math.sqrt((shortfall ** 2).mean()) * math.sqrt(periods) * 100)


def annual_returns(frame):
    """Return per calendar year, so one exceptional year cannot hide."""

    if frame is None or frame.empty:
        return []

    values = frame["portfolio_value"].astype(float)

    out = []

    for year, group in values.groupby(pd.DatetimeIndex(values.index).year):

        if len(group) < 2:
            continue

        first = float(group.iloc[0])
        last = float(group.iloc[-1])

        if first <= 0:
            continue

        out.append({
            "year": int(year),
            "return_pct": round((last / first - 1) * 100, 2),
            "sessions": len(group),
        })

    return out


def trade_stats(trades):
    """Win rate, payoff, profit factor, expectancy, holding periods.

    Both currencies. `pnl` is rials and `return_pct` is the position's own
    percentage move net of costs; profit factor is computed on rials because
    that is the number the account experiences.
    """

    rows = [t for t in (trades or []) if t.get("pnl") is not None]

    if not rows:
        return {"available": False, "reason": "no closed trades"}

    pnl = [float(t["pnl"]) for t in rows]
    percents = [float(t["pnl_pct"]) for t in rows if t.get("pnl_pct") is not None]

    wins = [v for v in pnl if v > 0]
    losses = [v for v in pnl if v <= 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    win_rate = len(wins) / len(pnl) * 100

    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0

    holding = [t["holding_days"] for t in rows if t.get("holding_days") is not None]

    reasons = {}

    for trade in rows:
        key = trade.get("reason") or "?"
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "available": True,
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "gross_profit": round(gross_profit, 0),
        "gross_loss": round(gross_loss, 0),
        "net_profit": round(sum(pnl), 0),
        # No losing trade makes the ratio undefined, not infinitely good.
        "profit_factor": (round(gross_profit / gross_loss, 2)
                          if gross_loss > 0 else None),
        "average_win": round(average_win, 0),
        "average_loss": round(average_loss, 0),
        "average_win_pct": (round(sum(p for p in percents if p > 0) /
                                  max(1, len([p for p in percents if p > 0])), 2)
                            if percents else None),
        "average_loss_pct": (round(sum(p for p in percents if p <= 0) /
                                   max(1, len([p for p in percents if p <= 0])), 2)
                             if percents else None),
        "largest_win": round(max(pnl), 0),
        "largest_loss": round(min(pnl), 0),
        "average_trade": round(sum(pnl) / len(pnl), 0),
        "average_trade_pct": (round(sum(percents) / len(percents), 2)
                              if percents else None),
        # Expectancy in rials per trade: what one more trade is worth on
        # average, which is the only form of it that can be multiplied by a
        # trade count to forecast anything.
        "expectancy": round(
            (win_rate / 100) * average_win + (1 - win_rate / 100) * average_loss, 0
        ),
        "expectancy_pct": (round(sum(percents) / len(percents), 2)
                           if percents else None),
        "payoff_ratio": (round(average_win / abs(average_loss), 2)
                         if average_loss < 0 else None),
        "average_holding_days": (round(sum(holding) / len(holding), 1)
                                 if holding else None),
        "max_holding_days": max(holding) if holding else None,
        "min_holding_days": min(holding) if holding else None,
        "max_consecutive_wins": _streak(pnl, lambda v: v > 0),
        "max_consecutive_losses": _streak(pnl, lambda v: v <= 0),
        "exit_reasons": reasons,
    }


def _streak(values, predicate):
    best = current = 0

    for value in values:
        if predicate(value):
            current += 1
            best = max(best, current)
        else:
            current = 0

    return best


def summarise(curve, trades, benchmark=None, initial_capital=None):
    """The full metric set for one backtest."""

    frame = equity_frame(curve)

    if frame.empty:
        return {"available": False, "reason": "no equity curve"}

    returns = frame["daily_return"].dropna()

    report = performance.from_daily_returns(
        returns, benchmark=benchmark,
        equity=initial_capital or float(frame["portfolio_value"].iloc[0]),
    )

    if not report.get("available"):
        return report

    values = frame["portfolio_value"].astype(float)

    start_value = float(initial_capital or values.iloc[0])
    end_value = float(values.iloc[-1])

    episodes = drawdown_episodes(values)

    depths = [e["depth_pct"] for e in episodes if e.get("depth_pct") is not None]

    report["initial_capital"] = round(start_value, 0)
    report["final_capital"] = round(end_value, 0)
    report["profit"] = round(end_value - start_value, 0)

    report["average_drawdown_pct"] = (round(sum(depths) / len(depths), 2)
                                      if depths else None)
    report["drawdown_episodes"] = len(episodes)
    report["deepest_episodes"] = sorted(
        episodes, key=lambda e: e.get("depth_pct") or 0)[:5]

    report["downside_volatility_pct"] = (
        round(downside_volatility(returns), 2)
        if downside_volatility(returns) is not None else None
    )

    report["annual_returns"] = annual_returns(frame)

    report["trades"] = trade_stats(trades)

    report["exposure"] = {
        "average_cash_pct": round(float(frame["cash_pct"].mean()), 2)
        if "cash_pct" in frame else None,
        "average_positions": round(float(frame["positions"].mean()), 2)
        if "positions" in frame else None,
        "sessions_fully_in_cash": int((frame["positions"] == 0).sum())
        if "positions" in frame else None,
    }

    return report


def compare(results):
    """One row per named result, for the variant and window comparisons."""

    rows = []

    for label, result in results:

        if result is None or not getattr(result, "available", False):
            reason = "INSUFFICIENT DATA"
            if result is not None and result.insufficient:
                reason = result.insufficient[0].split(":")[0]
            rows.append({"label": label, "available": False, "reason": reason})
            continue

        m = result.metrics
        t = m.get("trades") or {}

        rows.append({
            "label": label,
            "available": True,
            "total_return_pct": m.get("total_return_pct"),
            "cagr_pct": m.get("cagr_pct"),
            "max_drawdown_pct": (m.get("drawdown") or {}).get("max_drawdown_pct"),
            "sharpe": m.get("sharpe"),
            "sortino": m.get("sortino"),
            "calmar": m.get("calmar"),
            "win_rate_pct": t.get("win_rate_pct"),
            "profit_factor": t.get("profit_factor"),
            "trades": t.get("trades"),
        })

    return rows
