"""E20: where, if anywhere, is Alpha Core's information actually tradable?

REGISTERED BEFORE THIS RAN
--------------------------
`backtest_output/E20_preregistration.json`, hash 1f9d15b0..., frozen in the
research database before a single number below was computed.

THREE LAYERS, NEVER COLLAPSED INTO ONE NUMBER
-----------------------------------------------
    L1  INFORMATION    is there predictive information anywhere?
    L2  TRADABILITY    is it still there once names that cannot absorb the
                       strategy are removed?
    L3  MONETIZATION   does what remains survive execution, turnover and cost?

E19 established that the whole thing fails. It did not separate WHY, and the
three answers call for completely different responses: nothing to find, versus
something real that cannot be reached, versus something reachable that costs
more than it returns.

WHY ABSOLUTE PARTICIPATION AND NOT QUANTILES
----------------------------------------------
E19 cut liquidity by within-date quintile. That answers "which fifth of today's
market is most liquid", which in a thin market can still be untradable. E20
asks the question that decides whether an order can be placed: what fraction of
a name's average traded value would this position be? The caps are absolute,
they come from `config.MAX_ADV_PARTICIPATION_PCT` (5%) with one step either
side, and they were frozen before any of this ran.

WHAT E20 STRUCTURALLY CANNOT DO
--------------------------------
E19 spent the clean holdout. `windows_touched` will refuse a second evaluation
and it is right to. So E20 makes NO independent out-of-sample claim: it can
establish a negative conclusion, and a positive one found here would be an
in-sample finding needing a fresh experiment on untouched data. That asymmetry
is declared in the preregistration, not discovered here.

Alpha Core is not touched.
"""

import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np                                                  # noqa: E402
import pandas as pd                                                 # noqa: E402

import config                                                       # noqa: E402
from examples.edge_audit import (build_audit_panel, build_book,     # noqa: E402
                                 ic_series, panel_hash, spearman,
                                 summarise_book, summarise_ic)

OUT = os.path.join(ROOT, "backtest_output", "E20_capacity_audit.json")

SEED = 20260820

HORIZONS = (5, 10, 20, 60)          # the platform's existing set

PRIMARY_HORIZON = 20

BASKET = 10

MIN_NAMES = 50

ACCOUNT_EQUITY = config.ACCOUNT_EQUITY

POSITION_VALUE = ACCOUNT_EQUITY / float(BASKET)

ROUND_TRIP = config.TRANSACTION_COST_PCT

SESSIONS_PER_YEAR = config.TRADING_DAYS_PER_YEAR

MEASURED_TURNOVER_CAPACITY = 4.45

NULL_ARMS = 30

CAPACITY_LEVELS = {
    "T1_broad": 10.0,
    "T2_realistic": 5.0,          # config.MAX_ADV_PARTICIPATION_PCT
    "T3_conservative": 1.0,
}

CURVE_LEVELS = (50.0, 25.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.25)

SPLITS = {
    "in_sample": ("2014-09-21", "2021-09-01"),
    "validation": ("2021-09-01", "2023-03-01"),
    "reused_window": ("2023-03-01", "2024-09-01"),
}


def say(message=""):
    print(message, flush=True)


def rule(title):
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


# ==============================================================
# CAPACITY
# ==============================================================

E20_PANEL = os.path.join(ROOT, "backtest_output", "E20_audit_panel.csv.gz")


def build_e20_panel(verbose=True):
    """E19's audit panel plus fwd_60, written to a SEPARATE file.

    E19's panel is evidence and is read-only here. The platform's existing
    horizon set includes h=60 and E19 only needed up to h=20, so E20 needs one
    additional forward-return column -- and adding it to E19's file would
    change the hash of a frozen artifact that E19's report cites. A new file
    with its own hash is the only correct move: E20 may extend the evidence
    base, never rewrite it.
    """

    if os.path.exists(E20_PANEL):
        frame = pd.read_csv(E20_PANEL, compression="gzip")
        frame["date"] = pd.to_datetime(frame["date"])
        frame["ins_code"] = frame["ins_code"].astype(str)
        for column in ("next_locked_up", "next_unbuyable"):
            frame[column] = frame[column].astype(bool)
        if verbose:
            say("  E20 panel loaded: %d rows" % len(frame))
        return frame

    import backtest_config

    base = build_audit_panel(verbose=False)

    extra = pd.read_csv(
        os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv"),
        encoding="utf-8-sig", usecols=["date", "ins_code", "fwd_60"])

    extra["date"] = pd.to_datetime(extra["date"])
    extra["ins_code"] = extra["ins_code"].astype(str)

    frame = base.merge(extra, on=["date", "ins_code"], how="left")

    frame.to_csv(E20_PANEL, index=False, compression="gzip",
                 date_format="%Y-%m-%d", float_format="%.6f")

    if verbose:
        say("  E20 panel built: %d rows (E19 panel + fwd_60)" % len(frame))

    return frame


def add_participation(panel):
    """Required participation for one position, from decision-time ADV only.

    `adv` is the trailing 20-session mean traded value as of the decision date.
    Nothing here reads a bar after t.
    """

    frame = panel.copy()

    frame["participation_pct"] = np.where(
        frame["adv"] > 0, 100.0 * POSITION_VALUE / frame["adv"], np.inf)

    return frame


def eligible_at(panel, max_participation_pct):
    """The universe that can absorb the strategy at this cap."""

    return panel[panel["participation_pct"] <= max_participation_pct]


def capacity_profile(panel, level_pct):
    """Everything the mission asks to be reported per capacity level."""

    subset = eligible_at(panel, level_pct)

    if subset.empty:
        return {"max_participation_pct": level_pct, "rows": 0,
                "dates": 0, "eligible": False}

    per_date = subset.groupby("date")["ins_code"].nunique()

    usable = per_date[per_date >= MIN_NAMES]

    return {
        "max_participation_pct": level_pct,
        "implied_min_adv": POSITION_VALUE / (level_pct / 100.0),
        "rows": int(len(subset)),
        "dates_with_any_name": int(len(per_date)),
        "dates_with_enough_names": int(len(usable)),
        "names_per_date_median": float(per_date.median()),
        "names_per_date_min": int(per_date.min()),
        "share_of_panel_pct": round(100.0 * len(subset) / len(panel), 2),
        "median_adv": float(subset["adv"].median()),
        "median_participation_pct": float(subset["participation_pct"].median()),
        "eligible": bool(len(usable) >= 10),
    }


def phase2_capacity_map(panel):
    rule("PHASE 2 -- LIQUIDITY / CAPACITY MAP")

    say("  account equity   %s" % format(ACCOUNT_EQUITY, ",.0f"))
    say("  basket           %d" % BASKET)
    say("  position value   %s" % format(POSITION_VALUE, ",.0f"))
    say("  participation    position_value / adv, decision-time only")
    say()

    result = {"account_equity": ACCOUNT_EQUITY,
              "position_value": POSITION_VALUE,
              "levels": {}, "curve": {}}

    say("  %-18s %-8s %-16s %-10s %-10s %s"
        % ("level", "part.%", "min ADV", "rows", "% panel", "names/date med"))
    say("  " + "-" * 84)

    for name, level in CAPACITY_LEVELS.items():
        profile = capacity_profile(panel, level)
        result["levels"][name] = profile
        say("  %-18s %-8s %-16s %-10s %-10s %s"
            % (name, level, format(profile["implied_min_adv"], ",.0f"),
               profile["rows"], profile["share_of_panel_pct"],
               profile["names_per_date_median"]))

    say()
    say("  CAPACITY CURVE")
    say("  %-10s %-16s %-10s %-10s %s"
        % ("part.%", "min ADV", "rows", "% panel", "dates >= 50 names"))
    say("  " + "-" * 70)

    for level in CURVE_LEVELS:
        profile = capacity_profile(panel, level)
        result["curve"][str(level)] = profile
        say("  %-10s %-16s %-10s %-10s %s"
            % (level, format(profile["implied_min_adv"], ",.0f"),
               profile["rows"], profile["share_of_panel_pct"],
               profile["dates_with_enough_names"]))

    return result


# ==============================================================
# L1 / L2 -- INFORMATION AND TRADABILITY
# ==============================================================

def ic_block(panel, horizon, label, min_names=MIN_NAMES):
    frame = ic_series(panel, horizon=horizon, min_names=min_names)

    stats = summarise_ic(frame, label)

    if stats["dates"] > 1:
        ic = frame["ic"].to_numpy(float)
        se = ic.std(ddof=1) / math.sqrt(len(ic))
        stats["ci95"] = [round(float(ic.mean() - 1.96 * se), 5),
                         round(float(ic.mean() + 1.96 * se), 5)]
    else:
        stats["ci95"] = None

    return stats


def phase3_information(panel):
    rule("PHASE 3 -- L1 INFORMATION (before any capacity constraint)")

    result = {"full_cross_section": {}}

    say("  Full eligible cross-section, no capacity filter")
    say("  %-8s %-10s %-10s %-9s %-9s %-8s %s"
        % ("h", "mean IC", "ICIR", "t", "%pos", "dates", "95% CI"))
    say("  " + "-" * 78)

    for horizon in HORIZONS:
        stats = ic_block(panel, horizon, "h=%d" % horizon)
        result["full_cross_section"][str(horizon)] = stats
        say("  %-8s %-10s %-10s %-9s %-9s %-8s %s"
            % (horizon, stats["mean_ic"], stats["icir"], stats["t_stat"],
               stats["pct_positive"], stats["dates"], stats["ci95"]))

    primary = result["full_cross_section"][str(PRIMARY_HORIZON)]

    result["L1_verdict"] = {
        "declared_horizon": PRIMARY_HORIZON,
        "mean_ic": primary["mean_ic"],
        "t_stat": primary["t_stat"],
        "threshold_abs_t": 2.0,
        "passed": bool(abs(primary["t_stat"] or 0) >= 2.0),
    }

    say()
    say("  L1 at the declared h=%d: t = %s against a required |t| >= 2.0 -> %s"
        % (PRIMARY_HORIZON, primary["t_stat"],
           "PASS" if result["L1_verdict"]["passed"] else "FAIL"))

    return result


def phase4_tradability(panel):
    rule("PHASE 4 -- L2 TRADABILITY (capacity constraints imposed)")

    result = {"levels": {}, "curve": {}}

    say("  IC WITHIN THE TRADABLE UNIVERSE, by capacity level and horizon")
    say()
    say("  %-18s %-6s %-10s %-10s %-9s %-9s %s"
        % ("level", "h", "mean IC", "ICIR", "t", "%pos", "dates"))
    say("  " + "-" * 78)

    for name, level in CAPACITY_LEVELS.items():
        subset = eligible_at(panel, level)
        entry = {}
        for horizon in HORIZONS:
            stats = ic_block(subset, horizon, "%s h=%d" % (name, horizon))
            entry[str(horizon)] = stats
            say("  %-18s %-6s %-10s %-10s %-9s %-9s %s"
                % (name if horizon == HORIZONS[0] else "", horizon,
                   stats["mean_ic"], stats["icir"], stats["t_stat"],
                   stats["pct_positive"], stats["dates"]))
        result["levels"][name] = entry
        say("  " + "-" * 78)

    # ---- the curve, at the declared horizon only ------------------------
    say()
    say("  CAPACITY CURVE at the declared h=%d" % PRIMARY_HORIZON)
    say("  %-10s %-10s %-10s %-9s %-9s %s"
        % ("part.%", "mean IC", "ICIR", "t", "%pos", "dates"))
    say("  " + "-" * 66)

    for level in CURVE_LEVELS:
        subset = eligible_at(panel, level)
        stats = ic_block(subset, PRIMARY_HORIZON, "part<=%s" % level)
        result["curve"][str(level)] = stats
        say("  %-10s %-10s %-10s %-9s %-9s %s"
            % (level, stats["mean_ic"], stats["icir"], stats["t_stat"],
               stats["pct_positive"], stats["dates"]))

    # ---- the complement: the names capacity EXCLUDES ---------------------
    say()
    say("  THE COMPLEMENT -- names EXCLUDED at T2 (participation > 5%)")

    excluded = panel[panel["participation_pct"] > CAPACITY_LEVELS["T2_realistic"]]

    result["excluded_at_T2"] = ic_block(excluded, PRIMARY_HORIZON,
                                        "excluded at T2")

    say("    rows %d, mean IC %s, t %s"
        % (len(excluded), result["excluded_at_T2"]["mean_ic"],
           result["excluded_at_T2"]["t_stat"]))
    say()
    say("    This is the number that decides the case. If the information is")
    say("    here and not above, Alpha Core is a research signal rather than a")
    say("    trading signal.")

    t2 = result["levels"]["T2_realistic"][str(PRIMARY_HORIZON)]

    result["L2_verdict"] = {
        "level": "T2_realistic",
        "mean_ic": t2["mean_ic"],
        "icir": t2["icir"],
        "t_stat": t2["t_stat"],
        "passed": bool((t2["mean_ic"] or -1) >= 0.02
                       and (t2["icir"] or -1) >= 0.30
                       and abs(t2["t_stat"] or 0) >= 2.0),
    }

    say()
    say("  L2 at T2, h=%d: IC %s, ICIR %s, t %s -> %s"
        % (PRIMARY_HORIZON, t2["mean_ic"], t2["icir"], t2["t_stat"],
           "PASS" if result["L2_verdict"]["passed"] else "FAIL"))

    return result


# ==============================================================
# L3 -- MONETIZATION
# ==============================================================

def phase7_execution(panel):
    rule("PHASE 7 -- L3 MONETIZATION (execution, turnover, cost)")

    result = {"levels": {}, "horizons": {}}

    say("  %-18s %-6s %-11s %-11s %-10s %-10s %-9s %s"
        % ("level", "h", "requested", "realised", "turnover/y", "cost", "net",
           "positive"))
    say("  " + "-" * 92)

    for name, level in CAPACITY_LEVELS.items():
        subset = eligible_at(panel, level)
        entry = {}
        for horizon in HORIZONS:
            book = build_book(subset, horizon=horizon, basket=BASKET,
                              execute=True)
            stats = summarise_book(book, horizon=horizon,
                                   label="%s h=%d" % (name, horizon))
            entry[str(horizon)] = stats
            if stats.get("periods"):
                say("  %-18s %-6s %-11s %-11s %-10s %-10s %-9s %s"
                    % (name if horizon == HORIZONS[0] else "", horizon,
                       stats["requested_excess_pct"],
                       stats["gross_excess_pct"], stats["turnover_per_year"],
                       stats["cost_per_period_pct"],
                       stats["net_per_period_pct"], stats["net_positive"]))
        result["levels"][name] = entry
        say("  " + "-" * 92)

    t2 = result["levels"]["T2_realistic"][str(PRIMARY_HORIZON)]

    within_capacity = bool((t2.get("turnover_per_year") or 1e9)
                           <= MEASURED_TURNOVER_CAPACITY)

    result["L3_verdict"] = {
        "level": "T2_realistic",
        "horizon": PRIMARY_HORIZON,
        "net_per_period_pct": t2.get("net_per_period_pct"),
        "turnover_per_year": t2.get("turnover_per_year"),
        "measured_capacity": MEASURED_TURNOVER_CAPACITY,
        "within_capacity": within_capacity,
        "max_drawdown_pct": t2.get("max_drawdown_pct"),
        "net_positive": bool(t2.get("net_positive")),
    }

    say()
    say("  L3 at T2, h=%d: net %s%%/period, turnover %s/yr against a measured"
        % (PRIMARY_HORIZON, t2.get("net_per_period_pct"),
           t2.get("turnover_per_year")))
    say("     capacity of %s/yr -> %s"
        % (MEASURED_TURNOVER_CAPACITY,
           "within capacity" if within_capacity else "EXCEEDS CAPACITY"))

    return result


# ==============================================================
# PHASE 8 -- NULLS
# ==============================================================

def add_buckets_for_nulls(panel):
    frame = panel.copy()

    frame["liq_bucket"] = frame.groupby("date")["adv"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False,
                          duplicates="drop")
        if s.notna().sum() >= 25 else -1)

    frame["vol_bucket"] = frame.groupby("date")["volatility_60"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False,
                          duplicates="drop")
        if s.notna().sum() >= 25 else -1)

    frame["liqvol"] = (frame["liq_bucket"].astype(str) + "|"
                       + frame["vol_bucket"].astype(str))

    return frame


def matched_null(column, rng):
    def ranker(pool, date):
        alpha_picks = pool.sort_values("alpha", ascending=False).head(BASKET)
        if column is None:
            return pool.iloc[rng.permutation(len(pool))].head(BASKET)
        chosen, used = [], set()
        for stratum in alpha_picks[column]:
            candidates = pool[(pool[column] == stratum)
                              & (~pool.index.isin(used))]
            if candidates.empty:
                candidates = pool[~pool.index.isin(used)]
            if candidates.empty:
                break
            pick = candidates.iloc[int(rng.integers(0, len(candidates)))]
            used.add(pick.name)
            chosen.append(pick)
        return pd.DataFrame(chosen) if len(chosen) == BASKET else None
    return ranker


def phase8_nulls(panel):
    rule("PHASE 8 -- MATCHED NULLS AT T2 (the tradable universe)")

    subset = add_buckets_for_nulls(
        eligible_at(panel, CAPACITY_LEVELS["T2_realistic"]))

    treatment_book = build_book(subset, horizon=PRIMARY_HORIZON,
                                basket=BASKET, execute=True)

    if treatment_book.empty:
        return {"available": False,
                "reason": "no date carried a full basket at T2"}

    treatment = float((treatment_book["realised_pct"]
                       - treatment_book["market_pct"]).mean())

    definitions = [
        ("random_ranking", None),
        ("liquidity_matched", "liq_bucket"),
        ("volatility_matched", "vol_bucket"),
        ("liquidity_and_volatility_matched", "liqvol"),
    ]

    nulls = {}

    for name, column in definitions:
        arms = []
        for arm in range(NULL_ARMS):
            rng = np.random.default_rng(SEED + arm)
            book = build_book(subset, horizon=PRIMARY_HORIZON, basket=BASKET,
                              ranker=matched_null(column, rng), execute=True)
            if not book.empty:
                arms.append(float((book["realised_pct"]
                                   - book["market_pct"]).mean()))

        arms = np.asarray(arms, float)

        if not len(arms):
            continue

        nulls[name] = {
            "arms": int(len(arms)),
            "null_mean_excess_pct": round(float(arms.mean()), 5),
            "null_p95": round(float(np.percentile(arms, 95)), 5),
            "treatment_excess_pct": round(treatment, 5),
            "percentile": round(float((arms < treatment).mean() * 100), 2),
            "empirical_p": round(float((arms >= treatment).mean()), 4),
            "beats_p95": bool(treatment > np.percentile(arms, 95)),
        }

    # Exposure-matched: the market itself, held at the same exposure. The
    # benchmark is the cross-sectional mean, so its excess is zero by
    # construction and the comparison is just the sign of the treatment.
    nulls["exposure_matched_market"] = {
        "arms": 1, "null_mean_excess_pct": 0.0,
        "treatment_excess_pct": round(treatment, 5),
        "percentile": 100.0 if treatment > 0 else 0.0,
        "beats_p95": bool(treatment > 0),
        "note": "the benchmark IS the cross-sectional mean of the tradable "
                "universe, so a matched-exposure null has zero excess by "
                "construction",
    }

    say("  Treatment at T2: %+.5f%% mean excess per period" % treatment)
    say()
    say("  %-34s %-7s %-12s %-12s %-11s %s"
        % ("null", "arms", "null mean", "null p95", "percentile", "beats p95"))
    say("  " + "-" * 92)

    for name, entry in nulls.items():
        say("  %-34s %-7s %-12s %-12s %-11s %s"
            % (name, entry["arms"], entry["null_mean_excess_pct"],
               entry.get("null_p95", "-"), entry["percentile"],
               entry["beats_p95"]))

    primary = nulls.get("liquidity_matched", {})

    say()
    say("  PRIMARY NULL is liquidity-matched (preregistered): percentile %s"
        % primary.get("percentile"))

    return {"available": True, "treatment_excess_pct": round(treatment, 5),
            "nulls": nulls, "primary_null": "liquidity_matched",
            "primary_percentile": primary.get("percentile")}


# ==============================================================
# PHASE 9/10 -- SPLITS AND WALK-FORWARD AT T2
# ==============================================================

def phase9_splits(panel):
    rule("PHASE 9 -- SPLITS AT T2 (the third window is RE-USED, not a holdout)")

    subset = eligible_at(panel, CAPACITY_LEVELS["T2_realistic"])

    segments = {}

    say("  %-16s %-24s %-8s %-10s %-9s %s"
        % ("segment", "window", "dates", "IC", "t", "net %"))
    say("  " + "-" * 82)

    for name, (start, end) in SPLITS.items():
        block = subset[(subset["date"] >= start) & (subset["date"] < end)]
        stats = ic_block(block, PRIMARY_HORIZON, name)
        book = build_book(block, horizon=PRIMARY_HORIZON, basket=BASKET,
                          execute=True)
        economics = summarise_book(book, horizon=PRIMARY_HORIZON, label=name)
        segments[name] = {"window": [start, end], "ic": stats,
                          "book": economics}
        say("  %-16s %-24s %-8s %-10s %-9s %s"
            % (name, "%s..%s" % (start, end), stats["dates"],
               stats["mean_ic"], stats["t_stat"],
               economics.get("net_per_period_pct")))

    say()
    say("  The third window was spent by E19. It is reported as `reused_window`")
    say("  and E20 makes NO independent out-of-sample claim on it.")

    return {"segments": segments,
            "independent_oos_available": False,
            "reason": "E19 spent 2023-03-01..2024-09-01; windows_touched "
                      "records it and refuses a second evaluation"}


def phase10_walk_forward(panel):
    rule("PHASE 10 -- WALK-FORWARD AT T2")

    subset = eligible_at(panel, CAPACITY_LEVELS["T2_realistic"])

    frame = ic_series(subset, horizon=PRIMARY_HORIZON).sort_values("date")
    frame = frame.reset_index(drop=True)

    train, test = 20, 10

    folds = []
    start = 0

    say("  %-4s %-12s %-12s %-9s %-9s %-10s %-10s %s"
        % ("#", "test start", "test end", "train IC", "test IC", "names/date",
           "turnover/y", "net %"))
    say("  " + "-" * 92)

    while start + train + test <= len(frame):
        train_block = frame.iloc[start:start + train]
        test_block = frame.iloc[start + train:start + train + test]

        dates = set(test_block["date"])
        block = subset[subset["date"].isin(dates)]

        book = build_book(block, horizon=PRIMARY_HORIZON, basket=BASKET,
                          execute=True)
        economics = summarise_book(book, horizon=PRIMARY_HORIZON, label="fold")

        fold = {
            "fold": len(folds) + 1,
            "test_start": str(test_block["date"].iloc[0].date()),
            "test_end": str(test_block["date"].iloc[-1].date()),
            "train_ic": round(float(train_block["ic"].mean()), 5),
            "test_ic": round(float(test_block["ic"].mean()), 5),
            "names_per_date": round(float(test_block["names"].mean()), 1),
            "turnover_per_year": economics.get("turnover_per_year"),
            "net_per_period_pct": economics.get("net_per_period_pct"),
        }

        folds.append(fold)

        say("  %-4s %-12s %-12s %-9s %-9s %-10s %-10s %s"
            % (fold["fold"], fold["test_start"], fold["test_end"],
               fold["train_ic"], fold["test_ic"], fold["names_per_date"],
               fold["turnover_per_year"], fold["net_per_period_pct"]))

        start += test

    positive = [f for f in folds if f["test_ic"] > 0]

    share = (len(positive) / float(len(folds))) if folds else None

    say()
    say("  positive out-of-fold: %d of %d (%s)"
        % (len(positive), len(folds),
           "%.0f%%" % (share * 100) if share is not None else "n/a"))

    return {"folds": folds, "positive_folds": len(positive),
            "total_folds": len(folds),
            "positive_fold_share": round(share, 4) if share is not None else None}


# ==============================================================
# PHASE 11 -- ECONOMIC DECOMPOSITION
# ==============================================================

def phase11_decomposition(panel, tradability, execution):
    rule("PHASE 11 -- ECONOMIC DECOMPOSITION")

    full = build_book(panel, horizon=PRIMARY_HORIZON, basket=BASKET,
                      execute=False)
    full_stats = summarise_book(full, horizon=PRIMARY_HORIZON, label="full")

    t2_panel = eligible_at(panel, CAPACITY_LEVELS["T2_realistic"])

    t2_frictionless = summarise_book(
        build_book(t2_panel, horizon=PRIMARY_HORIZON, basket=BASKET,
                   execute=False), horizon=PRIMARY_HORIZON, label="T2 fric")

    t2_executed = summarise_book(
        build_book(t2_panel, horizon=PRIMARY_HORIZON, basket=BASKET,
                   execute=True), horizon=PRIMARY_HORIZON, label="T2 exec")

    steps = [
        ("1. gross alpha, full universe, frictionless",
         full_stats["gross_excess_pct"]),
        ("2. after restricting to tradable names (T2)",
         t2_frictionless["gross_excess_pct"]),
        ("3. after T+1 execution and unfilled slots",
         t2_executed["gross_excess_pct"]),
        ("4. after turnover x round trip",
         t2_executed["net_per_period_pct"]),
        ("5. after 0.10%/leg slippage",
         summarise_book(build_book(t2_panel, horizon=PRIMARY_HORIZON,
                                   basket=BASKET, execute=True),
                        horizon=PRIMARY_HORIZON, slippage=0.10,
                        label="T2 slip")["net_per_period_pct"]),
    ]

    say("  %-46s %12s %12s" % ("step", "excess %", "delta"))
    say("  " + "-" * 72)

    previous = None
    for label, value in steps:
        delta = "" if previous is None else "%+.4f" % (value - previous)
        say("  %-46s %12.4f %12s" % (label, value, delta))
        previous = value

    liquidity_effect = (t2_frictionless["gross_excess_pct"]
                        - full_stats["gross_excess_pct"])

    execution_effect = (t2_executed["gross_excess_pct"]
                        - t2_frictionless["gross_excess_pct"])

    result = {
        "signal_information_full_pct": full_stats["gross_excess_pct"],
        "liquidity_effect_pp": round(liquidity_effect, 4),
        "selection_effect_note":
            "selection and signal are the same operation here -- the score IS "
            "the selection rule, so they cannot be separated without changing "
            "Alpha Core, which is forbidden",
        "exposure_effect_note":
            "not separable on a panel: the benchmark is the cross-sectional "
            "mean of the SAME eligible universe, so market direction is "
            "differenced out by construction",
        "execution_loss_pp": round(execution_effect, 4),
        "turnover_per_year": t2_executed["turnover_per_year"],
        "transaction_cost_pp": round(-t2_executed["cost_per_period_pct"], 4),
        "final_net_pct": t2_executed["net_per_period_pct"],
        "steps": [{"step": label, "excess_pct": round(value, 4)}
                  for label, value in steps],
    }

    say()
    say("  WHERE THE APPARENT ALPHA GOES")
    say("    signal, full universe        %+.4f%%"
        % result["signal_information_full_pct"])
    say("    liquidity restriction        %+.4f pp" % result["liquidity_effect_pp"])
    say("    execution                    %+.4f pp" % result["execution_loss_pp"])
    say("    transaction cost             %+.4f pp" % result["transaction_cost_pp"])
    say("    FINAL NET                    %+.4f%%" % result["final_net_pct"])

    return result


# ==============================================================

def main():
    say("=" * 78)
    say("E20 -- ALPHA CORE TRADABLE INFORMATION / CAPACITY TEST")
    say("=" * 78)
    say("  preregistration  1f9d15b0... (frozen before this ran)")
    say("  declared horizon h=%d" % PRIMARY_HORIZON)
    say("  horizons         %s  (the platform's existing set)" % (HORIZONS,))
    say("  primary null     liquidity-matched")
    say("  seed             %d" % SEED)
    say("  control arm      E19-alpha-core-tradable-edge")
    say()
    say("  E20 makes NO independent out-of-sample claim: E19 spent the clean")
    say("  holdout, and the platform refuses a second evaluation.")

    panel = add_participation(build_e20_panel())

    say()
    say("  panel  %d rows, %d dates, hash %s"
        % (len(panel), panel["date"].nunique(), panel_hash(panel)[:24]))

    report = {
        "experiment_id": "E20-alpha-core-capacity",
        "preregistration_hash":
            "1f9d15b09459975593e525db2e86122a2487df6cbd964a1a60a2cce8c9f03878",
        "control_arm": "E19-alpha-core-tradable-edge",
        "declared_horizon": PRIMARY_HORIZON,
        "horizons": list(HORIZONS),
        "seed": SEED,
        "independent_oos_available": False,
        "rows": int(len(panel)),
        "dates": int(panel["date"].nunique()),
    }

    report["phase2_capacity_map"] = phase2_capacity_map(panel)
    report["phase3_information"] = phase3_information(panel)
    report["phase4_tradability"] = phase4_tradability(panel)
    report["phase7_execution"] = phase7_execution(panel)
    report["phase8_nulls"] = phase8_nulls(panel)
    report["phase9_splits"] = phase9_splits(panel)
    report["phase10_walk_forward"] = phase10_walk_forward(panel)
    report["phase11_decomposition"] = phase11_decomposition(
        panel, report["phase4_tradability"], report["phase7_execution"])

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)

    rule("E20 AUDIT COMPLETE")
    say("  written to %s" % os.path.relpath(OUT, ROOT))

    return 0


if __name__ == "__main__":
    sys.exit(main())
