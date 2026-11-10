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
    frame = database.load_history_by_inscode(ins_code, limit=2)
    if frame is None or frame.empty:
        return None, None, None
    close = float(frame["close"].iloc[-1])
    previous = float(frame["close"].iloc[-2]) if len(frame) > 1 else None
    return close, previous, str(frame["date"].iloc[-1])[:10]


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
            rows.append({"symbol": symbol, "error": "نماد پیدا نشد"})
            continue
        code = str(inst["ins_code"])
        close, previous, as_of = _last_two(code)
        row = {
            "symbol": inst.get("symbol_fa"), "name": inst.get("name_fa"),
            "ins_code": code, "sector": (inst.get("sector_name") or "").strip() or None,
            "equity": bool(inst.get("is_equity")),
            "quantity": quantity, "price": close, "as_of": as_of,
            "change_pct": round((close / previous - 1) * 100, 2)
            if close and previous else None,
            "value": close * quantity if close and quantity else None,
            "halted_since": halted.get(code),
        }
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
            row["dividend"] = {
                "dps": dividend["dps"],
                "fiscal_year_end": dividend.get("fiscal_year_end"),
                "yield_pct": round(dividend["dps"] / close * 100, 2) if close else None,
            }
        row["announcements"] = [
            {"published_at": a.get("published_at"), "kind": a.get("kind_label"),
             "title": a.get("title"), "url": a.get("url")}
            for a in codal_announcements.recent(days=ANNOUNCEMENT_DAYS,
                                                ins_code=code, limit=3)]
        rows.append(row)

    return _summarise(rows)


def _summarise(rows):
    total = sum(r["value"] for r in rows if r.get("value"))
    warnings = []

    for r in rows:
        r["weight_pct"] = round(r["value"] / total * 100, 1) \
            if total and r.get("value") else None

    if total:
        for r in rows:
            if r.get("value") and r["value"] / total > CONCENTRATION_NAME:
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

    as_of = max((r["as_of"] for r in rows if r.get("as_of")), default=None)
    return {"rows": rows, "total_value": total or None, "warnings": warnings,
            "as_of": as_of, "count": len(rows)}
