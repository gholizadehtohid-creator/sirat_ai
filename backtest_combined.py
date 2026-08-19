"""Does the combined score earn its extra components?

`backtest.py` answers "does the technical score work". It cannot answer the
question the portfolio actually depends on, because the basket is ranked on
scoring.combine() -- technical blended with flow, fundamentals and risk -- and
none of that blend had ever been measured against forward returns.

WHAT THIS CAN AND CANNOT TEST
-----------------------------
Every component has to be reconstructible *as of* the signal date or the test
is worthless: scoring a date in 2025 with a number computed in 2026 does not
measure an edge, it measures hindsight, and it always looks excellent.

    technical    YES.  Derived from bars up to the signal bar, nothing after.
    flow         YES, from ~2024-09.  client_type_history only reaches ~700
                 instruments from that month; before it the table holds a
                 few dozen names and any result would describe those, not the
                 market.
    fundamental  NO.  codal_financials holds 334 rows over 225 instruments,
                 and almost all of them are period_end 1404/12/29 or
                 1405/03/31 -- the two most recent reporting periods. There is
                 no fundamental history to reconstruct, so the fundamental
                 weight cannot be validated at any horizon. Backfilling
                 several years of filings is the prerequisite, not a longer
                 backtest.
    risk         Derived from the same bars as technical, so point-in-time,
                 but it is a sizing input rather than a return prediction and
                 is reported, not swept.

So this measures the FLOW component specifically, which is the part that is
both testable and currently unvalidated.

METHOD
------
The comparison is deliberately not "technical basket vs combined basket".
Those differ in which names they hold *and* how many, so a difference cannot
be attributed. Instead every technically-approved signal is recorded together
with its point-in-time flow score, and the trades are bucketed by that score.
If flow carries information, the high-flow bucket beats the low-flow bucket on
the same signals. If it does not, the buckets are indistinguishable and the
25-30% weight the basket gives it is unearned.
"""

import argparse
import math
import statistics
import sys

import pandas as pd

import config
import database
import moneyflow
import scoring
from backtest import back_adjust
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from ranking import rank_stocks


# Flow breadth reaches the whole market here; earlier months hold a few dozen
# instruments and would describe a sample nobody chose.
FLOW_BREADTH_FROM = "2024-09-01"


# Market regimes to test the same rule in, measured on TEDPIX month-end closes.
# A rule validated only in the first row has not been validated -- every
# long-only momentum system looks good in a market that trebles.
#
#   name        window                    TEDPIX over the window
REGIMES = {
    "bull-2020":  ("2020-01-31", "2020-07-31", "+345.8%"),
    "bear-2020":  ("2020-07-31", "2021-01-31", "-34.3%"),
    "bear-2021":  ("2020-11-30", "2021-05-31", "-21.8%"),
    "bear-2023":  ("2023-04-30", "2023-10-31", "-20.1%"),
    "bear-2025":  ("2025-02-28", "2025-08-31", "-14.6%"),
    "recent":     ("2025-05-01", "2026-08-11", "+78.4%"),
}

SCORING_WINDOW = 60

# Evaluating every bar is ~5x the cost for signals that mostly repeat while a
# trend persists. The step is applied to the *scan*, never to the exit.
DEFAULT_STEP = 3


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


# ==============================
# ONE SYMBOL
# ==============================

def signals_for(ins_code, symbol, bars, flow_rows, holding_days,
                threshold, step=DEFAULT_STEP, params=None,
                signal_from=None, signal_to=None):
    """Every technically-approved signal, with its point-in-time flow score.

    `signal_from`/`signal_to` bound the signal dates without touching the bars
    behind them, so a regime window is tested with a full warmup rather than
    with whatever indicators can be computed from inside the window alone.
    """

    params = params or DEFAULT_PARAMS

    if bars is None or len(bars) < required_bars(params) + holding_days + 5:
        return []

    # Before anything reads a price. An unadjusted capital increase is a 40%
    # overnight "loss" that never happened, and it corrupts three things at
    # once: the indicators feeding the score, the trade's own return, and --
    # worst -- the trailing stop, which exits on a low that was a re-pricing
    # rather than a move.
    bars, adjustments = back_adjust(bars)

    frame = calculate_indicators(bars, params)

    if "date" not in frame.columns:
        return []

    frame = frame.reset_index(drop=True)

    dates = frame["date"]
    opens = frame["open"] if "open" in frame.columns else frame["close"]
    closes = frame["close"]

    has_range = "high" in frame.columns and "low" in frame.columns

    # Flow lookup, indexed by date so a slice is a comparison not a search.
    flow_frame = None
    if flow_rows is not None and not flow_rows.empty and "date" in flow_rows:
        flow_frame = flow_rows.sort_values("date").reset_index(drop=True)

    warmup = max(required_bars(params), SCORING_WINDOW)
    last_signal_bar = len(frame) - holding_days - 2

    trades = []

    i = warmup

    while i <= last_signal_bar:

        signal_date = dates.iloc[i]

        if signal_from is not None and signal_date < signal_from:
            i += step
            continue

        if signal_to is not None and signal_date > signal_to:
            break

        window = frame.iloc[max(0, i - SCORING_WINDOW + 1):i + 1]

        try:
            technical = rank_stocks(window)
        except Exception:
            i += step
            continue

        score = technical.get("score")

        if score is None or score < threshold:
            i += step
            continue

        # ---- flow, as of the signal bar and no later ---------------------
        flow_score = None
        flow_verdict = None

        if flow_frame is not None:
            visible = flow_frame[flow_frame["date"] <= signal_date]
            tail = visible.tail(config.FLOW_BASELINE_WINDOW)

            if len(tail) >= config.FLOW_MIN_SESSIONS:
                try:
                    reading = moneyflow.analyse(tail)
                    flow_score = reading.get("score")
                    flow_verdict = reading.get("verdict")
                except Exception:
                    flow_score = None

        entry_index = i + 1
        exit_index = entry_index + holding_days

        entry_price = _finite(opens.iloc[entry_index]) or _finite(closes.iloc[entry_index])
        exit_price = _finite(closes.iloc[exit_index])

        if not entry_price or entry_price <= 0 or exit_price is None:
            i += step
            continue

        held = frame.iloc[entry_index:exit_index + 1]

        if has_range:
            peak = _finite(held["high"].max())
            trough = _finite(held["low"].min())
        else:
            peak = _finite(held["close"].max())
            trough = _finite(held["close"].min())

        gross = (exit_price - entry_price) / entry_price * 100
        net = gross - config.TRANSACTION_COST_PCT

        atr_pct = _finite(frame["ATR_PCT"].iloc[i]) if "ATR_PCT" in frame else None

        trail_exit = _trailing_exit(held, entry_price, atr_pct)

        risk = scoring.risk_score({
            "volatility_60": _finite(
                closes.iloc[max(0, i - 60):i + 1].pct_change().std() * math.sqrt(250) * 100
            ),
            "atr_pct": atr_pct,
            "avg_value_20": _finite(
                (frame["value"].iloc[max(0, i - 19):i + 1].mean())
                if "value" in frame.columns else None
            ),
        })

        trades.append({
            "symbol": symbol,
            "ins_code": ins_code,
            "signal_date": str(pd.to_datetime(signal_date).date()),
            "technical_score": score,
            "flow_score": flow_score,
            "flow_verdict": flow_verdict,
            "risk_score": risk.get("score"),
            "entry": entry_price,
            "exit": exit_price,
            "return": round(net, 2),
            "gross_return": round(gross, 2),
            "max_gain": round((peak - entry_price) / entry_price * 100, 2)
            if peak else None,
            "max_drawdown": round((trough - entry_price) / entry_price * 100, 2)
            if trough else None,
            "trail_return": trail_exit["return"],
            "trail_days": trail_exit["days"],
            "trail_reason": trail_exit["reason"],
            "atr_pct": atr_pct,
            "adjustments": len(adjustments),
        })

        # Non-overlapping per symbol, exactly as backtest.py does, so one
        # persistent trend cannot contribute twenty correlated trades.
        i = exit_index + 1

    return trades


def _trailing_exit(held, entry_price, atr_pct):
    """Walk the holding window applying the configured trailing stop.

    Mirrors portfolio.py: the effective stop is max(initial, peak - trail), so
    it can only ratchet upward. Returns the time-exit result unchanged when
    trailing is disabled or ATR is unavailable.
    """

    empty = {"return": None, "days": None, "reason": None}

    trail_multiple = getattr(config, "TRAIL_ATR_MULTIPLE", 0.0)

    if not atr_pct or atr_pct <= 0 or held is None or held.empty:
        return empty

    stop_distance = entry_price * (atr_pct * config.STOP_ATR_MULTIPLE) / 100.0
    initial_stop = entry_price - stop_distance

    if trail_multiple and trail_multiple > 0:
        trail_distance = entry_price * (atr_pct * trail_multiple) / 100.0
    else:
        trail_distance = None

    has_range = "high" in held.columns and "low" in held.columns

    peak_close = entry_price
    stop = initial_stop

    for n, (_, bar) in enumerate(held.iterrows(), start=1):

        low = _finite(bar["low"]) if has_range else _finite(bar["close"])
        close = _finite(bar["close"])

        if low is not None and low <= stop:
            out = (stop - entry_price) / entry_price * 100
            return {
                "return": round(out - config.TRANSACTION_COST_PCT, 2),
                "days": n,
                "reason": "trailing stop" if stop > initial_stop else "initial stop",
            }

        if close is not None:
            peak_close = max(peak_close, close)
            if trail_distance is not None:
                stop = max(stop, peak_close - trail_distance)

    final = _finite(held["close"].iloc[-1])

    if final is None:
        return empty

    out = (final - entry_price) / entry_price * 100

    return {
        "return": round(out - config.TRANSACTION_COST_PCT, 2),
        "days": len(held),
        "reason": "time",
    }


# ==============================
# THE SWEEP
# ==============================

def collect(limit=None, holding_days=20, threshold=70, step=DEFAULT_STEP,
            start=FLOW_BREADTH_FROM, end=None, verbose=True):
    """Signals across the universe, each carrying its point-in-time scores.

    `start` and `end` bound the SIGNAL dates. Bars before `start` are still
    loaded -- the indicators and the flow baseline need history behind the
    first signal, and cutting the frame at `start` would silently shorten
    every warmup and quietly change what the early signals mean.
    """

    instruments = database.load_instruments(equities_only=True)
    instruments.sort(key=lambda r: str(r.get("ins_code")))

    if limit:
        stride = max(1, len(instruments) // limit)
        instruments = instruments[::stride][:limit]

    start_ts = pd.Timestamp(start) if start else None
    end_ts = pd.Timestamp(end) if end else None

    if verbose:
        print(f"symbols: {len(instruments)}   holding: {holding_days}d   "
              f"threshold: {threshold}   signals: {start} .. {end or 'latest'}")

    trades = []
    covered = 0
    skipped_thin = 0

    for n, row in enumerate(instruments, start=1):

        code = str(row.get("ins_code"))
        symbol = row.get("symbol_fa")

        bars = database.load_history_by_inscode(code)

        if bars is None or bars.empty:
            continue

        # Keep the warmup, drop only what is past the window's end.
        if end_ts is not None:
            bars = bars[bars["date"] <= end_ts + pd.Timedelta(days=90)]

        if len(bars) < 120:
            skipped_thin += 1
            continue

        flow_rows = database.load_client_type_history(code)

        if flow_rows is not None and not flow_rows.empty:
            covered += 1

        found = signals_for(
            code, symbol, bars, flow_rows,
            holding_days=holding_days, threshold=threshold, step=step,
            signal_from=start_ts, signal_to=end_ts,
        )

        trades.extend(found)

        if verbose and n % 100 == 0:
            print(f"  {n}/{len(instruments)} scanned, {len(trades)} signals")

    if verbose:
        print(f"done: {len(trades)} signals, {covered} symbols had flow data"
              + (f", {skipped_thin} too thin" if skipped_thin else ""))

    return trades


# ==============================
# REPORTING
# ==============================

def _stats(trades, key="return"):
    values = [t[key] for t in trades if t.get(key) is not None]

    if not values:
        return None

    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "n": len(values),
        "win_rate": len(wins) / len(values) * 100,
        "mean": sum(values) / len(values),
        "median": statistics.median(values),
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def _print_stats(label, stats, width=34):
    if not stats:
        print(f"{label:<{width}} no data")
        return

    pf = stats["profit_factor"]

    print(f"{label:<{width}} {stats['n']:>6} {stats['win_rate']:>7.1f} "
          f"{stats['mean']:>9.2f} {stats['median']:>9.2f} "
          f"{stats['avg_win']:>9.2f} {stats['avg_loss']:>10.2f} "
          f"{(f'{pf:.2f}' if pf else '-'):>9}")


def _header(title, width=34):
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)
    print(f"{'bucket':<{width}} {'n':>6} {'win%':>7} {'mean%':>9} "
          f"{'median%':>9} {'avgwin%':>9} {'avgloss%':>10} {'PF':>9}")
    print("-" * 100)


def report(trades, exit_key="return"):
    """Everything the trade list can answer about the flow component."""

    label = "TIME EXIT" if exit_key == "return" else "TRAILING STOP EXIT"

    _header(f"ALL TECHNICALLY-APPROVED SIGNALS — {label}")
    _print_stats("all signals", _stats(trades, exit_key))

    # ---- the actual question ------------------------------------------
    with_flow = [t for t in trades if t.get("flow_score") is not None]
    without = [t for t in trades if t.get("flow_score") is None]

    _header(f"BY FLOW SCORE — does flow separate winners? — {label}")

    edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]

    for low, high in edges:
        bucket = [t for t in with_flow if low <= t["flow_score"] < high]
        _print_stats(f"flow {low}-{high - 1}", _stats(bucket, exit_key))

    print("-" * 100)
    _print_stats("flow score unavailable", _stats(without, exit_key))

    # ---- top vs bottom, stated plainly --------------------------------
    top = [t for t in with_flow if t["flow_score"] >= 60]
    bottom = [t for t in with_flow if t["flow_score"] < 40]

    top_stats = _stats(top, exit_key)
    bottom_stats = _stats(bottom, exit_key)

    print()
    print("-" * 100)

    if top_stats and bottom_stats:
        gap = top_stats["mean"] - bottom_stats["mean"]
        print(f"high flow (>=60) mean {top_stats['mean']:+.2f}%  "
              f"vs low flow (<40) mean {bottom_stats['mean']:+.2f}%  "
              f"-> gap {gap:+.2f} points over {top_stats['n']}/{bottom_stats['n']} trades")

        # A gap smaller than the noise in either bucket is not a finding.
        spread = max(abs(top_stats["mean"]), abs(bottom_stats["mean"]))
        if abs(gap) < 0.5:
            print("   the two buckets are indistinguishable — flow adds nothing here")
        elif abs(gap) < spread * 0.25:
            print("   the gap is small relative to the returns themselves — weak signal")
    else:
        print("not enough flow-scored trades to compare buckets")

    # ---- combined score, for completeness ------------------------------
    _header(f"BY COMBINED SCORE (technical 70 / flow 30) — {label}")

    weights = {"technical": 70, "fundamental": 0, "risk": 0, "flow": 30}

    for t in with_flow:
        blended = scoring.combine(
            technical=t["technical_score"], flow=t["flow_score"],
            weights=weights,
        )
        t["combined_score"] = blended.get("score")

    for low, high in [(70, 80), (80, 90), (90, 101)]:
        bucket = [t for t in with_flow
                  if t.get("combined_score") is not None
                  and low <= t["combined_score"] < high]
        _print_stats(f"combined {low}-{high - 1}", _stats(bucket, exit_key))

    # ---- technical alone, same trades, as the control ------------------
    _header(f"BY TECHNICAL SCORE (control) — {label}")

    for low, high in [(70, 80), (80, 90), (90, 101)]:
        bucket = [t for t in trades if low <= t["technical_score"] < high]
        _print_stats(f"technical {low}-{high - 1}", _stats(bucket, exit_key))


def regime_summary(trades, exit_key="return"):
    """One line per regime: does the flow edge survive a falling market?"""

    with_flow = [t for t in trades if t.get("flow_score") is not None]

    high = [t for t in with_flow if t["flow_score"] >= 60]
    low = [t for t in with_flow if t["flow_score"] < 40]

    all_stats = _stats(trades, exit_key)
    high_stats = _stats(high, exit_key)
    low_stats = _stats(low, exit_key)

    gap = None
    if high_stats and low_stats:
        gap = high_stats["mean"] - low_stats["mean"]

    return {
        "n": all_stats["n"] if all_stats else 0,
        "win_rate": all_stats["win_rate"] if all_stats else None,
        "mean": all_stats["mean"] if all_stats else None,
        "profit_factor": all_stats["profit_factor"] if all_stats else None,
        "high_flow_mean": high_stats["mean"] if high_stats else None,
        "high_flow_n": high_stats["n"] if high_stats else 0,
        "low_flow_mean": low_stats["mean"] if low_stats else None,
        "low_flow_n": low_stats["n"] if low_stats else 0,
        "flow_gap": gap,
    }


def run_regimes(limit=None, holding_days=20, threshold=70, step=DEFAULT_STEP,
                names=None):
    """The same rule, measured separately inside each market regime."""

    selected = names or list(REGIMES)

    rows = []

    for name in selected:

        if name not in REGIMES:
            print(f"unknown regime: {name}")
            continue

        start, end, index_move = REGIMES[name]

        print()
        print("#" * 100)
        print(f"# REGIME {name}   {start} .. {end}   TEDPIX {index_move}")
        print("#" * 100)

        trades = collect(
            limit=limit, holding_days=holding_days, threshold=threshold,
            step=step, start=start, end=end,
        )

        if not trades:
            print("  no signals in this window")
            rows.append((name, index_move, None, None))
            continue

        rows.append((name, index_move,
                     regime_summary(trades, "return"),
                     regime_summary(trades, "trail_return")))

    # ---- the comparison the whole exercise exists for -------------------
    print()
    print("=" * 112)
    print("REGIME COMPARISON — time exit")
    print("=" * 112)
    print(f"{'regime':<12} {'TEDPIX':>9} {'n':>6} {'win%':>7} {'mean%':>8} "
          f"{'PF':>7} {'hiFlow%':>9} {'loFlow%':>9} {'gap':>8}")
    print("-" * 112)

    for name, move, timed, _ in rows:
        if not timed:
            print(f"{name:<12} {move:>9}   no signals")
            continue
        pf = timed["profit_factor"]
        print(f"{name:<12} {move:>9} {timed['n']:>6} {timed['win_rate']:>7.1f} "
              f"{timed['mean']:>8.2f} {(f'{pf:.2f}' if pf else '-'):>7} "
              f"{_fmt(timed['high_flow_mean']):>9} {_fmt(timed['low_flow_mean']):>9} "
              f"{_fmt(timed['flow_gap']):>8}")

    print()
    print("=" * 112)
    print("REGIME COMPARISON — trailing stop exit")
    print("=" * 112)
    print(f"{'regime':<12} {'TEDPIX':>9} {'n':>6} {'win%':>7} {'mean%':>8} "
          f"{'PF':>7} {'hiFlow%':>9} {'loFlow%':>9} {'gap':>8}")
    print("-" * 112)

    for name, move, _, trailed in rows:
        if not trailed:
            print(f"{name:<12} {move:>9}   no signals")
            continue
        pf = trailed["profit_factor"]
        print(f"{name:<12} {move:>9} {trailed['n']:>6} {trailed['win_rate']:>7.1f} "
              f"{trailed['mean']:>8.2f} {(f'{pf:.2f}' if pf else '-'):>7} "
              f"{_fmt(trailed['high_flow_mean']):>9} {_fmt(trailed['low_flow_mean']):>9} "
              f"{_fmt(trailed['flow_gap']):>8}")

    print()
    print("gap = mean return of flow>=60 minus mean return of flow<40, inside")
    print("that regime. A rule that only works in the rising rows is a rule")
    print("that has been fitted to a rising market.")

    return rows


def _fmt(value):
    return f"{value:+.2f}" if value is not None else "-"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure whether the flow component improves the ranking."
    )
    parser.add_argument("--limit", type=int, default=150,
                        help="symbols to sample (default 150)")
    parser.add_argument("--holding", type=int, default=20,
                        help="sessions held (default 20)")
    parser.add_argument("--threshold", type=int, default=70,
                        help="technical score required to signal (default 70)")
    parser.add_argument("--step", type=int, default=DEFAULT_STEP,
                        help="bars between scans (default 3)")
    parser.add_argument("--start", default=FLOW_BREADTH_FROM,
                        help="earliest signal date")
    parser.add_argument("--end", default=None, help="latest signal date")
    parser.add_argument("--regimes", nargs="*", default=None,
                        help="run the regime comparison; "
                             f"names: {', '.join(REGIMES)}")

    args = parser.parse_args(argv)

    if args.regimes is not None:
        run_regimes(
            limit=args.limit, holding_days=args.holding,
            threshold=args.threshold, step=args.step,
            names=args.regimes or None,
        )
        return 0

    trades = collect(
        limit=args.limit, holding_days=args.holding,
        threshold=args.threshold, step=args.step, start=args.start,
        end=args.end,
    )

    if not trades:
        print("no signals — nothing to report")
        return 1

    report(trades, "return")
    report(trades, "trail_return")

    print()
    print("=" * 100)
    print("NOT TESTED: the fundamental component. codal_financials holds only")
    print("the two most recent reporting periods, so there is no point-in-time")
    print("fundamental score to score a past date with. Any figure produced for")
    print("it here would be hindsight, not evidence.")
    print("=" * 100)

    return 0


if __name__ == "__main__":
    sys.exit(main())
