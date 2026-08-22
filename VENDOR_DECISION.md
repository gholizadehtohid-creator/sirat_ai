# VENDOR DECISION — وب سرویس پویا / Pooya API

> **UPDATED 2026-08-22 (same day), after a real authentication attempt.** Two
> findings change the picture below and are recorded in the "Post-discovery
> update" section at the end: (1) historical order-book depth **does not exist**
> as a machine endpoint — settled from the client, `FAIL`; (2) automated login
> is **blocked by a WAF**. The decision **stays TRIAL ONLY**, but the trial's
> purpose narrows sharply. Read the update before acting.

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

---

# POST-DISCOVERY UPDATE — 2026-08-22

An authenticated discovery pass (credentials read from env, never printed, one
login attempt only) produced two decisive findings. Full detail in
`BORSVIEW_API_INVENTORY.md` and `BORSVIEW_DEPTH_AUDIT.md`.

## Finding 1 — historical depth does NOT exist. Settled.

The full application (6.6 MB of AngularJS, `PooyaBiNewApplication`) was read.
It calls REST endpoints for price, fundamentals, technicals, filings and
حقیقی/حقوقی flow — and **no order-book/depth/queue endpoint of any kind**.
Depth is served **live over a WebSocket only** (`/socket/market-data`,
Lightstreamer) and is not retained or queryable.

An app cannot show history it has no endpoint to fetch, so this does not depend
on getting past the WAF:

> **`HISTORICAL_DEPTH = FAIL`. The vendor does not solve SIRAT-AI's historical
> market-depth bottleneck.** The "10+ years" seen in the UI is `/historicalPrice`
> — a decade of price, which is not order-book depth.

## Finding 2 — automated access is blocked by a WAF.

The login POST — correct JSON body, browser headers, warmed session — returned
an HTML page titled `WAF`, no cookie, no JSON. **The credential was never
evaluated**; a firewall rejected the automated request itself.

Getting past a WAF is anti-bot circumvention, which the brief prohibits and
lists as a stop condition. A valid subscription does not change that: the WAF is
a deliberate control against machine access. So `AUTHENTICATION = BLOCKED_BY_WAF`,
and no endpoint content could be sampled.

## What the decision becomes

Still **TRIAL ONLY** — but the two things a trial could have proven have
changed:

- **Depth** is off the table regardless of any trial. Nothing the vendor could
  demonstrate brings back a history it never recorded.
- **Fundamentals / flow** remain the only reason to trial, and even that
  requires the vendor to provide a **sanctioned machine path** — a documented
  API key or an Excel/CSV export — because the WAF blocks scripted login. If the
  only access is the interactive browser UI, then for automation purposes this
  is effectively **`DO NOT BUY`**: a data feed you cannot reach from code is not
  a data feed.

## The single question that now decides everything

Put to the vendor (`info@pouyafinance.com`, 021-81903000, `t.me/bvhelp`):

> **"وب سرویس پویا"** — is there a **documented API with a key or token**, or a
> **bulk export**, that a program may use **without** driving the browser UI and
> **without** tripping the WAF? And does it include `published_at` on financial
> statements?

- **Yes, with a key** → run a real trial against fundamentals/flow; depth stays FAIL.
- **No, browser only** → `DO NOT BUY` for SIRAT-AI's purposes.

## Unchanged, and now the only forward path for depth

`depth.py` is SIRAT-AI's sole route to the one dataset that matters most, and
this discovery removes the last hope that a vendor could substitute for it. It
now also writes a per-capture provenance manifest (sha256 content hash, source,
collector version, timestamp) — closing the reproducibility requirement.
Second real capture recorded today: 920 instruments, 4,372 depth levels.

    python depth.py --install-schedule
