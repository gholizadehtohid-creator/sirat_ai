"""Survivorship bias: measure the part that is visible, name the part that is not.

THE PROBLEM, STATED EXACTLY
---------------------------
The universe is `instruments`, and `instruments` is the list of things listed
TODAY. A company that was delisted in 1403 is not in it, was never in it as far
as this database is concerned, and therefore never appears in any historical
window. It is absent precisely where its absence flatters the result: companies
leave this market after collapsing, not after doubling.

No amount of care inside the backtest engine fixes that. The fix is a
historical universe snapshot -- the list as it stood on each decision date --
and this project has never collected one.

WHAT CAN STILL BE MEASURED, AND WHY IT IS WORTH MEASURING
----------------------------------------------------------
Two effects of the same family ARE visible in the stored bars, and both are
real biases in their own right rather than proxies invented to have something
to report:

  STOPPED TRADING. An instrument still on today's list whose price series ends
  months before the window does. On this market that is a suspension, and a
  suspension is where a delisting starts. These names ARE in the universe, so
  the engine can hold them -- and what it cannot do is sell them, which is the
  honest half of the picture the queue model also describes.

  ENTERED MID-WINDOW. An instrument whose first bar falls after the window
  starts. It is in today's list and could not have been bought on day one. The
  engine already refuses it through MIN_HISTORY_BARS, so this is a check that
  the guard worked rather than a bias being corrected -- but the count belongs
  in the report, because a universe that grew 8% during the window is not the
  universe the headline figure implies.

The gap between an equal-weight market return computed over every instrument
and the same figure over only the instruments still trading at the end is a
LOWER BOUND on the survivorship effect. Lower, not an estimate: it prices only
the disappearances this database can see, and every company that left the list
entirely is priced at zero because it cannot be seen at all.

WHAT THIS MODULE WILL NOT DO
----------------------------
It will not produce a correction factor. A number that "adjusts for
survivorship" would be a guess wearing a decimal point, and applying it would
make an unmeasured quantity look measured. The report states the bound, states
the direction, and states that the full magnitude is unknown.
"""

import math

import pandas as pd


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# Sessions an instrument may be silent at the end of the window before it is
# treated as having stopped trading. A name can miss a fortnight for reasons
# that resolve; three months is a suspension.
STOPPED_TOLERANCE_SESSIONS = 60


def measure(data, start, end, cfg=None, trades=None,
            tolerance=STOPPED_TOLERANCE_SESSIONS):
    """Everything this database can say about its own survivorship problem."""

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)

    calendar = data.calendar()

    window = calendar[(calendar >= start) & (calendar <= end)]

    if len(window) < 2:
        return {"available": False,
                "reason": "fewer than two sessions in the window"}

    # The session an instrument must have traded on or after to count as still
    # trading at the end.
    cutoff_index = max(0, len(window) - 1 - int(tolerance))
    cutoff = window[cutoff_index]

    instruments = data.instruments()

    codes = sorted(instruments)

    if cfg is not None and cfg.universe_limit:
        codes = codes[:int(cfg.universe_limit)]

    stopped = []
    entered = []
    full = []

    returns_all = []
    returns_survivors = []

    for code in codes:

        bars = data.bars(code)

        if bars is None or bars.empty:
            continue

        first = pd.Timestamp(bars["date"].iloc[0])
        last = pd.Timestamp(bars["date"].iloc[-1])

        entry_index = data.position(code, start)
        exit_index = data.position(code, end)

        if first > start:
            entered.append({
                "ins_code": code,
                "symbol": instruments[code].get("symbol"),
                "first_bar": str(first.date()),
            })

        # The return this instrument delivered across the window, from the
        # first price available at or after the start to the last one at or
        # before the end. A name that stopped trading is carried to its final
        # print, which is what a holder would actually have experienced.
        window_return = None

        if entry_index >= 0 and exit_index > entry_index:
            entry_price = _finite(bars["close"].iloc[entry_index])
            exit_price = _finite(bars["close"].iloc[exit_index])
            if entry_price and entry_price > 0 and exit_price:
                window_return = (exit_price / entry_price - 1) * 100

        has_stopped = last < cutoff

        if has_stopped:
            stopped.append({
                "ins_code": code,
                "symbol": instruments[code].get("symbol"),
                "sector": instruments[code].get("sector"),
                "last_bar": str(last.date()),
                "silent_sessions": int(
                    len(calendar[(calendar > last) & (calendar <= end)])),
                "window_return_pct": (round(window_return, 2)
                                      if window_return is not None else None),
            })
        else:
            full.append(code)

        if window_return is not None:
            returns_all.append(window_return)
            if not has_stopped:
                returns_survivors.append(window_return)

    mean_all = (sum(returns_all) / len(returns_all)) if returns_all else None
    mean_survivors = ((sum(returns_survivors) / len(returns_survivors))
                      if returns_survivors else None)

    bound = (round(mean_survivors - mean_all, 2)
             if mean_all is not None and mean_survivors is not None else None)

    # ---- what the strategy itself touched --------------------------------
    stopped_codes = {row["ins_code"] for row in stopped}

    traded_stopped = None

    if trades is not None:
        touched = [t for t in trades if str(t.get("ins_code")) in stopped_codes]
        traded_stopped = {
            "trades": len(touched),
            "of_trades": len(trades),
            "share": (round(len(touched) / float(len(trades)), 4)
                      if trades else None),
            "net_pnl": round(sum(float(t.get("pnl") or 0) for t in touched), 0),
        }

    return {
        "available": True,
        "universe_today": len(codes),
        "window": [str(start.date()), str(end.date())],
        "tolerance_sessions": int(tolerance),
        "cutoff_session": str(pd.Timestamp(cutoff).date()),

        "stopped_trading": len(stopped),
        "stopped_share": round(len(stopped) / float(len(codes)), 4) if codes else None,
        "stopped_detail": sorted(
            stopped, key=lambda r: (r["window_return_pct"] is None,
                                    r["window_return_pct"] or 0))[:25],

        "entered_mid_window": len(entered),
        "entered_share": round(len(entered) / float(len(codes)), 4) if codes else None,
        "entered_detail": entered[:25],

        "mean_return_all_pct": round(mean_all, 2) if mean_all is not None else None,
        "mean_return_survivors_pct": (round(mean_survivors, 2)
                                      if mean_survivors is not None else None),
        "visible_bias_lower_bound_pp": bound,

        "strategy_exposure_to_stopped": traded_stopped,

        # Written out in full rather than left to a footnote, because the
        # single most important thing about this measurement is its ceiling.
        "unmeasurable": (
            "companies DELISTED before today are absent from the instruments "
            "table entirely, so they cannot be counted, priced, or bounded "
            "from this database. Correcting for them needs a historical "
            "universe snapshot per decision date, which this project has never "
            "collected. Every figure in this report is therefore optimistic by "
            "an unknown margin, and the bound above prices only the "
            "disappearances that are still visible."
        ),
        "verdict": _verdict(len(stopped), len(codes), bound),
    }


def _verdict(stopped, universe, bound):
    """A sentence a reader can act on, never a correction factor."""

    if not universe:
        return "no universe to measure"

    share = stopped / float(universe) * 100

    parts = ["%d of %d instruments (%.1f%%) stopped trading before the window "
             "ended" % (stopped, universe, share)]

    if bound is not None:
        parts.append(
            "excluding them raises the equal-weight market return by %.2f "
            "points, which is a LOWER BOUND on the survivorship effect -- the "
            "companies that left the listing entirely are not in this figure "
            "because they are not in this database" % bound
        )

    parts.append(
        "survivorship bias is NOT eliminated and this project must not claim "
        "it is"
    )

    return "; ".join(parts)
