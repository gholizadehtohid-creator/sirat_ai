"""Walk-forward, out-of-sample, Monte Carlo, sensitivity, and an overfit verdict.

WHY A SINGLE BACKTEST IS NOT EVIDENCE
-------------------------------------
A backtest is one sample of one path of one market, chosen by the person who
also chose the parameters. Every technique in this file exists to attack one of
the ways that arrangement lies:

    walk-forward     picks the parameter on data it then does not measure on,
                     which is the only honest way to price a tuned parameter
    out-of-sample    holds back a period that never touched any decision
    Monte Carlo      asks whether the ORDER of the trades made the result, and
                     whether the SET of them was lucky
    sensitivity      asks whether the result survives the parameter moving one
                     step, because a peak with cliffs on both sides is a
                     coincidence with a formula attached
    overfit score    combines the four into a verdict whose arithmetic is
                     printed rather than asserted

WHAT THE OVERFIT NUMBER IS AND IS NOT
-------------------------------------
It is not calibrated against an outside standard, because no such standard
exists. It is a written-down version of the five questions a careful person
asks before believing a backtest, with the thresholds visible in
backtest_config.py so they can be argued with instead of trusted. Anyone who
disagrees with a weight can change it and see the verdict move -- which is the
property that makes it a measurement rather than a decoration.
"""

import math
import random

import pandas as pd

import backtest_config
from backtesting import engine, metrics as metrics_module
from backtesting.strategy import (
    FUNDAMENTAL_ONLY,
    TECHNICAL_FUNDAMENTAL,
    TECHNICAL_FUNDAMENTAL_NEWS,
    TECHNICAL_ONLY,
)


def _metric(result, name):
    """One number out of a result, or None if the run produced nothing."""

    if result is None or not result.available:
        return None

    metrics = result.metrics

    if name == "calmar":
        return metrics.get("calmar")
    if name == "sharpe":
        return metrics.get("sharpe")
    if name == "cagr":
        return metrics.get("cagr_pct")
    if name == "total_return":
        return metrics.get("total_return_pct")
    if name == "profit_factor":
        return (metrics.get("trades") or {}).get("profit_factor")

    return metrics.get(name)


# ==============================
# SHARED PANEL
# ==============================

def master_panel(cfg, data, start, end, variant, verbose=True):
    """One panel over the whole period, and the date grid it was built on.

    Every sub-window, every sensitivity value and every walk-forward fold reads
    this same panel. That is not only a speed decision: rebuilding it per fold
    would let the folds disagree about what a score was on a shared date, and a
    walk-forward whose folds disagree is measuring its own cache.
    """

    from backtesting.strategy import Strategy

    strategy = Strategy(cfg, data, variant=variant, verbose=verbose)

    dates = data.rebalance_dates(start, end, cfg.rebalance_sessions)

    panel = strategy.build_panel(dates, use_cache=True)

    return panel, dates


def _slice(dates, start, end):
    """Rebalance dates inside a window."""

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)

    return [d for d in dates if start <= d <= end]


# ==============================
# WALK FORWARD
# ==============================

def walk_forward(cfg, data, start, end, variant=TECHNICAL_ONLY,
                 parameter="technical_gate", values=None, panel=None,
                 dates=None, verbose=True):
    """Fit on TRAIN months, measure on the VALIDATE months that follow.

    The parameter is chosen on the training half of each window and then
    applied, unchanged, to a period the choice never saw. The gap between the
    two columns is the cost of having tuned anything at all -- and it is the
    number that a single in-sample backtest cannot produce at any length.
    """

    values = values or backtest_config.SENSITIVITY_GRID.get(parameter)

    if not values:
        raise ValueError("no sweep values for %r" % parameter)

    if panel is None:
        panel, dates = master_panel(cfg, data, start, end, variant, verbose)

    windows = _windows(start, end,
                       backtest_config.WALK_FORWARD_TRAIN_MONTHS,
                       backtest_config.WALK_FORWARD_VALIDATE_MONTHS,
                       backtest_config.WALK_FORWARD_STEP_MONTHS)

    metric = backtest_config.SENSITIVITY_METRIC

    folds = []

    for number, (train_start, train_end, test_start, test_end) in enumerate(windows, 1):

        if verbose:
            print("  fold %d: train %s..%s   test %s..%s"
                  % (number, train_start.date(), train_end.date(),
                     test_start.date(), test_end.date()), flush=True)

        best = None

        for value in values:

            trial = cfg.replace(**{parameter: value})

            result = engine.run(
                train_start, train_end, cfg=trial, variant=variant, data=data,
                panel=panel, rebalance_dates=_slice(dates, train_start, train_end),
                verbose=False, benchmarks=False,
            )

            score = _metric(result, metric)

            if score is not None and (best is None or score > best[1]):
                best = (value, score, result)

        if best is None:
            folds.append({
                "fold": number,
                "train": [str(train_start.date()), str(train_end.date())],
                "test": [str(test_start.date()), str(test_end.date())],
                "chosen": None,
                "reason": "no parameter value produced a measurable result "
                          "in the training window",
            })
            continue

        chosen, train_score, train_result = best

        tested = engine.run(
            test_start, test_end, cfg=cfg.replace(**{parameter: chosen}),
            variant=variant, data=data, panel=panel,
            rebalance_dates=_slice(dates, test_start, test_end),
            verbose=False, benchmarks=False,
        )

        folds.append({
            "fold": number,
            "train": [str(train_start.date()), str(train_end.date())],
            "test": [str(test_start.date()), str(test_end.date())],
            "parameter": parameter,
            "chosen": chosen,
            "train_metric": train_score,
            "test_metric": _metric(tested, metric),
            "train_return_pct": _metric(train_result, "total_return"),
            "test_return_pct": _metric(tested, "total_return"),
            "test_trades": (tested.metrics.get("trades") or {}).get("trades")
            if tested.available else 0,
            "metric": metric,
        })

    return {
        "parameter": parameter,
        "values": list(values),
        "metric": metric,
        "folds": folds,
        "summary": _walk_forward_summary(folds),
    }


def _walk_forward_summary(folds):
    """Did the choices generalise, and did the parameter stay put?"""

    measured = [f for f in folds if f.get("test_metric") is not None]

    if not measured:
        return {"available": False,
                "reason": "no fold produced a measurable out-of-fold result"}

    train_values = [f["train_metric"] for f in measured
                    if f.get("train_metric") is not None]
    test_values = [f["test_metric"] for f in measured]

    chosen = [f.get("chosen") for f in measured]

    return {
        "available": True,
        "folds": len(measured),
        "mean_train_metric": round(sum(train_values) / len(train_values), 3)
        if train_values else None,
        "mean_test_metric": round(sum(test_values) / len(test_values), 3),
        "positive_test_folds": sum(1 for v in test_values if v and v > 0),
        # A parameter that lands somewhere different in every window was not
        # discovered, it was fitted to noise -- separately, each time.
        #
        # Undefined below two folds: one fold always yields 1.00, which reads
        # as maximum instability when it is in fact no information at all.
        "parameter_stability": (round(len(set(chosen)) / float(len(chosen)), 2)
                                if len(chosen) >= 2 else None),
        "chosen_values": chosen,
    }


def _windows(start, end, train_months, validate_months, step_months):
    """(train_start, train_end, test_start, test_end) tuples over the period."""

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)

    out = []

    cursor = start

    while True:

        train_start = cursor
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=validate_months)

        if test_end > end:
            break

        out.append((train_start, train_end, test_start, test_end))

        cursor = cursor + pd.DateOffset(months=step_months)

    return out


# ==============================
# OUT OF SAMPLE
# ==============================

def out_of_sample(cfg, data, start, end, variant=TECHNICAL_ONLY, panel=None,
                  dates=None, verbose=True):
    """Three consecutive periods, measured separately and never mixed.

    The boundary is a DATE in backtest_config, not a fraction of the data. A
    fraction would move every time the database gains bars, and a hold-out set
    that moves is not a hold-out set.
    """

    if panel is None:
        panel, dates = master_panel(cfg, data, start, end, variant, verbose)

    oos_start = pd.Timestamp(backtest_config.OUT_OF_SAMPLE_START)

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)

    if not (start < oos_start < end):
        return {
            "available": False,
            "reason": ("OUT_OF_SAMPLE_START (%s) is outside the backtest "
                       "window %s..%s, so there is no hold-out period"
                       % (oos_start.date(), start.date(), end.date())),
        }

    validate_start = oos_start - pd.DateOffset(
        months=backtest_config.WALK_FORWARD_VALIDATE_MONTHS)

    if validate_start <= start:
        # Not enough room for three periods: report two rather than invent a
        # third from four sessions.
        segments = [
            ("in_sample", start, oos_start),
            ("out_of_sample", oos_start, end),
        ]
    else:
        segments = [
            ("in_sample", start, validate_start),
            ("validation", validate_start, oos_start),
            ("out_of_sample", oos_start, end),
        ]

    out = {"available": True, "segments": {}}

    for name, segment_start, segment_end in segments:

        if verbose:
            print("  %-14s %s .. %s" % (name, segment_start.date(),
                                        segment_end.date()), flush=True)

        result = engine.run(
            segment_start, segment_end, cfg=cfg, variant=variant, data=data,
            panel=panel, rebalance_dates=_slice(dates, segment_start, segment_end),
            verbose=False, benchmarks=True,
        )

        out["segments"][name] = {
            "start": str(segment_start.date()),
            "end": str(segment_end.date()),
            "available": result.available,
            "reason": result.insufficient[0] if result.insufficient else None,
            "metrics": _headline(result),
            "result": result,
        }

    inside = out["segments"].get("in_sample", {}).get("metrics") or {}
    outside = out["segments"].get("out_of_sample", {}).get("metrics") or {}

    out["degradation"] = _degradation(inside.get("cagr_pct"),
                                      outside.get("cagr_pct"))

    return out


def _headline(result):
    if result is None or not result.available:
        return {}

    m = result.metrics
    t = m.get("trades") or {}

    return {
        "total_return_pct": m.get("total_return_pct"),
        "cagr_pct": m.get("cagr_pct"),
        "max_drawdown_pct": (m.get("drawdown") or {}).get("max_drawdown_pct"),
        "sharpe": m.get("sharpe"),
        "sortino": m.get("sortino"),
        "calmar": m.get("calmar"),
        "trades": t.get("trades"),
        "win_rate_pct": t.get("win_rate_pct"),
        "profit_factor": t.get("profit_factor"),
    }


def _degradation(in_sample, out_sample):
    """Share of in-sample CAGR that survived into the hold-out."""

    if in_sample is None or out_sample is None:
        return {"available": False, "reason": "one period produced no CAGR"}

    if in_sample <= 0:
        return {
            "available": True,
            "retained": None,
            "note": "in-sample CAGR was not positive, so there is nothing to "
                    "degrade from; judge the hold-out on its own terms",
        }

    return {
        "available": True,
        "in_sample_cagr_pct": in_sample,
        "out_of_sample_cagr_pct": out_sample,
        "retained": round(out_sample / in_sample, 2),
        "drop": round(1 - out_sample / in_sample, 2),
    }


# ==============================
# MONTE CARLO
# ==============================

def monte_carlo(result, runs=None, method=None, seed=None):
    """Thousands of alternative orderings of the same trades.

    Each trade's impact is measured as its rial P&L over the portfolio value on
    the day it was opened, so resampling compounds correctly instead of adding
    percentages that were earned on different amounts of money.

    "shuffle" keeps the exact set of trades and asks only whether their ORDER
    mattered. "bootstrap" resamples with replacement and so also asks whether
    the SET was lucky -- the larger question and the harsher answer.
    """

    if result is None or not result.available or not result.trades:
        return {"available": False, "reason": "no trades to resample"}

    runs = int(runs or backtest_config.MONTE_CARLO_RUNS)
    method = method or backtest_config.MONTE_CARLO_METHOD
    seed = backtest_config.MONTE_CARLO_SEED if seed is None else seed

    curve = result.curve

    impacts = []

    for trade in result.trades:

        pnl = trade.get("pnl")

        if pnl is None:
            continue

        equity = _equity_on(curve, trade.get("entry_date"))

        if not equity or equity <= 0:
            continue

        impacts.append(float(pnl) / float(equity))

    if len(impacts) < 5:
        return {"available": False,
                "reason": "only %d trades could be placed on the equity curve; "
                          "a distribution from that is noise" % len(impacts)}

    rng = random.Random(seed)

    finals = []
    drawdowns = []
    losses = 0

    for _ in range(runs):

        if method == "shuffle":
            path = impacts[:]
            rng.shuffle(path)
        else:
            path = [impacts[rng.randrange(len(impacts))]
                    for _ in range(len(impacts))]

        equity = 1.0
        peak = 1.0
        worst = 0.0

        for impact in path:
            equity *= (1 + impact)
            peak = max(peak, equity)
            worst = min(worst, equity / peak - 1)

        finals.append((equity - 1) * 100)
        drawdowns.append(worst * 100)

        if equity < 1.0:
            losses += 1

    finals.sort()
    drawdowns.sort()

    percentiles = {}

    for point in backtest_config.MONTE_CARLO_PERCENTILES:
        percentiles["p%d_return_pct" % point] = round(
            _percentile(finals, point), 2)
        percentiles["p%d_drawdown_pct" % point] = round(
            _percentile(drawdowns, 100 - point), 2)

    return {
        "available": True,
        "runs": runs,
        "method": method,
        "trades_per_path": len(impacts),
        "median_return_pct": round(_percentile(finals, 50), 2),
        "mean_return_pct": round(sum(finals) / len(finals), 2),
        "probability_of_loss_pct": round(losses / float(runs) * 100, 2),
        "expected_max_drawdown_pct": round(sum(drawdowns) / len(drawdowns), 2),
        "worst_drawdown_pct": round(min(drawdowns), 2),
        "percentiles": percentiles,
        # The single most useful line: the actual result's position in its own
        # distribution. Near the top means the realised path was the lucky one.
        "realised_return_pct": result.metrics.get("total_return_pct"),
        "realised_percentile": round(
            sum(1 for v in finals if v <= (result.metrics.get("total_return_pct") or 0))
            / float(len(finals)) * 100, 1),
    }


def _percentile(sorted_values, point):
    if not sorted_values:
        return float("nan")

    index = int(round((point / 100.0) * (len(sorted_values) - 1)))

    return sorted_values[max(0, min(len(sorted_values) - 1, index))]


def _equity_on(curve, date):
    """Portfolio value on a date, from the curve. Used to scale trade impact."""

    if curve is None or curve.empty or date is None:
        return None

    stamp = pd.Timestamp(date)

    index = curve.index.searchsorted(stamp, side="right") - 1

    if index < 0:
        return float(curve["portfolio_value"].iloc[0])

    return float(curve["portfolio_value"].iloc[index])


# ==============================
# SENSITIVITY
# ==============================

def sensitivity(cfg, data, start, end, variant=TECHNICAL_ONLY, grid=None,
                panel=None, dates=None, verbose=True):
    """Run the same window at every value of every listed parameter.

    A strategy whose result collapses when a parameter moves one step is fitted
    to that step. Fragility is measured as the coefficient of variation of the
    metric across a parameter's values, which is scale-free -- so a parameter
    measured in ATRs and one measured in score points are comparable.
    """

    grid = grid or backtest_config.SENSITIVITY_GRID

    if panel is None:
        panel, dates = master_panel(cfg, data, start, end, variant, verbose)

    metric = backtest_config.SENSITIVITY_METRIC

    window_dates = _slice(dates, start, end)

    out = {"metric": metric, "parameters": {}}

    for parameter, values in grid.items():

        if parameter not in backtest_config._OVERRIDABLE:
            out["parameters"][parameter] = {
                "skipped": "not an overridable parameter"}
            continue

        scores = []

        for value in values:

            trial = cfg.replace(**{parameter: value})

            result = engine.run(
                start, end, cfg=trial, variant=variant, data=data, panel=panel,
                rebalance_dates=window_dates, verbose=False, benchmarks=False,
            )

            scores.append({
                "value": value,
                "metric": _metric(result, metric),
                "return_pct": _metric(result, "total_return"),
                "max_drawdown_pct": ((result.metrics.get("drawdown") or {})
                                     .get("max_drawdown_pct")
                                     if result.available else None),
                "trades": ((result.metrics.get("trades") or {}).get("trades")
                           if result.available else 0),
            })

        measured = [s["metric"] for s in scores if s["metric"] is not None]

        cv = None

        if len(measured) >= 2:
            mean = sum(measured) / len(measured)
            if abs(mean) > 1e-9:
                variance = sum((v - mean) ** 2 for v in measured) / len(measured)
                cv = math.sqrt(variance) / abs(mean)

        best = max(scores, key=lambda s: (s["metric"] is not None, s["metric"] or -1e9))

        out["parameters"][parameter] = {
            "values": scores,
            "coefficient_of_variation": round(cv, 3) if cv is not None else None,
            "fragile": bool(cv is not None
                            and cv > backtest_config.SENSITIVITY_FRAGILE_CV),
            "best_value": best["value"],
            "best_metric": best["metric"],
            "live_value": getattr(cfg, parameter, None),
        }

        if verbose:
            print("  %-26s cv=%s  best=%s"
                  % (parameter,
                     ("%.2f" % cv) if cv is not None else "-",
                     best["value"]), flush=True)

    fragile = [name for name, entry in out["parameters"].items()
               if entry.get("fragile")]

    out["fragile_parameters"] = fragile

    out["fragility_share"] = round(
        len(fragile) / float(max(1, len([e for e in out["parameters"].values()
                                         if "values" in e]))), 2)

    return out


# ==============================
# VARIANT COMPARISON
# ==============================

def variant_comparison(cfg, data, start, end, verbose=True):
    """Does the fundamental side earn its weight? Does news exist at all?

    Each variant needs its OWN panel, because a fundamental score is not
    computed at all in the technical variant -- reusing one panel across them
    would either compute fundamentals nobody asked for or report their absence
    as a strategy result.
    """

    out = {}

    for variant in (TECHNICAL_ONLY, FUNDAMENTAL_ONLY, TECHNICAL_FUNDAMENTAL,
                    TECHNICAL_FUNDAMENTAL_NEWS):

        if verbose:
            print()
            print("  variant: %s" % variant)

        result = engine.run(start, end, cfg=cfg, variant=variant, data=data,
                            verbose=verbose, benchmarks=True)

        out[variant] = result

    return out


# ==============================
# OVERFITTING VERDICT
# ==============================

def overfitting_risk(result=None, oos=None, sweep=None, folds=None,
                     regimes=None):
    """LOW / MEDIUM / HIGH, with every point of the total attributable.

    Each test contributes its configured weight, scaled by how badly it failed
    where a scale makes sense. A test whose inputs are missing contributes
    nothing and says so -- an absent measurement is not a passed one, and the
    report prints how much of the model could actually run.
    """

    weights = backtest_config.OVERFIT_WEIGHTS

    findings = []
    score = 0.0
    weight_available = 0.0

    # ---- 1. out-of-sample degradation -----------------------------------
    degradation = (oos or {}).get("degradation") or {}

    if degradation.get("available") and degradation.get("retained") is not None:

        weight_available += weights["oos_degradation"]

        drop = degradation["drop"]

        if drop > backtest_config.OVERFIT_OOS_DROP:
            # Scale between the threshold and total collapse.
            severity = min(1.0, (drop - backtest_config.OVERFIT_OOS_DROP) /
                           max(1e-9, 1.0 - backtest_config.OVERFIT_OOS_DROP))
            points = weights["oos_degradation"] * (0.5 + 0.5 * severity)
            score += points
            findings.append({
                "test": "oos_degradation",
                "points": round(points, 1),
                "of": weights["oos_degradation"],
                "detail": "out-of-sample kept %.0f%% of in-sample CAGR "
                          "(threshold %.0f%%)"
                          % (degradation["retained"] * 100,
                             (1 - backtest_config.OVERFIT_OOS_DROP) * 100),
            })
        else:
            findings.append({
                "test": "oos_degradation", "points": 0.0,
                "of": weights["oos_degradation"],
                "detail": "out-of-sample kept %.0f%% of in-sample CAGR"
                          % (degradation["retained"] * 100),
            })
    else:
        findings.append({
            "test": "oos_degradation", "points": None,
            "of": weights["oos_degradation"],
            "detail": "not measurable: " + str(degradation.get("reason")
                                               or degradation.get("note")
                                               or "no hold-out result"),
        })

    # ---- 2. parameter fragility ------------------------------------------
    if sweep and sweep.get("parameters"):

        weight_available += weights["parameter_fragility"]

        share = sweep.get("fragility_share") or 0.0

        points = weights["parameter_fragility"] * min(1.0, share * 2)

        score += points

        findings.append({
            "test": "parameter_fragility",
            "points": round(points, 1),
            "of": weights["parameter_fragility"],
            "detail": "%d of %d swept parameters were fragile (cv > %.2f): %s"
                      % (len(sweep.get("fragile_parameters") or []),
                         len([e for e in sweep["parameters"].values()
                              if "values" in e]),
                         backtest_config.SENSITIVITY_FRAGILE_CV,
                         ", ".join(sweep.get("fragile_parameters") or []) or "none"),
        })
    else:
        findings.append({
            "test": "parameter_fragility", "points": None,
            "of": weights["parameter_fragility"],
            "detail": "not measurable: no sensitivity sweep was run",
        })

    # ---- 3. trade count ---------------------------------------------------
    trades = 0

    if result is not None and result.available:
        trades = (result.metrics.get("trades") or {}).get("trades") or 0

    weight_available += weights["trade_count"]

    minimum = backtest_config.OVERFIT_MIN_TRADES

    if trades < minimum:
        severity = 1.0 - (trades / float(minimum)) if minimum else 1.0
        points = weights["trade_count"] * severity
        score += points
        findings.append({
            "test": "trade_count", "points": round(points, 1),
            "of": weights["trade_count"],
            "detail": "%d closed trades against a minimum of %d; at that count "
                      "a 60%% win rate has a 95%% interval wide enough to "
                      "contain both edge and coin" % (trades, minimum),
        })
    else:
        findings.append({
            "test": "trade_count", "points": 0.0, "of": weights["trade_count"],
            "detail": "%d closed trades" % trades,
        })

    # ---- 4. period concentration -----------------------------------------
    concentration = _profit_concentration(result)

    if concentration is not None:

        weight_available += weights["period_concentration"]

        ceiling = backtest_config.OVERFIT_MAX_WINDOW_SHARE

        if concentration["share"] > ceiling:
            severity = min(1.0, (concentration["share"] - ceiling) /
                           max(1e-9, 1.0 - ceiling))
            points = weights["period_concentration"] * (0.5 + 0.5 * severity)
            score += points
            findings.append({
                "test": "period_concentration", "points": round(points, 1),
                "of": weights["period_concentration"],
                "detail": "%.0f%% of net profit came from %s alone, across %d "
                          "months of which %d lost money"
                          % (concentration["share"] * 100,
                             concentration["period"], concentration["months"],
                             concentration["losing_months"]),
            })
        else:
            findings.append({
                "test": "period_concentration", "points": 0.0,
                "of": weights["period_concentration"],
                "detail": "largest single month contributed %.0f%% of net profit"
                          % (concentration["share"] * 100),
            })
    else:
        findings.append({
            "test": "period_concentration", "points": None,
            "of": weights["period_concentration"],
            "detail": "not measurable: no monthly profit breakdown",
        })

    # ---- 5. regime failure ------------------------------------------------
    fold_results = (folds or {}).get("folds") or []

    measured_folds = [f for f in fold_results if f.get("test_metric") is not None]

    if measured_folds:

        weight_available += weights["regime_failure"]

        negative = sum(1 for f in measured_folds
                       if (f.get("test_return_pct") or 0) <= 0)

        share = negative / float(len(measured_folds))

        points = weights["regime_failure"] * share

        score += points

        findings.append({
            "test": "regime_failure", "points": round(points, 1),
            "of": weights["regime_failure"],
            "detail": "%d of %d out-of-fold windows lost money"
                      % (negative, len(measured_folds)),
        })
    elif regimes:
        weight_available += weights["regime_failure"]
        negative = sum(1 for value in regimes.values()
                       if value is not None and value <= 0)
        share = negative / float(max(1, len(regimes)))
        points = weights["regime_failure"] * share
        score += points
        findings.append({
            "test": "regime_failure", "points": round(points, 1),
            "of": weights["regime_failure"],
            "detail": "%d of %d regime windows lost money" % (negative, len(regimes)),
        })
    else:
        findings.append({
            "test": "regime_failure", "points": None,
            "of": weights["regime_failure"],
            "detail": "not measurable: no walk-forward or regime windows",
        })

    total_weight = float(sum(weights.values()))

    coverage = weight_available / total_weight if total_weight else 0.0

    # Rescale onto the full 0-100 range over the tests that could run, so a
    # partial model is not automatically LOW simply for having measured less.
    normalised = (score / weight_available * 100) if weight_available else None

    band = "UNKNOWN"

    # Too little of the model ran to name a band. Scoring zero out of the two
    # cheap tests and printing LOW would report the absence of evidence as
    # evidence of robustness, which is the exact error this file exists to
    # avoid making about anything else.
    enough = coverage >= backtest_config.OVERFIT_MIN_WEIGHT_MEASURED

    if normalised is not None and enough:
        band = next(label for floor, label in backtest_config.OVERFIT_BANDS
                    if normalised >= floor)

    note = ("%.0f%% of the overfitting model could run on the evidence "
            "available" % (coverage * 100))

    if not enough:
        note += (" -- below the %.0f%% needed to name a band, so the risk is "
                 "UNKNOWN rather than LOW"
                 % (backtest_config.OVERFIT_MIN_WEIGHT_MEASURED * 100))

    return {
        "risk": band,
        "score": round(normalised, 1) if normalised is not None else None,
        "raw_points": round(score, 1),
        "weight_measured": round(coverage, 2),
        "findings": findings,
        "note": note,
    }


def _profit_concentration(result):
    """Share of net profit contributed by the single best calendar month."""

    if result is None or not result.available or not result.trades:
        return None

    by_month = {}

    for trade in result.trades:

        pnl = trade.get("pnl")

        if pnl is None or not trade.get("exit_date"):
            continue

        key = str(pd.Timestamp(trade["exit_date"]).to_period("M"))

        by_month[key] = by_month.get(key, 0.0) + float(pnl)

    if not by_month:
        return None

    total = sum(by_month.values())

    if total <= 0:
        return None

    period, best = max(by_month.items(), key=lambda item: item[1])

    # The share can exceed 1.0, and that is the most informative case rather
    # than a bug: it means every other month together contributed nothing or
    # less, so one month IS the result.
    return {
        "period": period,
        "share": best / total,
        "months": len(by_month),
        "losing_months": sum(1 for value in by_month.values() if value <= 0),
    }
