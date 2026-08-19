"""Does any factor in SIRAT-AI predict anything? A diagnostic, not a redesign.

WHY THIS EXISTS
---------------
The portfolio study established that V2 returns 0.04% against an index that
returned 171.8%, and that a fully-invested top-10 basket of the same ranking
returns 136.7% against the index's 144.2%. The selection is the problem, and
"the selection" is six factors blended into one score.

This module takes the blend apart. It changes NO formula: every factor is read
from the same functions the live engine calls -- ranking.rank_stocks(),
factor_engine.momentum(), factor_engine.liquidity(), fundamental_engine -- and
the sub-readings (RSI, MACD, ATR, volume) are lifted straight out of the
indicator frame. What is new is only the MEASUREMENT: four forward horizons per
observation instead of one, deciles, information coefficients, and a window that
starts long before the weights were fitted.

THE ONE THING THAT MAKES THIS WORTH RUNNING
-------------------------------------------
The shipped weights were chosen on 2024-09-01 .. 2026-06-30. Every number this
project has quoted about them is in-sample. The price history in this database
goes back ~3,000 sessions, so the factors that need only prices CAN be measured
on a decade the fit never saw. The fundamental store cannot: it holds 2,194
filings and almost nothing before 1402, so the fundamental factor is untestable
out of sample and that is reported rather than papered over.

    python research_factors.py --build            the panel (slow, once)
    python research_factors.py --build --start 2015-01-01
    python research_factors.py                    the analysis (seconds)
"""

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

import backtest_config
import config
import factor_engine
import fundamental_engine
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from ranking import rank_stocks


PANEL = os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")

REPORT = os.path.join(backtest_config.OUTPUT_DIR, "factor_research.json")

# Forward horizons in sessions. 5/10 are the technical score's own horizon,
# 20 is what the system trades, 60 is the momentum factor's middle window.
HORIZONS = (5, 10, 20, 60)

# The date the shipped weights were fitted from. Everything before it is
# genuinely out of sample for those weights; everything after is not.
FIT_START = pd.Timestamp("2024-09-01")

# Every column that is a candidate signal. The first six are the factors the
# Swing Score blends; the rest are the readings underneath them, included so
# that "the technical score does not work" can be separated from "no technical
# input works".
SIGNALS = (
    "f_momentum", "f_technical", "f_fundamental", "f_liquidity",
    "rsi", "macd_hist_norm", "ma_gap_pct", "atr_pct", "volume_ratio",
    "volatility_60", "ret_20", "ret_60", "ret_120", "adx", "bb_pct",
)

LABELS = {
    "f_momentum": "Momentum factor (20/60/120 + RS)",
    "f_technical": "Technical score (ranking.py)",
    "f_fundamental": "Fundamental score (4 pillars)",
    "f_liquidity": "Liquidity factor",
    "swing": "Swing Score (shipped weights)",
    "swing_equal": "Swing Score (equal weights)",
    "rsi": "RSI(14)",
    "macd_hist_norm": "MACD histogram / price",
    "ma_gap_pct": "Price vs slow MA (%)",
    "atr_pct": "ATR as % of price",
    "volume_ratio": "Volume / 20d average",
    "volatility_60": "60-session volatility",
    "ret_20": "Raw 20-session return",
    "ret_60": "Raw 60-session return",
    "ret_120": "Raw 120-session return",
    "adx": "ADX(14)",
    "bb_pct": "Bollinger %B",
}


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# THE PANEL
# ==============================

def build(start=None, end=None, every=20, limit=None, path=None, verbose=True):
    """One row per (date, symbol): every factor, and four forward returns."""

    from backtesting.pit import PointInTimeData

    path = path or PANEL

    cfg = backtest_config.load()

    data = PointInTimeData(cfg, verbose=False)

    calendar = data.calendar()

    if len(calendar) == 0:
        print("no calendar; run seed_import.py --indices")
        return None

    start = pd.Timestamp(start) if start else calendar[0]
    end = pd.Timestamp(end) if end else calendar[-1]

    dates = data.rebalance_dates(start, end, every)

    instruments = data.instruments()

    codes = sorted(instruments)

    if limit:
        codes = codes[:int(limit)]

    params = DEFAULT_PARAMS

    warmup = max(required_bars(params), max(config.MOMENTUM_HORIZONS) + 1)

    index_frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)

    index_closes = {}

    if index_frame is not None and not index_frame.empty:
        stamps = pd.DatetimeIndex(index_frame["date"])
        values = index_frame["close"].tolist()
        for date in dates:
            cut = int(stamps.searchsorted(date, side="right"))
            index_closes[date] = values[:cut] if cut else []

    if verbose:
        print("=" * 78)
        print("FACTOR PANEL  %s .. %s" % (start.date(), end.date()))
        print("=" * 78)
        print("  decision dates %d every %d sessions" % (len(dates), every))
        print("  symbols        %d" % len(codes))
        print("  horizons       %s" % (HORIZONS,))

    rows = []

    scanned = skipped = 0

    started = time.time()

    for number, ins_code in enumerate(codes, start=1):

        meta = instruments[ins_code]

        bars = data.bars(ins_code)

        if bars is None or len(bars) < warmup + max(HORIZONS) + 5:
            skipped += 1
            continue

        try:
            frame = calculate_indicators(bars, params)
        except Exception:
            skipped += 1
            continue

        scanned += 1

        _observe(rows, frame, bars, dates, ins_code, meta, data, cfg,
                 index_closes, warmup, params)

        if verbose and number % 100 == 0:
            print("  %d/%d symbols, %d rows, %.0fs"
                  % (number, len(codes), len(rows), time.time() - started),
                  flush=True)

    frame = pd.DataFrame(rows)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    frame.to_csv(path, index=False, encoding="utf-8-sig")

    if verbose:
        print("\n  %d symbols scanned, %d skipped" % (scanned, skipped))
        print("  wrote %d rows to %s" % (len(frame), path))

    return frame


def _observe(rows, frame, bars, dates, ins_code, meta, data, cfg,
             index_closes, warmup, params):
    """Every date this symbol can be scored on."""

    bar_dates = bars["date"]

    closes = frame["close"].tolist()

    volumes = bars["volume"].tolist() if "volume" in bars.columns else []
    values = bars["value"].tolist() if "value" in bars.columns else []

    def column(name):
        return frame[name].tolist() if name in frame else []

    rsi = column("RSI")
    macd_hist = column("MACD_HIST")
    ma_slow = column("MA_SLOW")
    atr_pct = column("ATR_PCT")
    volume_ma = column("VOLUME_MA")
    adx = column("ADX")
    bb_pct = column("BB_PCT")

    for date in dates:

        index = int(bar_dates.searchsorted(date, side="right")) - 1

        if index < warmup:
            continue

        stale = data.stale_sessions(ins_code, date)

        if stale is not None and stale > cfg.max_stale_sessions:
            continue

        close = _finite(closes[index])

        if not close or close <= 0:
            continue

        # ---- forward returns, strictly after the decision bar --------------
        forwards = {}

        for horizon in HORIZONS:
            exit_index = index + horizon
            if exit_index >= len(closes):
                forwards["fwd_%d" % horizon] = None
                continue
            exit_close = _finite(closes[exit_index])
            forwards["fwd_%d" % horizon] = (
                None if not exit_close or exit_close <= 0
                else round((exit_close - close) / close * 100.0, 3))

        if all(v is None for v in forwards.values()):
            continue

        # ---- the factors, from the same functions the engine calls ---------
        window = frame.iloc[max(0, index - 59):index + 1]

        try:
            technical = rank_stocks(window)
        except Exception:
            technical = None

        technical_score = (technical.get("score")
                           if technical and technical.get("scored") else None)

        momentum = factor_engine.momentum(
            closes[:index + 1], index_closes=index_closes.get(date))

        adv = None

        if values:
            tail = [_finite(v) for v in values[max(0, index - 19):index + 1]]
            tail = [v for v in tail if v is not None]
            adv = sum(tail) / len(tail) if tail else None

        volume_ratio = None

        if volume_ma and volumes:
            base = _finite(volume_ma[index])
            volume = _finite(volumes[index])
            if base and base > 0 and volume is not None:
                volume_ratio = volume / base

        liquidity = factor_engine.liquidity(
            adv=adv, volume_ratio=volume_ratio,
            traded_sessions_pct=factor_engine.traded_share(
                volumes[max(0, index - 19):index + 1]))

        fundamental = _fundamental_at(data, cfg, ins_code, date, close,
                                      meta.get("sector"))

        # ---- sub-readings, lifted straight out of the frame ----------------
        gap = None

        level = _finite(ma_slow[index]) if ma_slow else None

        if level and level > 0:
            gap = (close - level) / level * 100.0

        hist = _finite(macd_hist[index]) if macd_hist else None

        returns = {}

        for back in (20, 60, 120):
            past = closes[index - back] if index >= back else None
            past = _finite(past)
            returns["ret_%d" % back] = (
                None if not past or past <= 0
                else round((close - past) / past * 100.0, 3))

        window_closes = [c for c in closes[max(0, index - 59):index + 1]
                         if _finite(c)]

        volatility = None

        if len(window_closes) > 5:
            series = pd.Series(window_closes).pct_change().std()
            volatility = _finite(series * math.sqrt(
                config.TRADING_DAYS_PER_YEAR) * 100)

        rows.append({
            "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
            "ins_code": ins_code,
            "symbol": meta.get("symbol"),
            "sector": meta.get("sector") or "?",
            "close": close,

            "f_momentum": momentum.get("score"),
            "f_technical": technical_score,
            "f_fundamental": fundamental,
            "f_liquidity": liquidity.get("score"),

            "rsi": _finite(rsi[index]) if rsi else None,
            "macd_hist_norm": (None if hist is None
                               else round(hist / close * 100.0, 4)),
            "ma_gap_pct": None if gap is None else round(gap, 3),
            "atr_pct": _finite(atr_pct[index]) if atr_pct else None,
            "volume_ratio": (None if volume_ratio is None
                             else round(volume_ratio, 3)),
            "volatility_60": (None if volatility is None
                              else round(volatility, 2)),
            "adx": _finite(adx[index]) if adx else None,
            "bb_pct": _finite(bb_pct[index]) if bb_pct else None,
            "adv": adv,
            **returns,
            **forwards,
        })


def _fundamental_at(data, cfg, ins_code, date, close, sector):
    """The v2 fundamental score from filings public before this session."""

    try:
        history = data.fundamental_history(
            ins_code, date,
            periods=int(getattr(cfg, "fundamental_trend_periods", 4) or 4))
    except Exception:
        return None

    if not history:
        return None

    try:
        ratios = data.market_ratios(history[-1], close, sector=sector)
    except Exception:
        return None

    result = fundamental_engine.evaluate(
        ratios=ratios,
        history=[dict(r, ratios=r.get("ratios") or {}) for r in history])

    return result.get("score")


# ==============================
# ANALYSIS
# ==============================

def load(path=None):

    path = path or PANEL

    if not os.path.exists(path):
        raise SystemExit("no panel at %s — run --build first" % path)

    frame = pd.read_csv(path, encoding="utf-8-sig")

    frame["date"] = pd.to_datetime(frame["date"])

    return frame


def add_scores(frame):
    """The blended scores, from factor_engine's own arithmetic."""

    import weight_optimizer as wo

    shipped = {
        factor_engine.REGIME: config.SWING_WEIGHT_REGIME,
        factor_engine.MOMENTUM: config.SWING_WEIGHT_MOMENTUM,
        factor_engine.TECHNICAL: config.SWING_WEIGHT_TECHNICAL,
        factor_engine.FUNDAMENTAL: config.SWING_WEIGHT_FUNDAMENTAL,
        factor_engine.LIQUIDITY: config.SWING_WEIGHT_LIQUIDITY,
        factor_engine.CATALYST: config.SWING_WEIGHT_CATALYST,
    }

    equal = {name: (0 if name == factor_engine.REGIME else 25)
             for name in factor_engine.FACTORS}
    equal[factor_engine.CATALYST] = 0

    work = frame.copy()

    if "f_regime" not in work:
        work["f_regime"] = np.nan
    if "f_catalyst" not in work:
        work["f_catalyst"] = np.nan

    frame["swing"] = wo.score(work, shipped)
    frame["swing_equal"] = wo.score(work, equal)

    return frame


def information_coefficient(frame, signal, horizon):
    """Spearman rank correlation between the signal and the forward return.

    Computed WITHIN each date and then averaged, never pooled. A pooled
    correlation over a rising market rewards any signal that happens to be
    high when the whole market is about to rise, which measures the calendar
    rather than the signal.
    """

    column = "fwd_%d" % horizon

    per_date = []

    for date, block in frame.groupby("date"):

        rows = block[[signal, column]].dropna()

        if len(rows) < 30:
            continue

        a = rows[signal].rank()
        b = rows[column].rank()

        if a.std() == 0 or b.std() == 0:
            continue

        per_date.append(float(a.corr(b)))

    if len(per_date) < 5:
        return {"ic": None, "dates": len(per_date)}

    values = np.array(per_date)

    mean = float(values.mean())
    stderr = float(values.std(ddof=1) / math.sqrt(len(values)))

    return {
        "ic": round(mean, 4),
        "ic_stderr": round(stderr, 4),
        "t_stat": round(mean / stderr, 2) if stderr > 0 else None,
        "dates": len(values),
        "hit_rate": round(float((values > 0).mean()) * 100, 1),
    }


def deciles(frame, signal, horizon, cost_pct=None):
    """Top and bottom decile forward returns, formed within each date."""

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    column = "fwd_%d" % horizon

    top, bottom, spread, market = [], [], [], []

    for date, block in frame.groupby("date"):

        rows = block[[signal, column]].dropna()

        if len(rows) < 30:
            continue

        n = max(1, len(rows) // 10)

        ordered = rows.sort_values(signal, ascending=False)

        high = float(ordered[column].head(n).mean())
        low = float(ordered[column].tail(n).mean())

        top.append(high)
        bottom.append(low)
        spread.append(high - low)
        market.append(float(rows[column].mean()))

    if len(top) < 5:
        return {"dates": len(top)}

    top = np.array(top)
    bottom = np.array(bottom)
    spread = np.array(spread)
    market = np.array(market)

    excess = top - market

    stderr = float(excess.std(ddof=1) / math.sqrt(len(excess)))

    return {
        "dates": len(top),
        "top_pct": round(float(top.mean()), 3),
        "bottom_pct": round(float(bottom.mean()), 3),
        "spread_pct": round(float(spread.mean()), 3),
        "market_pct": round(float(market.mean()), 3),
        "alpha_pct": round(float(excess.mean()), 3),
        "alpha_t": round(float(excess.mean()) / stderr, 2) if stderr > 0 else None,
        "alpha_net_pct": round(float(excess.mean()) - cost_pct, 3),
        "beat_market_pct": round(float((excess > 0).mean()) * 100, 1),
    }


def basket(frame, signal, horizon, top_n=10, cost_pct=None):
    """Compounded top-N equal-weight basket against the equal-weight market."""

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    column = "fwd_%d" % horizon

    equity = 1.0
    market = 1.0

    periods = 0

    for date, block in frame.groupby("date"):

        rows = block[[signal, column]].dropna()

        if len(rows) < 30:
            continue

        ordered = rows.sort_values(signal, ascending=False)

        picked = float(ordered[column].head(top_n).mean()) - cost_pct

        equity *= (1.0 + picked / 100.0)
        market *= (1.0 + float(rows[column].mean()) / 100.0)

        periods += 1

    if not periods:
        return {}

    return {
        "periods": periods,
        "basket_pct": round((equity - 1.0) * 100, 1),
        "market_pct": round((market - 1.0) * 100, 1),
        "excess_pct": round((equity - market) * 100, 1),
    }


def analyse(frame, label, horizons=HORIZONS, signals=None):
    """Every signal, every horizon."""

    signals = signals or (list(SIGNALS) + ["swing", "swing_equal"])

    out = {}

    for signal in signals:

        if signal not in frame:
            continue

        rows = {}

        for horizon in horizons:

            if "fwd_%d" % horizon not in frame:
                continue

            ic = information_coefficient(frame, signal, horizon)
            dec = deciles(frame, signal, horizon)

            rows[horizon] = {**ic, **dec}

        out[signal] = rows

    return {"label": label, "rows": out,
            "observations": len(frame),
            "dates": int(frame["date"].nunique())}


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Which factor, if any, predicts anything.")

    parser.add_argument("--build", action="store_true")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--every", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--path", default=None)

    args = parser.parse_args(argv)

    if args.build:
        built = build(start=args.start, end=args.end, every=args.every,
                      limit=args.limit, path=args.path)
        return 0 if built is not None else 1

    frame = load(args.path)

    frame = add_scores(frame)

    before = frame[frame["date"] < FIT_START]
    after = frame[frame["date"] >= FIT_START]

    payload = {
        "panel": {"observations": len(frame),
                  "dates": int(frame["date"].nunique()),
                  "from": str(frame["date"].min().date()),
                  "to": str(frame["date"].max().date())},
        "all": analyse(frame, "full window"),
        "out_of_sample": analyse(before, "before the fit (%s)"
                                 % FIT_START.date()),
        "in_sample": analyse(after, "the fitted window"),
    }

    payload["baskets"] = {
        signal: {
            "out_of_sample": basket(before, signal, 20),
            "in_sample": basket(after, signal, 20),
        }
        for signal in list(SIGNALS) + ["swing", "swing_equal"]
        if signal in frame
    }

    with open(REPORT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    print("wrote %s" % REPORT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
