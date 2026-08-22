# RESEARCH GAP ANALYSIS — what the vendor could and could not fix

**2026-08-22.** Maps SIRAT-AI's known bottlenecks against what the vendor
*claims*. Every vendor column is `UNKNOWN` until a trial runs — this document
says what each answer would mean, not what it is.

---

## 1. SIRAT-AI's bottlenecks, measured

These are not opinions; each was established by an experiment that failed.

| # | Bottleneck | Measured state | Can time alone fix it? |
|---|---|---|---|
| **B1** | **Historical order-book depth** | **1 capture**, taken 2026-08-22 | **No** — only forward collection, one session per day, forever |
| **B2** | PIT fundamental panel | median **3** filings/company; **0 of 764** reach the required 16 | No — needs a source with publication dates |
| **B3** | Price data contamination | **361 impossible sessions**, largest +19,976,600% | No — needs corporate-action data |
| **B4** | Unspent out-of-sample window | E19 and E21 spent **every** holdout in the archive | Yes — but only by waiting |
| **B5** | Impact model calibration | prefactor **imported from AAPL**, never calibrated locally | Partly — needs depth (B1) |
| **B6** | Survivorship quantification | no universe snapshot before 2026 | No — the past cannot be re-observed |

## 2. What the vendor would fix, per bottleneck

| Bottleneck | Vendor claim | Status | If PASS | If FAIL |
|---|---|---|---|---|
| **B1 depth** | **never mentioned** | `UNKNOWN` | the single biggest win available to this project | `depth.py` remains the only source that will ever exist |
| **B2 PIT fundamentals** | "بی‌نیاز کردن از سامانه کدال" | `UNKNOWN` | fundamental research unblocks years early | Codal crawl continues; 20,917 filings, weeks of runtime |
| **B3 contamination** | corporate actions never mentioned | `UNKNOWN` | independent reconciliation of 361 rows | contamination stays, `gates.price_sanity` keeps catching it |
| **B4 holdout** | — | **N/A** | vendor history is *already seen* data; it cannot create an unspent window | unchanged |
| **B5 impact model** | — | `UNKNOWN` | depth would allow local calibration | prefactor stays imported and swept |
| **B6 survivorship** | — | `UNKNOWN` | delisted-name history would quantify it | unchanged |

### The one that cannot be bought

**B4 is immune to any vendor.** A holdout is unspent *because nobody has looked
at it*. Purchasing ten years of history buys ten years someone has already
looked at — including us, in E14 through E22. The next genuine out-of-sample
claim can only be earned by waiting for sessions that have not happened yet.

No data purchase shortens that. It is worth stating plainly, because "buy more
history" is the intuitive response to a research programme that has run out of
road, and here it is the wrong one.

## 3. Capability comparison

| Capability | Existing SIRAT-AI | Vendor | Better | Replacement? |
|---|---|---|---|---|
| Daily OHLCV | 1,914,641 rows, 2001→2026, 818 names | claimed "تاریخچه" | `UNKNOWN` | `UNKNOWN` |
| Intraday | **none** | not mentioned | `UNKNOWN` | `UNKNOWN` |
| **Historical depth** | **1 capture** | **not mentioned** | `UNKNOWN` | `UNKNOWN` |
| Financials | 2,289 filings, 765/818 companies, median 3 | claimed complete | `UNKNOWN` | `UNKNOWN` |
| **Publication dates** | Codal `published_at`, 2,334/2,334 present | implied, unconfirmed | `UNKNOWN` | `UNKNOWN` |
| حقیقی/حقوقی flow | 1,681,938 rows, 2008→2026, 780 names | not mentioned | `UNKNOWN` | `UNKNOWN` |
| Corporate actions | **none** | not mentioned | `UNKNOWN` | `UNKNOWN` |
| Real price band | `depth.py` captures `pMax`/`pMin` since today | not mentioned | `UNKNOWN` | `UNKNOWN` |
| PIT layer | built, validated | n/a | — | vendor is a *source*, not a layer |

**Note on existing strengths.** SIRAT-AI's flow panel (1.68M rows, 339–653
names/date from 2014) and its Codal publication dates (2,334 of 2,334 present)
are genuinely good. The vendor would have to beat those, not merely have them.

## 4. Codal replacement decision — DEFERRED

Three outcomes were specified. **None can be selected on present evidence.**

| | Condition |
|---|---|
| **A** Vendor replaces Codal | requires `published_at` **and** restatement versioning **and** coverage ≥ current |
| **B** Vendor supplements Codal | if statements are served but publication timestamps are absent or partial |
| **C** Vendor insufficient | if only period-end dates are served |

The deciding question is Q2 of the trial. Until then this is `DEFERRED`, not
`B` — picking the middle option to appear decisive would be a guess wearing a
verdict's clothes.

## 5. Experiments the vendor could unblock

Recorded so the trial is judged against research value rather than field count.

| If the vendor provides | Newly possible |
|---|---|
| historical depth | queue-formation research on real depth; local impact calibration; the fill-probability model E23 could not build |
| PIT financials | the fundamental factor programme, blocked since day one |
| corporate actions | reconciliation of the 361 impossible rows; a properly adjusted price series |
| historical flow | independent validation of E21's panel |

And what stays impossible regardless: **a fresh out-of-sample window.**

## 6. Priority

1. **Q1 — historical depth.** The only bottleneck that time cannot fix and that
   a vendor could.
2. **Q2 — publication dates.** Decides the Codal question outright.
3. Everything else.

If Q1 fails, the vendor's value drops to *validation and convenience* — real,
but not transformative, and not urgent.
