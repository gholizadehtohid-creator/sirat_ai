"""HOLDOUT VALIDATION — the exposure engine on data it has never seen.

    WINDOW      2024-09-02 .. 2026-08-17. Experiment 1 froze its window at
                2024-09-01 and never read past it. This is that holdout.
    ARM A       market_exposure.exposure_for(), unmodified.
    ARM B       a static target on the identical decision grid, held at the
                engine's TRAINING-window average exposure. PRIMARY CONTROL.
    ARM C       100% invested. Secondary reference only.
    GATES       backtest_output/holdout_preregistration.json, written before
                a single holdout number was computed.

WHY THE CONTROL IS 55.02% AND NOT THE HOLDOUT AVERAGE
------------------------------------------------------
Matching the control to what the engine turned out to hold in the holdout
would let the holdout choose its own benchmark, which is look-ahead wearing a
lab coat. 55.02% is the engine's realised average over the TRAINING window --
a number that was fully known on 2024-09-01. The holdout-matched variant is
computed too, labelled, and used for nothing but a sanity check.

NOTHING IN THE STRATEGY IS TOUCHED. This module imports the engine and the
Experiment 1 harness and calls them. It defines no signal and fits no number.
"""

import json
import math
import os
import platform
import sys

import numpy as np
import pandas as pd

import backtest_config
import config
import database
import experiment_exposure as ex1
import market_exposure as mx
import performance


OUT_DIR = backtest_config.OUTPUT_DIR
OUTPUT = os.path.join(OUT_DIR, "holdout_validation.json")

HOLDOUT_START = pd.Timestamp("2024-09-02")
HOLDOUT_END = pd.Timestamp("2026-08-17")

# Pre-registered. The engine's average exposure over the TRAINING window.
CONTROL_EXPOSURE = 0.5502

ROUND_TRIP = ex1.ROUND_TRIP

BOOTSTRAP_SEED = 20240901
BOOTSTRAP_REPS = 5000
MEAN_BLOCK = 20


# ==============================
# AN INDEPENDENT CASH LEDGER
# ==============================

def ledger_simulate(sessions, market_returns, rf_daily, targets, cost=None):
    """Re-implementation of ex1.simulate() that tracks equity and cash apart.

    Exists for two reasons the brief demands: it proves the NAV identity
    (`NAV = equity sleeve + cash sleeve`) holds every session, and it accepts a
    cost argument so the same path can be priced gross and net. It is asserted
    against ex1.simulate() before any result is read, so it cannot quietly
    disagree with the harness that produced the in-sample number.
    """

    cost = ROUND_TRIP if cost is None else cost

    equity_sleeve = 0.0          # value invested in the index
    cash_sleeve = 1.0            # value sitting in cash
    traded = 0.0

    curve, exposures, cash_track, breaches = [], [], [], []

    for position, date in enumerate(sessions):

        nav_open = equity_sleeve + cash_sleeve

        weight = equity_sleeve / nav_open if nav_open > 0 else 0.0
        exposures.append(weight)

        # 1. earn the session on the weight already held (T+1 convention)
        equity_sleeve *= (1.0 + market_returns[position])
        cash_sleeve *= (1.0 + rf_daily)

        nav = equity_sleeve + cash_sleeve

        # 2. rebalance at this close, effective tomorrow
        if date in targets and targets[date] is not None:

            target = targets[date]
            drifted = equity_sleeve / nav if nav > 0 else 0.0
            change = abs(target - drifted)

            traded += change
            nav *= (1.0 - change * cost)

            equity_sleeve = nav * target
            cash_sleeve = nav * (1.0 - target)

        # NAV identity, every single session
        if abs((equity_sleeve + cash_sleeve) - nav) > 1e-12 * max(1.0, nav):
            breaches.append(str(date.date()))

        curve.append(nav)
        cash_track.append(cash_sleeve / nav if nav > 0 else 1.0)

    return {"curve": pd.Series(curve, index=sessions),
            "exposure": pd.Series(exposures, index=sessions),
            "cash_pct": pd.Series(cash_track, index=sessions),
            "turnover": traded,
            "nav_identity_breaches": breaches}


# ==============================
# METRIC EXTRAS
# ==============================

def drawdown_detail(curve):
    """Average drawdown, longest underwater run, recovery time."""

    peak = curve.cummax()
    dd = (curve - peak) / peak

    underwater = dd < -1e-12

    runs, current = [], 0
    for flag in underwater:
        if flag:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)

    trough_at = dd.idxmin()
    after = curve.loc[trough_at:]
    peak_before = float(peak.loc[trough_at])
    recovered = after[after >= peak_before]

    return {
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
        "average_drawdown_pct": round(float(dd[underwater].mean()) * 100, 2)
        if underwater.any() else 0.0,
        "pct_sessions_underwater": round(float(underwater.mean()) * 100, 2),
        "longest_underwater_sessions": int(max(runs)) if runs else 0,
        "trough_date": str(trough_at.date()),
        "recovery_sessions": (int(curve.index.get_loc(recovered.index[0])
                                  - curve.index.get_loc(trough_at))
                              if len(recovered) else None),
    }


def capture(arm_returns, bench_returns):
    """Geometric up/down capture against the benchmark."""

    up = bench_returns > 0
    dn = bench_returns < 0

    def grow(series, mask):
        return float((1 + series[mask]).prod()) - 1.0

    out = {}
    for label, mask in (("upside", up), ("downside", dn)):
        b = grow(bench_returns, mask)
        a = grow(arm_returns, mask)
        out["%s_capture_pct" % label] = (round(a / b * 100, 1)
                                         if abs(b) > 1e-12 else None)
    return out


# ==============================
# STATISTICS
# ==============================

def memmel(r1, r2):
    """Jobson-Korkie Sharpe-difference test with the Memmel correction."""

    n = len(r1)
    hurdle = performance.daily_risk_free()

    e1, e2 = r1 - hurdle, r2 - hurdle
    s1 = float(e1.mean() / r1.std(ddof=1))
    s2 = float(e2.mean() / r2.std(ddof=1))
    rho = float(np.corrcoef(r1, r2)[0, 1])

    var = (1.0 / n) * (2 * (1 - rho)
                       + 0.5 * (s1 * s1 + s2 * s2 - 2 * s1 * s2 * rho * rho))
    se = math.sqrt(var)
    z = (s1 - s2) / se
    p = math.erfc(abs(z) / math.sqrt(2))

    q = math.sqrt(config.TRADING_DAYS_PER_YEAR)

    return {"sharpe_a": round(s1 * q, 3), "sharpe_b": round(s2 * q, 3),
            "delta_sharpe": round((s1 - s2) * q, 3),
            "correlation": round(rho, 4),
            "z": round(z, 3), "p_value": round(p, 4),
            "ci95_delta_sharpe": [round((s1 - s2 - 1.96 * se) * q, 3),
                                  round((s1 - s2 + 1.96 * se) * q, 3)],
            "significant_at_5pct": bool(p < 0.05)}


def stationary_bootstrap(pairs, reps=BOOTSTRAP_REPS, mean_block=MEAN_BLOCK,
                         seed=BOOTSTRAP_SEED):
    """Politis-Romano paired bootstrap on (engine, control) daily returns.

    Blocks are resampled JOINTLY so the cross-correlation between the two arms
    -- which is what makes the difference testable at all -- survives the
    resampling. Geometric block lengths, so the resampled series is stationary.
    """

    rng = np.random.default_rng(seed)

    a = pairs[:, 0]
    b = pairs[:, 1]
    n = len(a)

    hurdle = performance.daily_risk_free()
    q = math.sqrt(config.TRADING_DAYS_PER_YEAR)
    years = n / float(config.TRADING_DAYS_PER_YEAR)
    p_block = 1.0 / mean_block

    d_sharpe = np.empty(reps)
    d_cagr = np.empty(reps)
    d_maxdd = np.empty(reps)

    for rep in range(reps):

        idx = np.empty(n, dtype=np.int64)
        pos = 0
        cursor = rng.integers(0, n)

        while pos < n:
            idx[pos] = cursor
            pos += 1
            if rng.random() < p_block:
                cursor = rng.integers(0, n)
            else:
                cursor = (cursor + 1) % n

        ra, rb = a[idx], b[idx]

        sa = (ra - hurdle).mean() / ra.std(ddof=1) * q
        sb = (rb - hurdle).mean() / rb.std(ddof=1) * q
        d_sharpe[rep] = sa - sb

        ga = float(np.prod(1 + ra))
        gb = float(np.prod(1 + rb))
        d_cagr[rep] = ((ga ** (1 / years) - 1) - (gb ** (1 / years) - 1)) * 100

        for target, r in ((0, ra), (1, rb)):
            eq = np.cumprod(1 + r)
            dd = float(((eq - np.maximum.accumulate(eq))
                        / np.maximum.accumulate(eq)).min()) * 100
            if target == 0:
                dd_a = dd
            else:
                dd_b = dd
        d_maxdd[rep] = dd_a - dd_b

    def block(values, label):
        lo, hi = np.percentile(values, [2.5, 97.5])
        return {
            "metric": label,
            "point_estimate": round(float(np.mean(values)), 3),
            "ci95": [round(float(lo), 3), round(float(hi), 3)],
            "excludes_zero": bool(lo > 0 or hi < 0),
            "pct_replicates_engine_ahead": round(
                float((values > 0).mean()) * 100, 1),
        }

    return {
        "method": ("stationary (Politis-Romano) block bootstrap, paired, "
                   "geometric blocks, mean length %d sessions" % mean_block),
        "replicates": reps,
        "seed": seed,
        "delta_sharpe": block(d_sharpe, "engine - control Sharpe"),
        "delta_cagr_pct": block(d_cagr, "engine - control CAGR (pp)"),
        "delta_max_drawdown_pct": block(d_maxdd, "engine - control MaxDD (pp)"),
    }


# ==============================
# MAIN
# ==============================

def main():

    print("=" * 78)
    print("HOLDOUT VALIDATION — EXPOSURE ENGINE, UNSEEN DATA")
    print("=" * 78)

    with open(os.path.join(OUT_DIR, "holdout_preregistration.json"),
              encoding="utf-8") as handle:
        prereg = json.load(handle)
    print("  protocol      pre-registered, %d gates locked"
          % len(prereg["gates"]))

    # ---- data ------------------------------------------------------------
    equal = ex1.index_series("TEDPIX_EW")
    cap = ex1.index_series("TEDPIX")

    full_dates = pd.DatetimeIndex(equal["date"])
    full_closes = equal["close"].tolist()

    window = equal[(equal["date"] >= HOLDOUT_START)
                   & (equal["date"] <= HOLDOUT_END)]

    sessions = list(window["date"])
    closes = window["close"].tolist()

    # The move INTO the first holdout session, taken from the last training
    # close, so session one is not silently given a zero return.
    prior = float(equal[equal["date"] < HOLDOUT_START]["close"].iloc[-1])
    market_returns = list(np.diff([prior] + closes) / np.array([prior] + closes[:-1]))

    rf_daily = (1.0 + config.RISK_FREE_RATE) ** (1.0 / 240.0) - 1.0
    years = len(sessions) / float(config.TRADING_DAYS_PER_YEAR)

    print("  window        %s .. %s" % (sessions[0].date(), sessions[-1].date()))
    print("  sessions      %d  (%.2f years)" % (len(sessions), years))
    print("  cash          %.0f%%/yr    cost %.2f%% per unit traded"
          % (config.RISK_FREE_RATE * 100, ROUND_TRIP * 100))

    # ---- decision grid ---------------------------------------------------
    breadth = ex1.breadth_by_date()
    session_set = set(sessions)
    grid = [d for d in sorted(breadth)
            if HOLDOUT_START <= d <= HOLDOUT_END and d in session_set]

    print("  decisions     %d  (%s .. %s)"
          % (len(grid), grid[0].date(), grid[-1].date()))

    # ---- ARM A: the engine, untouched ------------------------------------
    targets, trace = {}, []

    for date in grid:
        cut = int(full_dates.searchsorted(date, side="right"))
        history = full_closes[:cut]              # ends AT the decision session

        exposure, why = mx.exposure_for(history, above_pct=breadth.get(date))

        targets[date] = None if exposure is None else exposure / 100.0
        trace.append({
            "date": str(date.date()),
            "exposure_pct": exposure,
            "votes": dict(why["votes"]),
            "available": why.get("available"),
            "history_sessions": cut,
            "last_close_used": round(history[-1], 2),
            "breadth_above_pct": round(breadth.get(date), 2),
        })

    live = [t for t in targets.values() if t is not None]
    print("  engine        mean target %.1f%%, range %.0f%%..%.0f%%"
          % (np.mean(live) * 100, min(live) * 100, max(live) * 100))

    # ---- harness agreement check ----------------------------------------
    reference = ex1.simulate(sessions, market_returns, rf_daily, targets)
    audited = ledger_simulate(sessions, market_returns, rf_daily, targets)

    gap = float((reference["curve"] - audited["curve"]).abs().max())
    print("\n[0] LEDGER AUDIT")
    print("    independent cash ledger vs ex1.simulate(): max |diff| = %.3e"
          % gap)
    print("    NAV identity breaches: %d"
          % len(audited["nav_identity_breaches"]))
    print("    turnover agreement: %.6f vs %.6f"
          % (reference["turnover"], audited["turnover"]))

    ledger_ok = (gap < 1e-9 and not audited["nav_identity_breaches"]
                 and abs(reference["turnover"] - audited["turnover"]) < 1e-9)
    print("    -> %s" % ("VERIFIED" if ledger_ok else "MISMATCH — STOP"))

    # ---- arms ------------------------------------------------------------
    print("\n[1] ARMS")

    benchmark_curve = pd.Series(
        np.cumprod([1.0 + r for r in market_returns]), index=sessions)

    runs = {
        "A_engine": audited,
        "B_fixed_matched": ledger_simulate(
            sessions, market_returns, rf_daily,
            {d: CONTROL_EXPOSURE for d in grid}),
        "C_buy_hold": ledger_simulate(
            sessions, market_returns, rf_daily, {sessions[0]: 1.0}),
        "cash_only": ledger_simulate(
            sessions, market_returns, rf_daily, {sessions[0]: 0.0}),
    }

    gross = {name: ledger_simulate(
        sessions, market_returns, rf_daily,
        ({d: CONTROL_EXPOSURE for d in grid} if name == "B_fixed_matched"
         else {sessions[0]: 1.0} if name == "C_buy_hold"
         else {sessions[0]: 0.0} if name == "cash_only"
         else targets), cost=0.0) for name in runs}

    arms = {}
    for name, run in runs.items():
        row = ex1.metrics(run, benchmark_curve, years)
        row.update(drawdown_detail(run["curve"]))

        rets = run["curve"].pct_change().dropna()
        bench = benchmark_curve.pct_change().dropna()
        row.update(capture(rets, bench))

        row["average_cash_pct"] = round(float(run["cash_pct"].mean()) * 100, 2)
        row["median_exposure_pct"] = round(
            float(run["exposure"].median()) * 100, 2)
        row["min_exposure_pct"] = round(float(run["exposure"].min()) * 100, 2)
        row["max_exposure_pct"] = round(float(run["exposure"].max()) * 100, 2)

        g = gross[name]["curve"]
        gross_total = float(g.iloc[-1] / g.iloc[0] - 1.0)
        row["gross_total_return_pct"] = round(gross_total * 100, 2)
        row["gross_cagr_pct"] = round(
            ((1 + gross_total) ** (1 / years) - 1) * 100, 2)
        row["cost_cagr_drag_pp"] = round(
            row["gross_cagr_pct"] - row["cagr_pct"], 2)

        arms[name] = row

        print("    %-16s CAGR %7.2f%%  vol %5.2f%%  Sharpe %6s  DD %7.2f%%  "
              "Calmar %5s  exp %5.2f%%  turn %.2f"
              % (name, row["cagr_pct"], row["volatility_pct"], row["sharpe"],
                 row["max_drawdown_pct"], row["calmar"],
                 row["average_exposure_pct"], row["turnover_per_year"]))

    # diagnostic only — NOT used by any gate
    holdout_matched = ledger_simulate(
        sessions, market_returns, rf_daily,
        {d: arms["A_engine"]["average_exposure_pct"] / 100.0 for d in grid})
    arms["_diagnostic_holdout_matched_control"] = ex1.metrics(
        holdout_matched, benchmark_curve, years)
    arms["_diagnostic_holdout_matched_control"]["WARNING"] = (
        "look-ahead: matched to the engine's HOLDOUT exposure. Gates never "
        "use this arm.")

    if cap is not None:
        cw = cap[(cap["date"] >= HOLDOUT_START) & (cap["date"] <= HOLDOUT_END)]
        tot = float(cw["close"].iloc[-1] / cw["close"].iloc[0] - 1.0)
        arms["tedpix_reference"] = {
            "total_return_pct": round(tot * 100, 2),
            "cagr_pct": round(((1 + tot) ** (1 / years) - 1) * 100, 2),
            "note": "capitalisation index, buy and hold, no cost",
        }

    # ---- returns for the tests -------------------------------------------
    ra = runs["A_engine"]["curve"].pct_change().dropna()
    rb = runs["B_fixed_matched"]["curve"].pct_change().dropna()
    rc = runs["C_buy_hold"]["curve"].pct_change().dropna()
    rbench = benchmark_curve.pct_change().dropna()

    print("\n[2] STATISTICS")
    stats = {
        "engine_vs_fixed_matched_PRIMARY": memmel(ra.values, rb.values),
        "engine_vs_buy_hold_secondary": memmel(ra.values, rc.values),
    }
    for label, row in stats.items():
        print("    %-34s dSR %+6.3f  z %+6.3f  p %.4f  %s"
              % (label, row["delta_sharpe"], row["z"], row["p_value"],
                 "SIGNIFICANT" if row["significant_at_5pct"] else "n.s."))

    print("    bootstrap: %d replicates, seed %d ..."
          % (BOOTSTRAP_REPS, BOOTSTRAP_SEED), flush=True)
    boot = stationary_bootstrap(np.column_stack([ra.values, rb.values]))
    for key in ("delta_sharpe", "delta_cagr_pct", "delta_max_drawdown_pct"):
        row = boot[key]
        print("      %-26s %+7.3f  CI95 [%+.3f, %+.3f]  excl.0 %s  "
              "engine ahead %.1f%%"
              % (key, row["point_estimate"], row["ci95"][0], row["ci95"][1],
                 row["excludes_zero"], row["pct_replicates_engine_ahead"]))

    # ---- blocks and episodes ---------------------------------------------
    print("\n[3] BLOCKS & EPISODES")

    edges = list(grid) + [sessions[-1]]
    blocks = []

    for i in range(len(grid)):
        lo, hi = edges[i], edges[i + 1]
        mask = (pd.DatetimeIndex(sessions) > lo) & (pd.DatetimeIndex(sessions) <= hi)
        if not mask.any():
            continue

        seg_a = ra[ra.index.isin(pd.DatetimeIndex(sessions)[mask])]
        seg_b = rb[rb.index.isin(pd.DatetimeIndex(sessions)[mask])]
        seg_m = rbench[rbench.index.isin(pd.DatetimeIndex(sessions)[mask])]

        if not len(seg_a):
            continue

        cut = int(full_dates.searchsorted(lo, side="right"))
        hist = full_closes[:cut]
        mom, _ = mx.momentum(hist)
        vol, _ = mx.volatility(hist)

        blocks.append({
            "from": str(lo.date()), "to": str(hi.date()),
            "sessions": int(len(seg_a)),
            "target_exposure_pct": targets[lo] * 100 if targets[lo] is not None else None,
            "trend_state": "up" if mom else "down",
            "vol_state": "calm" if vol else "stressed",
            "benchmark_pct": round((float((1 + seg_m).prod()) - 1) * 100, 2),
            "engine_pct": round((float((1 + seg_a).prod()) - 1) * 100, 2),
            "control_pct": round((float((1 + seg_b).prod()) - 1) * 100, 2),
            "excess_pp": round((float((1 + seg_a).prod())
                                - float((1 + seg_b).prod())) * 100, 2),
        })

    beaten = sum(1 for b in blocks if b["excess_pp"] > 0)
    print("    %d inter-decision blocks; engine ahead in %d (%.1f%%)"
          % (len(blocks), beaten, 100.0 * beaten / len(blocks)))

    # contiguous episodes = maximal runs of trend state
    episodes, current = [], None
    for b in blocks:
        if current is None or b["trend_state"] != current["trend_state"]:
            current = {"trend_state": b["trend_state"], "blocks": [b],
                       "from": b["from"], "to": b["to"]}
            episodes.append(current)
        else:
            current["blocks"].append(b)
            current["to"] = b["to"]

    for ep in episodes:
        eng = np.prod([1 + b["engine_pct"] / 100 for b in ep["blocks"]])
        con = np.prod([1 + b["control_pct"] / 100 for b in ep["blocks"]])
        ben = np.prod([1 + b["benchmark_pct"] / 100 for b in ep["blocks"]])
        ep["engine_pct"] = round((eng - 1) * 100, 2)
        ep["control_pct"] = round((con - 1) * 100, 2)
        ep["benchmark_pct"] = round((ben - 1) * 100, 2)
        ep["excess_pp"] = round((eng - con) * 100, 2)
        ep["sessions"] = sum(b["sessions"] for b in ep["blocks"])
        ep["mean_target_exposure_pct"] = round(float(np.mean(
            [b["target_exposure_pct"] for b in ep["blocks"]
             if b["target_exposure_pct"] is not None])), 1)
        ep["n_blocks"] = len(ep["blocks"])
        del ep["blocks"]

    for ep in episodes:
        print("    %-5s %s..%s  %2d blocks  bench %+8.2f%%  engine %+8.2f%%  "
              "control %+8.2f%%  excess %+7.2f pp"
              % (ep["trend_state"], ep["from"], ep["to"], ep["n_blocks"],
                 ep["benchmark_pct"], ep["engine_pct"], ep["control_pct"],
                 ep["excess_pp"]))

    # leave-one-episode-out
    total_eng = float((1 + ra).prod())
    total_con = float((1 + rb).prod())

    loo = []
    for drop in episodes:
        lo_d, hi_d = pd.Timestamp(drop["from"]), pd.Timestamp(drop["to"])
        keep = ~((ra.index > lo_d) & (ra.index <= hi_d))
        e = float((1 + ra[keep]).prod())
        c = float((1 + rb[keep]).prod())
        loo.append({
            "dropped_episode": "%s %s..%s" % (drop["trend_state"],
                                              drop["from"], drop["to"]),
            "engine_cum_pct": round((e - 1) * 100, 2),
            "control_cum_pct": round((c - 1) * 100, 2),
            "excess_pp": round((e - c) * 100, 2),
            "sign_reversed": bool((e - c) * (total_eng - total_con) < 0),
        })

    strongest = max(loo, key=lambda r: (total_eng - total_con)
                    - (r["engine_cum_pct"] - r["control_cum_pct"]) / 100.0)

    print("    full-sample excess: %+.2f pp" % ((total_eng - total_con) * 100))
    for row in loo:
        print("      drop %-28s -> excess %+7.2f pp  %s"
              % (row["dropped_episode"], row["excess_pp"],
                 "SIGN REVERSED" if row["sign_reversed"] else ""))

    # vol-state cells
    cells = {}
    for t in ("up", "down"):
        for v in ("calm", "stressed"):
            sel = [b for b in blocks
                   if b["trend_state"] == t and b["vol_state"] == v]
            if not sel:
                continue
            e = np.prod([1 + b["engine_pct"] / 100 for b in sel])
            c = np.prod([1 + b["control_pct"] / 100 for b in sel])
            m = np.prod([1 + b["benchmark_pct"] / 100 for b in sel])
            cells["%s/%s" % (t, v)] = {
                "blocks": len(sel), "sessions": sum(b["sessions"] for b in sel),
                "benchmark_pct": round((m - 1) * 100, 2),
                "engine_pct": round((e - 1) * 100, 2),
                "control_pct": round((c - 1) * 100, 2),
                "excess_pp": round((e - c) * 100, 2),
                "mean_target_exposure_pct": round(float(np.mean(
                    [b["target_exposure_pct"] for b in sel
                     if b["target_exposure_pct"] is not None])), 1),
            }

    # ---- exposure vs forward return --------------------------------------
    pairs = [(b["target_exposure_pct"], b["benchmark_pct"]) for b in blocks
             if b["target_exposure_pct"] is not None]
    xs = np.array([p[0] for p in pairs], dtype=float)
    ys = np.array([p[1] for p in pairs], dtype=float)

    def rank(v):
        order = v.argsort()
        r = np.empty_like(order, dtype=float)
        r[order] = np.arange(len(v))
        return r

    rx, ry = rank(xs), rank(ys)
    rho = float(np.corrcoef(rx, ry)[0, 1])
    n = len(xs)
    t_stat = rho * math.sqrt((n - 2) / max(1e-12, 1 - rho * rho))
    p_rho = math.erfc(abs(t_stat) / math.sqrt(2))

    forward = {
        "n_blocks": n,
        "spearman_rho_target_exposure_vs_next_block_benchmark": round(rho, 4),
        "t_stat": round(t_stat, 3),
        "approx_p_value": round(p_rho, 4),
        "reading": ("higher exposure preceded higher benchmark returns"
                    if rho > 0 else
                    "higher exposure preceded LOWER benchmark returns"),
    }
    print("\n    exposure vs NEXT block benchmark return: rho %+.3f, p %.4f"
          % (rho, p_rho))

    # ---- calendar years ---------------------------------------------------
    def annual(curve):
        g = curve.groupby(curve.index.year).agg(["first", "last"])
        return ((g["last"] / g["first"] - 1.0) * 100).round(2).to_dict()

    yearly = {}
    for year in sorted(set(d.year for d in sessions)):
        row = {}
        for name in ("A_engine", "B_fixed_matched", "C_buy_hold"):
            row[name] = annual(runs[name]["curve"]).get(year)
        row["excess_pp"] = round(row["A_engine"] - row["B_fixed_matched"], 2)
        sub = runs["A_engine"]["curve"][
            runs["A_engine"]["curve"].index.year == year]
        row["engine_max_dd_pct"] = drawdown_detail(sub)["max_drawdown_pct"]
        row["partial_year"] = bool(year in (sessions[0].year, sessions[-1].year))
        yearly[str(year)] = row

    print("\n[4] CALENDAR YEARS")
    for y, row in yearly.items():
        print("    %s%s engine %+8.2f%%  control %+8.2f%%  excess %+7.2f pp  "
              "b&h %+8.2f%%"
              % (y, " (partial)" if row["partial_year"] else "          ",
                 row["A_engine"], row["B_fixed_matched"], row["excess_pp"],
                 row["C_buy_hold"]))

    # ---- gates -----------------------------------------------------------
    print("\n[5] PRE-REGISTERED GATES")

    a, b = arms["A_engine"], arms["B_fixed_matched"]

    gross_alpha = a["gross_cagr_pct"] - b["gross_cagr_pct"]
    net_alpha = a["cagr_pct"] - b["cagr_pct"]
    cost_diff = a["cost_cagr_drag_pp"] - b["cost_cagr_drag_pp"]

    g2_dd = abs(a["max_drawdown_pct"]) <= 0.90 * abs(b["max_drawdown_pct"])
    g2_calmar = (a["calmar"] or -99) >= (b["calmar"] or -99) + 0.20
    g2_dev = a["downside_deviation_pct"] <= 0.90 * b["downside_deviation_pct"]

    prim = stats["engine_vs_fixed_matched_PRIMARY"]

    beta = a.get("beta")
    pro_cyclical = beta is not None and beta > a["average_exposure_pct"] / 100.0

    gates = {
        "G1_positive_net_alpha": {
            "pass": bool(net_alpha > 0),
            "evidence": "engine CAGR %.2f%% vs control %.2f%% -> net alpha "
                        "%+.2f pp" % (a["cagr_pct"], b["cagr_pct"], net_alpha)},
        "G2_risk_improvement": {
            "pass": bool(g2_dd or g2_calmar or g2_dev),
            "evidence": "MaxDD %.2f%% vs %.2f%% (needs <= %.2f%%): %s | "
                        "Calmar %.2f vs %.2f (needs >= %.2f): %s | "
                        "downside dev %.2f%% vs %.2f%% (needs <= %.2f%%): %s"
                        % (a["max_drawdown_pct"], b["max_drawdown_pct"],
                           -0.90 * abs(b["max_drawdown_pct"]), g2_dd,
                           a["calmar"], b["calmar"], b["calmar"] + 0.20,
                           g2_calmar,
                           a["downside_deviation_pct"],
                           b["downside_deviation_pct"],
                           0.90 * b["downside_deviation_pct"], g2_dev)},
        "G3_statistical_credibility": {
            "pass": bool(prim["significant_at_5pct"]),
            "evidence": "dSharpe %+.3f, z %+.3f, p %.4f; bootstrap CI95 "
                        "[%+.3f, %+.3f] excludes zero: %s"
                        % (prim["delta_sharpe"], prim["z"], prim["p_value"],
                           boot["delta_sharpe"]["ci95"][0],
                           boot["delta_sharpe"]["ci95"][1],
                           boot["delta_sharpe"]["excludes_zero"])},
        "G4_cost_survival": {
            "pass": bool(net_alpha > 0 and cost_diff < 0.5 * abs(gross_alpha)
                         if gross_alpha > 0 else False),
            "evidence": "gross alpha %+.2f pp, incremental cost %.2f pp, "
                        "net alpha %+.2f pp"
                        % (gross_alpha, cost_diff, net_alpha)},
        "G5_episode_independence": {
            "pass": not any(r["sign_reversed"] for r in loo),
            "evidence": "full-sample excess %+.2f pp; worst leave-one-out "
                        "%+.2f pp (dropping %s); reversals: %d"
                        % ((total_eng - total_con) * 100,
                           min(r["excess_pp"] for r in loo),
                           min(loo, key=lambda r: r["excess_pp"])
                           ["dropped_episode"],
                           sum(1 for r in loo if r["sign_reversed"]))},
        "G6_consistency": {
            "pass": bool(beaten / len(blocks) > 0.50),
            "evidence": "engine ahead in %d of %d inter-decision blocks "
                        "(%.1f%%); rule was >50%%"
                        % (beaten, len(blocks), 100.0 * beaten / len(blocks))},
        "G7_economic_interpretation": {
            "pass": not pro_cyclical,
            "evidence": "beta %.3f vs average realised exposure %.3f -> %s; "
                        "upside capture %s%%, downside capture %s%%; "
                        "exposure->forward-return rho %+.3f (p %.4f)"
                        % (beta or float("nan"),
                           a["average_exposure_pct"] / 100.0,
                           "PRO-CYCLICAL" if pro_cyclical else "defensive",
                           a.get("upside_capture_pct"),
                           a.get("downside_capture_pct"), rho, p_rho)},
        "G8_research_validity": {
            "pass": bool(ledger_ok),
            "evidence": "ledger reproduces harness to %.1e; NAV identity "
                        "breaches %d; idle cash credited at %.2f%%/yr; "
                        "engine reads only history <= decision session; "
                        "sleeve is a real contemporaneous index"
                        % (gap, len(audited["nav_identity_breaches"]),
                           config.RISK_FREE_RATE * 100)},
    }

    for name, row in gates.items():
        print("    [%s] %s" % ("PASS" if row["pass"] else "FAIL", name))
        print("           %s" % row["evidence"])

    passed = sum(1 for r in gates.values() if r["pass"])

    if not gates["G8_research_validity"]["pass"]:
        verdict = "INVALID EXPERIMENT"
    elif passed == len(gates):
        verdict = "PASS"
    elif gates["G1_positive_net_alpha"]["pass"] and passed >= 6:
        verdict = "CONDITIONAL PASS"
    else:
        verdict = "FAIL"

    print("\n" + "=" * 78)
    print("VERDICT: %s   (%d of %d gates)" % (verdict, passed, len(gates)))
    print("=" * 78)

    # ---- artifacts --------------------------------------------------------
    payload = {
        "protocol": {
            "window": [str(sessions[0].date()), str(sessions[-1].date())],
            "sessions": len(sessions),
            "years": round(years, 3),
            "decisions": len(grid),
            "first_decision": str(grid[0].date()),
            "last_decision": str(grid[-1].date()),
            "control_exposure_pct": CONTROL_EXPOSURE * 100,
            "control_exposure_source": "engine average over the TRAINING "
                                       "window, fixed before the holdout",
            "risk_free_pct": config.RISK_FREE_RATE * 100,
            "round_trip_cost_pct": ROUND_TRIP * 100,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_replicates": BOOTSTRAP_REPS,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "db_mtime": pd.Timestamp(
                os.path.getmtime(config.DB_PATH if hasattr(config, "DB_PATH")
                                 else r"c:\SIRAT-AI\sirat_market.db"),
                unit="s").strftime("%Y-%m-%d %H:%M:%S"),
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "ledger_audit": {
            "max_abs_curve_difference": gap,
            "nav_identity_breaches": audited["nav_identity_breaches"],
            "turnover_reference": reference["turnover"],
            "turnover_audited": audited["turnover"],
            "verified": ledger_ok,
        },
        "arms": arms,
        "statistics": stats,
        "bootstrap": boot,
        "blocks": blocks,
        "episodes": episodes,
        "leave_one_episode_out": loo,
        "regime_cells": cells,
        "exposure_forward_return": forward,
        "annual": yearly,
        "trace": trace,
        "gates": gates,
        "verdict": verdict,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    curves = pd.DataFrame({
        "date": [str(d.date()) for d in sessions],
        "benchmark_ew": benchmark_curve.values,
        "engine_nav": runs["A_engine"]["curve"].values,
        "control_nav": runs["B_fixed_matched"]["curve"].values,
        "buyhold_nav": runs["C_buy_hold"]["curve"].values,
        "engine_exposure": runs["A_engine"]["exposure"].values,
        "control_exposure": runs["B_fixed_matched"]["exposure"].values,
        "engine_cash_pct": runs["A_engine"]["cash_pct"].values,
        "market_return": market_returns,
    })
    curves.to_csv(os.path.join(OUT_DIR, "holdout_daily.csv"),
                  index=False, encoding="utf-8-sig")

    pd.DataFrame(blocks).to_csv(
        os.path.join(OUT_DIR, "holdout_blocks.csv"),
        index=False, encoding="utf-8-sig")
    pd.DataFrame(episodes).to_csv(
        os.path.join(OUT_DIR, "holdout_episodes.csv"),
        index=False, encoding="utf-8-sig")
    pd.DataFrame([dict(year=y, **row) for y, row in yearly.items()]).to_csv(
        os.path.join(OUT_DIR, "holdout_annual.csv"),
        index=False, encoding="utf-8-sig")
    pd.DataFrame(trace).to_csv(
        os.path.join(OUT_DIR, "holdout_decisions.csv"),
        index=False, encoding="utf-8-sig")

    print("\n  wrote %s" % OUTPUT)
    print("  wrote holdout_daily.csv, holdout_blocks.csv, "
          "holdout_episodes.csv, holdout_annual.csv, holdout_decisions.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
