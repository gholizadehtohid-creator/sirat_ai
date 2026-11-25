"""Codal client: find a company's filings, and turn one into numbers.

codal.ir is where every listed Iranian company files. Two endpoints carry
everything this project needs, and the size difference between them decides
the whole design:

    search.codal.ir/api/search/v2/q      announcement metadata for one symbol.
                                         ~16 KB. Cheap enough to poll daily.

    excel.codal.ir/service/Excel/GetAll  the filing itself. 0.5-1 MB.

So the cheap call decides whether the expensive one runs. A statement is
downloaded only when the search shows a filing whose tracing number we have not
already stored -- which is what keeps a universe-wide fundamental refresh from
being an 800 MB download every morning.

Three things about this data that are not guessable and were verified against
live responses:

  THE EXCEL ENDPOINT RETURNS HTML -- FOR THE CURRENT ERA. It is named Excel,
  it sends `application/ms-excel`, and its body starts `\\r\\n<h`. It is an
  ASP.NET table dump, not a workbook -- openpyxl cannot open it and
  pandas.read_excel refuses it. It parses perfectly as HTML.

  OLD FILINGS ARE REAL LEGACY WORKBOOKS. Verified 2026-08-23 against a 1393
  interim statement: the same endpoint returns an OLE2 compound document
  (magic d0cf11e0a1b11ae1, a genuine BIFF .xls) for the early era, one flat
  right-to-left grid holding every statement. The two eras are told apart by
  the first eight BYTES of the body, never by date -- `split_sheets_xls`
  handles the old one and needs xlrd. Before this branch existed the old era
  read as "no table at all" and was classified transient, so a backfill
  walking past ~1400 backed off forever against documents that were never
  going to become HTML.

  AMOUNTS ARE IN MILLION RIALS, PER-SHARE FIGURES IN RIALS, ON THE SAME SHEET.
  Verified by reconstruction: فولاد's FY1404 sheet gives owners' profit
  1,242,982,482 and capital 1,935,000,000, both "ميليون ريال". At 1000 rials
  par that is 1.935e12 shares and an EPS of 642 rials against the 647 the same
  sheet reports. Treating the amounts as rials understates every company by a
  factor of a million and produces ratios that look merely odd rather than
  obviously broken.

  SHEETS ARE DELIMITED BY HEADINGS, NOT BY TABLE INDEX. A filing holds 10-70
  tables and their order shifts with the company's template: a bank, an
  investment company and a refiner return different sheet counts in different
  orders. Splitting on the <h*> headings and keeping the tables that follow
  each one is stable across all of them; "table 4 is the balance sheet" is not.

Nothing here raises. Every entry point returns a result object carrying an
error string, the same contract as providers/base.py, so a Codal outage
degrades the fundamental view instead of taking down a scan.
"""

import gzip
import io
import os
import re
import threading
import time
from urllib.parse import urlparse

import pandas as pd

import config
import persian
import settings
from providers.transport import (
    build_session,
    is_connection_error,
    maybe_rebind,
)

try:
    from logging_setup import get_logger
    log = get_logger(__name__)
except Exception:                                     # logging is optional here
    log = None


# ==============================
# RESULT
# ==============================

# Why a call failed, which decides what the caller does next. Collapsing these
# into one "error" string made the sync retry a company with nothing on Codal
# every six minutes forever, while a genuine rate-limit looked identical to it.
KIND_OK = "ok"
KIND_EMPTY = "empty"            # Codal answered; this symbol has no filings
KIND_TRANSIENT = "transient"    # rate limit, 5xx, route down -- try again soon
KIND_BAD = "bad"                # the response arrived and made no sense


# Smallest response that could be a real statement. Measured, not guessed:
# genuine filings in this database run 177 KB to 434 KB, and a throttled reply
# came back at 3,035 bytes. Anything under this is Codal declining to serve the
# document, which is a transient condition, not an unreadable filing.
MIN_FILING_BYTES = 20_000

# A heading naming a financial statement. Its presence with no <table> marks
# Codal's empty export (see fetch_sheets), which no block page imitates.
EMPTY_EXPORT_HEADING = re.compile(
    r"<h\d[^>]*>\s*(?:صورت|ترازنامه)", re.UNICODE)

# Codal's exporter answering with nothing in it (2026-11-25). Two shapes,
# both under MIN_FILING_BYTES and both repeated byte for byte for days:
#   * the export page's template with nothing rendered into it -- the
#     statement headings, then "{{table.title}}" and "{{cell.value | ...}}"
#     where the figures belong (13,185 bytes, four 1398 nine-month filings);
#   * the Excel-workbook shell with an empty body (1,450 bytes, three filings
#     of مهرگان and تپکو).
# Those seven were the whole backfill queue that day. Each had answered the
# same bytes 31 times; every round took them for a throttle and backed off,
# and the campaign never reached its finished, watch-the-feed state. No block
# page carries Codal's export template or its workbook shell, so content
# tells these apart from a throttle. They stay TRANSIENT here -- a new
# filing's export may not be built yet -- and the caller decides when a
# repeat settles one (codal_backfill.EMPTY_EXPORT_SETTLE_DAYS).
EXPORTER_EMPTY = "Codal's exporter sent"


def exporter_empty(text):
    """Which empty export `text` is ("an unrendered template", "an empty
    workbook"), or None for anything else -- a block page included."""

    if "{{table." in text or "{{cell.value" in text:
        return "an unrendered template"
    if "Excel Workbook Frameset" in text and "<table" not in text.lower():
        return "an empty workbook"
    return None


class CodalResult:
    """Uniform return type. `ok` means the caller got usable data."""

    __slots__ = ("data", "error", "warnings", "source", "kind")

    def __init__(self, data=None, error=None, warnings=None, source="codal",
                 kind=None):
        self.data = data
        self.error = error
        self.warnings = list(warnings or [])
        self.source = source
        self.kind = kind or (KIND_OK if (data is not None and not error)
                             else KIND_BAD)

    @property
    def ok(self):
        return self.data is not None and self.error is None

    @property
    def transient(self):
        """Worth retrying shortly, as opposed to a settled negative answer."""

        return self.kind == KIND_TRANSIENT

    def __repr__(self):
        return f"<CodalResult ok={self.ok} kind={self.kind} error={self.error!r}>"


def classify(exc):
    """Which kind of failure an exception represents."""

    status = getattr(getattr(exc, "response", None), "status_code", None)

    if status in (429, 500, 502, 503, 504) or is_connection_error(exc):
        return KIND_TRANSIENT

    return KIND_BAD


# ==============================
# NUMBERS
# ==============================

# Codal writes numbers in Persian digits with thousands separators, and marks
# negatives with parentheses rather than a minus sign: (۳,۴۹۳,۴۱۴,۵۲۹). A
# parser that only strips commas turns every cost line into a positive number,
# which silently inflates profit instead of failing.
_PAREN = re.compile(r"^\((.*)\)$")
_NOT_NUMERIC = re.compile(r"[^\d.\-]")


def parse_number(value):
    """Codal cell -> float, or None when the cell holds no number."""

    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number else None       # NaN check

    text = persian.normalize(str(value)).strip()

    if not text or text in ("-", "--", "—"):
        return None

    negative = False

    match = _PAREN.match(text)
    if match:
        negative = True
        text = match.group(1)

    text = text.replace("،", "").replace(",", "").replace("٬", "")
    text = _NOT_NUMERIC.sub("", text)

    if not text or text in ("-", "."):
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    return -number if negative else number


# ==============================
# CLIENT
# ==============================

class RateLimiter:
    """A shared minimum gap between requests that widens when Codal objects.

    A fixed delay is a guess about someone else's limit. Codal answers 429
    when the guess is wrong, and the only useful response is to slow down and
    stay slow for a while -- retrying at the same rate just collects more
    429s, which is what turned a bulk sync into twenty failures and an
    early abort.

    The penalty decays: after a quiet spell the delay walks back toward the
    base, so one burst of throttling does not leave the client crawling for
    the rest of the session.
    """

    def __init__(self, delay, maximum=8.0):
        self.base = delay
        self.delay = delay
        self.maximum = maximum
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            delay = self.delay
            gap = time.monotonic() - self._last

        if delay > 0 and gap < delay:
            time.sleep(delay - gap)

        with self._lock:
            self._last = time.monotonic()

    def penalise(self, factor=2.0):
        """Codal said slow down. Believe it."""

        with self._lock:
            self.delay = min(self.maximum, max(self.base, self.delay) * factor)
            return self.delay

    def relax(self, step=0.85):
        with self._lock:
            if self.delay > self.base:
                self.delay = max(self.base, self.delay * step)
            return self.delay


# One limiter for the whole process, not one per client.
#
# Codal throttles on sustained access, and a per-client limiter forgets that
# the moment a run ends: the next batch starts back at full speed, collects
# another 429 within seconds, and the penalty is re-learned from scratch every
# time. Sharing it means a throttle learned at 15:34 is still respected at
# 15:41, which is the only version of this that converges.
_SHARED_LIMITER = None
_LIMITER_LOCK = threading.Lock()


def shared_limiter():
    global _SHARED_LIMITER

    with _LIMITER_LOCK:
        if _SHARED_LIMITER is None:
            _SHARED_LIMITER = RateLimiter(
                config.CODAL_REQUEST_DELAY, maximum=config.CODAL_MAX_DELAY
            )
        return _SHARED_LIMITER


# One session for the whole process, for the same reason as the limiter but a
# different mechanism: codal.ir sits behind an F5 BIG-IP application firewall,
# which hands out a `TS...` cookie and uses it to recognise a returning client.
# A fresh session per client -- or per rebind -- throws that cookie away and
# arrives looking like a brand new visitor every time, which is precisely the
# pattern the firewall is built to throttle. Reusing the session keeps the
# cookie, so we stay a known caller rather than an endless stream of strangers.
_SHARED_SESSION = None
_SESSION_LOCK = threading.Lock()


def shared_session(rebuild=False):
    global _SHARED_SESSION

    with _SESSION_LOCK:
        if _SHARED_SESSION is None or rebuild:
            # No transport retry on 429/403: one refusal reaches the
            # client's limiter and the caller's pacer as one, instead of
            # four requests inside the throttle window (2026-11-11).
            _SHARED_SESSION = build_session(
                headers=CodalClient.HEADERS, host=CODAL_HOST,
                retry_status=(500, 502, 503, 504)
            )
        return _SHARED_SESSION

# The host whose route this client's sessions are bound for. search.codal.ir
# and excel.codal.ir are separate names but resolve into the same network, and
# probing one answer for the family beats three probes that agree. It is NOT
# the same answer as TSETMC's -- on this machine codal.ir comes through the
# tunnel while cdn.tsetmc.com only answers on the physical NIC -- which is why
# the binding is per host rather than one address for the whole process.
CODAL_HOST = "codal.ir"


def filing_url_refusal(url):
    """Why `url` is not an address to download a filing from, or None.

    A filing's address is whatever the search reply's ExcelUrl says, and
    fetch_sheets downloaded it as given -- over a session that may be bound
    to the physical NIC, which reaches the home network and this machine's
    own panel as well as Codal (the security review, 2026-11-23). Only
    https, and only codal.ir or a name under it: every one of the 37,657
    stored excel_url values is https://excel.codal.ir (checked that day).
    A backslash, a space or a control character is refused outright -- they
    are where two URL parsers stop agreeing on which host is meant.
    """

    text = str(url or "").strip()

    if any(ch == "\\" or ch.isspace() or ord(ch) < 32 for ch in text):
        return "not a plain URL"

    try:
        parts = urlparse(text)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return "not a URL"

    if parts.scheme.lower() != "https":
        return "scheme %s is not https" % (parts.scheme or "(none)")[:20]

    if host != CODAL_HOST and not host.endswith("." + CODAL_HOST):
        return "host %s is not %s" % ((host or "(none)")[:80], CODAL_HOST)

    return None


class CodalClient:
    """Search filings and parse them. Never raises."""

    name = "codal"

    # Codal is not TSETMC and does not want TSETMC's Referer, which
    # build_session() sets for the market API. Sending it works today but is a
    # lie about where the request came from, and the kind of thing a site
    # starts filtering on.
    HEADERS = {
        "Accept": "text/html,application/json,application/xhtml+xml,*/*",
        "Referer": "https://codal.ir/",
    }

    def __init__(self, session=None, delay=None, limiter=None):
        self._own_session = session is None
        self.session = session or shared_session()
        self.limiter = limiter or (
            RateLimiter(delay, maximum=config.CODAL_MAX_DELAY)
            if delay is not None else shared_limiter()
        )
        self._session_lock = threading.Lock()

    # ---- transport --------------------------------------------------------

    def _get(self, url, params=None, timeout=None):
        """GET, re-probing the route once if the network moved underneath us.

        This client holds a session bound to one local source address, chosen
        when the session was built. On this machine that address is what makes
        Iranian hosts reachable at all: with a TUN-mode VPN up, the default
        route reaches the world but not codal.ir, and the physical NIC reaches
        codal.ir but not the world. Binding picks the right one per host.

        The catch is that the choice is made once. The moment the user toggles
        their VPN, the bound address stops working and *every* Codal request
        fails, permanently, until the process restarts -- which is a terrible
        thing to ask of someone whose only mistake was turning off a VPN.

        So a route-level failure re-probes the route once and retries on a
        freshly built session. A refusal from Codal itself (404, 403, 500) is
        not a route failure and must not trigger this: during a bulk sync a few
        symbols legitimately fail, and re-probing the network for each would be
        pure churn. is_connection_error() draws that line, and
        maybe_rebind() rate-limits the probe process-wide so a burst of
        failures costs one re-probe rather than one per request.
        """

        timeout = timeout or (settings.TIMEOUT_CONNECT, settings.TIMEOUT_READ)

        self.limiter.wait()

        try:
            response = self.session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            self.limiter.relax()
            return response
        except Exception as exc:

            status = getattr(getattr(exc, "response", None), "status_code", None)

            # Codal answered, and what it said was "too fast". Widening the
            # gap is the whole remedy; re-probing the network would be
            # nonsense, since the network plainly works.
            if status == 429:
                waited = self.limiter.penalise()
                if log:
                    log.warning(
                        "codal rate-limited (429); request gap now %.1fs", waited
                    )
                raise

            if not is_connection_error(exc):
                raise

            first_error = exc

        host = urlparse(url).hostname or CODAL_HOST

        if not maybe_rebind(host):
            raise first_error

        with self._session_lock:
            if self._own_session:
                # The route really did move, so the bound session is useless
                # and has to be replaced. The firewall cookie is lost with it;
                # that is the price of a genuine route change, and is why this
                # only happens on a connection failure and never on a status.
                self.session = shared_session(rebuild=True)

        self.limiter.wait()

        response = self.session.get(url, params=params, timeout=timeout)
        response.raise_for_status()

        return response

    # ---- search -----------------------------------------------------------

    # Codal's search rejects a partial parameter set, so every flag it expects
    # is sent even where the value is a no-op. Dropping the ones that look
    # irrelevant returns an empty result rather than an error.
    SEARCH_DEFAULTS = {
        "Audited": "true",
        "AuditorRef": "-1",
        "Category": "-1",
        "Childs": "false",
        "CompanyState": "-1",
        "CompanyType": "-1",
        "Consolidatable": "true",
        "IsNotAudited": "false",
        "Length": "-1",
        "LetterType": "-1",
        "Mains": "true",
        "NotAudited": "true",
        "NotConsolidatable": "true",
        "Publisher": "false",
        "TracingNo": "-1",
        "search": "true",
    }

    def search_letters(self, symbol, pages=1):
        """Announcement metadata for one symbol, newest first.

        This is the cheap half of the design -- one 16 KB response tells us
        every filing that exists and, crucially, its TracingNo, so the caller
        can tell a new filing from one already stored without downloading it.

        The symbol goes out **as the exchange spells it**, deliberately not
        through persian.normalize(). That function folds آ to ا, which is
        exactly right for joining our own tables and exactly wrong for an
        outbound query: Codal knows آسیا and آبادا, has never heard of اسیا or
        ابادا, and answers a folded symbol with an empty result rather than an
        error. Sixteen companies came back as "no filings" for that reason
        alone, all of them at the start of the alphabet, which is the only
        reason it was noticeable at all.
        """

        symbol = (symbol or "").strip()

        if not symbol:
            return CodalResult(error="no symbol given", kind=KIND_BAD)

        letters = []
        warnings = []

        for page in range(1, max(1, pages) + 1):

            params = dict(self.SEARCH_DEFAULTS)
            params["Symbol"] = symbol
            params["PageNumber"] = str(page)

            try:
                payload = self._get(config.CODAL_SEARCH_URL, params=params).json()
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:160]}"
                if letters:
                    warnings.append(f"page {page}: {error}")
                    break
                return CodalResult(error=error, kind=classify(exc))

            batch = payload.get("Letters") or []

            if not batch:
                break

            letters.extend(batch)

        if not letters:
            # Codal answered and this symbol has nothing. Not a failure to
            # retry in six minutes -- many listed instruments (funds, rights,
            # newly listed names) genuinely never file.
            return CodalResult(error=f"no filings returned for {symbol}",
                               kind=KIND_EMPTY)

        return CodalResult(
            data=[_letter_row(item, symbol) for item in letters],
            warnings=warnings,
        )

    # ---- statements -------------------------------------------------------

    def fetch_sheets(self, excel_url, archive_key=None):
        """Download one filing and split it into named sheets.

        Returns {sheet name: [DataFrame, ...]}. A sheet can hold more than one
        table -- Codal appends a "دلايل تغييرات" note table to several
        statements -- so the value is a list and the caller takes the widest.
        """

        if not excel_url:
            return CodalResult(error="filing has no Excel URL", kind=KIND_BAD)

        # A filing never changes once published (a correction is a new letter
        # with its own tracing number), so a document already in the archive
        # is read from disk and never downloaded again -- re-parsing after an
        # extractor improvement costs nothing.
        body = archive_read(archive_key) if archive_key else None

        if body is None:
            # Downloaded only from Codal itself (filing_url_refusal). Checked
            # here and not above: a document already in the archive needs no
            # address. Settled, not transient -- the address will not change.
            refusal = filing_url_refusal(excel_url)
            if refusal:
                return CodalResult(error=f"filing URL refused: {refusal}",
                                   kind=KIND_BAD)

            try:
                response = self._get(excel_url, timeout=(
                    settings.TIMEOUT_CONNECT, max(settings.TIMEOUT_READ, 60)
                ))
                body = response.content
            except Exception as exc:
                return CodalResult(
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                    kind=classify(exc))

            if not body:
                return CodalResult(error="filing returned an empty body",
                                   kind=KIND_TRANSIENT)

            if archive_key and _archivable(body):
                archive_write(archive_key, body)

        return self.parse_body(body)

    def parse_body(self, body):
        """Split one downloaded (or archived) filing into named sheets."""

        # The era split. Old filings are genuine OLE2 workbooks and decoding
        # one as UTF-8 yields garbage with no <table> in it -- which the HTML
        # path reads as a firewall page and classifies TRANSIENT, so the old
        # era used to bounce forever without ever being wrong once.
        if body[:8] == XLS_MAGIC:

            try:
                sheets = split_sheets_xls(body)
            except ImportError:
                # An environment defect, not a document defect. Settling the
                # filing for it would make a missing package permanent.
                return CodalResult(
                    error="legacy .xls filing needs the xlrd package "
                          "(pip install xlrd); leaving it pending",
                    kind=KIND_TRANSIENT,
                )
            except Exception as exc:
                return CodalResult(
                    error=f"legacy .xls filing would not open: "
                          f"{type(exc).__name__}: {str(exc)[:120]}",
                    kind=KIND_BAD,
                )

            if not sheets:
                return CodalResult(
                    error=f"legacy .xls filing parsed to no statement "
                          f"sections ({len(body)} bytes)",
                    kind=KIND_BAD,
                )

            return CodalResult(data=sheets)

        text = body.decode("utf-8", errors="replace")

        sheets = split_sheets(text)

        if not sheets:

            # Codal's own empty export: the statement headings with no table
            # under any of them. Found 2026-11-05 at the head of the backfill
            # queue, where it had been read as a ~3 KB throttle page since
            # September -- the same five filings failed every morning, the
            # campaign backed off after them, and nothing behind them was ever
            # fetched. Not a throttle: the same filings came back empty on
            # three requests that day while neighbours fetched in between
            # returned full statements. A firewall page carries no statement
            # headings, so this cannot swallow one.
            if "<table" not in text.lower() and EMPTY_EXPORT_HEADING.search(text):
                return CodalResult(
                    error=(f"empty statement export: headings but no tables "
                           f"({len(body)} bytes)"),
                    kind=KIND_BAD,
                )

            # A body far too small to be a statement did not fail to parse --
            # it is not the statement. Under throttling Codal answers with a
            # short HTML page, and calling that KIND_BAD makes the caller mark
            # the filing parsed, so a document that was merely rate-limited is
            # discarded permanently and never fetched again.
            #
            # Measured on this database: genuine statements run 177 KB to
            # 434 KB, while the observed throttle reply was 3,035 bytes -- a
            # 58x gap. The guard sits between them with room on both sides.
            if len(body) < MIN_FILING_BYTES:
                # The byte count stays first in both texts: codal_monthly
                # reads it ("returned only N bytes").
                empty = exporter_empty(text)
                if empty:
                    return CodalResult(
                        error=(f"filing returned only {len(body)} bytes — "
                               f"{EXPORTER_EMPTY} {empty}, not a block page; "
                               f"left pending until it repeats"),
                        kind=KIND_TRANSIENT,
                    )
                return CodalResult(
                    error=(f"filing returned only {len(body)} bytes — too "
                           f"small to be a statement, treating as throttled"),
                    kind=KIND_TRANSIENT,
                )

            # SIZE ALONE IS NOT ENOUGH, and assuming it was cost 600-odd
            # filings. The firewall also serves LARGE interstitial pages --
            # 76 KB and 201 KB were both observed -- and those sailed past the
            # size guard, were called KIND_BAD, and so were marked parsed and
            # never fetched again. A random re-check of eight such filings
            # found six of them parsed perfectly on the next attempt.
            #
            # The distinguishing property is not length but content: a
            # statement document is a table dump and always contains <table>.
            # A block page, a login redirect or an error page does not. So a
            # bodyless-of-tables response is treated as transient -- worth
            # another attempt -- while a document that HAS tables and still
            # yields no sheets is genuinely unreadable and is settled.
            if "<table" not in text.lower():
                return CodalResult(
                    error=(f"response carried no table at all ({len(body)} "
                           f"bytes) — not a statement document, treating as "
                           f"throttled rather than unreadable"),
                    kind=KIND_TRANSIENT,
                )

            return CodalResult(
                error=f"filing parsed to zero sheets ({len(body)} bytes)",
                kind=KIND_BAD,
            )

        return CodalResult(data=sheets)


def is_letter(letter, *codes):
    """True when a filing carries one of `codes`.

    Both sides are normalised before comparing, and that is not decoration:
    persian.normalize() folds Persian digits to ASCII, so the letter code
    stored from a search hit is "ن-10" while the same code written by hand --
    or copied off codal.ir, which displays Persian digits -- is "ن-۱۰". They
    are different strings. Comparing them raw returns no filings at all, with
    no error anywhere, which is exactly the empty-join failure persian.py
    exists to prevent.
    """

    actual = persian.normalize(letter.get("letter_code") or "")

    return any(persian.normalize(code) == actual for code in codes)


def _letter_row(item, symbol):
    """One search hit, reduced to the fields worth storing."""

    def text(key):
        value = item.get(key)
        return persian.normalize(value) if isinstance(value, str) else value

    return {
        "tracing_no": str(item.get("TracingNo") or "").strip(),
        "symbol": symbol,
        "company_name": text("CompanyName"),
        "title": text("Title"),
        "letter_code": text("LetterCode"),
        # Codal stamps these in the Jalali calendar with Persian digits.
        # normalize() converts the digits; the calendar is left alone and the
        # value is stored as published, because converting it here would make
        # the stored row disagree with what codal.ir shows.
        "published_at": text("PublishDateTime"),
        "sent_at": text("SentDateTime"),
        "url": item.get("Url") or "",
        "excel_url": item.get("ExcelUrl") or "",
        "has_excel": bool(item.get("HasExcel")),
        "attachment_url": item.get("AttachmentUrl") or "",
    }


# ==============================
# SHEET SPLITTING
# ==============================

_HEADING = re.compile(r"(<h\d[^>]*>.*?</h\d>)", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def split_sheets(text):
    """Split a filing's HTML into {heading: [tables]} in document order.

    Keyed on the headings rather than on table position because the table
    order is a property of the company's filing template, not of Codal: a
    bank, an investment company and a refiner all return their balance sheet
    at a different index, and all three label it صورت وضعیت مالی.
    """

    if not text:
        return {}

    sheets = {}
    current = None

    for part in _HEADING.split(text):

        if not part:
            continue

        if part[:2].lower() == "<h" and _HEADING.match(part):
            title = _TAGS.sub("", part).strip()
            current = persian.normalize(title) or None
            continue

        if current is None or "<table" not in part.lower():
            continue

        try:
            frames = pd.read_html(io.StringIO(part))
        except Exception:
            # A malformed table costs its own sheet, never the whole filing.
            continue

        if frames:
            sheets.setdefault(current, []).extend(frames)

    return sheets


# ==============================
# LEGACY .XLS FILINGS (the pre-HTML era)
# ==============================

# The OLE2 compound-document signature. Every genuine legacy .xls starts with
# these eight bytes; no HTML response can, so this is the era test.
XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ==============================
# THE RAW ARCHIVE
# ==============================
#
# Every statement document as Codal served it, gzipped, one file per tracing
# number. Without it, every improvement to the extractor meant downloading
# ~20,000 filings again through a firewall that blocks for hours. Lives
# beside the databases, not inside them: ~5 GB raw, ~0.5 GB compressed, and
# a file that is written once and read rarely does not belong in SQLite.

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "codal_raw")


def _archive_path(key):
    key = re.sub(r"[^0-9A-Za-z_-]", "_", str(key))
    return os.path.join(ARCHIVE_DIR, key[-2:].rjust(2, "0"), key + ".gz")


def _archivable(body):
    """Only real documents: a legacy workbook or HTML that carries a table.

    A throttle page or Codal's empty export is not kept, so a later attempt
    still goes to the network instead of re-reading the failure from disk.
    """

    if body[:8] == XLS_MAGIC:
        return True

    return b"<table" in body.lower()


def archive_read(key):
    """The archived document for one tracing number, or None."""

    try:
        with gzip.open(_archive_path(key), "rb") as handle:
            return handle.read() or None
    except (OSError, EOFError):
        return None


def archive_write(key, body):
    """Store one document; atomic, so a crash never leaves half a file."""

    path = _archive_path(key)

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = path + ".tmp"
        with gzip.open(temp, "wb", compresslevel=6) as handle:
            handle.write(body)
        os.replace(temp, path)
    except OSError:
        pass                 # an archive failure must never fail the fetch


def archive_has(key):
    return os.path.exists(_archive_path(key))

# Section boundaries inside the flat grid, matched on normalised text with
# every space removed -- the old era writes "صورت سود وزیان" where the new one
# writes "صورت سود و زیان", and a space-sensitive match reads that as two
# different statements. Each pattern maps to the CANONICAL sheet name the
# extractor already looks for (financials.INCOME_SHEETS et al.), or to None
# for sections that exist only so their rows stop polluting a statement.
_XLS_SECTIONS = (
    ("صورتسودوزیان", "صورت سود و زیان"),
    ("ترازنامه", "صورت وضعیت مالی"),
    ("صورتوضعیتمالی", "صورت وضعیت مالی"),
    ("صورتجریان", "صورت جریان های نقدی"),
    ("گردشحساب", None),
    ("آمارتولید", None),
    ("گزارشفعالیت", None),
    ("سایراطلاعات", None),
    ("دلایلتغییر", None),
)

# A heading is a short cell. The filing's own title row ("اطلاعات و صورتهای
# مالی میاندوره ای ...") and the advisory sentences both contain statement
# words, and length is what separates a heading from prose that mentions one.
_XLS_HEADING_MAX_CHARS = 40

# Rows on one side of the balance sheet that identify a headless leading
# section as the balance sheet. The old grid often opens straight into the
# balance sheet with no heading row at all.
_XLS_BALANCE_MARKERS = ("جمعبدهیهاوحقوقصاحبانسهام", "جمعداراییها",
                        "داراییهایجاری")


def _xls_squash(value):
    """Normalised text with every space removed, or '' for a non-string."""

    if value is None or isinstance(value, (int, float)):
        return ""

    text = persian.normalize(str(value)).strip()

    if not text or text.lower() == "nan":
        return ""

    return re.sub(r"\s+", "", text)


def _xls_boundary(row):
    """(raw heading, canonical name) when this row starts a section."""

    for cell in row:

        squashed = _xls_squash(cell)

        if not squashed or len(squashed) > _XLS_HEADING_MAX_CHARS:
            continue

        for marker, canonical in _XLS_SECTIONS:
            if marker in squashed:
                # The comprehensive-income statement begins with the same
                # words as the income statement. It gets a section of its own
                # so its rows leave, but never the income statement's
                # canonical name -- pick_sheet's جامع exclusion then drops it
                # by the same rule the HTML path relies on.
                if canonical and "جامع" in squashed:
                    canonical = None
                return persian.normalize(str(cell)).strip(), canonical

    return None, None


def _xls_rows(section):
    """One section's grid rows -> [[label, current, comparative, ...], ...].

    The old grid is laid out right to left: each label owns the numeric cells
    to its LEFT, nearest first -- so reading a row left to right and flushing
    the accumulated numbers at each string cell, reversed, yields values in
    exactly the order `flatten` promises its callers: value[0] the reported
    period, value[1] the comparative. The balance sheet interleaves two
    statements on one row (liabilities mid-row, assets at the right edge) and
    this walk emits both labels with their own values.
    """

    out = []

    for row in section:

        buffer = []

        for cell in row:

            number = parse_number(cell)

            if number is not None:
                buffer.append(number)
                continue

            label = _xls_squash(cell) and persian.normalize(str(cell)).strip()

            if label:
                out.append([label] + list(reversed(buffer)))
                buffer = []

        # Numbers with no label to their right belong to nothing.

    return out


def split_sheets_xls(body):
    """A legacy OLE2 filing -> {sheet name: [DataFrame]}, same contract as
    `split_sheets`.

    Sheet names carry the canonical statement name so `pick_sheet` finds them
    with the constants it already has; the raw heading is kept alongside so a
    تلفیقی marker survives for the consolidated preference.
    """

    import xlrd  # noqa: F401  -- the engine read_excel resolves lazily

    book = pd.ExcelFile(io.BytesIO(body), engine="xlrd")

    sheets = {}

    for sheet_name in book.sheet_names:

        grid = book.parse(sheet_name, header=None)

        if grid.empty:
            continue

        rows = grid.values.tolist()

        # ---- split at headings ------------------------------------------
        sections = []
        current_key = None
        current_rows = []

        for row in rows:

            raw, canonical = _xls_boundary(row)

            if raw is not None:
                sections.append((current_key, current_rows))
                current_key = (raw, canonical)
                current_rows = []
                continue

            current_rows.append(row)

        sections.append((current_key, current_rows))

        for key, section_rows in sections:

            if not section_rows:
                continue

            if key is None:
                # The grid opens with no heading. When what follows is
                # recognisably the balance sheet, name it; anything else
                # headless is preamble and carries no statement.
                squashed = {_xls_squash(cell)
                            for row in section_rows for cell in row}
                if not any(marker in text for text in squashed
                           for marker in _XLS_BALANCE_MARKERS):
                    continue
                raw, canonical = "ترازنامه (بدون عنوان)", "صورت وضعیت مالی"
            else:
                raw, canonical = key

            parsed = _xls_rows(section_rows)

            if not parsed:
                continue

            width = max(len(row) for row in parsed)

            frame = pd.DataFrame(
                [row + [None] * (width - len(row)) for row in parsed])

            name = raw if not canonical or canonical in raw else (
                "%s — %s" % (raw, canonical))

            sheets.setdefault(name, []).append(frame)

    return sheets


def widest(frames):
    """The table a sheet is actually about: the one with the most rows.

    Several statements carry a small "دلايل تغييرات اطلاعات" note table after
    the statement proper. Taking the first table would sometimes return that
    note; taking the largest returns the statement.
    """

    if not frames:
        return None

    return max(frames, key=lambda f: (f.shape[0] * max(f.shape[1], 1)))


# ==============================
# ROW LOOKUP
# ==============================

def flatten(frame):
    """Statement table -> [(label, [values...])], numbers already parsed.

    Codal sheets carry a two-level header and a variable number of value
    columns (a balance sheet has three dates, an income statement two plus a
    percent-change column). Rather than assume a layout, this keeps the label
    and every numeric cell on the row, letting the caller take value[0] as the
    current period and value[1] as the comparative.
    """

    if frame is None or frame.empty:
        return []

    rows = []

    for _, row in frame.iterrows():

        cells = list(row.values)

        if not cells:
            continue

        label = persian.normalize(str(cells[0])) if cells[0] is not None else ""

        if not label or label.lower() == "nan":
            continue

        values = []

        for cell in cells[1:]:
            number = parse_number(cell)
            if number is not None:
                values.append(number)

        rows.append((label, values))

    return rows


def find_row(rows, aliases, exclude=()):
    """First row whose label matches one of `aliases`.

    Exact matches are taken across all aliases before any substring match is
    considered, and that ordering is load-bearing rather than tidiness. A
    balance sheet carries both "جمع دارایی‌ها" and "جمع دارایی‌های جاری", and
    the first is a substring of the second -- so a purely substring-based
    lookup for total assets returns current assets, a smaller number that is
    still perfectly plausible. Nothing downstream could detect that; the ratios
    would simply be wrong.

    Within each pass `aliases` is tried in order, so a caller puts the exact
    wording first and looser fallbacks after it. `exclude` drops rows that
    would otherwise match -- "سود(زیان) خالص" appears both as the bottom line
    and inside "سود(زیان) خالص عملیات متوقف شده".
    """

    wanted = [match_key(a) for a in aliases]
    banned = [match_key(x) for x in exclude]

    prepared = [(match_key(label), label, values) for label, values in rows]

    def usable(key, values):
        return bool(values) and not any(bad in key for bad in banned)

    for alias in wanted:
        for key, label, values in prepared:
            if key == alias and usable(key, values):
                return label, values

    for alias in wanted:
        for key, label, values in prepared:
            if alias in key and usable(key, values):
                return label, values

    return None, []


def match_key(text):
    """Normalise a label to the form labels are compared in: no spaces.

    Spacing around parentheses in Persian statement labels is not consistent,
    even within one filing. A bank writes the bottom line "سود(زیان)خالص", a
    manufacturer writes "سود(زیان) خالص", and an investment company writes
    "سود (زیان) خالص". Those are three different strings and neither equality
    nor substring matching relates them, so an alias written with one spelling
    silently finds nothing in the other two -- which is exactly how the bank
    template came back with no revenue and no profit while every other line
    parsed. Dropping spaces entirely makes the comparison about the words.
    """

    return persian.normalize(text).replace(" ", "")


def value_of(rows, aliases, exclude=(), index=0):
    """The `index`-th numeric cell of the first matching row, or None."""

    _label, values = find_row(rows, aliases, exclude=exclude)

    if len(values) > index:
        return values[index]

    return None
