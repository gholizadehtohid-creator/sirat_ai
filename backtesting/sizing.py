"""Four ways to decide how much. Only the size differs; every risk rule is shared.

WHY THIS IS A SEPARATE MODULE AND NOT A FLAG INSIDE portfolio.py
----------------------------------------------------------------
The comparison the specification asks for is only meaningful if the four
methods differ in EXACTLY one thing. Written as branches inside the portfolio,
they drift: one branch forgets the minimum-position floor, another rounds
differently, a third quietly skips the sector cap -- and the resulting table
reports a sizing comparison while actually measuring four slightly different
strategies.

So the stop, the target, the floor, the ceiling and the rounding all live here
once, and each mode contributes a single number: the rial value it wants in
this position. Everything after that is identical by construction.

THE FOUR
--------
    equal_weight          equity / MAX_POSITIONS. The null hypothesis. If a
                          cleverer rule cannot beat dividing by ten, the
                          cleverness is decoration.

    fixed_fractional      a fixed share of equity per name
                          (MAX_POSITION_PCT). What most retail sizing is in
                          practice, whatever it is called.

    volatility_adjusted   equal RISK rather than equal money: the target weight
                          scaled by a reference volatility over the name's own
                          ATR%, so a share that moves twice as much gets half
                          the money. Needs no stop to exist.

    risk_based            the live system's rule: risk budget / stop distance,
                          so a stop-out costs RISK_PER_TRADE_PCT of equity
                          whatever the name does. The default, because
                          backtesting anything else would measure a strategy
                          nobody runs.

WHAT THEY SHARE, AND WHY IT MATTERS THAT THEY DO
------------------------------------------------
Every mode gets the same ATR stop and the same ATR target. That is not a
convenience: without it, "risk_based did better" could mean "risk_based was the
only mode with a stop", and the table would be answering a question nobody
asked. The stop is a risk rule; sizing decides how much rides on it.

A position below MIN_POSITION_PCT is skipped rather than shrunk, in every mode.
A 0.4% position is a commission bill with a name attached.
"""

import math


EQUAL_WEIGHT = "equal_weight"
FIXED_FRACTIONAL = "fixed_fractional"
VOLATILITY_ADJUSTED = "volatility_adjusted"
RISK_BASED = "risk_based"

MODES = (RISK_BASED, EQUAL_WEIGHT, FIXED_FRACTIONAL, VOLATILITY_ADJUSTED)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def stop_distance(cfg, reading, price, stop_atr=None):
    """Rials between the entry and the stop. None when stops are disabled.

    Shared by every mode, including the ones that do not size on it, so that a
    mode comparison is a comparison of sizing and not of risk control.
    """

    price = _finite(price)

    if not price or price <= 0:
        return None

    atr = _finite((reading or {}).get("atr"))

    stop_atr = cfg.stop_atr_multiple if stop_atr is None else stop_atr

    if not stop_atr:
        return None

    if atr and atr > 0:
        return atr * stop_atr

    # No ATR: two full daily bands, the widest move the exchange permits over
    # two sessions. Wider than a real stop would be, which errs toward a
    # smaller position rather than a larger one.
    import config as live_config

    return price * (live_config.PRICE_BAND_PCT * 2 / 100.0)


def target_value(mode, cfg, reading, price, equity, reference_atr_pct=None):
    """The rials this mode wants in the position, before any cap or floor.

    Returns None when the mode cannot form an opinion -- a volatility-adjusted
    size with no volatility reading, for instance -- and the caller then skips
    the name rather than falling back to a different mode, which would make the
    comparison a blend.
    """

    equity = _finite(equity)
    price = _finite(price)

    if not equity or equity <= 0 or not price or price <= 0:
        return None

    ceiling_pct = cfg.max_position_pct / 100.0

    if mode == EQUAL_WEIGHT:
        positions = max(1, int(cfg.max_positions or 1))
        return equity / positions

    if mode == FIXED_FRACTIONAL:
        return equity * ceiling_pct

    if mode == VOLATILITY_ADJUSTED:

        atr_pct = _finite((reading or {}).get("atr_pct"))

        if atr_pct is None or atr_pct <= 0:
            return None

        reference = _finite(reference_atr_pct)

        if reference is None or reference <= 0:
            reference = _finite(getattr(cfg, "sizing_target_atr_pct", None)) or 2.0

        base = equity / max(1, int(cfg.max_positions or 1))

        return base * (reference / atr_pct)

    if mode == RISK_BASED:

        distance = stop_distance(cfg, reading, price)

        if not distance or distance <= 0:
            # Stops disabled: risk-based has nothing to divide by, so it
            # degenerates to the position cap. Stated rather than silent,
            # because it means the sweep value stop_atr_multiple=0 turns this
            # mode into fixed_fractional.
            return equity * ceiling_pct

        risk_budget = equity * (cfg.risk_per_trade_pct / 100.0)

        return (risk_budget / distance) * price

    raise ValueError("unknown sizing mode: %r" % mode)


def size(cfg, reading, price, equity, mode=None, stop_atr=None,
         target_atr=None, reference_atr_pct=None):
    """(shares, stop, target). The only entry point; every mode goes through it.

    The order is fixed and is the same in all four modes:

        1. what does this mode WANT, in rials
        2. cap it at MAX_POSITION_PCT of equity
        3. drop it entirely if it is under MIN_POSITION_PCT
        4. round down to whole shares

    Step 2 is not redundant even for the modes whose target IS the cap: the
    volatility-adjusted mode can want three times the cap for a very quiet
    share, and letting it have that would make a single name a third of the
    book while every other rule in the system still believed in a 15% limit.
    """

    mode = mode or getattr(cfg, "sizing_mode", RISK_BASED)

    price = _finite(price)

    if not price or price <= 0:
        return 0, None, None

    equity = _finite(equity)

    if not equity or equity <= 0:
        return 0, None, None

    wanted = target_value(mode, cfg, reading, price, equity,
                          reference_atr_pct=reference_atr_pct)

    if wanted is None:
        return 0, None, None

    ceiling = equity * (cfg.max_position_pct / 100.0)

    wanted = min(wanted, ceiling)

    floor = equity * (cfg.min_position_pct / 100.0)

    if wanted < floor:
        return 0, None, None

    shares = int(wanted / price)

    if shares <= 0 or shares * price < floor:
        return 0, None, None

    # ---- the risk rules, identical across modes --------------------------
    distance = stop_distance(cfg, reading, price, stop_atr=stop_atr)

    stop = price - distance if distance else None

    atr = _finite((reading or {}).get("atr"))

    target_atr = (cfg.take_profit_atr_multiple if target_atr is None
                  else target_atr)

    target = (price + atr * target_atr) if (atr and target_atr) else None

    return shares, stop, target


def reference_volatility(readings):
    """Median ATR% across the candidates, for the volatility-adjusted mode.

    Taken from the candidates themselves rather than from a constant so the
    mode neither inflates nor deflates the whole book against the other three.
    A fixed 2% target against a universe whose median is 4% would halve every
    position and then report the resulting cash drag as a property of
    volatility sizing.
    """

    values = sorted(
        v for v in (_finite((r or {}).get("atr_pct")) for r in readings or [])
        if v is not None and v > 0
    )

    if not values:
        return None

    middle = len(values) // 2

    if len(values) % 2:
        return values[middle]

    return (values[middle - 1] + values[middle]) / 2.0
