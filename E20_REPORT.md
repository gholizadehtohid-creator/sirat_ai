# SIRAT-AI — E20: TRADABLE INFORMATION / CAPACITY TEST

**Preregistered `1f9d15b0…` before computation. Decided by the Experiment
Manager. 2026-08-20.**

---

## 1. EXECUTIVE VERDICT

Alpha Core contains **real, strong, statistically overwhelming predictive
information — and essentially all of it lives in securities the strategy cannot
trade.** In the names that a 5% participation cap *excludes*, the score's
information coefficient is **+0.17066 (t = 7.14)**; in the names it *admits*, at
the declared 20-session horizon, it is **−0.01293 (t = −0.99)**. The capacity
curve is negative at **every** level tested, from a 50% participation cap down
to 0.25%. Of a frictionless +3.1735% gross alpha, **1.40pp is lost the moment
untradable names are removed and a further 1.80pp to execution**, before a
0.96pp round trip takes the remainder to **−0.99% per period**. E20 therefore
confirms and *refines* E19 rather than reversing it: E19 established that the
strategy fails; E20 establishes **why** — not an absence of information, and not
a fixable execution defect, but information located precisely where capacity is
not. Alpha Core is a **research signal, not a trading signal.**

---

## 2. INFORMATION → TRADABILITY → MONETIZATION

| Layer | Result | Evidence | PASS/FAIL |
|---|---|---|---|
| **L1 Information** | IC **0.01456**, t **1.232** at the declared h=20. Information does exist further in: IC **0.05101**, t **4.182** at h=5. | `phase3_information` | **FAIL** at h=20 |
| **L2 Tradability** | At T2 (participation ≤5%, the platform's own cap): IC **−0.01293**, ICIR −0.090, t **−0.985**. In the *excluded* names: IC **+0.17066**, t **+7.139**. | `phase4_tradability` | **FAIL** |
| **L3 Monetization** | Net **−0.992%**/period at T2 h=20; turnover **11.11/yr** against a measured capacity of **4.45/yr**; 90th percentile against the liquidity-matched null (95th required). | `phase7_execution`, `phase8_nulls` | **FAIL** |

**Manager verdict:** REJECT at **Stage 2 — ECONOMIC SIGNAL VALIDATION**
(`ic`, `rank_correlation`, `random_baseline`, `factor_decay`). Cheap rejection.
Ledger entry `fcc4684bb2c5d394…`.

---

## 3. LIQUIDITY / CAPACITY TABLE

Account equity 1,000,000,000 · basket 10 · position value 100,000,000.
Participation = position value ÷ trailing-20-session ADV, decision-time only.
**Absolute caps, not within-date quantiles** — a quantile labels the least-bad
name in a thin market "liquid", which is exactly how an untradable result looks
tradable.

| Level | Part. cap | Implied min ADV | % of panel | Names/date | IC (h=20) | t | Turnover/yr | Net/period |
|---|---|---|---|---|---|---|---|---|
| **T1 broad** | 10% | 1.0e9 | 75.7% | 487 | −0.01167 | −0.93 | 11.23 | **−1.2511%** |
| **T2 realistic** | **5%** | 2.0e9 | 69.6% | 462 | **−0.01293** | **−0.99** | 11.11 | **−0.9920%** |
| **T3 conservative** | 1% | 1.0e10 | 51.1% | 302 | −0.01286 | −0.68 | 11.19 | **−1.3565%** |

### The capacity curve (h=20) — negative at every level

| Part. cap | 50% | 25% | 10% | 5% | 2% | 1% | 0.5% | 0.25% |
|---|---|---|---|---|---|---|---|---|
| **mean IC** | −0.0095 | −0.0121 | −0.0117 | **−0.0129** | −0.0114 | −0.0129 | −0.0091 | −0.0175 |
| t | −0.78 | −0.97 | −0.93 | −0.99 | −0.76 | −0.68 | −0.49 | −0.88 |
| dates ≥50 names | 120 | 120 | 120 | 120 | 111 | 81 | 72 | 66 |

**There is no tradable region.** Loosening the constraint does not help and
tightening it does not help; the curve is flat and negative throughout. This is
the diagnostic the mission asked for, and it rules out "a genuine tradable
region" (case 4) unambiguously.

### The complement — what capacity throws away

| Universe | Rows | IC (h=20) | t |
|---|---|---|---|
| Admitted at T2 (participation ≤5%) | 46,370 | **−0.01293** | −0.99 |
| **Excluded at T2 (participation >5%)** | **20,253** | **+0.17066** | **+7.139** |

---

## 4. HORIZON TABLE

Horizons **5 / 10 / 20 / 60** — the platform's existing set from
`research_alpha_ic.py`. *The brief suggested h=40; no panel in this repository
carries `fwd_40`, and the brief's own instruction is to use existing definitions
rather than invent new ones. h=60 replaces it, and the substitution is declared
in the preregistration, not here.*

Full cross-section (no capacity constraint):

| h | mean IC | ICIR | t | %pos | 95% CI |
|---|---|---|---|---|---|
| 5 | **0.05101** | 0.382 | **4.182** | 64.2% | [0.0271, 0.0749] |
| 10 | 0.03003 | 0.233 | 2.556 | 56.7% | [0.0070, 0.0531] |
| **20** (declared) | **0.01456** | 0.113 | 1.232 | 51.7% | [−0.0086, 0.0377] |
| 60 | 0.01755 | 0.148 | 1.620 | 55.8% | [−0.0037, 0.0388] |

At T2, with execution and cost:

| h | T2 IC | t | Requested | Realised | Turnover/yr | Cost | **Net/period** |
|---|---|---|---|---|---|---|---|
| 5 | **+0.02765** | **2.127** | 2.0363 | 0.7545 | **44.44** | 0.9629 | **−0.2083%** |
| 10 | +0.00349 | 0.268 | 2.0602 | 0.5648 | 22.22 | 0.9629 | −0.3981% |
| **20** | −0.01293 | −0.985 | 1.7732 | −0.0292 | 11.11 | 0.9629 | **−0.9920%** |
| 60 | −0.00312 | −0.226 | 1.5850 | −1.5670 | 3.70 | 0.9629 | −2.5299% |

> **The answer to the mission's key question — "does shorter-horizon
> predictability disappear once capacity and execution are imposed?" — is:
> almost.** h=5 is the single cell that survives L2: IC +0.02765, t = 2.127 in
> tradable names. It is **not** promoted to the headline, because it is not the
> declared horizon and because monetising it needs **44.44 turnovers/year against
> a market measured to support 4.45** — a factor of ten. It is still net
> negative (−0.2083%/period) even before that constraint binds.

---

## 5. NULL TEST TABLE — at T2, the tradable universe

Treatment excess: **−0.02918%**/period.

| Null | Arms | Null mean | Null p95 | Percentile | Beats p95 |
|---|---|---|---|---|---|
| random ranking | 30 | −0.54278 | 0.02519 | 93.33 | **No** |
| **liquidity-matched (primary)** | 30 | −0.42119 | 0.05202 | **90.00** | **No** |
| volatility-matched | 30 | −0.47158 | 0.20353 | 86.67 | **No** |
| liquidity **and** volatility matched | 30 | −0.20417 | 0.35435 | **56.67** | **No** |
| exposure-matched market | 1 | 0.0 | — | 0.00 | **No** |

The liquidity-matched null was preregistered as **primary** precisely because
liquidity is the hypothesis. Note the progression: the more structure a null is
allowed to share with the strategy, the less the strategy beats it — **56.67th
percentile** against a null matched on both liquidity and volatility. In the
tradable universe Alpha Core does not outperform a random pick with the same
liquidity and volatility profile.

---

## 6. WALK-FORWARD TABLE — at T2

| # | Test start | Test end | Train IC | Test IC | Names/date | Turnover/yr | Net % |
|---|---|---|---|---|---|---|---|
| 1 | 2016-05-14 | 2017-02-08 | −0.05588 | **+0.00884** | 145.2 | 10.92 | +3.5186 |
| 2 | 2017-03-08 | 2017-12-12 | −0.02461 | −0.02749 | 159.0 | 11.28 | +0.1525 |
| 3 | 2018-01-07 | 2018-10-07 | −0.00932 | −0.02412 | 198.4 | 11.16 | −1.8083 |
| 4 | 2018-11-05 | 2019-08-05 | −0.02580 | −0.04409 | 388.4 | 11.76 | −2.5216 |
| 5 | 2019-09-04 | 2020-06-08 | −0.03410 | **+0.01098** | 522.3 | 11.76 | −8.8620 |
| 6 | 2020-07-07 | 2021-04-05 | −0.01656 | **+0.05459** | 523.0 | 10.56 | +3.6068 |
| 7 | 2021-05-03 | 2022-01-30 | +0.03278 | **+0.08262** | 519.0 | 10.92 | +2.9519 |
| 8 | 2022-02-28 | 2022-12-04 | +0.06860 | −0.02287 | 620.5 | 11.28 | −1.8629 |
| 9 | 2023-01-02 | 2023-10-09 | +0.02988 | −0.06466 | 660.8 | 11.64 | −3.5862 |
| 10 | 2023-11-06 | 2024-08-10 | −0.04377 | −0.01719 | 668.9 | 11.88 | +0.9923 |

**4 of 10 positive (40%)**, against a 60% floor. The information/capacity
relationship is **not** stable through time: the only strong folds are 6 and 7
(2020-07 → 2022-01), the Tehran bubble and its aftermath. Note fold 5 — positive
IC (+0.011) with a **−8.86%** net result, which is the capacity problem in a
single row: the signal was right and the book could not act on it.

---

## 7. ECONOMIC DECOMPOSITION

| Step | Excess % | Δ |
|---|---|---|
| 1. gross alpha, full universe, frictionless | **+3.1735** | — |
| 2. after restricting to tradable names (T2) | +1.7732 | **−1.4003** |
| 3. after T+1 execution and unfilled slots | −0.0292 | **−1.8024** |
| 4. after turnover × round trip | −0.9920 | −0.9628 |
| 5. after 0.10%/leg slippage | −1.1772 | −0.1852 |

| Component | Contribution |
|---|---|
| Signal information (full universe) | **+3.1735%** |
| **Liquidity/capacity restriction** | **−1.4003 pp (44% of the alpha)** |
| **Execution loss** | **−1.8024 pp (57% of the alpha)** |
| Transaction cost | −0.9629 pp |
| Slippage | −0.1852 pp |
| **Final net** | **−1.1772%** |
| Exposure effect | *not separable — the benchmark is the cross-sectional mean of the same eligible universe, so market direction is differenced out by construction* |
| Selection effect | *not separable from signal — the score **is** the selection rule, and separating them would require modifying Alpha Core, which is forbidden* |

**Two components, each larger than the surviving alpha, account for the whole
result.** Neither is a defect to be fixed: the first is what "tradable" means,
and the second is what a price-limit market does to an order.

---

## 8. FAILURE CLASSIFICATION

| Class | Present? | Basis |
|---|---|---|
| Infrastructure | **No** | 943/943 tests, 11/11 anchors, ledger chain intact, `reproducible=True` |
| Data | **No** | universe restorable and hash-verified; 120 dates × ≥50 names at every level down to a 2% cap |
| Governance | **No** | preregistered `1f9d15b0` before computation; Alpha Core `90024d86` unchanged; no threshold moved |
| Research design | **No** | horizon, splits, caps and nulls all frozen in advance; the one deviation (h=60 for h=40) is declared in the preregistration |
| Strategy | **Partly** | the strategy is not wrong about which stocks rise — it is wrong about which stocks can be bought |
| **Capacity** | **YES — primary** | information is +0.171 where participation >5% and −0.013 where it is ≤5%; no capacity level is positive |
| **Execution** | **YES — secondary** | 1.80pp of a 1.77pp post-capacity alpha destroyed by T+1 queues and unfilled slots |

**Primary classification: CAPACITY.** Secondary: EXECUTION. Not infrastructure,
not data, not governance, not research design.

---

## 9. E19 vs E20 — what is confirmed, rejected, refined

**E19 is not rewritten. Its record is immutable and its verdict stands.**

| E19 finding | E20 |
|---|---|
| No robust edge; 7 of 11 gates fail | **CONFIRMED.** E20 reaches REJECT independently, at the same stage. |
| IC 0.01456, t 1.23 at h=20 | **CONFIRMED** exactly — same panel, same declared horizon. |
| Within-date IC +0.212 illiquid vs −0.030 liquid | **REFINED and strengthened.** E19 used *relative* quintiles; E20 uses *absolute* participation caps, which is the measure that decides whether an order can be placed. On that measure the split is starker: **+0.171 excluded vs −0.013 admitted.** |
| h=5 net +0.028% but needs 48 turnovers/yr | **REFINED.** In the *tradable* universe h=5 is the only cell with information (IC +0.02765, t 2.127) — and it is **net negative** there (−0.2083%) even before the 44.44 vs 4.45 turnover constraint binds. E19's h=5 caveat was right and is now stronger. |
| Liquidity-matched null percentile 93.3 | **REFINED downward.** Restricted to the tradable universe it falls to **90.0**, and to **56.7** against a liquidity-and-volatility-matched null. |
| Execution lost 81.3% of apparent alpha | **CONFIRMED and decomposed.** 44% of the gross alpha is lost to the capacity restriction *before* execution is even simulated; execution then takes another 57%. |
| *(not asked)* Is the failure information or capacity? | **NEW.** E19 could not distinguish these. E20 does: it is capacity. |

**What E20 rejects from E19: nothing.** **What it adds: the mechanism.**

### One thing E20 structurally could not do

E19 spent the clean holdout (`2023-03-01..2024-09-01`); `windows_touched`
records it and the platform refuses a second evaluation. **E20 therefore makes
no independent out-of-sample claim** — the third window is reported as
`reused_window` throughout. This was declared in the preregistration, not
discovered afterwards, and it means E20 was **structurally capable of
establishing a negative conclusion but not a positive one.** Had E20 found a
tradable edge, it would have been an in-sample finding requiring a fresh
experiment on untouched data. It did not.

---

## 10. FINAL RECOMMENDATION

# B — INFORMATION EXISTS BUT IS NOT TRADABLE

Alpha Core contains genuine, strong predictive information — **t = 7.14** in the
securities that a realistic participation cap excludes. That information is
concentrated in names whose traded value cannot absorb a position, and it is
absent, indeed slightly negative, in every tradable subset at every capacity
level tested.

### Classification

> **ALPHA CORE — RESEARCH SIGNAL, NOT A TRADING SIGNAL.**

**Do not deploy. Do not proceed to paper trading. Do not connect capital.**

Alpha Core remains **frozen and hash-pinned** at `90024d86d7a5c268` as the
platform's adversarial regression fixture.

### What must not happen next

Every obvious rescue is now measured and closed: lowering the liquidity
threshold (the curve is negative at 50% participation), tightening it (negative
at 0.25%), shortening the horizon (h=5 is net negative in tradable names and
needs 10× the market's capacity), and reducing cost (E19 showed negative at
0.75× cost). None of these is available, and each is blocked by a control that
raises rather than a convention someone remembers.

### What is worth keeping

E20 turns Alpha Core's failure into a reusable measurement. The finding that
**predictive information on this market concentrates where capacity is not** is
a property of the market, not of this strategy, and it belongs in the
microstructure workstream of [RESEARCH_ROADMAP.md](RESEARCH_ROADMAP.md) —
specifically `capacity.py`, whose whole purpose is to answer *"can this be
traded"* before anything asks *"will this go up"*. Any future factor on this
market should be measured against the capacity curve **at Stage 1**, not
discovered to be untradable at Stage 5.

---

## Provenance

| | |
|---|---|
| experiment | `E20-alpha-core-capacity` |
| preregistration | `1f9d15b09459975593e525db2e86122a2487df6cbd964a1a60a2cce8c9f03878` |
| control arm | `E19-alpha-core-tradable-edge` (unmodified) |
| E20 panel | `E20_audit_panel.csv.gz` — E19's panel **+ fwd_60**, written to a separate file so E19's evidence hash is untouched |
| universe | `u20260819-668658bb` (1,394 instruments, restorable) |
| Alpha Core | `90024d86d7a5c268` — **unchanged** |
| seed | 20260820 |
| tests | 943 / 943 |
| ledger | 13 entries, chain intact |
| independent OOS | **none available** — E19 spent the clean holdout |
| artifacts | `E20_capacity_audit.json`, `E20_decision.json` |
