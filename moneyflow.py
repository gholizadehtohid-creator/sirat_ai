"""حقیقی/حقوقی flow: what the per-participant averages say.

TSETMC reports, for every session, how much individuals (حقیقی) and legal
entities (حقوقی) each bought and sold, and across how many participants. This
module turns that into a reading the rest of the project can score against.

Three things this gets right that a naive reading of the same numbers does not:

  THE COUNTS ARE THE SIGNAL. Value alone cannot separate one buyer committing a
  billion rials from a thousand buyers committing a million each. The first is
  someone acting on a view; the second is a crowd. قدرت خریدار -- mean rials
  per individual buyer divided by mean rials per individual seller -- is the
  metric Tehran traders actually read, and it exists only because the counts
  are published.

  NET INDIVIDUAL FLOW IS NET INSTITUTIONAL FLOW, MIRRORED. Every rial bought
  was sold by someone, so individual_buy + institutional_buy and
  individual_sell + institutional_sell are both the session's turnover, and
  therefore net individual is exactly minus net institutional. Reporting
  "retail money entered AND institutions distributed" as two agreeing signals
  is counting one fact twice. One direction is computed here; the other is
  named as its mirror.

  SIZE IS RELATIVE. Fifty billion rials into a small company is a takeover of
  the order book; the same into a giant is a rounding error. Net flow is
  normalised by the window's own turnover before it is scored.

Nothing here fetches. It reads what providers/tsetmc_cdn.py stored, so it works
identically offline, and it never scores a window too short to mean anything --
it says INSUFFICIENT rather than returning a confident number built from three
sessions that happened to be cached.
"""

import math

import pandas as pd

import config


# Reported alongside every score so a caller can tell a full reading from one
# assembled out of whatever happened to be present.
SCORED = "SCORED"
INSUFFICIENT = "INSUFFICIENT_DATA"

# What the score is called once it exists.
BULLISH = "ورود پول حقیقی"
NEUTRAL = "متعادل"
BEARISH = "خروج پول حقیقی"


def _value(x):
    """A float we can do arithmetic on, or None. NaN is not a number here."""

    if x is None:
        return None

    try:
        value = float(x)
    except (TypeError, ValueError):
        return None

    return value if math.isfinite(value) else None


def _total(frame, column):
    """Sum a column, treating absent and non-finite entries as zero.

    Summing is safe where averaging would not be: a missing session contributes
    nothing to a total, whereas it would silently reweight a mean.
    """

    if column not in frame.columns:
        return 0.0

    series = pd.to_numeric(frame[column], errors="coerce")

    return float(series.fillna(0).sum())


def _per_capita(value, count):
    """Mean rials per participant, or None when nobody was on that side.

    A zero count with a non-zero value is malformed rather than infinite, and a
    zero count with a zero value is simply a side nobody took. Both are None:
    the honest answer is "no per-participant figure exists", not a division
    result the caller would go on to compare against a threshold.
    """

    value = _value(value)
    count = _value(count)

    if value is None or count is None or count <= 0 or value <= 0:
        return None

    return value / count


def _band(value, bands):
    """First (fraction, note) whose threshold `value` clears. Bands descend."""

    for threshold, fraction, note in bands:
        if value >= threshold:
            return fraction, note(value)

    threshold, fraction, note = bands[-1]

    return fraction, note(value)


# ==============================
# THE READING
# ==============================

def measure(df, window=None):
    """Aggregate the last `window` sessions of one instrument's flow.

    Aggregating the window before dividing, rather than averaging per-session
    ratios, is deliberate: one thin session where four people sold makes a
    per-session ratio explode, and an average over ratios carries that spike
    into the verdict. Summing values and counts across the window first makes
    the result a value-weighted per-participant figure, where a thin session
    contributes in proportion to how little it traded.
    """

    window = window or config.FLOW_WINDOW

    if df is None or df.empty:
        return None

    frame = df.tail(window)

    individual_buy = _total(frame, "individual_buy_value")
    individual_sell = _total(frame, "individual_sell_value")
    institutional_buy = _total(frame, "institutional_buy_value")

    # Turnover from the buy side: both sides equal it by construction, and the
    # buy side is the one whose components are used elsewhere here.
    turnover = individual_buy + institutional_buy

    if turnover <= 0:
        return None

    buy_power = _per_capita(
        individual_buy, _total(frame, "individual_buy_count")
    )
    sell_power = _per_capita(
        individual_sell, _total(frame, "individual_sell_count")
    )

    net_individual = individual_buy - individual_sell

    # Sessions the individual side finished as a net buyer. Direction, not
    # agreement-with-the-total: a window that is consistently negative should
    # not earn conviction points for being consistent about it.
    daily_net = (
        pd.to_numeric(frame.get("individual_buy_value"), errors="coerce").fillna(0)
        - pd.to_numeric(frame.get("individual_sell_value"), errors="coerce").fillna(0)
    )

    positive = int((daily_net > 0).sum())

    return {
        "sessions": int(len(frame)),
        "first_date": _date(frame, 0),
        "last_date": _date(frame, -1),
        "turnover": turnover,
        "individual_buy_value": individual_buy,
        "individual_sell_value": individual_sell,
        "net_individual": net_individual,
        # Named, not computed: it is the same number with the sign flipped, and
        # a caller that recomputed it from the institutional columns would be
        # entitled to think it had found independent confirmation.
        "net_institutional": -net_individual,
        "net_pct": (net_individual / turnover) * 100.0,
        "buy_power": buy_power,
        "sell_power": sell_power,
        "power_ratio": (
            buy_power / sell_power
            if buy_power is not None and sell_power not in (None, 0)
            else None
        ),
        "sessions_positive": positive,
        "persistence": positive / len(frame),
    }


def _date(frame, position):
    if "date" not in frame.columns or frame.empty:
        return None

    stamp = frame["date"].iloc[position]

    if pd.isna(stamp):
        return None

    return str(pd.Timestamp(stamp).date())


# ==============================
# SCORE COMPONENTS
# ==============================

def _score_power(reading):
    """قدرت خریدار: is the average buyer committing more than the average seller?"""

    ratio = reading.get("power_ratio")

    if ratio is None or ratio <= 0:
        return None, "قدرت خریدار unavailable (a side had no participants)"

    return _band(ratio, [
        (config.FLOW_POWER_STRONG, 1.0,
         lambda r: f"قدرت خریدار {r:.2f} — buying heavily concentrated"),
        (config.FLOW_POWER_GOOD, 0.8,
         lambda r: f"قدرت خریدار {r:.2f} — buyers clearly larger than sellers"),
        (config.FLOW_POWER_WEAK, 0.45,
         lambda r: f"قدرت خریدار {r:.2f} — buyers marginally larger"),
        (config.FLOW_POWER_BAD, 0.2,
         lambda r: f"قدرت خریدار {r:.2f} — sellers larger than buyers"),
        (0.0, 0.05,
         lambda r: f"قدرت خریدار {r:.2f} — selling heavily concentrated"),
    ])


def _score_net(reading):
    """Net individual inflow as a share of the window's own turnover."""

    net_pct = reading.get("net_pct")

    if net_pct is None:
        return None, "net flow unavailable"

    return _band(net_pct, [
        (config.FLOW_NET_STRONG, 1.0,
         lambda v: f"ورود پول حقیقی {v:+.1f}% of turnover — strong inflow"),
        (config.FLOW_NET_GOOD, 0.8,
         lambda v: f"ورود پول حقیقی {v:+.1f}% of turnover — clear inflow"),
        (config.FLOW_NET_WEAK, 0.45,
         lambda v: f"ورود پول حقیقی {v:+.1f}% of turnover — mild, below the "
                   f"market's usual"),
        (config.FLOW_NET_BAD, 0.2,
         lambda v: f"خروج پول حقیقی {v:+.1f}% of turnover — net outflow"),
        (float("-inf"), 0.05,
         lambda v: f"خروج پول حقیقی {v:+.1f}% of turnover — heavy outflow"),
    ])


def _score_persistence(reading):
    """How much of the window agreed, rather than one session carrying it."""

    persistence = reading.get("persistence")

    if persistence is None:
        return None, "persistence unavailable"

    sessions = reading.get("sessions", 0)
    positive = reading.get("sessions_positive", 0)

    def note(value):
        return (f"{positive}/{sessions} sessions closed with retail as net "
                f"buyer ({value*100:.0f}%)")

    return _band(persistence, [
        (0.8, 1.0, note),
        (0.6, 0.75, note),
        (0.4, 0.5, note),
        (0.2, 0.25, note),
        (0.0, 0.05, note),
    ])


COMPONENTS = [
    ("قدرت خریدار", config.FLOW_WEIGHT_POWER, _score_power),
    ("ورود پول", config.FLOW_WEIGHT_NET, _score_net),
    ("پایداری", config.FLOW_WEIGHT_PERSISTENCE, _score_persistence),
]


def score(reading):
    """Weighted 0-100 over whatever components could be computed.

    Missing weight is redistributed across the rest, exactly as the fundamental
    screen does, and `weight_covered` reports how much of the model ran so a
    caller never mistakes a partial reading for a full one.
    """

    breakdown = {}
    notes = []

    available = []
    total_weight = 0.0

    for label, weight, fn in COMPONENTS:

        fraction, note = fn(reading)

        notes.append(f"{label}: {note}")

        if fraction is None:
            breakdown[label] = None
            continue

        available.append((label, weight, fraction))
        total_weight += weight

    if not available:
        return {
            "score": 0,
            "breakdown": {label: None for label, _, _ in COMPONENTS},
            "notes": notes,
            "weight_covered": 0.0,
        }

    scale = 100.0 / total_weight

    total = 0.0

    for label, weight, fraction in available:
        points = weight * scale * fraction
        breakdown[label] = round(points, 1)
        total += points

    return {
        "score": int(round(max(0.0, min(100.0, total)))),
        "breakdown": breakdown,
        "notes": notes,
        "weight_covered": round(total_weight / 100.0, 2),
    }


def label_for(value):
    if value >= config.FLOW_BULLISH_SCORE:
        return BULLISH
    if value <= config.FLOW_BEARISH_SCORE:
        return BEARISH
    return NEUTRAL


# ==============================
# PUBLIC ENTRY POINT
# ==============================

def analyse(df, window=None, min_sessions=None):
    """Full flow verdict for one instrument's stored client-type series.

    Returns a payload whose `verdict` is INSUFFICIENT_DATA when there are not
    enough sessions to judge. That case carries score None rather than 0: a
    zero here would rank a share we know nothing about below one we have
    measured and found to be bleeding money, which is the wrong way round.
    """

    window = window or config.FLOW_WINDOW
    min_sessions = (
        config.FLOW_MIN_SESSIONS if min_sessions is None else min_sessions
    )

    held = 0 if df is None else len(df)

    if held < min_sessions:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "label": None,
            "reason": (f"{held} session(s) of client-type data, "
                       f"need {min_sessions}"),
            "sessions": held,
            "reading": None,
            "baseline": None,
            "breakdown": {},
            "notes": [],
            "weight_covered": 0.0,
        }

    reading = measure(df, window=window)

    if reading is None:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "label": None,
            "reason": f"no turnover in the last {window} session(s)",
            "sessions": held,
            "reading": None,
            "baseline": None,
            "breakdown": {},
            "notes": [],
            "weight_covered": 0.0,
        }

    scored = score(reading)

    # The same measurement over a longer window, so the panel can say whether
    # the current reading is a change or simply what this share always looks
    # like. Not scored -- it is context for the score, not part of it.
    baseline = measure(df, window=config.FLOW_BASELINE_WINDOW)

    return {
        "verdict": SCORED,
        "score": scored["score"],
        "label": label_for(scored["score"]),
        "reason": None,
        "sessions": held,
        "reading": reading,
        "baseline": baseline,
        "breakdown": scored["breakdown"],
        "notes": scored["notes"],
        "weight_covered": scored["weight_covered"],
    }


# ==============================
# WHOLE-MARKET AGGREGATE
# ==============================

def _current_from(frames, window):
    """The oldest session a reading must reach to describe the market now.

    The market's newest `window` sessions, read off the stored flow rows
    themselves rather than the clock -- a holiday or a late fetch must not age
    the whole market, the rule database.halted_codes follows for price bars. A
    reading whose window ends before the oldest of them shares not one session
    with the window every current reading measures: it is another week's flow.
    The threshold is therefore the flow window itself, config.FLOW_WINDOW (one
    trading week) unless the caller asked for another. None when nothing is
    dated.
    """

    stamps = [frame["date"] for frame in frames.values()
              if frame is not None and "date" in frame.columns]

    if not stamps:
        return None

    sessions = sorted(pd.concat(stamps, ignore_index=True)
                      .dropna().dt.normalize().unique(), reverse=True)

    if not sessions:
        return None

    return str(pd.Timestamp(sessions[min(window, len(sessions)) - 1]).date())


def analyse_market(limit=None, top=15, window=None, min_sessions=None):
    """Whole-market flow from stored client-type data.

    Reads only what is cached, so this answers instantly and identically with
    the network down -- the fetch that fills the table is bootstrap's job, not
    this function's.

    Only readings of the CURRENT market are ranked or summed into the market
    figures. A halted share, and one whose stored flow ends before this
    week's window (_current_from), both still hold a scorable window -- the
    last one before they went quiet -- and on 2026-11-11 such windows held
    the #1 outflow (وسگلستا, ending 2021-07-31) and two of the strongest
    buyers (سباقر 2025-01, تکیمیا 2024-11): 88 of 779 scored windows ended
    before 2026-10-25, 43 of them above the liquidity floor. Both kinds are
    left out and counted.
    """

    import database

    window = window or config.FLOW_WINDOW

    instruments = database.load_instruments(equities_only=True)

    by_code = {str(r["ins_code"]): r for r in instruments}

    # Halted over config.HALT_EXCLUDE_DAYS: the rule technical.analyse and
    # the fundamental report already apply. Its window is whatever traded
    # before the halt, however long ago that was.
    halted = database.halted_codes()

    codes = [code for code in by_code if code not in halted]

    halted_count = len(by_code) - len(codes)

    if limit:
        codes = codes[:limit]

    frames = database.load_client_type_tail(codes, config.FLOW_BASELINE_WINDOW)

    current_from = _current_from(frames, window)

    scored = []
    readings = []
    insufficient = 0
    stale = 0

    for code in codes:

        frame = frames.get(code)

        if frame is None or frame.empty:
            continue

        result = analyse(frame, window=window, min_sessions=min_sessions)

        if result["verdict"] != SCORED:
            insufficient += 1
            continue

        reading = result["reading"]

        # Trading, but its stored flow stops before the market's newest
        # `window` sessions -- a fetch that has not reached it, or a name that
        # has not traded for a week or more. Its reading is not this week's.
        if (current_from is not None
                and (reading["last_date"] or "") < current_from):
            stale += 1
            continue

        readings.append(reading)

        instrument = by_code.get(code, {})

        scored.append({
            "ins_code": code,
            "symbol": instrument.get("symbol_fa"),
            "name": instrument.get("name_fa"),
            "sector_name": instrument.get("sector_name"),
            "score": result["score"],
            "label": result["label"],
            "sessions": result["sessions"],
            "turnover": reading["turnover"],
            "net_individual": reading["net_individual"],
            "net_pct": round(reading["net_pct"], 2),
            "power_ratio": (
                round(reading["power_ratio"], 2)
                if reading["power_ratio"] is not None else None
            ),
            "sessions_positive": reading["sessions_positive"],
            "last_date": reading["last_date"],
        })

    # A share that traded 40 million rials all week can post a +90% net figure
    # from one retail buyer, and would otherwise head the ranking above every
    # name the number actually means something for. The floor is the same
    # liquidity threshold the fundamental screen gates on, scaled to the window.
    floor = config.MIN_DAILY_VALUE * window

    liquid = [row for row in scored if row["turnover"] >= floor]

    ranked = sorted(liquid, key=lambda r: r["net_pct"], reverse=True)

    coverage = database.client_type_coverage()

    return {
        "window": window,
        "baseline_window": config.FLOW_BASELINE_WINDOW,
        "min_turnover": floor,
        "coverage": coverage,
        # Readings whose window ends before this session were left out as
        # stale (see _current_from).
        "current_from": current_from,
        "counts": {
            "considered": len(codes),
            "scored": len(scored),
            "liquid": len(liquid),
            "insufficient": insufficient,
            "halted": halted_count,
            "stale": stale,
        },
        "market": aggregate(readings),
        # The market aggregate is every scored name; these two lists are the
        # liquid subset, because a ranking is a list someone might act on.
        "inflow": ranked[:top],
        "outflow": list(reversed(ranked[-top:])) if ranked else [],
        "strongest_power": sorted(
            [r for r in liquid if r["power_ratio"] is not None],
            key=lambda r: r["power_ratio"], reverse=True
        )[:top],
    }


def aggregate(readings):
    """Market-wide flow from many instruments' readings.

    Summed in rials rather than averaged over percentages: a market's net
    retail flow is a quantity of money, and averaging each share's percentage
    would give a 2B rial minnow the same vote as a 2000B rial index heavyweight.
    The percentage is recovered at the end, from the summed totals.
    """

    usable = [r for r in readings if r]

    if not usable:
        return None

    turnover = sum(r["turnover"] for r in usable)
    net = sum(r["net_individual"] for r in usable)

    inflow = [r for r in usable if r["net_individual"] > 0]

    return {
        "instruments": len(usable),
        "turnover": turnover,
        "net_individual": net,
        "net_institutional": -net,
        "net_pct": (net / turnover * 100.0) if turnover else None,
        # Breadth: money can be flowing in on balance while most shares bleed,
        # if one heavyweight absorbs everything. This says which it is.
        "inflow_count": len(inflow),
        "breadth_pct": len(inflow) / len(usable) * 100.0,
    }


# ==============================
# THE MARKET, SESSION BY SESSION
# ==============================
# analyse_market() answers "which names is retail money going into over the
# last week". The question a trader asks first is simpler: did real money
# come into the market today, and is it still coming? That is one number per
# session -- net individual flow summed over every equity -- and its run.
#
# At MARKET level zero is roughly neutral, unlike per symbol (where the
# median share runs ~+4.8% because حقوقی sell to retail most weeks): over
# 621 sessions from 2024 the median market day was -1.2 trillion rials
# (-3.8% of individual buying) and 40% of days were net inflow (measured
# 2026-11-06). The typical range is reported beside every reading anyway,
# so a "+" is read against what a normal day looks like.

# A day whose stored rows cover far fewer symbols than usual is a fetch that
# stopped part-way, not a market that went quiet: one such day read -1305%.
PARTIAL_DAY_SHARE = 0.6


def market_daily(sessions=60, baseline=250):
    """Net individual (حقیقی) money per session, summed over all equities.

    Returns the last `sessions` complete sessions, oldest first, and a
    summary: the last session, 5- and 20-session sums, the run of same-sign
    days, and the typical range over `baseline` sessions. Rials throughout.
    """

    import statistics

    import database

    codes = [str(r["ins_code"])
             for r in database.load_instruments(equities_only=True)]
    if not codes:
        return None

    database.create_database()
    conn = database._connect()
    try:
        dates = [row[0] for row in conn.execute(
            "SELECT DISTINCT date FROM client_type_history "
            "ORDER BY date DESC LIMIT ?", (baseline + 5,))]
        if not dates:
            return None
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(f"""
            SELECT date,
                   SUM(individual_buy_value - individual_sell_value),
                   SUM(individual_buy_value + institutional_buy_value),
                   COUNT(*),
                   SUM(CASE WHEN individual_buy_value > individual_sell_value
                            THEN 1 ELSE 0 END)
            FROM client_type_history
            WHERE date >= ? AND ins_code IN ({placeholders})
              AND individual_buy_value IS NOT NULL
              AND individual_sell_value IS NOT NULL
            GROUP BY date ORDER BY date""", [min(dates)] + codes).fetchall()
    finally:
        conn.close()

    days = [{"date": str(d)[:10], "net_individual": float(net or 0.0),
             "turnover": float(turnover or 0.0), "symbols": int(n),
             "inflow_count": int(pos or 0)}
            for d, net, turnover, n, pos in rows]
    if not days:
        return None

    usual = statistics.median(d["symbols"] for d in days)
    complete = [d for d in days if d["symbols"] >= usual * PARTIAL_DAY_SHARE]
    partial = len(days) - len(complete)

    for d in complete:
        d["net_pct"] = (d["net_individual"] / d["turnover"] * 100.0
                        if d["turnover"] else None)
        d["breadth_pct"] = d["inflow_count"] / d["symbols"] * 100.0

    nets = [d["net_individual"] for d in complete]
    last = complete[-1]

    streak = 0
    sign = last["net_individual"] > 0
    for d in reversed(complete):
        if (d["net_individual"] > 0) != sign:
            break
        streak += 1

    def quantile(values, q):
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return {
        "sessions": complete[-sessions:],
        "last": last,
        "sum_5": sum(nets[-5:]),
        "sum_20": sum(nets[-20:]),
        "streak": {"days": streak, "direction": "in" if sign else "out"},
        "typical": {"sessions": len(nets),
                    "median": statistics.median(nets),
                    "p20": quantile(nets, 0.2), "p80": quantile(nets, 0.8),
                    "inflow_day_share": sum(1 for v in nets if v > 0) / len(nets)},
        "partial_days_skipped": partial,
        "scope": "equities",
    }
