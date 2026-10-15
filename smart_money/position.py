"""Where the price is standing: the range, the supports under it, the volume.

This is the half of the scanner that says NO. Money flowing in is interesting
only if the price has not already paid for it, so everything here exists to
establish one thing -- that the name is still at the bottom of its own range,
with a real level beneath it, and not three percent below a high it made last
week.

FOUR KINDS OF SUPPORT, BECAUSE THEY FAIL DIFFERENTLY
-----------------------------------------------------
    static      confirmed pivot lows, from technical.support_resistance(). The
                strongest evidence available from price alone, because it
                carries a `touches` count: a shelf the market has turned at
                three times is a different object from one it turned at once.
    dynamic     moving averages below the price. These move with it, so they
                never "break" so much as get left behind -- useful as a floor
                in a trend, worthless in a range.
    channel     the low of the multi-month range itself. The level everyone
                looking at the same chart can see.
    fibonacci   retracements of the last completed swing, via levels.fibonacci.

All four are gathered, the nearest below the price is reported as the one that
matters, and every one of them is returned so the reader can see whether the
nearest is a shelf with four touches or a moving average that happened to be
passing. "Near support" with no support named is the kind of claim this project
does not make.

THE RANGE POSITION IS THE VETO
-------------------------------
`position_pct` places the close between the range low and the range high, 0 at
the floor and 100 at the ceiling. The brief's risk rule -- سهم‌هایی که در سقف
قیمتی هستند حذف شوند -- is this number and a threshold, and it is applied as a
hard exclusion rather than as lost points, because a name at its high is not a
weaker version of an accumulation candidate. It is a different situation.
"""

import math

import pandas as pd

import levels
import technical


# The range the position is measured in: ~6 months of sessions. Long enough
# that a name has to have genuinely based rather than dipped for a fortnight,
# short enough that a company transformed two years ago is not still being
# measured against its old self.
RANGE_WINDOW = 120

# The longer confirmation range, ~1 year. Reported, not gated on: a name at the
# bottom of six months and the middle of twelve is in a different position from
# one at the bottom of both, and the reader should be able to tell.
RANGE_LONG = 250

# Above this position in the range, the name is not an accumulation candidate
# whatever its money flow says. A third of the way up from the floor.
MAX_POSITION_PCT = 35.0

# A support this close to the price is what "near support" means. On a market
# whose daily band is a few percent, 5% away is roughly a session or two of
# room -- close enough that the level is the thing holding the price up.
NEAR_SUPPORT_PCT = 5.0

# Sessions in the volume average. Twenty is the brief's own figure and the
# convention everywhere else in this project.
VOLUME_WINDOW = 20

# The brief's threshold for abnormal volume.
VOLUME_RATIO_FLOOR = 1.5

# Share of a window that must have ACTUALLY TRADED before the readings taken
# over it mean anything.
#
# THIS GATE EXISTS BECAUSE STAGE 1 FOUND WHAT IT MISSED. TSETMC emits a bar for
# every listed session whether or not anything changed hands, so a suspended
# name carries its last close forward at zero volume. On the validation session,
# فارس passed every other gate with an MFI of 74.8 "rising 74.8 points" and
# volume "4.95x its average" -- computed over a window in which it traded on 2
# of 14 sessions. Both numbers were a reopening gap, not accumulation:
#
#   * MFI weights each session by price x volume, so zero-volume bars
#     contribute nothing. Its window collapsed to two real observations, hit
#     0.00, went NaN, and printed 100.00 on the reopening session.
#   * the 20-session volume mean was mostly zeros, so ANY reopening looks like
#     a volume explosion.
#
# The single-session liquidity floor cannot catch this -- a reopening session
# has enormous turnover, which is exactly when the floor is checked.
#
# 85% is factor_engine.liquidity's own band edge (it scores 0.75 there and 0.4
# below 70). The scanner takes the stricter reading of the same scale, because
# for a liquidity SCORE a thin name is weak, while here it is arithmetically
# meaningless: 16.5% of the liquid universe sits below this line.
MIN_TRADED_PCT = 85.0

# A single session's move beyond this is not a price, it is a data fault.
#
# CITED, NOT CHOSEN. This is sirat/experiments/gates.py's own
# IMPOSSIBLE_SESSION_MOVE_PCT, imported rather than restated so the scanner and
# the governance layer cannot disagree about which bars are real.
#
# It started at 200.0 here, copied from examples/e21_contamination_audit.py, and
# Stage 1 validation caught the discrepancy: the scanner was admitting bars the
# platform's own Stage 1 gate counts as impossible, so a name could pass the
# scanner and fail the experiment that scores it. Where two constants in one
# codebase mean the same thing, the governance layer's is the one that binds.
#
# It stays far above the exchange's daily band on purpose -- legitimate
# reopenings after a long suspension do exceed the band (0.39% of bars clear
# 12%), and a check that failed on those would be switched off within a week.
from sirat.experiments.gates import IMPOSSIBLE_SESSION_MOVE_PCT

IMPOSSIBLE_MOVE_PCT = IMPOSSIBLE_SESSION_MOVE_PCT


def _finite(value):
    if value is None:
        return None

    try:
        out = float(value)
    except (TypeError, ValueError):
        return None

    return out if math.isfinite(out) else None


def integrity(frame, window=RANGE_WINDOW):
    """Is the window free of bars the market could not have printed?

    price_history has carried impossible returns before -- 252 rows that no
    session produced -- and both readings this module builds are wrecked by
    one. A single bad high redefines the top of the range, and a single bad
    close redefines the position within it. Cheaper to check than to explain
    afterwards.
    """

    if frame is None or "close" not in frame.columns:
        return {"clean": False, "reason": "سری قیمت پایانی موجود نیست"}

    closes = pd.to_numeric(frame["close"], errors="coerce").tail(window)

    returns = closes.pct_change().abs() * 100.0

    bad = int((returns > IMPOSSIBLE_MOVE_PCT).sum())

    if bad:
        return {
            "clean": False,
            "reason": ("%d بار حرکت بیش از %.0f%% در یک جلسه — داده مخدوش است"
                       % (bad, IMPOSSIBLE_MOVE_PCT)),
            "bad_bars": bad,
        }

    return {"clean": True, "reason": None, "bad_bars": 0}


def traded_share(frame, window):
    """Percent of the last `window` bars on which anything actually traded."""

    if frame is None or "volume" not in frame.columns or window <= 0:
        return None

    volumes = pd.to_numeric(frame["volume"], errors="coerce").tail(window)

    if volumes.empty:
        return None

    return float((volumes.fillna(0) > 0).sum()) / len(volumes) * 100.0


def tradability(frame, mfi_window=None, volume_window=VOLUME_WINDOW):
    """Did this name trade often enough for its readings to mean anything?

    Checked over BOTH windows the scanner takes readings on, because they fail
    differently: a gap in the MFI window corrupts the indicator itself, and a
    gap in the volume window corrupts the denominator every volume ratio is
    measured against. A name can pass one and fail the other -- on the
    validation session نیان traded 86% of the MFI window and 65% of the volume
    window, so its MFI was sound and its "2.8x average volume" was not.
    """

    from .flow import MFI_WINDOW

    mfi_window = mfi_window or MFI_WINDOW

    over_mfi = traded_share(frame, mfi_window)
    over_volume = traded_share(frame, volume_window)

    if over_mfi is None or over_volume is None:
        return {"tradable": False, "reason": "سری حجم موجود نیست",
                "traded_mfi_pct": None, "traded_volume_pct": None}

    thin = []

    if over_mfi < MIN_TRADED_PCT:
        thin.append("در پنجره‌ی MFI فقط %.0f%% از %d جلسه معامله شده"
                    % (over_mfi, mfi_window))

    if over_volume < MIN_TRADED_PCT:
        thin.append("در پنجره‌ی حجم فقط %.0f%% از %d جلسه معامله شده"
                    % (over_volume, volume_window))

    return {
        "tradable": not thin,
        "reason": ("؛ ".join(thin) + " — نماد متوقف یا در صف قفل بوده و "
                   "خوانش‌های این پنجره معنا ندارند") if thin else None,
        "traded_mfi_pct": over_mfi,
        "traded_volume_pct": over_volume,
    }


def range_position(frame, window=RANGE_WINDOW, long_window=RANGE_LONG):
    """Where the close sits between the window's low and high, 0-100.

    Uses the traded high and low, not closing prices: the range a trader sees
    is the one that was actually printed. Returns None when the window has no
    range at all -- a name that traded at one price for six months has no
    position within a range, and calling that 0 would put it at the floor.
    """

    if frame is None or "close" not in frame.columns:
        return None

    if len(frame) < window // 2:
        return None

    tail = frame.tail(window)

    highs = pd.to_numeric(tail.get("high", tail["close"]), errors="coerce")
    lows = pd.to_numeric(tail.get("low", tail["close"]), errors="coerce")
    closes = pd.to_numeric(tail["close"], errors="coerce")

    high = _finite(highs.max())
    low = _finite(lows.min())
    close = _finite(closes.iloc[-1])

    if None in (high, low, close) or high <= low or close <= 0:
        return None

    position = (close - low) / (high - low) * 100.0

    out = {
        "window": int(len(tail)),
        "low": low,
        "high": high,
        "close": close,
        "position_pct": position,
        "above_low_pct": (close - low) / low * 100.0 if low > 0 else None,
        "below_high_pct": (high - close) / high * 100.0 if high > 0 else None,
        "at_ceiling": position > MAX_POSITION_PCT,
    }

    # The longer range, reported alongside. A name at the bottom of both is
    # making a multi-year low; one at the bottom of six months and the middle
    # of a year has merely given back a rally, and those are not the same
    # setup even though the six-month number is identical.
    if len(frame) >= long_window // 2:
        long_tail = frame.tail(long_window)
        long_high = _finite(pd.to_numeric(
            long_tail.get("high", long_tail["close"]), errors="coerce").max())
        long_low = _finite(pd.to_numeric(
            long_tail.get("low", long_tail["close"]), errors="coerce").min())

        if (long_high is not None and long_low is not None
                and long_high > long_low):
            out["long_window"] = int(len(long_tail))
            out["long_position_pct"] = (
                (close - long_low) / (long_high - long_low) * 100.0)
            out["long_low"] = long_low
            out["long_high"] = long_high

    return out


def _candidate(kind, label, price, close, extra=None):
    """One support level below the close, with its distance in percent."""

    price = _finite(price)

    if price is None or price <= 0 or close <= 0 or price >= close:
        return None

    entry = {
        "kind": kind,
        "label": label,
        "price": round(price, 2),
        "distance_pct": (close - price) / close * 100.0,
    }

    if extra:
        entry.update(extra)

    return entry


def supports(frame, close, range_reading=None):
    """Every support under the price, nearest first, from all four families.

    Returns {"nearest": ..., "levels": [...]}. `nearest` is None when nothing
    sits below the close -- which happens on a name printing an all-time low,
    and is worth saying out loud rather than filling with the lowest number to
    hand.
    """

    close = _finite(close)

    if frame is None or close is None or close <= 0:
        return {"nearest": None, "levels": []}

    found = []

    # --- static: confirmed pivot lows, clustered, with their touch counts ---
    try:
        sr = technical.support_resistance(frame, close)
    except Exception:                                           # noqa: BLE001
        sr = {"supports": []}

    for level in (sr.get("supports") or []):
        entry = _candidate(
            "static", "حمایت استاتیک (پیوت)", level.get("price"), close,
            {"touches": level.get("touches"), "last_touch": level.get("date")})
        if entry:
            found.append(entry)

    # --- dynamic: moving averages that happen to be below the price ---------
    closes = pd.to_numeric(frame["close"], errors="coerce")

    for span, label in ((50, "میانگین ۵۰ روزه"), (200, "میانگین ۲۰۰ روزه")):
        if len(closes) < span:
            continue
        entry = _candidate("dynamic", label,
                           closes.rolling(span).mean().iloc[-1], close)
        if entry:
            found.append(entry)

    # --- channel: the floor of the range itself -----------------------------
    if range_reading:
        entry = _candidate(
            "channel", "کف کانال %d روزه" % range_reading.get("window", 0),
            range_reading.get("low"), close)
        if entry:
            found.append(entry)

    # --- fibonacci: retracements of the last completed swing -----------------
    try:
        fib = levels.fibonacci(frame)
    except Exception:                                           # noqa: BLE001
        fib = None

    for name, price in ((fib or {}).get("levels") or {}).items():
        if not name.startswith("retr_"):
            continue
        entry = _candidate("fibonacci", "فیبوناچی %s" % name.split("_")[1],
                           price, close)
        if entry:
            found.append(entry)

    found.sort(key=lambda e: e["distance_pct"])

    return {"nearest": found[0] if found else None, "levels": found}


def volume_ratio(frame, window=VOLUME_WINDOW):
    """Latest session's volume against its own `window`-session average.

    Two things this excludes from the denominator, for two different reasons:

    THE LATEST SESSION. A ratio whose denominator contains its own numerator is
    compressed toward 1 exactly when the session is unusual, which is the only
    time the number is worth reading.

    SESSIONS THAT DID NOT TRADE. TSETMC emits a bar for every listed session
    whether or not anything changed hands, so a suspended or queue-locked name
    contributes zeros. Averaging those in answers a different question -- "how
    much did this name trade per CALENDAR session" -- and the answer is
    mechanically small, so every reopening reads as a volume explosion. The
    question here is whether TODAY is unusual for a TRADING day, so the mean is
    taken over trading days. `tradability()` still refuses the name outright
    when too few of them exist; this keeps the ratio honest for the rest.
    """

    if frame is None or "volume" not in frame.columns:
        return None

    volumes = pd.to_numeric(frame["volume"], errors="coerce")

    if len(volumes) < window + 1:
        return None

    latest = _finite(volumes.iloc[-1])

    baseline = volumes.iloc[-(window + 1):-1]
    traded = baseline[baseline.fillna(0) > 0]

    if traded.empty:
        return None

    average = _finite(traded.mean())

    if latest is None or average is None or average <= 0:
        return None

    return latest / average
