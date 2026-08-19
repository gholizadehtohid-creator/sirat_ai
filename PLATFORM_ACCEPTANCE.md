# SIRAT-AI — PLATFORM ACCEPTANCE REPORT

**Phase 2 stabilization. 2026-08-19.**

Seven phases executed in order. **All pass.** The platform is a reproducible
quantitative research system.

---

## A. Reproducibility

| Item | Value |
|---|---|
| git | **installed** — 2.55.0.windows.4 |
| repository | `c:\SIRAT-AI\.git`, initialised 2026-08-19 |
| HEAD | `a54df9bfd9a89635928c0786cde2188d06933b00` |
| archive tag | **`v2.0-archive`** → `b145fc4a26f3de61747e3322679639d756f6df28` |
| working tree | **clean** — 0 modified paths |
| tracked files | 237 |
| `sirat_market.db` (424 MB) | ignored — confirmed by `git check-ignore` |
| **`git_bootstrap.py --check`** | **`reproducible = True`** |

Every experiment registered from this tree now carries a real commit. The
provenance block is:

```
git_commit · git_dirty · code_hash · config_hash
dataset_snapshot · universe_snapshot · executed_at · python
```

`code_hash` is taken over the source **as it sat on disk at execution**, not
over the commit, because fifteen Phase 1 experiments were run from dirty trees
and a result produced from uncommitted edits must not be able to masquerade as
the commit underneath it.

> **Note on the tool environment, not the platform.** The shells this session
> spawns inherit a PATH snapshot taken *before* git was installed, so they do
> not see `git` unless it is prepended explicitly. Git is on the machine PATH
> (`C:\Program Files\Git\cmd`); any newly-opened terminal resolves it normally.
> Every figure in this report was measured with git visible.

---

## B. CI

```
python ci.py     →     BUILD GREEN     (exit 0)
```

| | |
|---|---|
| tests run | **910** |
| passed | **910** |
| failed | **0** |
| errors | **0** |
| duration | 46s |

Composition: 784 pre-existing tests (unchanged, all still passing) + 126 new —
42 Manager, 47 gates, 20 fixture, 17 universe.

Three build stages, each reported separately: `TESTS`, `FIXTURE`, `GOVERNANCE`.
Governance is a **warning channel and never fails the build** — a build that
went red because a KPI slipped would get the KPI deleted; one that reports it
every time gets it looked at. With git installed, it now emits no warnings.

---

## C. Alpha Core regression fixture

Tier 1 (frozen panel, 66,623 rows over 120 decision dates) — every anchor
recomputed through the live analytics code, not read back from a record.

| Anchor | Expected | Tolerance | **Measured** | Δ |
|---|---|---|---|---|
| `alpha_core_frozen` | `90024d86d7a5c268` | exact | `90024d86d7a5c268` | — |
| **queue ratio** | 3.03× | ±0.15 | **3.02828** | −0.0017 |
| **cost direction** | 0 < excess < 1.04% | sign | **0.54957%** ✅ | — |
| `h1_excess` | 0.5496% | ±0.05 | **0.54957** | −0.00003 |
| **requested h=20** | +3.17% | ±0.20pp | **3.17348** | +0.0035 |
| **realised h=20** | +0.59% | ±0.20pp | **0.59298** | +0.0030 |
| **percentile** | ≤ 10th | — | **3.33rd** | — |
| IC @ h=1 | 0.10492 | ±0.002 | **0.10492** | 0.00000 |
| IC @ h=5 | 0.05101 | ±0.002 | **0.05101** | 0.00000 |
| IC @ h=10 | 0.03003 | ±0.002 | **0.03003** | 0.00000 |
| IC @ h=20 | 0.01456 | ±0.002 | **0.01456** | 0.00000 |

**ALL ANCHORS REPRODUCED.** The IC curve reproduces exactly to five decimals.

Tier 2 also verified: the panel was rebuilt from `sirat_market.db` (1,600,727
bars) and `factor_panel.csv`, and hashes identically —
`618d194e5bc2d4af`.

**The tolerances are two-sided.** A drift in the favourable direction fails
identically, and the report names it: *"MOVED FAVOURABLY — find what stopped
detecting the failure."* `tests/test_alpha_core_fixture.py::Adversarial`
injects a disabled queue detector, a refilling fill model, a leaked signal and
an edited strategy file, and asserts each turns the build red.

---

## D. Governance — Phase 1 replayed

All fifteen registry entries assessed. **Eleven are hypotheses**; E7 (diagnosis),
E8 and E10 (platform changes) and E12 (a decision) are not, and are excluded
from the KPI denominator — counting a platform change as a rejected idea would
flatter the number.

Every hypothesis was **registered and preregistered through the real `Manager`**,
and the Manager — not the replay script — decided each verdict, the stage
ordering, and the decision.

### Finding zero — the strict answer

**11 of 11 hypotheses stop at Stage 1 on `survivorship`.** No universe snapshot
was ever collected during Phase 1, so no experiment in the programme is
re-runnable against the universe as it stood. The platform would have halted
the programme on day one and demanded a snapshot — an afternoon's work that
cannot be recovered afterwards.

The replay below **grants today's snapshot retrospectively**, applied in exactly
one place and labelled as such, in order to answer the more useful question:
which stage catches each *idea*.

### The table

| Exp | Stage | Failed checks | Reason | Decision |
|---|---|---|---|---|
| E1 | **1** | `ic`, `rank_correlation`, `random_baseline`, `factor_decay` | **never measured** — went straight to portfolio Sharpe | REJECT |
| E2 | **2** | `ic`, `rank_correlation`, `factor_decay` | IC 0.01456 < 0.02 at the traded horizon | REJECT |
| E3 | **2** | `ic`, `rank_correlation`, `factor_decay` | IC 0.01456 < 0.02 | REJECT |
| E4 | **4** | `walk_forward`, `out_of_sample` | exposure→forward ρ = −0.126, t = −0.59, p = 0.559 | REJECT |
| E5 | **1** | `point_in_time` | the proposed holdout was already the fitted window — 17,695 obs over 23 dates | REJECT |
| E6 | **2** | `ic`, `rank_correlation`, `factor_decay` | ICIR 0.1125 < 0.30 | REJECT |
| E9 | **2** | `ic`, `rank_correlation`, `factor_decay` | IC 0.01456 < 0.02 | REJECT |
| E11 | **2** | `ic`, `rank_correlation`, `factor_decay` | IC 0.01456 < 0.02 | REJECT |
| E13 | **2** | `ic`, `rank_correlation`, `factor_decay` | retention 0.139 of peak IC < 0.50 | REJECT |
| E14 | **3** | `transaction_costs`, `liquidity`, `slippage`, `queue_effects` | 0.5496% excess vs a 1.04% round trip; queue ratio 3.03× > 1.50× | REJECT |
| E15 | **2** | `ic`, `rank_correlation`, `random_baseline`, `factor_decay` | filtered IC at h=20 is −0.03245 — below threshold **and the wrong sign** | REJECT |
| E7 | n/a | — | not a hypothesis: diagnosis | n/a |
| E8 | n/a | — | not a hypothesis: platform change | n/a |
| E10 | n/a | — | not a hypothesis: platform change | n/a |
| E12 | n/a | — | not a hypothesis: decision | n/a |

Registration status: **11 of 11 registered and preregistered**, each with a
hash-frozen preregistration. Every number above is cited in
`backtest_output/phase1_replay.json` to the artifact it came from.

### Two readings worth stating plainly

**E14 passes Stage 2 and dies at Stage 3.** At its declared horizon of h=1 the
signal is genuinely real — IC 0.10492, ICIR 0.805, t = 8.78 against a
permutation null. It fails because it is *untradeable*: a 0.55% edge against a
1.04% round trip, and a top decile 3.03× more likely to be limit-locked at the
moment it would be bought. That is the platform working exactly as designed —
real, and unexecutable are different verdicts.

**E4 is the only rejection that genuinely required expensive validation.** In
sample the exposure engine scored; only unseen data falsified it. Stage 4 is the
correct and cheapest stage that could have caught it. It is not a gap.

---

## E. Governance KPI

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
Result               = MEETS TARGET
```

**Phase 1 baseline for comparison: ~0%.** Every actual Phase 1 rejection landed
at the Stage 4–5 equivalent, after days or weeks of compute.

No missing gates were identified — the target is exceeded by 20.9 points, and
the single Stage 4 rejection is correctly placed rather than a gap.

### The finding underneath the number

Eight of eleven hypotheses die at Stage 2, and they die on **the same
measurement**: Alpha Core's IC at the horizon it was traded at, 0.01456 against
a required 0.02, with 13.9% of peak IC retained. That measurement is E13 — run
**thirteenth**. Every number it needed was computable from data that existed
before experiment 1.

One experiment, E1, failed not because a number was bad but because **the check
was never computed at all**. It went straight to portfolio Sharpe without anyone
establishing that its signal predicted anything.

---

## F. Universe snapshot system

| Item | Value |
|---|---|
| snapshot ID | **`u20260819-668658bb`** |
| taken | 2026-08-19T00:04:56 |
| constituents | **1,394 instruments** |
| content hash | `668658bb3e3cc48f…035e4a96` |

| Property | Result |
|---|---|
| 1. restores to a real instrument list | ✅ 1,394 rows, 9 columns |
| 2. payload hashes to its recorded value | ✅ recomputed identically |
| 3. constituents immutable | ✅ removing one instrument → **REFUSED** |
| 4. research can cite it | ✅ Stage 1 `survivorship` True with it, **False** without and **False** for `"UNAVAILABLE"` |
| 5. scheduler verifies restorability | ✅ corrupt copy → `due=True, restorable=False` |

**Failure test, as required:** one constituent was removed from the stored
payload on a copy of the database. `restore()` refused:

```
snapshot 'u20260819-668658bb' does not hash to its recorded value;
the stored universe has been altered
```

**Missing or corrupted evidence never becomes PASS.** This was a live defect,
not a hypothetical: the record already held `UNIV-20260818`, written by an
earlier partial implementation, which does not restore. `due()` originally read
only the timestamp, so it reported the universe as fresh and frozen when it was
neither — and the weekly schedule would never have fired again. `due()` now
verifies restorability and treats an unverifiable snapshot as absent.

---

## G. Promotion control

**Demonstrated, not asserted.** `examples/lifecycle_demo.py --promotion-control`
registers an experiment whose provenance is captured from a directory that
genuinely has no repository — the same measurement, in a place where the answer
is honestly absent, which is the state every Phase 1 experiment was recorded in.

All six stages are then recorded as PASS. The result:

```
git commit      UNAVAILABLE
reproducible    False
all six stages recorded as PASS

Attempting PROMOTE ...
REFUSED: cannot PROMOTE E18-unreproducible: no commit was recorded when it
was registered, so there is no code version to reproduce from. This cannot be
repaired after the fact -- register a new experiment citing this one, from a
recoverable tree.

decision        ARCHIVE
```

**Research can continue. Capital promotion cannot happen without evidence.**

The refusal is driven by the provenance stored **at registration**, and that is
permanent: an experiment registered from an unrecoverable tree stays unpromotable
however clean the repository becomes later. The message says so, and names the
only real remedy — a new experiment.

### All six governance controls, evidenced

| # | Control | Evidence |
|---|---|---|
| 1 | run without registration | `REFUSED: no experiment 'E16…' in the record. Register it before running anything.` |
| 2 | missing expected-failure conditions | `REFUSED: cannot register without: expected_failure.` |
| 3 | change thresholds after results exist | `REFUSED at the database: preregistration is immutable: register a new experiment citing this one` |
| 4 | skip validation stages | `REFUSED: stage 4 (ROBUSTNESS) cannot run: stage 1 (DATA INTEGRITY) has not run.` |
| 5 | promotion without reproducibility | `REFUSED: cannot PROMOTE … no commit was recorded when it was registered` |
| 6 | run after the final decision | `REFUSED: experiment 'E16…' already reached a decision. Register a new experiment citing it.` |

Controls 3 and 4 are also enforced below Python — SQLite triggers raise on any
`UPDATE`/`DELETE` of a frozen preregistration or a recorded stage result, so the
guarantee holds wherever the write comes from.

---

## Acceptance

| Phase | Result |
|---|---|
| 1 — Activate real reproducibility | ✅ `reproducible = True` |
| 2 — Verify CI | ✅ 910/910, BUILD GREEN |
| 3 — Verify governance | ✅ all six controls demonstrated |
| 4 — Replay E1–E15 | ✅ 11 hypotheses registered and adjudicated |
| 5 — Governance KPI | ✅ **90.9%** vs a 70% target |
| 6 — Universe snapshot | ✅ 5 of 5 properties, corruption refused |
| 7 — This report | ✅ |

**PLATFORM ACCEPTED.**

### Restrictions still in force

Unchanged by this acceptance. No ML, no new alpha factors, no fundamental
research, no use of 1402–1405 fundamental data, no microstructure optimization,
and **no optimization of Alpha Core** — it is hash-pinned, and editing it fails
the build regardless of the numbers.

### The roadmap this unlocks

1. PIT fundamental research — **gated** on the four §9 readiness criteria
   (≥24 consecutive quarters, ≥150 companies per date, median ≥16 filings per
   company, publication-lag integrity verified by hand). Currently 0 of 4.
2. ML ranking experiments — gated on 4 preconditions, currently 0 of 4.
3. Microstructure and execution optimization.
4. Paper trading validation.

Each now enters as a registered, preregistered hypothesis that must survive six
stages in order, or it does not enter at all.
