"""Performance measurement for backtests and live portfolios.

A total return is not a result. "This strategy returned 34%" is unreadable
without three things it omits: over how long, at what risk, and against what
alternative. This module supplies all three.

THE RISK-FREE RATE IS THE POINT HERE.

Iran's policy rate is ~23%. Almost every performance formula in circulation was
written for markets where the risk-free rate rounds to zero, and using them
unmodified is not a small error -- it inverts conclusions. A strategy returning
30% a year looks excellent by any absolute measure and is in fact barely ahead
of a bank deposit that carries no market risk, no drawdown and no effort. Every
ratio below subtracts the hurdle before dividing, so a number above zero means
"beat cash", not merely "made money".

The same applies to the benchmark. In a market whose index rises fast in
nominal terms, beating cash is still not enough -- the question is whether the
model beat simply owning the index, which is why relative_to() exists.
"""

import math

import numpy as np
import pandas as pd

import config


PERIODS = config.TRADING_DAYS_PER_YEAR


def daily_risk_free():
    """The policy rate expressed per session, compounded not divided.

    RISK_FREE_RATE / 240 would understate the hurdle: 23% compounded over 240
    sessions is not 240 times the daily figure. At this rate the difference is
    material, not academic.
    """

    return (1 + config.RISK_FREE_RATE) ** (1 / PERIODS) - 1


def _clean_series(returns):
    """Coerce to a float Series with no NaN, or an empty one."""

    if returns is None:
        return pd.Series(dtype=float)

    series = pd.Series(returns, dtype=float)

    return series.replace([np.inf, -np.inf], np.nan).dropna()


def from_daily_returns(returns, benchmark=None, equity=None):
    """Full metric set from a daily return series (as fractions, not percent)."""

    series = _clean_series(returns)

    if len(series) < 2:
        return {
            "available": False,
            "reason": f"فقط {len(series)} جلسه بازده موجود است؛ حداقل ۲ جلسه لازم است.",
        }

    sessions = len(series)
    years = sessions / PERIODS

    growth = float((1 + series).prod())

    total_return = (growth - 1) * 100

    # CAGR is undefined for a wiped-out account and misleading for a window
    # shorter than a season; report the window rather than annualising noise.
    if growth <= 0:
        cagr = None
    elif years > 0:
        cagr = (growth ** (1 / years) - 1) * 100
    else:
        cagr = None

    daily_std = float(series.std(ddof=1))
    volatility = daily_std * math.sqrt(PERIODS) * 100

    hurdle = daily_risk_free()

    excess = series - hurdle

    sharpe = _ratio(excess.mean(), daily_std)

    # Sortino divides by downside deviation only. Upside volatility is not
    # risk, and penalising it -- as Sharpe does -- ranks a strategy that
    # occasionally doubles below one that never moves.
    #
    # The denominator averages the squared shortfalls over EVERY period, not
    # just the losing ones. Dividing by the count of negative days instead
    # measures "how bad is a bad day" rather than "how much downside does this
    # strategy carry", and makes a strategy that loses rarely score no better
    # than one that loses constantly -- the exact distinction Sortino exists to
    # draw. It also made Sortino come out below Sharpe on symmetric returns,
    # which is impossible under the real definition.
    shortfall = excess.clip(upper=0)

    downside_std = float(math.sqrt((shortfall ** 2).mean()))

    sortino = _ratio(excess.mean(), downside_std)

    drawdown = drawdown_profile(series)

    max_dd = drawdown["max_drawdown_pct"] if drawdown else None

    # Calmar: annual return per unit of the worst loss actually endured. The
    # only ratio here whose denominator is a real historical event rather than
    # a statistical summary.
    calmar = (
        round(cagr / abs(max_dd), 2)
        if cagr is not None and max_dd not in (None, 0)
        else None
    )

    out = {
        "available": True,
        "sessions": sessions,
        "years": round(years, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "volatility_pct": round(volatility, 2),
        "risk_free_pct": round(config.RISK_FREE_RATE * 100, 2),
        "sharpe": round(sharpe * math.sqrt(PERIODS), 2) if sharpe is not None else None,
        "sortino": round(sortino * math.sqrt(PERIODS), 2) if sortino is not None else None,
        "calmar": calmar,
        "best_day_pct": round(float(series.max()) * 100, 2),
        "worst_day_pct": round(float(series.min()) * 100, 2),
        "positive_days_pct": round(float((series > 0).mean()) * 100, 2),
        "drawdown": drawdown,
    }

    # A return that beat 34% while cash paid 23% earned 11 points for the risk
    # it took. Stating both makes that arithmetic impossible to skip.
    if cagr is not None:
        out["excess_over_cash_pct"] = round(cagr - config.RISK_FREE_RATE * 100, 2)

    if equity:
        out["final_equity"] = round(equity * growth, 0)
        out["profit"] = round(equity * (growth - 1), 0)

    if benchmark is not None:
        out["benchmark"] = relative_to(series, benchmark)

    return out


def _ratio(numerator, denominator):
    """Guarded division: a zero denominator is undefined, not infinite."""

    if denominator is None or denominator <= 0:
        return None

    value = numerator / denominator

    return value if value == value else None


def drawdown_profile(returns):
    """Deepest fall, when it happened, and how long recovery took."""

    series = _clean_series(returns)

    if len(series) < 2:
        return None

    equity = (1 + series).cumprod()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak

    trough_value = float(drawdown.min())

    if trough_value != trough_value:
        return None

    trough = drawdown.idxmin()

    before = equity.loc[:trough]
    start = before.idxmax() if len(before) else None

    # Recovery is the first session after the trough that regains the old peak.
    after = equity.loc[trough:]
    target = float(peak.loc[trough])

    recovered = after[after >= target]
    recovery = recovered.index[0] if len(recovered) else None

    return {
        "max_drawdown_pct": round(trough_value * 100, 2),
        "peak_date": _label(start),
        "trough_date": _label(trough),
        "recovery_date": _label(recovery) if recovery is not None else None,
        "recovered": recovery is not None,
        "current_pct": round(float(drawdown.iloc[-1]) * 100, 2),
        "sessions_underwater": int((drawdown < 0).sum()),
    }


def relative_to(strategy, benchmark):
    """Alpha, beta, tracking error and information ratio against a benchmark.

    Alpha here is annualised Jensen's alpha: the return left over after what
    the market gave for the beta actually carried. A strategy up 40% in a
    market up 45% did not outperform, however good the absolute number reads.
    """

    a = _clean_series(strategy)
    b = _clean_series(benchmark)

    joined = pd.concat([a.rename("s"), b.rename("b")], axis=1, sort=False).dropna()

    if len(joined) < 20:
        return {
            "available": False,
            "reason": f"تنها {len(joined)} جلسه‌ی مشترک با شاخص وجود دارد.",
        }

    s, m = joined["s"], joined["b"]

    market_var = float(m.var(ddof=1))

    beta = float(s.cov(m) / market_var) if market_var > 0 else None

    hurdle = daily_risk_free()

    if beta is not None:
        alpha_daily = float((s - hurdle).mean() - beta * (m - hurdle).mean())
        alpha = ((1 + alpha_daily) ** PERIODS - 1) * 100
    else:
        alpha = None

    active = s - m

    tracking = float(active.std(ddof=1))

    information = (
        round(float(active.mean()) / tracking * math.sqrt(PERIODS), 2)
        if tracking > 0 else None
    )

    strategy_growth = float((1 + s).prod())
    benchmark_growth = float((1 + m).prod())

    return {
        "available": True,
        "sessions": len(joined),
        "strategy_return_pct": round((strategy_growth - 1) * 100, 2),
        "benchmark_return_pct": round((benchmark_growth - 1) * 100, 2),
        "excess_return_pct": round((strategy_growth - benchmark_growth) * 100, 2),
        "beta": round(beta, 3) if beta is not None else None,
        "alpha_pct": round(alpha, 2) if alpha is not None else None,
        "tracking_error_pct": round(tracking * math.sqrt(PERIODS) * 100, 2),
        "information_ratio": information,
        "correlation": round(float(s.corr(m)), 3),
    }


def from_trades(trades):
    """Trade-level statistics: win rate, profit factor, expectancy, streaks.

    These answer a different question from the return-series metrics above.
    Sharpe describes the equity curve; these describe the decisions that built
    it, and a strategy can look acceptable on one and alarming on the other --
    a high win rate carried by a few enormous losses, for instance.
    """

    rows = list(trades or [])

    if not rows:
        return {"available": False, "reason": "هیچ معامله‌ای ثبت نشده است."}

    returns = [float(t["return"]) for t in rows if t.get("return") is not None]

    if not returns:
        return {"available": False, "reason": "معاملات بازدهی ثبت‌شده ندارند."}

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    win_rate = len(wins) / len(returns) * 100

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    # No losing trade makes the ratio undefined, not infinitely good.
    profit_factor = (
        round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    )

    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0

    expectancy = (
        (win_rate / 100) * average_win + (1 - win_rate / 100) * average_loss
    )

    payoff = (
        round(average_win / abs(average_loss), 2) if average_loss < 0 else None
    )

    return {
        "available": True,
        "trades": len(returns),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "average_trade_pct": round(sum(returns) / len(returns), 2),
        "average_win_pct": round(average_win, 2),
        "average_loss_pct": round(average_loss, 2),
        "payoff_ratio": payoff,
        "profit_factor": profit_factor,
        "expectancy_pct": round(expectancy, 2),
        "best_trade_pct": round(max(returns), 2),
        "worst_trade_pct": round(min(returns), 2),
        "max_consecutive_wins": _streak(returns, lambda r: r > 0),
        "max_consecutive_losses": _streak(returns, lambda r: r <= 0),
    }


def _streak(values, predicate):
    """Longest run for which `predicate` holds."""

    best = current = 0

    for value in values:
        if predicate(value):
            current += 1
            best = max(best, current)
        else:
            current = 0

    return best


def _label(value):
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(value) if value is not None else None


def summary_lines(report):
    """Human-readable lines for the console, in reading order."""

    if not report.get("available"):
        return [report.get("reason", "unavailable")]

    lines = [
        f"Window            {report['sessions']} sessions ({report['years']} years)",
        f"Total return      {report['total_return_pct']:+.2f}%",
    ]

    if report.get("cagr_pct") is not None:
        lines.append(f"CAGR              {report['cagr_pct']:+.2f}%")
        lines.append(
            f"  vs cash at {report['risk_free_pct']:.0f}%   "
            f"{report['excess_over_cash_pct']:+.2f}% excess"
        )

    lines += [
        f"Volatility        {report['volatility_pct']:.2f}% annualised",
        f"Sharpe            {report['sharpe']}",
        f"Sortino           {report['sortino']}",
        f"Calmar            {report['calmar']}",
    ]

    dd = report.get("drawdown")
    if dd:
        lines.append(
            f"Max drawdown      {dd['max_drawdown_pct']:.2f}%  "
            f"({dd['peak_date']} → {dd['trough_date']}, "
            f"{'recovered' if dd['recovered'] else 'not recovered'})"
        )

    bench = report.get("benchmark")
    if bench and bench.get("available"):
        lines += [
            "",
            f"Benchmark         {bench['benchmark_return_pct']:+.2f}% "
            f"vs strategy {bench['strategy_return_pct']:+.2f}%",
            f"  excess          {bench['excess_return_pct']:+.2f} pp",
            f"  alpha           {bench['alpha_pct']}%   beta {bench['beta']}",
            f"  information     {bench['information_ratio']}",
        ]

    return lines
