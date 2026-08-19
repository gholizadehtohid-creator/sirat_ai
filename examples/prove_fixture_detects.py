"""Phase 4: prove the claim "if Alpha Core gets better, CI fails".

THE CLAIM BEING TESTED
----------------------
A regression fixture that only fires when results get WORSE is half a control.
Every time this project has seen a rejected result improve, the cause was a
defect rather than a discovery:

    a stale-bar trap that quietly removed the worst 33% of bars
    a shift(-h) that reached across a suspension and produced a -579% excess
    a close-fill assumption that reported ~62% CAGR
    four null execution counters read as "no queues were ever hit"

So the fixture's tolerances are two-sided, and this script demonstrates it by
breaking Alpha Core in the specific ways a defect would break it -- each one
making the strategy look BETTER -- and showing the build go red every time.

WHAT IS AND IS NOT PROVED
-------------------------
Proved: each of these seven corruptions is caught, and the failure report says
which direction the number moved.

Not proved: that every possible corruption is caught. No finite set of
injections can show that. What the two-sided band gives is the general
property -- any anchor that moves more than its tolerance fails, whichever way
it went -- and these seven show the property holds for the failure modes this
codebase has actually produced.

Nothing here writes to the repository. Every corruption is applied to an
in-memory copy of the frozen panel.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.fixtures import alpha_core, build_panel                  # noqa: E402


def disable_queue_detector(panel, expected):
    """Nothing is ever flagged as limit-locked."""
    panel = panel.copy()
    panel["next_locked_up"] = False
    return panel, expected


def weaken_queue_detector(panel, expected):
    """The detector still works, but misses two thirds of the top decile.

    A subtler failure than switching it off, and the one a real regression
    looks like: the ratio slides from 3.03 toward 1.0 rather than vanishing.
    """
    panel = panel.copy()
    ranked = panel.groupby("date")["alpha"].rank(pct=True)
    top = ranked >= 0.90
    # Clear two of every three locks in the top decile.
    drop = top & (panel.index % 3 != 0)
    panel.loc[drop, "next_locked_up"] = False
    return panel, expected


def refill_unbuyable_slots(panel, expected):
    """The fill model silently replaces an unbuyable name with the next one."""
    panel = panel.copy()
    panel["next_unbuyable"] = False
    return panel, expected


def leak_the_signal(panel, expected):
    """Forward returns nudged toward the score -- a leak, in a leak's shape."""
    panel = panel.copy()
    for horizon in (1, 5, 10, 20):
        column = "fwd_%d" % horizon
        panel[column] = panel[column] + (panel["alpha"] - 50.0) / 12.0
    return panel, expected


def inflate_the_edge(panel, expected):
    """The h=1 excess scaled up until it clears the round trip.

    This is the cost-failure direction inverting: the strategy stops being
    uneconomic, which is the single conclusion the programme rests on.
    """
    panel = panel.copy()
    ranked = panel.groupby("date")["alpha"].rank(pct=True)
    panel.loc[ranked >= 0.90, "fwd_1"] += 1.2
    return panel, expected


def improve_the_tail(panel, expected):
    """E11's treatment arm raised until it beats its own controls."""
    expected = copy.deepcopy(expected)
    expected["e11_distribution"]["treatment_cagr_pct"] = 40.0
    return panel, expected


def edit_alpha_core(panel, expected):
    """The strategy file itself is changed."""
    expected = copy.deepcopy(expected)
    expected["alpha_core_sha256"] = hashlib.sha256(
        b"an optimised alpha core").hexdigest()
    return panel, expected


CORRUPTIONS = [
    ("queue detector switched off", disable_queue_detector,
     "queue_ratio", "the artifact disappears entirely"),
    ("queue detector weakened", weaken_queue_detector,
     "queue_ratio", "the ratio slides toward 1.0"),
    ("unbuyable slots refilled", refill_unbuyable_slots,
     "realised_h20", "the execution shortfall closes"),
    ("forward returns leak the score", leak_the_signal,
     "ic@h20", "the signal survives to the traded horizon"),
    ("h=1 edge inflated past the round trip", inflate_the_edge,
     "cost_failure_direction", "the strategy becomes economic"),
    ("E11 treatment arm improved", improve_the_tail,
     "tail_percentile", "it leaves the tail of its own null"),
    ("backtesting/alpha_core.py edited", edit_alpha_core,
     "alpha_core_frozen", "the frozen strategy is no longer frozen"),
]


def say(message=""):
    print(message, flush=True)


def rule(title):
    say()
    say("=" * 76)
    say(title)
    say("=" * 76)


def run_with(panel, expected):
    """Run the fixture against a corrupted panel and/or expectations."""

    workdir = tempfile.mkdtemp(prefix="sirat_tamper_")

    path = os.path.join(workdir, "expected.json")

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(expected, handle, ensure_ascii=False)

    saved = alpha_core.EXPECTED_PATH

    try:
        alpha_core.EXPECTED_PATH = path
        return alpha_core.run(panel=panel, verbose=False)
    finally:
        alpha_core.EXPECTED_PATH = saved


def main():
    rule("PHASE 4 -- CAN THE FIXTURE BE FOOLED INTO PASSING?")

    if not os.path.exists(build_panel.PANEL_PATH):
        say("  the frozen panel is absent; rebuild it with")
        say("  python -m sirat.fixtures.build_panel")
        return 1

    panel = build_panel.load()

    baseline_expected = alpha_core._expected()

    # ---- the control -----------------------------------------------------
    rule("CONTROL -- the untouched panel")

    control = alpha_core.run(panel=panel, verbose=False)

    say("  checks        %d" % len(control["checks"]))
    say("  failed        %s" % (", ".join(control["failed"]) or "none"))
    say("  verdict       %s" % ("GREEN" if control["passed"] else "RED"))

    if not control["passed"]:
        say()
        say("  The control is already red. Fix that before reading anything "
            "below.")
        return 1

    # ---- the corruptions -------------------------------------------------
    rows = []

    for label, corrupt, anchor, effect in CORRUPTIONS:

        corrupted_panel, corrupted_expected = corrupt(
            panel, copy.deepcopy(baseline_expected))

        report = run_with(corrupted_panel, corrupted_expected)

        check = {c["check"]: c for c in report["checks"]}.get(anchor, {})

        detected = bool(not report["passed"])

        named = bool(anchor in report["failed"])

        direction = check.get("direction") or ""

        flagged_favourable = "FAVOURABLY" in direction

        rows.append({
            "label": label, "anchor": anchor, "effect": effect,
            "detected": detected, "named": named,
            "measured": check.get("measured"),
            "expected": check.get("expected"),
            "direction": direction,
            "all_failed": report["failed"],
            "flagged_favourable": flagged_favourable,
        })

        rule("CORRUPTION -- %s" % label)
        say("  intended effect   %s" % effect)
        say("  build             %s" % ("RED" if detected else "*** GREEN ***"))
        say("  anchors failed    %s" % ", ".join(report["failed"]))
        say("  %-17s measured %s against %s"
            % (anchor, check.get("measured"), check.get("expected")))

        if direction:
            say("  reported as       %s" % direction)

        if not detected:
            say()
            say("  *** THE FIXTURE PASSED A CORRUPTED PANEL. The two-sided")
            say("  *** guarantee does not hold and the platform is invalid.")

    # ---- the verdict -----------------------------------------------------
    rule("PHASE 4 VERDICT")

    say("  %-42s %-6s %-8s %s"
        % ("corruption", "build", "named", "flagged as favourable"))
    say("  " + "-" * 88)

    for row in rows:
        say("  %-42s %-6s %-8s %s"
            % (row["label"][:42], "RED" if row["detected"] else "GREEN",
               "yes" if row["named"] else "no",
               "yes" if row["flagged_favourable"] else
               ("n/a" if row["anchor"] == "alpha_core_frozen" else "no")))

    everything = all(r["detected"] and r["named"] for r in rows)

    say()

    if everything:
        say("  All %d corruptions were detected, and each was attributed to "
            "the" % len(rows))
        say("  anchor that should have caught it.")
        say()
        say("  PROVED: if Alpha Core becomes artificially better without a "
            "real reason,")
        say("  CI fails.")
    else:
        say("  NOT PROVED. At least one corruption survived.")

    # Six of the seven make Alpha Core look better and are reported as such;
    # the seventh is the frozen-file check, which has no direction because it
    # is not a measurement.
    favourable = [r for r in rows if r["anchor"] != "alpha_core_frozen"]

    say()
    say("  Of the %d measured corruptions, %d were explicitly labelled "
        "'MOVED FAVOURABLY'" % (len(favourable),
                                sum(1 for r in favourable
                                    if r["flagged_favourable"])))
    say("  in the failure report, so a reader cannot mistake an improvement "
        "for good news.")

    out = os.path.join(ROOT, "backtest_output", "fixture_tamper_proof.json")

    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"control": control["measurements"], "corruptions": rows,
                   "proved": everything}, handle, ensure_ascii=False,
                  indent=2, default=str)

    say()
    say("  evidence written to %s" % os.path.relpath(out, ROOT))

    return 0 if everything else 1


if __name__ == "__main__":
    sys.exit(main())
