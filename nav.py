"""Net asset value for investment companies, from Codal portfolio filings.

WHY THIS EXISTS
---------------
An investment company's P/E measures whether it sold something last year, not
whether it is cheap. The yardstick that does work is price to NAV -- and NAV
is not in any income statement, so the screen had no way to judge 185 names,
including the largest sector on the exchange.

Codal letter ن-۳۱ carries it. The filing lists every holding with its cost AND
its market value at period end, and totals both:

    صورت وضعیت پرتفوی سهام قابل معامله در بازار سرمایه   listed, at market
    صورت وضعیت پرتفوی سهام سایر شرکتها                    unlisted, at cost only

HOW NAV IS DERIVED
------------------
The balance sheet carries holdings at COST, so book equity understates the
company by exactly the unrealised gain on its portfolio. The filing states
that gain directly, in its own افزایش (کاهش) column:

    NAV = equity + (listed market value - listed cost)

This is adjusted book value, and it is the honest construction available from
these two documents. What it is NOT:

  * Unlisted holdings stay at cost. The filing publishes no market value for
    them because there is no market price -- so a company whose value sits in
    private subsidiaries has its NAV understated, and this module says so
    rather than pretending otherwise (`unlisted_at_cost` is reported).
  * It is a period-end snapshot. Between filings the portfolio moves with the
    market and this number does not.

Both limitations are recorded on every result so a caller can decide whether
the estimate is good enough, instead of receiving a bare number that looks
exact.
"""

import re

import pandas as pd

import codal
import config
from persian import normalize


# Sheet headings, normalised. Codal spells the same sheet both ways across
# filings -- پرتفوی and پورتفوی -- so both are matched rather than assuming
# whichever the first sample happened to use.
LISTED_SHEETS = (
    "صورت وضعیت پرتفوی سهام قابل معامله در بازار سرمایه",
    "صورت وضعیت پورتفوی سهام قابل معامله در بازار سرمایه",
)

UNLISTED_SHEETS = (
    "صورت وضعیت پرتفوی سهام سایر شرکتها",
    "صورت وضعیت پورتفوی سهام سایر شرکتها",
)

# Column headers are a two-level MultiIndex: ('انتهای دوره', 'ارزش بازار').
# Matching on the pair matters -- 'ارزش بازار' alone also appears under
# 'ابتدای دوره', which is the value at the START of the period.
END_OF_PERIOD = "انتهای دوره"

MARKET_VALUE = "ارزش بازار"
COST = "بهای تمام شده"
UNREALISED = "افزایش (کاهش)"

# The totals row. Summing the rows instead would double-count, because the
# sheet carries subtotals per listing board as well as a grand total.
TOTAL_LABELS = ("جمع", "جمع کل")

_TOTAL_RE = re.compile(r"^\s*(جمع|جمع کل)\s*$")


def _text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return normalize(str(value)).strip()


def _column(frame, level0, level1):
    """Find a column by its (group, header) pair, tolerating flat columns."""

    target0, target1 = normalize(level0), normalize(level1)

    for column in frame.columns:

        if isinstance(column, tuple):
            parts = [normalize(str(p)) for p in column]
        else:
            # pandas sometimes hands back the tuple already stringified.
            text = normalize(str(column))
            parts = [p.strip(" '\"") for p in text.strip("()").split("',")]

        if len(parts) < 2:
            continue

        if target0 in parts[0] and target1 in parts[1]:
            return column

    return None


def _total_row(frame):
    """Index of the grand-total row, or None.

    Taken from the label rather than from position: filings differ in how many
    blank rows trail the table, and `iloc[-1]` picked up a footnote.
    """

    if frame.empty:
        return None

    first = frame.columns[0]

    best = None

    for i in range(len(frame) - 1, -1, -1):

        label = _text(frame.iloc[i][first])

        if _TOTAL_RE.match(label):
            # The grand total is the LAST جمع; earlier ones are per-board
            # subtotals (جمع سهام درج شده and friends).
            return i

        if label in TOTAL_LABELS and best is None:
            best = i

    return best


def _value_at(frame, row_index, column):
    if row_index is None or column is None:
        return None

    try:
        return codal.parse_number(frame.iloc[row_index][column])
    except (KeyError, IndexError):
        return None


def _rows_sum(frame, column, upto):
    """The sum of `column` over the holding rows above `upto`.

    Holding rows: a name, not a «جمع ...» subtotal. None when no row carries
    a number.
    """

    if column is None or upto is None:
        return None
    first = frame.columns[0]
    total, seen = 0.0, False
    for i in range(upto):
        label = _text(frame.iloc[i][first])
        if not label or label.startswith("جمع"):
            continue
        try:
            value = codal.parse_number(frame.iloc[i][column])
        except (KeyError, IndexError):
            value = None
        if value is not None:
            total += value
            seen = True
    return total if seen else None


STAKE_IMPOSSIBLE = 1.5


def _inconsistent_rows(frame, upto):
    """Holding rows whose stake is over STAKE_IMPOSSIBLE of the investee's shares.

    Shares held (start + change, or the end count) against the investee's
    shares outstanding, from the row's own capital (million rials) and par.
    A stake over 100% is a typo in the filing -- زکوثر's 1405/07 report
    listed زشریف at ten times its shares, a 731% stake, and its NAV read four
    times too high (the NAV verification, 2026-11-12). Rows without the
    columns are not judged.
    """

    if upto is None:
        return 0
    capital = _column(frame, "سرمایه", "سرمایه")
    par = _column(frame, "ارزش اسمی", "ارزش اسمی")
    start = _column(frame, "ابتدای دوره", "تعداد سهام")
    change = _column(frame, "تغییرات", "تعداد سهام")
    end = _column(frame, END_OF_PERIOD, "تعداد سهام")
    if capital is None or par is None or (end is None and start is None):
        return 0
    first = frame.columns[0]
    bad = 0
    for i in range(upto):
        label = _text(frame.iloc[i][first])
        if not label or label.startswith("جمع"):
            continue
        cap = _value_at(frame, i, capital)
        nominal = _value_at(frame, i, par)
        held = _value_at(frame, i, end) if end is not None else None
        if held is None and start is not None:
            held = (_value_at(frame, i, start) or 0) +                 (_value_at(frame, i, change) or 0 if change is not None else 0)
        if not cap or not nominal or not held:
            continue
        outstanding = cap * 1_000_000 / nominal
        # 1.5, not 1.05: a row's capital column can predate the investee's
        # own capital increase (میدکو's filing read its 82.6% stake as
        # 121.5%), while a typo multiplies the stake (زکوثر: 731%).
        if outstanding > 0 and held / outstanding > STAKE_IMPOSSIBLE:
            bad += 1
    return bad


def _total_or_sum(frame, total, column):
    """The filed total, or the holdings' sum where the total reads zero.

    Codal's export leaves the «جمع» rows at 0 when the filer's form computed
    them on screen: شستا's 1405/07 portfolio listed eleven holdings worth
    7,056 thousand billion rials under a total of 0, and 11 of the first 36
    portfolios caught up on 2026-11-11 read as an empty portfolio. The
    holdings are summed only when the total is missing or zero.
    """

    filed = _value_at(frame, total, column)
    if filed:
        return filed
    summed = _rows_sum(frame, column, total)
    return summed if summed else filed


def _pick(sheets, names):
    """The first matching sheet's first table, or None."""

    if not sheets:
        return None

    wanted = [normalize(n) for n in names]

    for heading, tables in sheets.items():

        heading_text = normalize(str(heading))

        if not any(w in heading_text or heading_text in w for w in wanted):
            continue

        for table in tables:
            if isinstance(table, pd.DataFrame) and not table.empty:
                return table

    return None


def extract_portfolio(sheets):
    """Portfolio totals from one ن-۳۱ filing, in rials.

    Returns None when neither portfolio sheet is present -- an ordinary
    company's monthly report has no portfolio, and reporting zeros for it
    would put every manufacturer on the NAV screen at a NAV of nothing.
    """

    listed = _pick(sheets, LISTED_SHEETS)
    unlisted = _pick(sheets, UNLISTED_SHEETS)

    if listed is None and unlisted is None:
        return None

    scale = config.CODAL_AMOUNT_SCALE

    out = {
        "listed_cost": None,
        "listed_market": None,
        "listed_unrealised": None,
        "unlisted_cost": None,
        "holdings": 0,
        "has_market_value": False,
    }

    if listed is not None:

        total = _total_row(listed)

        cost = _total_or_sum(listed, total, _column(listed, END_OF_PERIOD, COST))
        market = _total_or_sum(listed, total,
                               _column(listed, END_OF_PERIOD, MARKET_VALUE))
        gain = _total_or_sum(listed, total,
                             _column(listed, END_OF_PERIOD, UNREALISED))
        if not gain and cost and market:
            gain = None                 # derived below from market - cost

        out["listed_cost"] = cost * scale if cost is not None else None
        out["listed_market"] = market * scale if market is not None else None

        if gain is not None:
            out["listed_unrealised"] = gain * scale
        elif cost is not None and market is not None:
            # The filing usually states it; derive only when it does not.
            out["listed_unrealised"] = (market - cost) * scale

        out["has_market_value"] = out["listed_market"] is not None

        out["inconsistent_rows"] = _inconsistent_rows(listed, total)

        # Holdings are the rows above the total that carry a company name.
        if total is not None:
            first = listed.columns[0]
            out["holdings"] = sum(
                1 for i in range(total)
                if _text(listed.iloc[i][first])
                and not _TOTAL_RE.match(_text(listed.iloc[i][first]))
            )

    if unlisted is not None:

        total = _total_row(unlisted)

        cost = _total_or_sum(unlisted, total,
                             _column(unlisted, END_OF_PERIOD, COST))

        out["unlisted_cost"] = cost * scale if cost is not None else None

    # The holdings themselves, for a NAV at today's prices (2026-11-25).
    try:
        out["rows"] = extract_holdings(sheets)
    except Exception:                                           # noqa: BLE001
        out["rows"] = None          # the totals stand on their own

    return out


SHARES = "تعداد سهام"
VALUE_PER_SHARE = "ارزش هر سهم"
OWNERSHIP = "درصد مالکیت"


def extract_holdings(sheets):
    """Every holding of one ن-۳۱ filing, row by row (added 2026-11-25).

    extract_portfolio() keeps only the totals, and a NAV from totals can move
    only with the index. The rows are what values each holding at TODAY's
    price (portfolio_value.py). Each row:

        {"section": "listed" | "unlisted", "row": n, "name",
         "capital" (rials), "par" (rials), "shares" (at period end),
         "ownership_pct", "cost" (rials), "market" (rials, listed only),
         "value_per_share" (rials, listed only)}

    The period-end share count is its own column where the filing has one,
    else the start plus the period's change -- the layout seen on 2026-11-25
    has no end count. Subtotal («جمع») and heading rows (a label and no
    figure, «سهام پذیرفته شده در بورس و فرابورس») are left out.
    """

    scale = config.CODAL_AMOUNT_SCALE
    out = []

    for section, names in (("listed", LISTED_SHEETS), ("unlisted", UNLISTED_SHEETS)):
        frame = _pick(sheets, names)
        if frame is None:
            continue

        total = _total_row(frame)
        upto = total if total is not None else len(frame)
        first = frame.columns[0]

        c_capital = _column(frame, "سرمایه", "سرمایه")
        c_par = _column(frame, "ارزش اسمی", "ارزش اسمی")
        c_start = _column(frame, "ابتدای دوره", SHARES)
        c_change = _column(frame, "تغییرات", SHARES)
        c_end = _column(frame, END_OF_PERIOD, SHARES)
        c_pct = _column(frame, END_OF_PERIOD, OWNERSHIP)
        c_cost = _column(frame, END_OF_PERIOD, COST)
        c_market = _column(frame, END_OF_PERIOD, MARKET_VALUE)
        c_per_share = _column(frame, END_OF_PERIOD, VALUE_PER_SHARE)

        for i in range(upto):
            name = _text(frame.iloc[i][first])
            if not name or name.startswith("جمع"):
                continue

            cost = _value_at(frame, i, c_cost)
            market = _value_at(frame, i, c_market) if section == "listed" else None
            shares = _value_at(frame, i, c_end)
            if shares is None:
                start = _value_at(frame, i, c_start)
                change = _value_at(frame, i, c_change)
                if start is not None or change is not None:
                    shares = (start or 0.0) + (change or 0.0)
            if cost is None and market is None and not shares:
                continue                # a heading row

            capital = _value_at(frame, i, c_capital)
            out.append({
                "section": section,
                "row": len(out),
                "name": name,
                "capital": capital * scale if capital is not None else None,
                "par": _value_at(frame, i, c_par),
                "shares": shares,
                "ownership_pct": _value_at(frame, i, c_pct),
                "cost": cost * scale if cost is not None else None,
                "market": market * scale if market is not None else None,
                "value_per_share": (_value_at(frame, i, c_per_share)
                                    if section == "listed" else None),
            })

    return out


def net_asset_value(portfolio, equity):
    """NAV = book equity + the unrealised gain the balance sheet omits.

    Returns a dict rather than a number so the caller can see what the
    estimate rests on. `complete` is False whenever unlisted holdings are
    present, because those sit at cost with no market value published -- the
    NAV is then a floor, not a valuation.
    """

    result = {
        "nav": None,
        "equity": equity,
        "unrealised": None,
        "unlisted_at_cost": None,
        "complete": False,
        "basis": None,
    }

    if equity is None or not portfolio:
        result["basis"] = "no equity or no portfolio filing"
        return result

    unrealised = portfolio.get("listed_unrealised")

    if unrealised is None:
        result["basis"] = "portfolio filing carries no market value"
        return result

    # A portfolio of holdings whose listed cost, market value and gain all
    # read zero is a misread total row, not an empty portfolio: 51 of 141
    # stored ones (وغدیر with 22 holdings) made NAV equal book equity, and
    # P/NAV 13.08 was only P/B (the review panel, 2026-11-11).
    if (portfolio.get("holdings") or 0) > 0 and \
            not any(portfolio.get(k) for k in
                    ("listed_cost", "listed_market", "listed_unrealised")):
        result["basis"] = ("portfolio filing lists holdings but read no listed "
                           "value -- a misread, not an empty portfolio")
        return result

    result["unrealised"] = unrealised
    result["unlisted_at_cost"] = portfolio.get("unlisted_cost")
    result["nav"] = equity + unrealised

    unlisted = portfolio.get("unlisted_cost") or 0

    result["complete"] = unlisted <= 0

    result["basis"] = (
        "book equity plus unrealised gain on listed holdings"
        if result["complete"] else
        "book equity plus unrealised gain on listed holdings; "
        "unlisted holdings remain at cost, so this understates NAV"
    )

    return result


def price_to_nav(market_cap, nav_result):
    """P/NAV, or None. Below 1.0 the market prices the company under its assets."""

    if not nav_result:
        return None

    nav = nav_result.get("nav")

    if not nav or nav <= 0 or not market_cap or market_cap <= 0:
        return None

    return market_cap / nav
