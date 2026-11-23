"""Per-trade risk: how risky is this position, and therefore how large may it be.

WHERE THIS SITS BETWEEN risk.py AND portfolio.py
------------------------------------------------
The project already has two risk layers and this is a third, distinct question:

    scoring.risk_score()   one symbol's volatility/ATR/liquidity as a 0-100
    risk.py                what an EXISTING basket is exposed to -- correlation,
                           VaR, beta, concentration. Only answerable on a set.
    portfolio.py           sizes a whole basket at once, against one equity
    risk_engine.py         ONE candidate: is it safe enough to open, and at what
                           size, given what is already held

The gap it fills is the standalone signal. `portfolio.build()` sizes ten names
together and cannot answer "may I open this one name now, and how much", which
is what a swing signal needs and what `trade_manager.py` calls. The sizing
sequence here is deliberately the same one portfolio.py documents -- what the
position wants, then every cap in turn, then whole shares -- and every cap reads
the SAME config constant, so the two cannot produce contradictory limits.

THE THREE RISKS THAT VOLATILITY DOES NOT CONTAIN
------------------------------------------------
`scoring.risk_score()` reads volatility, ATR and average turnover, and is reused
here rather than rewritten. Three exposures sit outside it:

  EXIT TIME. A position worth three days of a name's entire turnover cannot be
  sold in a day at the modelled price. Volatility says nothing about this and
  the annualised figure of an illiquid name is usually LOW, because it barely
  trades. RISK_MAX_EXIT_SESSIONS is the cap.

  STOP GEOMETRY. A stop closer to the entry than the daily price band will be
  hit by noise alone; one far wider than the band makes the risk-sized position
  too small to be worth a round trip. Both are risk facts about the trade rather
  than about the share, so neither can come from a volatility reading.

  A PENDING EVENT. An undirected material disclosure raises the size of the next
  move without saying which way. factor_engine deliberately refuses to score it
  as a catalyst; here it reduces SIZE, which is the honest response to "something
  large is about to happen and we do not know what".

WHY THE ±3% BAND MAKES EVERY NUMBER HERE A FLOOR
------------------------------------------------
A share that "should" fall 12% falls 3% for four sessions. Volatility computed
from daily closes therefore misses the tail -- exactly the part risk sizing
exists to survive -- so every figure this module produces understates the real
exposure. That is stated in the output rather than in a comment, because a risk
number a reader believes is a ceiling is more dangerous than no number.
"""

import math

import config
import scoring


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# Reasons a position ended up smaller than the risk budget wanted. Returned by
# name so a report can say WHY a position is small instead of only how small.
BOUND_RISK = "risk_budget"
BOUND_POSITION_CAP = "max_position_pct"
BOUND_LIQUIDITY = "adv_participation"
BOUND_SECTOR = "sector_budget"
BOUND_REGIME = "regime_budget"
BOUND_EQUITY = "available_cash"
BOUND_TOO_SMALL = "below_min_position"

CAVEAT = (
    "دامنه‌ی نوسان ±%.0f%% باعث می‌شود نوسان محاسبه‌شده از بسته‌های روزانه، دنباله‌ی "
    "توزیع را کم‌تر از واقع نشان دهد. اعداد ریسک این ماژول کف ریسک واقعی‌اند، نه سقف آن."
    % config.PRICE_BAND_PCT
)


# ==============================
# STOP GEOMETRY
# ==============================

def stop_distance(price, atr=None, atr_multiple=None, band_pct=None):
    """Rials between entry and stop, with the sanity band applied.

    Returns (distance, source, notes). `source` names where the number came
    from, which matters because an ATR-derived stop and a band-derived fallback
    are different degrees of confidence and a single number hides that.
    """

    price = _finite(price)

    notes = []

    if not price or price <= 0:
        return None, None, ["no usable price"]

    multiple = _finite(atr_multiple)

    if multiple is None:
        multiple = getattr(config, "V2_STOP_ATR_MULTIPLE",
                           config.STOP_ATR_MULTIPLE)

    atr = _finite(atr)

    if atr is not None and atr > 0 and multiple:
        distance = atr * multiple
        source = "atr"
    else:
        # Two full daily bands: the widest the exchange permits over two
        # sessions. Wider than a real stop, which errs toward a SMALLER
        # position -- the same fallback backtesting/sizing.py uses, and the same
        # direction of error.
        band = _finite(band_pct) or config.PRICE_BAND_PCT
        distance = price * (band * 2 / 100.0)
        source = "price_band"
        notes.append("ATR در دسترس نبود — حد ضرر از دو دامنه‌ی نوسان محاسبه شد")

    pct = distance / price * 100.0

    floor = (_finite(getattr(config, "V2_MIN_STOP_PCT", None))
             or _finite(config.RISK_MIN_STOP_PCT) or config.PRICE_BAND_PCT)
    ceiling = _finite(config.RISK_MAX_STOP_PCT) or 18.0

    if pct < floor:
        distance = price * (floor / 100.0)
        notes.append("حد ضرر %.1f%% از کف %.1f%% نزدیک‌تر بود — به کف رسانده شد"
                     % (pct, floor))
        source = source + "+floor"
    elif pct > ceiling:
        distance = price * (ceiling / 100.0)
        notes.append("حد ضرر %.1f%% از سقف %.1f%% دورتر بود — به سقف محدود شد"
                     % (pct, ceiling))
        source = source + "+ceiling"

    return distance, source, notes


def _score_stop(stop_pct):
    """A stop distance -> a 0-1 fraction. Mid-band is best, both tails are worse."""

    if stop_pct is None:
        return None, "stop distance unavailable"

    band = config.PRICE_BAND_PCT

    if stop_pct < band:
        return 0.2, ("حد ضرر %.1f%% داخل دامنه‌ی روزانه — با نوسان معمولی فعال می‌شود"
                     % stop_pct)

    if stop_pct <= band * 2:
        return 1.0, "حد ضرر %.1f%% — متناسب با دامنه" % stop_pct

    if stop_pct <= band * 3:
        return 0.7, "حد ضرر %.1f%% — کمی پهن" % stop_pct

    if stop_pct <= config.RISK_MAX_STOP_PCT:
        return 0.4, "حد ضرر %.1f%% — پهن، موقعیت کوچک می‌شود" % stop_pct

    return 0.15, "حد ضرر %.1f%% — بیش از حد پهن" % stop_pct


def _score_exit_time(sessions):
    """Sessions of average turnover the position represents -> 0-1."""

    if sessions is None:
        return None, "exit time unavailable (needs turnover and a size)"

    limit = _finite(config.RISK_MAX_EXIT_SESSIONS) or 3.0

    if sessions <= limit / 3.0:
        return 1.0, "خروج در کمتر از %.1f سشن" % (limit / 3.0)

    if sessions <= limit:
        return 0.65, "خروج در حدود %.1f سشن" % sessions

    if sessions <= limit * 2:
        return 0.3, "خروج حدود %.1f سشن — بالاتر از سقف %.1f" % (sessions, limit)

    return 0.05, "خروج حدود %.1f سشن — عملاً غیرقابل خروج" % sessions


# ==============================
# THE RISK SCORE
# ==============================

def risk_score(metrics=None, stop_pct=None, exit_sessions=None,
               event_count=0, sector_share_pct=None):
    """0-100 where HIGHER IS SAFER, over whichever inputs exist.

    The orientation is inherited from scoring.risk_score() and is not negotiable:
    a component meaning "more risk when higher", blended into a score where
    everything else means "better when higher", produces a plausible ranked list
    with a sign error in it -- the worst kind of wrong, because nothing about the
    output looks wrong.
    """

    base = scoring.risk_score(metrics or {})

    parts = []
    notes = list(base.get("notes") or [])

    if base.get("score") is not None:
        # The existing reading counts double. It is the measured part -- three
        # inputs of its own, calibrated against this market -- while the
        # additions below are geometry and structure.
        parts.extend([base["score"] / 100.0, base["score"] / 100.0])

    fraction, note = _score_stop(_finite(stop_pct))

    if fraction is not None:
        parts.append(fraction)
    notes.append(note)

    fraction, note = _score_exit_time(_finite(exit_sessions))

    if fraction is not None:
        parts.append(fraction)
    notes.append(note)

    share = _finite(sector_share_pct)

    if share is not None:
        ceiling = _finite(config.MAX_SECTOR_PCT) or 30.0
        if share >= ceiling:
            parts.append(0.1)
            notes.append("صنعت از قبل %.1f%% سبد است — سقف %.1f%%"
                         % (share, ceiling))
        elif share >= ceiling * 0.66:
            parts.append(0.5)
            notes.append("صنعت %.1f%% سبد است — به سقف نزدیک می‌شود" % share)
        else:
            parts.append(1.0)
            notes.append("تمرکز صنعتی %.1f%% — در محدوده" % share)

    events = int(event_count or 0)

    if events:
        # A pending undirected disclosure. Not a veto -- material filings are
        # routine -- but it does reduce the size, and it belongs in the risk
        # reading rather than in the catalyst factor, which cannot know the
        # direction. See the note in factor_engine.catalyst().
        parts.append(max(0.2, 1.0 - 0.25 * events))
        notes.append("%d افشای بااهمیت در جریان — جهت نامعلوم، حجم کاهش می‌یابد"
                     % events)

    if not parts:
        return {
            "score": None,
            "notes": notes + ["no risk input available"],
            "caveat": CAVEAT,
        }

    return {
        "score": int(round(sum(parts) / len(parts) * 100)),
        "notes": notes,
        "caveat": CAVEAT,
    }


# ==============================
# SECTOR ROOM
# ==============================

def sector_capacity(sector, positions=None, equity=None,
                    max_sector_pct=None, max_per_sector=None):
    """How much more of one sector the book can take. Rials and names.

    A sector cap is a constraint rather than a preference: ten names picked on
    score alone routinely land eight in one industry, and eight petrochemicals
    is one position wearing eight names. Both limits are returned because they
    bind differently -- the count limit stops a ninth name, the value limit stops
    a large ninth name.
    """

    equity = _finite(equity) or 0.0

    max_sector_pct = (_finite(max_sector_pct)
                      if max_sector_pct is not None else config.MAX_SECTOR_PCT)
    max_per_sector = (int(max_per_sector) if max_per_sector is not None
                      else config.MAX_PER_SECTOR)

    key = (sector or "?").strip() or "?"

    held_value = 0.0
    held_count = 0

    for position in positions or []:

        name = (position.get("sector_name") or position.get("sector") or "?")
        name = str(name).strip() or "?"

        if name != key:
            continue

        held_count += 1
        held_value += _finite(position.get("position_value")) or 0.0

    share = (held_value / equity * 100.0) if equity else None

    ceiling_value = equity * (max_sector_pct / 100.0) if equity else 0.0

    return {
        "sector": key,
        "held_count": held_count,
        "held_value": held_value,
        "share_pct": None if share is None else round(share, 2),
        "max_per_sector": max_per_sector,
        "max_sector_pct": max_sector_pct,
        "names_left": max(0, max_per_sector - held_count),
        "value_left": max(0.0, ceiling_value - held_value),
        "full": held_count >= max_per_sector,
    }


# ==============================
# POSITION SIZE
# ==============================

def position_size(price, distance, equity, risk_pct=None, adv=None,
                  max_position_pct=None, min_position_pct=None,
                  sector_room=None, cash_available=None):
    """The rials and whole shares this trade may have, and what bound it.

    The sequence is fixed and each step can only ever REDUCE the previous one:

        1. what the risk budget wants: risking risk_pct of equity to the stop
        2. the per-position ceiling
        3. the turnover-participation ceiling
        4. whatever room the sector has left
        5. whatever cash is actually free
        6. the minimum-size floor -- below it, no position at all
        7. round DOWN to whole shares

    Step 6 rejects rather than rounds up. A position under MIN_POSITION_PCT is
    not a small position; it is noise that still costs a round trip in
    commission and a line of attention.
    """

    price = _finite(price)
    distance = _finite(distance)
    equity = _finite(equity)

    notes = []

    if not price or price <= 0 or not equity or equity <= 0:
        return _no_position("no usable price or equity", notes)

    risk_pct = (_finite(risk_pct) if risk_pct is not None
                else config.RISK_PER_TRADE_PCT)

    max_position_pct = (_finite(max_position_pct)
                        if max_position_pct is not None
                        else config.MAX_POSITION_PCT)
    min_position_pct = (_finite(min_position_pct)
                        if min_position_pct is not None
                        else config.MIN_POSITION_PCT)

    risk_budget = equity * (risk_pct / 100.0)

    if not distance or distance <= 0:
        # No stop means nothing to divide the risk budget by. Falling back to
        # the position cap is stated rather than silent: it turns risk-based
        # sizing into fixed-fractional sizing for this name.
        wanted = equity * (max_position_pct / 100.0)
        bound = BOUND_POSITION_CAP
        notes.append("حد ضرر مشخص نیست — اندازه بر پایه‌ی سقف موقعیت تعیین شد")
    else:
        wanted = (risk_budget / distance) * price
        bound = BOUND_RISK

    ceiling = equity * (max_position_pct / 100.0)

    if wanted > ceiling:
        wanted = ceiling
        bound = BOUND_POSITION_CAP

    adv = _finite(adv)

    if adv is not None and adv <= 0:
        # Nothing traded in the window. This used to skip the liquidity cap,
        # so the one name that cannot be traded at all was the one name sized
        # without any participation limit.
        return _no_position("در دوره‌ی اخیر معامله‌ای نداشته — حجم قابل خرید صفر",
                            notes, bound=BOUND_LIQUIDITY)

    if adv is not None and adv > 0:
        participation = adv * (config.MAX_ADV_PARTICIPATION_PCT / 100.0)
        if wanted > participation:
            wanted = participation
            bound = BOUND_LIQUIDITY

    if sector_room is not None:
        room = _finite((sector_room or {}).get("value_left"))
        if (sector_room or {}).get("full"):
            return _no_position(
                "سقف تعداد نماد در صنعت %s پر است" % sector_room.get("sector"),
                notes, bound=BOUND_SECTOR)
        if room is not None and wanted > room:
            wanted = room
            bound = BOUND_SECTOR

    cash = _finite(cash_available)

    if cash is not None and wanted > cash:
        wanted = cash
        bound = BOUND_EQUITY

    floor = equity * (min_position_pct / 100.0)

    if wanted < floor:
        return _no_position(
            "اندازه‌ی مجاز %.2f%% سرمایه — زیر کف %.2f%%"
            % (wanted / equity * 100.0, min_position_pct),
            notes, bound=BOUND_TOO_SMALL)

    shares = int(math.floor(wanted / price))

    value = shares * price

    if shares <= 0 or value < floor:
        return _no_position(
            "پس از گرد کردن به سهم صحیح، موقعیت زیر کف می‌افتد", notes,
            bound=BOUND_TOO_SMALL)

    risk_rials = shares * distance if distance else None

    return {
        "shares": shares,
        "position_value": round(value, 0),
        "weight_pct": round(value / equity * 100.0, 2),
        "risk_rials": None if risk_rials is None else round(risk_rials, 0),
        "risk_pct_of_equity": (None if risk_rials is None
                               else round(risk_rials / equity * 100.0, 3)),
        "bound_by": bound,
        "adv_participation_pct": (round(value / adv * 100.0, 2)
                                  if adv and adv > 0 else None),
        "exit_sessions": (round(value / adv, 2) if adv and adv > 0 else None),
        "notes": notes,
        "reason": None,
    }


def risk_with_costs(risk_rials, position_value, cost_pct=None):
    """What a stopped-out position costs once the round trip is paid, in rials.

    position_size() above reports `risk_rials = shares x distance`: the loss
    if the stop fills at its own price and trading were free. A stopped trade
    still pays to get in and to get out -- at today's 1.2512% a 9% position
    adds 0.11% of equity to a stated 0.39-0.70% (the basket of 2026-11-23).
    `cost_pct` defaults to config.LIVE_ROUND_TRIP_PCT.

    For the live cards only. position_size() is unchanged: the registered
    backtests size through it, and charge their own costs at the fill.
    Still a floor -- a stop inside a sell queue fills lower, or not that day.
    """

    risk_rials = _finite(risk_rials)
    position_value = _finite(position_value)
    cost = _finite(cost_pct)

    if cost is None:
        cost = _finite(getattr(config, "LIVE_ROUND_TRIP_PCT", None))

    if risk_rials is None or position_value is None or cost is None:
        return None

    return round(risk_rials + position_value * cost / 100.0, 0)


def _no_position(reason, notes, bound=None):
    return {
        "shares": 0,
        "position_value": 0.0,
        "weight_pct": 0.0,
        "risk_rials": None,
        "risk_pct_of_equity": None,
        "bound_by": bound,
        "adv_participation_pct": None,
        "exit_sessions": None,
        "notes": list(notes) + [reason],
        "reason": reason,
    }


# ==============================
# PUBLIC
# ==============================

def assess(reading, equity=None, positions=None, regime=None, risk_pct=None,
           event_count=0, cash_available=None, atr_multiple=None):
    """One candidate's full risk picture: score, stop, size and every cap hit.

    `reading` needs `close`; everything else it can supply -- `atr`, `atr_pct`,
    `adv`, `volatility_60`, `sector` -- sharpens the answer and its absence is
    reported rather than assumed. `regime` is a regime_engine.assess() result,
    used to shrink the risk budget in a market that does not deserve full size.
    """

    reading = reading or {}

    equity = _finite(equity) or config.ACCOUNT_EQUITY

    price = _finite(reading.get("close"))

    distance, stop_source, stop_notes = stop_distance(
        price, atr=reading.get("atr"), atr_multiple=atr_multiple)

    stop_pct = (distance / price * 100.0) if (distance and price) else None

    room = sector_capacity(
        reading.get("sector") or reading.get("sector_name"),
        positions=positions, equity=equity)

    # The regime shrinks the risk BUDGET rather than the position cap. Halving
    # the per-trade risk in a defensive market shrinks every position
    # proportionally, which is what "reduce exposure" means; halving the cap
    # would only affect the largest one.
    risk_pct = (_finite(risk_pct) if risk_pct is not None
                else config.RISK_PER_TRADE_PCT)

    regime_notes = []

    if regime is not None:

        share = _finite(regime.get("mode_budget_share"))

        if share is not None and share < 1.0:
            risk_pct = risk_pct * share
            regime_notes.append(
                "حالت «%s» — ریسک هر معامله به %.2f%% سرمایه کاهش یافت"
                % (regime.get("mode_label") or regime.get("trading_mode"),
                   risk_pct))

        if not regime.get("available", True):
            regime_notes.append("وضعیت بازار نامعلوم — اندازه‌گذاری محافظه‌کارانه")

    size = position_size(
        price, distance, equity, risk_pct=risk_pct,
        adv=reading.get("adv"), sector_room=room,
        cash_available=cash_available,
    )

    score = risk_score(
        metrics={
            "volatility_60": reading.get("volatility_60"),
            "atr_pct": reading.get("atr_pct"),
            "avg_value_20": reading.get("adv"),
        },
        stop_pct=stop_pct,
        exit_sessions=size.get("exit_sessions"),
        event_count=event_count,
        sector_share_pct=room.get("share_pct"),
    )

    tradable = size["shares"] > 0 and (
        score["score"] is None or score["score"] >= config.RISK_MIN_SCORE)

    blockers = []

    if size["shares"] <= 0:
        blockers.append(size["reason"])

    if score["score"] is not None and score["score"] < config.RISK_MIN_SCORE:
        blockers.append("امتیاز ریسک %d زیر کف %d"
                        % (score["score"], config.RISK_MIN_SCORE))

    return {
        "tradable": bool(tradable),
        "risk_score": score["score"],
        "stop_distance": None if distance is None else round(distance, 2),
        "stop_price": (round(price - distance, 2)
                       if (price and distance) else None),
        "stop_distance_pct": None if stop_pct is None else round(stop_pct, 2),
        "stop_source": stop_source,
        "risk_pct_used": round(risk_pct, 4),
        "sector_room": room,
        "size": size,
        "blockers": blockers,
        "notes": (list(stop_notes) + regime_notes + list(score["notes"])
                  + list(size["notes"])),
        "caveat": CAVEAT,
    }
