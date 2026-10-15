"""Gold funds (صندوق‌های طلا) -- identification, data acquisition, and listing.

WHY A SEPARATE MODULE
---------------------
Gold ETFs are not equities. They track the price of gold and coins, not a
company's fundamentals, so the equity machinery deliberately excludes them:
`load_instruments(equities_only=True)` gates on `is_equity = 1`, and every
research/screen path uses that gate. Running the fundamental screen or the
six-factor model on a gold fund would be meaningless -- there is no balance
sheet to score.

What DOES apply to them is technical analysis, because that reads a price
series and a gold fund has one. So this module identifies the gold funds,
fetches their price history (the equity collector never does -- 0 of 164 funds
have bars), and hands the technical layer a clean list. The dashboard shows
them in their own section, apart from the equity views, because mixing an ETF
that tracks bullion into a table of stocks ranked on fundamentals would be a
category error dressed as a feature.

IDENTIFICATION
--------------
A fund is a gold fund when kind == 'fund' and its Persian name contains طلا
(gold) or سکه (coin). Verified against the universe: this catches all 14 gold
funds and excludes the two equities (حشکوه, مفاخر) that merely have طلا in
their names. Keyword matching is used rather than a hand-maintained code list
because a new gold fund lists under the same naming convention and should
appear without a code change.

"""

import database
import settings

# The words that name a gold fund. سکه (coin) is included because several funds
# are backed by coins rather than bars and carry سکه, not طلا, as the operative
# word (e.g. کهربا -- پشتوانه سکه طلا).
GOLD_WORDS = ("طلا", "سکه")


def is_gold_fund(instrument):
    """True when this instrument row is a gold ETF.

    Takes the dict shape load_instruments returns. The kind check is what keeps
    an equity with طلا in its name out; the keyword check is what keeps a
    non-gold fund out.
    """

    if (instrument.get("kind") or "") != "fund":
        return False

    name = (instrument.get("name_fa") or "")

    return any(word in name for word in GOLD_WORDS)


def gold_funds():
    """Every gold fund in the universe, as instrument dicts, symbol-sorted."""

    funds = [row for row in database.load_instruments(equities_only=False)
             if is_gold_fund(row)]

    funds.sort(key=lambda r: (r.get("symbol_fa") or ""))

    return funds


def gold_fund_codes():
    """Just the ins_codes -- what the history fetcher wants."""

    return [row["ins_code"] for row in gold_funds()]


def fetch_history(count=300, verbose=True, progress=None):
    """Fetch and store daily bars for every gold fund.

    The equity history collector never touches funds, so this is the only path
    that gives them a price_history. Reuses seed_import.import_history with an
    explicit ins_code list -- no new fetch logic, no change to the equity path.
    """

    import seed_import

    codes = gold_fund_codes()

    if not codes:
        if verbose:
            print("No gold funds in the universe. Run the universe import "
                  "first.")
        return 0

    if verbose:
        print("Fetching history for %d gold funds ..." % len(codes))

    return seed_import.import_history(
        ins_codes=codes, count=count, verbose=verbose, progress=progress)


def coverage():
    """How many gold funds currently have stored price history.

    Lets the dashboard say "10 of 14 have data, press fetch" rather than
    silently showing a short list.
    """

    funds = gold_funds()

    with_history = 0
    for row in funds:
        frame = database.load_history_by_inscode(row["ins_code"], limit=1)
        if frame is not None and not frame.empty:
            with_history += 1

    return {"total": len(funds), "with_history": with_history}


# ==============================
# NAV: WHAT THE UNITS ARE ACTUALLY WORTH
# ==============================
#
# A gold ETF has something an equity does not: a published net asset value.
# The fund holds bullion and coins, the manager values that holding daily, and
# TSETMC serves the per-unit figure. That makes the one comparison that matters
# for these instruments possible -- price against NAV, the حباب. A fund trading
# 3% above the metal it holds is a different proposition from the same fund
# trading 1% below it, and no amount of RSI says so.
#
# TWO ENDPOINTS, BECAUSE ONE DOES NOT CARRY BOTH HALVES
# -----------------------------------------------------
# `Fund/GetETFByInsCode` returns the NAV pair and nothing usable for price
# (its `iClose` came back 0 for every fund probed). `ClosingPrice/
# GetClosingPriceInfo` returns the price but reports `pRedTran` as 0.0 -- the
# same field name as the real NAV, carrying a zero. Reading NAV from the
# closing-price payload would therefore produce a plausible-looking حباب of
# +inf for every fund, silently. Each number is taken from the endpoint that
# actually populates it, and never from the one that merely names it.
#
# WHICH NAV, AND WHICH PRICE
# ---------------------------
# `pRedTran` is the ابطال (redemption) NAV and `pSubTran` the صدور (issuance)
# NAV; they differ by the fund's fee, ~0.4%. The حباب convention here is price
# over ابطال, because ابطال is what a holder gets out. The price is the
# session's پایانی, taken from the same pull as the NAV so the two sides of the
# ratio come from one moment -- a stored close from a bar that is a day older
# than the NAV would print a premium the market never quoted.

ETF_URL = "https://cdn.tsetmc.com/api/Fund/GetETFByInsCode/%s"

PRICE_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceInfo/%s"

# Above this the units cost meaningfully more than the metal behind them, below
# it meaningfully less. Not a signal -- a band that keeps the panel from
# dressing up rounding noise as a premium.
BUBBLE_BAND = 0.02


def _why(exc):
    """A short reason string. The exception type alone is not enough.

    "JSONDecodeError" is what a rate-limited TSETMC looks like from here -- it
    answers with an HTML error page, not JSON -- and that reads nothing like
    "the host refused the connection", which is the other way this fails. The
    two need different responses from whoever is reading the panel, so the
    panel is told which one happened.
    """

    detail = str(exc).strip().splitlines()
    detail = detail[0][:90] if detail else ""

    if not detail:
        return type(exc).__name__

    return "%s: %s" % (type(exc).__name__, detail)


def _number(value):
    """Tolerant numeric read, with zero treated as absent.

    Zero is not a price and not a NAV; it is how these endpoints spell "this
    field is not populated here" (see the pRedTran note above). Letting a zero
    through would divide a real price by nothing.
    """

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result != result or result == 0.0:
        return None

    return result


def _fund_nav(session, ins_code):
    """The NAV pair and the day's price for one fund, as far as each is had."""

    row = {"nav_redeem": None, "nav_issue": None, "close": None,
           "last": None, "prev_close": None, "date": None, "error": None}

    timeout = (settings.TIMEOUT_CONNECT, settings.TIMEOUT_READ)

    try:
        etf = (session.get(ETF_URL % ins_code, timeout=timeout)
               .json().get("etf") or {})
        row["nav_redeem"] = _number(etf.get("pRedTran"))
        row["nav_issue"] = _number(etf.get("pSubTran"))
    except Exception as exc:                                    # noqa: BLE001
        row["error"] = "NAV: %s" % _why(exc)

    try:
        info = (session.get(PRICE_URL % ins_code, timeout=timeout)
                .json().get("closingPriceInfo") or {})
        row["close"] = _number(info.get("pClosing"))
        row["last"] = _number(info.get("pDrCotVal"))
        row["prev_close"] = _number(info.get("priceYesterday"))
        row["date"] = str(info.get("dEven") or "") or None
    except Exception as exc:                                    # noqa: BLE001
        row["error"] = ((row["error"] + "; ") if row["error"] else "") + \
            "price: %s" % _why(exc)

    return row


def _rate(price, nav):
    """price / NAV, or None when either side is missing."""

    if price is None or nav is None:
        return None

    return price / nav


def nav_snapshot(codes=None):
    """Live NAV and day price for the gold funds, keyed by ins_code.

    Network-facing and deliberately non-fatal: TSETMC is unreachable from some
    networks on this machine, and the gold panel worked off stored bars long
    before it had a NAV column. A failure here leaves every fund's NAV None and
    the panel says so -- it does not take the technical read down with it.
    """

    from providers import transport

    if codes is None:
        codes = gold_fund_codes()

    codes = list(codes)

    if not codes:
        return {}

    try:
        session = transport.build_session(host="cdn.tsetmc.com")
    except Exception as exc:                                    # noqa: BLE001
        return {code: {"error": "session: %s" % _why(exc)} for code in codes}

    limiter = transport.RateLimiter(settings.RATE_LIMIT_DELAY)

    results = transport.map_concurrent(
        lambda code: _fund_nav(session, code), codes, limiter=limiter)

    snapshot = {}

    for code, result in results.items():
        if isinstance(result, Exception):
            snapshot[code] = {"error": _why(result)}
            continue

        result["price_to_nav"] = _rate(result["close"], result["nav_redeem"])
        result["bubble"] = (
            None if result["price_to_nav"] is None
            else result["price_to_nav"] - 1.0)

        snapshot[code] = result

    return snapshot
