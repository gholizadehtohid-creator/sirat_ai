# Market Impact — closing a falsity that was in every result

**`backtesting/impact.py`, 2026-08-22. Landed in commit `ec5e21a` alongside
E21; this document is its record, written separately because that commit's
message covers E21 and not this.**

RESEARCH_OPTIONS.md Option 2.

---

## 1. What was wrong

**Slippage has been reported as 0.0% in every result this programme has ever
produced** — not because trades were free, but because nothing computed it.

A zero that means *"not measured"* is indistinguishable in a report from a zero
that means *"measured, and it was nothing"*, and the second reading is the one
every reader takes. Every backtest in the ledger is therefore optimistic by an
amount nobody has ever put a number on.

## 2. The model

    I / σ_D  =  c · √(Q / V_D)

| | |
|---|---|
| `I` | expected adverse price move over the execution |
| `σ_D` | the name's daily volatility |
| `Q / V_D` | order size as a fraction of daily volume — participation |
| `c` | dimensionless prefactor |

### The prefactor is imported, not calibrated, and that is the point

| | |
|---|---|
| `c_raw` | **0.69**, CI [0.63, 0.77] |
| `c_eff` | **0.34** — bias-corrected; the default here |

Both come from a 2026 confirmation on a U.S. large-cap that consumed a full
Level-3 market-by-order tape — 178 trading days, ~0.5 billion book events — to
reconstruct 1,012 metaorders. A direct model comparison there preferred the
square-root form over linear and logarithmic impact.

**Nothing in this repository can reproduce that calibration.** There is no
order-by-order feed, no aggressor sign, no mid-price — only daily bars and
aggregated client-type counts. So `PREFACTOR_EFFECTIVE` is a **borrowed
constant from a different market**, and every result using it must say so and
must sweep it. `sensitivity()` exists to make that cheap rather than optional.

## 3. Three refusals

The module's value is not the arithmetic — that is one square root. It is the
set of cases where it declines to produce a number.

**1. A limit-locked name gets no number at all.** The square-root law rests on
a diffusive, near-martingale price. A name pinned at its band has zero realised
variance and unfillable demand, so the precondition fails *by construction*
rather than approximately. `estimate` returns `executable=False` with a reason.
On this market, the distinction between *expensive* and *impossible* is the one
that decides everything.

**2. Outside the calibrated range, the answer is flagged.** The published bins
span roughly 0.6%–2.7% of daily volume, recovered from the reported
non-dimensional impacts of 0.026 and 0.056 at `c_eff = 0.34`. Beyond that the
functional form is extrapolated — reasonable, but labelled.

**3. Missing inputs are missing, not zero.** No volatility, no volume, or a
non-positive one, refuses. `daily_volatility_pct` returns `None` rather than
0.0 for a name with too little history — zero volatility would make a new
listing look like the safest name in the universe.

No refusal ever returns `0.0`. That is asserted directly in
`tests/test_impact.py`, because a refusal that degraded to zero would simply
recreate the original bug.

## 4. What it costs, at 3% daily volatility

| Participation | Impact (round trip) | + 1.04% commission | |
|---:|---:|---:|---|
| 0.5% | 0.1442% | 1.1842% | extrapolated |
| 1.0% | 0.2040% | 1.2440% | |
| 2.0% | 0.2885% | 1.3285% | |
| **5.0%** | **0.4562%** | **1.4962%** | extrapolated |
| 10.0% | 0.6451% | 1.6851% | extrapolated |

**At this platform's own participation cap the cost hurdle rises by 44%** —
from 1.04% to 1.496% round trip. That is the size of the assumption every
previous result in this repository was making silently.

## 5. A finding, pinned as a test

`config.MAX_ADV_PARTICIPATION_PCT` is **5%**. The published calibration reaches
**2.7%**. So *every* estimate at the platform's own cap is an extrapolation.

That is not a reason to lower the cap — the cap is a liquidity decision, not an
impact-model decision. It is a reason that any capacity claim at 5% has to
carry the extrapolation flag, and
`test_the_platform_cap_sits_above_the_calibrated_band` fails if this ever
quietly stops being true.

## 6. What this module deliberately does not do

- **It does not touch commissions.** `backtest_config.EXPECTED_ROUND_TRIP_PCT`
  owns those. Impact stacks on top.
- **Nothing imports it.** Switching slippage on must be a *measured delta*
  inside a registered experiment, not a silent revision of every result in the
  ledger. `test_importing_changes_no_existing_behaviour` scans the tree and
  fails if anyone wires it in without one.

## 7. What it unblocks

Stage 3 — EXECUTION REALISM — requires `slippage` as one of its five checks.
Until now that check could not be reported honestly, which is why E21 was
registered with a scope ending at Stage 2. With this in place, a successor
experiment can run Stage 3 for the first time in this programme.

The order that follows, from `E21_REPORT.md`:

1. ✅ this module
2. a successor experiment preregistering `inst_net_ratio` at **−1**, with the
   queue-supply mechanism stated in advance
3. only then: does the E21 filter improve realised versus requested returns,
   with impact charged?

## Provenance

| | |
|---|---|
| code | `backtesting/impact.py`, 292 lines, commit `ec5e21a` |
| tests | `tests/test_impact.py`, 24 tests |
| calibration | **imported**, not reproducible here |
| wired in | **no** — by design, and enforced by a test |
| Alpha Core | untouched, `90024d86d7a5c268` |
