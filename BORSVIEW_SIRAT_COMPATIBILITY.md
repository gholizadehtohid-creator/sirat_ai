# BORSVIEW ↔ SIRAT-AI COMPATIBILITY

**2026-08-22.** Structural compatibility only. **No integration performed, none
approved.** This asks *if the vendor's data could one day connect*, not whether
it should — and the answer everywhere is gated behind an access path that does
not yet exist for machines.

---

## Compatibility, per SIRAT-AI subsystem

| SIRAT-AI layer | Candidate vendor endpoint | Structural fit | Blocker |
|---|---|---|---|
| `market_data` | `/historicalPrice`, `/market`, `/overall` | plausible — same daily OHLCV shape | WAF; adjustment policy UNKNOWN |
| `financials` | `/balancesheet`, `/incomestatement`, `/cashflow` | plausible | WAF; **`published_at` UNKNOWN** |
| `fundamental_scoring` | `/keystats`, `/keyratios`, `/fundamentalstatus` | plausible | same as financials |
| technical indicators | `/technicalanalysis`, `/technicalchart` | SIRAT-AI computes its own via `ta`; vendor's would be a **cross-check**, not a source | WAF; raw-vs-calculated UNKNOWN |
| `backtesting` / PIT | `/filings` | **only if** `published_at` is served | WAF; PIT UNKNOWN — the deciding unknown |
| flow research | `/insind` (حقیقی/حقوقی) | plausible — matches `client_type_history` | WAF; coverage/history UNKNOWN |
| `execution` | — | **no fit** — needs historical depth, which does not exist | `HISTORICAL_DEPTH = FAIL` |
| `portfolio` / `risk` | `/portfolio`, `/shareholders/` | account-specific, not research inputs | WAF |

## The three honest conclusions

1. **Execution/liquidity research gets nothing.** The layer that most needs new
   data — historical depth for impact and fill modelling — is exactly the one
   BourseView cannot supply. `HISTORICAL_DEPTH = FAIL`.

2. **Fundamentals and flow are *potentially* compatible, pending two unknowns:**
   whether the WAF permits any sanctioned machine access, and whether filings
   carry publication dates. Until both resolve, connection is not assessable.

3. **Technicals would be redundant.** SIRAT-AI already computes indicators from
   price via `ta`. Importing vendor-computed indicators adds a dependency and a
   provenance question without adding information.

## What compatibility does NOT mean

Structural fit is not permission and not value. Even if the WAF had a sanctioned
bypass and every field were perfect, the integration ban in the brief stands
until a trial proves content — and this audit could not obtain content.

## The unchanged recommendation

SIRAT-AI's own forward capture (`depth.py`) is the only route to the one dataset
that matters most, and no compatibility analysis changes that. Everything the
vendor *might* offer is either redundant (technicals), unverifiable behind the
WAF (fundamentals, flow), or absent (depth).
