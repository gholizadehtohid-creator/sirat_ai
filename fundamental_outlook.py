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
RECEIVABLE_GAP_PCT = 15.0               # receivables outgrowing sales by this


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
                    "audited": row.get("audited")})
    out.sort(key=lambda p: (p["end"], p["months"]), reverse=True)
    return out


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


def analyse(ins_code, price=None, periods=12, today=None, rows=None,
            monthly_rows=None):
    """The full read for one company, or None with nothing stored.

    `rows` (newest first, load_metrics' shape) replaces the store read, and
    `monthly_rows` the monthly-report read; the tests pass hand-built
    periods through them.
    """

    rows_from_store = rows is None
    if rows is None:
        rows = fundamental_db.load_metrics(ins_code, limit=periods)

    # A halted symbol's last close can be months or years old; a P/E on it
    # is not today's valuation (دتهران read 0.02 on a 2018 price). Report
    # the halt, value nothing.
    halted = halted_since(ins_code) if price and rows_from_store else None
    if halted:
        price = None
    ps = _periods(rows)

    if not ps:
        return None

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

    rev_prev = _f((same_prev or {}).get("items", {}).get("revenue")) \
        if same_prev else _backed_out(latest, "revenue", "revenue_growth")
    net_prev = _f(same_prev["items"].get("net_profit")) if same_prev else \
        _backed_out(latest, "net_profit", "profit_growth")

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
            g = min(max(growth_rev if growth_rev is not None else 0.0,
                        GROWTH_CAP[0]), GROWTH_CAP[1])
            fwd_rev = rev * (1 + g)
            fwd_net = fwd_rev * margin
            method = "next_year_trend"
    elif annual_prev and same_prev:
        a_rev = _f(annual_prev["items"].get("revenue"))
        a_net = _f(annual_prev["items"].get("net_profit"))
        s_rev, s_net = rev_prev, net_prev
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
    capital = _f(li.get("capital"))
    shares = capital / PAR_VALUE if capital else None

    # Owners' part of the group's profit: what a shareholder's EPS is.
    ttm_net = ttm_net * owners_share if ttm_net is not None else None
    fwd_net = fwd_net * owners_share if fwd_net is not None else None

    eps_ttm = _div(ttm_net, shares)
    eps_fwd = _div(fwd_net, shares)

    market_cap = price * shares if price and shares else None
    pe_ttm = _div(market_cap, ttm_net) if ttm_net and ttm_net > 0 else None
    pe_fwd = _div(market_cap, fwd_net) if fwd_net and fwd_net > 0 else None
    yield_fwd = _div(fwd_net, market_cap)

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
    if same_prev:
        r0 = _f(same_prev["items"].get("receivables"))
        r1 = _f(li.get("receivables"))
        if r0 and r1 and r0 > 0:
            rec_growth = r1 / r0 - 1

    payout = None
    if annual_prev and m == 12:
        payout = _div(-(_f(li.get("dividends_paid")) or 0.0), _profit(annual_prev["items"]))

    # ---- age -------------------------------------------------------------
    age_months = None
    today = today or jalali.today_jalali()
    try:
        ey, em = (int(p) for p in latest["end"].split("/")[:2])
        age_months = (today[0] - ey) * 12 + (today[1] - em)
    except (TypeError, ValueError, AttributeError):
        pass

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
    if growth_rev is not None:
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
    if net is not None and net < 0:
        flag("زیان‌ده در دوره‌ی اخیر")
    if cash_conversion is not None:
        if cash_conversion < 0.5:
            flag("فقط %.0f٪ سود به نقد عملیاتی تبدیل شده" % (cash_conversion * 100))
        elif cash_conversion >= 1.0:
            positives.append("نقد عملیاتی بیشتر از سود خالص (کیفیت سود خوب)")
    if rec_growth is not None and growth_rev is not None and \
            (rec_growth - growth_rev) * 100 > RECEIVABLE_GAP_PCT:
        flag("مطالبات %.0f٪ رشد کرده در برابر فروش %.0f٪ — فروش نسیه یا وصول کند"
             % (rec_growth * 100, growth_rev * 100))
    if interest_cover is not None and interest_cover < INTEREST_COVER_LOW:
        flag("سود عملیاتی فقط %.1f برابر هزینه مالی" % interest_cover)
    if fcf is not None and fcf < 0 and net_debt is not None and net_debt > 0:
        flag("جریان نقد آزاد منفی با بدهی خالص — سرمایه‌گذاری با وام")
    if eps_fwd is not None and eps_ttm is not None and eps_ttm > 0 and eps_fwd < eps_ttm * 0.9:
        flag("سود برآوردی سال جاری زیر سود ۱۲ ماه گذشته")
    if monthly:
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
            if (p.get("production_to_sales") or 0) > 1.3 and \
                    (p.get("amount_ytd") or 0) > 0:
                flag("تولید «%s» %.0f٪ بیشتر از فروش آن — انباشت موجودی"
                     % (p["name"], (p["production_to_sales"] - 1) * 100))
                break
        ratio = monthly.get("expense_to_income")
        if ratio is not None and monthly.get("template") == "insurance" and ratio > 0.7:
            flag("خسارت پرداختی %.0f٪ حق بیمه‌ی صادره" % (ratio * 100))

    bar = config.RISK_FREE_RATE
    if yield_fwd is not None and 0 < yield_fwd < bar:
        flag("بازده سود برآوردی %.0f٪ — کمتر از نرخ بدون ریسک %.0f٪"
             % (yield_fwd * 100, bar * 100))
    elif yield_fwd is not None and yield_fwd > 0 and pe_fwd is not None:
        positives.append("بازده سود برآوردی %.0f٪ (P/E آینده‌نگر %.1f)"
                         % (yield_fwd * 100, pe_fwd))

    return {
        "ins_code": str(ins_code),
        "latest": {"period_end": latest["end"], "months": m,
                   "audited": latest["audited"], "published": latest["published"],
                   "age_months": age_months},
        "has_prior_year": bool(annual_prev),
        "has_same_period": bool(same_prev),
        "method": method,
        "growth": {"revenue_pct": _pct(growth_rev), "profit_pct": _pct(growth_net),
                   "inflation_pct": round(inflation * 100, 1)},
        "margins": {"net_pct": _pct(margin), "net_prev_pct": _pct(margin_prev),
                    "gross_pct": _pct(_div(li.get("gross_profit"), rev)),
                    "operating_pct": _pct(_div(op, rev))},
        "ttm": {"revenue": ttm_rev, "profit": ttm_net, "eps": eps_ttm},
        "forward": {"revenue": fwd_rev, "profit": fwd_net, "eps": eps_fwd},
        "valuation": {"price": price, "market_cap": market_cap,
                      "pe_ttm": _round(pe_ttm), "pe_forward": _round(pe_fwd),
                      "earnings_yield_forward_pct": _pct(yield_fwd),
                      "pb": _round(pb), "roe_ttm_pct": _pct(roe_ttm),
                      "fcf_yield_pct": _pct(fcf_yield)},
        "cash": {"cash_conversion": _round(cash_conversion), "fcf": fcf,
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


def _round(x, digits=2):
    return None if x is None else round(x, digits)


_MARKET_DATE = []


def halted_since(ins_code):
    """The last date the symbol traded, if that was over a week before the
    market's newest session; None for a symbol that trades normally.

    The outlook prices a company at its last stored close. For a halted
    symbol that close is weeks or months old (بتهران, دتهران, شتهران on
    2026-11-05), and a P/E on it would read as today's.
    """

    from datetime import date

    conn = database._connect()
    if not _MARKET_DATE:
        _MARKET_DATE.append(conn.execute(
            "SELECT MAX(date) FROM price_history").fetchone()[0])
    last = conn.execute(
        "SELECT MAX(date) FROM price_history WHERE ins_code = ? AND volume > 0",
        (str(ins_code),)).fetchone()[0]
    conn.close()

    newest = _MARKET_DATE[0]
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


def screen(min_yield_pct=None, max_flags=1, limit=60, today=None):
    """Every company with a forward estimate, ranked by forward earnings yield.

    Only companies whose latest period is recent, with a prior year to
    complete it, and at most `max_flags` warnings, are ranked; the rest are
    counted, not hidden.
    """

    instruments = {str(r["ins_code"]): r
                   for r in database.load_instruments(equities_only=True)}
    prices = latest_prices()

    ranked, excluded = [], {"no_filing": 0, "stale": 0, "no_estimate": 0,
                            "flags": 0, "loss": 0}

    for code, inst in instruments.items():
        result = analyse(code, price=prices.get(code), today=today)
        if not result:
            excluded["no_filing"] += 1
            continue
        age = result["latest"]["age_months"]
        if age is not None and age > STALE_MONTHS:
            excluded["stale"] += 1
            continue
        ey = result["valuation"]["earnings_yield_forward_pct"]
        if ey is None or result["method"] == "annualised":
            excluded["no_estimate"] += 1
            continue
        if ey <= 0:
            excluded["loss"] += 1
            continue
        if len(result["flags"]) > max_flags:
            excluded["flags"] += 1
            continue
        if min_yield_pct is not None and ey < min_yield_pct:
            continue
        result.update(symbol=inst.get("symbol_fa"), name=inst.get("name_fa"),
                      sector=inst.get("sector_name"))
        ranked.append(result)

    ranked.sort(key=lambda r: -r["valuation"]["earnings_yield_forward_pct"])

    return {"ranked": ranked[:limit], "count": len(ranked),
            "excluded": excluded,
            "bar_pct": round(config.RISK_FREE_RATE * 100, 1)}
