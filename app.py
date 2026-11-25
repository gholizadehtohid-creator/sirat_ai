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

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

from flask import (Flask, copy_current_request_context, has_request_context,
                   jsonify, render_template, request)

import advisor
import bootstrap
import codal_sync
import config
import database
import indices
import moneyflow
import pipeline
import regime as regime_module
import regime_engine
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


# The names this panel answers to. A DNS-rebinding page points its own name
# at 127.0.0.1: its requests then carry Host evil.example and an Origin that
# "matches" it, and the same-origin check below let them through -- a POST
# /api/codal?now=1 skipped the Codal cooldown that way (the full test,
# 2026-11-12). Any other Host is refused before a route runs, GET included,
# which also keeps such a page from reading the panel's answers.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", SERVER_HOST.lower()})


def _hostname(netloc):
    from urllib.parse import urlsplit
    try:
        return (urlsplit("//" + (netloc or "")).hostname or "").lower()
    except ValueError:
        return ""


def _refuse(reason):
    return jsonify({"error": "cross-site request refused: %s" % reason}), 403


@app.before_request
def _same_origin_only():
    """Refuse what another site's page sent to this panel.

    The panel binds 127.0.0.1, but a page the user visits can still fire a
    request at it -- /api/bootstrap?force=1, or /api/codal?now=1, which skips
    the Codal cooldown (the review panel, 2026-11-11). Three checks:

    - the Host must be this machine's (DNS rebinding);
    - an /api/ request the browser marks as cross-site or same-site
      (Sec-Fetch-Site) is refused whatever its method: GET routes with
      ?refresh=1 or a new cache key start ~100 TSETMC requests and write
      files and research rows, and a top-level GET carries no Origin
      (the full test, 2026-11-12);
    - a POST's Origin (or Referer) must be this panel; "null" is refused.

    A request with none of these headers is not a browser (curl, the tests)
    and passes; a typed URL or bookmark sends Sec-Fetch-Site "none".
    """

    if _hostname(request.host) not in ALLOWED_HOSTS:
        return _refuse("host %s is not this panel" % (request.host or "?"))
    site = (request.headers.get("Sec-Fetch-Site") or "").lower()
    if request.path.startswith("/api/") and site in ("cross-site", "same-site"):
        return _refuse("sent from another site")
    if request.method != "POST":
        return None
    sender = request.headers.get("Origin") or request.headers.get("Referer")
    if not sender:
        return None                      # not a browser: curl, the tests
    if sender.strip().lower() == "null":
        return _refuse("opaque origin")
    from urllib.parse import urlparse
    host = (urlparse(sender).netloc or "").lower()
    if host == (request.host or "").lower() and _hostname(host) in ALLOWED_HOSTS:
        return None
    return _refuse("sent from %s" % (host or "an unknown page"))


# What the panel's own page may load, and who may frame it (the security
# review, 2026-11-23). No answer carried a policy: a symbol with markup in
# it, written into a table as it came, would have run with the reader's
# holdings in reach, and any site could put the panel in a frame and have
# its buttons clicked. The page needs nothing this forbids -- one module
# script from /static/, no inline script, no on* handler, no eval, no other
# host; its font and its API are its own, and data: is the favicon and the
# CSS icons. 'unsafe-inline' is for STYLE only: the page and panel.js carry
# some 150 style="" attributes.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


@app.after_request
def _security_headers(response):
    """The same headers on every answer, a refusal's and an error's included.

    A PDF keeps two of the four. /api/report's answer is shown by the
    browser's own viewer -- a page of the browser's that embeds the document
    -- and a policy and a frame ban written for the panel's HTML are not
    known to leave that viewer alone; it could not be tried here. Nothing is
    given up: the route is under /api/, so another site's frame is refused
    before it runs (_same_origin_only), and a PDF runs no script of ours.
    """

    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    if response.mimetype != "application/pdf":
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Frame-Options", "DENY")
    return response

CACHE_TTL = 300

# Past CACHE_TTL an entry is still SERVED, up to this age, while a fresh
# one is computed in the background: a full-market scan is one to two
# minutes, and every return to a tab after five minutes used to cost that
# wait again (2026-11-06). The answer is at most one computation old. Older
# than this -- a panel left open overnight -- it is recomputed while the
# caller waits. A new data_version is a new key, never a stale hit, so a
# finished fetch is still never answered from before it.
CACHE_STALE_GRACE = 2 * 3600

# Nothing ever left this cache except through invalidate(), and only the
# import buttons call that. A finished fetch moves data_version() and with it
# every key, so each fetch left the previous generation of scans in memory
# for the life of the process, beside every edit of the holdings list and
# every symbol looked up (the software review, 2026-11-23: one outlook screen
# is 847 KB as JSON). Two bounds, applied whenever an answer is stored:
#   - an entry past CACHE_STALE_GRACE is never served again -- memoise()
#     recomputes it while the caller waits -- so it is dropped;
#   - past CACHE_MAX_ENTRIES the least recently served go first. A page load
#     makes about twenty keys per data version, so this is several versions
#     and a session of symbol lookups, and reaching it costs a recomputation,
#     never a wrong answer.
CACHE_MAX_ENTRIES = 256

_cache = {}
_cache_lock = threading.Lock()
_inflight = {}                  # key -> the _Flight computing it now


class _Flight:
    """One computation in flight: who waits on it, and how it ended."""

    __slots__ = ("done", "value", "error")

    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error = None


def _evict(now):
    """Apply the two bounds above. Call with _cache_lock held."""

    for key in [k for k, hit in _cache.items()
                if now - hit["at"] >= CACHE_STALE_GRACE]:
        del _cache[key]

    excess = len(_cache) - CACHE_MAX_ENTRIES
    if excess > 0:
        idle = sorted(_cache,
                      key=lambda k: _cache[k].get("used", _cache[k]["at"]))
        for key in idle[:excess]:
            del _cache[key]


def _compute(key, producer):
    """producer(), stored under key; concurrent callers share one run.

    The tabs load in parallel and the panel now prefetches them, so the same
    scan could be asked for twice at once -- two full-market scans fighting
    over one GIL. The second caller waits for the first's answer instead.

    And for its failure. A waiter used to run the producer itself when the
    first run failed, so four tabs waiting on one failed scan became four
    scans at once (measured 2026-11-23: 4 callers, 4 runs) -- for
    /api/opportunities, four times ~100 TSETMC requests at a host that
    blocks bursts, at the moment it is already refusing. Every caller of
    one flight now gets that flight's answer or that flight's error; the
    retry is the next request, which finds nothing in flight.
    """

    with _cache_lock:
        flight = _inflight.get(key)
        owner = flight is None
        if owner:
            flight = _inflight[key] = _Flight()

    if not owner:
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value

    try:
        value = producer()
    except BaseException as exc:
        # Recorded before the waiters wake: a flight that ended with neither
        # a value nor an error would hand each of them None as the answer.
        flight.error = exc if isinstance(exc, Exception) else RuntimeError(
            "the computation was interrupted (%s)" % type(exc).__name__)
        raise
    else:
        flight.value = value
        with _cache_lock:
            now = time.time()
            _cache[key] = {"at": now, "used": now, "value": value}
            _evict(now)
        return value
    finally:
        with _cache_lock:
            _inflight.pop(key, None)
        flight.done.set()


def _refresh_in_background(key, producer):
    with _cache_lock:
        if key in _inflight:
            return

    # A producer may read flask.request; the copy keeps it readable after
    # the request that triggered the refresh has been answered.
    if has_request_context():
        producer = copy_current_request_context(producer)

    def run():
        try:
            _compute(key, producer)
        except Exception as exc:                                # noqa: BLE001
            log.warning("background refresh of %s failed: %s",
                        str(key).split("|")[0], exc)

    threading.Thread(target=run, name="cache-refresh", daemon=True).start()


def memoise(key, producer, force=False):
    """`producer()` under `key`: fresh for CACHE_TTL, then stale-while-refresh."""

    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            hit["used"] = time.time()   # what _evict() keeps is what was read last

    if hit is not None and not force:
        age = time.time() - hit["at"]
        if age < CACHE_TTL:
            return hit["value"]
        if age < CACHE_STALE_GRACE:
            _refresh_in_background(key, producer)
            return hit["value"]

    return _compute(key, producer)


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
        # data_stamp(), not progress(): progress() calls status(), which
        # re-probes the database (~5 s) once its own short cache is cold, and
        # this runs on nearly every request. It moves when a STEP delivers
        # changed data, in this process or the evening task's (2026-11-24),
        # where finished_at() moved only when this process's whole run ended:
        # an index fetched in seconds waited behind flow and Codal.
        return bootstrap.data_stamp()
    except Exception:
        # A cache key is not worth failing a request over. A constant here
        # degrades to exactly the previous behaviour rather than to no cache.
        return "unknown"


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

    from werkzeug.exceptions import HTTPException

    # A wrong URL or method is the caller's mistake, not a crash: it keeps its
    # own status (it was answered as a 500) and stays out of the log as a
    # traceback. The message keeps its "NotFound: 404 ..." form, which the
    # panel reads to tell a stale running panel from a broken feature.
    if isinstance(exc, HTTPException):
        log.warning("%s on %s %s", exc.code, request.method, request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), exc.code
        return exc

    # To the log file, not stderr: a panel started without a console has an
    # invalid stderr, print_exc then raised OSError inside this handler, and
    # the API answered an HTML 500 in place of the JSON message.
    log.error("unhandled error on %s", request.path, exc_info=exc)

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

    # The bar count and the span are price_history's, the table every analysis
    # reads. They used to be summed from `rows`, which is the legacy five-symbol
    # market_data table, so the footer on every tab said 1,505 bars from
    # 2025-05-07 to 2026-08-09 over a database of ~2 million bars to the latest
    # session. `symbols` stays for whatever still reads it. The aggregate scans
    # the whole table (~0.5 s), so it is memoised on the data version like
    # every other read of the stored bars.
    coverage = memoise(cache_key("status", data_version()),
                       database.history_coverage)

    return jsonify({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbols": rows,
        "provenance": database.get_provenance("price_history"),
        "total_bars": coverage["bars"],
        "date_min": coverage["first_date"],
        "date_max": coverage["last_date"],
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
    """Market regime: trend, breadth, participation, and the cash target.

    Answered by the engine that builds the basket, so the card and the book
    describe one market read. v2 sizes its book from regime_engine.assess(),
    which only ever tightens regime.py's figure -- a NORMAL mode spends 0.85 of
    it -- and this route used to answer regime.detect() under both engines: the
    card said "cash target 10%" beside a v2 basket holding 23.5% cash.
    """

    params = wanted_params()
    force = request.args.get("refresh") == "1"

    # In the key for the reason /api/portfolio gives: the two engines answer
    # differently from the same inputs, and a key without the engine would
    # serve whichever ran first for the next five minutes.
    engine = advisor.engine_name(request.args.get("engine"))

    # assess() is called exactly as swing_advisor.scan() calls it -- with no
    # arguments -- so the panel's indicator windows never reach the v2 read and
    # stay out of its key: an edit of them would only recompute the same read.
    key = cache_key("regime", engine, data_version(),
                    params_key(params) if engine == advisor.V1 else None)

    def build():
        if engine == advisor.V2:
            return _v2_regime(force=force)
        return regime_module.detect(params=params)

    return jsonify(memoise(key, build, force=force))


def _v2_regime(force=False):
    """regime_engine.assess() once per data version, for the regime card and
    the v2 book alike: they were two ~20 s reads of the same market on one
    page load (the full test, 2026-11-12)."""

    return memoise(cache_key("regime-v2-read", data_version()),
                   regime_engine.assess, force=force)


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

    # Only what the engine reads belongs in its key. v2 is handed neither the
    # score floor nor the indicator windows (see build() below), and the panel
    # sends both on every call, so with them in the key each edit of either
    # field cost a full v2 scan (~50 s, see config.DECISION_ENGINE) that
    # returned the same book.
    if engine == advisor.V2:
        min_score, windows = None, None
    else:
        min_score, windows = args["min_score"], params_key(params)

    key = cache_key(
        "portfolio", engine, data_version(), args["equity"], args["size"],
        args["max_per_sector"], min_score, args["risk_pct"],
        use_regime, windows,
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
                regime=_v2_regime(force=force),
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

    report = dict(memoise(key, build, force=force))
    report["track_record"] = basket_track_record()

    return jsonify(_with_autofetch(report, "history", "snapshot"))


def basket_track_record():
    """How the shipped basket did in its registered tests, for the panel.

    The basket is the panel's most concrete card -- share counts, entries,
    stops, targets -- and its own tests say it lost to the index and picked
    no better than chance (E26, backtest_output/E26_results.json: the
    shipped "baseline" engine +24.0% vs TEDPIX +92.9% over 2024-09..2025-12
    and +4.9% vs +94.4% in 2026, selection at the 18.8th and 1.4th
    percentile of random baskets). That was a folded note on another tab
    (found 2026-11-11), so a reader could put 90% of capital behind the
    strategy with the worst record. None if the results are missing.
    """

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "backtest_output", "E26_results.json")
    try:
        with open(path, encoding="utf-8") as handle:
            windows = json.load(handle)["windows"]
    except (OSError, ValueError, KeyError):
        return None
    out = []
    for span, w in sorted(windows.items()):
        try:
            out.append({
                "window": span,
                "basket_pct": w["total_return_pct"]["baseline"],
                "tedpix_pct": w["benchmarks_pct"]["tedpix"],
                "risk_free_pct": w["benchmarks_pct"].get("risk_free"),
                "selection_percentile": w["selection_percentile_vs_random"]
                ["engine_exits"]["baseline"],
            })
        except (KeyError, TypeError):
            continue
    return {"source": "E26", "windows": out} if out else None


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

    key = cache_key("indices", data_version(), bars, params_key(params))

    def build():
        return indices.analyse_all(params=params, bars=bars)

    report = dict(memoise(key, build, force=force) or {})
    report["provisional"] = _provisional("indices")

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


_SESSION_PROBE = {"at": 0.0, "value": None}
_SESSION_LOCK = threading.Lock()
SESSION_PROBE_TTL = 60.0


def _session_probe():
    """market_session.is_trading_now(), at most once a minute for the panel.

    It is two TSETMC requests (the whole market watch and the overview),
    and every /api/live called it -- the summary tab and the live tab both,
    on every load -- so an unreachable exchange stalled the first card and
    each page view sent traffic to a host that blocks bursts (found
    2026-11-11). Concurrent callers wait for the one probe in flight.
    """

    import market_session

    with _SESSION_LOCK:
        now = time.monotonic()
        if _SESSION_PROBE["value"] is not None and                 now - _SESSION_PROBE["at"] < SESSION_PROBE_TTL:
            return _SESSION_PROBE["value"]
        try:
            value = market_session.is_trading_now()
        except Exception as exc:                                # noqa: BLE001
            value = {"trading": None, "traded": None, "last_trade": None,
                     "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
        _SESSION_PROBE.update(at=now, value=value)
        return value


@app.route("/api/live")
def api_live():
    """The session as it stands: who is locked, what just changed, who is being bought.

    Reads what monitor.py has already written, plus one session probe
    (market_session.is_trading_now: two TSETMC requests) shared by every
    caller for a minute (_session_probe) -- the docstring used to say no
    request at all while every call made that probe (found 2026-11-11).

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

    import monitor

    top = request.args.get("top", type=int) or 15
    top = max(1, min(top, 50))

    captured_at = monitor.latest_capture()

    # The feed probe is one call and it is the only honest way to answer
    # "is a session running". Failure here must not take the panel down:
    # the stored half is still worth rendering, and says so.
    session = _session_probe()

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


@app.route("/api/allocation")
def api_allocation():
    """Stocks / gold / fixed income: the allocation E27 and E28 support.

    Shown above the stock basket because it is the only advice in the panel
    that passed a preregistered test. See allocation.py.
    """

    args = wanted_portfolio_args()
    force = request.args.get("refresh") == "1"

    key = cache_key("allocation", data_version(), args["equity"])

    def build():
        import allocation
        return allocation.plan(equity=args["equity"])

    try:
        return jsonify(memoise(key, build, force=force))
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/opportunities")
def api_opportunities():
    """Fund discounts to NAV and treasury-bill yields. See opportunities.py.

    Live network reads (~100 TSETMC requests), so memoised on the data
    version: a re-render costs nothing, the refresh button costs a minute.
    """

    force = request.args.get("refresh") == "1"

    key = cache_key("opportunities", data_version())

    def build():
        import opportunities
        return opportunities.report()

    try:
        return jsonify(memoise(key, build, force=force))
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/holdings", methods=["POST"])
def api_holdings():
    """The reader's own list of holdings, at one glance. See holdings.py.

    POST, not GET: the list is the reader's book, kept in their browser, and
    a query string would be written to the request log line by line. The
    server keeps it only as an in-memory cache key and never on disk. Reads
    only what is stored, so the memo is keyed on the data version.
    """

    body = request.get_json(silent=True) or {}
    text = str(body.get("list") or "")[:4000]
    force = bool(body.get("refresh"))

    key = cache_key("holdings", data_version(), text)

    def build():
        import holdings
        entries, errors = holdings.parse_with_errors(text)
        return holdings.review(entries, parse_errors=errors)

    try:
        return jsonify(memoise(key, build, force=force))
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/valuation")
def api_valuation():
    """The market's aggregate P/E against its own history and bills.

    The history is heavy and is built by the evening task
    (market_valuation.py --update); this only reads the stored file and the
    remembered bill yield, so it is not memoised -- a cache here could only
    serve yesterday's file after the evening task replaced it.
    """

    try:
        import market_valuation
        return jsonify(market_valuation.summary())
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/announcements")
def api_announcements():
    """Codal's important announcements of the last days. See codal_announcements.py.

    A read of the store the feed watch fills -- no network, no cache.
    ?days=3 (1-30), ?kind=agm|capital|material|halt|board.
    """

    import codal_announcements

    try:
        days = max(1, min(30, int(request.args.get("days", 3))))
    except ValueError:
        days = 3
    kind = request.args.get("kind") or None
    if kind and kind not in codal_announcements.KINDS:
        kind = None

    try:
        rows = codal_announcements.recent(days=days, kind=kind)
        return jsonify({"days": days, "kind": kind, "count": len(rows),
                        "kinds": codal_announcements.KIND_LABELS,
                        "rows": rows})
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/outlook")
def api_outlook():
    """Forward-looking read of filed statements. See fundamental_outlook.py.

    ?symbol=فولاد returns one company in full; without it, the ranking.
    """

    import fundamental_outlook
    import persian

    symbol = (request.args.get("symbol") or "").strip()
    force = request.args.get("refresh") == "1"

    try:
        if symbol:
            match = None
            wanted = persian.normalize(symbol)
            for row in database.load_instruments(equities_only=True):
                if persian.normalize(row.get("symbol_fa") or "") == wanted:
                    match = row
                    break
            if not match:
                return jsonify({"error": "نماد پیدا نشد: %s" % symbol}), 404
            code = str(match["ins_code"])
            halted = database.halted_codes().get(code, False)
            if halted is not False:
                return jsonify({"error": "نماد %s %s معامله نشده (متوقف) و "
                                         "تحلیل نمی‌شود."
                                % (symbol, "از %s" % halted if halted
                                   else "هیچ‌گاه")}), 404
            result = fundamental_outlook.analyse(
                code, price=fundamental_outlook.latest_prices().get(code))
            # An investment company's or a holding's portfolio at the bot's
            # fundamental value of each holding (portfolio_value.py).
            fundamental_outlook.attach_forward(result)
            if not result:
                return jsonify({"error": "صورت مالی ثبت‌شده‌ای برای %s نیست"
                                % symbol}), 404
            result.update(symbol=match.get("symbol_fa"), name=match.get("name_fa"))
            return jsonify(result)

        return jsonify(memoise(_outlook_key(), fundamental_outlook.screen,
                               force=force))
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


def _outlook_key():
    return cache_key("outlook", data_version())


# The ranking took 26 s to compute and the button asked for a fresh one on
# every click (the owner, 2026-11-25: "it takes very long"). The page now
# reads the cached answer, and this keeps one ready: computed in the
# background once a data run has delivered, and again before it would age
# past CACHE_STALE_GRACE -- where memoise() would recompute while the
# reader waits. A run in flight is waited out: its steps each move the key.
OUTLOOK_WARM_DELAY = 120.0          # the page's own first loads go first
OUTLOOK_WARM_EVERY = 60.0
OUTLOOK_REWARM_AGE = CACHE_STALE_GRACE - 20 * 60


def warm_outlook():
    """Compute the ranking if its cached answer is missing or nearly too old
    to serve. Returns True if it computed. Never two at once: a reader who
    asks meanwhile shares this computation (_compute)."""

    if bootstrap.is_running():
        return False
    key = _outlook_key()
    with _cache_lock:
        hit = _cache.get(key)
        busy = key in _inflight
    if busy or (hit is not None and time.time() - hit["at"] < OUTLOOK_REWARM_AGE):
        return False
    import fundamental_outlook
    _compute(key, fundamental_outlook.screen)
    return True


def start_warmer(delay=OUTLOOK_WARM_DELAY, every=OUTLOOK_WARM_EVERY):
    def loop():
        time.sleep(delay)
        while True:
            try:
                warm_outlook()
            except Exception as exc:                            # noqa: BLE001
                log.warning("warming the outlook ranking failed: %s: %s",
                            type(exc).__name__, str(exc)[:160])
            time.sleep(every)

    threading.Thread(target=loop, name="cache-warmer", daemon=True).start()


# One report at a time (the security review, 2026-11-23). A build starts a
# headless browser and holds it for minutes when Edge stalls, and nothing
# stopped a second request -- a double click, a reloaded tab, a link opened
# from another program -- from starting another browser beside it. A request
# that finds a build in progress waits this long, then is told to come back.
_REPORT_LOCK = threading.Lock()
REPORT_WAIT_SECONDS = 2.0


@app.route("/api/report")
def api_report():
    """One company's forward fundamental analysis as a PDF (stock_report.py).

    ?symbol=فولاد. Built when asked -- the user's choice (2026-11-23), not
    nightly -- in about three seconds, and opened in a new tab, so an error
    is a short page rather than JSON. Under /api/, so the same-origin guard
    applies: another site cannot make this machine print PDFs. One build at
    a time (_REPORT_LOCK): a second request is answered 429, not a browser.
    """

    import stock_report
    from flask import send_file
    from markupsafe import escape

    symbol = (request.args.get("symbol") or "").strip()

    def page(message, status):
        body = ('<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
                '<title>گزارش ساخته نشد</title><body style="font-family:Tahoma;'
                'padding:24px;color:#0f1729"><h3>گزارش ساخته نشد</h3><p>%s</p></body></html>'
                % escape(message))
        return body, status, {"Content-Type": "text/html; charset=utf-8"}

    if not symbol:
        return page("نماد را بنویسید.", 400)
    if not _REPORT_LOCK.acquire(timeout=REPORT_WAIT_SECONDS):
        body, status, headers = page(
            "گزارش دیگری در حال ساخت است؛ چند ثانیه‌ی دیگر دوباره امتحان کنید.",
            429)
        headers["Retry-After"] = "5"
        return body, status, headers
    try:
        path = stock_report.build(symbol)
    except stock_report.ReportError as exc:
        return page(str(exc), 404)
    except Exception as exc:                                    # noqa: BLE001
        app.logger.exception("report for %s failed", symbol)
        return page("%s: %s" % (type(exc).__name__, str(exc)[:200]), 500)
    finally:
        _REPORT_LOCK.release()
    return send_file(path, mimetype="application/pdf", as_attachment=False,
                     download_name=os.path.basename(path), max_age=0)


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

    key = cache_key("sectors", data_version(), approve, bars, min_members,
                    params_key(params))

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

    key = cache_key("fundamentals", data_version(), top)

    def build():
        return _fundamental_report(top)

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "codal", "universe"))


def _fundamental_report(top):
    """Rank the companies whose filings we hold, best fundamentals first."""

    instruments = database.load_instruments(equities_only=True)

    # Halted over a month: no current price, so no current valuation either.
    halted = database.halted_codes()

    by_code = {str(r["ins_code"]): r for r in instruments
               if str(r["ins_code"]) not in halted}

    snapshot = database.load_snapshot()

    market = {}
    if snapshot is not None and not snapshot.empty:
        for _, row in snapshot.iterrows():
            market[str(row.get("ins_code"))] = row.to_dict()

    # Whether each price can be dealt at: the outlook's trading read. شصفها
    # (±1% band, a buy queue on 14 of 20 sessions, 0.3 billion rials a day)
    # sat 10th here on a P/E of 2 (2026-11-11); like the outlook, a share
    # below the liquidity floor is listed apart, not ranked.
    import fundamental_outlook

    tails = database.load_history_tail(list(by_code),
                                       fundamental_outlook.TRADING_SESSIONS)
    bands = fundamental_outlook.price_bands()

    # A name the market watch left out had no price here, so no valuation at
    # all -- قپیرا ranked first with its Valuation pillar empty (2026-11-11).
    # Its last traded close stands in; the share count then comes from the
    # statement's capital line, and the row says so.
    off_board = set()
    for code in by_code:
        if (market.get(code) or {}).get("close"):
            continue
        frame = tails.get(code)
        if frame is None or frame.empty:
            continue
        traded = frame[frame["volume"].fillna(0) > 0]
        if not traded.empty and traded["close"].iloc[-1] > 0:
            market[code] = {"close": float(traded["close"].iloc[-1])}
            off_board.add(code)

    metrics = codal_sync.metrics_for(list(by_code), market_by_code=market)

    rows = []
    stale = []
    asset_valued = []
    thin = []

    for code, entry in metrics.items():

        instrument = by_code.get(code, {})
        trading = fundamental_outlook.trading_read(tails.get(code),
                                                   bands.get(code))

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
            "trading": trading,
            "warnings": list((trading or {}).get("notes") or []) +
                        list(entry.get("warnings") or []) +
                        (["در تابلوی امروز نبود: قیمت از آخرین معامله و تعداد "
                          "سهم از سرمایه‌ی صورت مالی است (ممکن است کهنه باشد)"]
                         if code in off_board else []),
        }

        if entry.get("stale"):
            # Listed separately rather than dropped: a company that stopped
            # filing is worth seeing, just never at the top of a ranking of
            # current fundamentals.
            stale.append(row)
        elif (instrument.get("sector_name") or "").strip() in                 config.EARNINGS_MULTIPLE_NOT_MEANINGFUL:
            # An investment company, bank or insurer is valued on its assets
            # (the outlook card ranks them by P/NAV and P/B). Even with P/E,
            # profitability and growth left out, the pillars that remain were
            # rescaled to 100 and five still sat in the top 40 (2026-11-11);
            # they are counted, not ranked beside operating companies.
            asset_valued.append(row)
        elif entry.get("score") is not None and trading and \
                trading.get("illiquid"):
            thin.append(row)
        elif entry.get("score") is not None:
            rows.append(row)

    rows.sort(key=lambda r: r["score"], reverse=True)
    thin.sort(key=lambda r: r["score"], reverse=True)
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
            "halted": len(halted),
            "asset_valued": len(asset_valued),
            "thin": len(thin),
        },
        "liquidity_floor": config.MIN_DAILY_VALUE,
        "thin": [{"symbol": r["symbol"], "score": r["score"],
                  "pe": (r["ratios"] or {}).get("pe"),
                  "avg_value": (r["trading"] or {}).get("avg_value")}
                 for r in thin[:fundamental_outlook.THIN_LISTED]],
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

    key = cache_key("moneyflow", data_version(), window, top)

    def build():
        return moneyflow.analyse_market(window=window, top=top)

    report = memoise(key, build, force=force)

    return jsonify(_with_autofetch(report, "flow", "universe"))


@app.route("/api/marketflow")
def api_marketflow():
    """Net individual (حقیقی) money into the whole market, session by session.

    moneyflow.market_daily: one sum per session over every equity, plus the
    last session, 5/20-session sums, the current run and the typical range.
    """

    sessions = max(10, min(request.args.get("sessions", 60, type=int) or 60, 250))
    force = request.args.get("refresh") == "1"
    key = cache_key("marketflow", data_version(), sessions)

    try:
        report = dict(memoise(key, lambda: moneyflow.market_daily(sessions=sessions),
                              force=force) or {})
        report["provisional"] = _provisional("flow")
        return jsonify(_with_autofetch(report, "flow", "universe"))
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


AUTOFETCH_STATUS_AGE = 60.0


def _provisional(part):
    """Today's figures while the official series lacks them, or None.

    today_provisional: the overview's index values and a volume x price
    estimate of the day's حقیقی flow, kept apart from every stored series and
    shown labelled «موقت تا انتشار رسمی». Outside the memoised answer: it
    changes on its own schedule, and reading it is a stat() and a cached
    lookup.
    """

    try:
        import today_provisional

        reading = today_provisional.current()
    except Exception:                                           # noqa: BLE001
        return None
    if not reading or not reading.get(part):
        return None
    return {"session": reading["session"], "fetched_at": reading["fetched_at"],
            "market_state": reading.get("market_state"),
            "official": reading.get("official"), part: reading[part]}


def _with_autofetch(report, *components):
    """Attach bootstrap state, and start a fetch if a component is missing.

    This is what makes the panel self-sufficient. Rather than answering "no
    data — go and run seed_import.py", an endpoint whose inputs are absent
    kicks off the download itself and tells the caller it is in flight, so the
    page can poll and fill in. bootstrap.start_background() is a no-op while a
    run is already going, and its per-step cooldown keeps an unreachable
    network from turning polling into a request flood.
    """

    # A minute-old reading is enough here: the answer only says whether to
    # start a fetch, and ensure() re-probes before it downloads anything.
    # At STATUS_TTL (5 s) every cached answer past it paid a ~1 s probe of
    # the whole price table (the full test, 2026-11-12).
    state = bootstrap.status(max_age=AUTOFETCH_STATUS_AGE)

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


@app.route("/api/health")
def api_health():
    """What ran and what did not: tasks, captures, forward log, backup, power.

    Nothing told the owner when a night's run failed or the laptop was off
    (the review, 2026-11-23). health.report() reads stored facts only -- log
    tails, the stores, the backup's manifest -- starts nothing, is cached
    for a minute and never raises. The import is local so that a defect in
    it cannot keep the panel from starting.
    """

    try:
        import health
        return jsonify(health.report(fresh=request.args.get("refresh") == "1"))
    except Exception as exc:                                    # noqa: BLE001
        return jsonify({"level": "ok", "attention": 0, "items": [],
                        "summary": "وضعیت سامانه خوانده نشد (%s)."
                                   % type(exc).__name__})


@app.route("/api/corporate_calendar")
def api_corporate_calendar():
    """Meetings, dividends and capital increases (2026-11-24).

    corporate_calendar.calendar() reads stored Codal letters and the
    exchange's adjustment list and makes no request. Each capital increase's
    full stage list is dropped here (the panel shows the latest stage); the
    answer stays under ~150 KB instead of ~480 KB.
    """

    import corporate_calendar

    try:
        back = int(request.args.get("back", 45))
        ahead = int(request.args.get("ahead", 60))
    except ValueError:
        back, ahead = 45, 60
    rep = corporate_calendar.calendar(back_days=max(1, min(back, 365)),
                                      ahead_days=max(1, min(ahead, 365)))
    for item in rep.get("capital_increases") or []:
        item.pop("stages", None)
    for item in rep.get("recent_decisions") or []:
        item.pop("other_letters", None)
    return jsonify(rep)


@app.route("/api/track_record")
def api_track_record():
    """Each card's badge, record and one specific warning (2026-11-24).

    track_record.registry() reads the experiments' result files and the
    forward logs, caches for two minutes and never raises; a card whose
    evidence cannot be read comes back with `error` set and no badge.
    """

    try:
        import track_record
        return jsonify(track_record.registry(
            use_cache=request.args.get("refresh") != "1"))
    except Exception as exc:                                    # noqa: BLE001
        return jsonify({"cards": [], "by_id": {}, "badges": {},
                        "error_detail": "%s: %s" % (type(exc).__name__, exc)})


# ---- the Bale bot's admin page (2026-11-24) ------------------------------
# The owner gives the bot to other people and administers them here. Every
# route is under the same-origin guard above, and the panel binds 127.0.0.1:
# whoever reaches these routes is at this machine. The token goes in through
# /api/bot/config and never comes back out (bot_store.public_config).

BOT_TIME = __import__("re").compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _bot_state():
    import bale_bot
    import bot_store
    bot_store.initialise()
    return {"config": bot_store.public_config(),
            "runner": bale_bot.runner_status(),
            "stats": bot_store.stats(),
            "users": bot_store.list_users()}


@app.route("/admin/bot")
def bot_admin_page():
    return render_template("bot_admin.html")


@app.route("/api/bot/state")
def api_bot_state():
    try:
        return jsonify(_bot_state())
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/bot/config", methods=["POST"])
def api_bot_config():
    """Save the settings; a new token is checked with getMe before it is kept."""

    import bale_bot
    import bot_api
    import bot_store

    body = request.get_json(silent=True) or {}
    changes = {}
    for key in ("summary_time", "alerts_time"):
        if key in body:
            value = str(body[key] or "").strip()
            if not BOT_TIME.match(value):
                return jsonify({"error": "ساعت باید به شکل HH:MM باشد"}), 400
            changes[key] = value
    for key, top in (("daily_quota", 1000), ("pdf_quota", 100)):
        if key in body:
            try:
                value = int(body[key])
            except (TypeError, ValueError):
                return jsonify({"error": "سهمیه باید عدد باشد"}), 400
            changes[key] = max(0, min(value, top))
    if "enabled" in body:
        changes["enabled"] = bool(body["enabled"])
    token = str(body.get("token") or "").strip()
    if token:
        if len(token) > 200 or any(ch.isspace() for ch in token):
            return jsonify({"error": "توکن معتبر نیست"}), 400
        config = bot_store.load_config()
        try:
            bot_api.BaleApi(token, config.get("api_base") or
                            "https://tapi.bale.ai").get_me()
        except bot_api.BaleError as exc:
            return jsonify({"error": "بله این توکن را نپذیرفت (%s)" % exc}), 400
        changes["token"] = token
    try:
        bot_store.save_config(changes)
        if token:
            bale_bot.stop()               # restart on the new token
        bale_bot.ensure(app)
        return jsonify(_bot_state())
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/bot/users/<int:chat_id>", methods=["POST"])
def api_bot_user(chat_id):
    """approve / block / pending / admin / user / quota / note / delete."""

    import bot_content
    import bot_store
    import bale_bot

    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    user = bot_store.get_user(chat_id)
    if not user:
        return jsonify({"error": "کاربر پیدا نشد"}), 404
    try:
        if action == "approve":
            bot_store.set_status(chat_id, bot_store.APPROVED)
            bot_store.enqueue(chat_id, bot_content.welcome_approved(user.get("first_name")),
                              markup=bale_bot.MENU_KEYBOARD)
        elif action == "block":
            bot_store.set_status(chat_id, bot_store.BLOCKED)
            bot_store.enqueue(chat_id, bot_content.BLOCKED)
        elif action == "pending":
            bot_store.set_status(chat_id, bot_store.PENDING)
        elif action in ("admin", "user"):
            if user.get("status") != bot_store.APPROVED:
                return jsonify({"error": "فقط کاربر تأییدشده ادمین می‌شود"}), 400
            bot_store.set_role(chat_id, action)
        elif action == "quota":
            fields = {}
            for key in ("daily_quota", "pdf_quota"):
                value = body.get(key)
                fields[key] = None if value in (None, "") else max(0, min(int(value), 1000))
            bot_store.set_fields(chat_id, fields)
        elif action == "note":
            bot_store.set_fields(chat_id, {"note": str(body.get("note") or "")[:200]})
        elif action == "delete":
            bot_store.delete_user(chat_id)
        else:
            return jsonify({"error": "اقدام ناشناخته"}), 400
        return jsonify(_bot_state())
    except (TypeError, ValueError):
        return jsonify({"error": "مقدار نامعتبر"}), 400
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/bot/broadcast", methods=["POST"])
def api_bot_broadcast():
    import bot_store

    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "متن خالی است"}), 400
    audience = "admins" if body.get("audience") == "admins" else "approved"
    try:
        bot_store.initialise()
        return jsonify({"queued": bot_store.broadcast(text[:3500], audience)})
    except Exception as exc:                                    # noqa: BLE001
        return handle_error(exc)


@app.route("/api/bootstrap")
def api_bootstrap_status():
    """What data is present, missing or stale, and any fetch in flight.

    And whether this panel's own code changed after it started
    (restart_check.py): the page's status poll is where it is said.
    """

    report = bootstrap.progress()
    try:
        import restart_check
        report["restart"] = restart_check.state()
    except Exception:                                           # noqa: BLE001
        report["restart"] = None
    return jsonify(report)


@app.route("/api/bootstrap", methods=["POST"])
def api_bootstrap_run():
    """Fetch whatever is missing or stale, in the background."""

    force = request.args.get("force") == "1"
    only = request.args.getlist("only") or None

    started = bootstrap.start_background(force=force, only=only)

    # No invalidate() here (2026-11-23). This is the page's own automatic
    # fetch; dropping the cache as it STARTS made every scan recompute
    # against a store being written, and again when the run ended. The key
    # moves once, when the run delivers (data_version). The refresh button
    # (/api/refresh) still drops it: that is a person asking.

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

    # The code as this panel loads it: the page is told when a file changes
    # under it (restart_check.py, 2026-11-25).
    try:
        import restart_check
        restart_check.mark_started()
    except Exception as exc:                                    # noqa: BLE001
        log.warning("restart check unavailable: %s", exc)

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
    #
    # All but Codal (bootstrap.AUTOMATIC): a Codal batch is minutes of paced
    # requests nothing on the session's cards waits for, and it ran on every
    # start. The evening task and the refresh button still run it.
    pending = [
        name for name, state in bootstrap.status().items()
        if state["needs_fetch"] and name in bootstrap.AUTOMATIC
    ]

    if pending:
        print(f"Fetching in the background: {', '.join(pending)}")
        print("  progress: /api/bootstrap")
        bootstrap.start_background(only=bootstrap.AUTOMATIC)
    else:
        print("All data present and current.")

    # While the panel runs, check every ten minutes for a session the
    # exchange has published since, and for today's provisional figures
    # after the close -- rather than only when a page happens to ask
    # (2026-11-24: a deferred index waited from 15:16 to the evening).
    bootstrap.start_keeper()
    print(f"Keeper: checking every {int(bootstrap.KEEPER_INTERVAL // 60)} min")

    # The fundamental outlook's ranking, kept computed (warm_outlook).
    start_warmer()

    # The Bale bot, when /admin/bot has it enabled with a token (2026-11-24).
    # In this process, handed this app: one copy of the panel's caches.
    try:
        import bale_bot
        if bale_bot.start(app):
            print("Bale bot: running (manage it at /admin/bot)")
    except Exception as exc:                                    # noqa: BLE001
        print(f"Bale bot: not started ({type(exc).__name__}: {exc})")

    print("=" * 60)

    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
