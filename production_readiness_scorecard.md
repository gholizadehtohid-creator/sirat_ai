# PRODUCTION READINESS SCORECARD — SIRAT-AI v2.1

**2026-08-18 · Mission outcome determined**

---

## Outcome

# OUTCOME B — A PREDEFINED GATE FAILED

The Phase 3 decision gate, preregistered in `experiment5A_design.md` §7 **before
Arm B ran**, returns:

```
recoverable_fraction = (B − CB) / (A1 − C1)
                     = (17.84 − 23.90) / (80.41 − 24.32)
                     = −6.06 / 56.09
                     = −10.8%
```

| Band | Preregistered action | |
|---|---|:--:|
| > 50% | Continue toward production preparation | |
| 20–50% | Identify remaining structural limitations | |
| **< 20%** | **Stop and redesign strategy specification** | ← |

**Not merely below the floor — negative.** Under the real allocator and order
book, Alpha Core does not merely fail to recover its frictionless edge; it
finishes *behind* random selection.

---

## The result

| | **B — Alpha Core** | **CB — random (30 seeds)** | Difference |
|---|---:|---:|---:|
| CAGR | **17.84%** | 23.90% *(mean)* | **−6.06pp** |
| Sharpe (23% hurdle) | −0.28 | +0.12 | −0.40 |
| Sortino | −0.39 | — | |
| Max drawdown | −19.10% | −22.82% | +3.72pp |
| Calmar | 0.93 | — | |
| **Percentile within random** | **3.3** | — | |
| **Empirical p-value** | **0.9667** | — | |

Alpha Core sits below the 5th percentile of thirty random rankings drawn from
the identical universe through the identical pipeline.

### The failure cannot be blamed on execution disadvantage

Alpha Core was **advantaged on every implementation dimension** and still lost:

| Dimension | B | CB | B's advantage |
|---|---:|---:|---:|
| Realized exposure | 48.94% | 42.37% | **+6.57pp** |
| Fill rate | 89.82% | 76.43% | **+13.39pp** |
| Cost drag | 131.73% | 189.94% | **−58.21pp (cheaper)** |
| Turnover | 4.45/yr | 4.42/yr | parity |

### Validity gate V1 — failed, in the conservative direction

The preregistered exposure-neutrality gate required ≤1.0pp; the realized gap is
**6.57pp**. Read strictly, the comparison is void.

**It should not be read strictly.** The gate exists to stop an exposure
advantage being mistaken for skill. Here the advantage belongs to the treatment,
and the treatment *lost anyway*. Voiding on the technicality would be hiding
behind a rule: the negative conclusion holds **a fortiori**, and would only
strengthen if exposure were equalised.

---

## Why: the coherent explanation

Three independent results now agree, and together they close the question.

| Evidence | Finding |
|---|---|
| **E6 rank-IC** | Signal is real (z = +8.3 vs permutation null) but **decays with horizon**: IC 0.0636 @5d → 0.0292 @20d. ICIR **fails** the 0.30 gate at the traded horizon. |
| **E9 frictionless** | The +56pp edge is earned at **11.26 turnovers/year** — ~94% of a ten-name book replaced every month. |
| **E11 production** | Achievable turnover is **4.45/yr — 40% of what the edge requires**. At that rotation the edge is gone, and then some. |

**Alpha Core's information decays faster than the market allows it to be
harvested.** Capturing it needs monthly near-total rotation of a concentrated
book; the participation cap intervened 476 times and 28.1% of fills were
partial. The signal is real and unreachable.

The measurement repairs confirm this rather than confounding it. P0–P2 nearly
doubled exposure (29.22% → 48.94%), raised occupancy (4.63 → 5.80) and made cash
earn its 23%. **The implementation got materially better and the relative result
got worse** (26.7th → 3.3rd percentile). The pipeline was not hiding alpha.

---

## Phase 6 — Live deployment criteria

| Criterion | Status | Evidence |
|---|:--:|---|
| Alpha survives forward test | ❌ **NOT STARTED** | Forward holdout never began; no post-2024-09-01 data used |
| Execution does not destroy expected edge | ❌ **FAILED** | `recoverable_fraction = −10.8%` |
| Exposure target is achieved | ⚠️ **PARTIAL** | 48.94% against a 90% target; occupancy is 92% of the gap |
| Risk limits work | ❌ **UNTESTED** | No kill switch, no live risk limits, no paper-trading evidence |
| Data pipeline stable | ⚠️ **PARTIAL** | PIT computation sound; **universe is survivors-only** |
| Paper trading matches backtest | ❌ **NOT STARTED** | `paper_*` tables hold 8 equity rows, 3 trades |

**0 of 6 pass. 1 hard failure. Phases 4, 5 and 6 do not open.**

---

## Deployment recommendation

# NO-GO

Not "not yet" — **no**, for this specification.

Alpha Core, frozen as it stands, must not receive real capital. The evidence is
not thin or ambiguous: under the honest pipeline it is at the 3.3rd percentile
of random with an empirical p of 0.97, while holding an exposure, fill-rate and
cost advantage over its own control.

### What is *not* being concluded

- **Not** that Alpha Core contains no information. It contains real,
  overwhelmingly significant cross-sectional information (E6, E9).
- **Not** that the engineering was wasted. P0–P2 are the reason this answer is
  trustworthy; without them the failure was attributable to a measurement
  defect, and it would have been rational to keep spending on the pipeline.
- **Not** that the research programme failed. It reached a correct negative on a
  preregistered gate without burning the forward holdout. That is a successful
  outcome.

### What must not happen next

Under the mission's own rules, **none** of the following is authorised by this
result: reweighting toward %B, loosening the entry gates to raise candidate
supply, shortening the rebalance interval to chase the 5-day IC, enlarging the
basket, relaxing the participation cap, or adding ML. Every one of them is a
model change motivated by in-sample evidence, and each would need its own
preregistered research phase.

**The %B-only candidate is the sole exception**, and only because it was frozen
*before* this result. It remains a legitimate arm for a future forward holdout.

### The honest path, if the programme continues

1. **Redesign the specification around the measured decay**, in a new
   preregistered research phase. The binding fact is that a 20-session
   rebalance cannot harvest a signal whose ICIR only clears 0.30 at 5–10
   sessions, in a market with a ±3% band, صف, and a participation cap. That is a
   specification problem, not a parameter problem.
2. **Start the point-in-time universe snapshot now.** Every result in this
   programme is inflated by an unmeasured survivorship amount, and only elapsed
   time fixes it.
3. **Do not start the forward holdout for Alpha Core v1.** Spending a 12-month
   clean window on a candidate already at the 3.3rd percentile is poor use of
   the only uncontaminated asset the programme has.

---

## Provenance

| Item | Value |
|---|---|
| Version | **SIRAT-AI-v2.1** |
| Gate preregistered | `experiment5A_design.md` §7, before Arm B ran |
| Primary artifact | `backtest_output/experiment5b_production.json` |
| Supporting | `production_pipeline_audit.md`, `experiment_registry.md`, `risk_register.md` |
| Alpha Core | **Unmodified throughout** |
| Forward holdout | **Not started, not burned** |
| Tests | 784 pass |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
