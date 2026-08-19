"""The session loop. Everything else in this package is called from here.

ONE PASS PER SESSION, IN A FIXED ORDER
--------------------------------------
    mark to market with today's bar
        -> settle orders that became eligible today  (sells before buys)
        -> check stops, targets and trailing stops
        -> on a rebalance session: score, rank, and raise tomorrow's orders
        -> record cash, holdings and equity

The order is not arbitrary. Orders settle before the rebalance so that the cash
raised by a sale is available to the decision that follows it -- which is what
a real account does. Stops are checked EVERY session, not only on rebalance
days: a risk rule that fires monthly is not a risk rule.

WHY DECISIONS AND FILLS ARE ON DIFFERENT DAYS
---------------------------------------------
Signals are computed from session T's closing data and orders are raised
against session T+1. See execution.py for the full reason; the short version is
that client-type figures for session T are published after T closes, so a fill
at T's close spends information that did not exist.

WHAT THIS CANNOT SHOW, STATED ONCE
----------------------------------
The universe is the instruments listed TODAY. Companies that delisted are
absent from every historical window, and they are absent precisely where it
matters most -- the bad periods. Every result this engine produces is therefore
optimistic by an unknown margin, and no amount of care inside the loop fixes
it. It is reported in the header of every report rather than in a footnote.
"""

import math

import pandas as pd

import backtest_config
import config
import performance
from backtesting import execution, metrics as metrics_module, sizing
from backtesting.pit import PointInTimeData
from backtesting.portfolio import Portfolio
from backtesting.strategy import BUY, Strategy, TECHNICAL_ONLY


# The research variant's name. Not in advisor.ENGINES on purpose: it is a
# measurement subject, not something the panel or the paper trader may select.
ALPHA_CORE = "alpha_core"


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class BacktestResult(object):
    """Everything one run produced, including the reasons it produced nothing."""

    def __init__(self, cfg, variant, start, end):

        self.cfg = cfg
        self.variant = variant
        self.start = start
        self.end = end
        self.engine = "v1"

        self.curve = pd.DataFrame()
        self.trades = []
        self.orders = []
        self.open_positions = []
        self.metrics = {}
        self.benchmarks = {}
        self.diagnostics = {}
        self.insufficient = []
        self.rebalance_dates = []

    @property
    def available(self):
        return not self.insufficient and not self.curve.empty

    def to_dict(self):
        return {
            "engine": self.engine,
            "variant": self.variant,
            "start": str(self.start),
            "end": str(self.end),
            "available": self.available,
            "insufficient_data": self.insufficient,
            "metrics": self.metrics,
            "benchmarks": self.benchmarks,
            "diagnostics": self.diagnostics,
            "trades": len(self.trades),
            "config": {
                "round_trip_pct": round(self.cfg.round_trip_pct(), 3),
                "execution_delay_sessions": self.cfg.execution_delay_sessions,
                "execution_price": self.cfg.execution_price,
                "rebalance_sessions": self.cfg.rebalance_sessions,
                "max_positions": self.cfg.max_positions,
                "risk_per_trade_pct": self.cfg.risk_per_trade_pct,
                "stop_atr_multiple": self.cfg.stop_atr_multiple,
                "take_profit_atr_multiple": self.cfg.take_profit_atr_multiple,
                "trail_atr_multiple": self.cfg.trail_atr_multiple,
                "technical_gate": self.cfg.technical_gate,
                "selection_mode": self.cfg.selection_mode,
                "model_queues": self.cfg.model_queues,
                "slippage_pct": self.cfg.slippage_pct,
            },
        }


def run(start, end, cfg=None, variant=TECHNICAL_ONLY, data=None, panel=None,
        rebalance_dates=None, verbose=True, benchmarks=True, use_cache=True,
        engine=None, limit=None, exposure_floor_pct=None,
        disable_regime=False, alpha_top_n=None, hold_sessions=None,
        equal_weight=False, alpha_ranking=None, alpha_seed=None,
        alpha_panel=None):
    """Run one backtest. Returns a BacktestResult, never raises on thin data.

    `exposure_floor_pct` and `disable_regime` are RESEARCH knobs, off by
    default and used only by research_exposure.py. They move how much of the
    ranked list the book may own and whether the market read may brake it; they
    change no factor, threshold, stop or risk budget. See swing_strategy.py.

    `engine="alpha_core"` runs the momentum + Bollinger %B research variant
    through this same loop. It cannot be selected live -- see below.

    `hold_sessions` replaces the exit model with a pure time exit: every
    position is closed exactly that many sessions after it filled, and stops,
    targets, trailing stops and rotation are all switched off. It exists for one
    isolation experiment -- does the ranking pay once the stop stops
    interrupting it -- and it is not a strategy anyone should trade, because a
    book with no stop has no defined loss. `equal_weight` goes with it: the
    risk-based sizing model is DEFINED as a fraction of the stop distance, so
    removing the stop removes the sizing rule too, and the honest replacement is
    an equal slice rather than a stop distance that is no longer used.

    `panel` and `rebalance_dates` travel together and must be passed together.
    A sub-window that recomputed its own decision dates would land between the
    dates the panel holds, find nothing on any of them, and report an empty
    basket as a strategy result -- so validation.py slices one master grid and
    hands both halves down.

    `engine="v2"` swaps ONLY the thing that decides -- which names, at what
    size, with what stop -- for the swing chain the panel and the paper trader
    run. Every session-loop step below it is the same object either way: the
    same T+1 window, the same صف test, the same participation cap, the same
    costs, the same equity curve and the same metrics. That is the whole point:
    a difference between the two results has to be the strategy, because
    nothing else differs.
    """

    cfg = cfg or backtest_config.BacktestConfig()

    data = data or PointInTimeData(cfg, verbose=verbose)

    result = BacktestResult(cfg, variant, start, end)

    warning = backtest_config.check_costs()

    if warning:
        result.diagnostics["cost_warning"] = warning
        if verbose:
            print("WARNING: " + warning)

    sessions = data.sessions(start, end)

    if len(sessions) < 20:
        result.insufficient.append(
            "only %d sessions between %s and %s; at least 20 are needed before "
            "any statistic means anything" % (len(sessions), start, end)
        )
        return result

    if rebalance_dates is None:
        rebalance_dates = data.rebalance_dates(start, end, cfg.rebalance_sessions)
    else:
        rebalance_dates = [pd.Timestamp(d) for d in rebalance_dates]

    result.rebalance_dates = rebalance_dates

    if len(rebalance_dates) < 2:
        result.insufficient.append(
            "fewer than two rebalance dates in the window at "
            "REBALANCE_SESSIONS=%d" % cfg.rebalance_sessions
        )
        return result

    import advisor

    # DEFAULTS TO V1 WHEN NOT ASKED, and does NOT follow config.DECISION_ENGINE.
    #
    # That constant is a preference about what to trade today. A backtest is a
    # measurement, and one whose subject changed because a live default moved
    # would silently invalidate every stored report, every walk-forward fold and
    # every comparison made against them. Which engine was measured has to be an
    # argument at the call site.
    # "alpha_core" is a RESEARCH variant and is deliberately not part of
    # advisor.ENGINES: nothing live can select it, and engine_name() would
    # reject it. It exists to be measured, not to be traded.
    if str(engine or "").lower() == ALPHA_CORE:
        which = ALPHA_CORE
    else:
        which = advisor.V1 if engine is None else advisor.engine_name(engine)

    result.engine = which

    if which == ALPHA_CORE:

        from backtesting.alpha_core import AlphaCoreStrategy

        strategy = AlphaCoreStrategy(cfg, data, verbose=verbose, limit=limit,
                                     top_n=alpha_top_n,
                                     hold_sessions=hold_sessions,
                                     equal_weight=equal_weight,
                                     ranking=alpha_ranking, seed=alpha_seed,
                                     panel=alpha_panel)

        panel = {}

    elif which == advisor.V2:

        from backtesting.swing_strategy import SwingStrategy

        strategy = SwingStrategy(cfg, data, verbose=verbose, limit=limit,
                                 exposure_floor_pct=exposure_floor_pct,
                                 disable_regime=disable_regime)

        panel = {}      # v2 sizes against the live book; see swing_strategy.py

    else:

        strategy = Strategy(cfg, data, variant=variant, verbose=verbose)

        # Checked BEFORE the panel is built. A news variant is refused on this
        # database whatever the panel says, and scoring 818 symbols for two
        # minutes to reach a conclusion already known is waste, not thoroughness.
        if strategy.uses_news:
            available, reason = data.news_available()
            if not available:
                result.insufficient.append("NEWS: " + reason)
                if verbose:
                    print("  INSUFFICIENT DATA -- NEWS: " + reason)
                return result

    if verbose:
        print("backtest %s .. %s   engine=%s variant=%s"
              % (start, end, which, variant if which == advisor.V1 else "-"))
        print("  %d sessions, %d rebalance dates every %d sessions"
              % (len(sessions), len(rebalance_dates), cfg.rebalance_sessions))

    if panel is None:
        panel = strategy.build_panel(rebalance_dates, use_cache=use_cache)

    problems = strategy.check_data(rebalance_dates, panel)

    if problems:
        result.insufficient.extend(problems)
        if verbose:
            for line in problems:
                print("  INSUFFICIENT DATA -- " + line)
        return result

    book = Portfolio(cfg)
    orders = execution.OrderBook(cfg)
    filler = execution.ExecutionModel(cfg)

    cash_targets = _regime_cash(cfg, rebalance_dates, verbose) if cfg.use_regime_cash else {}

    rebalance_set = set(rebalance_dates)

    # Names the engine may ever need a price for. Loading them once here rather
    # than inside the loop is most of the runtime.
    wanted_codes = {code for readings in panel.values() for code in readings}

    if verbose and wanted_codes:
        print("  loading prices for %d instruments the panel proposed"
              % len(wanted_codes), flush=True)

    diagnostics = {
        "sessions": len(sessions),
        "rebalances": len(rebalance_dates),
        "signals_total": 0,
        "signals_per_rebalance": [],
        "empty_rebalances": 0,
        "orders_raised": 0,
        "orders_expired": 0,
        "stopped_out": 0,
        "target_hit": 0,
        "rotated_out": 0,
        "queue_blocked_exit": 0,

        # ---- where a valid signal stopped being a position ----------------
        #
        # V1 reported 58.6% average cash and could not say why. These four
        # counters separate the candidates:
        #
        #   ranked_out    passed every entry test but sat below the top
        #                 MAX_POSITIONS, so the basket size discarded it
        #   blocked_by    reached the book and a portfolio rule refused it,
        #                 keyed by which rule
        #   sized_to_zero the rule allowed it and the sizing model asked for
        #                 less than MIN_POSITION_PCT, so nothing was bought
        #   unfilled      an order was raised and the market never filled it
        #
        # Together with strategy.rejection_summary() they account for every
        # candidate, which is what turns "cash was high" into a diagnosis.
        "ranked_out": 0,
        "blocked_by": {},
        "sized_to_zero": 0,
        "wanted_positions": 0,
        "opened_positions": 0,

        # Counted per side. `orders_raised` mixes entries with rotation sales,
        # and a funnel built on it reports a stage that grew -- 234 "orders"
        # out of 208 intended positions, which is 112% and visibly nonsense.
        # The buy figures are the ones the cash question is about.
        "buy_orders_raised": 0,
        "buy_orders_expired": 0,
    }

    for date in sessions:

        # ---- 0. idle cash earns the session -------------------------------
        #
        # Before the marks and before any fill, because cash held INTO this
        # session is what earns it. No-op unless cfg.credit_idle_cash is set,
        # so v2.0 runs are untouched.
        book.accrue_cash()

        # ---- 1. mark to market -------------------------------------------
        for ins_code in list(book.positions):
            bar = data.bar_on(ins_code, date)
            if bar is not None:
                close = _finite(bar.get("close"))
                if close:
                    book.mark(ins_code, close, date)

        # ---- 2. settle orders that became eligible ------------------------
        _settle(book, orders, filler, data, date, diagnostics)

        # ---- 3. exits: stops, targets, trailing stops ---------------------
        #
        # A time exit REPLACES them rather than joining them: the experiment it
        # serves asks what the ranking pays when nothing interrupts the holding
        # period, and a stop left running would be the interruption.
        exiting = set()

        if hold_sessions:
            exiting = _time_exits(book, orders, date, sessions, hold_sessions,
                                  diagnostics,
                                  delay=cfg.execution_delay_sessions)
        elif cfg.check_exits_daily:
            _apply_exits(book, orders, filler, data, date, sessions,
                         diagnostics)

        # ---- 4. rebalance -------------------------------------------------
        if date in rebalance_set:

            if date in cash_targets:
                # A defensive regime that only stops buying leaves the book
                # fully exposed to the fall it just warned about, so the floor
                # is applied in both directions.
                book.cash_reserve_pct = max(cfg.cash_reserve_pct,
                                            cash_targets[date])

            if getattr(strategy, "is_v2", False):
                _rebalance_v2(book, orders, strategy, data, date, sessions,
                              diagnostics, cfg, exiting=exiting)
            else:
                _rebalance(book, orders, strategy, panel, data, date, sessions,
                           diagnostics, cfg)

        # ---- 5. close the book on the final session ------------------------
        #
        # Before the last row is recorded, not after. Recording the mark and
        # then selling would leave a final equity that includes positions the
        # curve says were sold, and overstate it by one exit's costs -- small,
        # systematic, and in the flattering direction.
        if date == sessions[-1]:
            _liquidate(book, data, date, cfg)

        # ---- 6. record -----------------------------------------------------
        book.record(date)

        expired = orders.retire(date)

        diagnostics["orders_expired"] += len(expired)

        diagnostics["buy_orders_expired"] += sum(
            1 for order in expired if order.side == execution.BUY)

    curve = pd.DataFrame(book.curve)

    if curve.empty:
        result.insufficient.append("no equity curve was produced")
        return result

    curve = curve.set_index("date")

    result.curve = curve
    result.trades = book.closed
    result.orders = [o.as_dict() for o in orders.history]
    result.open_positions = book.snapshot()

    diagnostics.update(filler.stats)
    diagnostics["commission_paid"] = round(book.commission_paid, 0)
    diagnostics["tax_paid"] = round(book.tax_paid, 0)
    diagnostics["cash_interest_earned"] = round(
        getattr(book, "cash_interest_earned", 0.0), 0)
    diagnostics["credit_idle_cash"] = bool(
        getattr(cfg, "credit_idle_cash", False))
    diagnostics["blocked_future_reads"] = data.blocked_future_reads
    diagnostics["sizing_mode"] = getattr(cfg, "sizing_mode", None)

    if hasattr(strategy, "fundamental_coverage"):
        diagnostics["fundamental_coverage"] = round(
            strategy.fundamental_coverage, 3)

    if hasattr(strategy, "fundamental_filing_coverage"):
        diagnostics["fundamental_filing_coverage"] = round(
            strategy.fundamental_filing_coverage, 3)

    if hasattr(strategy, "news_coverage"):
        diagnostics["news_coverage"] = round(strategy.news_coverage, 3)

    if getattr(strategy, "universe_restricted", None):
        diagnostics["universe_restricted"] = strategy.universe_restricted

    diagnostics["entry_rejections"] = strategy.rejection_summary()

    diagnostics["cash_analysis"] = _cash_analysis(curve, diagnostics, cfg)

    result.diagnostics.update(diagnostics)

    benchmark_series = None

    if benchmarks:
        result.benchmarks = _benchmarks(data, curve, cfg, panel,
                                        rebalance_dates, verbose)
        benchmark_series = result.benchmarks.get("_tedpix_returns")
        result.benchmarks.pop("_tedpix_returns", None)

    result.metrics = metrics_module.summarise(
        curve, book.closed, benchmark=benchmark_series,
        initial_capital=book.initial_capital,
    )

    return result


# ==============================
# THE STEPS
# ==============================

def _settle(book, orders, filler, data, date, diagnostics):
    """Fill what can be filled today. Sales first, so buys can use the cash."""

    due = orders.due(date)

    if not due:
        return

    due.sort(key=lambda o: 0 if o.side == execution.SELL else 1)

    for order in due:

        bar = data.bar_on(order.ins_code, date)

        if bar is None:
            continue

        previous = data.bar_after(order.ins_code, date, offset=-1)
        previous_close = _finite(previous.get("close")) if previous is not None else None

        median_volume = data.median_volume(order.ins_code, date)

        if order.side == execution.SELL:

            position = book.positions.get(order.ins_code)

            if position is None:
                order.remaining = 0
                continue

            shares, price, refusal = filler.execute(
                order, bar, previous_close, median_volume,
                available_shares=position.shares,
            )

            if refusal or shares <= 0:
                if order.blocked == execution.SELL_QUEUE:
                    diagnostics["queue_blocked_exit"] += 1
                continue

            book.sell(order.ins_code, shares, price, date,
                      book.cfg.sell_cost_rate(), reason=order.reason or "exit",
                      tax_rate=book.cfg.sell_tax_pct / 100.0)

            order.fill(shares, price)

            filler.stats["filled" if order.is_done() else "partial"] += 1

        else:

            reading = order.reading or {}

            affordable = book.affordable_shares(
                order.remaining, filler.fill_price(bar) or 0.0,
                book.cfg.buy_cost_rate(),
            )

            if affordable <= 0:
                continue

            allowed, _ = book.can_open(reading)

            if not allowed and order.ins_code not in book.positions:
                continue

            shares, price, refusal = filler.execute(
                order, bar, previous_close, median_volume,
                available_shares=affordable,
            )

            if refusal or shares <= 0:
                continue

            # Stop and target are struck from the price actually paid, so the
            # risk budget means what it says rather than what the decision
            # session's close implied.
            stop = order.stop
            target = order.target
            atr_unit = order.atr_unit

            if order.ins_code not in book.positions and stop is not None:
                offset = order.reading.get("close") if order.reading else None
                if offset:
                    stop = price - (offset - stop)
                    if target is not None:
                        target = price + (target - offset)

            book.buy(reading, shares, price, date, book.cfg.buy_cost_rate(),
                     stop=stop, target=target, atr_unit=atr_unit)

            order.fill(shares, price)

            filler.stats["filled" if order.is_done() else "partial"] += 1


def _apply_exits(book, orders, filler, data, date, sessions, diagnostics):
    """Stops and targets, checked on every session against the real bar."""

    for ins_code in list(book.positions):

        bar = data.bar_on(ins_code, date)

        if bar is None:
            continue

        reason, price = book.exit_signal(ins_code, bar)

        if reason is None:
            continue

        previous = data.bar_after(ins_code, date, offset=-1)
        previous_close = _finite(previous.get("close")) if previous is not None else None

        state = filler.queue_state(bar, previous_close,
                                   data.median_volume(ins_code, date))

        position = book.positions[ins_code]

        if state == execution.SELL_QUEUE and not book.cfg.fill_out_of_sell_queue:
            # صف فروش: the stop is a wish, not an exit. Keep the position and
            # raise an order that retries -- which is exactly what happens to a
            # real stop-loss on this market, and why stop-based risk numbers
            # from a Western backtest do not transfer.
            diagnostics["queue_blocked_exit"] += 1

            orders.cancel_for(ins_code, side=execution.SELL)

            orders.submit(
                execution.SELL, ins_code, position.shares, date, sessions,
                reason=reason, symbol=position.symbol,
            )
            continue

        book.sell(ins_code, position.shares, price, date,
                  book.cfg.sell_cost_rate(), reason=reason,
                  tax_rate=book.cfg.sell_tax_pct / 100.0)

        orders.cancel_for(ins_code)

        diagnostics["stopped_out" if reason == "stop" else "target_hit"] += 1


def _rebalance(book, orders, strategy, panel, data, date, sessions,
               diagnostics, cfg):
    """Score, rank, exit what is no longer wanted, raise tomorrow's buys."""

    readings = panel.get(pd.Timestamp(date)) or {}

    candidates = strategy.signals(readings)

    diagnostics["signals_total"] += len(candidates)
    diagnostics["signals_per_rebalance"].append(
        {"date": str(pd.Timestamp(date).date()), "signals": len(candidates)}
    )

    if not candidates:
        diagnostics["empty_rebalances"] += 1

    ranked = strategy.rank(candidates)

    wanted = {}

    for reading in ranked:
        if len(wanted) >= cfg.max_positions:
            break
        wanted[reading["ins_code"]] = reading

    # Candidates that passed every entry test and lost only to the basket size.
    # A large number here means the strategy is not short of ideas, and no
    # amount of loosening the gates will reduce the cash.
    diagnostics["ranked_out"] += max(0, len(ranked) - len(wanted))

    # ---- exit what fell out of the basket -------------------------------
    for ins_code in list(book.positions):

        if ins_code in wanted:
            continue

        position = book.positions[ins_code]

        orders.cancel_for(ins_code, side=execution.SELL)

        raised = orders.submit(
            execution.SELL, ins_code, position.shares, date, sessions,
            reason="rotated out", symbol=position.symbol,
        )

        if raised:
            diagnostics["orders_raised"] += 1
            diagnostics["rotated_out"] += 1

    # ---- size and raise the buys -----------------------------------------
    equity = book.value()

    # The volatility-adjusted mode needs a reference to scale against, and it
    # is taken from THIS date's candidates rather than from a constant: a fixed
    # 2% target against a universe whose median is 4% would halve every
    # position, and the mode comparison would then be measuring cash drag
    # rather than sizing.
    reference_atr_pct = sizing.reference_volatility(list(readings.values()))

    for ins_code, reading in wanted.items():

        if ins_code in book.positions:
            continue

        diagnostics["wanted_positions"] += 1

        allowed, why = book.can_open(reading)

        if not allowed:
            diagnostics["blocked_by"][why] = (
                diagnostics["blocked_by"].get(why, 0) + 1)
            continue

        shares, stop, target = book.size_position(
            reading, reading["close"], equity=equity,
            reference_atr_pct=reference_atr_pct,
        )

        if shares <= 0:
            diagnostics["sized_to_zero"] += 1
            continue

        diagnostics["opened_positions"] += 1

        atr_unit = None

        if stop is not None and cfg.stop_atr_multiple:
            atr_unit = (reading["close"] - stop) / cfg.stop_atr_multiple

        raised = orders.submit(
            execution.BUY, ins_code, shares, date, sessions,
            reading=reading, reason="entry", stop=stop, target=target,
            atr_unit=atr_unit,
        )

        if raised:
            diagnostics["orders_raised"] += 1
            diagnostics["buy_orders_raised"] += 1


def _time_exits(book, orders, date, sessions, hold_sessions, diagnostics,
                delay=1):
    """Close every position that has been held its full term. Nothing else.

    The age is counted in SESSIONS, not in calendar days: a position opened
    before a two-week market closure has not been held for ten trading
    sessions, and counting days would exit it early on a market that shuts for
    Nowruz.

    THE TERM RUNS FROM THE DECISION BAR, NOT THE FILL, and that is the whole
    reason this function takes `delay`. The signal study measures its forward
    return from the close that produced the ranking; a term counted from the
    fill would end one session later, which on a 20-session rebalance lands one
    session AFTER the next decision date -- so the book would still be full when
    it was asked to re-select, sit out the whole next cycle, and report ~50%
    exposure that is an artefact of the calendar rather than a property of the
    exit rule. Counting from the decision bar makes the term end exactly on the
    next decision date, which is the holding period the signal study priced.

    The sale still goes through the ordinary order book, so T+1, the صف test and
    the participation cap apply exactly as they do to a stop. Only the REASON
    for selling is different.
    """

    if not hold_sessions:
        return set()

    index = getattr(_time_exits, "_index", None)

    if index is None or index.get("__for__") is not sessions:
        index = {stamp: position for position, stamp in enumerate(sessions)}
        index["__for__"] = sessions
        _time_exits._index = index

    today = index.get(date)

    if today is None:
        return set()

    exiting = set()

    for ins_code in list(book.positions):

        position = book.positions[ins_code]

        filled = index.get(pd.Timestamp(position.entry_date))

        if filled is None:
            continue

        decided = filled - int(delay or 0)

        if today - decided < hold_sessions:
            continue

        orders.cancel_for(ins_code, side=execution.SELL)

        raised = orders.submit(
            execution.SELL, ins_code, position.shares, date, sessions,
            reason="held %d sessions" % hold_sessions,
            symbol=position.symbol,
        )

        if raised:
            exiting.add(str(ins_code))
            diagnostics["orders_raised"] += 1
            diagnostics["time_exits"] = diagnostics.get("time_exits", 0) + 1

    return exiting


def _rebalance_v2(book, orders, strategy, data, date, sessions, diagnostics,
                  cfg, exiting=None):
    """Turn the v2 chain's decision into orders. It decides; this only records.

    No scoring, no ranking, no sizing and no stop arithmetic happens here --
    every number comes out of strategy.decide(), which is swing_advisor's own
    evaluate() plus portfolio_engine.allocate(). The two differences from
    _rebalance() above are both the v2 engine's rules rather than this
    function's:

      EXITS ARE THE ENGINE'S SELL, not "fell out of the basket". factor_engine
      answers a different question for a held name and SELL is the answer it
      gives; rotating out everything absent from today's book would be v1's
      rule applied to v2 signals.

      SIZE IS WHAT THE ALLOCATOR ALLOCATED. risk_engine sized it against this
      book's equity and sector exposure and portfolio_engine then cut it to fit,
      so re-sizing it here would discard the answer and ask a different model.
    """

    # A position already sold today is not capacity. _settle() fills sales
    # before purchases on the same session, so the money and the slot are both
    # free by the time any order raised here can execute -- and counting them as
    # held would leave a time-exited book unable to replace what it just sold.
    # `exiting` is empty in every mode except the time-exit experiment, so no
    # measured result changes.
    exiting = exiting or set()

    live = [position for position in book.positions.values()
            if str(position.ins_code) not in exiting]

    decision = strategy.decide(
        date,
        positions=[
            {
                "ins_code": position.ins_code,
                "symbol": position.symbol,
                "sector_name": position.sector,
                "position_value": position.market_value(),
            }
            for position in live
        ],
        equity=book.value(),
        cash=book.cash,
    )

    signals = {str(s.get("ins_code")): s for s in decision["signals"]}

    accepted = {str(s.get("ins_code")): s
                for s in decision["book"]["accepted"]}

    diagnostics["signals_total"] += len(decision["buys"])
    diagnostics["signals_per_rebalance"].append(
        {"date": str(pd.Timestamp(date).date()),
         "signals": len(decision["buys"])}
    )

    if not decision["buys"]:
        diagnostics["empty_rebalances"] += 1

    diagnostics["ranked_out"] += max(0, len(decision["buys"]) - len(accepted))

    for row in decision["book"].get("rejected") or []:
        key = row.get("reason_key") or "refused"
        diagnostics["blocked_by"][key] = diagnostics["blocked_by"].get(key, 0) + 1

    # ---- exits: the engine's own SELL ------------------------------------
    for ins_code in list(book.positions):

        signal = signals.get(str(ins_code))

        if signal is None or signal.get("action") != "SELL":
            continue

        position = book.positions[ins_code]

        orders.cancel_for(ins_code, side=execution.SELL)

        raised = orders.submit(
            execution.SELL, ins_code, position.shares, date, sessions,
            reason=(signal.get("reasons") or ["exit"])[0],
            symbol=position.symbol,
        )

        if raised:
            diagnostics["orders_raised"] += 1
            diagnostics["rotated_out"] += 1

    # ---- entries: exactly what portfolio_engine allocated -----------------
    for ins_code, signal in accepted.items():

        if ins_code in book.positions and ins_code not in exiting:
            continue

        diagnostics["wanted_positions"] += 1

        shares = int(signal.get("allocated_shares") or 0)

        entry = _finite(signal.get("entry_price"))
        stop = _finite(signal.get("stop_loss"))

        if shares <= 0 or not entry:
            diagnostics["sized_to_zero"] += 1
            continue

        diagnostics["opened_positions"] += 1

        atr_unit = None

        if stop is not None and cfg.stop_atr_multiple:
            atr_unit = (entry - stop) / cfg.stop_atr_multiple

        # The reading the fill path needs: a price to size the affordable
        # quantity from, and a sector for the book's own cap.
        reading = {
            "ins_code": ins_code,
            "symbol": signal.get("symbol"),
            "sector": signal.get("sector"),
            "close": entry,
        }

        raised = orders.submit(
            execution.BUY, ins_code, shares, date, sessions,
            reading=reading, reason="entry", stop=stop,
            target=_finite(signal.get("target_1")), atr_unit=atr_unit,
        )

        if raised:
            diagnostics["orders_raised"] += 1
            diagnostics["buy_orders_raised"] += 1


def _liquidate(book, data, date, cfg):
    """Close the book at the last session, at that session's close."""

    for ins_code in list(book.positions):

        bar = data.bar_on(ins_code, date)

        price = None

        if bar is not None:
            price = _finite(bar.get("close"))

        if price is None:
            position = book.positions[ins_code]
            price = position.last_price

        if not price:
            continue

        book.sell(ins_code, book.positions[ins_code].shares, price, date,
                  cfg.sell_cost_rate(), reason="end of window",
                  tax_rate=cfg.sell_tax_pct / 100.0)


# ==============================
# WHY THE BOOK WAS IN CASH
# ==============================

def _cash_analysis(curve, diagnostics, cfg):
    """Attribute the idle cash to a cause instead of reporting its level.

    "Average cash 58.6%" is a symptom with at least four distinct diseases, and
    they call for opposite remedies:

      NOT ENOUGH SIGNALS   the entry tests reject nearly everything, so the
                           book cannot fill however much money it has. Look at
                           entry_rejections.
      BASKET TOO SMALL     plenty passed and MAX_POSITIONS discarded the rest.
                           Loosening a gate would change nothing.
      PORTFOLIO CAPS       signals reached the book and the sector cap, the
                           cash reserve or the position count refused them.
      SIZING               the caps allowed it and the sizing model asked for
                           a position too small to be worth opening -- so the
                           money stayed in cash by arithmetic.

    Plus one that is not a decision at all: orders raised and never filled,
    which on this market means صف خرید. That cash was committed and the market
    refused it.

    This function only counts. It does not suggest a parameter change, because
    the specification is explicit that cash must not be reduced for its own
    sake -- and because the right remedy depends on which row is largest.
    """

    if curve is None or curve.empty:
        return {"available": False, "reason": "no equity curve"}

    average_cash = float(curve["cash_pct"].mean()) if "cash_pct" in curve else None

    average_positions = (float(curve["positions"].mean())
                         if "positions" in curve else None)

    wanted = diagnostics.get("wanted_positions") or 0
    opened = diagnostics.get("opened_positions") or 0
    blocked = dict(diagnostics.get("blocked_by") or {})
    sized_out = diagnostics.get("sized_to_zero") or 0
    ranked_out = diagnostics.get("ranked_out") or 0

    entries = diagnostics.get("entry_rejections") or {}

    # The BUY figures, not the totals: a rotation sale is not a step on the
    # path from a signal to a position, and mixing it in made the funnel
    # report a stage that grew.
    raised = diagnostics.get("buy_orders_raised") or 0
    expired = diagnostics.get("buy_orders_expired") or 0
    filled = diagnostics.get("filled") or 0
    partial = diagnostics.get("partial") or 0

    # A FUNNEL, NOT A LEAGUE TABLE.
    #
    # Sorting the causes by raw count reads as a ranking and is not one: the
    # first stage counts (candidate, date) pairs and the last counts orders,
    # so 16,098 rejections and 130 unfilled orders are not comparable
    # quantities. Presented as consecutive stages, each with the share that
    # survived it, the binding constraint is the stage with the steepest drop
    # -- which IS comparable, and is the number the question is really about.
    passed = entries.get("candidates_passed") or 0

    stages = [
        {
            "stage": "scored on a rebalance date",
            "count": entries.get("candidates_seen") or 0,
            "detail": None,
        },
        {
            "stage": "passed every entry test",
            "count": passed,
            "detail": entries.get("by_reason"),
        },
        {
            "stage": "selected into the top %d" % cfg.max_positions,
            "count": max(0, passed - ranked_out),
            "detail": None,
        },
        {
            "stage": "allowed by the portfolio rules",
            "count": max(0, wanted - sum(blocked.values())),
            "detail": [{"reason": k, "count": v,
                        "share": round(v / float(wanted), 4) if wanted else 0.0}
                       for k, v in sorted(blocked.items(), key=lambda kv: -kv[1])]
            or None,
        },
        {
            "stage": "sized at or above MIN_POSITION_PCT=%.1f%%"
                     % cfg.min_position_pct,
            "count": opened,
            "detail": None,
        },
        {
            "stage": "orders raised",
            "count": raised,
            "detail": None,
        },
        {
            "stage": "orders that reached a fill",
            "count": max(0, raised - expired),
            "detail": [{"reason": "expired unfilled (صف خرید or no turnover)",
                        "count": expired,
                        "share": round(expired / float(raised), 4)
                        if raised else 0.0}],
        },
    ]

    for index, stage in enumerate(stages):
        previous = stages[index - 1]["count"] if index else stage["count"]
        stage["of_previous"] = previous
        stage["survived"] = (round(stage["count"] / float(previous), 4)
                             if previous else None)
        stage["lost"] = max(0, previous - stage["count"]) if index else 0

    # The steepest proportional drop, ignoring the first stage which has no
    # predecessor. This is the answer to "why was the book in cash".
    narrowing = [s for s in stages[1:] if s["survived"] is not None]

    tightest = min(narrowing, key=lambda s: s["survived"]) if narrowing else None

    return {
        "available": True,
        "average_cash_pct": round(average_cash, 2) if average_cash is not None else None,
        "average_positions": round(average_positions, 2)
        if average_positions is not None else None,
        "max_positions": cfg.max_positions,
        "cash_reserve_pct": cfg.cash_reserve_pct,
        "positions_wanted": wanted,
        "positions_opened": opened,
        "fills": filled,
        "partial_fills": partial,
        "open_rate": round(opened / float(wanted), 4) if wanted else None,
        "funnel": stages,
        "tightest_stage": tightest["stage"] if tightest else None,
        "tightest_survival": tightest["survived"] if tightest else None,
        "largest_cause": (
            "%s — only %.1f%% of the previous stage survived it"
            % (tightest["stage"], (tightest["survived"] or 0) * 100)
            if tightest else None
        ),
        # The share of the cash that the configured floor explains. A book
        # averaging 58% cash against a 10% floor is not being held back by the
        # floor, and saying so stops the floor being blamed.
        "floor_explains_pct": (round(cfg.cash_reserve_pct / average_cash * 100, 1)
                               if average_cash else None),
    }


# ==============================
# BENCHMARKS
# ==============================

def _benchmarks(data, curve, cfg, panel, rebalance_dates, verbose=True):
    """Three comparisons, because beating cash is not the same as being worth it."""

    out = {}

    index_frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)

    if index_frame is not None and not index_frame.empty:

        series = index_frame.set_index("date")["close"]
        series = series.reindex(curve.index).ffill()

        returns = series.pct_change().dropna()

        out["_tedpix_returns"] = returns

        out["tedpix"] = _buy_and_hold_from_series(series, "شاخص کل (TEDPIX)")

    equal = data.index_series(backtest_config.BENCHMARK_EQUAL_WEIGHT)

    if equal is not None and not equal.empty:
        series = equal.set_index("date")["close"].reindex(curve.index).ffill()
        out["tedpix_equal_weight"] = _buy_and_hold_from_series(
            series, "شاخص هم‌وزن")

    basket = _liquid_basket(data, curve, cfg, panel, rebalance_dates)

    if basket:
        out["liquid_basket"] = basket

    if backtest_config.BENCHMARK_RISK_FREE:
        deposit = _risk_free(curve)
        if deposit:
            out["risk_free"] = deposit

    strategy_return = (
        float(curve["portfolio_value"].iloc[-1]) /
        float(curve["portfolio_value"].iloc[0]) - 1
    ) * 100

    out["strategy_return_pct"] = round(strategy_return, 2)

    for key in ("tedpix", "tedpix_equal_weight", "liquid_basket", "risk_free"):
        if key in out and out[key].get("return_pct") is not None:
            out[key]["excess_pct"] = round(
                strategy_return - out[key]["return_pct"], 2)

    return out


def _risk_free(curve):
    """A bank deposit over the same sessions. The benchmark with no drawdown.

    This is the alternative that actually competes for the money. At Iran's 23%
    policy rate, a strategy returning 20% over two years has not made 20% -- it
    has LOST about 30 points against leaving the money alone, while carrying
    every drawdown and every hour of effort. A report that compares only
    against the index can show a strategy beating the market and never mention
    that a deposit beat them both.

    Compounded over SESSIONS rather than calendar days, using the same
    convention performance.py uses for its hurdle, so the two figures agree.
    """

    sessions = len(curve)

    if sessions < 2:
        return None

    daily = performance.daily_risk_free()

    total = ((1 + daily) ** (sessions - 1) - 1) * 100

    return {
        "label": "سپرده بانکی (%.0f%% سالانه)" % (config.RISK_FREE_RATE * 100),
        "return_pct": round(total, 2),
        "sessions": sessions,
        "note": "no drawdown, no execution risk, no queue",
    }


def _buy_and_hold_from_series(series, label):
    """Total return of holding an index across the window."""

    clean = series.dropna()

    if len(clean) < 2:
        return {"label": label, "return_pct": None,
                "reason": "not enough index sessions in the window"}

    first = float(clean.iloc[0])
    last = float(clean.iloc[-1])

    if first <= 0:
        return {"label": label, "return_pct": None, "reason": "bad index level"}

    return {
        "label": label,
        "return_pct": round((last / first - 1) * 100, 2),
        "sessions": len(clean),
    }


def _liquid_basket(data, curve, cfg, panel, rebalance_dates):
    """Buy and hold the most liquid names on the start date, equal-weighted.

    Built from measured turnover rather than hand-picked, so it is a market
    basket and not a benchmark chosen because the strategy beats it. Costs are
    charged on both sides -- a benchmark that trades for free is not a
    benchmark a real alternative could have delivered.
    """

    if not rebalance_dates:
        return None

    start = rebalance_dates[0]

    readings = panel.get(pd.Timestamp(start)) or {}

    ranked = sorted(
        (r for r in readings.values() if r.get("adv")),
        key=lambda r: r["adv"], reverse=True,
    )[:backtest_config.BENCHMARK_BASKET_SIZE]

    if len(ranked) < 3:
        return None

    end = curve.index[-1]

    legs = []

    for reading in ranked:

        entry_bar = data.bar_after(reading["ins_code"], start, offset=1)

        if entry_bar is None:
            continue

        entry = _finite(entry_bar.get("open")) or _finite(entry_bar.get("close"))

        exit_index = data.position(reading["ins_code"], end)

        frame = data.bars(reading["ins_code"])

        if not entry or entry <= 0 or exit_index < 0 or frame is None:
            continue

        exit_price = _finite(frame["close"].iloc[exit_index])

        if not exit_price:
            continue

        gross = exit_price / entry - 1

        net = (1 + gross) * (1 - cfg.sell_cost_rate()) / (1 + cfg.buy_cost_rate()) - 1

        legs.append(net * 100)

    if not legs:
        return None

    return {
        "label": "سبد نقدشونده‌ی بازار (%d نماد، خرید و نگهداری)" % len(legs),
        "return_pct": round(sum(legs) / len(legs), 2),
        "names": len(legs),
    }


def _regime_cash(cfg, dates, verbose=True):
    """Cash floor per rebalance date, from regime.detect(as_of=)."""

    import regime

    out = {}

    for date in dates:
        try:
            read = regime.detect(as_of=date)
        except Exception as exc:
            if verbose:
                print("    regime failed at %s: %s" % (date, exc))
            continue

        if read.get("available"):
            out[pd.Timestamp(date)] = read.get("cash_target_pct")

    return out
