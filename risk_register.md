# RISK REGISTER — SIRAT-AI v2.1

**2026-08-18 · maintained alongside `experiment_registry.md`**

Severity: **C**ritical · **H**igh · **M**oderate · **L**ow
Status: **OPEN** · **MITIGATED** · **CLOSED** · **ACCEPTED**

---

## 1. Research validity risks

| # | Risk | Sev | Status | Evidence | Mitigation |
|---|---|:--:|:--:|---|---|
| R1 | **Survivorship bias** — universe is today's listings; no historical snapshot has ever been collected | **C** | **OPEN** | `backtesting/survivorship.py` states none exists. Visible attrition 1.7%/decade is a *lower bound* — names delisted before the DB was built were never fetched | Only elapsed time fixes it. **Start weekly PIT snapshots now.** Until then every absolute return in the programme is inflated by an unmeasured amount |
| R2 | **In-sample factor selection** — momentum and %B were chosen from a 2008–2024 diagnostic | **H** | **ACCEPTED** | `research_factors.py` `FIT_START = 2024-09-01` | Disclosed in every report. Only a forward holdout resolves it |
| R3 | **Holdout contamination** — 2024-09-01 → 2026 is labelled "the fitted window" and Alpha Core's factors were already measured there | **H** | **CLOSED** | E5 preflight stopped before burning it | Correctly caught. Any future holdout must start *after* 2026-08-18 |
| R4 | **Overfitting via researcher choice** — no fitted parameters, but factor set, basket size and rebalance interval were all chosen | **M** | **OPEN** | Alpha Core has zero tuned constants, which limits but does not eliminate this | Freeze in force. No change permitted without a new preregistered phase |
| R5 | **Signal instability** — IC negative over 2015–2019 (60 of 143 dates, 45% positive) | **H** | **OPEN** | E6 | Unresolved. A candidate that fails for five years is not deployable on a one-year holdout |
| R6 | **Result concentrated in one regime** — the 5A spread is ~4× larger in 2020-2022 than elsewhere | **M** | **OPEN** | E9 stability table | Disclosed. Both arms also lose to cash in 2 of 4 sub-periods |

---

## 2. Measurement and infrastructure risks

| # | Risk | Sev | Status | Evidence | Mitigation |
|---|---|:--:|:--:|---|---|
| R7 | **Idle cash earned 0% while charged a 23% hurdle** — every v2.0 Sharpe uninterpretable as a level | **C** | **MITIGATED** | `backtesting/portfolio.py` credited cash only from sale proceeds | P0: `accrue_cash()`, gated by `credit_idle_cash`, **default off** so v2.0 stays reproducible. v2.1 experiments opt in |
| R8 | **Execution counters reported `null`** — queue and participation data measured then discarded | **H** | **CLOSED** | Two reversed key names, one prefix change, one phantom key | P3: mapping corrected; 4 nulls → 0; guarded by 13 tests |
| R9 | **Cash attribution discarded** — `cash_analysis` and `entry_rejections` computed by the engine, never read | **H** | **CLOSED** | `engine.py:420/422` | P3: both surfaced verbatim, not recomputed |
| R10 | **Funnel counters unsummable** — recorded at two layers, `wanted 501` vs `blocked_by 333` | **M** | **MITIGATED** | Two different objects at two points in the loop | P2: registries separated, `assert_layers_disjoint()` enforces it on every call, rule written into the artifact. **Not merged — merging would be wrong** |
| R11 | **No git provenance** — git is not installed; no commit hash obtainable | **H** | **OPEN** | Every report carries "UNAVAILABLE" | Install git, `.gitignore` the 424 MB DB, commit. Blocks any auditable holdout |
| R12 | **Stored v2.0 artifacts hold pre-fix nulls** | **L** | **ACCEPTED** | `execution_realism.json` | Treat those four counters as *unmeasured*, never as zero. Documented in the registry |
| R13 | **Max drawdown understated in frictionless arms** — computed on 20-session period returns | **L** | **ACCEPTED** | E9 | Disclosed; intra-period drawdown is invisible at that resolution |

---

## 3. Strategy and execution risks

| # | Risk | Sev | Status | Evidence | Mitigation |
|---|---|:--:|:--:|---|---|
| R14 | **Signal decays faster than it can be harvested** — ICIR clears 0.30 only at 5–10 sessions; the system trades 20 | **C** | **OPEN** | E6 + E9 + E11 | **This is the root cause of the programme's failure.** Not fixable within the frozen specification |
| R15 | **Required rotation is unattainable** — the frictionless edge needs 11.26 turnovers/yr; production achieves 4.45 | **C** | **OPEN** | E9 vs E11 | Structural: ±3% band, صف, participation cap. 476 participation caps, 28.1% partial fills |
| R16 | **Candidate supply below basket size** — 1.47% pass rate gives 8.1 qualifying names per date against 10 slots | **H** | **OPEN** | 65,932 scored → 969 passed | Loosening gates is a **model change**, forbidden under the freeze |
| R17 | **Cost drag is enormous** — 131.73% cumulative for B, 189.94% for the random control | **H** | **OPEN** | E11 | At 4.45 turnovers/yr and 1.04% round trip, costs consume most of any edge |
| R18 | **Exposure target unmet** — 48.94% realized against 90% | **M** | **OPEN** | Occupancy is 92% of the gap | Downstream of R16 |
| R19 | **Slippage never modelled** — `slippage_pct = 0.0` | **M** | **OPEN** | E11 protocol | All results are optimistic by an unmeasured amount |
| R20 | **Cash rate held flat at 23% for eleven years** | **M** | **OPEN** | `config.RISK_FREE_RATE` | With ~51% of the book in cash, results are sensitive to an untested assumption |
| R21 | **No real-return accounting** — everything is nominal rial against ~40% CPI | **M** | **OPEN** | `config.INFLATION_RATE = 40.0` | A 17.84% nominal CAGR is deeply negative in real terms |

---

## 4. Production readiness risks

| # | Risk | Sev | Status | Mitigation |
|---|---|:--:|:--:|---|
| R22 | **No kill switch** | **C** | **OPEN** | Phase 4 item; not built. Blocks any live capital regardless of alpha |
| R23 | **No live risk limits** | **C** | **OPEN** | Phase 4; untested |
| R24 | **No paper-trading evidence** — `paper_equity` holds 8 rows, `paper_trades` 3 | **H** | **OPEN** | Phase 4/5; cannot verify backtest assumptions against live behaviour |
| R25 | **No execution monitoring** | **H** | **OPEN** | Phase 4 |
| R26 | **No data-quality monitoring in production** | **H** | **OPEN** | Phase 4 |
| R27 | **No performance attribution in production** | **M** | **OPEN** | Phase 4 |

**Phases 4–6 never opened**, because the Phase 3 gate failed. R22–R27 are
recorded as open rather than closed-by-non-applicability: they will bind again
if any successor specification reaches the same point.

---

## 5. Governance risks

| # | Risk | Sev | Status | Mitigation |
|---|---|:--:|:--:|---|
| R28 | **Post-hoc rescue of a failed candidate** — reweighting to %B, loosening gates, shortening the interval | **C** | **MITIGATED** | Freeze rule 1. Every such change is in-sample-motivated and requires a new preregistered phase. Named explicitly in the scorecard |
| R29 | **Spending the clean holdout on a failed candidate** | **H** | **OPEN** | Scorecard recommends *not* starting the forward holdout for Alpha Core v1. The uncontaminated window is the programme's scarcest asset |
| R30 | **Softening a gate after seeing the number** | **H** | **MITIGATED** | Gate preregistered before Arm B ran; V1's failure reported openly rather than used to void an unwelcome result |
| R31 | **Premature ML** | **H** | **MITIGATED** | Rule 2: 0 of 3 preconditions met. R1, R5 and R14 each independently block it |

---

## 6. Top risks, ranked

| Rank | # | Risk | Why it dominates |
|---|---|---|---|
| 1 | **R14** | Signal decays faster than it can be harvested | The root cause. Everything else is downstream |
| 2 | **R15** | Required rotation unattainable | The market-side half of R14; structural, not engineering |
| 3 | **R1** | Survivorship | Inflates every number produced; only time fixes it |
| 4 | **R22/R23** | No kill switch, no live risk limits | Independently disqualifying for real capital |
| 5 | **R11** | No git provenance | No holdout can be audited without it |

---

## 7. Register status

| | |
|---|---|
| Total entries | 31 |
| Critical open | **R1, R14, R15, R22, R23** |
| Closed by v2.1 work | R3, R8, R9 |
| Mitigated by v2.1 work | R7, R10, R28, R30, R31 |
| **Blocking real capital** | **All five criticals** |

Nothing in this register is closed by the Experiment 5B result. The failure
resolved a *question*; it removed no risk.

---

## Provenance

| Item | Value |
|---|---|
| Version | **SIRAT-AI-v2.1** |
| Companions | `experiment_registry.md`, `production_readiness_scorecard.md`, `production_pipeline_audit.md` |
| Git commit | **UNAVAILABLE — git is not installed on this machine** (R11) |
