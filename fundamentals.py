"""Fundamental metrics for TSE equities.

Two input tiers, because on a filtered network only one of them is available:

  TIER A -- derivable from cached daily bars alone (works offline):
      liquidity (average daily value traded), turnover, realised volatility,
      staleness, position in the 52-week range.

  TIER B -- needs a live snapshot or instrument-info call:
      EPS, P/E, market capitalisation, shares outstanding, free float.

Every metric records whether it was computable. Nothing is guessed: a symbol
with no EPS gets `pe = None`, not a default, and the screen reports reduced
coverage rather than a confident verdict built on absent data.
"""

import math

import pandas as pd

import config
import nav


def _finite(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def from_history(df, as_of=None):
    """TIER A: metrics derivable from daily bars alone."""

    out = {
        "bars": 0,
        "last_date": None,
        "last_trade_date": None,
        "stale_days": None,
        "close": None,
        "avg_value_20": None,
        "avg_volume_20": None,
        "volatility_60": None,
        "range_position": None,
        "headroom_pct": None,
        "return_60d": None,
    }

    if df is None or df.empty or "close" not in df.columns:
        return out

    frame = df.dropna(subset=["close"]).copy()

    if frame.empty:
        return out

    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")

    out["bars"] = len(frame)

    last_close = _finite(frame["close"].iloc[-1])
    out["close"] = last_close

    if "date" in frame.columns and len(frame):

        last_date = frame["date"].iloc[-1]
        out["last_date"] = last_date.strftime("%Y-%m-%d")

        reference = pd.to_datetime(as_of) if as_of else pd.Timestamp.today().normalize()

        # Staleness must be measured from the last session that actually
        # traded, not the last session on file. TSETMC emits a row for every
        # session a halted instrument was listed, carrying the frozen close at
        # zero volume -- so a share suspended for months still has a bar dated
        # yesterday, and a last-bar-date test would call it perfectly fresh.
        traded = frame
        if "volume" in frame.columns:
            traded = frame[frame["volume"].fillna(0) > 0]

        if len(traded):
            last_trade = traded["date"].iloc[-1]
            out["last_trade_date"] = last_trade.strftime("%Y-%m-%d")
            out["stale_days"] = int((reference - last_trade.normalize()).days)
        else:
            # Listed but never seen to trade in the cached window.
            out["stale_days"] = int((reference - frame["date"].iloc[0].normalize()).days)

    # Liquidity. Prefer the traded-value column; fall back to volume x close.
    if "value" in frame.columns and frame["value"].notna().any():
        values = frame["value"].tail(20)
    elif "volume" in frame.columns:
        values = (frame["volume"] * frame["close"]).tail(20)
    else:
        values = None

    if values is not None and values.notna().any():
        out["avg_value_20"] = _finite(values.mean())

    if "volume" in frame.columns and frame["volume"].notna().any():
        out["avg_volume_20"] = _finite(frame["volume"].tail(20).mean())

    # Realised volatility, annualised over ~250 TSE sessions.
    if len(frame) >= 20:
        returns = frame["close"].pct_change().tail(60).dropna()
        if len(returns) >= 10:
            out["volatility_60"] = _finite(returns.std() * math.sqrt(250) * 100)

    # Where the price sits in its own 52-week range (0 = low, 1 = high).
    window = frame["close"].tail(250)
    if len(window) >= 20:
        low, high = _finite(window.min()), _finite(window.max())
        if low is not None and high is not None and high > low and last_close:
            out["range_position"] = round((last_close - low) / (high - low), 4)

    # Clear air to the ceiling the market remembers.
    #
    # Measured over 6,565 point-in-time signals, returns over the following 20
    # sessions sorted almost perfectly by this one number -- +2.17% for shares
    # within 3% of their 120-session high against +8.08% for those with 20% or
    # more of room, and the win rate rose from 55% to 69% across the same
    # bands. It is the only addition tested this way that also improved the
    # basket, in four windows of five.
    #
    # The current bar is excluded: its own high sits above its own close by
    # construction, so including it credits every share with a fraction of a
    # percent of headroom that is arithmetic rather than resistance.
    window = getattr(config, "HEADROOM_WINDOW", 120)

    if "high" in frame.columns and len(frame) > window and last_close:
        ceiling = _finite(frame["high"].iloc[-(window + 1):-1].max())
        if ceiling is not None and last_close > 0:
            out["headroom_pct"] = round((ceiling - last_close) / last_close * 100, 2)

    if len(frame) >= 61:
        past = _finite(frame["close"].iloc[-61])
        if past and past > 0 and last_close:
            out["return_60d"] = round((last_close - past) / past * 100, 2)

    return out


def from_snapshot(row):
    """TIER B: metrics that need a live snapshot / instrument-info row."""

    out = {
        "eps": None,
        "pe": None,
        "earnings_yield": None,
        "market_cap": None,
        "total_shares": None,
        "base_volume": None,
        "free_float_pct": None,
        "sector_pe": None,
        "state": None,
    }

    if row is None:
        return out

    get = row.get if hasattr(row, "get") else lambda k, d=None: getattr(row, k, d)

    eps = _finite(get("eps"))
    close = _finite(get("close")) or _finite(get("last"))
    shares = _finite(get("total_shares"))

    out["eps"] = eps
    out["total_shares"] = shares
    out["base_volume"] = _finite(get("base_volume"))
    out["free_float_pct"] = _finite(get("free_float_pct"))
    out["sector_pe"] = _finite(get("sector_pe"))
    out["state"] = get("state")

    # A negative or zero EPS has no meaningful P/E -- leave it None and let
    # the loss-making gate handle the symbol.
    if eps and eps > 0 and close and close > 0:
        pe = close / eps
        out["pe"] = round(pe, 2) if pe < config.PE_ABSURD else None
        out["earnings_yield"] = round(eps / close * 100, 2)

    if shares and close:
        out["market_cap"] = shares * close

    return out


def build(history_df, snapshot_row=None, as_of=None, sector=None,
          portfolio=None, equity=None):
    """Full metric set for one instrument, with an explicit coverage flag.

    `sector` is carried because whether a metric is MEANINGFUL is a property
    of the business, not of the arithmetic. An investment company's P/E
    computes perfectly and tells you nothing -- so the screen needs to know
    which industry produced the number, not merely that a number exists.
    """

    metrics = {}
    metrics.update(from_history(history_df, as_of=as_of))
    metrics.update(from_snapshot(snapshot_row))

    sector = (sector or "").strip()

    metrics["sector_name"] = sector or None

    # Earnings multiples measure profitability against price. Where profit is
    # a disposal decision or a provisioning choice, they measure neither.
    metrics["earnings_multiple_meaningful"] = (
        sector not in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL
    )

    metrics["nav"] = None
    metrics["price_to_nav"] = None
    metrics["nav_complete"] = None
    metrics["nav_basis"] = None

    # NAV replaces the withheld earnings multiple where the filings allow it.
    # It needs both halves -- book equity from the statements and the
    # unrealised gain from the portfolio filing -- so it is computed here,
    # once, rather than in each place that wants to read it.
    if portfolio is not None or equity is not None:

        result = nav.net_asset_value(portfolio, _finite(equity))

        metrics["nav"] = result.get("nav")
        metrics["nav_complete"] = result.get("complete")
        metrics["nav_basis"] = result.get("basis")

        metrics["price_to_nav"] = nav.price_to_nav(
            metrics.get("market_cap"), result
        )

    metrics["has_price_data"] = metrics["bars"] >= config.MIN_HISTORY_BARS
    metrics["has_earnings_data"] = metrics["eps"] is not None
    metrics["has_size_data"] = metrics["market_cap"] is not None

    tiers = [
        metrics["has_price_data"],
        metrics["has_earnings_data"],
        metrics["has_size_data"],
    ]

    metrics["coverage"] = round(sum(1 for t in tiers if t) / len(tiers), 2)

    return metrics


def missing_fields(metrics):
    """Human-readable list of what could not be computed."""

    missing = []

    if not metrics.get("has_price_data"):
        bars = metrics.get("bars", 0)
        missing.append(f"price history ({bars} bars, need {config.MIN_HISTORY_BARS})")

    if not metrics.get("has_earnings_data"):
        missing.append("EPS / P/E (needs a market snapshot)")

    if not metrics.get("has_size_data"):
        missing.append("market cap (needs shares outstanding)")

    return missing
