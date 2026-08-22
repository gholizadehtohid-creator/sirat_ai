"""E22 -- explain the Stage 3 cost failure. DIAGNOSTIC ONLY.

Stage 3 reported two things that look incompatible:

    liquidity   PASS   admitted names have 1.115x the median ADV of excluded
    slippage    FAIL   the filtered basket paid 0.2512% impact vs 0.1759%

An earlier version of this file asserted the answer -- that the median ADV
comparison missed a thin tail -- and its own output refuted it: admitted rows
are cheaper on every row-level measure, 0.3233% against 0.3685% among filled
positions. The assertion was replaced with a measurement, which is what this
file should have contained in the first place.

WHAT THE MEASUREMENT SHOWS
---------------------------
Per session, the filter is CHEAPER. It is the aggregate that reverses, and it
reverses because a handful of sessions carry impact figures of 56-76% of price
across the entire cross-section -- which no market has ever printed.

The cause is eleven rows in a two-month sample with impossible returns, the
largest +19,976,600%, i.e. a prior close of 1 rial: new listings,
post-suspension reopenings and unadjusted capital increases. One such row sits
inside a 20-session trailing volatility window for twenty sessions.

E14's `annotate` has flagged exactly these bars as `extreme_gap` (|ret| > 12%)
since it was written. The platform already held the knowledge; the volatility
estimator did not use it.

Nothing here changes a threshold, a verdict or a score. Stage 3 is recorded and
closed, and E22 is REJECTed on `queue_effects`, which depends on none of this.

USAGE
    python examples/e22_diagnose.py
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
from examples import e22_preregister as prereg                  # noqa: E402
from examples import e22_stages as e22                          # noqa: E402

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

REPORT_PATH = os.path.join(OUT_DIR, "E22_diagnostics.json")

COMMISSION_PCT = backtest_config.EXPECTED_ROUND_TRIP_PCT

RISK_FREE_ANNUAL = 0.23

SESSIONS_PER_YEAR = 240.0

# E14's own constant, cited rather than reinvented: above this a bar is a
# reopening or a gap, not a band move.
EXTREME_MOVE_PCT = 12.0


def say(message):
    print(message, flush=True)


def main():
    say("E22 DIAGNOSTICS -- why did the filtered basket read as more expensive?")
    say("=" * 78)

    panel = pd.read_csv(PANEL_PATH, compression="gzip")

    scored = e22.charge_impact(e22.mark_eligible(e22.prepare(panel)))

    eligible = scored[scored["eligible"]].copy()
    eligible["threshold"] = eligible.groupby("date")["score"].transform(
        lambda s: s.quantile(prereg.FILTER_DECILE))
    admitted_mask = eligible["score"] < eligible["threshold"]
    eligible["filled"] = ~eligible["next_unbuyable_h1"].astype(bool)

    report = {}

    # ---- 1. row level ---------------------------------------------------
    say("")
    say("1. ROW LEVEL -- admitted names are CHEAPER, not more expensive")
    say("   %-10s %9s %9s %13s %13s"
        % ("set", "rows", "fail%", "impact all", "impact filled"))

    for label, mask in (("admitted", admitted_mask),
                        ("excluded", ~admitted_mask)):
        frame = eligible[mask]
        cost = frame["impact_pct"].to_numpy()
        fill = frame["filled"].to_numpy()
        row = {
            "rows": int(len(frame)),
            "fail_pct": float(100.0 * (~fill).mean()),
            "impact_all": float(np.nanmean(cost)),
            "impact_filled": float(np.nanmean(cost[fill])),
        }
        report[label] = row
        say("   %-10s %9d %9.4f %13.4f %13.4f"
            % (label, row["rows"], row["fail_pct"], row["impact_all"],
               row["impact_filled"]))

    say("   So the Stage 3 aggregate is not describing the population it "
        "sampled from.")

    # ---- 2. per session -------------------------------------------------
    say("")
    say("2. PER SESSION -- the filter is cheaper on the typical date")

    rows = []

    for date, frame in eligible.groupby("date", sort=True):
        if len(frame) < prereg.MIN_ELIGIBLE_NAMES:
            continue
        admitted = frame[frame["score"] < frame["threshold"]]
        fill_all = frame["filled"].to_numpy()
        fill_adm = admitted["filled"].to_numpy()
        if fill_all.sum() < 5 or fill_adm.sum() < 5:
            continue
        rows.append({
            "date": date,
            "names": int(len(frame)),
            "baseline": float(np.nanmean(
                frame["impact_pct"].to_numpy()[fill_all])),
            "filtered": float(np.nanmean(
                admitted["impact_pct"].to_numpy()[fill_adm])),
        })

    per_date = pd.DataFrame(rows)
    per_date["delta"] = per_date["filtered"] - per_date["baseline"]

    say("   sessions: %d" % len(per_date))
    say("   %-10s %12s %12s" % ("", "baseline", "filtered"))
    for q, label in ((0.50, "MEDIAN"), (0.95, "p95"), (1.00, "max")):
        say("   %-10s %12.4f %12.4f"
            % (label, per_date["baseline"].quantile(q),
               per_date["filtered"].quantile(q)))
    say("   %-10s %12.4f %12.4f"
        % ("mean", per_date["baseline"].mean(), per_date["filtered"].mean()))
    say("")
    say("   delta: median %+.4f pp, mean %+.4f pp, %.1f%% of sessions worse"
        % (per_date["delta"].median(), per_date["delta"].mean(),
           100.0 * (per_date["delta"] > 0).mean()))

    worst = per_date.nlargest(5, "delta")

    say("")
    say("   the five sessions driving the aggregate:")
    for row in worst.itertuples():
        say("     %s  %4d names  baseline %8.3f%%  filtered %8.3f%%"
            % (row.date, row.names, row.baseline, row.filtered))

    top_share = (100.0 * per_date.nlargest(max(1, len(per_date) // 100),
                                           "delta")["delta"].sum()
                 / per_date["delta"].sum())

    say("   the worst 1%% of sessions account for %.1f%% of the total delta "
        "-- they invert its sign, not merely dominate it." % top_share)

    report["per_session"] = {
        "sessions": int(len(per_date)),
        "baseline_median": float(per_date["baseline"].median()),
        "filtered_median": float(per_date["filtered"].median()),
        "delta_median": float(per_date["delta"].median()),
        "delta_mean": float(per_date["delta"].mean()),
        "sessions_worse_pct": float(100.0 * (per_date["delta"] > 0).mean()),
        "worst_1pct_share_of_delta": float(top_share),
        "worst_sessions": worst.to_dict("records"),
    }

    # ---- 3. the contamination -------------------------------------------
    say("")
    say("3. THE CAUSE -- returns no market ever printed")

    prices = panel.sort_values(["ins_code", "date"]).copy()
    prices["ret"] = prices.groupby("ins_code")["close"].pct_change() * 100.0

    impossible = prices[prices["ret"].abs() > 200.0]
    extreme = prices[prices["ret"].abs() > EXTREME_MOVE_PCT]

    say("   |return| > 200%%          : %d rows, %d names, %d dates"
        % (len(impossible), impossible["ins_code"].nunique(),
           impossible["date"].nunique()))
    say("   |return| > %.0f%% (E14 gap)  : %d rows (%.4f%% of the panel)"
        % (EXTREME_MOVE_PCT, len(extreme), 100.0 * len(extreme) / len(prices)))
    say("   largest single return    : {:+,.0f}%".format(prices["ret"].max()))
    say("")
    say("   A prior close of 1 rial produces a return of ~20,000,000%%. One "
        "such")
    say("   row sits inside a 20-session trailing volatility window for twenty")
    say("   sessions, and one contaminated name dominates every basket drawn")
    say("   on those dates. Panel signature: sigma mean %.2f%% vs median %.2f%%."
        % (float(np.nanmean(scored["sigma"])),
           float(np.nanmedian(scored["sigma"]))))

    report["contamination"] = {
        "rows_over_200pct": int(len(impossible)),
        "rows_over_extreme_gap": int(len(extreme)),
        "extreme_gap_share_pct": float(100.0 * len(extreme) / len(prices)),
        "max_return_pct": float(prices["ret"].max()),
        "sigma_mean": float(np.nanmean(scored["sigma"])),
        "sigma_median": float(np.nanmedian(scored["sigma"])),
        "already_flagged_by": ("E14 annotate(): extreme_gap = |ret| > %.0f%%"
                               % EXTREME_MOVE_PCT),
    }

    # ---- 4. the economics, which the contamination does not touch --------
    say("")
    say("4. THE ECONOMICS -- unaffected by any of the above")

    baseline_fail, filtered_fail = 3.6155, 3.0907
    cost_added = 0.1504

    fills_gained = (baseline_fail - filtered_fail) / 100.0

    cash_per_session = ((1.0 + RISK_FREE_ANNUAL) ** (1.0 / SESSIONS_PER_YEAR)
                        - 1.0) * 100.0

    say("   failed fills avoided       : %.4f pp of positions"
        % (baseline_fail - filtered_fail))
    say("   extra cost per filled slot : %.4f pp" % cost_added)
    say("   cash earns per session     : %.4f%% at the %.0f%% policy rate"
        % (cash_per_session, 100 * RISK_FREE_ANNUAL))
    say("   break-even per converted slot: %.2f pp"
        % (cost_added / fills_gained))
    say("")
    say("   The basket is RANDOM, so a converted fill has an expected excess")
    say("   over cash of zero by construction. Even if the cost were zero, the")
    say("   14.5%% reduction in failed fills is below the 30%% declared, so")
    say("   queue_effects fails on its own and the rejection does not rest on")
    say("   any of the cost arithmetic.")

    report["economics"] = {
        "failed_fills_avoided_pp": baseline_fail - filtered_fail,
        "cost_added_pp": cost_added,
        "break_even_pp_per_converted_slot": cost_added / fills_gained,
        "cash_per_session_pct": cash_per_session,
        "note": ("the reference basket is random, so the expected excess of a "
                 "converted fill is zero. This bounds what the filter is worth "
                 "in THIS experiment; it does not bound what it could be worth "
                 "in front of a validated signal."),
    }

    # ---- 5. what this settles -------------------------------------------
    say("")
    say("5. SETTLED AND OPEN")
    say("   settled : the filter does not pay for itself in front of a basket")
    say("             with no expected edge, and it reduces failed fills by")
    say("             half of what was declared necessary.")
    say("   NOT the filter's fault : the two cost checks. Per session the")
    say("             filter is cheaper; the aggregate is contaminated.")
    say("   open    : whether it pays in front of a basket with a real")
    say("             expected excess. That needs a validated signal.")
    say("   owed    : a volatility estimator that excludes E14 extreme_gap")
    say("             bars, and a Stage 1 check for physically impossible")
    say("             returns. Neither may be used to re-run E22.")

    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)

    say("")
    say("wrote %s" % REPORT_PATH)

    return 0


if __name__ == "__main__":
    sys.exit(main())
