# SIRAT-AI — RESEARCH PLATFORM VALIDATION

**Final acceptance. 2026-08-19.**

Seven phases executed in order. **All pass.** SIRAT-AI is a reproducible
quantitative research platform: results carry provenance, evidence is
re-verified rather than trusted, history cannot be rewritten, and a strategy
cannot reach capital without surviving six stages in order.

Supersedes the Phase 2 stabilization report of the same name.

---

## A. Repository reproducibility

| Item | Value |
|---|---|
| git | 2.55.0.windows.4 |
| HEAD | `8410dd67879be5d157a00af4376774d9d0a2f59c` |
| archive tag | **`v2.0-archive`** → `b145fc4a26f3de61747e3322679639d756f6df28` |
| working tree | **clean** — 0 modified paths |
| tracked files | 245 |
| `sirat_market.db` (424 MB) | ignored, confirmed by `git check-ignore` |
| `git_bootstrap.py --check` | **`reproducible = True`** |

Provenance recorded on every experiment: `git_commit`, `git_dirty`, `code_hash`,
`config_hash`, `dataset_snapshot`, `universe_snapshot`, `executed_at`, `python`.

`code_hash` covers the source **as it sat on disk at execution**, not the
commit, because fifteen Phase 1 experiments ran from dirty trees and a result
produced from uncommitted edits must not be able to pass as the commit beneath
it.

> **Tool-environment note.** Shells spawned by this session inherit a PATH
> snapshot from before git was installed, so they report `reproducible=False`
> unless `C:\Program Files\Git\cmd` is prepended. Git is on the machine PATH and
> any newly-opened terminal resolves it. Every figure here was measured with git
> visible.

---

## B. CI

```
python ci.py     →     BUILD GREEN     (exit 0)
```

| | |
|---|---|
| tests run | **934** |
| passed | **934** |
| failed | **0** |
| errors | **0** |

784 pre-existing (unchanged, all passing) + 150 new: 47 Manager, 47 gates,
20 fixture, 17 universe, 19 ledger.

Three stages, reported separately:

| Stage | Fails the build? | Why |
|---|---|---|
| `TESTS` | yes | the platform broke |
| `FIXTURE` | yes | the platform can no longer rediscover its own negatives, so it may not judge anything else |
| `GOVERNANCE` — KPI, snapshot age | **no** | management signals; one wired to a red build becomes a deleted signal |
| `GOVERNANCE` — **ledger chain** | **yes** | not a signal about research: it means the record of what was decided has been altered |

---

## C. Alpha Core regression

Frozen, hash-pinned, **not optimized**. Tier 1 recomputes every anchor through
the live analytics code from a frozen 66,623-row panel; Tier 2 rebuilds that
panel from the 424 MB database first.

| # | Anchor | Expected | Tolerance | **Measured** | Δ |
|---|---|---|---|---|---|
| 0 | `alpha_core_frozen` | `90024d86d7a5c268` | exact | `90024d86d7a5c268` | — |
| 1 | **queue ratio** | 3.03× | ±0.15 | **3.02828** | −0.0017 |
| 2 | **cost direction** | 0 < excess < 1.04% | sign | **0.54957%** ✅ | — |
| 2b | `h1_excess` | 0.5496% | ±0.05 | **0.54957** | −0.00003 |
| 3a | **requested h=20** | +3.17% | ±0.20pp | **3.17348** | +0.0035 |
| 3b | **realised h=20** | +0.59% | ±0.20pp | **0.59298** | +0.0030 |
| 4 | **tail percentile** | ≤ 10th | — | **3.33rd** | — |
| 5 | IC @ h=1 | 0.10492 | ±0.002 | **0.10492** | 0.00000 |
| 5 | IC @ h=5 | 0.05101 | ±0.002 | **0.05101** | 0.00000 |
| 5 | IC @ h=10 | 0.03003 | ±0.002 | **0.03003** | 0.00000 |
| 5 | IC @ h=20 | 0.01456 | ±0.002 | **0.01456** | 0.00000 |

**ALL ANCHORS REPRODUCED**, Tier 1 and Tier 2. The IC curve reproduces exactly
to five decimals. Tier 2 panel hash: `618d194e5bc2d4af`.

### Proof: "if Alpha Core gets artificially better, CI fails"

`python examples/prove_fixture_detects.py` — seven corruptions, six of which
make Alpha Core look **better**. Every one turns the build red and is attributed
to the anchor that should have caught it.

| Corruption | Build | Anchor named | Labelled favourable |
|---|---|---|---|
| queue detector switched off | **RED** | `queue_ratio` | ✅ |
| queue detector weakened (ratio → 1.27) | **RED** | `queue_ratio` | ✅ |
| unbuyable slots refilled (realised → 3.17) | **RED** | `realised_h20` | ✅ |
| forward returns leak the score (IC h=20 → 0.218) | **RED** | `ic@h20` + 7 others | ✅ |
| h=1 edge inflated past the round trip (→ 1.63%) | **RED** | `cost_failure_direction` | ✅ |
| E11 treatment arm improved (→ 100th pct) | **RED** | `tail_percentile` | ✅ |
| `backtesting/alpha_core.py` edited | **RED** | `alpha_core_frozen` | n/a |

Example of what a favourable failure reads like:

```
[FAIL] queue_ratio  measured None  expected 3.03000 +/- 0.15
   -> MOVED FAVOURABLY -- not one limit-locked bar was found anywhere in 120
      dates. The queue artifact has not shrunk, it has vanished, which on this
      market means the detector broke. 2.56% of the universe is normally locked.
```

**What is proved:** these seven are caught, and the direction is named.
**What is not:** that every possible corruption is caught — no finite set of
injections can show that. The two-sided band gives the general property; these
seven show it holds for the failure modes this codebase has actually produced.

---

## D. Governance — Phase 1 replayed

All fifteen registry entries assessed. Eleven are hypotheses; E7 (diagnosis),
E8 and E10 (platform changes) and E12 (a decision) are not, and are excluded
from the KPI denominator — counting a platform change as a rejected idea would
flatter the number.

Every hypothesis was **registered and preregistered through the real `Manager`**,
which decided each verdict, the stage ordering and the decision. Every figure is
cited to its artifact in `backtest_output/phase1_replay.json`.

| Exp | Hypothesis | Stage | Evidence | Decision |
|---|---|---|---|---|
| **E1** | a four-signal engine times exposure better than passive | **1** | Stage 2 checks **never computed** — went straight to portfolio Sharpe | REJECT |
| **E2** | Alpha Core adds value conditional on the exposure path | **2** | IC 0.01456 < 0.02 at h=20 · `alpha_decay_analysis.json` | REJECT |
| **E3** | Alpha Core beats random under production execution | **2** | IC 0.01456 < 0.02 · ICIR 0.1125 < 0.30 | REJECT |
| **E4** | the exposure engine adds value on unseen data | **4** | exposure→forward ρ = −0.126, t = −0.59, p = 0.559 · `holdout_report.md` | REJECT |
| **E5** | Alpha Core's factors generalise to an untouched window | **1** | the proposed holdout was already the fitted window — 17,695 obs, 23 dates | REJECT |
| **E6** | the Alpha Core score orders future returns | **2** | ICIR 0.1125 < 0.30 at the traded horizon | REJECT |
| **E9** | Alpha Core creates value before allocator and order book | **2** | IC 0.01456 < 0.02 — a 100th-percentile frictionless result | REJECT |
| **E11** | Alpha Core retains value through the real pipeline | **2** | IC 0.01456 < 0.02 · 30 backtests to establish what Stage 2 says in an hour | REJECT |
| **E13** | the signal decays faster than it can be harvested | **2** | retention 0.139 of peak IC < 0.50 | REJECT |
| **E14** | the h=1 signal detects queues, not alpha | **3** | 0.5496% excess vs a 1.04% round trip · queue ratio **3.03×** > 1.50× | REJECT |
| **E15** | an execution-aware filter makes the edge positive | **2** | filtered IC at h=20 = **−0.03245** — below threshold and wrong sign | REJECT |
| E7 | — | n/a | not a hypothesis: diagnosis | n/a |
| E8 | — | n/a | not a hypothesis: platform change | n/a |
| E10 | — | n/a | not a hypothesis: platform change | n/a |
| E12 | — | n/a | not a hypothesis: decision | n/a |

Registration: **11 of 11 registered and preregistered**, each with a hash-frozen
preregistration and a ledger entry.

### Two readings worth stating

**E14 passes Stage 2 and dies at Stage 3.** At its declared horizon of h=1 the
signal is genuinely real — IC 0.10492, ICIR 0.805, t = 8.78 against a
permutation null. It fails because it is *untradeable*. Real and unexecutable
are different verdicts and the platform reports them differently.

**E4 is the only rejection that genuinely required expensive validation.** In
sample the exposure engine scored; only unseen data falsified it. Stage 4 is the
correct and cheapest stage that could have caught it — not a gap in the gates.

---

## E. Governance KPI — two readings, never mixed

### Reading 1 — strict historical reality

*Only information that existed at the time of the original experiment.*

| Stage | Rejections |
|---|---:|
| 1 — DATA INTEGRITY | **11** |
| 2–6 | 0 |

```
Early rejection rate = 11 / 11 = 100.0%
```

**This 100% is degenerate and must not be quoted as evidence that the gates
discriminate.** No universe snapshot was ever collected during Phase 1, so every
hypothesis fails Stage 1 on `survivorship` before any other gate is reached. It
says one thing only: the platform would have halted the entire programme on day
one and demanded a snapshot — an afternoon's work that no later effort recovers.

### Reading 2 — counterfactual governed reality

*Today's validated snapshot infrastructure granted, so the remaining gates can
actually be reached.*

| Stage | Rejections |
|---|---:|
| 1 — DATA INTEGRITY | **1** |
| 2 — ECONOMIC SIGNAL VALIDATION | **8** |
| 3 — EXECUTION REALISM | **1** |
| 4 — ROBUSTNESS | **1** |
| 5 — STATISTICAL VALIDATION | 0 |
| 6 — PAPER TRADING READINESS | 0 |

```
Early rejection rate = (1 + 8 + 1) / 11 = 10 / 11 = 90.9%
Target               = 70%
Result               = MEETS TARGET, +20.9 points
```

**Phase 1 actual baseline: ~0%** — every real rejection landed at the Stage 4–5
equivalent, after days or weeks of compute.

These answer different questions and are never averaged or combined:

> **Reading 1** — *"what would the platform have done in 2025?"* Stopped
> everything at Stage 1.
> **Reading 2** — *"do the gates discriminate between ideas?"* Yes — 1 / 8 / 1 / 1.

### The finding underneath Reading 2

Eight of eleven hypotheses die at Stage 2 on **the same measurement**: Alpha
Core's IC at the horizon it was traded at, 0.01456 against a required 0.02, with
13.9% of peak IC retained. That measurement is E13 — run **thirteenth**. Every
number it needed was computable from data that existed before experiment 1.

One experiment, **E1**, failed not because a number was bad but because **the
check was never computed at all**.

---

## F. Universe snapshot system

| Item | Value |
|---|---|
| snapshot ID | **`u20260819-668658bb`** |
| taken | 2026-08-19T00:04:56 |
| constituents | **1,394 instruments** |
| content hash | `668658bb3e3cc48f…035e4a96` |

`python examples/verify_universe.py` — **7 of 7 pass.**

| # | Property | Result |
|---|---|---|
| 1 | restores to a real instrument list | ✅ 1,394 rows, 9 columns |
| 2 | payload hashes to its recorded value | ✅ recomputed identically |
| 3 | **constituents immutable** | ✅ removing one instrument → **REFUSED** |
| 4 | research can reference it | ✅ `survivorship` True with it; **False** without; **False** for `"UNAVAILABLE"` |
| 5 | scheduler verifies restorability | ✅ corrupt copy → `due=True, restorable=False` |
| 6 | **duplicate prevention** | ✅ unchanged universe → same id, `created=False`, 1 row |
| 7 | **removing snapshot data blocks promotion** | ✅ **REFUSED** at `decide(PROMOTE)` |

### The three required failure tests

```
1. modify one constituent
   → REFUSED: snapshot 'u20260819-668658bb' does not hash to its recorded
     value; the stored universe has been altered

2. modify the payload hash
   → REFUSED: same check, from the other side; restore() recomputes and compares

3. remove snapshot data
   → REFUSED: cannot PROMOTE E-PROMOTION-EVIDENCE: its universe snapshot
     'u20260819-668658bb' no longer restores. The experiment cited evidence
     that is now gone or altered.
```

**Missing evidence is never PASS.** This was a live defect, not a hypothetical:
the record already held `UNIV-20260818`, written by an earlier partial
implementation, which does not restore. `due()` originally read only the
timestamp, so it reported the universe as fresh and frozen when it was neither,
and the weekly schedule would never have fired again.

---

## G. The research ledger

**New in this phase.** `sirat/experiments/ledger.py`.

| | |
|---|---|
| entries | **11** |
| chain | **intact** |
| head | `354ec88217f5e7a45c07276bffd073c2f0aa5556d9b49d4c4769464022593c8b` |
| exports | `RESEARCH_LEDGER.md`, `backtest_output/research_ledger.json` |

Twelve required fields per entry — `experiment_id`, `date`, `hypothesis`,
`author`, `code_version`, `git_commit`, `config_hash`, `dataset_fingerprint`,
`universe_snapshot`, `preregistration_hash`, `validation_evidence`,
`final_verdict`. An entry missing any of them raises rather than being written.
`validation_evidence` is the **full stage record** — every check, its verdict and
the numbers behind it — because a ledger that records "rejected at stage 2"
without saying why is one nobody can audit.

### Why a hash chain and not just triggers

SQLite triggers stop a row being edited or deleted. They cannot stop a table
being dropped and rebuilt, or a database restored from a backup that is one
experiment short — **no `UPDATE` ever runs, so no trigger fires**. Each entry
hashes the one before it, so editing changes a hash, deleting breaks a link, and
reordering breaks both. `verify()` names the first entry where the history stops
adding up, and **CI fails the build on a broken chain**.

Three attacks are tested directly, the third being the one triggers cannot
defend: `test_a_rebuilt_ledger_with_a_missing_entry_is_detected`.

`Manager.decide()` appends automatically. Leaving it to the researcher would
reproduce exactly the Phase 1 failure — a markdown table somebody remembered to
update.

---

## H. Promotion control

**A result cannot reach capital deployment unless all five hold**, and three are
re-verified **live at promotion time** rather than read back from the record:

| # | Precondition | Exception | Checked |
|---|---|---|---|
| 1 | every stage passed | `ValidationMissing` | record |
| 2 | the code that ran can be recovered | `NotReproducible` | record, permanently |
| 3 | the dataset is identified | `EvidenceMissing` | **live** |
| 4 | the universe snapshot still restores | `EvidenceMissing` | **live** |
| 5 | the regression suite is green | `RegressionFailing` | **live** |

**Why 3–5 are live.** An experiment can be registered against a perfectly good
snapshot and promoted a month later, by which time the snapshot may have been
deleted or rewritten. A citation to something unrecoverable is not evidence.
This was a genuine gap found and closed during this phase — the promotion
control previously checked reproducibility and never re-verified the evidence it
cited.

**Why 2 is permanent.** An experiment registered from an unrecoverable tree
stays unpromotable however clean the repository becomes afterwards; the code
that produced the result is already gone. The refusal message says so and names
the only real remedy — a new experiment citing this one.

### Demonstrated

```
git commit      UNAVAILABLE
reproducible    False
all six stages recorded as PASS

Attempting PROMOTE ...
REFUSED: cannot PROMOTE E18-unreproducible: no commit was recorded when it was
registered, so there is no code version to reproduce from. This cannot be
repaired after the fact -- register a new experiment citing this one, from a
recoverable tree.

decision        ARCHIVE
```

**Research can continue. Capital promotion cannot happen without evidence.**

The positive case is also tested — `test_promote_succeeds_when_every_precondition_holds` —
because a control that refuses everything is indistinguishable from a broken one.

### All six governance controls

| # | Control | Evidence |
|---|---|---|
| 1 | run without registration | `REFUSED: no experiment 'E16…' in the record.` |
| 2 | missing expected-failure conditions | `REFUSED: cannot register without: expected_failure.` |
| 3 | change thresholds after results exist | `REFUSED at the database: preregistration is immutable` |
| 4 | skip validation stages | `REFUSED: stage 4 cannot run: stage 1 has not run.` |
| 5 | promote without reproducibility | `REFUSED: no commit was recorded when it was registered` |
| 6 | run after the final decision | `REFUSED: already reached a decision.` |

Controls 3 and 4 are enforced below Python as well — SQLite triggers raise on any
`UPDATE`/`DELETE` of a frozen preregistration or a recorded stage result.

---

## Acceptance

| Phase | Result |
|---|---|
| 1 — Historical replay through Manager | ✅ 11 hypotheses registered and adjudicated |
| 2 — Governance KPI, both readings | ✅ 100% strict / **90.9%** governed, kept separate |
| 3 — Official research ledger | ✅ 11 entries, hash chain intact, CI-enforced |
| 4 — Alpha Core as system regression | ✅ 11 anchors + 7 corruptions all detected |
| 5 — Universe snapshot hardening | ✅ 7 of 7, all three failure tests refuse |
| 6 — Platform acceptance | ✅ this report |
| 7 — Next research roadmap | ✅ `RESEARCH_ROADMAP.md` |

# PLATFORM ACCEPTED

### Restrictions still in force

No ML. No new alpha factors. No fundamental research on 1402–1405 data. No
microstructure optimization. **No modification of Alpha Core** — it is
hash-pinned, and editing it fails the build regardless of the numbers.

### What is unlocked

`RESEARCH_ROADMAP.md`, in order: PIT fundamental framework (blocked, 0 of 4
criteria) → factor research engine (open for price factors) → ML ranking layer
(forbidden, 0 of 4 preconditions) → microstructure (unblocked, deliberately
last).

Each enters as a registered, preregistered hypothesis that must survive six
stages in order, or it does not enter at all.

---

## Reproducing this report

```
python git_bootstrap.py --check          reproducibility
python ci.py                             934 tests + fixture + ledger
python examples/replay_phase1.py         both KPI readings
python examples/prove_fixture_detects.py the seven corruptions
python examples/verify_universe.py       7 universe properties
python examples/lifecycle_demo.py --both the six governance controls
```
