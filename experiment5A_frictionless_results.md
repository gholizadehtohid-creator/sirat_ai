# EXPERIMENT 5A — FRICTIONLESS ALPHA ISOLATION: RESULTS

**Localization experiment · 2026-08-18 · `python experiment_5a_frictionless.py`**

> **This is not alpha validation and cannot be read as such.** The window is
> in-sample for Alpha Core's factor selection. The only question asked is
> whether Alpha Core creates portfolio value *before* the allocator and the
> order book are introduced.

---

## 1. Experiment Setup

| | |
|---|---|
| **Arms** | A1 alpha top-10 · A2 alpha top-47 · C1 random top-10 (30 seeds) · C2 random top-47 (30 seeds) |
| **Ranking** | Frozen Alpha Core v1 — `0.5·rank(f_momentum) + 0.5·rank(bb_pct)`, reproduced verbatim from `alpha_core.alpha_scores()` |
| **Weighting** | Equal weight |
| **Fills** | Perfect, at the decision close |
| **Rebalance** | Every 20 sessions — the production `REBALANCE_SESSIONS` |
| **Horizon** | `fwd_20`, which *is* the rebalance interval — no overlap, no stitching |
| **Costs** | **Kept.** 1.04% round trip charged on the fraction of the book replaced, the Experiment 1 convention |
| **Cash rate** | 23%/yr = 1.7401% per period, used for Sharpe and Sortino |
| **Allocator** | **Not used** |
| **Execution model / queues / participation / liquidity** | **Not used** |
| **Random control** | Same eligible set, same basket size, same dates, order replaced by a seeded permutation — the rule `alpha_core.py` uses for its own random arm |

**Why the panel and not the engine.** A frictionless arm built by running the
production engine with its constraints switched off would leave the result
depending on switches being off. The panel already carries the two ranking
inputs — written by the same `factor_engine.momentum()` call `alpha_core.py`
makes — and a forward return over exactly the rebalance horizon. The arm is
built where those effects do not exist rather than where they are disabled.

**Nothing was tuned.** Basket sizes 10 and 47 are the production size and the
decile the rank-IC study measured. Neither was chosen from a result.

---

## 2. Data Scope

| | |
|---|---|
| Window | **2014-09-21 → 2024-08-10** (last decision), bounded at 2024-09-01 |
| Decision dates | **120** — identical to Experiments 2 and 3 |
| Years | 10.00 |
| Eligible names per date | min 380, median 547, max 738 |
| Dates dropped as too thin (<47 names) | **0** |
| Names scored but lacking `fwd_20` | 43 of 89,861 (0.05%) |
| **Post-2024 data read** | **None.** The contaminated window was not touched. |

**Eligibility is shared.** One set per date — names carrying both ranking inputs
*and* a forward return — used identically by all four arms. No arm sees a name
another cannot.

---

## 3. Results Table

### Performance

| Metric | **A1 alpha top-10** | **A2 alpha top-47** | C1 random top-10 | C2 random top-47 |
|---|---:|---:|---:|---:|
| CAGR (net) | **80.41%** | **69.26%** | 24.32% *(mean)* | 28.08% *(mean)* |
| CAGR (gross) | 102.94% | 87.54% | 40.61% | 43.62% |
| Cumulative return | 36,432.8% | 19,195.3% | — | — |
| Annualized volatility | 36.87% | 33.78% | 35.13% | 32.61% |
| Sharpe (23% hurdle) | **1.240** | **1.125** | 0.188 | 0.275 |
| Sortino | 2.923 | 2.909 | — | — |
| Maximum drawdown | −29.61% | −25.95% | — | — |
| Calmar | 2.72 | 2.67 | — | — |

### Portfolio behaviour

| Metric | A1 | A2 | C1 | C2 |
|---|---:|---:|---:|---:|
| Average holdings | 10.00 | 47.00 | 10.00 | 47.00 |
| Realized exposure | 100% | 100% | 100% | 100% |
| Turnover per year | **11.26** | 9.82 | 11.78 | 10.96 |
| Rebalance events | 120 | 120 | 120 | 120 |
| Cost drag | 22.53pp | 18.28pp | ~16.3pp | ~15.5pp |
| Mean period return | 5.5512% | 4.9073% | — | — |
| Median period return | 4.6058% | 3.1423% | — | — |

### Random control distributions (30 seeds each)

| | C1 top-10 | C2 top-47 |
|---|---:|---:|
| Mean CAGR | 24.32% | 28.08% |
| Median | 23.31% | 27.93% |
| Std dev | 6.09 | 2.16 |
| 5th / 95th percentile | 15.43% / 32.16% | 25.00% / 31.75% |
| Min / max | 12.17% / 37.92% | 24.02% / 32.89% |
| **Alpha percentile** | **100.0** | **100.0** |
| Alpha beats p95 | **Yes** | **Yes** |

Both alpha arms sit above the maximum of their 30-seed control distribution.

---

## 4. Statistical Tests

Paired on per-period net return, alpha basket minus the mean random basket, over
the same 120 dates.

| | **A1 vs C1** | **A2 vs C2** |
|---|---:|---:|
| Observations | 120 | 120 |
| Mean period difference | **+3.2578%** | **+2.4179%** |
| Median period difference | +2.4425% | +1.5305% |
| Std dev of the difference | 8.8439% | 4.1716% |
| Standard error | 0.8073% | 0.3808% |
| **95% confidence interval** | **[+1.675%, +4.840%]** | **[+1.672%, +3.164%]** |
| **t-statistic** | **+4.035** | **+6.349** |
| **p-value** | **5×10⁻⁵** | **≈2×10⁻¹⁰** |
| Periods positive | 63.33% | 72.50% |

Both intervals exclude zero by a wide margin.

### Alpha-minus-random spread (gross, per rebalance)

| | top-10 | top-47 |
|---|---:|---:|
| Mean spread | +3.2425% | +2.3370% |
| Median spread | +2.4012% | +1.5010% |
| Periods positive | 62.5% | 70.8% |
| Best period | +35.76% | +16.96% |
| Worst period | −16.45% | −6.77% |

### Outlier sensitivity — is one date carrying the result?

| Spread after removing… | top-10 | top-47 |
|---|---:|---:|
| nothing | +3.2425% | +2.3370% |
| the 1 best period | +2.9693% | +2.2141% |
| the 3 best periods | +2.4917% | +1.9980% |
| the 5 best periods | +2.0798% | +1.8214% |
| **the 10 best periods** | **+1.4177%** | **+1.4984%** |
| 5%/95% trimmed mean | +2.7423% | +2.1656% |

**Not outlier-driven.** Removing the ten best of 120 periods leaves both spreads
solidly positive.

### Control sanity

The random arms behave as a null should. The universe's mean 20-session forward
return was +3.377%, which compounds to 49.0%/yr with no dispersion; the random
arms earn 40.6% / 43.6% gross (volatility drag) and 24.3% / 28.1% net after
~11–12%/yr of costs. Coherent, and centred where a random draw from this
universe belongs.

---

## 5. Stability Analysis

No period is hidden.

### A1 — alpha top-10

| Period | Periods | CAGR | Sharpe | Alpha spread | % ahead of random |
|---|---:|---:|---:|---:|---:|
| 2014-2016 | 28 | 12.62% | **−0.251** | +1.5074% | 64.3% |
| 2017-2019 | 36 | 123.55% | 1.746 | +2.7729% | 52.8% |
| 2020-2022 | 36 | 163.56% | 2.119 | +6.1382% | 75.0% |
| 2023-2024 | 20 | 19.92% | **0.035** | +1.3050% | 55.0% |

### A2 — alpha top-47

| Period | Periods | CAGR | Sharpe | Alpha spread | % ahead of random |
|---|---:|---:|---:|---:|---:|
| 2014-2016 | 28 | 9.51% | **−0.553** | +0.9176% | 50.0% |
| 2017-2019 | 36 | 99.55% | 1.718 | +1.1936% | 63.9% |
| 2020-2022 | 36 | 150.45% | 1.917 | +5.5150% | **91.7%** |
| 2023-2024 | 20 | 14.34% | **−0.201** | +0.6618% | 75.0% |

### Two facts the headline hides

1. **The alpha spread is positive in all four sub-periods, for both baskets.**
   That is the localization result, and it is stable.
2. **Both arms lose to cash in two of four sub-periods.** In 2014-2016 and
   2023-2024 the CAGR (12.6%, 19.9%, 9.5%, 14.3%) sits below the 23% policy
   rate, and Sharpe is negative or near zero. **Beating random is not the same
   as beating cash**, and half the sample does the former without the latter.
3. The spread is far larger in 2020-2022 (+6.14% / +5.52%) than anywhere else —
   roughly four times the other periods. The effect is real everywhere and
   concentrated in one regime.

---

## 6. Interpretation

### Verdict

# PASS LOCALIZATION — both baskets

| Arm | Alpha CAGR | Random mean | Percentile | p | Verdict |
|---|---:|---:|---:|---:|---|
| top-10 | 80.41% | 24.32% | 100.0 | 5×10⁻⁵ | **PASS LOCALIZATION** |
| top-47 | 69.26% | 28.08% | 100.0 | ≈2×10⁻¹⁰ | **PASS LOCALIZATION** |

Alpha Core outperforms random under frictionless assumptions. Neither of the
brief's conditional readings applies: A1 did not fail, A2 did not fail, so this
is neither a concentration problem nor a case of diversification weakening the
signal.

### What this does and does not say

- It **does** say the failure observed in Experiment 3 — Alpha Core at the 26.7th
  percentile of random, p = 0.7419 — is **not** explained by an absent ranking
  signal. Under identical assumptions minus the pipeline, the same ranking sits
  at the 100th percentile.
- It **does not** say Alpha Core has alpha. The window is in-sample for the
  factor selection, and a survivors-only universe inflates every absolute figure
  here. Both effects hit alpha and random arms alike, so the *comparison* holds
  while the *levels* do not.

### On concentration

Top-10 earns more (80.41% vs 69.26%) with a slightly better Sharpe (1.240 vs
1.125) and Calmar (2.72 vs 2.67). But the top-47 edge is markedly **more
reliable**: t = 6.35 vs 4.04, 72.5% of periods positive vs 63.3%, and a control
distribution a third as wide (sd 2.16 vs 6.09).

**Concentration raises the expected return and lowers the confidence.** That is
a measurement, not a licence: changing the production basket size is a model
change requiring its own preregistration, and it may not be made in reaction to
this in-sample result.

### The number that reframes everything

| | Turnover per year |
|---|---:|
| A1 frictionless | **11.26** |
| Experiment 3, production pipeline | **2.75** |

**The frictionless arm rotates roughly four times more than the production
system ever managed.** Alpha Core's top-10 basket replaces ~94% of its names
every 20 sessions. The measured edge is the edge *of that rotation*.

This is the reconciliation between 5A and Experiment 3 — and it is also the
warning. The pipeline did not merely mis-account for the rotation; it blocked
about three-quarters of it. Whether that blocking was a fixable engineering
defect or the Tehran order book correctly refusing an unexecutable instruction
is **precisely what 5A cannot answer**, because it assumed perfect fills.

---

## 7. Recommendation

# PROCEED with the allocator and execution fixes — with a pre-committed expectation

The evidence now localizes the Experiment 3 failure to the implementation path.
That justifies the engineering, and it did not before: fixing an allocator to
better express a signal that does not exist would have been wasted work.

### Proceed, in this order

1. **P0 — idle-cash accrual.** Cash earns 0% against a 23% hurdle. Blocks any
   interpretation of Arm B's Sharpe.
2. **P1 — exposure correction.** Without it Arm B cannot be exposure-matched to
   A1, and the 5A design's V1 gate voids the comparison.
3. **P2 — unify the funnel counters** into one layer.
4. Then run **Arm B and Arm CB** and measure `A1 − B` directly.

*(P3, the diagnostic reporting repair, is already done.)*

### The expectation to commit to now, before the fixes are built

**Do not expect the fixes to recover the frictionless gap.** The 56-point gap
between A1 (80.41%) and C1 (24.32%) is earned by rotating ~94% of a ten-name
book every month, with perfect fills, in a market with ±3% daily price bands,
صف خرید/فروش, and participation limits. Experiment 3 measured a 93.3% fill rate
at 2.75 turnovers/year; at 11.26 it would be far worse, and the cost would rise
with it.

**A realistic prior: most of the frictionless edge is structurally unreachable,
not merely mis-implemented.** Write that down before Arm B runs, so a
disappointing result is not re-interpreted afterwards as a bug.

### The measurement that decides the programme

After P0–P2, run Arm B and CB and report:

```
recoverable fraction = (B − CB) / (A1 − C1)
```

Pre-specified reading, fixed here:

| Recoverable fraction | Reading |
|---|---|
| **> 50%** | The pipeline was the binding constraint. Proceed to the forward holdout. |
| **20–50%** | Partially recoverable. Re-examine basket size and rebalance frequency **as a preregistered change**, not a tune. |
| **< 20%** | The edge is unreachable at this turnover. Alpha Core as configured is not tradable; retire or re-specify from first principles. |

### What must not happen

No weight change, no factor change, no threshold change, no basket-size change,
no rebalance-frequency change, no ML — on the strength of this result. 5A is
in-sample. It diagnoses; it does not authorise. The forward holdout preregistered
in `experiment5A_design.md` §6 remains the only thing that can establish alpha.

---

## Provenance

| Item | Value |
|---|---|
| Experiment ID | EXP5A-FRICTIONLESS-2026-08-18 |
| Module | `experiment_5a_frictionless.py` (new; imports `research_alpha_ic.score_frame` for the score) |
| Files modified | **None** — Alpha Core, factor weights, thresholds and the engine were not touched |
| Seeds | 1–30 per basket size, deterministic |
| Holdout evaluated | **None.** Post-2024 data was not read. |
| Model changes | **None** |
| Python | 3.14.7 · numpy 2.5.1 · pandas 3.0.5 |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |

### Artifacts

| File | Contents |
|---|---|
| `backtest_output/experiment5a_frictionless.json` | Full results, per-seed controls, statistics, stability |
| `backtest_output/experiment5a_arms.csv` | Arm-level metrics |
| `backtest_output/experiment5a_spread_top10.csv` | 120 per-date spreads, top-10 |
| `backtest_output/experiment5a_spread_top47.csv` | 120 per-date spreads, top-47 |
