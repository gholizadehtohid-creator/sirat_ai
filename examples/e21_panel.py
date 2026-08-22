"""E21 -- build the flow/lock panel. Preregistration 2ce1d49dd90ebd46.

Reads `client_type_history` -- 1,681,938 rows that no experiment in this
programme has ever opened -- joins it to prices, attaches E14's microstructure
detector, and writes the panel Stage 1 and Stage 2 are measured on.

WHAT IS REUSED AND WHY THAT MATTERS
-------------------------------------
The lock detector is E14's `annotate`, imported and called, not reimplemented:

    locked_up  =  high == low  AND  volume > 0  AND  traded yesterday
                  AND  +1% < return <= +12%
    unbuyable  =  locked_up | reopen | extreme_gap

A fresh lock definition would be a free parameter, and a free parameter chosen
while a result is visible is the whole failure this platform exists to refuse.
E14's was preregistered, validated in anger, and is frozen in the fixture.

THE ONE MEASUREMENT DECISION THIS FILE MAKES, DECLARED
-------------------------------------------------------
E14 computed next-session state with `groupby.shift(-1)`, which moves one ROW.
A suspended name leaves no row at all, so over the 2-5 session horizons this
experiment needs for its decay check, shifting rows would silently reach across
a suspension and call a bar from three weeks later "session t+3".

So every name is reindexed onto the MARKET CALENDAR between its own first and
last observed session before the forward state is taken. Where a name has no
row on a calendar date, the forward target is left as **unknown (NaN)** and
excluded from measurement -- never filled with False.

Filling it False would assert that a suspended name was buyable, which is both
untrue and exactly the direction that flatters the signal: it would dilute the
positive class with quiet names. Absence is not approval, in this direction as
much as any other.

USAGE
    python examples/e21_panel.py
"""

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
import experiment_14_queue_artifact as e14                      # noqa: E402
from examples import e21_preregister as prereg                  # noqa: E402


DB = os.path.join(ROOT, "sirat_market.db")

OUT_DIR = backtest_config.OUTPUT_DIR

PANEL_PATH = os.path.join(OUT_DIR, "E21_flow_lock_panel.csv.gz")

# The panel is cut once, spanning discovery AND holdout. Cutting it twice would
# mean two builds that could drift apart; the window split is applied at
# measurement time, from the frozen preregistration.
PANEL_START = prereg.DISCOVERY_START
PANEL_END = prereg.HOLDOUT_END

FEATURES = ("inst_net_ratio", "indiv_power", "closed_at_high", "vol_ratio")


def say(message):
    print(message, flush=True)


# ==============================
# INPUTS
# ==============================

def read_prices(conn):
    say("reading price_history %s .. %s ..." % (PANEL_START, PANEL_END))

    bars = pd.read_sql_query(
        "SELECT ins_code, date, high, low, close, volume, value "
        "FROM price_history WHERE date BETWEEN ? AND ? ORDER BY ins_code, date",
        conn, params=(PANEL_START, PANEL_END))

    say("  %d bars, %d names" % (len(bars), bars["ins_code"].nunique()))

    return bars


def read_flow(conn):
    say("reading client_type_history ...")

    flow = pd.read_sql_query(
        "SELECT ins_code, date, "
        "       individual_buy_value, individual_sell_value, "
        "       institutional_buy_value, institutional_sell_value, "
        "       individual_buy_count, individual_sell_count "
        "FROM client_type_history WHERE date BETWEEN ? AND ?",
        conn, params=(PANEL_START, PANEL_END))

    say("  %d flow rows, %d names" % (len(flow), flow["ins_code"].nunique()))

    return flow


# ==============================
# FEATURES
# ==============================

def flow_features(flow):
    """The two flow components. Both are ratios, and both refuse to divide by
    a side nobody took -- `moneyflow._per_capita`'s rule, applied columnwise.
    """

    for column in flow.columns:
        if column not in ("ins_code", "date"):
            flow[column] = pd.to_numeric(flow[column], errors="coerce")

    total_buy = (flow["individual_buy_value"].fillna(0)
                 + flow["institutional_buy_value"].fillna(0))

    inst_net = (flow["institutional_buy_value"]
                - flow["institutional_sell_value"])

    # Institutional net as a share of the session's traded value. Zero is the
    # neutral point HERE because it is a signed share of a denominator, not a
    # raw rial figure -- the asymmetry that makes zero the wrong neutral point
    # for Iranian retail flow lives in the per-capita ratio below, not here.
    flow["inst_net_ratio"] = np.where(total_buy > 0, inst_net / total_buy,
                                      np.nan)

    buy_per_capita = np.where(
        (flow["individual_buy_count"] > 0) & (flow["individual_buy_value"] > 0),
        flow["individual_buy_value"] / flow["individual_buy_count"], np.nan)

    sell_per_capita = np.where(
        (flow["individual_sell_count"] > 0)
        & (flow["individual_sell_value"] > 0),
        flow["individual_sell_value"] / flow["individual_sell_count"], np.nan)

    # قدرت خریدار حقیقی. Logged so that "twice as much per buyer" and "half as
    # much per buyer" are equal and opposite rather than 1.0 and -0.5 around a
    # neutral point of 1.
    ratio = np.where((sell_per_capita > 0) & np.isfinite(buy_per_capita),
                     buy_per_capita / sell_per_capita, np.nan)

    flow["indiv_power"] = np.log(np.where(ratio > 0, ratio, np.nan))

    return flow[["ins_code", "date", "inst_net_ratio", "indiv_power"]]


def forward_state(bars, horizons):
    """Next-session microstructure state over the MARKET CALENDAR.

    See the module docstring: shifting rows crosses suspensions, so each name
    is reindexed onto the calendar first and missing sessions stay unknown.
    """

    calendar = np.sort(bars["date"].unique())

    say("  market calendar: %d sessions" % len(calendar))

    position = pd.Series(np.arange(len(calendar)), index=calendar)

    bars = bars.copy()
    bars["slot"] = bars["date"].map(position).astype("int64")

    frames = []

    for horizon in horizons:
        target = bars[["ins_code", "slot", "unbuyable", "locked_up"]].copy()
        target["slot"] = target["slot"] - horizon
        target = target.rename(columns={
            "unbuyable": "next_unbuyable_h%d" % horizon,
            "locked_up": "next_locked_up_h%d" % horizon})
        frames.append(target)

    out = bars

    for frame in frames:
        out = out.merge(frame, on=["ins_code", "slot"], how="left")

    # A name that has no row at slot+h either was suspended or had not listed
    # yet. Both are genuinely unknown, and both stay NaN.
    return out


def build():
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)

    try:
        bars = read_prices(conn)
        flow = read_flow(conn)
    finally:
        conn.close()

    say("annotating with E14's detector ...")

    bars = e14.annotate(bars)

    say("  locked_up rate  : %.4f%%" % (100.0 * bars["locked_up"].mean()))
    say("  unbuyable rate  : %.4f%%" % (100.0 * bars["unbuyable"].mean()))

    say("attaching forward state over the market calendar ...")

    bars = forward_state(bars, prereg.DECAY_HORIZONS)

    say("merging flow ...")

    features = flow_features(flow)

    panel = bars.merge(features, on=["ins_code", "date"], how="left")

    panel["closed_at_high"] = panel["closed_at_high"].astype(float)

    keep = ["date", "ins_code", "close", "volume", "value",
            "vol_ratio", "closed_at_high", "locked_up", "unbuyable",
            "inst_net_ratio", "indiv_power"]

    keep += ["next_unbuyable_h%d" % h for h in prereg.DECAY_HORIZONS]
    keep += ["next_locked_up_h%d" % h for h in prereg.DECAY_HORIZONS]

    panel = panel[keep]

    complete = panel[list(FEATURES)].notna().all(axis=1)

    say("")
    say("COVERAGE")
    say("  price rows                    : %d" % len(panel))
    for column in FEATURES:
        say("    %-26s %6.2f%% present"
            % (column, 100.0 * panel[column].notna().mean()))
    say("  all four components present   : %d  (%.2f%%)"
        % (complete.sum(), 100.0 * complete.mean()))

    panel["complete"] = complete

    os.makedirs(OUT_DIR, exist_ok=True)

    panel.to_csv(PANEL_PATH, index=False, compression="gzip")

    say("")
    say("wrote %s  (%.1f MB)"
        % (PANEL_PATH, os.path.getsize(PANEL_PATH) / 1048576.0))

    return panel


def main():
    say("E21 PANEL BUILD")
    say("=" * 70)
    say("preregistration : %s" % prereg.PREREGISTRATION["parameters"]["target"])
    say("window          : %s .. %s" % (PANEL_START, PANEL_END))
    say("")

    build()

    return 0


if __name__ == "__main__":
    sys.exit(main())
