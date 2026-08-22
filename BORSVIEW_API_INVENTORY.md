# BORSVIEW API INVENTORY — discovered from the client, blocked at the door

**2026-08-22.** API Discovery for the logged-in BourseView account.

**Authentication outcome: `BLOCKED_BY_WAF`.** Not FAIL (the credential was
never evaluated), not PASS. A Web Application Firewall rejected the automated
login request before it reached the auth layer. Details in §4.

**No credential value was ever printed, logged, stored, or placed in a URL.**
The classic auth path uses POST with a JSON body, so the password was never at
risk of appearing in a query string. `SECRET_SCAN = CLEAN`.

---

## 1. Architecture, as discovered from the client bundles

Everything here comes from reading 6.6 MB of the app's own JavaScript, served
publicly. No endpoint was guessed.

| | |
|---|---|
| App | `PooyaBiNewApplication` — an **AngularJS** SPA at `/home/` |
| REST base | `https://www.bourseview.com` |
| REST prefix (authenticated) | `/api/v1/`, with some `/api/v2/` and `/api/v3/` |
| Public prefix | `/public/` (auth, account) |
| Live market data | **WebSocket only** — `wss://www.bourseview.com/socket/market-data`, via Lightstreamer (`afrapushv7.emofid.com`) |
| Config source | `/jsbin/env.min.js` |
| Protection | **Web Application Firewall** in front of the API |

### Note recorded, not acted on

`env.min.js` (public) exposes a **shared Lightstreamer username and token**
belonging to Mofid's infrastructure, not to this account. **It was not
extracted, used, or stored**, and the local file that briefly held it was
deleted. Using a credential that is not ours is out of scope regardless of
whether it is exposed. This is a vendor hygiene finding, reported here without
the values.

## 2. Authentication mechanism

From `authenticateUserPass` in the bundle:

```
POST /public/account/authenticate/classic
Content-Type: application/json
body: { username, password, token }     // token = captcha token, empty when hidden
```

Alternate methods present: `/authenticate/mofid` (GET, params), `/google`,
`/linkedin`, `/yahoo`. CAPTCHA is **conditional** — the client carries
`ignoreCaptcha`, `showCaptcha`, `tries=1`, so it appears after failed attempts.
This is why only **one** login attempt was made.

## 3. Endpoint inventory (from the client, NOT verified live)

Every endpoint below is `UNKNOWN` for response content — the WAF blocked live
verification. Grouped by the View that calls each.

### Fundamental
`/balancesheet` · `/incomestatement` · `/cashflow` · `/keystats` ·
`/revenue` · `/cogs` · `/sganda` · `/otherRevenue` · `/nav` · `/invTurnover` ·
`/fundamentalchart` · `/fundamentalstatus` · `/keyratios`

### Filings / Codal
`/filings`

### Price & technical
`/historicalPrice` · `/technicalanalysis` · `/technicalchart`

### Index-level mirrors
`/indexhistoricalprice` · `/indexbalancesheet` · `/indexincomestatement` ·
`/indexcashflow` · `/indexkeystats` · `/indexrevenue` · `/indextechnicalanalysis`

### Ownership / flow
`/insind` — **حقیقی/حقوقی** (individual/institutional). The one flow endpoint.

### Market / account
`/market` · `/overall` · `/shareholders/` · `/portfolio` · `/profile` ·
`/api/v2/subscriptions` · `/api/v2/plans` · `/api/v3/financialModeling/`

### **Order-book / depth**
**NONE.** See §5.

## 4. Why authentication is BLOCKED_BY_WAF, and why the audit stops here

The login POST — with the exact shape the app uses, browser headers, a warmed
session cookie, and the physical-NIC binding — returned **HTTP 200 with an HTML
body titled `WAF`**, no `set-cookie`, no JSON. The response came from a firewall,
not the auth service. **The credential was never evaluated.**

A WAF is an anti-bot control. Getting past it would require changing the TLS
fingerprint, rotating User-Agents, mimicking header order, or other
detection-evasion — exactly what the brief prohibits ("circumvent … anti-bot",
"No security bypass") and lists as a stop condition. **A valid subscription does
not change this**: the WAF is a deliberate vendor control against automated
access, and evading it evades that decision.

So: `AUTHENTICATION = BLOCKED_BY_WAF`, and the audit stops at Phase 3 as
designed. Phases 5–14 (live samples, coverage, PIT, depth acceptance, quality)
cannot run, and their gates stay `UNKNOWN`.

## 5. The depth question — answered without authentication

This is the one verdict the WAF does **not** block, because it was settled from
the client code, which is complete and public.

**In 6.6 MB of the full application bundle, there is no depth / order-book /
queue endpoint of any kind.** Not under `/api/`, not under `/public/`, not in
any name — Persian or English. The terms `depth`, `orderbook`, `bestLimit`,
`مظنه` never appear in any URL context. `bid`/`ask`/`صف` appear only in the
**live WebSocket** handling.

An application cannot display history it has no endpoint to fetch. Therefore:

> **`HISTORICAL_DEPTH = FAIL`.** BourseView serves order-book depth as a **live
> WebSocket stream only**. There is no historical depth REST endpoint.

The "10+ years in the UI" the user sees is `/historicalPrice` — a decade of
**price** history, which is not order-book depth. This is precisely the
distinction the brief insists on: a historical price chart is not order-book
history.

## 6. Reproducibility

| | |
|---|---|
| method | read public bundles + one authenticated POST |
| bundles cached | `PooyaBiNewApplication` (1.16 MB), `lib` (3.83 MB), `lib2`, `lib3`, `grid-box` |
| live data retrieved | **none** — WAF blocked it |
| credentials | read from env, never printed/stored/logged |
| TLS | full verification throughout the auth attempt (no `verify=False`) |
| vendor socket token | seen in public config, **not used**, local copy deleted |
