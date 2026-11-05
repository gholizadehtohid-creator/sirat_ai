"""Opportunities that need no forecast: fund discounts and treasury-bill yields.

WHY THIS EXISTS
---------------
On 2026-11-05 every stock-selection method in this repository had failed its
test (Alpha Core E19/E20, the v2 basket, E26), while the two experiments that
passed were about WHAT TO HOLD (E27, E28). The user trades by hand. This
module finds the places where a manual trader can do measurably better
WITHOUT predicting a price:

  1. Fund discount to NAV. Two funds holding the same gold or the same index
     are the same exposure; the one trading further below its ابطال NAV is
     the same holding at a lower price. When the allocation says "buy gold",
     this says which gold fund is cheapest against what it holds.

  2. Treasury bills (اخزا). Zero-coupon government paper that pays 1,000,000
     rials at a dated maturity. Bought at price P and held to maturity, the
     return is fixed the moment it is bought: (1e6 / P) ** (365 / days) - 1.
     When that beats the fixed-income fund the allocation assumes (20%), the
     fixed-income part is better held as bills.

WHAT IT DOES NOT CLAIM
----------------------
A discount is not a forecast that it will close: some equity ETFs sit below
NAV for months. It is only a cheaper price for the same holding today. A bill
yield is only realised held to maturity; sold early, the price can be lower.
The bill price is the last close, which for thinly traded series may be days
old -- `traded_value` is reported so the user can see that. Commission is not
deducted: bills and funds carry far smaller fees than the 1.039% equity round
trip, and the broker's actual fee is not known here.
"""

import re
from datetime import date

import config
import database
import jalali

FACE_VALUE = 1_000_000                 # rials, every اخزا series
FUND_RATE = 0.20                       # the allocation's fixed-income assumption
MIN_DAYS = 7                           # a bill closer than this is noise
MIN_FUND_VALUE = 1e9                   # rials traded in the session
DISCOUNT_BAND = 0.02                   # gold.BUBBLE_BAND, the same convention

# Beyond this the gap is more likely a unit mismatch between TSETMC's NAV and
# price fields than a real price: دارایکم and پالایش read -39% and -32% on
# 2026-11-05, which no fund with a market maker sustains. Shown, flagged, and
# never named as the cheapest.
SUSPECT_GAP = 0.15

SEARCH_URL = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/%s"
PRICE_URL = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceInfo/%s"

# The search caps at 40 hits, and there are more than 40 bill series, so the
# prefix is extended by the series' first digit.
BILL_QUERIES = ["اخزا"] + ["اخزا%d" % d for d in range(10)]

# "اسنادخزانه-م2بودجه02-050923": the trailing six digits are the Jalali
# maturity, YYMMDD in the 1400s.
MATURITY = re.compile(r"(\d{2})(\d{2})(\d{2})\s*$")


# ==============================
# CLASSIFICATION
# ==============================

def fund_sleeve(name):
    """Which allocation sleeve a fund belongs to, from its name; or None."""

    import gold

    name = name or ""

    if gold.is_gold_fund({"kind": "fund", "name_fa": name}):
        return "gold"
    if "اهرمی" in name:
        # Leveraged funds have two unit classes; the traded unit's NAV is not
        # the fund's, so a "discount" on it means something else entirely.
        return None
    if "ثابت" in name or name.strip().endswith("-د"):
        return "fixed"
    if "شاخص" in name:
        return "equity"
    if name.strip().endswith("-س") or "سهام" in name:
        return "equity_active"
    return None


def maturity_of(name):
    """The bill's maturity as a Gregorian date, from its long name."""

    match = MATURITY.search((name or "").strip())

    if not match:
        return None

    yy, mm, dd = (int(g) for g in match.groups())

    try:
        return date(*jalali.to_gregorian(1400 + yy, mm, dd))
    except ValueError:
        return None


def maturity_jalali(name):
    """The same maturity as written in the name, as 14YY/MM/DD."""

    match = MATURITY.search((name or "").strip())

    if not match:
        return None

    yy, mm, dd = match.groups()

    return "14%s/%s/%s" % (yy, mm, dd)


def bill_yield(price, maturity, today):
    """Annual yield to maturity of a zero-coupon bill, or None."""

    if not price or not maturity:
        return None

    days = (maturity - today).days

    if days < MIN_DAYS or price <= 0 or price >= FACE_VALUE * 1.5:
        return None

    return (FACE_VALUE / float(price)) ** (365.0 / days) - 1.0


# ==============================
# NETWORK
# ==============================

def _session():
    from providers import transport
    return transport.build_session(host="cdn.tsetmc.com")


def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out != 0.0 else None


def discover_bills(session):
    """Every active اخزا series TSETMC's search returns: {ins_code: info}."""

    import settings

    found = {}
    timeout = (settings.TIMEOUT_CONNECT, settings.TIMEOUT_READ)

    for query in BILL_QUERIES:
        try:
            hits = (session.get(SEARCH_URL % query, timeout=timeout).json()
                    .get("instrumentSearch") or [])
        except Exception:                                       # noqa: BLE001
            continue

        for hit in hits:
            symbol = (hit.get("lVal18AFC") or "").strip()
            name = (hit.get("lVal30") or "").strip()
            if "اخزا" not in symbol or hit.get("lastDate") != 1:
                continue
            found[str(hit.get("insCode"))] = {"symbol": symbol, "name": name}

    return found


def _price(session, ins_code):
    import settings

    info = (session.get(PRICE_URL % ins_code,
                        timeout=(settings.TIMEOUT_CONNECT, settings.TIMEOUT_READ))
            .json().get("closingPriceInfo") or {})

    return {
        "close": _number(info.get("pClosing")),
        "last": _number(info.get("pDrCotVal")),
        "date": str(info.get("dEven") or "") or None,
        "traded_value": _number(info.get("qTotCap")) or 0.0,
    }


def treasury_bills(today=None):
    """Yield to maturity for every live bill, best first."""

    from providers import transport
    import settings

    today = today or date.today()

    try:
        session = _session()
    except Exception as exc:                                    # noqa: BLE001
        return {"bills": [], "error": "session: %s" % exc}

    bills = discover_bills(session)

    if not bills:
        return {"bills": [], "error": "no bill series found (TSETMC search)"}

    limiter = transport.RateLimiter(settings.RATE_LIMIT_DELAY)
    prices = transport.map_concurrent(lambda c: _price(session, c), bills,
                                      limiter=limiter)

    rows = []

    for code, info in bills.items():
        quote = prices.get(code)
        if isinstance(quote, Exception) or not quote:
            continue

        maturity = maturity_of(info["name"])
        price = quote["close"] or quote["last"]
        ytm = bill_yield(price, maturity, today)

        if ytm is None:
            continue

        rows.append({
            "symbol": info["symbol"],
            "name": info["name"],
            "maturity": maturity.isoformat(),
            "maturity_jalali": maturity_jalali(info["name"]),
            "days": (maturity - today).days,
            "price": price,
            "price_date": quote["date"],
            "traded_value": quote["traded_value"],
            "ytm_pct": round(ytm * 100, 2),
            "beats_fund": ytm > FUND_RATE,
        })

    rows.sort(key=lambda r: -r["ytm_pct"])

    return {"bills": rows, "fund_rate_pct": FUND_RATE * 100, "error": None}


def fund_discounts(per_sleeve=12):
    """Price against ابطال NAV for each sleeve's most traded funds.

    Capped per sleeve, not overall: fixed-income funds trade so heavily that
    an overall top-60 left no index fund in it at all.
    """

    import gold

    conn = database._connect()
    rows = conn.execute("""
        SELECT i.ins_code, i.symbol_fa, i.name_fa, COALESCE(s.value, 0)
        FROM instruments i
        JOIN market_snapshot s ON s.ins_code = i.ins_code
        WHERE i.kind = 'fund' AND COALESCE(s.value, 0) >= ?
        ORDER BY s.value DESC
    """, (MIN_FUND_VALUE,)).fetchall()
    conn.close()

    funds = []
    taken = {}
    for code, symbol, name, value in rows:
        sleeve = fund_sleeve(name)
        if sleeve and taken.get(sleeve, 0) < per_sleeve:
            taken[sleeve] = taken.get(sleeve, 0) + 1
            funds.append({"ins_code": str(code), "symbol": symbol,
                          "name": name, "sleeve": sleeve,
                          "traded_value": float(value or 0)})

    navs = gold.nav_snapshot([f["ins_code"] for f in funds])

    by_sleeve = {}

    for fund in funds:
        nav = navs.get(fund["ins_code"]) or {}
        bubble = nav.get("bubble")
        if bubble is None:
            continue
        fund.update(nav_redeem=nav.get("nav_redeem"), close=nav.get("close"),
                    price_date=nav.get("date"),
                    discount_pct=round(bubble * 100, 2),
                    suspect=abs(bubble) > SUSPECT_GAP,
                    notable=-SUSPECT_GAP <= bubble <= -DISCOUNT_BAND)
        by_sleeve.setdefault(fund["sleeve"], []).append(fund)

    for items in by_sleeve.values():
        items.sort(key=lambda f: f["discount_pct"])

    return by_sleeve


def report(today=None):
    """What the panel renders."""

    bills = treasury_bills(today)
    discounts = fund_discounts()

    best_bill = bills["bills"][0] if bills["bills"] else None

    summary = []

    if best_bill and best_bill["beats_fund"]:
        summary.append(
            "اوراق خزانه %s تا سررسید %s سالانه %.1f٪ بازده قطعی دارد؛ بیشتر از "
            "فرض ۲۰٪ صندوق درآمد ثابت. بخش درآمد ثابت را می‌توان با اخزا پر کرد."
            % (best_bill["symbol"], best_bill["maturity_jalali"],
               best_bill["ytm_pct"]))

    labels = {"gold": "طلا", "equity": "شاخصی", "fixed": "درآمد ثابت",
              "equity_active": "سهامی"}

    for sleeve in ("gold", "equity"):
        items = [f for f in discounts.get(sleeve) or [] if not f["suspect"]]
        if items:
            cheapest = items[0]
            summary.append(
                "ارزان‌ترین صندوق %s نسبت به NAV: %s با %+.1f٪."
                % (labels[sleeve], cheapest["symbol"], cheapest["discount_pct"]))

    return {
        "bills": bills,
        "funds": discounts,
        "sleeve_labels": labels,
        "summary": summary,
        "notes": [
            "هیچ‌کدام پیش‌بینی قیمت نیست. تخفیف به NAV یعنی همان دارایی را امروز "
            "ارزان‌تر می‌خرید؛ تضمین نمی‌کند تخفیف بسته شود.",
            "بازده اخزا فقط در صورت نگه‌داشتن تا سررسید قطعی است؛ فروش زودتر "
            "به قیمت روز است.",
            "قیمت اخزا آخرین قیمت پایانی است؛ برای سری‌های کم‌معامله ممکن است "
            "قدیمی باشد (ستون ارزش معامله).",
            "کارمزد کارگزار کسر نشده است.",
        ],
    }
