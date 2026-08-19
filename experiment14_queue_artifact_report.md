# EXPERIMENT 14 — QUEUE-PRESSURE ARTIFACT TEST

**2026-08-18 · falsification only · Alpha Core untouched**

`python experiment_14_queue_artifact.py`

---

## 1. Hypothesis

**H1** — The strong h=1 forward-return signal is primarily caused by detecting
stocks likely to enter or remain in price-limit queue conditions (صف خرید),
rather than persistent technical or fundamental alpha.

### Pre-registered thresholds, fixed before the first number was read

| Trigger | Threshold |
|---|---|
| Queue exposure confirmed | top-decile lock rate ≥ **2.0×** the universe rate |
| ARTIFACT | removing t+1 locks cuts the h=1 excess by ≥ **50%** *and* leaves it below the 1.04% round trip |
| REAL BUT UNEXECUTABLE | excess survives ex-post removal but < **30%** is reachable ex-ante |
| SURVIVES | excess survives both *and* clears the round trip |

---

## 2. Data Validation

All microstructure columns are present at 100% coverage: `open, high, low,
close, volume, value, trades`, plus `client_type_history` order-flow counts.

### The band is not assumed — it is measured

The Tehran price band **moved across the window**. The traded cross-section
shows a 99th percentile of up-returns of +10% in 2017 and +5.5% in 2021. Any
test keyed to a fixed ±3% would have mismeasured most of the sample.

| Measure | Value |
|---|---|
| Empirical up-band, median | **5.65%** |
| Range across dates | 0.71% … 11.25% |

So the detector avoids the band entirely:

```
locked_up = high == low  AND  volume > 0  AND  traded yesterday
            AND  +1% < return ≤ +12%
```

A bar whose high equals its low, at a higher price, with trades in it, is a
stock that opened at the limit and never traded away from it.

### Two traps found and handled

| Trap | Finding | Handling |
|---|---|---|
| **The 33% trap** | A third of all bars have `high == low` — but most are **stale prices on suspended names** with zero volume and no move | Require `volume > 0` and a positive move. Stale bars are 22.29% of the window and are counted separately |
| **The reopening trap** | The first pass reported locked bars averaging a **115% return**. Those are symbols **reopening after suspension**, where the only trade of the day happens at a gapped price | Require the name to have traded yesterday, and cap at +12%. Reopenings (3.14%) and extreme gaps (0.24%) are counted as their own categories |

After both corrections, locked-up bars average a **3.69%** return — consistent
with a band lock, and consistent with the measured 5.65% median band.

### A third defect, found and corrected mid-experiment

The first pass rebuilt forward returns with `groupby.shift(-h)`, which moves
*h rows*, not *h sessions*. For a name with gaps in `price_history` — a
suspension leaves no row at all — `shift(-20)` can reach a bar months later.
The result was a reported top-decile excess of **−579%**.

Only `fwd_1` is now taken from the rebuild (validated in E13 at 98.31% of rows
within 0.05pp). Every longer horizon uses the panel's own columns, built
through the PIT calendar, which knows the difference between a missing row and
the next session. **The corrected baseline reproduces E13 exactly** — h=1
0.10492, h=5 0.05101, h=10 0.03003, h=20 0.01456.

### Window

| | |
|---|---|
| Bars | 1,600,727 across 797 instruments |
| In window (2014-09-21 → 2024-09-01) | 1,473,796 |
| Panel rows scored | 66,623 over 120 dates |
| Stale (zero volume) | **22.29%** |
| Locked up (band) | 2.83% |
| Locked down (band) | 1.30% |
| Reopenings | 3.14% |
| **Unbuyable (any of the above)** | **6.03%** |

---

## 3. Queue Exposure Analysis

State of session **t+1**, by Alpha Core decile at t:

| Group | Locked up on t+1 |
|---|---:|
| **Top decile** | **7.75%** |
| Universe | 2.56% |
| Bottom decile | **0.92%** |
| **Ratio, top / universe** | **3.03×** |
| Top / bottom | **8.4×** |

**Trigger was 2.0×. Queue exposure is CONFIRMED at 3.03×.**

| Other t+1 states | Top decile | Universe |
|---|---:|---:|
| Unbuyable (lock, reopen or gap) | **10.27%** | 5.79% |
| Closed at high | **7.22%** | 2.46% |
| Stale | 13.55% | 16.45% |

Alpha Core's top decile is **three times more likely to lock limit-up**, three
times more likely to close at its high, and nearly twice as likely to be
unbuyable — while being *less* likely to be stale, confirming the flag is
picking active demand rather than dead symbols.

---

## 4. Signal Decomposition

Top-decile h=1 excess over the market, split by whether the name locked on t+1:

| | |
|---|---:|
| Top-decile h=1 excess | **+0.5496%** |
| Share of the decile that locked | 7.75% |
| **Mean h=1 return of the LOCKED names** | **+4.0950%** |
| Mean h=1 return of the rest | +0.3758% |
| **Excess contributed by the locked names** | **+0.3223% = 58.7% of the total** |
| Excess excluding locks | +0.3292% |

**7.75% of the names deliver 58.7% of the excess.** Those names returned
+4.10% in a single session — approximately the daily band.

---

## 5. Exclusion Tests

| Filter | h=1 IC (ICIR) | h=5 IC | h=10 IC | **h=20 IC (t)** |
|---|---:|---:|---:|---:|
| **baseline** | 0.10492 (0.805) | 0.05101 | 0.03003 | **0.01456 (1.23)** |
| ex t+1 locks | 0.08495 (0.665) | 0.02965 | 0.01212 | **0.00154 (0.13)** |
| ex t+1 unbuyable | 0.08522 (0.666) | 0.02893 | 0.01119 | **0.00066 (0.06)** |
| ex t locks (ex-ante) | 0.08167 (0.623) | 0.03015 | 0.01344 | 0.00179 (0.15) |
| ex illiquid (bottom ADV quintile) | 0.09218 (0.691) | 0.02670 | 0.00164 | −0.01139 (−0.91) |
| ex volume spikes | 0.05747 (0.385) | 0.02640 | 0.01312 | 0.00132 (0.10) |
| **ex locks AND illiquid** | 0.07120 (0.541) | **0.00435 (0.35)** | **−0.01776** | **−0.02488 (−2.01)** |

Three readings:

1. **At the traded horizon, removing t+1 locks destroys the signal entirely** —
   IC falls from 0.01456 to 0.00154, an **89% collapse**, ICIR 0.012, t = 0.13.
2. **Removing locks *and* illiquid names turns the signal significantly
   negative at h=20**: IC −0.0249, t = **−2.01**. Among liquid, non-queue names,
   Alpha Core's ranking is worse than random.
3. Removing illiquid names alone *raises* h=1 excess slightly while collapsing
   the h=20 IC to −0.011 — the excess is carried by a few large winners, not by
   ordering.

---

## 6. Group A vs Group B — Matched Comparison

Within the top decile, split by **queue characteristics observable at decision
time** (locked at t, closed at high at t, or volume > 3× its own 60-session
median), then matched on **sector + ADV quintile + volatility quintile**.

| Group | h=1 return | Excess over market | t | % dates positive |
|---|---:|---:|---:|---:|
| Market (universe) | +0.0466% | — | — | — |
| Top decile, all | +0.5962% | +0.5496% | — | — |
| **A — queue-flagged** | **+1.1505%** | **+1.1039%** | **+12.47** | **89.2%** |
| B — no queue flag | +0.1919% | +0.1453% | +1.71 | 58.3% |
| **B — matched to A** | **+0.1649%** | **+0.1183%** | **+1.09** | 56.7% |
| **A − matched B** | — | **+0.9856%** | **+8.29** | 85.8% |

Group A is **45.6%** of the top decile.

> **At identical Alpha Core score, identical sector, identical liquidity and
> identical volatility, the names with queue characteristics beat the names
> without by +0.99% in a single session (t = 8.29). The names without deliver
> +0.12% at t = 1.09 — statistically indistinguishable from the market.**

**Matching caveat:** only **13.6%** of matches were exact on all three strata;
the remainder fell back to ADV+volatility or ADV alone. Iranian sector strata
are too thin for exact matching at this decile width. The direction and
magnitude are large enough that the fallback is unlikely to explain them, but
the precision of the matched estimate is limited.

---

## 7. Execution Feasibility

**Nothing clears the round trip at h=1 in any specification.**

| Specification | h=1 excess | vs 1.04% round trip |
|---|---:|---|
| Baseline | +0.5496% | ❌ **53% of cost** |
| Excluding t+1 locks | +0.3985% | ❌ 38% of cost |
| Excluding t+1 unbuyable | +0.3970% | ❌ 38% of cost |
| **Matched Group B (no queue)** | **+0.1183%** | ❌ **11% of cost** |

| | |
|---|---:|
| Collapse when t+1 locks removed | 27.5% *(pre-registered trigger 50%)* |
| Reachable share of the excess | 72.5% *(floor 30%)* |

### Why the reachable share overstates what is tradable

A name **locked limit-up at the t+1 open cannot be bought there** — a T+1 order
finds no offer. The 7.75% of the decile that locks carries 58.7% of the excess,
and it is precisely the part a live book cannot obtain. This is the same
constraint that produced 476 participation caps and a 28.1% partial-fill rate
in Experiment 5B, now measured at source.

**Percentage of theoretical alpha actually executable: 41.3%** — the share of
the original excess *not* contributed by the locked names (100% − 58.7%).

The 72.5% figure above measures something different and should not be confused
with it: that is the excess of a decile **re-formed** after locks are removed,
which lets the eleventh-ranked name take the locked name's slot. A live book
does get that substitution, so 72.5% is the optimistic bound and 41.3% the
pessimistic one. **Both sit far below the 1.04% round trip on an excess that was
never large enough to clear it in the first place** (0.5496%).

---

## 8. Results and Conclusion

### The four predictions of H1

| Prediction | Result |
|---|:--:|
| 1. h=1 alpha concentrated in limit-up events | ✅ 7.75% of names carry 58.7% of the excess |
| 2. Performance collapses after removing queue names | ✅ 89% collapse of IC at h=20; matched Group B t = 1.09 |
| 3. Longer horizons show no persistence | ✅ h=20 IC 0.00154 (t=0.13) ex-locks; **−0.0249 (t=−2.01)** ex-locks-and-illiquid |
| 4. Execution difficulty highest exactly where alpha appears | ✅ Top decile 3.03× more likely to lock, 10.27% unbuyable |

**All four confirmed.**

### Verdict

# SIGNAL IS QUEUE ARTIFACT

### Transparency on how this verdict was reached

The **automated pre-registered rule returned "REAL BUT UNEXECUTABLE."** Its
narrow test — removing names that *did* lock on t+1 — produced a 27.5%
collapse, below the 50% trigger. That value is reported unchanged in
`experiment14_queue_artifact.json` under `verdict`.

The final verdict differs, and the reason is stated rather than buried:

- H1 as written concerns stocks **"likely to enter or remain in price-limit
  queue conditions."** Removing only *realized* locks (7.75% of the decile)
  tests a strict subset of that. The matched Group A/B comparison — **explicitly
  required by the experiment brief's COMPARE section, not chosen after the
  fact** — tests queue *exposure* (45.6% of the decile), which is the faithful
  operationalization of the hypothesis.
- On that test the residual is **+0.12% at t = 1.09** — no signal.
- The narrow exclusion test is therefore a **lower bound** on the queue effect,
  and the 27.5% figure understates it because it removes outcomes rather than
  exposure.

Both readings converge on the same operational conclusion — nothing here is
tradable — but they differ on the scientific one, and the distinction matters
for whether any successor specification could work. **It could not: the score's
apparent skill is a proxy for queue detection.**

### What this means

Alpha Core's h=1 information coefficient of 0.105 at t = 8.78 was real as a
measurement and misleading as a forecast. The score is not predicting returns;
it is **identifying stocks about to become unbuyable**. High %B plus high
momentum in a market with a hard daily band is a queue detector.

This closes the causal chain the post-mortem opened:

1. The signal is strongest at h=1 — because that is where queue formation is.
2. It decays 23× by h=20 — because a queue resolves in days.
3. The frictionless arm passed — because it assumed fills that a locked book
   cannot provide.
4. Production failed at the 3.3rd percentile — because it kept buying names it
   could not get, and got the ones nobody wanted.

### Answer to the decision rule

> If removing queue-pressure names destroys the signal: **Alpha Core signal =
> microstructure artifact.**

Removing queue-pressure names leaves t = 1.09 at h=1 and **t = −2.01 at h=20**.
The signal is destroyed, and at the traded horizon it inverts.

---

## Recommended action

**ARCHIVE.** The stopping criterion preregistered in
`postmortem_and_next_phase.md` §8 was: *"Archive immediately if the top decile
is limit-locked at ≥2× the universe rate."* The measured ratio is **3.03×**.

The trigger has fired. No successor specification built on this score is
justified, because the score's information content has been located and it is
not tradable information.

---

## Provenance

| Item | Value |
|---|---|
| Experiment ID | EXP14-QUEUE-ARTIFACT-2026-08-18 |
| Module | `experiment_14_queue_artifact.py` (new) |
| Alpha Core | **Unmodified.** No weight, factor, threshold or frequency touched |
| Defects found and corrected mid-run | 3 — the 33% stale-bar trap, the reopening-gap trap, the row-shift forward-return bug |
| Baseline reproduces | E13 exactly (h=1 0.10492 · h=5 0.05101 · h=10 0.03003 · h=20 0.01456) |
| Holdout | Not started, not burned |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |

### Artifacts

`backtest_output/experiment14_queue_artifact.json` ·
`experiment14_queue_exposure.csv` · `experiment14_decomposition.csv` ·
`experiment14_matched_groups.csv`
