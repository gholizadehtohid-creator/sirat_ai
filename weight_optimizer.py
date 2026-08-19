"""Which factor weights survive out of sample -- the study behind the v2 weights.

WHY THIS EXISTS BESIDE swing_backtest.py
----------------------------------------
`swing_backtest.py` answers "does the Swing Score sort by outcome" and includes a
one-factor-at-a-time sensitivity sweep whose stated purpose is to show how much of
a result is the weights. It deliberately does NOT choose weights: picking the best
row of that table and shipping it is exactly how a weight vector gets fitted to one
window.

This module does choose -- and the only reason it is allowed to is that it never
judges a weight vector on the window it was fitted on. Every number that decides
anything here comes from dates the fit did not see:

    in-sample / out-of-sample   fit on the first 60% of decision dates, judged on
                                the last 40%
    walk-forward                four expanding folds; each fold fits on its past
                                and is judged on the block that follows
    plateau, not peak           a vector is scored together with its grid
                                neighbours, so a lucky spike between two bad
                                points cannot win

WHAT IS BEING OPTIMISED, AND WHAT THAT WORD MEANS HERE
------------------------------------------------------
Not return. The objective is the annualised Sharpe of an equal-weight basket of
the top N names per decision date, subject to a minimum trade count -- and among
vectors whose out-of-sample results are statistically indistinguishable, the
simplest and most stable one wins rather than the highest one. With 24
non-overlapping periods the difference between the best and the tenth-best grid
point is noise, and treating it as signal is the failure this whole module is
built to avoid.

THE EXPENSIVE PART RUNS ONCE
----------------------------
Building the observations is a point-in-time pass over every symbol's bars, every
indicator and every filing -- minutes, not seconds. Re-scoring those observations
under a different weight vector is arithmetic on numbers already computed. So the
pass is run once by `--build`, cached to CSV, and every weight vector in the grid
is scored from that cache. The scorer is vectorised for speed and CHECKED against
factor_engine.swing_score() on a random sample at every run (`--verify`), because
a fast copy of the scoring rule that silently drifts from the real one would
invalidate every number below.

WHAT THIS IS NOT
----------------
Not a portfolio backtest. No sizing, no sector caps, no cash drag, no queue
modelling; each selected name is one equal-weight observation held for a fixed
number of sessions net of a round trip. Those omissions are what make the
measurement about the WEIGHTS. `run_v2.py` and the `backtesting/` package are
where a strategy is judged.
"""

import argparse
import itertools
import math
import os
import random
import sys

import numpy as np
import pandas as pd

import config
import factor_engine
import swing_backtest


# The cache of point-in-time observations. One row per (decision date, symbol),
# carrying every factor reading and the realised forward return.
DEFAULT_CACHE = os.path.join("backtest_output", "swing_observations.csv")

# Rebalance every 20 sessions on a ~240-session year.
PERIODS_PER_YEAR = 12.0

# The six factor columns as they are stored. Regime is kept in the cache even
# though it is no longer blended, because "the regime factor adds nothing to a
# ranking" is a claim this study has to be able to re-test rather than assume.
COLUMNS = {
    factor_engine.REGIME: "f_regime",
    factor_engine.MOMENTUM: "f_momentum",
    factor_engine.TECHNICAL: "f_technical",
    factor_engine.FUNDAMENTAL: "f_fundamental",
    factor_engine.LIQUIDITY: "f_liquidity",
    factor_engine.CATALYST: "f_catalyst",
}

# The weights that produced every measured result before this study. Kept as a
# literal rather than read from config, because config is what this study
# changes and the "before" column has to survive that change.
LEGACY_WEIGHTS = {
    factor_engine.REGIME: 20,
    factor_engine.MOMENTUM: 25,
    factor_engine.TECHNICAL: 20,
    factor_engine.FUNDAMENTAL: 15,
    factor_engine.LIQUIDITY: 10,
    factor_engine.CATALYST: 10,
}

# The specification's opening proposal for the regime-free score. A grid point
# like any other -- it is reported alongside the fitted vectors and wins only if
# the out-of-sample numbers say it should.
PROPOSED_WEIGHTS = {
    factor_engine.REGIME: 0,
    factor_engine.MOMENTUM: 30,
    factor_engine.TECHNICAL: 30,
    factor_engine.FUNDAMENTAL: 20,
    factor_engine.LIQUIDITY: 15,
    factor_engine.CATALYST: 5,
}

# The search space the specification asks for, in percent, on a 5-point step.
# Only combinations summing to 100 are kept.
GRID_RANGES = {
    factor_engine.MOMENTUM: range(20, 41, 5),
    factor_engine.TECHNICAL: range(20, 41, 5),
    factor_engine.FUNDAMENTAL: range(10, 31, 5),
    factor_engine.LIQUIDITY: range(5, 21, 5),
    factor_engine.CATALYST: range(0, 16, 5),
}

# Names taken per decision date. PORTFOLIO_SIZE, so the basket being measured is
# the size the portfolio engine would actually hold.
DEFAULT_TOP_N = config.PORTFOLIO_SIZE

# Plateau-Sharpe difference below which two weight vectors are called equal. On
# 14 training periods the standard error of a Sharpe estimate is roughly 1/sqrt(14)
# ~ 0.27, so 0.05 is well inside the noise and the band is if anything too tight.
TIE_BAND = 0.05


# ==============================
# THE CACHE
# ==============================

def build(start=None, end=None, hold=swing_backtest.DEFAULT_HOLD, limit=None,
          path=None, verbose=True):
    """Run the point-in-time pass once and write every observation to CSV."""

    import backtest_config
    from backtesting.pit import PointInTimeData

    path = path or DEFAULT_CACHE

    cfg = backtest_config.load()

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
        print("BUILDING OBSERVATIONS   %s .. %s" % (start.date(), end.date()))
        print("=" * 78)
        print("  decision dates    %d (every %d sessions)"
              % (len(dates), cfg.rebalance_sessions))
        print("  hold              %d sessions" % hold)

    built = swing_backtest.build_observations(cfg, data, dates, hold=hold,
                                              limit=limit, verbose=verbose)

    rows = built["observations"]

    if not rows:
        print("no scoreable observation in this window.")
        return None

    frame = pd.DataFrame([_flatten(row) for row in rows])

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    frame.to_csv(path, index=False, encoding="utf-8-sig")

    if verbose:
        print("\n  wrote %d observations over %d dates and %d symbols to %s"
              % (len(frame), frame["date"].nunique(),
                 frame["ins_code"].nunique(), path))

    return frame


def _flatten(row):
    """One observation dict -> one flat CSV row."""

    factors = row.get("factors") or {}

    out = {
        "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
        "ins_code": row.get("ins_code"),
        "symbol": row.get("symbol"),
        "sector": row.get("sector"),
        "close": row.get("close"),
        "forward_pct": row.get("forward_pct"),
        "adv": row.get("adv"),
        "trading_mode": row.get("trading_mode"),
        "regime_label": row.get("regime_label"),
        # The score the engine produced AT BUILD TIME, under whatever weights
        # were in config then. Never used in a calculation -- it exists so that
        # a rebuild after a weight change is visibly a different file.
        "swing_at_build": row.get("swing"),
    }

    for factor, column in COLUMNS.items():
        out[column] = factors.get(factor)

    return out


def load(path=None):
    """The cached observations as a frame, oldest date first."""

    path = path or DEFAULT_CACHE

    if not os.path.exists(path):
        raise SystemExit(
            "no observation cache at %s — run `python weight_optimizer.py "
            "--build` first (it takes a few minutes)." % path)

    frame = pd.read_csv(path, encoding="utf-8-sig")

    frame["date"] = pd.to_datetime(frame["date"])

    return frame.sort_values(["date", "ins_code"]).reset_index(drop=True)


# ==============================
# SCORING, VECTORISED
# ==============================

def score(frame, weights, min_coverage=None):
    """Every row's Swing Score under `weights`, as a float array with NaN gaps.

    The arithmetic is factor_engine.swing_score()'s, in array form: renormalise
    over the weight that has a reading, refuse below the coverage floor, clamp,
    round. verify() below checks the two agree.
    """

    min_coverage = (config.SWING_MIN_COVERAGE if min_coverage is None
                    else min_coverage)

    model_weight = float(sum(weights.values())) or 1.0

    readings = {}
    covered = np.zeros(len(frame), dtype=float)

    # factor_engine.FACTORS order, and the order matters. The engine accumulates
    # `weights[name] * scale * value` term by term in this sequence, and float
    # addition is not associative: summing the same terms in another order moves
    # the total by ~1e-14, which is invisible until a score lands on an exact
    # .5 and rounds the other way. Six rows in 1500 did exactly that before this
    # loop was written to match. verify() is what caught it.
    for factor in factor_engine.FACTORS:

        weight = float(weights.get(factor) or 0.0)

        if weight <= 0:
            # A zero-weighted factor contributes nothing to the blend AND
            # nothing to the covered weight -- the same as being absent, which
            # is what factor_engine does with it.
            continue

        values = pd.to_numeric(frame[COLUMNS[factor]],
                               errors="coerce").to_numpy(float)

        present = np.isfinite(values)

        readings[factor] = (weight, values, present)

        covered += np.where(present, weight, 0.0)

    scoreable = covered > 0

    with np.errstate(invalid="ignore", divide="ignore"):
        scale = np.where(scoreable, 1.0 / np.where(scoreable, covered, 1.0), 0.0)

    total = np.zeros(len(frame), dtype=float)

    for factor in factor_engine.FACTORS:

        if factor not in readings:
            continue

        weight, values, present = readings[factor]

        total += np.where(present, (weight * scale) * np.nan_to_num(values), 0.0)

    # The engine rounds coverage to two places BEFORE comparing it to the floor,
    # so a model at 0.495 refuses rather than passing on the third decimal.
    coverage = np.round(covered / model_weight, 2)

    blended = np.round(np.clip(total, 0.0, 100.0))

    return np.where(scoreable & (coverage >= min_coverage), blended, np.nan)


def verify(frame, samples=300, seed=17):
    """Cross-check the fast scorer against factor_engine on random rows.

    Runs on every invocation. A vectorised copy of a scoring rule that drifts
    from the rule the live system uses would make every table below a fiction,
    and the failure would be silent.
    """

    rng = random.Random(seed)

    variants = [LEGACY_WEIGHTS, PROPOSED_WEIGHTS] + [
        dict(zip(GRID_RANGES, combo), **{factor_engine.REGIME: 0})
        for combo in [(30, 25, 20, 15, 10), (20, 40, 30, 5, 5),
                      (40, 20, 10, 20, 10)]
    ]

    indices = [rng.randrange(len(frame)) for _ in range(min(samples, len(frame)))]

    mismatches = []

    for weights in variants:

        fast = score(frame, weights)

        for i in indices:

            row = frame.iloc[i]

            slow = factor_engine.swing_score(
                regime=_none_if_nan(row[COLUMNS[factor_engine.REGIME]]),
                momentum=_none_if_nan(row[COLUMNS[factor_engine.MOMENTUM]]),
                technical=_none_if_nan(row[COLUMNS[factor_engine.TECHNICAL]]),
                fundamental=_none_if_nan(row[COLUMNS[factor_engine.FUNDAMENTAL]]),
                liquidity=_none_if_nan(row[COLUMNS[factor_engine.LIQUIDITY]]),
                catalyst=_none_if_nan(row[COLUMNS[factor_engine.CATALYST]]),
                weights=weights,
            )

            expected = slow["score"]

            got = fast[i]

            if expected is None:
                if np.isfinite(got):
                    mismatches.append((i, weights, expected, got))
            elif not np.isfinite(got) or int(got) != int(expected):
                mismatches.append((i, weights, expected, got))

    return {
        "checked": len(indices) * len(variants),
        "mismatches": len(mismatches),
        "examples": mismatches[:5],
    }


def _none_if_nan(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(number) else number


# ==============================
# EVALUATION
# ==============================

def evaluate(frame, scores, dates=None, top_n=DEFAULT_TOP_N, min_score=None,
             gate=None, cost_pct=None, periods_per_year=PERIODS_PER_YEAR):
    """What an equal-weight basket of the top names would have done.

    ONE BASKET PER DECISION DATE. The names are ranked by Swing Score, the top
    `top_n` are taken, each gets an equal share of that date's capital, and the
    period return is their mean net forward return. Periods compound.

    `gate` applies the measured technical floor before ranking, because that is
    what factor_engine.decide() does: the Swing Score reorders what passes the
    gate, it does not license entering what fails it. A row with no technical
    reading passes, exactly as in decide().

    `min_score` additionally requires the Swing Score itself to clear a band. It
    is None for the ranking view -- a fixed threshold on a score whose scale
    moves with the weights would confound "these weights rank better" with
    "these weights produce bigger numbers".
    """

    cost_pct = (config.TRANSACTION_COST_PCT if cost_pct is None else cost_pct)

    gate = config.TECHNICAL_GATE if gate is None else gate

    forward = pd.to_numeric(frame["forward_pct"], errors="coerce").to_numpy(float)

    technical = pd.to_numeric(frame[COLUMNS[factor_engine.TECHNICAL]],
                              errors="coerce").to_numpy(float)

    stamps = frame["date"].to_numpy()

    eligible = np.isfinite(scores) & np.isfinite(forward)

    if gate is not None:
        # NaN technical passes, as in decide().
        eligible &= ~(np.isfinite(technical) & (technical < gate))

    if min_score is not None:
        eligible &= scores >= min_score

    dates = list(dates) if dates is not None else sorted(set(stamps.tolist()))

    period_returns = []
    trade_returns = []
    counts = []

    for date in dates:

        mask = eligible & (stamps == date)

        if not mask.any():
            continue

        picked = np.flatnonzero(mask)

        if top_n:
            order = np.argsort(-scores[picked], kind="stable")
            picked = picked[order[:int(top_n)]]

        net = forward[picked] - cost_pct

        period_returns.append(float(net.mean()))
        trade_returns.extend(net.tolist())
        counts.append(len(picked))

    return _performance(period_returns, trade_returns, counts,
                        periods_per_year)


def _performance(period_returns, trade_returns, counts, periods_per_year):

    if not period_returns:
        return {"periods": 0, "trades": 0, "mean_period_pct": None,
                "total_return_pct": None, "cagr_pct": None,
                "max_drawdown_pct": None, "sharpe": None,
                "win_rate_period": None, "win_rate_trade": None,
                "avg_basket": None, "period_returns": []}

    equity = 1.0
    peak = 1.0
    worst = 0.0

    for value in period_returns:
        equity *= (1.0 + value / 100.0)
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak * 100.0)

    array = np.array(period_returns, dtype=float)

    mean = float(array.mean())

    # Sample standard deviation. With 10-24 periods the population figure
    # flatters every Sharpe in the table by the same factor, which is exactly
    # the kind of quiet optimism a study that ranks by Sharpe cannot afford.
    deviation = float(array.std(ddof=1)) if len(array) > 1 else 0.0

    # THE RISK-FREE RATE IS SUBTRACTED, and this was a real defect rather than
    # a simplification. Every other Sharpe in this project comes from
    # performance.py, which charges the policy rate as a hurdle; this one did
    # not, so the two were never comparable and this one was flattered by
    # roughly RISK_FREE_RATE / volatility. On a market whose risk-free rate is
    # ~23% that is not a rounding difference: the same book scores well above
    # zero here and below zero in the portfolio backtest.
    #
    # It is compounded per PERIOD rather than divided, for the reason
    # performance.daily_risk_free() states about compounding.
    hurdle = ((1.0 + config.RISK_FREE_RATE) ** (1.0 / periods_per_year) - 1.0) * 100.0

    sharpe = (None if deviation <= 0
              else round((mean - hurdle) / deviation
                         * math.sqrt(periods_per_year), 3))

    years = len(array) / periods_per_year

    cagr = (round((equity ** (1.0 / years) - 1.0) * 100.0, 2)
            if years > 0 and equity > 0 else None)

    wins = [r for r in trade_returns if r > 0]

    return {
        "periods": len(array),
        "trades": len(trade_returns),
        "mean_period_pct": round(mean, 3),
        "std_period_pct": round(deviation, 3),
        "risk_free_period_pct": round(hurdle, 3),
        "total_return_pct": round((equity - 1.0) * 100.0, 2),
        "cagr_pct": cagr,
        "max_drawdown_pct": round(worst, 2),
        "sharpe": sharpe,
        "win_rate_period": round(sum(1 for r in array if r > 0)
                                 / float(len(array)) * 100.0, 1),
        "win_rate_trade": (round(len(wins) / float(len(trade_returns)) * 100.0, 1)
                           if trade_returns else None),
        "avg_basket": round(sum(counts) / float(len(counts)), 1),
        "period_returns": [round(float(v), 3) for v in array],
    }


# ==============================
# THE GRID
# ==============================

def grid(ranges=None, total=100):
    """Every weight vector in the specified ranges that sums to `total`."""

    ranges = ranges or GRID_RANGES

    factors = list(ranges)

    out = []

    for combo in itertools.product(*(ranges[f] for f in factors)):

        if sum(combo) != total:
            continue

        weights = {factor_engine.REGIME: 0}
        weights.update(dict(zip(factors, combo)))

        out.append(weights)

    return out


def key_of(weights):
    """A hashable, printable identity for a weight vector."""

    return tuple(int(weights.get(f) or 0) for f in factor_engine.FACTORS)


def label_of(weights):
    return "M%d/T%d/F%d/L%d/C%d" % (
        weights.get(factor_engine.MOMENTUM, 0),
        weights.get(factor_engine.TECHNICAL, 0),
        weights.get(factor_engine.FUNDAMENTAL, 0),
        weights.get(factor_engine.LIQUIDITY, 0),
        weights.get(factor_engine.CATALYST, 0),
    )


def neighbours(weights, candidates):
    """Grid points one 5-point step away on exactly two factors.

    A vector cannot move one factor alone and still sum to 100, so the
    neighbourhood of a point is the set of vectors reachable by moving 5 points
    from one factor to another. Averaging an objective over that set is what
    separates a plateau from a spike, and only a plateau survives new data.
    """

    here = np.array([weights.get(f, 0) for f in factor_engine.FACTORS],
                    dtype=float)

    out = []

    for other in candidates:

        there = np.array([other.get(f, 0) for f in factor_engine.FACTORS],
                         dtype=float)

        delta = np.abs(there - here)

        if delta.sum() == 10 and (delta > 0).sum() == 2:
            out.append(other)

    return out


# ==============================
# IN SAMPLE / OUT OF SAMPLE / WALK FORWARD
# ==============================

def split_dates(dates, train_share=0.6):
    """The chronological split. Never random -- shuffling dates would leak."""

    dates = sorted(dates)

    cut = max(1, int(round(len(dates) * train_share)))

    return dates[:cut], dates[cut:]


def folds(dates, min_train=8, test=4):
    """Expanding-window walk-forward folds: (train dates, test dates)."""

    dates = sorted(dates)

    out = []

    start = min_train

    while start + test <= len(dates):
        out.append((dates[:start], dates[start:start + test]))
        start += test

    return out


def sweep(frame, candidates, dates, **kwargs):
    """Evaluate every candidate on one set of dates. Returns a list of rows."""

    rows = []

    for weights in candidates:

        values = score(frame, weights)

        result = evaluate(frame, values, dates=dates, **kwargs)

        rows.append({"weights": weights, "key": key_of(weights),
                     "label": label_of(weights), **result})

    return rows


def rank(rows, min_periods=1):
    """Order by Sharpe, then by drawdown, then by mean. Unscoreable rows last."""

    def sortable(row):
        return (
            row["sharpe"] is not None and row["periods"] >= min_periods,
            row["sharpe"] if row["sharpe"] is not None else -99,
            row["max_drawdown_pct"] if row["max_drawdown_pct"] is not None else -99,
            row["mean_period_pct"] if row["mean_period_pct"] is not None else -99,
        )

    return sorted(rows, key=sortable, reverse=True)


def walk_forward(frame, candidates, dates, min_train=8, test=4, **kwargs):
    """Fit on the past, judge on the block that follows. Four folds here.

    Reports, per fold: the vector the training window would have chosen, what it
    then did on unseen dates, and what the two fixed vectors (legacy, proposed)
    did on the same unseen dates. The comparison is the whole point -- a fitted
    vector that cannot beat a fixed one out of sample is a fitted vector that
    learned the training window.
    """

    out = []

    for train, test_dates in folds(dates, min_train=min_train, test=test):

        fitted = rank(sweep(frame, candidates, train, **kwargs))[0]

        chosen = fitted["weights"]

        row = {
            "train_from": str(pd.Timestamp(train[0]).date()),
            "train_to": str(pd.Timestamp(train[-1]).date()),
            "test_from": str(pd.Timestamp(test_dates[0]).date()),
            "test_to": str(pd.Timestamp(test_dates[-1]).date()),
            "fitted_label": label_of(chosen),
            "fitted_in_sample": fitted,
            "fitted_out_of_sample": evaluate(
                frame, score(frame, chosen), dates=test_dates, **kwargs),
        }

        for name, weights in (("legacy", LEGACY_WEIGHTS),
                              ("proposed", PROPOSED_WEIGHTS)):
            row[name] = evaluate(frame, score(frame, weights),
                                 dates=test_dates, **kwargs)

        out.append(row)

    return out


def stability(frame, weights, dates, blocks=4, **kwargs):
    """The same vector measured on consecutive blocks of dates.

    A weight vector that earns its return in one block and gives it back in the
    next has a good average and no edge. The spread across blocks is what makes
    that visible.
    """

    dates = sorted(dates)

    size = max(1, len(dates) // blocks)

    values = score(frame, weights)

    out = []

    for start in range(0, len(dates), size):

        block = dates[start:start + size]

        if len(block) < 2:
            continue

        result = evaluate(frame, values, dates=block, **kwargs)

        out.append({
            "from": str(pd.Timestamp(block[0]).date()),
            "to": str(pd.Timestamp(block[-1]).date()),
            "mean_period_pct": result["mean_period_pct"],
            "win_rate_period": result["win_rate_period"],
            "max_drawdown_pct": result["max_drawdown_pct"],
            "sharpe": result["sharpe"],
        })

    means = [b["mean_period_pct"] for b in out if b["mean_period_pct"] is not None]

    return {
        "blocks": out,
        "worst_block_pct": min(means) if means else None,
        "spread_pct": (round(max(means) - min(means), 3)
                       if len(means) >= 2 else None),
        "positive_blocks": sum(1 for m in means if m > 0),
        "total_blocks": len(means),
    }


# ==============================
# THE REGIME, IN ITS NEW ROLE
# ==============================

def regime_table(frame, weights, top_n=DEFAULT_TOP_N, **kwargs):
    """What each trading mode's dates actually returned.

    This is the measurement behind the regime's new job. If the mode read is
    worth anything it shows up HERE -- as a difference between the outcomes of
    the dates it labelled -- rather than in a stock's score, where it is the same
    number for every name and cannot reorder anything.
    """

    values = score(frame, weights)

    modes = {}

    for mode, block in frame.groupby(frame["trading_mode"].fillna("UNKNOWN")):

        dates = sorted(set(block["date"].tolist()))

        result = evaluate(frame, values, dates=dates, top_n=top_n, **kwargs)

        modes[mode] = {
            "dates": len(dates),
            "mean_period_pct": result["mean_period_pct"],
            "win_rate_period": result["win_rate_period"],
            "worst_period_pct": (min(result["period_returns"])
                                 if result["period_returns"] else None),
            "max_drawdown_pct": result["max_drawdown_pct"],
        }

    return modes


def entry_floor_table(frame, weights, floors=(0, 5, 10, 15, 20),
                      modes=("DEFENSIVE", "SELECTIVE"), top_n=DEFAULT_TOP_N,
                      **kwargs):
    """Does demanding a higher score in a weak market pay for what it skips?

    The rule under test: in a `modes` session, a name must clear the buy band by
    `floor` extra points to be entered. The cost is the trades not taken; the
    hoped-for gain is a shallower drawdown. Both are reported, because a filter
    that improves the drawdown by refusing to trade at all has not improved
    anything.
    """

    values = score(frame, weights)

    stamps = frame["date"].to_numpy()

    weak_dates = set(
        frame.loc[frame["trading_mode"].isin(modes), "date"].tolist())

    all_dates = sorted(set(stamps.tolist()))

    rows = []

    for floor in floors:

        adjusted = values.copy()

        if floor:
            # Raising the required score on weak dates is equivalent to lowering
            # the score of every name on those dates, which keeps the ranking
            # inside the date intact and only changes what clears the band.
            weak = np.isin(stamps, list(weak_dates))
            adjusted = np.where(weak, adjusted - floor, adjusted)

        result = evaluate(frame, adjusted, dates=all_dates, top_n=top_n,
                          min_score=config.SWING_BUY_SCORE, **kwargs)

        rows.append({"extra_points": floor, **result})

    return rows


# ==============================
# THE REPORT
# ==============================

def run(path=None, top_n=DEFAULT_TOP_N, verbose=True, quick=False):

    frame = load(path)

    dates = sorted(set(frame["date"].tolist()))

    check = verify(frame, samples=60 if quick else 300)

    candidates = grid()

    if verbose:
        print("=" * 78)
        print("SIRAT-AI — WEIGHT OPTIMISATION")
        print("=" * 78)
        print("  observations      %d over %d symbols and %d decision dates"
              % (len(frame), frame["ins_code"].nunique(), len(dates)))
        print("  window            %s .. %s"
              % (pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[-1]).date()))
        print("  basket            top %d per date, equal weight, net of %.2f%%"
              % (top_n, config.TRANSACTION_COST_PCT))
        print("  grid              %d weight vectors summing to 100"
              % len(candidates))
        print("  scorer check      %d comparisons against factor_engine, "
              "%d mismatches" % (check["checked"], check["mismatches"]))

        print("\n  factor coverage — a weight cannot be fitted on a factor that "
              "is never present:")

        for factor in factor_engine.FACTORS:
            share = frame[COLUMNS[factor]].notna().mean() * 100
            print("    %-12s %5.1f%%%s"
                  % (factor, share,
                     "   NEVER PRESENT — its weight changes no score on this "
                     "data and is a design choice, not a measurement"
                     if share == 0 else ""))

    if check["mismatches"]:
        raise SystemExit("the vectorised scorer disagrees with "
                         "factor_engine.swing_score(); refusing to report "
                         "numbers from it.\n%r" % (check["examples"],))

    kwargs = {"top_n": top_n}

    train, test = split_dates(dates)

    # ---- 1. the fixed vectors, everywhere --------------------------------

    fixed = {
        "legacy (with regime 20%)": LEGACY_WEIGHTS,
        "legacy, regime removed": dict(LEGACY_WEIGHTS,
                                       **{factor_engine.REGIME: 0}),
        "proposed": PROPOSED_WEIGHTS,
        "shipped (config.py)": factor_engine.default_weights(),
    }

    headline = {
        name: {
            "all": evaluate(frame, score(frame, w), dates=dates, **kwargs),
            "in_sample": evaluate(frame, score(frame, w), dates=train, **kwargs),
            "out_of_sample": evaluate(frame, score(frame, w), dates=test,
                                      **kwargs),
        }
        for name, w in fixed.items()
    }

    # ---- 2. the grid, fitted in sample, judged out of sample --------------

    in_sample = rank(sweep(frame, candidates, train, **kwargs))

    out_of_sample = {}

    for row in in_sample:
        out_of_sample[row["key"]] = evaluate(
            frame, score(frame, row["weights"]), dates=test, **kwargs)

    # ---- 3. plateau, not peak --------------------------------------------
    #
    # Each candidate's objective is averaged with its grid neighbours', so a
    # vector only wins if the region around it also works.

    by_key = {row["key"]: row for row in in_sample}

    plateau = []

    for row in in_sample:

        family = [row] + [by_key[key_of(n)]
                          for n in neighbours(row["weights"], candidates)
                          if key_of(n) in by_key]

        sharpes = [r["sharpe"] for r in family if r["sharpe"] is not None]

        plateau.append({
            **row,
            "neighbours": len(family) - 1,
            "plateau_sharpe": (round(sum(sharpes) / len(sharpes), 3)
                               if sharpes else None),
            "worst_neighbour_sharpe": min(sharpes) if sharpes else None,
        })

    plateau.sort(key=lambda r: (r["plateau_sharpe"] is not None,
                                r["plateau_sharpe"] or -99,
                                r["worst_neighbour_sharpe"] or -99),
                 reverse=True)

    # ---- 4. walk forward --------------------------------------------------

    forward = walk_forward(frame, candidates, dates, **kwargs)

    # ---- 5. the recommendation -------------------------------------------

    recommended = _recommend(plateau, out_of_sample, headline)

    result = {
        "dates": dates,
        "train": train,
        "test": test,
        "top_n": top_n,
        "verify": check,
        "headline": headline,
        "in_sample": in_sample,
        "out_of_sample": out_of_sample,
        "plateau": plateau,
        "walk_forward": forward,
        "recommended": recommended,
        "stability": {
            name: stability(frame, w, dates, **kwargs)
            for name, w in list(fixed.items()) + [
                ("recommended", recommended["weights"])]
        },
        "regime": regime_table(frame, recommended["weights"], top_n=top_n),
        "entry_floor": entry_floor_table(frame, recommended["weights"],
                                         top_n=top_n),
    }

    if verbose:
        report(result)

    return result


def _recommend(plateau, out_of_sample, headline):
    """The vector this study is willing to defend.

    Not the best in-sample row: that is the spike the plateau ranking exists to
    reject. And emphatically NOT the best out-of-sample row -- choosing by the
    test window turns it into a training window, which is in-sample fitting with
    an extra step and a false sense of validation. The rule is:

        rank by PLATEAU Sharpe (in-sample only, averaged with each vector's grid
        neighbours), take the top decile, drop any vector that was negative out
        of sample, and take the best remaining one. Only when two are within
        TIE_BAND of each other does proximity to the specification's proposal
        break the tie.

    The order of those clauses matters and was got wrong once. Proximity as the
    PRIMARY tiebreak across the whole decile picked a vector that a dozen
    measurements later turned out to be worse than the plateau leader on
    out-of-sample Sharpe, on drawdown, on the worst walk-forward block and on
    the worst stability block. Preferring the explainable vector is a good rule
    for separating near-equals and a bad one for overruling evidence.

    The out-of-sample check is a VETO, not a ranking. It can remove a candidate
    the training window liked; it never promotes one, so no test-window number
    ever selects the winner.
    """

    ranked = [row for row in plateau if row["plateau_sharpe"] is not None]

    if not ranked:
        return {"weights": dict(PROPOSED_WEIGHTS),
                "label": label_of(PROPOSED_WEIGHTS),
                "why": "no grid point produced a Sharpe; falling back to the "
                       "specification's proposal"}

    cut = max(3, len(ranked) // 10)

    shortlist = ranked[:cut]

    survivors = [
        row for row in shortlist
        if (out_of_sample.get(row["key"], {}).get("mean_period_pct") or -99) > 0
    ]

    pool = survivors or shortlist

    leader = max(row["plateau_sharpe"] for row in pool)

    # Statistically indistinguishable, on 14 training periods. Anything inside
    # this band is a coin flip and is settled by explicability instead.
    contenders = [row for row in pool
                  if leader - row["plateau_sharpe"] <= TIE_BAND]

    def distance(row):
        return sum(abs((row["weights"].get(f) or 0) - (PROPOSED_WEIGHTS.get(f) or 0))
                   for f in factor_engine.FACTORS)

    best = min(contenders,
               key=lambda row: (distance(row), -(row["plateau_sharpe"] or 0)))

    return {
        "weights": dict(best["weights"]),
        "label": best["label"],
        "shortlist": len(shortlist),
        "survivors": len(survivors),
        "contenders": len(contenders),
        "plateau_sharpe": best["plateau_sharpe"],
        "in_sample": {k: best[k] for k in
                      ("sharpe", "mean_period_pct", "max_drawdown_pct",
                       "win_rate_period", "trades")},
        "out_of_sample": out_of_sample.get(best["key"]),
        "why": ("top %d of the plateau ranking, %d survived the out-of-sample "
                "veto, %d of those were within %.2f Sharpe of the leader and "
                "the nearest to the proposal won that tie"
                % (cut, len(survivors), len(contenders), TIE_BAND)),
    }


def _line(name, result):
    return ("  %-26s %6s %8s %8s %8s %8s %7s"
            % (name[:26],
               result["periods"],
               "—" if result["mean_period_pct"] is None
               else "%.2f%%" % result["mean_period_pct"],
               "—" if result["sharpe"] is None else "%.2f" % result["sharpe"],
               "—" if result["max_drawdown_pct"] is None
               else "%.1f%%" % result["max_drawdown_pct"],
               "—" if result["win_rate_trade"] is None
               else "%.1f%%" % result["win_rate_trade"],
               result["trades"]))


HEADER = ("  %-26s %6s %8s %8s %8s %8s %7s"
          % ("", "periods", "mean", "sharpe", "maxDD", "win%", "trades"))


def report(result):

    print("\n\n1. THE FIXED VECTORS")
    print("-" * 78)
    print(HEADER)

    for name, blocks in result["headline"].items():
        print("  %s" % name)
        for window in ("all", "in_sample", "out_of_sample"):
            print(_line("    " + window, blocks[window]))

    print("\n\n2. THE GRID, FITTED IN SAMPLE")
    print("-" * 78)
    print("  %-14s %8s %8s %8s   %8s %8s %8s"
          % ("weights", "IS mean", "IS shrp", "IS DD", "OOS mean", "OOS shrp",
             "OOS DD"))

    for row in result["in_sample"][:10]:
        oos = result["out_of_sample"].get(row["key"], {})
        print("  %-14s %7s%% %8s %7s%%   %7s%% %8s %7s%%"
              % (row["label"],
                 row["mean_period_pct"], row["sharpe"], row["max_drawdown_pct"],
                 oos.get("mean_period_pct"), oos.get("sharpe"),
                 oos.get("max_drawdown_pct")))

    print("\n  the best in-sample row is shown first; the OOS columns are what "
          "it\n  then did on dates it was not fitted on.")

    print("\n\n3. PLATEAU RANKING — averaged with each vector's grid neighbours")
    print("-" * 78)
    print("  %-14s %9s %9s %9s %8s"
          % ("weights", "plateau", "worst nb", "IS shrp", "nb"))

    for row in result["plateau"][:10]:
        print("  %-14s %9s %9s %9s %8d"
              % (row["label"], row["plateau_sharpe"],
                 row["worst_neighbour_sharpe"], row["sharpe"],
                 row["neighbours"]))

    print("\n\n4. WALK FORWARD — fit on the past, judged on what follows")
    print("-" * 78)
    print("  %-22s %-14s %9s %9s %9s"
          % ("test window", "fitted", "fitted", "legacy", "proposed"))

    beats = 0

    for fold in result["walk_forward"]:

        fitted = fold["fitted_out_of_sample"]["mean_period_pct"]
        legacy = fold["legacy"]["mean_period_pct"]
        proposed = fold["proposed"]["mean_period_pct"]

        if fitted is not None and proposed is not None and fitted > proposed:
            beats += 1

        print("  %-22s %-14s %8s%% %8s%% %8s%%"
              % ("%s..%s" % (fold["test_from"], fold["test_to"]),
                 fold["fitted_label"], fitted, legacy, proposed))

    print("\n  the fitted vector beat the fixed proposal on %d of %d unseen "
          "windows." % (beats, len(result["walk_forward"])))

    if beats <= len(result["walk_forward"]) / 2.0:
        print("  Fitting the weights per window did NOT generalise — which is "
              "the\n  result that argues for a fixed, explainable vector.")

    print("\n\n5. STABILITY ACROSS BLOCKS")
    print("-" * 78)

    for name, block in result["stability"].items():
        print("  %-26s blocks %s   worst %s%%   spread %s"
              % (name[:26],
                 "%d/%d positive" % (block["positive_blocks"],
                                     block["total_blocks"]),
                 block["worst_block_pct"], block["spread_pct"]))

    print("\n\n6. THE REGIME IN ITS NEW ROLE — what each mode's dates returned")
    print("-" * 78)
    print("  %-14s %7s %10s %9s %10s"
          % ("mode", "dates", "mean", "win%", "worst"))

    for mode, row in sorted(result["regime"].items(),
                            key=lambda kv: -(kv[1]["mean_period_pct"] or -99)):
        print("  %-14s %7d %9s%% %8s%% %9s%%"
              % (mode, row["dates"], row["mean_period_pct"],
                 row["win_rate_period"], row["worst_period_pct"]))

    print("\n\n7. ENTRY FLOOR IN WEAK MODES — extra points demanded")
    print("-" * 78)
    print(HEADER)

    for row in result["entry_floor"]:
        print(_line("+%d points" % row["extra_points"], row))

    print("\n\n8. RECOMMENDED")
    print("-" * 78)

    recommended = result["recommended"]

    print("  %s" % recommended["label"])
    print("  %s" % recommended["why"])

    for factor in factor_engine.FACTORS:
        print("    %-12s %d%%" % (factor, recommended["weights"].get(factor, 0)))


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="Fit and validate the Swing Score's factor weights.")

    parser.add_argument("--build", action="store_true",
                        help="run the point-in-time pass and cache it")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--hold", type=int, default=swing_backtest.DEFAULT_HOLD)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--quick", action="store_true",
                        help="fewer scorer cross-checks")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.build:
        frame = build(start=args.start, end=args.end, hold=args.hold,
                      limit=args.limit, path=args.cache,
                      verbose=not args.quiet)
        return 0 if frame is not None else 1

    result = run(path=args.cache, top_n=args.top, verbose=not args.quiet,
                 quick=args.quick)

    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
