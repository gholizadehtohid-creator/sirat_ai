# SIRAT-AI — What Has Been Built, What The Research Says, What To Do Next

**2026-08-22. This document is a report and a decision menu. No trading logic
was changed to produce it, no database was written, Alpha Core is untouched at
`90024d86d7a5c268`.**

It answers three questions in order:

1. What has this platform actually accomplished?
2. What have investors and researchers actually done successfully in 2024–2026,
   and how much of it survives contact with this market?
3. Which options are open, what does each cost, and which would I pick?

---

# PART 1 — WHERE THE PLATFORM STANDS

## 1.1 What was built

| Layer | State |
|---|---|
| Data acquisition | TSETMC + Codal, self-fetching, VPN-aware source binding |
| Backtesting engine | execution model, price bands, queue detection, PIT accessors |
| Research governance | Experiment Manager: registration, preregistration, 6 mandatory stages, hash-chained ledger |
| Reproducibility | git-backed, `reproducible=True`, archive tag `v2.0-archive` |
| Regression defence | Alpha Core fixture, 11 numeric anchors, fails the build on drift |
| Tests | 943, all green |

## 1.2 What was learned, and it is not a happy result

Twenty experiments ran. The programme's central finding is negative and it is
**solid**, which is worth more than a fragile positive one:

**E19 — Alpha Core rejected.** 7 of 11 preregistered gates fail. At the
declared horizon h=20: IC 0.01456, t=1.232, holdout IC **−0.02449** (sign
flipped out of sample), 5 of 10 folds positive, net **−0.3829%/period**, max
drawdown −36.39%. Nominal p = 0.22 — insignificant *before* any
multiple-testing correction, so multiple testing is not the excuse.

**E20 — the mechanism identified.** The information is real but sits where
trading is impossible. At a 5% participation cap, names **excluded** carry
IC **+0.17066 (t = 7.14)**; names **admitted** carry **−0.01293 (t = −0.99)**.
The capacity curve is negative at *every* level from 50% down to 0.25%. There
is no tradable region. Verdict: **Case B — a research signal, not a trading
signal.**

**The one number that governs everything that follows:** measured turnover
capacity is **~4.45 round-trips per year**. Alpha Core's viable horizon implied
24–48. That gap is not a tuning problem. It is the market.

## 1.3 Data assets, measured 2026-08-20

| Table | Rows | Span | Dates | Entities |
|---|---:|---|---:|---:|
| `price_history` | 1,914,641 | 2001-03 → 2026-08 | 6,094 | 818 |
| `client_type_history` | 1,681,938 | 2008-11 → 2026-08 | 4,202 | 780 |
| `index_history` | 7,253 | 2008-12 → 2026-08 | 4,266 | 2 |
| `codal_letters` | 37,383 | — | — | 792 |
| `codal_financials` | 2,194 | — | — | 764 |

**Two absences decide what is buildable.** There is **no order-book table** —
no depth, no bid/ask history, no intraday ticks. `market_snapshot` holds
`limit_up`/`limit_down`/`state` but is keyed on `ins_code` alone, so it is
overwritten every pull and has **no history**. And **no ML library** is
installed anywhere in 160 Python files.

**The largest untouched asset, now measured properly.** The roadmap recorded
`client_type_history` as "1.68M rows, never once used". Row count does not
prove a cross-sectional factor is buildable, so I measured the *width*:

| Era | Dates | Median names/date |
|---|---:|---:|
| 2008–2013 | 1,112 | **2** |
| 2014–2018 | 1,208 | **339** |
| 2019–2022 | 960 | **533** |
| 2023–2026 | 805 | **653** |

Unusable before 2014, wide after: ~2,970 dates × 339–653 names, joinable to
prices on 1,453,648 rows (75.9%), with **0% of rows missing the per-participant
count columns**. Usable start date is **2014-01-01** — and that boundary must
be declared in a preregistration before anyone looks at a result, or the start
date silently becomes a tuned parameter.

**The fundamental panel is worse than recorded.** Of 764 companies with parsed
financials the median is 3 filings and **zero companies reach 16**. Criterion 3
of the fundamental gate cannot be rescued by universe selection.

---

# PART 2 — WHAT THE RESEARCH FOUND

## 2.1 Honest note on the evidence quality

The research run was **degraded and did not complete cleanly**. 104 claims were
extracted from 21 sources; only 25 received verification votes before the
account hit its weekly limit, and the synthesis step never ran. 35 of 105
agents errored.

More importantly, **the refutation votes are not trustworthy**. I independently
re-fetched one claim the workflow marked refuted 0-3 — the square-root impact
law with c_raw = 0.69 and c_eff = 0.34 — and confirmed it is exactly what the
paper reports, including that "a direct model comparison decisively prefers the
square-root form over linear and logarithmic impact." So below, the refuted
bucket is reported as **unresolved**, never as false. Confidence is labelled on
every item.

## 2.2 Findings that hold up (independently confirmed or 3-0 verified)

**F1 — Gross-of-cost factor evaluation is mechanically fictitious.**
*(AFA-published; 3-0)* A deliberately sabotaged model — monthly reconstitution,
small-cap-only size leg, universe restricted to the costliest-to-trade half —
raised the gross squared Sharpe ratio as much as fifty years of asset-pricing
research did. Its **net** Sh² collapsed 95% to 0.18, identical to the CAPM.
Only the market factor remained profitable to trade after costs.
→ **This is the most important methodological finding in the whole run**, and
SIRAT-AI already does the right thing: costs are charged at Stage 3, before
robustness.

**F2 — The square-root impact law is confirmed, but cannot be calibrated here.**
*(arXiv 2606.24019; verified by me directly)* I = c·σ_D·(Q/V_D)^½ with
c_raw = 0.69 [0.63, 0.77], bias-corrected **c_eff = 0.34**. But the
confirmation consumed a full Level-3 tape — 178 days, ~0.5 billion book events
— to reconstruct just 1,012 metaorders. **The formula is importable with
published parameters; the local calibration is not reproducible** from daily
bars plus aggregated client-type counts.

**F3 — Crypto HFT execution results do not transfer.** *(3-0)* The evidence
base is a Binance perpetual venue with a **−0.5 bp maker rebate**, 1.5 bp taker
fee, negligible tick size, and ~11-second holding periods. Against a 1.04%
round-trip cost and 4.45 turns/year, only the qualitative adverse-selection
*mechanism* transfers — never the strategies or the sub-basis-point edges.

**F4 — Fill-probability modelling from queue dynamics is a live 2024–2026
method** *(2-0)* — and it needs message-level book state: queue sizes, spread,
arrival and cancellation intensities. **Not implementable from daily bars.**

**F5 — TSE-native microstructure evidence is thin and stale.** *(3-0)* The one
Persian LOB-predictability study rests on a single Persian year (1391 ≈
2012-13) and **10 randomly selected firms**. It establishes nothing about the
current regime or a 1,400-instrument universe.

## 2.3 Findings that are decision-relevant but unresolved

Reported with the evidence, flagged as unconfirmed. **None of these may be
acted on as fact without its own verification step.**

**U1 — The Iranian TSE factor literature shows strong publication-bias
signatures.** A meta-analysis pooling **89 studies, 746 effect sizes** reports
a mean effect size of **r = 0.289**. That is an implausibly large average
cross-sectional correlation for equity return predictors. Treat the domestic
literature as a prior on bias, not as a source of factors. *(2018 publication,
pre-2015 studies — outside the window, useful only as a base rate.)*

**U2 — In that pooled evidence the imported factor stack does not survive.**
P/E and book-to-market, firm value, size, age (p > 0.14), beta (p = 0.074),
liquidity ratios (p = 0.426), leverage (p = 0.29), activity ratios (p = 0.15),
accruals (p = 0.153) — none statistically associated with TSE returns.

**U3 — What did associate is accounting quality, which we cannot supply.**
Net earnings r = 0.322, profit margin r = 0.389, ROE r = 0.151, market return
r = 0.292, operating cash flow r = 0.232. Every one but market return needs a
PIT fundamental panel with restatement and publication-lag handling — exactly
what the median-3-filings panel cannot support.

**U4 — Price limits impose a measured liquidity cost on TSE itself.** An event
study of **893 price-limit events across 80 companies (1392–1396)** found
liquidity *declined* in the post-event window. The limit mechanism degrades
tradability rather than merely pausing it.

**U5 — حقوقی flow predicts limit-lock formation.** On the TSE, institutional
trading contributes more to the magnet effect — price accelerating toward the
دامنه نوسان band — than individual trading, **in 23 of 24 sampled companies**.
This is the direct argument that `client_type_history` is a candidate predictor
of صف خرید/صف فروش formation, not merely a sentiment proxy.

**U6 — Momentum's published premium is largely a cost artifact, and throttling
turnover partially recovers it.** Gross 0.64%/month, costs 63 bps/month, net
**0.01%/month**. Cost-aware partial rebalancing cuts costs to 30 bps and lifts
net to 0.23%/month — a 22-fold increase, but **t = 1.40, still insignificant**.

**U7 — Partial rebalancing dominates at very low trading intensity.** Every
candidate factor model achieved a higher net-of-cost Sh² than its fully
rebalanced version at a monthly trading intensity of only **6.9%**, with the
net optimum between 20% and 30% monthly.

**U8 — Limit-lock incidence is a function of band width, not just volatility.**
China's ChiNext widening from ±10% to ±20% cut upper-limit events 38.7% and
lower-limit events 68.4%. Lock rates are therefore *not* an exogenous market
property in a backtest.

**U9 — Naive order-flow-imbalance strategies lost money live** in the crypto
study: −1.96 bp/trade taker, −0.47 bp maker, one variant losing ~60% of capital
in a week at Sharpe −109. Order-book imbalance predicts direction on average
and still loses money once fill selection and fees apply. *(Same venue as F3,
so magnitudes do not transfer — but the mechanism is a standing warning against
Option 1 being framed as a return signal.)*

## 2.4 The reframing this research delivers

Put F1, U6 and U7 beside SIRAT-AI's own measured capacity of 4.45 round-trips
per year and the picture inverts:

> The 2023–2026 cost-aware literature independently concludes that **only very
> low turnover survives transaction costs**. SIRAT-AI has been treating its
> low capacity as the obstacle. The literature treats low turnover as the
> answer.

Alpha Core failed at 24–48 turns/year against a capacity of 4.45. That is not
evidence that this market is untradable. It is evidence that the programme
tested the wrong holding regime. **Nothing here rescues Alpha Core** — E19 and
E20 stand, and a low-turnover retest of Alpha Core would be exactly the
forbidden move. It means the *next* signal should be designed for the capacity
that exists, rather than discovered and then squeezed into it.

---

# PART 3 — THE OPTIONS

Ranked. Every one enters as a registered, preregistered experiment or it does
not enter.

### Option 1 — Flow-based limit-lock prediction  ⭐ recommended first

**Question:** *will this name be unbuyable tomorrow?* — not *will it go up?*

| | |
|---|---|
| Evidence | U5 (حقوقی drives the magnet effect, 23/24 companies), U4, U8 |
| Data | exists today: 2014+, 339–653 names/date, 1.45M joinable rows, 0% missing counts |
| New dependencies | none |
| New data acquisition | none |
| Turnover required | **none — it is a filter, not a strategy** |
| Cost | 1–2 weeks |
| Failure mode | "no predictive power" → a cheap Stage 1–2 rejection |

**Why it is first:** it is *measurement, not prediction*, so it structurally
cannot produce a false alpha discovery — the failure mode that killed Phase 1.
It uses the single largest untouched asset in the platform. It feeds the Stage 1
feasibility gate, which is what makes Stage 1 quantitative instead of
rhetorical. And it is the cheapest thing on this list.

**Hard constraint:** it must be registered as a *feasibility* signal. The
moment it is framed as "حقوقی flow predicts returns" it becomes a return
prediction and U9 becomes the expected outcome.

### Option 2 — Import a slippage model, close a known falsity

**Slippage has been 0.0% in every result this programme has ever produced.**
Every backtest in the ledger is therefore optimistic by an unmeasured amount.

| | |
|---|---|
| Evidence | F2 (verified: square-root form, c_eff = 0.34), F5, U4 |
| What | `impact.py` — published square-root form, **published** prefactor, declared as imported |
| Limit-locked names | modelled as non-executable inventory carried forward, not as fills |
| Cost | 3–5 days |
| Risk | **medium** — the prefactor is calibrated on AAPL. Using it on TSE is an assumption, and must be swept for sensitivity, not asserted |

**Why:** it corrects every future result and re-scores the past ones. It cannot
create an edge; it can only remove a fictitious one, which is the right
direction for a tool to be wrong in.

### Option 3 — Cost-charged, turnover-throttled construction

| | |
|---|---|
| Evidence | F1 (3-0), U6, U7 |
| What | make net-of-cost primary everywhere; add partial rebalancing as a first-class construction variant matched to 4.45 turns/year |
| Cost | ~1 week |
| Upside | highest on this list — it is the layer the literature says decides everything |

**⚠ Governance warning, and it is not a formality.** This sits one step away
from *"optimize turnover after seeing results"*, which is explicitly forbidden
by Mission 4 and Mission 5. It is legitimate **only** if:

- it is applied to a **new** signal, never to Alpha Core;
- the turnover schedule is preregistered from the **measured capacity (4.45)**,
  not chosen by trying values;
- E19's and E20's verdicts are not reopened under any result it produces.

If those three cannot be held, this option should not be taken at all.

### Option 4 — Unblock the fundamental panel (acquisition only, no research)

| | |
|---|---|
| Evidence | U2, U3 — only profitability and cash flow associate with TSE returns, and both need PIT depth |
| What | codal pagination fix + letter-type filter, crawl back to 1394 |
| Cost | ~1 week of work plus crawl time |
| Blocked until | 24 quarters, 150 companies/date, median 16 filings — currently ~4, ~100, **3** |

**Forbidden:** constructing any fundamental factor on 1402–1405 data. Including
"just to see". Including marked provisional. A provisional finding is a finding
the moment someone quotes it.

### Option 5 — Quantify survivorship  ⚑ flagged for your decision

The instrument list is **today's** list. There are no universe snapshots before
2026, so every historical result carries an unquantified survivorship bias —
including E19 and E20. Snapshots now run forward, but the past cannot be
recovered from a source that only serves the present.

This is the highest-integrity item on the list and I do not know yet whether it
is even feasible — it depends on whether delisted instruments remain
retrievable from TSETMC. Worth a half-day of investigation before committing.

## What is explicitly NOT an option

| Not an option | Why, in one line |
|---|---|
| ML ranking | 5 of 5 preconditions fail; the binding one is ~120 decision dates against 250 required, and no library choice changes that number |
| Intraday / order-book microstructure | F4 needs message-level state; no depth is stored and **depth cannot be backfilled** — only collected forward |
| Any fundamental factor on 1402–1405 | 0 of 764 companies reach the required filing depth |
| Anything that reopens Alpha Core | E19 and E20 are adjudicated; a low-turnover retest is the textbook forbidden move |
| Imported value/size/low-beta/accruals stack | U2 — none of them survive in the pooled TSE evidence |

## Recommended sequence

1. **Option 1** — cheapest, safest, largest unused asset, cannot produce a false discovery
2. **Option 2** — in parallel; corrects a known falsity in every result
3. **Option 5** — half a day of feasibility investigation, then decide
4. **Option 4** — background crawl; it is waiting on calendar time, so start the clock early
5. **Option 3** — last, and only under the three governance conditions above

---

## Provenance

| | |
|---|---|
| research run | `wf_53189079-1e8` — **degraded**: 104 claims, 25 verified, synthesis failed on account limit |
| independently re-verified by me | F2 (square-root law prefactors) |
| platform inventory | 2026-08-20, read-only, `mode=ro` on every database |
| code changed | **none** |
| Alpha Core | untouched, `90024d86d7a5c268` |
| decision | **yours** — nothing in Part 3 has been started |
