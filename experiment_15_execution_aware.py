"""EXPERIMENT 15 — EXECUTION-AWARE ALPHA VALIDATION.

    QUESTION    can a filter that uses ONLY decision-time information convert
                the Alpha Core signal into a positive net edge after costs?
    NOT         a rescue attempt. Alpha Core is a frozen input; the only new
                component sits after the ranking and never touches the score
    ARMS        A unfiltered · B filtered · C random from the filtered universe

WHY THE ANSWER IS LIKELY NO, WRITTEN BEFORE THE RUN
---------------------------------------------------
Experiment 14's `ex_t1_unbuyable` filter removed names using their t+1 queue
state -- an oracle no live system can have. It left an h=1 excess of 0.397%
against a 1.039% round trip. Since a decision-time filter cannot beat an oracle,
0.397% is the CEILING for this experiment and it already sits below cost. The
preregistration records this so a negative result cannot be reinterpreted
afterwards as a surprise.

THE FILTER IS A FEASIBILITY TEST, NOT A FORECAST
------------------------------------------------
Every rule asks "could this be bought", never "will this go up":

    locked at t          a name at the band today is likely at it tomorrow
    closed at high at t  demand pressed into the close
    volume spike at t    3x its own trailing median
    bottom-quintile ADV  too thin to take a position in
    stale at t           did not trade at all

All five read session t or earlier. None references a future bar, a future
price, a future volume or a future fill.

HOW UNFILLED SLOTS ARE TREATED
------------------------------
A name locked limit-up at the t+1 open cannot be bought: a T+1 order finds no
offer. Those slots are not quietly dropped -- dropping them would let the book
re-form around the eleventh name and flatter the result. They earn the policy
cash rate instead, which is what a real book holding an unfilled order earns.
"""

import hashlib
import json
import math
import os
import sys

import numpy as np
import pandas as pd

import backtest_config
import config
import research_alpha_ic as ric
import experiment_14_queue_artifact as e14


OUT_DIR = backtest_config.OUTPUT_DIR
PREREG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "experiment15_preregistration.json")
OUTPUT = os.path.join(OUT_DIR, "experiment15_execution_aware.json")

WINDOW_START = "2014-09-21"
WINDOW_END = "2024-09-01"

HORIZONS = (1, 5, 10, 20)
BASKET = 10
SEEDS = tuple(range(1, 31))

ROUND_TRIP = config.TRANSACTION_COST_PCT / 100.0 * 100.0     # percent
SESSIONS_PER_YEAR = config.TRADING_DAYS_PER_YEAR

ADV_FLOOR_QUANTILE = 0.20
VOLUME_SPIKE = 3.0

SUBPERIODS = (("2014-2016", "2014-01-01", "2016-12-31"),
              ("2017-2019", "2017-01-01", "2019-12-31"),
              ("2020-2022", "2020-01-01", "2022-12-31"),
              ("2023-2024", "2023-01-01", "2024-12-31"))


def cash_per_period(h):
    """Policy rate over h sessions, compounded."""
    return ((1.0 + config.RISK_FREE_RATE) ** (h / float(SESSIONS_PER_YEAR))
            - 1.0) * 100.0


def build():
    """Panel + microstructure, scored, one row per (name, decision date)."""

    bars = e14.annotate(e14.load_bars())
    bars = e14.forward(bars, (1,))
    bars = e14.next_session_state(bars)

    panel = pd.read_csv(os.path.join(OUT_DIR, "factor_panel.csv"),
                        encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ins_code"] = panel["ins_code"].astype(str)

    scored = ric.score_frame(panel)
    scored = scored[(scored["date"] >= WINDOW_START)
                    & (scored["date"] <= WINDOW_END)]

    keep = ["ins_code", "date", "fwd_1", "locked_up", "closed_at_high",
            "vol_ratio", "stale", "next_locked_up", "next_unbuyable"]

    merged = scored.merge(bars[keep], on=["ins_code", "date"], how="left")

    return merged[merged["alpha"].notna()].copy()


def apply_filter(block):
    """Decision-time feasibility test. Returns (kept, mask).

    Nothing here reads a bar after session t.
    """

    adv_floor = block["adv"].quantile(ADV_FLOOR_QUANTILE)

    blocked = (
        block["locked_up"].astype(bool)
        | block["closed_at_high"].astype(bool)
        | (block["vol_ratio"] > VOLUME_SPIKE).fillna(False)
        | (block["adv"] <= adv_floor)
        | block["stale"].astype(bool)
    )

    return block[~blocked], blocked


def book_return(picks, fwd, h, market):
    """Realised return of a T+1 book, unfilled slots earning cash.

    `picks` is what was REQUESTED. A name unbuyable at the t+1 open is not
    silently replaced -- the slot sits in cash, which is what an unfilled order
    actually earns.
    """

    if picks.empty:
        return np.nan, np.nan, 0.0

    unbuyable = picks["next_unbuyable"].astype(bool)

    filled = picks[~unbuyable]
    n = len(picks)

    cash = cash_per_period(h)

    realised = ((filled[fwd].sum() + unbuyable.sum() * cash) / n
                if n else np.nan)

    requested = float(picks[fwd].mean())

    return requested, float(realised), float((~unbuyable).mean())


def run_arm(frame, horizon, filtered, ranking="alpha", seed=None):
    """One arm at one horizon. Returns per-date rows."""

    fwd = "fwd_%d" % horizon
    rng = None if ranking == "alpha" else np.random.default_rng(seed)

    rows = []
    held = set()

    for date, block in frame.groupby("date"):

        pool = block.dropna(subset=["alpha", fwd])
        if len(pool) < 50:
            continue

        market = float(pool[fwd].mean())

        universe = pool
        if filtered:
            universe, _ = apply_filter(pool)
            if len(universe) < BASKET:
                continue

        if ranking == "alpha":
            order = universe.sort_values("alpha", ascending=False)
        else:
            order = universe.iloc[rng.permutation(len(universe))]

        picks = order.head(BASKET)

        requested, realised, fill = book_return(picks, fwd, horizon, market)

        names = set(picks["ins_code"])
        replaced = 1.0 if not held else len(names - held) / float(len(names))
        held = names

        rows.append({
            "date": date, "market": market,
            "requested": requested, "realised": realised,
            "fill_rate": fill,
            "queue_rate": float(picks["next_locked_up"].astype(bool).mean()),
            "universe_queue_rate": float(
                pool["next_locked_up"].astype(bool).mean()),
            "replaced": replaced,
            "ic": ric_ic(universe, fwd),
        })

    return pd.DataFrame(rows)


def ric_ic(block, fwd):
    good = block[["alpha", fwd]].dropna()
    if len(good) < 30:
        return np.nan
    rx = good["alpha"].rank().to_numpy()
    ry = good[fwd].rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def summarise(rows, horizon, label):
    if rows.empty:
        return None

    periods_per_year = SESSIONS_PER_YEAR / float(horizon)

    gross = (rows["realised"] - rows["market"]).mean()
    cost = rows["replaced"].mean() * ROUND_TRIP
    net = gross - cost

    ic = rows["ic"].dropna()
    sd = float(ic.std(ddof=1)) if len(ic) > 1 else 0.0

    d = rows["realised"] - rows["market"]
    se = d.std(ddof=1) / math.sqrt(len(d)) if len(d) > 1 else np.nan

    return {
        "label": label, "horizon": horizon, "dates": int(len(rows)),
        "mean_ic": round(float(ic.mean()), 5) if len(ic) else None,
        "icir": round(float(ic.mean() / sd), 4) if sd > 0 else None,
        "requested_excess_pct": round(
            float((rows["requested"] - rows["market"]).mean()), 4),
        "realised_excess_pct": round(float(gross), 4),
        "execution_shortfall_pct": round(
            float((rows["requested"] - rows["realised"]).mean()), 4),
        "turnover_per_period": round(float(rows["replaced"].mean()), 4),
        "turnover_per_year": round(
            float(rows["replaced"].mean()) * periods_per_year, 2),
        "cost_per_period_pct": round(float(cost), 4),
        "net_per_period_pct": round(float(net), 4),
        "net_annualised_pct": round(
            ((1 + net / 100.0) ** periods_per_year - 1) * 100, 2),
        "fill_rate_pct": round(float(rows["fill_rate"].mean()) * 100, 2),
        "queue_rate_pct": round(float(rows["queue_rate"].mean()) * 100, 3),
        "universe_queue_rate_pct": round(
            float(rows["universe_queue_rate"].mean()) * 100, 3),
        "queue_ratio": round(
            float(rows["queue_rate"].mean()
                  / rows["universe_queue_rate"].mean()), 3)
        if rows["universe_queue_rate"].mean() else None,
        "excess_t": round(float(d.mean() / se), 3) if se and se > 0 else None,
        "net_positive": bool(net > 0),
    }


def block_bootstrap(a, b, blocks=5000, size=5, seed=20260818):
    """Stationary block bootstrap on the paired per-period difference."""

    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n < 10:
        return None

    rng = np.random.default_rng(seed)
    means = np.empty(blocks)

    for i in range(blocks):
        idx = []
        while len(idx) < n:
            start = rng.integers(0, n)
            idx.extend(range((start), (start + size)))
        idx = [j % n for j in idx[:n]]
        means[i] = d[idx].mean()

    return {
        "mean": round(float(d.mean()), 5),
        "ci95": [round(float(np.percentile(means, 2.5)), 5),
                 round(float(np.percentile(means, 97.5)), 5)],
        "excludes_zero": bool(np.percentile(means, 2.5) > 0
                              or np.percentile(means, 97.5) < 0),
        "resamples": blocks, "block_size": size,
    }


def main():

    with open(PREREG, "rb") as handle:
        raw = handle.read()
    prereg_hash = hashlib.sha256(raw).hexdigest()

    print("=" * 78)
    print("EXPERIMENT 15 — EXECUTION-AWARE ALPHA VALIDATION")
    print("=" * 78)
    print("  preregistration sha256 %s" % prereg_hash[:32])
    print("  Alpha Core FROZEN. Filter is a feasibility test, not a forecast.")

    frame = build()

    print("\n[1] SCOPE")
    print("    rows %d over %d dates" % (len(frame), frame["date"].nunique()))

    # ---- filter effect on the universe -----------------------------------
    kept_share, blocked_reasons = [], []
    for date, block in frame.groupby("date"):
        kept, blocked = apply_filter(block)
        kept_share.append(len(kept) / float(len(block)))
        blocked_reasons.append({
            "locked": float(block["locked_up"].astype(bool).mean()),
            "at_high": float(block["closed_at_high"].astype(bool).mean()),
            "spike": float((block["vol_ratio"] > VOLUME_SPIKE).fillna(False).mean()),
            "thin": float((block["adv"] <= block["adv"].quantile(
                ADV_FLOOR_QUANTILE)).mean()),
            "stale": float(block["stale"].astype(bool).mean()),
        })

    br = pd.DataFrame(blocked_reasons)

    print("\n[2] FILTER EFFECT ON THE UNIVERSE")
    print("    names surviving the filter   %.1f%%" % (np.mean(kept_share)*100))
    for col in br.columns:
        print("    blocked by %-10s        %.1f%%" % (col, br[col].mean()*100))

    # ---- arms ------------------------------------------------------------
    print("\n[3] ARMS", flush=True)

    results = {"A": {}, "B": {}, "C": {}}
    bootstrap = {}
    raw_rows = {}

    for h in HORIZONS:

        a_rows = run_arm(frame, h, filtered=False, ranking="alpha")
        b_rows = run_arm(frame, h, filtered=True, ranking="alpha")

        results["A"]["h%d" % h] = summarise(a_rows, h, "A unfiltered")
        results["B"]["h%d" % h] = summarise(b_rows, h, "B filtered")

        raw_rows["A_h%d" % h] = a_rows
        raw_rows["B_h%d" % h] = b_rows

        pool = []
        for s in SEEDS:
            c_rows = run_arm(frame, h, filtered=True, ranking="random", seed=s)
            got = summarise(c_rows, h, "C random seed %d" % s)
            if got:
                got["seed"] = s
                pool.append(got)
            if s == SEEDS[0]:
                raw_rows["C_h%d" % h] = c_rows

        nets = np.array([p["net_annualised_pct"] for p in pool], float)

        results["C"]["h%d" % h] = {
            "seeds": len(pool),
            "net_annualised_mean": round(float(nets.mean()), 2),
            "net_annualised_p95": round(float(np.percentile(nets, 95)), 2),
            "net_annualised_min": round(float(nets.min()), 2),
            "net_annualised_max": round(float(nets.max()), 2),
            "per_seed": pool,
        }

        # B vs C on the paired per-period excess, first seed's path.
        c0 = raw_rows["C_h%d" % h]
        merged = b_rows.merge(c0, on="date", suffixes=("_b", "_c"))
        bootstrap["h%d" % h] = block_bootstrap(
            merged["realised_b"] - merged["market_b"],
            merged["realised_c"] - merged["market_c"])

    print("\n    %-14s %3s %8s %7s %11s %11s %10s %9s %8s"
          % ("arm", "h", "meanIC", "ICIR", "gross exc%", "net/period%",
             "net/yr%", "fill%", "queue x"))

    for arm in ("A", "B"):
        for h in HORIZONS:
            r = results[arm]["h%d" % h]
            if not r:
                continue
            print("    %-14s %3d %8.5f %7.3f %11.4f %11.4f %10.2f %9.1f %8.2f"
                  % (r["label"], h, r["mean_ic"] or 0, r["icir"] or 0,
                     r["realised_excess_pct"], r["net_per_period_pct"],
                     r["net_annualised_pct"], r["fill_rate_pct"],
                     r["queue_ratio"] or 0))

    for h in HORIZONS:
        c = results["C"]["h%d" % h]
        print("    %-14s %3d %8s %7s %11s %11s %10.2f %9s %8s"
              % ("C random mean", h, "-", "-", "-", "-",
                 c["net_annualised_mean"], "-", "-"))

    # ---- gates -----------------------------------------------------------
    print("\n[4] GATES")

    gates = {}

    a_ratio = results["A"]["h1"]["queue_ratio"]
    b_ratio = results["B"]["h1"]["queue_ratio"]
    gates["G1_artifact_removal"] = {
        "arm_a_queue_ratio": a_ratio, "arm_b_queue_ratio": b_ratio,
        "threshold": 1.5, "pass": bool(b_ratio <= 1.5)}

    g2 = any(results["B"]["h%d" % h]["net_annualised_pct"]
             > results["A"]["h%d" % h]["net_annualised_pct"] for h in HORIZONS)
    gates["G2_net_alpha"] = {
        "b_beats_a_at_any_horizon": bool(g2),
        "by_horizon": {("h%d" % h): {
            "A": results["A"]["h%d" % h]["net_annualised_pct"],
            "B": results["B"]["h%d" % h]["net_annualised_pct"]}
            for h in HORIZONS},
        "pass": bool(g2)}

    g3 = any(results["B"]["h%d" % h]["net_annualised_pct"]
             > results["C"]["h%d" % h]["net_annualised_p95"] for h in HORIZONS)
    gates["G3_random_separation"] = {
        "by_horizon": {("h%d" % h): {
            "B": results["B"]["h%d" % h]["net_annualised_pct"],
            "C_p95": results["C"]["h%d" % h]["net_annualised_p95"]}
            for h in HORIZONS},
        "pass": bool(g3)}

    g4 = any(results["B"]["h%d" % h]["net_per_period_pct"] > 0
             for h in HORIZONS)
    gates["G4_cost_survival"] = {
        "net_per_period_by_horizon": {
            ("h%d" % h): results["B"]["h%d" % h]["net_per_period_pct"]
            for h in HORIZONS},
        "pass": bool(g4)}

    g5 = any((bootstrap["h%d" % h] or {}).get("excludes_zero")
             for h in HORIZONS)
    gates["G5_statistical"] = {"bootstrap": bootstrap, "pass": bool(g5)}

    passing = sum(1 for h in HORIZONS
                  if results["B"]["h%d" % h]["net_per_period_pct"] > 0
                  and results["B"]["h%d" % h]["net_annualised_pct"]
                  > results["A"]["h%d" % h]["net_annualised_pct"])
    gates["G6_robustness"] = {
        "horizons_passing_g2_and_g4": passing,
        "required": 2, "pass": bool(passing >= 2)}

    for name, g in gates.items():
        print("    [%s] %s" % ("PASS" if g["pass"] else "FAIL", name))

    verdict = "PASS" if all(g["pass"] for g in gates.values()) else "FAIL"

    print("\n" + "=" * 78)
    print("VERDICT: %s" % verdict)
    print("=" * 78)

    payload = {
        "preregistration_sha256": prereg_hash,
        "protocol": {
            "window": [WINDOW_START, WINDOW_END],
            "horizons": list(HORIZONS), "basket": BASKET,
            "seeds": list(SEEDS),
            "round_trip_pct": ROUND_TRIP,
            "alpha_core_modified": False,
            "filter_uses_future_data": False,
            "oracle_bound_from_e14_pct": 0.3970,
        },
        "filter_effect": {
            "universe_surviving_pct": round(float(np.mean(kept_share))*100, 2),
            "blocked_by": {c: round(float(br[c].mean())*100, 2)
                           for c in br.columns},
        },
        "arms": results,
        "bootstrap": bootstrap,
        "gates": gates,
        "verdict": verdict,
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__, "pandas": pd.__version__,
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
