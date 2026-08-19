"""ALPHA CORE REGRESSION FIXTURE -- an adversarial test, not a strategy.

WHAT THIS IS
------------
Alpha Core v1 was rejected four times over, for four different reasons, and the
causal account is complete. This module keeps that rejection alive as a test.

    E13   the signal decays: IC 0.10492 at h=1 -> 0.01456 at h=20 (traded)
    E13   the economics fail: 0.55% excess against a 1.04% round trip
    E14   the mechanism is microstructure: the top decile is 3.03x more likely
          to be limit-locked at the moment it would be bought
    E15   execution eats it: the book requests +3.17% at h=20 and gets +0.59%
    E11   the portfolio loses: 3.3rd percentile against 30 random controls

THE TOLERANCES ARE TWO-SIDED, AND THAT IS THE WHOLE DESIGN
-----------------------------------------------------------
A number that drifts in the unfavourable direction fails. A number that drifts
in the FAVOURABLE direction fails identically, and this is the point of the
fixture rather than a side effect of using an interval.

A rejected strategy that starts looking better has, in every case this project
has actually seen, been rescued by a defect: a stale-bar trap that removed the
worst days, a `shift(-h)` that reached across a suspension, a fill model that
quietly refilled unbuyable slots. If Alpha Core's queue ratio falls to 1.2x, the
correct response is not relief. It is to find what stopped detecting queues.

    If future changes make Alpha Core look better without explanation:
    FAIL THE BUILD.

ALPHA CORE IS FROZEN
--------------------
`check_frozen()` hashes `backtesting/alpha_core.py` and compares it with the
recorded value. Any edit to the strategy fails the build regardless of the
numbers, because the anchors describe THAT code and describe nothing once it
changes. Re-pinning the hash is a deliberate two-file change that shows up in
review, which is the intended cost.

TIERS
-----
    run()        seconds. Recomputes all five anchors from the frozen panel,
                 through the live analytics code. Every CI build.
    run(full)    minutes. Rebuilds the panel from sirat_market.db first, so the
                 frozen panel cannot silently drift from the market data.
"""

import hashlib
import json
import math
import os

import numpy as np
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

EXPECTED_PATH = os.path.join(DATA_DIR, "alpha_core_expected.json")

STRATEGY_PATH = os.path.join(ROOT, "backtesting", "alpha_core.py")


# ==============================================================
# THE ANCHORS
# ==============================================================
#
# Held as code rather than data on purpose. A threshold in a JSON file next to
# the fixture is a threshold a red build can be made green by editing; a
# threshold here changes only in a diff someone has to justify.

# 1. Queue-pressure artifact. E14: top-decile lock rate / universe lock rate.
QUEUE_RATIO = 3.03
QUEUE_RATIO_TOLERANCE = 0.15

# 2. Cost failure. E13/E14: the h=1 top-decile excess against the round trip.
# The DIRECTION is what must hold -- the edge is smaller than the cost of
# capturing it -- so this anchor is asserted as an inequality plus a sanity band
# on the excess itself, not as a point estimate with a tolerance.
H1_EXCESS_PCT = 0.5496
H1_EXCESS_TOLERANCE = 0.05
ROUND_TRIP_PCT = 1.04

# 3. Holding-period execution failure. E15 arm A at the traded horizon.
REQUESTED_H20_PCT = 3.17
REALISED_H20_PCT = 0.59
SHORTFALL_TOLERANCE_PP = 0.20

# 4. Tail performance. E11: Alpha Core against 30 random-ranking controls.
MAX_PERCENTILE = 10.0

# 5. The reference IC curve, clean window, h = 1 / 5 / 10 / 20.
IC_CURVE = {1: 0.10492, 5: 0.05101, 10: 0.03003, 20: 0.01456}
IC_TOLERANCE = 0.002

# E14/E15 measurement parameters. These are not free choices -- they are what
# the recorded numbers were measured with, and changing one invalidates the
# comparison rather than improving it.
MIN_NAMES_DECILE = 50
BASKET = 10
TRADED_HORIZON = 20


class FixtureFailure(Exception):
    """The platform can no longer rediscover a known failure."""


def _expected():
    with open(EXPECTED_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _spearman(x, y):
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


def _check(name, measured, expected, tolerance, note=""):
    """One anchor, judged two-sided."""

    if measured is None or not np.isfinite(measured):
        # A check that could not be computed is a FAILED check, never a skipped
        # one, and it gets a direction like any other failure. An anchor that
        # goes quiet is usually a detector that stopped detecting, which is the
        # favourable-looking failure this fixture exists to catch.
        return {"check": name, "measured": None, "expected": expected,
                "tolerance": tolerance, "passed": False,
                "note": "could not be computed -- " + (note or "no value"),
                "direction": "NOT MEASURED -- the anchor produced no value at "
                             "all. Something stopped measuring rather than "
                             "something changing."}

    delta = measured - expected

    passed = bool(abs(delta) <= tolerance)

    return {
        "check": name,
        "measured": round(float(measured), 5),
        "expected": expected,
        "tolerance": tolerance,
        "delta": round(float(delta), 5),
        "passed": passed,
        # Named so a failure report says which way it moved. "Alpha Core got
        # better" is the reading that most needs to be unmissable.
        "direction": ("within tolerance" if passed
                      else ("MOVED FAVOURABLY -- find what stopped detecting "
                            "the failure" if _favourable(name, delta)
                            else "moved unfavourably")),
        "note": note,
    }


# For each anchor, does a POSITIVE delta mean Alpha Core looks better than
# recorded. Spelled out per check because "bigger is better" is wrong for three
# of the five and getting it backwards would put the alarming message on the
# harmless failure.
_BETTER_WHEN_HIGHER = {
    "queue_ratio": False,        # more queue exposure = more artifact
    "h1_excess": True,           # a bigger edge looks better
    "requested_h20": True,
    "realised_h20": True,        # realising more of the request looks better
    "tail_percentile": True,     # a higher percentile looks better
}


def _favourable(name, delta):
    key = name.split("@")[0]
    better_high = _BETTER_WHEN_HIGHER.get(key)
    if better_high is None:
        # The IC curve: a larger IC at any horizon reads as a stronger signal.
        better_high = True
    return bool(delta > 0) == bool(better_high)


# ==============================================================
# THE FIVE MEASUREMENTS
# ==============================================================

def measure_ic_curve(panel):
    """Anchor 5. Mean cross-sectional IC at h = 1 / 5 / 10 / 20."""

    out = {}

    for horizon in sorted(IC_CURVE):
        column = "fwd_%d" % horizon
        values = []
        for _, block in panel.groupby("date", sort=True):
            good = block[["alpha", column]].dropna()
            if len(good) < MIN_NAMES_DECILE:
                continue
            ic = _spearman(good["alpha"], good[column])
            if np.isfinite(ic):
                values.append(ic)
        out[horizon] = float(np.mean(values)) if values else None

    return out


def measure_queue_ratio(panel):
    """Anchor 1. Top-decile lock rate against the universe rate, per date.

    Averaged over dates and then divided, which is E14's construction. Pooling
    every name and dividing once would weight a date with 700 scoreable names
    seventy times a date with ten, and the decision dates are not equal-sized.
    """

    top_rates, universe_rates = [], []

    for _, block in panel.groupby("date", sort=True):

        good = block[block["alpha"].notna()]

        n = len(good)

        if n < MIN_NAMES_DECILE:
            continue

        k = max(1, int(round(n / 10.0)))

        order = np.argsort(-good["alpha"].to_numpy(float), kind="stable")

        top = good.iloc[order[:k]]

        top_rates.append(float(top["next_locked_up"].astype(bool).mean()))
        universe_rates.append(float(good["next_locked_up"].astype(bool).mean()))

    if not universe_rates:
        return {"ratio": None, "top_pct": None, "universe_pct": None,
                "dates": 0, "no_locks_detected": False}

    top = float(np.mean(top_rates)) * 100.0
    universe = float(np.mean(universe_rates)) * 100.0

    return {
        "ratio": (top / universe) if universe else None,
        "top_pct": top,
        "universe_pct": universe,
        "dates": len(top_rates),
        # 2.56% of this universe is limit-locked on any given session. A panel
        # in which NOTHING is locked has not discovered a calmer market; it has
        # lost the detector. Distinguished from a genuinely undefined ratio so
        # the failure report can say which happened.
        "no_locks_detected": bool(universe == 0.0),
    }


def measure_h1_excess(panel):
    """Anchor 2. Top-decile h=1 return minus the cross-sectional mean."""

    excess = []

    for _, block in panel.groupby("date", sort=True):

        good = block[["alpha", "fwd_1"]].dropna()

        n = len(good)

        if n < MIN_NAMES_DECILE:
            continue

        k = max(1, int(round(n / 10.0)))

        order = np.argsort(-good["alpha"].to_numpy(float), kind="stable")

        values = good["fwd_1"].to_numpy(float)

        excess.append(float(values[order[:k]].mean()) - float(values.mean()))

    if not excess:
        return {"excess_pct": None, "dates": 0}

    array = np.asarray(excess, float)

    sd = array.std(ddof=1) if len(array) > 1 else 0.0

    return {
        "excess_pct": float(array.mean()),
        "dates": len(array),
        "t_stat": (float(array.mean() / (sd / math.sqrt(len(array))))
                   if sd > 0 else None),
        "round_trip_pct": ROUND_TRIP_PCT,
        "clears_round_trip": bool(array.mean() > ROUND_TRIP_PCT),
    }


def cash_per_period(horizon):
    """What an unfilled slot earns over `horizon` sessions.

    NOT zero, and the difference is not cosmetic. Iran's policy rate is 23%, so
    twenty sessions of idle cash is +1.74% -- and with roughly 15% of the book
    unfilled that credit is +0.26pp of the realised excess. Setting it to zero
    reports a realised +0.33% instead of +0.59%, which would make the execution
    failure look nearly twice as severe as it is and put the fixture out of
    agreement with E15 for a reason that has nothing to do with execution.

    The same arithmetic as `experiment_15_execution_aware.cash_per_period`,
    reproduced rather than imported: that module reads 1.6M bars at import time
    through its own module-level constants, and a fixture that runs in seconds
    cannot depend on it.
    """

    import config

    return ((1.0 + config.RISK_FREE_RATE)
            ** (horizon / float(config.TRADING_DAYS_PER_YEAR)) - 1.0) * 100.0


def measure_holding_period(panel, cash=None):
    """Anchor 3. What the book requests at h=20 against what it receives.

    An unbuyable slot earns cash and is NOT refilled with the next name down.
    That single rule is the difference between +3.17% and +0.59%, and a fixture
    that quietly refilled would report no execution failure at all.
    """

    cash_per_slot = (cash_per_period(TRADED_HORIZON) if cash is None
                     else float(cash))

    rows = []

    for _, block in panel.groupby("date", sort=True):

        pool = block.dropna(subset=["alpha", "fwd_%d" % TRADED_HORIZON])

        if len(pool) < MIN_NAMES_DECILE:
            continue

        picks = pool.sort_values("alpha", ascending=False).head(BASKET)

        if len(picks) < BASKET:
            continue

        column = "fwd_%d" % TRADED_HORIZON

        market = float(pool[column].mean())

        unbuyable = picks["next_unbuyable"].astype(bool).to_numpy()

        filled = picks[~unbuyable]

        rows.append({
            "market": market,
            "requested": float(picks[column].mean()),
            "realised": float((filled[column].sum()
                               + unbuyable.sum() * cash_per_slot)
                              / len(picks)),
            "fill_rate": float((~unbuyable).mean()),
        })

    if not rows:
        return {"requested_excess_pct": None, "realised_excess_pct": None,
                "dates": 0}

    frame = pd.DataFrame(rows)

    requested = float((frame["requested"] - frame["market"]).mean())
    realised = float((frame["realised"] - frame["market"]).mean())

    return {
        "dates": int(len(frame)),
        "requested_excess_pct": requested,
        "realised_excess_pct": realised,
        "shortfall_pct": requested - realised,
        "shortfall_share": (realised / requested) if requested else None,
        "fill_rate_pct": float(frame["fill_rate"].mean() * 100.0),
        "cash_per_slot_pct": cash_per_slot,
    }


def measure_tail_percentile(distribution):
    """Anchor 4. Where Alpha Core sits in its own random-control distribution.

    Recomputed from E11's frozen arms rather than read back: the percentile is
    the statistic under test, and reading it from the record would test nothing.
    """

    treatment = float(distribution["treatment_cagr_pct"])

    controls = np.asarray(distribution["control_cagr_pct"], float)

    controls = controls[np.isfinite(controls)]

    if len(controls) < 10:
        return {"percentile": None, "controls": int(len(controls))}

    return {
        "percentile": float((controls < treatment).mean() * 100.0),
        "empirical_p": float((controls >= treatment).mean()),
        "treatment_cagr_pct": treatment,
        "control_mean_cagr_pct": float(controls.mean()),
        "controls": int(len(controls)),
    }


# ==============================================================
# THE FIXTURE
# ==============================================================

def check_frozen():
    """Alpha Core has not been edited."""

    expected = _expected()

    recorded = expected.get("alpha_core_sha256")

    actual = _sha256_file(STRATEGY_PATH)

    return {
        "check": "alpha_core_frozen",
        "measured": actual[:16],
        "expected": (recorded or "UNPINNED")[:16],
        "passed": bool(recorded and actual == recorded),
        "note": ("backtesting/alpha_core.py has been modified. Alpha Core is "
                 "frozen: it is a regression fixture, not a strategy to "
                 "improve. If the change is deliberate, re-pin "
                 "alpha_core_sha256 and say why in the commit."
                 if recorded and actual != recorded else ""),
        "direction": None,
    }


def run(panel=None, full=False, verbose=True):
    """Every anchor, recomputed. Returns a report; raises nothing."""

    from sirat.fixtures import build_panel

    def say(message):
        if verbose:
            print(message, flush=True)

    expected = _expected()

    if full:
        say("TIER 2 -- rebuilding the panel from sirat_market.db")
        panel = build_panel.build(verbose=verbose)
        fresh = build_panel.panel_hash(panel)
        frozen = expected.get("panel_sha256")
        panel_check = {
            "check": "panel_matches_database",
            "measured": fresh[:16],
            "expected": (frozen or "UNPINNED")[:16],
            "passed": bool(frozen and fresh == frozen),
            "note": ("the frozen panel no longer matches what the database "
                     "produces" if frozen and fresh != frozen else ""),
            "direction": None,
        }
    else:
        panel = build_panel.load() if panel is None else panel
        panel_check = None

    checks = [check_frozen()]

    if panel_check:
        checks.append(panel_check)

    # ---- 5. IC curve ------------------------------------------------------
    curve = measure_ic_curve(panel)

    for horizon in sorted(IC_CURVE):
        checks.append(_check("ic@h%d" % horizon, curve.get(horizon),
                             IC_CURVE[horizon], IC_TOLERANCE,
                             note="reference IC curve, clean window"))

    # ---- 1. queue ratio ---------------------------------------------------
    queue = measure_queue_ratio(panel)

    if queue.get("no_locks_detected"):
        checks.append({
            "check": "queue_ratio",
            "measured": None,
            "expected": QUEUE_RATIO,
            "tolerance": QUEUE_RATIO_TOLERANCE,
            "passed": False,
            "note": "top-decile lock rate / universe lock rate at t+1",
            "direction": (
                "MOVED FAVOURABLY -- not one limit-locked bar was found "
                "anywhere in %d dates. The queue artifact has not shrunk, it "
                "has vanished, which on this market means the detector broke. "
                "2.56%% of the universe is normally locked." % queue["dates"]),
        })
    else:
        checks.append(_check(
            "queue_ratio", queue["ratio"], QUEUE_RATIO, QUEUE_RATIO_TOLERANCE,
            note="top-decile lock rate / universe lock rate at t+1"))

    # ---- 2. cost failure --------------------------------------------------
    cost = measure_h1_excess(panel)

    checks.append(_check("h1_excess", cost["excess_pct"], H1_EXCESS_PCT,
                         H1_EXCESS_TOLERANCE,
                         note="top-decile h=1 excess, in percent"))

    # The direction check, which is the anchor the brief actually specifies.
    # Stated as its own check so a build that goes red says "the cost failure
    # stopped reproducing" rather than "a number moved".
    excess = cost["excess_pct"]

    # This anchor is a SIGN condition, not a tolerance band, so it needs its
    # own direction message -- and it is the one that most needs one. It
    # carries the single conclusion the whole programme rests on: the edge is
    # smaller than the cost of capturing it. A band-based check would say
    # "MOVED FAVOURABLY" automatically; without this, the most important
    # failure in the fixture would be the only one that went unlabelled.
    if excess is None:
        cost_direction = ("NOT MEASURED -- the top-decile excess produced no "
                          "value at all.")
    elif excess >= ROUND_TRIP_PCT:
        cost_direction = (
            "MOVED FAVOURABLY -- the edge now CLEARS the %.2f%% round trip "
            "(%.4f%%), so Alpha Core has become economic. This is the "
            "conclusion the entire programme rests on. Find what changed "
            "before believing it." % (ROUND_TRIP_PCT, excess))
    elif excess <= 0.0:
        cost_direction = (
            "moved unfavourably -- the top-decile excess is no longer positive "
            "(%.4f%%). The cost conclusion technically still holds, but for a "
            "different reason than the one on record, and that needs "
            "explaining too." % excess)
    else:
        cost_direction = None

    checks.append({
        "check": "cost_failure_direction",
        "measured": (None if excess is None else round(excess, 5)),
        "expected": "excess < round trip (%.2f%%)" % ROUND_TRIP_PCT,
        "passed": bool(excess is not None and 0.0 < excess < ROUND_TRIP_PCT),
        "note": ("The negative cost impact direction must remain: a per-period "
                 "edge smaller than the round trip it pays."),
        "direction": cost_direction,
    })

    # ---- 3. holding period ------------------------------------------------
    holding = measure_holding_period(panel)

    checks.append(_check("requested_h20", holding["requested_excess_pct"],
                         REQUESTED_H20_PCT, SHORTFALL_TOLERANCE_PP,
                         note="what the unfiltered book asks for at h=20"))

    checks.append(_check("realised_h20", holding["realised_excess_pct"],
                         REALISED_H20_PCT, SHORTFALL_TOLERANCE_PP,
                         note="what it receives once unbuyable slots sit in cash"))

    # ---- 4. tail ----------------------------------------------------------
    tail = measure_tail_percentile(expected["e11_distribution"])

    checks.append({
        "check": "tail_percentile",
        "measured": (None if tail["percentile"] is None
                     else round(tail["percentile"], 2)),
        "expected": "<= %.1fth percentile" % MAX_PERCENTILE,
        "passed": bool(tail["percentile"] is not None
                       and tail["percentile"] <= MAX_PERCENTILE),
        "note": "Alpha Core against %d random-ranking controls" % tail["controls"],
        "direction": (None if tail["percentile"] is None
                      or tail["percentile"] <= MAX_PERCENTILE
                      else "MOVED FAVOURABLY -- Alpha Core no longer sits in "
                           "the tail of its own null"),
    })

    failed = [c for c in checks if not c["passed"]]

    report = {
        "fixture": "alpha_core_v1",
        "tier": 2 if full else 1,
        "panel_rows": int(len(panel)),
        "panel_dates": int(panel["date"].nunique()),
        "checks": checks,
        "failed": [c["check"] for c in failed],
        "passed": not failed,
        "measurements": {
            "ic_curve": {k: (None if v is None else round(v, 5))
                         for k, v in curve.items()},
            "queue": {k: (None if v is None else
                          (round(v, 4) if isinstance(v, float) else v))
                      for k, v in queue.items()},
            "h1_excess": {k: (None if v is None else
                              (round(v, 4) if isinstance(v, float) else v))
                          for k, v in cost.items()},
            "holding_period": {k: (None if v is None else
                                   (round(v, 4) if isinstance(v, float) else v))
                               for k, v in holding.items()},
            "tail": {k: (None if v is None else
                         (round(v, 4) if isinstance(v, float) else v))
                     for k, v in tail.items()},
        },
    }

    if verbose:
        _print(report)

    return report


def _print(report):
    print("=" * 74)
    print("ALPHA CORE REGRESSION FIXTURE -- tier %d" % report["tier"])
    print("=" * 74)
    print("  %d panel rows over %d decision dates"
          % (report["panel_rows"], report["panel_dates"]))
    print()

    for check in report["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        expected = check.get("expected")
        if isinstance(expected, float):
            expected = "%.5f +/- %s" % (expected, check.get("tolerance"))
        print("  [%s] %-24s measured %-12s expected %s"
              % (mark, check["check"], check.get("measured"), expected))
        if not check["passed"]:
            if check.get("direction"):
                print("         -> %s" % check["direction"])
            if check.get("note"):
                print("         -> %s" % check["note"])

    print()
    print("  %s" % ("ALL ANCHORS REPRODUCED" if report["passed"]
                    else "FIXTURE FAILED: " + ", ".join(report["failed"])))
    print("=" * 74)


def assert_passes(full=False, verbose=False):
    """Raise on failure. What CI and the test suite call."""

    report = run(full=full, verbose=verbose)

    if not report["passed"]:
        lines = ["the platform can no longer rediscover Alpha Core's failures:"]
        for check in report["checks"]:
            if check["passed"]:
                continue
            lines.append("  %s: measured %s, expected %s%s"
                         % (check["check"], check.get("measured"),
                            check.get("expected"),
                            " -- " + check["direction"]
                            if check.get("direction") else ""))
        raise FixtureFailure("\n".join(lines))

    return report


if __name__ == "__main__":
    import sys

    full = "--full" in sys.argv

    sys.exit(0 if run(full=full)["passed"] else 1)
