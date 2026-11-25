"""Investment companies and holdings: their assets at today's prices, and at
the bot's fundamental values (2026-11-25).

WHY
---
The owner: "the system should compute the current value of these companies'
assets, and -- from the fundamental state of the shares in their portfolios,
which the bot already computes -- a forward-looking value of their assets."

Until now a NAV was the ن-۳۱ filing's period-end totals (nav.py), a month or
three old, carried at the index's move when stale: a company holding
petrochemicals in a month the banks rose read the banks' gain. The filing
lists every holding -- its share count, cost and market value -- so each can
be valued on its own.

TWO VALUES
----------
ارزش روز (value_today) -- each listed holding at TODAY's price:

    holding today = period-end shares x (capital increases since) x price
    NAV today     = the parent's own equity (its separate statement nearest
                    the portfolio's date)
                  + sum over listed holdings of (value today - cost)
                  + dividends its holdings declared since the period end
                  - the dividend the parent itself declared since

  Unlisted holdings stay at cost, as in the filing: no market prices them.
  A holding that cannot be matched to a symbol, or has no price, stays at
  its period-end value and is counted (`unpriced`).

ارزش آینده‌نگر (fundamental) -- each listed holding at the bot's own
fundamental value of that share instead of its price: fundamental_outlook.
fair_value(), trailing EPS at the industry's (or the share's own) P/E --
the same figure each company's card shows. It is a present value from
earnings multiples, the bot's stand-in for an NPV: there is no multi-year
cash-flow forecast to discount. A holding without one (a loss-maker, a
financial, a doubtful read) is carried at today's price, and the share of
the portfolio that has one is reported.

WHAT THESE ARE NOT
------------------
  * Tested. No ranking on them has been through a preregistered test; the
    multiples behind the fundamental value ranked operating companies no
    better than the index (E30-E33). They describe; they do not select.
  * Complete. Unlisted holdings at cost make NAV a floor where they are a
    large part of the company (`unlisted_share_pct`).
  * Exact. The parent's cash, debt and trades since the period end are not
    known until its next filing; a capital increase is taken as bonus
    shares (a cash one also cost the parent the subscription).
"""

import bisect
import re
import threading
import time
from datetime import date

import database
import jalali
from persian import normalize

# ==============================
# MATCHING A HOLDING TO ITS SYMBOL
# ==============================

# Letters Codal and TSETMC spell differently (آهن / اهن, تأمین / تامین).
_FOLD = str.maketrans({"آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا", "ؤ": "و",
                       "ئ": "ی", "ة": "ه", "ۀ": "ه"})

# Words that only decorate a company name; dropped from both sides.
_NOISE = {"شرکت", "سهامی", "عام", "خاص", "هلدینگ", "مادر", "تخصصی", "شرکتهای"}

# Too common to vouch for a match on their own (price + one shared word).
_COMMON = {"سرمایه", "گذاری", "سرمایهگذاری", "صنایع", "صنعتی", "گروه",
           "توسعه", "ایران", "ایرانیان", "پتروشیمی", "تولیدی", "بین",
           "المللی", "بینالمللی", "شیمی", "معدنی", "صنعت", "خدمات", "مدیریت",
           "تامین", "ملی", "پارس", "نفت", "گاز", "فولاد", "سیمان", "دارو",
           "داروسازی", "بانک", "بیمه", "اقتصاد", "کشت", "صنعتی", "استان"}

_RIGHTS = re.compile(r"\(?\s*حق\s*تقدم\s*\)?")
_PUNCT = re.compile(r"[()\[\]\-_.،,:؛/\\\"'«»]")

# A period-end close within this of the filing's per-share value is the
# same share; 0.5% when the name alone does not vouch for it.
PRICE_MATCH = 0.01
PRICE_ONLY_MATCH = 0.005


def _words(name):
    """(folded words, is a right) of a company name."""

    text = normalize(name or "").translate(_FOLD)
    rights = bool(_RIGHTS.search(text)) or bool(re.match(r"^ح\s*\.", text))
    text = _RIGHTS.sub(" ", text)
    text = re.sub(r"^ح\s*\.\s*", " ", text)
    # TSETMC abbreviates «سرمایه گذاری» as «س.» at the start of a name.
    text = re.sub(r"^س\s*\.\s*", "سرمایه گذاری ", text)
    text = _PUNCT.sub(" ", text)
    words = [w for w in text.split() if w and w not in _NOISE]
    return words, rights


def name_key(name):
    """A company name folded to compare across Codal and TSETMC."""

    words, rights = _words(name)
    return "".join(words), rights


_index = {"at": 0.0, "value": None}
_index_lock = threading.Lock()
INDEX_TTL = 600.0


def _instrument_index():
    """{"shares": {key: [code]}, "rights": {key: [code]}, "names": {code: words}}."""

    with _index_lock:
        if _index["value"] is not None and time.monotonic() - _index["at"] < INDEX_TTL:
            return _index["value"]
    conn = database._connect()
    try:
        rows = conn.execute("SELECT ins_code, symbol_fa, name_fa, kind "
                            "FROM instruments").fetchall()
    finally:
        conn.close()
    shares, rights, names, symbols, by_symbol = {}, {}, {}, {}, {}
    for code, symbol, name, kind in rows:
        if kind == "superseded":        # a board move's retired code
            continue
        key, is_right = name_key(name)
        if not key:
            continue
        is_right = is_right or kind == "rights"
        (rights if is_right else shares).setdefault(key, []).append(str(code))
        names[str(code)] = _words(name)[0]
        symbols[str(code)] = symbol
        if not is_right and symbol:
            folded = normalize(symbol).translate(_FOLD).replace(" ", "")
            by_symbol.setdefault(folded, []).append(str(code))
    value = {"shares": shares, "rights": rights, "names": names,
             "symbols": symbols, "by_symbol": by_symbol,
             "rights_codes": {c for codes in rights.values() for c in codes}}
    with _index_lock:
        _index.update(at=time.monotonic(), value=value)
        _matches.clear()            # matched against the index just replaced
    return value


def gregorian(jalali_text):
    """'1405/08/30' -> date, or None."""

    try:
        y, m, d = (int(p) for p in str(jalali_text).split("/")[:3])
        return date(*jalali.to_gregorian(y, m, d))
    except (TypeError, ValueError):
        return None


_closes = {}
_closes_lock = threading.Lock()

# Every holding matched so far, by (period end, name, per-share value): a
# screen of the market matches ~4,400 rows, the same ones each time.
_matches = {}


def closes_on(day):
    """{ins_code: raw close} at the last session on or before `day`."""

    key = day.isoformat()
    with _closes_lock:
        if key in _closes:
            return _closes[key]
    since = date.fromordinal(day.toordinal() - 20).isoformat()
    conn = database._connect()
    try:
        rows = conn.execute("""
            SELECT p.ins_code, p.close FROM price_history p
            JOIN (SELECT ins_code, MAX(date) AS d FROM price_history
                  WHERE date <= ? AND date >= ? GROUP BY ins_code) m
              ON m.ins_code = p.ins_code AND m.d = p.date
        """, (key + " 99", since)).fetchall()
    finally:
        conn.close()
    value = _Closes((str(c), float(v)) for c, v in rows if v)
    value.day = key
    with _closes_lock:
        if len(_closes) > 24:
            _closes.clear()
        _closes[key] = value
    return value


class _Closes(dict):
    """{ins_code: close}, with the codes whose close is near a value found
    by bisection instead of a scan of every code."""

    def __init__(self, items=()):
        super().__init__(items)
        self.sorted = sorted((v, c) for c, v in self.items())

    def near(self, value, tolerance):
        if not value:
            return []
        lo = bisect.bisect_left(self.sorted, (value * (1 - tolerance), ""))
        hi = bisect.bisect_right(self.sorted, (value * (1 + tolerance), "\uffff"))
        return [c for _v, c in self.sorted[lo:hi]]


def _near(a, b, tolerance):
    return bool(a and b) and abs(a / b - 1.0) <= tolerance


def match(row, period_closes, index=None):
    """(ins_code, how) for one listed holding, or (None, None).

    By name first; where the name finds several (or none), the filing's own
    period-end per-share value picks the one whose close that day was the
    same -- a check the name alone cannot give. A right (حق تقدم) is matched
    to the right if it is listed, else to its share ("underlying").
    """

    index = index or _instrument_index()
    memo = (getattr(period_closes, "day", None), row.get("name"),
            row.get("value_per_share"))
    if memo[0] is not None and memo in _matches:
        return _matches[memo]
    found = _match(row, period_closes, index)
    if memo[0] is not None:
        _matches[memo] = found
    return found


def _match(row, period_closes, index):
    key, is_right = name_key(row.get("name"))
    if not key:
        return None, None
    vps = row.get("value_per_share")
    pool = index["rights"] if is_right else index["shares"]

    def priced(codes, tolerance=PRICE_MATCH):
        return [c for c in codes if _near(period_closes.get(c), vps, tolerance)]

    found = pool.get(key, [])
    if len(found) == 1:
        return found[0], "name"
    if len(found) > 1:
        sure = priced(found)
        if len(sure) == 1:
            return sure[0], "name+price"

    # Some filings write the symbol, not the name («فزر», «صتوصل»).
    if not is_right and not found:
        by_symbol = index.get("by_symbol", {}).get(key, [])
        if len(by_symbol) == 1:
            return by_symbol[0], "symbol"

    # One name inside the other («پتروشیمی فجر» / «پتروشیمی فجر اصفهان»),
    # confirmed by the price.
    if vps:
        partial = [c for k, codes in pool.items()
                   if len(k) >= 4 and (k in key or key in k) for c in codes]
        sure = priced(partial)
        if len(sure) == 1:
            return sure[0], "partial+price"

        # The price, and one distinctive word in common.
        words = {w for w in _words(row.get("name"))[0]
                 if len(w) >= 3 and w not in _COMMON}
        if words:
            in_pool = index["rights_codes"] if is_right else None
            near = (period_closes.near(vps, PRICE_ONLY_MATCH)
                    if hasattr(period_closes, "near") else
                    [c for c, v in period_closes.items()
                     if _near(v, vps, PRICE_ONLY_MATCH)])
            sure = [c for c in near
                    if (c in in_pool if in_pool is not None
                        else c not in index["rights_codes"])
                    and words & set(index["names"].get(c) or ())]
            if len(sure) == 1:
                return sure[0], "price+word"

    if is_right:
        # The right itself is not listed (any more): its share is.
        code, how = _underlying(row, period_closes, index)
        if code:
            return code, "underlying"
    return None, None


def _underlying(row, period_closes, index):
    """The share a right (حق تقدم) is a right to."""

    return _match(dict(row, name=_RIGHTS.sub(" ", row.get("name") or ""),
                       value_per_share=None), period_closes, index)


# ==============================
# CORPORATE ACTIONS SINCE THE PERIOD END
# ==============================

_events = {"at": 0.0, "value": None}
_events_lock = threading.Lock()
EVENTS_TTL = 600.0


def _actions():
    """{ins_code: [(ex_date, kind, share_ratio, dps), ...]} since 1403."""

    with _events_lock:
        if _events["value"] is not None and time.monotonic() - _events["at"] < EVENTS_TTL:
            return _events["value"]
    out = {}
    try:
        import corporate_actions
        corporate_actions.ensure_tables()
        conn = database._connect()
        try:
            rows = conn.execute(
                "SELECT ins_code, ex_date, kind, share_ratio, dps "
                "FROM corporate_actions WHERE ex_date >= '2024-03-20' "
                "AND kind IN ('capital', 'dividend') "
                "ORDER BY ins_code, ex_date").fetchall()
        finally:
            conn.close()
        for code, ex_date, kind, ratio, dps in rows:
            out.setdefault(str(code), []).append((ex_date, kind, ratio, dps))
    except Exception:                                           # noqa: BLE001
        out = {}
    with _events_lock:
        _events.update(at=time.monotonic(), value=out)
    return out


def since(code, after, actions=None):
    """(share factor, dividends per period-end share) for `code` after the
    date `after`: what a holder of one share on that day holds and has been
    paid by now."""

    actions = _actions() if actions is None else actions
    factor, paid = 1.0, 0.0
    if after is None:
        return factor, paid
    cut = after.isoformat()
    for ex_date, kind, ratio, dps in actions.get(str(code), ()):
        if not ex_date or ex_date <= cut:
            continue
        if kind == "capital" and ratio and ratio > 0:
            factor *= float(ratio)
        elif kind == "dividend" and dps:
            paid += float(dps) * factor
    return factor, paid


# ==============================
# VALUING A PORTFOLIO
# ==============================

PAR = 1000.0
TOP_HOLDINGS = 12


def value_today(portfolio, rows, equity, shares, price, prices, parent=None,
                today=None, actions=None):
    """The parent's NAV with every listed holding at today's price.

    `portfolio`: the stored totals (load_codal_portfolio_latest's row);
    `rows`: its holdings (load_portfolio_rows); `equity`: the parent's own
    equity nearest the portfolio's date; `shares`, `price`: the parent's
    today; `prices`: {ins_code: today's price} (latest_prices). None without
    rows or equity.
    """

    if not rows or equity is None:
        return None

    period_end = portfolio.get("period_end")
    day = gregorian(period_end)
    period_closes = closes_on(day) if day else {}
    index = _instrument_index()
    actions = _actions() if actions is None else actions

    listed = [r for r in rows if r.get("section") == "listed"]
    unlisted_cost = sum(r.get("cost") or 0.0 for r in rows
                        if r.get("section") == "unlisted")

    # A right whose share is in the same filing under the same name is that
    # share's right, whatever the instrument list calls it (سنگ آهن گل گهر
    # and its right: the share matched by price, the right had no price).
    same_name = {}
    for row in listed:
        key, is_right = name_key(row.get("name"))
        if key and not is_right:
            code, how = match(row, period_closes, index)
            if code:
                same_name.setdefault(key, code)

    valued = []
    cost_total = period_total = today_total = dividends = 0.0
    priced_period = 0.0
    unpriced = adjusted = 0
    for row in listed:
        cost = row.get("cost") or 0.0
        period_value = row.get("market") or 0.0
        count = row.get("shares")
        vps = row.get("value_per_share")
        if not count and period_value and vps:
            count = period_value / vps
        code, how = match(row, period_closes, index)
        key, is_right = name_key(row.get("name"))
        if not code and is_right and same_name.get(key):
            code, how = same_name[key], "underlying"
        now = prices.get(code) if code else None
        if code and not now and code in index["rights_codes"]:
            # A right that no longer trades has become the share, bought at
            # par: worth the share's price less the par paid for it.
            under, _how = _underlying(row, period_closes, index)
            if under and prices.get(under):
                code, how, now = under, "underlying", prices[under]
        value = period_value
        factor = 1.0
        if code and now and count:
            factor, paid = since(code, day, actions)
            if how == "underlying":
                value = count * factor * max(now - PAR, 0.0)
            else:
                value = count * factor * now
                dividends += count * paid
            priced_period += period_value
            if abs(factor - 1.0) > 1e-9:
                adjusted += 1
        else:
            unpriced += 1
        cost_total += cost
        period_total += period_value
        today_total += value
        valued.append({"name": row.get("name"), "ins_code": code,
                       "right": bool(is_right or how == "underlying"
                                     or (code in index["rights_codes"])),
                       "symbol": index["symbols"].get(code) if code else None,
                       "match": how, "shares": count, "factor": factor,
                       "cost": cost, "period_value": period_value,
                       "value_today": value, "price": now if code else None,
                       "priced": bool(code and now and count)})

    own_dividend = 0.0
    if parent and day and shares:
        factor, paid = since(parent, day, actions)
        # paid is per share held at the period end; today's count is
        # `factor` times that many.
        own_dividend = paid * shares / factor if factor else 0.0

    nav = equity + (today_total - cost_total) + dividends - own_dividend
    out = {
        "nav": nav,
        "per_share": nav / shares if shares else None,
        "price_to_nav": (price * shares / nav) if price and shares and nav > 0 else None,
        "period_end": period_end,
        "equity": equity,
        "listed_cost": cost_total,
        "listed_period": period_total,
        "listed_today": today_total,
        "listed_change_pct": ((today_total / period_total - 1) * 100
                              if period_total else None),
        "unlisted_cost": unlisted_cost,
        "unlisted_share_pct": (unlisted_cost / nav * 100) if nav > 0 else None,
        "other_net": equity - cost_total - unlisted_cost,
        "dividends_in": dividends,
        "own_dividend": own_dividend,
        "holdings": len(listed),
        "unlisted_holdings": len(rows) - len(listed),
        "unpriced": unpriced,
        "priced_pct": (priced_period / period_total * 100) if period_total else None,
        "capital_adjusted": adjusted,
        "complete": unlisted_cost <= 0,
    }
    return out, valued


def fundamental(today_result, valued, fair_by_code, price=None, shares=None):
    """The NAV with each listed holding at the bot's fundamental value.

    `fair_by_code`: {ins_code: fair_value() of that share} -- central, low,
    high per share, on its basis. Only a share valued on earnings ("pe",
    not doubtful) counts; every other holding stays at today's price.
    """

    if not today_result:
        return None
    gain = {"central": 0.0, "low": 0.0, "high": 0.0}
    covered = 0.0
    for row in valued:
        code = row.get("ins_code")
        fair = fair_by_code.get(code) if code else None
        row["fair_per_share"] = None
        row["fair_value"] = None
        if not (fair and row.get("priced") and row.get("match") != "underlying"
                and fair.get("basis") == "pe" and not fair.get("doubtful")
                and fair.get("central")):
            continue
        count = (row.get("shares") or 0.0) * (row.get("factor") or 1.0)
        for part in gain:
            per_share = fair.get(part) or fair["central"]
            gain[part] += count * per_share - row["value_today"]
        row["fair_per_share"] = fair["central"]
        row["fair_value"] = count * fair["central"]
        covered += row["value_today"]

    nav = today_result["nav"]
    out = {part: nav + gain[part] for part in gain}
    listed_today = today_result.get("listed_today") or 0.0
    return {
        "nav": out["central"], "low": min(out.values()), "high": max(out.values()),
        "per_share": out["central"] / shares if shares else None,
        "price_to_value": (price * shares / out["central"])
                          if price and shares and out["central"] > 0 else None,
        "change_vs_today_pct": ((out["central"] / nav - 1) * 100) if nav > 0 else None,
        "covered_pct": (covered / listed_today * 100) if listed_today else None,
    }


def top(valued, limit=TOP_HOLDINGS):
    """The largest holdings today, for the card and the PDF."""

    total = sum(r["value_today"] for r in valued) or 0.0
    rows = sorted(valued, key=lambda r: -(r["value_today"] or 0.0))[:limit]
    return [{
        "name": r["name"], "symbol": r.get("symbol"), "ins_code": r.get("ins_code"),
        "right": bool(r.get("right")),
        "shares": r.get("shares"), "value_today": r["value_today"],
        "weight_pct": (r["value_today"] / total * 100) if total else None,
        "change_pct": ((r["value_today"] / r["period_value"] - 1) * 100
                       if r.get("period_value") else None),
        "priced": r.get("priced"), "match": r.get("match"),
        "fair_per_share": r.get("fair_per_share"), "fair_value": r.get("fair_value"),
        "price": r.get("price"),
    } for r in rows]
