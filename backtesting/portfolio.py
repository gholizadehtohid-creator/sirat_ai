"""Cash, positions, sizing and the rules that stop a basket becoming one bet.

WHAT THIS OWNS AND WHAT IT DOES NOT
-----------------------------------
This module knows how much money there is, what is held, what each position is
worth and what would have to be true before another one could be opened. It
does not know what to buy (strategy.py) and it does not know whether an order
could be filled (execution.py). Keeping those apart is what makes the
look-ahead rule checkable: nothing here reads a price it was not handed.

THE THREE-STAGE SIZING, AND WHY IT IS THREE STAGES
--------------------------------------------------
Ten positions each allowed 15% of equity sum to 150%. The first version of the
live portfolio.py produced exactly that -- a book worth 1.43x the account while
every individual position sat inside its own limit. Sizing therefore runs:

    what does this position WANT      risk budget / stop distance
    what can the BOOK afford          cash, cash reserve, sector value cap
    round to whole shares             a fractional share is not a thing

A position is skipped rather than shrunk below MIN_POSITION_PCT: a 0.4%
position is a commission bill with a name attached.
"""

import math

import config


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class Position(object):
    """One holding, with everything needed to exit it correctly."""

    __slots__ = ("ins_code", "symbol", "sector", "shares", "entry_price",
                 "entry_date", "stop", "target", "peak", "atr_unit",
                 "commission_paid", "last_price", "last_date", "fills")

    def __init__(self, ins_code, symbol, sector, shares, entry_price,
                 entry_date, stop=None, target=None, atr_unit=None,
                 commission=0.0):

        self.ins_code = ins_code
        self.symbol = symbol
        self.sector = sector or "?"
        self.shares = shares
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.stop = stop
        self.target = target
        self.peak = entry_price
        self.atr_unit = atr_unit
        self.commission_paid = commission
        self.last_price = entry_price
        self.last_date = entry_date
        self.fills = 1

    def market_value(self):
        return self.shares * (self.last_price or self.entry_price)

    def cost_basis(self):
        return self.shares * self.entry_price

    def add(self, shares, price, commission=0.0):
        """A later fill of the same order. Entry becomes the weighted average.

        Partial fills are the normal case in Tehran, not an edge case, so a
        position has to be able to grow without pretending the first fill's
        price applied to all of it.
        """

        total = self.shares + shares

        if total <= 0:
            return

        self.entry_price = (
            (self.entry_price * self.shares + price * shares) / total
        )

        self.shares = total
        self.commission_paid += commission
        self.fills += 1

    def unrealised_pct(self):
        if not self.entry_price:
            return None
        return (self.last_price / self.entry_price - 1) * 100


class Portfolio(object):
    """The book: cash, holdings, realised trades and the daily equity record."""

    def __init__(self, cfg, initial_capital=None):

        self.cfg = cfg
        self.initial_capital = float(
            initial_capital if initial_capital is not None
            else cfg.initial_capital
        )

        self.cash = self.initial_capital
        self.positions = {}
        self.closed = []
        self.curve = []

        # Cash floor for the current rebalance period. The regime can raise it;
        # nothing lowers it below the configured reserve.
        self.cash_reserve_pct = cfg.cash_reserve_pct

        self.commission_paid = 0.0
        self.tax_paid = 0.0
        self.cash_interest_earned = 0.0

    # ==============================
    # VALUATION
    # ==============================

    def holdings_value(self):
        return sum(p.market_value() for p in self.positions.values())

    def value(self):
        return self.cash + self.holdings_value()

    def weight(self, ins_code):
        total = self.value()
        position = self.positions.get(ins_code)
        if not total or position is None:
            return 0.0
        return position.market_value() / total * 100

    def sector_value(self, sector):
        return sum(p.market_value() for p in self.positions.values()
                   if p.sector == sector)

    def sector_count(self, sector):
        return sum(1 for p in self.positions.values() if p.sector == sector)

    def investable(self):
        """Value the book may hold in shares, after the cash floor."""

        return self.value() * (1 - self.cash_reserve_pct / 100.0)

    def accrue_cash(self):
        """Credit idle cash with one session of the policy rate.

        Off unless cfg.credit_idle_cash is set, so v2.0 results stay
        reproducible. Compounded per session rather than divided by 240: at 23%
        the difference over a decade is not a rounding matter.

        Returns the interest added, so the caller can report it rather than
        having to infer it from a cash balance that also moves on every fill.
        """

        if not getattr(self.cfg, "credit_idle_cash", False):
            return 0.0

        rate = float(getattr(self.cfg, "cash_annual_rate", 0.0) or 0.0)

        if rate <= 0 or self.cash <= 0:
            return 0.0

        periods = float(getattr(config, "TRADING_DAYS_PER_YEAR", 240))

        daily = (1.0 + rate) ** (1.0 / periods) - 1.0

        interest = self.cash * daily

        self.cash += interest
        self.cash_interest_earned += interest

        return interest

    def mark(self, ins_code, price, date=None):
        position = self.positions.get(ins_code)

        if position is None or not price:
            return

        position.last_price = price

        if date is not None:
            position.last_date = date

        position.peak = max(position.peak or price, price)

    # ==============================
    # ENTRY RULES
    # ==============================

    def can_open(self, reading, price=None):
        """(allowed, reason). Every portfolio-level constraint, in one place."""

        ins_code = reading["ins_code"]

        if ins_code in self.positions:
            return False, "already held"

        if len(self.positions) >= self.cfg.max_positions:
            return False, "position count at maximum"

        sector = reading.get("sector") or "?"

        if self.sector_count(sector) >= self.cfg.max_per_sector:
            return False, "sector name cap"

        total = self.value()

        if total <= 0:
            return False, "no equity"

        if self.sector_value(sector) / total * 100 >= self.cfg.max_sector_pct:
            return False, "sector value cap"

        if self.cash <= 0:
            return False, "no cash"

        if self.holdings_value() >= self.investable():
            return False, "cash reserve reached"

        return True, None

    def size_position(self, reading, price, equity=None,
                      stop_atr=None, target_atr=None, mode=None,
                      reference_atr_pct=None):
        """(shares, stop, target). Which mode decides the size is a parameter.

        Score decides WHICH, volatility decides HOW MUCH. A name scoring 90
        that moves twice as much as one scoring 80 does not deserve twice the
        money -- at equal rials it puts twice the risk into the book. That is
        the reasoning behind the default mode; backtesting/sizing.py holds the
        three alternatives it is compared against, and holds all four in one
        place so the comparison is about sizing and nothing else.
        """

        from backtesting import sizing

        equity = float(equity if equity is not None else self.value())

        return sizing.size(
            self.cfg, reading, price, equity,
            mode=mode or getattr(self.cfg, "sizing_mode", sizing.RISK_BASED),
            stop_atr=stop_atr, target_atr=target_atr,
            reference_atr_pct=reference_atr_pct,
        )

    def affordable_shares(self, shares, price, buy_cost_rate):
        """Trim an order to what cash and the cash reserve actually allow."""

        if shares <= 0 or not price or price <= 0:
            return 0

        unit = price * (1 + buy_cost_rate)

        room_cash = self.cash / unit

        room_book = max(0.0, self.investable() - self.holdings_value()) / unit

        room_position = (
            self.value() * (self.cfg.max_position_pct / 100.0) / price
        )

        return max(0, int(min(shares, room_cash, room_book, room_position)))

    # ==============================
    # TRANSACTIONS
    # ==============================

    def buy(self, reading, shares, price, date, buy_cost_rate,
            stop=None, target=None, atr_unit=None):
        """Settle a filled buy. Returns the rials that left the account."""

        if shares <= 0 or not price or price <= 0:
            return 0.0

        gross = shares * price
        commission = gross * buy_cost_rate
        outlay = gross + commission

        if outlay > self.cash + 1e-6:
            return 0.0

        self.cash -= outlay
        self.commission_paid += commission

        ins_code = reading["ins_code"]

        existing = self.positions.get(ins_code)

        if existing is not None:
            existing.add(shares, price, commission)
            return outlay

        self.positions[ins_code] = Position(
            ins_code=ins_code,
            symbol=reading.get("symbol"),
            sector=reading.get("sector"),
            shares=shares,
            entry_price=price,
            entry_date=date,
            stop=stop,
            target=target,
            atr_unit=atr_unit,
            commission=commission,
        )

        return outlay

    def sell(self, ins_code, shares, price, date, sell_cost_rate,
             reason="exit", tax_rate=0.0):
        """Settle a filled sale, closing the position when nothing is left."""

        position = self.positions.get(ins_code)

        if position is None or shares <= 0 or not price or price <= 0:
            return None

        shares = min(shares, position.shares)

        gross = shares * price
        cost = gross * sell_cost_rate

        # `cost` is the WHOLE sell-side deduction and already contains the tax,
        # so the tax has to be carved out of it rather than added beside it --
        # otherwise the two counters sum to more than the account actually paid
        # and every cost figure in the report is overstated.
        tax = gross * tax_rate

        self.cash += gross - cost
        self.commission_paid += max(0.0, cost - tax)
        self.tax_paid += tax

        share_of_entry_commission = (
            position.commission_paid * (shares / position.shares)
            if position.shares else 0.0
        )

        entry_value = shares * position.entry_price

        net_profit = (gross - cost) - (entry_value + share_of_entry_commission)

        trade = {
            "ins_code": ins_code,
            "symbol": position.symbol,
            "sector": position.sector,
            "entry_date": position.entry_date,
            "exit_date": date,
            "entry_price": round(position.entry_price, 2),
            "exit_price": round(price, 2),
            "shares": shares,
            "stop_price": round(position.stop, 2) if position.stop else None,
            "target_price": round(position.target, 2) if position.target else None,
            "gross_pnl": round(gross - entry_value, 0),
            "pnl": round(net_profit, 0),
            "pnl_pct": round(net_profit / entry_value * 100, 2) if entry_value else None,
            "return_pct": round((price / position.entry_price - 1) * 100, 2),
            "commission": round(cost + share_of_entry_commission, 0),
            "holding_days": self._holding_days(position.entry_date, date),
            "reason": reason,
            "fills": position.fills,
        }

        position.shares -= shares
        position.commission_paid -= share_of_entry_commission

        if position.shares <= 0:
            del self.positions[ins_code]

        self.closed.append(trade)

        return trade

    @staticmethod
    def _holding_days(entry_date, exit_date):
        try:
            import pandas as pd
            return int((pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days)
        except Exception:
            return None

    # ==============================
    # EXITS
    # ==============================

    def exit_signal(self, ins_code, bar, trail_atr=None):
        """(reason, price) if this bar triggers an exit, else (None, None).

        Both the stop and the target can be inside one daily range, and daily
        OHLC cannot say which came first. Resolving to the stop is the only
        defensible default: assuming the target turns every ambiguous bar into
        a win and quietly inflates the entire backtest.
        """

        position = self.positions.get(ins_code)

        if position is None or bar is None:
            return None, None

        cfg = self.cfg

        high = _finite(bar.get("high"))
        low = _finite(bar.get("low"))
        close = _finite(bar.get("close"))

        if close is None:
            return None, None

        if high is None:
            high = close
        if low is None:
            low = close

        trail_atr = cfg.trail_atr_multiple if trail_atr is None else trail_atr

        # Ratchet the stop upward on the highest close seen, never downward.
        if trail_atr and position.atr_unit and position.stop is not None:
            position.peak = max(position.peak or close, close)
            trailed = position.peak - position.atr_unit * trail_atr
            position.stop = max(position.stop, trailed)

        hit_stop = position.stop is not None and low <= position.stop
        hit_target = position.target is not None and high >= position.target

        if hit_stop and hit_target:
            if cfg.ambiguous_bar_resolves_to == "target":
                return "target", position.target
            return "stop", position.stop

        if hit_stop:
            return "stop", position.stop

        if hit_target:
            return "target", position.target

        return None, None

    # ==============================
    # RECORD
    # ==============================

    def record(self, date, note=None):
        """One row of the equity curve. Called once per session, always."""

        # Rounded first, then added. Rounding the total separately lets the
        # written CSV show a portfolio value that is not cash plus holdings by
        # a rial, which costs a reader more confidence than the rial is worth.
        cash = round(self.cash, 0)
        holdings = round(self.holdings_value(), 0)

        total = cash + holdings

        row = {
            "date": date,
            "cash": cash,
            "holdings_value": holdings,
            "portfolio_value": total,
            "positions": len(self.positions),
            "cash_pct": round(cash / total * 100, 2) if total else 100.0,
        }

        if note:
            row["note"] = note

        self.curve.append(row)

        return row

    def snapshot(self):
        """Open positions as plain dicts, for reports and paper trading."""

        return [
            {
                "ins_code": p.ins_code,
                "symbol": p.symbol,
                "sector": p.sector,
                "shares": p.shares,
                "entry_price": round(p.entry_price, 2),
                "entry_date": p.entry_date,
                "last_price": round(p.last_price, 2) if p.last_price else None,
                "stop_price": round(p.stop, 2) if p.stop else None,
                "target_price": round(p.target, 2) if p.target else None,
                "market_value": round(p.market_value(), 0),
                "unrealised_pct": (round(p.unrealised_pct(), 2)
                                   if p.unrealised_pct() is not None else None),
            }
            for p in self.positions.values()
        ]
