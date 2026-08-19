# SIRAT-AI — RESEARCH LEDGER

**Append-only. Generated from `sirat_research.db`; never edited by hand.**

Each entry hashes the one before it. Editing, deleting or reordering any entry
breaks every link after it and `ledger.verify()` names the first entry that fails.

| | |
|---|---|
| entries | 13 |
| chain intact | **True** |
| head | `fcc4684bb2c5d394fe2e9d64024b8462eba24ceb47c702ef07a99141dc73b551` |
| exported | 2026-08-19T11:25:57 |

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

