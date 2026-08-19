# EXPERIMENT 4 — ALPHA CORE INDEPENDENT OOS VALIDATION

## PRE-FLIGHT AUDIT — EXPERIMENT NOT RUN

**Independent audit · 2026-08-18 · no holdout number was computed**

---

## 1. Executive Verdict

# INVALID — FIX INFRASTRUCTURE FIRST

**The experiment was not run, and must not be run in its current form.**

Four preconditions written into the brief fail. Two are hard and cannot be
fixed on this machine today. Per §5 ("If not, STOP and report that a new clean
holdout must be defined and preregistered before testing"), §22 ("If
survivorship bias exists: INVALID EXPERIMENT") and §29 ("If the repository
cannot be made reproducible: STOP"), stopping is the instructed action, not a
judgement call.

No preregistration file was written, because a preregistration that locks a
contaminated holdout would give a compromised experiment the appearance of
rigour. **The single most valuable property of Experiment 4 is that it would be
clean. It cannot be clean today.**

| # | Precondition | Status | Fixable now? |
|---|---|---|---|
| §5 | A genuinely untouched holdout exists | **FAIL** | No — no untouched data exists |
| §29 | Reproducible git provenance | **FAIL** | No — git is not installed |
| §22 | Universe free of survivorship bias | **FAIL** | No — requires data never collected |
| §7 | Constant market exposure across arms | **FAIL** | Yes, but it is an infrastructure change |

---

## 2. Architecture Map (§4)

Delivered as required before any implementation. The files the brief named as
`alpha_core.py`, `strategy.py`, `portfolio.py`, `execution.py`, `engine.py`,
`pit.py` and `ReadingPanel` all exist, in `backtesting/`.

| # | Stage | Location |
|---|---|---|
| 1 | **Alpha Core scores** | `backtesting/alpha_core.py:264` `alpha_scores()` — 50% momentum percentile rank + 50% Bollinger %B percentile rank |
| 2 | **Ranking** | `alpha_core.py:255` `_percentile()` — cross-sectional rank within each decision date, `na_option="keep"` |
| 3 | **Universe formation** | `backtesting/pit.py` `PointInTimeData` — `calendar()`, `rebalance_dates()`; instrument list from the `instruments` table; `MIN_HISTORY_BARS` guard |
| 4 | **Selection** | `alpha_core.py` `_rebalance()` — top `N = cfg.max_positions` (10); exit is rotation out of the top N |
| 5 | **Position sizing** | `backtesting/sizing.py` — modes `risk_based` / `equal_weight` / `fixed_fractional` / `volatility_adjusted`; ATR-derived stop distance |
| 6 | **Order generation** | `risk_engine.assess()` → `trade_manager.plan()` → `portfolio_engine.allocate()` (position caps, sector spread, cash floor) |
| 7 | **Execution** | `backtesting/execution.py` — T+1 delay, queue detection (صف, approximated from price-band proximity + volume collapse), participation cap, partial fills |
| 8 | **Transaction costs** | `backtesting/portfolio.py` — `commission_paid` and `tax_paid` accumulated on `Position` and `Book` |
| 9 | **NAV** | `backtesting/portfolio.py:125` `Book.value() = self.cash + holdings_value()` |

**Two structural observations, both favourable to the design:**

- The **NAV identity is structurally enforced**, not merely checked —
  `Book.value()` is defined as cash plus marks, so it cannot drift. §24's
  requirement of zero unexplained breaches is satisfied by construction.
- **The random control is built into the same class.** `alpha_core.py` accepts
  `ranking="random"` with a seed, and the docstring states that this replaces
  *only* `alpha_scores()`, leaving eligibility, sizing, stops, allocation and
  execution identical. This is exactly the fair-universe construction §9
  requires, and it means Arms A and B differ in nothing but stock identity.

The harness is well built. The blockers below are about data and provenance,
not about code quality.

---

## 3. Blocker 1 — No untouched holdout exists (§5)

**This is the decisive blocker.**

The only data after Alpha Core's training window (Experiments 2 and 3 both used
2014-09-21 → 2024-09-01) is **2024-09-02 → 2026-08-17**. That window has been
touched three separate times.

### Evidence

**(a) The repository itself labels it "the fitted window."**

`research_factors.py` sets:

```python
FIT_START = pd.Timestamp("2024-09-01")
# Everything before it is genuinely out of sample for those weights;
# everything after is not.
```

and states in its own header:

> "The shipped weights were chosen on **2024-09-01 .. 2026-06-30**. Every number
> this project has quoted about them is in-sample."

**(b) Alpha Core's two factors have already been measured on exactly these
dates.** `factor_research.json` contains an `in_sample` block labelled
"the fitted window" holding **17,695 observations across 23 dates** — the same
23 decision dates the holdout would use (confirmed: 23 panel dates fall after
2024-09-01, spanning 2024-09-10 → 2026-07-12).

**(c) `research_exposure.py` ran the live strategy stack over this window.**
`backtest_config.DEFAULT_START = "2024-09-01"`, and `research_exposure.json`
reports funnel, stress and regime results across 2024-09-01 → 2026.

**(d) I evaluated the exposure engine on this window in the previous
experiment**, and its results are now published.

### Why this is fatal rather than a caveat

§5 says "Do not invent a split after examining results." The factor-level
behaviour of momentum and Bollinger %B over the proposed holdout has already
been computed and stored. A test run now would not be a holdout test — it would
be an in-sample test wearing a holdout's label, and neither a PASS nor a FAIL
would carry information. A PASS would be unbelievable; a FAIL would be
attributable to contamination.

### There is no alternative window

The database ends 2026-08-17. There is no later data to reserve, and carving a
split out of the pre-2024 period would place the test inside Alpha Core's own
training window.

---

## 4. Blocker 2 — Provenance cannot be made reproducible (§29)

§29 requires a git commit hash recorded in `provenance.json`, and explicitly
forbids substituting file modification times.

```
git rev-parse --is-inside-work-tree   →  git: command not found
Get-Command git                        →  not on PATH
C:\Program Files\Git                   →  does not exist
c:\SIRAT-AI\.git                       →  does not exist
```

**Git is not installed anywhere on this machine.** I cannot initialise a
repository, cannot create a commit, and cannot record a hash. §29's fallback is
explicit: *"If the repository cannot be made reproducible: STOP and report the
provenance problem before running the final holdout."*

This is the most easily fixed blocker — install git, `git init`, add a
`.gitignore` excluding `sirat_market.db` (424 MB), `__pycache__/` and
`backtest_output/cache/`, then commit. It requires an action outside my remit
on this machine.

---

## 5. Blocker 3 — Survivorship bias is present and documented (§22)

§22: *"Verify that the universe does not contain only currently surviving
securities... If survivorship bias exists: INVALID EXPERIMENT."*

The repository's own module answers this, and the answer is unambiguous.
`backtesting/survivorship.py`:

> "The universe is `instruments`, and `instruments` is the list of things listed
> TODAY. A company that was delisted in 1403 is not in it, was never in it as
> far as this database is concerned, and therefore never appears in any
> historical window. It is absent precisely where its absence flatters the
> result: companies leave this market after collapsing, not after doubling.
>
> No amount of care inside the backtest engine fixes that. The fix is a
> historical universe snapshot — the list as it stood on each decision date —
> and **this project has never collected one**."

The module explicitly refuses to emit a correction factor, on the correct
grounds that it would be "a guess wearing a decimal point."

### Honest scoping of this blocker

I measured the visible component earlier: of 476 names trading in 2015, 468 were
still trading a decade later — **1.7% attrition**. That is a *lower bound* that
counts only names still on today's list whose price series ended. Companies
removed from the list entirely are invisible and priced at zero.

**This blocker is fatal to absolute performance claims and largely cancels in
the primary test.** Arms A and B draw from an identical universe on identical
dates, so the bias inflates both equally; a relative Alpha-Core-vs-Random
comparison is not destroyed by it. On its own I would classify this as a
disclosed limitation rather than a stop. It is recorded as a gate failure
because §22 was pre-registered in absolute terms, and softening a
pre-registered gate after inspecting the situation is precisely what these
protocols exist to prevent.

---

## 6. Blocker 4 — Constant exposure cannot be enforced (§7)

§7 requires identical market exposure across all arms, and states: *"No
cash-timing advantage is allowed."* The current harness cannot deliver this.

From `execution_realism.json` (Experiment 3), against a 54.79% target:

| | Alpha Core | Random mean |
|---|---:|---:|
| Realised exposure | **29.22%** | **27.25%** |
| Fill rate | 93.3% | 83.67% |
| Average positions | 4.63 / 10 | 4.43 / 10 |
| Average cash | 70.78% | 73.63% |

Two distinct problems:

1. **The allocator delivers roughly half the requested exposure**, blocked by
   `portfolio_full` (~200), `cash_floor` (~170), `below_min_position` (~60) and
   sector caps. Requesting 100% would not produce 100%.
2. **The arms did not receive equal exposure.** Alpha Core realised 1.97pp more
   than the random mean, because its higher fill rate deployed more capital. In
   a rising market that is a return advantage unrelated to selection skill —
   exactly the confound §7 forbids.

Compounding this: `backtesting/portfolio.py` never credits idle cash while
`backtesting/metrics.py` charges a 23% hurdle. With ~71% of the book idle, every
arm carries a large artificial drag, and every Sharpe produced by that harness
is uninterpretable as a level.

*(Corrected 2026-08-18: an earlier draft named `backtest_portfolio.py`. That
file exists but is not the harness Experiment 3 runs — `backtesting.engine` uses
`backtesting/portfolio.py`. The defect was real and identical in substance; the
file name was wrong. Fixed in v2.1 via `Portfolio.accrue_cash()`.)*

This is fixable — bypass the allocator for a fixed-N equal-weight book, credit
idle cash at the policy rate, and report exposure-normalised results alongside
raw. But §3 requires that a discovered defect be **documented, not silently
fixed**, so it is documented here rather than repaired.

---

## 7. Finding — Experiments 2 and 3 selected factors on their own test set

Not a blocker, but material to how the prior Alpha Core rejection should be read,
and not previously stated.

`alpha_core.py`'s docstring records why Alpha Core is momentum + Bollinger %B:

> "Bollinger %B top-decile alpha +2.30% t = 7.04 · Momentum +1.14% t = 3.68 ·
> Technical score −0.14% · Liquidity −0.37% · shipped blend +0.11%"

measured over **2008–2024, 72,166 observations**. Experiments 2 and 3 then
tested Alpha Core over **2014-09-21 → 2024-09-01** — a subset of the window the
factors were chosen on.

The 50/50 weighting is genuinely unfitted, but **the choice of which two factors
to use was made on the test data**. Experiments 2 and 3 were therefore never a
clean test of Alpha Core's factor selection.

**This strengthens the existing rejection rather than weakening it.** Alpha Core
landed at the 26.7th percentile of random selection (p = 0.7419) *despite*
having its factors chosen on the same data. A signal that cannot beat random
with that advantage is unlikely to do so without it.

---

## 8. Gate Status

No gate could be evaluated, because no holdout number was computed.

| Gate | Status |
|---|---|
| G1 Net performance | Not evaluated |
| G2 Risk-adjusted performance | Not evaluated |
| G3 Statistical evidence | Not evaluated |
| G4 Cost survival | Not evaluated |
| G5 Rank predictiveness | Not evaluated |
| G6 Random-selection separation | Not evaluated |
| G7 Episode robustness | Not evaluated |
| **G8 Research validity** | **FAIL** — survivorship (§22), holdout contamination (§5), provenance (§29), exposure inequality (§7) |

Per §25, G8 failing means the verdict is INVALID and no PASS may be reported.

---

## 9. What a Clean Experiment 4 Requires

Ordered by whether it blocks, and by cost.

### Must be fixed before any rerun

1. **Install git; commit the research code.** `git init`, `.gitignore`
   excluding `sirat_market.db`, `__pycache__/`, `backtest_output/cache/`, then
   a clean commit whose hash goes into `provenance.json`. *Cost: minutes.*

2. **Define a forward holdout and freeze it.** The only uncontaminated data is
   data that does not exist yet. Fix a start date of today (2026-08-18), write
   the preregistration now, and do not evaluate until enough sessions have
   accumulated. At the current ~20-session rebalance cadence, **12 months gives
   roughly 12 decision dates and 240 sessions** — thin, but genuinely clean.
   *Cost: 12 months of waiting, near-zero engineering.*

3. **Fix the exposure and cash defects** so §7 can be honoured: bypass the
   allocator for a fixed-N equal-weight book at a pre-registered constant
   exposure, credit idle cash at the policy rate, and verify realised exposure
   is equal across arms to within a stated tolerance before reading any result.
   *Cost: ~2 days.* Do this now, so the harness is ready when the holdout
   matures.

### Should be fixed, cannot be retrofitted

4. **Begin collecting a point-in-time universe snapshot.** Record the traded
   instrument list weekly from today forward. This cannot repair history, but
   in two years it makes clean research possible. Nothing else fixes §22.
   *Cost: a scheduled job.*

### Available immediately, and worth more than the blocked experiment

5. **Run the cross-sectional rank-IC study on the pre-2024 window instead.**
   Rank IC, decile monotonicity, hit rate and top-minus-bottom spread for the
   momentum + %B blend do not require a portfolio, an allocator, or a clean
   exposure model — and the `factor_panel.csv` already holds 89,861 observations
   across 213 dates with forward returns. This cannot answer the primary
   question (it is in-sample for factor selection), but it can answer a sharper
   one: **is the 50/50 blend even monotonic in forward returns?** If the blend
   is not monotonic in-sample, no holdout is needed to reject it.
   *Cost: ~1 day. Highest information per unit of effort available today.*

---

## 10. Final Conclusion

> **Does Alpha Core contain genuine incremental stock-selection value after
> realistic execution costs?**

**Unknown, and not answerable with the data and infrastructure available
today.**

The existing evidence — 26.7th percentile against random, p = 0.7419, under
execution realism — is unfavourable, and §7 of this audit shows it was obtained
with the factors chosen on the same data. But that evidence is in-sample, and
Experiment 4 as specified would not have improved on it: the only post-training
data has already been examined at the factor level and is labelled "the fitted
window" by the repository's own code.

Running it anyway would have produced a number with the authority of a holdout
test and the information content of an in-sample one. That is worse than not
running it.

---

## 11. Next Action

# INVALID — FIX INFRASTRUCTURE FIRST

Concretely, and in order:

1. Install git and commit the research code (minutes).
2. Preregister a **forward** holdout starting 2026-08-18, evaluated no earlier
   than 2027-08 (near-zero cost, 12 months of patience).
3. Repair the exposure/cash defects so §7 can be honoured (~2 days).
4. Start recording a point-in-time universe snapshot weekly (a cron job).
5. Meanwhile run the **rank-IC diagnostic** on `factor_panel.csv` — it is
   available today, needs none of the blocked infrastructure, and can reject
   Alpha Core without a holdout if the blend is not monotonic.

**Do not** tune Alpha Core, add factors, or apply machine learning. Nothing in
this audit licenses any of that, and §26/§27 forbid it.

---

## Provenance

| Item | Value |
|---|---|
| Experiment ID | EXP4-PREFLIGHT-2026-08-18 |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
| Configuration hash | Not recorded — would require the git commit §29 mandates |
| Data snapshot | `sirat_market.db` 424.4 MB, mtime 2026-08-18 05:54; `factor_panel.csv` 89,861 rows, 2008-12-04 → 2026-07-12 |
| Seed | None — no stochastic component was executed |
| Python | 3.14.7 · numpy 2.5.1 · pandas 3.0.5 |
| Executed | 2026-08-18 |
| Holdout numbers computed | **Zero. The holdout remains unevaluated for Alpha Core.** |

Files that were **deliberately not created**, because creating them would
legitimise a contaminated experiment:
`alpha_core_holdout_preregistration.json`, `alpha_core_holdout_results.json`,
`alpha_core_daily_returns.csv`, `random_trials.csv`, `annual_results.csv`,
`episode_results.csv`, `rank_ic.csv`, `trade_ledger.csv`,
`cost_attribution.json`, `provenance.json`,
`alpha_core_holdout_report.md`.
