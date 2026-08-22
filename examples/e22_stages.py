"""E22 -- stages 1-3 against the frozen preregistration b4faab1bb9b2abf1.

Stage 3 has never run in this programme. Every previous experiment either
stopped before it or would have had to report slippage as 0.0% to get through
it. `backtesting/impact.py` is what changed.

WHAT THE EXECUTION TEST ACTUALLY DOES
--------------------------------------
For every session with enough eligible names:

    eligible   position of 100M rials is <= 5% of the name's trailing
               20-session median traded value, and all four score components
               are present
    baseline   draw 10 names uniformly at random from the eligible set
    filtered   draw 10 names uniformly at random from the eligible set MINUS
               its top score decile

Both baskets are then bought at t+1. A name that is `unbuyable` at t+1 is a
FAILED FILL -- not a fill at a worse price. Impact is charged only on positions
that actually filled, because a trade that did not happen did not pay for
liquidity it never took.

Twenty independent draws per session, one frozen seed. The randomness is the
control: a basket with no expected edge cannot blend a selection effect into
the execution measurement, and cannot discover an alpha by accident.

USAGE
    python examples/e22_stages.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
from backtesting import impact                                  # noqa: E402
from examples import e21_preregister as e21                     # noqa: E402
from examples import e21_stages as e21s                         # noqa: E402
from examples import e22_preregister as prereg                  # noqa: E402
from sirat.experiments import universe                          # noqa: E402
from sirat.experiments.manager import Manager                   # noqa: E402

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

REPORT_PATH = os.path.join(OUT_DIR, "E22_stage_results.json")

COMMISSION_PCT = backtest_config.EXPECTED_ROUND_TRIP_PCT


def say(message):
    print(message, flush=True)


# ==============================
# DECISION-TIME QUANTITIES
# ==============================

def prepare(panel):
    """ADV, volatility and both scores. Everything shifted to session t."""

    panel = panel.sort_values(["ins_code", "date"]).copy()

    grouped = panel.groupby("ins_code", sort=False)

    # Trailing median traded value, shifted so the reading predates the bar it
    # is used to size. Median rather than mean: one block trade should not
    # license a position the name cannot carry on an ordinary day.
    panel["adv"] = grouped["value"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).median())

    ret = grouped["close"].pct_change() * 100.0
    panel["sigma"] = ret.groupby(panel["ins_code"]).transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).std())

    complete = panel["complete"].astype(bool)

    scored = panel[complete].copy()

    by_date = scored.groupby("date", sort=False)

    primary = []
    corrected = []

    for name, sign in e21.COMPONENTS.items():
        ranked = by_date[name].rank(pct=True)
        primary.append(sign * ranked)
        # Secondary: the same composite with inst_net_ratio flipped. Declared
        # NON-INDEPENDENT in the preregistration -- the sign was chosen after
        # seeing E21's diagnostics, so it informs the next experiment and is
        # never itself a result.
        corrected.append((-sign if name == "inst_net_ratio" else sign) * ranked)

    scored["score"] = sum(primary) / float(len(e21.COMPONENTS))
    scored["score_corrected"] = sum(corrected) / float(len(e21.COMPONENTS))

    return scored


def mark_eligible(scored):
    """Can a 100M-rial position enter this name inside the 5% cap?"""

    cap = prereg.MAX_PARTICIPATION_PCT / 100.0

    scored["participation"] = prereg.POSITION_VALUE / scored["adv"]

    scored["eligible"] = (scored["adv"].notna()
                          & (scored["participation"] <= cap)
                          & scored["sigma"].notna()
                          & scored["next_unbuyable_h1"].notna())

    return scored


def charge_impact(frame):
    """Impact per row, through the module -- never a reimplemented formula.

    Computed only where it will be used (eligible rows), and left as NaN
    elsewhere. NaN is the honest value: it means "not computed", and the Stage
    3 gate counts those rather than treating them as free.
    """

    costs = np.full(len(frame), np.nan)
    computed = np.zeros(len(frame), dtype=bool)

    values = frame[["adv", "sigma", "eligible"]].to_numpy()

    for index in range(len(frame)):
        adv, sigma, eligible = values[index]

        if not eligible:
            continue

        result = impact.estimate(prereg.POSITION_VALUE, adv, sigma)

        if result.executable:
            costs[index] = result.cost_pct
            computed[index] = True

    frame = frame.copy()
    frame["impact_pct"] = costs
    frame["impact_computed"] = computed

    return frame


# ==============================
# THE EXECUTION EXPERIMENT
# ==============================

def run_baskets(scored, seed):
    """Random baskets, with and without the filter. One pass over sessions."""

    rng = np.random.default_rng(seed)

    eligible = scored[scored["eligible"]]

    records = []

    dates = []

    for date, frame in eligible.groupby("date", sort=True):
        n = len(frame)

        if n < prereg.MIN_ELIGIBLE_NAMES:
            continue

        score = frame["score"].to_numpy()
        failed = frame["next_unbuyable_h1"].to_numpy().astype(bool)
        cost = frame["impact_pct"].to_numpy()
        computed = frame["impact_computed"].to_numpy().astype(bool)
        adv = frame["adv"].to_numpy()

        # The decile is cut inside the ELIGIBLE set, not the whole universe:
        # a filter is applied to what you would actually have considered.
        threshold = np.quantile(score, prereg.FILTER_DECILE)
        keep = score < threshold

        dates.append({
            "date": date, "eligible": int(n),
            "after_filter": int(keep.sum()),
            "adv_admitted": float(np.median(adv[keep])) if keep.any() else np.nan,
            "adv_excluded": (float(np.median(adv[~keep]))
                             if (~keep).any() else np.nan),
        })

        if keep.sum() < prereg.BASKET:
            continue

        pool_filtered = np.flatnonzero(keep)

        for _ in range(prereg.DRAWS_PER_DATE):
            base = rng.choice(n, size=prereg.BASKET, replace=False)
            filt = rng.choice(pool_filtered, size=prereg.BASKET, replace=False)

            for label, picks in (("baseline", base), ("filtered", filt)):
                miss = failed[picks]
                filled = ~miss

                records.append({
                    "date": date,
                    "variant": label,
                    "failed": int(miss.sum()),
                    "filled": int(filled.sum()),
                    "impact_sum": float(np.nansum(cost[picks][filled])),
                    "impact_computed": int(computed[picks][filled].sum()),
                })

    return pd.DataFrame(records), pd.DataFrame(dates)


def summarise(baskets):
    out = {}

    for label, frame in baskets.groupby("variant"):
        positions = frame["failed"].sum() + frame["filled"].sum()
        filled = frame["filled"].sum()

        out[label] = {
            "baskets": int(len(frame)),
            "positions": int(positions),
            "filled": int(filled),
            "failed_fill_pct": float(100.0 * frame["failed"].sum() / positions),
            "impact_computed_pct": (
                float(100.0 * frame["impact_computed"].sum() / filled)
                if filled else float("nan")),
            "mean_impact_pct": (float(frame["impact_sum"].sum() / filled)
                                if filled else float("nan")),
        }

        out[label]["round_trip_pct"] = (COMMISSION_PCT
                                        + 2.0 * out[label]["mean_impact_pct"])

    return out


# ==============================
# STAGES
# ==============================

def stage_three(manager, scored, thresholds, seed):
    say("STAGE 3 -- EXECUTION REALISM   (first run in this programme)")

    say("  charging impact through backtesting/impact.py ...")
    scored = charge_impact(scored)

    say("  drawing %d random baskets per session ..." % prereg.DRAWS_PER_DATE)
    baskets, per_date = run_baskets(scored, seed)

    say("  %d baskets over %d sessions" % (len(baskets), per_date.shape[0]))
    say("")

    summary = summarise(baskets)

    base = summary["baseline"]
    filt = summary["filtered"]

    ratio = filt["failed_fill_pct"] / base["failed_fill_pct"]

    say("  %-12s %10s %10s %14s %12s"
        % ("variant", "positions", "failed%", "impact%", "round trip%"))
    for label in ("baseline", "filtered"):
        row = summary[label]
        say("  %-12s %10d %10.4f %14.4f %12.4f"
            % (label, row["positions"], row["failed_fill_pct"],
               row["mean_impact_pct"], row["round_trip_pct"]))

    say("")
    say("  failed-fill ratio filtered/baseline : %.4f  (must be <= %.2f)"
        % (ratio, thresholds["max_filtered_over_baseline_fill_failure"]))

    impact_delta = filt["mean_impact_pct"] - base["mean_impact_pct"]
    round_trip_delta = filt["round_trip_pct"] - base["round_trip_pct"]

    say("  impact delta                        : %+.4f pp (cap %.2f)"
        % (impact_delta, thresholds["max_impact_delta_pp"]))
    say("  round-trip delta                    : %+.4f pp (cap %.2f)"
        % (round_trip_delta, thresholds["max_round_trip_delta_pp"]))

    median_after = float(per_date["after_filter"].median())
    adv_ratio = float((per_date["adv_admitted"]
                       / per_date["adv_excluded"]).median())

    say("  median eligible after filter        : %.0f (floor %d)"
        % (median_after, thresholds["min_eligible_names"]))
    say("  admitted/excluded ADV ratio         : %.4f (floor %.1f)"
        % (adv_ratio, thresholds["min_admitted_excluded_adv_ratio"]))

    computed_pct = min(base["impact_computed_pct"],
                       filt["impact_computed_pct"])

    say("  impact computed on filled positions : %.2f%% (floor %.1f%%)"
        % (computed_pct, thresholds["min_impact_computed_pct"]))
    say("")

    metrics = {
        "queue_effects": bool(
            ratio <= thresholds["max_filtered_over_baseline_fill_failure"]),
        "slippage": bool(
            (computed_pct >= thresholds["min_impact_computed_pct"])
            and (impact_delta <= thresholds["max_impact_delta_pp"])),
        "participation_limits": bool(
            median_after >= thresholds["min_eligible_names"]),
        "liquidity": bool(
            adv_ratio >= thresholds["min_admitted_excluded_adv_ratio"]),
        "transaction_costs": bool(
            round_trip_delta <= thresholds["max_round_trip_delta_pp"]),
    }

    for name in sorted(metrics):
        say("    %-22s %s" % (name, "PASS" if metrics[name] else "FAIL"))

    detail = {
        "summary": summary,
        "failed_fill_ratio": ratio,
        "impact_delta_pp": impact_delta,
        "round_trip_delta_pp": round_trip_delta,
        "median_eligible_after_filter": median_after,
        "admitted_excluded_adv_ratio": adv_ratio,
        "impact_computed_pct": computed_pct,
        "sessions": int(per_date.shape[0]),
        "commission_pct": COMMISSION_PCT,
        "prefactor": impact.PREFACTOR_EFFECTIVE,
    }

    # The prefactor is borrowed from another market, so a verdict that depends
    # on it is a verdict about the constant. Swept and recorded either way.
    sweep = {}
    for c in impact.SENSITIVITY_PREFACTORS:
        scale = c / impact.PREFACTOR_EFFECTIVE
        sweep[str(c)] = {
            "baseline_round_trip_pct": (COMMISSION_PCT
                                        + 2.0 * base["mean_impact_pct"] * scale),
            "filtered_round_trip_pct": (COMMISSION_PCT
                                        + 2.0 * filt["mean_impact_pct"] * scale),
            "delta_pp": round_trip_delta * scale,
        }
    detail["prefactor_sweep"] = sweep

    say("")
    say("  prefactor sweep (borrowed constant, so the verdict is checked "
        "against all of them):")
    for c, row in sweep.items():
        say("    c=%-5s baseline %.4f%%  filtered %.4f%%  delta %+.4f pp"
            % (c, row["baseline_round_trip_pct"],
               row["filtered_round_trip_pct"], row["delta_pp"]))

    result = manager.run_stage(3, metrics, detail)

    say("")
    say("  -> %s%s" % (result["status"],
                       ("  failed: " + ", ".join(result["failed"]))
                       if result["failed"] else ""))

    return result, detail


def main():
    say("E22 -- DOES THE FLOW FILTER SURVIVE EXECUTION?")
    say("=" * 70)

    manager = Manager.load(prereg.EXPERIMENT_ID)
    thresholds = manager.thresholds()
    seed = manager.seed()

    say("preregistration verified. seed %d, next stage %s"
        % (seed, manager.next_stage()))
    say("out of sample: NONE AVAILABLE -- declared at registration")
    say("")

    panel = pd.read_csv(PANEL_PATH, compression="gzip")
    say("panel: %d rows" % len(panel))

    scored = prepare(panel)
    scored = mark_eligible(scored)

    say("scored %d rows, %d eligible (%.1f%%)"
        % (len(scored), int(scored["eligible"].sum()),
           100.0 * scored["eligible"].mean()))
    say("")

    report = {"experiment_id": prereg.EXPERIMENT_ID, "thresholds": thresholds,
              "seed": seed}

    # ---- Stage 1 --------------------------------------------------------
    say("STAGE 1 -- DATA INTEGRITY")

    alignment = e21s.verify_forward_alignment(panel)
    coverage = 100.0 * float(panel["complete"].astype(bool).mean())
    per_date = scored[scored["eligible"]].groupby("date").size()
    median_eligible = float(per_date.median()) if len(per_date) else 0.0

    try:
        universe.restore(prereg.UNIVERSE_SNAPSHOT)
        survivorship = True
    except Exception:                                           # noqa: BLE001
        survivorship = False

    say("  forward alignment : %.2f%%" % alignment["agreement_pct"])
    say("  universe snapshot : %s" % ("restored" if survivorship else "FAILED"))
    say("  median eligible   : %.0f names/date (floor %d)"
        % (median_eligible, thresholds["min_eligible_names"]))
    say("  flow coverage     : %.2f%%" % coverage)

    metrics1 = {
        "point_in_time": bool(alignment["agreement_pct"] == 100.0),
        "survivorship": survivorship,
        "universe_valid": bool(median_eligible
                               >= thresholds["min_median_names_discovery"]),
        "missing_data": bool(coverage >= thresholds["min_flow_coverage_pct"]),
    }

    detail1 = {"alignment": alignment, "coverage_pct": coverage,
               "median_eligible": median_eligible}

    stage1 = manager.run_stage(1, metrics1, detail1)
    say("  -> %s" % stage1["status"])
    say("")

    report["stage_1"] = {"status": stage1["status"], "detail": detail1}

    if stage1["status"] != "PASS":
        _write(report)
        return 0

    # ---- Stage 2 (re-measurement, NOT evidence) -------------------------
    say("STAGE 2 -- ECONOMIC SIGNAL   [RE-MEASUREMENT OF E21, NOT EVIDENCE]")

    primary = e21s.measure_ic(scored, "next_unbuyable_h1", seed,
                              e21.PERMUTATIONS)
    lift1 = e21s.decile_lift(scored, "next_unbuyable_h1")
    lift3 = e21s.decile_lift(scored, "next_unbuyable_h3")

    retention = ((lift3["lift"] - 1.0) / (lift1["lift"] - 1.0)
                 if lift1["lift"] > 1.0 else float("nan"))

    say("    lift h=1 %.3fx, h=3 %.3fx, retention %.3f"
        % (lift1["lift"], lift3["lift"], retention))

    metrics2 = {
        "ic": bool(primary["mean_ic"] >= thresholds["min_mean_ic"]),
        "rank_correlation": bool(primary["t_stat"]
                                 >= thresholds["min_ic_t_stat"]),
        "random_baseline": bool(primary["null_percentile"]
                                > thresholds["min_null_percentile"]),
        "factor_decay": bool((lift1["lift"] >= thresholds["min_decile_lift_h1"])
                             and (retention
                                  >= thresholds["min_lift_retention_h3"])),
    }

    detail2 = {"primary": primary, "lift_h1": lift1, "lift_h3": lift3,
               "retention_h3": retention,
               "status_note": "RE-MEASUREMENT of E21's archived result on "
                              "E21's window. Carries no independent "
                              "information and must not be counted as a "
                              "second confirmation."}

    stage2 = manager.run_stage(2, metrics2, detail2)
    say("  -> %s   (re-measurement)" % stage2["status"])
    say("")

    report["stage_2"] = {"status": stage2["status"], "detail": detail2}

    if stage2["status"] != "PASS":
        _write(report)
        return 0

    # ---- Stage 3 --------------------------------------------------------
    stage3, detail3 = stage_three(manager, scored, thresholds, seed)

    report["stage_3"] = {"status": stage3["status"],
                         "failed": stage3["failed"], "detail": detail3}

    _write(report)

    return 0


def _write(report):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    say("")
    say("wrote %s" % REPORT_PATH)


if __name__ == "__main__":
    sys.exit(main())
