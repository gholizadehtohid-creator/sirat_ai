"""SIRAT AI admin panel.

Serves the dashboard and a JSON API over the whole-market screen, the index
and sector analysis, the regime detector and the portfolio builder. Every knob
the panel exposes -- indicator windows, equity, basket size, sector cap --
arrives as a query parameter and is validated here before it reaches a model.

Results are memoised per parameter set: a dashboard load would otherwise
re-run the whole scan (and, when TSETMC is unreachable, wait on TLS timeouts)
for every XHR the page fires.

There is deliberately no per-symbol demo section any more. The panel used to
carry five hard-coded shares (FOLAD, FMLI, SHESTA, SHPNA, KHODRO) with their
own scan, price chart, backtest and threshold sweep, which meant the page's
most prominent numbers described five names chosen by hand rather than the
market the screen actually covers. Every view here is now driven by the full
universe. backtest.py and threshold_optimizer.py still work from the command
line for research on individual names.
"""

import sys
import threading
import time
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

import advisor
import bootstrap
import codal_sync
import config
import database
import indices
import moneyflow
import pipeline
import regime as regime_module
import sectors
import settings
import technical
from providers import transport
from config import (
    ACCOUNT_EQUITY,
    CURRENCY,
    MAX_PER_SECTOR,
    MAX_POSITION_PCT,
    MIN_CASH_PCT,
    PORTFOLIO_MIN_SCORE,
    PORTFOLIO_SIZE,
    RISK_PER_TRADE_PCT,
    SERVER_HOST,
    SERVER_PORT,
    WINDOW_MAX,
    WINDOW_MIN,
)
from database import database_summary
from indicators import DEFAULT_PARAMS, clean_params
from logging_setup import get_logger, setup_logging

log = get_logger(__name__)

app = Flask(__name__)

# Jinja caches templates unless debug is on, and the panel runs with debug off.
# That combination serves an edited dashboard.html only after a restart, while
# static/js/panel.js -- revalidated on every request -- updates immediately. The
# page then runs new JavaScript against old HTML, which used to leave the whole
# panel dead on "Loading data status…". Re-reading a changed template costs a
# stat() per render; a silently stale page costs an evening.
app.config["TEMPLATES_AUTO_RELOAD"] = True

CACHE_TTL = 300

_cache = {}
_cache_lock = threading.Lock()


def memoise(key, producer, force=False):
    """Cache `producer()` under `key` for CACHE_TTL seconds."""

    now = time.time()

    with _cache_lock:
        hit = _cache.get(key)
        if hit and not force and (now - hit["at"]) < CACHE_TTL:
            return hit["value"]

    value = producer()

    with _cache_lock:
        _cache[key] = {"at": now, "value": value}

    return value


def invalidate():
    with _cache_lock:
        _cache.clear()


def data_version():
    """A token that changes whenever a background fetch has DELIVERED new data.

    The cache is a speed device and must never be a time machine. Every import
    endpoint already calls invalidate(), but it does so when a download STARTS
    and bootstrap finishes minutes later in its own thread -- so the book
    computed from the pre-fetch database was being served for the rest of the
    TTL after new bars had already landed. Nothing looked wrong: the page said
    the data was current, because it was, and the basket beside it was not.

    Keyed on `finished_at` rather than on `running` deliberately. During a fetch
    the stored data is half-updated and the cached answer is at least a
    consistent one, so serving it costs nothing; the moment the run completes the
    token moves and the next request recomputes. Reading it is a dict lookup
    under bootstrap's own lock -- no query, no file, safe to call per request.
    """

    try:
        state = bootstrap.progress()
    except Exception:
        # A cache key is not worth failing a request over. A constant here
        # degrades to exactly the previous behaviour rather than to no cache.
        return "unknown"

    return str(state.get("finished_at") or "-")


# ==============================
# REQUEST PARSING
# ==============================

def wanted_params():
    """Indicator windows from the query string, clamped to sane bounds."""

    return clean_params({
        "ma_fast": request.args.get("ma_fast", type=int),
        "ma_slow": request.args.get("ma_slow", type=int),
        "rsi_window": request.args.get("rsi", type=int),
        "macd_fast": request.args.get("macd_fast", type=int),
        "macd_slow": request.args.get("macd_slow", type=int),
        "macd_signal": request.args.get("macd_signal", type=int),
        "volume_ma": request.args.get("volume_ma", type=int),
    })


def wanted_range():
    """?start=YYYY-MM-DD&end=YYYY-MM-DD, either side optional."""

    def parse(name):
        raw = (request.args.get(name) or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None

    start, end = parse("start"), parse("end")

    # A reversed range would silently return nothing; swap instead.
    if start and end and start > end:
        start, end = end, start

    return start, end


def wanted_equity():
    value = request.args.get("equity", type=float)

    if not value or value <= 0:
        return ACCOUNT_EQUITY

    return min(value, 1e15)


def wanted_portfolio_args():
    """Basket shape from the query string, each clamped to something sane."""

    def bounded(name, default, low, high, cast=int):
        value = request.args.get(name, type=cast)
        if value is None:
            return default
        return max(low, min(high, value))

    return {
        "equity": wanted_equity(),
        "size": bounded("size", PORTFOLIO_SIZE, 1, 50),
        "max_per_sector": bounded("max_per_sector", MAX_PER_SECTOR, 1, 10),
        "min_score": bounded("min_score", PORTFOLIO_MIN_SCORE, 0, 100),
        "risk_pct": bounded("risk", RISK_PER_TRADE_PCT, 0.05, 10.0, float),
    }


def cache_key(*parts):
    return "|".join(str(p) for p in parts)


def params_key(params):
    return ",".join(f"{k}={params[k]}" for k in sorted(params))


def full_scan_disabled():
    """The 403 the two full-market routes return when the guard is down.

    SIRAT_FULL_SCAN=0 is a deliberate switch -- it exists so a panel can be
    opened for a quick look without paying for a whole-market scan -- but it
    silences the two routes the suggested basket is built from, and the old
    reply ("full scans are disabled") named the variable without saying what
    it cost or how to undo it. A reader saw an empty basket and a fragment of
    English, and had no way to tell a switched-off feature from a broken one.

    The message is the whole fix: it says what is off, what that removes, and
    the exact command that restores it. `code` lets the panel style this as a
    setting rather than a failure -- nothing crashed, and calling it a crash
    sends the reader hunting for a bug that is not there.
    """

    return jsonify({
        "code": "full_scan_disabled",
        "error": (
            "اسکن کامل بازار خاموش است، پس غربال و سبد پیشنهادی ساخته "
            "نمی‌شوند. این پنل با متغیر SIRAT_FULL_SCAN=0 اجرا شده است — "
            "یک سوئیچ عمدی برای باز کردن سریع صفحه بدون هزینه‌ی اسکن کل "
            "بازار، نه یک خطا. برای برگرداندن آن، پنل را ببندید و بدون آن "
            "متغیر دوباره اجرا کنید:  python app.py  (یا run.bat)."
        ),
    }), 403


@app.errorhandler(Exception)
def handle_error(exc):
    """Return JSON on API routes so the panel can surface the real message."""

    traceback.print_exc()

    if request.path.startswith("/api/"):
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    raise exc


# ==============================
# PAGES
# ==============================

@app.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        defaults=DEFAULT_PARAMS,
        equity=ACCOUNT_EQUITY,
        currency=CURRENCY,
        portfolio_size=PORTFOLIO_SIZE,
        max_per_sector=MAX_PER_SECTOR,
        min_score=PORTFOLIO_MIN_SCORE,
        risk_pct=RISK_PER_TRADE_PCT,
        max_position_pct=MAX_POSITION_PCT,
        min_cash_pct=MIN_CASH_PCT,
        window_min=WINDOW_MIN,
        window_max=WINDOW_MAX
    )


# ==============================
# API
# ==============================

@app.route("/api/status")
def api_status():
    """Database coverage, used by the status strip and date-range bounds."""

    rows = database_summary()

    return jsonify({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbols": rows,
        "provenance": database.get_provenance("price_history"),
        "total_bars": sum(r["bars"] for r in rows),
        "date_min": min((r["first_date"] for r in rows), default=None),
        "date_max": max((r["last_date"] for r in rows), default=None),
        "config": {
            "equity": ACCOUNT_EQUITY,
            "currency": CURRENCY,
            "portfolio_size": PORTFOLIO_SIZE,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_per_sector": MAX_PER_SECTOR,
        }
    })


@app.route("/api/regime")
def api_regime():
    """Market regime: trend, breadth, participation, and the cash target."""

    params = wanted_params()
    force = request.args.get("refresh") == "1"

    key = cache_key("regime", params_key(params))

    def build():
        return regime_module.detect(params=params)

    return jsonify(memoise(key, build, force=force))


@app.route("/api/portfolio")
def api_portfolio():
    """The suggested basket, plus the risk analysis of what it produced.

    This is the endpoint the panel's final table is built from. It runs the
    whole chain -- regime, screen, portfolio, risk -- because each stage needs
    the one before it, and memoises the result so a page reload does not
    re-scan 800 instruments.
    """

    if not settings.FULL_SCAN_ENABLED:
        return full_scan_disabled()

    args = wanted_portfolio_args()
    params = wanted_params()

    use_regime = request.args.get("regime", "1") != "0"
    force = request.args.get("refresh") == "1"

    # ?engine=v2 runs the swing decision engine instead of the v1 chain. It is
    # part of the cache key and not an argument to the cached function: the two
    # engines produce completely different books from the same inputs, and a key
    # that ignored the engine would serve whichever one ran first for the next
    # five minutes.
    engine = advisor.engine_name(request.args.get("engine"))

    key = cache_key(
        "portfolio", engine, data_version(), args["equity"], args["size"],
        args["max_per_sector"], args["min_score"], args["risk_pct"],
        use_regime, params_key(params),
    )

    def build():

        if engine == advisor.V2:
            # No pipeline scan: the v2 chain screens the universe itself, and
            # handing it v1's shortlist would silently restrict it to the names
            # v1's fundamental gate approved.
            return advisor.run(
                equity=args["equity"],
                size=args["size"],
                max_per_sector=args["max_per_sector"],
                risk_pct=args["risk_pct"],
                use_regime=use_regime,
                engine=engine,
            )

        scan_report = memoise(
            # Same shape as /api/screen's key, so the two share one entry
            # rather than each paying for a full scan.
            cache_key("screen", data_version(), None, None, None,
                      params_key(params)),
            lambda: pipeline.run_scan(params=params),
        )

        return advisor.run(
            equity=args["equity"],
            size=args["size"],
            max_per_sector=args["max_per_sector"],
            min_score=args["min_score"],
            risk_pct=args["risk_pct"],
            use_regime=use_regime,
            scan_report=scan_report,
            engine=engine,
        )

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "history", "snapshot"))


# ==============================
# FULL-MARKET / FUNDAMENTAL API
# ==============================

@app.route("/api/providers")
def api_providers():
    """Provider health plus the effective transport settings."""

    import providers as provider_registry
    from providers.transport import probe

    diagnostics = []

    if request.args.get("probe") == "1":
        diagnostics = [
            probe(f"{settings.TSETMC_BASE_URL}/api/StaticData/GetTime"),
            probe(settings.UNIVERSE_URL),
        ]

    return jsonify({
        "settings": settings.describe(),
        "providers": provider_registry.available_providers(),
        "active": provider_registry.get_provider().name,
        "diagnostics": diagnostics,
    })


@app.route("/api/universe")
def api_universe():
    """Instrument universe coverage, and optionally the rows themselves."""

    counts = database.instrument_counts()

    rows = []

    if request.args.get("list") == "1":
        rows = database.load_instruments(
            equities_only=request.args.get("equities", "1") == "1",
            limit=request.args.get("limit", type=int) or 500,
        )

    return jsonify({"counts": counts, "instruments": rows})


@app.route("/api/screen")
def api_screen():
    """Two-stage scan: fundamental screen, then technical analysis."""

    if not settings.FULL_SCAN_ENABLED:
        return full_scan_disabled()

    limit = request.args.get("limit", type=int)
    approve = request.args.get("approve", type=int)
    max_technical = request.args.get("max_technical", type=int)
    params = wanted_params()
    force = request.args.get("refresh") == "1"

    key = cache_key(
        "screen", data_version(), limit, approve, max_technical,
        params_key(params)
    )

    def build():
        return pipeline.run_scan(
            limit=limit,
            approve_score=approve,
            max_technical=max_technical,
            params=params,
        )

    return jsonify(memoise(key, build, force=force))


@app.route("/api/indices")
def api_indices():
    """Full technical read on شاخص کل and شاخص کل هم‌وزن, plus their breadth.

    Carries what /api/technical carries for a share -- the latest indicator
    values, support and resistance from confirmed pivots, a transparent vote
    with every component shown, and a level ladder -- and two things only an
    index has: a vote on whether market turnover confirms the move, and one on
    the gap between the two indices. The ladder is two-sided and measured in
    the index's own ATR; see indices.index_targets for why it is not the
    equity target path.
    """

    params = wanted_params()
    bars = request.args.get("bars", type=int)
    force = request.args.get("refresh") == "1"

    key = cache_key("indices", bars, params_key(params))

    def build():
        return indices.analyse_all(params=params, bars=bars)

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "indices"))


@app.route("/api/technical")
def api_technical():
    """Technical reading for one instrument, by نماد.

    Indicator values, support/resistance levels, and a transparent BUY/SELL
    vote whose every component is shown -- a textbook reading, not a validated
    edge, and it ships with that caveat. Missing indicators are reported as
    null, never as zero.
    """

    symbol = request.args.get("symbol", type=str)

    if not symbol or not symbol.strip():
        return jsonify({"error": "a symbol (نماد) is required"}), 400

    params = wanted_params()
    bars = request.args.get("bars", type=int) or technical.SERIES_BARS
    force = request.args.get("refresh") == "1"

    key = cache_key("technical", data_version(), symbol.strip(), bars,
                    params_key(params))

    def build():
        return technical.analyse(symbol.strip(), params=params, bars=bars)

    report = memoise(key, build, force=force)

    # An unknown symbol or absent history is a 404, not a 200 with an error
    # body -- the panel distinguishes "not found" from "found, here it is".
    if isinstance(report, dict) and report.get("error"):
        return jsonify(report), 404

    return jsonify(report)


@app.route("/api/technical/top")
def api_technical_top():
    """The top-N names by the platform's technical score, over the liquid
    universe.

    A convenience ranking, memoised because it scores ~600 names through the
    full indicator engine. Carries the same caveat as the per-symbol signal:
    it ranks by textbook technicals, which this programme's research found no
    tradable edge in.
    """

    top = request.args.get("top", type=int) or 10
    top = max(1, min(top, 50))
    params = wanted_params()
    force = request.args.get("refresh") == "1"

    key = cache_key("technical_top", data_version(), top, params_key(params))

    def build():
        return technical.top_technical(top=top, params=params)

    return jsonify(memoise(key, build, force=force))


@app.route("/api/technical/inflow")
def api_technical_inflow():
    """Names that are both technically strong AND taking money in.

    The intersection of the technical scan and حقیقی/حقوقی flow, over the
    liquid universe. Memoised (it scores ~600 names), triggered by a button.
    Same caveat as the other technical views, doubled: two readings with no
    proven return edge do not make one by being intersected.
    """

    top = request.args.get("top", type=int) or 15
    top = max(1, min(top, 50))
    params = wanted_params()
    force = request.args.get("refresh") == "1"

    key = cache_key("technical_inflow", data_version(), top, params_key(params))

    def build():
        return technical.best_technical_inflow(top=top, params=params)

    return jsonify(memoise(key, build, force=force))


@app.route("/api/smartmoney")
def api_smart_money():
    """The Smart Money Accumulation Scanner: names bought at their own floor.

    One conjunction over the liquid universe -- MFI above 70 WHILE the price is
    still in the bottom third of its multi-month range, near a tested support --
    then scored on the transfer from حقوقی to حقیقی, the concentration of the
    receiving side, valuation against the sector, size, and volume.

    Memoised like the other whole-market views. NOT memoised away, though: every
    computed scan writes its candidates and its funnel to smart_money_signals
    with the timestamp it was computed at, so the list can be evaluated later
    instead of remembered. A cache hit serves a stored run and writes nothing,
    which is correct -- it is the same run.
    """

    top = request.args.get("top", type=int) or 20
    top = max(1, min(top, 50))
    params = wanted_params()
    force = request.args.get("refresh") == "1"

    key = cache_key("smart_money", data_version(), top, params_key(params))

    def build():
        import smart_money
        return smart_money.scan(top=top, params=params)

    return jsonify(memoise(key, build, force=force))


@app.route("/api/smartmoney/history")
def api_smart_money_history():
    """Past scans: the funnel per run, and the signals they raised.

    Read-only and never memoised -- it is a log, it only grows, and a stale
    view of it would hide the run that was just written.
    """

    limit = request.args.get("limit", type=int) or 30
    limit = max(1, min(limit, 200))

    symbol = request.args.get("ins_code")

    from smart_money import store

    if symbol:
        return jsonify({"ins_code": symbol,
                        "history": store.signal_history(symbol, limit=limit)})

    return jsonify({"runs": store.runs(limit=limit),
                    "signals": store.recent_signals(limit=limit)})


@app.route("/api/live")
def api_live():
    """The session as it stands: who is locked, what just changed, who is being bought.

    Reads only what monitor.py has already written. Deliberately issues no
    network request of its own -- a dashboard load must not put traffic on
    TSETMC, and the collector is at most one poll ahead of this anyway.

    NOT MEMOISED. Every other whole-market view here is cached for five
    minutes because it costs a scan; this one is three indexed reads, and a
    five-minute-old answer on a panel whose entire claim is "right now" would
    be the one cache in this file that actively misleads.

    `session` is asked of the live feed rather than the clock, and carries
    three states: the exchange published a complete frozen market every day
    for the eleven weeks it was shut, so "a bar exists" proves nothing and
    "unreachable" is not "closed".

    Every block carries `age_minutes`. The panel is the one view in this
    project most likely to be read as a live terminal, and the collector runs
    only during a session -- opened on a Thursday it is showing Wednesday's
    close, and opened while the scheduled task is broken it could be showing
    last month. The age is part of the answer.
    """

    import market_session
    import monitor

    top = request.args.get("top", type=int) or 15
    top = max(1, min(top, 50))

    captured_at = monitor.latest_capture()

    # The feed probe is one call and it is the only honest way to answer
    # "is a session running". Failure here must not take the panel down:
    # the stored half is still worth rendering, and says so.
    try:
        session = market_session.is_trading_now()
    except Exception as exc:                                    # noqa: BLE001
        session = {"trading": None, "traded": 0, "last_trade": None,
                   "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120])}

    return jsonify({
        "session": session,
        "collector": monitor.last_tick(),
        "counts": monitor.queue_counts(captured_at),
        "queues": monitor.queues(captured_at, limit=top),
        "events": monitor.recent_events(limit=40),
        "flow": monitor.live_flow(limit=top),
    })


@app.route("/api/gold")
def api_gold():
    """Gold funds (صندوق‌های طلا) with a technical read each.

    ETFs tracking bullion, not stocks -- shown apart from the equity views,
    with no fundamental scoring. Missing history is reported so the panel can
    offer a fetch, never silently hidden.
    """

    params = wanted_params()
    force = request.args.get("refresh") == "1"

    key = cache_key("gold", data_version(), params_key(params))

    def build():
        return technical.gold_list(params=params)

    return jsonify(memoise(key, build, force=force))


@app.route("/api/import/gold", methods=["POST"])
def api_import_gold():
    """Fetch and store price history for the gold funds. User-triggered."""

    def build():
        import gold
        stored = gold.fetch_history(verbose=False)
        invalidate()
        return {"stored": stored, "coverage": gold.coverage()}

    try:
        return jsonify(build())
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/intelligence")
def api_intelligence():
    """Market-intelligence REPORT (news analysis). Read-only, memoised.

    This endpoint is deliberately isolated from every trading route: it reads the
    event_intelligence tables and returns an analytical report. It produces no
    BUY/SELL and is never consumed by the Decision Engine -- see the package
    docstring. News is information here, not a signal.
    """

    window = request.args.get("window", type=int) or 72
    top = request.args.get("top", type=int) or 20
    force = request.args.get("refresh") == "1"

    key = cache_key("intelligence", window, top)

    def build():
        from event_intelligence import intelligence as ei
        return ei.report(window_hours=window, top=top)

    return jsonify(memoise(key, build, force=force))


@app.route("/api/intelligence/fetch", methods=["POST"])
def api_intelligence_fetch():
    """Fetch and classify all reachable feeds. User-triggered write path."""

    def build():
        from event_intelligence import intelligence as ei
        summ = ei.collect()
        invalidate()
        return summ

    try:
        return jsonify(build())
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/sectors")
def api_sectors():
    """Per-sector fundamental distribution and derived technical read."""

    params = wanted_params()
    approve = request.args.get("approve", type=int)
    bars = request.args.get("bars", type=int)
    min_members = request.args.get("min_members", type=int)
    force = request.args.get("refresh") == "1"

    key = cache_key("sectors", approve, bars, min_members, params_key(params))

    def build():
        report = sectors.analyse_all(
            params=params,
            approve_score=approve,
            bars=bars,
            min_members=min_members,
        )
        report["coverage"] = database.sector_coverage()
        return report

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "sectors", "snapshot", "history"))


@app.route("/api/codal")
def api_codal():
    """Codal coverage, sync progress, and the route each API is using.

    The route table is here rather than buried in diagnostics because on this
    machine it is the single most common cause of "the fundamentals are
    empty": codal.ir and cdn.tsetmc.com need different network paths, and a
    VPN toggle changes which. Showing what each host is bound to turns a
    mystery into a fact on the page.
    """

    progress = codal_sync.progress()

    return jsonify({
        "coverage": progress.get("coverage"),
        "running": codal_sync.is_running(),
        "checked": progress.get("checked"),
        "fetched": progress.get("fetched"),
        "failed": progress.get("failed"),
        "total": progress.get("total"),
        "current": progress.get("current"),
        "started_at": progress.get("started_at"),
        "finished_at": progress.get("finished_at"),
        "log": progress.get("log", [])[-15:],
        "routes": transport.bound_hosts(),
        "recheck_hours": config.CODAL_RECHECK_HOURS,
        "retry_minutes": config.CODAL_RETRY_MINUTES,
        # Seconds until Codal may be contacted again. Surfaced because "the
        # fundamentals stopped filling in" and "we are deliberately waiting
        # out a rate limit" look identical from the page otherwise.
        "cooling_off_seconds": codal_sync.cooling_off(),
    })


@app.route("/api/codal", methods=["POST"])
def api_codal_run():
    """Start a bounded Codal sync in the background."""

    limit = request.args.get("limit", type=int)

    # A forced run means the user just fixed something -- most often turned a
    # VPN off. Re-probing every route first is what makes the button do what
    # they expect instead of failing again on a cached address.
    if request.args.get("rebind") == "1":
        transport.reset_bind_cache()

    # Pressing the button is an explicit "try now", so it may skip the
    # automatic cool-off -- but only when asked, because the default path
    # must not be able to talk the client back into a rate limit.
    ignore_cooldown = request.args.get("now") == "1"

    waiting = codal_sync.cooling_off()

    if waiting and not ignore_cooldown:
        return jsonify({
            "started": False,
            "cooling_off_seconds": waiting,
            "message": (
                f"Codal is rate-limiting this client; waiting {waiting // 60} "
                f"more minutes before trying again. Retrying sooner renews the "
                f"limit rather than clearing it. Add ?now=1 to override."
            ),
            "routes": transport.bound_hosts(),
        })

    started = codal_sync.start_background(
        limit=max(1, min(limit or config.CODAL_MAX_FETCH_PER_RUN, 400)),
        ignore_cooldown=ignore_cooldown,
    )

    invalidate()

    return jsonify({
        "started": started,
        "already_running": not started,
        "cooling_off_seconds": waiting,
        "routes": transport.bound_hosts(),
    })


@app.route("/api/fundamentals")
def api_fundamentals():
    """Filed fundamentals and scores for the screened universe, from cache."""

    top = max(1, min(request.args.get("top", type=int) or 40, 300))
    force = request.args.get("refresh") == "1"

    key = cache_key("fundamentals", top)

    def build():
        return _fundamental_report(top)

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "codal", "universe"))


def _fundamental_report(top):
    """Rank the companies whose filings we hold, best fundamentals first."""

    instruments = database.load_instruments(equities_only=True)

    by_code = {str(r["ins_code"]): r for r in instruments}

    snapshot = database.load_snapshot()

    market = {}
    if snapshot is not None and not snapshot.empty:
        for _, row in snapshot.iterrows():
            market[str(row.get("ins_code"))] = row.to_dict()

    metrics = codal_sync.metrics_for(list(by_code), market_by_code=market)

    rows = []
    stale = []

    for code, entry in metrics.items():

        instrument = by_code.get(code, {})

        row = {
            "ins_code": code,
            "symbol": instrument.get("symbol_fa"),
            "name": instrument.get("name_fa"),
            "sector_name": instrument.get("sector_name"),
            "score": entry["score"],
            "reported_score": entry.get("reported_score"),
            "status": entry["status"],
            "verdict": entry["verdict"],
            "weight_covered": entry["weight_covered"],
            "period_end": entry["period_end"],
            "period_months": entry["period_months"],
            "period_age_months": entry.get("period_age_months"),
            "stale": entry.get("stale"),
            "audited": entry["audited"],
            "consolidated": entry["consolidated"],
            "breakdown": entry["breakdown"],
            "notes": entry["notes"],
            "ratios": entry.get("ratios") or {},
        }

        if entry.get("stale"):
            # Listed separately rather than dropped: a company that stopped
            # filing is worth seeing, just never at the top of a ranking of
            # current fundamentals.
            stale.append(row)
        elif entry.get("score") is not None:
            rows.append(row)

    rows.sort(key=lambda r: r["score"], reverse=True)
    stale.sort(key=lambda r: (r["period_end"] or ""), reverse=True)

    scored = [r["score"] for r in rows]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": database.codal_coverage(),
        "counts": {
            "scored": len(rows),
            "strong": sum(1 for r in rows if r["score"] >= 80),
            "weak": sum(1 for r in rows if r["score"] < 45),
            "stale": len(stale),
        },
        "stale_months": config.CODAL_STALE_MONTHS,
        "median_score": (sorted(scored)[len(scored) // 2] if scored else None),
        "top": rows[:top],
        "bottom": list(reversed(rows[-top:])) if rows else [],
        "stale": stale[:top],
    }


@app.route("/api/moneyflow")
def api_moneyflow():
    """حقیقی/حقوقی flow: who was on each side, market-wide and per symbol."""

    window = request.args.get("window", type=int)
    top = request.args.get("top", type=int)
    force = request.args.get("refresh") == "1"

    # Clamped for the same reason indicator windows are: these arrive from a
    # query string, and a window of 0 or 100000 would either divide by nothing
    # or drag the whole table through pandas.
    if window is not None:
        window = max(1, min(window, config.FLOW_BASELINE_WINDOW))

    top = max(1, min(top or 15, 100))

    key = cache_key("moneyflow", window, top)

    def build():
        return moneyflow.analyse_market(window=window, top=top)

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "flow", "universe"))


def _with_autofetch(report, *components):
    """Attach bootstrap state, and start a fetch if a component is missing.

    This is what makes the panel self-sufficient. Rather than answering "no
    data — go and run seed_import.py", an endpoint whose inputs are absent
    kicks off the download itself and tells the caller it is in flight, so the
    page can poll and fill in. bootstrap.start_background() is a no-op while a
    run is already going, and its per-step cooldown keeps an unreachable
    network from turning polling into a request flood.
    """

    state = bootstrap.status()

    missing = [c for c in components if state.get(c, {}).get("needs_fetch")]

    if missing:
        bootstrap.start_background(only=missing)

    report = dict(report or {})
    report["bootstrap"] = {
        "fetching": missing,
        # is_running() over progress(): this runs on every indices/sectors
        # request, and progress() re-probes the whole database.
        "running": bootstrap.is_running(),
        "components": {c: state.get(c) for c in components},
    }

    return report


@app.route("/api/bootstrap")
def api_bootstrap_status():
    """What data is present, missing or stale, and any fetch in flight."""

    return jsonify(bootstrap.progress())


@app.route("/api/bootstrap", methods=["POST"])
def api_bootstrap_run():
    """Fetch whatever is missing or stale, in the background."""

    force = request.args.get("force") == "1"
    only = request.args.getlist("only") or None

    started = bootstrap.start_background(force=force, only=only)

    invalidate()

    return jsonify({
        "started": started,
        "already_running": not started,
        "progress": bootstrap.progress(),
    })


@app.route("/api/import/indices", methods=["POST"])
def api_import_indices():
    """Fetch the published index series (needs a live route to tsetmc)."""

    import seed_import

    stored = seed_import.import_indices(verbose=False)

    invalidate()

    if not stored:
        return jsonify({
            "error": ("No index series could be fetched. This needs a working "
                      "route to tsetmc — check run.bat probe.")
        }), 502

    return jsonify({"stored": stored, "coverage": database.index_coverage()})


@app.route("/api/import/sectors", methods=["POST"])
def api_import_sectors():
    """Attach sectors to equities: one instrument-info call per symbol.

    Slow by nature -- there is no bulk route to sector membership -- so the
    panel should warn before firing this over the whole universe.
    """

    import seed_import

    limit = request.args.get("limit", type=int)

    attached = seed_import.import_sectors(limit=limit, verbose=False)

    invalidate()

    if not attached:
        return jsonify({
            "error": ("No sectors could be attached. This needs a working "
                      "route to tsetmc — check run.bat probe.")
        }), 502

    return jsonify({"coverage": database.sector_coverage()})


@app.route("/api/import/flow", methods=["POST"])
def api_import_flow():
    """Fetch the حقیقی/حقوقی history: one client-type call per symbol.

    As slow as the sector import and for the same reason -- no bulk route --
    so the panel confirms before firing it over the whole universe.
    """

    import seed_import

    limit = request.args.get("limit", type=int)

    stored = seed_import.import_client_type(limit=limit, verbose=False)

    invalidate()

    if not stored:
        return jsonify({
            "error": ("No client-type history could be fetched. This needs a "
                      "working route to tsetmc — check run.bat probe.")
        }), 502

    return jsonify({
        "stored": stored,
        "coverage": database.client_type_coverage(),
    })


@app.route("/api/import/universe", methods=["POST"])
def api_import_universe():
    """Populate the instrument universe (fetches from UNIVERSE_URL)."""

    import seed_import

    stats = seed_import.import_universe(verbose=False)

    invalidate()

    if stats is None:
        return jsonify({
            "error": (
                f"Could not fetch the universe from {settings.UNIVERSE_URL}. "
                "If that host is also blocked, set SIRAT_PROXY_URL or point "
                "SIRAT_UNIVERSE_URL at a local copy."
            )
        }), 502

    return jsonify({"imported": stats, "counts": database.instrument_counts()})


@app.route("/api/import/snapshot", methods=["POST"])
def api_import_snapshot():
    """Fetch a whole-market snapshot (needs a live route to tsetmc)."""

    import seed_import

    written = seed_import.import_snapshot(verbose=False)

    invalidate()

    if not written:
        return jsonify({
            "error": (
                "No snapshot could be fetched. This needs a working route to "
                "tsetmc — set SIRAT_PROXY_URL to a proxy outside this network, "
                "then retry."
            )
        }), 502

    return jsonify({"rows": written, "snapshot": database.snapshot_info()})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Drop every memoised payload and re-fetch whatever is stale.

    This used to refresh five hard-coded symbols. With the demo section gone
    there is no privileged subset to refresh, so it hands the work to
    bootstrap, which knows what is actually missing or stale across the whole
    universe and fetches only that.
    """

    invalidate()

    started = bootstrap.start_background(force=request.args.get("force") == "1")

    return jsonify({
        "started": started,
        "already_running": not started,
        "progress": bootstrap.progress(),
    })


def port_owner(host, port):
    """The PID already listening on `host:port`, or None.

    Windows lets a second process bind a port another one already holds
    (SO_REUSEADDR behaves like SO_REUSEPORT here), so a duplicate panel does
    not fail loudly -- it starts, prints its banner, and then serves nothing
    while the older process keeps answering. Since Flask re-reads the template
    on every render and static files revalidate per request, that older process
    serves the NEW page and the OLD Python: the browser shows a section whose
    every API call 404s, which looks exactly like a broken feature rather than
    a stale server. This has cost two debugging sessions; it is worth a check.
    """

    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((host, port)) != 0:
            return None

    # Something answered. Name it if we can -- "port in use" is a much less
    # useful message than "PID 11484, started three hours ago, is holding it".
    try:
        import subprocess

        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10
        ).stdout

        for line in out.splitlines():
            parts = line.split()
            if (len(parts) >= 5 and parts[0] == "TCP"
                    and parts[1].endswith(f":{port}")
                    and parts[3].upper() == "LISTENING"):
                return int(parts[4])
    except Exception:
        pass

    return -1                       # in use, owner unknown


def main():
    setup_logging()

    owner = port_owner(SERVER_HOST, SERVER_PORT)

    if owner is not None:
        print("=" * 60)
        print("SIRAT AI ADMIN PANEL — NOT STARTED")
        print("=" * 60)
        print(f"Something is already listening on {SERVER_HOST}:{SERVER_PORT}"
              + (f" (PID {owner})." if owner > 0 else "."))
        print()
        print("That is almost always an earlier copy of this panel. It keeps")
        print("serving the Python it loaded at startup while handing out the")
        print("current page and JavaScript, so anything added since it started")
        print("renders but cannot answer -- the feature looks broken.")
        print()
        print("Stop it and run this again:")
        if owner > 0:
            print(f"  powershell Stop-Process -Id {owner} -Force")
        else:
            print("  powershell \"Get-CimInstance Win32_Process -Filter \\\"Name "
                  "like '%python%'\\\" | Where-Object { $_.CommandLine -like "
                  "'*app.py*' } | Stop-Process -Force\"")
        print()
        print(f"Or serve on another port:  set SIRAT_SERVER_PORT={SERVER_PORT + 1}")
        print("=" * 60)
        return 1

    print("=" * 60)
    print("SIRAT AI ADMIN PANEL")
    print(f"  http://{SERVER_HOST}:{SERVER_PORT}")
    print("=" * 60)

    # Work out what is missing and download it while the UI comes up, rather
    # than serving empty tables and instructions. The panel binds immediately;
    # progress is at /api/bootstrap.
    pending = [
        name for name, state in bootstrap.status().items()
        if state["needs_fetch"]
    ]

    if pending:
        print(f"Fetching in the background: {', '.join(pending)}")
        print("  progress: /api/bootstrap")
        bootstrap.start_background()
    else:
        print("All data present and current.")

    print("=" * 60)

    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
