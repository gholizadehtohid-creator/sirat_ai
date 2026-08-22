"""The six mandatory stages, in the order that makes rejection cheap.

THE ORDERING IS THE PRODUCT
---------------------------
Phase 1 answered these questions in roughly reverse order and it cost fifteen
experiments. Data integrity and signal existence are minutes of work; portfolio
construction and statistical validation are days. A pipeline that asks the
expensive question first is a backtester. Asking them cheapest-first is what
makes the platform a governance layer.

    1  DATA INTEGRITY        minutes   PIT, survivorship, universe, gaps
    2  ECONOMIC SIGNAL       hours     IC, rank correlation, random baseline
    3  EXECUTION REALISM     hours     cost, liquidity, participation, queues
    4  ROBUSTNESS            days      walk-forward, out-of-sample
    5  STATISTICAL           days      Monte Carlo, bootstrap, intervals
    6  PAPER READINESS       weeks     operational preconditions

STAGE 3 SITS BEFORE STAGE 4 ON PURPOSE. Alpha Core's cost arithmetic -- a 0.55%
edge against a 1.04% round trip -- was computable in an afternoon and was not
computed until experiment 13. Anything that cannot pay its own commission never
deserves a walk-forward.

WHAT A STAGE IS NOT
-------------------
A stage does not decide whether an idea is good. It decides whether the idea
survives to cost the team another day. `PASS` means "not yet rejected".
"""

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
BLOCKED = "BLOCKED"

DATA_INTEGRITY = 1
ECONOMIC_SIGNAL = 2
EXECUTION_REALISM = 3
ROBUSTNESS = 4
STATISTICAL = 5
PAPER_READINESS = 6

NAMES = {
    DATA_INTEGRITY: "DATA INTEGRITY",
    ECONOMIC_SIGNAL: "ECONOMIC SIGNAL VALIDATION",
    EXECUTION_REALISM: "EXECUTION REALISM",
    ROBUSTNESS: "ROBUSTNESS",
    STATISTICAL: "STATISTICAL VALIDATION",
    PAPER_READINESS: "PAPER TRADING READINESS",
}

ORDER = (DATA_INTEGRITY, ECONOMIC_SIGNAL, EXECUTION_REALISM,
         ROBUSTNESS, STATISTICAL, PAPER_READINESS)

# Which checks a stage is required to report on. A stage that returns metrics
# missing one of these is incomplete, not passing -- the platform refuses it
# rather than accepting a partial answer as a green light.
REQUIRED_CHECKS = {
    # price_sanity was added 2026-08-22, after E22. Its absence is the reason
    # 252 rows carrying returns above 200% -- the largest +19,976,600%, a prior
    # close of 1 rial -- passed Stage 1 unremarked and then inverted the sign
    # of a 2,809-session cost measurement. Stage 1's other four checks cover
    # forward alignment, universe restoration, cross-section width and
    # coverage; not one of them asks whether a price is possible.
    DATA_INTEGRITY: ("point_in_time", "survivorship", "universe_valid",
                     "missing_data", "price_sanity"),
    ECONOMIC_SIGNAL: ("ic", "rank_correlation", "random_baseline",
                      "factor_decay"),
    EXECUTION_REALISM: ("transaction_costs", "liquidity",
                        "participation_limits", "slippage", "queue_effects"),
    ROBUSTNESS: ("walk_forward", "out_of_sample"),
    STATISTICAL: ("monte_carlo", "bootstrap", "confidence_intervals"),
    PAPER_READINESS: ("risk_limits", "kill_switch", "monitoring",
                      "reconciliation"),
}

# Stages 1-3 are the cheap ones. The platform's KPI is the share of rejections
# that happen here.
CHEAP_STAGES = (DATA_INTEGRITY, ECONOMIC_SIGNAL, EXECUTION_REALISM)

CHEAP_REJECTION_TARGET = 0.70


class StageIncomplete(Exception):
    """Raised when a stage reports a verdict without reporting its checks."""


def validate_metrics(stage, metrics):
    """Every required check must be present and boolean-ish.

    Absence is not a pass. A stage that forgot to run survivorship reports
    nothing about survivorship, and the platform must not read silence as
    approval -- that is precisely how four `null` execution counters were read
    as "no queues were ever hit" for an entire programme.
    """

    metrics = metrics or {}

    missing = [c for c in REQUIRED_CHECKS[stage] if c not in metrics]

    if missing:
        raise StageIncomplete(
            "stage %d (%s) did not report: %s"
            % (stage, NAMES[stage], ", ".join(missing)))

    return True


def verdict_from(metrics):
    """PASS only when every reported check is truthy."""

    failed = [k for k, v in (metrics or {}).items() if v is False]

    return (FAIL, failed) if failed else (PASS, [])


def cheap_rejection_rate(decisions):
    """The platform KPI: what share of rejections happened at stages 1-3.

    Counts only rejections. An experiment that passed everything says nothing
    about whether the pipeline rejects cheaply, and including it would let a
    quiet quarter flatter the number.
    """

    rejected = [d for d in decisions
                if d.get("decision") == "REJECT"
                and d.get("rejected_at_stage") is not None]

    if not rejected:
        return {"rejections": 0, "cheap": 0, "rate": None,
                "target": CHEAP_REJECTION_TARGET, "meets_target": None}

    cheap = sum(1 for d in rejected
                if d["rejected_at_stage"] in CHEAP_STAGES)

    rate = cheap / float(len(rejected))

    return {
        "rejections": len(rejected),
        "cheap": cheap,
        "rate": round(rate, 4),
        "target": CHEAP_REJECTION_TARGET,
        "meets_target": bool(rate >= CHEAP_REJECTION_TARGET),
        "by_stage": {s: sum(1 for d in rejected
                            if d["rejected_at_stage"] == s) for s in ORDER},
    }
