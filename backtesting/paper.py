"""Paper trading: the same engine, on live data, with no order ever sent.

WHY IT IS NOT "A BACKTEST OF THE LAST FEW DAYS"
-----------------------------------------------
A backtest can be re-run from scratch at any moment. A paper trade cannot: it
was raised at a moment, with the data available at that moment, and rebuilding
it later with today's better data is exactly the look-ahead everything else
here exists to prevent. So a paper account is PERSISTED, appended to and never
rewritten, in the paper_* tables.

That is also why a fresh account starts at the latest session rather than
replaying history. Replaying would produce a track record the account did not
live through, and a paper record whose value is that it was lived through
cannot be manufactured retrospectively.

WHAT IT SHARES WITH THE BACKTESTER
----------------------------------
Everything that decides: strategy.py for the signal, portfolio.py for the
sizing and the stops, execution.py for the T+1 fill, the queue test and the
participation cap. If the two disagreed about any of it, the paper account
would not be evidence about the backtest, and the backtest would not be a
forecast of the paper account.

WHICH ENGINE DECIDES
--------------------
`engine="v1"` is the chain above. `engine="v2"` raises the same orders from the
swing decision engine instead -- regime_engine, factor_engine, risk_engine,
trade_manager, portfolio_engine -- by calling advisor.v2_scan(), which is the
SAME function the panel calls. Not a copy of it: paper trading whose decisions
were assembled separately would be evidence about this file rather than about
the engine the system runs.

Everything after the decision is untouched and shared by both: the T+1 window,
the صف test, the participation cap, the cash floor at fill time, the persisted
book. Only the source of "which names, at what size, with what stop" changes.

THE V2 PATH DECIDES ON THE LATEST SESSION ONLY, and refuses on any earlier one.
The v2 chain reads the newest stored bars rather than reconstructing a past
date, so letting it decide a session from last week would score that week with
this week's prices -- the look-ahead this package exists to prevent. Catching up
still marks, settles and exits through those sessions; it just does not invent
decisions for them. In normal daily use the account is at the latest session and
this never binds.
"""

import math

import pandas as pd

import advisor
import backtest_config
import database
from backtesting import execution
from backtesting.pit import PointInTimeData
from backtesting.portfolio import Portfolio
from backtesting.strategy import Strategy, TECHNICAL_ONLY


DEFAULT_ACCOUNT = "default"


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class PaperTrader(object):
    """A virtual account that advances one session at a time, and remembers."""

    def __init__(self, account=DEFAULT_ACCOUNT, cfg=None,
                 variant=TECHNICAL_ONLY, data=None, verbose=True,
                 engine=None):

        self.account = account
        self.cfg = cfg or backtest_config.BacktestConfig()
        self.variant = variant
        self.verbose = verbose

        # Resolved by the same function the panel and the CLI use, so "v2"
        # cannot mean one thing here and another there.
        self.engine = advisor.engine_name(engine)

        self.data = data or PointInTimeData(self.cfg, verbose=verbose)

        # Built for both engines: v2 raises the orders, but the v1 strategy
        # object still owns check_data() and the panel cache, and constructing
        # it costs nothing until it is asked for a reading.
        self.strategy = Strategy(self.cfg, self.data, variant=variant,
                                 verbose=verbose)

        self.filler = execution.ExecutionModel(self.cfg)

    # ==============================
    # STATE
    # ==============================

    def _load_book(self):
        """Rebuild the portfolio object from what is on disk."""

        equity_rows = database.load_paper_equity(self.account)

        book = Portfolio(self.cfg,
                         initial_capital=backtest_config.PAPER_INITIAL_CAPITAL)

        if equity_rows:
            book.cash = float(equity_rows[-1]["cash"] or 0.0)

        for row in database.load_paper_positions(self.account):

            from backtesting.portfolio import Position

            position = Position(
                ins_code=str(row["ins_code"]),
                symbol=row["symbol"],
                sector=None,
                shares=float(row["shares"] or 0),
                entry_price=float(row["entry_price"] or 0),
                entry_date=row["entry_date"],
                stop=row["stop_price"],
                target=row["target_price"],
                atr_unit=row["atr_unit"],
            )

            position.peak = row["peak_price"] or position.entry_price

            book.positions[position.ins_code] = position

        last_date = pd.Timestamp(equity_rows[-1]["date"]) if equity_rows else None

        return book, last_date

    def _persist(self, book, date, note=None):

        for position in book.positions.values():
            database.save_paper_position(self.account, {
                "ins_code": position.ins_code,
                "symbol": position.symbol,
                "shares": position.shares,
                "entry_price": position.entry_price,
                "entry_date": position.entry_date,
                "stop_price": position.stop,
                "target_price": position.target,
                "peak_price": position.peak,
                "atr_unit": position.atr_unit,
            })

        database.save_paper_equity(
            self.account, pd.Timestamp(date).date(),
            round(book.cash, 0), round(book.holdings_value(), 0),
            round(book.value(), 0), len(book.positions), note,
        )

    # ==============================
    # THE STEP
    # ==============================

    def advance(self, until=None, max_sessions=20):
        """Process every session that has closed since the account last ran.

        Returns a summary of what happened. Running it twice on the same day
        does nothing the second time, which is the property that makes it safe
        to put on a schedule.
        """

        calendar = self.data.calendar()

        if len(calendar) == 0:
            return {"available": False,
                    "reason": "no trading calendar: the index history is empty"}

        end = pd.Timestamp(until) if until else calendar[-1]

        book, last_date = self._load_book()

        if last_date is None:
            # A new account starts NOW. Replaying history would manufacture a
            # track record the account never lived through.
            todo = [d for d in calendar if d <= end][-1:]
            fresh = True
        else:
            todo = [d for d in calendar if last_date < d <= end]
            fresh = False

        if max_sessions:
            todo = todo[:max_sessions]

        if not todo:
            return {
                "available": True,
                "account": self.account,
                "engine": self.engine,
                "processed": 0,
                "reason": "already up to date at %s" % (last_date.date()
                                                        if last_date is not None
                                                        else "-"),
                "portfolio_value": round(book.value(), 0),
                "positions": book.snapshot(),
            }

        last_rebalance = self._last_rebalance_date()

        summary = {
            "available": True,
            "account": self.account,
            "engine": self.engine,
            "variant": self.variant,
            "sessions": [str(d.date()) for d in todo],
            "processed": 0,
            "filled": 0,
            "exits": 0,
            "orders_raised": 0,
            "rebalanced_on": [],
        }

        for date in todo:

            # ---- mark ----------------------------------------------------
            for ins_code in list(book.positions):
                bar = self.data.bar_on(ins_code, date)
                if bar is not None:
                    close = _finite(bar.get("close"))
                    if close:
                        book.mark(ins_code, close, date)

            # ---- settle pending orders -----------------------------------
            summary["filled"] += self._settle(book, date)

            # ---- exits ---------------------------------------------------
            summary["exits"] += self._exits(book, date)

            # ---- decide --------------------------------------------------
            due = fresh or self._rebalance_due(calendar, last_rebalance, date)

            note = None

            if due:
                raised = self._decide(book, date, calendar)
                summary["orders_raised"] += raised
                summary["rebalanced_on"].append(str(date.date()))
                last_rebalance = date
                note = "rebalance"
                fresh = False

            self._persist(book, date, note)

            summary["processed"] += 1

        summary["portfolio_value"] = round(book.value(), 0)
        summary["cash"] = round(book.cash, 0)
        summary["positions"] = book.snapshot()
        summary["pending_orders"] = database.load_paper_orders(self.account)

        return summary

    def _last_rebalance_date(self):

        rows = database.load_paper_equity(self.account)

        dates = [pd.Timestamp(r["date"]) for r in rows
                 if (r.get("note") or "") == "rebalance"]

        return dates[-1] if dates else None

    def _rebalance_due(self, calendar, last_rebalance, date):

        if last_rebalance is None:
            return True

        between = calendar[(calendar > last_rebalance) & (calendar <= date)]

        return len(between) >= self.cfg.rebalance_sessions

    # ==============================
    # STEPS
    # ==============================

    def _settle(self, book, date):
        """Fill what the market allows, at this session's price."""

        filled = 0

        orders = database.load_paper_orders(self.account, status="open")

        orders.sort(key=lambda o: 0 if o["side"] == execution.SELL else 1)

        for row in orders:

            eligible = pd.Timestamp(row["eligible_from"])
            expires = pd.Timestamp(row["expires_on"])

            if date < eligible:
                continue

            if date > expires:
                row["status"] = "expired"
                database.save_paper_order(self.account, row)
                continue

            bar = self.data.bar_on(row["ins_code"], date)

            if bar is None:
                continue

            order = execution.Order(
                side=row["side"], ins_code=row["ins_code"],
                shares=int(row["shares"]), created_date=row["created_date"],
                eligible_from=eligible, expires_on=expires,
                reading={"ins_code": row["ins_code"], "symbol": row["symbol"],
                         "sector": None, "close": row["entry_close"]},
                reason=row["reason"], symbol=row["symbol"],
                stop=row["stop_price"], target=row["target_price"],
                atr_unit=row["atr_unit"],
            )

            order.remaining = int(row["shares"]) - int(row["filled_shares"] or 0)

            previous = self.data.bar_after(row["ins_code"], date, offset=-1)
            previous_close = (_finite(previous.get("close"))
                              if previous is not None else None)

            median_volume = self.data.median_volume(row["ins_code"], date)

            if order.side == execution.SELL:

                position = book.positions.get(row["ins_code"])

                if position is None:
                    row["status"] = "cancelled"
                    database.save_paper_order(self.account, row)
                    continue

                shares, price, refusal = self.filler.execute(
                    order, bar, previous_close, median_volume,
                    available_shares=position.shares)

                if refusal or shares <= 0:
                    continue

                trade = book.sell(row["ins_code"], shares, price, date,
                                  self.cfg.sell_cost_rate(),
                                  reason=row["reason"] or "exit",
                                  tax_rate=self.cfg.sell_tax_pct / 100.0)

                if trade:
                    database.close_paper_trade(
                        self.account, row["ins_code"], trade["entry_date"],
                        pd.Timestamp(date).date(), price, trade["pnl"],
                        trade["pnl_pct"], trade["holding_days"],
                        trade["reason"])

                    if row["ins_code"] not in book.positions:
                        database.delete_paper_position(self.account,
                                                       row["ins_code"])

            else:

                price_probe = self.filler.fill_price(bar) or 0.0

                affordable = book.affordable_shares(
                    order.remaining, price_probe, self.cfg.buy_cost_rate())

                if affordable <= 0:
                    continue

                shares, price, refusal = self.filler.execute(
                    order, bar, previous_close, median_volume,
                    available_shares=affordable)

                if refusal or shares <= 0:
                    continue

                stop = row["stop_price"]
                target = row["target_price"]

                # Restrike the stop from the price actually paid.
                if stop is not None and row["entry_close"]:
                    stop = price - (row["entry_close"] - stop)
                    if target is not None:
                        target = price + (target - row["entry_close"])

                book.buy({"ins_code": row["ins_code"], "symbol": row["symbol"],
                          "sector": None},
                         shares, price, pd.Timestamp(date).date(),
                         self.cfg.buy_cost_rate(),
                         stop=stop, target=target, atr_unit=row["atr_unit"])

                database.record_paper_trade(self.account, {
                    "ins_code": row["ins_code"], "symbol": row["symbol"],
                    "signal": "BUY",
                    "entry_date": pd.Timestamp(date).date(),
                    "entry_price": round(price, 2), "shares": shares,
                    "stop_price": round(stop, 2) if stop else None,
                    "target_price": round(target, 2) if target else None,
                    "reason": row["reason"],
                })

            order.fill(shares, price)

            row["filled_shares"] = (row["filled_shares"] or 0) + shares
            row["average_price"] = price
            row["status"] = ("filled" if row["filled_shares"] >= row["shares"]
                             else "open")

            database.save_paper_order(self.account, row)

            filled += 1

        return filled

    def _exits(self, book, date):
        """Stops and targets, checked against the real session bar."""

        exits = 0

        for ins_code in list(book.positions):

            bar = self.data.bar_on(ins_code, date)

            if bar is None:
                continue

            reason, price = book.exit_signal(ins_code, bar)

            if reason is None:
                continue

            previous = self.data.bar_after(ins_code, date, offset=-1)
            previous_close = (_finite(previous.get("close"))
                              if previous is not None else None)

            state = self.filler.queue_state(
                bar, previous_close, self.data.median_volume(ins_code, date))

            position = book.positions[ins_code]

            if state == execution.SELL_QUEUE and not self.cfg.fill_out_of_sell_queue:
                # صف فروش: the stop cannot be honoured today. Raise an order
                # that retries, exactly as a real stop-loss would sit unfilled.
                self._raise_order(execution.SELL, ins_code, position.shares,
                                  date, self.data.calendar(), reason=reason,
                                  symbol=position.symbol)
                continue

            trade = book.sell(ins_code, position.shares, price, date,
                              self.cfg.sell_cost_rate(), reason=reason,
                              tax_rate=self.cfg.sell_tax_pct / 100.0)

            if trade:
                database.close_paper_trade(
                    self.account, ins_code, trade["entry_date"],
                    pd.Timestamp(date).date(), price, trade["pnl"],
                    trade["pnl_pct"], trade["holding_days"], reason)

                database.delete_paper_position(self.account, ins_code)

                exits += 1

        return exits

    def _decide(self, book, date, calendar):
        """Score today, rank, and raise the orders that tomorrow will fill."""

        if self.engine == advisor.V2:
            return self._decide_v2(book, date, calendar)

        return self._decide_v1(book, date, calendar)

    # ==============================
    # THE V2 DECISION
    # ==============================

    def _decide_v2(self, book, date, calendar):
        """Raise orders from the swing engine -- the panel's own chain.

        No scoring, ranking, sizing or stop arithmetic happens here. Every
        number comes out of advisor.v2_scan(); this function only turns the
        book it returns into paper orders.
        """

        latest = pd.Timestamp(calendar[-1]) if len(calendar) else None

        if latest is not None and pd.Timestamp(date) != latest:
            # See the module docstring: the v2 chain reads the newest bars, so
            # deciding a past session with it would be look-ahead.
            if self.verbose:
                print("  [PAPER] %s is not the latest session (%s) — the v2 "
                      "engine does not decide past sessions"
                      % (date.date(), latest.date()), flush=True)
            return 0

        if self.verbose:
            print("  [PAPER] Engine = V2", flush=True)
            print("  scoring the market as of %s ..." % date.date(), flush=True)

        held = [
            {
                "ins_code": position.ins_code,
                "symbol": position.symbol,
                "sector_name": position.sector,
                "position_value": position.market_value(),
            }
            for position in book.positions.values()
        ]

        out = advisor.v2_scan(equity=book.value(), positions=held,
                              verbose=False)

        if self.verbose:
            self._report_engines(out)

        signals = {str(s.get("ins_code")): s for s in out.get("signals") or []}

        accepted = {str(s.get("ins_code")): s
                    for s in (out.get("book") or {}).get("accepted") or []}

        raised = 0

        # ---- exits: the engine's own SELL, not "fell out of the top ten" ----
        #
        # factor_engine.decide() answers a different question for a held name,
        # and SELL is the answer it gives. Rotating out everything absent from
        # today's book would be the v1 rule applied to v2 signals, which is a
        # strategy decision this file is not entitled to make.
        for ins_code in list(book.positions):

            signal = signals.get(str(ins_code))

            if signal is None or signal.get("action") != "SELL":
                continue

            position = book.positions[ins_code]

            if self._raise_order(execution.SELL, ins_code, position.shares,
                                 date, calendar,
                                 reason=(signal.get("reasons") or ["exit"])[0],
                                 symbol=position.symbol):
                raised += 1

        # ---- entries: exactly what portfolio_engine allocated ---------------
        for ins_code, signal in accepted.items():

            if ins_code in book.positions:
                continue

            shares = int(signal.get("allocated_shares") or 0)

            entry = _finite(signal.get("entry_price"))
            stop = _finite(signal.get("stop_loss"))

            if shares <= 0 or not entry:
                continue

            # The same conversion the v1 path makes, so the persisted atr_unit
            # means the same thing to the trailing stop in _exits().
            atr_unit = None

            if stop is not None and self.cfg.stop_atr_multiple:
                atr_unit = (entry - stop) / self.cfg.stop_atr_multiple

            if self._raise_order(execution.BUY, ins_code, shares, date,
                                 calendar, reason="entry",
                                 symbol=signal.get("symbol"), stop=stop,
                                 target=_finite(signal.get("target_1")),
                                 atr_unit=atr_unit, entry_close=entry):
                raised += 1

        return raised

    def _report_engines(self, out):
        """Say which engines answered -- read off the output, never asserted.

        A banner that printed ACTIVE unconditionally would be exactly the kind
        of claim this project keeps having to disprove. Each line is decided by
        whether the field only that engine produces came back.
        """

        signals = out.get("signals") or []

        def any_field(name):
            return any(s.get(name) is not None for s in signals)

        checks = (
            ("Decision Engine V2", any_field("swing_score")),
            ("Risk Engine V2", any_field("risk_score")),
            ("Trade Manager V2", any_field("target_1")),
        )

        for label, active in checks:
            print("  [PAPER] %-20s = %s"
                  % (label, "ACTIVE" if active else "INACTIVE"), flush=True)

        regime = out.get("regime") or {}

        print("  [PAPER] %-20s = %s / multiplier %s / entry floor +%s"
              % ("Regime", regime.get("trading_mode"),
                 regime.get("position_multiplier"), regime.get("entry_floor")),
              flush=True)

    # ==============================
    # THE V1 DECISION
    # ==============================

    def _decide_v1(self, book, date, calendar):
        """Score today, rank, and raise the orders that tomorrow will fill."""

        if self.verbose:
            print("  [PAPER] Engine = V1", flush=True)
            print("  scoring the market as of %s ..." % date.date(), flush=True)

        panel = self.strategy.build_panel([date], use_cache=True)

        readings = panel.get(pd.Timestamp(date)) or {}

        problems = self.strategy.check_data([date], panel)

        if problems:
            if self.verbose:
                for line in problems:
                    print("  INSUFFICIENT DATA -- " + line)
            return 0

        candidates = self.strategy.signals(readings)

        ranked = self.strategy.rank(candidates)

        wanted = {}

        for reading in ranked:
            if len(wanted) >= self.cfg.max_positions:
                break
            wanted[reading["ins_code"]] = reading

        raised = 0

        # ---- exit what fell out ----------------------------------------
        for ins_code in list(book.positions):

            if ins_code in wanted:
                continue

            position = book.positions[ins_code]

            if self._raise_order(execution.SELL, ins_code, position.shares,
                                 date, calendar, reason="rotated out",
                                 symbol=position.symbol):
                raised += 1

        # ---- enter what is new ------------------------------------------
        equity = book.value()

        for ins_code, reading in wanted.items():

            if ins_code in book.positions:
                continue

            allowed, _ = book.can_open(reading)

            if not allowed:
                continue

            shares, stop, target = book.size_position(
                reading, reading["close"], equity=equity)

            if shares <= 0:
                continue

            atr_unit = None

            if stop is not None and self.cfg.stop_atr_multiple:
                atr_unit = (reading["close"] - stop) / self.cfg.stop_atr_multiple

            if self._raise_order(execution.BUY, ins_code, shares, date,
                                 calendar, reason="entry",
                                 symbol=reading.get("symbol"), stop=stop,
                                 target=target, atr_unit=atr_unit,
                                 entry_close=reading["close"]):
                raised += 1

        return raised

    def _raise_order(self, side, ins_code, shares, date, calendar, reason=None,
                     symbol=None, stop=None, target=None, atr_unit=None,
                     entry_close=None):
        """Persist one order with its T+1 eligibility window."""

        if shares <= 0:
            return None

        sessions = list(calendar)

        index = sessions.index(pd.Timestamp(date)) if pd.Timestamp(date) in sessions else None

        if index is None:
            return None

        eligible_index = index + max(0, int(self.cfg.execution_delay_sessions))

        # The session it fills on may not exist yet -- that is the normal case
        # for a decision taken after today's close, and the order simply waits.
        if eligible_index < len(sessions):
            eligible = sessions[eligible_index]
        else:
            eligible = pd.Timestamp(date) + pd.Timedelta(days=1)

        expire_index = eligible_index + max(0, int(self.cfg.order_good_for_sessions) - 1)

        if expire_index < len(sessions):
            expires = sessions[expire_index]
        else:
            # The sessions the order would live through have not happened yet.
            # Tehran trades Saturday to Wednesday, so five sessions fall in
            # seven days; the +1 covers a holiday. Using seven days PER session
            # here -- the obvious guess -- kept an order alive for three weeks
            # and let a stale decision fill long after it stopped being one.
            span = max(1, int(self.cfg.order_good_for_sessions))
            expires = pd.Timestamp(eligible) + pd.Timedelta(
                days=int(math.ceil(span * 7.0 / 5.0)) + 1)

        return database.save_paper_order(self.account, {
            "ins_code": ins_code,
            "symbol": symbol,
            "side": side,
            "shares": int(shares),
            "created_date": pd.Timestamp(date).date(),
            "eligible_from": pd.Timestamp(eligible).date(),
            "expires_on": pd.Timestamp(expires).date(),
            "status": "open",
            "reason": reason,
            "stop_price": stop,
            "target_price": target,
            "atr_unit": atr_unit,
            "entry_close": entry_close,
        })


# ==============================
# REPORTING
# ==============================

def print_report(account=DEFAULT_ACCOUNT):
    """The paper account's trade blotter and its equity record."""

    trades = database.load_paper_trades(account)
    equity = database.load_paper_equity(account)
    positions = database.load_paper_positions(account)
    orders = database.load_paper_orders(account, status="open")

    print()
    print("=" * 110)
    print("PAPER ACCOUNT: %s" % account)
    print("=" * 110)

    if not equity:
        print("  no sessions recorded yet -- run `python main.py --paper` first")
        return

    first = equity[0]
    last = equity[-1]

    start_value = float(first["portfolio_value"] or 0)
    end_value = float(last["portfolio_value"] or 0)

    print("  %s .. %s   %d sessions" % (first["date"], last["date"], len(equity)))
    print("  value {:,.0f} -> {:,.0f}  ({:+.2f}%)".format(
        start_value, end_value,
        (end_value / start_value - 1) * 100 if start_value else 0.0))
    print("  cash {:,.0f}   positions {}".format(
        float(last["cash"] or 0), last["positions"]))

    print()
    print("OPEN POSITIONS")
    print("-" * 110)

    if positions:
        print("  %-12s %10s %12s %12s %12s %12s"
              % ("symbol", "shares", "entry", "stop", "target", "opened"))
        for row in positions:
            print("  %-12s %10s %12s %12s %12s %12s"
                  % (row["symbol"], row["shares"], row["entry_price"],
                     row["stop_price"], row["target_price"], row["entry_date"]))
    else:
        print("  none")

    if orders:
        print()
        print("PENDING ORDERS (raised, not yet filled -- T+%d)"
              % backtest_config.EXECUTION_DELAY_SESSIONS)
        print("-" * 110)
        for row in orders:
            print("  %-6s %-12s %10s shares   eligible %s   expires %s   %s"
                  % (row["side"], row["symbol"],
                     "{:,}".format(int(row["shares"] or 0)),
                     row["eligible_from"], row["expires_on"], row["reason"]))

    print()
    print("TRADES")
    print("-" * 110)

    if not trades:
        print("  none recorded")
        return

    print("  %-12s %-12s %-6s %11s %9s %11s %11s %10s %8s %7s %s"
          % ("entry", "symbol", "signal", "entry px", "qty", "stop",
             "target", "exit px", "P/L%", "days", "reason"))

    for trade in trades:
        print("  %-12s %-12s %-6s %11s %9s %11s %11s %10s %8s %7s %s"
              % (trade["entry_date"], trade["symbol"] or "-",
                 trade["signal"] or "-", _num(trade["entry_price"]),
                 _num(trade["shares"]), _num(trade["stop_price"]),
                 _num(trade["target_price"]), _num(trade["exit_price"]),
                 _num(trade["pnl_pct"]), trade["holding_days"] or "-",
                 trade["reason"] or "open"))

    closed = [t for t in trades if t.get("pnl") is not None]

    if closed:
        wins = [t for t in closed if t["pnl"] > 0]
        net = sum(t["pnl"] for t in closed)
        print()
        print("  closed %d   winners %d (%.1f%%)   net P/L %s"
              % (len(closed), len(wins), len(wins) / len(closed) * 100,
                 "{:,.0f}".format(net)))


def _num(value):
    if value is None:
        return "-"
    try:
        return "{:,.0f}".format(float(value)) if abs(float(value)) >= 100 \
            else "%.2f" % float(value)
    except (TypeError, ValueError):
        return str(value)
