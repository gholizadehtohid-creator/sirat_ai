# Political & Economic Intelligence — Phase 1 Audit & Architecture Proposal

**2026-08-22. Read-only audit. NO code was changed. No main project file was
modified.** Every claim below carries file:line evidence gathered by a
parallel read of the eight relevant subsystems.

> **SCOPE NARROWED BY THE USER, 2026-08-23.** News is to be used as an
> **analytical report ONLY — never as a trading signal.** It must NOT feed the
> Decision Engine / catalyst factor, and it is displayed in **its own dashboard
> window**. Sources must include **external news, central-bank (بانک مرکزی)
> news, and other useful feeds.** This is exactly the "Information Value = YES,
> Trading Alpha = NO" outcome §33 of the brief anticipated. Consequently the
> heavy signal-path governance below (E_NEWS_01, signal backtest, catalyst
> wiring, edge claims, look-ahead-into-decisions) is OUT OF SCOPE:
> `factor_engine` / `swing_advisor` / `pit` are NOT touched for signalling. The
> build reduces to: sources → collect → normalize → dedup → classify →
> map-to-market → **report panel**. The "no fabrication / honest absence"
> principles still fully apply to the report itself. With no ML/LLM dependency,
> the "analysis" is rule-based (keyword→category→market) and any directional
> lean is labelled a heuristic reading, never a signal.

The headline, stated first because it reframes the whole mission:

> **This is not a greenfield build. SIRAT-AI already contains a complete,
> PIT-disciplined news→catalyst→Decision-Engine skeleton — an interface, a
> factor slot, a point-in-time firewall, and a dormant DB hook — with one
> thing missing: data, and the collector/intelligence layer that would produce
> it.** The mission is to fill that skeleton, not to lay a new one beside it.

---

## 1. Current Architecture Audit

The decision path already has a named seam for news at every layer:

| Layer | File | What exists today |
|---|---|---|
| Factor model | `factor_engine.py:545` `swing_score()` | 6 factors, weight-renormalised over those present |
| Weights | `config.py:977-982` | regime 0, momentum 25, technical 20, fundamental 30, liquidity 15, **catalyst 10** |
| Decision | `factor_engine.py:639` `decide()` | → BUY / HOLD / SELL / AVOID |
| News factor | `factor_engine.py:460` `catalyst()` | built and wired, **produces no score** |
| Assembly seam | `factor_engine.py:826` `evaluate_symbol(..., news_reading=None, events=None)` | **the pre-built independent-factor hook** |
| Live feed seam | `swing_advisor.py:214` | calls `evaluate_symbol` but **omits `news_reading`** |
| Research feed seam | `backtesting/swing_strategy.py:386` + `pit.news_as_of()` | dated, PIT-gated, also unfed |
| News interface | `news.py` | `NewsItem`, `aggregate()`, `to_score()`, `NewsProvider` ABC, `register()` |
| Active provider | `news.py:512` | `DatabaseNewsProvider` → reads `news_items` table **that nothing creates** |

**Catalyst is dead for two compounding reasons** (both must be fixed):
1. No archive — `DatabaseNewsProvider.available()` is always `False` because the
   `news_items` table is never created (`news.py:337-387`, `NO_HISTORICAL_NEWS`).
2. No feed — the sole production caller `swing_advisor.evaluate()` never passes
   `news_reading`, so `evaluate_symbol` builds `catalyst(news_reading=None)` →
   `INSUFFICIENT`, and the 10% weight renormalises away.

## 2. Existing News Capabilities

| Capability | Status | Evidence |
|---|---|---|
| `NewsItem` model (ins_code, published_at, sentiment −1..1, impact 0..1, severity, source, url) | **PRESENT** | `news.py:93-148` |
| Impact-weighted aggregation + severity bands | **PRESENT** | `news.py:155-197` |
| sentiment → 0-100 score | **PRESENT** | `news.py:217` `to_score()` |
| `NewsProvider` ABC + `register()` plug-in point | **PRESENT** (zero callers) | `news.py:241-282, 493` |
| Undated news dropped, never guessed | **PRESENT** | `news.py:40-41, 165` |
| **Any live provider / any data** | **ABSENT** | `news.py:1` "the honest admission that it has no data"; `NullNewsProvider` is active |
| Multi-source dedup, taxonomy, causal graph, market-confirmation, event object | **ABSENT** | not modelled anywhere |

`news.py` is a clean, PIT-honest **sentiment** interface. The mission asks for
**event intelligence** — a superset it does not yet model.

## 3. Existing Database Structure

19 tables in `database.py:create_database()`. **None are news/event tables.**
Nearest neighbours are the Codal tables, which are genuine dated event material:

- `codal_letters` (`database.py:222`) — `tracing_no, ins_code, symbol, letter_code, title, published_at, url, …` — dated, titled announcements. Described in-code as a headline store.
- `codal_filings` (`database.py:275`) — per-filing PIT store, `published_at`, never overwritten.
- `data_provenance` (`database.py:349`) — `scope TEXT PRIMARY KEY, status, detail, checked_at`; **free-text scope, reusable for an events dataset with no schema change.**

Idioms a proposal must match: `CREATE TABLE IF NOT EXISTS` inside
`create_database()`, `CREATE INDEX IF NOT EXISTS`, upsert via `_connect()` +
`ON CONFLICT(pk) DO UPDATE SET col=excluded.col`, JSON blobs as TEXT columns,
`_add_missing_columns()` for later columns.

## 4. Decision-Engine Integration Points

The independent-factor seam already exists and is the correct attach point:

- **Assembly:** `factor_engine.evaluate_symbol(..., news_reading=None, events=None)` (`factor_engine.py:826`) → builds catalyst at `:883`, blends at `:892`.
- **Combine:** `swing_score(regime, momentum, technical, fundamental, liquidity, catalyst, …)` (`factor_engine.py:545`) — a new factor is a new kwarg + a `factors` dict entry (`:568`) + `default_weights()` (`:529`) + a `config.SWING_WEIGHT_*`.
- **Live:** inject at `swing_advisor.evaluate()` (`:214`).
- **Research:** inject at `swing_strategy.decide()` (`:386`) via `pit.news_as_of()`.
- **On/off:** there is **no `NEWS_ENABLED` boolean**; the de-facto lever is weight = 0 (exactly how `SWING_WEIGHT_REGIME=0` disables regime). The mission's `NEWS_ENABLED=False` rollout maps to `SWING_WEIGHT_CATALYST=0` (or a new weight) plus, preferably, an explicit flag we add.

**Caution — two parallel, easily-confused news mechanisms:**
- (a) `factor_engine.catalyst()` — 10% factor inside `swing_score` (**V2 engine**), currently dead.
- (b) `strategy.py NEWS_BLEND_WEIGHT=0.15` (`backtesting/strategy.py:81, 639`) — blended *after* `scoring.combine` in the **V1-combined backtest model C** (variant `TECHNICAL_FUNDAMENTAL_NEWS`).

A proposal must state which it attaches to. **Recommendation: the V2 catalyst
seam.** They share no code.

## 5. Existing PIT Capability

The filing side is a **complete, enforced** look-ahead firewall; the news side
is **architecturally safe but never exercised**:

- `pit._first_session_after()` (`pit.py:509`) turns a raw timestamp into a session-level `visible_from` using market-close hour + configured lag. **Reusable verbatim for events.**
- Enforcement: `if record["visible_from"] <= stamp … else blocked_future_reads += 1` (`pit.py:549`) — an auditable counter proves the firewall fired.
- `pit.news_as_of()` (`pit.py:745`) already delegates to the provider and returns `None` (not a neutral 50) when empty.
- `news.py` drops undated items and cuts on the raw timestamp — PIT-safe by construction (`news.py:435-452`).

Gaps to note precisely:
- There is **no literal `available_at`** field; the enforced key is `visible_from`. The mission's `available_at <= decision_timestamp` maps onto it.
- **`fingerprint()` (`pit.py:810`) does NOT hash any news/event table** — it covers price/flow/filings only. If we add a news archive, caches and experiment provenance will **not invalidate on news changes** until we add one line (`COUNT(*), MAX(published_at) FROM …`).
- `news.py` re-implements its own timestamp cut instead of calling `_first_session_after`, and its lookback is a `days*2` approximation, not true sessions (`news.py:448`). We should route events through `_first_session_after`.

## 6. Existing Backtesting Capability

Rich signal-diagnostics exist; **event-study machinery does not.**

| Present | Where |
|---|---|
| IC, ICIR, Newey-West t | `research_alpha_ic.py:127-244` |
| walk-forward, OOS + degradation | `validation.py:104, 264` |
| Monte Carlo, sensitivity, overfitting verdict | `validation.py:385, 517, 641` |
| session-aware forward returns | `research_factors.py:228-254` |
| permutation null (score shuffle) | `research_alpha_ic.py:162` |

| **Absent — must be built** | note |
|---|---|
| Event anchoring (t=0 at a news/filing timestamp) | none |
| Estimation window + normal-return baseline | none |
| Abnormal Return (AR), Cumulative AR (CAR), AAR/CAAR | none |
| Market-adjusted returns | fwd returns are **raw**; `index_closes` are loaded but never subtracted (`research_factors.py:145`) |
| `NEWS_ONLY` variant | only `TECHNICAL_ONLY/FUNDAMENTAL_ONLY/…_NEWS` exist (`strategy.py:51`) |

An event study **reuses** `research_factors.build` (panel + forward returns),
`research_alpha_ic` (per-date→aggregate stats + Newey-West + permutation null),
and `metrics.compare` — but the event-window/AR/CAR layer is new work.

## 7. Missing Components

Ordered by how much of the mission they carry:

1. **Data + collector** — no live news source of any kind; `register()` has zero callers.
2. **Event archive tables** — `news_items` (dormant spec) + the richer event/evidence/impact/reaction tables the mission specifies.
3. **Event Intelligence layer** — taxonomy, `Event` object, per-asset direction, impact/confidence scoring, source-credibility tiers, multi-source verification (FACT/REPORT/RUMOR/…), deduplication (N articles → 1 event + N evidence), causal graph.
4. **Market-confirmation + historical event DB** — did the market actually react; store expected vs actual N-day returns; event-similarity search.
5. **Event-study backtest** — AR/CAR/AAR, hit-rate, IC, decay, per horizon (1/3/5/10/20d).
6. **Persian/English NLP** beyond `persian.normalize` (tokenisation/entities) — `persian.py` is symbol/name-oriented only.
7. **Wiring** — pass `news_reading`/`events` on both the live (`swing_advisor.evaluate`) and research (`swing_strategy.decide`) seams.
8. **Governance fields** — `taxonomy_version / source_version / scoring_version` don't exist; `code_hash` doesn't cover repo-root `news.py`.
9. **Reporting + internal API** — no daily intelligence report, no `/api/news`.

## 8. Proposed Architecture

A new package **`event_intelligence/`** (the mission's `PoliticalEconomicIntelligence`),
built as a **provider behind the existing `news.NewsProvider` ABC** so the
Decision Engine needs no structural change — only a feed on the two seams.

```
event_intelligence/
  sources/            NewsProvider implementations (swappable)
    base.py           EventSource ABC (extends the reuse of transport + codal patterns)
    rss.py            RSSSource        (generic, dependency-light)
    codal_events.py   CodalEventSource (adapts existing codal_letters → events)
    persian_news.py   PersianNewsSource
    custom.py         CustomSource
  collect.py          rate-limited, checkpointed collector (reuses transport.RateLimiter,
                      codal_backfill checkpoint idiom, depth.py --if-due scheduler)
  normalize.py        reuses persian.normalize; entity/country/commodity/ticker tagging
  dedup.py            N articles → 1 underlying event + N evidence rows
  taxonomy.py         versioned event taxonomy (GEOPOLITICAL, SANCTIONS, OPEC, …)
  event.py            the Event object (per-asset direction, causal_chain, evidence)
  credibility.py      source tiers 1-4; Tier 4 alone never creates an impactful event
  impact.py           Impact/Confidence scoring (weights versioned, NOT hard-coded unless justified)
  causal.py           causal graph storage (Political → Macro → Industry → Company)
  confirmation.py     market reaction capture (t, t+1/3/5/10/20, volume, volatility)
  history.py          historical event DB + event-similarity search (evidence, not proof)
  iran_layer.py       Iran-specific macro map (USD/IRR, sanctions, budget, subsidies, …)
  intelligence.py     get_market_intelligence(date, market, horizon) — the internal API
  report.py           Daily Intelligence Report
  provider.py         EventNewsProvider(news.NewsProvider) — the bridge into the engine

backtesting/event_study.py   AR/CAR/AAR/CAAR, hit-rate, IC, decay (reuses research_factors + research_alpha_ic)
examples/e_news_01.py        the 3-arm experiment (A / B / C) under the governance layer
```

**Two strictly separate pipelines** (bند 16), reusing the platform's existing
live-vs-research split:
- **Historical/research:** events with `visible_from` (via `pit._first_session_after`), consumed only through `pit.news_as_of()` → `swing_strategy.decide()`. Feeds backtest + E_NEWS_01.
- **Live:** real-time reading injected at `swing_advisor.evaluate()` → `evaluate_symbol(news_reading=…)`, **never** touching `pit`. Mirrors the existing fundamental "inject-for-research / read-live-otherwise" split (`swing_advisor.py:149-158`).

**Rollout flag:** add an explicit `NEWS_ENABLED = False` in `config.py` (clearer
than weight=0), gating whether the seams pass a reading at all; catalyst weight
stays a second, independent lever.

## 9. Proposed Database Schema

New tables in `database.py` (matching the codal idioms), plus the dormant
`news_items` so the existing `DatabaseNewsProvider` lights up:

| Table | Purpose | Key |
|---|---|---|
| `news_sources` | source registry + credibility tier | `source_id` |
| `news_articles` | raw articles, one row per fetched item | `article_id` |
| `events` | one underlying event (deduped) | `event_id` |
| `event_evidence` | N articles supporting one event | `(event_id, article_id)` |
| `event_entities` | entities/countries/commodities/tickers per event | `(event_id, entity)` |
| `event_market_impacts` | per-asset (gold/oil/USD-IRR/TSE) direction+magnitude | `(event_id, market)` |
| `event_industry_impacts` | per-TSE-industry exposure | `(event_id, industry)` |
| `event_market_reactions` | measured t..t+20 price/volume/vol reaction | `(event_id, market, horizon)` |
| `event_historical_matches` | similarity links to past events | `(event_id, match_id)` |
| `event_scores` | impact/confidence + component breakdown + scoring_version | `(event_id, scoring_version)` |
| `news_items` | the existing dormant spec — activates `DatabaseNewsProvider` | `id` |

Every event carries the four timestamps (`published_at, collected_at,
processed_at, available_at`) and a `pit_status` (`VALID_FOR_BACKTEST` /
`INVALID_FOR_BACKTEST` — invalid rows kept, not deleted, per bند 17). Provenance
recorded via existing `set_provenance(scope='events', …)`.

## 10. Proposed Implementation Plan (phased, per bند 35)

Each phase ends with: Changes / Tests / Results / Risks / Next. `NEWS_ENABLED`
stays `False` until Phase 11.

1. **Data model** — tables + `news_items`; extend `pit.fingerprint()` to hash the news archive (the one-line gap). *No engine change.*
2. **Ingestion** — `EventSource` ABC + one real source (RSS and/or `CodalEventSource` from existing `codal_letters`, which needs no new network). Collector reuses transport/checkpoint/scheduler idioms.
3. **Event extraction + taxonomy + dedup** — articles → events + evidence.
4. **Impact engine** — credibility tiers, per-asset direction, impact/confidence (versioned weights).
5. **Historical event DB + similarity.**
6. **Market confirmation** — reuse session-aware forward returns; store reactions.
7. **PIT / leakage protection** — route events through `_first_session_after`; `available_at <= decision` tests; leakage test.
8. **Event-study backtest** — `backtesting/event_study.py` (AR/CAR/IC/decay).
9. **E_NEWS_01** — register under the governance layer; 3 arms (A/B/C); measure *incremental OOS value*, not in-sample.
10. **Decision-Engine integration** — feed both seams behind `NEWS_ENABLED`.
11. **Paper trading** — live reading on the live seam only.
12. **Reporting + `/api/news` + `get_market_intelligence()`.**
13. **Production readiness.**

## 11. Risks

- **No data is the binding constraint.** Iranian dated-news archives are scarce; a live RSS feed builds history only forward, exactly like `depth.py`. Realistically the first usable arm is `CodalEventSource` (dated announcements we already store) plus forward-collected news. *If a source is unavailable, record `DATA_UNAVAILABLE`; do not fabricate (bند 36).*
- **Look-ahead is the classic failure of news backtests.** Undated or mis-timestamped items must be `INVALID_FOR_BACKTEST`, never guessed. The filing firewall is the template.
- **`code_hash` blind spot** — `provenance.CODE_ROOTS=('sirat','backtesting')` excludes repo-root `news.py` and a new top-level `event_intelligence/`. Reproducibility fingerprints would silently miss news-code changes. **Must extend `CODE_ROOTS`.**
- **Data snooping** (bند 19) — no tuning on holdout, no post-hoc horizon/category selection; all params versioned in the frozen prereg.
- **Edge is unproven and must stay that way until measured** (bند 33) — "Information Value = YES, Trading Alpha = NO" is a valid, expected outcome given E19-E23.
- **Two news mechanisms** could be wired inconsistently — commit to the V2 catalyst seam.
- **`git` absent** → `reproducible=False` → E_NEWS_01 can register/run but never PROMOTE today (`provenance.py:161`); acceptable for research, blocks live.

## 12. Estimated Complexity

| Component | Complexity |
|---|---|
| DB schema + `news_items` + fingerprint fix | Low |
| `CodalEventSource` (adapt existing data) | Low–Medium |
| RSS/live source + collector + scheduler | Medium |
| Taxonomy + dedup + event extraction | Medium–High |
| Impact/credibility/causal/confidence | High (and the part most exposed to un-evidenced weights) |
| Market confirmation + historical DB + similarity | Medium |
| Event-study backtest | Medium |
| E_NEWS_01 + governance wiring | Medium |
| Decision-Engine + paper wiring (behind flag) | Low (seams pre-exist) |
| NLP beyond normalize (entities) | High if deep; Medium with a rules/gazetteer start |

Largest and riskiest: the qualitative→quantitative scoring (impact/confidence)
and the honest measurement of incremental value.

## 13. Exact Files Created / Modified (proposed — none touched yet)

**New:** the entire `event_intelligence/` package (§8), `backtesting/event_study.py`,
`examples/e_news_01.py`, `tests/test_event_intelligence.py`,
`tests/test_event_study.py`, `tests/test_event_pit.py`,
`EVENT_INTELLIGENCE.md`.

**Modified (surgical):**
- `database.py` — add the new tables + indexes + upserts inside `create_database()`.
- `backtesting/pit.py` — add the news/event table to `fingerprint()`; optionally route events through `_first_session_after`.
- `sirat/experiments/provenance.py` — extend `CODE_ROOTS` to include `event_intelligence` (and `news.py`); add `taxonomy_version/source_version/scoring_version` handling (or carry them in prereg parameters).
- `swing_advisor.py` — pass a live `news_reading` at `:214` (behind `NEWS_ENABLED`).
- `backtesting/swing_strategy.py` — pass `pit.news_as_of()` at `:386` (behind `NEWS_ENABLED`).
- `config.py` / `settings.py` — `NEWS_ENABLED`, `SWING_WEIGHT_*` if a distinct factor, `SIRAT_NEWS_API_KEY` (env pattern).
- `app.py` — a read-only `/api/news` (intelligence report), like the other routes.

**Not touched, ever, per the brief:** Alpha Core (`90024d86d7a5c268`), E19-E23
records, prior backtest results, existing strategy logic, existing factor weights
(other than adding a lever), the ledger's past entries.

---

## Acceptance gate for this phase

Per bند 37, this document is the deliverable of Phase 1 and **no main project
file has been changed to produce it.** The next step is your review of §8–§10
(architecture, schema, plan). On approval I proceed phase-by-phase, reporting
Changes / Tests / Results / Risks / Next after each, with `NEWS_ENABLED=False`
until the integration phase and E_NEWS_01 measured for *incremental OOS value*
before any claim of edge.

### Provenance
| | |
|---|---|
| method | parallel read-only audit of 8 subsystems, file:line evidence |
| code changed | **none** |
| Alpha Core | untouched, `90024d86d7a5c268` |
| workflow | `wf_ffd7ef53-6f9` (8 agents, 0 errors) |
