# EXPERIMENT 15 — EXECUTION-AWARE ALPHA VALIDATION

**2026-08-18 · preregistration sha256 `840af9be…` · Alpha Core frozen**

---

## 1. Executive Verdict

# FAIL

Four of six gates fail. The two that pass do so **meaninglessly** — they compare
one loss-making arm against a worse one — and are treated as failures in
substance (§11).

The experiment produced a clean, decisive answer:

> **The execution filter works. It raises the fill rate from 84.9% to 96.0% and
> cuts queue exposure from 5.01× the universe rate to 1.53×. In exchange the
> information coefficient falls 71% at one session and turns *negative* at every
> longer horizon.**

You can have the fills or the signal. Not both.

---

## 2. Research Question

> Can an execution-aware filter, using only decision-time information, convert
> the Alpha Core signal into a positive net edge after realistic costs?

**No.** The filter removes exactly the names that carry the signal, because
those are the same names.

---

## 3. Previous Failure Mechanism

Experiment 14 established that Alpha Core's short-horizon signal is a
**price-limit queue detector**. At matched score, sector, liquidity and
volatility, queue-flagged names beat non-queue names by +0.99% in one session
(t = 8.29); the non-queue residual was +0.12% at t = 1.09.

### The prediction was recorded before this experiment ran

E14's `ex_t1_unbuyable` filter used **future** queue status — an oracle no live
system can have. It left an h=1 excess of **+0.397%** against a **1.039%** round
trip. Since no decision-time filter can outperform an oracle, 0.397% was the
**ceiling** for Experiment 15, and it already sat below cost.

The preregistration states: *"A decision-time filter is expected to fail. This is
recorded before running so the outcome cannot be reinterpreted afterwards."*

**Measured outcome: Arm B h=1 gross excess +0.1467%** — well below even the
oracle bound, as expected.

---

## 4. Implementation Audit

Confirmed unchanged from `production_pipeline_audit.md`:

| Stage | Location |
|---|---|
| Alpha Core scores | `backtesting/alpha_core.py:264` `alpha_scores()` |
| Ranking primitive | `alpha_core.py:255` `_percentile()` |
| Selection | `engine.py:606-616` (top N), `portfolio_engine.allocate()` |
| Order creation | `engine.py:628-670` |
| Fill simulation | `backtesting/execution.py` — queues, participation, partials |
| Costs | `backtesting/portfolio.py:253/302` |

**Nothing in this chain was modified.** The filter is a new layer between
ranking and portfolio construction, and the score it receives is byte-identical
to Arm A's.

---

## 5. Experimental Design

| Arm | Selection | Universe | Exposure |
|---|---|---|---|
| **A** | Alpha Core top-10 | unfiltered | constant, 100% of sleeve |
| **B** | Alpha Core top-10 | **filtered** | identical |
| **C** | Random top-10, 30 seeds | **filtered** (same as B) | identical |

120 decision dates, 2014-09-21 → 2024-09-01. Horizons 1/5/10/20 sessions. The
retired exposure engine takes no part. Post-2024 holdout untouched.

**Unfilled slots earn the policy cash rate**, not a substitute name. Letting the
book re-form around the eleventh pick would flatter the result by handing it a
replacement a real unfilled order never gets.

---

## 6. Execution Filter Definition

Five rules, all reading session t or earlier. **No fitted parameters.**

| Rule | Excludes a name if | Universe blocked |
|---|---|---:|
| Locked | it closed at the band on session t | 2.7% |
| At high | it closed at its high with a positive move on t | 2.6% |
| Volume spike | session-t volume > 3× its own trailing 60-session median | 18.8% |
| Thin | ADV in the bottom quintile of the date's cross-section | 20.1% |
| Stale | it did not trade on session t | 15.6% |

**56.9% of the universe survives.** No rule references a future return, price,
volume, queue state or fill.

---

## 7. Queue Artifact Analysis — Before vs After

| | Arm A | Arm B | Change |
|---|---:|---:|---|
| Selected-book lock rate ÷ universe rate | **5.01×** | **1.53×** | **−69%** |
| Fill rate | 84.9% | **96.0%** | +11.1pp |

**The filter did substantially what it was designed to do.** Queue exposure fell
by more than two-thirds and the book became nearly fully fillable.

> **Gate G1 failed on a technicality: the pre-registered threshold was ≤1.5×
> and the measured value is 1.53×.** That is a miss of 0.03. Reporting it as a
> pass would be moving a threshold after seeing the number, so it stands as a
> fail — but the honest reading is that the filter *did* remove most of the
> artifact. That makes what follows more damning, not less.

### And the alpha went with it

| Horizon | Arm A IC | Arm B IC | Change |
|---:|---:|---:|---|
| 1 | 0.10492 | **0.03033** | **−71%** |
| 5 | 0.05101 | **−0.02248** | **sign flip** |
| 10 | 0.03003 | **−0.03424** | **sign flip** |
| 20 | 0.01456 | **−0.03245** | **sign flip** |

Removing queue exposure does not leave a smaller edge. It leaves a **negative**
one at every horizon the system could trade.

**This is the direct confirmation of Experiment 14 using only decision-time
information.** E14 reached it with an oracle; E15 reaches it with a filter a
live system could actually run.

---

## 8. Horizon Analysis

| Arm | h | Mean IC | ICIR | Gross excess | Net/period | **Net/yr** | Turnover/yr |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 1 | 0.10492 | 0.805 | +0.6395% | −0.3363% | **−55.45%** | 225.2 |
| A | 5 | 0.05101 | 0.382 | +1.0040% | +0.0281% | **+1.36%** | 45.0 |
| A | 10 | 0.03003 | 0.233 | +0.9218% | −0.0541% | −1.29% | 22.5 |
| A | 20 | 0.01456 | 0.113 | +0.5930% | −0.3829% | −4.50% | 11.3 |
| **B** | 1 | 0.03033 | 0.225 | +0.1467% | −0.8222% | **−86.21%** | 223.6 |
| **B** | 5 | −0.02248 | −0.155 | +0.0307% | −0.9382% | −36.39% | 44.7 |
| **B** | 10 | −0.03424 | −0.241 | +0.1119% | −0.8571% | −18.66% | 22.4 |
| **B** | 20 | −0.03245 | −0.239 | **−0.4481%** | −1.4170% | −15.74% | 11.2 |

**There is exactly one positive cell in the entire table**: Arm A at h=5,
**+1.36%/yr** — earned by an unfiltered book with a 5.01× queue exposure and a
15.1% unfill rate, against a **23%** policy cash rate. In real terms, against
~40% inflation, it is a catastrophic loss.

`Signal Decay < Trading Friction` is never satisfied.

---

## 9. Performance Results — the execution shortfall

This table is the clearest statement of the entire programme.

| Arm | h | **Requested** excess | **Realised** excess | Shortfall | Lost to execution |
|---|---:|---:|---:|---:|---:|
| A | 1 | +1.2275% | +0.6395% | +0.5880% | **48%** |
| A | 5 | +2.6245% | +1.0040% | +1.6206% | **62%** |
| A | 10 | +3.0204% | +0.9218% | +2.0986% | **69%** |
| **A** | **20** | **+3.1735%** | **+0.5930%** | **+2.5805%** | **81%** |
| B | 1 | +0.3146% | +0.1467% | +0.1679% | 53% |
| B | 5 | +0.4827% | +0.0307% | +0.4520% | 94% |
| B | 10 | +0.6408% | +0.1119% | +0.5289% | 83% |
| B | 20 | +0.2669% | **−0.4481%** | +0.7150% | — |

> **The unfiltered book requests +3.17% at the traded horizon and receives
> +0.59%. It loses 81% of its own alpha to execution.**
>
> **The filtered book, which fixes the execution, only ever requests +0.27%.**

That is the whole result in two sentences. Alpha Core can request alpha it
cannot obtain, or obtain alpha that is not there.

---

## 10. Cost Attribution

| | Arm A | Arm B |
|---|---:|---:|
| Round trip | 1.039% | 1.039% |
| Cost per period | 0.9759% | 0.9689% |
| Turnover, h=20 | 11.3/yr | 11.2/yr |
| Turnover, h=1 | 225.2/yr | 223.6/yr |

Costs are nearly identical across arms — the filter does not reduce trading, it
changes *which* names are traded. At h=1 the book turns over ~94% per session,
so the 1.039% round trip is paid ~225 times a year. **Cost is not what
distinguishes the arms; the vanished signal is.**

Slippage was held at **zero** deliberately. A failure at zero slippage is a
failure at any slippage.

---

## 11. Statistical Tests

Block bootstrap (5,000 resamples, block size 5) on the paired per-period
difference **B − C**:

| Horizon | Mean | 95% CI | Excludes zero |
|---:|---:|---|:--:|
| 1 | +0.3574 | [+0.1266, +0.5935] | ✅ |
| 5 | +0.5823 | [−0.0306, +1.2182] | ❌ |
| 10 | +0.7330 | [−0.1504, +1.6916] | ❌ |
| 20 | −0.0760 | [−1.6041, +1.5340] | ❌ |

Arm B vs the 30-seed random distribution on net annualised return:

| Horizon | Arm B | C mean | C p95 | B > p95 |
|---:|---:|---:|---:|:--:|
| 1 | −86.21% | −94.27% | −93.10% | ✅ |
| 5 | −36.39% | −49.58% | −40.96% | ✅ |
| 10 | −18.66% | −29.43% | −19.24% | ✅ |
| 20 | −15.74% | −16.37% | −9.77% | ❌ |

### Why G3 and G5 "passing" means nothing

Both gates are satisfied only in the sense that **Arm B loses less money than a
random control that loses more.** At h=1, Arm B beats the random p95 while
returning **−86.21% annualised**. At h=20 — the only horizon with a plausible
cost structure — it fails both.

Reporting "2 of 6 gates passed" as partial encouragement would be dishonest.
Neither gate provides evidence of a tradable edge; they provide evidence that
Alpha Core's ranking is not *literally* random. That was never in question — E6
established it at z = +8.3 — and it is not sufficient for deployment.

---

## 12. Gate Summary

| Gate | Result | Evidence |
|---|:--:|---|
| **G1 — artifact removal** | **FAIL** | Queue ratio 5.01× → 1.53× against a ≤1.5× threshold. Missed by 0.03; substantively the filter worked |
| **G2 — net alpha** | **FAIL** | Arm B is worse than Arm A at **every** horizon |
| **G3 — random separation** | *pass, meaningless* | B beats C p95 at 3 of 4 horizons — while losing 19–86%/yr |
| **G4 — cost survival** | **FAIL** | Net alpha negative at **every** horizon |
| **G5 — statistical** | *pass, meaningless* | Only h=1 CI excludes zero, at −86%/yr |
| **G6 — robustness** | **FAIL** | Zero horizons satisfy G2 and G4 together; required 2 |

**Substantive result: 0 of 6.**

---

## 13. Final Decision

# ARCHIVE SIRAT-AI TRADING SPECIFICATION

The programme has now tested every remaining hypothesis and each has closed:

| Component | Status | Closed by |
|---|---|---|
| Exposure engine | **RETIRED** | E4 — failed OOS, 1 of 8 gates |
| Alpha Core v1 | **REJECTED** | E11 — 3.3rd percentile, recoverable fraction −10.8% |
| Signal mechanism | **QUEUE ARTIFACT** | E14 — 3.03× queue exposure, non-queue residual t = 1.09 |
| Execution-aware rescue | **FAILED** | E15 — filter removes the artifact *and* the alpha |

**There is no remaining untested path within this specification.** The signal
was located, its mechanism identified, and the one proposed remedy tested and
falsified with a filter that a live system could actually run.

### What was proven, and it is worth stating

This is a **complete causal account**, not an unexplained failure:

1. Alpha Core carries real cross-sectional information (z = +8.3 vs a
   permutation null).
2. That information is concentrated at **one session** and decays 23× by twenty.
3. Its content is **queue formation** — identifying stocks about to become
   unbuyable.
4. Trading it therefore requires buying what cannot be bought: 81% of the
   requested alpha is lost to execution at the traded horizon.
5. Filtering out the unbuyable names removes the information with them, leaving
   a **negative** IC at every horizon.

### What must not happen next

- Do not tune the filter thresholds to clear G1's 1.53× — the alpha is already
  negative at 1.53×, and it would be more negative at 1.4×.
- Do not add ML. The problem is not functional form; it is that the information
  is queue formation.
- Do not revisit the exposure engine.
- Do not spend the untouched post-2024-09-01 holdout on any of this.

### What retains value

- The **data pipeline**, the **PIT machinery**, the **execution simulator** and
  the **microstructure detectors** built in E14–E15 are sound and reusable.
- The **experiment registry** and the preregistration discipline are the
  programme's most transferable output.
- The finding itself: *in a market with hard daily price bands, momentum and
  band-proximity factors measure queue formation rather than expected return.*
  That is a genuine, publishable microstructure result about the Tehran
  exchange, and it will save the next specification from repeating this.

---

## Provenance

| Item | Value |
|---|---|
| Experiment ID | EXP15-EXECUTION-AWARE-2026-08-18 |
| Preregistration | `experiment15_preregistration.json`, sha256 `840af9bebff79f6be1794dd82e39b6c8` |
| Module | `experiment_15_execution_aware.py` (new) |
| Alpha Core | **Unmodified** — factors, weights, formula, ranking, universe and selection all frozen |
| Filter | 5 rules, zero fitted parameters, no future data |
| Slippage | 0.0% (deliberately optimistic) |
| Holdout | **Not started, not burned** |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |

### Artifacts

`backtest_output/experiment15_execution_aware.json` ·
`experiment15_preregistration.json` · `experiment15_execution_aware_report.md`
