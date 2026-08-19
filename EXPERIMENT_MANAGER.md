# SIRAT-AI — EXPERIMENT MANAGER

**The research governance layer. Built 2026-08-19, Phase 2 item 1.**

---

## 1. Why this exists

Phase 1 produced a PIT data layer, an execution engine with T+1 and صف queues,
portfolio and risk modules, walk-forward, out-of-sample and Monte Carlo. Every
component worked. The programme still took fifteen experiments to reach a
conclusion that two of them could have delivered.

The audit finding, stated precisely:

> `backtesting/validation.py` is wired into the **backtest** path — `run_v2.py`,
> `backtesting/runner.py`, `validate_v2.py`. **Zero of the fifteen experiment
> scripts call any of it.** Research ran on a track that had no validation on it.

Governance existed as prose: `experiment_registry.md`, maintained by hand. The
discipline in it was real. What it could not do is **refuse**.

Every refusal in this layer maps to something that actually happened:

| Refusal | What it would have prevented |
|---|---|
| A stage cannot run before its predecessor passed | E13's cost arithmetic was an afternoon's work and was not done until experiment 13 |
| A stage that omits a required check raises | Four `null` execution counters read as "no queues were ever hit" for a whole programme (E8) |
| Thresholds come only from a hash-verified frozen record | Thirteen of fifteen experiments kept thresholds in a module docstring |
| A holdout window can be spent once | E5 caught this by hand, in a preflight audit written specially |
| PROMOTE requires a reproducible result | Every Phase 1 provenance block reads `UNAVAILABLE` |

---

## 2. Architecture

```
sirat/
  experiments/
    __init__.py      the public surface: Manager, gates, stages, universe
    manager.py       THE ENFORCEMENT POINT — register, run_stage, decide
    gates.py         the six stages, as checks that actually measure
    stages.py        stage constants, required checks, the KPI
    provenance.py    git · code · config · dataset · universe · timestamp
    store.py         append-only SQLite with immutability triggers
    universe.py      universe snapshots and their schedule
    cli.py           status · registry · show · register · snapshot · windows
  fixtures/
    alpha_core.py    the adversarial regression fixture
    build_panel.py   rebuilds the frozen panel from the market database
    data/            alpha_core_panel.csv.gz (1.8 MB) · alpha_core_expected.json

ci.py                the build
git_bootstrap.py     Week 1 item 1
examples/            lifecycle_demo.py — one experiment end to end
```

**Nothing that computes a number was reimplemented.** `backtesting/`,
`factor_engine.py` and `portfolio_engine.py` are untouched. This layer decides
when they run and whether the result stands.

### The control flow

```
                Manager.register(...)          <- refuses 11 missing fields
                        |                         freezes the preregistration
                        v                         captures provenance
              Manager.thresholds()             <- hash-verified on every read
                        |
                        v
    gates.data_integrity(panel, thresholds)    <- MEASURES, returns booleans
                        |
                        v
      Manager.run_stage(1, result)             <- refuses out of order,
                        |                         refuses incomplete,
                        v                         derives the verdict itself
             [FAIL] -> Manager.decide(REJECT)  <- names the stage, feeds the KPI
             [PASS] -> stage 2 ... stage 6
                        |
                        v
                Manager.decide(PROMOTE)        <- refuses without all 6 stages
                                                  refuses if not reproducible
```

### Why the stage order is the product

| Stage | Question | Cost to answer |
|---|---|---|
| 1 DATA INTEGRITY | Is the data even usable? | **minutes** |
| 2 ECONOMIC SIGNAL | Is there information at the traded horizon? | hours |
| 3 EXECUTION REALISM | Can it pay for itself and be filled? | hours |
| 4 ROBUSTNESS | Does it generalise? | days |
| 5 STATISTICAL | Is it distinguishable from luck? | days |
| 6 PAPER READINESS | Can it be operated? | weeks |

Stage 3 sits **before** Stage 4 deliberately. Alpha Core's 0.55% edge against a
1.04% round trip is Stage 3 arithmetic. Nothing that cannot pay its own
commission deserves a walk-forward.

---

## 3. Database schema

A new database, `sirat_research.db`, separate from the 424 MB
`sirat_market.db`. The market data gets refetched, backfilled and rebuilt; the
research record must outlive all of that and never share a transaction with a
data refresh.

| Table | Purpose | Immutability |
|---|---|---|
| `experiments` | registration + the full provenance block | insert-only by convention |
| `preregistration` | parameters, gates, expected metrics, thresholds, seed | **triggers block UPDATE and DELETE** |
| `stage_results` | one row per stage, with checks and detail | **triggers block UPDATE and DELETE** |
| `decisions` | the decision, its rationale, the stage it died at | **trigger blocks UPDATE** |
| `universe_snapshots` | frozen instrument lists, content-hashed | insert-or-ignore |
| `windows_touched` | the holdout ledger | append-only |

### Fields on `experiments`

Every one of the eleven required registration fields, plus provenance:

```
experiment_id · hypothesis · strategy_version · researcher · notes
expected_failure · acceptance_criteria · prereg_hash
git_commit · git_dirty · code_hash · config_hash
dataset_snapshot · universe_snapshot · reproducible
registered_at · status · superseded_by
```

### Three layers of tamper-evidence

The requirement is *"the system must prevent changing criteria after seeing
results"*. One mechanism is not enough, because each has a hole the next covers:

1. **SQLite triggers** raise on `UPDATE`/`DELETE` of a frozen preregistration.
   Holds wherever the write comes from, including a raw SQL prompt.
   *Hole:* a database rebuilt from scratch never runs an `UPDATE`.
2. **`content_hash` on the preregistration row**, verified on every read.
   Catches the rebuilt row.
   *Hole:* someone who rewrites the row could recompute the hash.
3. **`prereg_hash` on the `experiments` row**, cross-checked against (2).
   Two rows in two tables must agree, and `Manager.thresholds()` verifies both
   before returning a single number.

Tested directly — `test_a_rebuilt_row_that_does_not_hash_is_detected` drops the
triggers, rewrites the thresholds, and asserts that `thresholds()` still raises.

**What this does not claim:** nothing stops someone deleting the file. The
control is designed for the realistic failure — a competent person under time
pressure taking a shortcut that looks harmless — not for sabotage.

---

## 4. The six gates

Each function in `gates.py` measures, compares against a threshold handed to it
from the frozen record, and returns booleans nobody chose. **No gate has a
default threshold**; `ThresholdMissing` is raised for anything not
preregistered.

The governing rule: **a check that could not be computed is `False`, never
absent and never `True`.**

| Stage | Checks | What each actually measures |
|---|---|---|
| 1 | `point_in_time` | no forward column used as a feature; **no forward return larger than the trading calendar allows** — the `shift(-h)`-moves-rows defect that produced a −579% excess; no row outside the declared window |
| | `survivorship` | a universe snapshot exists and is named |
| | `universe_valid` | every date carries the minimum cross-section |
| | `missing_data` | score, label **and staleness** populated — a missing `stale` column fails, it does not pass |
| 2 | `ic` | mean cross-sectional IC **at the declared horizon only** |
| | `rank_correlation` | ICIR and % positive dates |
| | `random_baseline` | beats the p95 of a within-date permutation null |
| | `factor_decay` | retention of peak IC at the traded horizon — E13, as a gate |
| 3 | `transaction_costs` | excess per period minus turnover × round trip |
| | `liquidity` | basket share above the ADV floor |
| | `participation_limits` | required size against the allowance — **unmeasured fails** |
| | `slippage` | net after a preregistered charge on both legs (every Phase 1 result held slippage at 0.0%) |
| | `queue_effects` | basket lock rate / universe lock rate — E14, as a gate |
| 4 | `walk_forward` | share of out-of-fold blocks positive; or consumes `backtesting.validation.walk_forward()` |
| | `out_of_sample` | untouched holdout IC and degradation |
| 5 | `monte_carlo` | percentile against ≥5 random-ranking controls |
| | `bootstrap` | stationary block bootstrap on the **paired** difference; **an interval entirely below zero is a fail, not a pass** |
| | `confidence_intervals` | finite, and containing the point estimate |
| 6 | `risk_limits`, `kill_switch`, `monitoring`, `reconciliation` | each needs a named control **and** a named artifact; a bare `True` fails |

---

## 5. The Alpha Core regression fixture

`sirat/fixtures/alpha_core.py` — **an adversarial test, not a strategy.**

### The eleven anchors

| # | Anchor | Expected | Tolerance | Measured |
|---|---|---|---|---|
| 0 | `alpha_core_frozen` | sha256 of `backtesting/alpha_core.py` | exact | ✅ |
| 1 | `queue_ratio` | 3.03× | ±0.15 | **3.02828** |
| 2 | `h1_excess` | 0.5496% | ±0.05 | **0.54957** |
| 2b | `cost_failure_direction` | 0 < excess < 1.04% round trip | sign | ✅ |
| 3a | `requested_h20` | +3.17% | ±0.20pp | **3.17348** |
| 3b | `realised_h20` | +0.59% | ±0.20pp | **0.59298** |
| 4 | `tail_percentile` | ≤ 10th | — | **3.33** |
| 5 | `ic@h1/5/10/20` | 0.10492 / 0.05101 / 0.03003 / 0.01456 | ±0.002 | **exact to 5 dp** |

### The tolerances are two-sided, and that is the design

A number that drifts in the **favourable** direction fails exactly as hard as
one that does not, and the report says which way it moved:

```
[FAIL] queue_ratio  measured None  expected 3.03000 +/- 0.15
       -> MOVED FAVOURABLY -- not one limit-locked bar was found anywhere in
          120 dates. The queue artifact has not shrunk, it has vanished, which
          on this market means the detector broke. 2.56% of the universe is
          normally locked.
```

Every time this project has seen a rejected result improve, the cause was a
defect: a stale-bar trap, a `shift(-h)` across a suspension, a fill model that
silently refilled unbuyable slots. `tests/test_alpha_core_fixture.py::Adversarial`
injects each of those and asserts the build goes red.

### Two tiers

| Tier | Cost | What it proves |
|---|---|---|
| **1** — every build | ~5s | all eleven anchors recomputed **through the live analytics code** from a frozen 1.8 MB panel. Change how IC is measured, how a decile is cut, how an unfilled slot is treated → red |
| **2** — `ci.py --full` | minutes | rebuilds that panel from `sirat_market.db` + `factor_panel.csv` and asserts it still hashes the same |

Tier 1 is not "read the recorded numbers back". The panel holds only **inputs**
— scores, forward returns, next-session microstructure state. Every anchor is
computed when the test runs.

### One thing the fixture taught us during construction

The first Tier-1 run produced `realised_h20 = 0.331` against E15's 0.593. The
cause was not a defect in either: E15 credits an unfilled slot with the policy
rate over the holding period, and Iran's is 23%. Twenty sessions of idle cash on
~15% of the book is +0.26pp. Setting it to zero would have reported the
execution failure as nearly twice as severe as it is. That arithmetic is now in
`cash_per_period()` with the reasoning attached.

---

## 6. Week 1 reproducibility items

| Item | State |
|---|---|
| **git repository** | ⚠️ **BLOCKED — git is not installed on this machine.** `git_bootstrap.py` performs `init` + commit + `v2.0-archive` tag the moment it exists; until then it exits 2 with the install command. `.gitignore` is written and excludes `sirat_market.db` (424 MB), `sirat_fundamentals.db`, `__pycache__/`, `backtest_output/cache/` and the three regenerable large CSVs. |
| **Immutable provenance** | ✅ Every experiment carries git commit, code hash, config hash, dataset snapshot, universe snapshot and execution timestamp. Missing git is recorded as `UNAVAILABLE` + `reproducible=0`, and `Manager.decide()` **refuses PROMOTE**. |
| **Universe snapshot scheduler** | ✅ `sirat/experiments/universe.py`. First real snapshot captured: `u20260819-668658bb`, **1,394 instruments**, restorable and hash-verified. |

### The degradation is loud, not silent

Because git is absent, the demo's passing experiment reaches Stage 6, passes
every gate, and is then **refused promotion**:

```
Every stage passed. Attempting PROMOTE ...
REFUSED: cannot PROMOTE a result that cannot be re-derived: git is not
available. Remedy: install git, `git init`, commit the research code
decision            ARCHIVE
```

Research proceeds. Capital does not move.

### A defect this work found and fixed

The research database already held a snapshot, `UNIV-20260818`, written by an
earlier partial implementation. It does not restore — its payload does not hash
to its recorded value. But `due()` read only the timestamp, so it reported the
universe as **fresh and frozen when it was neither**, and the weekly schedule
would never have fired again.

`due()` now verifies the latest snapshot actually restores, and treats an
unverifiable one as absent. This is the same absence-read-as-approval failure
that let four `null` counters pass for "no queues were ever hit", in different
clothes.

---

## 7. CI

```
python ci.py                 unit suite + fixture tier 1 + governance report
python ci.py --full          adds tier 2 (needs the 424 MB market database)
python ci.py --json          machine-readable
run.bat ci                   the same, from the launcher
```

`.github/workflows/ci.yml` runs the build on push, PR, and weekly. Tier 2 runs
only on a self-hosted runner that has the market data, and is
`continue-on-error` — a missing database is an infrastructure fact, not a code
defect.

**A red build means one of three things and the report says which:** `TESTS`
(the platform broke), `FIXTURE` (the platform can no longer rediscover its own
negatives, and is not entitled to judge anything else), `GOVERNANCE` (a warning
— the record is in a state worth looking at).

Governance never fails the build. A build that went red because the KPI slipped
would get the KPI deleted; one that reports it every time gets it looked at.

---

## 8. The KPI

**Not** the number of experiments. **Not** the number of profitable backtests.

> At which stage are bad ideas rejected?  Target: **≥70% at stages 1–3.**

```
run.bat research            → the KPI
python -m sirat.experiments.cli status
```

`stages.cheap_rejection_rate()` counts **only rejections** — including passing
experiments would let a quiet quarter flatter the number. With no rejections the
rate is `None`, not 100%: a platform that has rejected nothing has not been
asked a question.

Phase 1 baseline: every rejection at the Stage 4–5 equivalent. **~0% cheap.**

---

## 9. Fundamental data restriction — enforced, not just documented

**No fundamental research on 1402–1405 data.** The measured state:

| Jalali year | Filings |
|---|---:|
| 1394–1401 *(eight years)* | **83 total** |
| 1402–1405 | 2,111 — **96% of everything** |

Median filings per company: **3**. Usable consecutive quarters: **~4**.

A factor measured on a four-quarter panel with a median of three filings per
company is not a factor; it is an anecdote that will pass every test you give
it. Four criteria must all hold before this reopens: ≥24 consecutive quarters,
≥150 companies per date, median ≥16 filings per company, and publication-lag
integrity verified by hand on a sample of 50.

The Stage 1 gate reaches this today through `min_names_per_date` — a fundamental
panel over this period cannot satisfy a cross-section requirement it does not
have the companies for.

---

## 10. Test coverage

| Suite | Tests | Covers |
|---|---:|---|
| `test_experiment_manager.py` | 42 | registration, preregistration immutability, stage ordering, decisions, holdout ledger, KPI |
| `test_experiment_gates.py` | 47 | all six gates, on synthetic panels with known answers |
| `test_alpha_core_fixture.py` | 20 | eleven anchors + adversarial corruption + panel integrity |
| `test_universe_snapshot.py` | 17 | capture, restore, hash verification, scheduling |
| **new total** | **126** | |
| **whole suite** | **910** | 784 pre-existing, all still passing |

One test is worth naming: `test_the_random_baseline_is_calibrated_rather_than_infallible`.
A single noise panel beats a 95th-percentile null 5% of the time **by
construction**, so asserting that one panel fails would assert that a coin came
up tails — and someone would eventually "fix" it by picking a friendlier seed.
The test runs twelve independent noise panels and bounds the pass rate instead.

---

## 11. Example lifecycle

```
run.bat lifecycle              a hypothesis that fails
run.bat lifecycle --passing    one that survives all six stages
```

The default is a **failing** experiment, because the platform's success
criterion is that bad ideas die cheaply. It tests short-horizon reversal —
chosen because Phase 1 already knows the answer — and the run shows, in order:

1. running a stage before registering → **refused**
2. registering without expected failure conditions → **refused**
3. provenance captured: prereg hash, code hash, config hash, dataset
   fingerprint, universe snapshot, `git_commit=UNAVAILABLE`, `reproducible=False`
4. moving a threshold after registration → **refused at the database**
5. skipping to Stage 4 → **refused**
6. Stage 1 passes; **Stage 2 fails on `factor_decay`** — retention 0.048 against
   a required 0.50. IC 0.469 at h=1, 0.033 at h=20. Alpha Core's exact shape,
   found in seconds instead of thirteen experiments
7. attempting PROMOTE anyway → **refused**
8. REJECT recorded at Stage 2, counted as a cheap rejection
9. running anything afterward → **refused**
10. KPI: **100% cheap rejection**

---

## 12. What is deliberately not built

- **No microstructure module.** The brief is explicit and it is right: build the
  manager first. Phase 1 failed with excellent components and no enforcement.
- **No ML.** 0 of 4 preconditions hold.
- **No fundamental factors.** See §9.
- **No changes to Alpha Core.** It is frozen and hash-pinned. Editing it fails
  the build regardless of the numbers.
