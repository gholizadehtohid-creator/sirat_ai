"""Backtest the configured universe across every threshold."""

from backtest import run_backtest
from threshold_optimizer import print_report, summarise
from config import THRESHOLDS

if __name__ == "__main__":

    results = run_backtest()

    detail = [
        {
            "threshold": r["threshold"],
            "symbol": r["symbol"],
            "signals": r["signals"],
            "win_rate": r["win_rate"],
            "average_return": r["average_return"],
            "expectancy": r["expectancy"],
            "max_drawdown": r["max_drawdown"],
            "total_return": r["total_return"]
        }
        for r in results
    ]

    print_report(summarise(detail, THRESHOLDS))
