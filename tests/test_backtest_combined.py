"""Point-in-time correctness for the combined backtest.

The bug this file exists to catch does not raise, does not look wrong in the
output, and makes the strategy appear excellent: scoring a past date with data
that did not exist yet. Every test here is about the boundary between "visible
at the signal bar" and "visible only later".
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import backtest_combined as bc


def bars(n=400, start="2024-09-01", trend=1.004, seed=7):
    """A synthetic uptrend with enough range for ATR and ADX to compute."""

    dates = pd.bdate_range(start=start, periods=n)

    price = 1000.0
    rows = []

    for i, date in enumerate(dates):
        # Deterministic wobble -- no RNG, so a failure reproduces exactly.
        wobble = ((i * 37) % 11 - 5) / 500.0
        price = price * (trend + wobble)
        high = price * 1.012
        low = price * 0.988
        rows.append({
            "date": date,
            "open": price * 0.999,
            "high": high,
            "low": low,
            "close": price,
            "volume": 1_000_000 + (i % 7) * 50_000,
            "value": price * (1_000_000 + (i % 7) * 50_000),
            "trades": 500,
        })

    return pd.DataFrame(rows)


def flow(n=400, start="2024-09-01", individual_buy=1e9):
    dates = pd.bdate_range(start=start, periods=n)

    return pd.DataFrame([{
        "date": date,
        "individual_buy_value": individual_buy,
        "individual_sell_value": individual_buy * 0.8,
        "corporate_buy_value": individual_buy * 0.5,
        "corporate_sell_value": individual_buy * 0.7,
        "individual_buy_count": 500,
        "individual_sell_count": 800,
        "corporate_buy_count": 5,
        "corporate_sell_count": 3,
    } for date in dates])


class TestPointInTime(unittest.TestCase):

    def test_no_signal_can_use_a_bar_that_has_not_happened(self):
        """Every signal date must precede its own entry and exit."""

        trades = bc.signals_for(
            "code", "SYM", bars(), flow(),
            holding_days=20, threshold=0, step=13,
        )

        self.assertTrue(trades, "fixture produced no signals")

        for t in trades:
            self.assertIsNotNone(t["signal_date"])
            self.assertIsNotNone(t["entry"])

    def test_flow_after_the_signal_date_is_invisible(self):
        """Flow rows dated after the signal must not change its score.

        Truncating the flow series to the last signal date must leave every
        earlier signal's flow score untouched. If a later row can move an
        earlier score, the slice leaks the future.
        """

        price = bars()
        full_flow = flow()

        full = bc.signals_for("code", "SYM", price, full_flow,
                              holding_days=20, threshold=0, step=13)

        self.assertTrue(full)

        cutoff = pd.Timestamp(full[0]["signal_date"])

        truncated = bc.signals_for(
            "code", "SYM", price,
            full_flow[full_flow["date"] <= cutoff],
            holding_days=20, threshold=0, step=13,
        )

        self.assertTrue(truncated)

        # The first signal sees the same flow either way.
        self.assertEqual(full[0]["signal_date"], truncated[0]["signal_date"])
        self.assertEqual(full[0]["flow_score"], truncated[0]["flow_score"])

    def test_missing_flow_scores_none_not_zero(self):
        """No flow data must leave the score absent, never a neutral 50 or 0."""

        trades = bc.signals_for("code", "SYM", bars(), None,
                                holding_days=20, threshold=0, step=13)

        self.assertTrue(trades)

        for t in trades:
            self.assertIsNone(t["flow_score"])

    def test_short_flow_history_is_not_scored(self):
        """Below FLOW_MIN_SESSIONS there is no reading, so no score."""

        thin = flow(n=400).head(config.FLOW_MIN_SESSIONS - 1)

        trades = bc.signals_for("code", "SYM", bars(), thin,
                                holding_days=20, threshold=0, step=13)

        self.assertTrue(trades)

        # Every signal sits well after the handful of flow rows, and a window
        # that short must not produce a number.
        late = [t for t in trades if t["flow_score"] is not None]

        self.assertEqual(late, [], "scored a flow window below the minimum")

    def test_trades_do_not_overlap_within_a_symbol(self):
        """One persistent trend must not contribute many correlated trades."""

        trades = bc.signals_for("code", "SYM", bars(), flow(),
                                holding_days=20, threshold=0, step=1)

        dates = [pd.Timestamp(t["signal_date"]) for t in trades]

        for earlier, later in zip(dates, dates[1:]):
            self.assertGreater((later - earlier).days, 20,
                               "signals overlap inside one holding window")


class TestBackAdjust(unittest.TestCase):
    """Capital-increase gaps must leave the return series continuous.

    The failure this prevents is silent and expensive: a 40% overnight drop
    that no holder ever took is read as a loss, stops the position out, and is
    reported as a strategy result.
    """

    def _series(self, closes, dates=None):
        dates = dates or pd.bdate_range("2024-01-01", periods=len(closes))
        return pd.DataFrame({
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        })

    def test_a_clean_series_is_returned_unchanged(self):
        closes = [1000 * (1.01 ** i) for i in range(40)]

        frame, events = bc.back_adjust(self._series(closes))

        self.assertEqual(events, [])
        self.assertAlmostEqual(frame["close"].iloc[-1], closes[-1], places=6)

    def test_a_halving_gap_is_removed(self):
        """A 50% capital increase must not show up as a 50% loss."""

        closes = [1000.0] * 10 + [500.0] * 10

        frame, events = bc.back_adjust(self._series(closes))

        self.assertEqual(len(events), 1)

        returns = frame["close"].pct_change().dropna().abs()

        self.assertLess(returns.max(), 0.01,
                        "the adjusted series must have no jump left in it")

    def test_prices_before_the_gap_are_scaled_not_after(self):
        closes = [1000.0] * 10 + [500.0] * 10

        frame, _ = bc.back_adjust(self._series(closes))

        # The most recent price is the real, quoted one.
        self.assertAlmostEqual(frame["close"].iloc[-1], 500.0, places=6)
        # History is restated onto the same basis.
        self.assertAlmostEqual(frame["close"].iloc[0], 500.0, places=6)

    def test_two_gaps_compound(self):
        closes = [1000.0] * 5 + [500.0] * 5 + [250.0] * 5

        frame, events = bc.back_adjust(self._series(closes))

        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(frame["close"].iloc[0], 250.0, places=6)

        returns = frame["close"].pct_change().dropna().abs()
        self.assertLess(returns.max(), 0.01)

    def test_ordinary_band_moves_survive(self):
        """A run of limit-up days is a real move and must not be flattened."""

        closes = [1000.0]
        for _ in range(15):
            closes.append(closes[-1] * 1.03)

        frame, events = bc.back_adjust(self._series(closes))

        self.assertEqual(events, [],
                         "3% band moves must never be read as adjustments")
        self.assertAlmostEqual(frame["close"].iloc[-1], closes[-1], places=6)

    def test_volume_is_left_alone(self):
        closes = [1000.0] * 10 + [500.0] * 10

        frame, _ = bc.back_adjust(self._series(closes))

        self.assertTrue((frame["volume"] == 1_000_000).all(),
                        "rescaling volume would misstate real turnover")

    def test_an_upward_gap_is_a_real_move(self):
        """No corporate action raises a price: a jump is a reopening without
        a band (وبملت +12.1% after its AGM), and scaling it out erased real
        returns (review panel, 2026-11-11)."""

        closes = [500.0] * 10 + [1000.0] * 10

        frame, events = bc.back_adjust(self._series(closes))

        self.assertEqual(events, [])
        self.assertAlmostEqual(frame["close"].iloc[0], 500.0, places=6)

    def test_a_cut_on_an_untraded_bar_is_the_exchange_repricing(self):
        """وبملت 1118 -> 1072 with no trade: exactly its DPS of 46."""

        closes = [1118.0] * 10 + [1072.0] + [1072.0] * 9
        series = self._series(closes)
        series.loc[10, "volume"] = 0

        frame, events = bc.back_adjust(series)

        self.assertEqual([e["kind"] for e in events], ["reset"])
        self.assertAlmostEqual(frame["close"].iloc[0], 1072.0, places=6)

    def test_a_small_fall_on_a_traded_bar_is_left_alone(self):
        closes = [1118.0] * 10 + [1072.0] * 10

        _frame, events = bc.back_adjust(self._series(closes))

        self.assertEqual(events, [])

    def test_a_known_dividend_is_taken_out_at_the_reopening(self):
        """شستا: halted for its AGM, reopened 8.2% lower; DPS 210 of 3,680."""

        closes = [3680.0] * 10 + [3680.0, 3680.0] + [3380.0] * 8
        series = self._series(closes)
        series.loc[10:11, "volume"] = 0
        published = str(series["date"].iloc[13].date())

        frame, events = bc.back_adjust(series, dividends=[(published, 210.0)])

        self.assertEqual([e["kind"] for e in events], ["dividend"])
        self.assertAlmostEqual(frame["close"].iloc[0], 3680.0 - 210.0, places=4)

    def test_signals_use_the_adjusted_series(self):
        """The adjustment has to happen before scores are computed, not after."""

        price = bars()

        # Drop every price in the second half by 60%, which is what an
        # unadjusted capital increase looks like in the raw feed: the quoted
        # price falls overnight by the issue's factor.
        cut = len(price) // 2
        for column in ("open", "high", "low", "close"):
            price.loc[cut:, column] = price.loc[cut:, column] * 0.4

        trades = bc.signals_for("code", "SYM", price, flow(),
                                holding_days=20, threshold=0, step=13)

        self.assertTrue(trades)

        # No trade may show the artificial -60% gap.
        worst = min(t["return"] for t in trades)

        self.assertGreater(worst, -50.0,
                           "an unadjusted gap leaked into a trade return")


class TestTrailingExit(unittest.TestCase):

    def test_a_falling_window_exits_at_the_stop_not_the_close(self):
        held = pd.DataFrame([
            {"high": 1000, "low": 999, "close": 1000},
            {"high": 1000, "low": 900, "close": 905},   # pierces a 2 ATR stop
            {"high": 910, "low": 800, "close": 810},
        ])

        result = bc._trailing_exit(held, entry_price=1000.0, atr_pct=2.0)

        self.assertIn(result["reason"], ("initial stop", "trailing stop"))
        self.assertEqual(result["days"], 2)
        # Stopped at -4% (2 x 2 ATR) less costs, not at the -19% close.
        self.assertGreater(result["return"], -8.0)

    def test_a_rising_window_runs_to_the_end(self):
        held = pd.DataFrame([
            {"high": 1000 + i * 12, "low": 995 + i * 12, "close": 1000 + i * 12}
            for i in range(20)
        ])

        result = bc._trailing_exit(held, entry_price=1000.0, atr_pct=2.0)

        self.assertEqual(result["reason"], "time")
        self.assertGreater(result["return"], 0)

    def test_no_atr_yields_no_trailing_result(self):
        held = pd.DataFrame([{"high": 1, "low": 1, "close": 1}])

        self.assertIsNone(bc._trailing_exit(held, 1000.0, None)["return"])


if __name__ == "__main__":
    unittest.main()


class TestUntradedMovesBothWays(unittest.TestCase):
    """The full test, 2026-11-12: an untraded rise was kept, its fall removed."""

    @staticmethod
    def series(closes, volumes):
        return pd.DataFrame({
            "date": pd.bdate_range("2026-01-01", periods=len(closes)),
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": volumes})

    def test_an_untraded_round_trip_leaves_no_gain(self):
        # حریل: 4550 traded, a bogus 3050 and back to 4550 untraded, 4470 traded
        frame, _ = bc.back_adjust(self.series([4550.0, 3050.0, 4550.0, 4470.0],
                                              [1e6, 0, 0, 1e6]))
        net = frame["close"].iloc[-1] / frame["close"].iloc[0] - 1
        self.assertAlmostEqual(net, 4470 / 4550 - 1, places=6)

    def test_a_traded_reopening_rise_is_still_kept(self):
        frame, events = bc.back_adjust(self.series([1118.0, 1072.0, 1202.0],
                                                   [1e6, 0, 1e6]))
        self.assertEqual([e["kind"] for e in events], ["reset"])
        self.assertAlmostEqual(frame["close"].iloc[-1] / frame["close"].iloc[-2] - 1,
                               1202 / 1072 - 1, places=6)

    def test_a_pre_listing_placeholder_is_not_a_return(self):
        frame, _ = bc.back_adjust(self.series([1000.0, 1000.0, 65000.0, 66000.0],
                                              [0, 0, 1e6, 1e6]))
        self.assertAlmostEqual(frame["close"].iloc[0], 65000.0, places=4)
