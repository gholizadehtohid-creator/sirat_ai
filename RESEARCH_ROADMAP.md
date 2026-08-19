# SIRAT-AI — NEXT RESEARCH ROADMAP

**Unlocked by the Phase 2 platform acceptance, 2026-08-19.**

Four workstreams, in a fixed order. Each is **gated**: it does not begin until
the gate above it holds, and every hypothesis inside it enters as a registered,
preregistered experiment that must survive six stages or it does not enter.

The ordering is not a preference. It is the same principle as the stage
pipeline — **answer the cheap, falsifiable question first** — applied to a year
of work instead of a week.

---

## Why this order and not the interesting one

The tempting order is ML first, because it is the most interesting. Phase 1 is
the argument against it. That programme had excellent components and no
enforcement, and it produced a well-instrumented failure. The same mistake at a
larger scale produces a better-instrumented one.

More concretely, **three of the four workstreams below cannot be evaluated
today**, and it is not a matter of effort:

| Workstream | What is missing | Can effort fix it now? |
|---|---|---|
| Fundamental | 96% of filings are 1402–1405; median 3 per company | **No** — the data must be crawled, then time must pass |
| Factor | needs the fundamental panel *or* clean price factors | partly |
| ML | needs a validated signal, a baseline, ≥250 dates | **No** — 0 of 4 preconditions |
| Microstructure | needs nothing that does not exist | **Yes** |

Microstructure is the only one that is unblocked today, and it is deliberately
**last** — because it is measurement rather than prediction, it cannot produce a
false discovery, and therefore it is the one that can safely wait. Putting it
first would be choosing the comfortable work.

---

## 1. Point-in-Time Fundamental Research Framework

**Status: BLOCKED. 0 of 4 readiness criteria.**

### The measured state

| Jalali year | Filings |
|---|---:|
| 1394–1401 *(eight years)* | **83 total** |
| 1402 | 493 |
| 1403 | 212 |
| 1404 | 762 |
| 1405 | 644 |

**96% of all parsed financials are from 1402–1405.** The parser is not the
problem — 3 extraction failures in 2,231 (0.13%), 0 fetch errors. The crawl
stopped: `pages` is 1 or 2 for every one of 818 companies, and `searched_to`
clusters at 1402/04. This is a pagination fix, not a rewrite.

### The gate — all four must hold

| # | Criterion | Threshold | Now |
|---|---|---|---|
| 1 | panel depth | ≥ 24 consecutive quarters | ~4 |
| 2 | cross-section per date | ≥ 150 companies with a filing | ~100 for 1402+, <10 before |
| 3 | filings per company | median ≥ 16 | **median 3** |
| 4 | publication-lag integrity | 100% carry `published_at`, and `fundamental_as_of()` verified by hand against 50 records | machinery exists, unverified |

**No fundamental factor may be constructed until all four pass.** A factor
measured on a four-quarter panel with a median of three filings per company is
not a factor; it is an anecdote that will pass every test you give it, because
there is not enough data for any test to fail.

### Work order

1. **Pagination depth** — remove the 1–2 page cap, crawl back to 1394. The
   binding constraint. ~1 week plus crawl time.
2. **Letter-type filter** — target financial statements; skip clarifications
   (1,196) and monthly activity reports (681/644/498). 37,383 letters contain
   perhaps 4–5k statements, and crawling blind wastes the rate budget. ~2 days.
   *Respect the two codal behaviours already on record: the 429 that renews
   itself on retry, and the two Persian normalisation forms that silently
   return nothing.*
3. **Validation rules** — balance-sheet identity, period continuity,
   restatement handling. **Before** research, not after. ~1 week.
4. **Readiness review** against the four criteria. Only then does §2 open.

### What is explicitly forbidden

Constructing any fundamental alpha factor on 1402–1405 data — including "just to
see", including as a "sanity check", including with the result marked
provisional. A provisional finding is a finding once someone quotes it.

---

## 2. Factor Research Engine

**Status: gated on §1 for fundamental factors. Open now for price-based
factors, under full governance.**

Most of this exists: `factor_engine.py`, `research_alpha_ic.py`,
`research_alpha_decay.py`, and the Stage 2 gate itself. What is missing is a
single entry point that makes the sequence non-optional.

### Requirements, each mapped to an existing control

| Requirement | Where it already lives |
|---|---|
| hypothesis registration | `Manager.register()` — 11 required fields |
| IC analysis | `gates.economic_signal` — at the **declared** horizon only |
| decay analysis | `factor_decay` check — retention of peak IC |
| transaction cost testing | `gates.execution_realism` — Stage 3, **before** robustness |
| benchmark comparison | `random_baseline` — a within-date permutation null |

### The one rule that carries most of the value

**The traded horizon is declared before measurement and only that horizon can
pass.** Alpha Core is 0.10492 at h=1 and 0.01456 at h=20, and it was traded at
h=20. Measuring every horizon and reporting the best one is a multiple
comparison; the platform records it as one.

### The first candidate, and it is not a new factor

Phase 1 left an untested trivial alternative standing: **%B alone dominates the
50/50 blend at every horizon** (−41% of IC at h=20 came from adding momentum).
A simpler model that beats the complicated one must be tested before anything
more elaborate is built. It is one registered experiment and it costs a day.

---

## 3. Machine Learning Ranking Layer

**Status: FORBIDDEN. 0 of 4 preconditions.**

| # | Condition | Now |
|---|---|---|
| 1 | a validated signal exists — some hypothesis passed stages 1–6 | ❌ none |
| 2 | data coverage — PIT universe with delisted names; fundamentals meeting §1 | ❌ neither |
| 3 | a baseline exists — the handcrafted alternative measured and beaten | ❌ none |
| 4 | sample size — ≥ 250 independent decision dates | ❌ 120 clean |

### The form ML is allowed to take when it opens

**A ranking tool, not a predictor.** The question is *"does a learned
combination beat the handcrafted one?"* — never *"find alpha"*. That framing is
not modesty; it is what makes the result falsifiable, because there is a
specific incumbent to beat.

Requirements, all mandatory:

- **No black-box prediction.** A model whose output cannot be attributed cannot
  be debugged when it fails live, and it will fail live.
- **Feature importance** reported for every model, every fold.
- **Stability testing** — feature importance that reorders between folds means
  the model was fitted to noise separately in each one. That is the same
  `parameter_stability` test `validation.walk_forward()` already applies to a
  swept parameter.
- **Walk-forward validation** — Stage 4, mandatory, no exceptions.

### What ML cannot compensate for, stated so it is not re-litigated

- **Missing data.** A learner on a survivors-only universe learns which stocks
  were not delisted. That is unavailable at decision time and catastrophic live.
- **Weak economics.** At the traded horizon Alpha Core was t = 1.23 on clean
  data. No functional form creates information that is not there.
- **Execution problems.** Trained on realised portfolio returns, a model learns
  to predict `portfolio_full` and `cash_floor` — the allocator's failures, not
  the market's.

---

## 4. Microstructure Research

**Status: unblocked today. Deliberately last.**

The highest-value domain and the safest, because it is **measurement, not
prediction** — which structurally avoids the failure mode that killed Phase 1.
Its purpose is never "will this go up"; it is "**can this be traded**".

### What exists, validated in anger

| Module | State |
|---|---|
| lock / reopening / gap / stale / unbuyable detection | **built and validated** — E14 |
| empirical per-date band estimation | **built and validated** — E14 |
| queue-exposure ratio | **built and validated** — E14, 3.03× |

### What is new, and the two holes it closes

| Module | Purpose |
|---|---|
| `capacity.py` | turnover achievable by ADV decile and band regime. **Highest priority** — it supplies the Feasibility Gate's rejection threshold, which is what makes Stage 1 quantitative rather than rhetorical |
| `impact.py` | a real slippage model. **Slippage has been 0.0% in every result this programme ever produced** |
| `fills.py` | fill probability from decision-time state only |
| `flow.py` | order imbalance from `client_type_history` — **1.68M rows spanning 2008–2026, never once used** |

### Worked example of what capacity buys

Alpha Core's viable horizon of h=5–10 implies 24–48 turnovers/year. Measured
capacity: **4.45/year**. That is a Stage 1 rejection computable in minutes, and
it would have ended Phase 1 in week two rather than after fifteen experiments.

---

## The measure of success

**Not** an approved strategy. Not a higher CAGR. Not a number of experiments.

> **A hypothesis rejected at Stage 1 in an afternoon, with an audit trail a
> third party could verify.**

That is the outcome Phase 1 could not produce, and it is worth more than
fifteen experiments' worth of retrospective clarity.

### The metrics that are tracked

| Metric | Target | Now |
|---|---|---|
| experiments with a full validation record | 100% | 100% (11 of 11 replayed) |
| reproducibility from (code, data, seed) | 100% | **100%** |
| rejections at stages 1–3 | ≥ 70% | **90.9%** |
| holdout windows evaluated twice | **0** | 0 |
| regression fixture green | every build | every build |
| ledger chain intact | every build | intact, 11 entries |

### The metrics that are never tracked

Number of strategies found. Number of profitable backtests. Backtest CAGR.

---

## Standing restrictions

Unchanged by any of the above, and not lifted by this roadmap:

- **Alpha Core is frozen** and hash-pinned. Editing it fails the build
  regardless of the numbers. It is a regression fixture, not a strategy.
- **No 1402–1405 fundamental factors** until §1's four criteria pass.
- **No ML** until its four preconditions pass.
- **No new indicators or alpha factors** outside a registered experiment.
- **The forward holdout has never been started.** It is spendable once, and
  `windows_touched` now enforces that rather than relying on someone noticing.

---

## Provenance

| | |
|---|---|
| unlocked by | `PLATFORM_ACCEPTANCE.md`, 2026-08-19 |
| git commit | `7e0d943` |
| archive tag | `v2.0-archive` |
| ledger head | `354ec88217f5e7a4…` (11 entries, chain intact) |
| code changed | **none** — this document is a plan |
