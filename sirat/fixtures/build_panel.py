"""Rebuild the frozen Alpha Core fixture panel from the primary data.

WHY THE FIXTURE NEEDS A FROZEN PANEL AT ALL
--------------------------------------------
Re-deriving E14 from source reads 1.6 million bars out of a 424 MB database and
a 23 MB factor panel. That is fine as a nightly job and unacceptable as a check
that runs on every commit -- a regression fixture nobody waits for is a
regression fixture somebody disables.

So the fixture runs in two tiers, and the split is chosen so that the fast tier
still tests the thing that matters:

    TIER 1  (seconds, every build)   recompute all five anchors THROUGH THE
                                     LIVE ANALYTICS CODE from a frozen panel
    TIER 2  (minutes, on demand)     rebuild that panel from the database and
                                     assert it still reproduces the anchors

Tier 1 is not "read the recorded numbers back". The recorded numbers are in
`alpha_core_expected.json`; the panel holds only inputs -- scores, forward
returns and next-session microstructure state -- and every anchor is computed
from them when the test runs. Change how IC is measured, how a decile is cut,
how an unfilled slot is treated, and Tier 1 goes red.

What Tier 1 cannot catch is the panel drifting away from the market data it was
cut from. That is Tier 2's job, and it is why this module exists rather than the
panel simply being a file somebody once produced.

USAGE
-----
    python -m sirat.fixtures.build_panel            rebuild and verify
    python -m sirat.fixtures.build_panel --verify   verify only, write nothing
"""

import argparse
import hashlib
import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

PANEL_PATH = os.path.join(DATA_DIR, "alpha_core_panel.csv.gz")

EXPECTED_PATH = os.path.join(DATA_DIR, "alpha_core_expected.json")

# E14's window, to the day. Not "roughly ten years": the anchors were measured
# on these dates and a window that moved would change every one of them while
# looking like the same test.
WINDOW_START = "2014-09-21"
WINDOW_END = "2024-09-01"

HORIZONS = (1, 5, 10, 20)

# What the fixture needs and nothing else. Every extra column is a column that
# can change for reasons unrelated to the five anchors and take the build red
# with it.
KEEP = ["date", "ins_code", "alpha", "fwd_1", "fwd_5", "fwd_10", "fwd_20",
        "next_locked_up", "next_unbuyable"]


def build(verbose=True):
    """The merged, scored panel restricted to E14's window."""

    import experiment_14_queue_artifact as e14
    import research_alpha_ic as ric
    import backtest_config

    def say(message):
        if verbose:
            print(message, flush=True)

    say("reading price_history ...")

    bars = e14.annotate(e14.load_bars())
    bars = e14.forward(bars, HORIZONS)
    bars = e14.next_session_state(bars)

    say("  %d bars, %d instruments" % (len(bars), bars["ins_code"].nunique()))

    panel_path = os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")

    if not os.path.exists(panel_path):
        raise SystemExit(
            "factor_panel.csv is missing. Rebuild it with research_factors.py "
            "before rebuilding the fixture -- the fixture is downstream of it.")

    say("reading factor_panel.csv ...")

    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ins_code"] = panel["ins_code"].astype(str)

    scored = ric.score_frame(panel)
    scored = scored[(scored["date"] >= WINDOW_START)
                    & (scored["date"] <= WINDOW_END)]

    merged = scored.merge(
        bars[["ins_code", "date", "fwd_1", "next_locked_up", "next_unbuyable"]],
        on=["ins_code", "date"], how="left")

    merged = merged[merged["alpha"].notna()].copy()

    for column in ("next_locked_up", "next_unbuyable"):
        # A name with no bar on the decision date has no next-session state.
        # False is right here and is not the usual absence-as-zero mistake: the
        # question is "would a T+1 order have been blocked", and a name that did
        # not trade cannot have been blocked by a queue. It is caught by the
        # staleness column instead.
        merged[column] = merged[column].fillna(False).astype(bool)

    frame = merged[KEEP].sort_values(["date", "ins_code"]).reset_index(drop=True)

    say("  %d rows over %d dates" % (len(frame), frame["date"].nunique()))

    return frame


def panel_hash(frame):
    """Content hash of the frozen panel.

    Over the rounded values that the CSV actually stores, so the hash describes
    the file a test will read rather than the float64 that produced it.
    """

    digest = hashlib.sha256()

    payload = frame.to_csv(index=False, date_format="%Y-%m-%d",
                           float_format="%.6f")

    digest.update(payload.encode("utf-8"))

    return digest.hexdigest()


def write(frame, path=None):
    path = path or PANEL_PATH

    os.makedirs(os.path.dirname(path), exist_ok=True)

    frame.to_csv(path, index=False, compression="gzip",
                 date_format="%Y-%m-%d", float_format="%.6f")

    return path


def load(path=None):
    """The frozen panel, as the fixture reads it."""

    path = path or PANEL_PATH

    if not os.path.exists(path):
        raise SystemExit(
            "the frozen fixture panel is missing (%s). Rebuild it with "
            "`python -m sirat.fixtures.build_panel`, which needs "
            "sirat_market.db and backtest_output/factor_panel.csv." % path)

    frame = pd.read_csv(path, compression="gzip")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["ins_code"] = frame["ins_code"].astype(str)

    for column in ("next_locked_up", "next_unbuyable"):
        frame[column] = frame[column].astype(bool)

    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true",
                        help="rebuild in memory and compare; write nothing")
    parser.add_argument("--out", default=PANEL_PATH)

    args = parser.parse_args(argv)

    frame = build()

    fresh = panel_hash(frame)

    print("\npanel hash  %s" % fresh)

    if args.verify:
        existing = load(args.out)
        recorded = panel_hash(existing)
        print("frozen hash %s" % recorded)
        if recorded != fresh:
            print("\nMISMATCH. The frozen panel no longer matches what the "
                  "database produces.\nThis is a real finding, not a stale "
                  "file: either the market data changed under\nthe fixture, or "
                  "the code that builds it did. Investigate before "
                  "regenerating.")
            return 1
        print("\nthe frozen panel still matches the database")
        return 0

    path = write(frame, args.out)

    print("wrote %s (%.1f MB)"
          % (path, os.path.getsize(path) / (1024.0 * 1024.0)))

    if os.path.exists(EXPECTED_PATH):
        with open(EXPECTED_PATH, encoding="utf-8") as handle:
            expected = json.load(handle)
        expected["panel_sha256"] = fresh
        with open(EXPECTED_PATH, "w", encoding="utf-8") as handle:
            json.dump(expected, handle, ensure_ascii=False, indent=2)
        print("updated panel_sha256 in %s" % EXPECTED_PATH)

    return 0


if __name__ == "__main__":
    sys.exit(main())
