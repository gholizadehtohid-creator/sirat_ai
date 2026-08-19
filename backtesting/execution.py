"""How an order becomes a fill: delay, queues (صف), participation, partials.

THE ONE-DAY GAP IS NOT PEDANTRY
-------------------------------
A decision taken from session T's closing data cannot be filled at session T's
close. The close already happened. Worse, the client-type figures the live
selection ranks on are published AFTER the session ends, so filling at that
close spends a number that did not exist yet. In a flow-ranked strategy that
single day is worth tens of percentage points of invented return -- it has
already happened once in this project, which is why tests/test_backtesting.py
now asserts against it.

So: decide at T, fill at T+1's open.

WHAT IS REAL AND WHAT IS AN APPROXIMATION
-----------------------------------------
Real, from stored data:
    the T+1 open, high, low, close and the session's traded value and volume.

Approximated, and reported as such in every backtest report:
    QUEUES. This database has no order book and no intraday prints, so a
    locked session is INFERRED: a session that traded in effectively a single
    price at the edge of the daily band was queued. That test is right far more
    often than it is wrong, and it is wrong in both directions -- it will miss
    a queue that broke late in the session, and it will call a queue on a
    genuinely one-price session. It is a model of صف, not a record of one.

    PARTICIPATION. Capping an order at a share of the session's own traded
    value models the fact that a large order moves the price or does not
    complete. The cap is real; the linearity is an assumption.

Nothing here invents a price. An order that cannot fill goes unfilled, and an
unfilled order is reported rather than quietly dropped -- "the strategy could
not get in" is a result, and one of the more important ones on this market.
"""

import math

import config


BUY = "BUY"
SELL = "SELL"

BUY_QUEUE = "buy_queue"        # صف خرید -- cannot get in
SELL_QUEUE = "sell_queue"      # صف فروش -- cannot get out


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class Order(object):
    """An instruction that survives until it fills, expires, or is cancelled."""

    __slots__ = ("side", "ins_code", "symbol", "shares", "remaining",
                 "created_date", "eligible_from", "expires_on", "reading",
                 "reason", "filled_shares", "filled_value", "blocked",
                 "stop", "target", "atr_unit")

    def __init__(self, side, ins_code, shares, created_date, eligible_from,
                 expires_on, reading=None, reason=None, symbol=None,
                 stop=None, target=None, atr_unit=None):

        self.side = side
        self.ins_code = ins_code
        self.symbol = symbol or (reading or {}).get("symbol")
        self.shares = shares
        self.remaining = shares
        self.created_date = created_date
        self.eligible_from = eligible_from
        self.expires_on = expires_on
        self.reading = reading
        self.reason = reason
        self.filled_shares = 0
        self.filled_value = 0.0
        self.blocked = None
        self.stop = stop
        self.target = target
        self.atr_unit = atr_unit

    @property
    def average_price(self):
        if not self.filled_shares:
            return None
        return self.filled_value / self.filled_shares

    def fill(self, shares, price):
        self.filled_shares += shares
        self.filled_value += shares * price
        self.remaining -= shares

    def is_done(self):
        return self.remaining <= 0

    def as_dict(self):
        return {
            "side": self.side,
            "ins_code": self.ins_code,
            "symbol": self.symbol,
            "shares": self.shares,
            "filled": self.filled_shares,
            "remaining": self.remaining,
            "created_date": str(self.created_date),
            "average_price": (round(self.average_price, 2)
                              if self.average_price else None),
            "reason": self.reason,
            "blocked": self.blocked,
        }


class ExecutionModel(object):
    """Turns (order, session bar) into a fill, or into a stated refusal."""

    def __init__(self, cfg):
        self.cfg = cfg

        # Counters, so a run can report how often the market said no rather
        # than only what it did when the market said yes.
        self.stats = {
            "orders": 0,
            "filled": 0,
            "partial": 0,
            "unfilled": 0,
            "blocked_buy_queue": 0,
            "blocked_sell_queue": 0,
            "capped_by_participation": 0,
            "expired": 0,
        }

    # ==============================
    # QUEUE DETECTION  (APPROXIMATION)
    # ==============================

    def queue_state(self, bar, previous_close, median_volume=None):
        """None, BUY_QUEUE or SELL_QUEUE for this session.

        Two independent tests, either of which is enough:

          1. the session traded in one price at the edge of the band, or
          2. the session moved most of the band on a fraction of its usual
             volume -- the signature of a queue that traded only its token
             opening amount.
        """

        if not self.cfg.model_queues:
            return None

        close = _finite(bar.get("close"))
        high = _finite(bar.get("high"))
        low = _finite(bar.get("low"))
        volume = _finite(bar.get("volume"))

        previous_close = _finite(previous_close)

        if close is None or not previous_close or previous_close <= 0:
            return None

        if high is None:
            high = close
        if low is None:
            low = close

        move_pct = (close - previous_close) / previous_close * 100

        band = config.PRICE_BAND_PCT * self.cfg.queue_band_fraction

        if abs(move_pct) < band:
            return None

        one_price = (high - low) <= (close * 1e-6)

        thin = (
            median_volume is not None and median_volume > 0
            and volume is not None
            and volume < median_volume * self.cfg.queue_volume_fraction
        )

        if not (one_price or thin):
            return None

        return BUY_QUEUE if move_pct > 0 else SELL_QUEUE

    # ==============================
    # FILLS
    # ==============================

    def fill_price(self, bar):
        """The price an order pays on this session.

        Slippage is NOT applied here -- it lives in the cost rate, so that a
        single number covers it on both sides and cannot be double-counted by
        a caller that also adjusts the price.
        """

        if self.cfg.execution_price == "close":
            price = _finite(bar.get("close"))
        else:
            price = _finite(bar.get("open")) or _finite(bar.get("close"))

        return price if price and price > 0 else None

    def participation_cap(self, bar, price):
        """Shares this order may take from the session's own turnover."""

        if not price or price <= 0:
            return 0

        value = _finite(bar.get("value"))

        if value is None or value <= 0:
            # No value column: fall back to volume, which is the same statement
            # in different units.
            volume = _finite(bar.get("volume"))
            if volume is None or volume <= 0:
                return 0
            value = volume * price

        allowed_value = value * (self.cfg.max_participation_pct / 100.0)

        return int(allowed_value / price)

    def execute(self, order, bar, previous_close=None, median_volume=None,
                available_shares=None):
        """(shares, price, refusal). Refusal is None when something filled.

        `available_shares` bounds a sale at what is actually held and a buy at
        what the book can pay for; the caller computes it because only the
        caller knows the cash.
        """

        if bar is None:
            return 0, None, "no session"

        price = self.fill_price(bar)

        if price is None:
            return 0, None, "no tradeable price"

        state = self.queue_state(bar, previous_close, median_volume)

        if state == BUY_QUEUE and order.side == BUY and not self.cfg.fill_into_buy_queue:
            self.stats["blocked_buy_queue"] += 1
            order.blocked = BUY_QUEUE
            return 0, price, "صف خرید"

        if state == SELL_QUEUE and order.side == SELL and not self.cfg.fill_out_of_sell_queue:
            self.stats["blocked_sell_queue"] += 1
            order.blocked = SELL_QUEUE
            return 0, price, "صف فروش"

        wanted = order.remaining

        if available_shares is not None:
            wanted = min(wanted, int(available_shares))

        if wanted <= 0:
            return 0, price, "nothing affordable"

        cap = self.participation_cap(bar, price)

        if cap <= 0:
            return 0, price, "no turnover"

        fillable = min(wanted, cap)

        if fillable < wanted:
            self.stats["capped_by_participation"] += 1

        if not self.cfg.allow_partial_fill and fillable < order.remaining:
            return 0, price, "partial fills disabled"

        # A sliver of a position is a commission bill with a name attached.
        # Below the floor the order simply waits for a better session.
        floor = int(order.shares * self.cfg.min_fill_fraction)

        if fillable < floor and order.filled_shares == 0:
            return 0, price, "below minimum fill fraction"

        if fillable <= 0:
            return 0, price, "nothing fillable"

        return fillable, price, None


class OrderBook(object):
    """Pending orders, and the calendar rules that keep them honest."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.pending = []
        self.history = []

    def submit(self, side, ins_code, shares, decision_date, sessions,
               reading=None, reason=None, stop=None, target=None,
               atr_unit=None, symbol=None):
        """Queue an order. It becomes eligible after the execution delay.

        `sessions` is the trading calendar; the delay is counted in SESSIONS,
        not in days. Counting in days would let a Thursday decision fill on a
        Friday that the exchange never opened.
        """

        if shares <= 0:
            return None

        index = _session_index(sessions, decision_date)

        if index is None:
            return None

        eligible_index = index + max(0, int(self.cfg.execution_delay_sessions))

        if eligible_index >= len(sessions):
            return None

        expire_index = min(
            len(sessions) - 1,
            eligible_index + max(0, int(self.cfg.order_good_for_sessions) - 1),
        )

        order = Order(
            side=side,
            ins_code=ins_code,
            shares=int(shares),
            created_date=decision_date,
            eligible_from=sessions[eligible_index],
            expires_on=sessions[expire_index],
            reading=reading,
            reason=reason,
            symbol=symbol,
            stop=stop,
            target=target,
            atr_unit=atr_unit,
        )

        self.pending.append(order)

        return order

    def due(self, date):
        """Orders eligible to trade on this session."""

        return [o for o in self.pending
                if o.eligible_from <= date <= o.expires_on and not o.is_done()]

    def retire(self, date):
        """Drop filled and expired orders. Returns the expired ones."""

        expired = [o for o in self.pending
                   if o.expires_on < date and not o.is_done()]

        self.pending = [o for o in self.pending
                        if not o.is_done() and o.expires_on >= date]

        self.history.extend(expired)

        return expired

    def cancel_for(self, ins_code, side=None):
        """Withdraw outstanding orders for a name, e.g. once it is stopped out."""

        kept = []
        cancelled = []

        for order in self.pending:
            if order.ins_code == ins_code and (side is None or order.side == side):
                cancelled.append(order)
            else:
                kept.append(order)

        self.pending = kept
        self.history.extend(cancelled)

        return cancelled


def _session_index(sessions, date):
    """Position of `date` in the calendar, or the next session after it."""

    import pandas as pd

    stamp = pd.Timestamp(date)

    for index, session in enumerate(sessions):
        if session >= stamp:
            return index

    return None
