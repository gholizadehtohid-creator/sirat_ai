# ALPHA CORE — RANK-IC DIAGNOSTIC

**Evaluation only · no backtest · no holdout · no tuning · 2026-08-18**

Reproducible: `python research_alpha_ic.py`. Seed 20260818.

---

## 1. Executive Verdict

# FAIL at the traded horizon — but the signal is NOT absent

The pre-specified Phase 5 rule returns **PASS candidate at 5 and 10 sessions,
FAIL at 20 and 60**. The system rebalances every **20 sessions**
(`backtest_config.REBALANCE_SESSIONS = 20`), so **at the horizon Alpha Core
actually trades, it fails its own gate** — ICIR 0.219 against a required 0.30.

That headline needs three qualifications, all of which matter more than the
verdict itself:

1. **Alpha Core has genuine, overwhelming cross-sectional signal.** Against a
   200-permutation null per date, mean IC sits **z = +8.3 standard errors** above
   random at the traded horizon (+18.0 at 5 sessions). Any reading of the earlier
   experiments as "Alpha Core contains no information" is wrong.
2. **The 50/50 blend is strictly worse than one of its own two legs at every
   horizon.** Bollinger %B alone beats the blend on mean IC, ICIR, top-decile
   spread and hit rate — everywhere. Momentum at the traded horizon is
   statistically indistinguishable from noise (t = 1.15, 50.4% positive periods).
   The blend is diluting a working signal with a non-working one.
3. **The usable part is the top tail, not the ordering.** The long-only top
   decile beat the market by **+2.09% per 20 sessions (t = +6.65)** and was
   positive in *every* sub-period — including the five years where the IC itself
   was negative. IC measures monotonic ordering across all ~470 names; a
   long-only top-N book only ever harvests the extreme.

**The failure is real, and it is a failure of the blend and of stability — not a
demonstration that the ranking is uninformative.**

---

## 2. Phase 1 — Repository Audit

### Locations and schema

| Item | Location |
|---|---|
| Factor panel | `backtest_output/factor_panel.csv` — 22.4 MB, 89,861 rows, 25 columns |
| Factor research | `backtest_output/factor_research.json` |
| Alpha Core | `backtesting/alpha_core.py` — `AlphaCoreStrategy.alpha_scores()` at line 264 |
| Ranking primitive | `alpha_core.py:255` `_percentile()` — `rank(pct=True, na_option="keep") * 100` |

Panel columns: `date, ins_code, symbol, sector, close, f_momentum, f_technical,
f_fundamental, f_liquidity, rsi, macd_hist_norm, ma_gap_pct, atr_pct,
volume_ratio, volatility_60, adx, bb_pct, adv, ret_20, ret_60, ret_120, fwd_5,
fwd_10, fwd_20, fwd_60`.

### The panel carries Alpha Core's actual inputs, not proxies

This was the decisive validity check before running anything.

| Alpha Core reads | Panel writes |
|---|---|
| `alpha_core.py:366/391` — `factor_engine.momentum(closes, …).get("score")` | `research_factors.py:332` — `f_momentum = momentum.get("score")` from `factor_engine.momentum(closes[:index+1], …)` |
| `alpha_core.py:279` — `r["bb_pct"]` from the indicator frame | `research_factors.py:224` — `bb_pct = column("BB_PCT")` at the decision bar |

Identical calls, identical fields. The diagnostic measures the real model.

### Confirmations required by Phase 1

- **No code changes required.** `research_alpha_ic.py` is a new read-only
  module. Nothing in `alpha_core.py`, `factor_engine.py` or the panel was
  touched.
- **No parameters optimized.** The score is reproduced verbatim; the only
  choices are measurement thresholds, all pre-specified in the module docstring
  before any number was read (30-name IC floor, 50-name decile floor, the
  panel's own 5/10/20/60 horizons, sub-period boundaries, 200-permutation null).
- **No future data leakage.** `f_momentum` is computed on `closes[:index+1]`
  where `index = searchsorted(date,'right') - 1` — the last bar at or before the
  decision date. Forward returns are built strictly after the decision bar
  (`research_factors.py:243`). Ranks are cross-sectional *within* a date, so no
  information crosses dates.

### Dimensions

| Measure | Value |
|---|---|
| Rows | 89,861 |
| Span (advertised) | 2008-12-04 → 2026-07-12 |
| **Span (effective)** | **2014-09-21 → 2026-07-12** |
| Decision dates (all) | 213 |
| **Decision dates usable (≥30 scoreable names)** | **143** |
| Dates dropped as too thin | 70 — *all* of them 2008-12-04 … 2014-08-24 |
| Unique symbols | 816 |
| Sectors | 49 |
| Scoreable names per date | min 3, median 470, max 738 |

**The panel's 2008 start is not real.** Every dropped date falls before
2014-08-24, because the database holds only 5–22 instruments before 2014. The
diagnostic is a **2014-onward** study with 143 monthly decision dates.

Input coverage: `f_momentum` 89,861 (100%), `bb_pct` 83,323 (92.7%). **6,538
names (7.3%) are dropped for a missing %B** — reproducing the engine's rule that
a name missing either input is dropped rather than scored on the half it has.

---

## 3. Phase 2–3 — Rank IC and Stability

Score reproduced exactly: ranks taken over **all** names carrying each input on
the date, then the name dropped if either is NaN — so a half-measured name still
moves the denominator without ever receiving a score, which is what the engine
does.

### Full sample (143 dates, 2014-09-21 → 2026-07-12)

| Signal / horizon | Dates | Mean IC | Median IC | IC sd | ICIR | t (Newey-West) | % positive | Spread % | Hit % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **alpha / 5** | 142 | 0.0636 | 0.0372 | 0.132 | **0.481** | 5.75 | 68.3 | 1.907 | 55.0 |
| **alpha / 10** | 142 | 0.0423 | 0.0256 | 0.130 | **0.324** | 3.87 | 59.9 | 2.506 | 54.2 |
| **alpha / 20** ← traded | 143 | 0.0292 | 0.0150 | 0.133 | **0.219** | 2.63 | 55.9 | 3.258 | 51.5 |
| **alpha / 60** | 141 | 0.0274 | 0.0190 | 0.121 | **0.227** | 2.57 | 58.9 | 4.288 | 52.3 |
| momentum / 5 | 142 | 0.0377 | 0.0122 | 0.142 | 0.265 | 3.17 | 54.2 | 1.021 | 50.2 |
| momentum / 10 | 142 | 0.0243 | 0.0099 | 0.140 | 0.174 | 2.08 | 54.2 | 1.380 | 50.4 |
| **momentum / 20** | 143 | 0.0139 | 0.0004 | 0.145 | **0.096** | **1.15** | **50.4** | 1.769 | 48.9 |
| momentum / 60 | 141 | 0.0137 | 0.0233 | 0.140 | 0.098 | **0.99** | 55.3 | 2.623 | 49.7 |
| **%B / 5** | 142 | **0.0838** | 0.0683 | 0.133 | **0.631** | 7.55 | 73.2 | 2.518 | 57.6 |
| **%B / 10** | 142 | **0.0627** | 0.0565 | 0.133 | **0.473** | 5.66 | 63.4 | 3.253 | 56.5 |
| **%B / 20** | 143 | **0.0495** | 0.0471 | 0.137 | **0.362** | 4.34 | 65.0 | 4.464 | 53.6 |
| **%B / 60** | 141 | **0.0429** | 0.0371 | 0.117 | **0.368** | 4.28 | 66.7 | 6.191 | 54.1 |

t-statistics are Newey-West corrected where the horizon overlaps the ~20-session
date spacing (lag 0 for h≤20, lag 2 for h=60).

### Random control — the measurement is unbiased

200 seeded permutations of the score per date, names and forward returns held
fixed:

| Horizon | Alpha mean IC | Random mean IC | Random per-date sd | SE of mean | **z** |
|---|---:|---:|---:|---:|---:|
| 5 | +0.06361 | +0.00009 | 0.0422 | 0.00353 | **+18.0** |
| 10 | +0.04227 | +0.00057 | 0.0424 | 0.00355 | **+11.9** |
| 20 | +0.02920 | −0.00015 | 0.0423 | 0.00354 | **+8.3** |
| 60 | +0.02736 | −0.00011 | 0.0421 | 0.00354 | **+7.7** |

The permutation null lands on zero with sd ≈ 0.042 exactly as it should. **Alpha
Core's ordering is not luck.** It is, however, small.

### Stability — the real problem

| Period | Dates | Alpha IC | Momentum IC | %B IC | Alpha % positive |
|---|---:|---:|---:|---:|---:|
| 2008-2014 | 4 | +0.0388 | +0.0123 | +0.0544 | 50.0% |
| **2015-2019** | **60** | **−0.0081** | **−0.0117** | +0.0091 | **45.0%** |
| 2020-2024H1 | 56 | +0.0371 | +0.0252 | +0.0566 | 58.9% |
| **2024H2-2026** | 23 | **+0.1056** | +0.0535 | +0.1370 | **78.3%** |

*(h = 20; the 2008-2014 row rests on 4 dates and is not interpretable.)*

Two things stand out and both are unfavourable:

- **The signal did not work for five years.** Over 2015–2019 — the single
  largest block, 60 of 143 dates — mean IC was **negative** at horizons 10, 20
  and 60, with only 45% of periods positive.
- **The strongest sub-period is the contaminated one.** 2024H2–2026 is the
  window `research_factors.py` labels "the fitted window" and which the
  Experiment 4 preflight ruled unusable. It carries IC +0.1056, more than triple
  the full-sample figure.

### Leave-one-sub-period-out (h = 20, full sample +0.02920)

| Removed | Mean IC without it | Dates |
|---|---:|---:|
| 2008-2014 | +0.02892 | 139 |
| 2015-2019 | **+0.05615** | 83 |
| 2020-2024H1 | +0.02412 | 87 |
| **2024H2-2026** | **+0.01456** | 120 |

**Removing the contaminated window halves the headline IC** (0.0292 → 0.0146).
No removal flips the sign, so the pre-specified concentration test passes — but
the honest reading is that roughly half the measured edge sits in 23 dates that
cannot be trusted.

---

## 4. Phase 4 — Factor Attribution

> **Is the blend adding value compared with individual factors?**

# No. The blend destroys value at every horizon.

| Horizon | Blend IC | %B alone | Blend gives up |
|---|---:|---:|---:|
| 5 | +0.0636 | +0.0838 | **24.1%** |
| 10 | +0.0423 | +0.0627 | **32.6%** |
| **20** ← traded | +0.0292 | +0.0495 | **41.0%** |
| 60 | +0.0274 | +0.0429 | **36.3%** |

Same story on every other measure. At h = 20: ICIR 0.219 (blend) vs 0.362 (%B);
% positive 55.9 vs 65.0; top-decile excess over market +2.086% vs +2.558%.

**Why the blend fails to diversify:** the two legs are **0.567 cross-sectionally
correlated** (median 0.585 across 143 dates, sd 0.105). Blending two signals
that already agree most of the time cannot diversify much — and when one of them
is near-noise at the traded horizon (momentum: t = 1.15, ICIR 0.096, 50.4%
positive — a coin flip), the 50/50 weighting simply halves the good signal's
contribution.

The `alpha_core.py` docstring justified the blend by citing top-decile alphas of
+2.30% (%B) and +1.14% (momentum). The measurement here confirms that ranking
*and* shows the consequence the docstring did not: **at 50/50, the weaker leg
costs 41% of the stronger one's information at the horizon the system trades.**

> **This is an observation, not a recommendation.** Reweighting toward %B on the
> strength of this diagnostic would be tuning on the test set — precisely the
> error that invalidated the earlier experiments. Any change to the weights
> requires its own preregistered forward holdout. See §7.

---

## 5. The Long-Only Decomposition

IC measures monotonic ordering across all ~470 names. A long-only top-N book
never harvests that — it harvests the top tail only. Separating the two changes
the picture materially.

### Top decile vs market vs bottom decile

| Horizon | Signal | Top % | Market % | Bottom % | **Top − Mkt** (long-only) | Mkt − Bot (unshortable) |
|---|---|---:|---:|---:|---:|---:|
| 20 | **alpha** | +5.463 | +3.377 | +2.205 | **+2.086** | +1.172 |
| 20 | %B | +5.936 | +3.377 | +1.472 | **+2.558** | +1.906 |
| 20 | momentum | +4.210 | +3.213 | +2.441 | +0.997 | +0.772 |
| 60 | alpha | +14.195 | +11.066 | +9.907 | +3.128 | +1.160 |

**About 64% of the blend's decile spread sits in the long leg** — the half a
long-only book can actually reach.

### The long leg is stable where the IC is not

| Period | Dates | Top − market (per 20 sessions) | % positive |
|---|---:|---:|---:|
| 2015-2019 | 60 | **+1.178%** | 63.3% |
| 2020-2024H1 | 56 | +2.957% | 75.0% |
| 2024H2-2026 | 23 | +2.758% | 78.3% |

**The top decile beat the market in all three sub-periods — including the five
years when the full-cross-section IC was negative.** A signal can order its
extreme well while ordering its middle badly, and that is what this one does.

Full sample: **+2.086% per 20 sessions, sd 3.75, n = 143, t = +6.65**,
≈ +28%/yr gross before costs, against ~2.86%/yr of cost drag at Experiment 3's
measured 2.75 turnovers/year.

### So why did Experiment 3 land at the 26.7th percentile?

A gross edge of ~+28%/yr and a cost drag of ~2.9%/yr cannot produce a
bottom-third result. The gap is implementation, and it is quantified in the
Experiment 4 preflight:

| Cause | Evidence |
|---|---|
| The book holds the top **2%**, not the top decile | top-10 of ~470 names; the top 2% is far noisier than the top 10% (~47 names) |
| Sector caps push away from the highest ranks | `blocked_by.sector_count` in `execution_realism.json` |
| The allocator delivered **4.63 of 10** slots | `average_positions` 4.63; `portfolio_full` ~200, `cash_floor` ~170, `below_min_position` ~60 |
| Realised exposure **29.2%** against a 54.8% target | `realized_exposure_pct` |
| Idle cash earned **0%** while charged a 23% hurdle | `backtesting/portfolio.py` credits cash only from sale proceeds *(fixed in v2.1)* |

**The cross-sectional signal is real; the path from ranking to fill is where it
is lost.** That is a different and more fixable problem than "no alpha."

### Three reasons not to over-read this

1. **Survivorship.** The panel universe is today's listings
   (`backtesting/survivorship.py`: *"this project has never collected"* a
   historical snapshot). A top decile selected on momentum and %B in a
   survivors-only universe is biased upward by an unmeasured amount — names that
   rose and then collapsed into delisting are simply absent.
2. **Contamination.** 23 of 143 dates are the fitted window.
3. **No frictions.** The decile figures assume costless monthly rebalancing of
   ~47 equally-weighted names with perfect fills — none of which the Tehran
   order book provides.

---

## 6. Phase 5 — Pre-Specified Decision Rule

Thresholds fixed in `research_alpha_ic.py` before any number was read.

| Horizon | Mean IC > 0 | ICIR > 0.3 | % positive > 50 | Not concentrated | **Verdict** |
|---|:--:|:--:|:--:|:--:|---|
| 5 | ✓ | ✓ (0.481) | ✓ (68.3) | ✓ | **PASS candidate** |
| 10 | ✓ | ✓ (0.324) | ✓ (59.9) | ✓ | **PASS candidate** |
| **20** ← traded | ✓ | **✗ (0.219)** | ✓ (55.9) | ✓ | **FAIL** |
| 60 | ✓ | **✗ (0.227)** | ✓ (58.9) | ✓ | **FAIL** |

**The gate that matters is horizon 20**, because that is what the system
rebalances on. It fails.

The 5- and 10-session passes are not a route to a product: they imply
rebalancing 24–48 times a year, and at 1.04% round trip that is 25–50%/yr of
cost against a top-decile excess of +1.37% and +1.73% per period respectively.
**Alpha Core's signal decays faster than its trading costs can be amortised.**

---

## 7. Phase 6 — Roadmap

*Design only. Nothing below was implemented.*

### 7.1 Required fixes before any real holdout

| # | Fix | Why | Cost |
|---|---|---|---|
| 1 | **Git provenance** | Not installed on this machine; §29 of the Experiment 4 brief cannot be met and mtimes are not a substitute | minutes |
| 2 | **Exposure neutrality** | Experiment 3 gave Alpha Core 29.22% realised exposure against random's 27.25% — a 1.97pp advantage from fill rates, not skill | ~1 day |
| 3 | **Execution confound removal** | Bypass the allocator for a fixed-N equal-weight book; verify realised exposure is equal across arms *before* reading any result | ~1 day |
| 4 | **Idle-cash accrual** | `backtesting/portfolio.py` credits no interest while `backtesting/metrics.py` charges a 23% hurdle — every Sharpe from that harness is uninterpretable as a level. *(Corrected 2026-08-18: an earlier draft named `backtest_portfolio.py`; the harness Experiment 3 actually runs is `backtesting/portfolio.py`.)* **Implemented in v2.1.** | ~2 hours |
| 5 | **Historical universe snapshots** | Nothing else ever fixes survivorship. Record the traded instrument list weekly from today | a cron job |

Items 2–4 are the reason the cross-sectional edge did not reach the portfolio.
They should be fixed **before** the holdout matures, not after.

### 7.2 A clean forward holdout

The only uncontaminated data is data that does not exist yet.

| Parameter | Value |
|---|---|
| Preregistered | Now, before any further analysis |
| Start | 2026-08-19 (the first session after this diagnostic) |
| Earliest evaluation | 2027-08 — ~12 decision dates, ~240 sessions |
| Preferred evaluation | 2028-08 — ~24 decision dates |
| Evaluated | **Once** |
| Primary arm | Alpha Core, unmodified — the 50/50 blend, exactly as it stands today |
| Primary control | ≥30 seeded random rankings from the identical eligible universe |
| Secondary reference | Equal-weight universe |
| Exposure | Constant, pre-registered, **verified equal across arms** |
| Primary metric | Net CAGR vs the random distribution; Alpha Core's percentile |
| Gates | The G1–G8 set from the Experiment 4 brief, with §7 exposure equality checked before results are read |

**Why not test the %B-only variant in the same holdout:** because this
diagnostic is what would have motivated it. Adding it now makes the holdout a
test of a hypothesis generated from the test data. If a %B-only variant is to be
evaluated, it must be preregistered as a *named, frozen alternative* at the same
moment, with the multiple-comparison cost stated up front — two arms, not one,
and the bar raised accordingly.

**Twelve decision dates is thin.** It will not resolve a 0.03 IC. It will
resolve whether the implementation defects were the binding constraint, which is
the more valuable question given §5.

### 7.3 When an ML layer should be introduced

**Not now, and not next.** Three conditions, all of which must hold first:

1. **The forward holdout returns a positive, exposure-neutral result for the
   handcrafted signal.** ML on a base signal that fails is curve-fitting with
   extra steps.
2. **The implementation defects (§7.1 items 2–4) are fixed and verified.** ML
   cannot recover an edge destroyed between ranking and fill; it would learn to
   predict the allocator's failures.
3. **A point-in-time universe exists.** ML trained on a survivors-only universe
   learns "which stocks did not get delisted," which is unlearnable in advance
   and catastrophic live.

When those hold, the honest framing is narrow: not "ML picks stocks," but
**"does a learned combination of the existing factors beat the handcrafted 50/50
blend?"** — a question this diagnostic already shows has a trivial non-ML answer
(%B alone beats the blend at every horizon). *Test the trivial answer first.* If
a single-factor reweighting captures most of the available gain, an ML layer has
very little left to earn and a great deal of overfitting risk to add.

Any ML phase needs its own train / validation / test split with the test set
untouched — which, given §5, means data that does not exist yet either.

---

## 8. Final Conclusion

> **Does Alpha Core have genuine cross-sectional predictive power?**

**Yes — and it fails its own gate at the horizon it trades.**

Both halves are true and neither should be dropped:

- The ranking orders future returns far better than chance (z = +8.3 to +18.0
  against a permutation null), and its top decile beat the market in every
  sub-period tested, including one in which the full-cross-section IC was
  negative.
- At the 20-session horizon the system rebalances on, ICIR is 0.219 against a
  pre-specified 0.30. The signal decays faster than trading costs can be
  amortised, the IC was negative for the five years 2015–2019, roughly half the
  measured edge sits in 23 contaminated dates, and **the 50/50 blend is
  strictly dominated by one of its own two legs at every horizon.**

The earlier portfolio-level rejection (26.7th percentile, p = 0.7419) is not
explained by an absent signal. It is explained by a weak, fast-decaying signal
meeting an execution stack that delivered 4.63 of 10 positions and 29% of a 55%
exposure target.

---

## 9. Recommended Next Action

# FREEZE ALPHA CORE AND FIX THE IMPLEMENTATION

Not RETIRE: the cross-sectional evidence is too strong to discard, and the
portfolio-level failure has an identified, fixable proximate cause.

Not PROCEED TO ML: two of three preconditions fail, and a non-ML answer (%B
alone) is already sitting untested.

In order:

1. **Freeze Alpha Core exactly as it is.** No reweighting, no factor changes.
   The %B finding is a hypothesis generated from this data and must not be
   acted on inside it.
2. **Preregister the forward holdout today** (§7.2), naming both Alpha Core and
   a frozen %B-only variant as arms, with the multiple-comparison cost stated.
3. **Fix the four implementation defects** (§7.1 items 1–4) while the holdout
   accumulates. These are the reason a +28%/yr gross decile edge produced a
   bottom-third portfolio.
4. **Start recording a weekly point-in-time universe snapshot.** Nothing else
   ever fixes survivorship, and every result above is inflated by an unmeasured
   amount until it exists.
5. **Do not evaluate the holdout before 2027-08**, and evaluate it once.

---

## Provenance

| Item | Value |
|---|---|
| Experiment ID | ALPHA-IC-DIAG-2026-08-18 |
| Module | `research_alpha_ic.py` (new, read-only; no existing file modified) |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
| Data snapshot | `factor_panel.csv`, 89,861 rows, 2008-12-04 → 2026-07-12 |
| Seed | 20260818 (200 permutations per date) |
| Python | 3.14.7 · numpy 2.5.1 · pandas 3.0.5 · *scipy absent — Spearman computed directly on percentile ranks, which is exact* |
| Backtests run | **None.** No holdout was evaluated. |
| Parameters tuned | **None.** |

### Artifacts

| File | Contents |
|---|---|
| `alpha_core_rank_ic.json` | Full results, protocol, decision rule |
| `rank_ic.csv` | 1,710 rows — per (date, signal, horizon) IC, deciles, hit ratio |
| `alpha_core_ic_summary.csv` | Full-sample summary per signal/horizon |
| `alpha_core_ic_stability.csv` | Sub-period breakdown |
| `alpha_core_rank_ic_report.md` | This report |
