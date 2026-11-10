"""Filed financials -> a Fundamental Score, grouped the way a decision needs it.

WHAT THIS ADDS OVER fundamental_scoring.py, AND WHAT IT DELIBERATELY REUSES
--------------------------------------------------------------------------
`fundamental_scoring.py` already turns a ratio set into 0-100 over six pillars,
with the coverage renormalisation that keeps a bank from being punished for
having no gross margin. Those bands were calibrated against this market and are
what the v2 backtest measured, so NONE of them are reimplemented here -- this
module calls the same pillar functions.

What it adds is three things the pillar model does not have:

  FOUR GROUPS INSTEAD OF SIX PILLARS. A decision engine asks four questions --
  is the business good, is it growing, is it cheap, and what could go wrong --
  and the six pillars answer them at the wrong granularity. Profitability and
  earnings quality are one question (is the profit real); financial health
  splits across two (moderate gearing is a quality matter, gearing past
  DEBT_HIGH is a solvency one).

  EARNINGS STABILITY. Nothing in the pillar model looks across periods. Two
  companies with identical latest statements are not the same investment when
  one has held a 12% margin for four quarters and the other swung from -8% to
  +12%, and on a swing horizon that difference is the risk. Measured on net
  margin rather than on profit because filed periods differ in length and a
  margin is already period-free.

  RISK AS A CEILING, NOT A COMPONENT. This is the substantive change. In a
  weighted average, a loss-making company with negative operating cash flow and
  a P/E-free balance sheet full of debt still scores in the sixties as long as
  it looks cheap -- the valuation pillar carries it and the average dilutes
  everything else. Cheapness is not a defence against insolvency, so the risk
  flags impose a CEILING on the total. A company that cannot pay its bills is
  capped no matter how cheap it is, and the cap is reported by name so nobody
  has to reverse-engineer why the score stopped where it did.

MISSING IS STILL NOT ZERO. Every group renormalises over the inputs that
exist, absent groups drop out of the blend, and below FUND_GROUP_MIN_COVERAGE
the verdict is INSUFFICIENT_DATA rather than a number. A company whose
statements this project has not collected is not a bad company.

WHERE THE DATA COMES FROM
-------------------------
`evaluate()` is pure: hand it ratios and it scores them, which is what makes it
testable and what lets the backtest feed it point-in-time figures.
`evaluate_stored()` is the live path and reads fundamental_db.py -- the weekly
store -- never codal.ir. There is no code path from a trading decision to a
network call in this module.
"""

import math

import config
import fundamental_scoring


QUALITY = "quality"
GROWTH = "growth"
VALUATION = "valuation"
RISK = "risk"

GROUPS = (QUALITY, GROWTH, VALUATION, RISK)

GROUP_LABELS = {
    QUALITY: "کیفیت",
    GROWTH: "رشد",
    VALUATION: "ارزش‌گذاری",
    RISK: "ریسک",
}

SCORED = "SCORED"
INSUFFICIENT = "INSUFFICIENT_DATA"


# ==============================
# RISK FLAGS
# ==============================
#
# Each flag carries a severity, which drives the risk group's own score, and
# optionally a ceiling on the total. The two are separate on purpose: a thin
# current ratio degrades the risk reading without capping the company, while
# negative equity caps it regardless of what the other three groups say.

CRITICAL = "CRITICAL"
MAJOR = "MAJOR"
MINOR = "MINOR"

SEVERITY_PENALTY = {
    CRITICAL: 0.60,
    MAJOR: 0.35,
    MINOR: 0.15,
}

FLAG_LOSS_MAKING = "LOSS_MAKING"
FLAG_NEGATIVE_EQUITY = "NEGATIVE_EQUITY"
FLAG_HEAVY_DEBT = "HEAVY_DEBT"
FLAG_CASH_BURN = "CASH_BURN"
FLAG_PROFIT_UNCOLLECTED = "PROFIT_UNCOLLECTED"
FLAG_ILLIQUID_BALANCE = "ILLIQUID_BALANCE"
FLAG_UNSTABLE_EARNINGS = "UNSTABLE_EARNINGS"
FLAG_EQUITY_BELOW_DEPOSIT = "EQUITY_BELOW_DEPOSIT"


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _average(parts):
    usable = [p for p in parts if p is not None]
    return sum(usable) / len(usable) if usable else None


def _ratios_of(record):
    """Accept either a stored period record or a bare ratio dict."""

    if not record:
        return {}

    if isinstance(record, dict) and isinstance(record.get("ratios"), dict):
        return record["ratios"]

    return record if isinstance(record, dict) else {}


# ==============================
# EARNINGS STABILITY
# ==============================

def stability(history):
    """How steady the earnings have been across the filed periods.

    Returns (fraction, notes). Needs at least three periods: two points give a
    direction, not a variability, and calling the difference between two
    quarters "unstable" would flag every company that had one soft season.

    Loss periods are counted separately from the variation. A company that
    averaged a 10% margin by alternating +25% and -5% is not the same as one
    that held 10% throughout, and the coefficient of variation alone treats a
    loss as merely a low number.
    """

    periods = [_ratios_of(record) for record in (history or [])]

    margins = [_finite(r.get("net_margin")) for r in periods]
    margins = [m for m in margins if m is not None]

    if len(margins) < 3:
        return None, ["earnings stability needs 3 filed periods, have %d"
                      % len(margins)]

    mean = sum(margins) / len(margins)

    losses = sum(1 for m in margins if m < 0)

    variance = sum((m - mean) ** 2 for m in margins) / len(margins)

    deviation = math.sqrt(variance)

    # Coefficient of variation on a mean that can be near zero or negative is
    # meaningless, so a company whose average margin is not clearly positive is
    # judged on the loss count alone rather than on a ratio that would explode.
    if mean <= 1.0:
        if losses:
            return 0.0, ["%d of %d periods were loss-making and the average "
                         "margin is %.1f%% — no stable earnings to measure"
                         % (losses, len(margins), mean)]
        return 0.25, ["average margin %.1f%% is too thin for a stability "
                      "reading" % mean]

    cv = deviation / mean

    if losses:
        # A loss inside the window caps stability regardless of the variation:
        # the swing horizon is short enough that one more loss period is a live
        # possibility rather than history.
        fraction = 0.2 if losses == 1 else 0.0
        return fraction, ["%d of %d periods were loss-making (margin %.1f%% "
                          "± %.1f%%)" % (losses, len(margins), mean, deviation)]

    if cv <= config.FUND_STABILITY_STABLE_CV:
        return 1.0, ["margin %.1f%% ± %.1f%% over %d periods — stable"
                     % (mean, deviation, len(margins))]

    if cv <= config.FUND_STABILITY_VOLATILE_CV:
        return 0.6, ["margin %.1f%% ± %.1f%% over %d periods — variable"
                     % (mean, deviation, len(margins))]

    return 0.25, ["margin %.1f%% ± %.1f%% over %d periods — erratic"
                  % (mean, deviation, len(margins))]


# ==============================
# SECTOR-RELATIVE VALUATION
# ==============================

def sector_medians(rows):
    """Median P/E, P/B and P/S across a group of companies.

    Pure and caller-grouped: this module has no idea which company is in which
    sector, and does not need to. The caller groups, this computes.

    Non-positive and absent multiples are dropped rather than treated as zero.
    A company with no earnings has no P/E, and including it as 0 would drag the
    sector median toward "everything is cheap".
    """

    out = {}

    for key in ("pe", "pb", "ps"):

        values = sorted(
            v for v in (_finite(_ratios_of(row).get(key)) for row in rows or [])
            if v is not None and v > 0
        )

        if len(values) < 3:
            # Fewer than three comparables is not a sector median, it is an
            # anecdote. MIN_SECTOR_CONSTITUENTS carries the same judgement for
            # sector indices.
            continue

        middle = len(values) // 2

        out[key] = (values[middle] if len(values) % 2
                    else (values[middle - 1] + values[middle]) / 2.0)

    return out


def relative_valuation(ratios, medians):
    """How this company's multiples compare with its sector's.

    Returns (fraction, notes). A discount to the sector is scored as cheap, a
    premium as expensive, and the size of the gap matters: 5% below the median
    is noise on a market where the band moves prices 3% a day.
    """

    ratios = _ratios_of(ratios)
    medians = medians or {}

    parts = []
    notes = []

    for key, label in (("pe", "P/E"), ("pb", "P/B"), ("ps", "P/S")):

        value = _finite(ratios.get(key))
        median = _finite(medians.get(key))

        if value is None or value <= 0 or median is None or median <= 0:
            continue

        discount = (median - value) / median * 100.0

        if discount >= 30:
            parts.append(1.0)
            notes.append("%s %.1f is %.0f%% below the sector median %.1f"
                         % (label, value, discount, median))
        elif discount >= 10:
            parts.append(0.75)
            notes.append("%s %.1f is %.0f%% below the sector median %.1f"
                         % (label, value, discount, median))
        elif discount >= -10:
            parts.append(0.5)
            notes.append("%s %.1f is in line with the sector median %.1f"
                         % (label, value, median))
        elif discount >= -40:
            parts.append(0.25)
            notes.append("%s %.1f is %.0f%% above the sector median %.1f"
                         % (label, value, -discount, median))
        else:
            parts.append(0.05)
            notes.append("%s %.1f is %.0f%% above the sector median %.1f"
                         % (label, value, -discount, median))

    if not parts:
        return None, ["no sector median to compare against"]

    return _average(parts), notes


# ==============================
# RISK FLAGS
# ==============================

def risk_flags(ratios, history=None):
    """Everything in the filing that could sink the position, named.

    Returns (flags, inputs_seen). `inputs_seen` is what separates "no problems"
    from "nothing to look at": a company with no balance sheet in the store has
    no flags AND no risk reading, and reporting the first without the second
    would present an absence as an all-clear.
    """

    ratios = _ratios_of(ratios)

    flags = []
    seen = []

    def flag(name, severity, message, cap=None):
        flags.append({
            "flag": name,
            "severity": severity,
            "message": message,
            "cap": cap,
        })

    # ---- is it making money ----------------------------------------------

    margin = _finite(ratios.get("net_margin"))
    roe = _finite(ratios.get("roe"))

    if margin is not None or roe is not None:
        seen.append("profitability")

    loss = ((margin is not None and margin < 0)
            or (roe is not None and roe < 0))

    if loss:
        flag(FLAG_LOSS_MAKING, CRITICAL,
             "زیان‌ده — سود خالص منفی است",
             cap=config.FUND_CAP_LOSS_MAKING)
    elif roe is not None and roe < config.RISK_FREE_RATE * 100:
        # Not a solvency problem, so no cap: the company works, the owners just
        # do worse than a deposit. That is a reason to rank it down, which the
        # profitability pillar already does, and a reason to say so out loud.
        flag(FLAG_EQUITY_BELOW_DEPOSIT, MINOR,
             "ROE %.0f%% زیر نرخ بدون ریسک %.0f%%"
             % (roe, config.RISK_FREE_RATE * 100))

    # ---- can it pay -------------------------------------------------------

    debt = _finite(ratios.get("debt_ratio"))
    equity_ratio = _finite(ratios.get("equity_ratio"))

    if debt is not None or equity_ratio is not None:
        seen.append("leverage")

    negative_equity = (
        (equity_ratio is not None and equity_ratio <= 0)
        or (debt is not None and debt >= 100)
    )

    if negative_equity:
        flag(FLAG_NEGATIVE_EQUITY, CRITICAL,
             "بدهی از دارایی بیشتر است — حقوق صاحبان سهام منفی",
             cap=config.FUND_CAP_NEGATIVE_EQUITY)
    elif debt is not None and debt > config.DEBT_HIGH:
        flag(FLAG_HEAVY_DEBT, MAJOR,
             "بدهی %.0f%% دارایی‌ها — بالاتر از حد %.0f%%"
             % (debt, config.DEBT_HIGH),
             cap=config.FUND_CAP_HEAVY_DEBT)

    current = _finite(ratios.get("current_ratio"))

    if current is not None:
        seen.append("liquidity")
        if current < config.CURRENT_RATIO_WEAK:
            flag(FLAG_ILLIQUID_BALANCE, MAJOR,
                 "نسبت جاری %.2f — بدهی جاری از دارایی جاری بیشتر است" % current)

    # ---- does the cash arrive --------------------------------------------

    ocf = _finite(ratios.get("operating_cash_flow"))

    if ocf is not None:
        seen.append("cash flow")
        if ocf < 0:
            flag(FLAG_CASH_BURN, MAJOR,
                 "جریان نقد عملیاتی منفی — شرکت نقد می‌سوزاند",
                 cap=config.FUND_CAP_CASH_BURN)

    quality = _finite(ratios.get("earnings_quality"))

    if quality is not None:
        seen.append("earnings quality")
        if quality < config.QUALITY_FAIR:
            flag(FLAG_PROFIT_UNCOLLECTED, MAJOR,
                 "نسبت نقد به سود %.2f — سود گزارش‌شده وصول نشده" % quality,
                 cap=config.FUND_CAP_PROFIT_UNCOLLECTED)

    # ---- how steady has it been ------------------------------------------

    fraction, _notes = stability(history)

    if fraction is not None:
        seen.append("stability")
        if fraction <= 0.25:
            flag(FLAG_UNSTABLE_EARNINGS, MAJOR,
                 "سود در دوره‌های گذشته بی‌ثبات یا زیان‌ده بوده است")

    return flags, seen


def _score_risk_group(flags, seen):
    """Flags -> a 0-1 fraction where 1.0 is 'nothing found'.

    Penalties add rather than multiply, and the sum is clamped: three MAJOR
    problems is not 0.27 of a company, it is a company to stay away from, and
    the difference between 0.27 and 0.0 changes no decision while the clamp
    keeps the arithmetic honest.
    """

    if not seen:
        return None, ["nothing in the store to assess risk from"]

    if not flags:
        return 1.0, ["no risk flag on %s" % ", ".join(seen)]

    penalty = sum(SEVERITY_PENALTY.get(f["severity"], 0.15) for f in flags)

    fraction = max(0.0, 1.0 - penalty)

    return fraction, [f["message"] for f in flags]


def ceiling_for(flags):
    """The lowest ceiling any flag imposes, and which flag imposed it."""

    caps = [(f["cap"], f["flag"]) for f in flags or [] if f.get("cap") is not None]

    if not caps:
        return None, None

    return min(caps, key=lambda pair: pair[0])


# ==============================
# THE FOUR GROUPS
# ==============================

def _group_quality(ratios, history):
    """Is the business good: returns, margins, cash conversion, steadiness."""

    profitability, profit_notes = fundamental_scoring._score_profitability(ratios)
    earnings, earnings_notes = fundamental_scoring._score_quality(ratios)
    health, health_notes = fundamental_scoring._score_health(ratios)
    steady, steady_notes = stability(history)

    notes = (list(profit_notes) + list(earnings_notes) + list(health_notes)
             + list(steady_notes))

    return _average([profitability, earnings, health, steady]), notes


def _group_growth(ratios):
    return fundamental_scoring._score_growth(ratios)


def _group_valuation(ratios, medians):
    """Absolute multiples, then the same multiples against the sector."""

    absolute, absolute_notes = fundamental_scoring._score_valuation(ratios)
    relative, relative_notes = relative_valuation(ratios, medians)

    notes = list(absolute_notes) + list(relative_notes)

    # The absolute reading carries twice the weight of the relative one. A
    # sector can be uniformly expensive, and being the cheapest name in an
    # expensive sector is a weaker fact than being cheap outright.
    parts = [p for p in (absolute, absolute, relative) if p is not None]

    return (sum(parts) / len(parts) if parts else None), notes


def default_weights():
    return {
        QUALITY: config.FUND_GROUP_WEIGHT_QUALITY,
        GROWTH: config.FUND_GROUP_WEIGHT_GROWTH,
        VALUATION: config.FUND_GROUP_WEIGHT_VALUATION,
        RISK: config.FUND_GROUP_WEIGHT_RISK,
    }


def status_for(score):
    return next(label for floor, label in config.FUND_STATUS_BANDS
                if score >= floor)


def evaluate(ratios=None, history=None, medians=None, weights=None,
             min_coverage=None):
    """Ratios (and optionally the filed history) -> a 0-100 Fundamental Score.

    `history` is the filed periods oldest-first, each either a record with a
    "ratios" key or a bare ratio dict. When absent, everything still works and
    the stability input simply drops out -- which is the normal case for a
    company with one filing in the store.

    `medians` is the sector's median multiples from sector_medians(). Absent, the
    valuation group scores on its absolute bands alone.
    """

    weights = dict(weights or default_weights())

    min_coverage = (config.FUND_GROUP_MIN_COVERAGE if min_coverage is None
                    else min_coverage)

    # The newest period is the one the point readings come from. When only a
    # history is supplied, its last entry is the newest.
    if ratios is None and history:
        ratios = history[-1]

    ratios = _ratios_of(ratios)

    flags, seen = risk_flags(ratios, history)

    scored = {
        QUALITY: _group_quality(ratios, history),
        GROWTH: _group_growth(ratios),
        VALUATION: _group_valuation(ratios, medians),
        RISK: _score_risk_group(flags, seen),
    }

    groups = {}
    notes = []

    available = []
    total_weight = 0.0

    for name in GROUPS:

        fraction, group_notes = scored[name]

        notes.extend("%s: %s" % (GROUP_LABELS[name], note)
                     for note in group_notes)

        groups[name] = {
            "label": GROUP_LABELS[name],
            "weight": weights.get(name, 0),
            "fraction": None if fraction is None else round(fraction, 3),
            "score": None if fraction is None else int(round(fraction * 100)),
            "points": None,
            "notes": list(group_notes),
        }

        if fraction is None:
            continue

        available.append((name, weights.get(name, 0), fraction))
        total_weight += weights.get(name, 0)

    model_weight = float(sum(weights.values())) or 1.0

    covered = round(total_weight / model_weight, 2)

    cap, cap_flag = ceiling_for(flags)

    if not available or covered < min_coverage:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "raw_score": None,
            "status": None,
            "groups": groups,
            "flags": flags,
            "cap": cap,
            "cap_flag": cap_flag,
            "capped": False,
            "coverage": covered,
            "weights": weights,
            "notes": notes,
            "reason": ("only %d%% of the fundamental model had inputs "
                       "(need %d%%)" % (covered * 100, min_coverage * 100)),
        }

    scale = 100.0 / total_weight

    total = 0.0

    for name, weight, fraction in available:
        points = weight * scale * fraction
        groups[name]["points"] = round(points, 1)
        total += points

    raw = int(round(max(0.0, min(100.0, total))))

    # The ceiling is applied last and reported separately from the raw score.
    # Both numbers matter: the raw one says how the model read the business, the
    # capped one is what any ranking must use, and collapsing them would hide
    # that a 72 became a 45 because the company is loss-making.
    value = raw if cap is None else min(raw, int(cap))

    return {
        "verdict": SCORED,
        "score": value,
        "raw_score": raw,
        "status": status_for(value),
        "groups": groups,
        "flags": flags,
        "cap": cap,
        "cap_flag": cap_flag,
        "capped": cap is not None and value < raw,
        "coverage": covered,
        "weights": weights,
        "notes": notes,
        "reason": None,
    }


# ==============================
# THE LIVE PATH
# ==============================

def _exchange_shares(ins_code):
    """Today's share count for one company from the market snapshot, or None.

    One primary-key read, because evaluate_stored() runs once per name in a
    scan: fundamental_outlook.exchange_shares() reads the whole snapshot, 34 ms
    a call measured on 2026-11-11, which is half a minute across the market;
    this is 3.3 ms, mostly the connection. Never raises -- no snapshot means
    the filing's own count, as before.
    """

    try:
        import database

        conn = database._connect()

        try:
            row = conn.execute(
                "SELECT total_shares FROM market_snapshot WHERE ins_code = ?",
                (str(ins_code),)).fetchone()
        finally:
            conn.close()
    except Exception:                                           # noqa: BLE001
        return None

    value = _finite(row[0]) if row else None

    return value if value and value > 0 else None


def evaluate_stored(ins_code, medians=None, before=None, periods=4,
                    weights=None, price=None, sector=None, shares=None):
    """Score one company from the weekly fundamental store.

    Reads fundamental_db, and nothing else but the snapshot's share count when
    re-striking at a price. If the store does not exist, or holds nothing for
    this company, the verdict is INSUFFICIENT_DATA with a reason naming which of
    the two it was -- because "run the collector" and "this company never files"
    call for different actions.

    `price` RE-STRIKES THE VALUATION at that price, through the same
    market_ratios() the point-in-time path uses. Without it the multiples are
    whatever the collector stored, which is a P/E against a price that has since
    moved -- and on this market it had moved a long way, so the panel was
    valuing companies at prices they no longer traded at. Callers that only want
    quality and growth can still omit it and get the old behaviour.

    `shares` is the share count that price is per; when omitted on a live
    re-strike (no `before`) it is the exchange's count from the market
    snapshot, since a price is today's and the filing's capital may be several
    capital increases old. See market_ratios().
    """

    import fundamental_db

    if not fundamental_db.exists():
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "raw_score": None,
            "status": None,
            "groups": {},
            "flags": [],
            "cap": None,
            "cap_flag": None,
            "capped": False,
            "coverage": 0.0,
            "weights": dict(weights or default_weights()),
            "notes": [],
            "reason": "no fundamental store at %s — run "
                      "`python fundamental_db.py --import-market-db` or the "
                      "weekly collector" % fundamental_db.path(),
        }

    rows = fundamental_db.load_metrics(ins_code, limit=periods, before=before)

    if not rows:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "raw_score": None,
            "status": None,
            "groups": {},
            "flags": [],
            "cap": None,
            "cap_flag": None,
            "capped": False,
            "coverage": 0.0,
            "weights": dict(weights or default_weights()),
            "notes": [],
            "reason": "the store holds no filing for %s%s"
                      % (ins_code, " published before %s" % before
                         if before else ""),
        }

    # load_metrics returns newest first; the pillar model and stability() both
    # want oldest first.
    history = list(reversed(rows))

    if price is not None:
        # A store read as of a past date gets no share count of today's: the
        # point-in-time rule market_ratios() keeps for the backtest.
        if shares is None and before is None:
            shares = _exchange_shares(ins_code)

        # Only the newest filing is re-struck. The older ones feed stability and
        # growth, which read margins and profit rather than multiples, so
        # re-pricing them would change nothing and cost a pass over the history.
        history = list(history)
        history[-1] = dict(history[-1],
                           ratios=market_ratios(history[-1], price,
                                                sector=sector, shares=shares))

    result = evaluate(ratios=history[-1], history=history, medians=medians,
                      weights=weights)

    newest = rows[0]

    result["period_end"] = newest.get("period_end")
    result["period_months"] = newest.get("period_months")
    result["published_at"] = newest.get("published_at")
    result["periods"] = len(history)
    result["source"] = newest.get("source")

    return result


def score(ratios, min_coverage=None):
    """The 0-100 number alone, for callers that want only the figure.

    Named to match fundamental_scoring.score() so the two are interchangeable in
    a blend, and returns None rather than 0 when the model could not run.
    """

    result = evaluate(ratios=ratios, min_coverage=min_coverage)

    return result["score"]


# ==============================
# VALUATION AT A PRICE
# ==============================

def market_ratios(record, price, sector=None, shares=None):
    """Valuation multiples recomputed at `price`. Never the stored ones.

    Lives here rather than in backtesting/pit.py because BOTH sides need it and
    only one of them had it. The backtest recomputed P/E, P/S and P/B at the
    decision session's close; the live panel scored whatever the collector
    happened to store, which was computed at whatever price ruled when it ran.

    MEASURED on 2026-08-15: those two disagreed for 203 of 284 companies, and
    always in the same direction -- the stored multiples were struck at older,
    lower prices, so the live path called the market cheaper than it was. The
    price frames were byte-identical by then; this was the whole remaining gap.

    `shares` is the exchange's share count today, for a caller striking the
    valuation at today's price. The stored count is the filing's capital at
    par, and after a capital increase today's price against it understates
    every multiple by the increase -- on 2026-11-11 فملی read P/E 8.2 against
    11.3, سصفها P/B 0.02 against 0.5. With it, P/E is market cap over the
    year's owners' profit, as in financials.ratios(). Without it -- the
    point-in-time path, which has no record of a past session's share count --
    nothing changes.

    Nothing about the SCORING changes: evaluate() is untouched and still reads a
    ratios dict. This decides which ratios it is handed.
    """

    ratios = dict((record or {}).get("ratios") or {})
    items = (record or {}).get("items") or {}

    price = _finite(price)

    exchange = _finite(shares)
    exchange = exchange if exchange and exchange > 0 else None

    shares = exchange or _finite(ratios.get("shares"))
    months = (record or {}).get("period_months") or ratios.get("period_months")

    # Wipe anything market-derived that came off disk, so a stored value can
    # never survive by accident into a score struck at a different price.
    for key in ("pe", "ps", "pb", "earnings_yield", "market_cap",
                "book_value_per_share"):
        ratios[key] = None

    if not price or price <= 0 or not shares or shares <= 0:
        return ratios

    # Only when replaced: the point-in-time path's dict stays as it was.
    if exchange:
        ratios["shares"] = exchange

    ratios["market_cap"] = price * shares

    eps_annual = _finite(ratios.get("eps_annual"))

    # An earnings multiple does not measure the same thing in every industry.
    # config.py explains which and why; the point here is that withholding a
    # component is honest and scoring it on a meaningless ratio is not.
    multiple_meaningful = sector not in config.EARNINGS_MULTIPLE_NOT_MEANINGFUL

    # Owners' profit, the earnings financials.ratios() strikes P/E on, read
    # the same way: the parent's share where the filing separates it.
    profit = (_finite(items.get("net_profit_owners"))
              or _finite(items.get("net_profit")))

    if eps_annual and eps_annual > 0 and multiple_meaningful:
        if exchange and profit is not None:
            # Market cap over the year's profit: no per-share figure, so no
            # share count but today's. A loss leaves P/E empty, as a negative
            # EPS already does.
            annual_profit = profit * (12.0 / float(months)) if months else profit
            if annual_profit > 0:
                ratios["pe"] = round(ratios["market_cap"] / annual_profit, 2)
                ratios["earnings_yield"] = round(
                    annual_profit / ratios["market_cap"] * 100, 2)
        else:
            ratios["pe"] = round(price / eps_annual, 2)
            ratios["earnings_yield"] = round(eps_annual / price * 100, 2)

    revenue = _finite(items.get("revenue"))

    if revenue and revenue > 0 and months:
        annual_revenue = revenue * (12.0 / float(months)) if months else revenue
        sales_per_share = annual_revenue / shares
        if sales_per_share > 0:
            ratios["ps"] = round(price / sales_per_share, 2)

    equity = _finite(items.get("equity_owners")) or _finite(items.get("equity"))

    if equity and equity > 0:
        book = equity / shares
        if book > 0:
            ratios["book_value_per_share"] = round(book, 2)
            ratios["pb"] = round(price / book, 2)

    return ratios
