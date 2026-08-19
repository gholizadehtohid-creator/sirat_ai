# SIRAT-AI V2 — RESEARCH GOVERNANCE PLATFORM
## IMPLEMENTATION PLAN

**2026-08-18 · CTO / Head of Quant Research · post-archive**

Companion to `SIRAT_AI_RESEARCH_PLATFORM_V2_PLAN.md` (architecture). This
document is the **build order**: concrete modules, concrete criteria, concrete
sequence.

---

## 1. Executive Summary

Phase 1 ended correctly: fifteen registered experiments archived a trading
specification with a complete causal account and without ever burning the
forward holdout.

The diagnosis of *why* it took fifteen experiments is not that the platform
lacked capability. **`walk_forward()`, `out_of_sample()` and `monte_carlo()`
were built, tested, and never called once.** The platform could have caught its
own failures and was never asked to.

> **The next phase is integration, not feature accumulation.** Almost every
> component this plan needs already exists in the repository. What does not
> exist is anything that *forces* their use.

Three things are genuinely new work: an **Experiment Manager** that makes
validation non-optional, a **Feasibility Gate** that costs minutes and would
have ended Phase 1 in week two, and a **Microstructure module** that promotes
the programme's one real discovery into reusable capability.

### The audit finding that reorders this plan

Section 9 was expected to specify a parser-improvement project. **The evidence
says the parser is fine and the crawl is shallow.** Of 2,231 attempted filings
only **3** failed extraction; every one of 818 companies has `error = None`; and
`pages` is **1 or 2** for all of them. The backfill fetched the most recent
20–40 letters per company and stopped.

**Consequence: 96% of all parsed financials are from 1402–1405. The eight years
before that hold 83 filings in total.** The fundamental panel is not
badly-parsed, it is barely-crawled — a pagination fix, not a rewrite.

---

## 2. Lessons from Fifteen Experiments — as Enforceable Rules

Each lesson is paired with the mechanism that makes it impossible to repeat.

| Lesson | Enforcement mechanism | Stage |
|---|---|---|
| The control arm decides the answer | `null_hypothesis` must **name the control arm**; runner reads it from the frozen record | 0 |
| Required turnover exceeded market capacity by 11× | **Feasibility Gate** rejects before any return is computed | 1 |
| Signal horizon must match trading horizon | `expected_holding_period` is required and gate 2 measures IC **at that horizon only** | 0 → 2 |
| Significance is not tradability | **Cost Gate runs before portfolio construction** | 3 |
| Microstructure can manufacture apparent alpha | **Execution Gate** measures queue-exposure ratio on every candidate | 4 |
| Frictionless tests localise, never validate | A frictionless arm alone cannot produce a *promote* decision | 5 |
| Reporting defects masquerade as findings | Absence-is-not-zero lint; `assert_layers_disjoint()` as a primitive | all |
| Contaminated windows inflate silently | Manager tracks touched windows and refuses a second evaluation | all |
| Gates can pass meaninglessly | Every gate carries a **sign and magnitude** condition, not just a comparison | all |
| Thresholds must not move after results | Thresholds live in the hashed record; runner cannot see any other value | 0 |
| Validation existed and was never used | **An experiment cannot reach a decision without a validation status** | 6 |
| `shift(-h)` moves rows, not sessions | Session-aware forward-return helper in the feature layer; direct `shift` banned | features |

---

## 3. Product Identity

**SIRAT-AI is a Quantitative Research Governance Platform.**

It answers five questions in a fixed order, and stops at the first *no*:

| # | Question | Stage | Cost to answer |
|---|---|---|---|
| 1 | Could this be traded at all? | 1 — Feasibility | **minutes** |
| 2 | Is there predictive information? | 2 — Predictive | hours |
| 3 | Does it survive costs? | 3 — Cost | minutes |
| 4 | Is it executable? | 4 — Execution | hours |
| 5 | Is it statistically credible? | 5–6 — Portfolio + Validation | days |

**The ordering is the product.** Phase 1 answered these in roughly reverse
order, which is why it cost fifteen experiments.

---

## 4. Architecture

```
sirat/
  data/           PIT contract · back-adjust · fingerprint · universe snapshots
  features/       factor library · panel builder · session-aware forwards
  registry/       hypothesis freeze + hash                          [NEW]
  gates/          feasibility · predictive · cost · execution       [NEW]
  sandbox/        frictionless arm · portfolio arm · random controls
  execution/      T+1 · queues · participation · partials · slippage
  microstructure/ queue risk · liquidity · fill probability · capacity  [NEW]
  validation/     walk-forward · OOS · Monte Carlo · bootstrap · overfit
  experiments/    manager · provenance · decisions                  [NEW]
  fixtures/       known-failed-strategy regression suite            [NEW]

archive/          market_exposure.py · alpha_core.py (import-blocked)
```

**Reuse ratio: 6 of 10 packages are existing code being moved and wrapped.**
Four are new, and two of those (`registry`, `experiments`) are the governance
layer whose absence caused Phase 1.

---

## 5. Experiment Lifecycle

### Stage 0 — Research Proposal

```
registry.propose(
    hypothesis, economic_mechanism, expected_holding_period,
    expected_turnover, expected_capacity, null_hypothesis,
    success_criteria, failure_criteria, author) -> token
```

All nine fields required. `economic_mechanism` needs a **second reader's
signature** — Alpha Core never had a mechanism, and no one had to notice.

`registry.freeze(token)` hashes the record. **No downstream call accepts an
unfrozen token.**

### Stage 1 — Feasibility Gate *(runs before any return is computed)*

```
gates.feasibility(token, universe) -> PASS | REJECT
```

| Check | Source | Rejects if |
|---|---|---|
| Required turnover vs measured capacity | `microstructure.capacity_curve()` | required > capacity |
| Liquidity requirement vs ADV distribution | `pit.average_value()` | position size > participation allowance |
| Expected cost vs claimed gross edge | round trip × turnover | cost ≥ claimed edge |
| Data coverage | `pit.fundamental_coverage()` etc. | insufficient for the stated horizon |

> **Worked example — Alpha Core would have died here.** Viable horizon h=5–10
> implies 24–48 turnovers/year. Measured capacity: **4.45/year.** Rejected in
> minutes, at experiment 1.

### Stage 2 — Predictive Test *(no portfolio)*

IC, ICIR, hit rate, rank stability, permutation null — **at the declared holding
period only.** Testing every horizon and selecting the best is a multiple
comparison and the manager records it as such.

### Stage 3 — Cost Gate *(before execution simulation)*

```
gross edge − commission − spread − turnover cost − expected impact = net edge
```

Rejects if net ≤ 0. **E13's table (h=1 alpha 0.55% vs a 1.04% round trip) is
exactly this calculation and it took an afternoon.**

### Stage 4 — Execution Gate

Fill probability, partial-fill rate, **queue-exposure ratio**,
requested-vs-realised shortfall. Rejects if the queue ratio exceeds a
preregistered multiple of the universe rate.

> **E14 measured 3.03× and E15 showed that removing it removes the alpha.** Any
> candidate above ~1.5× is presumed to be a microstructure artifact until proven
> otherwise.

### Stage 5 — Portfolio Test

Only surviving candidates: backtest, walk-forward, out-of-sample.

### Stage 6 — Statistical Validation *(mandatory)*

Monte Carlo, block bootstrap, ≥30 random controls, sub-period stability,
`overfitting_risk()`. **The manager will not accept a decision without a
validation status object.**

### Decisions

**Promote** (a *candidate*, not a strategy) · **Modify** (a **new token citing
the old**, never an edit in place) · **Reject** · **Archive**.

---

## 6. Experiment Manager

### Record

| Field | Source | Blocking |
|---|---|:--:|
| `experiment_id` | generated | |
| `hypothesis_token` + hash | registry | ✅ |
| `author`, `timestamp` | caller | ✅ |
| `git_commit` | `git rev-parse HEAD` | ✅ **blocked today** |
| `dataset_fingerprint` | `pit.fingerprint()` | ✅ exists |
| `parameters` | frozen record | ✅ |
| `seed` | required argument, **no default** | ✅ |
| `windows_touched` | runner | ✅ holdout protection |
| `validation_status` | validation engine | ✅ |
| `results`, `decision` | runner | |

Append-only. Superseded results are **marked, never overwritten** — the
discipline `experiment_registry.md` already follows by hand.

### Reproducibility contract

```
reproduce(experiment_id) -> identical results
```
from code version + data version + seed + configuration alone.

**Current state: 0% — no git.** This is the first thing Week 1 fixes.

---

## 7. Validation Governance

The audit's core finding, made structural.

| Function | Status | Integration |
|---|---|---|
| `walk_forward()` | built, **0 uses** | mandatory at Stage 5 |
| `out_of_sample()` | built, **0 uses** | mandatory at Stage 5 |
| `monte_carlo()` | built, **0 uses** | mandatory at Stage 6 |
| `sensitivity()` | built, used ad hoc | **restricted** — robustness reporting only; a parameter chosen from a sweep **invalidates the token** |
| `overfitting_risk()` | built, unused | mandatory at Stage 6 |
| Block bootstrap | ad hoc in E15 | extracted to `validation/` |
| Random controls | inside `alpha_core.py:287` | extracted to `sandbox/` |

**Rule: `manager.complete()` raises without a validation status.** No experiment
can be marked complete otherwise. That single constraint is the difference
between Phase 1 and Phase 2.

---

## 8. Regression Fixture — Known Failed Strategy Test

`sirat/fixtures/known_failed/` — **Alpha Core v1 is kept, frozen, and run on
every CI build.**

The platform must automatically rediscover all four failures:

| # | Must rediscover | Expected value | Tolerance |
|---|---|---|---|
| 1 | **Queue artifact** | top-decile lock ratio **3.03×** | ±0.15 |
| 2 | **Cost failure** | h=1 excess 0.55% < 1.04% round trip | sign must hold |
| 3 | **Execution failure** | requested +3.17% → realised +0.59% at h=20 | ±0.20pp |
| 4 | **Portfolio rejection** | 3.3rd percentile vs random | ≤ 10th |

Plus the reference IC curve: **0.10492 / 0.05101 / 0.03003 / 0.01456** at
h=1/5/10/20 (clean window, ±0.002).

> **If the platform cannot reject Alpha Core again, the platform is invalid and
> the build is red.** A governance platform that cannot re-derive its own known
> negative has no standing to judge a new idea.

The exposure engine gets a second fixture: it must re-fail its 8-gate holdout at
1 of 8.

---

## 9. Fundamental Data Roadmap

### Measured state — and a correction to the assumed priorities

| Stage | Count | Reading |
|---|---:|---|
| Letters indexed | 37,383 across 792 instruments | but see composition below |
| Filings parsed | 2,231 across 764 | |
| Financials stored | 2,194 across 764 | ≈ 2.9 per company |
| **Extraction failures** | **3** | *"no income or balance sheet the extractor could locate"* |
| **Fetch errors** | **0** — all 818 rows have `error = None` | |
| **Pages crawled per company** | **1 or 2** | 422 companies at 2 pages, 396 at 1 |
| Letters kept per company | 40 (398 firms) or 20 (351 firms) | a hard cap |

**The 37,383 letters are mostly not financial statements.** The largest classes
are clarification notices (1,196) and monthly activity reports (681/644/498).
Actual interim financial statements appear at ~460–490 per period.

### Period distribution — the real blocker

| Jalali year | Filings |
|---|---:|
| 1394–1401 *(eight years)* | **83 total** |
| 1402 | 493 |
| 1403 | 212 |
| 1404 | 762 |
| 1405 | 644 |

**96% of all financials are from 1402–1405.** `searched_to` clusters at
1402/04, confirming the crawl stopped there.

### Revised priority order

The brief proposed *parser coverage → failed recovery → validation rules →
universe expansion*. The evidence reorders it:

| Priority | Task | Justification | Effort |
|---|---|---|---|
| **1** | **Pagination depth** — remove the 1–2 page cap, crawl to 1394 | The binding constraint. 96% of history is simply un-fetched | ~1 week + crawl time |
| **2** | **Letter-type filter** — target financial statements, skip clarifications and monthly reports | 37,383 letters contain perhaps 4–5k statements; crawling blind wastes the rate budget | ~2 days |
| **3** | **Validation rules** — balance-sheet identity, period continuity, restatement handling | Needed *before* research, not after | ~1 week |
| **4** | *Parser coverage* | **Demoted: 3 failures in 2,231 (0.13%).** Not a problem | as-needed |
| **5** | *Universe expansion* | **Demoted: 764 of 792 instruments already covered (96%)** | none needed |

**Note on rate limits:** `codal-api-behaviour` in the operator notes records
that codal.ir returns a 429 which *renews itself on retry*, and that two Persian
normalisation forms silently return nothing. The crawl must respect both or the
backfill will appear to succeed while returning empty.

### Minimum readiness criteria — fundamental research is BLOCKED until all four hold

| # | Criterion | Threshold | Now |
|---|---|---|:--:|
| 1 | Panel depth | ≥ 24 consecutive quarters | ~4 usable |
| 2 | Cross-section per date | ≥ 150 companies with a filing in the period | ~100 for 1402+, <10 before |
| 3 | Filings per company | median ≥ 16 | **median 3** |
| 4 | Publication-lag integrity | 100% of records carry `published_at`, and `fundamental_as_of()` verified against a sample of 50 by hand | machinery exists, unverified |

**Do not begin fundamental factor research until all four pass.** A factor
measured on a 4-quarter panel is not a factor; it is an anecdote.

---

## 10. Microstructure Module

The highest-value domain, because it is **measurement, not prediction** — which
structurally avoids the failure mode that killed Phase 1.

```
microstructure/
  detect.py     lock · reopening · gap · stale · unbuyable      [BUILT E14]
  band.py       empirical per-date band estimation              [BUILT E14]
  queue.py      queue-exposure ratio, top-vs-universe           [BUILT E14]
  fills.py      fill probability from decision-time state       [BUILD]
  capacity.py   turnover capacity by ADV decile and regime      [BUILD]
  impact.py     slippage / market impact                        [BUILD]
  flow.py       order imbalance from client_type_history        [BUILD]
```

**Purpose is never "will this go up". Purpose is "can this be traded".**

Three components are already validated in anger. Four are new, and two of them
close known holes: **slippage has been 0.0% in every result the programme ever
produced**, and `client_type_history` — 1.68M rows of individual/institutional
buy/sell counts spanning 2008–2026 — has never been used.

`capacity.py` is the highest priority: it supplies the Feasibility Gate's
rejection threshold, which is what makes Stage 1 quantitative rather than
rhetorical.

---

## 11. Machine Learning Policy

**FORBIDDEN.** Four conditions, all required to reopen:

| # | Condition | Status |
|---|---|:--:|
| 1 | A validated signal exists — some hypothesis has passed Stages 1–6 | ❌ none |
| 2 | Data coverage sufficient — PIT universe with delisted names; fundamentals meeting §9 readiness | ❌ neither |
| 3 | A baseline model exists — the handcrafted alternative is measured and beaten | ❌ none |
| 4 | Sample size adequate — ≥ 250 independent decision dates | ❌ 120 clean, ~30 episodes |

**0 of 4.**

### What ML cannot compensate for

- **Missing data.** A learner on a survivors-only universe learns *which stocks
  were not delisted* — unavailable at decision time, catastrophic live.
- **Weak economics.** At the traded horizon Alpha Core was t = 1.23 on clean
  data. No functional form creates information.
- **Execution problems.** Trained on realised portfolio returns, a model learns
  to predict `portfolio_full` and `cash_floor` — the allocator's failures, not
  the market's.

When it reopens, ML enters as *"does a learned combination beat the handcrafted
one?"* — never *"find alpha"* — and **only after the trivial alternative is
tested.** Phase 1 left one standing: %B alone dominates the 50/50 blend at every
horizon and was never tested as a candidate.

---

## 12. 90-Day Execution Plan

### Week 1 — three mandatory items

| Item | Why it matters |
|---|---|
| **Install git; `git init`; commit** | **Verified absent today: `git` is not on PATH and there is no `.git` directory.** Reproducibility is currently **0%** — no experiment in this repository can be re-derived from a code version, because there are no code versions. Every provenance block for fifteen experiments reads "UNAVAILABLE". This blocks the Experiment Manager, which cannot record `git_commit`. `.gitignore` must exclude `sirat_market.db` (424 MB), `sirat_fundamentals.db`, `__pycache__/` and `backtest_output/cache/`. |
| **Repository versioning — tag `v2.0-archive`** | Phase 1's exact state must remain recoverable. The regression fixture compares against numbers produced by that code; without a tag, "reproduce E14" has no referent. |
| **Universe snapshot cron — weekly, starting immediately** | Survivorship is the one defect **only elapsed time can fix**. `survivorship.py` states no snapshot has ever been collected; measured attrition of 1.7%/decade is a *lower bound* because names delisted before the DB was built were never fetched. Every week of delay is a week of history permanently unrecoverable. Starting it costs an afternoon and pays for a decade. |

### Month 1 — Research governance

| Week | Deliverable |
|---|---|
| 1 | The three items above |
| 2 | `registry/` — propose, freeze, hash · `experiments/` — append-only manager with `pit.fingerprint()` and `git_commit` wired |
| 3 | Package reorganisation into `sirat/`; `market_exposure.py` and `alpha_core.py` moved to `archive/` and **import-blocked** except by the fixture suite |
| 3 | Platform primitives: absence-is-not-zero lint, `assert_layers_disjoint()`, session-aware forward-return helper (bans raw `shift(-h)`) |
| 4 | **Validation integration** — `manager.complete()` raises without a validation status |
| 4 | **Regression fixture suite** wired to CI with the §8 tolerances |

**Exit criterion:** an experiment cannot run without a frozen token, and cannot
complete without a validation status. CI re-derives Alpha Core's rejection.

### Month 2 — Data and execution intelligence

| Week | Deliverable |
|---|---|
| 5 | `microstructure/capacity.py` — the capacity curve. **Unblocks the Feasibility Gate** |
| 5 | `microstructure/impact.py` — first slippage model, replacing 0.0% |
| 6 | `gates/` — feasibility, predictive, cost, execution, in that order |
| 6 | `microstructure/fills.py` — fill probability from decision-time state |
| 7 | **Codal pagination fix** (§9 priority 1) + letter-type filter; launch the deep backfill |
| 8 | Fundamental validation rules; `microstructure/flow.py` on `client_type_history` |

**Exit criterion:** Stages 0–4 run end-to-end; the backfill is running and its
completion date is estimable from observed throughput.

### Month 3 — First research candidates

| Week | Deliverable |
|---|---|
| 9 | **H-C1 · Capacity curve** — turnover achievable by ADV decile and band regime |
| 10 | **H-C2 · Lock prediction** — t+1 lock probability from decision-time state. *A feasibility model, explicitly not an alpha model* |
| 11 | **H-D1 · Liquidity risk monitor** — staleness, concentration, unfillability of an arbitrary portfolio |
| 12 | Fundamental readiness review against the §9 four criteria; first end-to-end decision recorded through the platform |

**No target number of approved strategies.** Success is the **quality of
rejection**: a hypothesis that fails should fail at the earliest stage that can
detect it, with evidence a third party could check.

---

## 13. Success Metrics

**Never measured:** number of strategies found · number of profitable backtests.

| Metric | Target | Baseline |
|---|---|---|
| **Experiments with full validation status** | **100%** | ~0% — validation module never called |
| **Reproducibility** from (code, data, seed) | **100%** | **0%** — no git |
| **Time to reject a bad hypothesis** | **< 5 working days** | Phase 1: ~11 experiments to root cause |
| **Stage at which rejections occur** | ≥ 70% at Stages 1–3 | Phase 1: all at Stage 5+ |
| **False-positive reduction** | Frictionless-only passes never reach *promote* | E9 passed at the 100th percentile |
| Hypotheses with a signed mechanism | 100% | Alpha Core had none |
| Holdout windows evaluated twice | **0** | 0 — preserve it |
| Regression fixture green | every CI run | not yet wired |

**The most important metric is the fourth.** A platform whose rejections happen
at Stage 5 is a backtester. A platform whose rejections happen at Stage 1 is a
governance platform. Phase 1 operated entirely in the first mode.

---

## 14. Final Recommendation

**Proceed with the 90-day plan, in the stated order, and resist the two
temptations it is designed to prevent.**

### The two temptations

1. **Building features before enforcement.** The microstructure module is more
   interesting than the Experiment Manager. Build the manager first. Phase 1
   failed with excellent components and no enforcement, and repeating that with
   better components produces a better-instrumented failure.
2. **Starting fundamental research early.** The 1402–1405 data looks usable and
   is not: a 4-quarter panel with a median of 3 filings per company will produce
   a factor that appears to work. **Hold the §9 readiness gate.**

### What would make this phase a success

Not an approved strategy. **A hypothesis rejected at Stage 1 in an afternoon,
with an audit trail a third party could verify** — because that is the outcome
Phase 1 could not produce, and it is worth more than fifteen experiments'
retrospective clarity.

### What would make it a failure

Reaching month twelve with no promoted candidate **and** no transferable finding
**and** no reduction in time-to-rejection. Then the same archive discipline
applies to the platform itself.

---

## Provenance

| Item | Value |
|---|---|
| Phase | 2 — implementation plan |
| Companion | `SIRAT_AI_RESEARCH_PLATFORM_V2_PLAN.md` (architecture) |
| Basis | 15 registered experiments; direct inspection of the repo and both databases |
| New audit findings | Codal crawl capped at 1–2 pages · 3 extraction failures in 2,231 (0.13%) · 0 fetch errors · 96% of financials from 1402–1405 · 83 filings across 1394–1401 · git verified absent |
| Code changed | **None** — this document is a plan |
| Git commit | **UNAVAILABLE — verified: `git` not on PATH, no `.git` directory.** Week 1, item 1 |
