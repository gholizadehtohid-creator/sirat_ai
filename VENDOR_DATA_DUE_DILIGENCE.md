# VENDOR DATA DUE DILIGENCE — وب سرویس پویا / Pooya API

**2026-08-22. Phase 0 complete. Phases 1–6 CANNOT BE EXECUTED without a trial.**

**Vendor:** شرکت پردازش اطلاعات مالی مفید (Mofid Financial Information
Processing), part of کارگزاری مفید, operating under سازمان بورس و اوراق بهادار.

**Governing rule applied throughout: anything not proven by documentation or a
real sample is `UNKNOWN`.** No credential was requested, received or used.
`SECRET_SCAN = CLEAN`.

---

## 1. The evidence base, in full

This is everything the audit has. It is short, and that is the finding.

| # | Source | What it is |
|---|---|---|
| E1 | BourseView SPA bundle (1.48 MB, public) | marketing copy + pricing paragraph — **the only substantive source** |
| E2 | `api.bourseview.com/openapi.json` | FastAPI default, **75 bytes, zero declared paths** |
| E3 | `www.pouyafinance.com` | **content-free template page** — bootstrap boilerplate, "Engineering Manager", "Our clients", zero Persian, one link back to bourseview.com |
| E4 | `bourseview.com` route probes | no `/docs`, `/swagger`, `/api-docs`, `/terms`, `robots.txt`, `sitemap.xml` |
| E5 | TLS handshake, `pouyafinance.com` | chain incomplete — `unable to get local issuer certificate` |

**There is no API documentation portal, no data catalog, no schema, no endpoint
list and no published coverage table anywhere the audit could reach.**

## 2. What the vendor claims (E1, unverified)

Direct quotations, with what each does and does not establish.

> وب سرویس پویا از استاندارد **REST** و خروجی اطلاعات در قالب استاندارد **JSON**
> تبعیت می‌کند

→ protocol established. Nothing about content.

> کلیه اطلاعات معاملاتی لحظه‌ای بازار **و تاریخچه آن**، اطلاعیه‌ها، صورت‌های
> مالی و مجامع را به همراه اقلام محاسباتی همچون نسبت‌ها، نرخ‌های رشد و ضرایب

→ claims market data *and its history*, filings, statements, assemblies,
computed ratios. **No dates, no fields, no coverage, no granularity.**

> همزمان با انتشار اطلاعیه‌های شرکت‌ها در سامانه کدال … بصورت لحظه‌ای قابل
> دسترس … **ضمن بی‌نیاز کردن از سامانه کدال**

→ claims to obviate Codal. Hints — but does not state — that filings carry a
publication moment.

> مبلغ … با معیار hits … هر hit **دویست ریال** (برای اطلاعات قیمتی هر hit
> **صد ریال**) … Hits تعداد رکوردهای برگشت داده شده (**نماد، روز، قلم مالی**)

→ pricing established: 100 rials per price record, 200 otherwise.

> امکان استفاده از **سرویس آزمایشی** … عدم پیچیدگی مکاتبات اداری

→ **a trial is offered.** This is the single most actionable fact in the audit.

## 3. What is never mentioned, anywhere

Searched across the full 1.48 MB bundle and the vendor site. Every one of these
terms returns **zero** hits in any capability description:

| Term | Hits |
|---|---|
| عمق (depth) | **0** |
| دفتر سفارش (order book) | **0** |
| صف (queue) | **0** |
| مظنه (quote) | **0** |
| `orderbook` / `depth` (English) | **0** |
| publication date vs report date | never distinguished |
| حقیقی / حقوقی in the API description | **0** |
| adjustment policy (raw vs adjusted) | never stated |
| earliest available date | never stated |
| rate limits | never stated |
| retention policy | never stated |

## 4. The one structural argument worth weighing

Two independent observations point the same way on order-book depth:

1. **The pricing unit is a day.** A hit is defined as one *(symbol, day, item)*
   tuple. Order-book depth is inherently many snapshots *within* a day —
   SIRAT-AI's own `depth.py` produces 2,894 level-rows from a single daily
   capture of 586 symbols. A per-day billing unit is not how a depth product
   would be metered.
2. **The product describes itself as fundamental.** BourseView is
   "سامانه جامع تحلیل **بنیادی**" — a comprehensive *fundamental* analysis
   system. Microstructure is not what it sells itself as.

**This is inference, not proof, and it is recorded as inference.** The status
stays `UNKNOWN`, not `FAIL`. But it should shape expectations before money is
spent.

## 5. The four mandatory questions

### Q1 — Historical market depth → `UNKNOWN`

Every sub-question (earliest date, frequency, snapshot vs event, level count,
bid/ask fields, timestamp resolution, symbol coverage, retention, adjustment)
is **unanswerable from public evidence**. No sample obtainable without a trial.

Evidence available: zero mentions of depth/order-book/queue anywhere;
day-granular pricing unit; fundamental-analysis product positioning.

### Q2 — Historical price → `UNKNOWN`

"تاریخچه آن" is claimed. **No earliest date, no granularity, and — critically —
no adjustment policy is published.** Whether prices are `RAW`, `ADJUSTED`, or
adjusted by an undocumented method is `UNKNOWN`, and that alone blocks use in
research: SIRAT-AI already carries 361 impossible-return rows caused by
unadjusted corporate actions.

### Q3 — PIT financial statements → `UNKNOWN`

The claim "همزمان با انتشار … در سامانه کدال" *implies* the vendor tracks a
publication moment, because it markets latency against Codal. But **implying a
publication moment is not the same as serving `published_at` as a field**, and
no schema exists to check.

**Decision rule stands: if only period/report date is served,
`POINT_IN_TIME_FINANCIALS = FAIL`, however complete the data.**

### Q4 — Real/legal (حقیقی/حقوقی) flow → `UNKNOWN`

Not mentioned in any API capability description. The BourseView terminal
displays such data, but terminal display does not imply API exposure.

### Q5 — added by this audit: TLS chain integrity → `CONCERN`

`pouyafinance.com` presents an incomplete certificate chain. A production
collector forced to disable verification has no protection against
interception, which is unacceptable for data feeding trading decisions. **Must
be confirmed for the production API endpoints before any integration.**

## 6. What cannot be assessed at all without a trial

Phases 2–6 of the brief — real sample test, historical coverage test, PIT test,
depth acceptance test, data quality — require returned data. None exists.

Reporting these as anything other than `UNKNOWN` would be manufacturing a
result.

## 7. Audit reproducibility

| | |
|---|---|
| method | unauthenticated `GET`, ~1/second, via `providers.transport` |
| requests | ~40 across the whole audit |
| credentials | **none, at any point** |
| bundle cached | `bv_main.js`, 240,016 chars |
| TLS note | verification disabled **only** for reading `pouyafinance.com` public marketing content, never for data or credentials |
| code written | **none** |

## 8. The five questions to put to the vendor

In priority order. Answers 1 and 3 decide almost everything.

1. **Is historical order-book depth served?** If yes: earliest date, levels,
   snapshot or event, timestamp resolution, symbol coverage, retention.
2. Financial statements: **is `published_at` a field, distinct from
   `period_end`?** Are restatements versioned?
3. Price history: **earliest date, and raw or adjusted?** If adjusted, by what
   method, and are corporate actions exposed separately?
4. Is حقیقی/حقوقی flow served, from what date, daily or transaction-level?
5. Do the production API endpoints present a **complete TLS chain**?
