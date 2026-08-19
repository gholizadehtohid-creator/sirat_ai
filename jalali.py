"""Jalali (Solar Hijri) to Gregorian conversion.

WHY THIS EXISTS
---------------
Two calendars meet inside this project and nowhere else was it a problem.
Prices are stored Gregorian ("2026-08-11") because that is what TSETMC emits.
Codal stores everything Jalali ("1405/05/21 17:53:31") because that is what
codal.ir emits, and financials.py deliberately keeps period ends unconverted:
they are keys, and a converted key no longer matches what the site displays.

The backtester breaks that peace. To answer "was this filing public on the day
the model traded" the two have to be on one axis, and prices are the axis that
cannot move -- every bar, every indicator and the benchmark are Gregorian.

NO NEW DEPENDENCY
-----------------
jdatetime would do this in one line and adds a package to a requirements file
whose own header explains why four libraries were rejected. The algorithm is
forty lines of integer arithmetic with no ambiguity in it, and it is tested
against fixed points below rather than trusted.

The conversion is the standard day-count method: both calendars are reduced to
a day number and the difference is arithmetic. It is exact for 1206-1498 Jalali
(1827-2119 Gregorian), which covers every date any Iranian market data source
will ever produce.
"""

import re
from datetime import date, datetime, timedelta


# Days in each Gregorian month; February is patched per year.
_GREGORIAN_MONTH_DAYS = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_gregorian_leap(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def to_gregorian(jy, jm, jd):
    """(jy, jm, jd) Jalali -> (gy, gm, gd) Gregorian, as integers.

    Raises ValueError on a date that does not exist, rather than silently
    returning a nearby one -- a filing dated 1404/13/01 is a parsing bug, and
    quietly turning it into a real day would hide it inside a backtest result.
    """

    jy, jm, jd = int(jy), int(jm), int(jd)

    if not 1 <= jm <= 12:
        raise ValueError("Jalali month out of range: %s" % jm)

    # Months 1-6 have 31 days, 7-11 have 30, month 12 has 29 (30 in a leap
    # year). The leap check below is the same one the day count relies on.
    if jm <= 6:
        month_length = 31
    elif jm <= 11:
        month_length = 30
    else:
        month_length = 30 if is_jalali_leap(jy) else 29

    if not 1 <= jd <= month_length:
        raise ValueError("Jalali day out of range: %s/%s/%s" % (jy, jm, jd))

    shifted = jy + 1595

    days = (
        -355668
        + 365 * shifted
        + (shifted // 33) * 8
        + ((shifted % 33) + 3) // 4
        + jd
        + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    )

    gy = 400 * (days // 146097)
    days %= 146097

    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1

    gy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365

    gd = days + 1

    months = list(_GREGORIAN_MONTH_DAYS)

    if _is_gregorian_leap(gy):
        months[2] = 29

    gm = 1

    while gm <= 12 and gd > months[gm]:
        gd -= months[gm]
        gm += 1

    return gy, gm, gd


def is_jalali_leap(jy):
    """Leap year under the 33-year cycle the day count above assumes.

    Kept consistent with to_gregorian() on purpose: a different leap rule here
    would make month 12 accept a day the conversion then places in the next
    year, which is the one error in this file that would not raise.
    """

    return ((jy + 2346) * 683) % 2820 < 683


# "1405/05/21 17:53:31" or "1405/05/21" or "۱۴۰۵/۰۵/۲۱". Persian digits are
# folded before matching, so both spellings reach the same parser.
_DATE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")

_PERSIAN_DIGITS = {}

for _offset in (0x0660, 0x06F0):
    for _i in range(10):
        _PERSIAN_DIGITS[chr(_offset + _i)] = str(_i)

_DIGIT_TABLE = str.maketrans(_PERSIAN_DIGITS)


def parse(value, default_time=None):
    """Jalali date/datetime string -> a Gregorian `datetime`, or None.

    `default_time` fills the clock when the string carries only a date. It is
    the caller's decision because it is not a formatting choice: a filing whose
    time is unknown must be treated as arriving at the END of its day, which is
    23:59, so that a same-day trade cannot claim to have seen it.

    Returns None rather than raising for unparseable input -- a malformed
    published_at in one of 15,773 letters should cost that letter, not the run.
    Callers treat None as "publication date unknown", which downstream means
    the filing is unusable, not that it is usable.
    """

    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    text = str(value).strip().translate(_DIGIT_TABLE)

    if not text:
        return None

    found = _DATE.search(text)

    if not found:
        return None

    try:
        gy, gm, gd = to_gregorian(*(int(g) for g in found.groups()))
    except (ValueError, TypeError):
        return None

    hour, minute, second = default_time or (23, 59, 59)

    clock = _TIME.search(text[found.end():])

    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2))
        second = int(clock.group(3) or 0)

        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            hour, minute, second = default_time or (23, 59, 59)

    try:
        return datetime(gy, gm, gd, hour, minute, second)
    except ValueError:
        return None


def parse_date(value):
    """Jalali date string -> a Gregorian `date`, or None. Time discarded."""

    stamp = parse(value)

    return stamp.date() if stamp else None


def has_time(value):
    """Does this Jalali string carry a clock, or only a day?

    The backtester asks because the answer changes the rule it applies. With a
    timestamp a filing is visible from the moment it was published; without one
    it is visible from the next session, and the difference is a whole trading
    day of look-ahead.
    """

    if value is None:
        return False

    text = str(value).translate(_DIGIT_TABLE)

    found = _DATE.search(text)

    if not found:
        return False

    return bool(_TIME.search(text[found.end():]))


def add_months(jy, jm, count):
    """Jalali (year, month) shifted by `count` months. Day is not involved."""

    total = (jy * 12 + (jm - 1)) + count

    return total // 12, total % 12 + 1


def today_jalali(reference=None):
    """Gregorian date -> (jy, jm, jd). The inverse, by search.

    Only used to label reports, so a binary search over a 400-day bracket is
    cheaper to trust than a second conversion formula that could disagree with
    the first one.
    """

    target = reference or date.today()

    if isinstance(target, datetime):
        target = target.date()

    # Jalali year is Gregorian year - 621 or - 622; try both and keep the one
    # whose Farvardin 1 is on or before the target.
    for jy in (target.year - 621, target.year - 622):
        try:
            start = date(*to_gregorian(jy, 1, 1))
        except ValueError:
            continue

        if start <= target:
            delta = (target - start).days

            jm = 1
            remaining = delta

            while True:
                length = 31 if jm <= 6 else (30 if jm <= 11 else (30 if is_jalali_leap(jy) else 29))
                if remaining < length:
                    return jy, jm, remaining + 1
                remaining -= length
                jm += 1
                if jm > 12:
                    break

    return None


def sessions_later(stamp, days=1):
    """Calendar days after a datetime. Not sessions -- the caller aligns.

    Named for what the caller wants rather than what it does, and documented
    here rather than in the caller, because the distinction matters: this
    module has no trading calendar and must not pretend to one.
    """

    return stamp + timedelta(days=days) if stamp else None
