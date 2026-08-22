# PHASE 2 — CLOSING THE ACCOUNT

**2026-08-22.** Phase 2 built a governance layer and then spent it on five
questions. This is what it bought.

The headline is a structural finding about this market, not a strategy. It is
worth more than a strategy would have been, because it tells future work where
**not** to look — and it is the thing Phase 1 spent fifteen experiments failing
to articulate.

---

## 1. The finding

Three independent lines of attack, on different targets, with different
methods, converged on one mechanism:

| | The question | Where the information turned out to live |
|---|---|---|
| **E20** | can Alpha Core's signal be traded at capacity? | in names **too illiquid to absorb a position** |
| **E22** | does a lock filter improve a buy programme? | in fills whose **expected value was zero** |
| **E23** | can a sell-queue warning get you out? | in the **sessions where the trade cannot be made** |

> **On a market with hard price limits, information and executability are
> systematically anti-correlated.**

This is not three failures. It is one property of the market, measured three
ways. And each measurement identified the mechanism precisely rather than
reporting a disappointing number:

- **E20**: IC **+0.17066 (t 7.14)** in names *excluded* by a 5% participation
  cap against **−0.01293 (t −0.99)** in names admitted. The capacity curve is
  negative at every level from 50% down to 0.25%.
- **E22**: the filter cut failed fills by **14.5%** where 30% was needed, and
  break-even required each converted slot to be worth **28.66pp** against a
  random basket's expected zero.
- **E23**: precision **12.41%** at t+1 — the session you cannot sell in — and
  **3.24%** at t+2 among names you actually can sell, against ~29% needed. The
  warning and the lockout arrive together.

### Why this is a real finding and not an excuse

It is falsifiable and it was nearly falsified. **E21 does not fit the pattern**:
its decile lift is flat across liquidity quintiles — 2.375 / 2.335 / 2.414 /
2.417 / **2.237×** from most illiquid to most liquid — with IC still 0.110
(t 23.5) in the most liquid names. E20's failure mode did **not** repeat there.

So the anti-correlation is not universal. It binds on **actionability**, not on
liquidity alone: E21 predicts a state, and predicting is free. The moment any
of E22 or E23 asked the signal to *pay for an action*, the mechanism reappeared.

## 2. The experiments

| | Question | Verdict | Where |
|---|---|---|---|
| **E19** | does Alpha Core have a tradable edge? | **REJECT**, Stage 2 — 7 of 11 gates fail, holdout IC flipped to −0.02449 | `RESEARCH_STATUS.md` |
| **E20** | is the information tradable at capacity? | **Case B** — research signal, not a trading signal | `E20_REPORT.md` |
| **E21** | does حقوقی flow predict limit-locks? | **ARCHIVE** — IC 0.13650, holdout 0.15280, **the programme's only positive result** | `E21_REPORT.md` |
| **E22** | does the filter survive execution? | **REJECT**, Stage 3 — first Stage 3 ever run here | `E22_REPORT.md` |
| **E23** | can a sell warning get you out? | **rejected before registration**, in under an hour | `E23_FEASIBILITY.md` |

## 3. What the platform gained, and what each thing cost to learn

| | |
|---|---|
| **`backtesting/impact.py`** | slippage had been **0.0% in every result this programme ever produced** — not because trades were free but because nothing computed it. At 5% participation impact adds 0.456pp to a 1.04pp commission: a **44% higher cost hurdle** than every prior result assumed |
| **`gates.price_sanity`** | Stage 1's four checks asked whether a panel was aligned, restorable, wide and populated. **None asked whether a number in it was possible.** 361 impossible sessions sat in the panel, the largest **+29,966,500%** |
| **`run_stage` → `measured_by`** | E21 and E22 both hand-rolled Stage 1 and never called `gates.data_integrity`. The Manager validated that all four checks were *reported* and could not know they were computed inline |
| **the E21 audit** | the programme's only positive result was checked against the contamination that could have built it. **99.0%** of its IC survives removing every `extreme_gap`; the positive class is **97.36% genuine صف خرید** and contains exactly **one** impossible row |

Each of those was found by an experiment failing, not by a review. That is the
argument for running experiments that can fail.

### The methodological rule worth keeping

E21 and E22 ran on the **same contaminated data** and only one broke:

- E21 measures a **rank correlation** against a binary class. 869 contaminated
  rows inside 38,218 positives cannot move a rank statistic.
- E22 measured the **mean of a heavy-tailed cost**. One row with a volatility in
  the millions dominates a mean outright — five sessions inverted the sign of a
  2,809-session result.

> **Rank statistics are robust to exactly the contamination that destroys
> means.** Where a metric can be a rank, contaminated data costs almost
> nothing. Where it must be a mean, contamination must be excluded *before* the
> mean is taken, not after it is questioned.

## 4. What was refused, and why that is the product

| Refused | Because |
|---|---|
| flipping `inst_net_ratio` after E21 measured it at the wrong sign | choosing a parameter after seeing the result. Recorded **FALSIFIED**; the correction belongs in a successor preregistration |
| cleaning the panel and re-running E22's Stage 3 | repairing data after seeing a verdict. **It stays refused when the repair is obviously correct** — a rule that only binds when convenient is not a rule |
| registering E23 and recording "rejected at Stage 1" | the numbers came *before* registration. That is a feasibility scan, and the ledger is for experiments |
| re-opening E19 or E20 under any E21/E22/E23 result | they are adjudicated |

## 5. The numbers

| | |
|---|---|
| tests | **981**, green |
| CI | **BUILD GREEN** |
| ledger | **15 entries**, chain intact |
| cheap rejections (stages 1–3) | **93%** of 14, target 70% |
| holdout windows evaluated twice | **0** |
| Alpha Core | `90024d86d7a5c268`, untouched throughout |

### A gap in these metrics, recorded rather than papered over

The KPI counts rejections among **registered** experiments. E23 was rejected
*before* registration, in under an hour, with an audit trail — the cheapest and
best rejection there is, and precisely what `RESEARCH_ROADMAP.md` names as the
measure of success:

> *"A hypothesis rejected at Stage 1 in an afternoon, with an audit trail a
> third party could verify."*

**It counts for nothing in the current KPI.** Registering it to collect the
credit would have falsified the record, so the gap stands as a gap.

## 6. Where this leaves the research

**There is no live alpha candidate.** E19 and E20 closed Alpha Core. E22 and
E23 closed the two applications of E21. What remains is:

> **one validated signal with no proven use.**

E21 is real — it survived a holdout it spent exactly once, and an adversarial
contamination audit. What it predicts is *tradability*, and both attempts to
convert that into money failed for the same structural reason.

### What is honestly still open

| | |
|---|---|
| does the E21 filter pay in front of a basket with a **real** expected excess? | unanswerable — no validated return signal exists to put in front of it |
| the corrected `inst_net_ratio` sign | preregisterable, but **no unspent temporal holdout remains**; a name-split holdout would test cross-sectional universality only |
| fundamental factors | blocked — **0 of 764** companies reach the required filing depth |
| ML ranking | blocked — ~120 decision dates against 250 required |
| intraday microstructure | blocked — no depth is stored, and depth **cannot be backfilled**, only collected forward |

### The one thing that would change the picture

**Collect forward.** Every blocked item above is blocked on data that only time
and a running collector can supply: order-book depth, deeper Codal history, and
— most cheaply — an unspent forward window. E19 and E21 have consumed every
holdout in the archive; the next genuine out-of-sample claim has to be earned by
waiting.

That is an unsatisfying answer and it is the correct one. The alternative is to
keep re-measuring a decade that has already been read.

## Provenance

| | |
|---|---|
| phase opened | `PLATFORM_ACCEPTANCE.md`, 2026-08-19 |
| experiments | E19, E20, E21, E22 registered; E23 rejected pre-registration |
| code added | `backtesting/impact.py`, `gates.price_sanity`, `run_stage` provenance |
| code changed | **no strategy, no factor, no threshold moved to obtain a pass** |
| Alpha Core | `90024d86d7a5c268` |
