# SIRAT-AI — Holdout Validation of the Exposure Engine

**Independent backtesting audit · executed 2026-08-18 · holdout evaluated once**

Protocol pre-registered in `holdout_preregistration.json` before any holdout
number was computed. Raw results in `holdout_validation.json`.

---

## 1. Executive Verdict

# FAIL

The exposure engine does not survive its holdout. Over 469 unseen sessions it
returned **42.24% CAGR against the exposure-matched control's 43.31%** — it lost
to a static portfolio that uses no signals at all. It was riskier on every risk
measure: maximum drawdown −14.87% against −10.75%, Calmar 2.85 against 4.04,
downside deviation 6.87% against 4.86%, Sharpe 1.39 against 1.77. The only
statistically significant finding in the entire holdout is that **the engine's
drawdown is deeper than the control's** (bootstrap 95% CI on ΔMaxDD
[−9.33, −1.16], excluding zero). Its gross tilt was worth +1.38pp of CAGR and
its incremental trading cost was 2.45pp, so costs turned a marginal gross edge
into a net loss. Out-of-sample it captured **more downside (64.0%) than upside
(52.7%)** — the exact inverse of a defensive overlay — with beta 0.656 above its
own 59.6% average exposure, confirming the pro-cyclical mechanism the in-sample
review suspected. Higher exposure preceded *lower* subsequent market returns
(ρ = −0.126). Seven of eight pre-registered gates fail; the eighth, research
validity, passes, which means **this is a clean negative result and not an
infrastructure artifact**. The in-sample Sharpe of 1.49 does not replicate.

---

## 2. Data Audit

| Item | Finding |
|---|---|
| Holdout first session | 2024-09-03 |
| Holdout last session | 2026-08-17 |
| Sessions | 469 (1.95 years) |
| Tradable/valid sessions | 469 — no missing, no invalid |
| Duplicate dates | 0 |
| Non-positive closes | 0 |
| Longest session gap | 7 days (no gap > 10 days) |
| Decision dates | 23 (2024-09-10 … 2026-07-12) |
| Decision spacing | median 29 days (min 28, max 39) |
| Breadth names per date | min 749, median 776, max 785 |
| Dates below the 100-name breadth floor | 0 |
| Warm-up history before holdout | 2,517 EW sessions (engine needs 261) |

**Verified independently.** The brief's stated ~469 sessions and factor data to
~2026-07-12 are both correct.

**Two disclosures.**

1. The final decision is 2026-07-12 but sessions run to 2026-08-17, so the last
   25 sessions carry no new decision — exposure simply drifts. This is faithful
   to live operation. It happens to **help** the engine: that final block saw
   the benchmark rise 24.38% with the engine at 75%, contributing +4.45pp of
   excess. Without it the result would be worse.
2. 59 of 469 sessions (12.6%) show an unchanged index close. This affects all
   arms identically and does not bias the comparison.

**Point-in-time status: verified.** `market_exposure.py` reads only
`closes[:searchsorted(date, 'right')]`, a slice ending at the decision session.
The breadth input `ma_gap_pct` is taken at
`searchsorted(date,'right') - 1` — the last bar at or before the decision date —
from causal rolling indicators, and carries a staleness guard
(`max_stale_sessions`) that drops symbols with stale prices. The sleeve is a
real contemporaneous index, so it carries no survivorship bias; the breadth
universe is drawn from the current instrument list, whose measured attrition is
1.7% per decade.

---

## 3. Experimental Design

| Arm | Construction |
|---|---|
| **A — Engine** | `market_exposure.exposure_for(history, above_pct=breadth)` at each of the 23 decision dates. Unmodified. Output on the 0/25/50/75/100 ladder. |
| **B — Fixed matched** (PRIMARY CONTROL) | Static 55.02% target rebalanced on the *identical* decision grid. |
| **C — Buy & hold** (secondary reference only) | 100% invested from session one. |
| Cash | Idle fraction earns (1.23)^(1/240)−1 per session |
| Cost | 1.04% round trip on |target − drifted weight| at each rebalance |
| Timing | Return credited **before** rebalance, so a decision at a close first earns the next session (T+1) |

**How the control's exposure was fixed, and why it is not look-ahead.**
55.02% is the engine's realised average exposure over the *training* window
(2014-09-21 … 2024-09-01), read from `exposure_experiment.json`. It was fully
known on 2024-09-01. Matching the control to the engine's *holdout* exposure
would let the holdout choose its own benchmark; that variant was computed but is
labelled and used by no gate.

**Robustness of that choice.** The engine ran hotter out-of-sample than in
training — 59.78% mean target against 55.02% — so the pre-specified control is
*generous* to the engine. Against the look-ahead control handed the engine's own
59.56% holdout exposure, the engine still loses on every metric: CAGR 42.24% vs
44.93%, Sharpe 1.39 vs 1.76, MaxDD −14.87% vs −12.03%, Calmar 2.85 vs 3.74. The
failure is not an artifact of the matching level.

---

## 4. Main Results

| Metric | A — Engine | B — Fixed matched | C — Buy & hold | Cash |
|---|---:|---:|---:|---:|
| CAGR | 42.24% | **43.31%** | 60.58% | 22.95% |
| Total return | 99.09% | 102.01% | 152.32% | 49.73% |
| Volatility | 10.91% | 8.93% | 15.97% | 0.00% |
| Sharpe (23% hurdle) | 1.39 | **1.77** | 1.76 | — |
| Sortino | 2.21 | **2.91** | 2.87 | — |
| Max drawdown | −14.87% | **−10.75%** | −22.88% | 0.00% |
| Calmar | 2.85 | **4.04** | 2.65 | — |
| Average drawdown | −6.62% | **−4.16%** | −9.33% | 0.00% |
| Sessions underwater | 78.7% | 74.8% | 80.8% | 0% |
| Longest underwater run | 138 | 106 | 136 | 0 |
| Recovery from trough | 80 sessions | **39 sessions** | 69 sessions | — |
| Downside deviation | 6.87% | **4.86%** | 8.98% | 0.00% |
| Average exposure | 59.56% | 54.81% | 99.79% | 0% |
| Median / min / max exposure | 53.6 / 0 / 100% | 55.0 / 0 / 60.3% | 100 / 0 / 100% | 0% |
| Turnover per year | 2.12 | **0.48** | 0.51 | 0.00 |
| Rebalances | 23 | 23 | 1 | 1 |
| Cumulative cost drag | 4.30% | **0.98%** | 1.04% | 0% |
| Beta to sleeve | 0.656 | 0.558 | 1.000 | 0.000 |
| Correlation to sleeve | 0.960 | 0.997 | 1.000 | — |

TEDPIX (cap-weighted reference, no cost): 179.62% total, 69.25% CAGR.

**Engine minus control: −1.07pp CAGR, −0.38 Sharpe, −4.12pp max drawdown,
−1.19 Calmar.** The engine is worse on every single line.

---

## 5. Statistical Tests

### Sharpe difference — Jobson–Korkie with Memmel correction

| Comparison | Sharpe A | Sharpe B | ΔSharpe | ρ | z | p | 95% CI on ΔSharpe |
|---|---:|---:|---:|---:|---:|---:|---|
| **Engine vs fixed matched (primary)** | 1.393 | 1.764 | **−0.371** | 0.9646 | −1.852 | **0.0641** | [−0.764, +0.021] |
| Engine vs buy & hold (secondary) | 1.393 | 1.755 | −0.362 | 0.9600 | −1.769 | 0.0769 | [−0.763, +0.039] |

The primary difference is negative and falls just short of significance at 5% —
that is, the engine is *nearly significantly worse* than doing nothing.

### Stationary block bootstrap

Politis–Romano paired resampling, geometric blocks of mean length 20 sessions,
5,000 replicates, seed 20240901. Paired so cross-correlation survives.

| Statistic | Point estimate | 95% CI | Excludes zero | Replicates with engine ahead |
|---|---:|---|:--:|---:|
| Δ Sharpe | −0.385 | [−0.965, +0.191] | no | 10.0% |
| Δ CAGR (pp) | −0.651 | [−10.571, +13.641] | no | 41.1% |
| **Δ Max drawdown (pp)** | **−4.313** | **[−9.327, −1.160]** | **yes** | 0.2% |

The one statistically credible result in this holdout is that the engine's
drawdown is deeper. In 99.8% of resamples the engine's drawdown is worse.

### Exposure versus subsequent market return

Spearman ρ between target exposure at a decision date and the benchmark return
over the *following* block: **ρ = −0.126, t = −0.59, p = 0.559 (n = 23)**.
No predictive relationship; the point estimate has the wrong sign.

**Power caveat, stated plainly.** 23 decisions over 1.95 years cannot *confirm*
a modest positive effect. But gates G1, G2, G6 and G7 fail on the **sign** of
the point estimate, not on significance. The holdout is underpowered to prove
the engine works; it is not underpowered against what actually happened.

---

## 6. Episode Analysis

Episodes are maximal contiguous runs of the benchmark trend state, where trend
state at a decision date is the 60-session benchmark return > 0 — a threshold
already frozen in `market_exposure.py`, using only information available at the
decision date. Volatility state uses the same frozen 20-session-vs-240-median
rule.

| Trend | From | To | Blocks | Benchmark | Engine | Control | Excess | Mean target |
|---|---|---|---:|---:|---:|---:|---:|---:|
| down | 2024-09-10 | 2024-11-06 | 2 | −1.82% | +0.01% | +0.60% | −0.59 | 37.5% |
| up | 2024-11-06 | 2025-04-12 | 5 | +24.76% | +16.52% | +17.90% | −1.38 | 65.0% |
| down | 2025-04-12 | 2025-05-10 | 1 | +16.46% | +8.86% | +9.80% | −0.94 | 50.0% |
| **up** | **2025-05-10** | **2025-08-09** | **3** | **−17.77%** | **−12.78%** | **−7.86%** | **−4.92** | **75.0%** |
| down | 2025-08-09 | 2025-10-07 | 2 | +6.57% | +4.02% | +5.19% | −1.17 | 25.0% |
| up | 2025-10-07 | 2026-03-04 | 5 | +13.97% | +14.95% | +12.15% | +2.80 | 75.0% |
| down | 2026-03-04 | 2026-06-08 | 3 | +24.71% | +15.16% | +16.11% | −0.95 | 41.7% |
| up | 2026-06-08 | 2026-08-17 | 2 | +40.11% | +30.57% | +23.17% | +7.41 | 75.0% |

### Regime cells

| Cell | Blocks | Sessions | Benchmark | Engine | Control | Excess | Mean target |
|---|---:|---:|---:|---:|---:|---:|---:|
| up / calm | 6 | 120 | +20.20% | +15.81% | +16.83% | −1.02 | 79.2% |
| up / stressed | 9 | 184 | +36.29% | +31.72% | +28.44% | +3.27 | 66.7% |
| down / calm | 7 | 140 | +51.96% | +29.06% | +33.86% | −4.79 | 39.3% |
| down / stressed | 1 | 20 | 0.00% | +1.04% | +0.78% | +0.26 | 25.0% |

The `down / calm` cell is the most damaging: across 7 blocks the benchmark rose
**+51.96%** while the engine sat at a mean target of 39.3% and gave up 4.79pp to
the control. The engine's trend and momentum votes read "down" through a
half-doubling of the market.

### Leave-one-episode-out

Full-sample excess: **−2.92pp**.

| Episode removed | Excess without it | Sign reversed |
|---|---:|:--:|
| down 2024-09-10…2024-11-06 | −1.75 | no |
| up 2024-11-06…2025-04-12 | −0.48 | no |
| down 2025-04-12…2025-05-10 | −1.08 | no |
| **up 2025-05-10…2025-08-09** | **+9.04** | **YES** |
| down 2025-08-09…2025-10-07 | −0.64 | no |
| up 2025-10-07…2026-03-04 | −6.92 | no |
| down 2026-03-04…2026-06-08 | −1.09 | no |
| up 2026-06-08…2026-08-17 | −11.52 | no |

**Is the result broad-based or concentrated?** The *deficit* is concentrated:
removing the single drawdown episode of the holdout flips −2.92pp to +9.04pp.
G5 was written on the assumption that the engine would win, and asks whether an
advantage is episode-dependent. There is no advantage. The substantive finding
is worse than the gate's mechanical trigger: **the engine's single worst
relative episode is the one drawdown in the holdout — precisely the event a
defensive overlay exists to handle.**

### The failure mechanism, located

| Decision | Exposure | trend | momentum | breadth | volatility | Breadth reading | Next block benchmark |
|---|---:|---|---|---|---|---:|---:|
| 2025-05-10 | 75% | True | True | True | False | 71.8% | −1.78% |
| 2025-06-08 | 75% | True | True | **False** | True | **47.4%** | −11.42% |
| 2025-07-12 | 75% | True | True | **False** | True | **7.3%** | −5.49% |
| 2025-08-09 | 25% | False | False | False | True | 6.8% | +0.75% |

On 2025-07-12 only **7.3% of the market** was above its own slow mean. Breadth —
the single fast signal — voted no. It was outvoted 3-to-1 by trend, momentum and
volatility, all of which are slow, and the engine held **75%** into a −5.49%
move after already holding 75% through −11.42%. It cut to 25% on 2025-08-09,
*after* the fall was over, and was then underweight for the +0.75% and +5.78%
blocks that followed.

This is a lagging-indicator failure produced by the design itself: an unweighted
vote lets three slow trend-followers override the one signal that had already
detected the collapse.

---

## 7. Year-by-Year Results

| Year | Engine | Control | Excess | Buy & hold | Engine max DD |
|---|---:|---:|---:|---:|---:|
| 2024 (partial, from 09-03) | +16.99% | +17.69% | −0.70 | +28.40% | −2.69% |
| 2025 (full) | +23.08% | +27.49% | −4.41 | +29.58% | −14.87% |
| 2026 (partial, to 08-17) | +39.01% | +35.10% | +3.91 | +52.81% | −10.97% |

- Years beaten: **1 of 3 (33%)** — one full year, two partial; too few to gate on, which is why G6 uses inter-decision blocks.
- Largest annual win: **+3.91pp** (2026, partial)
- Largest annual underperformance: **−4.41pp** (2025, the only full year)
- The engine lost to the control in the only complete calendar year available.

### Consistency at the pre-registered unit

Engine ahead in **11 of 23 inter-decision blocks (47.8%)** — worse than a coin
flip. Rule was >50%, stated before results.

---

## 8. Exposure Analysis

| Measure | Engine |
|---|---:|
| Mean target exposure | 59.78% |
| Mean realised exposure | 59.56% |
| Median realised | 53.58% |
| Min / max realised | 0.0% / 100.0% |
| Training-window mean | 55.02% (ran **4.5pp hotter** out-of-sample) |

Ladder usage: 25% on 4 decisions (17%), 50% on 8 (35%), 75% on 9 (39%),
100% on 2 (9%). The engine never called 0%.

### Target versus realised

Realised (59.56%) tracks target (59.78%) to within 0.22pp. **Unlike Experiment
3, there is no execution shortfall here** — this harness applies the target to an
index sleeve directly, so the allocator defect that crippled the stock-selection
experiment is absent. The engine got the exposure it asked for and still lost.

### Is it defensive or pro-cyclical?

| Test | Result | Reading |
|---|---|---|
| Beta vs average exposure | 0.656 vs 0.596 | **beta exceeds exposure → pro-cyclical** |
| Capture, block frequency | up 52.7% / **down 64.0%** | captures more downside than upside |
| Capture, control | up 47.5% / down 46.2% | symmetric, as a constant weight should be |
| Capture, daily compound | up 40.0% / down 76.5% | same direction, convexity-distorted |
| Exposure → next-block return | ρ = −0.126, p = 0.559 | no predictive value, wrong sign |
| Overweight blocks (n=11) | mean benchmark +3.14%, mean excess +0.48pp | |
| Underweight blocks (n=12) | mean benchmark **+5.82%**, mean excess −0.48pp | **underweight in the better periods** |

Block-frequency capture is the primary measure; daily compounding makes subset
capture convex and understates any de-risked arm, so both are reported.

**Conclusion: the engine reduces exposure after markets have already fallen.**
It is not a defensive overlay. It is a lagged pro-cyclical tilt that was
systematically underweight during the stronger periods and overweight into the
one drawdown.

---

## 9. Transaction Cost Analysis

| | Engine | Control | Difference |
|---|---:|---:|---:|
| Gross CAGR | 45.41% | 44.03% | **+1.38pp gross alpha** |
| Cost drag | −3.17pp/yr | −0.72pp/yr | **−2.45pp incremental** |
| **Net CAGR** | **42.24%** | **43.31%** | **−1.07pp net alpha** |
| Turnover per year | 2.12 | 0.48 | 4.4× |
| Cumulative cost | 4.30% of NAV | 0.98% of NAV | |
| Total traded | 4.13 × NAV | 0.95 × NAV | |
| Mean cost per rebalance | 18.7 bps | 4.3 bps | |

```
Gross alpha      +1.38 pp
Trading costs    −2.45 pp
------------------------
Net alpha        −1.07 pp
```

The engine's timing produced a small positive gross tilt. Costs consumed
**178%** of it. Even if execution were free, +1.38pp of gross alpha against
+9.4pp of extra drawdown and −0.38 of Sharpe would not be a trade worth making.

---

## 10. Failure / Validity Audit

| Check | Result | Evidence |
|---|---|---|
| Cash accounting | **PASS** | Independent ledger tracking equity and cash separately reproduces `ex1.simulate()` to max abs difference **4.0×10⁻¹⁵**; turnover agrees to 10⁻⁹; **0 NAV-identity breaches** across 469 sessions × 4 arms. Idle cash credited (1.23)^(1/240)−1 every session. |
| The known `backtest_portfolio.py` cash defect | **Does not affect this experiment** | That defect (idle cash uncredited while a 23% hurdle is charged) lives in the *stock-selection* harness used by Experiment 3. This experiment uses `experiment_exposure.simulate()`. Verified by inspection **before** running. **Not fixed, not touched.** |
| Hurdle application | **PASS** | Sharpe and Sortino are hurdle-adjusted (excess over daily-compounded 23%); CAGR is raw geometric; Calmar is raw CAGR / \|MaxDD\|, *not* hurdle-adjusted. Applied identically to all arms, so comparisons are valid. Documented rather than changed. |
| Execution timing | **PASS** | Return credited before rebalance — a decision at close *t* first earns session *t+1*. No same-session look-ahead. |
| Point-in-time inputs | **PASS** | Engine reads `closes[:searchsorted(date,'right')]`. Breadth read at the last bar ≤ decision date from causal indicators, with a staleness guard. |
| Look-ahead in control construction | **PASS** | Control fixed at 55.02%, the training-window average, known on 2024-09-01. Holdout-matched variant labelled and used by no gate. |
| Survivorship | **PASS (low)** | Sleeve is a real contemporaneous index. Breadth universe attrition 1.7%/decade. |
| Missing / duplicate / invalid data | **PASS** | 0 duplicates, 0 non-positive closes, no gaps > 10 days. |
| Stale factors | **PASS** | `max_stale_sessions` guard drops stale symbols from the panel. |

**No defect materially affects this result.** G8 passes, which is what makes
this a clean negative rather than an infrastructure artifact.

---

## 11. Gate Results

| Gate | Result | Evidence |
|---|---|---|
| **G1** Positive net alpha | **FAIL** | Engine 42.24% vs control 43.31% → net alpha **−1.07pp** |
| **G2** Risk improvement | **FAIL** | MaxDD −14.87% vs −10.75% (needed ≤ −9.68%); Calmar 2.85 vs 4.04 (needed ≥ 4.24); downside dev 6.87% vs 4.86% (needed ≤ 4.37%). All three fail, and all three fail in the *wrong direction* |
| **G3** Statistical credibility | **FAIL** | ΔSharpe −0.371, z −1.852, **p = 0.0641**; bootstrap CI95 [−0.965, +0.191] includes zero |
| **G4** Cost survival | **FAIL** | Gross alpha +1.38pp, incremental cost 2.45pp, net alpha −1.07pp. Costs consumed 178% of gross alpha |
| **G5** Episode independence | **FAIL** | Full-sample excess −2.92pp; removing the 2025-05-10…2025-08-09 drawdown episode reverses it to +9.04pp. The deficit is concentrated in the one event the engine exists to handle |
| **G6** Consistency | **FAIL** | Engine ahead in 11 of 23 blocks (47.8%); pre-specified rule was >50% |
| **G7** Economic interpretation | **FAIL** | Beta 0.656 > average exposure 0.596 → **pro-cyclical**. Block capture: upside 52.7%, downside 64.0%. Exposure→forward-return ρ −0.126 (p 0.559). The defensive-overlay claim is falsified out-of-sample |
| **G8** Research validity | **PASS** | Ledger reproduces harness to 4.0×10⁻¹⁵; 0 NAV breaches; idle cash credited; PIT verified; T+1 verified; real index sleeve |

**1 of 8 gates passed.**

---

## 12. Final Research Conclusion

> **Does the existing Exposure Engine demonstrate robust incremental value over a
> correctly exposure-matched passive control in unseen data?**

**No.**

It does not merely fail to demonstrate value — it is measurably *worse* than the
control on return, on every risk measure, and on consistency, while charging 4.4×
the turnover for the privilege. The in-sample Sharpe advantage of 1.49 vs 1.43
reverses out-of-sample to 1.39 vs 1.77.

The in-sample review's suspicion is confirmed and sharpened. In-sample the engine
looked like a mild upside-participation effect concentrated in 2019–2020;
out-of-sample the same mechanism shows its cost. The engine is a lagged
pro-cyclical tilt: three slow trend signals outvote the one fast breadth signal,
so it holds maximum exposure into drawdowns and de-risks after them. That is not
a tuning problem, and it cannot be fixed by re-weighting the votes without
abandoning the unfitted design that was the engine's only defensible property.

The single robust, statistically significant out-of-sample finding is that the
engine **increases** drawdown relative to doing nothing (bootstrap CI on ΔMaxDD
excludes zero; 99.8% of resamples agree).

**Two honest limits on this conclusion.** The holdout is 1.95 years and 23
decisions — underpowered to confirm a modest positive effect, though not
underpowered against the negative signs actually observed. And a single holdout
is one draw; the correct reading is that the engine has now failed its only
out-of-sample test, not that its failure is proven for all time.

---

## 13. Recommended Next Action

# RETIRE EXPOSURE ENGINE

Not "freeze and collect more data": the mechanism is understood, adverse, and
structural rather than statistical. Collecting more data would test a rule whose
failure mode has already been located — it holds peak exposure into drawdowns
because slow votes outnumber the fast one.

Not "proceed to next research phase": nothing in this result supports building
on the engine.

Not "invalid — fix infrastructure first": G8 passed. The infrastructure is
sound and the negative result is real.

**Concrete steps.**

1. Retire `market_exposure.py` from the product critical path. Keep the file and
   these artifacts as the record of a correctly-run falsification.
2. Withdraw both remaining claims — "reduces downside risk" and "improves
   risk-adjusted outcomes" — from all material. Both are now falsified
   out-of-sample, not merely unproven.
3. Do **not** re-weight the votes, add a faster signal, change the ladder, or
   apply machine learning to this target. Any such change is fitted to a failure
   now known, and the holdout is spent.
4. The one genuine asset the programme retains is the pre-registered,
   frozen-protocol research method — which is what produced a trustworthy
   negative result in two working days. That method is worth more than the
   engine it just killed.
5. Separately, and independently of this decision: the `backtest_portfolio.py`
   idle-cash defect remains unfixed and will corrupt any future experiment run
   through that harness.

---

## Artifacts

| File | Contents |
|---|---|
| `holdout_preregistration.json` | Protocol and gates, written before results |
| `holdout_validation.json` | Full raw results |
| `holdout_statistics.json` | All tests, bootstrap, capture ratios |
| `holdout_daily.csv` | 469 sessions: NAV, exposure, cash %, returns per arm |
| `holdout_trade_ledger.csv` | 46 rebalances with notional, cost, weights |
| `holdout_blocks.csv` | 23 inter-decision blocks |
| `holdout_episodes.csv` | 8 trend episodes |
| `holdout_annual.csv` | Calendar-year results |
| `holdout_decisions.csv` | 23 decisions with all four votes |
| `holdout_provenance.json` | Environment, config, file and data snapshot |
| `holdout_report.md` | This report |

**Provenance note:** `c:\SIRAT-AI` is **not a git repository**, so no commit
hash can be recorded. File mtimes and sizes in `holdout_provenance.json` are the
only provenance available. Bootstrap seed 20240901, 5,000 replicates — the run
is deterministic and reproducible. Python 3.14.7, numpy 2.5.1, pandas 3.0.5.
