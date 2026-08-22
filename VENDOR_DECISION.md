# VENDOR DECISION — وب سرویس پویا / Pooya API

# DECISION: **TRIAL ONLY**

**2026-08-22.** Not `BUY`. Not `DO NOT BUY`.

---

## Why not BUY

Nine of ten gates are `UNKNOWN`. Buying now would mean paying for capabilities
that no public document describes:

- whether historical order-book depth exists at all — **the one thing SIRAT-AI
  actually needs**
- whether financial statements carry `published_at` distinct from `period_end`
- whether prices are raw or adjusted, and by what method
- how far back anything goes
- whether حقیقی/حقوقی flow is served

The vendor publishes **no API documentation, no data catalog, no schema, no
endpoint list and no coverage table**. The entire evidence base is one
marketing paragraph inside a JavaScript bundle.

## Why not DO NOT BUY

The access question is genuinely settled, and settled well:

- **REST, JSON**, stated explicitly by the vendor
- **published pricing** — 100 rials per price record, 200 otherwise
- **a named vendor under regulatory supervision** — پردازش اطلاعات مالی مفید,
  part of کارگزاری مفید, under سازمان بورس
- **real support channels** — `info@pouyafinance.com`, 021-81903000,
  `t.me/bvhelp`

This is a legitimate commercial data product. Refusing it on present evidence
would be refusing on *absence* of evidence, which is the same error in the
opposite direction.

## Why TRIAL ONLY is the correct answer

**The vendor offers a free trial** — "امکان استفاده از سرویس آزمایشی … عدم
پیچیدگی مکاتبات اداری".

Every one of the nine `UNKNOWN` gates is resolvable in a few hours of trial
access, and resolvable in **no other way**. No amount of further public
reconnaissance moves any of them. Buying before the trial spends money to learn
what the trial teaches free.

---

## What the trial must establish, in priority order

| # | Question | Decides |
|---|---|---|
| **1** | Is **historical order-book depth** served? | whether the vendor touches SIRAT-AI's real bottleneck |
| **2** | Is `published_at` a real field on financial statements? | whether Codal can be replaced or only supplemented |
| **3** | Price history: earliest date, and **raw or adjusted**? | whether it can validate the existing 1.9M rows |
| **4** | حقیقی/حقوقی flow: served? from when? | whether it extends the E21 panel |
| **5** | Do production endpoints present a **complete TLS chain**? | whether a production collector is safe to run |

Question 1 is the deciding one. **If depth is absent, the vendor cannot solve
the problem that made this evaluation urgent** — however good the rest is.

## Cost of the trial

Zero, per the vendor's own statement. The only expenditure is one phone call or
email.

## What must NOT happen during the trial

Carried from the brief and binding:

- no integration with SIRAT-AI
- no vendor data in any research result
- no strategy, alpha, backtest-engine or feature change
- no credential in source code
- no schema guessed, no coverage assumed
- isolated validation environment only

## The parallel action this decision does not change

**Whatever the trial shows, `depth.py` must be scheduled today.**

If the vendor has no historical depth, SIRAT-AI's own collector is the only
source that will ever exist. If the vendor *does* have it, our own series
becomes the independent check against it. Either way the answer is the same,
and every unscheduled day loses a session permanently.

    python depth.py --install-schedule

Current state: **one capture.**

---

## Confidence

**MEDIUM** on the decision (TRIAL ONLY) — the reasoning does not depend on any
unknown.

**LOW** on every capability claim — by construction, since none is verified.
