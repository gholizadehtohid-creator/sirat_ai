# SIRAT-AI — POST-MORTEM AND NEXT-PHASE DESIGN

**2026-08-18 · v2.1 · diagnosis only, no model changed**

---

## 1. Complete Experiment Post-Mortem

Every experiment in the registry, including the ones that failed and the ones
never run.

| # | Experiment | Hypothesis | Result | Validity | Final decision |
|---|---|---|---|---|---|
| E1 | **Market Exposure Baseline** | A four-signal engine times exposure better than fixed passive | PASS *as scored* — Sharpe 1.49 vs 1.36, MaxDD −25.77% vs −40.56% | ⚠️ **Compromised.** Conditions 1–2 scored against buy-and-hold, not the exposure-matched control (`fixed_matched`: Sharpe 1.43, MaxDD −16.35%, Calmar 2.45) | **Overturned** |
| E2 | **Conditional Alpha Core** (close-fill) | Selection adds value conditional on exposure | ~100th percentile, ~62% CAGR | ❌ **Invalid** — close-fill execution artifact | **Rejected** |
| E3 | **Execution Realism** | Alpha Core beats random under production execution | FAIL — 26.7th percentile, p = 0.7419 | ⚠️ Relative result valid; Sharpe *levels* void (idle cash 0% vs a 23% hurdle); exposure 29.22% vs 54.79% target | **Rejected, levels void** |
| E4 | **Exposure Engine Holdout** | Engine delivers incremental value on unseen data | **FAIL — 1 of 8 gates.** Engine 42.24% vs control 43.31%; Sharpe 1.39 vs 1.77; MaxDD −14.87% vs −10.75% | ✅ Clean — G8 research validity passed | **RETIRED** |
| E5 | **Experiment 4 Preflight** | *(never tested)* | **INVALID — not run.** Four preconditions failed: no untouched holdout, git absent, survivorship, exposure inequality | ✅ Correctly stopped; no holdout burned | **Stopped before running** |
| E6 | **Rank-IC Diagnostic** | The Alpha Core score orders future returns | PASS h=5,10 · **FAIL h=20 (traded), h=60**. Signal real (z = +8.3 vs permutation null); blend beaten by %B alone at every horizon | ✅ Stands; in-sample for factor selection | **Signal real, gate failed** |
| E7 | **Alpha Preservation Audit** | *(diagnosis)* | Exposure loss is **100% slot occupancy**; attribution machinery existed and was discarded | ✅ Stands | **Diagnosis accepted** |
| E8 | **P3 Reporting Repair** | *(engineering)* | Diagnostic nulls 4 → 0; fields 34 → 38 | ✅ Zero risk to results | **Merged, v2.1** |
| E9 | **5A Frictionless Isolation** | Alpha Core creates value *before* allocator and order book | **PASS LOCALIZATION.** Top-10 80.41% vs 24.32% random, 100th pct, p = 5×10⁻⁵ | ⚠️ In-sample; survivors-only; **needs 11.26 turnovers/yr** | **Localization only** |
| E10 | **P0 Idle-Cash Accrual** | *(engineering)* | Cash credited at policy rate; default off so v2.0 stays reproducible | ✅ 784 tests pass | **Merged, v2.1** |
| E11 | **5B Production Pipeline** | Alpha Core retains value through the real pipeline | **FAIL — 3.3rd percentile, p = 0.9667.** CAGR 17.84% vs 23.90% random | ⚠️ V1 exposure gate failed at 6.57pp — **favouring the treatment**, which lost anyway | **REJECTED** |
| E12 | **Phase 3 Decision Gate** | — | `recoverable_fraction = −10.8%` against a +20% floor | ✅ Preregistered before Arm B ran | **STOP AND REDESIGN** |
| E13 | **Alpha Decay Diagnosis** *(this phase)* | The signal decays faster than the system can harvest it | **CONFIRMED.** IC 0.105 @1 session → 0.015 @20 | ✅ `fwd_1` validated against the panel (98.31% within 0.05pp) | **Root cause identified** |

### Experiments never run, and why

| Not run | Reason |
|---|---|
| Fundamental-factor experiments | Frozen after E6; no surviving consumer. `f_fundamental` exists in the panel but was never independently validated |
| Alpha Core forward holdout | Deliberately not started — the scorecard recommends against spending a clean window on a 3.3rd-percentile candidate |
| ML experiments | Blocked by rule 2; 0 of 3 preconditions met throughout |

---

## 2. Root Cause Analysis

Each failure classified. **The categories are not equally represented, and that
is the finding.**

### A. Research failure

| Failure | Classification |
|---|---|
| Exposure engine was pro-cyclical, not defensive | **A — wrong hypothesis.** Beta 0.654 at 55% average exposure: the engine raised risk in calm trending markets. It was never a defensive overlay |
| E1's control was buy-and-hold, not exposure-matched | **A — wrong null.** The correct control was in the same results file and was not used for the headline conditions |
| 50/50 blend dilutes a working signal with a non-working one | **A — poor factor design.** Momentum at h=20: t = 1.15, 50.4% positive. The legs are 0.567 correlated, so the blend cannot diversify |
| Prediction target mismatched to the trading horizon | **A — wrong target.** The score predicts 1–5 session returns; the system trades 20 and holds 54 |

### B. Implementation failure

| Failure | Classification | Status |
|---|---|---|
| Idle cash uncredited while charged a 23% hurdle | **B — accounting error** | **FIXED** (v2.1) |
| Four execution counters reported `null` (key-name mismatch) | **B — reporting defect** | **FIXED** (v2.1) |
| `cash_analysis` / `entry_rejections` computed and discarded | **B — reporting defect** | **FIXED** (v2.1) |
| Funnel counters unsummable across two layers | **B — measurement defect** | **SEPARATED and documented** |
| Realized exposure 29.22% vs 54.79% target | **B — allocator/ceiling interaction** | **FIXED** — rose to 48.94% |

**Every identified implementation failure was fixed, and the result got worse**
(26.7th → 3.3rd percentile). This is the cleanest possible evidence that
category B was not the binding constraint.

### C. Economic failure

| Failure | Classification |
|---|---|
| Signal exists but cannot be harvested | **C — the binding constraint.** See §3 |
| Required rotation 11.26/yr; achievable 4.45/yr | **C — capacity limit.** 476 participation caps, 28.1% partial fills |
| Costs exceed edge at the horizon where the signal is strongest | **C — friction.** At h=1 the alpha is 0.55% against a 1.04% round trip: **−69%/yr** |
| Realized holding 53.9 sessions = 4.7 signal half-lives | **C — decay** |

### Verdict on causation

> **The programme failed for economic reasons (C), not research (A) or
> implementation (B) reasons — though A contributed and B was real, measured,
> and fixed.**

The A-failures made the system worse than it needed to be. The B-failures made
the diagnosis harder. **Neither caused the rejection.** The rejection is caused
by a signal whose information is concentrated in the first one to five sessions
of a market that charges 1.04% to turn a position over and physically prevents
the rotation rate required to harvest it.

---

## 3. Alpha Decay Analysis

`python research_alpha_decay.py` · window 2014-09-21 → 2024-09-01, 120 dates
· **contaminated post-2024 block excluded**

The one-session forward return was rebuilt from `price_history` and **validated
before use**: the same construction reproduces the panel's own `fwd_5` to a
median absolute difference of **0.00021pp**, with 98.31% of rows inside a
0.05pp tolerance fixed before the check ran.

### The decay curve

| Horizon | Dates | Mean IC | ICIR | t | % positive | Top−market | Top−bottom | Hit % |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **1** | 119 | **0.10492** | **0.805** | **8.78** | **81.5%** | 0.5496% | 0.7877% | 55.0% |
| **5** | 120 | 0.05101 | 0.382 | 4.18 | 64.2% | 1.3850% | 1.8267% | 56.0% |
| **10** | 120 | 0.03003 | 0.233 | 2.56 | 56.7% | 1.7222% | 2.3653% | 54.3% |
| **20** ← traded | 120 | **0.01456** | **0.113** | **1.23** | **51.7%** | 1.9573% | 2.8914% | 51.0% |
| **60** | 120 | 0.01755 | 0.148 | 1.62 | 55.8% | 3.1388% | 3.8365% | 51.6% |

**Does the signal decay? Yes — by a factor of seven between session 1 and
session 20.**

Two things worth stating plainly:

1. **At one session the signal is strong and unambiguous.** ICIR 0.805 clears
   the 0.30 gate nearly threefold; t = 8.78; 81.5% of dates positive.
2. **At the traded horizon it is a coin flip.** ICIR 0.113, t = 1.23, 51.7%
   positive — **statistically indistinguishable from zero on clean data.**

> **Note on the earlier E6 figures.** E6 reported IC 0.0292 / ICIR 0.219 at
> h=20 over 143 dates including the contaminated 2024H2–2026 block. Restricted
> to clean pre-2024-09 data the same measurement gives **0.0146 / 0.113** —
> exactly the leave-one-out value E6 predicted. The contaminated window was
> inflating the traded-horizon headline by roughly **2×**.

### Where the information actually is

| Sessions | Alpha gained | Per session |
|---|---:|---:|
| **1** | +0.5496% | **0.54960%** |
| 2–5 | +0.8354% | 0.20885% |
| 6–10 | +0.3372% | 0.06744% |
| 11–20 | +0.2351% | **0.02351%** |
| 21–60 | +1.1815% | 0.02954% |

**The accrual rate falls 23× from session 1 to sessions 11–20.**

### Half-life

A single exponential fitted across all five buckets gives **τ = 16.39 sessions,
half-life 11.36 sessions**. That figure is honest but flattering: the curve is
not one exponential. It has a **fast component** — session 1 to sessions 6–10 is
an 8× drop, implying a half-life near **3 sessions** — sitting on a **slow floor**
of ~0.03%/session that persists to day 60 and drags the fitted τ upward.

**The decision-relevant number is the fast component: the tradable information
is essentially gone after five sessions.**

---

## 4. Execution Feasibility — Half-Life vs Trading Cost

Round trip 1.04%. Rebalancing every *h* sessions pays it 240/h times a year.
Alpha is the top-decile excess over the market — the long-only harvestable part.

| h | Top−market | Cost | Net / period | Periods / yr | **Net / yr** | Clears cost? |
|---:|---:|---:|---:|---:|---:|:--:|
| **1** | 0.5496% | 1.0400% | **−0.4904%** | 240.0 | **−69.27%** | ❌ |
| **5** | 1.3850% | 1.0400% | +0.3450% | 48.0 | **+17.98%** | ✅ |
| **10** | 1.7222% | 1.0400% | +0.6822% | 24.0 | **+17.72%** | ✅ |
| **20** | 1.9573% | 1.0400% | +0.9173% | 12.0 | +11.58% | ✅ |
| **60** | 3.1388% | 1.0400% | +2.0988% | 4.0 | +8.66% | ✅ |

### The central economic fact

> **The signal is strongest exactly where the cost is most punishing.**

At one session the information is **7× stronger** than at twenty (IC 0.105 vs
0.015) — and trading it loses **69% a year** to commission alone. The
theoretical optimum sits at **h = 5–10**, worth roughly **+18%/yr** on paper.

### Why the optimum is unreachable

| Horizon | Turnover required | Production achieved | Shortfall |
|---:|---:|---:|---:|
| 5 | ~48 /yr | **4.45 /yr** | **11×** |
| 10 | ~24 /yr | 4.45 /yr | **5×** |
| 20 | ~12 /yr | 4.45 /yr | 2.7× |

**The economically viable horizon requires 5–11× more rotation than the system
has ever demonstrated** — against a market with a ±3% daily band, صف خرید/فروش,
and a participation cap that intervened 476 times with 28.1% partial fills.

### What the production system actually held

| | |
|---|---|
| Turnover (E11) | 4.45 /year |
| **Implied average holding** | **53.9 sessions** |
| Rebalance interval | 20 sessions |
| Held longer than intended by | **2.7×** |
| **Half-lives past decay** | **4.7** |

At 21–60 sessions the accrual rate is 0.0295%/session — indistinguishable from
holding the market. **The production book held its names four to five
half-lives past the point where the signal had anything left to say.** That is
the 3.3rd percentile, explained.

### Minimum half-life required

For a signal to clear friction it must deliver more than the round trip within
its usable life. With a 1.04% round trip and a top-decile excess accruing at
rate *r*:

```
required half-life ≈ the horizon at which cumulative alpha > 1.04%
```

Alpha Core crosses 1.04% between sessions 1 and 5. **A signal with this decay
profile needs a half-life of at least ~5 sessions to be tradable at all, and
~10 to be comfortable.** Alpha Core's fast component is ~3. It is marginal by
construction, before a single execution constraint is applied.

---

## 5. New Research Directions

Each must explain a known failure, be preregistrable, have a clear null, and
have a reason to expect improvement.

### H1 — The signal is a queue-pressure artifact

| Field | |
|---|---|
| **Hypothesis** | The 1-session IC of 0.105 measures names about to lock limit-up (صف خرید), not tradable alpha. High %B + high momentum in a ±3% band market is a proxy for imminent queue formation |
| **Why the current system failed** | Explains *simultaneously* why the signal is strongest at h=1, why it decays so fast, why fill rates and participation caps bind, and why the frictionless arm passes while production fails. It is the single hypothesis that explains all four |
| **Expected mechanism** | The top decile at h=1 is disproportionately limit-locked on the following session. A signal you cannot buy is not alpha; it is a queue detector |
| **Data required** | `price_history` (close, high, low) + `client_type_history`. Already local. No new collection |
| **Test design** | For each decision date, measure the share of top-decile vs bottom-decile vs market names that (a) open limit-up, (b) close at the band, (c) show a volume collapse consistent with صف, on session +1. Compare against the random control |
| **Null hypothesis** | Top-decile names are limit-locked at the same rate as the universe |
| **Success criteria** | *(This experiment succeeds by killing the project.)* If the top decile is limit-locked at ≥2× the universe rate, the signal is declared unharvestable and the programme archives |
| **Main risk** | None to the programme — it is cheap and decisive either way |
| **Effort** | **1–2 days** |
| **P(learning something useful)** | **High — ~85%** |

### H2 — Capacity-constrained universe

| Field | |
|---|---|
| **Hypothesis** | Restricting the universe to the most liquid decile raises achievable turnover enough to reach the h=5–10 viable horizon |
| **Why the current system failed** | Explains the rotation failure: 476 participation caps and 28.1% partial fills are a liquidity constraint, and the universe includes names that cannot absorb a position |
| **Expected mechanism** | Higher ADV → higher participation allowance → turnover rises from 4.45 toward the 24/yr that h=10 needs |
| **Data required** | Existing `adv` column in the panel |
| **Test design** | Re-run the E9/E11 pair on the top-ADV decile only. Report achievable turnover first, **before** any return number is read |
| **Null hypothesis** | Achievable turnover in the liquid subset does not exceed 8/yr, and IC in that subset is not different from the full universe |
| **Success criteria** | Turnover ≥ 15/yr **and** h=10 ICIR ≥ 0.30 in the liquid subset |
| **Main risk** | **Large.** Liquid Iranian names are the most efficiently priced; the signal may vanish exactly where it becomes tradable. Also reduces the universe to ~75 names, so a top-10 book becomes the top 13% — far less selective |
| **Effort** | ~1 week |
| **P(learning something useful)** | Moderate — ~55% |

### H3 — Short-horizon specification

| Field | |
|---|---|
| **Hypothesis** | A specification designed for a 5-session horizon captures net alpha where the 20-session one cannot |
| **Why the current system failed** | Directly addresses the target mismatch: the score predicts 1–5 sessions, the system trades 20 |
| **Expected mechanism** | h=5 shows +0.345%/period net of commission → +17.98%/yr on paper |
| **Data required** | Existing panel plus 5-session decision dates |
| **Null hypothesis** | Net-of-all-cost return at h=5 does not exceed the random control |
| **Success criteria** | Beats random at p<0.05 **after** commission, slippage, participation caps and partial fills |
| **Main risk** | **Severe, and known in advance.** Requires ~48 turnovers/yr against a demonstrated ceiling of 4.45 — an **11× gap**. H2 must succeed first or this is untestable |
| **Effort** | ~2 weeks, and **only after H2 passes** |
| **P(learning something useful)** | Low as a standalone — ~25% |

---

## 6. Machine Learning Evaluation

### Arguments for

- Nonlinear relationships and factor interactions are unexplored.
- The panel carries 20+ signals; only two are used.
- Ranking could plausibly improve on an unweighted 50/50 blend — indeed **%B
  alone already beats it**, which proves the blend is suboptimal.

### Arguments against

- **The problem is not functional form.** At the traded horizon the signal is
  t = 1.23 on clean data. ML cannot create information that is not there.
- **Where information exists (h=1, IC 0.105), the barrier is cost, not
  modelling.** No learner changes a 1.04% round trip.
- **Sample size.** 120 clean decision dates and ~25–35 independent regime
  episodes.
- **Survivorship.** A learner trained on a survivors-only universe learns "which
  stocks were not delisted" — unavailable at decision time, catastrophic live.
- **Multiple testing.** With 20+ panel columns and free functional form, the
  in-sample hypothesis space is effectively unbounded against a spent holdout.
- **Instability.** IC was negative over 2015–2019 (60 of 143 dates).

### Decision

# REJECT ML — for this problem

Not "delay". **Delay** implies ML is the right tool arriving too early; it is
not the right tool at all for a cost-versus-horizon mismatch. Every argument
*for* concerns ranking quality, and ranking quality is not what failed — the
frictionless top-10 already ranked well enough to beat random at the 100th
percentile and still lost 6 points of CAGR through the pipeline.

ML becomes a legitimate question again **only if** a future specification is
found in which the signal is demonstrably harvestable net of friction, and the
remaining open question is functional form. That is a different project.

---

## 7. Capital Preservation Review

> If this system existed today, what is the correct operational state?

# RESEARCH ONLY

| State | Verdict | Why |
|---|---|---|
| **Real capital** | ❌ | 3.3rd percentile of random; no kill switch; no live risk limits; 0 of 6 Phase 6 criteria |
| **Paper trading** | ❌ | Paper trading validates an *executable candidate* against live behaviour. There is no candidate worth validating — paper trading a rejected specification consumes months to confirm what E11 already showed |
| **Research only** | ✅ | One cheap, decisive experiment (H1) remains, and it can settle the question in days |
| **Archive** | ⏳ | The correct destination, but **not yet** — H1 is cheap enough that archiving before running it discards information at near-zero cost of retention |

---

## 8. Final Recommendation

# SIRAT-AI RESEARCH PROGRAM DECISION

## Executive Decision

# CONTINUE WITH NEW HYPOTHESIS

**Scoped to a single falsification experiment (H1), with a pre-committed
archive trigger. This is not a renewed research programme.**

### Evidence supporting the decision

1. The signal is real and strong at one session: **IC 0.105, ICIR 0.805,
   t = 8.78, 81.5% of dates positive.** That is not noise.
2. At the traded horizon it is **statistically zero on clean data**: ICIR 0.113,
   t = 1.23, 51.7% positive.
3. The contaminated 2024H2–2026 window was inflating the traded-horizon
   headline by **~2×**. Removing it halves the measured IC.
4. Alpha accrual falls **23×** from session 1 to sessions 11–20.
5. At h=1 the alpha (0.55%) is **half the round trip** (1.04%) — trading the
   strongest signal loses **69%/yr** to commission alone.
6. The viable horizon (h=5–10, ~+18%/yr on paper) needs **24–48 turnovers/yr**
   against a demonstrated ceiling of **4.45** — a 5–11× gap.
7. The production book held names **53.9 sessions**, 2.7× its intended interval
   and **4.7 half-lives** past decay.
8. **Every implementation defect was fixed and the result got worse**
   (26.7th → 3.3rd percentile). Category B is excluded as the cause.
9. Alpha Core was **advantaged** on exposure, fill rate and cost versus its own
   control and still lost 6 points of CAGR.
10. H1 is cheap (1–2 days), uses only local data, and is decisive in either
    direction — the strongest available reason not to archive today.

### What must NOT be done

- ❌ Do not tune Alpha Core — no weights, factors, thresholds, lookbacks.
- ❌ Do not revisit the exposure engine. It is retired.
- ❌ Do not add ML without a specification in which the signal is demonstrably
  harvestable.
- ❌ Do not burn the forward holdout on Alpha Core v1 or any rejected version.
- ❌ Do not shorten the rebalance interval to chase the h=5 number — that is
  selecting a lookback after seeing results, and H2 must pass first.
- ❌ Do not loosen the entry gates to raise candidate supply.
- ❌ Do not relax the participation cap to raise turnover. The cap models a real
  constraint; relaxing it manufactures an edge that does not exist.
- ❌ Do not start paper trading a rejected specification.

### If continuing

| | |
|---|---|
| **Next research question** | Is the 1-session Alpha Core signal a *tradable* return forecast, or a *queue-formation* detector — a prediction of which names will become unbuyable? |
| **Preregistration** | Written and hashed **before** any limit-lock statistic is computed. Must fix the band-proximity definition, the volume-collapse threshold, the ≥2× decision rule and the seed |
| **Success criteria** | The experiment succeeds by resolving the question in either direction. If the top decile is **not** disproportionately limit-locked, H2 is unlocked as a second and final experiment |
| **Stopping criteria** | **Archive immediately** if the top decile is limit-locked at ≥2× the universe rate. **Archive** if H1 is inconclusive and H2's turnover pre-check fails to reach 15/yr. **Archive** if any successor reaches a preregistered gate and fails |
| **Budget cap** | H1 then, at most, H2. **No third cycle without a new and independent source of evidence** |

---

## Final Question

> **Does current evidence justify spending another research cycle on SIRAT-AI?**

# YES — one cycle, and it is designed to end the project

The qualification is the answer. A full research programme is **not** justified:
the economic barrier is quantified, large, and structural — a 5–11× rotation gap
against a market that physically prevents it.

But one specific question remains genuinely open and genuinely cheap. A
1-session IC of 0.105 at t = 8.78 is a real measurement demanding a real
explanation, and there are exactly two: a fast-decaying return forecast that
friction happens to eat, or a queue detector that was never tradable at all.
**Those two have identical backtest signatures and opposite implications for
whether any successor specification could work.**

Answering it costs one to two days with data already on disk. Archiving without
that answer would leave the programme's most striking measurement unexplained,
and would leave open the possibility of someone rediscovering the same
1-session signal in two years and repeating the whole cycle.

**Expected outcome: H1 confirms the queue-artifact explanation and the project
archives with a complete causal account.** That is the most likely result and it
is a successful one.

---

## Provenance

| Item | Value |
|---|---|
| Version | **SIRAT-AI-v2.1** |
| New this phase | `research_alpha_decay.py`, `backtest_output/alpha_decay_analysis.json` |
| Model changes | **None.** Alpha Core untouched |
| Backtests run | **None.** Cross-sectional diagnosis only |
| Holdout | **Not started, not burned** |
| `fwd_1` validation | 98.31% of 71,461 rows within 0.05pp of the panel's own `fwd_5` construction |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
