"""E21 -- interrogate the positive result. DIAGNOSTIC ONLY.

Stage 2 passed with a mean IC of 0.1365 (t = 80.5) and the holdout held at
0.1528. That is the first positive result this programme has produced, which is
exactly why it gets audited harder than a negative one would.

THE SUSPICION, STATED BEFORE IT IS TESTED
-------------------------------------------
The preregistered composite has four components and two of them are
microstructure, not flow:

    closed_at_high   session t closed on its high after a >1% move
    vol_ratio        volume against its own trailing median

A name locked in a صف خرید at session t has high == low, so it necessarily
also has close == high. Queue locks are persistent. So `closed_at_high` at t is
close to "is it locked TODAY", and predicting "locked tomorrow" from "locked
today" is true, trivial, and nothing to do with حقوقی flow.

If the entire effect lives in that component, the composite passed and the
HYPOTHESIS did not -- and the two must not be reported as the same thing.

WHAT THIS FILE DOES NOT DO
---------------------------
It does not change the score, re-run a gate, or move a threshold. Stage 2 is
recorded and closed. These are diagnostics attached to the decision, computed
on the DISCOVERY window only. The holdout was spent once, in e21_stages.py, and
is not reopened here -- re-reading it for "just a diagnostic" is how a holdout
stops being one.

USAGE
    python examples/e21_diagnose.py
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
from examples import e21_preregister as prereg                  # noqa: E402
from examples import e21_stages as stages                       # noqa: E402

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

REPORT_PATH = os.path.join(OUT_DIR, "E21_diagnostics.json")

FLOW_ONLY = ("inst_net_ratio", "indiv_power")

MICRO_ONLY = ("closed_at_high", "vol_ratio")


def say(message):
    print(message, flush=True)


def composite(frame, names):
    grouped = frame.groupby("date", sort=False)
    parts = [prereg.COMPONENTS[n] * grouped[n].rank(pct=True) for n in names]
    return sum(parts) / float(len(names))


def measure(frame, names, label, seed, permutations=50):
    scored = frame.copy()
    scored["score"] = composite(scored, names)
    say("  %-34s" % label)
    out = stages.measure_ic(scored, "next_unbuyable_h1", seed, permutations,
                            verbose=False)
    lift = stages.decile_lift(scored, "next_unbuyable_h1")
    say("      mean IC %8.5f   t %8.2f   lift %5.3fx   (%d dates)"
        % (out["mean_ic"], out["t_stat"], lift["lift"], out["dates"]))
    out["lift"] = lift["lift"]
    out["top_rate_pct"] = lift["top_rate_pct"]
    out["universe_rate_pct"] = lift["universe_rate_pct"]
    return out


def main():
    say("E21 DIAGNOSTICS -- discovery window only, holdout NOT reopened")
    say("=" * 70)

    panel = pd.read_csv(PANEL_PATH, compression="gzip")

    frame = panel[panel["complete"].astype(bool)
                  & (panel["date"] >= prereg.DISCOVERY_START)
                  & (panel["date"] <= prereg.DISCOVERY_END)].copy()

    say("discovery rows: %d" % len(frame))
    say("")

    seed = prereg.SEED

    report = {"window": [prereg.DISCOVERY_START, prereg.DISCOVERY_END],
              "rows": int(len(frame)), "components": {}, "subsets": {}}

    # ---- 1. each component alone ---------------------------------------
    say("1. EACH COMPONENT ALONE")
    for name in prereg.COMPONENTS:
        report["components"][name] = measure(frame, (name,), name, seed)
    say("")

    # ---- 2. flow alone vs microstructure alone --------------------------
    say("2. THE TWO HALVES")
    report["components"]["FLOW_ONLY"] = measure(
        frame, FLOW_ONLY, "flow only (inst_net + indiv_power)", seed)
    report["components"]["MICRO_ONLY"] = measure(
        frame, MICRO_ONLY, "microstructure only", seed)
    report["components"]["ALL_FOUR"] = measure(
        frame, tuple(prereg.COMPONENTS), "all four (the preregistered score)",
        seed)
    say("")

    # ---- 3. the decisive test -------------------------------------------
    #
    # Drop every name already locked at session t. What remains is the set
    # where "it is already locked" carries no information, so anything the
    # score still finds is genuinely new.
    say("3. NAMES NOT ALREADY LOCKED AT SESSION t")

    unlocked = frame[~frame["locked_up"].astype(bool)]

    say("   rows: %d of %d  (%.1f%% of the panel)"
        % (len(unlocked), len(frame), 100.0 * len(unlocked) / len(frame)))
    say("   next-session unbuyable rate here: %.3f%%"
        % (100.0 * unlocked["next_unbuyable_h1"].mean()))
    say("")

    for label, names in (("flow only", FLOW_ONLY),
                         ("microstructure only", MICRO_ONLY),
                         ("all four", tuple(prereg.COMPONENTS))):
        report["subsets"]["unlocked_" + label.replace(" ", "_")] = measure(
            unlocked, names, label, seed)
    say("")

    # ---- 4. does it survive where a position could actually go? ---------
    #
    # E20's finding was that Alpha Core's information sat entirely in names too
    # illiquid to trade. If this signal does the same, it is true and useless
    # for the same reason, and that has to be found now rather than later.
    say("4. BY LIQUIDITY QUINTILE (E20's failure mode, re-checked)")

    frame["adv"] = frame.groupby("ins_code")["value"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=5).median())

    liquid = frame[frame["adv"].notna()].copy()
    liquid["quintile"] = liquid.groupby("date")["adv"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False,
                          duplicates="drop"))

    for q in range(5):
        subset = liquid[liquid["quintile"] == q]
        if len(subset) < 5000:
            continue
        label = "Q%d %s" % (q + 1, "(most illiquid)" if q == 0
                            else "(most liquid)" if q == 4 else "")
        report["subsets"]["liquidity_q%d" % (q + 1)] = measure(
            subset, tuple(prereg.COMPONENTS), label, seed)

    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)

    say("")
    say("wrote %s" % REPORT_PATH)

    return 0


if __name__ == "__main__":
    sys.exit(main())
