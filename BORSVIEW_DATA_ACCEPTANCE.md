# BORSVIEW DATA ACCEPTANCE

**2026-08-22.** Capability status after API discovery. Live verification was
blocked by a WAF, so every content claim is `UNKNOWN` — established endpoints
exist in the client, but none returned data to this audit.

`VERIFIED` requires a real sample. None was obtainable. The honest column is
mostly `UNKNOWN`, and that is the finding, not a shortfall.

---

## Acceptance table

| Capability | UI | API endpoint exists | Historical | Earliest date | Schema | PIT | Status |
|---|---|---|---|---|---|---|---|
| OHLCV price | yes | `/historicalPrice` | claimed | UNKNOWN | UNKNOWN | n/a | **endpoint exists, content UNKNOWN** |
| Technical indicators | yes | `/technicalanalysis`, `/technicalchart` | UNKNOWN | UNKNOWN | UNKNOWN | n/a | endpoint exists, content UNKNOWN |
| Balance sheet | yes | `/balancesheet` | UNKNOWN | UNKNOWN | UNKNOWN | **UNKNOWN** | endpoint exists, content UNKNOWN |
| Income statement | yes | `/incomestatement` | UNKNOWN | UNKNOWN | UNKNOWN | **UNKNOWN** | endpoint exists, content UNKNOWN |
| Cash flow | yes | `/cashflow` | UNKNOWN | UNKNOWN | UNKNOWN | **UNKNOWN** | endpoint exists, content UNKNOWN |
| Key ratios | yes | `/keystats`, `/keyratios` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | endpoint exists, content UNKNOWN |
| Filings / Codal | yes | `/filings` | UNKNOWN | UNKNOWN | UNKNOWN | **UNKNOWN** | endpoint exists, content UNKNOWN |
| **حقیقی/حقوقی flow** | yes | `/insind` | UNKNOWN | UNKNOWN | UNKNOWN | n/a | endpoint exists, content UNKNOWN |
| Shareholders | yes | `/shareholders/` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | endpoint exists, content UNKNOWN |
| Market overview | yes | `/market`, `/overall` | UNKNOWN | UNKNOWN | UNKNOWN | n/a | endpoint exists, content UNKNOWN |
| **Historical order-book depth** | live widget | **NONE** | **NO** | **N/A** | **N/A** | n/a | **FAIL — no endpoint** |
| Live depth | yes | WebSocket `/socket/market-data` | **live only** | N/A | UNKNOWN | n/a | live stream, not historical |
| Corporate actions | UNKNOWN | not found | UNKNOWN | UNKNOWN | UNKNOWN | n/a | not found in client |

## What is actually established

**Positive:** BourseView's app calls a rich set of REST endpoints for price,
fundamentals, technicals, filings, and حقیقی/حقوقی flow. The endpoints exist in
the client and the account presumably reaches them through the browser.

**Blocking:** a WAF rejects automated requests, so none of these could be
sampled. Whether they return usable, historical, PIT-safe data is `UNKNOWN` —
and cannot be resolved without either an access path the vendor sanctions for
machines, or evading the WAF, which is out of scope.

**Decisive:** the one capability SIRAT-AI most needs — historical depth — is
`FAIL` on evidence that does not depend on the WAF at all.

## The two conditional rules, resolved where possible

> **Historical depth absent** → confirmed. Vendor does NOT solve SIRAT-AI's
> historical market-depth bottleneck.

> **Financial publication date** → `UNKNOWN`. The `/filings` and statement
> endpoints exist, but without a live sample we cannot confirm they serve
> `published_at` distinct from `period_end`. Vendor financial data therefore
> **cannot yet be treated as a PIT replacement for Codal** — not proven unsafe,
> merely unproven.
