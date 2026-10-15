# E24 — INST_NET_RATIO AT THE CORRECTED SIGN: REJECTED AT STAGE 2

**2026-08-23. Successor (b) from E21's verdict. Preregistration
`87b995fae3e862c7…` frozen before any number below existed; ledger entry 16,
chain head `1178c5434df8b0fe…`, intact. Decision REJECT, failing
`factor_decay`. Alpha Core untouched at `90024d86d7a5c268`.**

---

## 1. What this experiment was, and was not

E21 preregistered `inst_net_ratio` at **+1** on the magnet-effect argument and
measured it at **−0.08707 (t −60.22)** — the opposite direction, strongly. It
recorded the directional claim FALSIFIED and refused to flip the sign after
seeing the result; the corrected sign belonged in a successor preregistration.
This is that successor.

| | |
|---|---|
| component | `inst_net_ratio` alone — no composite, no weights, no fitting |
| sign | **−1**, declared in advance with the mechanism: in a صف خرید the حقوقی side *supplies* the queue rather than forming it |
| window | discovery 2014-01-01 .. 2023-02-28 only |
| OOS claim | **none possible, declared at registration** — E19 and E21 spent every historical holdout and data ends 2026-08-18. The forward test belongs to `R-FORWARD-2027`, named and NOT claimed |
| provenance honesty | the direction was learned from E21's diagnostics on this same window. Sign confirmation was therefore near-assured and is the least-weighted part of the record. What was genuinely unmeasured: the null placement, the corrected-sign decile lift, decay retention, and the already-locked exclusion — Stage 2 gates on exactly those |
| panel | E21's artifact reused byte-identical (sha256 on record), then decontaminated by a **preregistered** rule |
| seed | 20260823 |

**Why the label E23 is skipped:** it is attached to the sell-queue feasibility
scan (`E23_FEASIBILITY.md`) that was rejected before registration and never
issued an id. Reusing it would blur a scan with a registered experiment.

## 2. The preregistered decontamination — E22's lesson, applied forward

E22's cost checks were inverted by 252 impossible rows and were deliberately
not repaired after the verdict. Here the repair was declared **before**
measurement, citing the platform's own ceiling
(`IMPOSSIBLE_SESSION_MOVE_PCT = 100.0`, `sirat/experiments/gates.py`):

> drop BOTH legs of every per-name close-to-close transition with |return| >
> 100%, iterate to a fixed point; a forward target that would read a dropped
> session becomes **unknown (NaN), never False**.

Applied exactly as declared: **104,188 rows dropped (5.50%) across 323 names,
3,040 dates, 1,448 iterations**. Worth recording for successors: around an
irreconcilable price-level break the both-legs iteration walks outward and
consumes real rows on *both* sides equally — blunter than identifying the fake
side. The rule was not refined mid-experiment; a successor should preregister
a placeholder-level test instead.

Stage 1 then **PASSED on the cleaned panel**: forward alignment 100% of 1,996
resolved samples; snapshot `u20260819-668658bb` restored and hash-verified;
median 419 names/date (floor 100); coverage 78.53% (floor 60%); `price_sanity`
— the check added after E22 — **0 impossible sessions remaining**, with 6,735
legitimate >12% reopenings retained.

## 3. Stage 2 — three gates passed, the fourth decided

Discovery window, 2,137 scored dates, 930,054 rows:

| Gate | Floor | Measured | |
|---|---|---:|---|
| `ic` | ≥ 0.05 | **0.08193** | PASS |
| `rank_correlation` | t ≥ 3.0 | **56.28** | PASS |
| `random_baseline` | > 95th pct | **100.00th** (null p95 0.00161) | PASS |
| `factor_decay` | lift ≥ 2.0× and retention ≥ 0.30 | lift **2.407×**, retention **0.206** | **FAIL** |

The decay profile is the finding:

| Horizon | top-decile rate | universe rate | lift |
|---|---:|---:|---:|
| h=1 | 9.525% | 3.957% | **2.407×** |
| h=2 | 8.418% | 5.637% | 1.493× |
| h=3 | 7.520% | 5.829% | **1.290×** |
| h=5 | 6.760% | 6.143% | 1.100× |

> **Institutional net selling is a one-session warning.** By h=3 only 20.6% of
> the h=1 excess concentration survives, against a preregistered floor of 30%.
> A feasibility filter has to survive the lag between computing a signal after
> the close and acting on it, and this component does not.

**The preregistered diagnostic** (reported, not gating): excluding names
already `locked_up` at t — 96.2% of rows survive — still measures IC 0.05374
(t 43.47), lift 1.994×. Roughly seven-tenths of the excess lift survives, so
this is not merely lock persistence; but the usable concentration sits at the
2× line even before decay is charged.

## 4. What is settled, what is not

**Settled:** `inst_net_ratio` at **−1** is real, correctly-signed, highly
significant *ordering* information with a **one-session horizon**, now on the
record the legitimate way. Any future composite preregistration may cite this
entry for the corrected sign. As a standalone feasibility filter it fails the
preregistered decay bar: **REJECT**.

**Not touched:** E21's composite result stands — it passed *despite* this
component at the wrong sign, and `indiv_power` (IC 0.16855, lift 4.349×)
remains the strongest carrier. E19, E20, E22 remain adjudicated. No window was
spent; `R-FORWARD-2027` remains unclaimed.

**Successor order, updated:** (a) impact model — **done**,
`backtesting/impact.py`; (b) corrected-sign preregistration — **done, this
record**; (c) whether a flow filter improves realised vs requested returns in
front of a basket with a real edge — still blocked on a validated return
signal, which this programme does not have (E19/E20).

## 5. Provenance

| | |
|---|---|
| preregistration | `87b995fae3e862c72210361c6205bcdef44d9c567ed36dd613f5d5767802b512`, frozen 2026-08-23 before measurement |
| git commit | `9bd62b6`, **dirty tree — `reproducible=False` recorded at registration**; PROMOTE was never in scope |
| panel | `backtest_output/E21_flow_lock_panel.csv.gz` (sha256 in `E21_flow_lock_panel.sha256`), read-only reuse |
| stage evidence | `backtest_output/E24_stage_results.json` |
| ledger | entry 16, head `1178c5434df8b0fe…`, chain intact |
| scripts | `examples/e24_preregister.py`, `examples/e24_stages.py`, `examples/e24_decision.py` |
| windows read | discovery 2014-01-01..2023-02-28 only; spent holdouts not read, not even for context |
