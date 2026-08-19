# PRODUCTION PIPELINE AUDIT — SIRAT-AI v2.1

**Phase 1 deliverable · 2026-08-18 · measurement repair complete**

Source: `backtest_output/experiment5b_production.json`, Alpha Core arm,
120 decision dates, 2014-09-21 → 2024-09-01, `credit_idle_cash=True`.

---

## 0. Summary

The three measurement repairs (P0, P1, P2) are complete and they **worked**:

| | Experiment 3 (v2.0) | Experiment 5B (v2.1) |
|---|---:|---:|
| Realized exposure | 29.22% | **48.94%** |
| Average positions | 4.63 / 10 | **5.80 / 10** |
| Fill rate | 93.3% | 89.8% |
| Idle cash earns | **0%** | **23%/yr** |
| Queue / participation counters | `null` | **measured** |
| Cash attribution | discarded | **surfaced** |

Every source of idle cash, every rejected candidate and every unfilled order is
now accounted for. The pipeline is no longer the unmeasured part of the system.

**And the alpha did not appear.** That is the finding this audit exists to
support, and it is developed in `production_readiness_scorecard.md`.

---

## 1. P0 — Exposure Diagnosis

### The reconciliation

```
target invested (100% − cash_reserve_pct 10%)        90.00%
realized exposure                                    48.94%
gap                                                 −41.06pp

average positions held                                 5.80  of 10
implied size per held position                        8.44%  = 48.94 / 5.80
target size per slot                                  9.00%  = 90.00 / 10

occupancy effect   (5.80 − 10) × 9.00%             = −37.81pp
sizing effect       5.80 × (8.44 − 9.00)           =  −3.25pp
                                                     ─────────
net                                                  −41.06pp   ✓ reconciles
```

**Occupancy is 92% of the gap.** Sizing now contributes −3.25pp, a reversal from
Experiment 3's +3.85pp: with the exposure ceiling removed, positions are sized
slightly *below* the equal-weight target rather than above it.

### Why only 5.8 of 10 slots

The binding constraint is **candidate supply**, not the allocator:

| Funnel stage | Count | Survival |
|---|---:|---:|
| Scored on a rebalance date | 65,932 | — |
| **Passed every entry test** | **969** | **1.47%** |
| Wanted (new names, post-dedup) | 501 | — |
| **Opened** | **501** | **100%** |
| Ranked out (passed, lost to basket size) | 468 | — |

**`open_rate = 1.00`.** Every candidate that reached the sizing stage was
opened. The allocator refused nothing it was asked to size.

969 qualifying names over 120 dates is **8.1 per rebalance against 10 slots**,
and some of those are already held. The book cannot fill ten slots because the
entry gates do not produce ten qualifying names.

### Every source of idle cash

| Source | Contribution | Evidence |
|---|---|---|
| **Empty slots — insufficient candidate supply** | dominant | 8.1 qualifying names/date vs 10 slots; `ranked_out` 468 shows surplus exists only on *some* dates |
| **Configured cash reserve** | 10.00pp floor | `cash_reserve_pct = 10.0` |
| **Position sizing below equal weight** | −3.25pp | risk-based sizing, 8.44% vs a 9.00% slot target |
| **Unfilled and partial orders** | see §2 | 356 partials of 1,267 fill events |
| Sized to zero | **0** | none |

Average cash 51.06%. It now **earns 23%/yr** (`credit_idle_cash=True`), so this
is genuine opportunity cost, not the double penalty of v2.0.

---

## 2. P1 — Execution Realism

### Layer A — order attempts

| Counter | Value | Reading |
|---|---:|---|
| `participation_capped` | **476** | **the dominant execution constraint** |
| `filled` | 911 | complete fills |
| `partial_fills` | 356 | **28.1%** of fill events |
| `buy_queue_blocked` | 107 | صف خرید |
| `queue_blocked_exit` | 47 | صف فروش trapping exits |
| `sell_queue_blocked` | 37 | |
| `orders_expired_total` | **0** | nothing expired unfilled |

These four counters read `null` in v2.0. They are now measured — the P3 repair
is what made this section possible.

### Every unfilled order

- **Zero orders expired.** Everything raised was eventually filled at least
  partly.
- **476 participation caps** — the ADV limit trimmed the order size. This is the
  single most frequent execution intervention in the system.
- **356 partial fills (28.1%)** — the direct consequence of the caps.
- **107 buy-queue blocks** and **47 exits trapped by a sell queue.**

Fill rate 89.82%, versus the random control's 76.43%. **Alpha Core's names fill
*better* than random ones**, which rules out execution adverse selection as the
explanation for its underperformance.

---

## 3. P2 — Counter Reconciliation

The two layers are now structurally separate and the artifact carries the rule.

| Layer | Counts | Source |
|---|---|---|
| **A — execution** | ORDER ATTEMPTS | `backtesting/execution.py` `filler.stats` |
| **B — construction** | CANDIDATE ADMISSIONS | `portfolio_engine.allocate()`, `book.can_open()` |

`assert_layers_disjoint()` runs on every call; `disjoint_verified: true` is
written into the artifact. `refused` is declared unavailable with its two
replacements named.

### Every rejected candidate

**Layer B — construction:**

| Reason | Count |
|---|---:|
| `portfolio_full` | 331 |
| `sector_count` | 2 |
| `sized_to_zero` | 0 |
| `ranked_out` (passed entry, lost to basket size) | 468 |
| `rotated_out` (held, fell out of the top N) | 647 |
| `empty_rebalances` | 0 |

**Entry stage:** 65,932 scored → 969 passed → 192 recorded refusals.
Pass rate **1.47%**.

### The reconciliation caveat, still standing

`wanted_positions` 501 = `opened_positions` 501, while `blocked_by` sums to 333.
These remain **unsummable** — they are recorded at different layers and count
different objects. P2 separated and documented them; it did not merge them,
because merging them would be wrong. Any future exposure attribution must
respect the split.

---

## 4. Desired vs Realized Portfolio

| Dimension | Desired | Realized | Gap | Owner |
|---|---:|---:|---:|---|
| Invested | 90.00% | 48.94% | −41.06pp | Layer B — candidate supply |
| Positions | 10 | 5.80 | −4.20 | Layer B — entry gates |
| Size per slot | 9.00% | 8.44% | −0.56pp | risk-based sizing |
| Order completion | 100% | 71.9% complete, 28.1% partial | — | Layer A — participation cap |
| Orders expired | 0 | 0 | 0 | — |
| Turnover | *(unconstrained: 11.26/yr)* | **4.45/yr** | **−6.81/yr** | Layer A + basket size |

**The last row is the one that matters.** The frictionless arm (5A) earned its
+56pp edge at 11.26 turnovers/year. The production pipeline achieves 4.45 —
**40% of the required rotation.** Not because the allocator refuses it
(`open_rate = 1.00`), but because only 8.1 names qualify per rebalance and the
participation cap trims 476 of the orders that do get raised.

---

## 5. What P0–P2 Did and Did Not Fix

**Fixed:**
- Idle cash earns the policy rate; Sharpe levels are interpretable for the first
  time.
- Exposure rose from 29.22% to 48.94% once the retired exposure engine's ceiling
  was removed.
- Occupancy rose from 4.63 to 5.80 slots.
- All four execution counters, plus cash attribution and entry rejections, are
  measured and reported.

**Not fixed, and not fixable by measurement:**
- **Candidate supply.** 1.47% of scored names pass the entry gates. Loosening
  those gates is a model change, forbidden while Alpha Core is frozen.
- **Rotation.** 4.45/yr against the 11.26/yr the frictionless edge requires.
- **Survivorship.** No point-in-time universe exists. Unchanged.

---

## 6. Verdict on Phase 1

**Phase 1 is complete.** The pipeline is fully measured: every source of idle
cash, every rejected candidate and every unfilled order has an owner and a
count, and the desired-versus-realized book reconciles to the rial.

The measurement repair also removed the leading hypothesis for the Experiment 3
failure. Exposure nearly doubled, occupancy rose, cash began earning, and
Experiment 5B still placed Alpha Core at the **3.3rd percentile** of random —
*worse* than Experiment 3's 26.7th. See `production_readiness_scorecard.md`.

---

## Provenance

| Item | Value |
|---|---|
| Version | **SIRAT-AI-v2.1** |
| Source | `backtest_output/experiment5b_production.json` |
| Changes | `backtesting/portfolio.py` (`accrue_cash`, default off), `backtesting/engine.py` (call site), `backtest_config.py` (`credit_idle_cash`, `cash_annual_rate`), `experiment_execution_realism.py` (reporting) |
| Tests | 784 pass |
| Alpha Core | **Unmodified** |
| Git commit | **UNAVAILABLE — git is not installed on this machine** |
