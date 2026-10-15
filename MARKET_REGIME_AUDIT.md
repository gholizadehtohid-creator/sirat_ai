# The market this bot trades is not the market its numbers describe

**2026-10-15. Measured, not read. Every figure below comes from this
repository's own database and is reproducible from the queries named beside
it. Nothing here is an experiment: no window is spent, no verdict is issued,
no preregistration is touched.**

This audit was triggered by a request to make the bot trade automatically and
watch the market continuously. The first question that has to be answered for
either of those is *what market*, and the answer turned out to be unrecorded
anywhere in this project.

---

## 1. The exchange was shut for eleven weeks this year

The Tehran equity market traded **nothing at all** from **2026-02-28 to
2026-05-18** — fifty consecutive sessions — and reopened on 2026-05-19 with
402 of 751 names trading, 442 the next day.

Triple-confirmed from three tables that are populated by three different
collectors, so no single collector failure can explain it:

| Evidence | During 2026-02-28 .. 2026-05-18 |
|---|---|
| `price_history.volume` (equities) | `SUM(volume>0) = 0` on every one of the 50 sessions |
| `client_type_history` | **zero rows** — no session, no حقیقی/حقوقی publication |
| `index_history` | both indices frozen at a single value: شاخص کل 3,713,955.9 and هم‌وزن 952,681.0 for 52 consecutive rows, resuming 2026-05-19 |

It was not the first. Since 2024-09 the equity market has had two closures:

```
2025-06-15 .. 2025-06-25     8 sessions
2026-02-28 .. 2026-05-18    50 sessions
                            --
total                       58 of 510 calendar sessions = 11.4%
```

**One session in nine, this exchange is not there — and when it goes, it goes
for months rather than hours.** For a bot that is meant to run unattended,
that is the single most consequential fact in this document.

### TSETMC does not tell you. It fills in.

On every closed session TSETMC emitted a complete bar for every listed
instrument, carrying the previous close forward at zero volume, and a frozen
value for both indices. 36,763 phantom equity bars across 49 dates in the
2026 closure alone.

So the shape of the data during a market closure is:

```
price_history        a full, plausible, entirely fictional bar for every name
index_history        a flat line that no index ever printed
client_type_history  silence -- the only table that tells the truth
```

A system that judges "is the market current" from `price_history` is told yes
throughout. This project does exactly that (`bootstrap._history_state`), which
means during those eleven weeks the panel reported the data as fresh, the
screen ranked names on frozen prices, and the basket builder sized positions
in a market where no position could be taken.

**The honest signal is the absence of flow, not the presence of a bar.**

---

## 2. حجم مبنا has been eliminated

Measured on the depth capture of 2026-10-15 (`depth_snapshot.base_volume`):

| base_volume | all instruments | equities |
|---|---:|---:|
| **1 share** | **1,036** | **534** |
| > 1 | 2 | 1 |

Base volume — the mechanism that damped a closing price toward the previous
close unless enough volume traded — is gone. With it set to one share, the
closing price moves the full distance on any trade at all.

This changes what a "closing price" *is*, and every number this project has
ever computed from one straddles the change.

---

## 3. Half of all sessions now close at the band limit

Share of equity sessions whose close-to-close move was ≥2.5% in absolute terms
— i.e. at or within a rounding tick of the ±3% band:

| Month | at the band | median abs move |
|---|---:|---:|
| 2025-06 | 12.8% | 0.00% |
| 2025-07 | 14.9% | 0.39% |
| 2025-08 | 13.1% | 0.34% |
| 2025-09 | 19.2% | 0.60% |
| 2025-10 | 26.4% | 1.20% |
| 2025-11 | 21.8% | 0.92% |
| 2025-12 | 32.9% | 1.66% |
| 2026-01 | **50.0%** | 2.50% |
| 2026-02 | 35.5% | 1.80% |
| 2026-03 .. 2026-05 | *market closed* | |
| 2026-06 | 54.7% | 2.69% |
| 2026-07 | 49.8% | 2.49% |
| 2026-08 | 48.9% | 2.46% |
| 2026-09 | 47.0% | 2.40% |
| 2026-10 | 45.1% | 2.35% |

**The rate roughly quadrupled, from ~13% to ~47%.**

**It is not attributed to the base-volume change here, and the reason is in
the table.** The climb begins in 2025-09 and is already at 32.9% in the month
the change landed. A single cause arriving in December cannot explain a rise
that started in September. Candidate explanations — inflation regime,
the closure and its aftermath, the base-volume change, a shift in retail
participation — are not separated by this data, and naming one would be a
story rather than a measurement.

What does not depend on the cause: **on roughly one session in two, a name
closes at a price where it cannot be bought or cannot be sold.** That is the
governing execution fact for anything automated on this market, and §6 is
about what follows from it.

---

## 4. The price band is published per instrument, and this project guesses it

`config.PRICE_BAND_PCT = 3.0` is a single global constant. The exchange
publishes `pMax`/`pMin` per instrument in every market-watch payload, and
`depth.py` has been storing them since August.

Measured against `prev_close` on the 2026-10-15 capture:

| Upper band | all | equities |
|---|---:|---:|
| ~3% | 726 | **532 of 535 (99.4%)** |
| negative / other | 309 | 3 |

**For equities the constant is right** — 99.4% sit in the 3% band, and the
scatter is integer-rial rounding, not regime. A claim encountered during this
research that Tehran now runs a segmented no-limit regime for some names
**does not reproduce in this data** and is not carried forward.

For non-equities it is simply wrong: every negative band belongs to a
non-equity. Since the gold funds are now in the panel and the smart-money
scanner reads them, the constant should give way to the published figure
wherever one exists.

---

## 5. The research panel reads a closed market as "buyable"

`examples/e21_panel.py` builds its market calendar as
`np.sort(bars["date"].unique())` over `price_history` — which includes the
phantom closure dates. Its own docstring states the rule it means to enforce:

> Where a name has no row on a calendar date, the forward target is left as
> **unknown (NaN)** and excluded from measurement — never filled with False.
> Filling it False would assert that a suspended name was buyable, which is
> both untrue and exactly the direction that flatters the signal.

The guard was written for a **missing row**. TSETMC supplies a **phantom
row**, so the guard never fires: `locked_up` requires `volume > 0`, a frozen
bar has none, and the target evaluates to False — *buyable* — on a day the
exchange was shut.

Exposure, measured over E21's window (2024-09-01 .. 2026-08-18, 470 calendar
sessions, 266,266 flow-bearing name-dates):

| Horizon | rows whose target lands on a dead session | share of panel |
|---|---:|---:|
| h=1 | 1,304 | 0.49% |
| h=2 | 2,600 | 0.98% |
| h=3 | 3,901 | 1.47% |
| h=5 | 6,455 | 2.42% |

**This does not overturn anything, and the direction matters.** False
"buyable" labels dilute the positive class, so the bias runs *against* the
hypotheses E21 and E24 were testing — their published results are, to this
extent, conservative. E24's own discovery window (2014-01-01 .. 2023-02-28)
predates both closures entirely and is untouched.

It is recorded because a successor rebuilding the panel should exclude dead
sessions from the calendar, and because the general lesson is sharper than the
instance: **this platform's integrity gates test whether a price is
*possible*, never whether a session *happened*.** `price_sanity` catches a
19,976,600% return. It passes fifty sessions of exactly 0.00% without a word.

---

## 6. What follows for automation

Nothing above is a signal and none of it improves a forecast. All of it
constrains what an automated order path may assume.

1. **"No data" and "no market" are different states, and only one of them is
   currently representable.** The order path needs a third answer beside
   fresh/stale: *the exchange is not trading*. The observable that
   distinguishes it is flow publication and traded-name count, never bar
   presence.
2. **An order is more likely than not to rest unfilled.** With ~47% of
   sessions closing at a limit, any system that compares a target position
   against a *held* position — rather than against held **plus working** —
   will re-send the same order every session until the lock breaks and then
   fill all of them at once. This is the mechanism by which this specific bot
   is most likely to lose money, and it is caused by market structure rather
   than by any bug.
3. **A kill switch cannot promise to flatten.** Selling into a limit-down
   queue is joining a queue, not exiting. A halt control here means *stop
   sending, cancel working* and must say so rather than implying an exit it
   cannot deliver.
4. **Eleven weeks is longer than any timeout.** Every retry, cool-off and
   staleness policy in this project is tuned in hours. The failure mode that
   actually occurred lasted 1,800 of them.
5. **Calibration has a shelf life measured against regime changes, not
   months.** Any parameter fitted on data before 2025-09 describes a market
   where names reached their limit a third as often.

---

## Reproducing this

Every figure comes from `sirat_market.db` via the queries embedded above:
closures from `price_history` joined to `instruments` on `is_equity = 1`,
grouped by date on `SUM(volume > 0)`; the flow silence from
`client_type_history`; the frozen indices from `index_history`; base volume
and the published band from the `depth_snapshot` row set of
`captured_at = '2026-10-15T08:14:48+00:00'`; the panel exposure by mapping
`client_type_history` dates onto the `price_history` calendar and testing
whether slot+h is a dead session.

The depth capture that §2 and §4 rest on is the third this project has ever
taken, and the first since 2026-08-22 — 54 days of order-book state that no
longer exists anywhere.
