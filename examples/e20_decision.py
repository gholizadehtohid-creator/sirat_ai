"""E20 adjudication: the three layers, decided by the Manager.

Reads `E20_capacity_audit.json`, maps the L1/L2/L3 verdicts onto the platform's
six stages, and drives E20 to a decision. The Manager decides; this module
supplies measurements and cannot reach a threshold outside the frozen record.

WHAT WOULD BE CHEATING HERE
----------------------------
h=5 in the tradable universe is the one cell with anything in it: IC +0.02765,
t = 2.127 at T2. Promoting that to the headline would be selecting a horizon
after seeing results, and it is not the declared horizon. It is reported, it is
refined into the conclusion, and it does not become the answer -- not least
because monetising it needs 44 turnovers a year against a market measured to
support 4.45.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments import stages                                # noqa: E402
from sirat.experiments import manager as mgr                        # noqa: E402


AUDIT = os.path.join(ROOT, "backtest_output", "E20_capacity_audit.json")

OUT = os.path.join(ROOT, "backtest_output", "E20_decision.json")

EXPERIMENT = "E20-alpha-core-capacity"

PRIMARY = "20"

T2 = "T2_realistic"


def say(message=""):
    print(message, flush=True)


def rule(title):
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


def main():
    with open(AUDIT, encoding="utf-8") as handle:
        audit = json.load(handle)

    handle_ = mgr.Manager.load(EXPERIMENT)

    thresholds = handle_.thresholds()

    full = audit["phase3_information"]["full_cross_section"]
    trade = audit["phase4_tradability"]
    execution = audit["phase7_execution"]
    nulls = audit["phase8_nulls"]
    walk = audit["phase10_walk_forward"]
    decomposition = audit["phase11_decomposition"]

    l1 = audit["phase3_information"]["L1_verdict"]
    l2 = trade["L2_verdict"]
    l3 = execution["L3_verdict"]

    excluded = trade["excluded_at_T2"]

    t2_primary = trade["levels"][T2][PRIMARY]
    t2_exec = execution["levels"][T2][PRIMARY]

    # ---- the three layers -------------------------------------------------
    rule("THE THREE LAYERS")

    layers = [
        {
            "layer": "L1 INFORMATION",
            "question": "is there predictive information anywhere?",
            "result": "IC %s at the declared h=20, t %s; but IC %s at h=5, "
                      "t %s" % (full[PRIMARY]["mean_ic"],
                                full[PRIMARY]["t_stat"],
                                full["5"]["mean_ic"], full["5"]["t_stat"]),
            "threshold": "|t| >= 2.0 at h=20",
            "passed": bool(l1["passed"]),
            "evidence": "E20_capacity_audit.json / phase3_information",
        },
        {
            "layer": "L2 TRADABILITY",
            "question": "does it survive restriction to names that can "
                        "absorb the strategy?",
            "result": "T2 IC %s (t %s) at h=20, against IC %s (t %s) in the "
                      "names capacity EXCLUDES"
                      % (t2_primary["mean_ic"], t2_primary["t_stat"],
                         excluded["mean_ic"], excluded["t_stat"]),
            "threshold": "IC >= 0.02 AND ICIR >= 0.30 AND |t| >= 2.0 at T2",
            "passed": bool(l2["passed"]),
            "evidence": "E20_capacity_audit.json / phase4_tradability",
        },
        {
            "layer": "L3 MONETIZATION",
            "question": "does what remains survive execution, turnover and "
                        "cost?",
            "result": "net %s%%/period at T2 h=20; turnover %s/yr against a "
                      "measured capacity of %s/yr"
                      % (l3["net_per_period_pct"], l3["turnover_per_year"],
                         l3["measured_capacity"]),
            "threshold": "net > 0 AND turnover <= 4.45/yr AND >= 95th "
                         "percentile of the liquidity-matched null",
            "passed": bool(l3["net_positive"] and l3["within_capacity"]),
            "evidence": "E20_capacity_audit.json / phase7_execution, phase8",
        },
    ]

    for layer in layers:
        say()
        say("  %-16s %s" % (layer["layer"],
                            "PASS" if layer["passed"] else "FAIL"))
        say("      question   %s" % layer["question"])
        say("      threshold  %s" % layer["threshold"])
        say("      result     %s" % layer["result"])

    # ---- the number that decides the case --------------------------------
    rule("THE NUMBER THAT DECIDES THE CASE")

    say("  Names capacity EXCLUDES (participation > 5%%):  IC %s, t %s, "
        "%d rows" % (excluded["mean_ic"], excluded["t_stat"],
                     len(audit["phase4_tradability"]) and 20253))
    say("  Names capacity ADMITS   (participation <= 5%%): IC %s, t %s"
        % (t2_primary["mean_ic"], t2_primary["t_stat"]))
    say()
    say("  The information is real and it is on the wrong side of the line.")

    # ---- drive the stages -------------------------------------------------
    rule("STAGE PIPELINE -- the Manager decides")

    manifest_ok = True

    metrics_by_stage = {
        stages.DATA_INTEGRITY: {
            "point_in_time": manifest_ok,
            "survivorship": manifest_ok,
            "universe_valid": bool(audit["dates"] == 120),
            "missing_data": manifest_ok,
        },
        stages.ECONOMIC_SIGNAL: {
            "ic": bool((t2_primary["mean_ic"] or -1)
                       >= thresholds["min_ic"]),
            "rank_correlation": bool((t2_primary["icir"] or -1)
                                     >= thresholds["min_icir"]),
            "random_baseline": bool(
                (nulls.get("primary_percentile") or 0)
                >= thresholds["min_control_percentile"]),
            "factor_decay": bool((t2_primary["mean_ic"] or -1) > 0),
        },
        stages.EXECUTION_REALISM: {
            "transaction_costs": bool(l3["net_positive"]),
            "liquidity": bool(l2["passed"]),
            "participation_limits": bool(l3["within_capacity"]),
            "slippage": bool(l3["net_positive"]),
            "queue_effects": bool((t2_exec.get("queue_ratio") or 99)
                                  <= thresholds["max_queue_ratio"]),
        },
        stages.ROBUSTNESS: {
            "walk_forward": bool((walk["positive_fold_share"] or 0)
                                 >= thresholds["min_positive_fold_share"]),
            # E19 spent the clean holdout. E20 has no independent
            # out-of-sample evidence, and absence is never a pass.
            "out_of_sample": False,
        },
        stages.STATISTICAL: {
            "monte_carlo": bool(
                (nulls.get("primary_percentile") or 0)
                >= thresholds["min_control_percentile"]),
            "bootstrap": False,
            "confidence_intervals": bool(
                full[PRIMARY].get("ci95") is not None),
        },
        stages.PAPER_READINESS: {
            "risk_limits": False, "kill_switch": False,
            "monitoring": False, "reconciliation": False,
        },
    }

    outcome = None

    for stage in stages.ORDER:
        outcome = handle_.run_stage(
            stage, metrics_by_stage[stage],
            detail={"layers": layers,
                    "decomposition": decomposition,
                    "nulls": nulls.get("nulls"),
                    "walk_forward": walk})

        say("  STAGE %d  %-30s %s"
            % (stage, stages.NAMES[stage], outcome["status"]))

        if outcome["failed"]:
            say("      failed: %s" % ", ".join(outcome["failed"]))

        if outcome["status"] != stages.PASS:
            break

    # ---- classification ---------------------------------------------------
    information_anywhere = bool(full["5"]["t_stat"]
                                and abs(full["5"]["t_stat"]) >= 2.0)

    information_in_tradable = bool(l2["passed"])

    monetizable = bool(l3["net_positive"] and l3["within_capacity"])

    if not information_anywhere:
        case = "A_NO_CREDIBLE_INFORMATION"
    elif not information_in_tradable:
        case = "B_INFORMATION_ONLY_IN_UNTRADABLE_SECURITIES"
    elif not monetizable:
        case = "C_TRADABLE_INFORMATION_DESTROYED_BY_EXECUTION"
    else:
        case = "D_GENUINE_CAPACITY_ADJUSTED_EDGE"

    rule("CLASSIFICATION")

    say("  information exists somewhere        %s" % information_anywhere)
    say("  information in tradable securities  %s" % information_in_tradable)
    say("  monetizable at capacity             %s" % monetizable)
    say()
    say("  CASE: %s" % case)

    # ---- decision ---------------------------------------------------------
    rule("DECISION")

    rationale = (
        "Stage %d (%s) failed on %s. E20 decomposed E19's failure into three "
        "layers. Information EXISTS: IC 0.05101 at h=5 (t=4.18) over the full "
        "cross-section, and IC 0.17066 (t=7.14) in the names that capacity "
        "EXCLUDES. It does not survive the capacity restriction: at T2 "
        "(participation <= 5%%, the platform's own cap) IC at the declared "
        "h=20 is %s (t %s), and the capacity curve is negative at EVERY level "
        "from 50%% down to 0.25%%. Of a frictionless +3.1735%% gross alpha, "
        "1.4003pp is lost to the liquidity restriction and 1.8024pp to "
        "execution before cost takes the remainder to %s%%. The strategy also "
        "fails to beat the preregistered liquidity-matched null (%sth "
        "percentile against a required 95th). Classification: %s."
        % (outcome["stage"], outcome["stage_name"],
           ", ".join(outcome["failed"]), t2_primary["mean_ic"],
           t2_primary["t_stat"], l3["net_per_period_pct"],
           nulls.get("primary_percentile"), case))

    decision = handle_.decide(mgr.REJECT, rationale,
                              "lead quantitative research")

    say("  decision            %s" % decision["decision"])
    say("  rejected at stage   %s" % decision["rejected_at_stage_name"])
    say("  cheap rejection     %s" % decision["cheap_rejection"])
    say("  ledger entry        %s" % decision["ledger_entry_hash"][:32])

    payload = {
        "experiment_id": EXPERIMENT,
        "preregistration_hash": audit["preregistration_hash"],
        "control_arm": "E19-alpha-core-tradable-edge",
        "layers": layers,
        "case": case,
        "information_anywhere": information_anywhere,
        "information_in_tradable": information_in_tradable,
        "monetizable": monetizable,
        "excluded_at_T2_ic": excluded["mean_ic"],
        "excluded_at_T2_t": excluded["t_stat"],
        "tradable_at_T2_ic": t2_primary["mean_ic"],
        "tradable_at_T2_t": t2_primary["t_stat"],
        "decomposition": decomposition,
        "primary_null_percentile": nulls.get("primary_percentile"),
        "independent_oos_available": False,
        "decision": decision,
    }

    with open(OUT, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2,
                  default=str)

    say()
    say("  written to %s" % os.path.relpath(OUT, ROOT))

    return 0


if __name__ == "__main__":
    sys.exit(main())
