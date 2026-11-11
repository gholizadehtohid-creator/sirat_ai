"""Codal filings -> canonical line items -> fundamental ratios.

codal.py knows how to fetch a filing and split it into sheets. This module
knows what the sheets *mean*: which Persian row label is revenue, that amounts
are in million rials while per-share figures are in rials, and how to turn two
periods of that into growth, margins and returns.

Everything is alias-driven and defensive by construction. A line item that is
not in a filing comes back None and stays None -- there is no substitution, no
zero-fill and no derivation from a plausible-looking neighbour. Iranian filing
templates differ by industry (a bank has no gross profit, an investment company
has no cost of sales), so absent items are the normal case rather than an
error, and every ratio reports whether its inputs were actually present.

The one derivation that IS done: when a statement omits a subtotal but carries
its parts, the subtotal is computed and marked as derived. Total liabilities is
the common case -- some templates print only the current and non-current
halves.
"""

import math
import re

import codal
import config
import persian


# ==============================
# REPORTING PERIOD
# ==============================

# Codal files annual statements and 3/6/9-month interim ones under the same
# letter code, and their figures are period totals -- an interim EPS is that
# quarter's EPS, not a run rate. Valuing a share on it without annualising
# divides a full-year price by a quarter of a year's earnings and reports a
# P/E four times too high. شپنا came back at 18.7 against a real 4.7 before
# this existed, which is wrong but not obviously wrong, so nothing downstream
# would have caught it.
_MONTHS = re.compile(r"دوره\s*(\d+)\s*ماهه")


def period_months(title):
    """Months the filing covers, from its title. None when it cannot be told."""

    text = persian.normalize(title or "")

    if not text:
        return None

    match = _MONTHS.search(text)

    if match:
        months = int(match.group(1))
        return months if 1 <= months <= 12 else None

    # "سال مالی منتهی به" -- a full financial year.
    if "سال مالی" in text:
        return 12

    return None


# "منتهی به ۱۴۰۴/۱۲/۲۹" -- the Jalali date the period closed. Kept as filed
# rather than converted: it is the key a filing is stored under, and a
# converted key would no longer match what codal.ir displays.
_PERIOD_END = re.compile(r"منتهی\s*به\s*(\d{4}/\d{2}/\d{2})")


def period_end(title):
    """Jalali period-end date from a filing title, or None."""

    match = _PERIOD_END.search(persian.normalize(title or ""))

    return match.group(1) if match else None


def period_age_months(period_end, reference):
    """How many months older than `reference` a filing's period is.

    Both are Jalali "YYYY/MM/DD" strings and are compared as such rather than
    converted, because converting introduces a calendar dependency to answer a
    question that is really just arithmetic on year and month.

    `reference` is meant to be the newest period any company has filed, which
    is a better "now" than the wall clock: it moves with the reporting season
    instead of making every company look three months stale in the gap between
    a quarter closing and its statements appearing.
    """

    def parts(value):
        try:
            year, month, _day = str(value).split("/")[:3]
            return int(year), int(month)
        except (ValueError, AttributeError, IndexError):
            return None

    a, b = parts(period_end), parts(reference)

    if a is None or b is None:
        return None

    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def is_audited(title):
    """True/False/None -- audited, not audited, or not stated.

    Kept because an audited annual statement and an unaudited one for the same
    period are different filings that Codal publishes weeks apart, and the
    audited figures supersede the earlier ones.
    """

    text = persian.normalize(title or "")

    if "حسابرسی نشده" in text:
        return False

    if "حسابرسی شده" in text:
        return True

    return None


# A parenthesised name in a statement title: «... (حسابرسی شده) (شرکت فولادکالا
# فوکا)». Since 1405 a parent publishes its subsidiaries' statements under its
# OWN symbol with the subsidiary named like this. Found 2026-11-10: 182 had
# been stored as their parents' latest period -- فولاد's 6-month statement was
# a football club's (capital 10 million rials against 1,935 trillion), and the
# outlook read فولاد's revenue as down 99.9%.
_SUBSIDIARY = re.compile(r"\(\s*((?:شرکت|سهامی خاص)[^()]*)\)")


def subsidiary_of(title):
    """The subsidiary a statement belongs to, or None for the issuer's own."""

    match = _SUBSIDIARY.search(persian.normalize(title or ""))

    return match.group(1).strip() if match else None


def annualise(value, months):
    """Scale a period figure to a yearly rate.

    Applied only to flow items (revenue, profit, EPS, cash flow). Balance
    sheet items are a position at a date and mean the same whatever period
    the statement covers -- annualising equity would be nonsense.
    """

    value = _finite(value)

    if value is None or not months or months <= 0:
        return value

    if months == 12:
        return value

    return value * 12.0 / months


# ==============================
# SHEET SELECTION
# ==============================

# Codal files consolidated (تلفیقی) and standalone versions of each statement
# in the same document. Consolidated is preferred: it is the group the listed
# share actually represents. A company with no subsidiaries files only the
# standalone one, so both spellings are looked for.
INCOME_SHEETS = ("صورت سود و زیان",)
BALANCE_SHEETS = ("صورت وضعیت مالی",)
CASHFLOW_SHEETS = ("صورت جریان های نقدی",)
MONTHLY_SHEETS = ("گزارش فعالیت ماهانه",)

# "صورت سود و زیان جامع" is the statement of comprehensive income -- a
# different statement that begins with the same words. Matching on the prefix
# alone silently reads it instead of the income statement.
EXCLUDE_SHEETS = ("جامع",)


def pick_sheet(sheets, wanted, prefer_consolidated=True):
    """The frame for one statement, preferring the consolidated version."""

    if not sheets:
        return None, None

    matches = []

    for name, frames in sheets.items():

        normalised = persian.normalize(name)

        if any(bad in normalised for bad in EXCLUDE_SHEETS):
            continue

        if not any(persian.normalize(w) in normalised for w in wanted):
            continue

        consolidated = "تلفیقی" in normalised

        matches.append((consolidated, name, codal.widest(frames)))

    if not matches:
        return None, None

    matches.sort(key=lambda m: m[0], reverse=bool(prefer_consolidated))

    _consolidated, name, frame = matches[0]

    return name, frame


# ==============================
# LINE ITEMS
# ==============================
#
# Aliases are written with the ZWNJ spellings Codal uses; codal.find_row folds
# both sides through persian.normalize(), which strips ZWNJ, so "دارایی‌ها" and
# "داراییها" match the same row.

INCOME_ITEMS = {
    # Banks state a singular "درآمد عملیاتی" and investment companies a
    # "جمع درآمدهای عملیاتی"; both are the same line for our purposes. The
    # plural is listed first so a manufacturer's exact row wins outright.
    "revenue": (
        ["درآمدهای عملیاتی", "جمع درآمدهای عملیاتی", "درآمد عملیاتی",
         "فروش خالص", "درآمد حاصل از"],
        [],
    ),
    "cogs": (
        ["بهای تمام شده درآمدهای عملیاتی", "بهای تمام شده کالای فروش رفته",
         "بهای تمام شده"],
        [],
    ),
    "gross_profit": (["سود(زیان) ناخالص", "سود (زیان) ناخالص"], []),
    "operating_profit": (
        ["سود(زیان) عملیاتی", "سود (زیان) عملیاتی"], []
    ),
    "finance_cost": (["هزینه های مالی", "هزینه‏ های مالی"], []),
    "pretax_profit": (
        ["سود(زیان) عملیات در حال تداوم قبل از مالیات",
         "سود (زیان) قبل از مالیات", "سود(زیان) قبل از مالیات"],
        [],
    ),
    # "سود(زیان) خالص" is also the opening line of the comprehensive income
    # statement and the prefix of the discontinued-operations line, so both are
    # excluded rather than trusted to sort themselves out by ordering.
    "net_profit": (
        ["سود(زیان) خالص", "سود (زیان) خالص"],
        ["متوقف شده", "هر سهم", "عملیات در حال تداوم"],
    ),
    "net_profit_owners": (["مالکان شرکت اصلی"], []),
    # "پایه هر سهم" is last, so it is only ever a substring match tried
    # after the other two. The 1404-era template labels basic EPS "سود
    # (زیان) پایه هر سهم", which "سود پایه هر سهم" misses by the "(زیان)",
    # and where that filing's "خالص هر سهم" row is empty it had no EPS at
    # all: 1516177, and 58 of an 80-filing sample of the 202 archived among
    # the 575 stored with a net profit and no EPS (2026-11-11). Any earlier,
    # it would displace the "خالص هر سهم" figure filings parse now (1534498:
    # 3,374 for 3,306).
    "eps": (
        ["سود پایه هر سهم", "سود (زیان) خالص هر سهم", "پایه هر سهم"],
        ["عملیاتی", "غیرعملیاتی", "ناشی از"],
    ),
    "capital": (["سرمایه"], ["افزایش سرمایه", "در جریان"]),
}

BALANCE_ITEMS = {
    "total_assets": (["جمع دارایی‌ها", "جمع دارایی ها"], ["جاری"]),
    "current_assets": (["جمع دارایی‌های جاری"], ["غیرجاری"]),
    "non_current_assets": (["جمع دارایی‌های غیرجاری"], []),
    "total_liabilities": (["جمع بدهی‌ها", "جمع بدهی ها"], ["جاری"]),
    "current_liabilities": (["جمع بدهی‌های جاری"], ["غیرجاری"]),
    "non_current_liabilities": (["جمع بدهی‌های غیرجاری"], []),
    "equity": (["جمع حقوق مالکانه"], ["بدهی"]),
    "equity_owners": (
        ["حقوق مالکانه قابل انتساب به مالکان شرکت اصلی"], []
    ),
    "cash": (["موجودی نقد"], []),
    "inventory": (["موجودی مواد و کالا"], []),
    "capital": (["سرمایه"], ["افزایش سرمایه", "در جریان", "صرف"]),
    "retained_earnings": (["سود (زیان) انباشته", "سود(زیان) انباشته",
                           "سود انباشته"], []),
    # Added 2026-11-05 for the outlook (fundamental_outlook.py): working
    # capital, debt and fixed assets. Current-era wording first, the
    # pre-1398 ترازنامه wording after it.
    "ppe": (["دارایی‌های ثابت مشهود", "داراییهای ثابت مشهود"], []),
    "receivables": (
        ["دریافتنی‌های تجاری و سایر دریافتنی‌ها",
         "حسابها و اسناد دریافتنی تجاری", "دریافتنی‌های تجاری"],
        ["بلندمدت", "بلند مدت", "هزینه"],
    ),
    "payables": (
        ["پرداختنی‌های تجاری و سایر پرداختنی‌ها",
         "حسابها و اسناد پرداختنی تجاری", "پرداختنی‌های تجاری"],
        ["بلندمدت", "بلند مدت"],
    ),
    "borrowings_short": (
        ["تسهیلات مالی", "تسهیلات مالی دریافتی"],
        ["بلندمدت", "بلند مدت"],
    ),
    "borrowings_long": (
        ["تسهیلات مالی بلندمدت", "تسهیلات مالی دریافتی بلندمدت",
         "تسهیلات مالی بلند مدت"],
        [],
    ),
    "dividends_payable": (["سود سهام پرداختنی"], []),
    "advances_received": (["پیش‌دریافت‌ها", "پیش دریافتها"], []),
}

CASHFLOW_ITEMS = {
    "operating_cash_flow": (
        ["جریان خالص ورود (خروج) نقد حاصل از فعالیتهای عملیاتی",
         "جریان خالص ورود (خروج) وجه نقد ناشی از فعالیتهای عملیاتی",
         "خالص جریان ورود (خروج) وجه نقد ناشی از فعالیتهای عملیاتی"],
        [],
    ),
    "cash_from_operations": (["نقد حاصل از عملیات"], []),
    "tax_paid": (["پرداختهای نقدی بابت مالیات"], []),
    "investing_cash_flow": (
        ["جریان خالص ورود (خروج) نقد حاصل از فعالیتهای سرمایه‌گذاری"], []
    ),
    "financing_cash_flow": (
        ["جریان خالص ورود (خروج) نقد حاصل از فعالیتهای تامین مالی"], []
    ),
    # Filed negative (an outflow); kept with its sign.
    "capex": (
        ["پرداخت‌های نقدی برای خرید دارایی‌های ثابت مشهود",
         "وجوه پرداختی بابت خرید دارایی‌های ثابت مشهود",
         "وجوه پرداختی جهت خرید دارایی‌های ثابت مشهود"],
        [],
    ),
    "dividends_paid": (
        ["پرداخت‌های نقدی بابت سود سهام", "سود سهام پرداختی"],
        ["دریافت"],
    ),
}


def read_items(frame, spec, index=0):
    """Pull a statement's canonical items out of one table.

    `index` selects the column: 0 is the period being reported, 1 the
    comparative. Both live on the same row, which is why a filing gives two
    periods of growth for the price of one download.
    """

    rows = codal.flatten(frame)

    out = {}

    for key, (aliases, exclude) in spec.items():
        out[key] = codal.value_of(rows, aliases, exclude=exclude, index=index)

    return out


# ==============================
# EXTRACTION
# ==============================

def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scale(items, keys, factor=config.CODAL_AMOUNT_SCALE):
    """Convert million-rial amounts to rials, leaving per-share figures alone."""

    for key in keys:
        value = _finite(items.get(key))
        items[key] = value * factor if value is not None else None

    return items


# Per-share figures are already in rials on the same sheet; scaling them would
# be a factor-of-a-million error hiding inside a plausible number.
PER_SHARE_KEYS = {"eps"}


def extract(sheets, index=0):
    """One filing's sheets -> canonical line items, in rials.

    `index=0` reads the reported period, `index=1` the comparative period on
    the same sheet. Returns None only when not one of the three statements
    could be located -- a filing with an income statement but no cash flow
    still yields everything the income statement carries.
    """

    if not sheets:
        return None

    income_name, income = pick_sheet(sheets, INCOME_SHEETS)
    balance_name, balance = pick_sheet(sheets, BALANCE_SHEETS)
    cash_name, cash = pick_sheet(sheets, CASHFLOW_SHEETS)

    if income is None and balance is None:
        return None

    items = {}
    present = {}

    if income is not None:
        found = read_items(income, INCOME_ITEMS, index=index)
        _scale(found, [k for k in INCOME_ITEMS if k not in PER_SHARE_KEYS])
        items.update(found)
        present["income"] = income_name

    if balance is not None:
        found = read_items(balance, BALANCE_ITEMS, index=index)
        _scale(found, list(BALANCE_ITEMS))
        # The balance sheet's own capital row wins over the income statement's
        # when both exist -- same figure, but the balance sheet is where it
        # belongs and the income-statement copy is a footnote.
        for key, value in found.items():
            if value is not None or key not in items:
                items[key] = value
        present["balance"] = balance_name

    if cash is not None:
        found = read_items(cash, CASHFLOW_ITEMS, index=index)
        _scale(found, list(CASHFLOW_ITEMS))
        items.update(found)
        present["cashflow"] = cash_name

    items["sheets_used"] = present
    items["consolidated"] = any(
        "تلفیقی" in persian.normalize(n or "") for n in present.values()
    )

    _derive(items)

    return items


def _derive(items):
    """Fill subtotals a template omitted but whose parts it printed.

    Marked in `derived` so a reader can tell a filed number from an arithmetic
    one. Nothing here invents data: each rule is an identity that holds by
    definition of the statement.
    """

    derived = []

    def get(key):
        return _finite(items.get(key))

    if get("total_liabilities") is None:
        current, non_current = get("current_liabilities"), get("non_current_liabilities")
        if current is not None and non_current is not None:
            items["total_liabilities"] = current + non_current
            derived.append("total_liabilities")

    if get("total_assets") is None:
        current, non_current = get("current_assets"), get("non_current_assets")
        if current is not None and non_current is not None:
            items["total_assets"] = current + non_current
            derived.append("total_assets")

    # Assets = liabilities + equity, by construction of the balance sheet.
    if get("equity") is None:
        assets, liabilities = get("total_assets"), get("total_liabilities")
        if assets is not None and liabilities is not None:
            items["equity"] = assets - liabilities
            derived.append("equity")

    if get("total_liabilities") is None:
        assets, equity = get("total_assets"), get("equity")
        if assets is not None and equity is not None:
            items["total_liabilities"] = assets - equity
            derived.append("total_liabilities")

    if get("gross_profit") is None:
        revenue, cogs = get("revenue"), get("cogs")
        if revenue is not None and cogs is not None:
            # Costs are filed as negatives (parenthesised); adding is correct
            # for that sign convention, and abs() would break the one case
            # where a template files them positive.
            items["gross_profit"] = revenue + cogs if cogs < 0 else revenue - cogs
            derived.append("gross_profit")

    items["derived"] = derived

    return items


def shares_outstanding(items):
    """Share count implied by filed capital, at the 1000-rial par value.

    Every TSE share has a par value of 1000 rials, so capital / 1000 is the
    share count. Deriving it from the filing rather than taking TSETMC's
    `total_shares` matters after a capital increase: the two disagree for as
    long as it takes the exchange to update, and a filing's own EPS is only
    reproducible against the filing's own capital.
    """

    capital = _finite(items.get("capital"))

    if not capital or capital <= 0:
        return None

    return capital / 1000.0


# ==============================
# RATIOS
# ==============================

def _ratio(numerator, denominator, percent=True):
    """Guarded division. A zero or absent denominator yields None, not inf."""

    numerator = _finite(numerator)
    denominator = _finite(denominator)

    if numerator is None or denominator is None or denominator == 0:
        return None

    value = numerator / denominator

    if not math.isfinite(value):
        return None

    return round(value * 100, 2) if percent else round(value, 4)


# A positive figure is compared with its base only when the base is at least
# this share of it -- below that the percentage measures how small the base
# was, not how the business grew. فناوا's six months read EPS +175,200% and
# profit +150,777% (EPS 1 rial -> 1,753) and took full growth points, which
# sorts a company that was barely profitable a year ago above every company
# that actually grew.
#
# Measured over the 14,496 stored filings on 2026-11-11: growth is p95 +291%
# revenue, +649% profit, +526% EPS, and the tail thins steadily past that
# with no natural break. 5% -- a base under a twentieth of today's figure,
# growth beyond +1,900% -- sits at about p98 in all three and leaves 1.3% of
# revenue, 2.0% of profit and 1.6% of EPS comparisons as "not comparable".
# Every figure below the line is returned exactly as before.
#
# A swing from a small profit to a large loss keeps its number. Its sign is
# what the scoring reads ("contracting"), and None there would drop a zero
# from the growth pillar and lift a company for falling into a loss.
GROWTH_MIN_BASE_SHARE = 0.05


def _growth(current, previous):
    """Percent change, with the sign trap Iranian filings walk into.

    Growth off a negative base is not meaningful: a company going from a
    1,000 loss to a 500 loss computes as -150% improvement under the usual
    formula, and from -1,000 to +500 as +150%, which ranks a turnaround below
    a company that merely grew. Both are reported as None -- "not comparable"
    -- rather than as a number that would sort. So is growth off a base too
    small to divide by (GROWTH_MIN_BASE_SHARE), its limiting case from above.
    """

    current = _finite(current)
    previous = _finite(previous)

    if current is None or previous is None or previous <= 0:
        return None

    if current > 0 and previous < current * GROWTH_MIN_BASE_SHARE:
        return None

    return round((current - previous) / previous * 100, 2)


def comparable_growth(value):
    """A stored growth figure, or None where its base was negligible.

    Growth is stored when a filing is parsed, so every filing parsed before
    GROWTH_MIN_BASE_SHARE existed still carries the old number, and the read
    paths carry it into a live score. The figure alone is enough to apply the
    same rule: growth g means current = previous * (1 + g/100), so the base
    was under GROWTH_MIN_BASE_SHARE of a positive current exactly when
    1 + g/100 exceeds its reciprocal.
    """

    value = _finite(value)

    if value is None:
        return None

    if 1 + value / 100.0 > 1.0 / GROWTH_MIN_BASE_SHARE:
        return None

    return value


def ratios(current, previous=None, market=None, months=None):
    """Every fundamental ratio derivable from one or two filed periods.

    `market` optionally carries the traded price and share count so the
    valuation ratios (P/E, P/S, P/B) can be computed; without it the profit,
    growth and health ratios still are.

    `months` is the period the filing covers. Flow figures are annualised by
    it before anything compares them to a price or to a balance-sheet
    position; growth is not, because both sides of a growth comparison come
    from the same statement and therefore cover the same span already.
    """

    current = current or {}
    previous = previous or {}
    market = market or {}

    out = {}
    out["period_months"] = months
    out["annualised"] = bool(months and months != 12)

    revenue = _finite(current.get("revenue"))
    net_profit = _finite(current.get("net_profit"))
    owners_profit = _finite(current.get("net_profit_owners")) or net_profit
    equity = _finite(current.get("equity_owners")) or _finite(current.get("equity"))
    assets = _finite(current.get("total_assets"))
    liabilities = _finite(current.get("total_liabilities"))
    operating_cf = _finite(current.get("operating_cash_flow"))

    # ---- margins ----------------------------------------------------------

    out["gross_margin"] = _ratio(current.get("gross_profit"), revenue)
    out["operating_margin"] = _ratio(current.get("operating_profit"), revenue)
    out["net_margin"] = _ratio(net_profit, revenue)

    # ---- returns ----------------------------------------------------------
    #
    # Against period-end equity rather than an average of opening and closing.
    # The average is the textbook form, but it needs an opening balance the
    # filing only carries for some templates, and mixing the two definitions
    # across a ranked list is worse than using the simpler one consistently.
    #
    # The profit is annualised first: a quarter's profit over year-end equity
    # is a quarter of the real return, and would rank every company that
    # happened to file an interim statement below one that filed an annual.
    out["roe"] = _ratio(annualise(owners_profit, months), equity)
    out["roa"] = _ratio(annualise(net_profit, months), assets)

    # ---- financial health -------------------------------------------------

    out["debt_ratio"] = _ratio(liabilities, assets)
    out["current_ratio"] = _ratio(
        current.get("current_assets"), current.get("current_liabilities"),
        percent=False
    )
    out["equity_ratio"] = _ratio(equity, assets)

    # ---- cash ------------------------------------------------------------

    out["operating_cash_flow"] = operating_cf
    out["operating_cash_flow_annual"] = annualise(operating_cf, months)
    out["ocf_margin"] = _ratio(operating_cf, revenue)

    # Earnings quality: does reported profit arrive as cash? Only meaningful
    # against a profit that exists -- against a loss the ratio's sign says
    # nothing about quality. Both sides come from the same statement, so no
    # annualising: the ratio of two period figures is already period-free.
    if operating_cf is not None and net_profit is not None and net_profit > 0:
        out["earnings_quality"] = round(operating_cf / net_profit, 3)
    else:
        out["earnings_quality"] = None

    # ---- growth -----------------------------------------------------------

    out["revenue_growth"] = _growth(revenue, previous.get("revenue"))
    out["profit_growth"] = _growth(net_profit, previous.get("net_profit"))
    out["operating_profit_growth"] = _growth(
        current.get("operating_profit"), previous.get("operating_profit")
    )
    out["eps_growth"] = _growth(current.get("eps"), previous.get("eps"))
    out["equity_growth"] = _growth(equity, previous.get("equity"))

    # ---- valuation --------------------------------------------------------

    price = _finite(market.get("close"))
    exchange_shares = _finite(market.get("total_shares"))
    shares = exchange_shares or shares_outstanding(current)

    out["shares"] = shares
    out["market_cap"] = price * shares if price and shares else None

    filed_eps = _finite(current.get("eps"))

    # EPS from the filing is the audited figure; deriving it from profit and
    # share count is the fallback for a template that does not print it.
    if filed_eps is None and owners_profit is not None and shares:
        filed_eps = round(owners_profit / shares, 2)
        out["eps_derived"] = True

    out["eps"] = filed_eps

    # The price is a claim on a year of earnings, so the earnings side has to
    # be a year's worth too. On an annual filing this changes nothing.
    annual_eps = annualise(filed_eps, months)
    annual_revenue = annualise(revenue, months)

    out["eps_annual"] = round(annual_eps, 2) if annual_eps is not None else None

    # Price and earnings have to be counted in the same shares. The price is
    # per share TODAY; the filed EPS is per share of the capital the filing
    # stood on, and after a capital increase the two differ by its factor --
    # شصفها (300 billion rials of capital -> 15.3 billion shares) read P/E
    # 0.04 and an earnings yield of 2,613% against a real ~2. On 2026-11-11
    # 60 of 577 profitable names traded on over 1.2x their filed share count.
    # So where the exchange gives today's count, P/E is market cap over the
    # year's owners' profit, which needs no per-share figure at all --
    # fundamental_outlook's construction. Rescaling the EPS by the two counts
    # instead would double-correct 27 of the 78 mismatched names, whose EPS is
    # already on today's count or whose capital line was misread (ملت, اسیا).
    annual_profit = annualise(owners_profit, months)

    if exchange_shares and exchange_shares > 0 and annual_profit is not None:
        value, earnings = out["market_cap"], annual_profit
    else:
        value, earnings = price, annual_eps

    # The filed EPS must be positive as well: a loss has no P/E, and an EPS
    # whose sign disagrees with the profit line is a filing misread.
    if value and earnings and earnings > 0 and annual_eps and annual_eps > 0:
        pe = value / earnings
        out["pe"] = round(pe, 2) if pe < config.PE_ABSURD else None
        out["earnings_yield"] = round(earnings / value * 100, 2)
    else:
        out["pe"] = None
        out["earnings_yield"] = None

    if out["market_cap"] and annual_revenue:
        out["ps"] = round(out["market_cap"] / annual_revenue, 2)
    else:
        out["ps"] = None

    if out["market_cap"] and equity and equity > 0:
        out["pb"] = round(out["market_cap"] / equity, 2)
        out["book_value_per_share"] = round(equity / shares, 2) if shares else None
    else:
        out["pb"] = None
        out["book_value_per_share"] = None

    # ---- provenance -------------------------------------------------------

    out["has_income"] = revenue is not None or net_profit is not None
    out["has_balance"] = assets is not None
    out["has_cashflow"] = operating_cf is not None
    out["has_comparative"] = bool(previous)

    return out


def summarise(items):
    """The filed figures worth carrying alongside the ratios."""

    keys = [
        "revenue", "gross_profit", "operating_profit", "net_profit",
        "net_profit_owners", "eps", "capital",
        "total_assets", "current_assets", "total_liabilities",
        "current_liabilities", "equity", "equity_owners", "cash", "inventory",
        "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
        "cogs", "finance_cost", "pretax_profit", "retained_earnings", "ppe",
        "receivables", "payables", "borrowings_short", "borrowings_long",
        "dividends_payable", "advances_received", "capex", "dividends_paid",
    ]

    out = {key: _finite((items or {}).get(key)) for key in keys}

    out["consolidated"] = bool((items or {}).get("consolidated"))
    out["derived"] = list((items or {}).get("derived") or [])

    return out
