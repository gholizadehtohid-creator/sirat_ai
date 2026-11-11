"""Portfolio-level risk: correlation, volatility, beta, VaR and drawdown.

Per-position risk is decided in portfolio.py -- each name is sized so that
being stopped out costs a fixed slice of equity. That is necessary and not
sufficient. Ten positions each risking 1% do not risk 10% if they all fall
together, and they do not risk 3% if they are independent; the number depends
entirely on how they co-move, which no per-position rule can see. This module
measures that.

TWO WARNINGS ABOUT TSE DATA, both of which make every figure here optimistic:

  PRICE BAND.  Daily moves are capped at ±PRICE_BAND_PCT. A share that "should"
      fall 12% falls 3% a day for four days instead. Volatility measured from
      daily closes is therefore truncated -- the tail that VaR exists to
      measure is precisely the part the band hides. Treat these numbers as a
      floor on risk, never a ceiling.

  QUEUE DAYS.  A share locked in a sell queue (صف فروش) records a small move on
      near-zero volume, or no trade at all. Those bars enter the return series
      looking calm. The days you most need to sell are the days that look
      quietest in the data.

Correlations computed on band-limited returns are also biased upward in calm
periods and downward in crashes -- the opposite of what a risk model needs.
Nothing here can fix that; stating it is the honest alternative to pretending
the numbers are unconditional.
"""

import math

import numpy as np
import pandas as pd

import config
import database


# Below this many overlapping sessions a correlation is an artefact of the
# sample, not a property of the pair.
MIN_OVERLAP = 40

# A name whose last trade is this many days before the rest of the book's
# newest is halted, and is measured apart rather than shrinking every
# other name's window to its last trading day.
STALE_DAYS = 30

# Confidence level for VaR / CVaR, as a percentile of the loss distribution.
VAR_CONFIDENCE = 0.95


def _annualise(daily_std):
    return daily_std * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100


def returns_matrix(ins_codes, bars=None, labels=None):
    """Daily returns for several instruments, aligned on shared dates.

    Returns (frame, dropped). The frame has one column per instrument that had
    usable history; `dropped` names the ones that did not, so a caller never
    silently computes a five-name correlation matrix for a ten-name basket.
    """

    bars = bars or config.HISTORY_BARS

    series = {}
    dropped = []

    for n, code in enumerate(ins_codes):

        label = labels[n] if labels else str(code)

        frame = database.load_history_by_inscode(code, limit=bars)

        if frame is None or frame.empty or "close" not in frame.columns:
            dropped.append(label)
            continue

        frame = frame.dropna(subset=["close"]).copy()

        if len(frame) < MIN_OVERLAP:
            dropped.append(label)
            continue

        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")

        # A halted share carries its frozen close at zero volume, which enters
        # a return series as a run of exact zeros. Those are not "calm days" --
        # they are days the share did not trade, and leaving them in pulls
        # every volatility and correlation estimate toward zero.
        if "volume" in frame.columns:
            frame = frame[frame["volume"].fillna(0) > 0]

        if len(frame) < MIN_OVERLAP:
            dropped.append(label)
            continue

        returns = frame.set_index("date")["close"].pct_change().dropna()

        # An unadjusted capital increase shows up as a single enormous negative
        # return that no amount of averaging survives. Drop moves outside what
        # the price band physically permits.
        limit = config.CORPORATE_ACTION_THRESHOLD / 100.0
        returns = returns[returns.abs() <= limit]

        if len(returns) < MIN_OVERLAP:
            dropped.append(label)
            continue

        series[label] = returns

    if not series:
        return pd.DataFrame(), dropped

    # Aligned on dates EVERY name traded: one name halted since 2025-09
    # shrank a whole book's window to the 15 sessions before its halt, and
    # the card reported a 2025 drawdown of -1.6% "near the peak" for a book
    # 7.3% under it (the review panel, 2026-11-11). A name with no trade in
    # the last STALE_DAYS of the others' window is named and left out.
    newest = max(s.index.max() for s in series.values())
    for label in [k for k, s in series.items()
                  if s.index.max() < newest - pd.Timedelta(days=STALE_DAYS)]:
        dropped.append(label)
        del series[label]

    if not series:
        return pd.DataFrame(), dropped

    matrix = pd.DataFrame(series).dropna()

    return matrix, dropped


def benchmark_returns(key="TEDPIX", bars=None):
    """Daily returns of a market index, for beta and relative comparison."""

    spec = config.INDICES.get(key)

    if spec is None:
        return pd.Series(dtype=float)

    frame = database.load_index_history(spec["insCode"], limit=bars)

    if frame is None or frame.empty or "close" not in frame.columns:
        return pd.Series(dtype=float)

    frame = frame.dropna(subset=["close"]).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")

    return frame.set_index("date")["close"].pct_change().dropna()


def correlation(matrix):
    """Correlation matrix as plain nested lists, JSON-safe."""

    if matrix is None or matrix.empty or matrix.shape[1] < 2:
        return None

    corr = matrix.corr()

    return {
        "labels": list(corr.columns),
        "values": [[_clean(v) for v in row] for row in corr.to_numpy()],
        "average": _clean(_average_offdiagonal(corr.to_numpy())),
    }


def _average_offdiagonal(values):
    """Mean pairwise correlation, excluding the diagonal of ones."""

    n = len(values)

    if n < 2:
        return None

    total, count = 0.0, 0

    for i in range(n):
        for j in range(i + 1, n):
            value = values[i][j]
            if value == value:
                total += value
                count += 1

    return total / count if count else None


def portfolio_volatility(matrix, weights):
    """Annualised portfolio volatility, and the naive figure it improves on.

    The comparison is the point. `undiversified` is what the volatility would
    be if every holding moved together -- the weighted average of the
    individual volatilities. The gap between the two IS the diversification,
    expressed in the same units as the risk it reduces.
    """

    if matrix is None or matrix.empty:
        return None

    columns = [c for c in matrix.columns if c in weights]

    if not columns:
        return None

    w = np.array([weights[c] for c in columns], dtype=float)

    total = w.sum()

    if total <= 0:
        return None

    w = w / total

    sub = matrix[columns]

    cov = sub.cov().to_numpy()

    variance = float(w @ cov @ w)

    if variance < 0 or variance != variance:
        return None

    portfolio_std = math.sqrt(variance)

    individual = sub.std().to_numpy()

    undiversified = float(w @ individual)

    return {
        "annualised_pct": round(_annualise(portfolio_std), 2),
        "undiversified_pct": round(_annualise(undiversified), 2),
        "diversification_ratio": (
            round(undiversified / portfolio_std, 3) if portfolio_std > 0 else None
        ),
        "daily_pct": round(portfolio_std * 100, 3),
        "names": len(columns),
    }


def beta(portfolio_daily, market_daily):
    """Beta and R-squared of the portfolio against the market index."""

    if portfolio_daily is None or market_daily is None:
        return None

    joined = pd.concat(
        [portfolio_daily.rename("p"), market_daily.rename("m")],
        axis=1, sort=False
    ).dropna()

    if len(joined) < MIN_OVERLAP:
        return None

    market_variance = joined["m"].var()

    if not market_variance or market_variance <= 0:
        return None

    covariance = joined["p"].cov(joined["m"])

    b = covariance / market_variance

    correlation_pm = joined["p"].corr(joined["m"])

    return {
        "beta": round(float(b), 3),
        "r_squared": (
            round(float(correlation_pm ** 2), 3)
            if correlation_pm == correlation_pm else None
        ),
        "sessions": len(joined),
    }


def value_at_risk(daily_returns, confidence=VAR_CONFIDENCE, equity=None):
    """Historical and parametric VaR, plus conditional VaR (expected shortfall).

    Historical VaR makes no distributional assumption and is the one to trust
    here: TSE daily returns are neither normal nor symmetric, and the
    parametric figure is reported alongside mainly to show how far the normal
    assumption is from the observed tail.

    CVaR answers the question VaR does not -- "when the bad day happens, how
    bad is it on average?" A VaR of 2% says nothing about whether the worst
    case beyond it is 2.5% or 25%.
    """

    if daily_returns is None or len(daily_returns) < MIN_OVERLAP:
        return None

    values = np.asarray(daily_returns, dtype=float)
    values = values[~np.isnan(values)]

    if len(values) < MIN_OVERLAP:
        return None

    quantile = 1 - confidence

    historical = float(np.quantile(values, quantile))

    tail = values[values <= historical]

    conditional = float(tail.mean()) if len(tail) else historical

    mean = float(values.mean())
    std = float(values.std(ddof=1))

    # Normal quantile at 95% / 99% without pulling in scipy for one constant.
    z = {0.95: 1.6448536269514722, 0.99: 2.3263478740408408}.get(
        round(confidence, 2), 1.6448536269514722
    )

    parametric = mean - z * std

    out = {
        "confidence": confidence,
        "historical_pct": round(historical * 100, 2),
        "parametric_pct": round(parametric * 100, 2),
        "conditional_pct": round(conditional * 100, 2),
        "worst_day_pct": round(float(values.min()) * 100, 2),
        "sessions": len(values),
    }

    if equity:
        out["historical_rials"] = round(abs(historical) * equity, 0)
        out["conditional_rials"] = round(abs(conditional) * equity, 0)

    return out


def concentration(weights):
    """Herfindahl index and effective number of positions.

    Ten positions of 9% each and ten where one holds 50% are both "ten
    positions". The effective count -- 1 / sum of squared weights -- says the
    first is worth ten and the second barely three.
    """

    values = [w for w in weights.values() if w and w > 0]

    if not values:
        return None

    total = sum(values)

    if total <= 0:
        return None

    shares = [w / total for w in values]

    hhi = sum(s * s for s in shares)

    return {
        "hhi": round(hhi, 4),
        "effective_positions": round(1 / hhi, 2) if hhi > 0 else None,
        "positions": len(values),
        "largest_pct": round(max(shares) * 100, 2),
    }


def sector_exposure(positions):
    """Rial and percentage exposure per industry."""

    totals = {}

    for position in positions:
        sector = position.get("sector_name") or "بدون صنعت"
        totals[sector] = totals.get(sector, 0) + (position.get("position_value") or 0)

    invested = sum(totals.values())

    if invested <= 0:
        return []

    rows = [
        {
            "sector_name": sector,
            "value": round(value, 0),
            "pct_of_invested": round(value / invested * 100, 2),
        }
        for sector, value in totals.items()
    ]

    rows.sort(key=lambda r: r["value"], reverse=True)

    return rows


def analyse(positions, equity=None, bars=None, benchmark_key="TEDPIX"):
    """Full risk report for a built portfolio.

    `equity` is still accepted, for the callers that pass it, and no longer
    used: the rial VaR is struck on the money in the basket rather than on the
    account -- see the comment at the VaR call.
    """

    if not positions:
        return {
            "available": False,
            "reason": "سبدی برای تحلیل ریسک وجود ندارد.",
            "sector_exposure": [],
        }

    codes = [p["ins_code"] for p in positions]
    labels = [p["symbol"] for p in positions]

    matrix, dropped = returns_matrix(codes, bars=bars, labels=labels)

    exposure = sector_exposure(positions)

    weights = {
        p["symbol"]: (p.get("position_value") or 0)
        for p in positions
    }

    report = {
        "available": not matrix.empty,
        "sessions": int(matrix.shape[0]) if not matrix.empty else 0,
        "names_measured": int(matrix.shape[1]) if not matrix.empty else 0,
        "names_dropped": dropped,
        "sector_exposure": exposure,
        "concentration": concentration(weights),
        "notes": [],
    }

    if dropped:
        report["notes"].append(
            f"{len(dropped)} نماد به‌دلیل نبود سابقه‌ی کافی از محاسبات ریسک "
            f"کنار گذاشته شد: {'، '.join(dropped)}"
        )

    if matrix.empty:
        report["reason"] = (
            "هیچ‌کدام از نمادهای سبد سابقه‌ی معاملاتی کافی برای محاسبه‌ی "
            f"همبستگی ندارند (حداقل {MIN_OVERLAP} جلسه لازم است)."
        )
        return report

    if matrix.shape[0] < MIN_OVERLAP:
        report["notes"].append(
            f"تنها {matrix.shape[0]} جلسه‌ی مشترک بین نمادها وجود دارد؛ "
            "اعداد همبستگی و نوسان را با احتیاط بخوانید."
        )

    report["correlation"] = correlation(matrix)
    report["volatility"] = portfolio_volatility(matrix, weights)

    # Portfolio daily return series, weighted by position value.
    columns = [c for c in matrix.columns if c in weights]
    total_weight = sum(weights[c] for c in columns)

    if columns and total_weight > 0:

        w = pd.Series(
            {c: weights[c] / total_weight for c in columns}
        )

        portfolio_daily = (matrix[columns] * w).sum(axis=1)

        # Rials on the money this series describes. The weights are position
        # values over their own sum, so portfolio_daily is the BASKET's return
        # and its quantile is a loss on the basket's value. It was struck on
        # account equity, which charged the unallocated cash the same loss: a
        # basket holding 76.5% of equity had its VaR and CVaR overstated 1.31x.
        # A name dropped for want of history is in neither the series nor this
        # value, as the note above already tells the user.
        report["var"] = value_at_risk(portfolio_daily, equity=total_weight)
        report["drawdown"] = max_drawdown(portfolio_daily)

        market = benchmark_returns(benchmark_key, bars=bars)

        if not market.empty:
            report["beta"] = beta(portfolio_daily, market)
            report["benchmark"] = config.INDICES[benchmark_key]["name"]

    report["price_band_warning"] = (
        f"دامنه‌ی نوسان روزانه‌ی ±{config.PRICE_BAND_PCT}٪ دنباله‌ی توزیع بازده را "
        "می‌بُرد؛ نوسان و VaR محاسبه‌شده کفِ ریسک واقعی است، نه سقف آن."
    )

    return report


def max_drawdown(daily_returns):
    """Deepest peak-to-trough fall of a compounded return series."""

    if daily_returns is None or len(daily_returns) < 2:
        return None

    equity = (1 + pd.Series(daily_returns)).cumprod()

    peak = equity.cummax()

    drawdown = (equity - peak) / peak

    trough = drawdown.min()

    if trough != trough:
        return None

    end = drawdown.idxmin()

    # The peak this fall started from is the last high before the trough.
    before = equity.loc[:end]
    start = before.idxmax() if len(before) else None

    return {
        "max_drawdown_pct": round(float(trough) * 100, 2),
        "peak_date": _date(start),
        "trough_date": _date(end),
        "current_pct": round(float(drawdown.iloc[-1]) * 100, 2),
    }


def _date(value):
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _clean(value):
    """NaN and infinities are not numbers a JSON consumer can use."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number != number or number in (float("inf"), float("-inf")):
        return None

    return round(number, 4)


if __name__ == "__main__":

    import pipeline
    import portfolio as portfolio_module

    report = pipeline.run_scan(progress=lambda m: print(f"  ... {m}"))

    basket = portfolio_module.build(report["final"])

    risk = analyse(basket["positions"], equity=basket["settings"]["equity"])

    print()
    print("=" * 78)
    print("SIRAT AI — تحلیل ریسک سبد / PORTFOLIO RISK")
    print("=" * 78)

    if not risk.get("available"):
        print(risk.get("reason", "unavailable"))
        raise SystemExit(0)

    print(f"Measured over {risk['sessions']} common sessions, "
          f"{risk['names_measured']} names.")

    vol = risk.get("volatility")
    if vol:
        print()
        print(f"Portfolio volatility   {vol['annualised_pct']:.2f}% annualised")
        print(f"If perfectly correlated{vol['undiversified_pct']:>7.2f}%")
        print(f"Diversification ratio  {vol['diversification_ratio']}")

    corr = risk.get("correlation")
    if corr:
        print(f"Average pair correlation {corr['average']}")

    var = risk.get("var")
    if var:
        print()
        print(f"VaR {int(var['confidence']*100)}% (historical)  "
              f"{var['historical_pct']:.2f}%  "
              f"= {var.get('historical_rials', 0):,.0f} rials")
        print(f"CVaR / expected shortfall {var['conditional_pct']:.2f}%  "
              f"= {var.get('conditional_rials', 0):,.0f} rials")
        print(f"Worst observed session    {var['worst_day_pct']:.2f}%")

    b = risk.get("beta")
    if b:
        print()
        print(f"Beta vs {risk.get('benchmark')}  {b['beta']}  "
              f"(R² {b['r_squared']}, {b['sessions']} sessions)")

    dd = risk.get("drawdown")
    if dd:
        print(f"Max drawdown {dd['max_drawdown_pct']:.2f}% "
              f"({dd['peak_date']} → {dd['trough_date']})")

    conc = risk.get("concentration")
    if conc:
        print()
        print(f"Effective positions {conc['effective_positions']} "
              f"of {conc['positions']} (largest {conc['largest_pct']}%)")

    print()
    print("Sector exposure:")
    for row in risk["sector_exposure"]:
        print(f"  {row['sector_name'][:34]:<36} {row['pct_of_invested']:>6.2f}%")

    for note in risk.get("notes", []):
        print(f"\nNOTE: {note}")

    print(f"\n{risk['price_band_warning']}")
