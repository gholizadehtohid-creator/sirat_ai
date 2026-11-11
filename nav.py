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

        cost = _value_at(listed, total, _column(listed, END_OF_PERIOD, COST))
        market = _value_at(listed, total,
                           _column(listed, END_OF_PERIOD, MARKET_VALUE))
        gain = _value_at(listed, total,
                         _column(listed, END_OF_PERIOD, UNREALISED))

        out["listed_cost"] = cost * scale if cost is not None else None
        out["listed_market"] = market * scale if market is not None else None

        if gain is not None:
            out["listed_unrealised"] = gain * scale
        elif cost is not None and market is not None:
            # The filing usually states it; derive only when it does not.
            out["listed_unrealised"] = (market - cost) * scale

        out["has_market_value"] = out["listed_market"] is not None

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

        cost = _value_at(unlisted, total, _column(unlisted, END_OF_PERIOD, COST))

        out["unlisted_cost"] = cost * scale if cost is not None else None

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
