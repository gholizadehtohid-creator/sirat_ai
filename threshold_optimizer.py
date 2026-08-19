"""Sweep score thresholds and aggregate performance across the universe.

Changes over the previous version:

  * It was bare top-level script code, so `import threshold_optimizer` ran a
    full network-backed sweep as a side effect. Wrapped in functions with a
    __main__ guard.
  * Symbols that produced no signal still contributed max_drawdown = 0.0 to
    the aggregate. Drawdown can be positive when a trade never traded below
    its entry, so a no-trade symbol's placeholder could win the min() and
    report 0% drawdown for a threshold that actually lost money. Zero-signal
    rows are excluded from the aggregate.
  * "Best threshold" was argmax(expectancy) over all rows, so a threshold
    with a single lucky trade outranked one with fifty. A minimum sample size
    is now required.
"""

from config import CAPITAL_PER_TRADE, HOLDING_DAYS, STOCK_LIST, THRESHOLDS
from backtest import backtest_symbol
from market_data import get_symbol_data


# Below this many trades, an expectancy figure is noise rather than an edge.
MIN_SIGNALS_FOR_RANKING = 5


def optimize(
    symbols=None,
    thresholds=None,
    holding_days=HOLDING_DAYS,
    params=None,
    capital=CAPITAL_PER_TRADE,
    start=None,
    end=None,
    verbose=True
):
    """Run the sweep and return (summary_rows, per_symbol_rows)."""

    symbols = symbols or STOCK_LIST
    thresholds = thresholds if thresholds is not None else THRESHOLDS

    # Load each history once; the sweep reuses it across every threshold.
    frames = {
        symbol: get_symbol_data(symbol, verbose=verbose)
        for symbol in symbols
    }

    detail = []

    for threshold in thresholds:

        for symbol in symbols:

            frame = frames.get(symbol)

            if frame is None or frame.empty:
                continue

            try:
                result = backtest_symbol(
                    symbol,
                    holding_days=holding_days,
                    threshold=threshold,
                    df=frame,
                    params=params,
                    capital=capital,
                    start=start,
                    end=end
                )
            except Exception as exc:
                print(f"ERROR {symbol} @ {threshold}: {exc}")
                continue

            if result is None:
                continue

            detail.append({
                "threshold": threshold,
                "symbol": symbol,
                "signals": result["signals"],
                "win_rate": result["win_rate"],
                "average_return": result["average_return"],
                "expectancy": result["expectancy"],
                "max_drawdown": result["max_drawdown"],
                "total_return": result["total_return"],
                "net_pnl": result.get("net_pnl", 0)
            })

    summary = summarise(detail, thresholds)

    return summary, detail


def summarise(detail, thresholds):
    """Signal-weighted aggregate per threshold."""

    summary = []

    for threshold in thresholds:

        rows = [r for r in detail if r["threshold"] == threshold]

        # A symbol that never traded has no drawdown to contribute.
        traded = [r for r in rows if r["signals"] > 0]

        total_signals = sum(r["signals"] for r in traded)

        if total_signals == 0:
            summary.append({
                "threshold": threshold,
                "signals": 0,
                "symbols_traded": 0,
                "win_rate": 0.0,
                "average_return": 0.0,
                "expectancy": 0.0,
                "max_drawdown": 0.0,
                "total_return": 0.0,
                "net_pnl": 0.0
            })
            continue

        def weighted(field):
            return sum(r[field] * r["signals"] for r in traded) / total_signals

        summary.append({
            "threshold": threshold,
            "signals": total_signals,
            "symbols_traded": len(traded),
            "win_rate": round(weighted("win_rate"), 2),
            "average_return": round(weighted("average_return"), 2),
            "expectancy": round(weighted("expectancy"), 2),
            "max_drawdown": round(min(r["max_drawdown"] for r in traded), 2),
            "total_return": round(
                sum(r["total_return"] for r in traded) / len(traded), 2
            ),
            "net_pnl": round(sum(r.get("net_pnl", 0) for r in traded), 0)
        })

    return summary


def best_threshold(summary):
    """Highest expectancy among thresholds with a usable sample size."""

    eligible = [
        row for row in summary
        if row["signals"] >= MIN_SIGNALS_FOR_RANKING
    ]

    if not eligible:
        # Fall back to anything that traded, but the caller should treat the
        # result as indicative only.
        eligible = [row for row in summary if row["signals"] > 0]

    if not eligible:
        return None

    return max(eligible, key=lambda r: r["expectancy"])


def print_report(summary):

    print()
    print("=" * 82)
    print("                     OPTIMIZATION SUMMARY")
    print("=" * 82)
    print(
        f"{'THRESHOLD':<11}{'SIGNALS':<9}{'WIN RATE':<11}"
        f"{'AVG RET':<10}{'EXPECTANCY':<13}{'MAX DD':<10}{'TOTAL RET':<10}"
    )
    print("-" * 82)

    for row in summary:
        print(
            f"{row['threshold']:<11}{row['signals']:<9}"
            f"{row['win_rate']:<11.2f}{row['average_return']:<10.2f}"
            f"{row['expectancy']:<13.2f}{row['max_drawdown']:<10.2f}"
            f"{row['total_return']:<10.2f}"
        )

    best = best_threshold(summary)

    print()
    print("=" * 82)

    if best is None:
        print("No qualifying signals at any threshold.")
        print("=" * 82)
        return

    print("                       BEST THRESHOLD")
    print("=" * 82)
    print()
    print(f"Best Threshold : {best['threshold']}")
    print(f"Signals        : {best['signals']}")
    print(f"Win Rate       : {best['win_rate']:.2f}%")
    print(f"Average Return : {best['average_return']:.2f}%")
    print(f"Expectancy     : {best['expectancy']:.2f}%")
    print(f"Maximum DD     : {best['max_drawdown']:.2f}%")

    if best["signals"] < MIN_SIGNALS_FOR_RANKING:
        print()
        print(
            f"WARNING: only {best['signals']} trades in the sample "
            f"(min {MIN_SIGNALS_FOR_RANKING} for a reliable ranking). "
            "Treat this as indicative only."
        )

    print()
    print("=" * 82)


if __name__ == "__main__":

    print("=" * 82)
    print("               SIRAT AI - THRESHOLD OPTIMIZER")
    print("=" * 82)

    summary_rows, _ = optimize()

    print_report(summary_rows)
