# EXPERIMENT 5A — DESIGN

## Isolating alpha quality vs basket size vs execution constraints

**Design only. Not executed. 2026-08-18**

---

## 0. What this experiment can and cannot establish

**It runs on 2014-09-21 → 2024-09-01 — the same window Experiments 2 and 3
used.** That window is in-sample for Alpha Core's factor selection (momentum and
%B were chosen from a 2008–2024 diagnostic), and the post-2024 window is
contaminated.

| 5A **can** | 5A **cannot** |
|---|---|
| Localise *where* alpha is lost between ranking and fill | Establish that Alpha Core has alpha |
| Measure the cost of concentrating from decile to top-2% | Produce a number that supports a go-live decision |
| Measure total implementation loss, A − B | Substitute for the forward holdout |
| Show whether B beats a matched random control **as built** | Justify any parameter change |

**5A is a decomposition, not a verdict.** Its output is a diagnosis of the
pipeline. The forward holdout (§6) remains the only thing that can establish
alpha. Any result here that looks like evidence of skill must be treated as
in-sample and reported as such.

---

## 1. Hypotheses

Stated before any arm is run.

| ID | Hypothesis | Decides |
|---|---|---|
| **H1** | *Alpha quality.* With the pipeline removed, Alpha Core beats a matched random control at the same basket size. | Whether the ranking has portfolio-level value at all |
| **H2** | *Basket size.* A2 (top 47) beats A1 (top 10) on risk-adjusted terms. | Whether the production basket size discards the signal the rank-IC study measured |
| **H3** | *Implementation loss.* A1 − B is large and positive. | Whether the pipeline, not the signal, is the binding constraint |
| **H4** | *Incremental value as built.* B beats a matched random control under the identical production pipeline. | Whether the system as it stands has value |
| **H5** | *Exposure neutrality.* Realised exposure is equal across every arm. | **Gate** — not a hypothesis. Failure voids the comparison. |

H2 exists because the rank-IC diagnostic measured the **top decile (~47 names)**
while the book takes the **top 10 of ~470 = the top 2%**. Those are different
portfolios and the difference has never been priced.

---

## 2. Experiment Matrix

Every arm: same universe, same decision dates, same frozen Alpha Core, same
1.04% round-trip cost. **Costs are never removed** — a frictionless arm without
costs measures nothing anyone can trade.

| Arm | Selection | Basket | Fills | Liquidity / queues / participation | Caps (sector, cash floor, min/max position) | Purpose |
|---|---|---|---|---|---|---|
| **A1** | Alpha Core | Top 10 | perfect, at decision close | disabled | disabled | Concentrated alpha capability |
| **A2** | Alpha Core | Top 47 | perfect | disabled | disabled | Diversification effect |
| **B** | Alpha Core | Top 10 | current model — T+1, queues, partials | active | active | Implementation loss |
| **C1** | Random × ≥30 seeds | Top 10 | perfect | disabled | disabled | Null for A1 |
| **C2** | Random × ≥30 seeds | Top 47 | perfect | disabled | disabled | Null for A2 |
| **CB** | Random × ≥30 seeds | Top 10 | current model | active | active | Null for B |

### Why three random controls and not one

A random arm is only a control for the treatment it is matched to. `C1` prices
luck at 10 names frictionlessly; `CB` prices luck at 10 names through the same
allocator and order book that mangled Experiment 3. Comparing B against `C1`
would attribute the pipeline's damage to the ranking. **Every treatment arm gets
its own matched null.** `alpha_core.py` already supports this via
`ranking="random"`, which replaces only `alpha_scores()`.

### Exposure

All arms are held at one **pre-registered constant exposure**. The frictionless
arms achieve it by construction (equal weight across the basket). Arm B and CB
must be *measured* against it — and if they cannot reach it, that shortfall is
the headline finding, not a nuisance.

---

## 3. Metrics

### Per arm

| Group | Metrics |
|---|---|
| Return | CAGR, total return, annual returns |
| Risk | volatility, Sharpe, Sortino, max drawdown, Calmar, drawdown duration |
| Trading | turnover/yr, trades, commission, tax, cost drag, gross vs net CAGR |
| Exposure | target, **realised**, average positions, average cash, per-date realised-vs-target series |
| Execution (**Layer A**) | `blocked_buy_queue`, `blocked_sell_queue`, `capped_by_participation`, `expired`, `filled`, `partial` |
| Construction (**Layer B**) | `blocked_by`, `wanted_positions`, `opened_positions`, `sized_to_zero`, `ranked_out` |
| Attribution | `cash_analysis`, `entry_rejections` |

Layer A and Layer B are **never summed** — enforced by
`assert_layers_disjoint()`, now recorded in every artifact.

### Derived quantities — the actual output of the experiment

| Quantity | Isolates |
|---|---|
| **A1 − C1**, **A2 − C2** | frictionless alpha, by basket size |
| **A2 − A1** | cost of concentrating from decile to top-2% |
| **A1 − B** | **total implementation loss** |
| **B − CB** | incremental value as built — *the only number with a decision attached* |
| **(A1 − C1) − (B − CB)** | how much of the frictionless edge the pipeline destroys |

---

## 4. Pass / Fail Criteria

Pre-specified. Margins fixed here, before any arm runs.

### Validity gates — checked first; failure voids everything

| Gate | Criterion |
|---|---|
| **V1 — exposure equality** | Realised exposure across A1/C1, A2/C2 and B/CB must match **within 1.0pp** per pair. Experiment 3's 1.97pp Alpha-vs-random gap is exactly the confound this catches. |
| **V2 — NAV identity** | Zero unexplained breaches, every session, every arm |
| **V3 — layer disjointness** | `counter_layers.disjoint_verified == true` in every artifact |
| **V4 — identical universe** | Eligible-name count identical across arms on every decision date |

Fail any of V1–V4 → **INVALID**, re-run after the fix, do not interpret.

### Hypothesis gates

| Gate | Criterion | Reads on |
|---|---|---|
| **G1** | A1 above the **90th percentile** of the C1 distribution on net CAGR | H1 |
| **G2** | A2 above the **90th percentile** of C2 | H1 |
| **G3** | A2 − A1 ≥ **+2.0pp** net CAGR **and** A2 Calmar ≥ A1 Calmar | H2 |
| **G4** | A1 − B ≥ **+5.0pp** net CAGR | H3 |
| **G5** | B above the **90th percentile** of CB on net CAGR | H4 |

### Decision table

| G1/G2 | G5 | Reading | Action |
|:--:|:--:|---|---|
| PASS | PASS | Signal exists and survives the pipeline | Proceed to the forward holdout with the current pipeline |
| PASS | FAIL | **Signal exists, pipeline destroys it** — the expected outcome given Experiment 3 | Fix P0–P2, re-run 5A, *then* the holdout |
| FAIL | FAIL | No portfolio-level signal even frictionlessly | Retire Alpha Core; the rank-IC top-tail edge does not survive a 10-name basket |
| FAIL | PASS | Incoherent | Suspect a validity defect; do not interpret |

**G3 is a measurement, not a licence.** If A2 beats A1, the production basket
size is mis-specified — but changing it is a *model change* requiring its own
preregistration. It may not be altered inside 5A or as a reaction to 5A.

### Explicitly forbidden reactions

Whatever 5A returns: no weight change, no factor change, no threshold change, no
basket-size change, no rebalance-frequency change, no ML. 5A is in-sample. It
diagnoses; it does not authorise.

---

## 5. Execution Prerequisites

5A cannot run meaningfully until:

| # | Prerequisite | Status |
|---|---|---|
| P3 | Diagnostic reporting repair | ✅ **Done 2026-08-18** |
| P0 | Idle-cash accrual — cash earns 0% against a 23% hurdle | ❌ Open — **blocks B and CB**; without it their Sharpe is uninterpretable |
| P1 | Exposure correction — a path that can actually hold the target | ❌ Open — **blocks V1**; without it B/CB cannot be exposure-matched to A1/C1 |
| P2 | Funnel counters unified into one layer | ❌ Open — degrades attribution, does not block |
| P5 | Git provenance | ❌ Open — git not installed |

**A1, A2, C1 and C2 can be built now** — they bypass the allocator and the order
book, so P0 and P1 do not touch them. **B and CB must wait for P0 and P1.**

Running the frictionless arms first is worthwhile: if G1 and G2 fail, Alpha Core
is dead at the portfolio level regardless of execution, and P0–P2 need not be
built for this purpose at all.

---

## 6. Phase 5 — Forward Holdout Preregistration (DRAFT)

**Draft only. Not locked, not hashed. Separate from 5A and not conditional on
its result.**

| Field | Value |
|---|---|
| Window start | **2026-08-19** — the first session after the rank-IC diagnostic |
| Earliest evaluation | **2027-08** (~12 decision dates) · preferred **2028-08** (~24) |
| Evaluations permitted | **One** |
| Universe | Identical across arms, per decision date |
| Exposure | Constant, pre-registered, **verified equal across arms before any result is read** |
| Rebalance | 20 sessions — frozen at the current production value |
| Costs | 1.04% round trip, current execution model |

### Candidates

| # | Name | Score | Frozen |
|---|---|---|---|
| 1 | **Alpha Core v1** | `0.5·rank(f_momentum) + 0.5·rank(bb_pct)` | 2026-08-18, `alpha_core.py:264` unmodified |
| 2 | **%B-only v1** | `rank(bb_pct)` | 2026-08-18, fixed zero-weight variant |
| 3 | **Random baseline** | seeded permutation, ≥30 seeds | `ranking="random"` |

### Rules

1. **No selection based on historical results.** Candidate 2 is included because
   it is a *named, frozen* alternative, not because it scored well — and its
   origin is declared: it comes from the 2026-08-18 rank-IC diagnostic, which is
   in-sample for it. Freezing it now is what stops the holdout from becoming a
   test of a hypothesis generated from the test data.
2. **No tuning after locking** — no weight, threshold, window, factor, basket
   size, rebalance frequency or cap.
3. **Multiple-comparison cost stated up front** — Holm–Bonferroni across the two
   non-null candidates, written into the file, not applied afterwards.
4. **Exposure equality is a gate**, not a metric: >1.0pp divergence voids the
   evaluation.
5. **The window is not extended, shortened or shifted** after results are seen.

### Honest limit

Twelve decision dates cannot resolve a 0.03 information coefficient. What the
holdout *can* resolve is whether the implementation defects were the binding
constraint — which, given Experiment 3 and the rank-IC result, is the more
valuable question and the one 5A is designed to set up.

---

## 7. Provenance

| Item | Value |
|---|---|
| Task ID | EXP5A-DESIGN-2026-08-18 |
| Status | **Design only — not executed** |
| Backtests run | **None** |
| Holdouts evaluated | **None** |
| Model changes | **None** |
| Prerequisite completed this task | P3 — see `implementation_fix_spec.md` |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
