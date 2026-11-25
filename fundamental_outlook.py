"""A forward-looking read of a company's filed statements.

WHY THIS EXISTS
---------------
The fundamental score (fundamental_engine.py) reads the LATEST statement:
its margins, its gearing, its P/E on that one period annualised. On
2026-11-05 the user asked for an analysis that is comprehensive and looks
forward. Two things the score cannot see:

  1. WHERE THE YEAR IS HEADING. A 6-month statement is half a year. Doubling
     it ignores seasonality; this module completes the year the way an
     analyst does -- the months still to come are last year's same months,
     grown at the rate this year has grown so far, at this year's margin.
     That is an ESTIMATE of the current fiscal year, labelled as such, not a
     forecast of anything the company has said.

  2. WHETHER THE PROFIT IS BEING CONVERTED AND FUNDED. Receivables growing
     faster than sales, profit without operating cash, capex paid for with
     new debt, interest eating operating profit -- the things that turn a good
     year on paper into a bad one in cash.

THE METHOD, PER COMPANY
-----------------------
  latest      the newest stored period (3, 6, 9 or 12 months)
  same_prev   the same months of the prior fiscal year: the stored filing
              when there is one, otherwise backed out of the latest filing's
              own year-on-year growth (its comparative column)
  annual_prev the prior fiscal year's 12-month statement
  TTM         latest + annual_prev - same_prev   (latest when it is annual);
              where that cannot be netted, built from the statements' own
              spans on one basis (_flow)
  forward     latest + (annual_prev - same_prev) * (1 + g), the remaining
              revenue at the TTM margin; g = inflation + 0.3 x (YTD growth
              - inflation) (rest_growth: the months to come grow much less
              like the months gone than was assumed until 2026-11-25). For
              an annual latest: next year at this year's revenue grown by
              the same rule and this year's margin; growth capped to
              [-50%, +100%].

Owners' profit is used where the filing separates it; shares are capital at
the 1,000-rial par. Price is the last stored close.

WHAT IT DOES NOT CLAIM
----------------------
None of this has been tested as a predictor of returns. The fundamental
quality signal that would be (RESEARCH_OPTIONS Option 4) waits for the Codal
backfill; until then this is analysis for a human, and the panel says so.
Line items added on 2026-11-05 (receivables, debt, capex, dividends) exist
only for filings parsed after that date; older ones report those parts as
unavailable rather than as zero.
"""

import math

import config
import database
import fundamental_db
import jalali

PAR_VALUE = 1_000                       # rials per share
GROWTH_CAP = (-0.50, 1.00)
# How much of a company's own sales growth the months NOT YET REPORTED keep;
# the rest is inflation (rest_growth).
#
# Until 2026-11-25 the months to come were grown at the year-to-date rate
# itself. Measured on 3,975 past estimates whose year then closed (fiscal
# years 1399-1405, each decided from what was public on its day), the growth
# of the rest of the year hardly follows the growth so far:
#
#     sales so far    -27%   +11%   +31%   +49%   +69%   +89%  +117%  +244%
#     rest of year    +28%   +31%   +36%   +43%   +49%   +54%   +56%   +44%
#
# A year compared with a year whose prices had not yet stepped up shows a
# growth the later months, compared with months already at the new prices,
# do not have; and a company that sold early has less left to sell. So a
# year-to-date fall read 26-59% too low a profit and a doubling 11-23% too
# high. With the weight at 0.3 the median error of the year's profit, by
# outlook_calibration's own point-in-time measure, went from 35.6 to 30.6%
# three months in, 27.4 to 25.4% at six, 21.2 to 19.2% at nine, a year not
# yet begun 36.4 to 35.4%; lower in every fiscal year 1400-1405 and in both
# halves of them. In 1405, the devaluation year, the centre mattered as much
# as the weight: at that year's 77% inflation the six-month error was 30.3%,
# at the long-run 40% it was 32.7%, as it was 37.6% -- so the centre is
# config.INFLATION_RATE and has to be kept current.
#
# Tried beside it and NOT adopted, each no better on the same points: the
# last quarter's margin, last year's same-season margin, a build from
# operating profit, finance cost and tax (a point better nine months in,
# no better earlier), this year's own monthly pace, and the estimate
# multiplied by its bucket's past median error (no gain on years it had not
# seen). The margin stays the trailing twelve months'.
REST_GROWTH_WEIGHT = 0.3
STALE_MONTHS = 7                        # a newest period older than this
INTEREST_COVER_LOW = 2.0

# Twelve-month operating cash over profit, measured across the 379 ranked
# companies on 2026-11-10: deciles -0.17 0.06 0.22 0.36 0.49 0.63 0.83 1.04
# 1.66, median 0.49. At 77% inflation working capital absorbs half of a
# typical company's profit, so 0.5 described the median company rather than
# warned of one; the warning marks the market's bottom third.
CASH_CONVERSION_LOW = 0.25
RECEIVABLE_GAP_PCT = 15.0               # receivables outgrowing sales by this

# Consolidated and separate statements are compared only when they describe
# about the same business: monthly revenue within this ratio of each other.
BASIS_SCALE_BAND = (0.67, 1.5)

# Points by which actual growth must beat, or miss, the growth the price
# requires before it is called out.
GROWTH_CUSHION_PCT = 15.0

# Past these, a result is far likelier a filing the parser misread than a
# company: counted and named for a manual check, never ranked.
SUSPECT_PE = 1.0
SUSPECT_GROWTH_PCT = (-80.0, 400.0)
SUSPECT_MARGIN_PCT = 100.0

# Banks, insurers, investment and holding companies: P/E on a disposal gain or
# a provision is not a valuation (config.EARNINGS_MULTIPLE_NOT_MEANINGFUL).
# Ranked in their own list, by P/NAV where the portfolio filing allows it,
# else by P/B.
FINANCIAL = "financial"
OPERATING = "operating"

# ---- can the price be dealt at? --------------------------------------------
# Found 2026-11-11, from the user: "how does شصفها have a forward P/E under
# 2?". The filings were read right -- TSETMC's own P/E was 2.2 against our
# TTM 2.18 -- but the price was not one anybody could deal at: a ±1% band, all
# trades at one price above the day before (a buy queue) on 15 of 20
# sessions, 0.2 billion rials a day where the names below it traded hundreds.
# A P/E on such a price measures how slowly the board lets it move, not what
# the market thinks, and it led the ranking. The floor is the one the
# fundamental screen already gates on (config.MIN_DAILY_VALUE, the mean value
# of the newest 20 bars, zero days included -- fundamentals.avg_value_20).
TRADING_SESSIONS = 20
# Snapshot 2026-11-11: 744 equities at ±3%, 14 at ±2%, 5 at ±1%. A ±3% band
# on a cheap share reads narrower after the limits are cut to the tick, so
# one tick is allowed before a band is called narrow.
NARROW_BAND_PCT = 2.5
TICK_RIALS = 10
# Sessions of the 20 whose every trade was at one price: a queue at a band
# edge. 68 of 904 equities had 10 or more on 2026-11-11.
LOCKED_SESSIONS = 10
# Names under the floor listed by screen(), cheapest-looking first; the rest
# are counted.
THIN_LISTED = 20

# ---- is the profit the business's, and does the exchange agree? -----------
# The second audit of 2026-11-11 (the user: "test the fundamental section
# again for problems of this kind"):
#  * حافرین earned 23.6 thousand billion rials in six months on an operating
#    profit of 3.05 -- a gain outside the business -- and read P/E 0.96.
#    Net profit above ONE_OFF_RATIO x operating profit (or a net profit on an
#    operating loss) is named, and the fundamentals card does not score a P/E
#    on it. At 1.5x it named 33 of the outlook's companies, most of them
#    between 1.5 and 2.0 for reasons that recur: a holding's dividends from
#    its subsidiaries (پترول 1.76, مبین 1.92), dairy farms' livestock
#    revaluation (زقیام, تلیسه, زپارس), cement makers' interest income
#    (سمتاز 1.94). Above 2x the business is the minority of the profit
#    (حافرین 7.7, کسرام 12.8, نمرینو 44.5, قثابت 3.8).
#  * TSETMC publishes its own P/E. Ours on the trailing year matched it
#    (median 0.99, 93% within ±25%) except where a group's consolidated
#    profit and the parent's own differ (همراه 4.8 vs 8.6, شفا 10.9 vs 38.5):
#    a gap outside PUBLISHED_PE_BAND is shown, not silently resolved.
#  * جوین, قپیرا, شلیا: two or three months of monthly sales up 220-240%
#    against a year whose price step came mid-way, extrapolated to the year
#    at the +100% cap -- forward profit exactly twice the trailing.
ONE_OFF_RATIO = 2.0
# Operating profit above gross profit by this share of itself: "other
# operating income" -- livestock or FX revaluation, mostly -- is that much of
# what the P/E rests on (سیمرغ: gross 2,335 bn, operating 17,918 bn).
OTHER_OPERATING_SHARE = 0.3

# A portfolio filing (ن-31) older than this many months prices NAV on that
# month's market; past it the listed holdings are carried at the index's move.
NAV_STALE_MONTHS = 1
# An operating company gets a P/NAV only when its listed portfolio is at
# least this share of its market value: a holding listed under its
# subsidiaries' industry, not a manufacturer with a few shares on the side.
HOLDING_LISTED_SHARE = 0.2
# The parent's equity this many months before the portfolio is named.
EQUITY_GAP_MONTHS = 6
# Today's share count this much above the statement's capital is a raise
# since the statement.
RAISE_TOLERANCE = 0.05
PUBLISHED_PE_BAND = (0.67, 1.5)
EARLY_MONTHS = 4
EARLY_JUMP = 1.5
# The statement's capital line against the AGM's capital, as share counts:
# past this ratio either way one of them is misread (a raise between the two
# is real but rarely 6x).
SHARES_DISAGREE = 6.0
# The last three months' sales growth against the year-to-date's, and the
# last three months' purchases-to-sales against the year's, past which the
# difference is named (needs the monthly history, codal_monthly --history).
RECENT_GAP_PCT = 15.0
COST_TREND_PTS = 5.0
# A fiscal year shorter than twelve months is accepted at these lengths
# (short_years). Shorter than three is more likely two misread dates than a
# year; a long year (over twelve) is not one Iranian companies file.
SHORT_YEAR_MONTHS = (3, 11)
# A trailing year built across a fiscal-year change (_trailing_across_change)
# is kept only while its revenue is within this ratio of the latest period's
# annualised: outside it a period from another series was netted in.
ACROSS_CHANGE_BAND = (0.5, 2.0)


def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _div(a, b):
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(x, digits=1):
    return None if x is None else round(x * 100, digits)


def rest_growth(growth, inflation_pct):
    """The sales growth expected of months not yet reported: inflation, and
    REST_GROWTH_WEIGHT of what the company's own growth differs from it by
    (`growth` a fraction, `inflation_pct` a percentage), inside GROWTH_CAP."""

    centre = inflation_pct / 100.0
    pulled = centre + REST_GROWTH_WEIGHT * (growth - centre)
    return min(max(pulled, GROWTH_CAP[0]), GROWTH_CAP[1])


def fiscal_year_end(period_end, months):
    """(jy, jm) of the fiscal year a period belongs to."""

    try:
        jy, jm = (int(p) for p in str(period_end).split("/")[:2])
    except (TypeError, ValueError):
        return None

    return jalali.add_months(jy, jm, 12 - int(months or 12))


def _profit(items):
    owners = _f(items.get("net_profit_owners"))
    return owners if owners is not None else _f(items.get("net_profit"))


def _month_index(period_end):
    """year * 12 + month of a period end, or None. Only a month's last days
    (29, 30, 31) count: a statement ends at a month's end, and a date such as
    1404/01/01 or 1400/10/10 is a misread one, not a fiscal year's edge."""

    try:
        y, m, d = (int(p) for p in str(period_end).split("/")[:3])
    except (TypeError, ValueError):
        return None
    if d < 29 or not 1 <= m <= 12:
        return None
    return y * 12 + m


def short_years(rows):
    """{period_end: months} of the annual statements that cover less than a
    year; rows in load_metrics' shape.

    A company that moves its fiscal year-end files one SHORT year between
    the two: کگل's, کچاد's and دکوثر's ran 1404/01..1404/09 when they moved
    from Esfand to Azar (found 2026-11-25; about 60 companies moved in
    1402-1405, most of them to Azar). Codal titles it «سال مالی منتهی به
    ...» like any year, so it is stored with period_months 12. Read as a
    year it is a quarter short of one (its EPS 25% low), and the trailing
    twelve months across it could not be built: the new year's nine months
    have no "same nine months" a year before.

    An annual statement ending at E is short when the company's own filings
    put its fiscal year's START inside the twelve months before E: an earlier
    annual statement ends there, or the interim statements within the year
    start there. An interim within the year that starts earlier -- a
    twelve-month year's -- contradicts it, and the statement stays a year: a
    subsidiary's filings stored under the parent's symbol carry a start of
    their own, and the parent's interims are what overrule them.
    """

    spans = set()
    ends = {}
    for row in rows:
        e = _month_index(row.get("period_end"))
        if e is None:
            continue
        months = int(row.get("period_months") or 12)
        spans.add((e, months))
        if months == 12:
            ends.setdefault(e, []).append(row.get("period_end"))
    annual = sorted(ends)
    out = {}
    for e in annual:
        inside = [(pe, pm) for pe, pm in spans if pm < 12 and e - 12 < pe <= e]
        bounds = {a for a in annual if e - 12 < a < e}
        bounds |= {pe - pm for pe, pm in inside if e - 12 < pe - pm < e}
        if not bounds:
            continue
        start = max(bounds)
        if any(pe - pm < start for pe, pm in inside if pe > start):
            continue
        months = e - start
        if SHORT_YEAR_MONTHS[0] <= months <= SHORT_YEAR_MONTHS[1]:
            for end in ends[e]:
                out[end] = months
    return out


def _periods(rows):
    """The stored periods, newest first. A short fiscal year (short_years)
    carries its true length in "months", so its fiscal year is read as the
    one its own interims belong to; "annual" stays True for every statement
    Codal titles as a year, short or not, and "short_year" marks the short."""

    short = short_years(rows)
    out = []
    for row in rows:
        items = row.get("items") or {}
        titled = int(row.get("period_months") or 12)
        months = short.get(row.get("period_end"), titled) if titled == 12 \
            else titled
        fy = fiscal_year_end(row.get("period_end"), months)
        if fy is None:
            continue
        out.append({"end": row.get("period_end"), "months": int(months),
                    "fy": fy, "items": items, "ratios": row.get("ratios") or {},
                    "published": row.get("published_at"),
                    "audited": row.get("audited"),
                    "consolidated": bool(row.get("consolidated")),
                    "annual": titled == 12,
                    "short_year": titled == 12 and months != 12})
    # A short year and an interim of the same months can end on one day (the
    # year's nine-month statement and the year's own, on the other basis):
    # the year's statement, the later and audited one, is read first.
    out.sort(key=lambda p: (p["end"], p["months"], p["annual"]), reverse=True)
    return out


def _has_income(items):
    items = items or {}
    return any(items.get(k) is not None
               for k in ("revenue", "net_profit", "operating_profit"))


def _monthly_revenue(periods):
    values = sorted(v for v in (_div(_f(p["items"].get("revenue")), p["months"])
                                for p in periods) if v and v > 0)
    return values[len(values) // 2] if values else None


def choose_basis(periods, today=None):
    """(periods, basis): like with like, never a group against its parent.

    Found 2026-11-10: 347 of 785 companies file SEPARATE statements at 3 and
    9 months and CONSOLIDATED ones at 6 and 12. For a holding company the two
    are different businesses -- the parent alone earns little but its
    subsidiaries' dividends -- and comparing a separate half-year with last
    year's consolidated one read خبهمن's revenue as down 98%.

    When the two bases are about the same size (a manufacturer with small
    subsidiaries) they are one series and all periods are kept, the freshest
    first. When they differ materially, only the consolidated periods are
    used, the whole group being what the shares own -- unless the newest of
    them is stale, in which case the separate series is the better read.
    """

    consolidated = [p for p in periods if p["consolidated"]]
    separate = [p for p in periods if not p["consolidated"]]

    if not consolidated:
        return periods, "separate"
    if not separate:
        return periods, "consolidated"

    a, b = _monthly_revenue(consolidated), _monthly_revenue(separate)
    if a and b and BASIS_SCALE_BAND[0] <= a / b <= BASIS_SCALE_BAND[1]:
        return periods, "mixed"

    age = _age_months(consolidated[0]["end"], today)
    if age is not None and age > STALE_MONTHS:
        # The group's statements are stale and the parent's alone are not.
        # Usually only for weeks: the 6-month consolidated statement follows
        # the separate one. Named apart so the gap is not read as no data.
        return separate, "separate_fallback"
    return consolidated, "consolidated"


def _months_between(earlier, later):
    """Whole months from one Jalali 'YYYY/MM/..' to another, or None."""

    try:
        y1, m1 = (int(p) for p in str(earlier).split("/")[:2])
        y2, m2 = (int(p) for p in str(later).split("/")[:2])
    except (TypeError, ValueError):
        return None
    return (y2 - y1) * 12 + (m2 - m1)


def _age_months(period_end, today=None):
    today = today or jalali.today_jalali()
    try:
        ey, em = (int(p) for p in str(period_end).split("/")[:2])
        return (today[0] - ey) * 12 + (today[1] - em)
    except (TypeError, ValueError, AttributeError, IndexError):
        return None


def _find(periods, fy, months):
    for p in periods:
        if p["fy"] == fy and p["months"] == months:
            return p
    return None


def _prior_fy(fy):
    return (fy[0] - 1, fy[1])


def _flow(pool, lo, hi, key):
    """`key` summed over the months (lo, hi] -- _month_index values -- from
    the cumulative statements in `pool`, or None when they cannot give it.

    Built for a fiscal-year change, where "last year's annual less last
    year's same months" has no same months: the window is filled from its
    end backwards with the longest statement ending where it still needs
    filling, and one that starts before the window is cut at `lo` by its own
    year's statement ending there. Across کگل's move (Esfand -> Azar, 1404)
    the twelve months to 1405/06 are the new year's nine plus the short
    year (1404/01..09) less its six months to 1404/06. `pool` must be ONE
    basis: a consolidated year less a separate half is a quarter of
    neither (کگل's group short year 1,651,000 bn rials less the parent's
    half 610,000, where the parent's own year is 909,000).
    """

    by_end = {}
    for p in pool:
        e = _month_index(p["end"])
        if e is not None:
            by_end.setdefault(e, []).append(p)
    total, cur = 0.0, hi
    while cur > lo:
        here = sorted(by_end.get(cur, ()), reverse=True,
                      key=lambda p: (p["months"], p.get("annual", False)))
        if not here:
            return None
        value = _f(here[0]["items"].get(key))
        if value is None:
            return None
        start = cur - here[0]["months"]
        if start >= lo:
            total, cur = total + value, start
            continue
        part = next((q for q in by_end.get(lo, ())
                     if lo - q["months"] == start), None)
        cut = _f(part["items"].get(key)) if part else None
        if cut is None:
            return None
        total, cur = total + value - cut, lo
    return total


def _trailing_across_change(pool, latest, key):
    """The twelve months to `latest`'s end, from `pool` (_flow)."""

    e = _month_index(latest["end"])
    return None if e is None else _flow(pool, e - 12, e, key)


def _basis_pool(periods, latest, versions=None):
    """The periods on `latest`'s basis, with the store's other releases of
    that basis filling the periods `periods` holds on the other one.

    `metrics` keeps one release per period, so last year's six months are
    held as the audited consolidated release while this year's newest is
    separate (کگل's 1404/06: the separate 610,000 bn is only in
    metric_versions). `versions` are the company's releases in
    pit_fundamentals.load_versions' shape."""

    same = [p for p in periods if p["consolidated"] == latest["consolidated"]]
    if not versions:
        return same
    import pit_fundamentals
    extra = pit_fundamentals.periods_as_of(versions, "9999",
                                           latest["consolidated"])
    held = {(_month_index(p["end"]), p["months"]) for p in same}
    rows = [{"period_end": p["end"], "period_months": 12 if p["annual"]
             else p["months"], "items": p["items"]} for p in periods]
    short = short_years(rows + extra)
    for p in _periods(extra):
        if short.get(p["end"]) and p["annual"]:
            p["months"], p["short_year"] = short[p["end"]], True
        key = (_month_index(p["end"]), p["months"])
        if key[0] is not None and key not in held and _has_income(p["items"]):
            same.append(p)
            held.add(key)
    return same


def _backed_out(latest, key, growth_key):
    """Prior-year same-period figure from the latest filing's own growth."""

    value = _f(latest["items"].get(key))
    growth = _f(latest["ratios"].get(growth_key))
    if value is None or growth is None or growth <= -100:
        return None
    return value / (1 + growth / 100.0)


def monthly_reads(ins_code, fy_end_month, rows=None):
    """What the newest monthly activity reports say, or None.

    Year-to-date sales growth, this month against the year's monthly run
    rate, price against volume for the main products, purchases and energy
    against sales, production against sales, and the company's own estimate
    of FX earnings for the rest of the year.
    """

    if rows is None:
        import codal_monthly
        try:
            rows = codal_monthly.load(ins_code, limit=codal_monthly.HISTORY_MONTHS)
        except Exception:                                       # noqa: BLE001
            rows = []
    if not rows:
        return None

    newest = rows[0]
    try:
        py, pm = (int(p) for p in newest["period_end"].split("/")[:2])
    except (KeyError, ValueError):
        return None

    elapsed = (pm - int(fy_end_month or 12)) % 12 or 12
    fiscal_year = jalali.add_months(py, pm, 12 - elapsed)

    sales = newest.get("sales") or {}
    ytd, ytd_prev, month = (_f(sales.get(k)) for k in ("ytd", "ytd_prev", "month"))

    out = {
        "period_end": newest["period_end"],
        "published_at": newest.get("published_at"),
        "template": newest.get("template"),
        "months_elapsed": elapsed,
        "fiscal_year": fiscal_year,
        "sales_month": month, "sales_ytd": ytd, "sales_ytd_prev": ytd_prev,
        "ytd_growth": (ytd / ytd_prev - 1) if ytd and ytd_prev and ytd_prev > 0 else None,
        "momentum": _div(month, ytd / elapsed) if ytd and month is not None and elapsed > 1 else None,
    }

    def growth(block):
        a, b = _f((block or {}).get("ytd")), _f((block or {}).get("ytd_prev"))
        return a / b - 1 if a and b and b > 0 else None

    out["purchases_growth"] = growth(newest.get("purchases"))
    out["energy_ytd"] = _f((newest.get("energy") or {}).get("ytd"))
    out["purchases_to_sales"] = _div((newest.get("purchases") or {}).get("ytd"), ytd)
    out["fx_forecast_rest_of_year"] = _f((newest.get("fx") or {}).get("forecast"))

    expense = newest.get("expense") or {}
    out["expense_ytd"] = _f(expense.get("ytd"))
    out["expense_to_income"] = _div(expense.get("ytd"), ytd)

    products = []
    for p in (newest.get("products") or [])[:6]:
        now, before = p.get("ytd") or {}, p.get("ytd_prev") or {}
        products.append({
            "name": p.get("name"), "unit": p.get("unit"),
            "amount_ytd": now.get("amount"),
            "price_change": (now["rate"] / before["rate"] - 1)
            if now.get("rate") and before.get("rate") else None,
            "volume_change": (now["sales_qty"] / before["sales_qty"] - 1)
            if now.get("sales_qty") and before.get("sales_qty") else None,
            "production_to_sales": _div(now.get("production"), now.get("sales_qty")),
        })
    out["products"] = products

    # The month-by-month series for a trend line, oldest first.
    out["series"] = [{"period_end": r["period_end"],
                      "sales": _f((r.get("sales") or {}).get("month")),
                      "purchases": _f((r.get("purchases") or {}).get("month"))}
                     for r in reversed(rows)]

    # ---- the last three months, when the history is stored (2026-11-23) --
    # Year-to-date growth averages a whole year of price steps; the last
    # three months against the same three a year before say where the year
    # is now, and purchases against sales over them where the margin is
    # heading -- the cost side, which is where the estimate's error lies
    # (outlook_calibration).
    by_month = {}
    for r in rows:
        try:
            y, mm = (int(p) for p in r["period_end"].split("/")[:2])
        except (KeyError, ValueError):
            continue
        by_month[(y, mm)] = r
    recent = [jalali.add_months(py, pm, -k) for k in range(3)]
    year_before = [(y - 1, mm) for y, mm in recent]
    s_now = [_f((by_month.get(k) or {}).get("sales", {}).get("month"))
             if by_month.get(k) and by_month[k].get("sales") else None for k in recent]
    s_then = [_f((by_month.get(k) or {}).get("sales", {}).get("month"))
              if by_month.get(k) and by_month[k].get("sales") else None for k in year_before]
    out["recent_growth"] = None
    if all(v is not None for v in s_now + s_then) and sum(s_then) > 0:
        out["recent_growth"] = sum(s_now) / sum(s_then) - 1
    p_now = [_f(((by_month.get(k) or {}).get("purchases") or {}).get("month"))
             for k in recent]
    out["recent_purchases_to_sales"] = None
    if all(v is not None for v in p_now + s_now) and sum(s_now) > 0:
        out["recent_purchases_to_sales"] = sum(p_now) / sum(s_now)
    out["months_stored"] = len(by_month)

    # The newest three products' monthly selling rate, oldest first.
    out["product_rates"] = []
    for p in (newest.get("products") or [])[:3]:
        points = []
        for r in reversed(rows):
            for q in r.get("products") or []:
                if q.get("name") == p.get("name"):
                    rate = _f((q.get("month") or {}).get("rate"))
                    if rate:
                        points.append({"period_end": r["period_end"], "rate": rate})
                    break
        if len(points) >= 2:
            out["product_rates"].append({"name": p.get("name"), "unit": p.get("unit"),
                                         "points": points})
    return out


_UNSET = object()


def sold_share(periods, fiscal_year, months, base):
    """The share of fiscal year `fiscal_year`'s sales made by its month
    `months`, or None with nothing to tell it by.

    From that year's 3, 6 and 9-month statements on the annual statement's
    (`base`) own basis, a straight line between them. A sugar maker sells
    most of its year in a few months; "months / 12" put a tenth of the year
    where a third of it was. On the past estimates (emulated: the statement
    in the monthly report's place) the year's own split against months / 12
    was 29.8 against 31.3% three months in, 25.1 against 26.2% at six, 18.7
    against 20.0% at nine (2026-11-25)."""

    total = _f(base["items"].get("revenue")) if base else None
    if not total or total <= 0:
        return None
    knots = {0: 0.0, 12: 1.0}
    for p in periods:
        if p["fy"] != fiscal_year or p["annual"] or p["months"] not in (3, 6, 9) \
                or p["consolidated"] != base["consolidated"]:
            continue
        sold = _f(p["items"].get("revenue"))
        if sold is not None and 0 < sold < total:
            knots.setdefault(p["months"], sold / total)
    at = sorted(knots)
    if len(at) == 2 or any(knots[a] >= knots[b] for a, b in zip(at, at[1:])):
        return None
    k = min(max(int(months or 0), 0), 12)
    for a, b in zip(at, at[1:]):
        if a <= k <= b:
            return knots[a] + (knots[b] - knots[a]) * (k - a) / (b - a)
    return None


def monthly_year_estimate(base_revenue, ytd_growth, months_elapsed,
                          statement_revenue, statement_profit, run_margin,
                          inflation_pct=None, done_share=None):
    """(revenue, profit) of the fiscal year from a monthly report, or None.

    `base_revenue` is the prior fiscal year's; `ytd_growth` the report's own
    year-to-date growth; `statement_revenue` / `statement_profit` the newest
    statement of THIS fiscal year when there is one. `inflation_pct` is the
    centre the months to come are grown toward (config.INFLATION_RATE when
    not given) and `done_share` the share of last year's sales its same
    months held (sold_share; months / 12 without it).

    Since 2026-11-25 the months to come grow by rest_growth(), not at the
    year-to-date rate capped (see REST_GROWTH_WEIGHT: emulated on the past
    estimates, 35.7 to 31.3% three months in, 28.7 to 26.6% at six), and
    the months gone are weighed by what they were of last year, not by
    their count (sold_share).

    Until the review of 2026-11-23 this was prior year x (1 + growth capped
    at +100%), all of it at the trailing margin. Two things were wrong with
    that for the 92 of 117 ranked names it priced:
      * The cap was put on months already SOLD. In a year of 77% inflation
        67 of the 92 had grown more than 100% to date, so their year was
        held to exactly twice the last one -- for some, below the sales they
        had already reported. Now the months elapsed keep the growth they
        had, and only the months to come are capped (the elapsed share is
        months / 12: the prior year's month-by-month split is not known
        here).
      * The profit the company had already REPORTED for the year was thrown
        away and re-estimated from a margin (خودرو: a forward profit on a
        first-half loss). Now the statement's own months stand as reported
        and only the revenue beyond them is put at the trailing margin.
    A year estimated below the revenue the statement already shows is no
    estimate (the monthly total and the statement differ in what they
    count): None, and the statement's own method stands.
    """

    if not base_revenue or base_revenue <= 0 or ytd_growth is None or \
            run_margin is None:
        return None
    done = min(max(int(months_elapsed or 0), 1), 12) / 12.0
    if done_share is not None and 0 < done_share < 1:
        done = done_share
    grown = max(ytd_growth, GROWTH_CAP[0])
    rest = rest_growth(ytd_growth, config.INFLATION_RATE
                       if inflation_pct is None else inflation_pct)
    revenue = base_revenue * (done * (1 + grown) + (1 - done) * (1 + rest))
    if statement_revenue is not None and statement_profit is not None:
        if revenue <= statement_revenue:
            return None
        return revenue, statement_profit + (revenue - statement_revenue) * run_margin
    return revenue, revenue * run_margin


def analyse(ins_code, price=None, periods=12, today=None, rows=None,
            monthly_rows=None, dividend=_UNSET, shares=_UNSET, sector=_UNSET,
            portfolio=_UNSET, opportunity=None, bars=_UNSET, band=_UNSET,
            published=_UNSET, calibration=_UNSET, fx_record=_UNSET,
            capital_events=_UNSET, peers=_UNSET, inflation_pct=None,
            preread=None, holdings=_UNSET, prices_all=_UNSET,
            versions=_UNSET):
    """The full read for one company, or None with nothing stored.

    `rows` (newest first, load_metrics' shape) replaces the store read, and
    `monthly_rows` the monthly-report read; the tests pass hand-built
    periods through them. `dividend` is the last AGM's
    codal_announcements.latest_dividends() entry, `shares` the exchange's
    share count, `sector` the instrument's sector name and `portfolio` its
    newest ن-31 filing; screen() passes all of them so 800 companies cost one
    read each. `opportunity` is opportunity_rate()'s answer. `bars` (the
    newest price bars) and `band` (the snapshot's limits) are trading_read()'s
    inputs; with `rows` given and neither passed there is no trading read --
    a point-in-time valuation must not be judged on today's tape. `published`
    is TSETMC's own P/E, read from the snapshot likewise.

    Added 2026-11-23 (the user: make the forward analysis more accurate and
    useful, "as a professional analyst"), each read from the stores when
    the rows are, and absent otherwise -- a test or a point-in-time read
    passes them or goes without:
      `calibration`     outlook_calibration's record: the range the estimate
                        has historically fallen in, and the dollar slopes;
      `fx_record`       fx.py's stored dollar series, for the scenarios;
      `capital_events`  the company's capital-raise announcements;
      `peers`           valuation_peers(): the industry's and the market's
                        P/E and the company's own P/E history, for the
                        fair-value range.
    `inflation_pct` replaces config.INFLATION_RATE: outlook_calibration
    scores past years with the rate they faced, not today's (the review of
    2026-11-23: at today's 76.8% the "year not yet begun" bucket read 12%
    too low).

    `preread` holds what screen() read for every company at once (added
    2026-11-25: one connection per company per store was most of the
    ranking's 26 s). Its "metrics", "monthly" and "halted" are exactly what
    load_metrics, codal_monthly.load and halted_since would return here,
    so the read is still the store's: unlike `rows`, they change nothing
    else about how the company is read.

    `holdings` (the portfolio's rows, load_portfolio_rows) and `prices_all`
    (latest_prices) value an investment company's or a holding's listed
    portfolio at today's prices (portfolio_value.py, 2026-11-25); read from
    the stores when not given, and absent with `rows`.

    `versions` (the company's releases, pit_fundamentals.load_versions'
    shape) let the trailing year be built across a fiscal-year change on
    one basis (_basis_pool, 2026-11-25); read from the store only for a
    company whose year-end moved, and absent with `rows`.
    """

    rows_from_store = rows is None
    preread = preread or {}
    if calibration is _UNSET:
        calibration = None
        if rows_from_store:
            import outlook_calibration
            calibration = outlook_calibration.load()
    if fx_record is _UNSET:
        fx_record = None
        if rows_from_store:
            import fx
            fx_record = fx.load()
    if capital_events is _UNSET:
        capital_events = (capital_events_for([ins_code]).get(str(ins_code))
                          if rows_from_store else None)
    if peers is _UNSET:
        peers = valuation_peers() if rows_from_store else None
    if rows is None:
        rows = (preread["metrics"] if "metrics" in preread
                else fundamental_db.load_metrics(ins_code, limit=periods))
    if dividend is _UNSET:
        dividend = None
        if rows_from_store:
            import codal_announcements
            dividend = codal_announcements.latest_dividends().get(str(ins_code))
    if shares is _UNSET:
        shares = (exchange_shares().get(str(ins_code))
                  if rows_from_store else None)
    if sector is _UNSET:
        sector = None
        if rows_from_store:
            for inst in database.load_instruments(equities_only=True):
                if str(inst["ins_code"]) == str(ins_code):
                    sector = inst.get("sector_name")
                    break
    if portfolio is _UNSET:
        portfolio = (database.load_codal_portfolio_latest([ins_code])
                     .get(str(ins_code)) if rows_from_store else None)
    if bars is _UNSET:
        bars = (database.load_history_tail([ins_code], TRADING_SESSIONS)
                .get(str(ins_code)) if rows_from_store else None)
    if band is _UNSET:
        band = price_bands().get(str(ins_code)) if rows_from_store else None
    if published is _UNSET:
        published = (published_pe().get(str(ins_code))
                     if rows_from_store else None)
    opportunity = opportunity or opportunity_rate()
    today = today or jalali.today_jalali()
    inflation_rate = (config.INFLATION_RATE if inflation_pct is None
                      else float(inflation_pct))

    # A halted symbol's last close can be months or years old; a P/E on it
    # is not today's valuation (دتهران read 0.02 on a 2018 price). Report
    # the halt, value nothing.
    halted = None
    if price and rows_from_store:
        halted = (preread["halted"] if "halted" in preread
                  else halted_since(ins_code))
    if halted:
        price = None
    all_periods = _periods(rows)

    if not all_periods:
        return None

    # A period stored without an income statement (وتوکا's and غبهنوش's six
    # months: a balance sheet and nothing else) is no reading of earnings: as
    # the newest it left the TTM unbuildable, and the card fell back to an
    # annualised quarter (the full test, 2026-11-12). Skipped here, once, for
    # the card, the scan and the outlook alike; kept when nothing else is.
    with_income = [p for p in all_periods if _has_income(p["items"])]
    skipped_period = None
    if with_income and len(with_income) < len(all_periods):
        if all_periods[0] is not with_income[0]:
            skipped_period = all_periods[0]["end"]
        all_periods = with_income

    ps, basis = choose_basis(all_periods, today)
    kind = FINANCIAL if (sector or "").strip() in \
        config.EARNINGS_MULTIPLE_NOT_MEANINGFUL else OPERATING

    latest = ps[0]
    li = latest["items"]
    m = latest["months"]
    prior = _prior_fy(latest["fy"])

    annual_prev = _find(ps, prior, 12)
    same_prev = _find(ps, prior, m) if m != 12 else annual_prev

    rev = _f(li.get("revenue"))

    # Growth and margins compare TOTAL net profit with total net profit: the
    # backed-out prior figure comes from the filing's own growth ratio, which
    # is computed on total profit. Mixing in the owners' share made a
    # 36% -> 22% "margin collapse" out of a minority interest (سفارس,
    # 2026-11-05). The owners' share is applied only at the per-share step.
    net = _f(li.get("net_profit"))
    owners_share = _div(_profit(li), net) if net and net > 0 else None
    if owners_share is None or not 0 < owners_share <= 1.5:
        owners_share = 1.0

    # Growth is measured against the same KIND of statement. Where a "mixed"
    # series stores last year's period consolidated and this year's separate,
    # the filing's own comparative column -- always its own basis -- is the
    # prior figure: فولاد read +16% on separate-vs-consolidated six months
    # while its monthly reports said +68% (2026-11-10).
    like = same_prev if same_prev and \
        same_prev["consolidated"] == latest["consolidated"] else None

    def _prior(key, growth_key):
        if like:
            return _f(like["items"].get(key))
        backed = _backed_out(latest, key, growth_key)
        if backed is not None or not same_prev:
            return backed
        return _f(same_prev["items"].get(key))

    rev_prev = _prior("revenue", "revenue_growth")
    net_prev = _prior("net_profit", "profit_growth")

    growth_rev = _div(rev, rev_prev)
    growth_rev = None if growth_rev is None or rev_prev <= 0 else growth_rev - 1
    growth_net = _div(net, net_prev)
    growth_net = None if growth_net is None or net_prev <= 0 else growth_net - 1

    margin = _div(net, rev)
    margin_prev = _div(net_prev, rev_prev)

    # ---- TTM and the current fiscal year, estimated -----------------------
    ttm_rev = ttm_net = fwd_rev = fwd_net = None
    method = None

    if m == 12:
        ttm_rev, ttm_net = rev, net
        if rev is not None and net is not None and margin is not None:
            # A whole year not yet begun: inflation, and a part of what last
            # year's growth differed from it by (rest_growth). 167% one year
            # is rarely 167% the next; inflation is what nominal sales drift
            # back to. Without a year before to measure a growth on, it is
            # inflation alone.
            last = growth_rev if growth_rev is not None else inflation_rate / 100.0
            g = rest_growth(last, inflation_rate)
            fwd_rev = rev * (1 + g)
            fwd_net = fwd_rev * margin
            method = "next_year_trend"
    elif annual_prev and same_prev:
        a_rev = _f(annual_prev["items"].get("revenue"))
        a_net = _f(annual_prev["items"].get("net_profit"))
        # Last year's same months as STORED, on the annual's own basis: the
        # remaining months are the annual less them, one basis throughout.
        s_rev = _f(same_prev["items"].get("revenue"))
        s_net = _f(same_prev["items"].get("net_profit"))
        if None not in (net, a_net, s_net):
            ttm_net = net + a_net - s_net
        if None not in (rev, a_rev, s_rev):
            ttm_rev = rev + a_rev - s_rev
            if growth_rev is not None:
                # The months to come do not grow as the months gone did:
                # REST_GROWTH_WEIGHT, measured.
                g = rest_growth(growth_rev, inflation_rate)
                remaining = (a_rev - s_rev) * (1 + g)
                fwd_rev = rev + remaining
                # The remaining months at the TRAILING TWELVE MONTHS' margin,
                # not the year-to-date one: measured on 3,763 past estimates
                # (outlook_calibration, 2026-11-23) the year-to-date margin
                # missed the year's profit by a median 28.2% (40.3% three
                # months in) and the trailing one by 26.7% (36.2%), with half
                # the low bias. A few months' margin carries their one-offs
                # and their season; twelve carry a whole year of both.
                run_margin = (_div(ttm_net, ttm_rev)
                              if ttm_net is not None and ttm_rev and ttm_rev > 0
                              else margin)
                if net is not None and run_margin is not None:
                    fwd_net = net + remaining * run_margin
                    method = "year_completed"

    # ---- no "same months" to net: the statements' own spans (2026-11-25) --
    # Where the formula above cannot be built -- the prior fiscal year was
    # short (short_years), or the store holds last year's months on the
    # other basis only -- the twelve months are built from the spans the
    # statements cover, on the latest's basis alone, the store's other
    # releases filling what `metrics` holds on the other one. First for the
    # companies whose year-end moved; then for every company without a
    # trailing year, once measured: of 180 such on 2026-11-25 it built one
    # for 19, and 13 of the 17 with a P/E to compare were within 10% of
    # TSETMC's own, where the annualised figure they had been given was for
    # one of sixteen.
    moved = len({p["fy"][1] for p in all_periods}) > 1
    across = spanned = False
    across_tried = False
    if m != 12 and ttm_rev is None and ttm_net is None:
        across_tried = moved
        if versions is _UNSET:
            versions = None
            if rows_from_store:
                import pit_fundamentals
                versions = pit_fundamentals.load_versions(ins_code).get(
                    str(ins_code), [])
        pool = _basis_pool(all_periods, latest, versions)
        t_rev = _trailing_across_change(pool, latest, "revenue")
        t_net = _trailing_across_change(pool, latest, "net_profit")
        scale = _div(t_rev, rev * 12 / m) if rev and rev > 0 else None
        if t_net is not None and t_rev and t_rev > 0 and scale is not None \
                and ACROSS_CHANGE_BAND[0] <= scale <= ACROSS_CHANGE_BAND[1]:
            ttm_rev, ttm_net, spanned, across = t_rev, t_net, True, moved
            # Last year's same months, on the same basis, when the pool holds
            # them and the filing's own comparative did not give a growth.
            e = _month_index(latest["end"])
            if growth_rev is None and e is not None:
                before = _flow(pool, e - 12 - m, e - 12, "revenue")
                if before and before > 0:
                    growth_rev = rev / before - 1
            # The months still to come this year are the tail of the twelve
            # (the twelve less this year's own), grown and at the trailing
            # margin as above.
            if growth_rev is not None and net is not None and \
                    not latest["short_year"]:
                g = rest_growth(growth_rev, inflation_rate)
                remaining = (t_rev - rev) * (1 + g)
                fwd_rev = rev + remaining
                fwd_net = net + remaining * (t_net / t_rev)
                method = "year_completed"

    # A short year is a finished year: what follows it is a whole new one,
    # estimated as after any year -- on the twelve months to its end, not
    # on its own few (and never "completed" to a year the company no longer
    # has).
    if latest["short_year"]:
        fwd_rev = fwd_net = method = None
        trailing_margin = (_div(ttm_net, ttm_rev)
                           if ttm_net is not None and ttm_rev and ttm_rev > 0
                           else None)
        if trailing_margin is not None:
            last = growth_rev if growth_rev is not None else inflation_rate / 100.0
            g = rest_growth(last, inflation_rate)
            fwd_rev = ttm_rev * (1 + g)
            fwd_net = fwd_rev * trailing_margin
            method = "next_year_trend"
    short_seen = next((p for p in all_periods if p["short_year"]), None)
    short_year_text = (" (سال منتهی به %s فقط %d ماه بود)"
                       % (short_seen["end"], short_seen["months"])
                       if short_seen else "")

    if fwd_net is None and net is not None and m != 12:
        # No prior year to complete it with: plain annualisation, flagged.
        fwd_rev = rev * 12 / m if rev is not None else None
        fwd_net = net * 12 / m
        method = "annualised"

    # ---- the monthly activity report, when it is newer -------------------
    # A monthly report is ~10 days old; the statement it replaces here is
    # one to four months older. When it is newer, the year's revenue is the
    # prior fiscal year's revenue grown at the year-to-date sales growth the
    # monthly report itself states (this year to date vs the same date last
    # year, both in the same report, so gross-vs-net differences between the
    # monthly total and the statement's revenue cancel in the ratio). Profit
    # is that revenue at the trailing margin.
    if monthly_rows is None and "monthly" in preread:
        monthly_rows = preread["monthly"]
    monthly = monthly_reads(ins_code, latest["fy"][1], monthly_rows)
    if monthly and monthly.get("ytd_growth") is not None and \
            monthly["period_end"] > latest["end"]:
        fy_m = monthly["fiscal_year"]
        base = _find(ps, (fy_m[0] - 1, fy_m[1]), 12)
        base_rev = _f(base["items"].get("revenue")) if base else None
        run_margin = _div(ttm_net, ttm_rev) if ttm_rev and ttm_net is not None \
            else margin
        share_sold = sold_share(ps, (fy_m[0] - 1, fy_m[1]),
                                monthly["months_elapsed"], base)
        estimate = monthly_year_estimate(
            base_rev, monthly["ytd_growth"], monthly["months_elapsed"],
            rev if latest["fy"] == fy_m and m != 12 else None,
            net if latest["fy"] == fy_m and m != 12 else None, run_margin,
            inflation_pct=inflation_rate, done_share=share_sold)
        if estimate:
            fwd_rev, fwd_net = estimate
            method = "monthly_sales"
            # What the months gone were of last year's sales, when the
            # statements told it (None: months / 12).
            monthly = dict(monthly, year_share=share_sold)

    # ---- per share and valuation -----------------------------------------
    # The exchange's share count, not the statement's capital line: that is
    # the capital at the period's end, and a raise since then (or a stale
    # line -- شصفها's read 300 billion rials against 15.3 billion shares)
    # leaves the market value, and every multiple on it, wrong by the ratio.
    capital = _f(li.get("capital"))
    shares = _f(shares)
    shares_source = "exchange"
    if not shares or shares <= 0:
        shares = capital / PAR_VALUE if capital else None
        shares_source = "capital" if shares else None
    # A name the market watch has never listed (133 equities) is valued on
    # its capital line alone, and nothing checks it: کاریز's read 465 billion
    # shares against its AGM's 2.4 billion, and its dividend came out at 0.6
    # rials (the full test, 2026-11-12). The AGM's capital is a second
    # reading; past SHARES_DISAGREE either way the count is not used to
    # rescale the dividend, and in every case the multiple is a doubt.
    agm_shares = None
    if dividend and _f(dividend.get("capital")):
        agm_shares = _f(dividend.get("capital")) * 1_000_000 / PAR_VALUE
    shares_disagree = bool(shares_source == "capital" and shares and agm_shares
                           and not (1 / SHARES_DISAGREE <= shares / agm_shares
                                    <= SHARES_DISAGREE))

    # Owners' part of the group's profit: what a shareholder's EPS is.
    ttm_net_total = ttm_net
    ttm_net = ttm_net * owners_share if ttm_net is not None else None
    fwd_net = fwd_net * owners_share if fwd_net is not None else None

    eps_ttm = _div(ttm_net, shares)
    eps_fwd = _div(fwd_net, shares)

    market_cap = price * shares if price and shares else None
    pe_ttm = _div(market_cap, ttm_net) if ttm_net and ttm_net > 0 else None
    pe_fwd = _div(market_cap, fwd_net) if fwd_net and fwd_net > 0 else None
    yield_fwd = _div(fwd_net, market_cap)

    # Cash dividend of the last annual AGM (ن-52), against today's price.
    dps, dividend_stale = dps_today(dividend, None if shares_disagree else shares,
                                    all_periods)
    dividend_yield = (_div(dps, price) if dps is not None and price
                      and not dividend_stale else None)

    equity = _f(li.get("equity_owners")) or _f(li.get("equity"))
    pb = _div(market_cap, equity) if equity and equity > 0 else None
    roe_ttm = _div(ttm_net, equity) if equity and equity > 0 else None

    # ---- cash, funding, working capital ----------------------------------
    ocf = _f(li.get("operating_cash_flow"))
    capex = _f(li.get("capex"))
    fcf = ocf + capex if ocf is not None and capex is not None else None
    fcf_annual = fcf * 12 / m if fcf is not None else None
    fcf_yield = _div(fcf_annual, market_cap)
    cash_conversion = _div(ocf, net) if net and net > 0 else None

    # Over twelve months where the statements allow: an interim period's
    # cash flow carries the working capital that 77% inflation builds into
    # inventory and receivables mid-year, and on six-month figures 196 of 379
    # ranked companies read as "profit not turning into cash" (2026-11-10).
    cash_conversion_basis = "period" if cash_conversion is not None else None
    if m != 12 and annual_prev and same_prev:
        a_ocf = _f(annual_prev["items"].get("operating_cash_flow"))
        s_ocf = _f(same_prev["items"].get("operating_cash_flow"))
        if None not in (ocf, a_ocf, s_ocf) and ttm_net_total and                 ttm_net_total > 0:
            # Group cash against group profit, not the owners' share of it.
            cash_conversion = (ocf + a_ocf - s_ocf) / ttm_net_total
            cash_conversion_basis = "ttm"

    debt = None
    if li.get("borrowings_short") is not None or li.get("borrowings_long") is not None:
        debt = (_f(li.get("borrowings_short")) or 0.0) + (_f(li.get("borrowings_long")) or 0.0)
    cash = _f(li.get("cash"))
    net_debt = debt - cash if debt is not None and cash is not None else None
    debt_to_equity = _div(debt, equity) if equity and equity > 0 else None

    op = _f(li.get("operating_profit"))
    # Twelve months of operating profit, as revenue and profit are (for
    # EV/EBIT; 2026-11-26, from the owner's walk through BourseView).
    ttm_op = None
    if m == 12 and not latest["short_year"]:
        ttm_op = op
    elif annual_prev and same_prev and op is not None:
        a_op = _f(annual_prev["items"].get("operating_profit"))
        s_op = _f(same_prev["items"].get("operating_profit"))
        if a_op is not None and s_op is not None:
            ttm_op = op + a_op - s_op
    fin = _f(li.get("finance_cost"))
    interest_cover = _div(op, abs(fin)) if fin else None

    rev_annual = rev * 12 / m if rev else None
    receivable_days = _div(_f(li.get("receivables")), rev_annual)
    receivable_days = receivable_days * 365 if receivable_days is not None else None

    rec_growth = None
    if like:
        r0 = _f(like["items"].get("receivables"))
        r1 = _f(li.get("receivables"))
        if r0 and r1 and r0 > 0:
            rec_growth = r1 / r0 - 1

    payout = None
    if annual_prev and m == 12:
        payout = _div(-(_f(li.get("dividends_paid")) or 0.0), _profit(annual_prev["items"]))

    # ---- NAV, for the companies whose earnings are not a valuation ---------
    # The portfolio filing (ن-31) is the parent's own holdings, so its book
    # equity is the parent's: the newest SEPARATE period when there is one.
    nav_result = price_to_nav = price_to_nav_est = nav_move = None
    nav_today = nav_valued = None
    nav_stale = False
    # Every company that files a portfolio, not only those the exchange
    # files under a financial industry: سیدکو, تاصیکو, برکت and خگستر are
    # holdings the exchange lists under their subsidiaries' industries
    # (cement, mining, pharma, autos), and their P/NAV was never computed
    # (2026-11-11). Their ranking stays on earnings; P/NAV is shown beside.
    # An operating company with a small listed portfolio is not valued on
    # it: حکشتی (a shipping line), خکرمان (a car maker, 1.3% of its value
    # listed) and وثخوز (a developer) read P/NAV 3.8-5.7 as book equity (the
    # NAV verification, 2026-11-12). A holding listed under its subsidiaries'
    # industry is (سیدکو, تاصیکو): its listed portfolio is a large part of
    # what it is worth.
    nav_notes = []
    listed_value = _f((portfolio or {}).get("listed_market"))
    holding_like = kind == FINANCIAL or (
        market_cap and listed_value and
        listed_value >= HOLDING_LISTED_SHARE * market_cap)
    if portfolio and holding_like:
        import nav as nav_module
        # The book equity the portfolio's gains are added to is the separate
        # statement NEAREST the portfolio's date: equity from 1405/06 on top of
        # gains marked at 1405/04 counted twice what was realised, or paid out
        # at the AGM, in between (the review panel, 2026-11-11).
        own = _nearest_separate(all_periods, portfolio.get("period_end")) or latest
        own_equity = _f(own["items"].get("equity_owners")) or \
            _f(own["items"].get("equity"))
        nav_result = nav_module.net_asset_value(portfolio, own_equity)
        price_to_nav = nav_module.price_to_nav(market_cap, nav_result)
        # Three reasons the figure may not be what it seems (the NAV
        # verification of 2026-11-12 recomputed 32 portfolios from the raw
        # filings: 25 agreed, these were the rest).
        if (portfolio.get("inconsistent_rows") or 0) > 0:
            nav_notes.append(
                "گزارش پرتفوی ناسازگار است: در %d ردیف سهم مالکیت بیش از ۱۰۰٪ "
                "سرمایه‌ی شرکتِ سرمایه‌پذیر است (خطای تایپی در گزارش)؛ P/NAV "
                "حساب نشد" % portfolio["inconsistent_rows"])
            price_to_nav = None
        gap = _months_between(own.get("end"), portfolio.get("period_end"))
        if price_to_nav is not None and gap is not None and gap > EQUITY_GAP_MONTHS:
            nav_notes.append(
                "حقوق صاحبان سهام شرکت مادر از دوره‌ی %s است، %d ماه پیش از "
                "پرتفوی (صورت جداگانه‌ی تازه‌تر ذخیره نشده)؛ P/NAV تقریبی است"
                % (own.get("end"), gap))
        own_capital = _f(own["items"].get("capital"))
        if price_to_nav is not None and shares and own_capital and \
                shares > own_capital / PAR_VALUE * (1 + RAISE_TOLERANCE):
            nav_notes.append(
                "تعداد سهم امروز %.0f٪ بیشتر از سرمایه‌ی صورت مالی است (افزایش "
                "سرمایه پس از دوره)؛ اگر نقدی بوده، پولش هنوز در حقوق صاحبان "
                "سهام نیست و P/NAV بیش از واقع است"
                % ((shares / (own_capital / PAR_VALUE) - 1) * 100))
        # 130 of 141 portfolios were 1405/04/31 while TEDPIX had risen 72.5%
        # since: every P/NAV was struck on July's prices, by a different
        # amount per name, so the list's ORDER was wrong too. Past
        # NAV_STALE_MONTHS the listed holdings are carried at the index's move
        # -- an estimate, named as one.
        age = _age_months(portfolio.get("period_end"), today)
        if price_to_nav is not None and age is not None and \
                age > NAV_STALE_MONTHS:
            nav_stale = True
            nav_move = index_move_since(portfolio.get("period_end"))
            listed = _f(portfolio.get("listed_market"))
            if nav_move is not None and listed:
                estimate = dict(nav_result,
                                nav=nav_result["nav"] + listed * nav_move)
                price_to_nav_est = nav_module.price_to_nav(market_cap, estimate)

        # Each listed holding at TODAY's price (portfolio_value.py, the
        # owner's request of 2026-11-25), where the filing's rows are held:
        # the index's move above moved a petrochemical holding with the
        # banks. Not on a report whose rows contradict themselves.
        if holdings is _UNSET:
            holdings = None
            if rows_from_store:
                key = (str(ins_code), portfolio.get("period_end"))
                holdings = database.load_portfolio_rows([key]).get(key)
        if holdings and own_equity is not None and \
                not (portfolio.get("inconsistent_rows") or 0):
            import portfolio_value
            got = portfolio_value.value_today(
                portfolio, holdings, own_equity, shares, price,
                latest_prices() if prices_all is _UNSET else prices_all,
                parent=ins_code)
            if got:
                nav_today, nav_valued = got

    # ---- age -------------------------------------------------------------
    age_months = _age_months(latest["end"], today)

    inflation = inflation_rate / 100.0

    # ---- what to watch ---------------------------------------------------
    flags, positives = [], []
    stale_dividend_note = None

    def flag(text):
        flags.append(text)

    if halted:
        flag("نماد از %s معامله نشده (متوقف)؛ قیمت روز ندارد، پس P/E و بازده سود محاسبه نشد"
             % ("مدت‌ها پیش" if halted == "never" else halted))

    if age_months is not None and age_months > STALE_MONTHS:
        flag("آخرین صورت مالی %d ماه پیش تمام شده؛ برآوردها کهنه است" % age_months)
    if basis == "separate_fallback":
        flag("صورت مالی تلفیقیِ تازه هنوز منتشر نشده؛ ارقام فقط شرکت مادر است و "
             "با ارقام گروه قابل مقایسه نیست")
    if net is not None and net < 0:
        flag("زیان‌ده در دوره‌ی اخیر")
    # A moved year-end, said where it changes what the figures mean.
    basis_name = "تلفیقی" if latest["consolidated"] else "شرکت اصلی (جداگانه)"
    if latest["short_year"]:
        flag("آخرین صورت سالانه یک سال مالی کوتاه %d ماهه است (شرکت پایان سال "
             "مالی را تغییر داده)؛ با سال ۱۲ ماهه مقایسه نشد و برآورد سال بعد "
             "بر پایه‌ی ۱۲ ماه منتهی به %s است" % (m, latest["end"]))
    elif across:
        flag("شرکت پایان سال مالی را تغییر داده%s؛ سود ۱۲ ماه گذشته از دو طرف "
             "این تغییر و فقط از صورت‌های %s ساخته شد"
             % (short_year_text, basis_name))
    elif across_tried:
        flag("شرکت پایان سال مالی را تغییر داده%s؛ سود ۱۲ ماه گذشته روی یک "
             "مبنا ساخته نشد، پس P/E دوازده‌ماهه ندارد" % short_year_text)

    # A bank's "revenue" is interest, an investment company's is disposal
    # gains: sales growth, margins, cash conversion, receivables, interest
    # cover and the monthly sales read are an operating company's questions
    # and only noise for these.
    if kind == OPERATING:
        # The monthly report's year-to-date growth, when it is newer, says the
        # same thing more recently -- one flag, not two.
        newer_monthly = bool(monthly and monthly.get("ytd_growth") is not None
                             and monthly["period_end"] > latest["end"])
        if growth_rev is not None and not newer_monthly:
            if growth_rev < inflation:
                flag("رشد فروش %.0f٪ — زیر تورم %.0f٪، یعنی کوچک شدن واقعی"
                     % (growth_rev * 100, inflation * 100))
            else:
                positives.append("رشد فروش %.0f٪، بالاتر از تورم %.0f٪"
                                 % (growth_rev * 100, inflation * 100))
        if margin is not None and margin_prev is not None:
            change = (margin - margin_prev) * 100
            if change <= -3:
                flag("حاشیه سود خالص %.1f واحد افت کرده (%.1f٪ → %.1f٪)"
                     % (-change, margin_prev * 100, margin * 100))
            elif change >= 3:
                positives.append("حاشیه سود خالص %.1f واحد بهتر شده (%.1f٪ → %.1f٪)"
                                 % (change, margin_prev * 100, margin * 100))
        if cash_conversion is not None:
            if cash_conversion < CASH_CONVERSION_LOW:
                flag("فقط %.0f٪ سود %s به نقد عملیاتی تبدیل شده"
                     % (cash_conversion * 100,
                        "۱۲ ماه گذشته" if cash_conversion_basis == "ttm"
                        else "دوره"))
            elif cash_conversion >= 1.0:
                positives.append("نقد عملیاتی بیشتر از سود خالص")
        if rec_growth is not None and growth_rev is not None and                 (rec_growth - growth_rev) * 100 > RECEIVABLE_GAP_PCT:
            flag("مطالبات %.0f٪ رشد کرده در برابر فروش %.0f٪ — فروش نسیه یا وصول کند"
                 % (rec_growth * 100, growth_rev * 100))
        if interest_cover is not None and interest_cover < INTEREST_COVER_LOW:
            flag("سود عملیاتی فقط %.1f برابر هزینه مالی" % interest_cover)
        if fcf is not None and fcf < 0 and net_debt is not None and net_debt > 0:
            flag("جریان نقد آزاد منفی با بدهی خالص — سرمایه‌گذاری با وام")
        if eps_fwd is not None and eps_ttm is not None and eps_ttm > 0 and                 eps_fwd < eps_ttm * 0.9:
            flag("سود برآوردی سال جاری زیر سود ۱۲ ماه گذشته")
    if monthly and kind == OPERATING:
        mg = monthly.get("ytd_growth")
        if mg is not None:
            text = ("فروش ماهانه از ابتدای سال تا %s: %+.0f٪ نسبت به سال قبل"
                    % (monthly["period_end"], mg * 100))
            (positives if mg >= inflation else flags).append(
                text + ("" if mg >= inflation else " — زیر تورم"))
        mom = monthly.get("momentum")
        if mom is not None and monthly["months_elapsed"] >= 3:
            if mom < 0.8:
                flag("فروش ماه آخر %.0f٪ زیر میانگین ماهانه‌ی امسال (کند شدن، یا فصلی)"
                     % ((1 - mom) * 100))
            elif mom > 1.2:
                positives.append("فروش ماه آخر %.0f٪ بالاتر از میانگین ماهانه‌ی امسال"
                                 % ((mom - 1) * 100))
        pg = monthly.get("purchases_growth")
        if pg is not None and mg is not None and (pg - mg) * 100 > 15:
            flag("خرید مواد اولیه %+.0f٪ در برابر فروش %+.0f٪ — فشار بر حاشیه سود"
                 % (pg * 100, mg * 100))
        # Where the year is now, from the stored monthly history.
        rg = monthly.get("recent_growth")
        if rg is not None and mg is not None:
            if (rg - mg) * 100 < -RECENT_GAP_PCT:
                flag("فروش سه ماه اخیر %+.0f٪ نسبت به همان سه ماه سال قبل، کندتر از "
                     "%+.0f٪ ابتدای سال؛ برآوردِ بر پایه‌ی رشد ابتدای سال خوش‌بینانه است"
                     % (rg * 100, mg * 100))
            elif (rg - mg) * 100 > RECENT_GAP_PCT:
                positives.append("فروش سه ماه اخیر %+.0f٪ نسبت به همان سه ماه سال قبل، "
                                 "تندتر از %+.0f٪ ابتدای سال" % (rg * 100, mg * 100))
        rp, ytd_p = monthly.get("recent_purchases_to_sales"), monthly.get("purchases_to_sales")
        if rp is not None and ytd_p is not None and (rp - ytd_p) * 100 > COST_TREND_PTS:
            flag("خرید مواد اولیه در سه ماه اخیر %.0f٪ فروش بوده در برابر %.0f٪ از "
                 "ابتدای سال — حاشیه‌ی ماه‌های آینده زیر فشار است"
                 % (rp * 100, ytd_p * 100))
        for p in monthly.get("products") or []:
            if (p.get("production_to_sales") or 0) > 1.3 and                     (p.get("amount_ytd") or 0) > 0:
                flag("تولید «%s» %.0f٪ بیشتر از فروش آن — انباشت موجودی"
                     % (p["name"], (p["production_to_sales"] - 1) * 100))
                break
    # Why the multiple itself may not be a valuation: each is a warning on
    # the row and, in screen(), a reason not to rank it -- the ranking is by
    # that multiple.
    doubts = []
    if shares_source == "capital":
        flag("تعداد سهم از خط سرمایه‌ی صورت مالی است، نه از بورس (نماد در "
             "دیده‌بان بازار نیامده)%s؛ ارزش بازار و ضریب‌ها تأیید نشده‌اند"
             % (" و با سرمایه‌ی مجمع %.1f برابر فاصله دارد"
                % max(shares / agm_shares, agm_shares / shares)
                if shares_disagree else ""))
        doubts.append({"code": "shares", "label": "تعداد سهم تأییدنشده"})
    if kind == OPERATING:
        # Profit that is not the business's: a P/E on it is not a valuation.
        one_off = one_off_reason(li)
        if one_off:
            flag(one_off)
            doubts.append({"code": "one_off", "label": "سود خارج از فروش"})
        # Our trailing P/E against the exchange's own.
        gap = published_gap_reason(pe_ttm, published)
        if gap:
            flag(gap)
            doubts.append({"code": "published_gap",
                           "label": "P/E ما %.1f، بورس %.1f"
                                    % (pe_ttm, published)})
        # A year projected from its first few months of monthly sales.
        if method == "monthly_sales" and monthly and \
                monthly["months_elapsed"] <= EARLY_MONTHS and \
                fwd_net and ttm_net and ttm_net > 0 and \
                fwd_net >= EARLY_JUMP * ttm_net:
            flag("برآورد سود سال جاری %.1f برابر سود ۱۲ ماه گذشته است و فقط "
                 "بر %d ماه گزارش ماهانه تکیه دارد؛ P/E آینده‌نگر خوش‌بینانه "
                 "است%s"
                 % (fwd_net / ttm_net, monthly["months_elapsed"],
                    " (P/E دوازده‌ماهه %.1f)" % pe_ttm
                    if pe_ttm is not None else ""))
            doubts.append({"code": "early_year",
                           "label": "برآورد سال از %d ماه"
                                    % monthly["months_elapsed"]})
    if monthly:
        ratio = monthly.get("expense_to_income")
        if ratio is not None and monthly.get("template") == "insurance" and ratio > 0.7:
            flag("خسارت پرداختی %.0f٪ حق بیمه‌ی صادره" % (ratio * 100))

    # ---- what today's price asks of the company ---------------------------
    # A shareholder's return is roughly the earnings yield plus the growth of
    # those earnings; bills pay the opportunity rate with neither risk nor
    # growth. So the growth the price REQUIRES is the rate less the earnings
    # yield, and the question is whether the company is growing faster than
    # that. Nominal both sides, so inflation cancels. An approximation -- it
    # assumes earnings reach shareholders and grow at about the sales rate --
    # and labelled as one.
    rate = opportunity["rate_pct"] / 100.0
    required_growth = required_full = cushion = actual_growth = None
    # What reaches the holder is the dividend, not the earnings: a share's
    # return is its dividend yield plus the growth of its price, and the
    # earnings kept back are what pays for that growth -- counting them as
    # yield AND expecting the growth counts them twice. The first version
    # took the whole earnings yield (فولاد "needs 22% growth"; on the 1.1%
    # it pays out it needs about 43%; the review of 2026-11-23). The payout
    # is the last AGM's: DPS x the shares it was declared on / that year's
    # owners' profit. Without a recorded AGM the whole-earnings figure is
    # shown, named as the most favourable case.
    payout_ratio = None
    if dividend and _f(dividend.get("dps")) is not None and \
            _f(dividend.get("capital")):
        fy_end = str(dividend.get("fiscal_year_end") or "")[:7]
        year = next((p for p in all_periods
                     if p["annual"] and str(p["end"])[:7] == fy_end), None)
        owners_then = _profit(year["items"]) if year else None
        if owners_then and owners_then > 0:
            paid = _f(dividend["dps"]) * _f(dividend["capital"]) * 1_000_000 / PAR_VALUE
            payout_ratio = min(max(paid / owners_then, 0.0), 1.0)
    if kind == OPERATING and yield_fwd is not None and yield_fwd > 0:
        required_full = rate - yield_fwd
        required_growth = rate - yield_fwd * (payout_ratio
                                             if payout_ratio is not None else 1.0)
        actual_growth = (monthly.get("ytd_growth") if monthly and
                         monthly.get("ytd_growth") is not None else growth_rev)
        if actual_growth is not None:
            cushion = actual_growth - required_growth
        # A forward yield above the bills' is shown in the hurdle row as a
        # fact; it is not a positive -- the ranking that sorts on it lost to
        # the index (E30), and 8 of 8 names it ticked were outside that
        # ranking anyway (the full test, 2026-11-12).
        if required_growth <= 0:
            pass
        elif cushion is not None and cushion * 100 < -GROWTH_CUSHION_PCT:
            flag("قیمت امروز، در برابر اخزای %.0f٪%s، رشد سالانه‌ی حدود %.0f٪ "
                 "سود را لازم دارد؛ رشد فعلی فروش %.0f٪ است"
                 % (rate * 100,
                    " و با تقسیم سود %.0f٪" % (payout_ratio * 100)
                    if payout_ratio is not None else "",
                    required_growth * 100, actual_growth * 100))
        elif cushion is not None and cushion * 100 > GROWTH_CUSHION_PCT:
            positives.append("رشد فعلی فروش %.0f٪، بیشتر از رشد %.0f٪ که قیمت "
                             "امروز در برابر اخزای %.0f٪ لازم دارد"
                             % (actual_growth * 100, required_growth * 100,
                                rate * 100))
    # No standing tick for being profitable: "✓ earnings yield X%" on every
    # company with a profit read as clearance to buy, and E30 tested exactly
    # this yield as a pick-list and rejected it (the review panel, 2026-11-11).
    if kind == FINANCIAL:
        flag("بانک، بیمه، سرمایه‌گذاری یا هلدینگ: P/E و رشد فروش معیار "
             "ارزش‌گذاری نیست؛ با P/NAV یا P/B و ROE سنجیده می‌شود")
        # Valued on NAV, so what weakens the NAV is a warning on the row; an
        # operating holding's notes stay beside its P/NAV, not among the
        # warnings its earnings ranking counts.
        for note in nav_notes:
            flag(note)
        today_ptn = (nav_today or {}).get("price_to_nav")
        if today_ptn is not None:
            (positives if today_ptn < 1 else flags).append(
                "P/NAV به قیمت امروز سهام پرتفوی %.2f — %s (پرتفوی %s، %d سهم "
                "بورسی%s)" % (
                    today_ptn,
                    "زیر ارزش خالص دارایی‌ها" if today_ptn < 1
                    else "بالاتر از ارزش خالص دارایی‌ها",
                    portfolio.get("period_end"), nav_today["holdings"],
                    "؛ %d سهم بی‌قیمت به ارزش پایان دوره" % nav_today["unpriced"]
                    if nav_today["unpriced"] else ""))
            if (nav_today.get("unlisted_share_pct") or 0) >= 30:
                flag("%.0f٪ ارزش خالص دارایی‌ها سهام غیربورسی به بهای تمام‌شده "
                     "است؛ ارزش واقعی‌شان معلوم نیست و NAV یک کف است"
                     % nav_today["unlisted_share_pct"])
        elif nav_stale:
            flag("پرتفوی ثبت‌شده مال %s است و شاخص کل از آن زمان %s تغییر کرده؛ "
                 "P/NAV به قیمت‌های آن روز %.2f است%s"
                 % (portfolio.get("period_end"),
                    "%+.0f٪" % (nav_move * 100) if nav_move is not None else "نامعلوم",
                    price_to_nav,
                    " و اگر سهام بورسی‌اش هم‌پای شاخص رفته باشد حدود %.2f (تخمینی)"
                    % price_to_nav_est if price_to_nav_est is not None else ""))
        elif price_to_nav is not None:
            (positives if price_to_nav < 1 else flags).append(
                "P/NAV %.2f — %s" % (price_to_nav,
                                     "زیر ارزش خالص دارایی‌ها" if price_to_nav < 1
                                     else "بالاتر از ارزش خالص دارایی‌ها"))
    if dividend_yield is not None and dps > 0:
        positives.append("سود نقدی مجمع سال %s: %s ریال هر سهم (به تعداد سهم امروز) — "
                         "بازده نقدی %.1f٪"
                         % (dividend["fiscal_year_end"], format(int(round(dps)), ","),
                            dividend_yield * 100))
    elif dividend_stale and dps:
        # Said as a notice (below, once `result` exists): a gap in what is
        # stored, not a weakness of the company -- as a warning it counted
        # against 34 names in the ranking (the review of 2026-11-23).
        stale_dividend_note = (
            "آخرین سود نقدی ثبت‌شده مال سال مالی %s است و مجمع سال بعد ثبت "
            "نشده؛ بازده نقدی امروز حساب نشد" % dividend["fiscal_year_end"])

    result = {
        "ins_code": str(ins_code),
        "kind": kind,
        "basis": basis,
        "latest": {"period_end": latest["end"], "months": m,
                   "audited": latest["audited"], "published": latest["published"],
                   "age_months": age_months,
                   "consolidated": latest["consolidated"],
                   "short_year": latest["short_year"]},
        "skipped_period": skipped_period,
        # The short fiscal year the read had to work around (short_years),
        # and whether the trailing twelve months were built across it.
        "short_year": ({"period_end": short_seen["end"],
                        "months": short_seen["months"]}
                       if short_seen and (latest["short_year"] or across_tried)
                       else None),
        "ttm_across_change": across,
        # The trailing year was built from the statements' spans on one
        # basis (_flow), not by the usual formula.
        "ttm_from_spans": spanned,
        "has_prior_year": bool(annual_prev),
        "has_same_period": bool(same_prev),
        "method": method,
        "growth": {"revenue_pct": _pct(growth_rev), "profit_pct": _pct(growth_net),
                   "inflation_pct": round(inflation * 100, 1),
                   "inflation_point_pct": config.INFLATION_POINT_RATE,
                   "inflation_as_of": config.INFLATION_AS_OF},
        "margins": {"net_pct": _pct(margin), "net_prev_pct": _pct(margin_prev),
                    "gross_pct": _pct(_div(li.get("gross_profit"), rev)),
                    "operating_pct": _pct(_div(op, rev))},
        "ttm": {"revenue": ttm_rev, "profit": ttm_net, "eps": eps_ttm,
                "operating": ttm_op},
        "forward": {"revenue": fwd_rev, "profit": fwd_net, "eps": eps_fwd},
        "valuation": {"price": price, "market_cap": market_cap,
                      "pe_ttm": _round(pe_ttm), "pe_forward": _round(pe_fwd),
                      "pe_published": published,
                      "earnings_yield_forward_pct": _pct(yield_fwd),
                      "pb": _round(pb), "roe_ttm_pct": _pct(roe_ttm),
                      "fcf_yield_pct": _pct(fcf_yield),
                      # The multiples BourseView shows beside P/E: sales,
                      # the enterprise (debt less cash added) on operating
                      # profit, and free cash flow, each over twelve months
                      # where the statements give twelve.
                      "ps": _round(_div(market_cap, ttm_rev)
                                   if ttm_rev and ttm_rev > 0 else None),
                      "ev_ebit": _round(_div(market_cap + (net_debt or 0.0), ttm_op)
                                        if market_cap and ttm_op and ttm_op > 0
                                        else None),
                      "p_fcf": _round(_div(market_cap, fcf_annual)
                                      if market_cap and fcf_annual and fcf_annual > 0
                                      else None),
                      "book_equity": equity,
                      "net_debt": net_debt,
                      "dps": dps,
                      "dividend_fiscal_year": (dividend or {}).get("fiscal_year_end"),
                      "dividend_yield_pct": _pct(dividend_yield),
                      "dividend_stale": dividend_stale,
                      "shares": shares, "shares_source": shares_source,
                      "price_to_nav": _round(price_to_nav),
                      "price_to_nav_est": _round(price_to_nav_est),
                      "nav_stale": nav_stale,
                      "nav_index_move_pct": _pct(nav_move),
                      "nav_notes": nav_notes,
                      "nav": (nav_result or {}).get("nav"),
                      "nav_complete": (nav_result or {}).get("complete"),
                      "price_to_nav_today": _round((nav_today or {}).get("price_to_nav")),
                      "nav_today": nav_today,
                      "nav_forward": None,
                      "nav_period": (portfolio or {}).get("period_end")
                      if nav_result else None},
        "hurdle": {"rate_pct": opportunity["rate_pct"],
                   "as_of": opportunity["as_of"],
                   "source": opportunity["source"],
                   "required_growth_pct": _pct(required_growth),
                   "required_growth_full_payout_pct": _pct(required_full),
                   "payout_pct": _pct(payout_ratio),
                   "payout_year": (dividend or {}).get("fiscal_year_end")
                   if payout_ratio is not None else None,
                   "actual_growth_pct": _pct(actual_growth),
                   "cushion_pct": _pct(cushion)},
        "cash": {"cash_conversion": _round(cash_conversion),
                 "cash_conversion_basis": cash_conversion_basis, "fcf": fcf,
                 "capex": capex, "payout_pct": _pct(payout)},
        "balance": {"debt": debt, "net_debt": net_debt,
                    "debt_to_equity": _round(debt_to_equity),
                    "interest_cover": _round(interest_cover),
                    "receivable_days": _round(receivable_days, 0),
                    "receivable_growth_pct": _pct(rec_growth)},
        "monthly": monthly,
        "flags": flags,
        "positives": positives,
        "doubts": doubts,
        "notices": [stale_dividend_note] if stale_dividend_note else [],
    }

    # ---- 2026-11-23: what a professional reads beside the estimate ---------
    # The discrete quarters (the cumulative filings differenced), the range
    # the estimate has historically fallen in, the dollar what-ifs, a
    # capital raise under way, and a fair-value range. Each is analysis for
    # the reader; none changes the ranking's order.
    result["quarters"] = quarterly_series(all_periods)

    ttm_gross = None
    gp = _f(li.get("gross_profit"))
    if m == 12:
        ttm_gross = gp
    elif annual_prev and same_prev and gp is not None:
        a_gp = _f(annual_prev["items"].get("gross_profit"))
        s_gp = _f(same_prev["items"].get("gross_profit"))
        if a_gp is not None and s_gp is not None:
            ttm_gross = gp + a_gp - s_gp
    gross_margin = (_div(ttm_gross, ttm_rev) if ttm_gross is not None and ttm_rev
                    else _div(gp, rev))
    ttm_margin = (_div(ttm_net_total, ttm_rev)
                  if ttm_net_total is not None and ttm_rev and ttm_rev > 0
                  else margin)
    months_known = (monthly["months_elapsed"] if method == "monthly_sales" and monthly
                    else 12 if method == "next_year_trend" else m)

    estimate_range = None
    if method in ("year_completed", "next_year_trend", "monthly_sales") and \
            fwd_net and fwd_net > 0:
        estimate_range = calibrated_range(calibration, months_known, ttm_margin,
                                          fwd_net, shares, price, method)
    result["forward"]["range"] = estimate_range
    if estimate_range and estimate_range["wide"] and kind == OPERATING:
        # A notice, not a warning that counts against the company: it is
        # about how far the estimator can be trusted, not about the business
        # (30 names were dropped from the ranking for it; the review of
        # 2026-11-23).
        result["notices"].append(
            "برآورد سود این‌گونه شرکت‌ها (حاشیه‌ی سود %s) در گذشته پراکنده "
            "بوده: در نیمی از موارد سود واقعی سال بین %.0f٪ و %.0f٪ برآورد "
            "بود؛ عدد برآوردی را با احتیاط بخوانید"
            % (estimate_range["band_label"] or "نامعلوم",
               estimate_range["q25"] * 100, estimate_range["q75"] * 100))

    if kind == OPERATING:
        if method == "monthly_sales" and monthly and fwd_rev:
            remaining_rev = fwd_rev * (12 - monthly["months_elapsed"]) / 12.0
        elif method == "next_year_trend":
            remaining_rev = fwd_rev
        elif fwd_rev is not None and rev is not None:
            remaining_rev = fwd_rev - rev
        else:
            remaining_rev = None
        pretax = _f(li.get("pretax"))
        keep = _div(net, pretax) if net and pretax and pretax > 0 else None
        # The year being estimated, for "the rate against this year's
        # average": the latest statement's year, or the one after it when
        # that statement closed its year (اکو read +44.8% against an
        # average that included the whole closed year; the same review).
        if method == "monthly_sales" and monthly:
            forecast_fy = tuple(monthly["fiscal_year"])
        elif method == "next_year_trend":
            forecast_fy = (latest["fy"][0] + 1, latest["fy"][1])
        else:
            forecast_fy = latest["fy"]
        result["fx"] = dollar_scenarios(
            ins_code, sector, calibration, fx_record, remaining_rev,
            gross_margin, keep, owners_share, fwd_net, shares, price,
            forecast_fy, net_margin=ttm_margin)
    else:
        result["fx"] = None

    capital = capital_reading(capital_events)
    result["capital"] = capital
    if capital:
        result["notices"].append(capital["text"])

    result["fair_value"] = fair_value(
        kind, ins_code, sector, eps_ttm, eps_fwd, estimate_range, price, peers,
        nav_result=nav_result, nav_stale=nav_stale,
        nav_estimate=(price_to_nav_est and market_cap
                      and market_cap / price_to_nav_est),
        shares=shares, doubts=doubts, nav_today=nav_today)

    # The portfolio row by row, at today's prices; attach_forward() adds
    # each holding's fundamental value once the other companies are read.
    if nav_valued:
        import portfolio_value
        result["holdings"] = portfolio_value.top(nav_valued)
        result["_valued"] = nav_valued

    # What the tape says about the price every multiple above rests on. First
    # among the warnings: a P/E on a price nobody can deal at is not a
    # valuation, however right the filing.
    trading = trading_read(bars, band) if price and not halted else None
    result["trading"] = trading
    if trading and trading["notes"]:
        flags[:0] = trading["notes"]

    suspect = suspect_reason(result)
    result["suspect"] = suspect
    if suspect:
        flags.insert(0, "داده‌ی ناممکن (%s): به احتمال زیاد صورت مالی درست "
                        "خوانده نشده؛ دستی بررسی کنید" % suspect)
        # Its "strengths" are the same misread (فارس: "sales +83179%, above
        # inflation").
        del positives[:]
    return result


def _round(x, digits=2):
    return None if x is None else round(x, digits)


# ---- 2026-11-23: the professional's additions -----------------------------

QUARTERS_SHOWN = 12
FA_QUARTER = {3: "فصل ۱", 6: "فصل ۲", 9: "فصل ۳", 12: "فصل ۴"}


def quarterly_series(periods, limit=QUARTERS_SHOWN):
    """Discrete quarters, oldest first: each cumulative filing less the one
    three months before it, ON THE SAME BASIS.

    The standard Iranian read ("سود فصل به فصل"): a 9-month statement says
    little about its third quarter until the 6-month one is taken away. A
    quarter is left out when its two filings are of different bases (347
    companies file separate at 3 and 9 months, consolidated at 6 and 12):
    their difference would be two businesses, not three months.
    """

    by_key = {}
    for p in periods:
        if p["months"] in (3, 6, 9, 12):
            by_key.setdefault((p["fy"], p["months"], p["consolidated"]), p)
    out = []
    seen = set()
    keys = sorted({(fy, m) for fy, m, _c in by_key}, reverse=True)
    for fy, m in keys:
        if (fy, m) in seen:
            continue
        for cons in (True, False):
            cur = by_key.get((fy, m, cons))
            prev = by_key.get((fy, m - 3, cons)) if m > 3 else None
            if not cur or (m > 3 and not prev):
                continue

            def part(key, cur=cur, prev=prev):
                a = _f(cur["items"].get(key))
                if prev is None:
                    return a
                b = _f(prev["items"].get(key))
                return None if a is None or b is None else a - b

            revenue = part("revenue")
            if revenue is None:
                continue
            net = part("net_profit")
            out.append({"fiscal_year": "%d/%02d" % fy, "quarter": m // 3,
                        "label": "%s %d" % (FA_QUARTER[m], fy[0]),
                        "period_end": cur["end"], "consolidated": cons,
                        "revenue": revenue, "gross_profit": part("gross_profit"),
                        "operating_profit": part("operating_profit"),
                        "net_profit": net,
                        "net_margin_pct": _pct(_div(net, revenue))
                        if revenue and revenue > 0 else None})
            seen.add((fy, m))
            break
        if len(out) >= limit:
            break
    out.reverse()
    # The same quarter a year before, for the growth a reader looks for first.
    index = {(q["fiscal_year"], q["quarter"]): q for q in out}
    for q in out:
        y, mm = (int(x) for x in q["fiscal_year"].split("/"))
        before = index.get(("%d/%02d" % (y - 1, mm), q["quarter"]))
        q["revenue_yoy_pct"] = (_pct(q["revenue"] / before["revenue"] - 1)
                                if before and before["revenue"] and before["revenue"] > 0
                                and before["consolidated"] == q["consolidated"]
                                else None)
    return out


def calibrated_range(calibration, months_known, margin, profit, shares, price,
                     method=None):
    """Where the year's profit has landed against estimates like this one,
    or None without a calibration (outlook_calibration.py)."""

    import outlook_calibration

    b = outlook_calibration.range_for(calibration, months_known, margin, method)
    if not b or not profit or profit <= 0:
        return None
    out = {"q10": b["q10"], "q25": b["q25"], "q50": b["q50"], "q75": b["q75"],
           "q90": b["q90"], "n": b["n"], "key": b["key"], "basis": b["basis"],
           "months_known": int(months_known or 0),
           "months_bucket": b["months_bucket"], "band": b["band"],
           "band_label": outlook_calibration.MARGIN_LABELS.get(b["band"]),
           "median_abs_error_pct": _pct(b.get("median_abs_error")),
           # 2026-11-24: what the band was measured on ("pit": decided the
           # day the statement was first published, scored against the
           # year's first release; "restated": the latest releases), the
           # fiscal years behind it, and how it held on years left out of
           # it (outlook_calibration.range_for; None where not measured).
           "source": b.get("source") or "restated", "years": b.get("years"),
           "coverage": b.get("coverage"), "coverage80": b.get("coverage80"),
           "wide": (b["q75"] - b["q25"]) > outlook_calibration.WIDE_BAND,
           "profit_low": profit * b["q25"], "profit_high": profit * b["q75"],
           "profit_low80": profit * b["q10"], "profit_high80": profit * b["q90"]}
    for key in ("low", "high", "low80", "high80"):
        out["eps_" + key] = _div(out["profit_" + key], shares)
    out["pe_low"] = (_round(price / out["eps_high"])
                     if price and out["eps_high"] and out["eps_high"] > 0 else None)
    out["pe_high"] = (_round(price / out["eps_low"])
                      if price and out["eps_low"] and out["eps_low"] > 0 else None)
    return out


FX_SCENARIOS = (-0.20, 0.20, 0.40)
# Share of a pre-tax change that reaches net profit when the filing does not
# say: the 25% corporate rate, less the exemptions most listed exporters use.
FX_DEFAULT_KEEP = 0.8
FX_WEAK_R2 = 0.10


def dollar_scenarios(ins_code, sector, calibration, fx_record, remaining_rev,
                     gross_margin, keep, owners_share, forward_profit, shares,
                     price, fiscal_year_end, net_margin=None):
    """The year's profit if the exchange-centre dollar moves, or None.

    For the months still to come, sales move by the company's measured
    slope against the NIMA rate and the cost of sales by its own:
        sales    x ((1 + s) ** slope_sales - 1)
        cost     x ((1 + s) ** slope_cost  - 1)
    the difference taxed at the filing's own net/pre-tax ratio. The slopes
    come from outlook_calibration (quarter on same quarter a year before,
    shrunk toward the industry's) and the fit is weak in every industry, so
    this is a what-if with its R² beside it -- the base estimate is not
    moved by it.
    """

    import fx
    import outlook_calibration

    sens = outlook_calibration.sensitivity_for(calibration, ins_code, sector)
    now = fx.latest(fx_record) if fx_record else {}
    if not sens or "nima" not in now:
        return None
    day, rate = now["nima"]
    out = {"as_of": day, "nima": rate,
           "free": now.get("free", (None, None))[1],
           "sensitivity": sens, "weak": (sens.get("r2") or 0) < FX_WEAK_R2,
           "remaining_revenue": remaining_rev, "scenarios": []}
    if fiscal_year_end:
        start = jalali.add_months(fiscal_year_end[0], fiscal_year_end[1], -11)
        avg = fx.average("%04d/%02d/01" % start, day, "nima", fx_record)
        out["year_average"] = avg
        out["vs_year_average_pct"] = _pct(rate / avg - 1) if avg else None
    if not remaining_rev or remaining_rev <= 0 or forward_profit is None or \
            gross_margin is None:
        return out
    cost_ratio = min(max(1 - gross_margin, 0.0), 1.2)
    keep = keep if keep and 0.5 <= keep <= 1.0 else FX_DEFAULT_KEEP
    out["cost_ratio"] = round(cost_ratio, 3)
    out["keep"] = round(keep, 3)

    def change(s):
        d_sales = remaining_rev * ((1 + s) ** sens["revenue"] - 1)
        d_cost = remaining_rev * cost_ratio * ((1 + s) ** sens["cost"] - 1)
        return (d_sales - d_cost) * keep * owners_share

    # Beside the measured slope, the reading for a business whose every
    # price and cost is struck in dollars: the remaining months' profit moves
    # one for one. The measured slopes rank refiners (0.25) below pharma
    # (0.86) -- world prices moved against the rate in the sample -- so for a
    # dollar-priced industry this second column is the one to read, and the
    # table shows both rather than one EPS to a decimal. Not a ceiling: a
    # company whose costs move less than its sales can exceed it.
    remaining_profit = (remaining_rev * net_margin * owners_share
                        if net_margin is not None else None)
    for s in FX_SCENARIOS:
        profit = forward_profit + change(s)
        eps = _div(profit, shares)
        full = (forward_profit + remaining_profit * s
                if remaining_profit is not None else None)
        out["scenarios"].append({
            "shock_pct": round(s * 100), "nima": rate * (1 + s),
            "profit": profit, "eps": eps,
            "change_pct": _pct(_div(profit - forward_profit, abs(forward_profit)))
            if forward_profit else None,
            "full_eps": _div(full, shares),
            "full_change_pct": _pct(_div(full - forward_profit, abs(forward_profit)))
            if forward_profit and full is not None else None,
            "pe": _round(price / eps) if price and eps and eps > 0 else None})
    out["per_10pct_pct"] = (_pct(change(0.10) / abs(forward_profit))
                            if forward_profit else None)
    out["full_pass_per_10pct_pct"] = (
        _pct(remaining_profit * 0.10 / abs(forward_profit))
        if forward_profit and remaining_profit is not None else None)
    return out


# Capital-raise announcements (codal_announcements kind "capital"), latest
# stage first. Titles never name the source; a rights offering (پذیره‌نویسی,
# حق تقدم) is the one stage that says the money is the shareholders' cash.
CAPITAL_STAGES = (
    ("registered", ("ثبتافزایشسرمایه",), "ثبت شده (انجام شد)"),
    ("rights_result", ("فروشحقتقدم",), "فروش حق تقدم‌های استفاده‌نشده"),
    ("subscription", ("پذیرهنویسی",), "پذیره‌نویسی (افزایش سرمایه نقدی)"),
    ("egm_decided", ("تصمیماتمجمععمومیفوقالعاده",), "مجمع فوق‌العاده تصمیم گرفت"),
    ("egm_called", ("دعوتبهمجمععمومیفوقالعاده",), "مجمع فوق‌العاده دعوت شد"),
    ("submitted", ("مدارکومستندات",), "مدارک به سازمان بورس رفت"),
    ("audited", ("اظهارنظرحسابرس",), "حسابرس گزارش توجیهی را دید"),
    ("proposed", ("پیشنهادهییتمدیره", "پیشنهادهیاتمدیره"), "پیشنهاد هیئت مدیره"),
    ("board_meeting", ("زمانتشکیلجلسههییتمدیره", "زمانتشکیلجلسههیاتمدیره"),
     "جلسه‌ی هیئت مدیره اعلام شد"),
)
CAPITAL_MONTHS = 12
CAPITAL_EGM_ONLY = ("egm_decided", "egm_called")


def capital_events_for(ins_codes, months=CAPITAL_MONTHS):
    """{ins_code: [(published_at, title)]} newest first, of the last
    `months` months' capital-raise announcements."""

    import fundamental_db

    today = jalali.today_jalali()
    since = "%04d/%02d/%02d" % (jalali.add_months(today[0], today[1], -months)
                                + (today[2],))
    out = {}
    conn = fundamental_db._connect()
    try:
        wanted = [str(c) for c in ins_codes] if ins_codes else None
        sql = ("SELECT ins_code, published_at, title FROM announcements "
               "WHERE kind = 'capital' AND published_at >= ?")
        args = [since]
        if wanted and len(wanted) < 500:
            sql += " AND ins_code IN (%s)" % ",".join("?" * len(wanted))
            args += wanted
        rows = conn.execute(sql + " ORDER BY published_at DESC", args).fetchall()
    except Exception:                                           # noqa: BLE001
        rows = []
    finally:
        conn.close()
    for code, published, title in rows:
        out.setdefault(str(code), []).append((published, title))
    return out


def capital_reading(events):
    """The newest stage of a capital raise, in words, or None."""

    if not events:
        return None
    import persian

    def flat(text):
        return "".join(persian.normalize(str(text or "")).replace("‌", " ").split())

    stages = []
    for published, title in events:
        f = flat(title).replace("ي", "ی").replace("ك", "ک").replace("ئ", "ی")
        for key, needles, label in CAPITAL_STAGES:
            if any(n in f for n in needles):
                stages.append((published, key, label, title))
                break
    # ن-56/ن-57 are ANY extraordinary meeting -- the title carries no agenda
    # -- so a meeting with no capital-specific letter beside it is not read
    # as a raise: 50 names had been given the notice for an EGM alone (the
    # review of 2026-11-23).
    if not any(key not in CAPITAL_EGM_ONLY for _p, key, _l, _t in stages):
        return None
    newest = max(stages, key=lambda s: s[0] or "")
    # Cash is read from the round still open: a subscription that ended in a
    # registration belongs to the raise before, not to a new proposal.
    registered = [p or "" for p, key, _l, _t in stages if key == "registered"]
    opened = max(registered) if registered and newest[1] != "registered" else ""
    cash = any(key in ("subscription", "rights_result") and (p or "") >= opened
               for p, key, _l, _t in stages)
    done = newest[1] == "registered"
    text = ("افزایش سرمایه: %s (%s)." % (newest[2], (newest[0] or "")[:10]))
    if done:
        text += " تعداد سهم بورس پس از ثبت به‌روز می‌شود؛ سود هر سهم با تعداد تازه حساب شده است."
    elif cash:
        text += (" نقدی است (پذیره‌نویسی): پول تازه وارد شرکت می‌شود و تا به سود برسد، "
                 "سود هر سهم با تعداد بیشتر سهم رقیق می‌شود.")
    else:
        text += (" منبع در عنوان اطلاعیه نیست: اگر از سود انباشته یا تجدید ارزیابی باشد "
                 "فقط تعداد سهم و قیمت تعدیل می‌شود؛ اگر نقدی باشد سود هر سهم رقیق می‌شود.")
    return {"stage": newest[1], "stage_label": newest[2], "date": newest[0],
            "cash": cash, "done": done, "text": text,
            "events": [{"date": p, "title": t} for p, t in events[:8]]}


# Fair value. The company's own P/E is read over this many of
# market_valuation's points (every 21 sessions: about a year), not its whole
# 2022-onward history: at 40-80% inflation trailing P/Es drift down as
# nominal profit grows, and E33 found whole industries "cheap against their
# own history" for that reason alone.
OWN_PE_POINTS = 12
OWN_PE_MIN_POINTS = 6
PEER_PE_MAX = 60.0
_PEERS = {"at": None, "value": None}
PEERS_TTL = 600.0


def valuation_peers():
    """{sector_pe, sector_n, market_pe, market_as_of, own_pe} for fair_value(),
    re-read after PEERS_TTL seconds."""

    import time as _time

    now = _time.monotonic()
    if _PEERS["at"] is not None and now - _PEERS["at"] < PEERS_TTL:
        return _PEERS["value"]
    sectors = {}
    for inst in database.load_instruments(equities_only=True):
        sectors[str(inst["ins_code"])] = (inst.get("sector_name") or "").strip()
    by_sector = {}
    for code, pe in published_pe().items():
        if 0 < pe <= PEER_PE_MAX and sectors.get(code):
            by_sector.setdefault(sectors[code], []).append(pe)
    value = {"sector_pe": {k: _round(_median(v)) for k, v in by_sector.items()
                           if len(v) >= SECTOR_PEERS_MIN},
             "sector_n": {k: len(v) for k, v in by_sector.items()},
             "market_pe": None, "market_as_of": None, "own_pe": {}}
    try:
        import market_valuation
        points = (market_valuation.load() or {}).get("points") or []
    except Exception:                                           # noqa: BLE001
        points = []
    if points:
        value["market_pe"] = points[-1].get("pe")
        value["market_as_of"] = points[-1].get("date")
        history = {}
        for p in points[-OWN_PE_POINTS:]:
            for code, pair in (p.get("by_company") or {}).items():
                try:
                    cap, profit = float(pair[0]), float(pair[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if cap > 0 and profit > 0 and cap / profit <= PEER_PE_MAX:
                    history.setdefault(code, []).append((p.get("date"), cap / profit))
        for code, pts in history.items():
            if len(pts) >= OWN_PE_MIN_POINTS:
                pes = sorted(x[1] for x in pts)
                value["own_pe"][code] = {"median": _round(_median(pes)),
                                         "low": _round(pes[0]), "high": _round(pes[-1]),
                                         "n": len(pts), "from": pts[0][0],
                                         "to": pts[-1][0]}
    _PEERS.update(at=now, value=value)
    return value


def fair_value(kind, ins_code, sector, eps_ttm, eps, estimate_range, price,
               peers, nav_result=None, nav_stale=False, nav_estimate=None,
               shares=None, doubts=(), nav_today=None):
    """A value range from reference multiples, or None.

    Operating companies. The reference multiples -- the industry's median
    P/E on the exchange, the share's own median over the past year, the
    whole market's -- are all TRAILING twelve-month P/Es, so they are put on
    the trailing EPS: what the share would be priced at on today's profit
    if it traded like them. The industry's is the central one (a refiner is
    not priced like the market); the range runs from the lowest multiple to
    the highest. A second reading puts the same multiples on the year's
    estimated EPS (its calibrated range at the ends): the value IF the
    estimate comes true and the multiples hold. A first version put the
    trailing multiples on the forward EPS alone and read شتران +172%: in a
    year of 77% inflation that counts the year's growth twice.

    Financials: the NAV per share. Either way a reading of where reference
    multiples put the price, not a target: the rankings built on these
    numbers did not beat the index (E30-E33).
    """

    if kind == FINANCIAL:
        # Today's prices for the listed holdings where the rows are held
        # (portfolio_value); else the filing's, moved with the index.
        today = (nav_today or {}).get("nav")
        nav = today or (nav_estimate if nav_stale and nav_estimate else
                        (nav_result or {}).get("nav"))
        if not nav or not shares or nav <= 0:
            return None
        per_share = nav / shares
        # An incomplete NAV (unlisted holdings at cost: every one of the 49
        # stored on 2026-11-23) is a FLOOR, and holdings trade at a discount
        # to NAV as a rule: shown as the price's share of it, never as an
        # "upside" (شستا had read +36.6%, سلیم +131%; the same review).
        floor = (nav_result or {}).get("complete") is False
        return {"basis": "nav", "central": per_share, "low": None, "high": None,
                "anchors": [{"label": "ارزش خالص دارایی هر سهم" +
                             (" (پرتفوی بورسی به قیمت امروز)" if today else
                              " (تخمینی با حرکت شاخص)" if nav_stale else ""),
                             "multiple": None, "value": per_share}],
                "floor": floor,
                "price_to_value": _round(price / per_share) if price else None,
                "upside_pct": None,
                "doubtful": bool(doubts), "forward": None,
                "central_multiple": None, "central_basis": "nav"}
    if not eps_ttm or eps_ttm <= 0 or not peers:
        return None
    anchors = []
    s_pe = (peers.get("sector_pe") or {}).get((sector or "").strip())
    if s_pe:
        anchors.append({"key": "sector",
                        "label": "میانه‌ی P/E صنعت (%d نماد، بورس)"
                        % (peers.get("sector_n") or {}).get((sector or "").strip(), 0),
                        "multiple": s_pe})
    own = (peers.get("own_pe") or {}).get(str(ins_code))
    if own:
        anchors.append({"key": "own", "label": "P/E خود سهم در یک سال گذشته (میانه)",
                        "multiple": own["median"]})
    if peers.get("market_pe") and 0 < peers["market_pe"] <= PEER_PE_MAX:
        anchors.append({"key": "market", "label": "P/E کل بازار",
                        "multiple": peers["market_pe"]})
    if not anchors:
        return None
    for a in anchors:
        a["value"] = eps_ttm * a["multiple"]
    multiples = sorted(a["multiple"] for a in anchors)
    central_multiple = anchors[0]["multiple"]       # the industry's, if there is one
    central = eps_ttm * central_multiple
    # An exchange industry code is not a peer group (فلزات اساسی is 49
    # names, copper beside steel): where the industry's multiple and the
    # share's own are far apart the central value is named as unsettled
    # (فملی read -35% on the industry, +11% on itself; 37 of 98 ranked
    # names were outside the band).
    disagree = bool(s_pe and own and not
                    PUBLISHED_PE_BAND[0] <= own["median"] / s_pe <= PUBLISHED_PE_BAND[1])
    # No forward reading: trailing multiples on the year's estimated EPS
    # count a year of nominal growth twice (it read a median +35%, three
    # quarters of the ranked names "undervalued"; the same review).
    return {"basis": "pe", "central_multiple": central_multiple,
            "central_basis": anchors[0]["key"],
            "central": central, "low": eps_ttm * multiples[0],
            "high": eps_ttm * multiples[-1],
            "anchors": anchors, "own_pe": own, "disagree": disagree,
            "upside_pct": _pct(central / price - 1) if price else None,
            "doubtful": bool(doubts), "forward": None}


# The market's newest session, re-read after MARKET_DATE_TTL seconds. It was
# cached for the life of the process, so a panel started on a session
# measured every halt against that day: started when 11-03 was newest, it
# flagged 4 of the 10 names it should have, and priced استقلال, بازرگام, تکنار,
# چکاوه, شمواد and ولراز on stale closes (found 2026-11-11).
_MARKET_DATE = {"at": None, "value": None}
MARKET_DATE_TTL = 120.0


def _market_date():
    import time

    now = time.monotonic()
    if _MARKET_DATE["at"] is None or now - _MARKET_DATE["at"] > MARKET_DATE_TTL:
        newest = database.newest_equity_bar()
        _MARKET_DATE["value"] = None if newest is None else str(newest.date())
        _MARKET_DATE["at"] = now
    return _MARKET_DATE["value"]


def halted_since(ins_code):
    """The last date the symbol traded, if that was over a week before the
    market's newest session; None for a symbol that trades normally.

    The outlook prices a company at its last stored close. For a halted
    symbol that close is weeks or months old (بتهران, دتهران, شتهران on
    2026-11-05), and a P/E on it would read as today's.
    """

    newest = _market_date()
    conn = database._connect()
    last = conn.execute(
        "SELECT MAX(date) FROM price_history WHERE ins_code = ? AND volume > 0",
        (str(ins_code),)).fetchone()[0]
    conn.close()

    return _halt_from(last, newest)


def _halt_from(last, newest):
    """halted_since()'s rule, given the symbol's last traded date and the
    market's newest session."""

    from datetime import date

    if not newest:
        return None
    if not last:
        return "never"
    gap = (date.fromisoformat(newest[:10]) - date.fromisoformat(last[:10])).days
    return last[:10] if gap > 7 else None


def halted_since_all():
    """{ins_code: halted_since(ins_code)} for every symbol with a bar, read at
    once -- a code without one is "never", as there. screen() asked this
    800 times, one query each: 5.7 of the ranking's 26 s (2026-11-25).

    The newest traded bar of each code by a skip-scan over the (ins_code,
    date) key, as database.halted_codes reads it.
    """

    newest = _market_date()
    conn = database._connect()
    try:
        rows = conn.execute("""
            WITH RECURSIVE c(x) AS (
              SELECT MIN(ins_code) FROM price_history
              UNION ALL
              SELECT (SELECT MIN(ins_code) FROM price_history WHERE ins_code > c.x)
              FROM c WHERE c.x IS NOT NULL)
            SELECT x, (SELECT date FROM price_history p WHERE p.ins_code = c.x
                         AND p.volume > 0 ORDER BY date DESC LIMIT 1)
            FROM c WHERE x IS NOT NULL
        """).fetchall()
    finally:
        conn.close()
    return {str(code): _halt_from(last, newest) for code, last in rows}


_PRICES = {"at": None, "value": None}


def _snapshot_failed(what, exc):
    """A market-watch read that failed is said once in the log: returned as
    an empty answer and nothing else, it moved EVERY company to its capital-
    line share count without a line anywhere (the review of 2026-11-23)."""

    import logging
    logging.getLogger("sirat.outlook").warning(
        "market snapshot read failed (%s): %s: %s", what,
        type(exc).__name__, str(exc)[:160])


def latest_prices():
    """{ins_code: the newest price a trade set}, one query per source.

    The newest TRADED bar's close, or the market watch's close when its
    session is newer: on 11-11 the bars stopped at 11-10 while the watch and
    the fundamentals card had 11-11, and the outlook, the dividend yield and
    P/NAV were priced a session behind the card beside them (شستا 3,590
    against 3,690; the full test, 2026-11-12). An untraded bar's close is a
    carried price, not one a trade set, and is not used.
    """

    import holdings
    import time as _time

    # The scan of price_history costs ~2 s, and the report and the
    # single-symbol lookup paid it on every request (the review of
    # 2026-11-23). Two minutes, like the market date beside it.
    now = _time.monotonic()
    if _PRICES["at"] is not None and now - _PRICES["at"] < MARKET_DATE_TTL:
        return _PRICES["value"]

    conn = database._connect()
    try:
        rows = conn.execute("""
            SELECT p.ins_code, p.close, p.date FROM price_history p
            JOIN (SELECT ins_code, MAX(date) d FROM price_history
                  WHERE volume > 0 GROUP BY ins_code) m
              ON m.ins_code = p.ins_code AND m.d = p.date
        """).fetchall()
        try:
            snaps = conn.execute("""
                SELECT s.ins_code, s.close, s.captured_at FROM market_snapshot s
                JOIN (SELECT ins_code, MAX(captured_at) at FROM market_snapshot
                      GROUP BY ins_code) m
                  ON m.ins_code = s.ins_code AND m.at = s.captured_at
                WHERE s.close > 0
            """).fetchall()
        except Exception:                                       # noqa: BLE001
            snaps = []
    finally:
        conn.close()
    out = {str(code): (_f(close), str(day)[:10]) for code, close, day in rows}
    for code, close, captured in snaps:
        session = holdings.snapshot_session(captured)
        held = out.get(str(code))
        if session and _f(close) and (held is None or session > held[1]):
            out[str(code)] = (_f(close), session)
    value = {code: close for code, (close, _day) in out.items()}
    _PRICES.update(at=now, value=value)
    return value


def exchange_shares():
    """{ins_code: shares outstanding} from the newest market snapshot."""

    conn = database._connect()
    try:
        rows = conn.execute("""
            SELECT s.ins_code, s.total_shares FROM market_snapshot s
            JOIN (SELECT ins_code, MAX(captured_at) at FROM market_snapshot
                  GROUP BY ins_code) m
              ON m.ins_code = s.ins_code AND m.at = s.captured_at
        """).fetchall()
    except Exception as exc:                                    # noqa: BLE001
        _snapshot_failed("shares", exc)
        rows = []
    finally:
        conn.close()
    return {str(code): _f(n) for code, n in rows if _f(n)}


def price_bands():
    """{ins_code: (previous close, upper limit, lower limit)} from the newest
    market snapshot."""

    conn = database._connect()
    try:
        rows = conn.execute("""
            SELECT s.ins_code, s.prev_close, s.limit_up, s.limit_down
            FROM market_snapshot s
            JOIN (SELECT ins_code, MAX(captured_at) at FROM market_snapshot
                  GROUP BY ins_code) m
              ON m.ins_code = s.ins_code AND m.at = s.captured_at
        """).fetchall()
    except Exception as exc:                                    # noqa: BLE001
        _snapshot_failed("bands", exc)
        rows = []
    finally:
        conn.close()
    return {str(code): (_f(prev), _f(up), _f(down))
            for code, prev, up, down in rows}


def _nearest_separate(periods, when):
    """The separate (parent-only) period whose end is nearest `when`
    (a Jalali date), preferring one at or before it; None without any."""

    separate = [p for p in periods if not p["consolidated"]]
    if not separate or not when:
        return separate[0] if separate else None

    def months(end):
        try:
            y, m = (int(x) for x in str(end).split("/")[:2])
            return y * 12 + m
        except (TypeError, ValueError):
            return None

    target = months(when)
    if target is None:
        return separate[0]
    ranked = [(abs(months(p["end"]) - target) + (0.5 if months(p["end"]) > target else 0), i, p)
              for i, p in enumerate(separate) if months(p["end"]) is not None]
    return min(ranked)[2] if ranked else separate[0]


_INDEX = {"at": None, "frame": None}


def index_move_since(jalali_date):
    """TEDPIX's change from the session on or before `jalali_date` to its
    newest close, as a fraction; None when either end is missing."""

    import time

    try:
        jy, jm, jd = (int(x) for x in str(jalali_date).split("/")[:3])
        then = "%04d-%02d-%02d" % jalali.to_gregorian(jy, jm, jd)
    except (TypeError, ValueError):
        return None
    now = time.monotonic()
    if _INDEX["at"] is None or now - _INDEX["at"] > MARKET_DATE_TTL:
        try:
            frame = database.load_index_history(config.INDICES["TEDPIX"]["insCode"])
        except Exception:                                       # noqa: BLE001
            frame = None
        _INDEX.update(at=now, frame=frame)
    frame = _INDEX["frame"]
    if frame is None or frame.empty:
        return None
    dates = frame["date"].dt.strftime("%Y-%m-%d")
    before = frame[dates <= then]
    if before.empty:
        return None
    start, end = float(before["close"].iloc[-1]), float(frame["close"].iloc[-1])
    return end / start - 1 if start > 0 else None


def dps_today(dividend, shares, periods=()):
    """(DPS on today's share count, whether it is an older year's).

    The AGM's DPS is per share of the capital at the AGM; after a bonus
    issue or a split the price is per share of a larger count, and the yield
    read on the two was off by the issue's factor -- شپدیس 81.5% for about 2%,
    151 of 692 names off by more than 10% (the review panel, 2026-11-11).
    And a DPS from a fiscal year older than the newest annual statement is
    last year's payout while this year's AGM is pending or unrecorded (104
    names): not a yield to quote today.
    """

    dividend = dividend or {}
    dps = _f(dividend.get("dps"))
    if dps is None:
        return None, False
    # The dividends table keeps the AGM's capital in MILLION rials (فولاد:
    # 1,935,000 for 1,935 billion shares): shares then = capital x 1e6 / par.
    capital = _f(dividend.get("capital"))
    shares = _f(shares)
    if capital and capital > 0 and shares and shares > 0:
        dps = dps * (capital * 1_000_000 / PAR_VALUE) / shares
    stale = False
    fy = str(dividend.get("fiscal_year_end") or "")
    annual = [str(p["end"]) for p in periods or () if p.get("months") == 12]
    if fy and annual and max(annual)[:7] > fy[:7]:
        stale = True
    return dps, stale


def published_pe():
    """{ins_code: TSETMC's own P/E} from the newest market snapshot."""

    conn = database._connect()
    try:
        rows = conn.execute("""
            SELECT s.ins_code, s.pe FROM market_snapshot s
            JOIN (SELECT ins_code, MAX(captured_at) at FROM market_snapshot
                  GROUP BY ins_code) m
              ON m.ins_code = s.ins_code AND m.at = s.captured_at
        """).fetchall()
    except Exception as exc:                                    # noqa: BLE001
        _snapshot_failed("pe", exc)
        rows = []
    finally:
        conn.close()
    return {str(code): _f(pe) for code, pe in rows if _f(pe) and _f(pe) > 0}


def one_off_reason(items):
    """Why a period's net profit is not the business's own, or None."""

    net = _f((items or {}).get("net_profit"))
    op = _f((items or {}).get("operating_profit"))
    if net is None or op is None or net <= 0:
        return None
    if op <= 0:
        return ("سود خالص مثبت با زیان عملیاتی: سود از فعالیت اصلی نیست "
                "(احتمالاً یک‌باره)، پس P/E بر آن ارزش‌گذاری نیست")
    if net > ONE_OFF_RATIO * op:
        return ("سود خالص %.1f برابر سود عملیاتی است: بیشتر سود از فعالیت "
                "اصلی نیست (احتمالاً یک‌باره)، پس P/E بر آن ارزش‌گذاری نیست"
                % (net / op))
    # The same income booked INSIDE operating profit ("other operating
    # income": livestock or FX revaluation) passed the test above: 18 of the
    # 130 ranked names had operating profit above gross profit (the review
    # panel, 2026-11-11).
    gross = _f((items or {}).get("gross_profit"))
    if gross is not None and op > 0 and op - max(gross, 0) > OTHER_OPERATING_SHARE * op:
        head = ("سود عملیاتی %.1f برابر سود ناخالص است" % (op / gross)
                if gross > 0 else "سود عملیاتی با وجود زیان ناخالص مثبت است")
        return (head + ": بخش بزرگی از سود عملیاتی از فروش نیست (سایر درآمدهای "
                "عملیاتی، مانند تسعیر ارز یا تجدید ارزیابی)، پس P/E بر آن "
                "ارزش‌گذاری نیست")
    return None


def published_gap_reason(pe_ours, pe_published):
    """A note when our trailing P/E and TSETMC's disagree materially."""

    if not pe_ours or not pe_published or pe_ours <= 0 or pe_published <= 0:
        return None
    ratio = pe_ours / pe_published
    if PUBLISHED_PE_BAND[0] <= ratio <= PUBLISHED_PE_BAND[1]:
        return None
    return ("P/E دوازده‌ماهه‌ی ما %.1f است و P/E اعلامی بورس %.1f: معمولاً از "
            "تفاوت صورت تلفیقی و جداگانه یا سودی یک‌باره است؛ پیش از تصمیم "
            "صورت مالی را ببینید" % (pe_ours, pe_published))


def trailing_multiples(periods_by_code, market_by_code, sector_by_code=None):
    """{ins_code: P/E and P/S on the trailing twelve months} for the
    fundamentals card, from analyse()'s TTM on the stored periods.

    The card annualised its newest interim: six months x 2. In a year of
    77% inflation the newest half is the dearest, so every P/E read low --
    median 0.82 of TSETMC's, 62% within ±25%, against this module's TTM at
    0.99 and 93% (2026-11-11). `profit` None means no TTM could be built (no
    prior year stored); the caller keeps its annualised figure and says so.
    A loss over the trailing year is a TTM too: P/E None, profit <= 0.
    """

    sector_by_code = sector_by_code or {}
    bills = {"rate_pct": config.OPPORTUNITY_RATE, "as_of": None,
             "source": "config"}
    out = {}
    for code, rows in (periods_by_code or {}).items():
        market = market_by_code.get(code) or {}
        price = _f(market.get("close"))
        if not rows or not price:
            continue
        try:
            r = analyse(code, price=price, rows=rows, monthly_rows=[],
                        dividend=None, shares=_f(market.get("total_shares")),
                        sector=sector_by_code.get(code), portfolio=None,
                        opportunity=bills, bars=None, band=None,
                        published=None)
        except Exception:                                       # noqa: BLE001
            continue
        if not r or r["ttm"]["profit"] is None:
            continue
        cap = r["valuation"]["market_cap"]
        pe = r["valuation"]["pe_ttm"]
        revenue = r["ttm"]["revenue"]
        # Uncapped: on a positive trailing profit a P/E of 60 or more is the
        # market's real multiple (کفرا 72.3 against TSETMC's 72.5, ثغرب 88.1
        # against 88.2). Capped at PE_ABSURD it showed «نامعلوم» with a
        # "profit not positive" tooltip and dropped out of the score, which
        # raised it (the full test, 2026-11-12). The cap stays where a single
        # filing's ratio may be a misread (financials.ratios).
        out[code] = {
            "pe": pe,
            "ps": _round(cap / revenue) if cap and revenue and revenue > 0
            else None,
            "profit": r["ttm"]["profit"],
            "revenue": revenue,
            "period_end": r["latest"]["period_end"],
            "months": r["latest"]["months"],
            "basis": r["basis"],
        }
    return out


def _band_pct(band):
    """Half the day's permitted range as a percent of the previous close,
    and whether it is narrower than an ordinary ±3% band cut to the tick."""

    prev, up, down = band if band else (None, None, None)
    if not prev or not up or not down or up <= down or prev <= 0:
        return None, False
    half = (up - down) / 2.0
    pct = half / prev * 100
    if pct > 50:                        # a malformed row, not a band
        return None, False
    return pct, (half + TICK_RIALS) / prev * 100 < NARROW_BAND_PCT


def _money_fa(rials):
    if rials >= 1e12:
        return "%s هزار میلیارد" % format(round(rials / 1e12, 1), ",g")
    if rials >= 1e9:
        return "%s میلیارد" % format(round(rials / 1e9, 1), ",g")
    return "%s میلیون" % format(round(rials / 1e6), ",")


def trading_read(bars, band=None):
    """Whether the last close is a price anybody can deal at, or None.

    `bars` is the newest TRADING_SESSIONS rows of price_history (oldest
    first, a DataFrame with date/high/low/close/volume/value), `band` the
    snapshot's (previous close, upper limit, lower limit). `illiquid` names
    are not ranked by screen(); `notes` say why, in the panel's words.
    """

    if bars is None or len(bars) == 0:
        return None

    import numpy as np

    bars = bars.tail(TRADING_SESSIONS)
    # Arrays, not a row loop: .iloc per bar made this 9 s of the
    # fundamentals card's 13 over ~740 names (2026-11-11).
    volume = bars["volume"].fillna(0).to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    value = (bars["value"].fillna(0).to_numpy(dtype=float)
             if "value" in bars.columns else volume * np.nan_to_num(close))
    avg_value = float(value.mean())

    traded = int((volume > 0).sum())
    high = bars["high"].to_numpy(dtype=float)
    one_price = (volume > 0) & (high == bars["low"].to_numpy(dtype=float))
    prev = np.concatenate(([np.nan], close[:-1]))
    prev[~(prev > 0)] = np.nan                  # no previous close: no side
    with np.errstate(invalid="ignore"):
        up = int((one_price & (high > prev)).sum())
        down = int((one_price & (high < prev)).sum())
    single = int(one_price.sum())

    band_pct, narrow = _band_pct(band)
    illiquid = avg_value < config.MIN_DAILY_VALUE

    notes = []
    if illiquid:
        notes.append(
            "کم‌معامله: میانگین ارزش معاملات %d جلسه‌ی اخیر %s ریال در روز "
            "(کف: %s)%s — به این قیمت نمی‌شود به اندازه‌ی معنادار خرید یا فروخت"
            % (len(bars), _money_fa(avg_value),
               _money_fa(config.MIN_DAILY_VALUE),
               "، %d جلسه بدون معامله" % (len(bars) - traded)
               if traded < len(bars) else ""))
    if narrow:
        notes.append(
            "دامنه‌ی نوسان فقط ±%.0f٪ است: قیمت روزی بیش از این جابه‌جا نمی‌شود، "
            "پس P/E پایین ممکن است عقب ماندن قیمت باشد نه ارزانی" % band_pct)
    if up >= LOCKED_SESSIONS:
        notes.append(
            "در %d جلسه از %d جلسه همه‌ی معاملات در یک قیمت و بالاتر از روز "
            "قبل بود (صف خرید): خرید به قیمت تابلو عملاً ممکن نیست"
            % (up, len(bars)))
    elif down >= LOCKED_SESSIONS:
        notes.append(
            "در %d جلسه از %d جلسه همه‌ی معاملات در یک قیمت و پایین‌تر از روز "
            "قبل بود (صف فروش): قیمت تابلو هنوز به تعادل نرسیده"
            % (down, len(bars)))

    return {"sessions": len(bars), "traded_sessions": traded,
            "avg_value": avg_value, "floor": config.MIN_DAILY_VALUE,
            "illiquid": illiquid, "band_pct": _round(band_pct, 1),
            "narrow_band": narrow, "single_price_sessions": single,
            "queue_up_sessions": up, "queue_down_sessions": down,
            "notes": notes}


def opportunity_rate(today=None):
    """The bill yield the outlook measures equity against.

    The yield opportunities.treasury_bills() last stored, if under two weeks
    old; else config.OPPORTUNITY_RATE with its date. Never a network call.
    """

    import opportunities
    stored = opportunities.remembered_bill_yield(today=today)
    if stored:
        return {"rate_pct": stored["yield_pct"], "as_of": stored["as_of"],
                "source": "اخزا (%d سری)" % (stored.get("bills") or 0)}
    return {"rate_pct": config.OPPORTUNITY_RATE,
            "as_of": config.OPPORTUNITY_RATE_AS_OF, "source": "config"}


def suspect_reason(result):
    """Why a result is likelier a misread filing than a company, or None."""

    if result["kind"] != OPERATING:
        return None
    pe = result["valuation"]["pe_forward"]
    growth = result["growth"]["revenue_pct"]
    margin = result["margins"]["net_pct"]
    if pe is not None and pe < SUSPECT_PE:
        return "P/E آینده‌نگر %.2f" % pe
    if growth is not None and not             SUSPECT_GROWTH_PCT[0] <= growth <= SUSPECT_GROWTH_PCT[1]:
        return "رشد فروش %+.0f٪" % growth
    if margin is not None and margin > SUSPECT_MARGIN_PCT:
        return "حاشیه سود خالص %.0f٪" % margin
    return None


def _median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else         (values[middle - 1] + values[middle]) / 2.0


SECTOR_PEERS_MIN = 3


def _preread_all(codes, periods=12):
    """A function code -> analyse()'s `preread` for that code."""

    import logging

    import codal_monthly

    log = logging.getLogger("sirat.outlook")
    batches = {}
    for name, read in (
            ("metrics", lambda: fundamental_db.load_metrics_many(codes, limit=periods)),
            ("monthly", lambda: codal_monthly.load_many(
                codes, limit=codal_monthly.HISTORY_MONTHS)),
            ("halted", halted_since_all)):
        try:
            batches[name] = read()
        except Exception as exc:                                # noqa: BLE001
            log.warning("outlook: the batch read of %s failed, read per "
                        "company instead: %s: %s", name, type(exc).__name__,
                        str(exc)[:160])
    never = _halt_from(None, _market_date())

    def one(code):
        code = str(code)
        out = {}
        if "metrics" in batches:
            out["metrics"] = batches["metrics"].get(code, [])
        if "monthly" in batches:
            out["monthly"] = batches["monthly"].get(code, [])
        if "halted" in batches:
            out["halted"] = batches["halted"].get(code, never)
        return out

    return one


def _inputs(codes=None):
    """What analyse() reads for many companies, read once (screen and
    fair_values). `codes` limits the per-company batches."""

    instruments = {str(r["ins_code"]): r
                   for r in database.load_instruments(equities_only=True)}
    wanted = list(instruments) if codes is None else [str(c) for c in codes]
    import codal_announcements
    import fx
    import outlook_calibration
    portfolios = database.load_codal_portfolio_latest()
    try:
        holdings = database.load_portfolio_rows(
            [(c, p.get("period_end")) for c, p in portfolios.items()])
    except Exception:                                           # noqa: BLE001
        holdings = None             # each company reads its own, as before
    return {
        "instruments": instruments,
        "prices": latest_prices(),
        "shares": exchange_shares(),
        "bands": price_bands(),
        "published": published_pe(),
        "tails": database.load_history_tail(wanted, TRADING_SESSIONS),
        "portfolios": portfolios,
        "holdings": holdings,
        "opportunity": opportunity_rate(),
        "calibration": outlook_calibration.load(),
        "fx_record": fx.load(),
        "capital": capital_events_for(None),
        "peers": valuation_peers(),
        "halted": database.halted_codes(),
        "dividends": codal_announcements.latest_dividends(),
        "preread": _preread_all(wanted),
    }


def _analyse_with(code, inputs, today=None):
    """analyse() for one company on the shared inputs."""

    inst = inputs["instruments"].get(code) or {}
    portfolio = inputs["portfolios"].get(code)
    extra = {}
    if inputs.get("holdings") is not None:
        extra["holdings"] = inputs["holdings"].get(
            (code, (portfolio or {}).get("period_end")))
    return analyse(code, price=inputs["prices"].get(code), today=today,
                   preread=inputs["preread"](code),
                   dividend=inputs["dividends"].get(code),
                   shares=inputs["shares"].get(code),
                   sector=inst.get("sector_name"),
                   portfolio=portfolio, opportunity=inputs["opportunity"],
                   bars=inputs["tails"].get(code), band=inputs["bands"].get(code),
                   published=inputs["published"].get(code),
                   calibration=inputs["calibration"],
                   fx_record=inputs["fx_record"],
                   capital_events=inputs["capital"].get(code),
                   peers=inputs["peers"], prices_all=inputs["prices"], **extra)


# The fundamental values the last screen() read for every company, for the
# single-company view's portfolio (attach_forward): one company's holdings
# are tens of other companies, each a full read.
_FAIR = {"at": None, "value": None}
FAIR_TTL = 3 * 3600.0


def fair_values(codes):
    """{ins_code: fair_value()} for these companies: the last screen's when
    it is fresh and has them, else read now."""

    import time as _time

    codes = [str(c) for c in codes if c]
    # The last screen read every equity: a code it has no value for has
    # none (a halted name, a fund, a right), not one still to be read.
    cached = _FAIR["value"]
    if cached is not None and _time.monotonic() - _FAIR["at"] < FAIR_TTL:
        return cached
    inputs = _inputs(codes)
    out = {}
    for code in codes:
        if code in inputs["halted"] or code not in inputs["instruments"]:
            continue
        result = _analyse_with(code, inputs)
        if result and result.get("fair_value"):
            out[code] = result["fair_value"]
    return out


def attach_forward(result, fair_by_code=None):
    """Add the forward-looking value of a portfolio to an analyse() result:
    every listed holding at the bot's fundamental value of that share
    (portfolio_value.fundamental). Drops the per-row working list."""

    valued = result.pop("_valued", None) if result else None
    if not valued:
        return result
    import portfolio_value
    if fair_by_code is None:
        fair_by_code = fair_values([r.get("ins_code") for r in valued])
    v = result["valuation"]
    v["nav_forward"] = portfolio_value.fundamental(
        v.get("nav_today"), valued, fair_by_code, price=v.get("price"),
        shares=v.get("shares"))
    result["holdings"] = portfolio_value.top(valued)
    return result


def screen(min_yield_pct=None, max_flags=1, limit=60, today=None):
    """Every company with a forward estimate, in two lists.

    `ranked`: operating companies by forward earnings yield, each with its
    P/E against its industry's median and the growth its price requires.
    `financials`: banks, insurers, investment and holding companies, by P/NAV
    where the portfolio filing allows, else by P/B. Only companies whose
    latest period is recent, with a prior year to complete it, and at most
    `max_flags` warnings, are ranked; the rest are counted, not hidden, and a
    result that looks like a misread filing is named under `suspect`. A
    share trading below the liquidity floor is named under `thin`, the
    cheapest-looking first: its price is not one a buyer can deal at. The
    tape's other warnings (a narrow band, a queue) are shown with the row but
    not counted against `max_flags` -- they say how to deal, not what the
    company is worth.
    """

    # Each company's statements, monthly reports, last traded day and
    # portfolio rows, read for all of them at once (_inputs): per company,
    # through a connection each, the reads were 16 of this function's 26 s
    # (2026-11-25).
    import fx

    inputs = _inputs()
    instruments = inputs["instruments"]
    opportunity = inputs["opportunity"]
    calibration = inputs["calibration"]
    fx_record = inputs["fx_record"]
    analysed = {}

    ranked, financials, suspect, thin, doubtful = [], [], [], [], []
    excluded = {"no_filing": 0, "stale": 0, "no_estimate": 0, "flags": 0,
                "loss": 0, "halted": 0, "suspect": 0, "basis_gap": 0,
                "illiquid": 0, "doubtful": 0}
    halted = inputs["halted"]

    for code, inst in instruments.items():
        if code in halted:
            excluded["halted"] += 1
            continue
        result = _analyse_with(code, inputs, today)
        if not result:
            excluded["no_filing"] += 1
            continue
        analysed[code] = result
        result.update(symbol=inst.get("symbol_fa"), name=inst.get("name_fa"),
                      sector=inst.get("sector_name"))
        age = result["latest"]["age_months"]
        if age is not None and age > STALE_MONTHS:
            excluded["stale"] += 1
            continue

        trading = result.get("trading") or {}
        if trading.get("illiquid"):
            excluded["illiquid"] += 1
            v = result["valuation"]
            thin.append({"symbol": result["symbol"], "kind": result["kind"],
                         "avg_value": trading["avg_value"],
                         "traded_sessions": trading["traded_sessions"],
                         "sessions": trading["sessions"],
                         "band_pct": trading["band_pct"],
                         "pe_forward": v["pe_forward"],
                         "earnings_yield_forward_pct":
                             v["earnings_yield_forward_pct"]})
            continue
        tape_notes = len(trading.get("notes") or ())

        if result["kind"] == FINANCIAL and result.get("doubts"):
            excluded["doubtful"] += 1
            doubtful.append({"symbol": result["symbol"],
                             "reasons": [d["label"] for d in result["doubts"]],
                             "pe_forward": result["valuation"]["pe_forward"],
                             "earnings_yield_forward_pct":
                                 result["valuation"]["earnings_yield_forward_pct"]})
            continue
        if result["kind"] == FINANCIAL:
            if result["valuation"]["pb"] is not None or                     result["valuation"]["price_to_nav"] is not None:
                financials.append(result)
            else:
                excluded["no_estimate"] += 1
            continue

        ey = result["valuation"]["earnings_yield_forward_pct"]
        if ey is None or result["method"] == "annualised":
            excluded["basis_gap" if result["basis"] == "separate_fallback"
                     else "no_estimate"] += 1
            continue
        if ey <= 0:
            excluded["loss"] += 1
            continue
        reason = suspect_reason(result)
        if reason:
            excluded["suspect"] += 1
            suspect.append({"symbol": result["symbol"], "reason": reason,
                            "period_end": result["latest"]["period_end"]})
            continue
        if result.get("doubts"):
            # The multiple the ranking sorts on is not a valuation here: a
            # one-off profit, a gap to the exchange's own P/E, a year
            # projected from two months (the second audit, 2026-11-11).
            excluded["doubtful"] += 1
            doubtful.append({"symbol": result["symbol"],
                             "reasons": [d["label"] for d in result["doubts"]],
                             "pe_forward": result["valuation"]["pe_forward"],
                             "earnings_yield_forward_pct":
                                 result["valuation"]["earnings_yield_forward_pct"]})
            continue
        if len(result["flags"]) - tape_notes > max_flags:
            excluded["flags"] += 1
            continue
        if min_yield_pct is not None and ey < min_yield_pct:
            continue
        ranked.append(result)

    # Relative value: each P/E against its own industry's, among the ranked.
    by_sector = {}
    for r in ranked:
        by_sector.setdefault(r["sector"], []).append(
            r["valuation"]["pe_forward"])
    medians = {k: _median([v for v in vs if v]) for k, vs in by_sector.items()
               if len([v for v in vs if v]) >= SECTOR_PEERS_MIN}
    for r in ranked:
        median = medians.get(r["sector"])
        pe = r["valuation"]["pe_forward"]
        r["valuation"]["sector_pe_median"] = _round(median)
        r["valuation"]["pe_vs_sector"] = _round(pe / median) if median and pe             else None
        r["valuation"]["sector_peers"] = len(by_sector.get(r["sector"]) or [])

    # Every portfolio's forward-looking value, on the fundamental values
    # just read for every company; and the per-row working lists dropped.
    import time as _time
    fair_by_code = {c: r["fair_value"] for c, r in analysed.items()
                    if r.get("fair_value")}
    _FAIR.update(at=_time.monotonic(), value=fair_by_code)
    for result in analysed.values():
        attach_forward(result, fair_by_code)

    ranked.sort(key=lambda r: -r["valuation"]["earnings_yield_forward_pct"])
    def _nav_key(r):
        # Today's prices for the listed holdings where the rows are held;
        # else a stale portfolio's estimate (holdings carried at the index's
        # move); else the filing as it stands.
        v = r["valuation"]
        nav = v.get("price_to_nav_today")
        if nav is None:
            nav = v["price_to_nav_est"] if v.get("nav_stale") else v["price_to_nav"]
        return (nav is None, nav if nav is not None else (v["pb"] or 99))

    financials.sort(key=_nav_key)

    thin.sort(key=lambda r: -(r["earnings_yield_forward_pct"] or -1e9))
    doubtful.sort(key=lambda r: -(r["earnings_yield_forward_pct"] or -1e9))

    return {"ranked": ranked[:limit], "count": len(ranked),
            "financials": financials[:limit],
            "financials_count": len(financials),
            "suspect": suspect,
            "thin": thin[:THIN_LISTED], "thin_count": len(thin),
            "doubtful": doubtful,
            "liquidity_floor": config.MIN_DAILY_VALUE,
            "excluded": excluded,
            "bar_pct": opportunity["rate_pct"],
            "opportunity": opportunity,
            "calibration": ({"built_at": calibration.get("built_at"),
                             "points": calibration.get("points"),
                             "source": (calibration.get("band") or {}).get(
                                 "source", "restated")}
                            if calibration else None),
            "dollar": fx.summary(fx_record) if fx_record else None}
