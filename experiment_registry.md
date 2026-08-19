# SIRAT-AI — EXPERIMENT REGISTRY

**The complete record. Nothing here is overwritten; superseded results are
marked, not deleted.**

Last updated 2026-08-18.

---

## Version history

| Version | Change | Effect on prior results |
|---|---|---|
| **v2.0** | The state as of Experiment 3 | baseline |
| **v2.1** | P3 diagnostic reporting repair; P0 idle-cash accrual (`credit_idle_cash`, **default off**) | **None.** P3 is reporting-only; P0 is inert unless opted into. Every v2.0 number remains reproducible bit for bit. |

---

## Registry

### E1 — Market Exposure Baseline
| | |
|---|---|
| **Hypothesis** | A deterministic four-signal market-state engine decides *when* and *how much* to be invested better than fixed passive exposure. |
| **Preregistration** | Frozen in `experiment_exposure.py` before the run: six conditions, 30 seeds, 20% relative drawdown and 0.10 Sharpe margins. |
| **Success criteria** | All six conditions. |
| **Result** | **PASS as scored** — engine Sharpe 1.49 vs passive 1.36, MaxDD −25.77% vs −40.56%. |
| **Status** | ⚠️ **Overturned.** Conditions 1 and 2 were scored against buy-and-hold, not against the exposure-matched control `fixed_matched` (Sharpe 1.43, MaxDD −16.35%, Calmar 2.45). Against the correct control the engine is *worse* on every risk measure. |
| **Artifact** | `backtest_output/exposure_experiment.json` |

### E2 — Conditional Alpha Core (close-fill)
| | |
|---|---|
| **Hypothesis** | Alpha Core selection adds value conditional on the exposure path. |
| **Result** | ~100th percentile, ~62% CAGR. |
| **Status** | ❌ **Rejected as an execution artifact.** Close-fill assumption. |
| **Artifact** | `backtest_output/conditional_experiment.json` |

### E3 — Execution Realism
| | |
|---|---|
| **Hypothesis** | Alpha Core beats random selection under production execution. |
| **Preregistration** | 120 decision dates, 30 seeds, frozen exposure identical across arms; criterion "alpha core sharpe > random p95 sharpe". |
| **Result** | **FAIL** — 26.7th percentile, empirical p = 0.7419. |
| **Status** | ✅ Stands as a *relative* result. ⚠️ Sharpe *levels* uninterpretable (idle cash 0% vs a 23% hurdle). Realized exposure 29.22% vs a 54.79% target. |
| **Artifact** | `backtest_output/execution_realism.json` |

### E4 — Exposure Engine Holdout Validation
| | |
|---|---|
| **Hypothesis** | The exposure engine delivers incremental value on unseen data. |
| **Preregistration** | `backtest_output/holdout_preregistration.json`, 8 gates, written before any holdout number. Control fixed at 55.02% = the *training-window* average. |
| **Success criteria** | All 8 gates. |
| **Result** | **FAIL — 1 of 8.** Engine 42.24% CAGR vs control 43.31%; Sharpe 1.39 vs 1.77; MaxDD −14.87% vs −10.75%. Only significant finding: the engine's drawdown is *deeper* (bootstrap CI [−9.33, −1.16]). |
| **Status** | ✅ Clean negative — G8 (research validity) passed. **Exposure engine RETIRED.** |
| **Artifact** | `backtest_output/holdout_validation.json`, `holdout_report.md` |

### E5 — Experiment 4 Preflight (Alpha Core OOS)
| | |
|---|---|
| **Hypothesis** | *(never tested)* |
| **Result** | **INVALID — not run.** Four preconditions failed: no untouched holdout (§5), git absent (§29), survivorship (§22), exposure inequality (§7). |
| **Key finding** | `research_factors.py` labels 2024-09-01 → 2026-06-30 "the fitted window"; Alpha Core's two factors were already measured there (17,695 obs, 23 dates). |
| **Status** | ✅ Correctly stopped. No holdout burned. |
| **Artifact** | `backtest_output/experiment4_preflight_audit.md` |

### E6 — Alpha Core Rank-IC Diagnostic
| | |
|---|---|
| **Hypothesis** | The Alpha Core score orders future returns. |
| **Preregistration** | Thresholds fixed in `research_alpha_ic.py` docstring before any number: 30-name IC floor, 50-name decile floor, horizons 5/10/20/60, 200-permutation null, seed 20260818. |
| **Success criteria** | Mean IC > 0, ICIR > 0.3, % positive > 50, not concentrated. |
| **Result** | **PASS at h=5,10 · FAIL at h=20 (traded) and h=60.** ICIR 0.219 at the traded horizon vs a required 0.30. Signal is real: z = +8.3 vs a permutation null. Blend beaten by %B alone at every horizon (−41% of IC at h=20). IC negative over 2015–2019. |
| **Status** | ✅ Stands. In-sample for factor selection. |
| **Artifact** | `backtest_output/alpha_core_rank_ic.json`, `alpha_core_rank_ic_report.md` |

### E7 — Alpha Preservation Audit
| | |
|---|---|
| **Type** | Diagnosis, no experiment. |
| **Key finding** | Exposure loss is **100% slot occupancy** (occupancy −29.42pp, sizing **+3.85pp**). Attribution machinery existed and was discarded by two reversed key names and one omitted read. |
| **Status** | ✅ Stands. |
| **Artifact** | `backtest_output/experiment5_alpha_preservation_audit.md` |

### E8 — P3 Diagnostic Reporting Repair (v2.1)
| | |
|---|---|
| **Change** | Corrected four key mappings; exposed `cash_analysis` and `entry_rejections`; separated Layer A / Layer B counters with `assert_layers_disjoint()`. |
| **Result** | Diagnostic nulls 4 → 0; fields 34 → 38. **775 tests pass.** |
| **Risk to results** | **Zero** — `profile()` runs after the engine returns and writes nothing back. |
| **Artifact** | `implementation_fix_spec.md` |

### E9 — Experiment 5A, Frictionless Alpha Isolation
| | |
|---|---|
| **Hypothesis** | Alpha Core creates portfolio value *before* the allocator and order book are introduced. |
| **Preregistration** | `experiment5A_design.md`, written before the run. Baskets 10 and 47 fixed a priori (production size, and the rank-IC decile). Seeds 1–30. |
| **Success criteria** | Alpha CAGR > random mean **and** paired test significant **and** mean period difference > 0. |
| **Result** | **PASS LOCALIZATION, both baskets.** Top-10: 80.41% vs 24.32%, 100th percentile, t = 4.04, p = 5×10⁻⁵. Top-47: 69.26% vs 28.08%, 100th percentile, t = 6.35, p ≈ 2×10⁻¹⁰. Robust to removing the 10 best of 120 periods. |
| **Caveats** | In-sample for factor selection; survivors-only universe; **turnover 11.26/yr vs the production system's 2.75**. Both arms lose to cash in 2 of 4 sub-periods. |
| **Status** | ✅ Stands as localization. **Not** alpha validation. |
| **Artifact** | `backtest_output/experiment5a_frictionless.json`, `experiment5A_frictionless_results.md` |

### E10 — P0 Idle-Cash Accrual (v2.1)
| | |
|---|---|
| **Change** | `Portfolio.accrue_cash()` credits idle cash at the policy rate, compounded per session; called at the top of the engine's session loop. Gated by `cfg.credit_idle_cash`, **default False**. |
| **Correction** | The defect is in `backtesting/portfolio.py`, **not** `backtest_portfolio.py` as earlier documents stated. Experiment 3 runs through `backtesting.engine`. Defect identical in substance; file name was wrong. |
| **Result** | **784 tests pass.** Default-off verified by test: cash does not move, interest is 0.0. |
| **Risk to results** | **Zero while off.** v2.0 remains reproducible. |

### E11 — Experiment 5B, Production Pipeline (v2.1)
| | |
|---|---|
| **Hypothesis** | Alpha Core retains incremental value through the real allocator and order book. |
| **Preregistration** | `experiment5A_design.md` §3–4. Gate: `recoverable_fraction = (B − CB) / (A1 − C1)`; >50% continue, 20–50% structural limits, <20% redesign. Validity gate V1: exposure gap ≤ 1.0pp. |
| **Design** | Retired exposure engine excluded. `credit_idle_cash=True`. Same 120 dates, top-10, 30 seeds, stops off. |
| **Result** | **FAIL — 3.3rd percentile, empirical p = 0.9667.** B CAGR 17.84% vs CB mean 23.90%. Production edge **−6.06pp**. `recoverable_fraction = −0.108`. Gate: **STOP AND REDESIGN SPECIFICATION.** |
| **Validity** | V1 exposure neutrality **FAILED** — B 48.94% vs CB 42.37%, gap 6.57pp against a 1.0pp limit. The gap favours **B**, so the negative conclusion holds *a fortiori*. B also beat CB on fill rate (89.82% vs 76.43%) and cost drag (131.73% vs 189.94%) and still lost. |
| **Status** | ✅ Clean negative. The implementation fixes worked — exposure 29.22% → 48.94%, positions 4.63 → 5.8 — and the alpha did **not** appear. |
| **Artifact** | `backtest_output/experiment5b_production.json`, `production_pipeline_audit.md` |

### E12 — Phase 3 Decision Gate
| | |
|---|---|
| **Criterion** | Preregistered in `experiment5A_design.md` §7 before Arm B ran: >50% continue · 20–50% structural limits · <20% redesign. |
| **Computed** | `(B − CB) / (A1 − C1) = (17.84 − 23.90) / (80.41 − 24.32) = −6.06 / 56.09 = **−10.8%**` |
| **Result** | **BELOW the 20% floor, and negative.** |
| **Decision** | **STOP AND REDESIGN SPECIFICATION.** Mission Outcome B — a predefined gate failed and the reason is documented. |
| **Artifact** | `production_readiness_scorecard.md`, `risk_register.md` |

### E13 — Alpha Decay Diagnosis
| | |
|---|---|
| **Hypothesis** | The signal exists but decays faster than the trading system can harvest it. |
| **Preregistration** | Horizons 1/5/10/20/60 fixed a priori; `fwd_1` validation tolerance (0.05pp, ≥95% of rows) fixed before the check ran. Window restricted to clean pre-2024-09 data. |
| **Result** | **CONFIRMED.** IC 0.10492 @h=1 (ICIR 0.805, t=8.78, 81.5% positive) → **0.01456 @h=20** (ICIR 0.113, t=1.23, 51.7% positive). Accrual falls **23×** from session 1 to sessions 11–20. Fitted half-life 11.36 sessions; fast component ~3. |
| **Key economics** | At h=1 alpha is 0.55% against a 1.04% round trip → **−69%/yr**. Viable horizon h=5–10 needs 24–48 turnovers/yr against a demonstrated **4.45**. Production held names **53.9 sessions = 4.7 half-lives** past decay. |
| **Correction to E6** | E6's h=20 figures (IC 0.0292, ICIR 0.219) included the contaminated 2024H2–2026 block. On clean data: **0.0146 / 0.113** — the contaminated window inflated the traded-horizon headline ~2×. |
| **Validity** | ✅ `fwd_1` rebuilt from `price_history` and validated against the panel's own `fwd_5`: median abs diff 0.00021pp, 98.31% within tolerance. |
| **Status** | ✅ **Root cause identified.** Failure is economic (category C), not research or implementation. |
| **Artifact** | `backtest_output/alpha_decay_analysis.json`, `postmortem_and_next_phase.md` |

### E14 — Queue-Pressure Artifact Falsification
| | |
|---|---|
| **Hypothesis** | H1 — the h=1 signal detects stocks entering or remaining in price-limit queues (صف خرید), not persistent alpha. |
| **Preregistration** | Thresholds fixed in the module docstring before any number: queue exposure ≥2.0×, artifact = ≥50% collapse **and** below the round trip, executable floor 30%. Band **not assumed** — measured per date. |
| **Result** | **QUEUE EXPOSURE CONFIRMED at 3.03×** (top decile 7.75% locked on t+1 vs universe 2.56%, bottom decile 0.92%). 7.75% of the decile carries **58.7%** of the h=1 excess, returning **+4.10%** in one session. |
| **Matched test** | At equal score, sector, ADV quintile and volatility quintile: queue-flagged names +1.1039% excess (**t = +12.47**); non-queue names **+0.1183% (t = +1.09)**. A − B = **+0.9856% (t = +8.29)**. |
| **Exclusion** | Removing t+1 locks collapses h=20 IC by **89%** (0.01456 → 0.00154, t = 0.13). Removing locks **and** illiquid names turns it **significantly negative**: IC −0.0249, **t = −2.01**. |
| **Execution** | Nothing clears the 1.04% round trip in any specification — baseline 0.5496%, ex-locks 0.3985%, matched Group B **0.1183%**. Executable share 41.3%–72.5% of an excess that never cleared cost. |
| **Defects found** | 3, all corrected mid-run: the 33% stale-bar trap; reopening gaps averaging 115%; `shift(-h)` moving *rows* not sessions (produced a −579% excess). Corrected baseline reproduces E13 exactly. |
| **Automated verdict** | "REAL BUT UNEXECUTABLE" — the narrow pre-registered rule removes only *realized* locks (27.5% collapse, below the 50% trigger). Reported unchanged in the JSON. |
| **Final verdict** | **SIGNAL IS QUEUE ARTIFACT.** The matched comparison — required by the brief's COMPARE section, not chosen post hoc — is the faithful test of H1 as written (queue *exposure*, 45.6% of the decile) rather than a strict subset (realized locks, 7.75%). Divergence documented in the report. |
| **Status** | ✅ **Root cause closed.** The score identifies stocks about to become unbuyable. |
| **Artifact** | `backtest_output/experiment14_queue_artifact.json`, `experiment14_queue_artifact_report.md` |

### E15 — Execution-Aware Alpha Validation
| | |
|---|---|
| **Hypothesis** | An execution-aware filter using only decision-time information converts the Alpha Core signal into a positive net edge after costs. |
| **Preregistration** | `experiment15_preregistration.json`, sha256 `840af9be…`, locked before any result. Records E14's oracle bound (+0.397% vs a 1.039% round trip) as the **ceiling**, and states in advance that failure is expected. |
| **Filter** | 5 rules, **zero fitted parameters**, no future data: locked at t · closed at high at t · volume > 3× own 60-session median · bottom-quintile ADV · stale. 56.9% of the universe survives. |
| **Filter worked** | Queue exposure **5.01× → 1.53×** (−69%); fill rate **84.9% → 96.0%**. |
| **Alpha went with it** | IC h=1 **0.10492 → 0.03033** (−71%); h=5/10/20 all **flip negative** (−0.022, −0.034, −0.032). |
| **Execution shortfall** | Arm A requests **+3.1735%** at h=20 and realises **+0.5930%** — **81% lost to execution**. Arm B, which fixes execution, only ever requests +0.2669%. |
| **Result** | **FAIL — 0 of 6 gates substantively.** G1 missed by 0.03 (1.53 vs ≤1.50); G2, G4, G6 fail outright. G3 and G5 "pass" only by losing less than a worse control (Arm B at h=1: −86.21%/yr). Net alpha negative at **every** horizon. |
| **Status** | ✅ Clean negative. Slippage held at 0.0% — a failure at zero slippage is a failure at any. |
| **Decision** | **ARCHIVE SIRAT-AI TRADING SPECIFICATION.** No untested path remains within this specification. |
| **Artifact** | `backtest_output/experiment15_execution_aware.json`, `experiment15_execution_aware_report.md` |

### E16 — Experiment Manager (research governance layer)
| | |
|---|---|
| **Type** | Platform build, not an experiment. Recorded here because the registry is the record of everything that changes what a result means. |
| **Problem** | Validation existed and was never used *by the research path*. `walk_forward()`, `out_of_sample()` and `monte_carlo()` are wired into `run_v2.py`, `backtesting/runner.py` and `validate_v2.py` — and **zero of the fifteen experiment scripts call any of them**. Governance was `experiment_registry.md`, maintained by hand, with no power to refuse. |
| **Built** | `sirat/experiments/` — Manager, six stage gates, append-only store with immutability triggers, provenance, universe snapshots, CLI. `sirat/fixtures/alpha_core.py` — the adversarial regression fixture. `ci.py`, `.github/workflows/ci.yml`, `git_bootstrap.py`. |
| **Enforced** | No run without registration · preregistration frozen and hash-verified on every read · stages cannot run out of order or after a cheap failure · a missing check is a failure, not a pass · a holdout window can be spent once · **PROMOTE refused without all six stages and a reproducible result**. |
| **Fixture** | All **11 anchors reproduce**: queue ratio 3.02828 (3.03 ±0.15) · h=1 excess 0.54957 · requested +3.17348 / realised +0.59298 at h=20 · 3.33rd percentile · IC curve 0.10492 / 0.05101 / 0.03003 / 0.01456 to five decimals. Tolerances **two-sided**: a favourable drift fails identically. |
| **Correction to the V2 plan** | `SIRAT_AI_RESEARCH_PLATFORM_V2_IMPLEMENTATION_PLAN.md` §7 states the validation functions have "0 uses". Literally false — they are called from the backtest path. The accurate and worse finding is that the *experiment* path never touched them. |
| **Defect found and fixed** | `universe.due()` read only a snapshot's timestamp, so the pre-existing `UNIV-20260818` row — which does not restore — reported the universe as fresh and frozen when it was neither, and the weekly schedule would never have fired again. `due()` now verifies restorability and treats an unverifiable snapshot as absent. |
| **Week 1 status** | Provenance ✅ · universe snapshot scheduler ✅ (`u20260819-668658bb`, 1,394 instruments, restorable) · **git ⚠️ BLOCKED — not installed on this machine.** Reproducibility remains 0% and PROMOTE is refused platform-wide until it is. |
| **Tests** | 126 new, **910 total, all passing.** |
| **Risk to prior results** | **Zero.** No file that computes a number was modified. Alpha Core is hash-pinned and untouched. |
| **Artifact** | `EXPERIMENT_MANAGER.md`, `sirat_research.db` |

---

## Programme outcome

**ARCHIVED, 2026-08-18.** Every component tested and closed:

| Component | Status | Closed by |
|---|---|---|
| Exposure engine | RETIRED | E4 — 1 of 8 gates OOS |
| Alpha Core v1 | REJECTED | E11 — 3.3rd percentile, recoverable fraction −10.8% |
| Signal mechanism | QUEUE ARTIFACT | E14 — 3.03× queue exposure, non-queue residual t = 1.09 |
| Execution-aware rescue | FAILED | E15 — filter removes the artifact *and* the alpha |

The forward holdout was **never started and never burned**. Alpha Core was
**never modified**. The programme ends with a complete causal account rather
than an unexplained failure.

---

## Rules in force

1. **Alpha Core is frozen** — no factor, weight, threshold, ranking or feature change until a separate preregistered model-research phase.
2. **No ML** until a clean point-in-time universe, stable alpha evidence, and reliable execution measurement all exist. Currently **0 of 3**.
3. **Every experiment carries** a hypothesis, a preregistration, success criteria and failure criteria before it runs.
4. **Every modification is versioned.** Prior results are never overwritten.
5. **The forward holdout has not started.** No post-2024-09-01 data has been used to evaluate Alpha Core.
