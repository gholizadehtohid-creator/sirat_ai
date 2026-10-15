"""حقوقی -> حقیقی: how much moved, and into how few hands.

THE FACT THIS MODULE IS BUILT AROUND
-------------------------------------
Every rial bought was sold by somebody. On TSETMC's own numbers,

    individual_buy + institutional_buy  ==  individual_sell + institutional_sell

so net حقیقی flow is net حقوقی flow with the sign flipped. moneyflow.py says
this in its docstring and refuses to report both; this module inherits the
rule. "حقوقی sold" and "حقیقی bought" are ONE observation. Scoring them as two
agreeing pieces of evidence would double the weight of a single number, and on
a 100-point model that is a third of the score awarded twice for one fact.

So the two components here measure different columns:

    transfer()      VALUES.  How much ownership moved, against the window's own
                    turnover. One fact, scored once. This is the 20-point
                    component.

    code_to_code()  COUNTS.  How concentrated the receiving side is -- whether
                    the stock went to a handful of large buyers or to a crowd.
                    Independent of the size of the transfer: the same net
                    inflow can arrive as ten tickets or ten thousand, and only
                    the count columns can tell those apart. This is the
                    15-point component.

WHAT THE SMART MONEY RATIO CAN AND CANNOT SEE
----------------------------------------------
The brief asks for

    Smart Money Ratio = volume bought by LARGE individuals / total daily volume

TSETMC does not publish per-code figures. It publishes, per side, a value, a
volume and a COUNT of participants -- so the average ticket is knowable and the
distribution behind it is not. Anyone claiming to have measured "the volume
bought by large individuals" from this feed has estimated it.

This module estimates it, and says so, with one stated definition:

    elevation   = today's average individual ticket / the same name's own
                  baseline average ticket over the preceding sessions
    large_share = max(0, 1 - 1/elevation)
    ratio       = individual share of session volume x large_share

At elevation 1 (buyers as big as usual) large_share is 0 and the ratio is 0.
At 2x it is 0.5, at 4x it is 0.75, and it approaches 1 without reaching it.
Read it as "the share of the session's volume that is the EXCESS size of
individual buyers over their own normal ticket" -- which is what is actually
measurable -- and not as a count of large buyers, which is not.

The baseline is the name's OWN history, never the market's. An average ticket
of 400m rials is enormous in a small industrial and unremarkable in a bank, and
a cross-sectional threshold would rank the scanner by company size a second
time, having already scored size on purpose in its own component.
"""

import math

import config


# Sessions in the recent reading. config.FLOW_WINDOW is what moneyflow uses,
# and using anything else here would put two different "recent" windows in one
# report.
WINDOW = config.FLOW_WINDOW

# Sessions the baseline ticket is taken from. FLOW_BASELINE_WINDOW minus the
# recent window, because the baseline must EXCLUDE the sessions it is the
# baseline for -- a ticket compared against a mean that contains it is compared
# against itself, and the elevation shrinks toward 1 exactly when the signal is
# strongest.
BASELINE_WINDOW = config.FLOW_BASELINE_WINDOW

# Minimum baseline sessions. Below this the elevation is a ratio to an
# anecdote, and the honest answer is that there is no baseline.
MIN_BASELINE_SESSIONS = 8

# Participants below which a per-capita figure is one person's rounding error
# rather than an average. Not a filter on the SIGNAL -- few buyers is the
# signal -- but on the arithmetic: a "mean ticket" over two codes is a ticket.
MIN_BUYERS = 3

# Churn floor. A session where حقیقی bought 100bn and sold 98bn moved almost
# nothing while looking enormous on the buy side; that shape is wash-like, and
# the کد به کد component must not pay for it. Net as a share of gross حقیقی
# turnover, so it is scale-free.
MIN_NET_TO_GROSS = 0.10


def _finite(value):
    if value is None:
        return None

    try:
        out = float(value)
    except (TypeError, ValueError):
        return None

    return out if math.isfinite(out) else None


def _sum(frame, column):
    """Sum a column over a frame, absent or non-finite entries as zero."""

    import pandas as pd

    if frame is None or column not in frame.columns:
        return 0.0

    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def transfer(reading):
    """Ownership actually changing hands, from a moneyflow.measure() reading.

    Takes the reading rather than recomputing it, so this scanner and the
    panel's own flow view can never disagree about the same window's numbers.

    Returns None when there is no reading. The direction test is on net حقیقی
    flow ONLY; `institutional_supply` is reported as the mirror it is, not as
    a second measurement.
    """

    if not reading:
        return None

    net_pct = _finite(reading.get("net_pct"))

    if net_pct is None:
        return None

    turnover = _finite(reading.get("turnover")) or 0.0
    net = _finite(reading.get("net_individual"))

    return {
        "sessions": reading.get("sessions"),
        "turnover": turnover,
        "net_individual": net,
        # Named as the mirror, never recomputed from the institutional columns.
        # A caller that re-derived it would be entitled to believe it had found
        # independent confirmation of the number above it.
        "institutional_supply": None if net is None else -net,
        "net_pct": net_pct,
        "power_ratio": _finite(reading.get("power_ratio")),
        "persistence": _finite(reading.get("persistence")),
        "sessions_positive": reading.get("sessions_positive"),
        "into_individual_hands": net_pct > 0,
    }


def code_to_code(frame, volume_ratio=None, window=WINDOW,
                 baseline_window=BASELINE_WINDOW):
    """Concentration of the buying side: the کد به کد reading.

    `frame` is a client-type frame (database.load_client_type_tail), oldest
    first, holding at least `baseline_window` sessions. `volume_ratio` is the
    session's traded volume against its own 20-session average, used only for
    the genuineness test -- a transfer with no volume behind it is a quote, not
    a trade.

    Returns a dict whose `genuine` flag carries the risk rule "کد به کد ضعیف یا
    مصنوعی حذف شود". `reasons` says which test failed, because a scanner that
    silently drops a name teaches nobody anything.
    """

    if frame is None or len(frame) < MIN_BASELINE_SESSIONS + 1:
        return None

    recent = frame.tail(window)
    baseline = frame.iloc[:-window] if len(frame) > window else None

    if baseline is None or len(baseline) < MIN_BASELINE_SESSIONS:
        return None

    buy_value = _sum(recent, "individual_buy_value")
    sell_value = _sum(recent, "individual_sell_value")
    buy_count = _sum(recent, "individual_buy_count")
    sell_count = _sum(recent, "individual_sell_count")

    buy_volume = _sum(recent, "individual_buy_volume")
    inst_buy_volume = _sum(recent, "institutional_buy_volume")

    total_volume = buy_volume + inst_buy_volume

    if buy_value <= 0 or buy_count <= 0 or total_volume <= 0:
        return None

    ticket = buy_value / buy_count

    base_value = _sum(baseline, "individual_buy_value")
    base_count = _sum(baseline, "individual_buy_count")

    if base_value <= 0 or base_count <= 0:
        return None

    baseline_ticket = base_value / base_count

    if baseline_ticket <= 0:
        return None

    elevation = ticket / baseline_ticket

    # The excess share of the average ticket. Bounded [0, 1) by construction --
    # no clamp needed, and none applied, so a suspicious 0.99 stays visible as
    # a 99x ticket rather than being flattened into "1.0, like everything else
    # strong".
    large_share = max(0.0, 1.0 - (1.0 / elevation)) if elevation > 0 else 0.0

    volume_share = buy_volume / total_volume

    ratio = volume_share * large_share

    gross = buy_value + sell_value
    net_to_gross = abs(buy_value - sell_value) / gross if gross > 0 else 0.0

    reasons = []

    if buy_count < MIN_BUYERS:
        reasons.append(
            "میانگین تعداد خریدار حقیقی %.1f است — کمتر از آنکه «میانگین» "
            "معنا بدهد" % buy_count)

    if buy_value <= sell_value:
        reasons.append(
            "فروش حقیقی از خرید حقیقی بیشتر بوده — انتقالی به سمت حقیقی رخ "
            "نداده")

    if net_to_gross < MIN_NET_TO_GROSS:
        reasons.append(
            "خالص فقط %.0f%% از گردش حقیقی است — الگوی چرخش (wash) نه انتقال"
            % (net_to_gross * 100))

    if volume_ratio is not None and volume_ratio < 1.0:
        reasons.append(
            "حجم %.2f برابر میانگین ۲۰ روزه — انتقال بدون حجم پشتوانه ندارد"
            % volume_ratio)

    return {
        "sessions": int(len(recent)),
        "baseline_sessions": int(len(baseline)),
        "buyers": buy_count,
        "sellers": sell_count,
        # Fewer buyers than sellers is the shape of a کد به کد: stock leaving
        # many hands and arriving in few. Reported as the raw counts too, so
        # the reader is never asked to trust a derived ratio alone.
        "buyer_seller_ratio": (buy_count / sell_count) if sell_count > 0
                              else None,
        "ticket": ticket,
        "baseline_ticket": baseline_ticket,
        "elevation": elevation,
        "large_share": large_share,
        "volume_share": volume_share,
        "smart_money_ratio": ratio,
        "net_to_gross": net_to_gross,
        "genuine": not reasons,
        "reasons": reasons,
    }
