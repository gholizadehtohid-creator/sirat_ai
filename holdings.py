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


# A Latin comma separates entries only when it is not a thousands group: the
# broker apps write «1,000,000», and splitting there read it as 1 share and a
# symbol «000» -- the book off a thousandfold and the today card ordering
# trades on it (the full test, 2026-11-12). \d matches Persian digits too.
_SPLIT = re.compile(r"[\r\n،;]+|,(?!\d{3}(?!\d))")
_NUMBER = re.compile(r"^[\d.,٬]+$")


def parse_with_errors(text):
    """(entries, errors): parse()'s entries, and {symbol: why its quantity
    could not be read} -- e.g. «12 500», two numbers where one was meant."""

    order, quantities, errors = [], {}, {}
    # Split first: normalising folds a newline into a space, and two lines
    # then read as one symbol with stray words after it.
    for chunk in _SPLIT.split(text or ""):
        parts = persian.normalize(chunk).split()
        if not parts:
            continue
        symbol = parts[0]
        numbers = [p for p in parts[1:] if _NUMBER.match(p)]
        quantity = None
        if len(numbers) == 1:
            try:
                quantity = float(numbers[0].replace(",", "").replace("٬", ""))
            except ValueError:
                quantity = None
        elif len(numbers) > 1:
            errors[symbol] = ("تعداد «%s» خوانا نیست (چند عدد جدا از هم)؛ "
                              "ارقام را بدون فاصله بنویسید" % " ".join(parts[1:]))
        if symbol not in quantities:
            order.append(symbol)
            quantities[symbol] = quantity
        elif quantity is not None:
            quantities[symbol] = (quantities[symbol] or 0) + quantity
    return [(s, quantities[s]) for s in order[:MAX_SYMBOLS]], errors


def parse(text):
    """'فولاد 1,000, شپنا' -> [('فولاد', 1000.0), ('شپنا', None)].

    One symbol per line or comma (Persian or Latin), an optional quantity
    after it, thousands separators allowed. Digits may be Persian. Repeats
    are merged, quantities added.
    """

    return parse_with_errors(text)[0]


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
    bar = None
    if frame is not None and not frame.empty:
        bar = (float(frame["close"].iloc[-1]),
               float(frame["close"].iloc[-2]) if len(frame) > 1 else None,
               str(frame["date"].iloc[-1])[:10], "history")
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
    snap = None
    if row:
        close = row[0] if row[0] and row[0] > 0 else row[1]
        if close and close > 0:
            previous = row[2] if row[2] and row[2] > 0 else None
            snap = (float(close), previous, snapshot_session(row[3]), "snapshot")
    # The fresher of the two: in the evening, before the history step has
    # stored the day, an equity priced from its bar was a session behind
    # the funds priced from the watch, under one "prices as of" date (the
    # full test, 2026-11-12).
    if bar and snap and snap[2] and snap[2] > bar[2]:
        return snap
    return bar or snap or (None, None, None, None)


TRADING_WEEKDAYS = (5, 6, 0, 1, 2)       # Saturday .. Wednesday


def snapshot_session(captured_at):
    """The session a market-watch capture prices, as 'YYYY-MM-DD'.

    The capture stamp is UTC; the session is Tehran's (UTC+3:30). A capture
    before 09:00, or on a Thursday or Friday, prices the session before --
    the UTC capture date read Thursday for Wednesday's close (the full test,
    2026-11-12). A weekday holiday is not known here.
    """

    from datetime import datetime, timedelta, timezone

    try:
        stamp = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(timezone(timedelta(hours=3, minutes=30)))
    day = local.date()
    if local.hour < 9:
        day -= timedelta(days=1)
    while day.weekday() not in TRADING_WEEKDAYS:
        day -= timedelta(days=1)
    return day.isoformat()


def _last_trade(ins_code):
    conn = database._connect()
    try:
        row = conn.execute("SELECT MAX(date) FROM price_history WHERE "
                           "ins_code = ? AND volume > 0", (str(ins_code),)).fetchone()
    except Exception:                                           # noqa: BLE001
        row = None
    finally:
        conn.close()
    return str(row[0])[:10] if row and row[0] else None


def bill_quotes():
    """{normalised symbol: quote} the bills card last read (data/bill_quotes.json)."""

    import json
    import os

    import opportunities
    try:
        with open(opportunities.BILL_QUOTES_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


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
    if (inst.get("kind") or "") == "rights":
        return "equity"              # a rights issue is the company's shares
    if (inst.get("kind") or "") != "fund":
        return None
    import opportunities
    sleeve = opportunities.fund_sleeve(inst.get("name_fa") or "")
    if sleeve in ("equity", "equity_active"):
        return "equity"
    return sleeve


def review(entries, parse_errors=None):
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
    bands = fo.price_bands()
    quotes = bill_quotes()
    try:
        newest = database.newest_equity_bar()
        newest = str(newest)[:10] if newest is not None else None
    except Exception:                                           # noqa: BLE001
        newest = None
    parse_errors = parse_errors or {}

    rows = []
    for symbol, quantity in entries:
        if symbol in parse_errors:
            rows.append({"symbol": symbol, "quantity": None,
                         "error": parse_errors[symbol], "parse_error": True})
            continue
        inst = instruments.get(persian.normalize_symbol(symbol))
        if not inst:
            # Treasury bills (اخزا) are not in the instrument list: priced
            # here from the quotes the bills card last read (the holdings
            # total, weights and warnings left bills out while the panel
            # priced them -- the full test, 2026-11-12), and filed under
            # fixed income. Without a quote the panel prices them itself.
            bill = persian.normalize(symbol).startswith("اخزا")
            quote = quotes.get(persian.normalize(symbol).replace(" ", "")) if bill else None
            price = (quote or {}).get("price")
            rows.append({"symbol": symbol, "quantity": quantity,
                         "sleeve": "fixed" if bill else None,
                         "bill": bill, "price": price,
                         "as_of": (quote or {}).get("price_date"),
                         "ytm_pct": (quote or {}).get("ytm_pct"),
                         "value": price * quantity if price and quantity else None,
                         "error": None if bill else "نماد پیدا نشد"})
            continue
        code = str(inst["ins_code"])
        close, previous, as_of, source = _last_two(code)
        last_trade = _last_trade(code)
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
            # In the halted map with None: never traded at all. halted.get()
            # read that as "not halted", and a never-traded name stood at its
            # 1,000-rial placeholder with a green "no warning" (the full test).
            "halted": code in halted,
            "halted_since": halted.get(code) if code in halted else None,
            "last_trade": last_trade,
            "missed_last_session": bool(newest and last_trade and last_trade < newest
                                        and code not in halted
                                        and bool(inst.get("is_equity"))),
        }
        row["change_beyond_band"] = _beyond_band(row["change_pct"], bands.get(code))
        if row["equity"] and code not in halted:
            outlook = fo.analyse(code, price=close, dividend=dividends.get(code),
                                 shares=shares.get(code), sector=row["sector"],
                                 opportunity=opportunity)
            if outlook:
                v = outlook["valuation"]
                row["outlook"] = {
                    "kind": outlook["kind"],
                    "pe_forward": v["pe_forward"],
                    "pe_ttm": v["pe_ttm"],
                    "price_to_nav": v["price_to_nav"],
                    "price_to_nav_est": v.get("price_to_nav_est"),
                    "nav_stale": v.get("nav_stale"),
                    "nav_period": v.get("nav_period"),
                    "nav_notes": v.get("nav_notes") or [],
                    "flags": outlook["flags"],
                    "positives": outlook["positives"],
                    "latest_period": outlook["latest"]["period_end"],
                }
                # The outlook's own reading of the dividend: on today's share
                # count, and withheld when it is an older year's -- the card
                # quoted payouts up to five years old the outlook in the same
                # row refused (the full test, 2026-11-12).
                if v.get("dps") is not None:
                    row["dividend"] = {
                        "dps": v.get("dps"),
                        "fiscal_year_end": v.get("dividend_fiscal_year"),
                        "yield_pct": v.get("dividend_yield_pct"),
                        "stale": bool(v.get("dividend_stale")),
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


def _beyond_band(change_pct, band=None):
    """True when a day's change is more than the symbol's own band allows.

    `band` is the market watch's (previous close, upper, lower). A flat
    3.6% called a gold fund's ordinary ±10% day and a leveraged fund's ±6%
    day "a capital increase, split or reopening" (the full test, 2026-11-12).
    """

    import config

    if change_pct is None:
        return False
    limit = BAND_EXCESS_PCT if BAND_EXCESS_PCT is not None \
        else config.CORPORATE_ACTION_THRESHOLD
    prev, up, down = band if band else (None, None, None)
    if prev and up and down and prev > 0:
        width = max(up / prev - 1.0, 1.0 - down / prev) * 100
        if 0 < width < 50:
            limit = width + 0.6
    return abs(change_pct) > limit


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
    # A bill has no instrument code; it stands in on its own yield (below).
    positions += [{"ins_code": "bill:" + r["symbol"], "symbol": r["symbol"],
                   "position_value": r["value"], "sector_name": "اوراق خزانه"}
                  for r in rows if r.get("bill") and r.get("value")]
    if not positions or not total:
        return None
    # The rial VaR is struck on the holdings that could be measured (risk.py
    # since 2026-11-11): a name dropped for too little history is named in
    # names_dropped and is not in the figure. `total` stays the caller's test.
    # Funds with no daily bars of their own (every equity and fixed-income
    # fund) stand in on what they hold: an index fund on its index, a
    # fixed-income fund on a steady bill rate. Without it the plan's own
    # 70/30 book was measured on its gold fund alone -- beta -0.17 (the full
    # test, 2026-11-12). The card names the stand-ins.
    proxies, proxied = _proxies(rows)
    report = risk.analyse(positions, proxies=proxies)
    if not report.get("available"):
        return {"available": False, "reason": report.get("reason"),
                "notes": report.get("notes") or []}
    corr = report.get("correlation") or {}
    return {
        "available": True,
        "sessions": report.get("sessions"),
        "measured_pct": round((report.get("measured_pct") or 0) * sum(
            p["position_value"] for p in positions) / total, 1)
        if report.get("measured_pct") is not None else None,
        "proxied": proxied,
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


def _proxies(rows):
    """({ins_code: stand-in daily returns}, [what each stands in on])."""

    import pandas as pd

    import config

    out, named = {}, []
    index_returns = {}

    def index_series(key):
        if key not in index_returns:
            frame = database.load_index_history(config.INDICES[key]["insCode"])
            if frame is None or frame.empty:
                index_returns[key] = None
            else:
                frame = frame.copy()
                frame["date"] = pd.to_datetime(frame["date"])
                index_returns[key] = (frame.set_index("date")["close"]
                                      .astype(float).pct_change().dropna())
        return index_returns[key]

    for r in rows:
        if r.get("bill") and r.get("value"):
            base = index_series("TEDPIX")
            rate = (r.get("ytm_pct") or 0) / 100.0
            if base is not None and rate > 0:
                out["bill:" + r["symbol"]] = pd.Series(
                    (1 + rate) ** (1 / 240.0) - 1, index=base.index)
                named.append("%s ← بازده تا سررسید %.0f٪" % (r["symbol"], rate * 100))
            continue
        code = r.get("ins_code")
        if not code or not r.get("value") or r.get("equity"):
            continue
        frame = database.load_history_by_inscode(code, limit=2)
        if frame is not None and not frame.empty:
            continue                                # it has bars of its own
        name = r.get("name") or ""
        if r.get("sleeve") == "equity":
            # normalize drops the ZWNJ: «هم‌وزن» reads «هموزن».
            folded = persian.normalize(name).replace(" ", "")
            key = "TEDPIX_EW" if "هموزن" in folded else "TEDPIX"
            series = index_series(key)
            if series is not None:
                out[str(code)] = series
                named.append("%s ← %s" % (r["symbol"], config.INDICES[key]["name"]))
        elif r.get("sleeve") == "fixed":
            base = index_series("TEDPIX")
            if base is not None:
                import opportunities
                rate = opportunities.FUND_RATE
                out[str(code)] = pd.Series((1 + rate) ** (1 / 240.0) - 1,
                                           index=base.index)
                named.append("%s ← نرخ ثابت %.0f٪" % (r["symbol"], rate * 100))
    return out, named


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
        if r.get("halted"):
            warnings.append("%s از %s معامله نشده (متوقف)"
                            % (r["symbol"], r["halted_since"] or "هیچ‌گاه"))
        elif r.get("missed_last_session"):
            warnings.append("%s در آخرین جلسه‌ی بازار معامله نشد (آخرین معامله "
                            "%s)؛ تغییر روز و ریسکش را صفر نخوانید"
                            % (r["symbol"], r.get("last_trade")))
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
