# SIRAT-AI RESEARCH PLATFORM v2 — TRANSFORMATION PLAN

**2026-08-18 · Phase 2 · post-archive**

---

## 1. Executive Summary

Fifteen experiments retired an exposure engine, rejected a stock-selection
signal, identified its true mechanism, and falsified the one proposed rescue.
The trading specification is archived.

**The infrastructure built along the way is not.** The audit below finds a
point-in-time data layer with back-adjustment and filing-date lag, a validation
module with walk-forward, Monte Carlo, sensitivity sweeps and an
overfitting-risk scorer, an execution simulator with queue and participation
modelling, and — newest and most valuable — a set of microstructure detectors
that produced the programme's one genuine discovery.

> **The strategy was the liability. The platform is the asset.**

The transformation is therefore **mostly reorganisation and governance, not
construction.** Two capabilities were performed manually across fifteen
experiments and must become code: a **Hypothesis Registry** and an **Experiment
Manager**. Everything else already exists and needs promoting from
"scripts that happened to work" to "platform with an enforced contract".

The single most valuable output of Phase 1 is not a component. It is a
falsifiable, transferable finding about this market:

> **In an exchange with hard daily price bands, momentum and band-proximity
> factors measure queue formation rather than expected return.**

Phase 2's purpose is to find the next such finding faster, and to reject the
next bad idea before capital is exposed rather than after.

---

## 2. Lessons Learned from Fifteen Experiments

| # | Lesson | Cost of learning it |
|---|---|---|
| 1 | **The control arm decides the answer.** E1 passed against buy-and-hold and failed against an exposure-matched blend that was in the same results file. | Four experiments built on an overturned premise |
| 2 | **Reporting defects masquerade as research findings.** Four execution counters read `null` for an entire programme because of reversed key names; `cash_analysis` was computed and discarded. | E3's Sharpe levels were uninterpretable and nobody knew |
| 3 | **Fixing the implementation can make the result worse — and that is information.** Exposure 29%→49%, occupancy 4.63→5.80, cash credited, and the percentile fell 26.7→3.3. | The cleanest evidence in the programme |
| 4 | **Frictionless tests localise, they never validate.** E9 passed at the 100th percentile; E11 failed at the 3.3rd. The gap was 11.26 vs 4.45 turnovers/year. | One near-miss at a false positive |
| 5 | **A signal's horizon must match the trading horizon.** IC 0.105 at h=1, 0.015 at h=20, and the book held for 54 sessions. | The root cause, found at experiment 13 of 15 |
| 6 | **Statistical significance is not tradability.** z = +8.3 against a permutation null, and −69%/yr after commission. | The central economic lesson |
| 7 | **Microstructure can manufacture apparent alpha.** 7.75% of names carried 58.7% of the excess, and they were the unbuyable ones. | The programme's actual discovery |
| 8 | **Contaminated windows inflate silently.** Including 2024H2–2026 doubled the traded-horizon IC. | Two experiments reported inflated headlines |
| 9 | **Preregistration works, including when it is inconvenient.** G1 failed by 0.03 and stayed failed. | Nothing — this is the process working |
| 10 | **Gates can pass meaninglessly.** E15's G3 and G5 passed by losing less than a worse control. | Nearly a false "partial success" |
| 11 | **Absence is not zero.** `.get(key) or 0` turned unmeasured counters into confident zeros. | Invisible for an entire experiment |
| 12 | **`shift(-h)` moves rows, not sessions.** Across suspension gaps it produced a −579% excess. | Caught in-run, twice |

**The meta-lesson:** eleven of these twelve are process failures, not
intellectual ones. A platform that enforces the process would have surfaced most
of them in week one.

---

## 3. Current Asset Inventory

### Data layer

| Component | Current status | Verdict |
|---|---|---|
| `price_history` | **1,913,892 bars, 2001-03-25 → 2026-08-17**, OHLCV + value + trades, 100% non-null since 2014 | **KEEP** — institutional quality |
| `client_type_history` | **1,681,295 rows, 2008-11-26 → 2026-08-17** — individual/institutional buy/sell value, volume **and counts** | **KEEP** — the strongest under-used asset |
| `index_history` | 7,251 rows, two indices, 2008 → 2026 | **KEEP** |
| Corporate actions | `back_adjust()` applied **once**, inside `pit.bars()`, for capital increases | **KEEP** — correctly placed |
| Point-in-time controls | `PointInTimeData` — `history_until`, `bar_on`, `bar_after`, `stale_sessions`, `fundamental_as_of` with `_first_session_after(lag_sessions)` | **KEEP** — the best component in the repo |
| Dataset versioning | `pit.fingerprint()` | **KEEP and PROMOTE** — becomes the Experiment Manager's dataset hash |
| **Survivorship** | **No historical universe snapshot has ever been collected** (`survivorship.py` says so explicitly) | **BUILD** — the single largest data gap |
| **Fundamental pipeline** | `codal_letters` 37,383 indexed · **`codal_financials` only 2,194 parsed across 764 companies ≈ 2.9 filings each ≈ 7% of a complete panel** | **MODIFY** — blocked on a backfill, not impossible |
| `market_data`, `market_snapshot` | 2025-05 onward only; live-panel scaffolding | **KEEP, low priority** |
| `paper_*` tables | 8 equity rows, 3 trades | **DELETE** — vestigial, and paper trading has no candidate |

### Research layer

| Component | Current status | Verdict |
|---|---|---|
| `factor_engine.py`, `indicators.py` | Momentum, %B, RSI, MACD, ATR, ADX, volume — all PIT-correct | **KEEP** |
| `research_factors.py` | Builds the 25-column factor panel with four forward horizons | **KEEP and GENERALISE** — becomes the Factor Research Engine |
| `research_alpha_ic.py` | Rank IC, ICIR, decile spreads, permutation nulls, sub-period stability | **KEEP and PROMOTE** — this is the Alpha Validation Engine's predictive test |
| `alpha_core.py` | The rejected ranking | **KEEP, FROZEN, QUARANTINED** — retained as a regression fixture and a negative control, never as a candidate |
| `market_exposure.py` | The retired exposure engine | **ARCHIVE** — move out of the import path |
| `portfolio_engine.py` | Two-pass sector allocation, caps, cash floor | **MODIFY** — needs a fixed-N equal-weight research path that bypasses discretionary caps |
| Experiment framework | **Fifteen bespoke `experiment_*.py` scripts** with hand-rolled preregistration in docstrings | **REPLACE** — this is the biggest structural gap |

### Validation layer

| Component | Current status | Verdict |
|---|---|---|
| `backtesting/engine.py` | T+1 session loop, rebalance, exits, diagnostics, cash accrual (v2.1) | **KEEP** |
| `validation.walk_forward()` | Rolling train/validate folds with degradation reporting | **KEEP — never used in Phase 1** |
| `validation.out_of_sample()` | In-sample vs out-of-sample with `_degradation()` | **KEEP — never used** |
| `validation.monte_carlo()` | Resampling with configurable method and seed | **KEEP — never used** |
| `validation.sensitivity()` | Parameter grid sweeps | **KEEP, GOVERN** — powerful and dangerous; must be gated (§7) |
| `validation.overfitting_risk()` | Composite scorer over OOS, sweep, folds, profit concentration | **KEEP and PROMOTE to mandatory** |
| Random controls | Seeded permutation ranking inside `alpha_core.py:287` | **KEEP and EXTRACT** — belongs in the platform, not in a strategy |
| Bootstrap | Block bootstrap written ad hoc in E15 | **EXTRACT to a shared module** |

> **The most striking audit finding:** `walk_forward`, `out_of_sample` and
> `monte_carlo` were built, tested, and **never used in fifteen experiments.**
> The platform had validation machinery the research process did not reach for.
> That is a governance failure, not a capability gap.

### Execution layer

| Component | Current status | Verdict |
|---|---|---|
| `execution.py` | T+1 eligibility, queue detection, participation caps, partial fills, expiry, per-counter stats | **KEEP** — validated in anger |
| `portfolio.py` | Book, marks, costs, commission/tax split, `accrue_cash()` (v2.1) | **KEEP** |
| Counter-layer separation | `LAYER_A_EXECUTION` / `LAYER_B_PORTFOLIO` + `assert_layers_disjoint()` | **KEEP and PROMOTE to platform-wide** |
| **Microstructure detectors** | Band-agnostic lock detection, reopening/gap separation, stale-bar handling, unbuyable classification (E14/E15) | **KEEP and PROMOTE to a first-class module** — the programme's genuine discovery |
| Slippage model | **Does not exist** — held at 0.0% throughout | **BUILD** |

---

## 4. New Architecture — SIRAT-AI Research Platform v2

```
  DATA LAYER                    sirat/data/
    price · flow · index · fundamentals · universe snapshots
    point-in-time contract · back-adjustment · dataset fingerprint
        |
  FEATURE ENGINEERING           sirat/features/
    factor library · panel builder · forward returns · PIT assertions
        |
  HYPOTHESIS REGISTRY           sirat/registry/          <-- NEW
    economic rationale · mechanism · null · gates · signed & hashed
        |
  BACKTEST SANDBOX              sirat/sandbox/
    frictionless arm · portfolio arm · random controls
        |
  EXECUTION SIMULATION          sirat/execution/
    T+1 · queues · participation · partials · slippage · fill probability
        |
  STATISTICAL VALIDATION        sirat/validation/
    permutation · bootstrap · walk-forward · Monte Carlo · overfitting risk
        |
  RESEARCH DECISION             sirat/decisions/         <-- NEW
    promote · modify · reject · archive — recorded, immutable
```

### The two architectural rules

1. **Nothing may skip a layer.** A hypothesis cannot reach the sandbox without a
   registry entry; a candidate cannot reach a decision without execution
   simulation. Enforced in code, not convention.
2. **Every layer emits a fingerprint.** Data version, code version, parameters,
   seed. The Experiment Manager composes them into one experiment hash.

---

## 5. Module Design

### A. Hypothesis Registry — `sirat/registry/`

A hypothesis is a **typed, hashed record** created before any data is touched.

| Field | Required | Enforcement |
|---|---|---|
| `hypothesis` | ✅ | non-empty |
| `economic_rationale` | ✅ | **why should this pay?** — free text, but the reviewer field must be signed |
| `expected_mechanism` | ✅ | the causal story; E14 existed because Alpha Core never had one |
| `data_required` | ✅ | resolved against the data layer; fails fast if coverage is insufficient |
| `null_hypothesis` | ✅ | must name the control arm explicitly |
| `success_criteria` | ✅ | numeric, with margins |
| `failure_criteria` | ✅ | numeric — **separate from "not success"** |
| `horizon` | ✅ | must match the intended rebalance |
| `capacity_assumption` | ✅ | turnover the hypothesis implies, checked against measured feasibility |

`registry.freeze()` writes the record, hashes it, and returns a token. **No
sandbox call accepts an unfrozen token.**

> **Lesson 1 is enforced here:** the null hypothesis must name its control arm.
> E1 could not have scored itself against buy-and-hold while calling the field
> `beats_fixed`.

### B. Experiment Manager — `sirat/experiments/`

Records automatically, with no researcher discretion:

| Recorded | Source |
|---|---|
| Experiment ID | generated |
| Hypothesis token | registry |
| Code version | **git commit — blocked until git exists** |
| Dataset version | `pit.fingerprint()` |
| Parameters | the frozen registry record |
| Random seed | required argument, no default |
| Results | whatever the arms returned |
| Decision | promote / modify / reject / archive |

Append-only. A superseded result is **marked, never overwritten** — the
discipline `experiment_registry.md` already follows by hand.

### C. Factor Research Engine — `sirat/features/`

Generalises `research_factors.py` so a factor can be **measured without becoming
a strategy**. Supports technical, fundamental, liquidity, volatility and
alternative signals behind one interface:

```
factor.compute(pit_data, dates, universe) -> panel
```

**Hard rule: the engine returns a panel. It never returns a portfolio.** The gap
between "this factor has IC" and "therefore trade it" is where Phase 1 lost a
year, and the type system should make crossing it deliberate.

### D. Alpha Validation Engine — `sirat/validation/`

Five gates, in order, each cheap enough to kill the idea before the next runs:

| # | Gate | Test | Kills |
|---|---|---|---|
| 1 | **Predictive** | Rank IC, ICIR, permutation null, **at the intended horizon** | Signals with no information |
| 2 | **Random benchmark** | ≥30 seeded controls, matched universe/basket/exposure | Signals indistinguishable from luck |
| 3 | **Cost** | Per-period excess vs round trip **before any portfolio is built** | Signals that cannot pay their own commission |
| 4 | **Execution** | Fill probability, queue exposure ratio, requested-vs-realised shortfall | **Alpha Core would have died here, at experiment 2** |
| 5 | **Robustness** | Walk-forward, block bootstrap, sub-period stability, `overfitting_risk()` | Signals living in one regime |

**Gate 3 before gate 4, and both before a portfolio exists.** E13's cost table
(h=1 alpha 0.55% vs a 1.04% round trip) could have been computed in an
afternoon, at experiment 1.

### E. Execution Intelligence Layer — `sirat/execution/`

The discovery area, promoted to a first-class module.

| Capability | Status | Source |
|---|---|---|
| Band-agnostic lock detection | **Built** | E14 — `high==low ∧ volume>0 ∧ traded_prev ∧ 1%<ret≤12%` |
| Empirical band estimation | **Built** | per-date 99.5th percentile, reopening-excluded |
| Reopening / gap separation | **Built** | E14 — after the 115%-return trap |
| Stale-bar handling | **Built** | 22.29% of bars are zero-volume |
| Unbuyable classification | **Built** | lock ∨ reopen ∨ extreme gap |
| Queue exposure ratio | **Built** | selected-book rate ÷ universe rate |
| Requested-vs-realised shortfall | **Built** | E15 — the 81% number |
| **Fill probability model** | **BUILD** | conditional on decision-time state; the natural next step |
| **Slippage model** | **BUILD** | currently 0.0%; the largest known optimism in every result |
| **Order-flow imbalance** | **BUILD** | `client_type_history` counts are indexed and unused |

---

## 6. Research Workflow

Mandatory. Enforced by the platform, not by discipline.

| Step | Gate to proceed |
|---|---|
| **1 · Propose** | Hypothesis stated in one sentence |
| **2 · Document mechanism** | **Economic rationale signed by a second reader.** No mechanism, no experiment |
| **3 · Preregister** | `registry.freeze()` returns a token; hash recorded |
| **4 · Feasibility pre-check** | Data coverage sufficient **and** the implied turnover is within measured capacity — *before* any return is computed |
| **5 · Predictive test** | Gates 1–2 |
| **6 · Cost and execution test** | Gates 3–4 |
| **7 · Statistical validation** | Gate 5 |
| **8 · Decision** | Promote · Modify · Reject · Archive — recorded immutably |

**Step 4 is new and it is the most important addition.** Alpha Core's implied
turnover (48/yr at its viable horizon) exceeded measured capacity (4.45/yr) by
an order of magnitude. That check costs minutes and would have ended the
programme at week two.

### The decision vocabulary

- **Promote** — passes all five gates; becomes a strategy *candidate*, not a
  strategy.
- **Modify** — a specification flaw is identified **and the modification is
  registered as a new hypothesis** with its own token. Never an edit in place.
- **Reject** — fails a gate. The record stays.
- **Archive** — no remaining testable path.

---

## 7. Governance Rules — What Not To Build

| Prohibited | Why | Enforcement |
|---|---|---|
| **Endless parameter optimisation** | `validation.sensitivity()` is a grid sweep; used casually it is a p-hacking engine | Sweeps allowed **only** for robustness reporting, never for selection. Any parameter chosen from a sweep invalidates the hypothesis token |
| **Improving a failed strategy after seeing results** | The defining temptation | A rejected hypothesis token cannot be reopened. A successor needs a new token that **cites the failed one** and states what is different and why |
| **ML without an economic hypothesis** | §8 | Registry requires `expected_mechanism`; "the model finds it" is not a mechanism |
| **Backtest-only strategies** | E9 passed frictionlessly and failed in production | Gates 3–4 are not skippable |
| **Ignoring execution constraints** | The programme's cause of death | Execution simulation is mandatory before any promote decision |
| **Reusing a spent holdout** | E5 caught this once | The Experiment Manager tracks which windows each token has touched and refuses a second evaluation |
| **Threshold changes after results** | E15's G1 failed by 0.03 and stayed failed | Thresholds live in the frozen record; the runner reads them from the hash |

### Two rules earned specifically from Phase 1

1. **Absence is not zero.** Any diagnostic that cannot be measured reports
   `None` and is declared unavailable. `.get(key) or 0` is banned platform-wide.
2. **Counters from different layers are never summed.** `assert_layers_disjoint()`
   becomes a platform primitive.

---

## 8. ML Policy — When SIRAT-AI May Allow Machine Learning

**Current status: CLOSED.** Not "delayed" — closed, with named reopening
conditions.

| Precondition | Required evidence | Status |
|---|---|:--:|
| **1 · A validated signal exists** | Some hypothesis has passed all five gates, including execution | ❌ none has |
| **2 · Feature quality proven** | Point-in-time universe with delisted names; features stable across regimes | ❌ no universe snapshots |
| **3 · Sample size sufficient** | ≥ 250 independent decision dates, or a panel with genuine cross-sectional independence | ❌ 120 clean dates, ~25–35 regime episodes |
| **4 · Overfitting controls exist** | Walk-forward wired in, untouched test set, `overfitting_risk()` mandatory, multiple-comparison budget tracked | ⚠️ machinery exists, never used |

**All four must hold.** Today: **0 of 4** fully, 1 partial.

### The framing that will apply when it reopens

ML enters as **"does a learned combination beat the handcrafted one?"** — a
narrow, falsifiable question with a specific control — never as *"find alpha"*.

And it enters **after** the trivial non-ML alternative has been tested. Phase 1
produced a standing example: %B alone dominates the 50/50 blend at every
horizon, and that was never tested as a candidate. **Test the trivial answer
first** is now policy.

### What ML must never be used for

Rescuing a rejected hypothesis. If a signal fails gates 1–5, a more flexible
functional form applied to the same features is not new evidence — it is a
larger hypothesis space searched against the same spent data.

---

## 9. Future Research Domains

Ranked on the evidence, not on appeal.

| Rank | Domain | Feasibility | Expected value | Data availability | Research risk |
|---:|---|---|---|---|---|
| **1** | **C · Liquidity & microstructure** | **High** | **Moderate–high** | **Excellent** | **Low** |
| **2** | **D · Risk analytics** | **High** | **Moderate** | **Excellent** | **Low** |
| **3** | **A · Fundamental long-horizon** | **Low now** | **Highest** | **~7% — blocked** | Moderate |
| **4** | **B · Event-driven** | Moderate | Low–moderate | Partial | **High** |

### 1 · Liquidity and microstructure — *start here*

**Feasibility high, risk low, and the discovery is already ours.** 1.91M OHLCV
bars and 1.68M client-type rows; the detectors are built and validated. This is
**measurement, not prediction**, which structurally avoids the failure mode that
killed Phase 1.

Candidate questions: what predicts a صف lock one session ahead; what is the true
capacity curve of the Tehran book by ADV decile; how much does the band regime
change (10% → 5.5% → 6.9%) alter achievable turnover.

**Why it is ranked first despite moderate EV:** it produces *constraints* that
every future hypothesis must satisfy. A validated capacity curve makes Step 4 of
the workflow quantitative and would have ended Phase 1 in week two.

### 2 · Risk analytics — *build alongside*

**Does not require predicting returns.** Liquidity risk, drawdown attribution,
portfolio stress testing, concentration and staleness monitoring. Every input
exists. Directly useful as decision support even with zero alpha — which is
precisely the platform's new purpose.

### 3 · Fundamental long-horizon — *highest value, data-blocked*

**Structurally the best fit to the discovered constraint.** Low turnover means
low execution dependency — the exact axis on which Phase 1 died. A quarterly
signal needs ~4 turnovers/year, which is *within* measured capacity (4.45).

**But the data is not there.** `codal_financials` holds **2,194 parsed filings
across 764 companies — about 2.9 each, roughly 7% of a complete panel.** Against
37,383 indexed `codal_letters`, the raw material exists; the parsing does not.

**This is a data-engineering task of known size, not a research risk.** It is
ranked third only because it cannot start today. If the backfill succeeds it
becomes rank 1, and the roadmap treats it that way.

### 4 · Event-driven — *last*

`codal_letters` carries 37,383 rows with publication timestamps, so the event
data exists. But event studies live at **short horizons** — exactly where E14
found queue formation dominating. An earnings-surprise signal in a ±5% band
market will point at names locking limit-up the next morning.

**High research risk: it is the most likely domain to rediscover the Phase 1
failure in new clothes.** Do not enter it until the capacity curve from Domain C
exists to bound it.

---

## 10. 90-Day Roadmap

### Month 1 — Infrastructure and governance

| Week | Deliverable | Rationale |
|---|---|---|
| 1 | **Install git; commit; wire `code_version` into provenance** | Blocks everything auditable. Open since E5 |
| 1 | **Universe snapshot cron — weekly, starting immediately** | Only elapsed time fixes survivorship. Every week of delay is permanent |
| 2 | `sirat/registry/` — hypothesis freeze + hash | The missing enforcement point |
| 2 | `sirat/experiments/` — append-only manager, `pit.fingerprint()` wired in | Replaces 15 bespoke scripts |
| 3 | Package reorganisation into `sirat/`; `market_exposure.py` archived out of the import path; `alpha_core.py` quarantined as a negative-control fixture | Stop the rejected code being importable by accident |
| 3 | Platform primitives: `assert_layers_disjoint`, absence-is-not-zero lint, shared block bootstrap | Encode lessons 2, 10, 11 |
| 4 | **Codal backfill spike** — parse 500 letters, measure yield and cost per filing | Sizes Domain A before committing to it |

**Month 1 exit criterion:** an experiment cannot run without a frozen token and
a dataset fingerprint.

### Month 2 — Research modules

| Week | Deliverable |
|---|---|
| 5 | `sirat/features/` — factor interface returning **panels, never portfolios** |
| 5 | Migrate `research_alpha_ic.py` into validation gate 1 |
| 6 | Gates 2–3: random-control harness (extracted from `alpha_core.py:287`) and the **pre-portfolio cost test** |
| 7 | Gate 4: execution intelligence module — detectors promoted, **fill-probability model** built |
| 7 | **Slippage model** — the largest known optimism in every Phase 1 result |
| 8 | Gate 5: wire `walk_forward`, `monte_carlo`, `overfitting_risk` into the mandatory path |

**Month 2 exit criterion:** the five gates run end-to-end on a frozen token, and
**Alpha Core v1 is used as the regression fixture** — the platform must
reproduce its rejection automatically. A platform that cannot re-derive its own
known negative is not validated.

### Month 3 — First new hypotheses

| Week | Deliverable |
|---|---|
| 9 | **H-C1 · Capacity curve.** What turnover is achievable per ADV decile, per band regime? Produces the constraint Step 4 needs |
| 10 | **H-C2 · Lock prediction.** Can t+1 lock probability be predicted from decision-time state? *Explicitly a feasibility model, not an alpha model* |
| 11 | **H-D1 · Liquidity risk monitor.** Staleness, concentration and unfillability of an arbitrary portfolio |
| 12 | Codal backfill decision gate; first end-to-end research decision recorded through the platform |

**Month 3 exit criterion:** at least one hypothesis has traversed all eight
workflow steps and reached a recorded decision — **including, acceptably, a
rejection.**

---

## 11. Success Metrics

The platform's product is **correct decisions per unit of time**, not returns.

| Metric | Target | Why |
|---|---|---|
| **Time to first kill** | **< 5 working days** from proposal to reject | Phase 1 took ~11 experiments to reach its root cause |
| **Cost of a rejection** | < 10% of the cost of a full backtest | Gates ordered cheapest-first |
| **Hypotheses with a documented mechanism** | **100%** | Alpha Core never had one |
| **Experiments with a frozen token before first data touch** | **100%** | Enforced |
| **Reproducibility** | 100% re-derivable from (code hash, data fingerprint, seed) | Currently 0% — no git |
| **Feasibility pre-check coverage** | 100% of hypotheses before any return is computed | The single highest-leverage gate |
| **Holdout integrity** | Zero windows evaluated twice | Manager-enforced |
| **Regression fixture** | Alpha Core rejection re-derived on every CI run | Proves the platform still detects a known negative |
| Promoted candidates | **No target** | Deliberately unset — a rate target would corrupt the gates |

> The last row matters most. **The platform must never be measured on how many
> strategies it approves.** Phase 1's real cost was not the failed strategy; it
> was eleven months spent before the process could say so.

---

## 12. What This Plan Does Not Do

- **It does not revive the trading specification.** Alpha Core and the exposure
  engine are retained only as frozen regression fixtures and negative controls.
- **It does not promise alpha.** No domain in §9 is asserted to contain a
  tradable edge; three of the four are ranked on *feasibility of learning*, not
  expected return.
- **It does not add ML.** §8 is closed with named reopening conditions.
- **It does not assume the platform is the answer either.** If twelve months of
  Phase 2 produce no promoted candidate and no transferable finding, that is
  itself a result, and the same archive discipline applies.

---

## Provenance

| Item | Value |
|---|---|
| Phase | 2 — platform transformation |
| Basis | 15 registered experiments, `experiment_registry.md` |
| Audit method | Direct inspection of `backtesting/`, `pit.py`, `validation.py`, and both databases |
| Key audit findings | `walk_forward`/`out_of_sample`/`monte_carlo` built and never used · fundamental panel ~7% complete · no universe snapshots · no slippage model · no git |
| Code changed | **None** — this document is a plan |
| Git commit | **UNAVAILABLE — git is not installed on this machine** (Month 1, Week 1) |
