# SIRAT-AI — RESEARCH LEDGER

**Append-only. Generated from `sirat_research.db`; never edited by hand.**

Each entry hashes the one before it. Editing, deleting or reordering any entry
breaks every link after it and `ledger.verify()` names the first entry that fails.

| | |
|---|---|
| entries | 26 |
| chain intact | **True** |
| head | `35f2c7a7caf3c77c7b94d38908676c97b3300560093830b7b84f6fd3f7b90922` |
| exported | 2026-11-24T09:21:07 |

---

## Summary

| # | Experiment | Verdict | Stage | Reproducible | Author |
|---|---|---|---|---|---|
| 1 | `E1-market-exposure-baseline` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 2 | `E2-conditional-alpha-core` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 3 | `E3-execution-realism` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 4 | `E4-exposure-holdout` | **REJECT** | 4 | no | phase 1 (replayed 2026-08-19) |
| 5 | `E5-experiment4-preflight` | **REJECT** | 1 | no | phase 1 (replayed 2026-08-19) |
| 6 | `E6-alpha-core-rank-ic` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 7 | `E9-experiment5a-frictionless` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 8 | `E11-experiment5b-production` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 9 | `E13-alpha-decay` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 10 | `E14-queue-artifact` | **REJECT** | 3 | no | phase 1 (replayed 2026-08-19) |
| 11 | `E15-execution-aware-rescue` | **REJECT** | 2 | no | phase 1 (replayed 2026-08-19) |
| 12 | `E19-alpha-core-tradable-edge` | **REJECT** | 2 | yes | lead quantitative research |
| 13 | `E20-alpha-core-capacity` | **REJECT** | 2 | yes | lead quantitative research |
| 14 | `E21-flow-lock-feasibility` | **ARCHIVE** | — | no | SIRAT-AI Research |
| 15 | `E22-flow-filter-execution` | **REJECT** | 3 | no | SIRAT-AI Research |
| 16 | `E24-inst-net-supply-direction` | **REJECT** | 2 | no | SIRAT-AI Research |
| 17 | `E25-smart-money-accumulation-stage1` | **REJECT** | 1 | no | SIRAT-AI Research |
| 18 | `E34-real-bills` | **REJECT** | 1 | no | Claude, at the owner's request of 2026-11-24 (research report item 1: «بازآزمایی ترکیب ۷۰/۳۰ با سری واقعی بازده اخزا») |
| 19 | `E35-real-bills` | **ARCHIVE** | — | no | Claude, at the owner's request of 2026-11-24 (research report item 1: «بازآزمایی ترکیب ۷۰/۳۰ با سری واقعی بازده اخزا»); successor of E34 after its Stage-1 failure |
| 20 | `E26-v2-basket-stop-cadence-cash` | **REJECT** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-05) |
| 21 | `E27-market-exposure-allocation` | **ARCHIVE** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-05 ('investigate every way to make the bot profitable')) |
| 22 | `E28-gold-sleeve` | **ARCHIVE** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-05) |
| 23 | `E30-outlook-forward-yield` | **REJECT** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-10 ('check whether trading the stocks the forward-looking section recommends would be profitable')) |
| 24 | `E31-laggard-quality-flow-turn` | **REJECT** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-10 ('yes, do it')) |
| 25 | `E32-forward-fundamental-quality` | **REJECT** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-10) |
| 26 | `E33-best-of-cheap-industries` | **REJECT** | 1 | no | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-11) |

---

## Multiple testing (generated)

Every number here is recomputed from the record and the result files on each export (sirat/experiments/multiplicity.py). Nothing here changes a verdict above; it says how much each one is worth.

| | |
|---|---|
| experiments in the record | 26 |
| trials counted (N) | 25 |
| not counted | E34: stopped at Stage 1 on a data-layer defect before any return existed (successor E35) |
| expected best standardised Sharpe of N skill-less tries | 2.00 |
| ... per year on 5.3 / 9 / 15 years | 0.87 / 0.67 / 0.52 |
| a new positive needs | t > 3 or DSR > 0.95, not p < 0.05 |

### Reading A — every hypothesis one family (Holm at 0.05, m = 29)

| hypothesis | p | threshold | survives |
|---|---|---|---|
| E28 H2_gold_alone | 0.00025 | 0.001724 | **yes** |
| E27 H1_passive | 0.00727 | 0.001786 | no |
| E28 H1_gold_as_defence | 0.01467 | 0.001852 | no |
| E35 H1_gold_vs_real_bills | 0.04050 | 0.001923 | no |
| E35 H2_equity_vs_real_bills | 0.06119 | 0.002 | no |
| E35 H3_three_part_vs_gold | 0.06599 | 0.002083 | no |
| E27 H2_trend | 0.18863 | 0.002174 | no |
| E30 H1 | 0.24140 | 0.002273 | no |
| E32 H1 | 0.24440 | 0.002381 | no |
| E31 H1 | 0.25880 | 0.0025 | no |
| E33 H1 | 0.36480 | 0.002632 | no |

Hypotheses with no p-value in a result file count in m and never survive.

### Reading B — each experiment's own preregistered rule

| hypothesis | p | rule | accepted |
|---|---|---|---|
| E27 H1_passive | 0.00727 | Bonferroni over 2 (0.025 each) | **yes** |
| E27 H2_trend | 0.18863 | Bonferroni over 2 (0.025 each) | no |
| E28 H1_gold_as_defence | 0.01467 | Bonferroni over 2 (0.025 each) | **yes** |
| E28 H2_gold_alone | 0.00025 | Bonferroni over 2 (0.025 each) | **yes** |
| E30 H1 | 0.24140 | one primary test | no |
| E31 H1 | 0.25880 | second test on E30's window | no |
| E32 H1 | 0.24440 | third test on E30's window | no |
| E33 H1 | 0.36480 | fourth test on the same data | no |
| E35 H1_gold_vs_real_bills | 0.04050 | Holm at 0.05 over H1-H3 | no |
| E35 H2_equity_vs_real_bills | 0.06119 | Holm at 0.05 over H1-H3 | no |
| E35 H3_three_part_vs_gold | 0.06599 | Holm at 0.05 over H1-H3 | no |

### Reading C — families declared 2026-11-24

Prospective for E35 onward; post hoc for everything earlier (the boundaries were drawn after E27 and E28 were known), which is why A and B stand beside it.

**allocation** — what to hold: equity, gold, fixed income, bills -- not which stock (E1, E4, E27, E28, E34, E35)

| hypothesis | p | threshold | survives |
|---|---|---|---|
| E28 H2_gold_alone | 0.00025 | 0.005556 | **yes** |
| E27 H1_passive | 0.00727 | 0.00625 | no |
| E28 H1_gold_as_defence | 0.01467 | 0.007143 | no |
| E35 H1_gold_vs_real_bills | 0.04050 | 0.008333 | no |
| E35 H2_equity_vs_real_bills | 0.06119 | 0.01 | no |
| E35 H3_three_part_vs_gold | 0.06599 | 0.0125 | no |
| E27 H2_trend | 0.18863 | 0.016667 | no |
| E1 (no p stated) | — | — | no |
| E4 (no p stated) | — | — | no |

**selection** — which stock: baskets, fundamental and price rules (E2, E6, E9, E11, E13, E19, E20, E26, E30-E33)

| hypothesis | p | threshold | survives |
|---|---|---|---|
| E30 H1 | 0.24140 | 0.004167 | no |
| E32 H1 | 0.24440 | 0.004545 | no |
| E31 H1 | 0.25880 | 0.005 | no |
| E33 H1 | 0.36480 | 0.005556 | no |
| E2 (no p stated) | — | — | no |
| E6 (no p stated) | — | — | no |
| E9 (no p stated) | — | — | no |
| E11 (no p stated) | — | — | no |
| E13 (no p stated) | — | — | no |
| E19 (no p stated) | — | — | no |
| E20 (no p stated) | — | — | no |
| E26 (no p stated) | — | — | no |

**execution** — can a signal be traded: costs, queues, fills (E3, E5, E14, E15, E22)

| hypothesis | p | threshold | survives |
|---|---|---|---|
| E3 (no p stated) | — | — | no |
| E5 (no p stated) | — | — | no |
| E14 (no p stated) | — | — | no |
| E15 (no p stated) | — | — | no |
| E22 (no p stated) | — | — | no |

**flow** — what client-type flow predicts (E21, E24, E25)

| hypothesis | p | threshold | survives |
|---|---|---|---|
| E21 (no p stated) | — | — | no |
| E24 (no p stated) | — | — | no |
| E25 (no p stated) | — | — | no |

### Deflated Sharpe ratio at N = 25

| result | SR per period | obs | basis | PSR vs 0 | DSR | > 0.95 |
|---|---|---|---|---|---|---|
| E27 H1 passive equity vs 20% fund | 0.1874 | 170 | t / sqrt(blocks), 20-session blocks | 0.9921 | 0.6706 | no |
| E28 H2 gold alone vs 20% fund | 0.3315 | 110 | t / sqrt(blocks), 20-session blocks | 0.9996 | 0.9242 | no |
| E35 70/30 equity/gold vs real bills | 0.2704 | 106 | monthly | 0.9968 | 0.7791 | no |
| E35 equity vs real bills | 0.2042 | 106 | monthly | 0.9808 | 0.5413 | no |

Normal returns assumed (skew 0, kurtosis 3) where the result file does not store the series; fat tails lower the DSR.

### Windows: use and the stop rule

| year | experiments that evaluated it |
|---|---|
| 2009 | 1 — E27 |
| 2010 | 1 — E27 |
| 2011 | 1 — E27 |
| 2012 | 1 — E27 |
| 2013 | 1 — E27 |
| 2014 | 1 — E27 |
| 2015 | 1 — E27 |
| 2016 | 1 — E27 |
| 2017 | 3 — E27, E28, E35 |
| 2018 | 3 — E27, E28, E35 |
| 2019 | 3 — E27, E28, E35 |
| 2020 | 3 — E27, E28, E35 |
| 2021 | 6 — E27, E28, E30, E31, E32, E35 |
| 2022 | 6 — E27, E28, E30, E31, E32, E35 |
| 2023 | 8 — E19, E27, E28, E30, E31, E32, E33, E35 |
| 2024 | 10 — E19, E21, E26, E27, E28, E30, E31, E32, E33, E35 |
| 2025 | 9 — E21, E26, E27, E28, E30, E31, E32, E33, E35 |
| 2026 | 9 — E21, E26, E27, E28, E30, E31, E32, E33, E35 |

| window | years | trials recorded / upper bound | next trial's luck hurdle (annual Sharpe) | max trials | exhausted |
|---|---|---|---|---|---|
| selection 2021-06..2026-08 | 5.2 | 10 / 25 | 0.88 | 50 | no |
| allocation 2017-10..2026-07 | 8.8 | 10 / 25 | 0.68 | 382 | no |
| full 2009..2026-08 | 17.6 | 10 / 25 | 0.48 | 10000 | no |

Windows recorded by the Manager only: Phase 1's replays and E20/E22/E24/E25 recorded none, so the per-year counts are a lower bound; the stop rule uses every trial as the upper bound.

Stop rule: once a window's next trial would expect a best annual Sharpe of 1.0 from luck alone, no further historical test on it can confirm anything; only data the programme has not seen can (the forward log).
Exploratory experiments (Phase 1, replayed without preregistration) count in N and are never accepted.

---

## 1. `E1-market-exposure-baseline`

> A deterministic four-signal market-state engine decides when and how much to be invested better than fixed passive exposure.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:27 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `d6f3c2776311051e4f072123` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `611d1f9ef110aee65c292937` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `47b9134b203e755c96d7f161` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, random_baseline, factor_decay. NEVER MEASURED IN PHASE 1

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, random_baseline, rank_correlation |

---

## 2. `E2-conditional-alpha-core`

> Alpha Core selection adds value conditional on the exposure path.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:27 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `4b7c7cd0e90a96f4de3f66b9` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `87db2bf17a2a291dd38507e6` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `ff9831a9ceb9807d3f9b87df` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, factor_decay. 0.01456 < 0.02 required

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, rank_correlation |

---

## 3. `E3-execution-realism`

> Alpha Core beats random selection under production execution.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:27 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `180739892d92713d1b054a78` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `83798ce48bb33080210dbd32` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `2c3694475e2a655b25a69564` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, factor_decay. 0.01456 < 0.02 required

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, rank_correlation |

---

## 4. `E4-exposure-holdout`

> The exposure engine delivers incremental value on unseen data.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:28 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `30deab9afe8deba325c57c3a` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `006cfc575c835c61a7ad61ab` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `11d35d9d9f9d53f3d9ced75c` |

**Rationale.** Stage 4 (ROBUSTNESS) failed on walk_forward, out_of_sample. block capture upside 52.7% vs downside 64.0%: the defensive claim inverts out of fold

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **PASS** | all 4 passed |
| 3 EXECUTION REALISM | **PASS** | all 5 passed |
| 4 ROBUSTNESS | **FAIL** | failed: out_of_sample, walk_forward |

---

## 5. `E5-experiment4-preflight`

> Alpha Core's two factors generalise to an untouched forward window.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:28 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `444fcfbce383c57b63c49006` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `d4a9978f9a259819221cabbb` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `f3c95d8707b1bbea6d4cf8b2` |

**Rationale.** Stage 1 (DATA INTEGRITY) failed on point_in_time. the proposed holdout 2024-09-01..2026-06-30 was already the fitted window: 17,695 observations over 23 dates

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: point_in_time |

---

## 6. `E6-alpha-core-rank-ic`

> The Alpha Core score orders future returns.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:29 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `32e7b973e742165e3a10e150` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `5442c7a26751ca2ac9566f88` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `d988603ea5011904a19ec413` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, factor_decay. 0.01456 < 0.02 required

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, rank_correlation |

---

## 7. `E9-experiment5a-frictionless`

> Alpha Core creates portfolio value before the allocator and order book are introduced.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:29 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `e409e9fef8704623715a1c0a` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `480beece095d7ba1a8e52b48` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `23a7d6384c984b15467f7bd0` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, factor_decay. 0.01456 < 0.02 required

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, rank_correlation |

---

## 8. `E11-experiment5b-production`

> Alpha Core retains incremental value through the real allocator and order book.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:29 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `687c758e2cdc9740eb6925f1` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `35f980d9446c9158ed158897` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `d485f4ecc2e72e5e93724019` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, factor_decay. 0.01456 < 0.02 required

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, rank_correlation |

---

## 9. `E13-alpha-decay`

> The signal exists but decays faster than the trading system can harvest it.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:30 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `33a0e58f4a2fc48c29a46935` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `009ed04579ec32c870ce4ab3` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `fa17661db116e9e336551e79` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, factor_decay. 0.01456 < 0.02 required

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, rank_correlation |

---

## 10. `E14-queue-artifact`

> The h=1 signal detects stocks entering or remaining in price-limit queues, not persistent alpha.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:30 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `86b3ceecacb691cbb9127006` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `b3c5328b8221ad116286b870` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `ab78877e0ab460c01fa0817a` |

**Rationale.** Stage 3 (EXECUTION REALISM) failed on transaction_costs, liquidity, participation_limits, slippage, queue_effects. top-decile h=1 excess 0.5496% against a 1.04% round trip: -0.49% per period, -69%/yr

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **PASS** | all 4 passed |
| 3 EXECUTION REALISM | **FAIL** | failed: liquidity, participation_limits, queue_effects, slippage, transaction_costs |

---

## 11. `E15-execution-aware-rescue`

> An execution-aware filter using only decision-time information converts the Alpha Core signal into a positive net edge after costs.

| Field | Value |
|---|---|
| date | 2026-08-19T08:39:31 |
| author | phase 1 (replayed 2026-08-19) |
| strategy version | phase-1 (v2.0-archive) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `422c1ad9da36460860176f18` |
| config hash | `3a8e3e793755b4cf81c3d301` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `0fb5a5ff9acec7f7dfc855d1` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `354ec88217f5e7a45c07276b` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, random_baseline, factor_decay. filtered IC at h=20 is -0.03245, below 0.02 and the wrong sign

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, random_baseline, rank_correlation |

---

## 12. `E19-alpha-core-tradable-edge`

> Alpha Core (50% momentum rank + 50% Bollinger %B rank) contains predictive information that survives realistic trading: it orders future returns at the horizon it is actually traded at, that ordering beats structurally-matched null models, and the resulting book clears its own transaction costs under T+1 execution with price-limit queues.

| Field | Value |
|---|---|
| date | 2026-08-19T10:34:00 |
| author | lead quantitative research |
| strategy version | alpha_core_v1 (frozen, sha256 90024d86d7a5c268) |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `9a3c93aed225140066972b92` |
| config hash | `b475242ec0b83422b0f89e99` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `9fd80d297b6ec43632e742a9` |
| reproducible | True |
| **verdict** | **REJECT** |
| entry hash | `6a761c7ee07fc6087a540b3b` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, random_baseline, factor_decay. The declared test -- Alpha Core at h=20, all regimes, against every structurally-matched null -- produces mean IC 0.01456 (t=1.23) against a required 0.02, a NEGATIVE holdout IC of -0.02449, 5 of 10 positive walk-forward folds, and a net edge of -0.38% per period that stays negative under every cost perturbation including a favourable one. 7 of 11 preregistered gates fail. This was recorded as an expected failure condition before the experiment ran.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, random_baseline, rank_correlation |

---

## 13. `E20-alpha-core-capacity`

> Alpha Core's predictive information survives restriction to securities that can genuinely absorb the strategy at measured market capacity. Stated as three separable claims: (L1) the score carries statistically credible information somewhere in the cross-section; (L2) that information is still present after the universe is restricted to names whose average traded value can absorb a position at a preregistered participation cap; (L3) whatever remains at L2 survives T+1 execution, queues, partial fills, turnover and a 1.04% round trip.

| Field | Value |
|---|---|
| date | 2026-08-19T11:25:43 |
| author | lead quantitative research |
| strategy version | alpha_core_v1 (frozen, sha256 90024d86d7a5c268) -- UNMODIFIED |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `4ee4a691a1208168353ef7ee` |
| config hash | `fc8cc9a53637340ffcf180cd` |
| dataset fingerprint | `90e30213b30e6e73` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `1f9d15b09459975593e525db` |
| reproducible | True |
| **verdict** | **REJECT** |
| entry hash | `fcc4684bb2c5d394fe2e9d64` |

**Rationale.** Stage 2 (ECONOMIC SIGNAL VALIDATION) failed on ic, rank_correlation, random_baseline, factor_decay. E20 decomposed E19's failure into three layers. Information EXISTS: IC 0.05101 at h=5 (t=4.18) over the full cross-section, and IC 0.17066 (t=7.14) in the names that capacity EXCLUDES. It does not survive the capacity restriction: at T2 (participation <= 5%, the platform's own cap) IC at the declared h=20 is -0.01293 (t -0.985), and the capacity curve is negative at EVERY level from 50% down to 0.25%. Of a frictionless +3.1735% gross alpha, 1.4003pp is lost to the liquidity restriction and 1.8024pp to execution before cost takes the remainder to -0.992%. The strategy also fails to beat the preregistered liquidity-matched null (90.0th percentile against a required 95th). Classification: B_INFORMATION_ONLY_IN_UNTRADABLE_SECURITIES.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay, ic, random_baseline, rank_correlation |

---

## 14. `E21-flow-lock-feasibility`

> Institutional (حقوقی) net flow and per-capita individual buying power at session t predict, cross-sectionally, whether a Tehran-listed name is UNBUYABLE at session t+1 -- locked in a صف خرید, reopening, or gapped away. This is a tradability prediction, not a return prediction: the output is a feasibility filter for the Stage 1 gate, and it is measured against a microstructure state, never against a forward return.

| Field | Value |
|---|---|
| date | 2026-08-22T09:06:07 |
| author | SIRAT-AI Research |
| strategy version | flow-lock-composite-v1 |
| code hash | `d725813862b0bb77fdfd7e5a` |
| git commit | `8aa75f5c6c7d82b5297efe76` |
| config hash | `44136fa355b3678a1146ad16` |
| dataset fingerprint | `6ccda7cdf68e41c5` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `2ce1d49dd90ebd4602094f91` |
| reproducible | False |
| **verdict** | **ARCHIVE** |
| entry hash | `f5d3af47da67b8acd5edaf85` |

**Rationale.** ARCHIVE. Stage 1 and Stage 2 -- the experiment's entire declared scope -- both PASS, and the holdout held.

MEASURED, discovery window 2014-01-01..2023-02-28, 2,128 dates, median 425 names/date: mean per-date Spearman(score, next_unbuyable) = 0.13650 against a frozen floor of 0.05; t = 80.52 against a floor of 3.0; the observed mean sits at the 100th percentile of a 200-fold within-date label permutation null whose p95 is 0.00204; top-decile unbuyable rate 10.650% against a universe rate of 4.057%, a lift of 2.625x against a floor of 2.0x, retaining 0.506 of its excess lift at h=3 against a floor of 0.30.

HOLDOUT 2024-09-02..2026-08-18, spent once and never previously evaluated by any experiment in the ledger: mean IC 0.15280, t = 29.24, lift 2.049x. The sign held and the magnitude grew. E19's holdout IC flipped sign to -0.02449; this one did not.

THREE DIAGNOSTICS, discovery window only, holdout not reopened:

(1) NOT MERELY LOCK PERSISTENCE. A name locked at session t has high == low and therefore close == high, so the closed_at_high component risked making the whole result a restatement of 'locked today, locked tomorrow'. Excluding every name already locked at t -- 96.0% of the panel survives, base rate falls from 4.057% to 2.300% -- the composite still measures IC 0.08823 (t = 62.61) at a lift of 2.233x, and the two flow components alone still measure 0.05031 at a lift of 1.729x. The signal is not the tautology.

(2) E20'S FAILURE MODE DOES NOT REPEAT. E20 killed Alpha Core because its information sat only where trading was impossible: IC +0.212 in the most illiquid quintile and -0.030 in the most liquid. Here the decile lift is almost flat across the liquidity spectrum -- 2.375x / 2.335x / 2.414x / 2.417x / 2.237x from most illiquid to most liquid -- and IC stays positive and significant in the most liquid quintile at 0.110 (t = 23.46). Predicting tradability is not predicting returns, and it does not inherit that failure.

(3) ONE DECLARED SIGN WAS WRONG, AND IT IS NOT BEING CORRECTED HERE. inst_net_ratio was preregistered at +1 on the magnet-effect argument that institutional trading drives price toward the band. Measured alone it carries IC -0.08707 (t = -60.22) at a lift of 0.416x -- the opposite direction, strongly. Mechanically this is coherent: in a صف خرید the حقوقی side is typically supplying the queue rather than forming it. The composite passed DESPITE this component, not because of it. Flipping the sign now would be choosing a parameter after seeing the result, so the directional claim in the hypothesis is recorded as FALSIFIED and the corrected sign belongs in a successor preregistration, not in this record. The predictive content that survives is indiv_power (قدرت خریدار حقیقی), IC 0.16855 at a lift of 4.349x -- also a client_type flow variable, so the broad hypothesis holds while its specific direction did not.

WHY NOT PROMOTE. Stage 3 is EXECUTION REALISM and one of its five required checks is slippage, which has been 0.0% in every result this programme has produced because no impact model exists. That check cannot be reported as passing without reading absence as approval, so E21 was registered with a scope ending at Stage 2 and it ends there. The provenance also records git_dirty=True and reproducible=False at registration time, which independently bars promotion.

SUCCESSOR WORK, in order: (a) an impact model, RESEARCH_OPTIONS.md Option 2, without which no execution claim is measurable; (b) a successor experiment preregistering inst_net_ratio at -1 with the mechanism stated in advance; (c) only then, whether using this as a Stage 1 feasibility filter improves realised versus requested returns.

This experiment does not touch, re-score or reference Alpha Core (90024d86d7a5c268). E19 and E20 remain adjudicated.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **PASS** | all 4 passed |

---

## 15. `E22-flow-filter-execution`

> Refusing to buy the names E21's composite ranks in its top decile raises the fill rate of an otherwise randomly chosen basket by at least 30%, without moving the basket into thinner stock, without raising the round-trip cost of a filled position once square-root impact is charged, and without shrinking the eligible universe below 50 names on the median session.

| Field | Value |
|---|---|
| date | 2026-08-22T09:39:13 |
| author | SIRAT-AI Research |
| strategy version | e21-composite-frozen + impact-v1 |
| code hash | `bd71e2694c920ae08600efca` |
| git commit | `279b15158fe6635e76d553bb` |
| config hash | `44136fa355b3678a1146ad16` |
| dataset fingerprint | `6ccda7cdf68e41c5` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `b4faab1bb9b2abf19f6bd118` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `6d83b7b2b54bd9505a3886c7` |

**Rationale.** REJECT at Stage 3 (EXECUTION REALISM), the first Stage 3 ever run in this programme. Three of five checks failed: queue_effects, slippage, transaction_costs.

THE REJECTION IS SOUND ON queue_effects ALONE, and that check depends on no cost model, no volatility estimate and no imported constant -- only on whether an order filled. Over 112,400 random baskets across 2,810 sessions, 562,000 positions per variant: the baseline failed to fill 3.6155% of positions and the filtered basket 3.0907%, a ratio of 0.8549 against a preregistered ceiling of 0.70. The filter reduces failed fills by 14.5% where 30% was declared necessary. It works, and it works less than half as well as required.

THE ECONOMICS FAIL INDEPENDENTLY OF EVERY COST MODEL. Converting a failed fill into a fill happens for 0.5248pp of positions while the filter adds 0.1504pp to every filled position, so break-even requires each converted slot to be worth 28.66pp. The reference basket is drawn uniformly at random and therefore has an expected excess over cash of zero by construction. A cost with no offsetting benefit is not a close call.

THE TWO COST CHECKS FAILED FOR A REASON THAT IS NOT THE FILTER, AND THIS IS RECORDED SO THE FILTER IS NOT BLAMED FOR IT. Measured per session, the filter is CHEAPER on the typical date: median per-date impact on filled positions is 0.0824% baseline against 0.0697% filtered, p95 0.1803% against 0.1422%, and only 26.1% of the 2,809 sessions are worse under the filter (median delta -0.0011pp, mean -0.0064pp). The aggregate reversed because of five consecutive sessions in June 2022 reporting mean impact between 56% and 76% of price across the whole 550-name cross-section. The worst 1% of dates account for -254.6% of the total delta -- they do not merely dominate the mean, they invert its sign.

ROOT CAUSE, IDENTIFIED: eleven rows in a two-month sample carry impossible returns, the largest being +19,976,600% on 2022-05-22, which means a prior close of 1 rial. These are new listings, post-suspension reopenings and unadjusted capital increases. One such row sits inside a 20-session trailing volatility window for twenty sessions, and a single contaminated name dominates the mean impact of every basket drawn on those dates. It also explains the panel-wide signature: sigma mean 8.79% against a median of 2.26%.

DEFECT CLASSIFICATION: **data and tooling, not strategy.**
  data     - price_history carries unadjusted and placeholder prices that produce returns no market ever printed.
  tooling  - backtesting/impact.py accepted a daily volatility of several hundred percent because it validates only 'positive and finite'. It was written two days ago and this is its first real use.
  gate     - Stage 1 passed this panel. Its four checks cover forward alignment, universe restoration, cross-section width and coverage; not one of them looks at whether a return is physically possible. E14's `annotate` has flagged exactly these bars as `extreme_gap` (|ret| > 12%) since it was written, so the platform already held the knowledge the volatility estimator failed to use.

WHAT IS NOT BEING DONE. The panel is not being cleaned and Stage 3 is not being re-run under this experiment id. The thresholds were frozen before any number existed and the measurement was exactly what was declared; repairing the data after seeing the verdict and re-measuring is the textbook forbidden move, and it would be forbidden even though the repair is obviously correct. E22 rejects. A successor experiment may ask the cost question again with a volatility sanity bound preregistered.

WHAT E22 SETTLES: the filter does not pay for itself in front of a basket with no expected edge. WHAT IT DOES NOT SETTLE: whether it pays in front of a basket that has one. That needs a validated return signal, and this programme does not have one -- E19 and E20 established that Alpha Core is not it.

No out-of-sample claim was available and none is made; that limitation was declared at registration, not discovered here. Alpha Core is untouched at 90024d86d7a5c268.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 4 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **PASS** | all 4 passed |
| 3 EXECUTION REALISM | **FAIL** | failed: queue_effects, slippage, transaction_costs |

---

## 16. `E24-inst-net-supply-direction`

> Institutional net flow at session t predicts, cross-sectionally, that a Tehran-listed name is UNBUYABLE at session t+1 -- at the NEGATIVE sign: relative institutional net selling, not buying, accompanies queue formation, because the institutional side supplies a buy queue rather than forming it. This is a tradability prediction, not a return prediction, and it is the preregistered correction of the one directional claim E21 recorded as falsified.

| Field | Value |
|---|---|
| date | 2026-08-23T19:05:26 |
| author | SIRAT-AI Research |
| strategy version | inst-net-supply-v1 |
| code hash | `70abd33e672402c9d20a3b10` |
| git commit | `9bd62b67bc3883d355b194e1` |
| config hash | `44136fa355b3678a1146ad16` |
| dataset fingerprint | `089c7421c5a5fab3` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `87b995fae3e862c72210361c` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `1178c5434df8b0fe6322f1d6` |

**Rationale.** REJECT at Stage 2 (ECONOMIC SIGNAL VALIDATION), failing factor_decay. Preregistration 87b995fae3e862c72210361c6205bcdef44d9c567ed36dd613f5d5767802b512, frozen before any number below existed.

WHAT PASSED, measured on the discovery window 2014-01-01..2023-02-28 (2,137 scored dates, 930,054 rows, floor 100 names/date): at the corrected sign of -1, mean per-date Spearman IC 0.08193 against a floor of 0.05; t = 56.28 against a floor of 3.0; the observed mean sits at the 100th percentile of a 200-fold within-date label permutation null whose p95 is 0.00161. The direction E21 recorded as FALSIFIED for its +1 declaration is therefore confirmed at -1 under a preregistration that declared it in advance -- but this confirmation was near-assured from E21's own diagnostic on the same window and is deliberately the least-weighted part of this record. It is an in-sample formalisation, declared as such at registration, with no out-of-sample claim available: every historical holdout is spent and the forward test belongs to R-FORWARD-2027 when it accrues.

WHAT FAILED, and it is the part that was genuinely unmeasured before this run: decay. The top decile of the signed score carries a 2.407x unbuyable-rate lift at h=1 (9.525% vs 3.957%), clearing the 2.0x floor -- but by h=3 the lift is 1.290x and by h=5 it is 1.100x, a retention of 0.206 against a preregistered floor of 0.30. Institutional net selling is a ONE-SESSION warning: the excess concentration is nearly gone two sessions later. A feasibility filter must survive the lag between computing a signal after the close and acting on it, and this component does not.

THE PREREGISTERED DIAGNOSTIC (reported, not gating): excluding names already locked_up at t retains 96.2% of rows and still measures IC 0.05374 (t 43.47) with a top-decile lift of 1.994x. About seven-tenths of the h=1 excess lift survives the exclusion, so the signal is not merely lock persistence -- but its usable concentration sits at the 2x line even before decay is charged.

DECONTAMINATION, applied exactly as preregistered and worth recording for successors: the both-legs rule on |return| > 100% transitions dropped 104,188 rows (5.50%) across 323 names and 3,040 dates over 1,448 iterations. Around an irreconcilable price-level break (an unadjusted rebasing), the iteration walks outward and consumes real rows on both sides of the break equally -- blunter than identifying the fake side would be. The rule is not being refined mid-experiment and the stages are not being re-run; a successor should preregister a placeholder-level test instead. Stage 1 passed on the cleaned panel: forward alignment 100% of 1,996 resolved samples, universe snapshot u20260819-668658bb restored, median 419 names/date, coverage 78.53%, zero impossible sessions remaining with 6,735 legitimate >12% reopenings retained.

WHAT THIS SETTLES: inst_net_ratio at -1 is real, correctly-signed, highly significant ORDERING information with a one-session horizon, and it is now on the record the legitimate way. Any future composite preregistration may cite this entry for the -1 sign. WHAT IT DOES NOT SETTLE OR TOUCH: E21's composite result stands (it passed DESPITE this component at the wrong sign); indiv_power remains the strongest carrier (E21: IC 0.16855, lift 4.349x); E19, E20 and E22 remain adjudicated; Alpha Core is untouched at 90024d86d7a5c268. No window was spent: the discovery window was reused as declared and R-FORWARD-2027 remains unclaimed.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 5 passed |
| 2 ECONOMIC SIGNAL VALIDATION | **FAIL** | failed: factor_decay |

---

## 17. `E25-smart-money-accumulation-stage1`

> سهام کوچک و متوسطی که در کف حمایتی، با ورود پول هوشمند، MFI بالای ۷۰، P/E پایین‌تر از میانه‌ی صنعت و کد به کد حقوقی به حقیقی قرار دارند، در آینده بازدهی بالاتری از بازار ایجاد می‌کنند. -- The scanner's own hypothesis, registered so that Stage 1 can decide whether it is testable on the data this database holds. Stage 1 alone is attempted here.

| Field | Value |
|---|---|
| date | 2026-08-24T00:44:28 |
| author | SIRAT-AI Research |
| strategy version | smart-money-accumulation-v1 |
| code hash | `fe5861347b3cce25720a6d94` |
| git commit | `9bd62b67bc3883d355b194e1` |
| config hash | `44136fa355b3678a1146ad16` |
| dataset fingerprint | `8d32f90cae3e7265` |
| universe snapshot | `u20260819-668658bb` |
| preregistration hash | `0bedef72d4d96ddd89d3702d` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `084aa09af59f2a82b3e992c5` |

**Rationale.** REJECTED AT STAGE 1 -- DATA INTEGRITY. Four of five checks failed, all of them about the data rather than the idea. The hypothesis is UNTESTED, not disproved: nothing here measured a return.

point_in_time: market_snapshot carries one captured_at and is replaced on every pull, so the valuation (15 pts) and size (10 pts) components have no value on any past date. Scoring a 2024 signal with 2026 P/E is look-ahead.
survivorship: the universe is today's list. delisted_sweep.py has discovered 13,245 instruments outside it and fetched 0 bars, so no historical universe can be restored per decision date.
missing_data: P/E covers 59.8% and market cap 63.2% of the equity universe, against a 60% floor.
price_sanity: 47 impossible session transitions remain in the 2023+ panel and no decontamination rule was preregistered for this scanner.

universe_valid passed (median 776 names per session over 867 sessions).

Recorded as a diagnostic, gating nothing: an UPPER BOUND on breadth over the two already-spent windows gives a median of 11 candidates per session, 10 or more on 55.9% of sessions, and a 2026 mean of 2.9. The requested portfolio rule takes the top 10 at Score >= 75; on the live session that threshold yielded 1 name. Breadth is therefore a second, independent obstacle to the requested design, and it is a property of the filter rather than of any market regime.

SUCCESSOR PATH, in the order that unblocks the most for the least work:
(a) version market_snapshot into a per-session series -- this alone makes a full-model historical test possible from that day forward;
(b) finish delisted_sweep.py's fetch phase to populate census_price_history;
(c) OR register a DIFFERENT, point-in-time-only model: MFI 15 + transfer 20 + کد به کد 15 + support 15 + volume 10, rescaled from 75 to 100, which is testable on stored data today and must not be described as this scanner;
(d) the full return claim may be tested out of sample only against R-FORWARD-2027, by a successor registered once it accrues.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship |

---

## 18. `E34-real-bills`

> H1_gold_vs_real_bills: Sharpe(eq70_gold30) > Sharpe(eq70_bills30). One-sided. Re-tests E28 H1. | H2_equity_vs_real_bills: mean monthly excess of equity_100 over bills > 0. One-sided. Re-tests E27 H1 on the window bills exist. | H3_three_part_vs_gold: Sharpe(eq70_gold15_bills15) differs from Sharpe(eq70_gold30). Two-sided.

| Field | Value |
|---|---|
| date | 2026-11-24T09:11:09 |
| author | Claude, at the owner's request of 2026-11-24 (research report item 1: «بازآزمایی ترکیب ۷۰/۳۰ با سری واقعی بازده اخزا») |
| strategy version | allocation-real-bills-v1 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `de93c396639401b941933ea2` |
| config hash | `44136fa355b3678a1146ad16` |
| dataset fingerprint | `c09822da85d64b68` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `0ae4199a84c8926c4dfd9409` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `efb41d1150f966561461a8dc` |

**Rationale.** Stage 1 failed: missing_data, price_sanity. The preregistration says the experiment then says nothing.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, price_sanity |

---

## 19. `E35-real-bills`

> H1_gold_vs_real_bills: Sharpe(eq70_gold30) > Sharpe(eq70_bills30). One-sided. Re-tests E28 H1. | H2_equity_vs_real_bills: mean monthly excess of equity_100 over bills > 0. One-sided. Re-tests E27 H1 on the window bills exist. | H3_three_part_vs_gold: Sharpe(eq70_gold15_bills15) differs from Sharpe(eq70_gold30). Two-sided.

| Field | Value |
|---|---|
| date | 2026-11-24T09:13:17 |
| author | Claude, at the owner's request of 2026-11-24 (research report item 1: «بازآزمایی ترکیب ۷۰/۳۰ با سری واقعی بازده اخزا»); successor of E34 after its Stage-1 failure |
| strategy version | allocation-real-bills-v1 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `44136fa355b3678a1146ad16` |
| dataset fingerprint | `30f2b07eed618977` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `7baddda39598818dd9aea5b9` |
| reproducible | False |
| **verdict** | **ARCHIVE** |
| entry hash | `9cf7182b40c08156e4c0dbb4` |

**Rationale.** Allocation re-test, closed as the preregistration's decision rule says (ARCHIVE: not a signal for the stage pipeline). Verdicts: H1 gold vs real bills NOT ACCEPTED (p 0.0405); H2 equity vs real bills NOT ACCEPTED (p 0.0612); H3 three-part vs gold NOT ACCEPTED (p 0.0660); Holm at 0.05 within the experiment, family 'allocation'. Results: backtest_output\E35_results.json.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **PASS** | all 5 passed |

---

## 20. `E26-v2-basket-stop-cadence-cash`

> [Registered retroactively 2026-11-23] The v2 basket's losses on 2024-09-01..2026-11-04 are driven by its book mechanics more than by its selection: stops inside normal daily noise exit 67-75% of trades at the stop (9 of 44 on the entry day in 2026), and a 20-session rebalance then leaves the freed capital idle, so the book averages 80-85% cash earning nothing. Widening the stop beyond two daily price bands, refilling the book every 5 sessions and crediting idle cash at the policy rate should raise total return in BOTH windows relative to the unchanged v2 book.

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:11 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-05) |
| strategy version | examples/basket_random_null.py as first committed bfd8fb0 (2026-11-05T08:02:31+03:30); result commit a0888ef |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `845b640741b62766005814f7` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `e38623c7fab0abb147031084` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `ef8d73df6c1c4677d0d58e59` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E26_results.json, commit a0888ef, 2026-11-05T11:31:08+03:30): {"verdict": "REVERT. E26 beat the baseline in 2024-09..2025-12 but not in 2026, and the rule required both. V2_STOP_ATR_MULTIPLE, V2_MIN_STOP_PCT and V2_REBALANCE_SESSIONS are back at 2.0, 3% and 20. The idle-cash credit and the panel's fixed-income note stay, as preregistered."}. Preregistration: backtest_output/E26_preregistration.json, commit bfd8fb0, 2026-11-05T08:02:31+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. REJECT follows the experiment's own verdict. The stage number says where the platform's record stops; it is NOT the reason the experiment gave.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

## 21. `E27-market-exposure-allocation`

> [Registered retroactively 2026-11-23] The v2 basket lost money in 2024-09..2026-11 while the index rose ~93% a year; its selection is at or below random and it held 80-85% cash. The one family the research catalog lists as NEVER tested is plain allocation between the equity market and a fixed-income fund (E1/E4 tested a four-signal TIMING engine, which lost to its own static exposure-matched control). Does holding the market, with or without a simple trend rule, beat a fixed-income fund after costs? | H1_passive: Always 100% in the equity index (after the holding drag). Claim: annualised return exceeds the 20% fixed-income fund in the primary window. | H2_trend: 100% equity while TEDPIX's close is above its 200-session simple moving average, else 100% fixed income. Signal read at a session's close, switch executed at the NEXT session's close, switch cost charged. Claim: higher Sharpe (excess over the 20% fund) than H1 AND a smaller maximum drawdown than H1, in the primary window.

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:11 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-05 ('investigate every way to make the bot profitable')) |
| strategy version | examples/allocation_study.py as first committed d4db109 (2026-11-05T08:53:28+03:30); result commit b772e21 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `8a826ec85f15849f1bc880fd` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `8beebda395acfa8e13f84c2f` |
| reproducible | False |
| **verdict** | **ARCHIVE** |
| entry hash | `5987b62f3ea539b9bd23deab` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E27_results.json, commit b772e21, 2026-11-05T08:53:42+03:30): {"verdict": {"H1_passive": "ACCEPT", "H2_trend": "REJECT", "alpha_per_hypothesis": 0.025}}. Preregistration: backtest_output/E27_preregistration.json, commit ecc494b, 2026-11-05T08:52:40+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. ARCHIVE, not PROMOTE and not a platform acceptance: 'ACCEPT' is the study's own preregistered test, on the assumptions its preregistration declares.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

## 22. `E28-gold-sleeve`

> [Registered retroactively 2026-11-23] E27 accepted holding the equity market over a 20% fixed-income fund. Does a gold sleeve (Tehran-listed gold funds) improve on that, either as the defensive sleeve of a static mix or on its own? | H1_gold_as_defence: 70% equity / 30% gold has a higher Sharpe (excess over the 20% fund, 20-session blocks) than 70% equity / 30% fixed income. Memmel one-sided test. | H2_gold_alone: 100% gold sleeve has a higher mean 20-session return than the 20% fund. One-sided t-test.

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:12 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-05) |
| strategy version | examples/gold_sleeve_study.py as first committed 395efa0 (2026-11-05T08:58:32+03:30); result commit 173e089 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `5418595038bcd6e5d8014cff` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `1785911ced036527c0c30ec0` |
| reproducible | False |
| **verdict** | **ARCHIVE** |
| entry hash | `471f571e27acb4bb6befdec5` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E28_results.json, commit 173e089, 2026-11-05T08:58:59+03:30): {"verdict": {"H1_gold_as_defence": "ACCEPT", "H2_gold_alone": "ACCEPT", "alpha_per_hypothesis": 0.025}}. Preregistration: backtest_output/E28_preregistration.json, commit f1c4724, 2026-11-05T08:58:08+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. ARCHIVE, not PROMOTE and not a platform acceptance: 'ACCEPT' is the study's own preregistered test, on the assumptions its preregistration declares.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

## 23. `E30-outlook-forward-yield`

> [Registered retroactively 2026-11-23] If the bot had bought, each month, the companies the fundamental outlook (fundamental_outlook.py, as rebuilt on 2026-11-10) ranks highest by forward earnings yield, would it have made money -- and more than simply holding the same eligible companies, the index, or bills? | H1: The top-10 portfolio beats the eligible equal-weight portfolio (B1).

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:13 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-10 ('check whether trading the stocks the forward-looking section recommends would be profitable')) |
| strategy version | examples/outlook_backtest.py as first committed ee17634 (2026-11-10T22:23:02+03:30); result commit 8bd8ab8 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `4b3ebb5a594c9ecdb6317b83` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `34bd39a85f39263ec468b825` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `bd93440151daa1a3a9d79f07` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E30_results.json, commit 8bd8ab8, 2026-11-10T22:25:19+03:30): {"decision": "REJECT", "primary": {"beat_control_share_of_periods": 0.541, "control_eligible_ew": {"annualised_net_pct": 29.8, "cumulative_net_pct": 305.5, "mean_names": 85.3}, "excess_log_per_period_pct": 0.345, "first_half_mean_excess_pct": 0.028, "newey_west_t": 0.7, "p_one_sided": 0.2414, "periods": 61, "second_half_mean_excess_pct": 0.652, "strategy": {"annualised_net_pct": 35.0, "cumulative_gross_pct": 565.2, "cumulative_net_pct": 400.5, "mean_names": 9.8}, "top": 10}, "random_null": {"draws": 1000, "null_median_pct": 365.7, "null_p90_pct": 542.3, "percentile": 91.4, "strategy_gross_pct": 565.2}}. Preregistration: backtest_output/E30_preregistration.json, commit ee17634, 2026-11-10T22:23:02+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. REJECT follows the experiment's own verdict. The stage number says where the platform's record stops; it is NOT the reason the experiment gave.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

## 24. `E31-laggard-quality-flow-turn`

> [Registered retroactively 2026-11-23] Does a stock that has lagged the market, whose fundamentals are sound and cheap, and whose retail selling has just stopped, go on to beat the market? The user's request of 2026-11-10: 'the bot should say which stock has fallen behind the market and has a better future, and say buy it'. | H1: The F+L+S basket beats B1.

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:14 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-10 ('yes, do it')) |
| strategy version | examples/laggard_backtest.py as first committed 629e392 (2026-11-10T22:38:29+03:30); result commit 1ae0e20 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `b1c3181b91714841c309d6ad` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `7c2e4c2cecc3d15afbf2607a` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `4857c1a0acb0878cc124de6e` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E31_results.json, commit 1ae0e20, 2026-11-10T22:43:25+03:30): {"decision": "REJECT", "primary": {"annualised_net_pct": 33.5, "control_annualised_net_pct": 29.8, "control_cumulative_net_pct": 305.5, "cumulative_net_pct": 370.6, "excess_log_per_period_pct": 0.244, "first_half_mean_excess_pct": 0.745, "label": "F+L+S", "newey_west_t": 0.65, "p_one_sided": 0.2588, "real_basket_periods": 29, "second_half_mean_excess_pct": -0.241}, "random_null": {"null_median_pct": 175.6, "percentile": 89.7, "strategy_gross_real_periods_pct": 302.1}}. Preregistration: backtest_output/E31_preregistration.json, commit 629e392, 2026-11-10T22:38:29+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. REJECT follows the experiment's own verdict. The stage number says where the platform's record stops; it is NOT the reason the experiment gave.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

## 25. `E32-forward-fundamental-quality`

> [Registered retroactively 2026-11-23] Would buying the companies whose forward-looking fundamentals are BEST -- profit rising, sales outgrowing others, margins widening, profit turning into cash -- regardless of their price, have made money, and more than holding every eligible company? The user's request of 2026-11-10: 'check buying the stocks that had better forward-looking fundamentals too'. | H1: The top-10 quality portfolio beats B1.

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:14 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-10) |
| strategy version | examples/quality_backtest.py as first committed 7b31fba (2026-11-10T22:47:07+03:30); result commit 73b767c |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `43fda9d0dc5266e57202b436` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `a3255eb0bdee7f3c06ad4d76` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `bd05656327c8cbd3ca0b2bb7` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E32_results.json, commit 73b767c, 2026-11-10T22:50:05+03:30): {"alpha": 0.0167, "decision": "REJECT", "primary": {"beat_control_share_of_periods": 0.541, "control_eligible_ew": {"annualised_net_pct": 29.8, "cumulative_net_pct": 305.5, "mean_names": 85.3}, "excess_log_per_period_pct": 0.279, "first_half_mean_excess_pct": 0.473, "newey_west_t": 0.69, "p_one_sided": 0.2444, "periods": 61, "second_half_mean_excess_pct": 0.09, "strategy": {"annualised_net_pct": 34.0, "cumulative_gross_pct": 541.5, "cumulative_net_pct": 380.6, "mean_names": 9.8}, "top": 10}, "random_null": {"null_median_pct": 371.5, "percentile": 89.3, "strategy_gross_pct": 541.5}}. Preregistration: backtest_output/E32_preregistration.json, commit 7b31fba, 2026-11-10T22:47:07+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. REJECT follows the experiment's own verdict. The stage number says where the platform's record stops; it is NOT the reason the experiment gave.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

## 26. `E33-best-of-cheap-industries`

> [Registered retroactively 2026-11-23] Would buying the best company of each industry that is cheaper than usual have made money, and more than holding every eligible company? The user's request of 2026-11-11: 'test the assumption that if you buy the best stock from industries cheaper than usual, you profit'. | H1: The best-of-cheap-industries portfolio beats B1.

| Field | Value |
|---|---|
| date | 2026-11-24T09:14:15 |
| author | retroactive registration 2026-11-23 (original researcher, as preregistered: Claude, at the user's request of 2026-11-11) |
| strategy version | examples/cheap_sector_backtest.py as first committed b2ddd6a (2026-11-11T21:00:41+03:30); result commit 0cad8a7 |
| code hash | `eacaa87f461236f9a9de172f` |
| git commit | `0e6b5087280c21b0c3ecc360` |
| config hash | `25a1e3dbd6e0e8c2530292cc` |
| dataset fingerprint | `fbdd13696870f334` |
| universe snapshot | `UNAVAILABLE` |
| preregistration hash | `75e211be8d2e1c2afa4d9ba7` |
| reproducible | False |
| **verdict** | **REJECT** |
| entry hash | `35f2c7a7caf3c77c7b94d389` |

**Rationale.** RETROACTIVE RECORD, registered after the fact on 2026-11-23. Verdict as the experiment's own result file states it (backtest_output/E33_results.json, commit 0cad8a7, 2026-11-11T21:04:10+03:30): {"alpha": 0.0125, "decision": "REJECT", "primary": {"active_periods": 36, "beat_control_share_of_active_periods": 0.556, "control_annualised_net_pct": 23.6, "control_cumulative_net_pct": 106.2, "excess_log_per_period_pct": 0.175, "first_half_mean_excess_pct": -0.17, "mean_names_when_active": 8.8, "newey_west_t": 0.35, "p_one_sided": 0.3648, "periods": 37, "second_half_mean_excess_pct": 0.503, "strategy_annualised_net_pct": 26.0, "strategy_cumulative_net_pct": 120.0}, "random_null": {"draws": 1000, "null_median_pct": 106.5, "null_p90_pct": 204.9, "percentile": 84.7, "strategy_gross_pct": 179.7}}. Preregistration: backtest_output/E33_preregistration.json, commit b2ddd6a, 2026-11-11T21:00:41+03:30. The Experiment Manager did not adjudicate this experiment when it ran: none of its six stages was run, so Stage 1 is recorded as not run, which the platform reads as a fail. REJECT follows the experiment's own verdict. The stage number says where the platform's record stops; it is NOT the reason the experiment gave.

| Stage | Status | Checks |
|---|---|---|
| 1 DATA INTEGRITY | **FAIL** | failed: missing_data, point_in_time, price_sanity, survivorship, universe_valid |

---

