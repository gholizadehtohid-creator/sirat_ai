"""Scan the universe and rank every symbol by score."""

from backtest import slice_dates
from config import STOCK_LIST, SYMBOLS
from indicators import DEFAULT_PARAMS, calculate_indicators
from market_data import get_symbol_data_with_meta
from ranking import rank_stocks


def scan_market(
    symbols=None,
    force_refresh=False,
    params=None,
    start=None,
    end=None,
    verbose=True
):
    """Score every symbol, best first.

    Rows are returned even when a symbol fails so the panel can show what went
    wrong instead of silently dropping it.
    """

    symbols = symbols or STOCK_LIST
    params = params or DEFAULT_PARAMS

    results = []

    for symbol in symbols:

        if verbose:
            print(f"Scanning: {symbol}")

        df, meta = get_symbol_data_with_meta(
            symbol,
            force_refresh=force_refresh,
            verbose=verbose
        )

        base = {
            "symbol": symbol,
            "name": SYMBOLS.get(symbol, {}).get("name", symbol),
            "source": meta["source"],
            "bars": meta["bars"],
            "age_hours": meta.get("age_hours"),
            "error": meta["error"]
        }

        df = slice_dates(df, start, end)

        if df is None or df.empty:
            results.append({**base, **_no_data_row()})
            continue

        enriched = calculate_indicators(df, params)

        if enriched.empty:
            results.append({**base, **_no_data_row()})
            continue

        result = rank_stocks(enriched)

        last_close = float(enriched["close"].iloc[-1])

        change_pct = None

        if len(enriched) >= 2:
            previous = float(enriched["close"].iloc[-2])
            if previous > 0:
                change_pct = round(((last_close - previous) / previous) * 100, 2)

        last_date = None

        if "date" in enriched.columns:
            last_date = enriched["date"].iloc[-1].strftime("%Y-%m-%d")

        results.append({
            **base,
            **result,
            "close": round(last_close, 2),
            "change_pct": change_pct,
            "last_date": last_date,
            "bars_used": len(enriched)
        })

    results.sort(key=lambda r: r["score"], reverse=True)

    for position, row in enumerate(results, start=1):
        row["rank"] = position

    return results


def _no_data_row():
    return {
        "score": 0,
        "status": "NO DATA",
        "trend": "Unknown",
        "rsi": None,
        "macd": None,
        "volume": 0,
        "momentum": 0,
        "close": None,
        "change_pct": None,
        "last_date": None,
        "bars_used": 0,
        "signals": ["Data: unavailable"],
        "breakdown": {},
        "report": "No price data available for the selected range."
    }
