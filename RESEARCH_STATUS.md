# SIRAT-AI — RESEARCH STATUS

**E19: Alpha Core tradable edge — final adjudication. 2026-08-19.**

Preregistered `9fd80d29…` before any number below was computed.
Decided by the Experiment Manager, not by the analyst.

---

# 1. RESEARCH STATUS — executive summary

| Question | Answer |
|---|---|
| **Is Alpha Core predictive?** | **At h=1, overwhelmingly — and it is an artifact.** IC 0.10492, t = 8.78. E14 established the mechanism: the top decile is 3.03× more likely to be limit-locked at the moment it would be bought. **At h=20, the horizon it is traded at, no.** IC 0.01456, t = 1.23. |
| **Is the predictive edge statistically credible?** | **No.** Nominal two-sided p = 0.22 at the declared horizon — not significant before any multiplicity correction, so no correction can rescue it. |
| **Does it survive OOS?** | **No.** The holdout IC is **negative**: −0.02449 (t = −1.02). The sign flipped out of sample. |
| **Does it survive walk-forward?** | **No.** 5 of 10 sequential out-of-fold blocks positive — a coin flip against a 60% floor. |
| **Does it beat nulls?** | **Not all of them.** 100th percentile against unconstrained random ranking, but **93.3rd against the liquidity-matched null**, below the 95th required. A null allowed to hold the same illiquid names does nearly as well. |
| **Does it survive execution?** | **No.** The book requests +3.1735% and receives +0.5930% — **81.3% of the alpha is lost to execution** at a fill rate of 84.92% and a basket queue ratio of 5.01×. |
| **Does it survive costs?** | **No.** Net −0.3829%/period (−4.50%/yr). Negative under **every** perturbation, including a *favourable* cost of 0.75×. |
| **Is it robust?** | **Not applicable — it is uniformly negative.** There is no fragility question when every scenario sits on the wrong side of zero. |
| **Ready for paper trading?** | **No.** |
| **Ready for real capital?** | **NOT READY FOR LIVE CAPITAL.** |

### The one-sentence finding

> Alpha Core's only statistically overwhelming result is at a horizon where the
> signal identifies stocks that are about to become unbuyable; at the horizon it
> can actually be traded, there is no result at all.

**Outcome: B — NO ROBUST EDGE.**

---

# 2. GATE TABLE

**7 of 11 mandatory gates FAIL.** Thresholds frozen in
`backtest_output/E19_preregistration.json` before the audit ran.

| Gate | Result | Metric | Threshold | Observed | Evidence |
|---|---|---|---|---|---|
| **G1** Data integrity | **PASS** | PIT, restorable universe, regression anchors | all pass | snapshot `u20260819-668658bb` restorable; 11/11 anchors | `research_manifest.json` |
| **G2** Reproducibility | **PASS** | regenerates from commit/code/config/data/seed | `reproducible=True` | HEAD `c498824`, clean, panel `e895b169…`, seed 20260819 | `research_manifest.json` |
| **G3** Predictive edge | **FAIL** | IC / ICIR / %pos at declared h=20 | ≥0.02, ≥0.30, ≥55% | **0.01456** (t 1.232), **0.1125**, **51.67%** | `E19_edge_audit.json` |
| **G4** Out-of-sample | **FAIL** | holdout IC, degradation | >0 and ≤0.50 | **−0.02449** (t −1.021), degradation **2.19** | phase3.out_of_sample |
| **G5** Walk-forward | **FAIL** | positive out-of-fold share | ≥60% | **5 of 10 = 50%** | phase3.walk_forward |
| **G6** Null tests | **FAIL** | percentile vs **every** matched null | ≥95th | worst **93.33** (liquidity-matched) | phase4_nulls |
| **G7** Execution realism | **FAIL** | net per period after execution + cost | >0 | **−0.3829%** (−4.50%/yr) | phase5_execution |
| **G8** Cost robustness | **FAIL** | net at cost ×1.25 + slippage | >0 | **−0.6269%**, −0.8145%; even ×0.75 → **−0.1389%** | phase9_robustness |
| **G9** Exposure | **PASS** | gap ≤10pp **or** fully attributed | either | gap 25.57pp, attributed ~100% to slot occupancy | E7 / E11 |
| **G10** Drawdown / risk | **FAIL** | maxDD ≤35%, not single-regime | both | **−36.39%**; positive only in BEAR + SIDEWAYS | phase5, phase10 |
| **G11** Research integrity | **PASS** | no undisclosed post-hoc selection | frozen record | prereg `9fd80d29` predates the audit; Alpha Core `90024d86` unchanged | phase8 |

**Manager verdict:** REJECT at **Stage 2 — ECONOMIC SIGNAL VALIDATION**, failing
`ic`, `rank_correlation`, `random_baseline`, `factor_decay`. Cheap rejection.
Ledger entry `6a761c7ee07fc608…`.

---

# 3. EXPERIMENT LEDGER

12 entries, hash chain **intact**, head `…`. Full record in
[RESEARCH_LEDGER.md](RESEARCH_LEDGER.md); machine-readable in
`backtest_output/research_ledger.json`.

| # | Experiment | Stage | Verdict | Repro |
|---|---|---|---|---|
| 1–11 | E1–E15 replay (11 hypotheses) | 1 / 2 / 3 / 4 | REJECT ×11 | yes |
| **12** | **E19 Alpha Core tradable edge** | **2** | **REJECT** | **yes** |

E19's entry carries: hypothesis, author, `code_version` (strategy + code hash),
git commit, config hash, dataset fingerprint `90e30213b30e6e73`, universe
snapshot `u20260819-668658bb`, preregistration hash `9fd80d29…`, the full
six-stage validation evidence, and the final verdict.

**Promotion status: NONE PROMOTED.** Zero experiments in this repository have
ever reached PROMOTE.

---

# 4. FAILURE MAP

Each failed gate classified. **None is an infrastructure defect.**

| Gate | Exact failure | Root cause | Class | Recoverable without changing the question? |
|---|---|---|---|---|
| **G3** | IC 0.01456 vs 0.02; ICIR 0.1125 vs 0.30 | The signal decays with a fitted half-life of 11.36 sessions and is traded at 20. Only **13.9%** of peak IC survives to the traded horizon. | **5 — genuine strategy failure** | **No.** Trading it faster is a different strategy and requires 24–48 turnovers/yr against a measured capacity of 4.45. |
| **G4** | Holdout IC **−0.02449**, sign flipped | Year-by-year IC is negative in 2017, 2018, 2019, 2023 and 2024, and positive almost entirely in 2020–2022. The holdout landed outside the favourable block. | **5 — genuine strategy failure** | **No.** Moving the boundary to include 2020–2022 would be selecting the split after seeing results. |
| **G5** | 5 of 10 folds positive | Same cause as G4 — the edge is concentrated in one macro episode. | **5 — genuine strategy failure** | No. |
| **G6** | 93.33rd vs liquidity-matched null | **IC by liquidity quintile: +0.212 in the most illiquid, −0.030 in the most liquid.** The information lives where trading is hardest. A null permitted the same illiquid names captures most of it. | **5 — genuine strategy failure** | No. Restricting to liquid names removes the signal (E14: excluding locks *and* illiquid names turns h=20 IC significantly negative, −0.0249, t = −2.01). |
| **G7** | Net −0.3829%/period | Requests +3.1735%, receives +0.5930%. Basket queue ratio **5.01×**; fill rate 84.92%. | **5 — genuine strategy failure**, execution-mediated | **No.** E15 tested exactly this rescue: a decision-time filter cut queue exposure 5.01×→1.53× and cut IC at h=1 by 71%, flipping h=5/10/20 negative. The filter removes the artifact *and* the alpha. |
| **G8** | Negative under every cost scenario | The gross edge is smaller than the round trip it must pay. | **5 — genuine strategy failure** | No. Cost is a market fact, and reducing it in the model would be changing an assumption to obtain a pass. |
| **G10** | maxDD −36.39% vs 35% | Drawdown on a book whose net edge is already negative. | **5 — genuine strategy failure** | No. |

**Classification summary: 7 genuine strategy failures, 0 infrastructure, 0 data,
0 governance, 0 research-design.** Nothing was fixed and re-run; nothing needed
to be.

### Results that would have supported a pass, and are not promoted

Stated explicitly because omitting them would be cherry-picking in reverse:

- **100th percentile** against unconstrained random ranking (30 arms).
- **h=5 net is marginally positive**, +0.0281%/period (+1.36%/yr).
- **Profitable in BEAR (+0.21%) and SIDEWAYS (+1.22%)** regimes; the loss is
  concentrated in BULL (−1.34%).
- Hit rate 68.33% — the top decile beats the cross-section on two dates in three.

None of these is the declared test. The declared test is h=20, all regimes,
against **every** matched null. Promoting any of them would be selecting a
result after seeing results, which is the specific failure this platform exists
to prevent. The h=5 figure in particular requires 48 turnovers/year against a
measured market capacity of 4.45.

---

# 5. FINAL RECOMMENDATION

# B — DO NOT PROCEED: NO ROBUST EDGE

Alpha Core does not possess a tradable edge at the horizon it is traded at. The
evidence is not marginal and not fragile: **seven of eleven preregistered gates
fail**, the out-of-sample IC is negative, and the net edge stays negative under
a cost assumption 25% *more favourable* than the real one.

**Do not proceed to paper trading. Do not connect real capital.**

### Why this is a successful research outcome

The programme reached this conclusion in one registered experiment, rejected at
**Stage 2** — hours of work — with a complete audit trail. Phase 1 needed
fifteen experiments and a year to reach the same place, and could not have
proved it afterwards.

The platform's KPI now reads **92% of rejections at stages 1–3**, against a 70%
target and a Phase 1 baseline of ~0%.

### Production readiness

Fourteen conditions were required. **NOT READY FOR LIVE CAPITAL** — missing:
OOS edge, walk-forward survival, null-test superiority, execution-realistic
profitability, cost robustness, acceptable drawdown, paper-trading stability,
operational monitoring, and an automatic kill switch. The nine governance and
integrity conditions (reproducibility, PIT, universe, audit logging, rollback)
are satisfied.

### What survives, and is worth keeping

The three findings are **transferable measurements**, not a strategy:

1. Predictive information on this market concentrates in illiquid, low-volatility
   names — IC +0.212 in the bottom ADV quintile, −0.030 in the top.
2. Price-limit queue formation is detectable and quantifiable, and a signal that
   predicts it will look like alpha until it is executed.
3. The gap between requested and realised return, **81.3% here**, is the single
   most informative diagnostic in this market and is measurable before any
   portfolio is built.

All three belong to the microstructure workstream in
[RESEARCH_ROADMAP.md](RESEARCH_ROADMAP.md), where the purpose is *"can this be
traded"* rather than *"will this go up"* — which is the framing that
structurally cannot produce a false discovery.

### What must not happen next

Alpha Core stays **frozen and hash-pinned**. It is a regression fixture. Any
attempt to recover it by changing the horizon, relaxing a threshold, dropping
the liquidity-matched null, moving the OOS boundary or selecting the BEAR regime
would be rescuing a failed experiment, and every one of those moves is now
blocked by a control that raises rather than a convention someone remembers.

---

## Provenance

| | |
|---|---|
| experiment | `E19-alpha-core-tradable-edge` |
| preregistration | `9fd80d297b6ec43632e742a9c529d84e810585de39cc5ec140889aa35665475f` |
| audit panel | `e895b169594452ca…` (66,623 rows, 120 dates, 807 instruments) |
| universe | `u20260819-668658bb` (1,394 instruments, restorable) |
| dataset | `90e30213b30e6e73` |
| Alpha Core | `90024d86d7a5c268` — **unchanged** |
| seed | 20260819 |
| tests | 943 / 943 |
| ledger | 12 entries, chain intact |
| artifacts | `E19_edge_audit.json`, `E19_gate_decision.json`, `research_manifest.json` |
