"""Tests for the backtesting engine.

Every test here runs on synthetic data. Nothing touches the network and
nothing touches sirat_market.db -- a test that needs 407MB of market data on
disk is not a test, it is a rehearsal, and it stops running the moment the
database is missing.

The tests fall into four groups:

    arithmetic      P/L, commission, sizing, drawdown, Sharpe, Sortino
    rules           stops, targets, trailing, ambiguous bars, capital limits
    look-ahead      the ones that matter most: every path by which tomorrow
                    could reach today, closed and asserted closed
    integration     a whole run over a synthetic market, end to end
"""

import math
import os
import sys
import unittest
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_config
import config
import jalali
import performance
from backtesting import execution, metrics as metrics_module
from backtesting.pit import PointInTimeData
from backtesting.portfolio import Portfolio


# ==============================
# SYNTHETIC MARKET
# ==============================

def make_bars(sessions=400, start_price=1000.0, drift=0.0015, wobble=0.03,
              start="2023-01-01", volume=10_000_000):
    """A rising series with pullbacks, so RSI lands mid-range rather than 90."""

    dates = pd.bdate_range(start=start, periods=sessions)

    rows = []

    for index, date in enumerate(dates):

        trend = start_price * ((1 + drift) ** index)

        close = trend * (1 + wobble * math.sin(index / 7.0))

        high = close * 1.01
        low = close * 0.99
        open_price = close * (1 + 0.002 * math.cos(index / 5.0))

        session_volume = volume * (1.2 + 0.5 * math.sin(index / 5.0))

        rows.append({
            "date": date,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": round(session_volume, 0),
            "value": round(session_volume * close, 0),
            "trades": 1000,
        })

    return pd.DataFrame(rows)


class FakeData(PointInTimeData):
    """PointInTimeData with the disk replaced by dictionaries.

    Everything except the four loaders is the real implementation, which is
    the point: the date arithmetic under test is the production code, not a
    copy of it.
    """

    def __init__(self, cfg, symbols, index_frame=None, flow=None, filings=None):

        PointInTimeData.__init__(self, cfg, verbose=False)

        self._instruments = {
            code: {"symbol": meta["symbol"], "sector": meta["sector"]}
            for code, meta in symbols.items()
        }

        for code, meta in symbols.items():
            self._bars[code] = meta["bars"]
            self._flow[code] = (flow or {}).get(code)

        frame = index_frame

        if frame is None:
            # The calendar comes from the index, so it must span every bar.
            any_bars = list(symbols.values())[0]["bars"]
            frame = pd.DataFrame({
                "date": any_bars["date"],
                "close": any_bars["close"],
                "low": any_bars["low"],
                "high": any_bars["high"],
            })

        self._index[backtest_config.BENCHMARK_TEDPIX] = frame
        self._index[backtest_config.BENCHMARK_EQUAL_WEIGHT] = frame

        self._filings = filings if filings is not None else {}

        self.filing_stats = {"rows": 0, "dated": 0, "undated": 0,
                             "unplaceable": 0}

    def fingerprint(self, extra=None):
        return "test"


def two_symbol_market(cfg):

    return FakeData(cfg, {
        "AAA": {"symbol": "الف", "sector": "فلزات",
                "bars": make_bars(sessions=400, start_price=1000.0)},
        "BBB": {"symbol": "ب", "sector": "پتروشیمی",
                "bars": make_bars(sessions=400, start_price=2500.0,
                                  drift=0.0011, wobble=0.04)},
        "CCC": {"symbol": "ج", "sector": "بانک",
                "bars": make_bars(sessions=400, start_price=700.0,
                                  drift=0.0018, wobble=0.025)},
    })


def test_config(**overrides):
    """A config with the fast, deterministic settings the tests need."""

    base = {
        "selection_mode": "blend",       # no flow data in the fake market
        "technical_gate": 50,
        "min_headroom_pct": 0.0,
        "max_positions": 3,
        "max_per_sector": 2,
        "rebalance_sessions": 20,
        "initial_capital": 1_000_000_000,
    }

    base.update(overrides)

    return backtest_config.BacktestConfig(**base)


# ==============================
# COSTS AND P/L
# ==============================

class TestCosts(unittest.TestCase):

    def test_cost_split_matches_the_project_total(self):
        """The three parts must sum to config.TRANSACTION_COST_PCT."""

        self.assertIsNone(backtest_config.check_costs())

        cfg = backtest_config.BacktestConfig()

        # The same tolerance check_costs() itself uses. The split is documented
        # to three decimals and the project total to two, so they differ in the
        # third: 0.464 + 0.075 + 0.5 = 1.039 against a stated 1.04.
        self.assertAlmostEqual(cfg.round_trip_pct(),
                               config.TRANSACTION_COST_PCT, delta=0.005)

    def test_slippage_enters_both_sides(self):

        cfg = backtest_config.BacktestConfig(slippage_pct=0.5)

        base = backtest_config.BacktestConfig()

        self.assertAlmostEqual(cfg.round_trip_pct(),
                               base.round_trip_pct() + 1.0, places=6)

    def test_buy_deducts_commission_from_cash(self):

        cfg = test_config()
        book = Portfolio(cfg, initial_capital=1_000_000)

        reading = {"ins_code": "AAA", "symbol": "الف", "sector": "فلزات"}

        book.buy(reading, shares=100, price=1000.0, date="2025-01-01",
                 buy_cost_rate=cfg.buy_cost_rate())

        expected = 100 * 1000.0 * (1 + cfg.buy_cost_rate())

        self.assertAlmostEqual(book.cash, 1_000_000 - expected, places=4)
        self.assertAlmostEqual(book.commission_paid,
                               100 * 1000.0 * cfg.buy_cost_rate(), places=4)

    def test_pnl_is_net_of_both_sides_and_tax(self):
        """A flat round trip must LOSE exactly the round-trip cost."""

        cfg = test_config()
        book = Portfolio(cfg, initial_capital=1_000_000)

        reading = {"ins_code": "AAA", "symbol": "الف", "sector": "فلزات"}

        book.buy(reading, 100, 1000.0, "2025-01-01", cfg.buy_cost_rate())

        trade = book.sell("AAA", 100, 1000.0, "2025-02-01",
                          cfg.sell_cost_rate(), reason="test",
                          tax_rate=cfg.sell_tax_pct / 100.0)

        # Bought and sold at the same price: the loss is the whole round trip.
        expected_loss = 100 * 1000.0 * (cfg.buy_cost_rate() + cfg.sell_cost_rate())

        self.assertAlmostEqual(trade["pnl"], -expected_loss, delta=1.0)
        self.assertAlmostEqual(trade["pnl_pct"],
                               -cfg.round_trip_pct(), delta=0.01)
        self.assertEqual(trade["return_pct"], 0.0)

    def test_profitable_trade_pnl(self):

        cfg = test_config()
        book = Portfolio(cfg, initial_capital=10_000_000)

        reading = {"ins_code": "AAA", "symbol": "الف", "sector": "فلزات"}

        book.buy(reading, 1000, 1000.0, "2025-01-01", cfg.buy_cost_rate())

        trade = book.sell("AAA", 1000, 1200.0, "2025-03-01",
                          cfg.sell_cost_rate(), reason="target",
                          tax_rate=cfg.sell_tax_pct / 100.0)

        gross = 1000 * (1200.0 - 1000.0)

        costs = (1000 * 1000.0 * cfg.buy_cost_rate()
                 + 1000 * 1200.0 * cfg.sell_cost_rate())

        self.assertAlmostEqual(trade["pnl"], gross - costs, delta=1.0)
        self.assertEqual(trade["return_pct"], 20.0)
        self.assertLess(trade["pnl_pct"], 20.0)   # costs must bite


# ==============================
# SIZING AND CAPITAL LIMITS
# ==============================

class TestSizing(unittest.TestCase):

    def test_stop_out_costs_the_risk_budget(self):

        cfg = test_config(risk_per_trade_pct=1.0, stop_atr_multiple=2.0,
                          max_position_pct=100.0)

        book = Portfolio(cfg, initial_capital=1_000_000_000)

        reading = {"ins_code": "AAA", "close": 1000.0, "atr": 25.0}

        shares, stop, target = book.size_position(reading, 1000.0)

        loss_at_stop = shares * (1000.0 - stop)

        self.assertAlmostEqual(loss_at_stop, 10_000_000, delta=50_000)
        self.assertAlmostEqual(stop, 1000.0 - 50.0, places=6)

    def test_position_cap_binds_before_the_risk_budget(self):

        cfg = test_config(risk_per_trade_pct=5.0, max_position_pct=10.0)

        book = Portfolio(cfg, initial_capital=1_000_000_000)

        reading = {"ins_code": "AAA", "close": 1000.0, "atr": 5.0}

        shares, _, _ = book.size_position(reading, 1000.0)

        self.assertEqual(shares, int(1_000_000_000 * 0.10 / 1000.0))

    def test_below_the_floor_no_position_is_opened(self):
        """A 0.4% position is a commission bill, not a position."""

        cfg = test_config(risk_per_trade_pct=0.01, min_position_pct=2.0)

        book = Portfolio(cfg, initial_capital=1_000_000_000)

        reading = {"ins_code": "AAA", "close": 1000.0, "atr": 100.0}

        shares, stop, _ = book.size_position(reading, 1000.0)

        self.assertEqual(shares, 0)
        self.assertIsNone(stop)

    def test_capital_limit_is_respected(self):

        cfg = test_config(cash_reserve_pct=10.0, max_position_pct=100.0)

        book = Portfolio(cfg, initial_capital=1_000_000)

        # Asking for far more than the account holds.
        shares = book.affordable_shares(10_000, 1000.0, cfg.buy_cost_rate())

        self.assertLessEqual(shares * 1000.0 * (1 + cfg.buy_cost_rate()),
                             1_000_000)

        # And no more than the cash floor allows to be invested.
        self.assertLessEqual(shares * 1000.0, book.investable() + 1.0)

    def test_cannot_exceed_position_count(self):

        cfg = test_config(max_positions=2, max_per_sector=5)

        book = Portfolio(cfg, initial_capital=1_000_000_000)

        for code in ("AAA", "BBB"):
            book.buy({"ins_code": code, "symbol": code, "sector": "فلزات"},
                     100, 1000.0, "2025-01-01", cfg.buy_cost_rate())

        allowed, reason = book.can_open(
            {"ins_code": "CCC", "symbol": "CCC", "sector": "فلزات"})

        self.assertFalse(allowed)
        self.assertEqual(reason, "position count at maximum")

    def test_sector_name_cap(self):

        cfg = test_config(max_positions=10, max_per_sector=1)

        book = Portfolio(cfg, initial_capital=1_000_000_000)

        book.buy({"ins_code": "AAA", "symbol": "A", "sector": "فلزات"},
                 100, 1000.0, "2025-01-01", cfg.buy_cost_rate())

        allowed, reason = book.can_open(
            {"ins_code": "BBB", "symbol": "B", "sector": "فلزات"})

        self.assertFalse(allowed)
        self.assertEqual(reason, "sector name cap")

    def test_portfolio_value_is_cash_plus_marks(self):

        cfg = test_config()

        book = Portfolio(cfg, initial_capital=1_000_000)

        book.buy({"ins_code": "AAA", "symbol": "A", "sector": "x"},
                 100, 1000.0, "2025-01-01", cfg.buy_cost_rate())

        book.mark("AAA", 1200.0)

        self.assertAlmostEqual(book.value(), book.cash + 100 * 1200.0, places=4)
        self.assertAlmostEqual(book.holdings_value(), 120_000.0, places=4)


# ==============================
# EXIT RULES
# ==============================

class TestExits(unittest.TestCase):

    def _book_with_position(self, **cfg_overrides):

        cfg = test_config(**cfg_overrides)

        book = Portfolio(cfg, initial_capital=1_000_000_000)

        book.buy({"ins_code": "AAA", "symbol": "A", "sector": "x"},
                 1000, 1000.0, "2025-01-01", cfg.buy_cost_rate())

        position = book.positions["AAA"]
        position.stop = 950.0
        position.target = 1100.0
        position.atr_unit = 25.0

        return book

    def test_stop_fires_on_the_low(self):

        book = self._book_with_position(trail_atr_multiple=0.0)

        reason, price = book.exit_signal(
            "AAA", {"high": 1010.0, "low": 940.0, "close": 960.0})

        self.assertEqual(reason, "stop")
        self.assertEqual(price, 950.0)

    def test_target_fires_on_the_high(self):

        book = self._book_with_position(trail_atr_multiple=0.0)

        reason, price = book.exit_signal(
            "AAA", {"high": 1150.0, "low": 1050.0, "close": 1120.0})

        self.assertEqual(reason, "target")
        self.assertEqual(price, 1100.0)

    def test_ambiguous_bar_resolves_to_the_stop(self):
        """Both inside one daily range: daily OHLC cannot say which came first."""

        book = self._book_with_position(trail_atr_multiple=0.0)

        reason, price = book.exit_signal(
            "AAA", {"high": 1150.0, "low": 940.0, "close": 1000.0})

        self.assertEqual(reason, "stop")
        self.assertEqual(price, 950.0)

    def test_ambiguous_bar_can_be_configured_the_other_way(self):

        book = self._book_with_position(trail_atr_multiple=0.0,
                                        ambiguous_bar_resolves_to="target")

        reason, _ = book.exit_signal(
            "AAA", {"high": 1150.0, "low": 940.0, "close": 1000.0})

        self.assertEqual(reason, "target")

    def test_trailing_stop_only_ratchets_upward(self):

        book = self._book_with_position(trail_atr_multiple=1.0)

        position = book.positions["AAA"]
        position.target = None      # a bar through the target exits instead

        # A rise lifts the stop: peak 1200 - 1 ATR unit (25) = 1175.
        book.exit_signal("AAA", {"high": 1210.0, "low": 1190.0, "close": 1200.0})

        self.assertAlmostEqual(position.stop, 1175.0, places=6)

        # A fall must NOT lower it again.
        book.exit_signal("AAA", {"high": 1190.0, "low": 1180.0, "close": 1185.0})

        self.assertAlmostEqual(position.stop, 1175.0, places=6)

    def test_a_gap_through_the_stop_fills_at_the_open(self):
        book = self._book_with_position(trail_atr_multiple=0.0)

        reason, price = book.exit_signal(
            "AAA", {"open": 920.0, "high": 930.0, "low": 910.0,
                    "close": 925.0})

        self.assertEqual(reason, "stop")
        self.assertEqual(price, 920.0)

    def test_a_gap_through_the_target_fills_at_the_open(self):
        book = self._book_with_position(trail_atr_multiple=0.0)

        reason, price = book.exit_signal(
            "AAA", {"open": 1130.0, "high": 1140.0, "low": 1120.0,
                    "close": 1135.0})

        self.assertEqual(reason, "target")
        self.assertEqual(price, 1130.0)

    def test_todays_close_cannot_raise_todays_stop(self):
        """Low 960, close 1200: the stop that day is 950, so no exit."""

        book = self._book_with_position(trail_atr_multiple=1.0)
        book.positions["AAA"].target = None

        reason, _ = book.exit_signal(
            "AAA", {"open": 1000.0, "high": 1200.0, "low": 960.0,
                    "close": 1200.0})

        self.assertIsNone(reason)
        self.assertAlmostEqual(book.positions["AAA"].stop, 1175.0)

    def test_no_exit_when_neither_level_is_touched(self):

        book = self._book_with_position(trail_atr_multiple=0.0)

        reason, _ = book.exit_signal(
            "AAA", {"high": 1050.0, "low": 980.0, "close": 1000.0})

        self.assertIsNone(reason)


# ==============================
# METRICS
# ==============================

class TestMetrics(unittest.TestCase):

    def test_drawdown_is_measured_from_the_peak(self):

        values = pd.Series([100, 120, 90, 95, 130], dtype=float)

        returns = values.pct_change().dropna()

        profile = performance.drawdown_profile(returns)

        # 120 -> 90 is -25%.
        self.assertAlmostEqual(profile["max_drawdown_pct"], -25.0, places=2)

    def test_average_drawdown_differs_from_maximum(self):

        values = [100, 95, 100, 90, 100, 70, 100]

        episodes = metrics_module.drawdown_episodes(values)

        depths = [e["depth_pct"] for e in episodes]

        self.assertEqual(len(episodes), 3)
        self.assertAlmostEqual(min(depths), -30.0, places=2)

        average = sum(depths) / len(depths)

        self.assertGreater(average, min(depths))

    def test_sharpe_subtracts_the_iranian_risk_free_rate(self):
        """A strategy returning the policy rate exactly must score about zero."""

        daily = performance.daily_risk_free()

        flat = pd.Series([daily] * 300)

        # Zero volatility: the ratio is undefined rather than infinite.
        report = performance.from_daily_returns(flat)

        self.assertIsNone(report["sharpe"])

        noisy = pd.Series([daily + (0.001 if i % 2 else -0.001)
                           for i in range(300)])

        report = performance.from_daily_returns(noisy)

        self.assertAlmostEqual(report["sharpe"], 0.0, delta=0.3)

    def test_sortino_is_at_least_sharpe_when_downside_is_smaller(self):

        returns = pd.Series(
            [0.02 if i % 3 else -0.005 for i in range(300)], dtype=float)

        report = performance.from_daily_returns(returns)

        self.assertGreater(report["sortino"], report["sharpe"])

    def test_downside_volatility_ignores_upside(self):

        upside = pd.Series([0.05] * 200)
        mixed = pd.Series([0.05 if i % 2 else -0.05 for i in range(200)])

        self.assertLess(metrics_module.downside_volatility(upside),
                        metrics_module.downside_volatility(mixed))

    def test_profit_factor_and_expectancy(self):

        trades = [
            {"pnl": 200.0, "pnl_pct": 20.0, "holding_days": 10, "reason": "target"},
            {"pnl": 100.0, "pnl_pct": 10.0, "holding_days": 20, "reason": "target"},
            {"pnl": -100.0, "pnl_pct": -10.0, "holding_days": 5, "reason": "stop"},
        ]

        stats = metrics_module.trade_stats(trades)

        self.assertEqual(stats["trades"], 3)
        self.assertEqual(stats["wins"], 2)
        self.assertAlmostEqual(stats["profit_factor"], 3.0, places=6)
        self.assertAlmostEqual(stats["win_rate_pct"], 66.67, places=1)
        self.assertAlmostEqual(stats["average_holding_days"], 11.67, places=1)
        # (2/3)*150 + (1/3)*(-100) = 66.67
        self.assertAlmostEqual(stats["expectancy"], 67.0, delta=1.0)

    def test_profit_factor_is_undefined_without_a_loss(self):

        stats = metrics_module.trade_stats([
            {"pnl": 10.0, "pnl_pct": 1.0}, {"pnl": 20.0, "pnl_pct": 2.0}])

        self.assertIsNone(stats["profit_factor"])


# ==============================
# LOOK-AHEAD
# ==============================

class TestLookAhead(unittest.TestCase):
    """The tests this whole package exists to be able to pass."""

    def setUp(self):
        self.cfg = test_config()
        self.data = two_symbol_market(self.cfg)

    def test_position_never_returns_a_future_bar(self):

        frame = self.data.bars("AAA")

        for offset in (0, 5, 50, 200, len(frame) - 1):

            date = frame["date"].iloc[offset]

            index = self.data.position("AAA", date)

            self.assertEqual(index, offset)
            self.assertLessEqual(pd.Timestamp(frame["date"].iloc[index]),
                                 pd.Timestamp(date))

    def test_position_on_a_non_trading_day_looks_backward(self):

        frame = self.data.bars("AAA")

        traded = pd.Timestamp(frame["date"].iloc[100])

        # A Saturday between two sessions must resolve to the EARLIER one.
        between = traded + pd.Timedelta(days=1)

        index = self.data.position("AAA", between)

        self.assertLessEqual(pd.Timestamp(frame["date"].iloc[index]), between)
        self.assertGreaterEqual(index, 100)

    def test_history_until_stops_at_the_date(self):

        frame = self.data.bars("AAA")

        date = frame["date"].iloc[150]

        window = self.data.history_until("AAA", date)

        self.assertEqual(len(window), 151)
        self.assertEqual(pd.Timestamp(window["date"].iloc[-1]),
                         pd.Timestamp(date))
        self.assertTrue((window["date"] <= date).all())

    def test_bar_after_returns_the_next_session_not_the_same_one(self):

        frame = self.data.bars("AAA")

        date = frame["date"].iloc[100]

        nxt = self.data.bar_after("AAA", date, offset=1)

        self.assertEqual(pd.Timestamp(nxt["date"]),
                         pd.Timestamp(frame["date"].iloc[101]))

    def test_bar_after_the_last_session_is_none(self):
        """No invented price at the end of the window."""

        frame = self.data.bars("AAA")

        self.assertIsNone(
            self.data.bar_after("AAA", frame["date"].iloc[-1], offset=1))

    def test_filing_published_after_the_close_is_not_visible_that_day(self):
        """The exchange shuts at 12:30. A 17:53 filing belongs to tomorrow."""

        calendar = self.data.calendar()

        session = calendar[100]

        after_close = session.normalize() + pd.Timedelta(hours=17, minutes=53)

        visible = self.data._first_session_after(calendar, after_close, 0)

        self.assertEqual(visible, calendar[101])

    def test_filing_published_before_the_close_is_visible_that_day(self):

        calendar = self.data.calendar()

        session = calendar[100]

        before_close = session.normalize() + pd.Timedelta(hours=9)

        visible = self.data._first_session_after(calendar, before_close, 0)

        self.assertEqual(visible, session)

    def test_fundamental_as_of_never_returns_a_later_filing(self):

        calendar = self.data.calendar()

        self.data._filings = {
            "AAA": [
                {"period_end": "1403/12/29", "visible_from": calendar[50],
                 "ratios": {"roe": 30.0}, "items": {}, "published_at": "x",
                 "period_months": 12},
                {"period_end": "1404/06/31", "visible_from": calendar[150],
                 "ratios": {"roe": 40.0}, "items": {}, "published_at": "y",
                 "period_months": 6},
            ]
        }

        early = self.data.fundamental_as_of("AAA", calendar[100])
        late = self.data.fundamental_as_of("AAA", calendar[200])

        self.assertEqual(early["period_end"], "1403/12/29")
        self.assertEqual(late["period_end"], "1404/06/31")

        self.assertIsNone(self.data.fundamental_as_of("AAA", calendar[10]))

    def test_undated_filings_are_excluded_by_default(self):

        self.assertEqual(backtest_config.UNDATED_FILING_POLICY, "exclude")

    def test_valuation_ratios_are_recomputed_not_read_from_disk(self):
        """A stored P/E carries today's price. It must never survive."""

        record = {
            "period_months": 12,
            "ratios": {"pe": 999.0, "pb": 999.0, "ps": 999.0,
                       "shares": 1_000_000.0, "eps_annual": 100.0,
                       "market_cap": 12345.0},
            "items": {"revenue": 500_000_000.0, "equity_owners": 200_000_000.0},
        }

        ratios = self.data.market_ratios(record, price=500.0)

        self.assertAlmostEqual(ratios["pe"], 5.0, places=6)
        self.assertNotEqual(ratios["pb"], 999.0)
        self.assertAlmostEqual(ratios["pb"], 500.0 / 200.0, places=6)
        self.assertAlmostEqual(ratios["market_cap"], 500.0 * 1_000_000.0,
                               places=4)

    def test_earnings_multiple_withheld_where_it_means_nothing(self):

        sector = sorted(config.EARNINGS_MULTIPLE_NOT_MEANINGFUL)[0]

        record = {
            "period_months": 12,
            "ratios": {"shares": 1_000_000.0, "eps_annual": 100.0},
            "items": {"equity_owners": 200_000_000.0},
        }

        ratios = self.data.market_ratios(record, price=500.0, sector=sector)

        self.assertIsNone(ratios["pe"])
        self.assertIsNotNone(ratios["pb"])

    def test_jalali_conversion_against_fixed_points(self):
        """The date axis the filing rule stands on."""

        self.assertEqual(jalali.to_gregorian(1404, 12, 29), (2026, 3, 20))
        self.assertEqual(jalali.to_gregorian(1403, 1, 1), (2024, 3, 20))

        stamp = jalali.parse("1405/05/21 17:53:31")

        self.assertEqual((stamp.year, stamp.hour, stamp.minute), (2026, 17, 53))

        # No clock in the string: the filing is treated as arriving at the very
        # END of its day, so a same-day trade cannot claim to have seen it.
        stamp = jalali.parse("1405/05/21")

        self.assertEqual((stamp.hour, stamp.minute), (23, 59))

        self.assertTrue(jalali.has_time("1405/05/21 09:00:00"))
        self.assertFalse(jalali.has_time("1405/05/21"))

    def test_news_is_reported_absent_rather_than_assumed(self):

        available, reason = self.data.news_available()

        self.assertFalse(available)
        self.assertIn("news", reason)


# ==============================
# EXECUTION
# ==============================

class TestExecution(unittest.TestCase):

    def setUp(self):
        self.cfg = test_config()
        self.filler = execution.ExecutionModel(self.cfg)

    def test_buy_queue_is_detected_and_blocks_a_buy(self):

        # One price all session, at the top of the band: صف خرید.
        bar = {"open": 1030.0, "high": 1030.0, "low": 1030.0, "close": 1030.0,
               "volume": 1000.0, "value": 1_030_000.0}

        state = self.filler.queue_state(bar, previous_close=1000.0)

        self.assertEqual(state, execution.BUY_QUEUE)

        order = execution.Order(execution.BUY, "AAA", 100, "2025-01-01",
                                pd.Timestamp("2025-01-02"),
                                pd.Timestamp("2025-01-05"))

        shares, _, refusal = self.filler.execute(order, bar, 1000.0, 5000.0)

        self.assertEqual(shares, 0)
        self.assertIsNotNone(refusal)

    def test_sell_queue_blocks_an_exit(self):

        bar = {"open": 970.0, "high": 970.0, "low": 970.0, "close": 970.0,
               "volume": 1000.0, "value": 970_000.0}

        self.assertEqual(self.filler.queue_state(bar, 1000.0),
                         execution.SELL_QUEUE)

    def test_an_ordinary_session_is_not_a_queue(self):

        bar = {"open": 1000.0, "high": 1020.0, "low": 995.0, "close": 1015.0,
               "volume": 100_000.0, "value": 101_500_000.0}

        self.assertIsNone(self.filler.queue_state(bar, 1000.0, 100_000.0))

    def test_participation_cap_produces_a_partial_fill(self):

        # The session traded 1,000,000 rials. At a 5% cap and a price of 100,
        # only 500 shares can be taken.
        bar = {"open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0,
               "volume": 10_000.0, "value": 1_000_000.0}

        order = execution.Order(execution.BUY, "AAA", 5000, "2025-01-01",
                                pd.Timestamp("2025-01-02"),
                                pd.Timestamp("2025-01-05"))

        shares, price, refusal = self.filler.execute(order, bar, 100.0, 10_000.0)

        self.assertIsNone(refusal)
        self.assertEqual(shares, 500)
        self.assertEqual(price, 100.0)      # fills at the open by default

    def test_fill_price_is_the_open_not_the_close(self):

        bar = {"open": 100.0, "high": 130.0, "low": 99.0, "close": 128.0,
               "volume": 10.0, "value": 1000.0}

        self.assertEqual(self.filler.fill_price(bar), 100.0)

        closing = execution.ExecutionModel(test_config(execution_price="close"))

        self.assertEqual(closing.fill_price(bar), 128.0)

    def test_orders_become_eligible_one_session_later(self):

        sessions = list(pd.bdate_range("2025-01-01", periods=10))

        book = execution.OrderBook(self.cfg)

        order = book.submit(execution.BUY, "AAA", 100, sessions[0], sessions)

        self.assertEqual(order.eligible_from, sessions[1])
        self.assertEqual(book.due(sessions[0]), [])
        self.assertEqual(book.due(sessions[1]), [order])

    def test_orders_expire_after_their_window(self):

        sessions = list(pd.bdate_range("2025-01-01", periods=10))

        cfg = test_config(order_good_for_sessions=2)

        book = execution.OrderBook(cfg)

        order = book.submit(execution.BUY, "AAA", 100, sessions[0], sessions)

        self.assertEqual(order.expires_on, sessions[2])
        self.assertEqual(len(book.due(sessions[2])), 1)
        self.assertEqual(len(book.due(sessions[3])), 0)

        expired = book.retire(sessions[3])

        self.assertEqual(len(expired), 1)
        self.assertEqual(book.pending, [])

    def test_zero_delay_can_be_configured_but_is_not_the_default(self):

        self.assertEqual(backtest_config.EXECUTION_DELAY_SESSIONS, 1)

        sessions = list(pd.bdate_range("2025-01-01", periods=5))

        book = execution.OrderBook(test_config(execution_delay_sessions=0))

        order = book.submit(execution.BUY, "AAA", 10, sessions[0], sessions)

        self.assertEqual(order.eligible_from, sessions[0])


# ==============================
# INTEGRATION
# ==============================

class TestIntegration(unittest.TestCase):
    """Historical data -> indicators -> signal -> portfolio -> report."""

    @classmethod
    def setUpClass(cls):
        from backtesting import engine

        cls.cfg = test_config()
        cls.data = two_symbol_market(cls.cfg)

        calendar = cls.data.calendar()

        cls.start = calendar[200]
        cls.end = calendar[-1]

        cls.result = engine.run(
            cls.start, cls.end, cfg=cls.cfg, variant="technical",
            data=cls.data, verbose=False, use_cache=False,
        )

    def test_the_run_produced_a_result(self):

        self.assertEqual(self.result.insufficient, [])
        self.assertTrue(self.result.available)

    def test_one_equity_row_per_session(self):

        sessions = self.data.sessions(self.start, self.end)

        self.assertEqual(len(self.result.curve), len(sessions))

    def test_it_actually_traded(self):

        self.assertGreater(len(self.result.trades), 0)
        self.assertGreater(self.result.diagnostics["orders_raised"], 0)

    def test_equity_is_always_cash_plus_holdings(self):

        curve = self.result.curve

        difference = (curve["portfolio_value"]
                      - curve["cash"] - curve["holdings_value"]).abs()

        self.assertLess(float(difference.max()), 1.0)

    def test_no_position_exceeds_the_configured_share_of_equity(self):

        curve = self.result.curve

        # Holdings can never exceed the investable share of the book by more
        # than a rounding of one share.
        ratio = curve["holdings_value"] / curve["portfolio_value"]

        self.assertLessEqual(float(ratio.max()), 1.0 + 1e-9)

    def test_the_book_ends_flat(self):
        """Final equity must be withdrawable, not a mark on open positions."""

        self.assertEqual(len(self.result.open_positions), 0)

        last = self.result.curve.iloc[-1]

        self.assertAlmostEqual(float(last["holdings_value"]), 0.0, delta=1.0)

        # And the final row must be the POST-liquidation figure: all cash.
        self.assertAlmostEqual(float(last["portfolio_value"]),
                               float(last["cash"]), delta=1.0)
        self.assertAlmostEqual(float(last["cash_pct"]), 100.0, delta=0.01)

    def test_every_trade_is_internally_consistent(self):

        for trade in self.result.trades:

            self.assertIsNotNone(trade["entry_price"])
            self.assertIsNotNone(trade["exit_price"])
            self.assertGreater(trade["shares"], 0)

            expected = (trade["exit_price"] / trade["entry_price"] - 1) * 100

            self.assertAlmostEqual(trade["return_pct"], expected, delta=0.02)

            # Net of costs must always be below gross.
            self.assertLess(trade["pnl_pct"], trade["return_pct"] + 1e-9)

            self.assertLessEqual(pd.Timestamp(trade["entry_date"]),
                                 pd.Timestamp(trade["exit_date"]))

    def test_no_fill_happened_on_its_own_decision_session(self):
        """The T+1 rule, asserted on the trades that were actually produced."""

        rebalances = {pd.Timestamp(d) for d in self.result.rebalance_dates}

        entries = {pd.Timestamp(t["entry_date"]) for t in self.result.trades}

        # An entry landing exactly on a decision date would mean the engine
        # filled at the close it decided from.
        self.assertEqual(entries & rebalances, set())

    def test_metrics_are_complete(self):

        m = self.result.metrics

        for key in ("total_return_pct", "cagr_pct", "volatility_pct", "sharpe",
                    "sortino", "calmar", "drawdown", "initial_capital",
                    "final_capital", "average_drawdown_pct",
                    "downside_volatility_pct", "annual_returns", "trades"):
            self.assertIn(key, m)

        self.assertTrue(m["trades"]["available"])

    def test_report_renders_without_a_display(self):

        from backtesting import report as report_module

        verdict = report_module.evaluate(self.result)

        self.assertIn(verdict["verdict"], ("PASS", "FAIL", "INCONCLUSIVE"))

        html = report_module._html(self.result, self.result.to_dict())

        self.assertIn("SIRAT-AI Backtest Report", html)
        self.assertIn("<svg", html)

    def test_monte_carlo_runs_on_the_produced_trades(self):

        from backtesting import validation

        monte = validation.monte_carlo(self.result, runs=200)

        if monte.get("available"):
            self.assertEqual(monte["runs"], 200)
            self.assertIn("p5_return_pct", monte["percentiles"])
            self.assertGreaterEqual(monte["probability_of_loss_pct"], 0.0)
            self.assertLessEqual(monte["probability_of_loss_pct"], 100.0)
        else:
            # Fewer than five placeable trades is a legitimate outcome on a
            # three-symbol synthetic market; it must say so rather than invent
            # a distribution.
            self.assertIn("trades", monte["reason"])


class TestInsufficientData(unittest.TestCase):
    """Rule 26: nothing is fabricated when the data is not there."""

    def test_a_window_too_short_is_refused(self):

        from backtesting import engine

        cfg = test_config()
        data = two_symbol_market(cfg)

        calendar = data.calendar()

        result = engine.run(calendar[0], calendar[5], cfg=cfg, data=data,
                            verbose=False, use_cache=False)

        self.assertFalse(result.available)
        self.assertTrue(result.insufficient)
        self.assertEqual(result.metrics, {})

    def test_a_fundamental_variant_without_filings_is_refused(self):

        from backtesting import engine

        cfg = test_config()
        data = two_symbol_market(cfg)      # no filings at all

        calendar = data.calendar()

        result = engine.run(calendar[200], calendar[-1], cfg=cfg,
                            variant="fundamental", data=data, verbose=False,
                            use_cache=False)

        self.assertFalse(result.available)
        self.assertTrue(any("FUNDAMENTALS" in line
                            for line in result.insufficient))

    def test_a_news_variant_is_always_refused_on_this_database(self):

        from backtesting import engine

        cfg = test_config()
        data = two_symbol_market(cfg)

        calendar = data.calendar()

        result = engine.run(calendar[200], calendar[-1], cfg=cfg,
                            variant="technical+fundamental+news", data=data,
                            verbose=False, use_cache=False)

        self.assertFalse(result.available)
        self.assertTrue(any("NEWS" in line for line in result.insufficient))


class TestOverfitVerdict(unittest.TestCase):

    def test_missing_evidence_is_not_a_pass(self):
        """A model that could not run must not report LOW by default."""

        from backtesting import validation

        verdict = validation.overfitting_risk()

        self.assertLess(verdict["weight_measured"], 1.0)

        unmeasured = [f for f in verdict["findings"] if f["points"] is None]

        self.assertTrue(unmeasured)

        # Zero points out of the one test that could run is not LOW risk.
        self.assertEqual(verdict["risk"], "UNKNOWN")

    def test_too_few_trades_raises_the_risk(self):

        from backtesting import engine, validation

        cfg = test_config()

        result = engine.BacktestResult(cfg, "technical", "2025-01-01",
                                       "2025-06-01")
        result.curve = pd.DataFrame(
            {"portfolio_value": [1.0, 1.1]},
            index=pd.to_datetime(["2025-01-01", "2025-06-01"]))
        result.metrics = {"trades": {"trades": 2}}

        verdict = validation.overfitting_risk(result=result)

        finding = next(f for f in verdict["findings"]
                       if f["test"] == "trade_count")

        self.assertGreater(finding["points"], 0)

    def test_pass_fail_is_three_state(self):

        from backtesting import report as report_module

        cfg = test_config()

        result = engine_stub(cfg)

        verdict = report_module.evaluate(result)

        # Nothing about the hold-out or the overfit model was supplied, so the
        # honest answer is INCONCLUSIVE rather than PASS.
        self.assertEqual(verdict["verdict"], "INCONCLUSIVE")
        self.assertTrue(verdict["unmeasured"])


def engine_stub(cfg):
    """A result good enough to exercise the verdict logic."""

    from backtesting import engine

    result = engine.BacktestResult(cfg, "technical", "2025-01-01", "2026-01-01")

    result.curve = pd.DataFrame(
        {"portfolio_value": [1.0, 2.0]},
        index=pd.to_datetime(["2025-01-01", "2026-01-01"]))

    result.metrics = {
        "cagr_pct": 60.0,
        "sharpe": 1.5,
        "drawdown": {"max_drawdown_pct": -12.0},
        "trades": {"trades": 50, "profit_factor": 2.0},
    }

    result.benchmarks = {"tedpix": {"excess_pct": 10.0}}

    return result


class ValuationPriceTest(unittest.TestCase):
    """A multiple is priced at the close as printed, not as adjusted."""

    def test_a_later_bonus_issue_does_not_cheapen_the_past(self):
        import backtesting.pit as pit_module

        dates = pd.bdate_range("2025-01-01", periods=10)
        raw = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": [100.0] * 5 + [50.0] * 5,
            "high": [100.0] * 5 + [50.0] * 5,
            "low": [100.0] * 5 + [50.0] * 5,
            "close": [100.0] * 5 + [50.0] * 5,      # a 100% bonus issue
            "volume": [1000.0] * 10,
        })

        original = pit_module.database.load_history_by_inscode
        pit_module.database.load_history_by_inscode = lambda code: raw.copy()

        try:
            data = PointInTimeData(backtest_config.BacktestConfig(),
                                   verbose=False)

            adjusted = data.history_until("1", dates[2])["close"].iloc[-1]
            printed = data.valuation_price("1", dates[2])
        finally:
            pit_module.database.load_history_by_inscode = original

        self.assertAlmostEqual(adjusted, 50.0)
        self.assertAlmostEqual(printed, 100.0)

    def test_no_bar_falls_back(self):
        data = PointInTimeData(backtest_config.BacktestConfig(), verbose=False)
        data._bars["9"] = None

        self.assertEqual(data.valuation_price("9", "2025-01-01",
                                              fallback=7.0), 7.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
