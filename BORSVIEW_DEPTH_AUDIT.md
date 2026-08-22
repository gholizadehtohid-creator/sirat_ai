# BORSVIEW DEPTH AUDIT

**2026-08-22. The one question this whole evaluation existed to answer.**

# VERDICT: HISTORICAL_DEPTH = FAIL (live-only)

BourseView does **not** provide historical order-book depth through any
machine-readable interface. It streams **live** depth over a WebSocket and
keeps no queryable history of it.

This verdict does **not** depend on authentication, which was blocked by a WAF.
It was established from the application's own complete client code, which is
public.

---

## How this was determined

The BourseView web app (`PooyaBiNewApplication`, AngularJS) was downloaded in
full — 6.6 MB across five bundles — and every data endpoint it can call was
extracted from the source. An app cannot render data it has no endpoint to
request, so the endpoint list is authoritative about what the service exposes.

## Test-by-test, against the brief's Phase 10

| Test | Question | Result |
|---|---|---|
| **A** Historical availability | is historical depth queryable? | **NO** — no historical depth endpoint exists |
| **B** Data structure | bid/ask levels with volumes? | live stream only; no historical schema to record |
| **C** Frequency | snapshot / multiple / event? | **live event stream** (Lightstreamer push); not stored |
| **D** Timestamp resolution | date / minute / second? | N/A for history — nothing is retained |
| **E** Depth levels | how many levels? | live only; irrelevant to a non-existent history |
| **F** UI/API match | does UI history equal API history? | **cannot match** — the UI's history is *price*, not depth |

### EARLIEST_DEPTH_DATE / LATEST_DEPTH_DATE

**N/A.** There is no historical depth series to have a start or end date.

## The evidence, concretely

1. **No depth endpoint.** Across `/api/v1/`, `/api/v2/`, `/api/v3/` and
   `/public/`, there is no `depth`, `orderbook`, `bestLimit`, `queue` or `مظنه`
   endpoint. The complete data-endpoint list is in `BORSVIEW_API_INVENTORY.md`
   §3 and every entry is price, fundamental, technical, flow, or account.

2. **Depth lives on the live socket.** `env.min.js` declares
   `MARKET_DATA_SOCKET = wss://www.bourseview.com/socket/market-data`
   (Lightstreamer). The tokens `bid`/`ask`/`صف` appear in the bundle **only**
   in WebSocket-handling code — a push stream, which is by nature live, not
   historical.

3. **The historical views are price and fundamentals.** The app's View files
   include `HistoricalPrice.html`, `BalanceSheet.html`, `IncomeStatement.html`,
   `KeyRatios.html`, `Filings.html` — and **no** `Depth.html` or
   `OrderBook.html`.

## The distinction the user's report turns on

The user reports seeing "10+ years of depth" in the UI. Against the evidence,
that is almost certainly one of:

- **`/historicalPrice`** — a decade of OHLCV price history (real, and BourseView
  clearly has it), **which is not order-book depth**; or
- a **live** depth widget that only ever shows *now*, alongside a price chart
  that shows ten years.

The brief names this exact trap: *a historical price chart is not order-book
history; a daily depth summary is not intraday order-book history.* Nothing in
the client code supports a queryable historical order book.

**If a live-authenticated session later reveals a depth history endpoint this
static analysis missed, this verdict should be revisited — but the endpoint
would have to exist in the client, and it does not.**

## What this means for SIRAT-AI

Historical order-book depth remains SIRAT-AI's one irreplaceable gap, and **no
vendor examined can fill it retroactively** — depth that was not recorded as it
happened cannot be reconstructed.

The only path is forward capture, which SIRAT-AI already started:
`depth.py`, one capture held, needs scheduling. BourseView changes nothing
about that conclusion.
