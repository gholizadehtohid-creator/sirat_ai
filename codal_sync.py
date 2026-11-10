"""Keep the local Codal mirror current without ever blocking a scan.

The constraint this module exists to satisfy: a full universe of filings is
~800 MB and hours of downloading, while the daily job that actually matters --
price history, indicators, ranking -- must not wait for any of it.

Three mechanisms, in the order they save work:

  1. THE CHEAP CALL GATES THE EXPENSIVE ONE. A search is ~16 KB and lists every
     filing with its tracing number. A statement is ~1 MB. So the search runs
     first, and a statement is downloaded only for tracing numbers not already
     marked parsed. Steady state is one small request per company per day and
     no downloads at all until someone files something.

  2. PACED BY ATTEMPT, NOT BY AGE. A company that has filed nothing new stays
     "not fresh" forever if freshness is judged by how old its last filing is
     -- annual reports are eleven months stale by design. Recency is measured
     from the last *check*, so a quiet company is checked daily and downloaded
     never.

  3. A CEILING PER RUN. Even with both of the above, a cold start wants 818
     statements. CODAL_MAX_FETCH_PER_RUN caps it, and the queue is ordered by
     how much the rest of the system already cares about each symbol, so the
     names in the portfolio and at the top of the screen fill in first and the
     tail fills in over subsequent days.

Nothing here raises, and nothing here is on the request path: the panel reads
whatever is already stored, and a Codal outage means yesterday's figures rather
than a stalled scan.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import codal
import config
import database
import financials
import nav
import settings

try:
    from logging_setup import get_logger
    log = get_logger(__name__)
except Exception:
    log = None


_lock = threading.Lock()

_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "checked": 0,
    "fetched": 0,
    "failed": 0,
    "total": 0,
    "current": None,
    "log": [],
}

_thread = None


def _now():
    return datetime.now(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _note(message):
    with _lock:
        _state["log"].append({
            "at": _now().isoformat(timespec="seconds"),
            "message": message,
        })
        _state["log"] = _state["log"][-80:]

    if log:
        log.info("codal: %s", message)


# ==============================
# COOL-OFF AFTER A THROTTLED RUN
# ==============================
#
# Stored in the provenance table rather than in memory so it survives a panel
# restart. Restarting is the first thing anyone does when a feature looks
# stuck, and forgetting the cool-off on restart would mean the fastest way to
# get rate-limited again is to try to fix it.

_COOLDOWN_SCOPE = "codal:cooldown"


def cooldown_state():
    """(until, minutes) of the current cool-off, or (None, 0)."""

    record = database.get_provenance(_COOLDOWN_SCOPE) or {}

    until = _parse(record.get("detail"))

    try:
        minutes = int(record.get("status") or 0)
    except (TypeError, ValueError):
        minutes = 0

    return until, minutes


def cooling_off():
    """Seconds left before Codal may be contacted again, or 0."""

    until, _minutes = cooldown_state()

    if until is None:
        return 0

    remaining = (until - _now()).total_seconds()

    return int(remaining) if remaining > 0 else 0


def start_cooldown():
    """Double the cool-off after another throttled run."""

    _until, minutes = cooldown_state()

    minutes = min(
        config.CODAL_COOLDOWN_MAX_MINUTES,
        (minutes * 2) if minutes else config.CODAL_COOLDOWN_START_MINUTES,
    )

    until = _now() + timedelta(minutes=minutes)

    database.set_provenance(
        _COOLDOWN_SCOPE, str(minutes), until.isoformat(timespec="seconds")
    )

    _note(f"cooling off for {minutes} minutes — Codal is throttling us, and "
          f"retrying sooner renews the throttle rather than clearing it")

    return minutes


def clear_cooldown():
    until, minutes = cooldown_state()

    if until is not None or minutes:
        database.set_provenance(_COOLDOWN_SCOPE, "0", "")


# ==============================
# WHAT TO DO NEXT
# ==============================

def due_for_check(status_row, recheck_hours=None, retry_minutes=None,
                  empty_hours=None):
    """Has enough time passed to ask Codal whether this company filed?

    Three intervals, because the last outcome can mean three different things
    and they have wildly different shelf lives:

      succeeded        good for a day. Companies file quarterly, so a check
                       that found nothing new will keep finding nothing new.

      failed, transient  worth nothing at all. A rate limit or a dead route
                       says something about the network, not the company, so
                       it expires in minutes. Collapsing this into the daily
                       interval is what made a VPN toggle look like a
                       permanent outage: the route came back in seconds and
                       the panel stayed empty until tomorrow, because every
                       symbol had been marked "checked" while failing.

      failed, empty    Codal answered and has nothing for this symbol. Usually
                       permanent -- funds, rights, newly listed names -- so it
                       backs off for days rather than competing for a run's
                       budget with companies that would actually return data.
    """

    recheck_hours = (config.CODAL_RECHECK_HOURS if recheck_hours is None
                     else recheck_hours)
    retry_minutes = (config.CODAL_RETRY_MINUTES if retry_minutes is None
                     else retry_minutes)
    empty_hours = (config.CODAL_EMPTY_BACKOFF_HOURS if empty_hours is None
                   else empty_hours)

    if not status_row:
        return True

    last = _parse(status_row.get("last_checked"))

    if last is None:
        return True

    since = _now() - last

    if status_row.get("error"):

        kind = status_row.get("error_kind")

        if kind == codal.KIND_EMPTY:
            return since > timedelta(hours=empty_hours)

        if kind == codal.KIND_BAD:
            # The response arrived and made no sense. Not the network, so not
            # the fast retry -- but not settled either, since a company's next
            # filing may parse where this one did not.
            return since > timedelta(hours=recheck_hours)

        return since > timedelta(minutes=retry_minutes)

    return since > timedelta(hours=recheck_hours)


def priority(instrument, portfolio_codes=(), screened_codes=()):
    """Queue order. Lower sorts first.

    The point of the ordering is that a cold start is useful on day one: the
    names already held or already at the top of the screen get their filings
    first, rather than the universe filling in alphabetically over a week
    while the portfolio view stays empty.
    """

    code = str(instrument.get("ins_code"))

    if code in portfolio_codes:
        return 0

    if code in screened_codes:
        return 1

    return 2


def plan(limit=None, portfolio_codes=(), screened_codes=(), recheck_hours=None,
         retry_minutes=None):
    """The companies worth contacting Codal about on this run."""

    limit = config.CODAL_MAX_FETCH_PER_RUN if limit is None else limit

    instruments = database.load_instruments(equities_only=True)

    status = database.load_codal_status()

    due = [
        row for row in instruments
        if due_for_check(status.get(str(row["ins_code"])), recheck_hours,
                         retry_minutes)
    ]

    # A company that failed transiently is the most likely to succeed now --
    # the network moved, or the rate limit expired -- so it goes to the front
    # rather than waiting behind the routine daily re-checks.
    retrying = {
        str(code) for code, row in status.items()
        if row.get("error_kind") == codal.KIND_TRANSIENT
    }

    due.sort(key=lambda row: (
        0 if str(row["ins_code"]) in retrying else 1,
        priority(row, portfolio_codes, screened_codes),
        str(row.get("symbol_fa") or ""),
    ))

    return due[:limit] if limit else due


# ==============================
# ONE COMPANY
# ==============================

def codal_symbol(instrument):
    """The symbol to send to Codal: the exchange's own spelling.

    `symbol_fa` is folded for internal joining -- persian.normalize turns آ
    into ا so that a snapshot and a universe list agree -- and that folding is
    exactly wrong on the way out. Codal knows آسیا, آبادا and آریا; it has
    never heard of اسیا, ابادا or اریا, and it answers those with an empty
    result rather than an error. Fifty-one companies, every one of them
    starting with آ, came back as "no filings" for that reason alone.

    `symbol_raw` is the unfolded spelling the universe import kept for exactly
    this kind of use, so it is preferred and the folded form is the fallback.
    """

    raw = (instrument.get("symbol_raw") or "").strip()

    return raw or (instrument.get("symbol_fa") or "").strip()


def sync_company(client, instrument, max_statements=2):
    """Search, then download only the filings we do not already hold.

    `max_statements` bounds the download per company per run. Two is the
    useful number: the newest filing, plus the one before it when a company
    has just published an unaudited statement that its audited version will
    shortly replace.
    """

    ins_code = str(instrument["ins_code"])

    symbol = codal_symbol(instrument)

    found = client.search_letters(symbol)

    if not found.ok:
        database.set_codal_status(
            ins_code, symbol=symbol, checked=True,
            error=found.error, error_kind=found.kind,
        )
        return {"symbol": symbol, "ok": False, "error": found.error,
                "kind": found.kind, "transient": found.transient,
                "fetched": 0, "letters": 0}

    letters = found.data

    database.save_codal_letters(ins_code, letters)

    already = database.known_tracing_numbers(ins_code)

    # Only filings whose figures this project reads, that carry a machine
    # readable body, and that we have not already parsed.
    pending = [
        letter for letter in letters
        if codal.is_letter(letter, config.CODAL_LETTER_STATEMENTS)
        and letter.get("excel_url")
        and str(letter.get("tracing_no")) not in already
    ]

    stored = 0
    errors = []
    transient = False

    for letter in pending[:max_statements]:

        record, error, kind = parse_letter(client, letter)

        if error:
            errors.append(error)
            if kind == codal.KIND_TRANSIENT:
                # The route or the rate limit, not this filing. Leave it
                # unparsed so the next run tries it again, and stop touching
                # this company now rather than burning the remaining budget.
                transient = True
                break
            # A filing we genuinely cannot read. Mark it parsed so a megabyte
            # is not re-downloaded every run to fail the same way.
            database.mark_letter_parsed(letter["tracing_no"])
            continue

        if record:
            database.save_codal_financials(ins_code, record)
            stored += 1

        database.mark_letter_parsed(letter["tracing_no"])

    # ---- portfolio filings (ن-۳۱) ---------------------------------------
    #
    # Only the newest one is worth holding: NAV is a position at a date, so an
    # older portfolio answers a question nobody asked. One download per
    # investment company per run keeps this cheap for the ~95 companies that
    # file it and free for the ~700 that do not.
    if not transient:

        portfolio_pending = [
            letter for letter in letters
            if codal.is_letter(letter, config.CODAL_LETTER_PORTFOLIO)
            and letter.get("excel_url")
            and str(letter.get("tracing_no")) not in already
        ]

        for letter in portfolio_pending[:1]:

            record, error, kind = parse_portfolio_letter(client, letter)

            if error:
                errors.append(error)
                if kind == codal.KIND_TRANSIENT:
                    transient = True
                    break
                database.mark_letter_parsed(letter["tracing_no"])
                continue

            if record:
                database.save_codal_portfolio(ins_code, record)
                stored += 1

            # Marked either way: a manufacturer's monthly report parses to no
            # portfolio, and re-downloading it every run to find that out
            # again would spend the budget on companies that have no NAV.
            database.mark_letter_parsed(letter["tracing_no"])

    periods = database.load_codal_financials(ins_code, limit=1)

    database.set_codal_status(
        ins_code,
        symbol=symbol,
        checked=True,
        fetched=bool(stored),
        letters=len(letters),
        periods=len(database.load_codal_financials(ins_code)),
        newest_period=periods[0]["period_end"] if periods else None,
        error="; ".join(errors)[:300] if errors else None,
        error_kind=(codal.KIND_TRANSIENT if transient
                    else (codal.KIND_BAD if errors else None)),
    )

    return {
        "symbol": symbol,
        # A company whose search succeeded is a successful check even if one of
        # its filings would not parse -- the search is what "checked" means.
        "ok": not transient,
        "error": "; ".join(errors) if errors else None,
        "kind": codal.KIND_TRANSIENT if transient else None,
        "transient": transient,
        "fetched": stored, "letters": len(letters),
        "pending": len(pending),
    }


def parse_portfolio_letter(client, letter):
    """One ن-۳۱ filing -> (record, error, kind). Never raises.

    Returns (None, None, KIND_OK) when the filing has no portfolio sheets:
    ordinary companies file a monthly report under this code too, and that is
    not an error, just nothing to store.
    """

    title = letter.get("title") or ""

    got = client.fetch_sheets(letter.get("excel_url"),
                              archive_key=letter.get("tracing_no"))

    if not got.ok:
        return None, f"{letter.get('tracing_no')}: {got.error}", got.kind

    portfolio = nav.extract_portfolio(got.data)

    if not portfolio:
        return None, None, codal.KIND_OK

    ending = financials.period_end(title)

    if not ending:
        return (None, f"{letter.get('tracing_no')}: no period end in title",
                codal.KIND_BAD)

    return {
        "period_end": ending,
        "tracing_no": letter.get("tracing_no"),
        "title": title,
        **portfolio,
    }, None, codal.KIND_OK


def parse_letter(client, letter):
    """One filing -> (record, error, kind). Never raises.

    `kind` is what the caller needs to decide whether to try again: a
    rate-limited download deserves another attempt shortly, while a filing
    whose title carries no period end will never parse no matter how often it
    is fetched.
    """

    title = letter.get("title") or ""

    # Decided from the title, before a byte is downloaded: another company's
    # figures under this symbol would replace its own latest period.
    subsidiary = financials.subsidiary_of(title)
    if subsidiary:
        return (None, f"{letter.get('tracing_no')}: subsidiary's statement "
                      f"({subsidiary})", codal.KIND_BAD)

    got = client.fetch_sheets(letter.get("excel_url"),
                              archive_key=letter.get("tracing_no"))

    if not got.ok:
        return None, f"{letter.get('tracing_no')}: {got.error}", got.kind

    current = financials.extract(got.data, index=0)

    if not current:
        return (None, f"{letter.get('tracing_no')}: no statements in filing",
                codal.KIND_BAD)

    previous = financials.extract(got.data, index=1)

    months = financials.period_months(title)
    ending = financials.period_end(title)

    if not ending:
        return (None, f"{letter.get('tracing_no')}: no period end in title",
                codal.KIND_BAD)

    ratios = financials.ratios(current, previous, market=None, months=months)

    return {
        "period_end": ending,
        "period_months": months,
        "tracing_no": letter.get("tracing_no"),
        "title": title,
        "audited": financials.is_audited(title),
        "consolidated": bool(current.get("consolidated")),
        "items": financials.summarise(current),
        # Ratios are recomputed against a live price whenever they are read --
        # valuation moves daily while the filing does not -- so what is stored
        # here is the price-independent half. Keeping it saves reparsing a
        # megabyte to recover ROE.
        "ratios": ratios,
    }, None, codal.KIND_OK


# ==============================
# A RUN
# ==============================

def run(limit=None, portfolio_codes=(), screened_codes=(), recheck_hours=None,
        retry_minutes=None, max_statements=2, progress=None,
        abort_after=None, ignore_cooldown=False):
    """Sync a bounded batch. Returns a summary; never raises."""

    abort_after = (config.CODAL_ABORT_AFTER_FAILURES if abort_after is None
                   else abort_after)

    waiting = cooling_off()

    if waiting and not ignore_cooldown:
        _note(f"skipped — cooling off for another {waiting // 60}m {waiting % 60}s")
        return {"checked": 0, "fetched": 0, "failed": 0, "planned": 0,
                "cooling_off": waiting}

    queue = plan(
        limit=limit,
        portfolio_codes=portfolio_codes,
        screened_codes=screened_codes,
        recheck_hours=recheck_hours,
        retry_minutes=retry_minutes,
    )

    with _lock:
        _state["total"] = len(queue)
        _state["checked"] = 0
        _state["fetched"] = 0
        _state["failed"] = 0

    if not queue:
        _note("nothing due for a Codal check")
        return {"checked": 0, "fetched": 0, "failed": 0, "planned": 0}

    _note(f"checking {len(queue)} companies")

    client = codal.CodalClient()

    checked = fetched = failed = 0
    consecutive = 0
    aborted = None

    for index, instrument in enumerate(queue, start=1):

        symbol = instrument.get("symbol_fa") or instrument["ins_code"]

        with _lock:
            _state["current"] = symbol

        try:
            outcome = sync_company(client, instrument,
                                   max_statements=max_statements)
        except Exception as exc:                # the contract says never raise
            outcome = {"ok": False, "fetched": 0,
                       "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

        checked += 1
        fetched += outcome.get("fetched", 0)

        if outcome.get("ok"):
            consecutive = 0
        else:
            failed += 1
            # Only route-level and rate-limit failures suggest Codal itself is
            # the problem. A symbol that simply has no filings -- funds, rights,
            # newly listed names, of which the universe holds plenty -- is a
            # settled answer, and counting it toward the abort meant a run
            # could stop before reaching a single company that would have
            # worked.
            consecutive = consecutive + 1 if outcome.get("transient") else 0

        with _lock:
            _state["checked"] = checked
            _state["fetched"] = fetched
            _state["failed"] = failed

        if progress:
            progress(index, len(queue), symbol)

        # Codal is unreachable rather than these particular companies being
        # unlucky. Grinding through the rest of the queue costs minutes and
        # cannot succeed; stopping now means the retry -- minutes away, not
        # tomorrow -- starts the moment the route is back.
        if abort_after and consecutive >= abort_after:
            aborted = (f"stopped after {consecutive} consecutive failures — "
                       f"codal.ir looks unreachable")
            _note(aborted)
            break

    with _lock:
        _state["current"] = None

    _note(f"done: {checked} checked, {fetched} filings stored, {failed} failed")

    if fetched:
        try:
            import fundamental_db
            fundamental_db.sync_from_market_db()
        except Exception as exc:                                # noqa: BLE001
            _note(f"fundamental store sync failed: {exc}")

    cooldown = None

    if aborted:
        cooldown = start_cooldown()
    elif fetched:
        # Something actually came back, so whatever the throttle was, it is
        # over. One good run wipes the escalation rather than leaving the next
        # one waiting out a penalty that no longer applies.
        clear_cooldown()

    return {"checked": checked, "fetched": fetched, "failed": failed,
            "planned": len(queue), "aborted": aborted,
            "cooldown_minutes": cooldown}


def start_background(ignore_cooldown=False, **kwargs):
    """Kick off a run in a daemon thread. True if one started."""

    kwargs["ignore_cooldown"] = ignore_cooldown

    global _thread

    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
        _state["started_at"] = _now().isoformat(timespec="seconds")
        _state["finished_at"] = None

    def target():
        try:
            run(**kwargs)
        finally:
            with _lock:
                _state["running"] = False
                _state["finished_at"] = _now().isoformat(timespec="seconds")

    _thread = threading.Thread(target=target, name="sirat-codal", daemon=True)
    _thread.start()

    return True


def is_running():
    with _lock:
        return bool(_state["running"])


def progress():
    with _lock:
        snapshot = dict(_state)
        snapshot["log"] = list(_state["log"])

    snapshot["coverage"] = database.codal_coverage()

    return snapshot


# ==============================
# READING WHAT IS STORED
# ==============================

def metrics_for(ins_codes, market_by_code=None, sector_by_code=None):
    """Fundamental ratios and scores for many companies, from cache only.

    This is what the scan calls. It touches the network never and the database
    twice -- the filings, and the sectors unless `sector_by_code` is given -- so
    adding fundamentals to a whole-market ranking costs two queries, which is
    the difference between a fundamental view and a slower bot.
    """

    import fundamental_scoring

    market_by_code = market_by_code or {}

    latest = database.load_codal_latest(ins_codes)

    # Which industry each company is in decides whether its P/E means anything
    # (config.EARNINGS_MULTIPLE_NOT_MEANINGFUL). Read here rather than asked of
    # the callers, so the panel's card and the scan cannot disagree about it.
    if sector_by_code is None:
        sector_by_code = {str(row["ins_code"]): row.get("sector_name")
                          for row in database.load_instruments()}

    # "Now", taken from the market rather than the clock: the newest period any
    # company has filed. Using the wall clock would mark the whole market stale
    # in the weeks between a quarter closing and its statements appearing.
    newest_period = max(
        (r.get("period_end") or "" for r in latest.values()), default=""
    )

    out = {}

    for code, record in latest.items():

        items = record.get("items") or {}

        # Valuation is recomputed here rather than read from storage: P/E moves
        # with the price every session while the filing behind it does not, so
        # a stored P/E is wrong by the end of the day it was written.
        ratios = financials.ratios(
            items,
            previous=None,
            market=market_by_code.get(code) or {},
            months=record.get("period_months"),
        )

        # Growth needs the comparative period, which the stored ratios already
        # hold from when the filing was parsed. Carry those across rather than
        # re-downloading the filing to recover them -- through the near-zero
        # base rule, which filings parsed before it existed never went
        # through (فناوا carried EPS +175,200% into a full growth score).
        stored = record.get("ratios") or {}

        for key in ("revenue_growth", "profit_growth", "eps_growth",
                    "operating_profit_growth", "equity_growth"):
            if ratios.get(key) is None:
                ratios[key] = financials.comparable_growth(stored.get(key))

        # An investment company's P/E is last year's disposals, a bank's and
        # an insurer's a provisioning choice: scored, they put وجامی (P/E 1.16)
        # first and 13 such names in the top 40 on 2026-11-11. Withheld from
        # the score the way fundamental_engine.market_ratios() and the snapshot
        # screen withhold them -- but still shown, as the screen does, because
        # the panel reads an empty P/E as "above 60" and the multiple itself
        # is a fact; it is the "very cheap" verdict on it that is wrong.
        sector = (sector_by_code.get(code) or "").strip()
        withheld = sector in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL

        scored = fundamental_scoring.score(
            dict(ratios, pe=None, earnings_yield=None) if withheld else ratios
        )

        notes = list(scored["notes"])

        if withheld and ratios.get("pe") is not None:
            # In place of the scorer's "P/E unavailable (no positive
            # earnings)", which is what it says of any absent P/E and is
            # untrue of a profitable bank.
            note = (f"Valuation: P/E {ratios['pe']:.1f} withheld — "
                    f"{sector} is valued on assets, not earnings")
            notes = [note if n.startswith("Valuation: P/E unavailable") else n
                     for n in notes]
            if note not in notes:
                notes.append(note)

        age = financials.period_age_months(
            record.get("period_end"), newest_period
        )

        stale = age is not None and age > config.CODAL_STALE_MONTHS

        # A company that stopped filing years ago -- usually suspended or in
        # dispute -- otherwise ranks on those old figures as if they described
        # today. The score is kept and shown, because the numbers are real and
        # were really filed, but the verdict says so and callers that rank on
        # it drop the score rather than trusting it.
        out[code] = {
            "period_end": record.get("period_end"),
            "period_months": record.get("period_months"),
            "period_age_months": age,
            "stale": stale,
            "audited": record.get("audited"),
            "consolidated": record.get("consolidated"),
            "title": record.get("title"),
            "updated_at": record.get("updated_at"),
            "items": items,
            "ratios": ratios,
            "score": None if stale else scored["score"],
            "reported_score": scored["score"],
            "status": None if stale else scored["status"],
            "verdict": ("STALE" if stale else scored["verdict"]),
            "breakdown": scored["breakdown"],
            "notes": (
                notes + [
                    f"Filing: last reported period {record.get('period_end')} "
                    f"is {age} months behind the market's newest — treated as "
                    f"out of date."
                ] if stale else notes
            ),
            "weight_covered": scored["weight_covered"],
        }

    return out
