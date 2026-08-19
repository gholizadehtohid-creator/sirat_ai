"""Does the V2 BACKTEST run the v2 engine, and does the engine's answer survive?

THE GAP THESE TESTS CLOSE
-------------------------
The whole backtesting/ package -- point-in-time data, T+1, صف, partial fills,
costs, metrics -- was wired to the v1 scoring model, and nothing in it imported
a v2 module. So "V2 was backtested" meant a signal study with no execution, and
the portfolio machinery had never seen the engine that actually trades.

These tests assert the connection in both directions:

    A-D   the v2 engines are CALLED during a v2 backtest
    E     the v1 strategy is NOT
    F     the backtest and the live/paper path agree on one symbol
    G-H   T+1 and look-ahead still hold on the new path
    I-J   the size, stop and target the v2 engine produced are the ones the
          execution layer receives -- not re-derived on the way through

They drive the real functions with injected data rather than the production
database, because the claim under test is "these parts are connected", and a
test that needs 800 symbols to prove a call happened is a test nobody runs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import advisor
import backtest_config
import factor_engine
import portfolio_engine
import risk_engine
import scoring
import swing_advisor
import trade_manager
from backtesting import engine as engine_module
from backtesting import execution
from backtesting.portfolio import Portfolio
from backtesting.strategy import Strategy


class Counter:
    """Count calls into real functions and put them back afterwards."""

    def __init__(self, *targets):
        self.targets = targets
        self.counts = {}
        self._originals = []

    def __enter__(self):
        for owner, name in self.targets:
            original = getattr(owner, name)
            key = "%s.%s" % (getattr(owner, "__name__", owner.__class__.__name__),
                             name)
            self.counts[key] = 0
            self._originals.append((owner, name, original))

            def make(key=key, original=original):
                def wrapper(*args, **kwargs):
                    self.counts[key] += 1
                    return original(*args, **kwargs)
                return wrapper

            setattr(owner, name, make())
        return self

    def __exit__(self, *exc):
        for owner, name, original in self._originals:
            setattr(owner, name, original)
        return False


class FakeStrategy(object):
    """A SwingStrategy stand-in that returns one fixed decision."""

    is_v2 = True

    def __init__(self, accepted=None, signals=None):
        self.accepted = accepted if accepted is not None else []
        self.signals = signals if signals is not None else []
        self.seen = []

    def decide(self, date, positions=None, equity=None, cash=None):
        self.seen.append({"date": date, "positions": list(positions or []),
                          "equity": equity, "cash": cash})
        return {
            "date": date,
            "regime": {"available": True, "trading_mode": "NORMAL"},
            "policy": {},
            "equity": equity,
            "signals": self.signals,
            "buys": [s for s in self.signals if s.get("action") == "BUY"],
            "holdings": [],
            "book": {"accepted": self.accepted, "rejected": []},
        }

    def check_data(self, dates, panel=None):
        return []

    def rejection_summary(self):
        return []


def _signal(ins_code="111", action="BUY", shares=5000, entry=1000.0,
            stop=950.0, target=1150.0, score=88):
    return {
        "ins_code": ins_code, "symbol": "الف", "sector": "فلز",
        "action": action, "swing_score": score, "risk_score": 70,
        "entry_price": entry, "stop_loss": stop, "target_1": target,
        "allocated_shares": shares, "allocated_value": shares * entry,
        "reasons": ["stub"],
    }


def _diagnostics():
    return {"signals_total": 0, "signals_per_rebalance": [],
            "empty_rebalances": 0, "orders_raised": 0, "rotated_out": 0,
            "ranked_out": 0, "blocked_by": {}, "sized_to_zero": 0,
            "wanted_positions": 0, "opened_positions": 0,
            "buy_orders_raised": 0}


# ==============================
# A-E: WHICH ENGINES RUN
# ==============================

class EnginesCalledTest(unittest.TestCase):
    """A, B, C, D and E, on one real decision through SwingStrategy."""

    @classmethod
    def setUpClass(cls):
        from backtesting.swing_strategy import SwingStrategy
        cls.SwingStrategy = SwingStrategy

    def _strategy(self):
        """A SwingStrategy with one hand-built symbol and no database."""

        cfg = backtest_config.BacktestConfig()

        bars = self._bars()

        class Data:
            blocked_future_reads = 0

            def instruments(self):
                return {"111": {"symbol": "الف", "sector": "فلز",
                                "name": "الف"}}

            def bars(self, ins_code):
                return bars

            def stale_sessions(self, ins_code, date):
                return 0

            def index_series(self, key):
                return None

            def fundamental_history(self, ins_code, date, periods=4):
                return []

            def market_ratios(self, record, close, sector=None):
                return {}

        strategy = self.SwingStrategy(cfg, Data(), verbose=False)

        # The store and the market read are the two inputs that would reach for
        # the database; both are injected so the test stays offline.
        strategy._medians = {}
        strategy._turnover = ([], pd.DatetimeIndex([]))
        strategy.regime_at = lambda date, breadth=None: {
            "available": True, "trading_mode": "NORMAL",
            "mode_label": "عادی", "max_invested_pct": 90.0,
            "position_multiplier": 0.85, "entry_floor": 0, "score": 60,
        }

        return strategy

    def _bars(self):
        sessions = 400

        dates = pd.bdate_range("2024-01-01", periods=sessions)

        closes = [1000.0 * (1.0 + 0.0015 * i) for i in range(sessions)]

        return pd.DataFrame({
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [500_000.0] * sessions,
            "value": [500_000_000.0] * sessions,
            "trades": [900.0] * sessions,
        })

    def test_a_the_backtest_calls_factor_engine(self):
        strategy = self._strategy()

        with Counter((factor_engine, "swing_score"),
                     (factor_engine, "decide")) as counter:
            strategy.decide(pd.Timestamp("2025-06-02"), equity=1e9)

        self.assertGreater(counter.counts["factor_engine.swing_score"], 0)
        self.assertGreater(counter.counts["factor_engine.decide"], 0)

    def test_b_the_backtest_calls_risk_engine(self):
        strategy = self._strategy()

        with Counter((risk_engine, "assess")) as counter:
            strategy.decide(pd.Timestamp("2025-06-02"), equity=1e9)

        self.assertGreater(counter.counts["risk_engine.assess"], 0)

    def test_c_the_backtest_calls_trade_manager(self):
        strategy = self._strategy()

        with Counter((trade_manager, "plan")) as counter:
            strategy.decide(pd.Timestamp("2025-06-02"), equity=1e9)

        self.assertGreater(counter.counts["trade_manager.plan"], 0)

    def test_d_the_backtest_calls_portfolio_engine(self):
        strategy = self._strategy()

        with Counter((portfolio_engine, "allocate")) as counter:
            strategy.decide(pd.Timestamp("2025-06-02"), equity=1e9)

        self.assertGreater(counter.counts["portfolio_engine.allocate"], 0)

    def test_e_no_v1_scoring_runs_on_the_v2_path(self):
        strategy = self._strategy()

        with Counter((scoring, "evaluate"), (scoring, "selection_score"),
                     (Strategy, "signals"), (Strategy, "rank")) as counter:
            strategy.decide(pd.Timestamp("2025-06-02"), equity=1e9)

        self.assertEqual(sum(counter.counts.values()), 0,
                         "the v1 model ran inside a v2 backtest: %r"
                         % counter.counts)

    def test_f_the_backtest_uses_the_same_evaluate_as_live_and_paper(self):
        """One decision function, three callers. The anti-drift assertion."""

        import inspect

        from backtesting import swing_strategy

        self.assertIn("swing_advisor.evaluate",
                      inspect.getsource(swing_strategy.SwingStrategy.decide))

        self.assertIn("portfolio_engine.allocate",
                      inspect.getsource(swing_strategy.SwingStrategy.decide))

        # ... and the live/paper side goes through the same evaluate().
        self.assertIn("evaluate(", inspect.getsource(swing_advisor.scan))

    def test_f2_the_same_inputs_give_the_same_decision_live_and_historical(self):
        """Feed one frame to both callers and require an identical signal."""

        strategy = self._strategy()

        date = pd.Timestamp("2025-06-02")

        historical = strategy.decide(date, equity=1e9)

        frame = strategy._frame_for("111", date)

        live = swing_advisor.evaluate(
            "111", strategy.instruments["111"], frame,
            regime=strategy.regime_at(date), medians={}, equity=1e9,
            positions=[], index_series=None, fundamental=None,
        )

        self.assertTrue(historical["signals"], "no signal was produced")

        mine = historical["signals"][0]

        for field in ("swing_score", "action", "entry_price", "stop_loss",
                      "target_1", "shares"):
            self.assertEqual(mine.get(field), live.get(field),
                             "%s differs between the backtest and live paths"
                             % field)


# ==============================
# G-J: THE DECISION SURVIVES EXECUTION
# ==============================

class DecisionSurvivesExecutionTest(unittest.TestCase):

    def setUp(self):
        self.cfg = backtest_config.BacktestConfig()
        self.sessions = list(pd.bdate_range("2025-01-01", periods=10))
        self.book = Portfolio(self.cfg)
        self.orders = execution.OrderBook(self.cfg)

    def _rebalance(self, strategy, date=None):
        engine_module._rebalance_v2(
            self.book, self.orders, strategy, None,
            date or self.sessions[0], self.sessions, _diagnostics(), self.cfg)

    def test_g_the_order_is_not_eligible_until_the_next_session(self):
        strategy = FakeStrategy(accepted=[_signal()],
                                signals=[_signal()])

        self._rebalance(strategy)

        raised = self.orders.pending[0]

        self.assertEqual(pd.Timestamp(raised.created_date),
                         pd.Timestamp(self.sessions[0]))
        self.assertGreater(pd.Timestamp(raised.eligible_from),
                           pd.Timestamp(self.sessions[0]),
                           "an order could fill on its own decision session")

    def test_h_the_strategy_is_only_ever_asked_about_the_decision_date(self):
        """Look-ahead cannot enter through the rebalance call."""

        strategy = FakeStrategy(accepted=[], signals=[])

        self._rebalance(strategy, date=self.sessions[3])

        self.assertEqual(len(strategy.seen), 1)
        self.assertEqual(pd.Timestamp(strategy.seen[0]["date"]),
                         pd.Timestamp(self.sessions[3]))

    def test_i_the_position_size_from_v2_reaches_the_order_unchanged(self):
        strategy = FakeStrategy(accepted=[_signal(shares=4321)],
                                signals=[_signal(shares=4321)])

        self._rebalance(strategy)

        self.assertEqual(self.orders.pending[0].shares, 4321)

    def test_j_the_stop_and_target_from_v2_reach_the_order_unchanged(self):
        strategy = FakeStrategy(
            accepted=[_signal(stop=903.5, target=1207.25)],
            signals=[_signal(stop=903.5, target=1207.25)])

        self._rebalance(strategy)

        order = self.orders.pending[0]

        self.assertAlmostEqual(order.stop, 903.5, places=4)
        self.assertAlmostEqual(order.target, 1207.25, places=4)

    def test_the_engine_does_not_resize_what_the_allocator_allocated(self):
        """A second sizing model on top of v2's would silently halve the book."""

        import inspect

        source = inspect.getsource(engine_module._rebalance_v2)

        self.assertNotIn("size_position", source)
        self.assertIn("allocated_shares", source)

    def test_a_held_name_is_sold_only_on_the_engines_own_sell(self):
        from backtesting.portfolio import Position

        for code in ("222", "333"):
            self.book.positions[code] = Position(
                ins_code=code, symbol=code, sector="x", shares=100,
                entry_price=10.0, entry_date=self.sessions[0])

        strategy = FakeStrategy(
            accepted=[],
            signals=[_signal(ins_code="222", action="SELL")])

        self._rebalance(strategy)

        sold = [o.ins_code for o in self.orders.pending
                if o.side == execution.SELL]

        self.assertEqual(sold, ["222"],
                         "a held name with no SELL signal was rotated out")


# ==============================
# THE ENGINE PICKS THE RIGHT STRATEGY
# ==============================

class EngineDispatchTest(unittest.TestCase):

    def test_a_backtest_defaults_to_v1_and_ignores_the_live_default(self):
        """A measurement must not change because a live preference moved."""

        import inspect

        source = inspect.getsource(engine_module.run)

        self.assertIn("advisor.V1 if engine is None", source,
                      "engine.run() follows config.DECISION_ENGINE, so every "
                      "stored backtest silently changes meaning when the panel "
                      "default moves")

    def test_asking_for_v2_selects_the_swing_strategy(self):
        import inspect

        source = inspect.getsource(engine_module.run)

        self.assertIn("SwingStrategy", source)
        self.assertIn("is_v2", inspect.getsource(engine_module.run))

    def test_the_result_records_which_engine_produced_it(self):
        result = engine_module.BacktestResult(
            backtest_config.BacktestConfig(), "technical", "2025-01-01",
            "2025-06-01")

        self.assertEqual(result.engine, "v1")
        self.assertIn("engine", result.to_dict())

    def test_both_engines_share_one_execution_model(self):
        """The comparison is only fair if nothing but the strategy differs."""

        import inspect

        source = inspect.getsource(engine_module.run)

        # One settle, one exit check, one liquidation, one metrics call --
        # outside the strategy branch.
        for shared in ("_settle(", "_apply_exits(", "_liquidate(",
                       "metrics_module.summarise("):
            self.assertEqual(source.count(shared), 1,
                             "%s appears more than once, so the engines may "
                             "not be sharing it" % shared)


if __name__ == "__main__":
    unittest.main()
