# EXPERIMENT 5 — ALPHA PRESERVATION AUDIT + IMPLEMENTATION FIX PLAN

**Diagnosis and experiment design only · 2026-08-18**

No backtest run. No holdout evaluated. No model parameter read, changed or
selected. Every number below is read from stored artifacts or from source.

---

## 0. Headline

The exposure-attribution machinery this audit was asked to build **already
exists, is already wired, and is already correct.** It was discarded at the
reporting layer by two key-name mismatches and one omitted key. Phase 2's
deliverable is not a missing capability — it is three lines of reporting code.

The second finding is arithmetic and exact: **the exposure shortfall is entirely
slot occupancy, not position sizing.** Sizing is, if anything, mildly
compensating.

---

## 1. Phase 1 — Portfolio Pipeline Audit

### The path, stage by stage

| # | Stage | Input | Output | Filtering / constraint | How a valid Alpha candidate is removed |
|---|---|---|---|---|---|
| 1 | **Alpha score** `alpha_core.py:264` | per-name readings: `momentum`, `bb_pct` | `alpha` ∈ 0–100 | name dropped if **either** input is NaN | **7.3% of rows** (6,538 of 89,861) lost for a missing %B alone |
| 2 | **Ranking** `alpha_core.py:529` | scored names | list sorted by `alpha` desc | none | none |
| 3 | **Candidate selection** `alpha_core.py:515-521` | ranked names | `buys` list | `factor_engine.decide()` must return BUY; `trade_manager.plan()` must build a plan | no stop constructible · size below floor · reward/risk under floor. Counted in `self.rejections` |
| 4 | **Basket cap** `engine.py:606-616` | `buys` | top **N = 10** | `cfg.max_positions = 10` | everything past rank 10 → `ranked_out` |
| 5 | **Sector constraints** `portfolio_engine.allocate()` | top-10 in rank order | accepted / rejected | `MAX_PER_SECTOR = 2`, `MAX_SECTOR_PCT = 30%`; **two-pass**: one name per sector first, then seconds | rank-3 in an already-doubled sector is refused while rank-9 in a fresh sector is admitted |
| 6 | **Position sizing** `sizing.py`, mode `risk_based` | accepted signal + ATR | `allocated_value`, shares | `RISK_PER_TRADE_PCT = 1.0`, stop = `2.0 × ATR`, cap `MAX_POSITION_PCT = 15%`, floor `MIN_POSITION_PCT = 2%`, `spread_ceiling()` divides the budget | a high-ATR name sizes below 2% and is dropped (`below_min_position`); `shares <= 0` → `sized_to_zero` |
| 7 | **Order generation** `engine.py:628-670` | sized positions | BUY/SELL orders | T+1 — `eligible_from` is the next session | none directly |
| 8 | **Execution simulation** `execution.py` | orders | fills | queue detection (band proximity + volume collapse), participation cap, partial fills, expiry | صف خرید blocks entry · participation caps the size · order expires unfilled |
| 9 | **Portfolio exposure** `portfolio.py:125` | fills + cash | `Book.value() = cash + holdings_value()` | `MIN_CASH_PCT = 10%`, `TARGET_INVESTED_PCT = 90%`, plus the frozen exposure cap | cash floor refuses the marginal position |

### Bottleneck table

Ordered by measured or structurally-implied severity.

| Rank | Bottleneck | Stage | Evidence | Severity |
|---|---|---|---|---|
| 1 | **Slot occupancy — 4.63 of 10 held** | 4–8 | `average_positions 4.63`; see §2 | **Critical — explains 100%+ of the exposure gap** |
| 2 | **`portfolio_full`** — 240 rejections | 5 | 48.8% of allocator refusals | High |
| 3 | **`cash_floor`** — 172 rejections | 5/9 | 35.0% of refusals; `MIN_CASH_PCT = 10%` binds against a 90% invested target | High |
| 4 | **Top-10 of ~470 = the top 2%** | 4 | the rank-IC study measured the top *decile* (~47 names); the book takes a fifth of that | High — a noise amplifier, not a constraint bug |
| 5 | **`below_min_position`** — 66 rejections | 6 | 13.4% of refusals; the 2% floor vs risk-based sizing on high-ATR names | Moderate |
| 6 | **Unfilled buys** — 30 of 448 expired | 8 | fill rate 93.3% | Moderate |
| 7 | **Partial fills** — 16.68% | 8 | 168 of 1,007 fill events partial | Moderate |
| 8 | **`queue_blocked_exit`** — 33 | 8 | صف فروش trapping exits | Low–moderate |
| 9 | **`sector_count`** — 14 rejections | 5 | 2.8% of refusals | Low |
| 10 | **Missing %B** — 7.3% of rows | 1 | drops names before they can ever be ranked | Low, but free to measure |

---

## 2. Phase 2 — Exposure Failure Analysis

### 2.1 The exact decomposition

This part is arithmetic on stored figures, not inference.

```
target exposure                       54.79%   (mean_target_exposure_pct)
realized exposure                     29.22%   (realized_exposure_pct)
gap                                  −25.57pp  (46.7% of target unrealized)

average positions held                 4.63    of 10 slots  → 46.3% occupancy
implied size per held position         6.31%   = 29.22 / 4.63
target size per slot                   5.48%   = 54.79 / 10

occupancy effect   (4.63 − 10) × 5.48%       = −29.42pp
sizing effect       4.63 × (6.31 − 5.48)     =  +3.84pp
                                               ─────────
net                                            −25.58pp   ✓ reconciles
```

### **The sizing model is not the problem. It is compensating.**

Held positions are **15% larger** than the equal-weight target (6.31% vs 5.48%).
Had all ten slots been filled at the observed size, exposure would have been
**63.1%** — *above* the 54.79% target, where the cash floor would have capped it.

**Every percentage point of the shortfall, and then some, comes from empty
slots.** Any fix aimed at position sizing is aimed at the wrong stage.

### 2.2 What drives the empty slots

Allocator refusals from `execution_realism.json` (Alpha Core arm):

| Cause | Count | Share of refusals |
|---|---:|---:|
| `portfolio_full` | 240 | 48.8% |
| `cash_floor` | 172 | 35.0% |
| `below_min_position` | 66 | 13.4% |
| `sector_count` | 14 | 2.8% |
| **Total** | **492** | **100%** |

> **These are shares of *refusals*, not shares of *exposure loss*.** They cannot
> be converted into percentage-point contributions from stored data — see §2.4.
> Reporting them as "liquidity X%, sector cap Y%" would be fabrication.

Execution-side losses, measured:

| Effect | Value |
|---|---|
| Buy orders raised | 448 |
| Buy orders expired unfilled | 30 → **fill rate 93.3%** |
| Partial fills | 168 of 1,007 → **16.68%** |
| Exits blocked by صف فروش | 33 |
| Turnover | 2.75 /yr |
| Cost drag | 34.85% cumulative |

### 2.3 The counters that were collected and thrown away

**This is the most actionable finding in the audit.**

`engine.py:400` executes `diagnostics.update(filler.stats)`, so the execution
counters *are* merged into diagnostics. The reporting layer then asks for the
wrong keys:

| `profile()` in `experiment_execution_realism.py` reads | `execution.py` actually writes | Result |
|---|---|---|
| `buy_queue_blocked` | **`blocked_buy_queue`** | `null` |
| `sell_queue_blocked` | **`blocked_sell_queue`** | `null` |
| `participation_capped` | **`capped_by_participation`** | `null` |
| `refused` | *(no such key)* | `null` |

Word order reversed on two, prefix changed on the third, fourth never existed.
**The صف and participation data from Experiment 3 was measured correctly and
silently discarded at report time.**

Worse — and better:

- **`engine.py:422` already computes `diagnostics["cash_analysis"]`.**
  `_cash_analysis()` attributes idle cash to exactly the categories Phase 2 asks
  for: *not enough signals · basket too small · portfolio caps · sizing ·
  orders raised and never filled (صف خرید)*. Its own docstring says
  *"Average cash 58.6% is a symptom with at least four distinct diseases, and
  they call for opposite remedies."* **`profile()` never reads it.**
- **`engine.py:420` already computes `diagnostics["entry_rejections"]`** via
  `rejection_summary()`, described in `alpha_core.py:564` as *"the shape
  engine._cash_analysis() reads"*. Also never read.

**Phase 2's entire deliverable is implemented, wired, and discarded by the
reporting layer.** Recovering it costs three lines and one re-run of an
already-run in-sample experiment. It requires no research change.

### 2.4 A reconciliation defect that must be fixed before trusting any attribution

`wanted_positions = 448` and `opened_positions = 448`, while `blocked_by` sums
to **492**. Read as one funnel these are impossible: a blocked candidate should
appear in `wanted` and not in `opened`.

The cause is that the two counters are written at **different layers**.
`engine.py:652/670` increments `wanted_positions` / `opened_positions` around
its own `book.can_open()` loop, while Alpha Core routes allocation through
`portfolio_engine.allocate()` (`alpha_core.py:533`), whose refusals populate
`blocked_by` separately.

**Consequence:** the stored funnel counters cannot be summed, and any exposure
attribution built on them today would be unsound. This must be reconciled
*before* the numbers in §2.2 are used for anything beyond ranking.

---

## 3. Phase 3 — Selection vs Execution Separation (design only)

Three arms over the **same universe, same dates, same frozen Alpha Core**. The
purpose is to place the loss on a specific stage by removing one stage at a time.

### Experiment A — Alpha-only (frictionless ceiling)

| | |
|---|---|
| **Purpose** | Measure pure ranking power as a portfolio, with the pipeline removed |
| **Construction** | Top-N by `alpha`, equal-weighted, rebalanced on the same grid |
| **Fills** | Perfect, at the decision close |
| **Liquidity / participation / queues** | Disabled |
| **Sector caps, cash floor, min position, max position** | **Disabled** |
| **Costs** | **Kept** — same 1.04% round trip |
| **Exposure** | Constant, pre-registered, **100% of the invested sleeve** |
| **Reports** | CAGR, Sharpe, realized exposure (must be ≈100%), turnover, cost drag |

A must be run at **two basket sizes: N = 10 and N = 47** (the top decile). The
rank-IC study measured the decile; the book takes the top 2%. Running both
prices the *concentration* decision separately from every other effect.

### Experiment B — Execution realistic

| | |
|---|---|
| **Purpose** | Measure total implementation loss, A − B |
| **Construction** | Current engine, unchanged: `portfolio_engine.allocate()`, `risk_based` sizing, sector caps, cash floor |
| **Fills** | Current model — T+1, queues, participation, partials |
| **Costs** | Same |
| **Exposure** | Same constant target as A |
| **Reports** | Everything A reports, **plus** the full recovered telemetry: `cash_analysis`, `entry_rejections`, `blocked_buy_queue`, `blocked_sell_queue`, `capped_by_participation`, `expired`, and a **per-date realized-vs-target exposure series** |

### Experiment C — Random control

| | |
|---|---|
| **Purpose** | Measure true incremental value; B − C is the only number that matters |
| **Construction** | `ranking="random"` — already built into `alpha_core.py:287-304`, replacing **only** `alpha_scores()` so eligibility, sizing, caps and execution are byte-identical |
| **Seeds** | ≥ 30, deterministic |
| **Exposure** | Same constant target, **and realized exposure must be verified equal to B's within a stated tolerance before any result is read** |
| **Reports** | Full distribution; Alpha Core's percentile |

### What the differences mean

| Quantity | Isolates |
|---|---|
| **A(N=47) − A(N=10)** | cost of concentrating from decile to top-2% |
| **A − B** | total implementation loss |
| **B − C** | true incremental selection value under realistic execution |
| **A − C(frictionless)** | selection value with no implementation at all |

**Pre-commitment required before running:** if realized exposure in B and C
differs by more than **1.0pp**, the comparison is void and must be re-run after
the exposure fix — not interpreted. Experiment 3's 1.97pp gap (29.22 vs 27.25)
is precisely the confound this rule exists to catch.

---

## 4. Phase 4 — Preregistration Proposal (frozen candidates)

**Proposal only. Nothing here is locked until the file is written and hashed.**

| | Candidate 1 | Candidate 2 | Candidate 3 |
|---|---|---|---|
| Name | **Alpha Core v1** | **%B-only v1** | **Random baseline** |
| Score | `0.5·rank(f_momentum) + 0.5·rank(bb_pct)` | `rank(bb_pct)` | seeded permutation |
| Source | `alpha_core.py:264`, unmodified | same module, momentum weight 0 | `ranking="random"` |
| Status | frozen 2026-08-18 | frozen 2026-08-18 | n/a |

### Rules

1. **No future tuning.** No weight, threshold, window, factor, basket size,
   rebalance frequency or cap may change after the file is hashed.
2. **No weight changes and no factor replacement**, including for Candidate 2 —
   it is a *fixed* zero-weight variant, not a tunable family.
3. **One evaluation date**, no earlier than **12 months** after the start, and
   evaluated **once**.
4. **Multiple-comparison cost stated up front.** Three candidates are tested, so
   the per-candidate significance bar is tightened (Holm–Bonferroni over the two
   non-null arms). This must be written into the file, not applied afterwards.
5. **Candidate 2 is declared to originate from the 2026-08-18 rank-IC
   diagnostic**, which is in-sample for it. This is why it must be frozen *now*
   — naming it later would make the holdout a test of a hypothesis generated
   from the test data.
6. **Exposure equality is a gate, not a metric.** If realized exposure differs
   across arms by more than 1.0pp, the evaluation is void.

---

## 5. Phase 5 — Required Code Changes Before the Holdout

Priority order. **Every item is engineering; none touches a model parameter.**

### P1 — Exposure engine correction

| | |
|---|---|
| **Affected** | `experiment_execution_realism.py` (`ExposureConstrainedAlphaCore.policy_for`), `portfolio_engine.py` (`admit`, `capacity`, `spread_ceiling`), `config.MIN_CASH_PCT` interaction |
| **Problem** | The exposure target is applied as a *ceiling* that "only ever tightens", then `MIN_CASH_PCT = 10%` and `portfolio_full` bind underneath it. Realized 29.22% against 54.79% target. |
| **Fix** | Provide a fixed-N equal-weight allocation path that meets a pre-registered constant exposure, bypassing the discretionary caps for research arms. Assert realized ≈ target per date. |
| **Expected impact** | Closes a 25.6pp exposure gap; makes A/B/C comparable at all |
| **Risk of changing research results** | **HIGH — intended.** Every prior portfolio-level number becomes non-comparable. Must be versioned, and prior results re-labelled as "pre-fix", not silently superseded. |

### P2 — Allocation reliability

| | |
|---|---|
| **Affected** | `portfolio_engine.py` (two-pass sector spread, `spread_ceiling`), `backtesting/engine.py:606-670` |
| **Problem** | 4.63 of 10 slots held. `portfolio_full` (240) and `cash_floor` (172) are 83.7% of refusals. Funnel counters recorded at two layers and unreconcilable (§2.4). |
| **Fix** | Unify the funnel counters into one layer so every candidate has exactly one terminal disposition; emit a per-date `slots_wanted / slots_filled / reason` record. |
| **Expected impact** | Makes the occupancy failure attributable rather than merely visible |
| **Risk** | **LOW** if counting only. **HIGH** if the two-pass sector logic is altered — do not, in this phase. |

### P3 — Fill accounting audit

| | |
|---|---|
| **Affected** | `experiment_execution_realism.py` (`profile()`), any other reporting layer reading diagnostics |
| **Problem** | Four counters read under wrong names (§2.3); `cash_analysis` and `entry_rejections` computed and never read |
| **Fix** | Rename the reads to `blocked_buy_queue`, `blocked_sell_queue`, `capped_by_participation`, `expired`; add `cash_analysis` and `entry_rejections` to the emitted profile |
| **Expected impact** | Recovers Phase 2's entire attribution **with no research change** |
| **Risk** | **ZERO.** Reporting only — no path that produces a return is touched. **Do this first: it is free and it may change which of P1/P2 matters most.** |

### P4 — Historical universe snapshots

| | |
|---|---|
| **Affected** | new scheduled job; `backtesting/survivorship.py` (which currently, correctly, refuses to invent a correction) |
| **Problem** | Universe is today's listing. `survivorship.py`: *"this project has never collected one."* Measured visible attrition 1.7%/decade is a lower bound only. |
| **Fix** | Record the traded instrument list weekly from today forward. **Cannot repair history.** |
| **Expected impact** | Nothing for ~2 years; the only path to a clean universe thereafter |
| **Risk** | **ZERO** to existing results (additive data collection) |

### P5 — Git provenance

| | |
|---|---|
| **Affected** | repository root; `.gitignore` must exclude `sirat_market.db` (424 MB), `__pycache__/`, `backtest_output/cache/` |
| **Problem** | git is **not installed on this machine**; no commit hash obtainable; mtimes are not a substitute |
| **Fix** | Install git, `git init`, commit the research code, record the hash in every provenance file |
| **Expected impact** | Makes any future holdout reproducible and auditable |
| **Risk** | **ZERO** to results |

### One more, not in the brief's list but higher priority than P4/P5

### P0 — Idle-cash accrual

| | |
|---|---|
| **Affected** | `backtesting/portfolio.py` — `Portfolio.cash` moves only on `-= outlay` (line 256) and `+= gross - cost` (line 302); `backtesting/metrics.py:128` charges a 23% hurdle |
| **Correction (2026-08-18)** | An earlier draft named `backtest_portfolio.py`. That file exists but is **not** the harness Experiment 3 runs: `experiment_execution_realism.py` imports `backtesting.engine`, which uses `backtesting/portfolio.py`. The defect is real and identical in substance; the file name was wrong. |
| **Problem** | With ~71% of the book idle, cash earns 0% while being charged a 23% hurdle — a double penalty. **Every Sharpe from this harness is uninterpretable as a level.** |
| **Fix** | Credit idle cash at `config.RISK_FREE_RATE` per session, as `experiment_exposure.simulate()` already does |
| **Expected impact** | Sharpe levels become comparable across harnesses for the first time |
| **Risk** | **HIGH — intended.** Changes every stored portfolio-level Sharpe. Must be versioned. |

**Recommended order: P3 (free, zero-risk, informative) → P0 → P1 → P2 → P5 → P4.**

---

## 6. Phase 6 — ML Gate Decision

> **Are the prerequisites satisfied?**

# No. None of the three is satisfied.

| Criterion | Status | Evidence |
|---|---|---|
| **1. Clean PIT data** | **FAIL** | Point-in-time *computation* is verified sound (`closes[:index+1]`, staleness guard, forward returns strictly after the decision bar). But the **universe** is survivors-only, and `survivorship.py` states no historical snapshot has ever been collected. Clean PIT requires both. |
| **2. Stable alpha signal** | **FAIL** | Mean IC negative over 2015–2019 (−0.0081 across 60 dates, 45% positive). ICIR 0.219 at the traded horizon against a pre-specified 0.30. Removing the contaminated window halves the headline IC. |
| **3. Reliable execution measurement** | **FAIL** | Realized 29.22% vs 54.79% target; funnel counters unreconcilable across two layers (§2.4); four execution counters reported `null` through a key-name bug; `cash_analysis` computed and discarded. |

### Why ML would increase overfitting risk right now

1. **It would learn the survivorship pattern.** Trained on a universe containing
   only companies that never delisted, a model learns "which stocks did not get
   removed from the exchange." That label is unavailable at decision time and
   the failure mode is silent and catastrophic in live trading.

2. **It would fit the instability rather than see through it.** The signal works
   in 2020–2026 and does not work in 2015–2019. With 143 monthly observations
   and ~4 distinguishable regimes, any learner has enough capacity to memorise
   which regime is which and no sample with which to generalise across them.

3. **It would learn the allocator's failures, not the market's.** The measured
   outcome is a portfolio that received 4.63 of 10 slots and 29% of a 55%
   exposure target. A model trained on *realized* portfolio returns is trained
   on a target contaminated by a fixable engineering defect — it would learn to
   predict `portfolio_full` and `cash_floor` and score itself for it.

4. **A trivial non-ML answer is already untested.** The rank-IC diagnostic
   showed %B alone dominates the 50/50 blend at every horizon. If a
   single-factor zero-weighting captures most of the available gain, an ML layer
   has very little left to earn and a great deal of variance to add. **Test the
   trivial answer first.**

5. **The holdout is spent.** ML needs train / validation / test. The only
   post-training data is already contaminated, so an ML phase would have no
   clean test set to be honest against — the same blocker that stopped
   Experiment 4, compounded by a far larger hypothesis space.

**Gate decision: ML remains closed.** Reopen only when the forward holdout
returns a positive exposure-neutral result for a frozen handcrafted candidate,
P0–P2 are fixed and verified, and a point-in-time universe has been accumulating
for at least a year.

---

## 7. Summary of Findings

| # | Finding | Type | Confidence |
|---|---|---|---|
| 1 | Exposure shortfall is **100% slot occupancy**; sizing contributes **+3.84pp** in the *right* direction | Exact arithmetic on stored data | **High** |
| 2 | Four execution counters are `null` because of **key-name mismatches**, not missing instrumentation | Source-verified | **High** |
| 3 | `cash_analysis` and `entry_rejections` — Phase 2's exact deliverable — are **computed and discarded** | Source-verified | **High** |
| 4 | Funnel counters are written at **two layers** and cannot be summed (`wanted 448` vs `blocked 492`) | Source-verified | **High** |
| 5 | The book takes the **top 2%**, while the rank-IC evidence is for the **top decile** | Structural | **High** |
| 6 | Idle cash earns 0% against a 23% hurdle, making every Sharpe from that harness uninterpretable as a level | Source-verified | **High** |
| 7 | Per-cause **percentage-point** attribution of exposure loss is **not recoverable** from stored artifacts | Absence of evidence | **High** |
| 8 | ML prerequisites: **0 of 3** satisfied | Derived | **High** |

---

## Provenance

| Item | Value |
|---|---|
| Task ID | EXP5-PRESERVATION-AUDIT-2026-08-18 |
| Backtests run | **None** |
| Holdouts evaluated | **None** |
| Model parameters read for selection | **None** |
| Files modified | **None** — this report is the only output |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
| Sources | `backtesting/alpha_core.py` · `backtesting/engine.py` · `backtesting/execution.py` · `backtesting/portfolio.py` · `backtesting/sizing.py` · `backtesting/survivorship.py` · `portfolio_engine.py` · `experiment_execution_realism.py` · `config.py` · `backtest_config.py` · `backtest_output/execution_realism.json` · `backtest_output/alpha_core_rank_ic.json` |
| Constants read | `PORTFOLIO_SIZE 10` · `MAX_POSITION_PCT 15.0` · `MIN_POSITION_PCT 2.0` · `MIN_CASH_PCT 10.0` · `MAX_PER_SECTOR 2` · `MAX_SECTOR_PCT 30.0` · `TARGET_INVESTED_PCT 90.0` · `RISK_PER_TRADE_PCT 1.0` · `STOP_ATR_MULTIPLE 2.0` · `SIZING_MODE risk_based` · `REBALANCE_SESSIONS 20` |
