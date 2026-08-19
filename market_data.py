"""Market data access with a SQLite cache in front of the TSETMC API.

The previous version imported save_market_data() but never called it, so the
database stayed empty and every caller re-hit the network. A threshold sweep
of 7 thresholds x 5 symbols issued 35 identical HTTP requests; now it issues
at most 5, and zero when the cache is warm.

Data provenance is tracked so the admin panel can state where numbers came
from instead of implying everything is live.
"""

import pandas as pd

from config import CACHE_MAX_AGE_HOURS, HISTORY_BARS, MIN_BARS
from database import (
    cache_age_hours,
    create_database,
    load_history_by_inscode,
    load_instruments,
    load_market_data,
    save_market_data,
)
from persian import normalize_symbol
from tsetmc_data import get_tsetmc_history


# Provenance labels surfaced in the UI.
SOURCE_LIVE = "live"
SOURCE_CACHE = "cache"
SOURCE_STALE = "stale-cache"
SOURCE_NONE = "unavailable"

# Bars for the whole market live in price_history, keyed by insCode; the legacy
# market_data table is keyed by a Latin ticker and only ever held the five
# symbols the prototype shipped with. Everything downstream of the universe
# import writes to the former, so a caller that reads only the latter sees an
# empty frame for all 818 instruments and silently falls back to the five --
# which looks like a working backtest over the wrong data.
SOURCE_MAIN = "price-history"

_INDEX = {"built": False, "map": {}}


def _symbol_index():
    """symbol -> insCode for every known instrument, folded for matching."""

    if _INDEX["built"]:
        return _INDEX["map"]

    mapping = {}

    try:
        instruments = load_instruments()
    except Exception:
        instruments = []

    for row in instruments or []:
        code = str(row.get("ins_code") or "")
        if not code:
            continue
        for field in ("symbol_fa", "symbol_raw"):
            name = row.get(field)
            if name:
                mapping.setdefault(normalize_symbol(name), code)

    _INDEX["map"] = mapping
    _INDEX["built"] = True

    return mapping


def resolve_inscode(symbol):
    """insCode for a symbol, or None when the universe has never seen it."""

    if not symbol:
        return None

    key = normalize_symbol(symbol)

    if not key:
        return None

    index = _symbol_index()

    return index.get(key) or index.get(key.upper()) or index.get(key.lower())


def get_symbol_data(symbol, force_refresh=False, verbose=False):
    """Return a price frame for `symbol`, preferring the cache."""

    df, _ = get_symbol_data_with_meta(
        symbol,
        force_refresh=force_refresh,
        verbose=verbose
    )

    return df


def get_symbol_data_with_meta(symbol, force_refresh=False, verbose=False):
    """Return (frame, meta) where meta records provenance and any error."""

    create_database()

    # The main store first: it is insCode-keyed and covers the whole universe,
    # and it is what the scanner, the portfolio and the flow report all read.
    # Only when a symbol is absent from it does the legacy per-symbol cache --
    # and the network path behind it -- come into play.
    if not force_refresh:

        ins_code = resolve_inscode(symbol)

        if ins_code:

            bars = load_history_by_inscode(ins_code, limit=HISTORY_BARS)

            if len(bars) >= MIN_BARS:

                if verbose:
                    print(
                        f"{symbol}: {len(bars)} bars from price_history "
                        f"(insCode {ins_code})"
                    )

                return bars, {
                    "symbol": symbol,
                    "ins_code": ins_code,
                    "source": SOURCE_MAIN,
                    "bars": len(bars),
                    "age_hours": None,
                    "error": None
                }

    cached = load_market_data(symbol, limit=HISTORY_BARS)

    age = cache_age_hours(symbol)

    cache_is_fresh = (
        age is not None
        and age < CACHE_MAX_AGE_HOURS
        and len(cached) >= MIN_BARS
    )

    if cache_is_fresh and not force_refresh:

        if verbose:
            print(
                f"{symbol}: {len(cached)} bars from cache "
                f"(age {age:.1f}h)"
            )

        return cached, {
            "symbol": symbol,
            "source": SOURCE_CACHE,
            "bars": len(cached),
            "age_hours": round(age, 1),
            "error": None
        }

    # Cache is cold, stale, or a refresh was requested.
    try:

        fresh = get_tsetmc_history(symbol, HISTORY_BARS)

        if fresh is None or fresh.empty:
            raise ValueError("empty response")

        save_market_data(symbol, fresh, source="tsetmc")

        stored = load_market_data(symbol, limit=HISTORY_BARS)

        if verbose:
            print(f"{symbol}: {len(stored)} bars fetched from TSETMC")

        return stored, {
            "symbol": symbol,
            "source": SOURCE_LIVE,
            "bars": len(stored),
            "age_hours": 0.0,
            "error": None
        }

    except Exception as exc:

        message = f"{type(exc).__name__}: {exc}"

        # Network down is not fatal while we still hold usable history.
        if not cached.empty:

            if verbose:
                print(
                    f"{symbol}: TSETMC unreachable, serving "
                    f"{len(cached)} cached bars ({message})"
                )

            return cached, {
                "symbol": symbol,
                "source": SOURCE_STALE,
                "bars": len(cached),
                "age_hours": round(age, 1) if age is not None else None,
                "error": message
            }

        if verbose:
            print(f"{symbol}: no data available ({message})")

        return pd.DataFrame(), {
            "symbol": symbol,
            "source": SOURCE_NONE,
            "bars": 0,
            "age_hours": None,
            "error": message
        }


def refresh_all(symbols, verbose=True):
    """Force a network refresh for every symbol. Returns provenance rows."""

    report = []

    for symbol in symbols:

        _, meta = get_symbol_data_with_meta(
            symbol,
            force_refresh=True,
            verbose=verbose
        )

        report.append(meta)

    return report
