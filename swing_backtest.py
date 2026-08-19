"""Does the Swing Score sort by outcome? Five measurements, on point-in-time data.

WHAT THIS ANSWERS AND WHY IT IS NOT A PORTFOLIO BACKTEST
-------------------------------------------------------
The `backtesting/` package already runs a full portfolio simulation with
execution modelling, queues, costs, walk-forward validation and Monte Carlo. That
machinery is untouched and is where a strategy is judged.

This module answers a narrower question that has to be answered FIRST: does the
Swing Score sort names by what happened next? A score that does not rank by
outcome cannot be improved by better execution, and measuring it inside a
portfolio simulation hides the answer behind position sizing, sector caps and
cash drag. So this is a point-in-time SIGNAL study, in exactly the form
backtest_combined.py used to establish that the technical score does not sort
above its own gate -- the finding that shaped the live selection rule.

    1. buckets()          does a higher Swing Score mean a better forward return
    2. filter_comparison() does the fundamental filter earn its 15% of weight
    3. drawdown           worst peak-to-trough of an equal-weight signal book
    4. win rate           share of signals that finished positive, per bucket
    5. weight_sensitivity() how much of the result is the weights we chose

THE THREE HONESTY RULES THIS INHERITS
-------------------------------------
  NO FUTURE ROWS. Every reading comes from backtesting/pit.PointInTimeData, which
  refuses them, and the forward return is read from bars strictly AFTER the
  decision date.

  THE PARTICIPATION FIGURE IS COMPUTED, NOT SKIPPED. A regime factor worth 20% of
  the score cannot be replaced by a constant 50 in the study that validates the
  score. regime.participation() costs a full-universe pass per date, which is
  hours over a few hundred dates, so the flag is collected during the pass this
  module already makes over every symbol's bars -- and voted on by
  regime.assemble(), the same function detect() uses, rather than by a second copy
  of the rule.

  COSTS ARE SUBTRACTED. A round trip on this exchange is ~1.04%, and a study that
  reports gross returns will show a positive edge for any signal at all on a
  market whose median name rose.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No position sizing, no sector caps, no queue modelling, no compounding. Each
signal is one equal-weight observation held for a fixed number of sessions. Those
omissions are what make the measurement about the SCORE; adding them back is what
`run_v2.py` is for.
"""

import argparse
import math
import sys
import time

import pandas as pd

import config
import factor_engine
import fundamental_engine
import regime
import regime_engine
import scoring
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars


# Sessions a signal is held. 20 is one trading month and is what
# backtest_combined.py used, so the two studies are comparable.
DEFAULT_HOLD = 20

# Swing Score bucket edges. Deliberately aligned to the action bands in config so
# the table answers "did the BUY band outperform the HOLD band" rather than
# "did some arbitrary decile outperform".
DEFAULT_EDGES = (0, 40, 50, 60, config.SWING_BUY_SCORE, 80, 101)

# The weight a switched-off factor is given when the sweep puts it back in, to
# ask whether it should have been switched off. 20 because that is what the
# regime factor carried before it was measured and removed.
REINSTATE_WEIGHT = 20


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# BUCKETS
# ==============================

def buckets(observations, key="swing", edges=None, cost_pct=None):
    """Group observations by a score and report what followed each band.

    The columns are chosen so a flat mean cannot hide a bad distribution:

        n            how many signals -- a band of nine tells you nothing
        mean         average net forward return
        median       the typical outcome, which a few outliers cannot move
        win_rate     share that finished positive
        profit_factor  gross gains over gross losses
        worst        the single worst outcome in the band

    MONOTONICITY IS THE POINT. A score that ranks by outcome produces means that
    rise across the bands. One that does not is still usable as a GATE -- that is
    exactly what the technical score turned out to be -- but must not be used to
    order a basket, and the only way to know which it is, is this table.
    """

    edges = tuple(edges or DEFAULT_EDGES)

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    rows = []

    for low, high in zip(edges, edges[1:]):

        band = [
            observation for observation in observations
            if _finite(observation.get(key)) is not None
            and low <= observation[key] < high
        ]

        returns = [_finite(o.get("forward_pct")) for o in band]
        returns = [r - cost_pct for r in returns if r is not None]

        rows.append(_stats("%d-%d" % (low, high - 1), returns))

    unscored = [o for o in observations if _finite(o.get(key)) is None]

    return {
        "key": key,
        "edges": edges,
        "cost_pct": cost_pct,
        "rows": rows,
        "unscored": len(unscored),
        "monotonic": _monotonic([r["mean"] for r in rows if r["n"] >= 30]),
    }


def _stats(label, returns):

    if not returns:
        return {"band": label, "n": 0, "mean": None, "median": None,
                "win_rate": None, "profit_factor": None, "worst": None,
                "best": None}

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    gains = sum(wins)
    pain = -sum(losses)

    ordered = sorted(returns)

    middle = len(ordered) // 2

    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2.0)

    return {
        "band": label,
        "n": len(returns),
        "mean": round(sum(returns) / len(returns), 2),
        "median": round(median, 2),
        "win_rate": round(len(wins) / float(len(returns)) * 100, 1),
        "profit_factor": (round(gains / pain, 2) if pain > 0
                          else (float("inf") if gains > 0 else None)),
        "worst": round(min(returns), 2),
        "best": round(max(returns), 2),
    }


def _monotonic(means):
    """Do the band means rise? Returns True/False/None when there is too little."""

    values = [m for m in means if m is not None]

    if len(values) < 3:
        return None

    return all(b >= a for a, b in zip(values, values[1:]))


# ==============================
# THE FUNDAMENTAL FILTER
# ==============================

def filter_comparison(observations, floor=None, cost_pct=None):
    """What the fundamental filter keeps, what it discards, and what that cost.

    Three populations rather than two, and the third is the one usually omitted:

        all          every signal
        passed       fundamental score at or above the floor
        no_reading   NO FUNDAMENTAL DATA AT ALL

    A comparison that reports only the first two answers the wrong question. On
    this market most names have no filing in the store on most dates, so
    "filtered" and "unfiltered" differ mostly in how many uncovered companies they
    dropped -- which measures Codal coverage, not the fundamental model. Splitting
    the uncovered names out is what separates "the filter works" from "the covered
    companies happen to be the large liquid ones".
    """

    floor = config.FUND_APPROVE_SCORE if floor is None else floor

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    def net(rows):
        values = [_finite(o.get("forward_pct")) for o in rows]
        return [v - cost_pct for v in values if v is not None]

    scored = [o for o in observations
              if _finite(o.get("fundamental")) is not None]
    blind = [o for o in observations
             if _finite(o.get("fundamental")) is None]

    passed = [o for o in scored if o["fundamental"] >= floor]
    failed = [o for o in scored if o["fundamental"] < floor]

    return {
        "floor": floor,
        "cost_pct": cost_pct,
        "all": _stats("all signals", net(observations)),
        "with_reading": _stats("has a filing", net(scored)),
        "passed": _stats("fundamental >= %d" % floor, net(passed)),
        "failed": _stats("fundamental < %d" % floor, net(failed)),
        "no_reading": _stats("no filing in the store", net(blind)),
        "coverage": (round(len(scored) / float(len(observations)), 4)
                     if observations else None),
        "verdict": _filter_verdict(_stats("p", net(passed)),
                                   _stats("f", net(failed)),
                                   _stats("b", net(blind))),
    }


def _filter_verdict(passed, failed, blind):
    """Did the filter separate outcomes, or only separate coverage?"""

    if passed["n"] < 30 or failed["n"] < 30:
        return ("INSUFFICIENT: %d signals passed and %d failed the filter; "
                "neither side supports a conclusion (need 30 each)"
                % (passed["n"], failed["n"]))

    gap = passed["mean"] - failed["mean"]

    if gap <= 0:
        return ("NO EDGE: names passing the filter returned %.2f%% against "
                "%.2f%% for names failing it. The fundamental gate is not "
                "separating outcomes on this window."
                % (passed["mean"], failed["mean"]))

    note = ("SEPARATES: +%.2f%% for the passed set over the failed set "
            "(%.2f%% vs %.2f%%)" % (gap, passed["mean"], failed["mean"]))

    if blind["n"] >= 30 and blind["mean"] >= passed["mean"]:
        note += (". CAVEAT: names with NO filing returned %.2f%%, at least as "
                 "much as the ones that passed -- so the gate may be selecting "
                 "for Codal coverage rather than for fundamentals."
                 % blind["mean"])

    return note


# ==============================
# DRAWDOWN AND THE SIGNAL BOOK
# ==============================

def signal_book(observations, min_score=None, hold=DEFAULT_HOLD,
                cost_pct=None):
    """An equal-weight curve from the signals that cleared a score.

    Not a portfolio: every signal on a date gets an equal share of that date's
    capital, there is no sizing and no cash drag, and dates with no signal are
    flat. That makes the resulting drawdown a property of the SIGNAL rather than
    of an allocation policy -- which is what is being measured. A real book's
    drawdown will differ, and `run_v2.py` is where that number comes from.
    """

    min_score = config.SWING_BUY_SCORE if min_score is None else min_score

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    by_date = {}

    for observation in observations:

        score = _finite(observation.get("swing"))
        forward = _finite(observation.get("forward_pct"))

        if score is None or forward is None or score < min_score:
            continue

        by_date.setdefault(observation["date"], []).append(forward - cost_pct)

    if not by_date:
        return {"periods": 0, "curve": [], "max_drawdown_pct": None,
                "total_return_pct": None, "win_rate": None, "signals": 0,
                "min_score": min_score,
                "reason": "no signal cleared %d" % min_score}

    equity = 1.0

    curve = []
    period_returns = []

    for date in sorted(by_date):

        batch = by_date[date]

        mean = sum(batch) / len(batch)

        period_returns.append(mean)

        equity *= (1.0 + mean / 100.0)

        curve.append({"date": date, "equity": round(equity, 6),
                      "signals": len(batch), "return_pct": round(mean, 3)})

    peak = 0.0
    worst = 0.0

    for point in curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            worst = min(worst, (point["equity"] - peak) / peak * 100.0)

    wins = sum(1 for r in period_returns if r > 0)

    return {
        "periods": len(curve),
        "signals": sum(len(v) for v in by_date.values()),
        "curve": curve,
        "min_score": min_score,
        "hold_sessions": hold,
        "total_return_pct": round((equity - 1.0) * 100.0, 2),
        "max_drawdown_pct": round(worst, 2),
        "win_rate": round(wins / float(len(period_returns)) * 100.0, 1),
        "mean_period_pct": round(sum(period_returns) / len(period_returns), 3),
        "reason": None,
        "caveat": ("این منحنی سبد نیست: هر سیگنال وزن مساوی دارد، اندازه‌گذاری و "
                   "سقف صنعت اعمال نشده، و افت سرمایه‌ی یک سبد واقعی با این عدد "
                   "یکی نیست."),
    }


# ==============================
# WEIGHT SENSITIVITY
# ==============================

def weight_sensitivity(observations, variants=None, min_score=None,
                       cost_pct=None):
    """Re-score every observation under different factor weights.

    THE QUESTION THIS ANSWERS is not "which weights are best" -- picking the best
    row of this table and shipping it is precisely how a set of weights gets
    fitted to one window. It answers "how much of the result is the weights",
    which is the opposite question. A ranking that survives every variant is a
    ranking that came from the factors; one that only works at 25% momentum came
    from the sweep.

    Re-scoring is cheap because each observation carries its factor readings, so
    no bars are re-read and no indicators are recomputed.
    """

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    min_score = config.SWING_BUY_SCORE if min_score is None else min_score

    variants = variants or default_variants()

    rows = []

    for label, weights in variants:

        rescored = []

        for observation in observations:

            factors = observation.get("factors") or {}

            blend = factor_engine.swing_score(
                regime=factors.get(factor_engine.REGIME),
                momentum=factors.get(factor_engine.MOMENTUM),
                technical=factors.get(factor_engine.TECHNICAL),
                fundamental=factors.get(factor_engine.FUNDAMENTAL),
                liquidity=factors.get(factor_engine.LIQUIDITY),
                catalyst=factors.get(factor_engine.CATALYST),
                weights=weights,
            )

            if blend["score"] is None:
                continue

            rescored.append({"swing": blend["score"],
                             "date": observation["date"],
                             "forward_pct": observation.get("forward_pct")})

        table = buckets(rescored, cost_pct=cost_pct)

        selected = [o for o in rescored
                    if _finite(o["swing"]) is not None
                    and o["swing"] >= min_score]

        net = [_finite(o.get("forward_pct")) for o in selected]
        net = [v - cost_pct for v in net if v is not None]

        rows.append({
            "variant": label,
            "weights": weights,
            "selected": len(selected),
            "selected_mean": (round(sum(net) / len(net), 2) if net else None),
            "selected_win_rate": (round(sum(1 for v in net if v > 0)
                                        / float(len(net)) * 100, 1)
                                  if net else None),
            "monotonic": table["monotonic"],
        })

    means = [r["selected_mean"] for r in rows if r["selected_mean"] is not None]

    spread = (round(max(means) - min(means), 2) if len(means) >= 2 else None)

    return {
        "rows": rows,
        "spread_pct": spread,
        "verdict": _sensitivity_verdict(rows, spread),
    }


def default_variants():
    """The weight grid. Each variant moves ONE factor and renormalises the rest.

    Moving one at a time is what makes the row attributable. A grid that varies
    three weights at once produces a best row nobody can explain, and explaining
    it is the entire purpose.
    """

    base = factor_engine.default_weights()

    variants = [("baseline", dict(base))]

    for factor in factor_engine.FACTORS:

        if not base[factor]:
            # A factor already at zero cannot be halved or doubled: every
            # multiple of zero is zero, so all three variants would silently be
            # the baseline and the factor would vanish from a grid that claims
            # to cover it. The live question for a switched-off factor is
            # whether switching it back ON hurts -- which is what the regime
            # weight being 0 rests on, and it has to stay re-testable.
            weights = dict(base)
            weights[factor] = REINSTATE_WEIGHT

            variants.append(("%s on" % factor, weights))
            continue

        for multiple, name in ((0.0, "off"), (0.5, "half"), (2.0, "double")):

            weights = dict(base)
            weights[factor] = base[factor] * multiple

            variants.append(("%s %s" % (factor, name), weights))

    # Two structural variants: no market read at all, and market read only.
    equal = {factor: 100.0 / len(factor_engine.FACTORS)
             for factor in factor_engine.FACTORS}

    variants.append(("equal weights", equal))

    return variants


def _sensitivity_verdict(rows, spread):

    if spread is None:
        return "INSUFFICIENT: fewer than two variants produced a selection"

    baseline = next((r for r in rows if r["variant"] == "baseline"), None)

    if baseline is None or baseline["selected_mean"] is None:
        return "INSUFFICIENT: the baseline weights produced no selection"

    better = [r for r in rows
              if r["selected_mean"] is not None
              and r["selected_mean"] > baseline["selected_mean"]]

    note = ("the selected set's mean return spans %.2f percentage points across "
            "%d weight variants" % (spread, len(rows)))

    if spread > abs(baseline["selected_mean"]):
        return ("FRAGILE: " + note + ", which is more than the baseline result "
                "itself (%.2f%%). The weights are carrying the result."
                % baseline["selected_mean"])

    if len(better) > len(rows) * 0.6:
        return ("MISCALIBRATED: " + note + "; %d of %d variants beat the "
                "baseline, so the chosen weights are not a local optimum."
                % (len(better), len(rows)))

    return "ROBUST: " + note + ", with the baseline in the upper half."


# ==============================
# BUILDING THE OBSERVATIONS
# ==============================

def build_observations(cfg, data, dates, hold=DEFAULT_HOLD, params=None,
                       limit=None, verbose=True, use_regime=True):
    """One row per (date, symbol) with every factor and the forward return.

    ONE PASS PER SYMBOL, not one per (symbol, date). The indicator frame over a
    full history costs about what a single date's costs, so computing it once and
    reading it at each date is the difference between a minute and an afternoon --
    the same reason backtesting/strategy.py builds its panel this way.

    The regime is assembled AFTER the pass, because the participation figure it
    needs is an aggregate over the symbols the pass has just visited.
    """

    params = params or DEFAULT_PARAMS

    dates = [pd.Timestamp(d) for d in dates]

    instruments = data.instruments()

    codes = sorted(instruments)

    if limit:
        codes = codes[:int(limit)]

    import backtest_config

    index_frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)

    index_closes_by_date = _index_closes(index_frame, dates)

    warmup = max(required_bars(params), max(config.MOMENTUM_HORIZONS) + 1)

    # {date: [above_slow_ma bools]} -- the participation figure, collected free.
    breadth = {date: [0, 0] for date in dates}

    partial = []

    scanned = 0
    skipped = 0

    started = time.time()

    for number, ins_code in enumerate(codes, start=1):

        meta = instruments[ins_code]

        bars = data.bars(ins_code)

        if bars is None or len(bars) < warmup + hold + 5:
            skipped += 1
            continue

        try:
            enriched = calculate_indicators(bars, params)
        except Exception:
            skipped += 1
            continue

        scanned += 1

        if verbose and scanned % 100 == 0:
            print("  observations: %d/%d symbols, %.0fs"
                  % (number, len(codes), time.time() - started), flush=True)

        _observe_symbol(partial, enriched, bars, dates, ins_code, meta, data,
                        cfg, hold, index_closes_by_date, breadth, params)

    if verbose:
        print("  observations: %d symbols scanned, %d skipped, %d rows"
              % (scanned, skipped, len(partial)), flush=True)

    regime_by_date = ({} if not use_regime
                      else _regimes(data, cfg, dates, breadth, params,
                                    verbose=verbose))

    # The regime factor is written in last, because it is a property of the DATE
    # and every row on that date shares it.
    for row in partial:

        assessment = regime_by_date.get(row["date"])

        row["regime_label"] = (assessment or {}).get("spec_name")
        row["trading_mode"] = (assessment or {}).get("trading_mode")

        row["factors"][factor_engine.REGIME] = (assessment or {}).get("score")

        blend = factor_engine.swing_score(
            regime=row["factors"].get(factor_engine.REGIME),
            momentum=row["factors"].get(factor_engine.MOMENTUM),
            technical=row["factors"].get(factor_engine.TECHNICAL),
            fundamental=row["factors"].get(factor_engine.FUNDAMENTAL),
            liquidity=row["factors"].get(factor_engine.LIQUIDITY),
            catalyst=row["factors"].get(factor_engine.CATALYST),
        )

        row["swing"] = blend["score"]
        row["coverage"] = blend["coverage"]

    return {
        "observations": partial,
        "dates": dates,
        "symbols": scanned,
        "skipped": skipped,
        "hold_sessions": hold,
        "regimes": regime_by_date,
        "used_regime": bool(use_regime),
    }


def _index_closes(frame, dates):
    """{date: the index closes up to and including that date}."""

    if frame is None or frame.empty:
        return {}

    closes = frame["close"].tolist()
    stamps = pd.DatetimeIndex(frame["date"])

    out = {}

    for date in dates:
        cut = int(stamps.searchsorted(date, side="right"))
        out[date] = closes[:cut] if cut else []

    return out


def _observe_symbol(rows, enriched, bars, dates, ins_code, meta, data, cfg,
                    hold, index_closes_by_date, breadth, params):
    """Write one symbol's rows for every date it can be scored on."""

    from ranking import rank_stocks

    bar_dates = bars["date"]

    closes = enriched["close"].tolist()

    slow = enriched["MA_SLOW"].tolist() if "MA_SLOW" in enriched else []

    values = bars["value"].tolist() if "value" in bars.columns else []
    volumes = bars["volume"].tolist() if "volume" in bars.columns else []

    warmup = max(required_bars(params), max(config.MOMENTUM_HORIZONS) + 1)

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

        # ---- participation, for the regime vote --------------------------
        if slow:
            level = _finite(slow[index])
            if level is not None:
                breadth[date][1] += 1
                if close > level:
                    breadth[date][0] += 1

        # ---- the forward return ------------------------------------------
        #
        # Strictly after the decision bar. `index + hold` and not `index + hold
        # - 1`: the position is opened on the bar AFTER the decision, so the
        # first session of the hold is index+1.
        exit_index = index + hold

        if exit_index >= len(closes):
            continue

        exit_close = _finite(closes[exit_index])

        if not exit_close or exit_close <= 0:
            continue

        forward = (exit_close - close) / close * 100.0

        # ---- the factors --------------------------------------------------

        window = enriched.iloc[max(0, index - 59):index + 1]

        try:
            technical = rank_stocks(window)
        except Exception:
            technical = None

        technical_score = (technical.get("score")
                           if technical and technical.get("scored") else None)

        momentum = factor_engine.momentum(
            closes[:index + 1],
            index_closes=index_closes_by_date.get(date))

        adv = None

        if values:
            tail = [_finite(v) for v in values[max(0, index - 19):index + 1]]
            tail = [v for v in tail if v is not None]
            adv = sum(tail) / len(tail) if tail else None

        volume_ratio = None

        if "VOLUME_MA" in enriched:
            baseline = _finite(enriched["VOLUME_MA"].iloc[index])
            volume = _finite(volumes[index]) if volumes else None
            if baseline and baseline > 0 and volume is not None:
                volume_ratio = volume / baseline

        liquidity = factor_engine.liquidity(
            adv=adv, volume_ratio=volume_ratio,
            traded_sessions_pct=factor_engine.traded_share(
                volumes[max(0, index - 19):index + 1]))

        fundamental = _fundamental_at(data, cfg, ins_code, date, close,
                                      meta.get("sector"))

        rows.append({
            "date": date,
            "ins_code": ins_code,
            "symbol": meta.get("symbol"),
            "sector": meta.get("sector") or "?",
            "close": close,
            "forward_pct": round(forward, 3),
            "technical": technical_score,
            "fundamental": fundamental,
            "momentum": momentum.get("score"),
            "liquidity": liquidity.get("score"),
            "adv": adv,
            "factors": {
                factor_engine.MOMENTUM: momentum.get("score"),
                factor_engine.TECHNICAL: technical_score,
                factor_engine.FUNDAMENTAL: fundamental,
                factor_engine.LIQUIDITY: liquidity.get("score"),
                # No news archive exists, so this factor is absent by design
                # rather than by omission. See factor_engine.catalyst().
                factor_engine.CATALYST: None,
                factor_engine.REGIME: None,      # written in after the pass
            },
        })


def _fundamental_at(data, cfg, ins_code, date, close, sector):
    """The v2 fundamental score from filings public before this session closed.

    Uses the SAME point-in-time machinery the existing backtest uses --
    pit.fundamental_history() and pit.market_ratios() -- so the valuation
    multiples are recomputed at this date's price rather than read off disk with
    today's price in them.
    """

    try:
        history = data.fundamental_history(
            ins_code, date,
            periods=int(getattr(cfg, "fundamental_trend_periods", 4) or 4))
    except Exception:
        return None

    if not history:
        return None

    newest = history[-1]

    try:
        ratios = data.market_ratios(newest, close, sector=sector)
    except Exception:
        return None

    result = fundamental_engine.evaluate(
        ratios=ratios,
        history=[dict(record, ratios=record.get("ratios") or {})
                 for record in history],
    )

    return result.get("score")


def _regimes(data, cfg, dates, breadth, params, verbose=True):
    """A regime read per date, voted by regime.assemble().

    The index states are cheap -- one series, cut per date. The participation
    figure is the expensive input and it has already been counted during the
    symbol pass, which is the whole reason assemble() was split out of detect().
    """

    if verbose:
        print("  regimes: %d dates" % len(dates), flush=True)

    turnover_series = _turnover(data, dates)

    out = {}

    for date in dates:

        cap = regime.index_state("TEDPIX", params=params, as_of=date)
        equal = regime.index_state("TEDPIX_EW", params=params, as_of=date)

        above, counted = breadth.get(date, (0, 0))

        participation = None

        if counted:
            participation = {
                "instruments": counted,
                "above_slow_ma_pct": round(above / counted * 100, 2),
                # Not collected: this study needs the slow-MA share only, and
                # counting the fast one too would double the work for a figure
                # the vote does not read.
                "above_fast_ma_pct": None,
                "slow_ma": params["ma_slow"],
                "fast_ma": params["ma_fast"],
            }

        report = regime.assemble(cap, equal, participation, params=params)

        history = turnover_series.get(date)

        out[date] = regime_engine.assess(
            report,
            turnover=(regime_engine.turnover_state(history) if history
                      else {"state": None, "reason": "no turnover series"}),
        )

    return out


def _turnover(data, dates):
    """{date: turnover values up to that date}, from the stored market totals."""

    try:
        import database
        frame = database.market_turnover()
    except Exception:
        return {}

    if frame is None or frame.empty:
        return {}

    stamps = pd.DatetimeIndex(frame["date"])
    values = frame["turnover"].tolist()

    out = {}

    for date in dates:
        cut = int(stamps.searchsorted(date, side="right"))
        if cut:
            out[date] = values[:cut]

    return out


# ==============================
# THE REPORT
# ==============================

def run(start=None, end=None, hold=DEFAULT_HOLD, limit=None, verbose=True,
        use_regime=True, cfg=None):
    """The whole study. Returns everything it computed."""

    import backtest_config
    from backtesting.pit import PointInTimeData

    cfg = cfg or backtest_config.load()

    if limit:
        cfg = cfg.replace(universe_limit=limit)

    data = PointInTimeData(cfg, verbose=verbose)

    calendar = data.calendar()

    if len(calendar) == 0:
        print("no trading calendar: the index history is empty. "
              "Run `python seed_import.py --indices` first.")
        return None

    start = pd.Timestamp(start or backtest_config.DEFAULT_START)
    end = pd.Timestamp(end) if end else calendar[-1]

    dates = data.rebalance_dates(start, end, cfg.rebalance_sessions)

    if verbose:
        print("=" * 78)
        print("SIRAT-AI — SWING SCORE STUDY   %s .. %s"
              % (start.date(), end.date()))
        print("=" * 78)
        print("  decision dates    %d (every %d sessions)"
              % (len(dates), cfg.rebalance_sessions))
        print("  hold              %d sessions" % hold)
        print("  round trip cost   %.3f%%" % config.TRANSACTION_COST_PCT)

    built = build_observations(cfg, data, dates, hold=hold, limit=limit,
                               verbose=verbose, use_regime=use_regime)

    observations = built["observations"]

    if not observations:
        print("\nno scoreable observation in this window.")
        return built

    out = dict(built)

    out["swing_buckets"] = buckets(observations, key="swing")
    out["technical_buckets"] = buckets(observations, key="technical")
    out["momentum_buckets"] = buckets(observations, key="momentum")
    out["filter"] = filter_comparison(observations)
    out["book"] = signal_book(observations, hold=hold)
    out["sensitivity"] = weight_sensitivity(observations)

    if verbose:
        print_report(out)

    return out


def _table(rows, title):

    lines = ["", title, "-" * len(title),
             "  %-10s %6s %8s %8s %8s %7s %8s"
             % ("band", "n", "mean", "median", "win%", "PF", "worst")]

    for row in rows:
        if not row["n"]:
            lines.append("  %-10s %6d        —" % (row["band"], 0))
            continue
        lines.append(
            "  %-10s %6d %7.2f%% %7.2f%% %7.1f%% %7s %7.2f%%"
            % (row["band"], row["n"], row["mean"], row["median"],
               row["win_rate"],
               "inf" if row["profit_factor"] == float("inf")
               else ("—" if row["profit_factor"] is None
                     else "%.2f" % row["profit_factor"]),
               row["worst"]))

    return lines


def print_report(out):

    print()
    print("=" * 78)
    print("RESULTS — %d observations over %d symbols and %d dates"
          % (len(out["observations"]), out["symbols"], len(out["dates"])))
    print("=" * 78)

    print("\nAll returns are NET of a %.3f%% round trip and cover %d sessions "
          "after\nthe decision bar." % (config.TRANSACTION_COST_PCT,
                                        out["hold_sessions"]))

    for key, title in (("swing_buckets", "1. SWING SCORE — does it sort by outcome"),
                       ("technical_buckets", "   technical score, for comparison"),
                       ("momentum_buckets", "   momentum factor, for comparison")):

        table = out[key]

        for line in _table(table["rows"], title):
            print(line)

        verdict = table["monotonic"]

        print("  monotonic across bands with n>=30: %s"
              % {True: "YES — higher scores did return more",
                 False: "NO — the score does not order outcomes above its own "
                        "bands, which makes it a gate rather than a ranking",
                 None: "too few observations per band to say"}[verdict])

    # ---- 2. the fundamental filter ---------------------------------------
    filter_result = out["filter"]

    print("\n\n2. FUNDAMENTAL FILTER — before and after")
    print("-" * 40)
    print("  coverage: %.1f%% of signals had a filing in the store"
          % ((filter_result["coverage"] or 0) * 100))

    for key in ("all", "with_reading", "passed", "failed", "no_reading"):
        row = filter_result[key]
        if not row["n"]:
            print("  %-26s      — no observation" % row["band"])
            continue
        print("  %-26s n=%-6d mean %7.2f%%  win %5.1f%%"
              % (row["band"], row["n"], row["mean"], row["win_rate"]))

    print("\n  %s" % filter_result["verdict"])

    # ---- 3 & 4. drawdown and win rate ------------------------------------
    book = out["book"]

    print("\n\n3. DRAWDOWN AND WIN RATE — equal-weight signal book")
    print("-" * 50)

    if book.get("reason"):
        print("  %s" % book["reason"])
    else:
        print("  signals              %d over %d decision dates"
              % (book["signals"], book["periods"]))
        print("  total return         %.2f%%" % book["total_return_pct"])
        print("  max drawdown         %.2f%%" % book["max_drawdown_pct"])
        print("  periods won          %.1f%%" % book["win_rate"])
        print("  mean period          %.3f%%" % book["mean_period_pct"])
        print("\n  %s" % book["caveat"])

    # ---- 5. weight sensitivity -------------------------------------------
    sensitivity = out["sensitivity"]

    print("\n\n4. WEIGHT SENSITIVITY — how much of this is the weights")
    print("-" * 55)
    print("  %-22s %8s %9s %8s" % ("variant", "selected", "mean", "win%"))

    for row in sensitivity["rows"]:
        print("  %-22s %8d %8s %8s"
              % (row["variant"], row["selected"],
                 "—" if row["selected_mean"] is None
                 else "%.2f%%" % row["selected_mean"],
                 "—" if row["selected_win_rate"] is None
                 else "%.1f%%" % row["selected_win_rate"]))

    print("\n  %s" % sensitivity["verdict"])

    if not out.get("used_regime"):
        print("\n\nNOTE: the regime factor was DISABLED for this run, so the "
              "Swing Score\nabove is a five-factor score renormalised over 80% "
              "of the model.")


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Measure whether the Swing Score sorts by outcome.")

    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--hold", type=int, default=DEFAULT_HOLD,
                        help="sessions held per signal (default %d)" % DEFAULT_HOLD)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the universe, for a quick run")
    parser.add_argument("--no-regime", action="store_true",
                        help="skip the per-date regime read (faster, and the "
                             "score is then a five-factor one)")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    result = run(start=args.start, end=args.end, hold=args.hold,
                 limit=args.limit, verbose=not args.quiet,
                 use_regime=not args.no_regime)

    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
