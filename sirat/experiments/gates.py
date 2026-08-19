"""The six stages, as checks that actually run.

WHY GATES ARE A MODULE AND NOT AN HONOUR SYSTEM
-----------------------------------------------
`Manager.run_stage()` will accept a dict of booleans, and a researcher in a
hurry could hand it `{"survivorship": True}` without having measured anything.
That is the one hole the manager cannot close by itself, so this module closes
it from the other side: each function here MEASURES, compares against a
threshold it was handed from the frozen preregistration, and returns booleans
nobody chose.

The rule every function obeys: **a check that could not be computed is False,
never absent and never True.** Absence-as-approval is how four `null` execution
counters were read as "no queues were ever hit" for an entire programme.

WHAT A PANEL IS HERE
--------------------
One tidy DataFrame, one row per (date, instrument):

    date        decision date, a real trading session
    ins_code    instrument
    score       the signal being tested, computed from data up to `date`
    fwd_<h>     forward return in PERCENT over h SESSIONS after `date`

plus, when the stage needs them:

    next_locked_up   the name is limit-locked at t+1 (unbuyable at the open)
    next_unbuyable   locked, reopening, or gapped at t+1
    adv              average daily traded value, decision-time
    stale            no trade on the decision bar

This shape is what `research_alpha_ic.score_frame()` already produces and what
E13/E14/E15 all worked on, so nothing has to be rebuilt to use these gates.

THRESHOLDS COME FROM THE CALLER, ALWAYS
----------------------------------------
No function here has a default threshold. A default is a number nobody chose,
and a gate whose threshold is a module constant is a gate whose threshold can be
edited after the result exists. Every one is passed in from
`Manager.thresholds()`, which reads the hash-verified frozen record.
"""

import math

import numpy as np
import pandas as pd

from sirat.experiments import stages


class GateInputMissing(Exception):
    """The gate was asked to run without the data it needs to run.

    Distinct from a failing check on purpose. "Liquidity is unacceptable" and
    "nobody supplied a liquidity column" are different findings and collapsing
    them into one False would let a missing column read as a measured failure.
    """


class ThresholdMissing(Exception):
    """A threshold the gate needs was not preregistered."""


class GateResult(object):
    """Booleans for the manager, numbers for the reader.

    Both halves are required. A stage recorded as PASS with no numbers behind it
    cannot be checked by a third party, and an experiment a third party cannot
    check is a claim rather than a result.
    """

    def __init__(self, stage, metrics, detail):
        self.stage = stage
        self.metrics = metrics
        self.detail = detail

    @property
    def passed(self):
        return stages.verdict_from(self.metrics)[0] == stages.PASS

    @property
    def failed_checks(self):
        return stages.verdict_from(self.metrics)[1]

    def __repr__(self):
        return "<GateResult stage=%d %s failed=%s>" % (
            self.stage, "PASS" if self.passed else "FAIL",
            ",".join(self.failed_checks) or "-")


def _need(thresholds, *names):
    """Pull thresholds, refusing to invent one that was never frozen."""

    missing = [n for n in names if n not in (thresholds or {})]

    if missing:
        raise ThresholdMissing(
            "these thresholds were not preregistered: %s. Add them to the "
            "preregistration and register a new experiment -- they cannot be "
            "chosen now." % ", ".join(missing))

    return tuple(thresholds[n] for n in names)


def _finite(values):
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def _spearman(x, y):
    """Rank correlation. NaN when it is not defined, never 0.0.

    Returning 0.0 for an undefined correlation would silently move a mean IC
    toward zero and make a thin date look like a neutral one.
    """

    x = np.asarray(x, float)
    y = np.asarray(y, float)

    good = np.isfinite(x) & np.isfinite(y)

    x, y = x[good], y[good]

    if len(x) < 3:
        return np.nan

    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()

    if rx.std() == 0 or ry.std() == 0:
        return np.nan

    return float(np.corrcoef(rx, ry)[0, 1])


def _per_date_ic(panel, horizon, min_names, score="score"):
    """Cross-sectional IC for each date. The unit of every Stage 2 number."""

    column = "fwd_%d" % int(horizon)

    if column not in panel.columns:
        raise GateInputMissing(
            "panel has no %s column, so the signal cannot be measured at the "
            "horizon it is traded at" % column)

    rows = []

    for date, block in panel.groupby("date", sort=True):
        good = block[[score, column]].dropna()
        if len(good) < min_names:
            continue
        rows.append({
            "date": date,
            "names": len(good),
            "ic": _spearman(good[score], good[column]),
        })

    return pd.DataFrame(rows)


def _decile_excess(panel, horizon, min_names, score="score"):
    """Top-decile return minus the cross-section mean, per date.

    Decile rather than the whole cross-section because that is what a basket
    strategy actually holds, and because a rank correlation can be positive
    while the top decile is not -- which is exactly the shape a signal takes
    when its information sits in the losers.
    """

    column = "fwd_%d" % int(horizon)

    rows = []

    for date, block in panel.groupby("date", sort=True):
        good = block[[score, column]].dropna()
        n = len(good)
        if n < min_names:
            continue
        k = max(1, int(round(n / 10.0)))
        order = np.argsort(-good[score].to_numpy(float), kind="stable")
        top = good[column].to_numpy(float)[order[:k]]
        market = float(good[column].mean())
        rows.append({"date": date, "k": k, "top_pct": float(top.mean()),
                     "market_pct": market,
                     "excess_pct": float(top.mean()) - market})

    return pd.DataFrame(rows)


def _t_stat(values):
    values = _finite(values)
    if len(values) < 2:
        return None
    sd = values.std(ddof=1)
    if sd <= 0:
        return None
    return float(values.mean() / (sd / math.sqrt(len(values))))


# ==============================================================
# STAGE 1 -- DATA INTEGRITY
# ==============================================================

# The largest one-session move the Tehran band permits, with room for a
# reopening. A "20-session return" of several thousand percent is not a return,
# it is groupby.shift(-20) moving twenty ROWS across a suspension gap -- the
# defect that produced a -579% top-decile excess in E14's first pass.
MAX_SESSION_MOVE_PCT = 12.0


def data_integrity(panel, thresholds, window=None, universe_snapshot=None,
                   horizons=(1,), score="score"):
    """Stage 1. Minutes of work, and it ends most bad ideas.

    Checks
    ------
    point_in_time   no forward column is being used as a feature, and no
                    forward return is larger than the trading calendar allows
    survivorship    a universe snapshot exists for the window
    universe_valid  enough names per date to support a cross-section
    missing_data    the score and the traded-horizon label are populated
    """

    (max_missing, min_names, max_stale) = _need(
        thresholds, "max_missing_pct", "min_names_per_date", "max_stale_pct")

    if panel is None or len(panel) == 0:
        raise GateInputMissing("stage 1 needs a panel with rows in it")

    for required in ("date", "ins_code", score):
        if required not in panel.columns:
            raise GateInputMissing("panel has no %r column" % required)

    detail = {"rows": int(len(panel)),
              "dates": int(panel["date"].nunique()),
              "instruments": int(panel["ins_code"].nunique())}

    # ---- point in time ---------------------------------------------------
    #
    # Two distinct leaks, both seen in this repository.
    forward_columns = [c for c in panel.columns if c.startswith("fwd_")]

    detail["forward_columns"] = forward_columns

    # (a) a forward return being used as the score.
    score_is_forward = str(score).startswith("fwd_")

    # (b) shift(-h) moving rows rather than sessions. A 1-session forward return
    #     outside the band, or an h-session one outside what h sessions of the
    #     band could compound to, means the label reached across a gap.
    impossible = 0
    checked = 0

    for column in forward_columns:
        try:
            horizon = int(column.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        ceiling = ((1.0 + MAX_SESSION_MOVE_PCT / 100.0) ** horizon - 1.0) * 100.0
        values = panel[column].to_numpy(float)
        values = values[np.isfinite(values)]
        checked += len(values)
        impossible += int((np.abs(values) > ceiling).sum())

    impossible_pct = (100.0 * impossible / checked) if checked else 0.0

    detail["impossible_forward_returns"] = impossible
    detail["impossible_forward_pct"] = round(impossible_pct, 4)

    # Any leak at all fails. This is not a tolerance question: one row that
    # reached across a suspension can move a cross-sectional mean by more than
    # the entire effect being measured.
    point_in_time = bool(not score_is_forward and impossible == 0)

    detail["score_is_forward_return"] = bool(score_is_forward)

    if window:
        start, end = window
        outside = panel[(panel["date"] < pd.Timestamp(start))
                        | (panel["date"] > pd.Timestamp(end))]
        detail["rows_outside_window"] = int(len(outside))
        point_in_time = bool(point_in_time and len(outside) == 0)

    # ---- survivorship ----------------------------------------------------
    #
    # There is exactly one honest check here and it is not a statistic. The
    # universe is the list of instruments listed TODAY; a company delisted in
    # 1403 is absent precisely where its absence flatters the result. No test on
    # this panel can detect that, because the rows were never fetched. What CAN
    # be required is that the universe used was frozen and is recoverable, so
    # that a year from now this experiment can be re-run against the list as it
    # stood rather than the list as it will be.
    survivorship = bool(universe_snapshot
                        and universe_snapshot != "UNAVAILABLE")

    detail["universe_snapshot"] = universe_snapshot or "UNAVAILABLE"

    # ---- universe validity ------------------------------------------------
    per_date = panel.groupby("date")[score].apply(lambda s: int(s.notna().sum()))

    thin = int((per_date < min_names).sum())

    detail["names_per_date_median"] = float(per_date.median())
    detail["names_per_date_min"] = int(per_date.min())
    detail["dates_below_minimum"] = thin
    detail["min_names_per_date"] = min_names

    universe_valid = bool(thin == 0)

    # ---- missing data -----------------------------------------------------
    missing_score = float(panel[score].isna().mean() * 100.0)

    worst_label = 0.0

    for horizon in horizons:
        column = "fwd_%d" % int(horizon)
        if column in panel.columns:
            worst_label = max(worst_label,
                              float(panel[column].isna().mean() * 100.0))

    stale_pct = (float(panel["stale"].astype(bool).mean() * 100.0)
                 if "stale" in panel.columns else None)

    detail["missing_score_pct"] = round(missing_score, 3)
    detail["missing_label_pct"] = round(worst_label, 3)
    detail["stale_pct"] = None if stale_pct is None else round(stale_pct, 3)
    detail["max_missing_pct"] = max_missing
    detail["max_stale_pct"] = max_stale

    missing_data = bool(missing_score <= max_missing
                        and worst_label <= max_missing
                        # A missing staleness column is not a passing staleness
                        # check. 22.3% of this market's bars are stale and a
                        # panel that does not report it has not looked.
                        and stale_pct is not None
                        and stale_pct <= max_stale)

    return GateResult(stages.DATA_INTEGRITY, {
        "point_in_time": point_in_time,
        "survivorship": survivorship,
        "universe_valid": universe_valid,
        "missing_data": missing_data,
    }, detail)


# ==============================================================
# STAGE 2 -- ECONOMIC SIGNAL VALIDATION
# ==============================================================

def economic_signal(panel, thresholds, horizon, seed, permutations=200,
                    decay_horizons=(1, 5, 10, 20), score="score"):
    """Stage 2. Is there information, at the horizon it will be traded at.

    THE HORIZON ARGUMENT IS THE POINT. Measuring IC at every horizon and
    reporting the best one is a multiple comparison dressed as a finding: Alpha
    Core is 0.10492 at h=1 and 0.01456 at h=20, and it was traded at h=20. The
    declared horizon comes from the frozen preregistration and this gate scores
    that one. The others are measured for the decay curve and cannot pass it.

    Checks
    ------
    ic                mean IC at the traded horizon clears the floor
    rank_correlation  ICIR clears its floor -- a mean IC with no stability is
                      one good quarter
    random_baseline   the real score beats the 95th percentile of a permutation
                      null built on the same rows
    factor_decay      the signal still exists at the traded horizon rather than
                      only at h=1
    """

    (min_ic, min_icir, min_pct_positive, min_names,
     min_decay_retention) = _need(
        thresholds, "min_ic", "min_icir", "min_pct_positive_ic",
        "min_names_per_date", "min_decay_retention")

    horizon = int(horizon)

    frame = _per_date_ic(panel, horizon, min_names, score=score)

    if frame.empty:
        raise GateInputMissing(
            "no date carried %d scoreable names at h=%d; there is nothing to "
            "measure" % (min_names, horizon))

    ic = _finite(frame["ic"])

    if len(ic) < 2:
        raise GateInputMissing("fewer than two usable dates at h=%d" % horizon)

    mean_ic = float(ic.mean())
    sd = float(ic.std(ddof=1))
    icir = float(mean_ic / sd) if sd > 0 else 0.0
    pct_positive = float((ic > 0).mean() * 100.0)

    detail = {
        "horizon": horizon,
        "dates": int(len(ic)),
        "mean_ic": round(mean_ic, 5),
        "icir": round(icir, 4),
        "t_ic": round(icir * math.sqrt(len(ic)), 3),
        "pct_positive_ic": round(pct_positive, 2),
        "min_ic": min_ic, "min_icir": min_icir,
        "min_pct_positive_ic": min_pct_positive,
    }

    # ---- permutation null -------------------------------------------------
    #
    # The score is shuffled WITHIN each date. Shuffling across dates would break
    # the cross-sectional structure and produce a null that is easy to beat for
    # reasons that have nothing to do with the signal.
    rng = np.random.default_rng(int(seed))

    column = "fwd_%d" % horizon

    blocks = [(b[score].to_numpy(float), b[column].to_numpy(float))
              for _, b in panel.groupby("date", sort=True)
              if b[[score, column]].dropna().shape[0] >= min_names]

    null = []

    for _ in range(int(permutations)):
        per_date = []
        for scores, labels in blocks:
            good = np.isfinite(scores) & np.isfinite(labels)
            if good.sum() < min_names:
                continue
            shuffled = rng.permutation(scores[good])
            value = _spearman(shuffled, labels[good])
            if np.isfinite(value):
                per_date.append(value)
        if per_date:
            null.append(float(np.mean(per_date)))

    null = np.asarray(null, float)

    if len(null) < 20:
        raise GateInputMissing(
            "the permutation null produced only %d draws; it cannot support a "
            "95th percentile" % len(null))

    p95 = float(np.percentile(null, 95))
    empirical_p = float((null >= mean_ic).mean())

    detail["null_permutations"] = int(len(null))
    detail["null_mean_ic"] = round(float(null.mean()), 5)
    detail["null_p95_ic"] = round(p95, 5)
    detail["empirical_p"] = round(empirical_p, 4)

    # ---- decay ------------------------------------------------------------
    #
    # E13's finding, as a gate. A signal at 0.105 on h=1 that is 0.015 by h=20
    # has not survived to the horizon anyone can trade, and reporting the h=1
    # number as "the IC" is the mistake that cost this programme a year.
    curve = {}

    for h in decay_horizons:
        if "fwd_%d" % int(h) not in panel.columns:
            continue
        rows = _per_date_ic(panel, int(h), min_names, score=score)
        values = _finite(rows["ic"]) if not rows.empty else np.array([])
        curve[int(h)] = round(float(values.mean()), 5) if len(values) else None

    detail["decay_curve"] = curve

    peak = max((v for v in curve.values() if v is not None), default=None)

    retention = (mean_ic / peak) if (peak and peak > 0) else None

    detail["peak_ic"] = peak
    detail["retention_at_traded_horizon"] = (None if retention is None
                                             else round(retention, 4))
    detail["min_decay_retention"] = min_decay_retention

    return GateResult(stages.ECONOMIC_SIGNAL, {
        "ic": bool(mean_ic >= min_ic),
        "rank_correlation": bool(icir >= min_icir
                                 and pct_positive >= min_pct_positive),
        "random_baseline": bool(mean_ic > p95),
        # None retention is a failure, not a pass: a signal whose peak IC is
        # zero or negative has no curve to retain anything of.
        "factor_decay": bool(retention is not None
                             and retention >= min_decay_retention),
    }, detail)


# ==============================================================
# STAGE 3 -- EXECUTION REALISM
# ==============================================================

def execution_realism(panel, thresholds, horizon, basket, round_trip_pct,
                      score="score"):
    """Stage 3. Can the edge pay for itself, and can the order be filled.

    THIS RUNS BEFORE ROBUSTNESS ON PURPOSE. A walk-forward costs days; the
    arithmetic below costs seconds and would have ended Phase 1 in week two:
    a 0.55% top-decile excess against a 1.04% round trip is a -69%/year
    strategy, and it was not computed until experiment 13.

    Checks
    ------
    transaction_costs    excess per period exceeds the round trip it pays
    liquidity            the basket is not concentrated in untradeable names
    participation_limits the required size fits inside the ADV allowance
    slippage             the edge survives the preregistered slippage charge
    queue_effects        the top decile is not systematically limit-locked at
                         the moment it would be bought
    """

    (max_queue_ratio, min_fill_rate, max_participation,
     slippage_pct, min_liquid_share) = _need(
        thresholds, "max_queue_ratio", "min_fill_rate_pct",
        "max_participation_pct", "slippage_pct", "min_liquid_share")

    horizon = int(horizon)
    basket = int(basket)

    column = "fwd_%d" % horizon

    if column not in panel.columns:
        raise GateInputMissing("panel has no %s column" % column)

    detail = {"horizon": horizon, "basket": basket,
              "round_trip_pct": round_trip_pct,
              "slippage_pct": slippage_pct}

    # ---- the book, date by date ------------------------------------------
    rows = []
    held = set()

    for date, block in panel.groupby("date", sort=True):

        pool = block.dropna(subset=[score, column])

        if len(pool) < basket:
            continue

        picks = pool.sort_values(score, ascending=False).head(basket)

        market = float(pool[column].mean())

        requested = float(picks[column].mean())

        if "next_unbuyable" in picks.columns:
            unbuyable = picks["next_unbuyable"].astype(bool).to_numpy()
        else:
            raise GateInputMissing(
                "panel has no next_unbuyable column, so fills cannot be "
                "modelled. A fill rate that is assumed rather than measured is "
                "the assumption that produced a 62% CAGR in E2.")

        # An unfilled slot sits in cash. It is NOT silently refilled with the
        # next name down -- that would be a book nobody could have traded.
        filled = picks[~unbuyable]

        realised = float(filled[column].sum() / len(picks))

        names = set(picks["ins_code"])
        replaced = 1.0 if not held else len(names - held) / float(len(names))
        held = names

        row = {
            "date": date, "market_pct": market,
            "requested_pct": requested, "realised_pct": realised,
            "fill_rate": float((~unbuyable).mean()),
            "turnover": replaced,
        }

        if "next_locked_up" in pool.columns:
            row["basket_lock_rate"] = float(
                picks["next_locked_up"].astype(bool).mean())
            row["universe_lock_rate"] = float(
                pool["next_locked_up"].astype(bool).mean())

        if "adv" in pool.columns:
            floor = float(pool["adv"].quantile(0.20))
            row["liquid_share"] = float((picks["adv"] > floor).mean())
            row["basket_adv_median"] = float(picks["adv"].median())

        rows.append(row)

    book = pd.DataFrame(rows)

    if book.empty:
        raise GateInputMissing(
            "no date carried a full basket of %d names" % basket)

    detail["dates"] = int(len(book))

    # ---- transaction costs ------------------------------------------------
    gross = float((book["realised_pct"] - book["market_pct"]).mean())
    turnover = float(book["turnover"].mean())
    cost = turnover * float(round_trip_pct)
    net = gross - cost

    requested_excess = float((book["requested_pct"] - book["market_pct"]).mean())
    shortfall = requested_excess - gross

    detail.update({
        "requested_excess_pct": round(requested_excess, 4),
        "realised_excess_pct": round(gross, 4),
        "execution_shortfall_pct": round(shortfall, 4),
        "shortfall_share": (round(shortfall / requested_excess, 4)
                            if requested_excess else None),
        "turnover_per_period": round(turnover, 4),
        "cost_per_period_pct": round(cost, 4),
        "net_per_period_pct": round(net, 4),
        "excess_t": _t_stat(book["realised_pct"] - book["market_pct"]),
    })

    # ---- slippage ---------------------------------------------------------
    #
    # Every result this programme produced held slippage at 0.0%. A gate that
    # inherits that assumption inherits the blind spot, so the charge is
    # preregistered and applied to both legs of every round trip.
    slipped = net - turnover * 2.0 * float(slippage_pct)

    detail["net_after_slippage_pct"] = round(slipped, 4)

    # ---- fills, queues, liquidity ----------------------------------------
    fill_rate = float(book["fill_rate"].mean() * 100.0)

    detail["fill_rate_pct"] = round(fill_rate, 2)
    detail["min_fill_rate_pct"] = min_fill_rate

    if "basket_lock_rate" in book.columns:
        basket_lock = float(book["basket_lock_rate"].mean())
        universe_lock = float(book["universe_lock_rate"].mean())
        queue_ratio = (basket_lock / universe_lock) if universe_lock else None
        detail.update({
            "basket_lock_rate_pct": round(basket_lock * 100.0, 3),
            "universe_lock_rate_pct": round(universe_lock * 100.0, 3),
            "queue_ratio": None if queue_ratio is None else round(queue_ratio, 3),
            "max_queue_ratio": max_queue_ratio,
        })
    else:
        queue_ratio = None
        detail["queue_ratio"] = None

    if "liquid_share" in book.columns:
        liquid_share = float(book["liquid_share"].mean())
        detail["liquid_share"] = round(liquid_share, 4)
        detail["min_liquid_share"] = min_liquid_share
    else:
        liquid_share = None

    # ---- participation ----------------------------------------------------
    #
    # What fraction of a name's daily traded value one basket slot would have
    # to be. Reported as a share of ADV so the number means the same thing at
    # any account size.
    if "basket_adv_median" in book.columns and "position_value" in panel.columns:
        position = float(panel["position_value"].median())
        participation = 100.0 * position / float(book["basket_adv_median"].mean())
    elif "participation_pct" in panel.columns:
        participation = float(panel["participation_pct"].median())
    else:
        participation = None

    detail["participation_pct"] = (None if participation is None
                                   else round(participation, 4))
    detail["max_participation_pct"] = max_participation

    return GateResult(stages.EXECUTION_REALISM, {
        "transaction_costs": bool(net > 0),
        "liquidity": bool(liquid_share is not None
                          and liquid_share >= min_liquid_share),
        # An unmeasured participation rate is not a satisfied participation
        # limit. Supply position sizes or a participation column.
        "participation_limits": bool(participation is not None
                                     and participation <= max_participation),
        "slippage": bool(slipped > 0),
        "queue_effects": bool(queue_ratio is not None
                              and queue_ratio <= max_queue_ratio),
    }, detail)


# ==============================================================
# STAGE 4 -- ROBUSTNESS
# ==============================================================

def robustness(panel, thresholds, horizon, train_dates, test_dates,
               holdout=None, score="score", backtest_validation=None):
    """Stage 4. Walk-forward and out-of-sample, on the declared horizon.

    Two entry points, because this platform has two kinds of hypothesis.

    A PANEL hypothesis ("does this score order returns") walks forward over
    decision dates: fit nothing, but measure the IC on each test block that was
    never used to choose the score, and require most blocks to be positive.

    A PORTFOLIO hypothesis has a `BacktestResult`, and then
    `backtesting.validation.walk_forward()` / `out_of_sample()` are the right
    tools and their output is passed in as `backtest_validation`. Those
    functions have existed and been tested since Phase 1 and were never once
    called by an experiment. This argument is the wire that connects them.
    """

    (min_positive_folds, max_degradation) = _need(
        thresholds, "min_positive_fold_share", "max_oos_degradation")

    horizon = int(horizon)

    detail = {"horizon": horizon}

    if backtest_validation:
        # The shapes `backtesting.validation` actually returns:
        #   walk_forward()  -> {"folds": [...], "summary": {"folds": n,
        #                       "positive_test_folds": k, ...}}
        #   out_of_sample() -> {"segments": {...},
        #                       "degradation": {"drop": 1 - oos/is, ...}}
        # Read them as they are rather than as they might be named: a gate that
        # silently gets None out of a wrong key reports "not measured", and a
        # not-measured robustness check is the one that has to be loudest.
        forward = backtest_validation.get("walk_forward") or {}
        summary = forward.get("summary") or {}

        if not summary.get("available"):
            raise GateInputMissing(
                "walk_forward produced no measurable fold: %s"
                % summary.get("reason", "no summary"))

        total = int(summary["folds"])
        positive = int(summary.get("positive_test_folds") or 0)
        share = (positive / float(total)) if total else None

        oos = backtest_validation.get("out_of_sample") or {}
        drop = (oos.get("degradation") or {}).get("drop")

        # `drop` already carries this module's convention: the share of the
        # in-sample result that did NOT survive. No sign flip.
        degradation_value = None if drop is None else float(drop)

        detail.update({
            "folds": total, "positive_folds": positive,
            "positive_fold_share": (None if share is None else round(share, 4)),
            "mean_test_metric": summary.get("mean_test_metric"),
            "parameter_stability": summary.get("parameter_stability"),
            "oos_available": bool(oos.get("available")),
            "oos_degradation": degradation_value,
            "source": "backtesting.validation",
        })
    else:
        min_names = thresholds.get("min_names_per_date", 30)

        frame = _per_date_ic(panel, horizon, min_names, score=score)

        if frame.empty:
            raise GateInputMissing("no scoreable dates at h=%d" % horizon)

        frame = frame.sort_values("date").reset_index(drop=True)

        folds = _walk_forward_folds(frame, train_dates, test_dates)

        if not folds:
            raise GateInputMissing(
                "%d dates cannot support a walk-forward of train=%d test=%d"
                % (len(frame), train_dates, test_dates))

        positive = [f for f in folds if f["test_ic"] is not None
                    and f["test_ic"] > 0]

        share = len(positive) / float(len(folds))

        in_sample = _finite([f["train_ic"] for f in folds
                             if f["train_ic"] is not None])
        out_sample = _finite([f["test_ic"] for f in folds
                              if f["test_ic"] is not None])

        # Degradation as a SHARE of the in-sample number rather than a
        # difference, so it means the same thing whatever the IC's scale.
        degradation = (None if not len(in_sample) or in_sample.mean() <= 0
                       else float(1.0 - out_sample.mean() / in_sample.mean()))

        detail.update({
            "folds": len(folds), "positive_folds": len(positive),
            "positive_fold_share": round(share, 4),
            "in_sample_ic": round(float(in_sample.mean()), 5) if len(in_sample) else None,
            "out_sample_ic": round(float(out_sample.mean()), 5) if len(out_sample) else None,
            "oos_degradation": None if degradation is None else round(degradation, 4),
            "fold_detail": folds,
            "source": "panel walk-forward",
        })

        degradation_value = degradation

    detail["min_positive_fold_share"] = min_positive_folds
    detail["max_oos_degradation"] = max_degradation

    # ---- the untouched holdout -------------------------------------------
    #
    # Optional, and when it is absent the check is False rather than skipped.
    # An out-of-sample claim with no out-of-sample data is the claim this
    # platform exists to refuse.
    if holdout is not None and len(holdout):
        rows = _per_date_ic(holdout, horizon,
                            thresholds.get("min_names_per_date", 30),
                            score=score)
        values = _finite(rows["ic"]) if not rows.empty else np.array([])
        holdout_ic = float(values.mean()) if len(values) else None
        detail["holdout_dates"] = int(len(values))
        detail["holdout_ic"] = (None if holdout_ic is None
                                else round(holdout_ic, 5))
        out_of_sample_ok = bool(holdout_ic is not None and holdout_ic > 0
                                and (degradation_value is None
                                     or degradation_value <= max_degradation))
    else:
        detail["holdout_ic"] = None
        out_of_sample_ok = bool(degradation_value is not None
                                and degradation_value <= max_degradation)

    return GateResult(stages.ROBUSTNESS, {
        "walk_forward": bool(share is not None and share >= min_positive_folds),
        "out_of_sample": out_of_sample_ok,
    }, detail)


def _walk_forward_folds(frame, train_dates, test_dates):
    """Rolling, non-overlapping test blocks over the date axis."""

    folds = []

    train_dates = int(train_dates)
    test_dates = int(test_dates)

    start = 0

    while start + train_dates + test_dates <= len(frame):
        train = frame.iloc[start:start + train_dates]
        test = frame.iloc[start + train_dates:start + train_dates + test_dates]

        train_ic = _finite(train["ic"])
        test_ic = _finite(test["ic"])

        folds.append({
            "fold": len(folds) + 1,
            "train_start": str(train["date"].iloc[0].date()),
            "train_end": str(train["date"].iloc[-1].date()),
            "test_start": str(test["date"].iloc[0].date()),
            "test_end": str(test["date"].iloc[-1].date()),
            "train_ic": round(float(train_ic.mean()), 5) if len(train_ic) else None,
            "test_ic": round(float(test_ic.mean()), 5) if len(test_ic) else None,
        })

        start += test_dates

    return folds


# ==============================================================
# STAGE 5 -- STATISTICAL VALIDATION
# ==============================================================

def statistical(treatment, controls, thresholds, seed, block_size=5,
                draws=5000):
    """Stage 5. Is the result distinguishable from luck.

    `treatment` is the per-period series the strategy produced. `controls` is a
    list of the same series from random-ranking arms -- the null model that
    keeps eligibility, sizing, exits, caps, execution and costs identical and
    replaces only the order. Anything else is not a control; it is a different
    strategy.

    Checks
    ------
    monte_carlo           the treatment sits above the preregistered percentile
                          of the control distribution
    bootstrap             a stationary block bootstrap on the PAIRED per-period
                          difference excludes zero
    confidence_intervals  the interval is finite and reported
    """

    (min_percentile,) = _need(thresholds, "min_control_percentile")

    treatment = _finite(treatment)

    if len(treatment) < 3:
        raise GateInputMissing(
            "the treatment series has %d usable periods" % len(treatment))

    if not controls:
        raise GateInputMissing(
            "no control arms. A percentile against nothing is not a statistic.")

    control_means = _finite([np.nanmean(_finite(c)) for c in controls])

    if len(control_means) < 5:
        raise GateInputMissing(
            "only %d usable control arms; the distribution cannot support a "
            "percentile" % len(control_means))

    point = float(treatment.mean())

    percentile = float((control_means < point).mean() * 100.0)

    empirical_p = float((control_means >= point).mean())

    detail = {
        "periods": int(len(treatment)),
        "controls": int(len(control_means)),
        "treatment_mean": round(point, 5),
        "control_mean": round(float(control_means.mean()), 5),
        "control_p95": round(float(np.percentile(control_means, 95)), 5),
        "percentile": round(percentile, 2),
        "empirical_p": round(empirical_p, 4),
        "min_control_percentile": min_percentile,
    }

    # ---- block bootstrap on the paired difference -------------------------
    #
    # Paired, and blocked. Paired because the arms share every period and an
    # unpaired test throws that away; blocked because daily excess returns on
    # this market are autocorrelated and an iid bootstrap would report an
    # interval far tighter than the data supports.
    reference = _finite(np.nanmean(np.vstack(
        [np.resize(_finite(c), len(treatment)) for c in controls]), axis=0))

    n = min(len(treatment), len(reference))

    difference = treatment[:n] - reference[:n]

    rng = np.random.default_rng(int(seed))

    block_size = max(1, int(block_size))

    means = []

    for _ in range(int(draws)):
        sample = []
        while len(sample) < n:
            start = int(rng.integers(0, n))
            sample.extend(difference[start:start + block_size])
        means.append(float(np.mean(sample[:n])))

    means = np.asarray(means, float)

    low = float(np.percentile(means, 2.5))
    high = float(np.percentile(means, 97.5))

    detail.update({
        "bootstrap_draws": int(draws),
        "block_size": block_size,
        "paired_difference_mean": round(float(difference.mean()), 5),
        "ci95": [round(low, 5), round(high, 5)],
        "ci_excludes_zero": bool(low > 0 or high < 0),
    })

    return GateResult(stages.STATISTICAL, {
        "monte_carlo": bool(percentile >= min_percentile),
        # Excluding zero is not enough -- an interval entirely BELOW zero
        # excludes zero and means the strategy lost. The sign is part of the
        # condition.
        "bootstrap": bool(low > 0),
        "confidence_intervals": bool(math.isfinite(low) and math.isfinite(high)
                                     and low <= float(difference.mean()) <= high),
    }, detail)


# ==============================================================
# STAGE 6 -- PAPER TRADING READINESS
# ==============================================================

# What each operational check has to be able to point at. A boolean with no
# evidence behind it is someone's recollection.
PAPER_EVIDENCE = {
    "risk_limits": "position cap, sector cap, gross exposure cap, stop policy",
    "kill_switch": "the condition that halts trading and who can trigger it",
    "monitoring": "what is watched live and the alert that fires",
    "reconciliation": "how paper fills are compared with the tape, daily",
}


def paper_readiness(evidence, thresholds):
    """Stage 6. Operational preconditions, evidenced rather than asserted.

    This gate cannot measure anything -- there is no dataset for "is there a
    kill switch". What it CAN refuse is a bare True. Each check passes only when
    the caller supplies a non-empty description of the control and the artifact
    that implements it, and both are stored in the record for a reviewer.
    """

    (min_paper_sessions,) = _need(thresholds, "min_paper_sessions")

    evidence = evidence or {}

    metrics = {}
    detail = {"required": PAPER_EVIDENCE,
              "min_paper_sessions": min_paper_sessions}

    for check, description in PAPER_EVIDENCE.items():
        entry = evidence.get(check)
        ok = bool(isinstance(entry, dict)
                  and str(entry.get("control", "")).strip()
                  and str(entry.get("artifact", "")).strip())
        metrics[check] = ok
        detail[check] = entry if ok else "MISSING -- needs %s" % description

    sessions = evidence.get("paper_sessions")

    detail["paper_sessions"] = sessions

    if sessions is None or int(sessions) < int(min_paper_sessions):
        # Folded into monitoring rather than added as a check the stage
        # definition does not have: `stages.REQUIRED_CHECKS` is the contract and
        # a gate does not get to extend it.
        metrics["monitoring"] = False
        detail["monitoring"] = (
            "MISSING -- %s paper sessions recorded against a required %s"
            % (sessions, min_paper_sessions))

    return GateResult(stages.PAPER_READINESS, metrics, detail)
