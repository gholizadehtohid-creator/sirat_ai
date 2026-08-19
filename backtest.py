"""Walk-forward backtest of the ranking model.

Correctness fixes over the original version:

  * Look-ahead. It scored bar i and bought at bar i's own close -- a price
    only knowable once the bar being scored has finished. Entry is now the
    NEXT bar's open, the first price actually tradeable after the signal.
  * Trade dates. It recorded df.index[i] right after indicators.py reset the
    index, so every "date" was a row number. Reads the date column now.
  * Drawdown. Computed from closes only, so an intraday move through a stop
    was invisible. Uses intrabar low/high when available.
  * Cost. Returns were gross; a round-trip commission is deducted.
  * Speed. df.iloc[:i+1].copy() copied the whole history on every bar (O(n^2))
    when the scorer reads only the last few rows. Slices a fixed window.

Indicator windows, the date range and position size are all parameters so the
admin panel can vary them.
"""

import math

import pandas as pd

import config
from config import (
    CAPITAL_PER_TRADE,
    DEFAULT_THRESHOLD,
    HOLDING_DAYS,
    HOLDING_PERIODS,
    STOCK_LIST,
    THRESHOLDS,
    TRANSACTION_COST_PCT,
)
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from market_data import get_symbol_data
from ranking import rank_stocks


# rank_stocks reads at most the last 11 rows; 60 is a safe window.
SCORING_WINDOW = 60


def back_adjust(df, threshold=None):
    """Remove capital-increase and dividend gaps from a price series.

    TSETMC serves raw prices. A capital increase drops the quoted price
    overnight by the adjustment factor, and nothing in the feed marks it, so an
    unadjusted series shows a 40% "loss" no holder ever took -- and a backtest
    reads it as one, stops out of it, and reports the result as a strategy
    outcome.

    Returns (adjusted_frame, events). Prices before each gap are scaled so the
    series is continuous; volume is left alone, because it is used for
    liquidity rather than for returns and rescaling it would misstate the
    turnover that actually happened.

    Why adjust rather than drop the affected windows: the exchange enforces a
    daily price band, so a single-session move past the widest band ever in
    force is not a price move at all. Dropping is also not neutral -- most
    moves this large are downward, so discarding them removes losses
    preferentially and flatters the result.
    """

    threshold = (
        getattr(config, "PRICE_ADJUST_THRESHOLD", 12.0)
        if threshold is None else threshold
    ) / 100.0

    if df is None or df.empty or "close" not in df.columns:
        return df, []

    frame = df.reset_index(drop=True).copy()

    closes = frame["close"].astype(float).to_numpy()

    n = len(closes)

    if n < 2:
        return frame, []

    factors = [1.0] * n
    events = []
    running = 1.0

    # Backwards, so one factor accumulates every later adjustment: a series
    # with two capital increases needs the earliest prices scaled by both.
    for i in range(n - 1, 0, -1):

        previous, current = closes[i - 1], closes[i]

        if previous and previous > 0 and current and current > 0:

            ratio = current / previous

            if abs(ratio - 1.0) > threshold:
                running *= ratio
                events.append({
                    "index": i,
                    "date": frame["date"].iloc[i] if "date" in frame else None,
                    "ratio": round(ratio, 4),
                    "move_pct": round((ratio - 1.0) * 100, 2),
                })

        factors[i - 1] = running

    if not events:
        return frame, []

    for column in ("open", "high", "low", "close"):
        if column in frame.columns:
            frame[column] = frame[column].astype(float) * factors

    events.reverse()

    return frame, events


def detect_corporate_actions(df, threshold=None):
    """Dates whose overnight move is too large to be trading.

    TSETMC's daily series is NOT adjusted for capital increases or dividends.
    On an adjustment the price drops overnight by the adjustment factor, which
    a backtest would otherwise read as a real loss the holder suffered. With a
    +/-3% band in force, any move past ~3.6% is such an event.
    """

    threshold = threshold or config.CORPORATE_ACTION_THRESHOLD

    if df is None or df.empty or "close" not in df.columns:
        return []

    frame = df.dropna(subset=["close"])

    if len(frame) < 2 or "date" not in frame.columns:
        return []

    closes = frame["close"].to_numpy()
    dates = frame["date"].tolist()

    events = []

    for i in range(1, len(closes)):
        previous = closes[i - 1]
        if not previous or previous <= 0:
            continue
        move = (closes[i] - previous) / previous * 100
        if abs(move) > threshold:
            events.append({
                "date": _fmt_date(dates[i]),
                "move_pct": round(float(move), 2),
                "from": round(float(previous), 2),
                "to": round(float(closes[i]), 2),
            })

    return events


def buy_and_hold(df, cost_pct=None):
    """Benchmark: buy the first bar, hold to the last.

    A strategy return means nothing on its own. In a market rising this fast
    in nominal terms, the question is not "did it make money" but "did it beat
    simply holding".
    """

    cost_pct = config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct

    if df is None or df.empty or "close" not in df.columns:
        return None

    frame = df.dropna(subset=["close"])

    if len(frame) < 2:
        return None

    first = float(frame["close"].iloc[0])
    last = float(frame["close"].iloc[-1])

    if first <= 0:
        return None

    gross = (last - first) / first * 100

    return {
        "start_date": _fmt_date(frame["date"].iloc[0]) if "date" in frame else None,
        "end_date": _fmt_date(frame["date"].iloc[-1]) if "date" in frame else None,
        "start_price": round(first, 2),
        "end_price": round(last, 2),
        "return": round(gross - cost_pct, 2),
        "gross_return": round(gross, 2),
        "bars": len(frame),
    }


def crosses_regime_break(start_date, end_date):
    """Does a window straddle the base-volume regime change?"""

    if not start_date or not end_date:
        return False

    return str(start_date) <= config.REGIME_BREAK_DATE <= str(end_date)


def slice_dates(df, start=None, end=None):
    """Restrict a price frame to [start, end] inclusive."""

    if df is None or df.empty or "date" not in df.columns:
        return df

    out = df

    if start:
        out = out[out["date"] >= pd.to_datetime(start)]

    if end:
        out = out[out["date"] <= pd.to_datetime(end)]

    return out.reset_index(drop=True)


def empty_result(symbol, threshold=None, holding_days=None):
    """Zeroed metrics for a symbol that produced no qualifying signal."""

    return {
        "symbol": symbol,
        "threshold": threshold,
        "holding_days": holding_days,
        "signals": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "average_return": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "profit_factor": None,
        "expectancy": 0.0,
        "max_drawdown": 0.0,
        "equity_drawdown": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "total_return": 0.0,
        "net_pnl": 0.0,
        "trades": [],
        "equity_curve": []
    }


def backtest_symbol(
    symbol,
    holding_days=HOLDING_DAYS,
    threshold=DEFAULT_THRESHOLD,
    df=None,
    params=None,
    capital=CAPITAL_PER_TRADE,
    start=None,
    end=None,
    verbose=False
):
    """Backtest one symbol. Pass `df` to reuse an already-loaded frame."""

    params = params or DEFAULT_PARAMS

    if df is None:
        df = get_symbol_data(symbol, verbose=verbose)

    if df is None or df.empty:
        return None

    df = slice_dates(df, start, end)

    if df is None or df.empty:
        return None

    df = calculate_indicators(df, params)

    warmup = required_bars(params)

    if df.empty or len(df) <= warmup + holding_days + 1:
        return None

    has_intrabar = "low" in df.columns and "high" in df.columns

    closes = df["close"]
    opens = df["open"] if "open" in df.columns else closes
    dates = df["date"] if "date" in df.columns else pd.Series(df.index)

    # Unadjusted corporate actions look like huge overnight moves. Trades
    # spanning one are marked so their return is never mistaken for a
    # tradeable result.
    actions = detect_corporate_actions(df)
    action_dates = {event["date"] for event in actions}

    trades = []

    # Entry is bar i+1, exit is bar i+1+holding_days, so i must leave room.
    last_signal_bar = len(df) - holding_days - 2

    i = warmup

    while i <= last_signal_bar:

        window = df.iloc[max(0, i - SCORING_WINDOW + 1):i + 1]

        try:
            result = rank_stocks(window)
        except Exception as exc:
            if verbose:
                print(f"Ranking error at bar {i}: {exc}")
            i += 1
            continue

        if result["score"] < threshold:
            i += 1
            continue

        entry_index = i + 1
        exit_index = entry_index + holding_days

        entry_price = opens.iloc[entry_index]

        # Some feeds report 0 for the open on thin days.
        if not entry_price or pd.isna(entry_price) or entry_price <= 0:
            entry_price = closes.iloc[entry_index]

        exit_price = closes.iloc[exit_index]

        if pd.isna(entry_price) or entry_price <= 0 or pd.isna(exit_price):
            i += 1
            continue

        gross_return = ((exit_price - entry_price) / entry_price) * 100
        net_return = gross_return - TRANSACTION_COST_PCT

        # Whole shares only, so rial P&L is exact rather than fractional.
        shares = int(math.floor(capital / entry_price)) if entry_price > 0 else 0
        position_value = shares * entry_price
        net_pnl = position_value * (net_return / 100)

        held = slice(entry_index, exit_index + 1)

        if has_intrabar:
            peak = df["high"].iloc[held].max()
            trough = df["low"].iloc[held].min()
        else:
            peak = closes.iloc[held].max()
            trough = closes.iloc[held].min()

        trades.append({
            "symbol": symbol,
            "signal_date": _fmt_date(dates.iloc[i]),
            "entry_date": _fmt_date(dates.iloc[entry_index]),
            "exit_date": _fmt_date(dates.iloc[exit_index]),
            "entry": round(float(entry_price), 2),
            "exit": round(float(exit_price), 2),
            "score": result["score"],
            "shares": shares,
            "position_value": round(position_value, 0),
            "net_pnl": round(net_pnl, 0),
            "return": round(net_return, 2),
            "gross_return": round(gross_return, 2),
            "max_gain": round(((peak - entry_price) / entry_price) * 100, 2),
            "max_drawdown": round(((trough - entry_price) / entry_price) * 100, 2)
        })

        # Non-overlapping: the next candidate signal is after this trade exits.
        i = exit_index + 1

    if not trades:
        if verbose:
            print(f"{symbol}: no qualifying signals at threshold {threshold}")
        return empty_result(symbol, threshold, holding_days)

    return _summarise(symbol, threshold, holding_days, trades, verbose=verbose)


def _fmt_date(value):
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(value)


def _summarise(symbol, threshold, holding_days, trades, verbose=False):
    """Turn a trade list into performance metrics plus an equity curve."""

    trades_df = pd.DataFrame(trades)

    returns = trades_df["return"]

    wins_df = trades_df[returns > 0]
    losses_df = trades_df[returns <= 0]

    wins = len(wins_df)
    losses = len(losses_df)
    total = len(trades_df)

    win_rate = (wins / total) * 100

    average_win = wins_df["return"].mean() if wins else 0.0
    average_loss = losses_df["return"].mean() if losses else 0.0

    total_profit = wins_df["return"].sum()
    total_loss = abs(losses_df["return"].sum())

    # No losing trade means the ratio is undefined, not infinite profit.
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else None

    expectancy = (
        (win_rate / 100) * average_win
        + ((100 - win_rate) / 100) * average_loss
    )

    equity = 100.0
    equity_curve = [{"date": trades[0]["entry_date"], "equity": 100.0}]
    peak_equity = equity
    worst_equity_dd = 0.0

    for trade in trades:
        equity *= 1 + (trade["return"] / 100)
        peak_equity = max(peak_equity, equity)
        worst_equity_dd = min(
            worst_equity_dd,
            ((equity - peak_equity) / peak_equity) * 100
        )
        equity_curve.append({
            "date": trade["exit_date"],
            "equity": round(equity, 2)
        })

    if verbose:
        print(
            f"{symbol} @ {threshold}/{holding_days}d: {total} trades, "
            f"win rate {win_rate:.1f}%, expectancy {expectancy:.2f}%"
        )

    return {
        "symbol": symbol,
        "threshold": threshold,
        "holding_days": holding_days,
        "signals": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "average_return": round(returns.mean(), 2),
        "average_win": round(average_win, 2),
        "average_loss": round(average_loss, 2),
        "profit_factor": profit_factor,
        "expectancy": round(expectancy, 2),
        "max_drawdown": round(trades_df["max_drawdown"].min(), 2),
        "equity_drawdown": round(worst_equity_dd, 2),
        "best_trade": round(returns.max(), 2),
        "worst_trade": round(returns.min(), 2),
        "total_return": round(equity - 100, 2),
        "net_pnl": round(trades_df["net_pnl"].sum(), 0),
        "trades": trades,
        "equity_curve": equity_curve
    }


def holding_sweep(
    symbols=None,
    periods=None,
    threshold=DEFAULT_THRESHOLD,
    frames=None,
    params=None,
    capital=CAPITAL_PER_TRADE,
    start=None,
    end=None
):
    """Compare holding periods at a fixed threshold.

    Answers "how long should a position be held", which the threshold sweep
    cannot: that one varies selectivity while holding stays pinned.
    """

    symbols = symbols or STOCK_LIST
    periods = periods if periods is not None else HOLDING_PERIODS
    params = params or DEFAULT_PARAMS

    rows = []

    for period in periods:

        trades = []

        for symbol in symbols:

            frame = frames.get(symbol) if frames else None

            result = backtest_symbol(
                symbol,
                holding_days=period,
                threshold=threshold,
                df=frame,
                params=params,
                capital=capital,
                start=start,
                end=end
            )

            if result:
                trades.extend(result["trades"])

        if not trades:
            rows.append({
                "holding_days": period, "signals": 0, "win_rate": 0.0,
                "average_return": 0.0, "expectancy": 0.0,
                "total_return": 0.0, "net_pnl": 0.0
            })
            continue

        returns = [t["return"] for t in trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]

        win_rate = (len(wins) / len(returns)) * 100
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        equity = 100.0
        for r in returns:
            equity *= 1 + (r / 100)

        rows.append({
            "holding_days": period,
            "signals": len(returns),
            "win_rate": round(win_rate, 2),
            "average_return": round(sum(returns) / len(returns), 2),
            "expectancy": round(
                (win_rate / 100) * avg_win + ((100 - win_rate) / 100) * avg_loss, 2
            ),
            "total_return": round(equity - 100, 2),
            "net_pnl": round(sum(t["net_pnl"] for t in trades), 0)
        })

    return rows


def run_backtest(
    symbols=None,
    thresholds=None,
    holding_days=HOLDING_DAYS,
    params=None,
    capital=CAPITAL_PER_TRADE,
    start=None,
    end=None,
    verbose=True
):
    """Backtest every symbol across every threshold.

    Each symbol's history is loaded once and reused for all thresholds instead
    of being refetched per combination.
    """

    symbols = symbols or STOCK_LIST
    thresholds = thresholds if thresholds is not None else THRESHOLDS
    params = params or DEFAULT_PARAMS

    if verbose:
        print("=" * 60)
        print("SIRAT AI BACKTEST")
        print(f"Symbols: {', '.join(symbols)}")
        print(f"Thresholds: {thresholds}")
        print(f"Holding days: {holding_days}")
        print(f"Round-trip cost: {TRANSACTION_COST_PCT}%")
        print("=" * 60)

    frames = {s: get_symbol_data(s, verbose=verbose) for s in symbols}

    results = []

    for threshold in thresholds:

        if verbose:
            print(f"\n--- Threshold {threshold} ---")

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
                    end=end,
                    verbose=verbose
                )
            except Exception as exc:
                print(f"ERROR {symbol} @ {threshold}: {exc}")
                continue

            if result is not None:
                results.append(result)

    if verbose:
        print("\n" + "=" * 60)
        print(f"BACKTEST COMPLETE - {len(results)} symbol/threshold runs")
        print("=" * 60)

    return results


if __name__ == "__main__":
    run_backtest()
