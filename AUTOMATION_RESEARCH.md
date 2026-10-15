# Automating this bot: what the field knows, what was verified here, what was built

**2026-10-15.** Commissioned to answer one request — *make the bot trade
automatically and watch the market continuously* — by surveying Iranian and
international practice and applying what holds up.

Four research threads ran in parallel: Iranian broker access and regulation,
intraday market-data architecture, live-trading safety engineering, and the
quantitative evidence on whether an edge is findable here at all. Roughly 500
web searches and the primary PDFs where they could be retrieved.

**Every load-bearing claim was re-measured against this machine, this feed and
this database before being acted on. Two were refuted that way. The
measurements, not the citations, are what the build rests on.**

---

## The answer in four sentences

**Continuous monitoring: buildable, and now built.** The whole market with
five-level order books arrives in one 0.9-second HTTP call, and a delta cursor
reduces a no-change poll to 644 bytes. Whole-market حقیقی/حقوقی arrives in
another 0.5-second call, replacing 316 seconds of per-symbol fetching.

**Automated order placement: not built, deliberately.** No Iranian broker
publishes a retail order API. The only working route is a reverse-engineered
private endpoint with a captcha in its login path and **no order-cancel
implemented**, in a market where roughly half of all sessions close at a price
limit. Building it would produce a bot that can enter positions it cannot exit
and cannot cancel.

---

## Part 1 — What was verified, and what did not survive verification

### 1.1 Refuted: the segmented no-limit regime

A source reported that Tehran now runs some names without price-fluctuation
limits. Checked against the exchange's own published `pMax`/`pMin` on the
2026-10-15 capture: **532 of 535 equities (99.4%) sit in the 3% band.** The
scatter is integer-rial rounding. `config.PRICE_BAND_PCT = 3.0` is correct and
was left alone.

*Partly true, in a way that matters less:* every non-3% band belongs to a
non-equity. The project applies a global constant where the exchange publishes
a per-instrument figure, which is now wrong for the gold funds the panel
carries.

### 1.2 Refuted: that the −4.50%/yr figure is inflation-contaminated

With Iranian inflation at **87.9% y/y** (Statistical Center of Iran, July
2026) against a 23% policy rate, a nominal return and an excess return differ
by an order of magnitude, and a research thread flagged this as the single
highest-value thing to check. Checked in `examples/edge_audit.py`:

```python
excess = book["realised_pct"] - book["market_pct"]
net    = float(excess.mean()) - cost - slip
```

**It is an excess over the market.** Inflation sits in both legs and
differences out. E19's headline number means what the project believes it
means. No change required.

### 1.3 Confirmed: base volume has been eliminated

**1,036 of 1,038 instruments carry `base_volume = 1`.** حجم مبنا — the damping
that stopped a closing price moving its full distance on thin turnover — is
gone, so a closing price is now a different object from the one every stored
number was computed against.

### 1.4 Confirmed, and worse than reported: the market shuts for months

The Tehran equity market traded **nothing at all from 2026-02-28 to
2026-05-18** — fifty sessions — plus eight in June 2025. **58 of 510 sessions
since 2024-09, one in nine.** TSETMC published a full phantom market on every
one of those days. Three independent witnesses agree; details and the code
that now detects it are in [MARKET_REGIME_AUDIT.md](MARKET_REGIME_AUDIT.md).

### 1.5 Confirmed: the delta cursor and the bulk flow call

| Call | Measured here |
|---|---|
| `GetMarketWatch&withBestLimits=true` | 996,728 B, 0.9 s, 1,038 instruments + 4,818 book levels |
| `&refID=<largest rid>` | **644 B, 0.1 s** when nothing changed |
| `GetClientTypeAll` | 519,336 B, **0.5 s**, 2,286 instruments |

The accounting identity `buy == sell` holds on the bulk flow payload with a
maximum absolute gap of **0.0** across all 2,286 instruments.

### 1.6 Confirmed, and it is a bug in this repository

`depth.content_hash()` **cannot detect a frozen feed.** It hashes the stored
tuples, and `captured_at` is element 1 of every one, so two byte-identical
market states a minute apart hash differently by construction. Demonstrated
with the same payload at two instants: `f40b0fa6…` and `98278115…`. Fixed by
adding `state_hash()`, which drops that element. The original is a correct
provenance fingerprint and was left intact.

### 1.7 Measured here, not found anywhere: قدرت خریدار survives without values

`GetClientTypeAll` publishes volumes and participant counts but **no rial
values**, which would normally disqualify it from computing buyer power. Over
**57,649 stored rows** carrying both:

| | |
|---|---|
| median relative difference | **0.0135%** |
| 95th percentile | 0.497% |
| correlation | **0.99999999** |

Both legs of one instrument trade at nearly one price within a session, so
price cancels in the ratio. **The project's primary flow signal is computable
intraday, from one call.**

---

## Part 2 — The evidence on whether an edge exists here

Reported because the request was to find what works, and this is what the
literature says works. It is unwelcome and it is well sourced.

### 2.1 The base rate

| Study | Population | Result |
|---|---|---|
| Barber & Odean (2000), *JF* | 66,465 US households | high-turnover households 11.4%/yr vs 17.9% market |
| Barber, Lee, Liu & Odean (2009), *RFS* | **every** Taiwanese investor | individuals lose 3.8pp/yr = 2.2% of GDP |
| Barber et al., Taiwan day traders | 1992–2006 | **<1%** predictably profitable net of fees |
| Chague et al. | **all** 19,646 Brazilian futures day traders | of those persisting >300 days, **97% lost money** |
| ESMA / FCA | EU and UK retail CFD accounts | 74–89% / 82% lose money |

The professional comparison is the relevant one for a systematic research
operation: **5–7% of 7,355 hedge funds showed significant alpha** under an
eight-factor model, and the share with positive significant alpha fell from
~20% to ~10% over the sample.

### 2.2 The finding that lands directly on this project's own work

**Chen, Gao, He, Jiang & Xiong (2019), *J. Econometrics* 208**, on
account-level Shenzhen data: after an upper-limit close, price continues for
two more days (peak **+2.59%**) and then reverses to **−1.21% cumulative by
day 120**. And:

> **83.6% of large investors who buy on an upper-limit day sell the very next
> day.**

Corroborated independently by Zeng et al. (2024, *FRL*) and Lin et al. (2023,
*JBF*), the latter finding the effect **stronger among retail-held stocks**.

This is the external replication of what E19 found internally and called an
artifact: *"the top decile is 3.03× more likely to be limit-locked at the
moment it would be bought."* Two methods, two markets, one conclusion. **A
long-only strategy that buys into limit-up strength is taking the losing side
of a documented professional trade**, and Iran — like China — has no effective
short selling, so the leg where the alpha lives is unavailable.

### 2.3 The reframe worth keeping

E21's flow→lock model (IC 0.082, t 56, one-session horizon) is **a good
execution model and a bad alpha model.** As an exclusion rule — *do not size
into a name you will not be able to exit* — it keeps all of its measured
value without being asked to generate return. That costs nothing and discards
nothing.

### 2.4 The forward window cannot settle the question

Using Lo (2002)'s standard error for the Sharpe ratio, `SE(SR) ≈
√((1+SR²/2)/T)`: with ~240 daily observations, clearing t = 2 requires a
**realized annualized Sharpe of about 2.0**; at Harvey's recommended t = 3
bar, about 3.0. R-FORWARD-2027 has roughly **26% power against a true Sharpe
of 1.0**.

**"Wait for the forward window to decide" is not a decision procedure.** The
window will return inconclusive for any realistic edge. This is arithmetic.
The forward log should continue — it costs one scheduled job and it is the
only prospective evidence this programme will ever have — but it must not be
described as the thing that will settle the question.

### 2.5 The test that should be run before any other

**Barber, Lin & Odean (2024), *JFQA* 59(6)**: retail order imbalance earns
**+13.0 bps equal-weighted and −10.1 bps trade-value-weighted** over one week.
Same signal, same stocks, **the sign flips with the weighting scheme** — and
long-short strategies on heavy-retail stocks returned **−14.8% annualized**
against +6.6% elsewhere.

Tehran is, in its entirety, the heavy-retail subsample. **Re-running the
حقیقی/حقوقی work under value weighting and trade-value weighting is the
highest-value single experiment available**, and it is a successor that must
be preregistered, not a diagnostic to be run while the answer is visible. It
is recorded here and nowhere else, unrun.

A second lead from the same literature: Jones, Shi, Zhang & Zhang (2025,
*JFQA* 60(1)) find **small Chinese retail accounts cannot predict returns
while large ones can, and are contrarian.** TSETMC publishes buy/sell counts
alongside values, so `value ÷ count` gives average ticket size per participant
— a direct proxy for that split. As far as the search found, **untested on TSE
data.**

### 2.6 The inflation frame the project does not currently carry

`RISK_FREE_RATE = 0.23` is the correct Sharpe benchmark — Sharpe requires a
differential return — but it is not a real return. Against 87.9% inflation a
23% deposit loses **34.5%** of its purchasing power a year. Dimson, Marsh &
Staunton over 2,558 country-years: in the **highest 5% inflation bucket, real
equity returns average −10.0%/yr.** Iran sits in the high-inflation,
low-growth cell where the historical average is about **−4.7%/yr real**.

> **"It is important to distinguish between beating inflation and hedging
> against inflation."** — DMS

Emerging-market equity inflation betas run ≈1.00 with tight standard errors —
equities *hedge* inflation. Gold does marginally better on the unexpected
component (β 1.06). Neither *beats* it. The project already tracks the gold
funds; that panel is doing more work than it was given credit for.

---

## Part 3 — What was built

| Built | Why |
|---|---|
| `market_session.py` | Did the market trade? Asked of turnover and flow publication, never of a calendar. 15 tests. |
| `providers.get_client_type_all()` | Whole-market flow in one call, with the eight field names declared so a rename fails loudly. 12 tests. |
| `depth.state_hash()` | A market fingerprint with the capture instant removed, so a frozen feed is detectable. |
| `depth._utf8_stdout()` | An encoding fault could end a capture that cannot be retaken. |
| `monitor.py` | The session watch: two cadences, session-gated, event transitions, a tick ledger. 20 tests. |

### The monitor's design, and the arithmetic behind it

A full capture is 5,856 rows. Over 240 sessions:

| cadence | rows/year |
|---|---|
| every 1 min | 295.1 M — would bury this database |
| every 5 min | 59.0 M |
| **every 15 min** | **19.7 M — ten times `price_history`** |
| every 30 min | 9.8 M |

So: **a full book-and-flow capture every 15 minutes, and an event poll every
60 seconds** using the delta cursor. Minute-resolution awareness of
transitions; quarter-hour resolution of the whole book. That is what "watch
continuously" can honestly mean on this storage budget.

Four rules the monitor holds to:

1. **It observes; it does not signal.** Nothing reaches `factor_engine`,
   `swing_advisor` or the PIT firewall. Events are *definitions* — "the ask
   side at level one is empty while the bid side is not" has no free parameter
   in it. There is deliberately no "volume is 2.5× average".
2. **A restart is not a market event.** The first poll sets a baseline and
   emits nothing, or restarting at 11:00 would report 300 queues forming.
3. **Unknown is never False.** A gap in the feed produces no event, in either
   direction — `e21_panel`'s rule applied to the book.
4. **Every tick is a row, including the ones that collected nothing.**
   Intraday data cannot be backfilled, so an unrecorded gap becomes
   indistinguishable from a motionless market.

---

## Part 4 — What was NOT built, and why

### 4.1 Order placement

| Route | Status |
|---|---|
| مفید / Mofid | Official answer: *"معاملات الگوریتمی در سامانه مفید تریدر امکان‌پذیر نمی‌باشد"* |
| آگاه / Agah | Customer club states API access is not available |
| فارابی | In-platform conditional orders only, activated by phone |
| تدبیرپرداز، صحرا | **Sell a real algorithmic-trading API — to brokers, not to individuals** |
| Python ecosystem | `pytse-client`, `oxtapus`, `finpy-tse`, `5j9/tsetmc`, `tsetmc-api` — **every maintained package is read-only** |

The one project found that places orders drives a private Agah endpoint. It
has a **captcha in the login path**, so unattended re-authentication is
impossible, and **cancellation is not implemented at all**.

**Regulation.** Algorithmic trading was banned outright on 2 Mehr 1399
(23 Sep 2020) and unbanned conditionally on 30 Farvardin 1400 (19 Apr 2021)
under eight conditions — supervisory infrastructure, operational sign-off from
both exchanges, technical sign-off from TSETMC, security sign-off, periodic
logs. **All eight attach to the broker.** A retail user does not hold a
licence; they get access through a licensed broker's registered system with a
شناسه الگوریتم issued through that broker. A self-written bot on a private
endpoint satisfies none of them.

**The concrete next step costs a phone call, not a sprint.** Ask two or three
licensed brokers: is there a programmatic order API for an individual client,
what does شناسه الگوریتم registration require, and what does it cost. Every
public source stops at "contact the brokerage"; that question is unanswerable
from the open web and it decides whether the execution half is legal before it
decides whether it is buildable.

### 4.2 The safety controls, and the honest reason they are specified not coded

Stage 6 of this platform's own pipeline requires four controls with a named
artifact each. They are specified below rather than implemented, because a
kill switch with nothing to kill is the "inventory, not analysis" failure the
SEC cited Knight Capital for — *"the assessment largely focused on compiling
an inventory of existing controls … it did not consider the consequences of
potential malfunctions."*

**The one that is specific to this market, and the most likely way this bot
loses money.** With ~47% of sessions closing at a limit, an order will often
rest unfilled for days. A system comparing a target position against a *held*
position will re-send it every session until the lock breaks, then fill all of
them at once:

> wants 10,000 shares → limit-up, unfilled → position still 0 → sends 10,000
> again → …day five the lock breaks → **owns 50,000 shares of an illiquid name
> it sized at 10,000**

**Every position limit and every target-vs-current difference must be computed
as `position + working orders`.** That is Knight's SEC finding B verbatim
(*"position limits … did not account for the firm's exposure from outstanding
orders"*), and here it is caused by market structure rather than by any bug.

The rest of the Tier-0 list, each traceable to a regulator, a trade body, or a
documented incident:

| Control | Shape | The incident |
|---|---|---|
| Idempotent submission | deterministic client order id persisted **before** sending; after an ambiguous response query by id, never blind-retry | SEC Rule 15c3-5 names duplicate orders explicitly; a single timeout doubles a position |
| Kill switch | halt state in the DB, read **before every order**, fail-closed when unreadable, manual re-arm only | every incident in the record ran longer than it should have; an in-memory flag is cleared by the scheduler that restarts the process |
| Pre-trade hard limits | max shares **relative to that name's ADV**, max notional per order and per day, max position, gross exposure, cash check; refuse to send where no size limit is configured | Everbright 2013 is literally the absence of a notional ceiling (¥7.27bn filled, record ¥523m fine); Mizuho 2005 sent an order 41× the company's share count |
| Price collar | tied to the **daily band**, not a fixed percentage | Knight's 9.5% collar never fired — the erroneous orders moved prices by less than that |
| Staleness / instrument-state gate | `market_session` and `screening._gate_stale` as a **block**, not a filter; "no data" halts | the eleven-week closure, and FIA §3.1 |
| Heartbeat | external service alerts when pings **stop** | at a few trades a week, a bot that died a month ago looks exactly like a bot with no signals |
| Reconciliation | query the broker on startup, **the broker wins**, a mismatch halts | Samsung Securities 2018: 2.81bn phantom shares sold because every layer trusted its own book |
| Written incident procedure | halt first, diagnose with the order flow **off**, lift only when the cause is *identified* | Knight spent 45 minutes debugging live and its attempted fix made things worse |

**A naming correction for `sirat/experiments/gates.py`.** The `reconciliation`
check is described as *"how paper fills are compared with the tape, daily"*.
That is a **fill-realism** check. Reconciliation compares against an
*independent external record*, which does not exist until there is a broker.
Letting the first satisfy a box named the second is the substitution that
makes a control register lie. Both are needed; they are different stages.

**And the metric correction for this market.** The standard paper-vs-live
divergence metric is slippage. Here the dominant divergence will be **fill
rate** — the order that never filled because it sat behind a queue at the
limit. Unfilled-order rate and days-to-fill should be the headline; price
slippage secondary.

---

## Part 5 — What to do next, in order

1. **Install the monitor on a schedule** so the session data starts
   accumulating. It cannot be backfilled; every session not captured is gone.
2. **Start a paper account that actually advances daily.** Stage 6 requires 60
   paper sessions; the database holds 8 rows, all test accounts from August,
   the longest having run 2 sessions. This is a clock that has not been
   started, and starting it costs one line in the evening job.
3. **Make the phone call** about broker API access. It is free and it decides
   whether Part 4.1 is a permanent answer or a temporary one.
4. **Preregister the weighting test** (§2.5). It is the one experiment the
   literature says is most likely to change what this programme believes about
   its own best result.
5. **Only then** build the execution path, Tier 0 first, against a real broker
   contract.

---

## Sources

The four research threads produced roughly 120 cited sources. The ones
load-bearing for a decision above:

**Safety and controls** — [SEC Admin. Proc. 34-70694, Knight
Capital](https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf) ·
[FIA, *Best Practices for Automated Trading Risk Controls*
(2024)](https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf)
· [SEC Rule
15c3-5](https://www.federalregister.gov/documents/2010/11/15/2010-28303/risk-management-controls-for-brokers-or-dealers-with-market-access)
· [MiFID II RTS
6](https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160719-rts-6_en.pdf)
· [Concretum, *Building Reliable Trading
Systems*](https://concretumgroup.com/building-reliable-trading-systems-algorithmic-trading-automation/)

**The edge question** — [Barber & Odean
(2000)](https://faculty.haas.berkeley.edu/odean/papers/returns/individual_investor_performance_final.pdf)
· [Barber et al., Taiwan day
traders](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trading%20and%20Learning%20110217.pdf)
· [Chague et al., Brazil](https://www.ssrn.com/abstract=3423101) · [Chen, Gao,
He, Jiang & Xiong, price
limits](https://wxiong.mycpanel.princeton.edu/papers/PriceLimit.pdf) ·
[Barber, Lin & Odean (2024)](https://doi.org/10.1017/S0022109023000601) ·
[Hou, Xue & Zhang, *Replicating
Anomalies*](https://global-q.org/uploads/1/2/2/6/122679606/houxuezhang2020rfs.pdf)
· [Jensen, Kelly & Pedersen](https://www.nber.org/papers/w28432) · [Novy-Marx
& Velikov, trading
costs](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf) · [Lo,
*The Statistics of Sharpe
Ratios*](https://traders.studentorg.berkeley.edu/papers/The-Statistics-of-Sharpe-Ratios.pdf)

**Iranian access and regulation** —
[BoursePress, the 1399 ban](https://boursepress.ir/news/158294) · [Mehr, the
eight conditions](https://www.mehrnews.com/news/5192979) · [Tahlil Bazaar,
licensed brokers](https://www.tahlilbazaar.com/news/183412) ·
[BabakEslami/tse-market-data](https://github.com/BabakEslami/tse-market-data)

**Market context** — [Statistical Center of Iran inflation via Trading
Economics](https://tradingeconomics.com/iran/inflation-cpi) · [Bekaert & Wang,
inflation
betas](https://business.columbia.edu/sites/default/files-efs/pubfiles/4878/InfRPJuly_14_2010.pdf)
· [Dimson, Marsh &
Staunton](https://cdn.e-fundresearch.com/files/AacU5vr8pY843Op5PjbmA1IvxfM3mPgMUgvTW3j9.pdf)

**A standing caveat on the secondary sources.** Two search summaries were
caught materially misstating primary results during this work, and one claim
about Tehran's price-limit regime did not survive contact with the exchange's
own published figures. Anything above not traced to a primary document, or to
a measurement taken on this machine, should be treated as provisional.
