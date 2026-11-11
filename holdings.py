"""The user's own holdings, at one glance.

WHY THIS EXISTS
---------------
Five preregistered tests (E19-E32) found no stock-selection rule in this
repository that beat holding the market; the two that passed (E27, E28) are
about WHAT TO HOLD. What the evidence leaves a manual investor is: hold the
market and gold in funds, and watch what you already own. The panel had no
place for the second -- the guide told the user to "check the stocks you
own" and nothing listed them.

This reads only what is stored (no request to TSETMC or Codal): for each
symbol the last close and the day's change, the position's value and weight
when a quantity is given, the outlook's warnings for an operating company,
the last AGM's cash dividend, and the newest Codal announcements. Across the
book: one name or one industry carrying too much of it, halted symbols, and
names with several warnings.

It recommends nothing. Every number is the same one the rest of the panel
shows for that symbol.
"""

import re

import database
import fundamental_outlook as fo
import persian

MAX_SYMBOLS = 40

# One name, or one industry, past these shares of the book is called out.
CONCENTRATION_NAME = 0.20
CONCENTRATION_SECTOR = 0.40

# An outlook with at least this many warnings is called out by name.
MANY_FLAGS = 2

ANNOUNCEMENT_DAYS = 90

# A day's move past what the price band permits is a capital increase, a
# unit split or a reopening -- not a day's gain or loss to report as one.
BAND_EXCESS_PCT = None      # None: config.CORPORATE_ACTION_THRESHOLD


def parse(text):
    """'فولاد 1000, شپنا' -> [('فولاد', 1000.0), ('شپنا', None)].

    One symbol per line or comma (Persian or Latin), an optional quantity
    after it. Digits may be Persian. Repeats are merged, quantities added.
    """

    order, quantities = [], {}
    # Split first: normalising folds a newline into a space, and two lines
    # then read as one symbol with stray words after it.
    for chunk in re.split(r"[,\r\n،;]+", text or ""):
        parts = persian.normalize(chunk).split()
        if not parts:
            continue
        symbol = parts[0]
        quantity = None
        if len(parts) > 1:
            digits = parts[1].replace(",", "").replace("٬", "")
            try:
                quantity = float(digits)
            except ValueError:
                quantity = None
        if symbol not in quantities:
            order.append(symbol)
            quantities[symbol] = quantity
        elif quantity is not None:
            quantities[symbol] = (quantities[symbol] or 0) + quantity
    return [(s, quantities[s]) for s in order[:MAX_SYMBOLS]]


def _last_two(ins_code):
    """(close, previous close, as-of date, source).

    The daily bars first. No equity or fixed-income fund has any (the
    collector fetches equities and, since 11-05, gold funds), so a book that
    followed the plan -- 70% in an index fund -- valued that 70% at nothing,
    and the today card told its owner, the day after, to buy it again and
    sell the gold (the panel review, 2026-11-11). The market watch prices
    every traded instrument, funds included: its final price stands in.
    """

    frame = database.load_history_by_inscode(ins_code, limit=2)
    if frame is not None and not frame.empty:
        close = float(frame["close"].iloc[-1])
        previous = float(frame["close"].iloc[-2]) if len(frame) > 1 else None
        return close, previous, str(frame["date"].iloc[-1])[:10], "history"
    conn = database._connect()
    try:
        row = conn.execute(
            "SELECT close, last, prev_close, captured_at FROM market_snapshot "
            "WHERE ins_code = ? ORDER BY captured_at DESC LIMIT 1",
            (str(ins_code),)).fetchone()
    except Exception:                                           # noqa: BLE001
        row = None
    finally:
        conn.close()
    if not row:
        return None, None, None, None
    close = row[0] if row[0] and row[0] > 0 else row[1]
    if not close or close <= 0:
        return None, None, None, None
    previous = row[2] if row[2] and row[2] > 0 else None
    return float(close), previous, str(row[3] or "")[:10] or None, "snapshot"


def sleeve_of(inst):
    """Which allocation sleeve a holding counts toward: equity, gold, fixed,
    or None for one the plan has no sleeve for (a leveraged or mixed fund).

    Decided here, by the rule the opportunities card files funds by
    (opportunities.fund_sleeve). The panel's own name match read 33 of the 40
    fixed-income funds that end in «-د» (اوند among them) as equity, and two
    equity funds with «زر» in the name as gold.
    """

    if inst.get("is_equity"):
        return "equity"
    if (inst.get("kind") or "") != "fund":
        return None
    import opportunities
    sleeve = opportunities.fund_sleeve(inst.get("name_fa") or "")
    if sleeve in ("equity", "equity_active"):
        return "equity"
    return sleeve


def review(entries):
    """The book: one row per symbol, totals, and what to look at."""

    import codal_announcements

    instruments = {}
    for row in database.load_instruments(equities_only=False):
        key = persian.normalize_symbol(row.get("symbol_fa") or "")
        if key and key not in instruments:
            instruments[key] = row

    halted = database.halted_codes()
    dividends = codal_announcements.latest_dividends()
    shares = fo.exchange_shares()
    opportunity = fo.opportunity_rate()

    rows = []
    for symbol, quantity in entries:
        inst = instruments.get(persian.normalize_symbol(symbol))
        if not inst:
            # Treasury bills (اخزا) are not in the instrument list: the
            # panel prices them from the bills it read and files them under
            # fixed income, so the quantity travels with the row.
            bill = persian.normalize(symbol).startswith("اخزا")
            rows.append({"symbol": symbol, "quantity": quantity,
                         "sleeve": "fixed" if bill else None,
                         "bill": bill,
                         "error": None if bill else "نماد پیدا نشد"})
            continue
        code = str(inst["ins_code"])
        close, previous, as_of, source = _last_two(code)
        row = {
            "symbol": inst.get("symbol_fa"), "name": inst.get("name_fa"),
            "ins_code": code, "sector": (inst.get("sector_name") or "").strip() or None,
            "equity": bool(inst.get("is_equity")),
            "sleeve": sleeve_of(inst),
            "quantity": quantity, "price": close, "as_of": as_of,
            "price_source": source,
            "change_pct": round((close / previous - 1) * 100, 2)
            if close and previous else None,
            "value": close * quantity if close and quantity else None,
            "halted_since": halted.get(code),
        }
        row["change_beyond_band"] = _beyond_band(row["change_pct"])
        if row["equity"] and code not in halted:
            outlook = fo.analyse(code, price=close, dividend=dividends.get(code),
                                 shares=shares.get(code), sector=row["sector"],
                                 opportunity=opportunity)
            if outlook:
                row["outlook"] = {
                    "kind": outlook["kind"],
                    "pe_forward": outlook["valuation"]["pe_forward"],
                    "pe_ttm": outlook["valuation"]["pe_ttm"],
                    "price_to_nav": outlook["valuation"]["price_to_nav"],
                    "flags": outlook["flags"],
                    "positives": outlook["positives"],
                    "latest_period": outlook["latest"]["period_end"],
                }
        dividend = dividends.get(code)
        if dividend and dividend.get("dps") is not None:
            # On today's share count, as the outlook reads it (dps_today).
            dps, _old = fo.dps_today(dividend, shares.get(code))
            row["dividend"] = {
                "dps": dps,
                "fiscal_year_end": dividend.get("fiscal_year_end"),
                "yield_pct": round(dps / close * 100, 2) if close and dps is not None else None,
            }
        row["announcements"] = [
            {"published_at": a.get("published_at"), "kind": a.get("kind_label"),
             "title": a.get("title"), "url": a.get("url")}
            for a in codal_announcements.recent(days=ANNOUNCEMENT_DAYS,
                                                ins_code=code, limit=3)]
        rows.append(row)

    book = _summarise(rows)
    book["risk"] = _risk(rows, book["total_value"])
    return book


def _beyond_band(change_pct):
    """True when a day's change is more than the price band allows."""

    import config

    limit = BAND_EXCESS_PCT if BAND_EXCESS_PCT is not None         else config.CORPORATE_ACTION_THRESHOLD
    return change_pct is not None and abs(change_pct) > limit


def _risk(rows, total):
    """The book's own risk, by the arithmetic the basket's risk card uses.

    Volatility, beta to the index, a bad day's loss in rials and the deepest
    fall -- all of them for TODAY's weights carried back over the last
    sessions, which is what the reader holds now, not what they held then.
    risk.returns_matrix drops a capital-increase day instead of reading it as
    a 40% loss. None without quantities: weights need values.
    """

    import risk

    positions = [{"ins_code": r["ins_code"], "symbol": r["symbol"],
                  "position_value": r["value"],
                  "sector_name": r.get("sector") or "بدون صنعت"}
                 for r in rows if r.get("value") and r.get("ins_code")]
    if not positions or not total:
        return None
    # The rial VaR is struck on the holdings that could be measured (risk.py
    # since 2026-11-11): a name dropped for too little history is named in
    # names_dropped and is not in the figure. `total` stays the caller's test.
    report = risk.analyse(positions)
    if not report.get("available"):
        return {"available": False, "reason": report.get("reason"),
                "notes": report.get("notes") or []}
    corr = report.get("correlation") or {}
    return {
        "available": True,
        "sessions": report.get("sessions"),
        "names_measured": report.get("names_measured"),
        "names_dropped": report.get("names_dropped") or [],
        "volatility": report.get("volatility"),
        "var": report.get("var"),
        "drawdown": report.get("drawdown"),
        "beta": report.get("beta"),
        "benchmark": report.get("benchmark"),
        "average_correlation": corr.get("average"),
        "concentration": report.get("concentration"),
        "notes": report.get("notes") or [],
    }


def _summarise(rows):
    total = sum(r["value"] for r in rows if r.get("value"))
    warnings = []

    for r in rows:
        r["weight_pct"] = round(r["value"] / total * 100, 1) \
            if total and r.get("value") else None

    if total:
        for r in rows:
            # A share, not a fund: an index fund at 70% of the book is the
            # plan itself, not one name's risk.
            if r.get("equity") and r.get("value") and \
                    r["value"] / total > CONCENTRATION_NAME:
                warnings.append("%s %.0f٪ سبد است؛ بیش از %.0f٪ در یک نماد ریسک "
                                "تمرکز دارد" % (r["symbol"], r["value"] / total * 100,
                                                CONCENTRATION_NAME * 100))
        sectors = {}
        for r in rows:
            if r.get("value") and r.get("sector"):
                sectors[r["sector"]] = sectors.get(r["sector"], 0) + r["value"]
        for sector, value in sorted(sectors.items(), key=lambda kv: -kv[1]):
            if value / total > CONCENTRATION_SECTOR:
                warnings.append("صنعت «%s» %.0f٪ سبد است؛ بیش از %.0f٪ در یک صنعت"
                                % (sector, value / total * 100,
                                   CONCENTRATION_SECTOR * 100))

    for r in rows:
        if r.get("halted_since"):
            warnings.append("%s از %s معامله نشده (متوقف)" % (r["symbol"], r["halted_since"]))
        flags = (r.get("outlook") or {}).get("flags") or []
        if len(flags) >= MANY_FLAGS:
            warnings.append("%s %d هشدار بنیادی دارد؛ جزئیات در ردیف خودش"
                            % (r["symbol"], len(flags)))
        if r.get("error"):
            warnings.append("«%s»: %s" % (r["symbol"], r["error"]))
        elif r.get("quantity") and not r.get("value"):
            warnings.append("«%s»: قیمتی در دست نیست، پس در جمع سبد و وزن‌ها "
                            "نیامد" % r["symbol"])

    # Positions the total leaves out: with any, a weight or a rebalance
    # amount computed from the total is wrong by their value.
    unpriced = [r["symbol"] for r in rows
                if r.get("quantity") and not r.get("value")]
    as_of = max((r["as_of"] for r in rows if r.get("as_of")), default=None)
    return {"rows": rows, "total_value": total or None, "warnings": warnings,
            "unpriced": unpriced, "as_of": as_of, "count": len(rows)}
