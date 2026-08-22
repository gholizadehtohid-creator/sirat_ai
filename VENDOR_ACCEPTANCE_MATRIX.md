# VENDOR ACCEPTANCE MATRIX — وب سرویس پویا

**2026-08-22.** Status is `UNKNOWN` wherever documentation or a real sample is
absent. Only `G1` has evidence strong enough for `PASS`.

---

## Requirement matrix

| Requirement | Evidence | Status | Confidence | Research impact if PASS |
|---|---|---|---|---|
| Official machine access | vendor states REST/JSON, publishes per-record pricing, offers a trial, gives phone/email/Telegram | **PASS** | **HIGH** | removes the governance impasse entirely |
| Historical order-book depth | **zero** mentions of عمق/دفتر سفارش/صف/depth anywhere; day-granular billing unit; product self-describes as *fundamental* analysis | **UNKNOWN** *(inference: unlikely)* | LOW | would close SIRAT-AI's only irreplaceable gap |
| Historical price | "تاریخچه آن" claimed; no earliest date, no granularity, **no adjustment policy** | **UNKNOWN** | LOW | would validate/replace 1.9M existing rows |
| PIT financials (`published_at` ≠ `period_end`) | marketing implies a publication moment ("همزمان با انتشار … کدال") but no schema confirms a field | **UNKNOWN** | LOW | would unblock the fundamental panel outright |
| Real/legal flow (حقیقی/حقوقی) | not mentioned in any API capability text | **UNKNOWN** | LOW | would extend/validate the E21 panel |
| Corporate actions | never mentioned | **UNKNOWN** | LOW | would explain the 361 impossible rows |
| Data quality | no sample obtainable | **UNKNOWN** | — | — |
| Historical coverage | no dates published anywhere | **UNKNOWN** | — | — |
| Rate limits | never stated | **UNKNOWN** | — | — |
| Retention policy | never stated | **UNKNOWN** | — | — |
| Reproducibility of vendor data | no schema, no versioning, no field list to pin | **UNKNOWN** | — | — |
| TLS chain integrity | `pouyafinance.com` chain **incomplete**; production endpoints untested | **CONCERN** | MEDIUM | a collector forced to `verify=False` is unacceptable for trading data |

---

## VENDOR DATA GATE

| Gate | | Basis |
|---|---|---|
| **G1** Official machine access | **PASS** | REST/JSON declared, priced, trial offered, contactable |
| **G2** Historical depth | **UNKNOWN** | no evidence either way; inference says unlikely |
| **G3** Historical price | **UNKNOWN** | claimed, undated, adjustment policy unstated |
| **G4** PIT financial data | **UNKNOWN** | implied by marketing, unconfirmed by schema |
| **G5** Real/legal historical flow | **UNKNOWN** | never mentioned |
| **G6** Data quality | **UNKNOWN** | no sample |
| **G7** Historical coverage | **UNKNOWN** | no published dates |
| **G8** Reproducibility | **UNKNOWN** | nothing to pin a result to |
| **G9** Research value | **UNKNOWN** | follows G2–G5 |
| **G10** Economic justification | **UNKNOWN** | price known, value unknown |

**One PASS, nine UNKNOWN, one CONCERN.** That distribution is the honest state
of the evidence, not a failure of the audit: nine of the ten gates require
returned data, and no data can be returned without a trial.

---

## Research value scoring — WITHHELD

The brief specifies weights (depth 35%, PIT financials 25%, price 15%, flow
15%, coverage 10%) and two scores.

**Both are withheld.** Scoring `UNKNOWN` inputs produces a number that looks
like a measurement and is not one — precisely the failure mode SIRAT-AI's
governance layer exists to prevent. A score computed from unknowns would be
quoted later as if it were evidence.

The weights are recorded and will be applied **after** a trial, when G2–G7 carry
real verdicts.

---

## The rule that will apply once G2 and G4 resolve

Stated in advance so it cannot be softened after the fact:

> **If historical depth is absent:** Vendor may still be valuable, but it does
> NOT solve SIRAT-AI's historical market-depth bottleneck.

> **If financial publication date is absent:** Vendor financial data cannot be
> treated as a full Point-in-Time replacement for Codal.
