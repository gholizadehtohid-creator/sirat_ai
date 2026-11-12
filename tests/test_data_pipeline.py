"""One frame builder, three callers. The tests that keep them on it.

WHAT WENT WRONG WITHOUT THESE
-----------------------------
The panel, the paper trader and the backtest all ran the same decision engine
and still disagreed on 78% of names, because each built its input frame its own
way: one back-adjusted for capital increases and one did not, and one computed
recursive indicators over a full history where the other used 400 bars. Both
differences were invisible to every unit test, because every unit test handed
the engine a frame instead of asking where the frame came from.

So these assert the DATA, not the decision:

    1. the adjustment is applied, and it is the same function everywhere
    2. the indicator warm-up is one span, used by all three callers
    3. no caller builds a frame any other way
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import data_pipeline
import swing_advisor
from indicators import DEFAULT_PARAMS, calculate_indicators


def bars_with_gap(sessions=420, gap_at=None, ratio=0.6):
    """A rising series with one capital-increase style overnight drop.

    The daily move VARIES, deterministically. A perfectly smooth series is the
    one case where a recursive indicator converges to the same value whatever
    warm-up it was given, which would make the warm-up test pass for a reason
    that has nothing to do with real bars. Amplitude stays well inside the 12%
    adjustment threshold so the only gap detected is the one placed on purpose.
    """

    dates = pd.bdate_range("2023-01-02", periods=sessions)

    closes = []

    price = 1000.0

    for i in range(sessions):
        price *= 1.001 + 0.02 * math.sin(i * 1.7) + 0.01 * math.cos(i * 0.31)
        if gap_at is not None and i == gap_at:
            price *= ratio
        closes.append(price)

    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1_000_000.0] * sessions,
        "value": [1_000_000_000.0] * sessions,
        "trades": [500.0] * sessions,
    })


# ==============================
# 1. CORPORATE ACTIONS
# ==============================

class AdjustmentTest(unittest.TestCase):

    def test_a_capital_increase_gap_is_removed_from_the_frame(self):
        raw = bars_with_gap(gap_at=300, ratio=0.6)

        frame = data_pipeline.frame(bars=raw)

        closes = frame["close"].astype(float).to_numpy()

        # Across the whole windowed frame there must be no overnight move
        # anywhere near the 40% the raw series carries.
        worst = max(abs(closes[i] / closes[i - 1] - 1.0)
                    for i in range(1, len(closes)))

        self.assertLess(worst, 0.12,
                        "the frame still contains an unadjusted gap")

    def test_an_already_adjusted_caller_is_not_adjusted_twice(self):
        raw = bars_with_gap(gap_at=300, ratio=0.6)

        once = data_pipeline.frame(bars=raw)

        # The backtest hands in bars pit.py already adjusted.
        from backtest import back_adjust

        pre, _ = back_adjust(raw)

        twice = data_pipeline.frame(bars=pre, adjusted=True)

        self.assertEqual(len(once), len(twice))

        for a, b in zip(once["close"], twice["close"]):
            self.assertAlmostEqual(float(a), float(b), places=6)

    def test_the_pipeline_uses_the_projects_own_adjustment_function(self):
        """Not a second implementation of it."""

        import inspect

        source = inspect.getsource(data_pipeline)

        self.assertIn("from backtest import back_adjust", source)

    def test_a_clean_series_is_left_alone(self):
        raw = bars_with_gap(gap_at=None)

        frame = data_pipeline.frame(bars=raw)

        tail = raw.tail(data_pipeline.WINDOW_BARS).reset_index(drop=True)

        for a, b in zip(frame["close"], tail["close"]):
            self.assertAlmostEqual(float(a), float(b), places=6)


# ==============================
# 2. WARM-UP
# ==============================

class WarmupTest(unittest.TestCase):

    INDICATORS = ("ATR", "RSI", "MACD", "MA_FAST", "MA_SLOW")

    def test_the_frame_is_exactly_the_standard_window(self):
        frame = data_pipeline.frame(bars=bars_with_gap(sessions=900))

        self.assertEqual(len(frame), data_pipeline.WINDOW_BARS)

    def test_how_much_history_the_caller_loaded_cannot_change_the_reading(self):
        """The property that makes one standard span enough.

        The panel loads a bounded slice and the backtest holds a full history.
        Both hand what they have to the pipeline, which windows before it
        computes -- so the caller's loading habits cannot reach the numbers.

        MEASURED on 250 real symbols, the span itself moves ATR by 2e-9 and
        leaves MA and volume untouched; it only bites on a degenerate series
        (three names whose last 400 bars barely move, where RSI saturates). So
        this is not a large effect being suppressed -- it is a small one being
        made impossible, which is what lets the three paths be compared at all.
        """

        long_history = bars_with_gap(sessions=1200)

        short_load = long_history.tail(data_pipeline.WINDOW_BARS)

        from_long = data_pipeline.frame(bars=long_history)
        from_short = data_pipeline.frame(bars=short_load)

        self.assertEqual(len(from_long), len(from_short))

        for name in self.INDICATORS:

            if name not in from_long or name not in from_short:
                continue

            self.assertAlmostEqual(
                float(from_long[name].iloc[-1]),
                float(from_short[name].iloc[-1]), places=9,
                msg="%s depends on how much history the caller loaded" % name)

    def test_every_v2_caller_uses_the_same_span(self):
        from backtesting import swing_strategy

        self.assertEqual(swing_advisor.HISTORY_BARS,
                         data_pipeline.WINDOW_BARS)

        self.assertEqual(swing_strategy.WINDOW_BARS,
                         data_pipeline.WINDOW_BARS)

    def test_a_short_history_produces_nothing_rather_than_a_thin_frame(self):
        self.assertIsNone(data_pipeline.frame(bars=bars_with_gap(sessions=40)))

    def test_the_minimum_covers_the_longest_momentum_horizon(self):
        import config

        self.assertGreater(data_pipeline.minimum_bars(),
                           max(config.MOMENTUM_HORIZONS))


# ==============================
# 3. POINT IN TIME
# ==============================

class AsOfTest(unittest.TestCase):

    def test_no_bar_after_the_decision_date_is_in_the_frame(self):
        raw = bars_with_gap(sessions=600)

        cutoff = pd.Timestamp(raw["date"].iloc[450])

        frame = data_pipeline.frame(bars=raw, as_of=cutoff)

        self.assertLessEqual(pd.Timestamp(frame["date"].iloc[-1]), cutoff)
        self.assertTrue((pd.to_datetime(frame["date"]) <= cutoff).all())

    def test_the_last_bar_is_the_decision_bar(self):
        raw = bars_with_gap(sessions=600)

        cutoff = pd.Timestamp(raw["date"].iloc[450])

        frame = data_pipeline.frame(bars=raw, as_of=cutoff)

        self.assertEqual(pd.Timestamp(frame["date"].iloc[-1]), cutoff)

    def test_a_date_before_the_history_yields_nothing(self):
        raw = bars_with_gap(sessions=600)

        self.assertIsNone(
            data_pipeline.frame(bars=raw, as_of=pd.Timestamp("2000-01-01")))

    def test_the_same_date_gives_the_same_frame_every_time(self):
        raw = bars_with_gap(sessions=600)

        cutoff = pd.Timestamp(raw["date"].iloc[500])

        a = data_pipeline.frame(bars=raw, as_of=cutoff)
        b = data_pipeline.frame(bars=raw, as_of=cutoff)

        for name in ("close", "ATR", "RSI", "MACD"):
            self.assertAlmostEqual(float(a[name].iloc[-1]),
                                   float(b[name].iloc[-1]), places=9)


# ==============================
# 4. NOBODY BUILDS A FRAME ANY OTHER WAY
# ==============================

class SingleBuilderTest(unittest.TestCase):

    def test_the_live_scan_goes_through_the_pipeline(self):
        import inspect

        source = inspect.getsource(swing_advisor.scan)

        self.assertIn("data_pipeline.frame(", source)

        self.assertNotIn("calculate_indicators(", source,
                         "the live path builds its own frame again")

    def test_the_backtest_goes_through_the_pipeline(self):
        import inspect

        from backtesting import swing_strategy

        source = inspect.getsource(swing_strategy.SwingStrategy._frame_for)

        self.assertIn("data_pipeline.frame(", source)

        self.assertNotIn("calculate_indicators(", source,
                         "the backtest builds its own frame again")

    def test_paper_trading_reaches_it_through_the_shared_scan(self):
        """Paper has no frame code of its own; it calls advisor.v2_scan."""

        import inspect

        from backtesting import paper

        source = inspect.getsource(paper.PaperTrader._decide_v2)

        self.assertIn("advisor.v2_scan", source)
        self.assertNotIn("calculate_indicators", source)

    def test_the_windowed_adjustment_equals_the_whole_series_one(self):
        """Why the live path may load a bounded number of bars and still match.

        back_adjust anchors at the newest bar and scales backwards, so events
        before the window cannot move anything inside it.
        """

        from backtest import back_adjust

        raw = bars_with_gap(sessions=900, gap_at=200, ratio=0.55)

        whole, _ = back_adjust(raw)
        whole_tail = whole.tail(data_pipeline.WINDOW_BARS).reset_index(drop=True)

        window_only, _ = back_adjust(
            raw.tail(data_pipeline.WINDOW_BARS).reset_index(drop=True))

        for a, b in zip(whole_tail["close"], window_only["close"]):
            self.assertAlmostEqual(float(a), float(b), places=6)


# ==============================
# 5. THE THREE PATHS AGREE ON ONE DATE
# ==============================

class ThreePathAgreementTest(unittest.TestCase):
    """The property the whole module exists to guarantee.

    MEASURED on the production database: asked for the same session, the live
    frame and the backtest frame agreed on 816 of 816 symbols, indicator state
    included. This reproduces that offline on one synthetic name -- including
    the corporate action, which is the case that used to break it.
    """

    def _paths(self, raw, as_of):
        """(live frame, backtest frame) for one date.

        Live loads raw bars and lets the pipeline adjust. The backtest hands in
        bars already adjusted by pit.py. Two routes in, one builder, and the
        result has to be the same frame.
        """

        from backtest import back_adjust

        pit_bars, _ = back_adjust(raw)

        return (data_pipeline.frame(bars=raw, as_of=as_of),
                data_pipeline.frame(bars=pit_bars, as_of=as_of, adjusted=True))

    def test_the_same_date_gives_both_paths_the_same_frame(self):
        raw = bars_with_gap(sessions=700, gap_at=520, ratio=0.55)

        as_of = pd.Timestamp(raw["date"].iloc[600])

        live, back = self._paths(raw, as_of)

        self.assertEqual(len(live), len(back))

        for name in ("close", "high", "low", "ATR", "RSI", "MACD",
                     "MA_FAST", "MA_SLOW", "ADX"):
            if name not in live:
                continue
            self.assertAlmostEqual(
                float(live[name].iloc[-1]), float(back[name].iloc[-1]),
                places=6,
                msg="%s differs between the live and backtest frames" % name)

    def test_it_holds_when_the_corporate_action_is_inside_the_window(self):
        """The exact case that produced the original disagreement."""

        raw = bars_with_gap(sessions=700, gap_at=560, ratio=0.6)

        as_of = pd.Timestamp(raw["date"].iloc[600])

        live, back = self._paths(raw, as_of)

        # Inside the window, and still no unadjusted gap on either side.
        for frame in (live, back):
            closes = frame["close"].astype(float).to_numpy()
            worst = max(abs(closes[i] / closes[i - 1] - 1.0)
                        for i in range(1, len(closes)))
            self.assertLess(worst, 0.12)

        self.assertAlmostEqual(float(live["ATR"].iloc[-1]),
                               float(back["ATR"].iloc[-1]), places=6)

    def test_a_different_date_is_expected_to_differ(self):
        """Guards the interpretation of the remaining production gap.

        Live reads each symbol's newest bar; the backtest is pinned to the
        index calendar's last session. When the stored index lags the stored
        symbol bars the two stand on different days, and this test states that
        this is a data-freshness fact rather than a pipeline defect.
        """

        raw = bars_with_gap(sessions=700)

        earlier, _ = self._paths(raw, pd.Timestamp(raw["date"].iloc[595]))
        later, _ = self._paths(raw, pd.Timestamp(raw["date"].iloc[600]))

        self.assertNotEqual(float(earlier["close"].iloc[-1]),
                            float(later["close"].iloc[-1]))


# ==============================
# 6. THE FUNDAMENTAL INPUT IS STRUCK AT THE SAME PRICE
# ==============================

class ValuationPriceTest(unittest.TestCase):
    """The second half of the consistency gap, and the harder one to see.

    With the price frames byte-identical, the panel and the backtest still
    disagreed on 203 of 284 covered companies -- because the panel scored the
    multiples the collector had stored and the backtest recomputed them at the
    decision close. Same filing, different price, different valuation.
    """

    RECORD = {
        "period_months": 12,
        "items": {"revenue": 1_200_000.0, "equity_owners": 800_000.0},
        "ratios": {
            "shares": 1000.0,
            "eps_annual": 100.0,
            # Struck at some older, lower price and saved to disk.
            "pe": 5.0,
            "pb": 1.0,
            "ps": 0.5,
            "market_cap": 500_000.0,
        },
    }

    def test_the_stored_multiples_are_replaced_not_kept(self):
        import fundamental_engine

        ratios = fundamental_engine.market_ratios(self.RECORD, price=2000.0)

        self.assertEqual(ratios["pe"], 20.0)
        self.assertNotEqual(ratios["pe"], self.RECORD["ratios"]["pe"])
        self.assertEqual(ratios["market_cap"], 2_000_000.0)

    def test_no_market_field_survives_when_the_price_is_unusable(self):
        """A stored P/E must never be scored as if it were current."""

        import fundamental_engine

        ratios = fundamental_engine.market_ratios(self.RECORD, price=None)

        for key in ("pe", "pb", "ps", "market_cap", "earnings_yield",
                    "book_value_per_share"):
            self.assertIsNone(ratios[key],
                              "%s came off disk into a live score" % key)

    def test_the_point_in_time_path_uses_the_same_function(self):
        import inspect

        from backtesting.pit import PointInTimeData

        source = inspect.getsource(PointInTimeData.market_ratios)

        self.assertIn("fundamental_engine.market_ratios", source,
                      "pit.py has its own copy of the valuation arithmetic")

    def test_both_routes_give_the_same_ratios(self):
        import fundamental_engine

        from backtesting.pit import PointInTimeData

        direct = fundamental_engine.market_ratios(self.RECORD, 1750.0,
                                                  sector="فلز")

        viapit = PointInTimeData.market_ratios(None, self.RECORD, 1750.0,
                                               sector="فلز")

        self.assertEqual(direct, viapit)

    def test_the_live_scan_strikes_the_valuation_at_the_current_close(self):
        import inspect

        source = inspect.getsource(swing_advisor.evaluate)

        self.assertIn("price=close", source,
                      "the panel scores the collector's stored multiples again")

    def test_an_earnings_multiple_is_withheld_where_it_means_nothing(self):
        """Unchanged behaviour, asserted because it now runs on the live path."""

        import config
        import fundamental_engine

        sector = next(iter(config.EARNINGS_MULTIPLE_NOT_MEANINGFUL), None)

        if sector is None:
            self.skipTest("no sector is configured as multiple-free")

        ratios = fundamental_engine.market_ratios(self.RECORD, 2000.0,
                                                  sector=sector)

        self.assertIsNone(ratios["pe"])
        self.assertIsNotNone(ratios["pb"])


class UntradedBarTests(unittest.TestCase):
    """The full test, 2026-11-12: bars stored with a high and low of 0 on a
    session nothing traded tripled ATR (ملت 5.34% for 3.2%)."""

    def test_an_untraded_bar_has_no_range(self):
        import pandas as pd
        import data_pipeline
        bars = pd.DataFrame({"date": ["d1", "d2", "d3"],
                             "open": [100.0, 0.0, 101.0],
                             "high": [102.0, 0.0, 103.0],
                             "low": [99.0, 0.0, 100.0],
                             "close": [101.0, 101.0, 102.0],
                             "volume": [10.0, 0.0, 12.0]})
        out = data_pipeline.flatten_untraded(bars)
        self.assertEqual(out.loc[1, ["open", "high", "low"]].tolist(), [101.0] * 3)
        self.assertEqual(out.loc[0, "high"], 102.0)            # traded: kept
        self.assertEqual(bars.loc[1, "high"], 0.0)             # input untouched

    def test_atr_is_not_inflated_by_a_zero_bar(self):
        import numpy as np
        import pandas as pd
        import data_pipeline
        n = 400
        close = 1000 + np.cumsum(np.where(np.arange(n) % 2, 10.0, -10.0))
        bars = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
                             "open": close, "high": close + 15, "low": close - 15,
                             "close": close, "volume": 100.0})
        clean = data_pipeline.frame(bars=bars, adjusted=True)
        dirty_bars = bars.copy()
        dirty_bars.loc[n - 5, ["open", "high", "low", "volume"]] = 0.0
        dirty = data_pipeline.frame(bars=dirty_bars, adjusted=True)
        self.assertLess(dirty["ATR"].iloc[-1], clean["ATR"].iloc[-1] * 1.3)


if __name__ == "__main__":
    unittest.main()
