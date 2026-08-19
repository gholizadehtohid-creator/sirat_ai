# IMPLEMENTATION FIX SPECIFICATION — P3 (REPORTING ONLY)

**Executed 2026-08-18 · reporting layer only · no research result changed**

---

## 1. Scope

| | |
|---|---|
| **In scope** | Diagnostic key mapping, exposure of already-computed attribution, counter-layer separation |
| **Out of scope** | The allocator (P2), exposure correction (P1), idle-cash accrual (P0), Alpha Core, factor weights, thresholds, execution behaviour |
| **Risk class** | **ZERO to results** — see §5 |

**Not done, deliberately:** the allocator was not touched. Phase 2 of the brief
says to separate the counters, not to fix the layer they describe. The
`portfolio_full` / `cash_floor` occupancy failure remains exactly as measured.

---

## 2. Changed Files

| File | Change | Lines | Behaviour affected |
|---|---|---|---|
| `experiment_execution_realism.py` | Counter registries, layer extractor, disjointness assertion, corrected `profile()` | +95 / ~18 modified | **Reporting only** |
| `tests/test_execution_diagnostics_mapping.py` | **New** — 13 tests guarding the mapping and the layer split | +155 | None (test) |

No other file was opened for writing.

---

## 3. Exact Changes

### 3.1 The key-mapping defect

`backtesting/engine.py:400` runs `diagnostics.update(filler.stats)`, so the
execution counters *were* reaching diagnostics. `profile()` asked for names
nothing writes:

| `profile()` read (before) | `execution.py` writes | Result before | Result after |
|---|---|---|---|
| `buy_queue_blocked` | `blocked_buy_queue` | `null` | **mapped** |
| `sell_queue_blocked` | `blocked_sell_queue` | `null` | **mapped** |
| `participation_capped` | `capped_by_participation` | `null` | **mapped** |
| `refused` | *(nothing)* | `null` | **declared unavailable** — §3.4 |

The public report names are unchanged, so no downstream reader breaks. Only the
source key each one reads from was corrected.

### 3.2 Two registries, one assertion

```python
LAYER_A_EXECUTION = {          # order attempts — backtesting/execution.py
    "buy_queue_blocked":    "blocked_buy_queue",
    "sell_queue_blocked":   "blocked_sell_queue",
    "participation_capped": "capped_by_participation",
    "orders_expired_total": "expired",
    "filled":               "filled",
    "partial_fills":        "partial",
    "queue_blocked_exit":   "queue_blocked_exit",
}

LAYER_B_PORTFOLIO = {          # candidate admissions — portfolio_engine/engine
    "blocked_by":        "blocked_by",
    "wanted_positions":  "wanted_positions",
    "opened_positions":  "opened_positions",
    "sized_to_zero":     "sized_to_zero",
    "ranked_out":        "ranked_out",
    "rotated_out":       "rotated_out",
    "empty_rebalances":  "empty_rebalances",
    "signals_total":     "signals_total",
}
```

`assert_layers_disjoint()` raises if the two registries ever share a **report
name** or a **source key**. It runs on every `profile()` call and its result is
written into the artifact as `counter_layers.disjoint_verified`.

**Why this is the whole Phase-2 fix.** The layers cannot be summed because they
count different objects: Layer B refuses a *candidate* before an order exists;
Layer A blocks an *order* that Layer B already admitted. The stored artifact
shows what happens when this is forgotten — `wanted_positions 448` and
`opened_positions 448` beside a `blocked_by` summing to **492**, impossible for
one funnel and correct for two. Keeping the registries structurally separate
means a future edit cannot re-merge them without tripping the assertion.

### 3.3 Absence is not zero

```python
out[report_name] = (diagnostics[source_key]
                    if source_key in diagnostics else None)
```

`.get(key) or 0` would have turned every unmapped counter into a confident
zero — which is precisely how the original defect stayed invisible for the whole
of Experiment 3. A counter nobody wrote reports `None`; a counter written as `0`
reports `0`. Two tests pin this distinction.

### 3.4 `refused` — declared, not dropped

No layer writes it. Rather than leave a permanently-null field (which reads as
a measurement that came back empty) or delete it silently (which reads as a
field nobody wanted), it is named in `UNAVAILABLE_COUNTERS` with its reason and
its replacements:

> *"no layer writes this key. Entry refusals live in `entry_rejections`
> (engine.py:420, built by the strategy's `rejection_summary()`); allocator
> refusals live in `blocked_by`. Read those two instead."*

This is carried into the artifact under `counter_layers.unavailable`.

### 3.5 Attribution already computed, now surfaced

Passed through **verbatim**. Neither is recomputed — both have an owner inside
the engine and a second opinion here would be a second implementation.

| Key | Produced at | Contents |
|---|---|---|
| `cash_analysis` | `engine.py:422` via `_cash_analysis()` | idle cash attributed to *not enough signals · basket too small · portfolio caps · sizing · orders raised and never filled (صف خرید)* |
| `entry_rejections` | `engine.py:420` via `rejection_summary()` | per-reason entry refusal counts and shares |

---

## 4. Phase 3 — Verification

### Before

| Measure | Value |
|---|---|
| Fields in `profile()` output | 34 |
| Diagnostic fields null from key mismatch | **4** |
| `cash_analysis` | **absent** |
| `entry_rejections` | **absent** |
| Layer separation | none — counters interleaved in one flat dict |
| Assertion against summing layers | none |

### After

| Measure | Value |
|---|---|
| Fields in `profile()` output | **38** |
| Diagnostic fields null from key mismatch | **0** |
| `cash_analysis` | **exposed** (passthrough) |
| `entry_rejections` | **exposed** (passthrough) |
| Layer separation | `execution_layer` (7 keys) / `portfolio_layer` (8 keys), verified disjoint |
| Assertion against summing layers | `assert_layers_disjoint()`, runs per call, recorded in the artifact |

Newly visible: `execution_layer`, `portfolio_layer`, `cash_analysis`,
`entry_rejections`, `counter_layers`. Removed as a live field: `refused`
(now declared under `counter_layers.unavailable`).

Demonstrated against a fixture carrying the real source names:

```
buy_queue_blocked      before=None  after=11
sell_queue_blocked     before=None  after=5
participation_capped   before=None  after=7
disjoint verified:     True
```

### Tests

| Suite | Result |
|---|---|
| `tests/test_execution_diagnostics_mapping.py` (new, 13 tests) | **OK** |
| `tests/test_experiment_execution_realism.py` (8 tests, guards the seam) | **OK** |
| **Full suite — `python -m unittest discover -s tests`** | **775 tests, OK** |

### Confirmations required by Phase 3

- **No portfolio result changed.**
- **No backtest result changed.**
- **No Alpha Core logic changed.**

---

## 5. Risk Assessment

| Risk | Assessment | Basis |
|---|---|---|
| **Results change** | **None possible** | `profile()` is called *after* `run_arm()` returns. It reads `result` and returns a new dict; it writes nothing back to the result, the book, the engine or config. No code path that produces a return was opened. |
| **Downstream readers break** | **Very low** | Every pre-existing report name is retained. Only the source key behind four of them changed, and only from a name nothing wrote. |
| **Schema change** | **Additive** | Five keys added. One (`refused`) moved from a permanently-null field to a declared-unavailable entry — the only non-additive change, deliberate and documented. |
| **A future edit re-merges the layers** | **Guarded** | `assert_layers_disjoint()` raises on both report-name and source-key collision, on every call. |
| **Silent zeros return** | **Guarded** | Two tests assert that an absent counter is `None` and a written zero is `0`. |
| **Stored artifacts become stale** | **Real, and unavoidable** | `execution_realism.json` still holds the pre-fix nulls. It must be regenerated by re-running Experiment 3 — an *in-sample* re-run of an already-run experiment, not a holdout. Until then, treat the four counters in the stored file as unmeasured, not as zero. |

### The one action this fix enables and does not itself perform

Re-running Experiment 3 to populate `cash_analysis`, `entry_rejections` and the
three queue/participation counters. That is a backtest, which this task
forbids, so it is left for the next task. **It changes no research conclusion —
it only makes the existing conclusion attributable.**

---

## 6. Provenance

| Item | Value |
|---|---|
| Task ID | EXP5-P3-FIX-2026-08-18 |
| Files modified | `experiment_execution_realism.py` |
| Files added | `tests/test_execution_diagnostics_mapping.py` |
| Model parameters changed | **None** |
| Backtests run | **None** |
| Holdouts evaluated | **None** |
| Tests | 775 pass |
| Git commit | **UNAVAILABLE — git is not installed on this machine** (P5 remains open) |
| Python | 3.14.7 · numpy 2.5.1 · pandas 3.0.5 |
