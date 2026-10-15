"""The 100-point model, the vetoes that run before it, and the sentence it writes.

THE WEIGHTS ARE THE BRIEF'S, UNCHANGED
---------------------------------------
    MFI                        15
    P/E against the sector      15
    small / mid cap             10
    حقوقی -> حقیقی transfer      20
    کد به کد concentration      15
    floor and support position  15
    abnormal volume             10
                               ---
                               100

They are declared here, once, and nothing else in this package holds a number
that adds to a score.

MISSING COMPONENTS SCORE ZERO. THEY ARE NOT REDISTRIBUTED.
-----------------------------------------------------------
moneyflow.score() spreads the weight of an uncomputable component across the
rest, and is right to: it is describing ONE name and the question is "what does
the flow say about it". This is a RANKING, and the same rule would be a bug.
Redistribute here and a name with no P/E, no market cap and no client-type data
can score 100 out of the three components it does have -- and the scanner would
put the market's worst-documented names at the top of its list, every run,
which is the opposite of what a screen is for.

So absent evidence earns nothing, and `coverage` reports how much of the model
actually ran, so a 70 out of a full model and a 70 out of two-thirds of one
stay distinguishable.

THE VETOES RUN FIRST, AND THEY ARE NOT DEDUCTIONS
--------------------------------------------------
The brief's risk rules (11) are exclusions, and they are implemented as
exclusions. A name at the top of its range is not a weaker accumulation
candidate that should score 60 instead of 85; it is not one at all, and letting
it into the list on a high score from the other six components is exactly how a
screen ends up recommending the thing it was built to avoid.
"""

import math

import config

from . import position


# ==============================
# WEIGHTS -- the brief's, and the only place they exist
# ==============================

MFI = "MFI"
VALUATION = "P/E نسبت به صنعت"
SIZE = "اندازه شرکت"
TRANSFER = "حقوقی به حقیقی"
CODE_TO_CODE = "کد به کد"
SUPPORT = "کف و حمایت"
VOLUME = "حجم غیرعادی"

WEIGHTS = {
    MFI: 15,
    VALUATION: 15,
    SIZE: 10,
    TRANSFER: 20,
    CODE_TO_CODE: 15,
    SUPPORT: 15,
    VOLUME: 10,
}

# The brief's own gate. Not a tunable: moving it is changing what the scanner
# is, not tuning how it behaves.
MFI_FLOOR = 70.0

# The MFI climb, in index points over the rise window, that earns the full rise
# term. 20 points of a 0-100 oscillator inside a week is a decisive change in
# who is paying for the stock.
MFI_RISE_FULL = 20.0


# ==============================
# MARKET-CAP BANDS
# ==============================
#
# Terciles of the live snapshot, NOT fixed rial thresholds.
#
# On a market whose currency has lost most of its value in a decade, an
# absolute cutoff silently reclassifies the whole exchange upward: a "large
# cap" line drawn at 50 trillion rials captures a third of the market this year
# and two thirds in three years, with nobody having changed anything. Terciles
# of whatever is listed today keep "small" meaning small RELATIVE TO THIS
# MARKET, which is what the category was ever for.
#
# Measured on the session this was written: p33 = 33T rial, p67 = 121T rial.

SMALL = "Small Cap"
MID = "Mid Cap"
LARGE = "Large Cap"

SIZE_FA = {SMALL: "کوچک", MID: "متوسط", LARGE: "بزرگ"}

# Large caps are kept, at reduced weight -- the brief's instruction, and the
# right one: a large company being accumulated is still worth seeing, it just
# cannot move the way a small one can.
SIZE_FRACTION = {SMALL: 1.0, MID: 0.85, LARGE: 0.30}


def size_band(cap, p33, p67):
    if cap is None or p33 is None or p67 is None:
        return None

    if cap <= p33:
        return SMALL

    if cap <= p67:
        return MID

    return LARGE


# ==============================
# VALUATION BANDS
# ==============================
#
# READING OF AN AMBIGUOUS LINE IN THE BRIEF, RECORDED SO IT CAN BE CORRECTED.
#
# The brief asks for two things that pull against each other: cheaper than the
# sector ranks higher (بند ۲), and "هرچه فاصله منطقی کمتر باشد، امتیاز بالاتر"
# -- the smaller the REASONABLE gap, the better. Taken together with "با
# سودآوری مناسب ارزان‌تر از گروه", they were read as one idea: a moderate
# discount is the signal, and an extreme one is usually a warning. A share at a
# 90% discount to its sector's P/E is not nine times the opportunity of one at
# 10%; it is a company the market has decided will not keep earning, and this
# programme has a whole fundamental_engine of risk flags for that case.
#
# So the band peaks in the middle and tapers at both ends. If the intended
# meaning was "monotonically cheaper is better", the two 65%+ rows are the only
# lines that need changing.

VALUATION_BANDS = [
    # (discount_from_sector_median_pct, fraction, note)
    (65.0, 0.55, "%.0f%% زیر میانه صنعت — تخفیف بیش از حد؛ معمولاً بازار "
                 "انتظار افت سود دارد"),
    (45.0, 0.85, "%.0f%% زیر میانه صنعت — تخفیف چشمگیر"),
    (20.0, 1.00, "%.0f%% زیر میانه صنعت — ارزان‌تر از گروه، در محدوده منطقی"),
    (8.0, 0.75, "%.0f%% زیر میانه صنعت — کمی ارزان‌تر از گروه"),
    (-8.0, 0.45, "%+.0f%% نسبت به میانه صنعت — هم‌تراز گروه"),
    (-30.0, 0.15, "%+.0f%% نسبت به میانه صنعت — گران‌تر از گروه"),
    (float("-inf"), 0.0, "%+.0f%% نسبت به میانه صنعت — به‌مراتب گران‌تر از گروه"),
]


# ==============================
# STATUS
# ==============================
#
# Every label is a watch label. None of them is a buy: this programme's own
# research (E19, E21, E23, E24) rejected the return claims behind every
# ingredient in this model, and a status vocabulary containing the word "خرید"
# would quietly overturn that with a colour.

STRONG_WATCH = "تحت نظر ویژه"
WATCH = "تحت نظر"
MONITOR = "زیر نظر"
WEAK = "ضعیف"

STATUS_BANDS = [(75, STRONG_WATCH), (60, WATCH), (45, MONITOR)]


def status_for(score):
    for floor, label in STATUS_BANDS:
        if score >= floor:
            return label

    return WEAK


def _finite(value):
    if value is None:
        return None

    try:
        out = float(value)
    except (TypeError, ValueError):
        return None

    return out if math.isfinite(out) else None


def _band(value, bands):
    """First (fraction, note) whose descending threshold `value` clears."""

    for threshold, fraction, note in bands:
        if value >= threshold:
            return fraction, note

    return bands[-1][1], bands[-1][2]


# ==============================
# THE VETOES (risk rules, بند ۱۱)
# ==============================

def vetoes(candidate):
    """Reasons this name must not appear at all. Empty list means it may.

    Each entry is a (rule, sentence) pair -- the rule so a caller can count
    which gate does the work, the sentence so the reader is told why a name
    they expected is missing. ALL failing rules are returned, not the first, so
    the counts describe the market rather than the order of the checks. They
    therefore sum to more than the number of names rejected.
    """

    out = []

    integrity = candidate.get("integrity") or {}

    if not integrity.get("clean", False):
        out.append(("data_integrity", integrity.get("reason")
                    or "داده قیمتی قابل اتکا نیست"))

    # Thin trading. Not a weak signal -- an arithmetically meaningless one.
    # Stage 1 validation found this gate missing: فارس qualified on an MFI and
    # a volume ratio computed over a window it traded 2 of 14 sessions in.
    tradable = candidate.get("tradability") or {}

    if not tradable.get("tradable", False):
        out.append(("thin_trading", tradable.get("reason")
                    or "تعداد جلسات معامله‌شده برای خوانش کافی نیست"))

    ranged = candidate.get("range") or {}
    mfi = candidate.get("mfi") or {}
    supports = candidate.get("supports") or {}

    value = _finite(mfi.get("value"))

    if value is None or value <= MFI_FLOOR:
        out.append(("mfi_floor",
                    "MFI %s — شرط بالای %d برقرار نیست"
                    % ("نامشخص" if value is None else "%.1f" % value,
                       int(MFI_FLOOR))))

    pos = _finite(ranged.get("position_pct"))

    if pos is None:
        out.append(("no_range", "محدوده قیمتی چندماهه قابل محاسبه نیست"))
    elif pos > position.MAX_POSITION_PCT:
        # سهم‌هایی که در سقف قیمتی هستند حذف شوند.
        out.append(("at_ceiling",
                    "قیمت در %.0f%% بالای محدوده %d روزه — در کف نیست"
                    % (pos, ranged.get("window") or 0)))

    # Queue state. E14's detector: a session with no range that moved by the
    # band is a queue, and a queued name cannot be bought at a normal price.
    # E21 measured that this flow signal predicts locks rather than returns, so
    # a scanner built on flow that does NOT check for locks is measuring E21's
    # finding and calling it accumulation.
    locked = candidate.get("locks") or {}

    if locked.get("locked_now"):
        out.append(("queue_locked", locked.get("reason")
                    or "در آخرین جلسه در صف بوده"))

    # The premise itself. "Near the floor of the range" says where the price
    # stands; it does not say the price is standing still. A name bouncing off a
    # crash satisfies the range gate while being the opposite of quiet
    # accumulation -- and the failure of such a bounce is what puts it in a
    # صف فروش the following week.
    moved = candidate.get("advance") or {}

    if moved.get("quiet") is False:
        out.append(("already_moved", moved.get("reason")
                    or "حرکت قیمت قبلاً شروع شده"))

    # بند ۴ states the transfer as a CONDITION -- "شرط مهم: وجود انتقال سهم از
    # حقوقی به حقیقی‌های قدرتمند" -- not merely as twenty points, so it is
    # enforced as one. A name whose money is leaving is not a weakly accumulated
    # name; it is a distributed one, and the scanner is called Accumulation.
    transfer = candidate.get("transfer")

    if not transfer:
        out.append(("no_flow_data",
                    "داده‌ی حقیقی/حقوقی برای این نماد ذخیره نشده — شرط انتقال "
                    "قابل بررسی نیست"))
    elif not transfer.get("into_individual_hands"):
        out.append(("no_transfer",
                    "خالص %+.1f%% — پول از دست حقیقی خارج شده، انتقال حقوقی "
                    "به حقیقی رخ نداده"
                    % (_finite(transfer.get("net_pct")) or 0.0)))

    nearest = supports.get("nearest")
    distance = _finite((nearest or {}).get("distance_pct"))

    # MFI بالا بدون حمایت قیمتی کافی نباید سیگنال بدهد. This is the rule that
    # makes the whole scanner mean something: without it, an MFI above 70 is
    # just the market going up, and half the exchange qualifies.
    if nearest is None:
        out.append(("no_support",
                    "هیچ حمایتی زیر قیمت شناسایی نشد — کف تاریخی در حال ساخته "
                    "شدن است"))
    elif distance is None or distance > position.NEAR_SUPPORT_PCT:
        out.append(("far_from_support",
                    "نزدیک‌ترین حمایت %.1f%% پایین‌تر است — بیش از آستانه %.0f%%"
                    % (distance or 0.0, position.NEAR_SUPPORT_PCT)))

    return out


# ==============================
# THE COMPONENTS
# ==============================

def _score_mfi(candidate):
    """15 points. Level, direction, and the divergence that is the real tell.

    The brief asks for extra credit when MFI is high WHILE the price is still
    at support. That credit is the divergence term and only the divergence
    term -- "MFI up while price down" is literally what a positive divergence
    is. Paying for it again out of a price-position bonus would be the support
    component's 15 points scored twice under two names.
    """

    mfi = candidate.get("mfi") or {}

    value = _finite(mfi.get("value"))

    if value is None:
        return None, "MFI قابل محاسبه نیست"

    fraction = 0.45
    note = "MFI %.1f" % value

    # The RISE, scored continuously rather than as a flag.
    #
    # It was a flag until the component's discrimination was measured: among the
    # 231 names that clear MFI>70, the component took just FOUR distinct values
    # with a standard deviation of 1.81 out of 15 points. Gating on MFI and then
    # scoring MFI in three coarse steps meant 15 of the 100 points barely
    # reordered anything. The brief asks for "افزایش MFI در روزهای اخیر بررسی
    # شود" -- examine the rise -- and a boolean does not examine a magnitude.
    #
    # MFI_RISE_FULL points is the climb that earns the whole rise term. 20
    # points of a 0-100 oscillator over a week is a decisive change in who is
    # paying; below that it scales linearly, and a fall earns nothing.
    change = _finite(mfi.get("change"))
    sessions = mfi.get("rise_sessions") or 0

    if change is not None and change > 0:
        fraction += 0.30 * min(1.0, change / MFI_RISE_FULL)
        note += " و در %d جلسه‌ی اخیر %+.1f واحد رشد کرده" % (sessions, change)
    elif change is not None:
        note += " و در %d جلسه‌ی اخیر %+.1f واحد تغییر کرده" % (sessions, change)

    div = mfi.get("divergence") or {}

    if div.get("positive"):
        fraction += 0.25
        note += ("؛ واگرایی مثبت: کف قیمت %.1f%% پایین‌تر آمده اما MFI %+.1f "
                 "واحد بالاتر رفته" % (div.get("price_change_pct") or 0.0,
                                       div.get("mfi_change") or 0.0))

    return min(1.0, fraction), note


def _score_valuation(candidate):
    """15 points. The name's P/E against its own sector's median.

    Against the SECTOR, never the market: a P/E of 12 is expensive in سيمان and
    cheap in دارو, and a market-wide comparison would rank this scanner by
    industry membership. fundamental_engine.sector_medians supplies the median
    and refuses to compute one from fewer than three comparables, which is the
    rule inherited here.
    """

    valuation = candidate.get("valuation") or {}

    pe = _finite(valuation.get("pe"))
    median = _finite(valuation.get("sector_pe"))

    if pe is None or pe <= 0:
        return None, ("P/E مثبتی برای این نماد ثبت نشده — شرکت زیان‌ده است یا "
                      "سود گزارش‌شده ندارد")

    if median is None or median <= 0:
        return None, ("میانه‌ی P/E صنعت «%s» با حداقل ۳ هم‌گروه در دسترس نیست"
                      % (valuation.get("sector") or "؟"))

    discount = (median - pe) / median * 100.0

    fraction, template = _band(discount, VALUATION_BANDS)

    return fraction, ("P/E %.1f در برابر میانه‌ی صنعت %.1f — %s"
                      % (pe, median, template % discount))


def _score_size(candidate):
    """10 points. Small and mid caps up, large caps kept but muted."""

    size = candidate.get("size") or {}

    band = size.get("band")
    cap = _finite(size.get("market_cap"))

    if band is None or cap is None:
        return None, "ارزش بازار قابل محاسبه نیست (تعداد سهام یا قیمت ثبت نشده)"

    return SIZE_FRACTION[band], (
        "ارزش بازار %.1f همت — %s (%s)"
        % (cap / 1e13, SIZE_FA[band], band))


def _score_transfer(candidate):
    """20 points. How much ownership moved from حقوقی to حقیقی hands.

    Thresholds are config.FLOW_NET_*, the same percentiles moneyflow scores
    against, so this component and the panel's flow view cannot disagree about
    what counts as a strong inflow. Persistence modulates rather than adds: a
    transfer carried by one session and one carried by five are different
    events at the same net percentage, and the count columns already say which
    is which.
    """

    reading = candidate.get("transfer") or {}

    net_pct = _finite(reading.get("net_pct"))

    if net_pct is None:
        return None, "داده‌ی حقیقی/حقوقی برای این نماد موجود نیست"

    if net_pct <= 0:
        return 0.0, ("خالص %+.1f%% از گردش — پول از دست حقیقی خارج شده، "
                     "انتقالی به سمت حقیقی رخ نداده" % net_pct)

    level, note = _band(net_pct, [
        (config.FLOW_NET_STRONG, 1.00, "ورود قوی"),
        (config.FLOW_NET_GOOD, 0.80, "ورود روشن"),
        (config.FLOW_NET_WEAK, 0.45, "ورود ملایم"),
        (float("-inf"), 0.0, "بدون ورود"),
    ])

    persistence = _finite(reading.get("persistence"))

    # 0.7 of the score is the size of the transfer, 0.3 is how much of the
    # window agreed with it. A one-session spike keeps 70% of what it earned,
    # not 100% and not nothing.
    if persistence is None:
        fraction = level
        detail = ""
    else:
        fraction = level * (0.7 + 0.3 * persistence)
        detail = " در %d از %d جلسه" % (reading.get("sessions_positive") or 0,
                                        reading.get("sessions") or 0)

    return fraction, ("خالص %+.1f%% از گردش پنجره به سمت حقیقی — %s%s"
                      % (net_pct, note, detail))


def _score_code_to_code(candidate):
    """15 points. Not how much moved -- into how few hands it moved.

    This is the only component that reads the participant COUNTS, which is what
    keeps it independent of the 20-point transfer component above. Same net
    inflow, ten tickets or ten thousand, is two different events, and nothing
    in the value columns can tell them apart.

    Fails closed: "کد به کد ضعیف یا مصنوعی حذف شود" is implemented by returning
    zero with the failed test named, never by quietly awarding partial credit.
    """

    reading = candidate.get("code_to_code")

    if not reading:
        return None, "شمارش خریداران/فروشندگان حقیقی برای این نماد موجود نیست"

    if not reading.get("genuine", False):
        return 0.0, ("کد به کد رد شد — %s"
                     % "؛ ".join(reading.get("reasons") or ["نامشخص"]))

    ratio = _finite(reading.get("smart_money_ratio")) or 0.0

    fraction, note = _band(ratio, [
        (0.40, 1.00, "تمرکز بسیار بالا"),
        (0.25, 0.85, "تمرکز بالا"),
        (0.12, 0.60, "تمرکز محسوس"),
        (0.05, 0.35, "تمرکز خفیف"),
        (float("-inf"), 0.10, "تمرکز ناچیز"),
    ])

    return fraction, (
        "Smart Money Ratio %.2f — %s: میانگین خرید هر کد حقیقی %.1fx پایه‌ی "
        "خودِ نماد، و حقیقی %.0f%% از حجم جلسه را گرفته"
        % (ratio, note, reading.get("elevation") or 0.0,
           (reading.get("volume_share") or 0.0) * 100))


def _score_support(candidate):
    """15 points. How deep in the range, and how close to a real level.

    Half the component is the position in the range and half is the distance to
    the nearest support, because they answer different questions: the first is
    "has this already run", the second is "is there anything holding it up".
    A name can be deep in its range with nothing beneath it (making a new low)
    and that is not the same setup.
    """

    ranged = candidate.get("range") or {}
    supports = candidate.get("supports") or {}

    pos = _finite(ranged.get("position_pct"))
    nearest = supports.get("nearest")

    if pos is None or nearest is None:
        return None, "موقعیت در محدوده یا حمایت زیر قیمت قابل تعیین نیست"

    # Position: 0% of the range is 1.0, the veto threshold is 0.0, linear.
    ceiling = position.MAX_POSITION_PCT
    depth = max(0.0, min(1.0, (ceiling - pos) / ceiling)) if ceiling > 0 else 0.0

    distance = _finite(nearest.get("distance_pct")) or 0.0
    near_limit = position.NEAR_SUPPORT_PCT
    proximity = max(0.0, min(1.0, (near_limit - distance) / near_limit)) \
        if near_limit > 0 else 0.0

    # A tested shelf is stronger evidence than a moving average that happens to
    # be passing. Touches are the only strength measure price alone offers, so
    # they are the only bonus offered.
    touches = nearest.get("touches")
    bonus = 0.0

    if nearest.get("kind") == "static" and touches:
        bonus = min(0.15, 0.05 * float(touches))

    fraction = min(1.0, 0.5 * depth + 0.5 * proximity + bonus)

    note = ("قیمت در %.0f%% پایینِ محدوده %d روزه، و %.1f%% بالای %s %s"
            % (pos, ranged.get("window") or 0, distance,
               nearest.get("label") or "حمایت",
               "(%d بار تست شده)" % touches if touches else ""))

    long_pos = _finite(ranged.get("long_position_pct"))

    if long_pos is not None:
        note += "؛ در محدوده %d روزه: %.0f%%" % (
            ranged.get("long_window") or 0, long_pos)

    return fraction, note


def _score_volume(candidate):
    """10 points, and the one component that is capped by another.

    "حجم غیرعادی بدون ورود پول هوشمند امتیاز کامل نگیرد" -- volume on its own
    says a lot of stock changed hands, not who took it. A distribution day and
    an accumulation day look identical on this axis. So the earned fraction is
    capped at 40% unless the transfer component is positive, and the cap is
    named in the note rather than applied silently.
    """

    ratio = _finite((candidate.get("volume") or {}).get("ratio"))

    if ratio is None:
        return None, "میانگین حجم ۲۰ روزه قابل محاسبه نیست"

    fraction, note = _band(ratio, [
        (3.0, 1.00, "حجم بسیار غیرعادی"),
        (2.0, 0.85, "حجم به‌شدت بالاتر از میانگین"),
        (position.VOLUME_RATIO_FLOOR, 0.65, "حجم بالاتر از آستانه ۱٫۵ برابر"),
        (1.0, 0.30, "حجم اندکی بالاتر از میانگین"),
        (float("-inf"), 0.0, "حجم کمتر از میانگین"),
    ])

    net_pct = _finite((candidate.get("transfer") or {}).get("net_pct"))

    capped = ""

    if net_pct is None or net_pct <= 0:
        if fraction > 0.4:
            fraction = 0.4
            capped = ("؛ سقف‌گذاری شد: حجم بالا بدون ورود پول هوشمند امتیاز "
                      "کامل نمی‌گیرد")

    return fraction, ("حجم %.2f برابر میانگین ۲۰ روزه — %s%s"
                      % (ratio, note, capped))


COMPONENTS = [
    (MFI, _score_mfi),
    (VALUATION, _score_valuation),
    (SIZE, _score_size),
    (TRANSFER, _score_transfer),
    (CODE_TO_CODE, _score_code_to_code),
    (SUPPORT, _score_support),
    (VOLUME, _score_volume),
]


def score(candidate):
    """The 0-100 accumulation score, its breakdown, and its coverage.

    `coverage` is the share of the model's weight that could be computed at
    all. It is not used to inflate the score (see the module docstring) -- it
    is reported so nobody reads a 62 built from five components as the same
    claim as a 62 built from seven.
    """

    breakdown = {}
    notes = []
    total = 0.0
    covered = 0

    for label, fn in COMPONENTS:

        weight = WEIGHTS[label]

        fraction, note = fn(candidate)

        notes.append("%s: %s" % (label, note))

        if fraction is None:
            breakdown[label] = None
            continue

        covered += weight

        points = weight * max(0.0, min(1.0, fraction))
        breakdown[label] = round(points, 1)
        total += points

    value = int(round(max(0.0, min(100.0, total))))

    return {
        "score": value,
        "status": status_for(value),
        "breakdown": breakdown,
        "notes": notes,
        "coverage": round(covered / sum(WEIGHTS.values()), 2),
        "max_possible": covered,
    }


# ==============================
# THE SENTENCE
# ==============================

def narrative(candidate, scored):
    """One Persian paragraph a human can check against the chart.

    Built only from components that actually fired. A template that says the
    same seven things about every name teaches the reader to stop reading it,
    and one that asserts a condition the data did not support is worse than
    saying nothing.
    """

    parts = []

    mfi = _finite((candidate.get("mfi") or {}).get("value"))
    ranged = candidate.get("range") or {}
    nearest = (candidate.get("supports") or {}).get("nearest") or {}

    pos = _finite(ranged.get("position_pct"))
    distance = _finite(nearest.get("distance_pct"))

    if pos is not None and distance is not None:
        parts.append(
            "این نماد در %.0f%% پایینِ محدوده‌ی %d روزه‌ی خود و تنها %.1f%% "
            "بالای %s قرار دارد"
            % (pos, ranged.get("window") or 0, distance,
               nearest.get("label") or "نزدیک‌ترین حمایت"))

    if mfi is not None:
        sentence = "MFI روی %.1f است" % mfi
        div = (candidate.get("mfi") or {}).get("divergence") or {}
        if div.get("positive"):
            sentence += (" و با وجود پایین‌تر آمدن کف قیمت، خودِ MFI بالاتر "
                         "رفته — واگرایی مثبت")
        elif (candidate.get("mfi") or {}).get("rising"):
            sentence += " و در حال صعود است"
        parts.append(sentence)

    valuation = candidate.get("valuation") or {}
    pe = _finite(valuation.get("pe"))
    sector_pe = _finite(valuation.get("sector_pe"))

    if pe is not None and sector_pe is not None and sector_pe > 0:
        if pe < sector_pe:
            parts.append("P/E برابر %.1f در مقابل میانه‌ی %.1f گروه «%s» — "
                         "ارزان‌تر از هم‌گروه‌ها"
                         % (pe, sector_pe, valuation.get("sector") or "؟"))
        else:
            parts.append("P/E برابر %.1f در مقابل میانه‌ی %.1f گروه «%s» — "
                         "گران‌تر از هم‌گروه‌ها"
                         % (pe, sector_pe, valuation.get("sector") or "؟"))

    transfer = candidate.get("transfer") or {}
    net_pct = _finite(transfer.get("net_pct"))

    if net_pct is not None and net_pct > 0:
        parts.append("در %d جلسه‌ی اخیر خالص %.1f%% از گردش معاملات از حقوقی "
                     "به حقیقی منتقل شده"
                     % (transfer.get("sessions") or 0, net_pct))

    code = candidate.get("code_to_code") or {}

    if code.get("genuine"):
        parts.append("و میانگین خرید هر کد حقیقی %.1f برابر پایه‌ی خودِ نماد "
                     "است (Smart Money Ratio %.2f) — الگوی کد به کد"
                     % (code.get("elevation") or 0.0,
                        code.get("smart_money_ratio") or 0.0))
    elif code.get("reasons"):
        parts.append("اما کد به کد تأیید نشد: %s" % code["reasons"][0])

    ratio = _finite((candidate.get("volume") or {}).get("ratio"))

    if ratio is not None and ratio >= 1.0:
        parts.append("حجم معاملات %.2f برابر میانگین ۲۰ روزه است" % ratio)

    size = candidate.get("size") or {}

    if size.get("band"):
        parts.append("اندازه‌ی شرکت %s است" % SIZE_FA[size["band"]])

    body = "، ".join(parts) + "."

    return ("%s امتیاز جمع‌آوری %d از ۱۰۰ (%s). این یک فهرست تحت نظر است، "
            "نه سیگنال خرید." % (body, scored["score"], scored["status"]))
