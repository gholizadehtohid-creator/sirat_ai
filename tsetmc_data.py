"""TSETMC CDN client.

Note on reachability: cdn.tsetmc.com refuses TLS from many networks outside
Iran (the handshake dies with an unexpected EOF). Callers should treat a
fetch failure as normal and fall back to the SQLite cache -- market_data.py
does exactly that.
"""

import requests
import pandas as pd

from config import REQUEST_TIMEOUT, SYMBOLS, TSETMC_URL


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def get_tsetmc_history(symbol, count=300):
    """Fetch daily OHLCV for one symbol, oldest bar first."""

    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown symbol: {symbol}")

    ins_code = SYMBOLS[symbol]["insCode"]

    url = (
        f"{TSETMC_URL}/api/ClosingPrice/"
        f"GetClosingPriceDailyList/"
        f"{ins_code}/{count}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    records = response.json().get("closingPriceDaily", [])

    if not records:
        raise ValueError(f"No historical data received for {symbol}")

    rows = [
        {
            "date": str(item.get("dEven")),
            "open": item.get("priceFirst"),
            "high": item.get("priceMax"),
            "low": item.get("priceMin"),
            "close": item.get("pClosing"),
            "volume": item.get("qTotTran5J"),
            "transactions": item.get("zTotTran")
        }
        for item in records
    ]

    df = pd.DataFrame(rows)

    df["date"] = pd.to_datetime(
        df["date"],
        format="%Y%m%d",
        errors="coerce"
    )

    for column in ["open", "high", "low", "close", "volume", "transactions"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["date", "close"])

    return df.sort_values("date").reset_index(drop=True)


if __name__ == "__main__":

    print("===== TSETMC CONNECTIVITY TEST =====")

    for symbol in SYMBOLS:

        try:
            df = get_tsetmc_history(symbol, 20)
            print(f"{symbol}: {len(df)} records, latest {df['date'].iloc[-1].date()}")

        except Exception as exc:
            print(f"{symbol}: FAILED - {type(exc).__name__}: {exc}")

    print("===== TEST COMPLETE =====")
