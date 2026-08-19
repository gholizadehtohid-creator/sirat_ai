"""ALPHA CORE — ADVERSARIAL REGRESSION FIXTURE.

ALPHA CORE IS NOT A STRATEGY. IT IS A TEST.
-------------------------------------------
It was rejected across Experiments 11, 13, 14 and 15 with a complete causal
account. Its numbers are now the platform's calibration weights: a governance
layer that cannot re-derive its own known negative has no standing to judge a
new idea, so the build fails when it cannot.

THE CHECK IS TWO-SIDED, AND THAT IS THE POINT
---------------------------------------------
A one-sided fixture only catches the platform getting worse. The failure mode
that actually threatens a research programme is the opposite: a change that
quietly makes a rejected strategy look good again -- a loosened cost model, a
fill assumption that stops modelling queues, a survivorship leak.

So an improvement outside tolerance fails the build exactly as loudly as a
degradation, and the message says so. If Alpha Core starts looking better,
someone must explain why before the number is accepted.

    1  queue ratio          3.03x     +/- 0.15
    2  cost failure         h=1 net edge must stay NEGATIVE
    3  execution shortfall  +3.17% requested -> +0.59% realised, +/- 0.20pp
    4  tail performance     percentile vs random <= 10th
    5  IC curve             0.10492 / 0.05101 / 0.03003 / 0.01456  +/- 0.002
"""

import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

OUT_DIR = os.path.join(ROOT, "backtest_output")

TOLERANCE_QUEUE = 0.15
TOLERANCE_EXECUTION_PP = 0.20
TOLERANCE_IC = 0.002
PERCENTILE_CEILING = 10.0

REFERENCE = {
    "queue_ratio": 3.03,
    "requested_excess_h20": 3.1735,
    "realised_excess_h20": 0.5930,
    "percentile_vs_random": 3.3,
    "ic_curve": {"h1": 0.10492, "h5": 0.05101,
                 "h10": 0.03003, "h20": 0.01456},
}

SOURCES = {
    "queue": "experiment14_queue_artifact.json",
    "decay": "alpha_decay_analysis.json",
    "execution": "experiment15_execution_aware.json",
    "production": "experiment5b_production.json",
}


class FixtureUnavailable(Exception):
    """A source artifact is missing. Not a pass -- the fixture cannot run."""


def _load(key):
    path = os.path.join(OUT_DIR, SOURCES[key])
    if not os.path.exists(path):
        raise FixtureUnavailable(
            "%s is missing; the regression fixture cannot certify the "
            "platform without it" % SOURCES[key])
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _direction(observed, reference, tolerance, label):
    """Classify a deviation, naming which way it went."""

    delta = observed - reference

    if abs(delta) <= tolerance:
        return {"check": label, "status": "PASS", "observed": observed,
                "reference": reference, "delta": round(delta, 5)}

    # "Better" means the rejected strategy looks less rejected.
    return {
        "check": label, "status": "FAIL", "observed": observed,
        "reference": reference, "delta": round(delta, 5),
        "tolerance": tolerance,
        "direction": "IMPROVED" if delta > 0 else "DEGRADED",
    }


def check_queue_ratio():
    doc = _load("queue")
    ratio = doc["queue_exposure"]["ratio"]
    got = _direction(ratio, REFERENCE["queue_ratio"], TOLERANCE_QUEUE,
                     "queue_ratio")
    if got["status"] == "FAIL" and got["direction"] == "DEGRADED":
        got["note"] = ("queue exposure fell: the detector may have stopped "
                       "seeing locks, or the cost/fill model changed")
    elif got["status"] == "FAIL":
        got["note"] = "queue exposure rose beyond tolerance"
    return got


def check_cost_failure():
    """The h=1 edge must remain unable to pay its own round trip."""

    doc = _load("decay")
    row = doc["feasibility"]["h1"]

    net = row["net_per_period_pct"]

    ok = net < 0

    return {
        "check": "cost_failure_direction", "status": "PASS" if ok else "FAIL",
        "observed": net, "reference": "must be negative",
        "alpha_pct": row["alpha_pct"], "cost_pct": row["cost_pct"],
        "note": None if ok else
        ("the h=1 net edge turned POSITIVE. Alpha Core cannot have become "
         "tradable; the cost model has almost certainly been weakened"),
    }


def check_execution_shortfall():
    doc = _load("execution")
    arm = doc["arms"]["A"]["h20"]

    results = [
        _direction(arm["requested_excess_pct"],
                   REFERENCE["requested_excess_h20"],
                   TOLERANCE_EXECUTION_PP, "requested_excess_h20"),
        _direction(arm["realised_excess_pct"],
                   REFERENCE["realised_excess_h20"],
                   TOLERANCE_EXECUTION_PP, "realised_excess_h20"),
    ]

    for row in results:
        if row["status"] == "FAIL" and row.get("direction") == "IMPROVED":
            row["note"] = ("realised return improved: check whether fills, "
                           "queues or participation limits are still modelled")

    return results


def check_tail_performance():
    doc = _load("production")
    percentile = doc["percentile"]

    ok = percentile <= PERCENTILE_CEILING

    return {
        "check": "percentile_vs_random", "status": "PASS" if ok else "FAIL",
        "observed": percentile, "reference": "<= %.1f" % PERCENTILE_CEILING,
        "note": None if ok else
        ("Alpha Core now ranks above the 10th percentile of random. It has "
         "not improved; something in selection, exposure or costs has"),
    }


def check_ic_curve():
    doc = _load("decay")
    curve = doc["decay_curve"]

    results = []

    for key, reference in REFERENCE["ic_curve"].items():
        if key not in curve:
            results.append({"check": "ic_%s" % key, "status": "FAIL",
                            "note": "horizon missing from the decay curve"})
            continue
        got = _direction(curve[key]["mean_ic"], reference, TOLERANCE_IC,
                         "ic_%s" % key)
        if got["status"] == "FAIL" and got.get("direction") == "IMPROVED":
            got["note"] = ("IC rose beyond tolerance: suspect a leak, a "
                           "contaminated window, or a changed universe")
        results.append(got)

    return results


def run():
    """Every check. Returns (ok, results)."""

    results = []

    results.append(check_queue_ratio())
    results.append(check_cost_failure())
    results.extend(check_execution_shortfall())
    results.append(check_tail_performance())
    results.extend(check_ic_curve())

    ok = all(r["status"] == "PASS" for r in results)

    return ok, results


def main():
    print("=" * 74)
    print("ALPHA CORE REGRESSION FIXTURE — the platform must reject it again")
    print("=" * 74)

    try:
        ok, results = run()
    except FixtureUnavailable as exc:
        print("  FIXTURE UNAVAILABLE: %s" % exc)
        return 2

    for row in results:
        line = "  [%s] %-24s" % (row["status"], row["check"])
        if "observed" in row:
            line += " observed %-10s reference %s" % (
                row["observed"], row["reference"])
        print(line)
        if row.get("direction"):
            print("        direction: %s (delta %+.5f, tolerance %s)"
                  % (row["direction"], row["delta"], row.get("tolerance")))
        if row.get("note"):
            print("        %s" % row["note"])

    print("\n" + "=" * 74)
    if ok:
        print("RESULT: PASS — the platform still rediscovers every known failure")
    else:
        improved = [r for r in results if r.get("direction") == "IMPROVED"]
        print("RESULT: FAIL — the build is red")
        if improved:
            print("\n  %d check(s) moved in the IMPROVED direction." % len(improved))
            print("  Alpha Core has not got better. Something in the platform")
            print("  has stopped measuring a constraint. Explain before merging.")
    print("=" * 74)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
