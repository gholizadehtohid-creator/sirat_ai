"""Risk and performance maths, checked against cases with known answers.

The recurring theme: a formula written for a market with a ~0% risk-free rate
gives the wrong verdict in a market paying 23%. Several tests below exist only
to pin that down, because the failure is not an exception -- it is a Sharpe
ratio that flatters a strategy which lost to a bank deposit.
"""

import math
import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import performance
import risk


class TestRiskFreeHandling(unittest.TestCase):

    def test_daily_rate_compounds_rather_than_divides(self):
        daily = performance.daily_risk_free()

        compounded = (1 + daily) ** config.TRADING_DAYS_PER_YEAR - 1

        self.assertAlmostEqual(compounded, config.RISK_FREE_RATE, places=9)

        naive = config.RISK_FREE_RATE / config.TRADING_DAYS_PER_YEAR
        self.assertLess(daily, naive,
                        "compounding must give a smaller daily rate than division")

    def test_strategy_matching_the_risk_free_rate_scores_about_zero(self):
        """Earning exactly the policy rate is zero excess return, not success."""

        daily = performance.daily_risk_free()
        returns = pd.Series([daily] * 500)

        report = performance.from_daily_returns(returns)

        self.assertAlmostEqual(report["cagr_pct"], config.RISK_FREE_RATE * 100,
                               delta=0.5)
        self.assertAlmostEqual(report["excess_over_cash_pct"], 0.0, delta=0.5)

    def test_a_thirty_percent_year_is_barely_ahead_of_cash(self):
        """The headline case: +30% looks excellent and is nearly break-even."""

        bars = config.TRADING_DAYS_PER_YEAR
        daily = (1.30) ** (1 / bars) - 1

        report = performance.from_daily_returns(pd.Series([daily] * bars))

        self.assertAlmostEqual(report["cagr_pct"], 30.0, delta=0.5)
        self.assertLess(report["excess_over_cash_pct"], 8.0)
        self.assertGreater(report["excess_over_cash_pct"], 6.0)


class TestPerformanceMetrics(unittest.TestCase):

    def setUp(self):
        rng = np.random.default_rng(11)
        self.returns = pd.Series(rng.normal(0.0012, 0.011, 600))

    def test_report_is_complete(self):
        report = performance.from_daily_returns(self.returns)

        for key in ("total_return_pct", "cagr_pct", "volatility_pct",
                    "sharpe", "sortino", "calmar", "drawdown"):
            self.assertIn(key, report)

        self.assertTrue(report["available"])

    def test_sortino_is_at_least_sharpe_for_symmetric_returns(self):
        report = performance.from_daily_returns(self.returns)

        self.assertIsNotNone(report["sharpe"])
        self.assertIsNotNone(report["sortino"])
        self.assertGreater(report["sortino"], report["sharpe"])

    def test_drawdown_on_a_known_path(self):
        # +10%, -20%, +5%: the trough is 12% below the 1.10 peak.
        returns = pd.Series([0.10, -0.20, 0.05])

        profile = performance.drawdown_profile(returns)

        self.assertAlmostEqual(profile["max_drawdown_pct"], -20.0, places=6)

    def test_drawdown_recovery_is_detected(self):
        returns = pd.Series([0.0, -0.10, 0.0, 0.15])

        profile = performance.drawdown_profile(returns)

        self.assertTrue(profile["recovered"])

    def test_unrecovered_drawdown_says_so(self):
        returns = pd.Series([0.0, -0.30, 0.01, 0.01])

        profile = performance.drawdown_profile(returns)

        self.assertFalse(profile["recovered"])
        self.assertIsNone(profile["recovery_date"])

    def test_too_short_a_series_is_refused_not_guessed(self):
        report = performance.from_daily_returns(pd.Series([0.01]))

        self.assertFalse(report["available"])
        self.assertIn("reason", report)

    def test_zero_volatility_yields_no_sharpe_rather_than_infinity(self):
        report = performance.from_daily_returns(pd.Series([0.0] * 300))

        self.assertIsNone(report["sharpe"],
                          "a zero denominator is undefined, not infinite")


class TestBenchmark(unittest.TestCase):

    def test_beating_the_market_in_absolute_terms_can_still_be_underperformance(self):
        bars = 300
        market = pd.Series([0.0020] * bars)
        strategy = pd.Series([0.0015] * bars)

        report = performance.from_daily_returns(strategy, benchmark=market)

        bench = report["benchmark"]

        self.assertTrue(bench["available"])
        self.assertGreater(bench["strategy_return_pct"], 0)
        self.assertLess(bench["excess_return_pct"], 0,
                        "a positive return below the index is underperformance")

    def test_beta_of_an_identical_series_is_one(self):
        rng = np.random.default_rng(5)
        market = pd.Series(rng.normal(0.001, 0.01, 400))

        report = performance.from_daily_returns(market, benchmark=market)

        self.assertAlmostEqual(report["benchmark"]["beta"], 1.0, places=6)
        self.assertAlmostEqual(report["benchmark"]["correlation"], 1.0, places=6)

    def test_short_overlap_is_refused(self):
        report = performance.from_daily_returns(
            pd.Series([0.01] * 100), benchmark=pd.Series([0.01] * 5)
        )

        self.assertFalse(report["benchmark"]["available"])


class TestTradeStatistics(unittest.TestCase):

    def test_profit_factor_and_expectancy(self):
        trades = [{"return": 10.0}, {"return": 10.0},
                  {"return": -5.0}, {"return": -5.0}]

        stats = performance.from_trades(trades)

        self.assertEqual(stats["win_rate_pct"], 50.0)
        self.assertEqual(stats["profit_factor"], 2.0)
        self.assertAlmostEqual(stats["expectancy_pct"], 2.5, places=4)

    def test_no_losses_leaves_profit_factor_undefined(self):
        stats = performance.from_trades([{"return": 5.0}, {"return": 3.0}])

        self.assertIsNone(stats["profit_factor"],
                          "no losing trade means undefined, not infinite profit")

    def test_streaks(self):
        trades = [{"return": r} for r in (1, 2, 3, -1, -2, 4, 5)]

        stats = performance.from_trades(trades)

        self.assertEqual(stats["max_consecutive_wins"], 3)
        self.assertEqual(stats["max_consecutive_losses"], 2)

    def test_no_trades_is_reported_not_zeroed(self):
        stats = performance.from_trades([])

        self.assertFalse(stats["available"])


class TestValueAtRisk(unittest.TestCase):

    def setUp(self):
        rng = np.random.default_rng(3)
        self.returns = pd.Series(rng.normal(0.0, 0.02, 1000))

    def test_var_is_negative_and_cvar_is_worse(self):
        report = risk.value_at_risk(self.returns)

        self.assertLess(report["historical_pct"], 0)
        self.assertLessEqual(report["conditional_pct"], report["historical_pct"],
                             "CVaR is the mean beyond VaR and cannot be milder")

    def test_worst_day_is_at_least_as_bad_as_cvar(self):
        report = risk.value_at_risk(self.returns)

        self.assertLessEqual(report["worst_day_pct"], report["conditional_pct"])

    def test_rial_amounts_scale_with_equity(self):
        a = risk.value_at_risk(self.returns, equity=1_000_000_000)
        b = risk.value_at_risk(self.returns, equity=2_000_000_000)

        self.assertAlmostEqual(b["historical_rials"] / a["historical_rials"],
                               2.0, places=6)

    def test_short_series_is_refused(self):
        self.assertIsNone(risk.value_at_risk(pd.Series([0.01] * 5)))


class TestPortfolioVolatility(unittest.TestCase):

    def test_diversification_ratio_exceeds_one_for_uncorrelated_names(self):
        rng = np.random.default_rng(21)

        matrix = pd.DataFrame({
            "A": rng.normal(0, 0.02, 500),
            "B": rng.normal(0, 0.02, 500),
            "C": rng.normal(0, 0.02, 500),
        })

        weights = {"A": 1.0, "B": 1.0, "C": 1.0}

        report = risk.portfolio_volatility(matrix, weights)

        self.assertGreater(report["diversification_ratio"], 1.3,
                           "three independent names should cut volatility")

    def test_identical_names_give_no_diversification(self):
        rng = np.random.default_rng(22)
        series = rng.normal(0, 0.02, 500)

        matrix = pd.DataFrame({"A": series, "B": series})

        report = risk.portfolio_volatility(matrix, {"A": 1.0, "B": 1.0})

        self.assertAlmostEqual(report["diversification_ratio"], 1.0, places=6)

    def test_volatility_is_annualised_with_tse_session_count(self):
        matrix = pd.DataFrame({"A": [0.01, -0.01] * 200})

        report = risk.portfolio_volatility(matrix, {"A": 1.0})

        daily = matrix["A"].std(ddof=1)
        expected = daily * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100

        self.assertAlmostEqual(report["annualised_pct"], round(expected, 2),
                               places=2)


class TestConcentration(unittest.TestCase):

    def test_equal_weights_give_full_effective_count(self):
        report = risk.concentration({"A": 1, "B": 1, "C": 1, "D": 1})

        self.assertAlmostEqual(report["effective_positions"], 4.0, places=2)

    def test_one_dominant_name_collapses_the_effective_count(self):
        report = risk.concentration({"A": 90, "B": 3, "C": 3, "D": 4})

        self.assertLess(report["effective_positions"], 1.5,
                        "ten names with one holding 90% is not ten positions")
        self.assertEqual(report["positions"], 4)

    def test_empty_weights_return_none(self):
        self.assertIsNone(risk.concentration({}))


class TestSectorExposure(unittest.TestCase):

    def test_percentages_sum_to_one_hundred(self):
        positions = [
            {"sector_name": "بانک", "position_value": 300},
            {"sector_name": "فلزات", "position_value": 500},
            {"sector_name": "بانک", "position_value": 200},
        ]

        rows = risk.sector_exposure(positions)

        self.assertAlmostEqual(sum(r["pct_of_invested"] for r in rows), 100.0,
                               places=4)

        banks = next(r for r in rows if r["sector_name"] == "بانک")
        self.assertAlmostEqual(banks["pct_of_invested"], 50.0, places=4)

    def test_missing_sector_is_labelled_not_dropped(self):
        rows = risk.sector_exposure([{"position_value": 100}])

        self.assertEqual(rows[0]["sector_name"], "بدون صنعت")


class TestRiskAnalyseGuards(unittest.TestCase):

    def test_no_positions_is_reported_not_crashed(self):
        report = risk.analyse([])

        self.assertFalse(report["available"])
        self.assertIn("reason", report)
        self.assertEqual(report["sector_exposure"], [])


class TestRiskAnalyseVarBase(unittest.TestCase):
    """The rial VaR is a loss on the basket, not on the whole account."""

    def test_rial_var_is_struck_on_the_basket_value_not_on_equity(self):
        rng = np.random.default_rng(7)
        matrix = pd.DataFrame({
            "A": rng.normal(0.0, 0.02, 300),
            "B": rng.normal(0.0, 0.02, 300),
        })

        # Half the account invested; C has too little history and is dropped,
        # so it is in neither the return series nor the rial base.
        positions = [
            {"ins_code": "a", "symbol": "A", "position_value": 300_000_000},
            {"ins_code": "b", "symbol": "B", "position_value": 200_000_000},
            {"ins_code": "c", "symbol": "C", "position_value": 100_000_000},
        ]

        with mock.patch.object(risk, "returns_matrix",
                               return_value=(matrix, ["C"])), \
                mock.patch.object(risk, "benchmark_returns",
                                  return_value=pd.Series(dtype=float)):
            report = risk.analyse(positions, equity=1_000_000_000)

        basket = matrix["A"] * 0.6 + matrix["B"] * 0.4
        expected = risk.value_at_risk(basket, equity=500_000_000)

        self.assertEqual(report["var"]["historical_rials"],
                         expected["historical_rials"])
        self.assertEqual(report["var"]["conditional_rials"],
                         expected["conditional_rials"])

        # The account figure charged the 400M of cash and the unmeasured name
        # the basket's loss rate too.
        on_equity = risk.value_at_risk(basket, equity=1_000_000_000)
        self.assertAlmostEqual(on_equity["historical_rials"]
                               / report["var"]["historical_rials"], 2.0,
                               places=6)



class TestOwnBandAndProxies(unittest.TestCase):
    """The full test, 2026-11-12: a flat 3.6% dropped a gold fund's real
    ±10% days, and funds without bars left the plan's own book measured on
    its gold alone."""

    def bars(self, moves):
        closes = [100.0]
        for m in moves:
            closes.append(closes[-1] * (1 + m))
        return pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(closes)),
                             "close": closes, "volume": [1000] * len(closes)})

    def test_a_gold_funds_ordinary_day_is_kept_and_a_split_is_not(self):
        moves = [0.01, -0.01] * 30 + [-0.098, 0.5]
        with mock.patch.object(risk.database, "load_history_by_inscode",
                               return_value=self.bars(moves)), \
             mock.patch("fundamental_outlook.price_bands",
                        return_value={"g": (1000.0, 1100.0, 900.0)}):
            frame, dropped = risk.returns_matrix(["g"])
        self.assertEqual(dropped, [])
        self.assertAlmostEqual(frame["g"].min(), -0.098, places=6)
        self.assertLess(frame["g"].max(), 0.5)

    def test_a_flat_threshold_still_guards_a_name_without_a_band(self):
        moves = [0.01, -0.01] * 30 + [-0.098]
        with mock.patch.object(risk.database, "load_history_by_inscode",
                               return_value=self.bars(moves)), \
             mock.patch("fundamental_outlook.price_bands", return_value={}):
            frame, _ = risk.returns_matrix(["s"])
        self.assertGreater(frame["s"].min(), -0.05)

    def test_a_fund_without_bars_stands_in_on_its_proxy(self):
        proxy = pd.Series([0.01, -0.02] * 30,
                          index=pd.date_range("2026-01-02", periods=60))
        with mock.patch.object(risk.database, "load_history_by_inscode",
                               return_value=pd.DataFrame()), \
             mock.patch("fundamental_outlook.price_bands", return_value={}):
            frame, dropped = risk.returns_matrix(["f", "x"], proxies={"f": proxy})
        self.assertEqual(dropped, ["x"])
        self.assertEqual(list(frame.columns), ["f"])

    def test_the_measured_share_of_the_book_is_reported(self):
        frame = pd.DataFrame({"الف": [0.01, 0.02]})
        positions = [{"symbol": "الف", "position_value": 75.0},
                     {"symbol": "ب", "position_value": 25.0}]
        self.assertEqual(risk._measured_pct(positions, frame), 75.0)
        self.assertIsNone(risk._measured_pct(positions, pd.DataFrame()))


if __name__ == "__main__":
    unittest.main()
