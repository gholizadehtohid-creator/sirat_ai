# E25 — Smart Money Accumulation Scanner: Stage 1 Validation Report

**Experiment** `E25-smart-money-accumulation-stage1`
**Ledger** seq 17, entry `084aa09af59f`, chain verified intact (17 entries)
**Date** 2026-08-24 · **Data session** 2026-08-19 · **Universe snapshot** `u20260819-668658bb`
**Windows spent** none. `R-FORWARD-2027` named, not claimed.

---

## The two verdicts, which are not the same verdict

| | Result |
|---|---|
| **Does the scanner compute what it claims?** | **YES** — 19/19 independent checks pass, after two real defects were found and fixed |
| **Can its return hypothesis be tested on this database?** | **NO** — Experiment Manager Stage 1 (DATA INTEGRITY) failed 4 of 5 checks |

These are different questions with different fixes, and a report that merged them
would send someone hunting for a bug that is not there. The scanner is correct as
a **live** instrument. It is not currently testable as a **historical** one.

**No conclusion about profitability is issued anywhere in this report.** Stage 2
was not run, and under the platform's own rules it must not be.

---

## Part 1 — Validation of the filter

Reproducible: `python examples/smart_money_validation.py`
Artifacts: `backtest_output/E25_stage1_validation.txt` / `.json`

### 1.1 Data audit

| Input | Source | Coverage | History | If absent |
|---|---|---|---|---|
| MFI | `price_history` H/L/C/V | 818/818 equities | 2001-03-25 → 2026-08-23 | reading is `None`, name fails the gate |
| P/E | `market_snapshot.pe` | 59.8% of equities | **NONE — 1 timestamp, replaced per pull** | 15 pts score **zero** |
| P/E (sector) | `fundamental_engine.sector_medians` | 30 sectors with ≥3 comparables | **NONE** | 15 pts score zero |
| Market Cap | `total_shares × close` | 63.2% of equities | **NONE — share counts unversioned** | 10 pts score zero |
| حقیقی / حقوقی | `client_type_history` | 95.4% of equities | 2008-11-26 → 2026-08-23 | **VETO** — name excluded |
| کد به کد | derived from the count columns | needs ≥9 sessions | same as above | 15 pts score zero |
| حجم | `price_history.volume` | 96.9% | full | 10 pts score zero |
| حمایت | pivots + SMA + range + Fibonacci | derived | full | **VETO** — name excluded |

A **failed** test scores 0. **Missing** data scores `None` and is reported as
reduced coverage. The two are never merged, because "we looked and it was not
there" and "we could not look" are different claims.

### 1.2 Factor-by-factor verification — all pass

| Factor | How it was checked | Result |
|---|---|---|
| MFI | reimplemented from first principles (typical price → raw money flow → ±sums), compared bar by bar | max difference **2.7 × 10⁻¹³** |
| MFI bounds | textbook property | always within [0, 100] |
| P/E | `pe` vs `close/EPS` on 385 names | 384 agree within 2%; 1 outlier |
| Sector P/E | medians recomputed by hand for 30 sectors | exact match; none built from <3 comparables |
| Market cap | `shares × close`, five largest inspected | فملی 2714 همت, فارس 1234, شپنا 705 — plausible ordering |
| Size bands | terciles of the live market | 173 / 172 / 172 — an exact third each |
| Flow | the accounting identity `buy = sell` on every session of 16 names | **gap exactly 0.0** |
| Flow mirror | حقوقی supply is `−`(حقیقی net), never recomputed | exact on all rows |
| کد به کد | Smart Money Ratio recomputed by hand from the stated formula | exact on 16 names; always < 1.0 |
| کد به کد separation | does the genuineness test reject anything? | 7 genuine / 9 rejected — it discriminates |
| Supports | all four families, ordering, position relative to price | all below price, nearest-first, all four families reachable |

### 1.3 Look-ahead audit

- Every price-derived reading is a **pure function of the bars supplied** (25 names).
- **No pivot** is reported inside the unconfirmable tail — `levels.swings` is honest about confirmation lag.
- **One structural look-ahead exists and is named**: P/E, sector P/E and market cap come from a single-timestamp snapshot. For a live scan that is correct. For any past date it is look-ahead, and the data to fix it does not exist here.

### 1.4 Two defects found — and fixed

**Defect 1 — suspended and queue-locked names passed every gate.**

TSETMC emits a bar for every listed session whether or not anything traded, so a
suspended name carries its close forward at zero volume. On the validation
session:

| Name | Traded, MFI window | Traded, volume window | What the scanner claimed |
|---|---|---|---|
| **فارس** | **2 of 14 (14%)** | 5 of 20 (25%) | "MFI 74.8, rising +74.8", "volume 4.95× average" |
| **نیان** | 12 of 14 (86%) | 13 of 20 (65%) | "MFI 100.0", "volume 2.80× average" |

Both figures were arithmetic over sessions that did not happen. MFI weights each
session by price × volume, so its window collapsed to two real observations, hit
0.00, went NaN, and printed 100.00 on the reopening session. The volume "average"
was mostly zeros, so any reopening reads as an explosion. The single-session
liquidity floor cannot catch this — a reopening session has enormous turnover,
which is precisely when the floor is checked.

**2 of 5 candidates were artefacts — a 40% false-positive rate.**

Fixed: a hard `thin_trading` gate requiring ≥85% traded sessions in **both**
windows (they fail differently — نیان passed one and failed the other), and the
volume average now excludes non-trading sessions. 107 names (17.7% of the
"liquid" universe) are removed by this gate.

**Defect 2 — the scanner disagreed with the governance layer about reality.**

`smart_money/position.py` used 200% as the impossible-move threshold, copied from
`examples/e21_contamination_audit.py`. The platform's Stage 1 gate
(`sirat/experiments/gates.py`) uses **100%**. A name could pass the scanner on a
bar the platform counts as impossible. Fixed by importing the governance
constant rather than restating it.

Both defects are now guarded by tests. **1177 tests pass.**

### 1.5 False positives — clean after the fixes

Every selected row is re-derived through **7 independent recomputations**: MFI,
range position, support existence and distance, traded share, net حقیقی inflow,
کد به کد genuineness, and the stated P/E gap. **No discrepancy remains.**

### 1.6 False negatives — deferred, with the reason

"Stocks that rose but the filter missed" is an **outcome** question. Answering it
means measuring forward returns, which is Stage 2 work, and Stage 2 is blocked
below. What *is* available now is the mechanical half — the funnel names which
gate rejected what, every run:

```
818 universe → 606 liquid → 495 traded ≥85% → 231 MFI>70 → 5 at floor → 3 qualified

rejected: at_ceiling 227 · mfi_floor 266 · thin_trading 107
          far_from_support 1 · no_transfer 1 · data_integrity 1
```

The dominant rejector is **`at_ceiling` (227)** — names with money flowing in that
have already moved. That is the filter working as designed, and it is also where
its false negatives will live if it has any.

### 1.7 Parameter sensitivity — in-sample, one session, selectivity only

| MFI floor | Names | | Volume ratio | Names | | Score ≥ | Names |
|---|---|---|---|---|---|---|---|
| 65 | 9 | | 1.5 | 2 | | 70 | 1 |
| **70** | **5** | | 2.0 | 2 | | 75 | **1** |
| 75 | 1 | | | | | 80 | 0 |

**This measures how many names survive, not how well they perform.** Picking a
setting because it produced a nicer list, then claiming an edge for it later, is
exactly the selection error E19/E21 documented.

**Recommended initial setting: MFI 70, Volume Ratio 1.5 — i.e. the brief's own.**
Not because they were measured best (nothing here measures performance) but
because 75 collapses the output to one name and 1.5 vs 2.0 does not discriminate
at all. **Do not use a score threshold as an entry rule yet** — the score has no
validated meaning; use it as a rank.

---

## Part 2 — Portfolio construction: BLOCKED, and why

### 2.1 The Experiment Manager rejected it at Stage 1

```
stage 1  DATA INTEGRITY  --  FAIL
   point_in_time    FAIL
   survivorship     FAIL
   universe_valid   PASS
   missing_data     FAIL
   price_sanity     FAIL

next stage allowed: None
decision: REJECT at stage 1
```

| Check | Measured | Threshold |
|---|---|---|
| `point_in_time` | 1 snapshot timestamp; valuation (15 pts) + size (10 pts) unversioned | ≥2 |
| `survivorship` | 13,245 instruments discovered outside today's universe, **0 bars fetched** | ≥1 |
| `universe_valid` | median **776** names/session over 867 sessions | ≥100 ✓ |
| `missing_data` | P/E **59.8%**, market cap **63.2%** | ≥60% |
| `price_sanity` | **47** impossible transitions in 666,954 panel rows | 0 |

**The hypothesis is untested, not disproved.** Nothing here measured a return.

### 2.2 A second, independent obstacle: breadth

Measured over the two **already-spent** windows (E19's and E21's — reading them
again spends nothing), applying only the point-in-time gates. Support and flow
gates are omitted, and both can only remove names, so this is an **upper bound**:

| Year | Sessions | Mean/session | Median | ≥10 names |
|---|---|---|---|---|
| 2023 | 240 | 18.5 | 12 | 60.0% |
| 2024 | 241 | 28.9 | 16 | 71.4% |
| 2025 | 240 | 29.3 | 14 | 65.0% |
| **2026** | 146 | **2.9** | **1** | **8.9%** |
| **all** | 867 | 21.7 | 11 | **55.9%** |

The requested rule is "Score ≥ 75, take the top 10 at 10% each". On the upper
bound — before scoring, before the threshold — 10 names are available on 55.9% of
sessions, and on 8.9% of 2026 sessions. On the live session, Score ≥ 75 yielded
**one** name.

This is structural, not regime bad luck: the filter looks for money entering a
name whose price has *not* moved, so it emits many candidates when the market is
basing and almost none when it is rising. A continuously-held 10-name equal-weight
book cannot be fed by it.

---

## What would unblock a successor

In the order that unblocks the most for the least work:

1. **Version `market_snapshot`** into a per-session series (one row per instrument per session). This alone makes a full-model historical test possible *from that day forward* — it cannot recover the past.
2. **Finish `delisted_sweep.py`'s fetch phase** to populate `census_price_history`. Discovery is already done (14,633 instruments); zero bars are fetched.
3. **Or register a different, point-in-time-only model** — MFI 15 + transfer 20 + کد به کد 15 + support 15 + volume 10, rescaled from 75 to 100. That *is* testable on stored data today, but it is a different model with different weights and must be preregistered as such, never described as this scanner.
4. **The full return claim** may be tested out of sample only against `R-FORWARD-2027` (2026-08-19 → 2027-08-19), by a successor registered once that window accrues.

Meanwhile the scanner writes every run — candidates, funnel, and computed-at
timestamp — to `smart_money_signals`. That log is what turns a year of waiting
into a year of admissible evidence, on the same timestamp rule as
`examples/forward_prediction_log.py`.

---

## Answers to the questions posed

| Question | Answer |
|---|---|
| Best parameters? | MFI 70, Volume Ratio 1.5 (the brief's own). Chosen on selectivity, **not** performance — no performance was measured. |
| Best portfolio size? | **Unanswerable, and the filter cannot support 10.** Breadth is the binding constraint. |
| Best holding period? | **Unanswerable.** Requires forward returns. |
| Which conditions help, which hurt? | **Unanswerable for returns.** Mechanically, `at_ceiling` (227) and `mfi_floor` (266) do almost all the filtering; `far_from_support` and `no_transfer` rejected 1 each. |
| What raises Sharpe / cuts drawdown? | **Unanswerable.** No Sharpe or drawdown was computed. |
| A / B / C verdict? | **None of the three.** A, B and C all assert something about edge. The correct verdict is **D: not yet testable** — Stage 1 data integrity fails, and the honest label for an untested hypothesis is untested. |

---

# Addendum — weaknesses found after the user's live observation

> «از سه سهمی که از فیلتر آمد بیرون دو تا صف فروش بودند»

That observation exposed a defect the whole validation had missed, and pulling
on it exposed four more. All were measured before being fixed, and one
candidate fix was measured and **rejected**.

## The root defect: the premise was never actually implemented

The scanner's premise is *money entering **while the price has not yet moved***.
"Has not yet moved" was implemented as **position within the 120-session range**
— which says where the price stands relative to its own past, not whether it is
moving now. A name that fell 60% and is bouncing violently sits in the bottom
third of its range while being the exact opposite of quiet accumulation.

Measured across the 231 names clearing MFI>70:

| | median 10-session return |
|---|---|
| liquid universe | **+11.0%** |
| MFI>70 pool | **+19.0%** |

**MFI above 70 on this market does not find quiet names. It finds names that
have already run.** All three candidates had: پارس +9.1%, سخوز +15.0%,
حشکوه +13.8%. A failing violent bounce is exactly what ends in a صف فروش.

## Five fixes

| # | Weakness | Evidence | Fix |
|---|---|---|---|
| 1 | **No queue check at all** | 19.9% of the MFI>70 pool was locked on the last session; the 2026-08-22 order book showed سخوز at limit-up with 4.9M shares in 228 buy orders against 894 to sell, فارس with 401M shares in 1,856 orders | E14's `unbuyable` detector, reused unchanged, as a hard veto |
| 2 | **The premise unimplemented** | above | market-relative `advance` gate: excess over the liquid universe's own median, margin = `config.PRICE_BAND_PCT` (one daily band) |
| 3 | **Stores misaligned in time** | 636 names had client-type rows to 08-23 while price stopped at 08-19 — the transfer component described a different week from the MFI component | single `as_of` date; every price and flow series truncated to it |
| 4 | **Cross-section built from different dates** | a handful of names carried bars 3 sessions fresher than the rest, so even the market median mixed days | same fix — as-of = newest date where ≥90% of the population has a bar |
| 5 | **Staleness invisible** | data is 5 days behind the live market | `staleness` block on every report, warning banner in the panel |

Plus the MFI component itself: it had **4 distinct values, sd 1.81 of 15
points** — gating on MFI and then scoring it in three coarse steps meant 15
points barely reordered anything. The rise term is now continuous in the
magnitude of the climb (the brief asks to *examine* the rise; a boolean does not
examine a magnitude). Now **48 distinct values**.

## One candidate fix, measured and rejected

Chronic band-closing looked like an obvious disqualifier — two of three
candidates closed at the daily band 6 times in 20 sessions. Then the
distribution was checked:

| population | median band closes in 20 |
|---|---|
| eligible universe | **6** |
| MFI>70 pool | **7** |

Gating on it would reject the **median name of this market**. It measures the
exchange's daily band, not the candidate. So the count is now **reported on
every row and decides nothing**.

## Effect

```
before:  818 → 606 liquid → 231 MFI>70 → 5 at floor →                    3 qualified
after:   818 → 606 liquid → 495 traded → 231 MFI>70 → 5 at floor
                          → 5 not queued → 4 still quiet →               2 qualified
```

`1183 tests pass.` The two new gates and the lock detector's three-way
separation (lock vs stale bar vs reopening gap) are each covered by tests.

## What is still unfixed, and cannot be fixed in code

The scanner describes **2026-08-19**. You are looking at **2026-08-24**. Three
trading sessions of queue state, price and flow are simply not in the database,
and no gate can recover them. A name that was clean at the last stored close can
be sitting in a صف by the time the list is read — which is precisely what
happened. The panel now says so in a warning banner instead of presenting a
5-day-old picture as current.

---

# Addendum 2 — the root cause was not in the scanner

> «هر دو سهم معرفی شده صف فروش هستند»

Three consecutive live readings landed in queues. That is a pattern, so it was
measured rather than patched again.

## First: is the filter a queue detector?

Over 2022-06 → 2026-08 (both windows already spent by E19/E21; a **tradability
diagnostic**, no return computed, no edge claimed), applying the point-in-time
gates:

| horizon | selected → صف فروش | universe base rate | lift |
|---|---|---|---|
| t+1 | 0.89% | 1.70% | **0.52×** |
| t+3 | 0.95% | 1.76% | **0.54×** |
| t+5 | 0.98% | 1.76% | **0.55×** |
| any of 5 | 3.2% | 5.4% | **0.59×** |

**No.** Historically the filter's picks hit sell queues at roughly *half* the
market's rate. The live failures were not the filter selecting queue-prone names.

## Then: the actual root cause — a platform-wide freshness bug

`bootstrap._history_state()` measured recency with `database.market_turnover()`,
which sums **every row in `price_history`** — and `gold.py` fetches the bullion
funds on their own schedule. On 2026-08-24:

```
newest bar over ALL of price_history : 2026-08-23   ← gold funds only
newest bar over EQUITIES only        : 2026-08-19   ← every single equity
```

So the panel reported `history: newest 2026-08-23, behind: False,
needs_fetch: False`, the auto-fetcher never refetched, and **every scanner, the
screen and the basket were reading a three-session-old market while the panel
said the data was current.** The same query also printed `832/818 equities have
bars` — a coverage ratio above 100%, which is what a wrong denominator looks
like.

Fixed with `database.newest_equity_bar()` and an equity-scoped coverage count.
On the very next restart the panel printed *"Fetching in the background:
history"* and pulled the missing sessions. Equity bars now reach **2026-08-23**,
coverage **818/818**.

**This was never a Smart Money defect. It affected the whole platform.**

## What the missing sessions contained

| نماد | 08-22 | 08-23 | 08-24 (observed) |
|---|---|---|---|
| پارس | +2.34% | **−2.63%** | صف فروش |
| حشکوه | +2.87% | **−1.29%** | صف فروش |
| سخوز | **صف خرید** | **صف خرید** | — |

The market topped. The filter had selected names at the crest of a market-wide
melt-up and could not see the reversal because the reversal was not in the
database.

## The last hole: a relative gate cannot see a market-wide run

`advance()` asks whether a name outran *the market*. In a melt-up every name
passes while every name is extended — حشکوه's excess was **+0.3pp** while it was
up **9.1% outright**.

Added `market_state()`. On the current session it reports:

```
regime: صعود شتابان   locked-up 13.3%   locked-down 0.9%
median 10-session return +8.8%   informative: FALSE
```

with the note that on such a session "money entering" is a property of the
market, not of any name in it. Measured precedent: **51% of liquid names closed
limit-up on 2026-08-08.**

## Staleness now blocks instead of warning

It warned first, and the list was acted on twice anyway. A run more than one
session behind is now marked `actionable: false`, every row carries `stale:
true`, and the panel presents it as *"سابقه‌ی یک جلسه‌ی گذشته، نه فهرست جاری"*
rather than a live watchlist. Staleness is counted in **trading sessions**
(Tehran trades Saturday–Wednesday), so a normal weekend is not an alarm.

## Current state

```
as-of 2026-08-23 · 1 session behind · actionable: TRUE
regime صعود شتابان · informative: FALSE
818 → 609 liquid → 525 traded → 268 MFI>70 → 3 at floor → 3 not queued
    → 3 still quiet → 2 qualified
```

`1189 tests pass.`
