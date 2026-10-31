"""Five stock factors in, one Swing Score out -- and the BUY / HOLD / SELL call.

WHY THIS EXISTS BESIDE scoring.py RATHER THAN REPLACING IT
---------------------------------------------------------
`scoring.py` blends four readings (technical, fundamental, risk, flow) and is
what every measured result in this project was produced with. It stays exactly
as it is. This module answers a different question over a longer horizon: not
"how good is this name right now" but "is this a swing position for the next few
weeks", and that question needs two inputs scoring.py does not have -- the state
of the market, and momentum measured over months rather than over ten sessions.

    scoring.py       one bar, four readings, ranks today's candidates
    factor_engine    five stock factors over weeks, decides an action

Both are kept because they disagree usefully. A name can be the best thing on
the board today and a poor swing candidate because it has been drifting for six
months, and a single score cannot say both.

THE FIVE FACTORS, AND THE OVERLAP BETWEEN THEM
----------------------------------------------
    fundamental  30%   from fundamental_engine, off the weekly store
    momentum     25%   20/60/120-session return and relative strength
    technical    20%   the measured ranking.py score, unchanged
    liquidity    15%   can this be entered and exited at the modelled price
    catalyst     10%   news sentiment, when a provider exists -- see below

    regime        0%   NOT A FACTOR. See below.

The weights are from weight_optimizer.py: 264 grid vectors over the ranges the
v2 specification asked for, fitted on the first 14 decision dates and judged
only on the last 9, each vector averaged with its grid neighbours so that a
spike between two bad points cannot win. They describe a plateau, and the
plateau is wide -- which is the honest way of saying that several nearby vectors
would do about as well and none of these numbers is precise to the point.

THE CATALYST WEIGHT IS THE ONE NUMBER HERE THAT WAS NOT MEASURED, and it cannot
be: there is no dated news archive, so the factor is absent on all 17,672
observations, and a weight on a never-present factor changes no score at all --
it is excluded from the blend and from the covered weight alike. Its 10% is the
design intent for the day a provider exists, not a fitted value. What the grid
actually chose is the ratio between the other four.

TECHNICAL OVERLAPS BOTH ITS NEIGHBOURS. The ranking.py score spends 15 of its
100 points on 5- and 10-session momentum and 15 more on volume against its own
average -- the same ratio factor_engine.liquidity() reads. So technical is
correlated with momentum by construction and shares an input with liquidity, and
the total is not five independent opinions. This is stated rather than
engineered away because the horizons genuinely differ: a share that jumped 8%
this week inside a six-month downtrend scores well on technical and badly on
momentum, and on a multi-week horizon the second reading is the one that
matters. Anyone re-weighting these factors should know the two are not
independent.

THE REGIME CANNOT RANK, AND THIS IS ARITHMETIC RATHER THAN AN OPINION
---------------------------------------------------------------------
The regime score is a property of the DATE. Every name scored on the same session
receives the same value. A component with zero cross-sectional variance
contributes exactly zero to the ordering of a list -- adding the same number to
every row cannot reorder it -- while still consuming its share of the weight.
Its only effect on a RANKING was therefore to dilute the factors that do vary.

MEASURED, on 17,672 point-in-time observations over 816 symbols and 24 decision
dates (swing_backtest.py, 20-session hold, net of costs): the mean return of the
selected set was 2.54% at the old 20% regime weight, 4.14% with the regime factor
switched off, and 1.50% at double weight. Monotone in the wrong direction.

So the weight is now 0 and the market read does the job it can do. It is still
consulted on every decision, in the three places where a market-wide fact is the
right input:

    entries        CASH_ONLY refuses them outright, and every other weak mode
                   raises the score a name must clear -- decide() below
    size           regime_engine.position_multiplier() shrinks the per-trade
                   risk budget in risk_engine
    exposure       the cash target, in regime_engine.assess()

The regime factor is still COMPUTED and still reported in `readings`, because a
panel that shows a decision should show the market it was taken in. It is simply
not blended.

MISSING IS NOT ZERO -- the same contract as scoring.py and for the same reason.
Weights renormalise over the factors that exist. Two of the five (fundamental,
catalyst) are absent for most of this market most of the time, and scoring them
as zero would rank every uncovered company at the bottom for a fact about this
project's data collection rather than about the company. Below
SWING_MIN_COVERAGE there is no Swing Score at all.

AVOID IS NOT SELL. A distinction the specification's four actions imply and
that is easy to get wrong: AVOID means "do not enter", SELL means "exit what is
held". They are produced by different code paths here because they answer
different questions, and a system that returns SELL for a name it never owned
generates orders nobody can fill.
"""

import math

import config
import news as news_module


BUY = "BUY"
HOLD = "HOLD"
SELL = "SELL"
AVOID = "AVOID"

ACTIONS = (BUY, HOLD, SELL, AVOID)

ACTION_LABELS = {
    BUY: "خرید",
    HOLD: "نگهداری / زیر نظر",
    SELL: "فروش",
    AVOID: "اجتناب",
}

REGIME = "regime"
MOMENTUM = "momentum"
TECHNICAL = "technical"
FUNDAMENTAL = "fundamental"
LIQUIDITY = "liquidity"
CATALYST = "catalyst"

FACTORS = (REGIME, MOMENTUM, TECHNICAL, FUNDAMENTAL, LIQUIDITY, CATALYST)

FACTOR_LABELS = {
    REGIME: "وضعیت بازار",
    MOMENTUM: "مومنتوم",
    TECHNICAL: "تکنیکال",
    FUNDAMENTAL: "بنیادی",
    LIQUIDITY: "نقدشوندگی",
    CATALYST: "محرک",
}

SCORED = "SCORED"
INSUFFICIENT = "INSUFFICIENT_DATA"


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series(values):
    """A price/volume sequence as a list of floats, oldest first.

    Accepts a pandas Series, a numpy array, a plain list, or a frame column
    name already extracted. NaNs are dropped rather than forward-filled: a
    missing close is a session this project has no price for, and inventing one
    would put a fabricated bar inside a return calculation.
    """

    if values is None:
        return []

    if hasattr(values, "tolist"):
        values = values.tolist()

    out = []

    for value in values:
        number = _finite(value)
        if number is not None:
            out.append(number)

    return out


def _weighted(pairs):
    """Weighted mean over (fraction, weight) pairs, renormalised. None if empty."""

    usable = [(f, w) for f, w in pairs
              if f is not None and w is not None and w > 0]

    if not usable:
        return None

    total = sum(w for _f, w in usable)

    return sum(f * w for f, w in usable) / total


# ==============================
# MOMENTUM
# ==============================

def _momentum_band(value):
    """One horizon's return -> a 0-1 fraction, on inflation-aware bands."""

    if value >= config.MOMENTUM_STRONG_PCT:
        return 1.0
    if value >= config.MOMENTUM_GOOD_PCT:
        return 0.8
    if value >= config.MOMENTUM_FLAT_PCT:
        return 0.5
    if value >= config.MOMENTUM_WEAK_PCT:
        return 0.25
    return 0.0


def _relative_band(excess):
    """Excess return over the index -> a 0-1 fraction."""

    if excess >= config.MOMENTUM_RS_STRONG:
        return 1.0
    if excess >= config.MOMENTUM_RS_GOOD:
        return 0.75
    if excess >= config.MOMENTUM_RS_WEAK:
        return 0.4
    return 0.05


def _horizon_return(values, back):
    """Percent change over `back` observations, or None if the history is short."""

    if len(values) <= back:
        return None

    past = values[-1 - back]

    if not past or past <= 0:
        return None

    return (values[-1] - past) / past * 100.0


def momentum(closes, index_closes=None, horizons=None, weights=None):
    """Multi-horizon price momentum, with relative strength when an index exists.

    Returns a reading dict; `score` is 0-100 or None. Deliberately NOT a single
    horizon: a 60-session return alone cannot distinguish a share that rose
    steadily from one that rose 40% in a week two months ago and has drifted
    since, and on a multi-week horizon those are opposite setups. Three horizons
    weighted toward the shortest is the compromise -- recent behaviour dominates,
    but a name in a long downtrend cannot look strong.

    Relative strength carries MOMENTUM_RS_WEIGHT of the reading. On a market this
    correlated an absolute return mostly measures the index, so a share that rose
    12% while the index rose 15% is a laggard the absolute reading calls strong.
    Without an index series the factor still scores, on the absolute reading
    alone, and says so in its notes.
    """

    horizons = tuple(horizons or config.MOMENTUM_HORIZONS)
    weights = tuple(weights or config.MOMENTUM_HORIZON_WEIGHTS)

    if len(weights) != len(horizons):
        # Equal weights rather than an exception: a sweep that moves the horizon
        # list should not have to move the weight list in lockstep.
        weights = tuple(1.0 for _ in horizons)

    values = _series(closes)
    index = _series(index_closes)

    if len(values) < 2:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "returns": {},
            "excess": {},
            "notes": ["not enough price history for a momentum reading"],
            "horizons": horizons,
            "reason": "fewer than 2 usable closes",
        }

    returns = {}
    excess = {}

    absolute_pairs = []
    relative_pairs = []

    notes = []

    for horizon, weight in zip(horizons, weights):

        change = _horizon_return(values, horizon)

        if change is None:
            continue

        returns[horizon] = round(change, 2)

        absolute_pairs.append((_momentum_band(change), weight))

        index_change = _horizon_return(index, horizon) if index else None

        if index_change is not None:
            gap = change - index_change
            excess[horizon] = round(gap, 2)
            relative_pairs.append((_relative_band(gap), weight))

    if not absolute_pairs:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "returns": {},
            "excess": {},
            "notes": ["history too short for any of the horizons %s"
                      % (horizons,)],
            "horizons": horizons,
            "reason": "no horizon had enough history",
        }

    absolute = _weighted(absolute_pairs)
    relative = _weighted(relative_pairs)

    for horizon in sorted(returns):
        if horizon in excess:
            notes.append("%d سشن: %+.1f%% (شاخص %+.1f واحد اختلاف)"
                         % (horizon, returns[horizon], excess[horizon]))
        else:
            notes.append("%d سشن: %+.1f%%" % (horizon, returns[horizon]))

    if relative is None:
        notes.append("no index series — scored on absolute return alone")
        fraction = absolute
    else:
        share = config.MOMENTUM_RS_WEIGHT
        fraction = (1.0 - share) * absolute + share * relative

    return {
        "verdict": SCORED,
        "score": int(round(max(0.0, min(100.0, fraction * 100.0)))),
        "absolute": None if absolute is None else round(absolute, 3),
        "relative": None if relative is None else round(relative, 3),
        "returns": returns,
        "excess": excess,
        "horizons": horizons,
        "notes": notes,
        "reason": None,
    }


# ==============================
# LIQUIDITY / VOLUME
# ==============================

def liquidity(adv=None, volume_ratio=None, traded_sessions_pct=None,
              position_value=None):
    """Can this be entered and exited at the modelled price?

    Three independent readings, because each fails differently:

      adv                   average daily value traded, against MIN_DAILY_VALUE
      volume_ratio          today's volume against its own 20-session mean
      traded_sessions_pct   how often it trades at all

    The third is specific to Tehran and is the one a generic liquidity score
    misses: a name can post a respectable average turnover and still have gone
    untraded on a third of the sessions in the window, because the exchange emits
    a bar for every listed session whether or not anything changed hands. An
    average computed over those zeros looks tradable; the position that cannot be
    exited on the day it needs to be is not.
    """

    parts = []
    notes = []

    adv = _finite(adv)

    if adv is not None:
        floor = float(config.MIN_DAILY_VALUE or 1)
        multiple = adv / floor if floor else 0.0

        if multiple >= 10:
            parts.append(1.0); notes.append("گردش روزانه %.0f برابر کف — بسیار روان" % multiple)
        elif multiple >= 4:
            parts.append(0.8); notes.append("گردش روزانه %.1f برابر کف — روان" % multiple)
        elif multiple >= 1:
            parts.append(0.5); notes.append("گردش روزانه %.1f برابر کف — کافی" % multiple)
        elif multiple >= 0.5:
            parts.append(0.25); notes.append("گردش روزانه %.2f برابر کف — نازک" % multiple)
        else:
            parts.append(0.05); notes.append("گردش روزانه %.2f برابر کف — خروج دشوار" % multiple)
    else:
        notes.append("گردش روزانه در دسترس نیست")

    volume_ratio = _finite(volume_ratio)

    if volume_ratio is not None:
        # Volume above its own average is participation. Far above it, on a
        # swing horizon, is as often a climax as a start -- so the top band is
        # not the highest ratio.
        if volume_ratio >= 3.0:
            parts.append(0.6); notes.append("حجم %.1f برابر میانگین — جهش مشکوک" % volume_ratio)
        elif volume_ratio >= 1.5:
            parts.append(1.0); notes.append("حجم %.1f برابر میانگین — مشارکت قوی" % volume_ratio)
        elif volume_ratio >= 1.0:
            parts.append(0.8); notes.append("حجم %.1f برابر میانگین" % volume_ratio)
        elif volume_ratio >= 0.6:
            parts.append(0.45); notes.append("حجم %.1f برابر میانگین — کم‌رمق" % volume_ratio)
        else:
            parts.append(0.15); notes.append("حجم %.1f برابر میانگین — بی‌رمق" % volume_ratio)
    else:
        notes.append("مبنای حجم در دسترس نیست")

    traded = _finite(traded_sessions_pct)

    if traded is not None:
        if traded >= 95:
            parts.append(1.0)
        elif traded >= 85:
            parts.append(0.75)
        elif traded >= 70:
            parts.append(0.4)
        else:
            parts.append(0.1)
        notes.append("در %.0f%% سشن‌های اخیر معامله شده" % traded)

    if not parts:
        return {
            "verdict": INSUFFICIENT, "score": None, "notes": notes,
            "adv_participation_pct": None,
            "reason": "no liquidity input available",
        }

    participation = None

    value = _finite(position_value)

    if value is not None and adv and adv > 0:
        participation = round(value / adv * 100.0, 2)
        if participation > config.MAX_ADV_PARTICIPATION_PCT:
            notes.append("موقعیت %.1f%% گردش روزانه — بالاتر از سقف %.1f%%"
                         % (participation, config.MAX_ADV_PARTICIPATION_PCT))

    return {
        "verdict": SCORED,
        "score": int(round(sum(parts) / len(parts) * 100)),
        "adv": adv,
        "adv_participation_pct": participation,
        "notes": notes,
        "reason": None,
    }


def traded_share(volumes, sessions=20):
    """Percent of the last `sessions` bars that actually traded."""

    values = _series(volumes)

    if not values:
        return None

    window = values[-int(sessions):] if sessions else values

    if not window:
        return None

    return round(sum(1 for v in window if v > 0) / float(len(window)) * 100.0, 1)


# ==============================
# CATALYST
# ==============================

def catalyst(news_reading=None, events=None, as_of=None,
             window_sessions=None):
    """News sentiment as a 0-100, plus any undirected events, kept apart.

    THE UNDIRECTED-EVENT PROBLEM, and why events do not score.

    A ن-۲۰ material disclosure raises the probability of a large move without
    saying which way. Scoring its mere presence as a positive catalyst would mean
    every company under investigation, every capital increase and every profit
    warning read as a buy signal -- and because material disclosures cluster
    before big moves, a backtest would report that as the catalyst factor
    working. So an event with no sentiment attached contributes NOTHING to the
    score. It is returned in `events` for the risk engine to widen a stop
    against, which is the honest use of "something is about to happen".

    A score therefore exists only when a news provider supplies sentiment. On
    this database that means the factor is absent and its 10% of weight
    renormalises away, exactly as news.py's design intends.
    """

    window_sessions = int(window_sessions or news_module.DEFAULT_WINDOW_SESSIONS)

    listed = list(events or [])

    notes = []

    score = news_module.to_score(news_reading) if news_reading else None

    if listed:
        notes.append("%d رویداد بااهمیت در پنجره‌ی %d سشنی"
                     % (len(listed), window_sessions))

    if score is None:
        reason = (news_reading or {}).get("reason") or news_module.NO_HISTORICAL_NEWS

        notes.append("no dated news sentiment — catalyst factor withheld")

        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "sentiment": None,
            "severity": (news_reading or {}).get("severity"),
            "events": listed,
            "event_count": len(listed),
            "notes": notes,
            "reason": reason,
        }

    notes.append("احساسات خبری %+.2f روی %d خبر"
                 % (news_reading.get("sentiment") or 0.0,
                    news_reading.get("items") or 0))

    return {
        "verdict": SCORED,
        "score": score,
        "sentiment": news_reading.get("sentiment"),
        "severity": news_reading.get("severity"),
        "polarity": news_reading.get("polarity"),
        "events": listed,
        "event_count": len(listed),
        "notes": notes,
        "reason": None,
    }


# ==============================
# THE SWING SCORE
# ==============================

def default_weights():
    return {
        REGIME: config.SWING_WEIGHT_REGIME,
        MOMENTUM: config.SWING_WEIGHT_MOMENTUM,
        TECHNICAL: config.SWING_WEIGHT_TECHNICAL,
        FUNDAMENTAL: config.SWING_WEIGHT_FUNDAMENTAL,
        LIQUIDITY: config.SWING_WEIGHT_LIQUIDITY,
        CATALYST: config.SWING_WEIGHT_CATALYST,
    }


def status_for(score):
    return next(label for floor, label in config.SWING_STATUS_BANDS
                if score >= floor)


def swing_score(regime=None, momentum=None, technical=None, fundamental=None,
                liquidity=None, catalyst=None, weights=None,
                min_coverage=None):
    """Blend whichever of the scored factors are present into one 0-100.

    Every argument is a 0-100 score or None. The arithmetic is deliberately the
    same as scoring.combine() -- renormalise over present weight, refuse below a
    coverage floor -- so that a reader who has understood one has understood both.

    `regime` is still accepted, and at its shipped weight of 0 it changes
    nothing: a zero-weighted factor is excluded from the blend and from the
    covered weight alike, so passing it or omitting it produces the same score.
    The parameter is kept because the weight is a config value rather than a law
    -- weight_optimizer.py and swing_backtest.py both put the regime back in to
    re-test the decision to take it out, and neither could if the argument were
    gone.
    """

    weights = dict(weights or default_weights())

    min_coverage = (config.SWING_MIN_COVERAGE if min_coverage is None
                    else min_coverage)

    factors = {
        REGIME: _finite(regime),
        MOMENTUM: _finite(momentum),
        TECHNICAL: _finite(technical),
        FUNDAMENTAL: _finite(fundamental),
        LIQUIDITY: _finite(liquidity),
        CATALYST: _finite(catalyst),
    }

    model_weight = float(sum(weights.values())) or 1.0

    available = {
        name: value for name, value in factors.items()
        if value is not None and weights.get(name)
    }

    covered_weight = sum(weights[name] for name in available)

    coverage = round(covered_weight / model_weight, 2)

    contributions = {}

    missing = [FACTOR_LABELS[name] for name in FACTORS
               if factors.get(name) is None]

    if not available or coverage < min_coverage:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "scored": False,
            "status": None,
            "coverage": coverage,
            "factors": factors,
            "contributions": contributions,
            "weights": weights,
            "missing": missing,
            "reason": ("only %d%% of the swing model had inputs (need %d%%); "
                       "missing: %s"
                       % (coverage * 100, min_coverage * 100,
                          "، ".join(missing) or "-")),
        }

    scale = 1.0 / covered_weight

    total = 0.0

    for name, value in available.items():
        points = weights[name] * scale * value
        contributions[name] = round(points, 1)
        total += points

    value = int(round(max(0.0, min(100.0, total))))

    return {
        "verdict": SCORED,
        "score": value,
        "scored": True,
        "status": status_for(value),
        "coverage": coverage,
        "factors": factors,
        "contributions": contributions,
        "weights": weights,
        "missing": missing,
        "reason": None,
    }


# ==============================
# THE DECISION
# ==============================

def decide(swing, technical=None, regime=None, risk_score=None,
           fundamental=None, held=False, gate=None):
    """Turn a Swing Score into BUY / HOLD / SELL / AVOID, with the reasons.

    `held` is the whole reason there are four actions rather than two. For a
    name not owned the question is whether to enter, and the answers are BUY,
    HOLD (watch it) and AVOID. For one already owned the question is whether to
    stay, and the answers are HOLD and SELL. Nothing here ever returns SELL for
    a name the caller does not hold.

    `regime` is a regime_engine.assess() result and is consulted TWICE, both
    times about whether rather than about which: CASH_ONLY refuses new entries
    outright, and every other weak mode raises the score an entry must clear by
    regime_engine.entry_floor(). Neither can reorder the candidates -- the
    ranking inside a session is identical in a bull market and a bear one, and
    only the number of names that qualify changes. That distinction is the
    reason the regime is no longer a factor in the score itself.

    THE FLOOR IS NOT APPLIED TO EXITS, deliberately. A floor that also raised
    the exit threshold would liquidate the whole book on the session the market
    turned defensive -- selling the bottom, as policy, on a signal that is late
    by construction.

    `fundamental` is a fundamental_engine.evaluate() result -- passed whole
    rather than as a score, because a critical solvency flag is an exit reason
    on its own and the score alone cannot carry it.
    """

    gate = config.TECHNICAL_GATE if gate is None else gate

    reasons = []

    score = _finite((swing or {}).get("score"))

    mode = (regime or {}).get("trading_mode")

    critical = [
        f for f in ((fundamental or {}).get("flags") or [])
        if f.get("severity") == "CRITICAL"
    ]

    # ---- the exit path ----------------------------------------------------

    if held:

        if critical:
            reasons.append("هشدار بنیادی بحرانی: %s" % critical[0]["message"])
            return _verdict(SELL, reasons, score)

        if mode == "CASH_ONLY" and (regime or {}).get("available", True):
            # A MEASURED CASH_ONLY is a bear market WITH high volatility --
            # the one condition where holding through is the expensive choice.
            reasons.append("بازار نزولی و پرنوسان — خروج از موقعیت")
            return _verdict(SELL, reasons, score)

        if regime is not None and not regime.get("available", True):
            # regime_engine.assess() ALSO answers CASH_ONLY when it could not
            # read the market at all. That is a reason to stop entering, which
            # the entry path already does, and never a reason to sell: a
            # missing index history used to liquidate the whole book into
            # whatever queue the market had, on a data fault.
            reasons.append("وضعیت بازار قابل تشخیص نیست — خروج فقط بر پایه‌ی "
                           "امتیاز سهم")

        if score is None:
            # Refusing to score is not a sell signal. A held position whose
            # inputs went missing needs a human, not a market order.
            reasons.append("امتیاز قابل محاسبه نیست — موقعیت بدون تغییر می‌ماند "
                           "(%s)" % ((swing or {}).get("reason") or "دلیل نامعلوم"))
            return _verdict(HOLD, reasons, score)

        if score < config.SWING_EXIT_SCORE:
            reasons.append("امتیاز %d زیر حد خروج %d"
                           % (score, config.SWING_EXIT_SCORE))
            return _verdict(SELL, reasons, score)

        reasons.append("امتیاز %d بالای حد خروج %d"
                       % (score, config.SWING_EXIT_SCORE))
        return _verdict(HOLD, reasons, score)

    # ---- the entry path ---------------------------------------------------

    if regime is not None and not _allows_entries(regime):
        reasons.append("حالت بازار «%s» ورود جدید را مجاز نمی‌کند"
                       % ((regime or {}).get("mode_label") or mode))
        return _verdict(AVOID, reasons, score)

    if score is None:
        reasons.append((swing or {}).get("reason")
                       or "امتیاز سوئینگ قابل محاسبه نیست")
        return _verdict(AVOID, reasons, score)

    technical = _finite(technical)

    # The gate is kept because it is the one selection rule in this project that
    # has been measured: 3,393 point-in-time signals showed clearing 70 matters
    # and clearing it by more does not. The Swing Score reorders what passes; it
    # does not license entering what fails.
    if technical is not None and technical < gate:
        reasons.append("تکنیکال %d زیر دروازه‌ی %d" % (technical, gate))
        return _verdict(AVOID, reasons, score)

    risk_score = _finite(risk_score)

    if risk_score is not None and risk_score < config.RISK_MIN_SCORE:
        reasons.append("امتیاز ریسک %d زیر کف %d"
                       % (risk_score, config.RISK_MIN_SCORE))
        return _verdict(AVOID, reasons, score)

    if critical:
        reasons.append("هشدار بنیادی بحرانی: %s" % critical[0]["message"])
        return _verdict(AVOID, reasons, score)

    # The market cannot promote a name, but it can decide how good a name has to
    # be before it is worth owning at all. This is the only place a regime read
    # touches an individual decision, and it moves a THRESHOLD rather than a
    # score -- so the order of the candidates is identical in every market and
    # only the number of them that qualify changes.
    floor = _entry_floor(regime)

    buy_at = config.SWING_BUY_SCORE + floor

    if score >= buy_at:
        if floor:
            reasons.append("امتیاز سوئینگ %d — بالای حد خرید %d (حد پایه %d + %d "
                           "به دلیل حالت «%s»)"
                           % (score, buy_at, config.SWING_BUY_SCORE, floor,
                              (regime or {}).get("mode_label") or mode))
        else:
            reasons.append("امتیاز سوئینگ %d — بالای حد خرید %d"
                           % (score, buy_at))
        return _verdict(BUY, reasons, score, buy_at=buy_at)

    if floor and score >= config.SWING_BUY_SCORE:
        reasons.append("امتیاز سوئینگ %d بالای حد پایه %d است اما حالت بازار "
                       "«%s» حداقل %d را می‌خواهد — زیر نظر"
                       % (score, config.SWING_BUY_SCORE,
                          (regime or {}).get("mode_label") or mode, buy_at))
        return _verdict(HOLD, reasons, score, buy_at=buy_at)

    if score >= config.SWING_HOLD_SCORE:
        reasons.append("امتیاز سوئینگ %d — زیر حد خرید %d، زیر نظر"
                       % (score, buy_at))
        return _verdict(HOLD, reasons, score, buy_at=buy_at)

    reasons.append("امتیاز سوئینگ %d — زیر حد نگهداری %d"
                   % (score, config.SWING_HOLD_SCORE))
    return _verdict(AVOID, reasons, score, buy_at=buy_at)


def _allows_entries(assessment):

    import regime_engine

    return regime_engine.allows_new_entries(assessment)


def _entry_floor(assessment):

    import regime_engine

    return regime_engine.entry_floor(assessment)


def _verdict(action, reasons, score, buy_at=None):

    buy_at = config.SWING_BUY_SCORE if buy_at is None else buy_at

    # Conviction is how far into the buy band the score sits, on 0-1. Measured
    # against the threshold this decision actually used, not against the base
    # one: a 74 in a defensive market has barely cleared a raised bar and
    # reporting it as the same conviction as a 74 in a calm one would hand the
    # sizing code a number that means two different things. Reported separately
    # from the score so a caller can size by conviction without re-deriving the
    # band arithmetic, and None for anything that is not a BUY because
    # conviction in an avoided name is not a meaningful quantity.
    if action == BUY and score is not None:
        span = max(1.0, 100.0 - buy_at)
        conviction = round(max(0.0, min(1.0, (score - buy_at) / span)), 3)
    else:
        conviction = None

    return {
        "action": action,
        "label": ACTION_LABELS[action],
        "reasons": reasons,
        "score": None if score is None else int(score),
        "conviction": conviction,
        "buy_threshold": int(buy_at),
    }


# ==============================
# ONE SYMBOL, END TO END
# ==============================

def evaluate_symbol(frame=None, index_closes=None, regime=None,
                    fundamental=None, news_reading=None, events=None,
                    technical=None, adv=None, held=False, weights=None,
                    position_value=None):
    """Assemble every factor for one name and decide. Returns one dict.

    `frame` is an indicator frame from indicators.calculate_indicators() ending
    at the decision bar -- the caller is responsible for not handing it future
    rows, exactly as backtesting/strategy.py is. Everything else is optional and
    absent inputs simply drop out of the blend.

    `technical` may be passed in when the caller has already scored the frame
    (the backtest panel has), so the same bar is not ranked twice.
    """

    from ranking import rank_stocks

    notes = []

    closes = None
    volumes = None
    volume_ratio = None

    if frame is not None and len(frame):

        if "close" in frame:
            closes = frame["close"]

        if "volume" in frame:
            volumes = frame["volume"]

        last = frame.iloc[-1]

        volume = _finite(last.get("volume"))
        volume_ma = _finite(last.get("VOLUME_MA"))

        if volume is not None and volume_ma and volume_ma > 0:
            volume_ratio = volume / volume_ma

        if technical is None:
            reading = rank_stocks(frame)
            technical = reading.get("score") if reading.get("scored") else None
            notes.extend(reading.get("signals") or [])

        if adv is None and "value" in frame:
            tail = _series(frame["value"])[-20:]
            adv = sum(tail) / len(tail) if tail else None

    momentum_reading = momentum(closes, index_closes=index_closes)

    liquidity_reading = liquidity(
        adv=adv,
        volume_ratio=volume_ratio,
        traded_sessions_pct=traded_share(volumes),
        position_value=position_value,
    )

    catalyst_reading = catalyst(news_reading=news_reading, events=events)

    fundamental_score = None

    if fundamental:
        fundamental_score = fundamental.get("score")

    regime_score_value = (regime or {}).get("score")

    blend = swing_score(
        regime=regime_score_value,
        momentum=momentum_reading.get("score"),
        technical=technical,
        fundamental=fundamental_score,
        liquidity=liquidity_reading.get("score"),
        catalyst=catalyst_reading.get("score"),
        weights=weights,
    )

    decision = decide(
        blend, technical=technical, regime=regime,
        fundamental=fundamental, held=held,
    )

    return {
        "swing": blend,
        "decision": decision,
        "readings": {
            MOMENTUM: momentum_reading,
            LIQUIDITY: liquidity_reading,
            CATALYST: catalyst_reading,
            TECHNICAL: {"score": technical},
            FUNDAMENTAL: fundamental,
            REGIME: regime,
        },
        "notes": notes,
    }
