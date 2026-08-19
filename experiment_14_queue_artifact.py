"""EXPERIMENT 14 — QUEUE-PRESSURE ARTIFACT FALSIFICATION TEST.

    H1          the h=1 Alpha Core signal detects stocks about to enter or
                remain in a price-limit queue (صف خرید), not persistent alpha
    NOT         an attempt to improve, tune or rescue anything. No weight, no
                factor, no threshold and no rebalance frequency is touched
    METHOD      decompose the h=1 top-decile excess return by microstructure
                state, then remove the queue names and re-measure

WHY A LOCKED BAR IS THE RIGHT DETECTOR, AND WHY THE BAND IS NOT ASSUMED
----------------------------------------------------------------------
The Tehran price band moved over the window -- the traded cross-section shows a
99th percentile of +10% in 2017 and +5.5% in 2021 -- so any test keyed to a
fixed ±3% would silently mismeasure most of the sample. It is also unnecessary:

    locked_up  =  high == low  AND  return > +1%  AND  volume > 0

A bar whose high equals its low, at a higher price, with trades in it, is a
stock that opened at the limit and never traded away from it. That is the
signature of صف خرید and it needs no band estimate. The band IS estimated per
date as a cross-check, never as the detector.

THE 33% TRAP. A third of all bars have high == low, but most are stale prices
on suspended names with zero volume and no move. Requiring volume > 0 AND a
positive move separates a queue from a corpse; conflating them would have made
every illiquid name look like a lock.

PRE-REGISTERED BEFORE THE FIRST NUMBER WAS READ
-----------------------------------------------
    queue exposure confirmed   top-decile lock rate >= 2.0x the universe rate
    ARTIFACT                   removing t+1 locks cuts the h=1 top-minus-market
                               excess by >= 50% AND leaves it below the 1.04%
                               round trip
    REAL BUT UNEXECUTABLE      excess survives ex-post removal but < 30% of it
                               is reachable ex-ante
    SURVIVES                   excess survives both, and clears the round trip
"""

import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

import backtest_config
import config
import research_alpha_ic as ric


DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sirat_market.db")
OUT_DIR = backtest_config.OUTPUT_DIR
OUTPUT = os.path.join(OUT_DIR, "experiment14_queue_artifact.json")

WINDOW_START = "2014-09-21"
WINDOW_END = "2024-09-01"

HORIZONS = (1, 5, 10, 20)

ROUND_TRIP = config.TRANSACTION_COST_PCT / 100.0 * 100.0    # in percent

# ---- pre-registered thresholds -------------------------------------------
QUEUE_EXPOSURE_RATIO = 2.0      # top-decile lock rate vs universe
ARTIFACT_COLLAPSE = 0.50        # >= 50% of the excess must vanish
EXECUTABLE_FLOOR = 0.30         # < 30% reachable ex-ante = unexecutable

LOCK_MIN_RETURN = 1.0           # percent; separates a queue from a stale bar

# A genuine صف lock moves by the daily band, and the band never exceeded ~10%
# anywhere in this window. A "locked" bar returning 115% on average -- which is
# what the first pass measured -- is not a queue at all: it is a symbol
# reopening after a suspension, where the only trade of the day happens at a
# gapped price. The two are different phenomena with different tradability, so
# they are separated rather than pooled.
LOCK_MAX_RETURN = 12.0          # percent; above this it is a reopening, not a band

VOLUME_SPIKE = 3.0              # x median volume = "abnormal"


def load_bars():
    con = sqlite3.connect(DB)
    frame = pd.read_sql_query(
        "select ins_code, date, open, high, low, close, volume, value "
        "from price_history where date >= ? and date <= ? "
        "and close is not null",
        con, params=("2014-01-01", "2025-01-01"))
    con.close()

    frame["date"] = pd.to_datetime(frame["date"])
    frame["ins_code"] = frame["ins_code"].astype(str)

    return frame.sort_values(["ins_code", "date"]).reset_index(drop=True)


def annotate(bars):
    """Per-bar microstructure state. Nothing here looks across names."""

    grouped = bars.groupby("ins_code", sort=False)

    bars["prev_close"] = grouped["close"].shift(1)
    bars["ret"] = (bars["close"] / bars["prev_close"] - 1.0) * 100.0

    # Median volume over the trailing 60 sessions of the SAME name, shifted so
    # the reading is available before the bar it describes.
    bars["vol_med60"] = grouped["volume"].transform(
        lambda s: s.shift(1).rolling(60, min_periods=20).median())

    bars["vol_ratio"] = bars["volume"] / bars["vol_med60"]

    traded = bars["volume"] > 0
    no_range = bars["high"] == bars["low"]

    # Did it trade YESTERDAY? A name that did not is reopening, and a reopening
    # gap is a different animal from a band lock.
    traded_prev = grouped["volume"].shift(1) > 0

    bars["stale"] = ~traded
    bars["reopen"] = traded & ~traded_prev
    bars["extreme_gap"] = traded & (bars["ret"].abs() > LOCK_MAX_RETURN)

    bars["locked_up"] = (no_range & traded & traded_prev
                         & (bars["ret"] > LOCK_MIN_RETURN)
                         & (bars["ret"] <= LOCK_MAX_RETURN))
    bars["locked_down"] = (no_range & traded & traded_prev
                           & (bars["ret"] < -LOCK_MIN_RETURN)
                           & (bars["ret"] >= -LOCK_MAX_RETURN))
    bars["closed_at_high"] = ((bars["close"] == bars["high"]) & traded
                              & (bars["ret"] > LOCK_MIN_RETURN)
                              & (bars["ret"] <= LOCK_MAX_RETURN))
    bars["volume_spike"] = bars["vol_ratio"] > VOLUME_SPIKE

    # Anything a T+1 buy order cannot reach at the open: a locked queue, or a
    # symbol that gapped away on reopening.
    bars["unbuyable"] = bars["locked_up"] | bars["reopen"] | bars["extreme_gap"]

    return bars


def band_by_date(bars):
    """Empirical up-band per session, as a cross-check on the detector."""

    # Reopenings and gaps are excluded: a symbol returning to the board after a
    # suspension is not describing the day's band, and a handful of them drags
    # the estimate from ~7% to ~105%.
    traded = bars[(bars["volume"] > 0) & bars["ret"].notna()
                  & ~bars["reopen"].astype(bool)
                  & (bars["ret"].abs() <= LOCK_MAX_RETURN)]

    out = traded.groupby("date")["ret"].quantile(0.995)

    return out.round(2)


def forward(bars, horizons):
    """ONE-SESSION forward return only, and that is deliberate.

    groupby.shift(-h) moves h ROWS, not h sessions. For a name with gaps in
    price_history -- a suspension leaves no row at all -- shift(-20) can reach
    a bar months later, and the resulting "20-session return" of several
    thousand percent destroys any cross-sectional mean it lands in. The first
    pass of this module did exactly that and reported a top-decile excess of
    -579%.

    At h=1 the hazard is small and was validated in E13 (98.31% of rows within
    0.05pp of the panel's own construction). For every longer horizon the
    panel's fwd_* columns are used instead: research_factors.py builds them
    through the PIT calendar, which knows the difference between a missing row
    and the next session.
    """

    grouped = bars.groupby("ins_code", sort=False)["close"]

    bars["fwd_1"] = (grouped.shift(-1) / bars["close"] - 1.0) * 100.0

    return bars


def next_session_state(bars):
    """The state of session t+1, attached to the row for session t."""

    grouped = bars.groupby("ins_code", sort=False)

    for column in ("locked_up", "locked_down", "stale", "closed_at_high",
                   "volume_spike", "reopen", "extreme_gap", "unbuyable"):
        # astype(bool) matters: a shifted boolean column comes back as object,
        # and `~` on object does bitwise negation on ints -- silently turning
        # a mask into garbage instead of raising.
        bars["next_" + column] = (grouped[column].shift(-1)
                                  .fillna(False).astype(bool))

    bars["next_vol_ratio"] = grouped["vol_ratio"].shift(-1)

    return bars


def spearman(x, y):
    if len(x) < 3:
        return np.nan
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def decile_measure(block, fwd):
    """IC and top-decile excess for one date, on whatever rows are passed."""

    good = block[["alpha", fwd]].dropna()
    n = len(good)
    if n < ric.MIN_NAMES_DECILE:
        return None

    x = good["alpha"].to_numpy(float)
    y = good[fwd].to_numpy(float)

    k = max(1, int(round(n / 10.0)))
    order = np.argsort(-x, kind="stable")

    top = y[order[:k]]

    return {"names": n, "ic": spearman(x, y), "top_pct": float(top.mean()),
            "market_pct": float(y.mean()),
            "excess_pct": float(top.mean() - y.mean())}


def summarise(rows, label):
    if not rows:
        return None

    ic = np.array([r["ic"] for r in rows], float)
    ic = ic[~np.isnan(ic)]
    ex = np.array([r["excess_pct"] for r in rows], float)

    sd = float(ic.std(ddof=1)) if len(ic) > 1 else 0.0

    return {
        "label": label,
        "dates": len(rows),
        "mean_ic": round(float(ic.mean()), 5) if len(ic) else None,
        "icir": round(float(ic.mean() / sd), 4) if sd > 0 else None,
        "t_ic": round(float(ic.mean() / sd * math.sqrt(len(ic))), 3)
        if sd > 0 else None,
        "excess_pct": round(float(ex.mean()), 4),
        "excess_t": round(float(ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex)))), 3)
        if len(ex) > 1 and ex.std(ddof=1) > 0 else None,
        "clears_round_trip": bool(ex.mean() > ROUND_TRIP),
    }


def main():

    print("=" * 78)
    print("EXPERIMENT 14 — QUEUE-PRESSURE ARTIFACT FALSIFICATION")
    print("=" * 78)
    print("  falsification only. Alpha Core untouched.")

    # ---- 1. data ---------------------------------------------------------
    print("\n[1] DATA VALIDATION", flush=True)

    bars = annotate(load_bars())
    bars = forward(bars, HORIZONS)
    bars = next_session_state(bars)

    print("    bars                %d, %d instruments"
          % (len(bars), bars["ins_code"].nunique()))

    scope = bars[(bars["date"] >= WINDOW_START) & (bars["date"] <= WINDOW_END)]
    scope = scope[scope["ret"].notna()]

    print("    in window           %d" % len(scope))
    print("    stale (zero volume) %.2f%%" % (scope["stale"].mean() * 100))
    print("    locked UP (band)    %.2f%%" % (scope["locked_up"].mean() * 100))
    print("    locked DOWN (band)  %.2f%%" % (scope["locked_down"].mean() * 100))
    print("    reopening bars      %.2f%%" % (scope["reopen"].mean() * 100))
    print("    extreme gaps >%.0f%%  %.2f%%"
          % (LOCK_MAX_RETURN, scope["extreme_gap"].mean() * 100))
    print("    UNBUYABLE (any)     %.2f%%" % (scope["unbuyable"].mean() * 100))
    print("    closed at high      %.2f%%"
          % (scope["closed_at_high"].mean() * 100))

    band = band_by_date(scope)
    print("    empirical up-band   median %.2f%%, range %.2f%%..%.2f%%"
          % (band.median(), band.min(), band.max()))
    print("    mean |ret| of locked-up bars: %.2f%%"
          % scope[scope["locked_up"]]["ret"].mean())

    # ---- 2. panel + score ------------------------------------------------
    panel = pd.read_csv(os.path.join(OUT_DIR, "factor_panel.csv"),
                        encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ins_code"] = panel["ins_code"].astype(str)

    scored = ric.score_frame(panel)
    scored = scored[(scored["date"] >= WINDOW_START)
                    & (scored["date"] <= WINDOW_END)]

    keep = ["ins_code", "date", "ret", "vol_ratio", "stale", "locked_up",
            "locked_down", "closed_at_high", "volume_spike", "reopen",
            "extreme_gap", "unbuyable",
            "next_locked_up", "next_locked_down", "next_stale",
            "next_closed_at_high", "next_volume_spike", "next_reopen",
            "next_extreme_gap", "next_unbuyable", "next_vol_ratio"]
    keep += ["fwd_1"]           # longer horizons come from the panel

    # The panel keeps fwd_5/10/20 (session-aware, built through the PIT
    # calendar); only fwd_1 comes from the rebuild. No column collides.
    merged = scored.merge(bars[keep], on=["ins_code", "date"], how="left")
    merged = merged[merged["alpha"].notna()]

    print("\n    panel rows scored   %d over %d dates"
          % (len(merged), merged["date"].nunique()))

    # ---- 3. queue exposure ----------------------------------------------
    print("\n[3] QUEUE EXPOSURE — session t+1 state by Alpha Core decile")

    rows = []
    for date, block in merged.groupby("date"):
        good = block[block["alpha"].notna()]
        n = len(good)
        if n < ric.MIN_NAMES_DECILE:
            continue
        k = max(1, int(round(n / 10.0)))
        order = np.argsort(-good["alpha"].to_numpy(float), kind="stable")
        top = good.iloc[order[:k]]
        bottom = good.iloc[order[-k:]]
        rows.append({
            "date": date,
            "top_locked": float(top["next_locked_up"].astype(bool).mean()),
            "bottom_locked": float(bottom["next_locked_up"].astype(bool).mean()),
            "universe_locked": float(good["next_locked_up"].astype(bool).mean()),
            "top_unbuyable": float(top["next_unbuyable"].astype(bool).mean()),
            "universe_unbuyable": float(
                good["next_unbuyable"].astype(bool).mean()),
            "top_stale": float(top["next_stale"].mean()),
            "universe_stale": float(good["next_stale"].mean()),
            "top_at_high": float(top["next_closed_at_high"].mean()),
            "universe_at_high": float(good["next_closed_at_high"].mean()),
        })

    exposure = pd.DataFrame(rows)

    top_lock = exposure["top_locked"].mean() * 100
    uni_lock = exposure["universe_locked"].mean() * 100
    bot_lock = exposure["bottom_locked"].mean() * 100
    ratio = top_lock / uni_lock if uni_lock else float("nan")

    print("    top decile locked up on t+1     %6.2f%%" % top_lock)
    print("    universe locked up on t+1       %6.2f%%" % uni_lock)
    print("    bottom decile locked up on t+1  %6.2f%%" % bot_lock)
    print("    ---> RATIO top/universe          %.2fx  (trigger %.1fx)"
          % (ratio, QUEUE_EXPOSURE_RATIO))
    print("    top decile UNBUYABLE on t+1     %6.2f%%  (universe %.2f%%)"
          % (exposure["top_unbuyable"].mean() * 100,
             exposure["universe_unbuyable"].mean() * 100))
    print("    top decile closed at high       %6.2f%%  (universe %.2f%%)"
          % (exposure["top_at_high"].mean() * 100,
             exposure["universe_at_high"].mean() * 100))
    print("    top decile stale on t+1         %6.2f%%  (universe %.2f%%)"
          % (exposure["top_stale"].mean() * 100,
             exposure["universe_stale"].mean() * 100))

    queue_exposure_confirmed = ratio >= QUEUE_EXPOSURE_RATIO

    # ---- 4. contribution -------------------------------------------------
    print("\n[4] SIGNAL DECOMPOSITION — where the h=1 excess comes from")

    contrib = []
    for date, block in merged.groupby("date"):
        good = block.dropna(subset=["alpha", "fwd_1"])
        n = len(good)
        if n < ric.MIN_NAMES_DECILE:
            continue
        k = max(1, int(round(n / 10.0)))
        order = np.argsort(-good["alpha"].to_numpy(float), kind="stable")
        top = good.iloc[order[:k]]
        market = float(good["fwd_1"].mean())

        mask = top["next_locked_up"].astype(bool)
        locked = top[mask]
        clean = top[~mask]

        unb = top["next_unbuyable"].astype(bool)

        contrib.append({
            "date": date, "k": k, "market": market,
            "top_all": float(top["fwd_1"].mean()),
            "n_locked": len(locked),
            "locked_ret": float(locked["fwd_1"].mean()) if len(locked) else np.nan,
            "clean_ret": float(clean["fwd_1"].mean()) if len(clean) else np.nan,
            "locked_share": len(locked) / float(k),
            "unbuyable_share": float(unb.mean()),
            "buyable_ret": (float(top[~unb]["fwd_1"].mean())
                            if (~unb).any() else np.nan),
        })

    cf = pd.DataFrame(contrib)

    total_excess = float((cf["top_all"] - cf["market"]).mean())
    clean_excess = float((cf["clean_ret"] - cf["market"]).mean())
    locked_ret = float(cf["locked_ret"].mean())
    locked_share = float(cf["locked_share"].mean())

    # Contribution of the locked names to the decile's total excess.
    locked_contrib = float(
        (cf["locked_share"] * (cf["locked_ret"] - cf["market"])).mean())

    print("    top-decile h=1 excess over market      %+7.4f%%" % total_excess)
    print("    share of the decile locked up on t+1    %6.2f%%"
          % (locked_share * 100))
    print("    mean h=1 return of the LOCKED names    %+7.4f%%" % locked_ret)
    print("    mean h=1 return of the REST            %+7.4f%%"
          % float(cf["clean_ret"].mean()))
    print("    excess contributed BY the locked names %+7.4f%%  (%.1f%% of total)"
          % (locked_contrib, 100.0 * locked_contrib / total_excess
             if total_excess else float("nan")))
    print("    excess of the decile EXCLUDING locks   %+7.4f%%" % clean_excess)

    # ---- 5. exclusion tests ---------------------------------------------
    print("\n[5] EXCLUSION TESTS — IC and excess after removing each group")

    adv_cut = merged["adv"].quantile(0.20)

    filters = {
        "baseline": lambda f: f,
        "ex_t1_locks": lambda f: f[~f["next_locked_up"].astype(bool)],
        "ex_t1_unbuyable": lambda f: f[~f["next_unbuyable"].astype(bool)],
        "ex_t_locks": lambda f: f[~f["locked_up"].astype(bool)],
        "ex_illiquid": lambda f: f[f["adv"] > adv_cut],
        "ex_volume_spikes": lambda f: f[~f["next_volume_spike"].astype(bool)],
        "ex_locks_and_illiquid":
            lambda f: f[(~f["next_locked_up"].astype(bool))
                        & (f["adv"] > adv_cut)],
    }

    exclusion = {}

    print("    %-22s %3s %8s %7s %7s %10s %8s"
          % ("filter", "h", "meanIC", "ICIR", "t", "excess%", "clears?"))

    for name, fn in filters.items():
        exclusion[name] = {}
        for h in HORIZONS:
            fwd = "fwd_%d" % h
            rows = []
            for date, block in merged.groupby("date"):
                got = decile_measure(fn(block.dropna(subset=["alpha", fwd])),
                                     fwd)
                if got:
                    rows.append(got)
            got = summarise(rows, "%s/h%d" % (name, h))
            if got:
                exclusion[name]["h%d" % h] = got
                print("    %-22s %3d %8.5f %7.3f %7.2f %10.4f %8s"
                      % (name, h, got["mean_ic"] or 0, got["icir"] or 0,
                         got["t_ic"] or 0, got["excess_pct"],
                         "yes" if got["clears_round_trip"] else "NO"))

    # ---- 6. execution feasibility ---------------------------------------
    print("\n[6] EXECUTION FEASIBILITY")

    base_h1 = exclusion["baseline"]["h1"]["excess_pct"]
    ex_h1 = exclusion["ex_t1_locks"]["h1"]["excess_pct"]

    collapse = 1.0 - (ex_h1 / base_h1) if base_h1 else float("nan")

    # A name locked at the t+1 open cannot be bought there: a T+1 order finds
    # no offer. Its forward return is unreachable, whatever it turns out to be.
    reachable = ex_h1 / base_h1 if base_h1 else float("nan")

    print("    h=1 excess, all names               %+7.4f%%" % base_h1)
    print("    h=1 excess, excluding t+1 locks     %+7.4f%%" % ex_h1)
    print("    collapse when locks removed          %6.1f%%  (trigger %.0f%%)"
          % (collapse * 100, ARTIFACT_COLLAPSE * 100))
    print("    reachable share of the excess        %6.1f%%  (floor %.0f%%)"
          % (reachable * 100, EXECUTABLE_FLOOR * 100))
    print("    round trip                          %7.4f%%" % ROUND_TRIP)
    print("    does the reachable excess clear it?  %s"
          % ("YES" if ex_h1 > ROUND_TRIP else "NO"))

    # ---- 7. verdict ------------------------------------------------------
    artifact = (collapse >= ARTIFACT_COLLAPSE) and (ex_h1 <= ROUND_TRIP)
    survives = (ex_h1 > ROUND_TRIP) and (collapse < ARTIFACT_COLLAPSE)

    if artifact:
        verdict = "SIGNAL IS QUEUE ARTIFACT"
    elif survives:
        verdict = "SIGNAL SURVIVES — NEW RESEARCH PHASE JUSTIFIED"
    else:
        verdict = "SIGNAL IS REAL BUT UNEXECUTABLE"

    print("\n" + "=" * 78)
    print("VERDICT: %s" % verdict)
    print("=" * 78)

    payload = {
        "protocol": {
            "hypothesis": "H1 — the h=1 signal detects queue formation, "
                          "not persistent alpha",
            "window": [WINDOW_START, WINDOW_END],
            "horizons": list(HORIZONS),
            "lock_definition": "high == low AND volume > 0 AND return > %.1f%%"
                               % LOCK_MIN_RETURN,
            "band_not_assumed": True,
            "thresholds": {
                "queue_exposure_ratio": QUEUE_EXPOSURE_RATIO,
                "artifact_collapse": ARTIFACT_COLLAPSE,
                "executable_floor": EXECUTABLE_FLOOR,
                "round_trip_pct": ROUND_TRIP,
            },
            "alpha_core_modified": False,
        },
        "data_validation": {
            "bars": int(len(bars)),
            "in_window": int(len(scope)),
            "stale_pct": round(float(scope["stale"].mean()) * 100, 3),
            "locked_up_pct": round(float(scope["locked_up"].mean()) * 100, 3),
            "locked_down_pct": round(float(scope["locked_down"].mean()) * 100, 3),
            "empirical_band_median_pct": round(float(band.median()), 3),
            "empirical_band_min_pct": round(float(band.min()), 3),
            "empirical_band_max_pct": round(float(band.max()), 3),
        },
        "queue_exposure": {
            "top_decile_locked_pct": round(top_lock, 3),
            "universe_locked_pct": round(uni_lock, 3),
            "bottom_decile_locked_pct": round(bot_lock, 3),
            "ratio": round(ratio, 3),
            "confirmed": bool(queue_exposure_confirmed),
            "top_closed_at_high_pct": round(
                float(exposure["top_at_high"].mean()) * 100, 3),
            "universe_closed_at_high_pct": round(
                float(exposure["universe_at_high"].mean()) * 100, 3),
        },
        "decomposition": {
            "top_decile_excess_pct": round(total_excess, 4),
            "locked_share_of_decile_pct": round(locked_share * 100, 3),
            "locked_names_return_pct": round(locked_ret, 4),
            "excess_from_locked_pct": round(locked_contrib, 4),
            "excess_from_locked_share": round(
                locked_contrib / total_excess, 4) if total_excess else None,
            "excess_excluding_locks_pct": round(clean_excess, 4),
        },
        "exclusion_tests": exclusion,
        "execution_feasibility": {
            "baseline_h1_excess_pct": round(base_h1, 4),
            "ex_locks_h1_excess_pct": round(ex_h1, 4),
            "collapse_fraction": round(collapse, 4),
            "reachable_fraction": round(reachable, 4),
            "round_trip_pct": ROUND_TRIP,
            "reachable_clears_round_trip": bool(ex_h1 > ROUND_TRIP),
        },
        "verdict": verdict,
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__, "pandas": pd.__version__,
            "executed": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

    exposure.to_csv(os.path.join(OUT_DIR, "experiment14_queue_exposure.csv"),
                    index=False, encoding="utf-8-sig")
    cf.to_csv(os.path.join(OUT_DIR, "experiment14_decomposition.csv"),
              index=False, encoding="utf-8-sig")

    print("\n  wrote %s" % OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
