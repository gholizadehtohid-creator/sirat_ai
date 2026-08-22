# E22 — DOES THE FLOW FILTER SURVIVE EXECUTION?

**Decision: REJECT at Stage 3. 2026-08-22.**
Preregistration `b4faab1bb9b2abf1`, frozen before any number existed.
Ledger entry `6d83b7b2b54bd950`.

**The first Stage 3 — EXECUTION REALISM — ever run in this programme.** It was
unreachable until `backtesting/impact.py` landed, because one of its five
required checks is slippage and slippage had been 0.0% in every result this
programme ever produced.

---

## 1. The question, and why the basket is random

*If you refuse to buy the names E21's score ranks most likely to lock, do you
actually get filled more often — and what does that refusal cost?*

The reference basket is drawn **uniformly at random**, 10 names, 20 independent
draws per session, frozen seed. That is the control: a basket with no expected
edge cannot blend a selection effect into an execution measurement, and cannot
discover an alpha by accident.

Both baskets are bought at t+1. A name `unbuyable` at t+1 is a **failed fill**,
not a fill at a worse price.

### Two admissions made at registration, not discovered afterwards

1. **No out-of-sample claim is possible.** E19 spent 2023-03-01..2024-09-01 and
   E21 spent 2024-09-02..2026-08-18; the data ends 2026-08-18. E22 could
   establish a failure and could never establish generalisation.
2. **The primary score is E21's frozen composite, wrong sign included.** E21
   recorded that `inst_net_ratio` was preregistered at +1 and measures at
   −0.087. Correcting it here would fit the composite to a window with no
   holdout left to catch the fit.

---

## 2. Result

| Stage | | |
|---|---|---|
| 1 — DATA INTEGRITY | **PASS** | forward alignment 100%, 415 eligible names/date median |
| 2 — ECONOMIC SIGNAL | **PASS** | *re-measurement of E21, declared non-evidence* |
| 3 — EXECUTION REALISM | **FAIL** | 3 of 5 checks |

### Stage 3, in full

112,400 baskets over 2,810 sessions; 562,000 positions per variant.

| | baseline | filtered |
|---|---:|---:|
| failed fills | 3.6155% | **3.0907%** |
| impact (filled) | 0.1759% | 0.2512% |
| round trip | 1.3919% | 1.5423% |

| Check | | |
|---|---|---|
| `liquidity` | PASS | admitted/excluded ADV ratio 1.115 |
| `participation_limits` | PASS | 404 names survive the filter |
| `queue_effects` | **FAIL** | ratio 0.8549 vs ceiling 0.70 |
| `slippage` | **FAIL** | +0.0752pp vs cap 0.05 |
| `transaction_costs` | **FAIL** | +0.1504pp vs cap 0.05 |

---

## 3. The rejection stands on `queue_effects` alone

That check depends on **no cost model, no volatility estimate and no imported
constant** — only on whether an order filled.

**The filter reduces failed fills by 14.5% where 30% was declared necessary.**
It works. It works at less than half the required strength.

### And the economics fail independently

| | |
|---|---|
| failed fills avoided | 0.5248pp of positions |
| extra cost per filled slot | 0.1504pp |
| **break-even per converted slot** | **28.66pp** |
| expected excess of a random basket | **zero, by construction** |

A cost with no offsetting benefit is not a close call.

---

## 4. The two cost failures are NOT the filter's fault, and that is recorded

Reporting only §3 would overstate the case against the filter. Reporting only
this section would rescue it. Both are true.

**Per session, the filter is cheaper:**

| | baseline | filtered |
|---|---:|---:|
| median per-date impact | 0.0824% | **0.0697%** |
| p95 | 0.1803% | **0.1422%** |
| max | 68.68% | 76.27% |

Median delta **−0.0011pp**, mean **−0.0064pp**, and only **26.1%** of 2,809
sessions are worse under the filter.

The aggregate reversed because of five consecutive sessions in June 2022
reporting mean impact between **56% and 76% of price** across the whole
550-name cross-section. The worst 1% of sessions account for **−254.6%** of the
total delta — they do not dominate its mean, they **invert its sign**.

### Root cause

**252 rows in `price_history` carry returns above 200%**, across 240 names and
222 dates. The largest is **+19,976,600%** — a prior close of 1 rial. New
listings, post-suspension reopenings, unadjusted capital increases. 7,388 rows
(0.39% of the panel) exceed E14's `extreme_gap` threshold.

One such row sits inside a 20-session trailing volatility window **for twenty
sessions**, and one contaminated name dominates every basket drawn on those
dates. The panel-wide signature: σ mean **8.79%** against a median of **2.26%**.

### Defect classification — data and tooling, not strategy

| | |
|---|---|
| **data** | `price_history` carries unadjusted and placeholder prices producing returns no market ever printed |
| **tooling** | `impact.py` accepted a daily volatility of several hundred percent — it validates only "positive and finite". This was its first real use |
| **gate** | Stage 1 passed this panel. Its four checks cover forward alignment, universe restoration, cross-section width and coverage. **Not one asks whether a return is physically possible** |

E14's `annotate` has flagged exactly these bars as `extreme_gap` (|ret| > 12%)
since it was written. **The platform already held the knowledge the volatility
estimator failed to use.**

---

## 5. What was deliberately not done

The panel was **not cleaned** and Stage 3 was **not re-run** under this
experiment id.

The thresholds were frozen before any number existed and the measurement was
exactly what was declared. Repairing data after seeing a verdict and
re-measuring is the move this platform exists to refuse — and it stays refused
when the repair is obviously correct. That is the whole point of a rule that
only binds when it is inconvenient.

E22 rejects. The cost question belongs to a successor experiment with a
volatility sanity bound preregistered.

## 6. The fix, applied forward only

`backtesting/impact.py` now:

- **refuses** a daily volatility above 12% — on a band-limited market the
  theoretical maximum is the band itself, so a higher reading means the return
  series contains a bar the market never printed;
- **drops** returns beyond 12% from `daily_volatility_pct` rather than
  winsorising them — winsorising keeps the bar at the boundary and still biases
  the estimate upward.

**12.0 is E14's `LOCK_MAX_RETURN`, cited rather than reinvented** — a physical
ceiling, not a threshold chosen to make a result pass. Pinned by
`test_the_ceiling_is_e14s_constant_not_a_new_one`.

Also corrected: `test_importing_changes_no_existing_behaviour` asserted that
*nothing anywhere* imported the module and went red the moment E22 used it as
designed. That was the contract stated too loosely. It now asserts the real
rule — **the engine never charges impact on its own; registered experiments
may** — plus a second test that the scanner is actually finding something, so a
broken scan cannot pass as compliance.

---

## 7. Settled, open, owed

| | |
|---|---|
| **settled** | the filter does not pay for itself in front of a basket with no expected edge, and reduces failed fills by half of what was declared necessary |
| **not the filter's fault** | the two cost checks — per session the filter is cheaper; the aggregate is contaminated |
| **open** | whether it pays in front of a basket with a real expected excess. That needs a validated return signal, and E19/E20 established Alpha Core is not one |
| **owed** | a Stage 1 check for physically impossible returns. Neither it nor the volatility bound may be used to re-run E22 |

## Provenance

| | |
|---|---|
| preregistration | `b4faab1bb9b2abf1` |
| ledger | `6d83b7b2b54bd950` |
| stage results | `backtest_output/E22_stage_results.json` |
| diagnostics | `backtest_output/E22_diagnostics.json` |
| rejected at | Stage 3 — still a **cheap** rejection under the platform KPI |
| prefactor sweep | delta positive at c = 0.17 / 0.34 / 0.51 / 0.69 — the verdict does not depend on the borrowed constant |
| Alpha Core | untouched, `90024d86d7a5c268` |
