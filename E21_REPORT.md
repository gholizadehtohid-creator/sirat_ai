# E21 — FLOW-BASED LIMIT-LOCK FEASIBILITY SIGNAL

**Decision: ARCHIVE. 2026-08-22.**
Preregistration hash `2ce1d49dd90ebd46`, frozen before the panel existed.
Ledger entry `f5d3af47da67b8ac`, chain intact at 14 entries.

**The first positive result this programme has produced** — and the reason it is
ARCHIVE rather than PROMOTE is written into the preregistration, not chosen
after seeing it.

---

## 1. The question

*Does institutional (حقوقی) flow at session t predict that a name is
**unbuyable** at session t+1?*

Not "will it go up". The target is a microstructure state — locked in a
صف خرید, reopening, or gapped away — and the output is a feasibility filter.
That framing is the whole reason this experiment was chosen first: it is
measurement rather than prediction, so it structurally cannot produce a false
alpha discovery, which is the failure mode that ended Phase 1.

## 2. What was frozen, before any number existed

| | |
|---|---|
| target | E14's detector, reused unchanged: `unbuyable = locked_up \| reopen \| extreme_gap` |
| score | equal-weight mean of within-date percentile ranks of four components. **No fitting** |
| components, signs declared | `inst_net_ratio` +1, `indiv_power` +1, `closed_at_high` +1, `vol_ratio` +1 |
| discovery | 2014-01-01 .. 2023-02-28 |
| excluded | 2023-03-01 .. 2024-09-01 — E19 spent this as its holdout; not reused, not even for training |
| holdout | 2024-09-02 .. 2026-08-18 — never evaluated by any experiment in the ledger |
| seed | 20260822 |
| scope | **stages 1–2 only**; terminal decision ARCHIVE or REJECT |

The 2014 start date is a data fact, not a choice made after seeing a result:
the median number of names carrying flow on a date is **2** before 2014 and
339–653 after it. A within-date rank cannot be measured on two names.

## 3. Results

### Stage 1 — DATA INTEGRITY: **PASS**

| Check | Result |
|---|---|
| point in time | **100%** agreement on 1,997 resolved samples; 3 absent sessions correctly left *unknown* |
| survivorship | snapshot `u20260819-668658bb` restored and hash-verified |
| universe valid | 2,187 dates, median **425** names/date (floor 100) |
| missing data | **75.73%** coverage (floor 60%) |

The forward-alignment check is a recomputation, not an assertion: for a random
sample it re-derives session t+1 on the market calendar and compares. This
matters because `shift(-h)` moves *rows*, and a suspension leaves no row — the
defect that once produced a "20-session return" of several thousand percent.

### Stage 2 — ECONOMIC SIGNAL: **PASS, all four checks**

| Metric | Frozen threshold | Measured |
|---|---|---|
| mean per-date IC | ≥ 0.05 | **0.13650** |
| t-statistic | ≥ 3.0 | **80.52** |
| vs permutation null | > 95th pct | **100th** (null p95 = 0.00204) |
| top-decile lift, h=1 | ≥ 2.0× | **2.625×** |
| lift retention, h=3 | ≥ 0.30 | **0.506** |

Top-decile unbuyable rate **10.650%** against a universe rate of 4.057%.
2,128 dates scored.

### Holdout — spent once

| | |
|---|---|
| window | 2024-09-02 .. 2026-08-18, 395 dates |
| mean IC | **0.15280** |
| t | **29.24** |
| decile lift | **2.049×** |

**The sign held and the magnitude grew.** E19's holdout IC flipped sign to
−0.02449. This one did not.

---

## 4. The interrogation

A t-statistic of 80 gets audited harder than a negative result would. Three
diagnostics, discovery window only — the holdout was spent once and was not
reopened for "just a diagnostic".

### 4.1 Is it merely lock persistence?

A name locked at session t has `high == low`, therefore `close == high`, so the
`closed_at_high` component risked making the entire result a restatement of
*"locked today, locked tomorrow"* — true, trivial, and nothing to do with flow.

Excluding every name already locked at t (96.0% of the panel survives; base
rate falls from 4.057% to 2.300%):

| Score | mean IC | t | lift |
|---|---:|---:|---:|
| flow components only | 0.05031 | 40.21 | **1.729×** |
| microstructure only | 0.07348 | 41.82 | 2.428× |
| all four (preregistered) | 0.08823 | 62.61 | **2.233×** |

**It is not the tautology.** The composite retains 65% of its IC and a 2.23×
lift on names that are not already locked, and the flow half alone still
carries 1.73×.

### 4.2 Does E20's failure mode repeat?

E20 killed Alpha Core because its information sat only where trading was
impossible — IC **+0.212** in the most illiquid quintile against **−0.030** in
the most liquid. The same check here:

| Liquidity quintile | mean IC | lift |
|---|---:|---:|
| Q1 most illiquid | 0.20828 | 2.375× |
| Q2 | 0.13128 | 2.335× |
| Q3 | 0.11983 | 2.414× |
| Q4 | 0.10754 | 2.417× |
| **Q5 most liquid** | **0.11005** | **2.237×** |

**The lift is essentially flat across the liquidity spectrum**, and IC stays
positive and significant in the most liquid names (t = 23.46). E20's failure
mode does not repeat — which is coherent, because predicting *tradability* is
not predicting *returns* and does not inherit that constraint.

### 4.3 One declared sign was wrong — and is not being corrected here

Each component measured alone:

| Component | mean IC | t | lift |
|---|---:|---:|---:|
| `inst_net_ratio` | **−0.08707** | −60.22 | **0.416×** |
| `indiv_power` | +0.16855 | 78.69 | **4.349×** |
| `closed_at_high` | +0.26111 | 65.20 | 8.972× |
| `vol_ratio` | +0.04583 | 21.34 | 1.977× |

`inst_net_ratio` was preregistered at **+1** on the magnet-effect argument that
institutional trading drives price toward the band. It measures at the
**opposite sign, strongly**. Mechanically that is coherent: in a صف خرید the
حقوقی side is typically *supplying* the queue rather than forming it, so heavy
institutional buying marks a name where retail is *not* queuing.

**The composite passed despite this component, not because of it.**

Flipping the sign now would be choosing a parameter after seeing the result.
So the specific directional claim in the hypothesis is recorded as
**FALSIFIED**, and the corrected sign belongs in a successor preregistration.
What survives is `indiv_power` — قدرت خریدار حقیقی, IC 0.16855 at a 4.35× lift
— which is also a `client_type` flow variable. **The broad hypothesis holds
while its specific direction did not.**

---

## 5. Why ARCHIVE and not PROMOTE

Two independent bars, both recorded before the result:

1. **Scope.** Stage 3 is EXECUTION REALISM and one of its five required checks
   is slippage, which has been **0.0% in every result this programme has ever
   produced** because no impact model exists. Reporting that check as passing
   would be reading absence as approval — the exact defect this platform was
   built to refuse. E21 was registered with a scope ending at Stage 2.
2. **Provenance.** Registration captured `git_dirty=True` and
   `reproducible=False`, which independently bars promotion.

## 6. Successor work, in order

| | |
|---|---|
| (a) | an **impact model** — RESEARCH_OPTIONS.md Option 2. No execution claim is measurable without it |
| (b) | a successor experiment preregistering `inst_net_ratio` at **−1**, with the queue-supply mechanism stated in advance |
| (c) | only then: does using this as a Stage 1 feasibility filter improve realised versus requested returns? |

## 7. Provenance

| | |
|---|---|
| preregistration | `2ce1d49dd90ebd46` — frozen before the panel was built |
| ledger entry | `f5d3af47da67b8ac`; 14 entries, chain intact |
| panel | `backtest_output/E21_flow_lock_panel.csv.gz`, 1,894,584 rows. Not committed — 65.8 MB and regenerable by `python examples/e21_panel.py`. Pinned instead by sha256 `42fab63b61fb1ebf55fbea48b7c4a34d3aaf2668f752ed5dce61f29a134df39d` |
| stage results | `backtest_output/E21_stage_results.json` |
| diagnostics | `backtest_output/E21_diagnostics.json` |
| holdout | spent once, recorded in `windows_touched` |
| Alpha Core | untouched, `90024d86d7a5c268`. E19 and E20 remain adjudicated |
