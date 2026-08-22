# E23 — SELL-QUEUE ESCAPE SIGNAL: REJECTED BEFORE REGISTRATION

**2026-08-22. A feasibility scan, not an experiment. No experiment id was
issued and nothing was written to the ledger — deliberately, and §5 explains
why that is the honest choice rather than a gap.**

---

## 1. The idea, and why it looked better than E22

E22 rejected the buy-side filter, and its limitation was specific: the filter
bought *fills* in front of a **random** basket, whose expected excess over cash
is zero by construction. A filter that improves fills is worth nothing when the
fills are worth nothing.

The sell side has an asymmetry that removes exactly that objection:

> **Avoiding a loss is valuable regardless of whether the position had a
> positive expected return.**

E21 established that `client_type` flow predicts queue formation
(IC 0.13650, holdout 0.15280, lift 2.6×, survives a contamination audit at 99%
retention). E21 tested only the **buy** queue — a stock you cannot buy. The
mirror is the **sell** queue: a stock you are trapped inside.

The prize is real and large:

| Horizon | median return after a `locked_down` bar | all bars |
|---|---:|---:|
| h=1 | −1.99% | 0.00% |
| h=3 | −3.45% | 0.00% |
| h=5 | −4.24% | 0.00% |
| h=10 | −5.45% | 0.00% |

Client-type coverage on `locked_down` bars is **99.95%** — the data is present
almost exactly when it is needed.

## 2. The break-even, computed before anything was measured

An exit rule pays its cost on **every flagged position** and saves only on the
**true positives**. So:

    precision × avoided_loss  ≥  cost

    avoided loss (h=5)   4.24 pp
    cost                 1.04 pp commission + ~0.20 pp impact = 1.24 pp
    ------------------------------------------------------------
    precision required   ~29%

The base rate of a newly forming sell queue among sellable bars is **1.41%**.
So the rule needs a lift of roughly **20×**. E21's composite achieved 2.6× at
the decile.

## 3. The scan — seven decision-time states, all recorded

Restricted to bars that are **sellable at t** (traded, not already
`locked_down`), because precision among names you cannot sell is not precision.

| State at t | bars | precision | lift |
|---|---:|---:|---:|
| any sellable bar | 1,422,652 | 1.41% | 1.0× |
| **`closed_at_low`** | 14,498 | **12.41%** | **8.8×** |
| `locked_down` 2 of last 3 | 9,912 | 10.33% | 7.3× |
| `closed_at_low` & `vol_ratio` > 1 | 10,057 | 10.13% | 7.2× |
| `closed_at_low` & ret < −4% | 3,270 | 8.96% | 6.4× |
| `locked_down` yesterday | 18,510 | 7.91% | 5.6× |
| ret < −3% | 118,512 | 2.98% | 2.1× |

Best available: **12.41%** against **29%** required. Already short by a factor
of 2.3 — but that is not the finding.

## 4. The finding: the signal cannot be acted on

**Every one of these states is known only after the session closes.**
`close == low` is not established until the close, and TSETMC publishes
client-type data after the close. So the earliest possible action is **t+1**.

And if the lock arrives at t+1, t+1 is exactly the session in which you cannot
sell. Predicting t+1 is therefore useless — the same defect that ended E14 and
E15: *a signal that must be acted on in the instant it is computed is not a
signal.*

The executable design must predict **t+2** and sell at **t+1**:

| | |
|---|---:|
| flagged by `closed_at_low` at t | 14,498 |
| exit at t+1 succeeded | 87.59% |
| **of those, locked down at t+2** | **3.24%** |
| precision required | **~29%** |
| lift of the usable arm | 2.2× |

**Nine times short.**

### The mechanism, which is the part worth keeping

The 12.41% at t+1 and the 3.24% at t+2-among-sellable are **the same fact seen
twice**. Names that were going to lock mostly lock at **t+1** — and those are
precisely the ones you cannot escape. By t+2, the names you can still sell are
disproportionately the ones that were never going to lock.

> **The signal's precision is concentrated entirely in the cases where the exit
> is impossible.**

This is E20's mechanism in a new domain. E20 killed Alpha Core because its
information sat in names too illiquid to trade; here the information sits in
the *sessions* where the trade cannot be made. Generalised:

> **On a market with hard price limits, a warning about a limit event and the
> lockout itself arrive together.** That is market structure, not a modelling
> failure, and no functional form fixes it.

## 5. Why this is not in the ledger, and what that exposes

Registering E23 now and recording "rejected at Stage 1" would **misrepresent
the record**: the numbers above came *before* any registration. That is a
feasibility scan, not a preregistered experiment, and the ledger is for
experiments.

But this exposes something about the platform's own metrics. The KPI counts
*rejections at stages 1–3 among registered experiments*. It has **no place for
a hypothesis rejected before it was ever registered** — which is the cheapest
and best rejection there is, and the one the roadmap names as the measure of
success:

> *"A hypothesis rejected at Stage 1 in an afternoon, with an audit trail a
> third party could verify."*

This one took under an hour and produced an audit trail. It should count for
more than an experiment that was registered and then failed, and at present it
counts for nothing at all. **Recorded as a gap in the platform's metrics, not
as a reason to fake a registration.**

## 6. What was scanned, so the multiplicity is on the record

Seven states, listed in §3, plus two forward horizons (t+1, t+2). Had any state
cleared 29%, that multiplicity would have had to enter a preregistration
explicitly. None did, so the point is moot — but it is recorded rather than
forgotten, because a scan whose failures go unlisted is how a later success
gets overstated.

## Provenance

| | |
|---|---|
| scanned | 1,894,584 bars, 2014-01-01 .. 2026-08-18 |
| detector | E14's `annotate`, unchanged |
| experiment id | **none issued** |
| ledger | **untouched**, 15 entries |
| Alpha Core | untouched, `90024d86d7a5c268` |
