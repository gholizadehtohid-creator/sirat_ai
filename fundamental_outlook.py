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
  TTM         latest + annual_prev - same_prev   (latest when it is annual)
  forward     latest + (annual_prev - same_prev) * (1 + YTD growth), and
              profit as that revenue at the year-to-date margin. For an
              annual latest: next year at this year's revenue growth and
              margin, growth capped to [-50%, +100%].

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


def _periods(rows):
    out = []
    for row in rows:
        items = row.get("items") or {}
        months = row.get("period_months") or 12
        fy = fiscal_year_end(row.get("period_end"), months)
        if fy is None:
            continue
        out.append({"end": row.get("period_end"), "months": int(months),
                    "fy": fy, "items": items, "ratios": row.get("ratios") or {},
                    "published": row.get("published_at"),
                    "audited": row.get("audited"),
                    "consolidated": bool(row.get("consolidated"))})
    out.sort(key=lambda p: (p["end"], p["months"]), reverse=True)
    return out


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
            rows = codal_monthly.load(ins_code, limit=13)
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
                      "sales": _f((r.get("sales") or {}).get("month"))}
                     for r in reversed(rows)]
    return out


_UNSET = object()


def analyse(ins_code, price=None, periods=12, today=None, rows=None,
            monthly_rows=None, dividend=_UNSET, shares=_UNSET, sector=_UNSET,
            portfolio=_UNSET, opportunity=None, bars=_UNSET, band=_UNSET):
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
    a point-in-time valuation must not be judged on today's tape.
    """

    rows_from_store = rows is None
    if rows is None:
        rows = fundamental_db.load_metrics(ins_code, limit=periods)
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
    opportunity = opportunity or opportunity_rate()
    today = today or jalali.today_jalali()

    # A halted symbol's last close can be months or years old; a P/E on it
    # is not today's valuation (دتهران read 0.02 on a 2018 price). Report
    # the halt, value nothing.
    halted = halted_since(ins_code) if price and rows_from_store else None
    if halted:
        price = None
    all_periods = _periods(rows)

    if not all_periods:
        return None

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
            # A whole year not yet begun: last year's growth halfway back to
            # inflation, not carried forward whole. 167% one year is rarely
            # 167% the next; inflation is what nominal sales drift back to.
            # (A year under way grows its remaining months at the rate it has
            # actually run at, below -- that is evidence, this is a forecast.)
            last = growth_rev if growth_rev is not None else 0.0
            g = min(max(0.5 * last + 0.5 * config.INFLATION_RATE / 100.0,
                        GROWTH_CAP[0]), GROWTH_CAP[1])
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
        if None not in (rev, a_rev, s_rev):
            ttm_rev = rev + a_rev - s_rev
            if growth_rev is not None:
                g = min(max(growth_rev, GROWTH_CAP[0]), GROWTH_CAP[1])
                remaining = (a_rev - s_rev) * (1 + g)
                fwd_rev = rev + remaining
                if net is not None and margin is not None:
                    fwd_net = net + remaining * margin
                    method = "year_completed"
        if None not in (net, a_net, s_net):
            ttm_net = net + a_net - s_net

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
    monthly = monthly_reads(ins_code, latest["fy"][1], monthly_rows)
    if monthly and monthly.get("ytd_growth") is not None and \
            monthly["period_end"] > latest["end"]:
        fy_m = monthly["fiscal_year"]
        base = _find(ps, (fy_m[0] - 1, fy_m[1]), 12)
        base_rev = _f(base["items"].get("revenue")) if base else None
        run_margin = _div(ttm_net, ttm_rev) if ttm_rev and ttm_net is not None \
            else margin
        if base_rev and run_margin is not None:
            g = min(max(monthly["ytd_growth"], GROWTH_CAP[0]), GROWTH_CAP[1])
            fwd_rev = base_rev * (1 + g)
            fwd_net = fwd_rev * run_margin
            method = "monthly_sales"

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
    dps = _f((dividend or {}).get("dps"))
    dividend_yield = _div(dps, price) if dps is not None and price else None

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
    nav_result = price_to_nav = None
    if kind == FINANCIAL and portfolio:
        import nav as nav_module
        own = next((p for p in all_periods if not p["consolidated"]), latest)
        own_equity = _f(own["items"].get("equity_owners")) or             _f(own["items"].get("equity"))
        nav_result = nav_module.net_asset_value(portfolio, own_equity)
        price_to_nav = nav_module.price_to_nav(market_cap, nav_result)

    # ---- age -------------------------------------------------------------
    age_months = _age_months(latest["end"], today)

    inflation = config.INFLATION_RATE / 100.0

    # ---- what to watch ---------------------------------------------------
    flags, positives = [], []

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
                positives.append("نقد عملیاتی بیشتر از سود خالص (کیفیت سود خوب)")
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
        for p in monthly.get("products") or []:
            if (p.get("production_to_sales") or 0) > 1.3 and                     (p.get("amount_ytd") or 0) > 0:
                flag("تولید «%s» %.0f٪ بیشتر از فروش آن — انباشت موجودی"
                     % (p["name"], (p["production_to_sales"] - 1) * 100))
                break
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
    required_growth = cushion = actual_growth = None
    if kind == OPERATING and yield_fwd is not None and yield_fwd > 0:
        required_growth = rate - yield_fwd
        actual_growth = (monthly.get("ytd_growth") if monthly and
                         monthly.get("ytd_growth") is not None else growth_rev)
        if actual_growth is not None:
            cushion = actual_growth - required_growth
        if required_growth <= 0:
            positives.append("بازده سود برآوردی %.0f٪ از بازده اخزا (%.0f٪) بیشتر "
                             "است؛ قیمت برای توجیه شدن به رشد نیاز ندارد"
                             % (yield_fwd * 100, rate * 100))
        elif cushion is not None and cushion * 100 < -GROWTH_CUSHION_PCT:
            flag("قیمت امروز، در برابر اخزای %.0f٪، رشد سالانه‌ی حدود %.0f٪ "
                 "سود را لازم دارد؛ رشد فعلی فروش %.0f٪ است"
                 % (rate * 100, required_growth * 100, actual_growth * 100))
        elif cushion is not None and cushion * 100 > GROWTH_CUSHION_PCT:
            positives.append("رشد فعلی فروش %.0f٪، بیشتر از رشد %.0f٪ که قیمت "
                             "امروز در برابر اخزای %.0f٪ لازم دارد"
                             % (actual_growth * 100, required_growth * 100,
                                rate * 100))
    if kind == OPERATING and yield_fwd is not None and yield_fwd > 0             and pe_fwd is not None:
        positives.append("بازده سود برآوردی %.0f٪ (P/E آینده‌نگر %.1f)"
                         % (yield_fwd * 100, pe_fwd))
    if kind == FINANCIAL:
        flag("بانک، بیمه، سرمایه‌گذاری یا هلدینگ: P/E و رشد فروش معیار "
             "ارزش‌گذاری نیست؛ با P/NAV یا P/B و ROE سنجیده می‌شود")
        if price_to_nav is not None:
            (positives if price_to_nav < 1 else flags).append(
                "P/NAV %.2f — %s" % (price_to_nav,
                                     "زیر ارزش خالص دارایی‌ها" if price_to_nav < 1
                                     else "بالاتر از ارزش خالص دارایی‌ها"))
    if dividend_yield is not None and dps > 0:
        positives.append("سود نقدی مجمع سال %s: %s ریال هر سهم — بازده نقدی %.1f٪"
                         % (dividend["fiscal_year_end"], format(int(round(dps)), ","),
                            dividend_yield * 100))

    result = {
        "ins_code": str(ins_code),
        "kind": kind,
        "basis": basis,
        "latest": {"period_end": latest["end"], "months": m,
                   "audited": latest["audited"], "published": latest["published"],
                   "age_months": age_months,
                   "consolidated": latest["consolidated"]},
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
        "ttm": {"revenue": ttm_rev, "profit": ttm_net, "eps": eps_ttm},
        "forward": {"revenue": fwd_rev, "profit": fwd_net, "eps": eps_fwd},
        "valuation": {"price": price, "market_cap": market_cap,
                      "pe_ttm": _round(pe_ttm), "pe_forward": _round(pe_fwd),
                      "earnings_yield_forward_pct": _pct(yield_fwd),
                      "pb": _round(pb), "roe_ttm_pct": _pct(roe_ttm),
                      "fcf_yield_pct": _pct(fcf_yield),
                      "dps": dps,
                      "dividend_fiscal_year": (dividend or {}).get("fiscal_year_end"),
                      "dividend_yield_pct": _pct(dividend_yield),
                      "shares": shares, "shares_source": shares_source,
                      "price_to_nav": _round(price_to_nav),
                      "nav": (nav_result or {}).get("nav"),
                      "nav_complete": (nav_result or {}).get("complete"),
                      "nav_period": (portfolio or {}).get("period_end")
                      if nav_result else None},
        "hurdle": {"rate_pct": opportunity["rate_pct"],
                   "as_of": opportunity["as_of"],
                   "source": opportunity["source"],
                   "required_growth_pct": _pct(required_growth),
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
    }

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
    return result


def _round(x, digits=2):
    return None if x is None else round(x, digits)


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

    from datetime import date

    newest = _market_date()
    conn = database._connect()
    last = conn.execute(
        "SELECT MAX(date) FROM price_history WHERE ins_code = ? AND volume > 0",
        (str(ins_code),)).fetchone()[0]
    conn.close()

    if not newest:
        return None
    if not last:
        return "never"
    gap = (date.fromisoformat(newest[:10]) - date.fromisoformat(last[:10])).days
    return last[:10] if gap > 7 else None


def latest_prices():
    """{ins_code: last stored close}, one query for the whole universe."""

    conn = database._connect()
    rows = conn.execute("""
        SELECT p.ins_code, p.close FROM price_history p
        JOIN (SELECT ins_code, MAX(date) d FROM price_history GROUP BY ins_code) m
          ON m.ins_code = p.ins_code AND m.d = p.date
    """).fetchall()
    conn.close()
    return {str(code): _f(close) for code, close in rows}


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
    except Exception:                                           # noqa: BLE001
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
    except Exception:                                           # noqa: BLE001
        rows = []
    finally:
        conn.close()
    return {str(code): (_f(prev), _f(up), _f(down))
            for code, prev, up, down in rows}


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

    bars = bars.tail(TRADING_SESSIONS).reset_index(drop=True)
    volume = bars["volume"].fillna(0)
    value = bars["value"].fillna(0) if "value" in bars.columns \
        else volume * bars["close"].fillna(0)
    avg_value = float(value.mean())

    traded = int((volume > 0).sum())
    up = down = 0
    for i in range(len(bars)):
        row = bars.iloc[i]
        if not row["volume"] or row["volume"] <= 0 or row["high"] != row["low"]:
            continue
        prev = _f(bars.iloc[i - 1]["close"]) if i > 0 else None
        price = _f(row["high"])
        if prev and price and price > prev:
            up += 1
        elif prev and price and price < prev:
            down += 1
    single = int(((volume > 0) & (bars["high"] == bars["low"])).sum())

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

    instruments = {str(r["ins_code"]): r
                   for r in database.load_instruments(equities_only=True)}
    prices = latest_prices()
    shares = exchange_shares()
    bands = price_bands()
    tails = database.load_history_tail(list(instruments), TRADING_SESSIONS)
    portfolios = database.load_codal_portfolio_latest()
    opportunity = opportunity_rate()

    ranked, financials, suspect, thin = [], [], [], []
    excluded = {"no_filing": 0, "stale": 0, "no_estimate": 0, "flags": 0,
                "loss": 0, "halted": 0, "suspect": 0, "basis_gap": 0,
                "illiquid": 0}
    halted = database.halted_codes()
    import codal_announcements
    dividends = codal_announcements.latest_dividends()

    for code, inst in instruments.items():
        if code in halted:
            excluded["halted"] += 1
            continue
        result = analyse(code, price=prices.get(code), today=today,
                         dividend=dividends.get(code), shares=shares.get(code),
                         sector=inst.get("sector_name"),
                         portfolio=portfolios.get(code), opportunity=opportunity,
                         bars=tails.get(code), band=bands.get(code))
        if not result:
            excluded["no_filing"] += 1
            continue
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

    ranked.sort(key=lambda r: -r["valuation"]["earnings_yield_forward_pct"])
    financials.sort(key=lambda r: (
        r["valuation"]["price_to_nav"] is None,
        r["valuation"]["price_to_nav"] if r["valuation"]["price_to_nav"]
        is not None else (r["valuation"]["pb"] or 99)))

    thin.sort(key=lambda r: -(r["earnings_yield_forward_pct"] or -1e9))

    return {"ranked": ranked[:limit], "count": len(ranked),
            "financials": financials[:limit],
            "financials_count": len(financials),
            "suspect": suspect,
            "thin": thin[:THIN_LISTED], "thin_count": len(thin),
            "liquidity_floor": config.MIN_DAILY_VALUE,
            "excluded": excluded,
            "bar_pct": opportunity["rate_pct"],
            "opportunity": opportunity}
