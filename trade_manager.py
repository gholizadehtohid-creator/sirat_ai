"""One signal, fully specified: entry, stop, two targets, reward/risk, duration.

WHAT A SIGNAL HAS TO CONTAIN BEFORE IT IS ACTIONABLE
---------------------------------------------------
`portfolio.py` already produces most of these fields for the ten names it
selects. This module produces them for ONE candidate, standalone, and adds the
three the specification asks for that no existing path emits:

    a second target      where the remainder is scaled out
    a reward/risk ratio  computed from the target that survived, not assumed
    a holding period     how long the move is expected to take

THE THIRD TARGET RULE IS THE ONE THAT CHANGES DECISIONS
-------------------------------------------------------
Every exit in this project is a multiple of ATR, which knows nothing about
whether a three-month high sits half a percent above the computed target. On an
exchange with a ±3% band that matters more than elsewhere: supply parked at a
remembered level is not absorbed in one session, it caps the move for days. So
when `levels.overhead()` reports a wall inside the computed target, the target is
pulled back under it -- AND THE REWARD/RISK IS RECOMPUTED. That recomputation is
the point. A trade whose target is capped at 1.4x its stop distance is no longer
the 2.0x trade the sizing assumed, and if it falls below MIN_RISK_REWARD the
action degrades from BUY to AVOID. A signal engine that reports a capped target
beside its original, uncapped ratio is stating something false.

THE HOLDING PERIOD IS AN ESTIMATE OF DURATION, NEVER A PROMISE
--------------------------------------------------------------
It is derived, not forecast: distance to target, divided by the net directional
progress the share has actually been making per session. Price does not travel
its whole ATR in one direction every day, so the ATR is discounted by
HOLD_ATR_EFFICIENCY, and the result is clamped to the swing horizon. It answers
"is this a two-week trade or a two-month trade" -- which is what decides whether
the position belongs in a swing book at all -- and it says nothing about whether
the target will be reached.

NOTHING HERE PLACES AN ORDER. Every function returns a dict.
"""

import math

import config
import factor_engine


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# TARGETS
# ==============================

def targets(entry, stop_distance, overhead_pct=None, rr1=None, rr2=None,
            tolerance=None):
    """Two targets from the stop distance, pulled under overhead supply.

    Returns a dict with both prices, both reward/risk ratios, and whether either
    was capped. `overhead_pct` is levels.overhead()['nearest_pct'] -- the
    distance to the closest remembered ceiling, in percent of price.

    A ceiling is only allowed to CUT a target, never to extend one. Letting a
    distant ceiling raise a target would turn "there is nothing in the way" into
    a reason to want more, which is not what the level says.
    """

    entry = _finite(entry)
    stop_distance = _finite(stop_distance)

    if not entry or entry <= 0 or not stop_distance or stop_distance <= 0:
        # Every key the success path returns, so a caller reading the result
        # never has to know which branch produced it.
        return {
            "target_1": None, "target_2": None,
            "target_1_pct": None, "target_2_pct": None,
            "rr_1": None, "rr_2": None,
            "capped": False, "cap_source": None,
            "overhead_pct": (None if _finite(overhead_pct) is None
                             else round(_finite(overhead_pct), 2)),
            "notes": ["no usable entry or stop distance"],
        }

    rr1 = _finite(rr1) if rr1 is not None else config.TARGET_1_RR
    rr2 = _finite(rr2) if rr2 is not None else config.TARGET_2_RR

    tolerance = (_finite(tolerance) if tolerance is not None
                 else config.TARGET_CEILING_TOLERANCE)

    notes = []

    first = entry + stop_distance * rr1
    second = entry + stop_distance * rr2

    capped = False
    cap_source = None

    room = _finite(overhead_pct)

    if room is not None and room > 0:

        ceiling = entry * (1.0 + room / 100.0)

        # Sit just under the wall rather than on it. A limit order at the exact
        # level queues behind every order already parked there.
        just_under = ceiling * (1.0 - 0.005)

        if just_under < first:
            # The wall is inside the first target. Only respect it when it is
            # not so close that the trade collapses to nothing -- a ceiling
            # within a tolerance-fraction of the stop distance means there is no
            # trade here, and the reward/risk that comes out of this will say so.
            if (just_under - entry) >= stop_distance * tolerance:
                first = just_under
                capped = True
                cap_source = "overhead"
                notes.append(
                    "هدف اول به زیر مقاومت %.1f%% بالاتر از قیمت کشیده شد" % room)
            else:
                # A wall this close leaves nothing. Floored at the entry rather
                # than allowed below it: `just_under` can land under the entry
                # when the ceiling is within half a percent, and a "target"
                # below the entry price is not a small target, it is nonsense in
                # a field a reader will act on. Zero reward gives a zero ratio,
                # which fails MIN_RISK_REWARD -- the correct outcome, reached
                # with a printable number.
                first = max(entry, just_under)
                capped = True
                cap_source = "overhead_tight"
                notes.append(
                    "مقاومت فقط %.1f%% بالاتر از قیمت است — فضایی برای هدف نمانده"
                    % room)

        if second <= first:
            # A second target at or below the first is not a second target.
            second = first + stop_distance * max(0.5, rr2 - rr1)
            notes.append("هدف دوم بر پایه‌ی فاصله‌ی حد ضرر از هدف اول محاسبه شد")

    reward_1 = first - entry
    reward_2 = second - entry

    return {
        "target_1": round(first, 2),
        "target_2": round(second, 2),
        "target_1_pct": round(reward_1 / entry * 100.0, 2),
        "target_2_pct": round(reward_2 / entry * 100.0, 2),
        "rr_1": round(reward_1 / stop_distance, 2),
        "rr_2": round(reward_2 / stop_distance, 2),
        "capped": capped,
        "cap_source": cap_source,
        "overhead_pct": None if room is None else round(room, 2),
        "notes": notes,
    }


# ==============================
# DURATION
# ==============================

def holding_period(target_pct, atr_pct=None, efficiency=None):
    """Sessions the move to target would take at the observed daily range.

    Returns (sessions, note). None when there is no ATR to divide by: a duration
    estimate built on an assumed volatility would be a guess wearing a number,
    and the caller can report "unknown" honestly instead.

    The clamp to HOLD_MIN/MAX_SESSIONS is not cosmetic. Below the floor the
    arithmetic is claiming a target three sessions away, which the ±3% band makes
    almost impossible for any target worth 2x a stop; above the ceiling the trade
    is not a swing trade, and saying so is more useful than reporting 94 sessions.
    """

    target_pct = _finite(target_pct)
    atr_pct = _finite(atr_pct)

    if target_pct is None or target_pct <= 0:
        return None, "no target distance to travel"

    if atr_pct is None or atr_pct <= 0:
        return None, "no ATR — expected duration cannot be estimated"

    efficiency = (_finite(efficiency) if efficiency is not None
                  else config.HOLD_ATR_EFFICIENCY)

    if not efficiency or efficiency <= 0:
        return None, "ATR efficiency is not positive"

    progress = atr_pct * efficiency

    raw = target_pct / progress

    floor = int(config.HOLD_MIN_SESSIONS)
    ceiling = int(config.HOLD_MAX_SESSIONS)

    sessions = int(round(raw))

    if sessions < floor:
        return floor, ("حرکت تا هدف حدود %.1f سشن برآورد شد — به کف %d سشن "
                       "رسانده شد" % (raw, floor))

    if sessions > ceiling:
        return ceiling, ("حرکت تا هدف حدود %.0f سشن برآورد شد — بیش از افق "
                         "سوئینگ (%d سشن)" % (raw, ceiling))

    return sessions, ("حرکت %.1f%% تا هدف با پیشروی روزانه‌ی %.2f%% — حدود %d سشن"
                      % (target_pct, progress, sessions))


# ==============================
# THE SIGNAL
# ==============================

def plan(reading, decision=None, risk=None, overhead_pct=None,
         min_risk_reward=None, held=False):
    """One fully specified signal. The dict a user or an order router can act on.

    `reading` carries the symbol and the bar: `symbol`, `close`, `atr_pct`,
    `sector`. `decision` is factor_engine.decide()'s output and `risk` is
    risk_engine.assess()'s -- both optional, and when absent the plan reports
    levels without an action rather than inventing one.

    THE ACTION CAN BE DOWNGRADED HERE, and only downgraded. A BUY whose target
    had to be pulled under a nearby ceiling can fail MIN_RISK_REWARD, and a BUY
    the risk engine will not size is not a BUY. Nothing in this function can turn
    an AVOID into a BUY -- the direction is one-way by construction, so a bug
    here cannot manufacture a position.
    """

    reading = reading or {}
    risk = risk or {}

    min_risk_reward = (_finite(min_risk_reward) if min_risk_reward is not None
                       else config.MIN_RISK_REWARD)

    entry = _finite(reading.get("close"))

    action = (decision or {}).get("action")

    notes = []
    downgrades = []

    distance = _finite(risk.get("stop_distance"))
    stop_price = _finite(risk.get("stop_price"))

    if distance is None and entry:
        # No risk assessment supplied: derive the stop from the same helper the
        # risk engine uses, so a plan built without one is not built on a
        # different stop rule.
        import risk_engine
        distance, source, stop_notes = risk_engine.stop_distance(
            entry, atr=reading.get("atr"))
        stop_price = entry - distance if (entry and distance) else None
        notes.extend(stop_notes)
        notes.append("حد ضرر مستقیماً محاسبه شد (بدون risk_engine.assess)")

    level = targets(entry, distance, overhead_pct=overhead_pct)

    notes.extend(level["notes"])

    sessions, duration_note = holding_period(
        level["target_1_pct"], atr_pct=reading.get("atr_pct"))

    notes.append(duration_note)

    size = risk.get("size") or {}

    # ---- downgrades -------------------------------------------------------

    if action == factor_engine.BUY:

        rr = _finite(level.get("rr_1"))

        # A hair of tolerance, because rr_1 is a rounded ratio of two floats and
        # an uncapped target computed AT the minimum would otherwise reject or
        # accept depending on the last bit of a division. The tolerance is
        # smaller than the rounding, so it cannot admit a genuinely worse trade.
        if rr is not None and rr < min_risk_reward - 0.005:
            downgrades.append(
                "نسبت سود به زیان %.2f زیر حد %.2f%s"
                % (rr, min_risk_reward,
                   " (هدف زیر مقاومت محدود شد)" if level["capped"] else ""))
            action = factor_engine.AVOID

        elif risk and not risk.get("tradable", True):
            downgrades.append("موتور ریسک این موقعیت را قابل باز کردن نمی‌داند: %s"
                              % "؛ ".join(risk.get("blockers") or ["-"]))
            action = factor_engine.AVOID

        elif size and size.get("shares", 0) <= 0:
            downgrades.append("حجم قابل خرید صفر است: %s"
                              % (size.get("reason") or "-"))
            action = factor_engine.AVOID

        elif sessions is not None and sessions >= config.HOLD_MAX_SESSIONS:
            # Not an AVOID: a slow trade is still a trade. But it is not a swing
            # trade, and the caller ranking a swing book should see that.
            notes.append("افق این معامله بلندتر از افق سوئینگ است")

    return {
        "symbol": reading.get("symbol"),
        "ins_code": reading.get("ins_code"),
        "name": reading.get("name"),
        "sector": reading.get("sector") or reading.get("sector_name"),

        "action": action,
        "action_label": factor_engine.ACTION_LABELS.get(action),
        "held": bool(held),
        "swing_score": (decision or {}).get("score"),
        "conviction": (decision or {}).get("conviction"),

        "entry_price": None if entry is None else round(entry, 2),
        # A band rather than a single price. The ±3% limit means the open can
        # gap past a single quoted level, and a signal that can only be taken at
        # exactly one price is a signal that mostly does not get taken.
        "entry_low": None if entry is None else round(entry * 0.99, 2),
        "entry_high": None if entry is None else round(entry * 1.01, 2),

        "stop_loss": None if stop_price is None else round(stop_price, 2),
        "stop_distance_pct": (round(distance / entry * 100.0, 2)
                              if (distance and entry) else None),
        "stop_source": risk.get("stop_source"),

        "target_1": level["target_1"],
        "target_2": level["target_2"],
        "target_1_pct": level["target_1_pct"],
        "target_2_pct": level["target_2_pct"],
        "target_capped": level["capped"],
        "target_cap_source": level["cap_source"],
        "overhead_pct": level["overhead_pct"],

        "risk_reward": level["rr_1"],
        "risk_reward_target_2": level["rr_2"],

        "expected_holding_sessions": sessions,
        "expected_holding_note": duration_note,

        "shares": size.get("shares"),
        "position_value": size.get("position_value"),
        "weight_pct": size.get("weight_pct"),
        "risk_rials": size.get("risk_rials"),
        "risk_pct_of_equity": size.get("risk_pct_of_equity"),
        "bound_by": size.get("bound_by"),
        "risk_score": risk.get("risk_score"),

        "reasons": list((decision or {}).get("reasons") or []),
        "downgrades": downgrades,
        "notes": notes,
    }


def describe(signal):
    """The signal as lines of text, for a terminal report or a log."""

    if not signal:
        return ["no signal"]

    def number(value, suffix=""):
        return "-" if value is None else "%s%s" % (value, suffix)

    lines = [
        "%-12s %s" % (signal.get("symbol") or "?",
                      signal.get("action_label") or signal.get("action") or "?"),
    ]

    if signal.get("swing_score") is not None:
        lines.append("  امتیاز سوئینگ    %s" % signal["swing_score"])

    lines.extend([
        "  قیمت ورود        %s  (%s .. %s)"
        % (number(signal.get("entry_price")), number(signal.get("entry_low")),
           number(signal.get("entry_high"))),
        "  حد ضرر           %s  (%s)"
        % (number(signal.get("stop_loss")),
           number(signal.get("stop_distance_pct"), "%")),
        "  هدف اول          %s  (%s)%s"
        % (number(signal.get("target_1")),
           number(signal.get("target_1_pct"), "%"),
           "  ← محدود شده به مقاومت" if signal.get("target_capped") else ""),
        "  هدف دوم          %s  (%s)"
        % (number(signal.get("target_2")),
           number(signal.get("target_2_pct"), "%")),
        "  سود به زیان      %s   (هدف دوم %s)"
        % (number(signal.get("risk_reward")),
           number(signal.get("risk_reward_target_2"))),
        "  افق نگهداری      %s سشن" % number(signal.get("expected_holding_sessions")),
    ])

    if signal.get("shares"):
        lines.append("  حجم              %s سهم  (%s%% سرمایه، ریسک %s%%)"
                     % (signal["shares"], number(signal.get("weight_pct")),
                        number(signal.get("risk_pct_of_equity"))))
        if signal.get("bound_by"):
            lines.append("  محدودشده توسط    %s" % signal["bound_by"])

    if signal.get("risk_score") is not None:
        lines.append("  امتیاز ریسک      %s" % signal["risk_score"])

    for reason in signal.get("reasons") or []:
        lines.append("  • %s" % reason)

    for downgrade in signal.get("downgrades") or []:
        lines.append("  ! %s" % downgrade)

    return lines
