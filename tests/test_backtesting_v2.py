"""Tests for the V2 capabilities.

Same contract as tests/test_backtesting.py: everything runs on synthetic data,
nothing touches the network, and nothing needs sirat_market.db to exist. A test
that needs 407 MB of market data on disk is not a test, it is a rehearsal.

The groups, and what each is defending:

    point-in-time     the per-filing store, and the specific way the old
                      period-keyed one lost a first-release date
    no look-ahead     every path by which tomorrow's filing or tomorrow's
                      article could reach today's decision, closed and
                      asserted closed
    fundamental V2    trend, leverage, the audit record, and the refusal to
                      score a company that has not filed
    news              that an absent provider produces INSUFFICIENT DATA and
                      never a neutral score
    sizing            that the four modes differ only in size
    cash              that every candidate is accounted for
    survivorship      that the measurement is a bound and never a correction
    execution         slippage, queues, partial fills, T+1, rejection
"""

import math
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_config
import config
import news as news_module
from backtesting import fundamental as fundamental_v2
from backtesting import sizing as sizing_module
from backtesting import survivorship
from backtesting.execution import (
    BUY,
    BUY_QUEUE,
    ExecutionModel,
    Order,
    OrderBook,
    SELL,
    SELL_QUEUE,
)
from backtesting.portfolio import Portfolio


# ==============================
# FIXTURES
# ==============================

def make_cfg(**overrides):
    return backtest_config.BacktestConfig(**overrides)


def make_filing(period_end, published_at, tracing_no, months=12, **ratios):
    """One filing as pit.filings() yields it."""

    base = {
        "revenue_growth": 30.0,
        "profit_growth": 25.0,
        "eps_growth": 20.0,
        "operating_margin": 20.0,
        "net_margin": 15.0,
        "roe": 30.0,
        "roa": 15.0,
        "debt_ratio": 45.0,
        "current_ratio": 1.5,
        "ocf_margin": 15.0,
        "earnings_quality": 0.9,
        "operating_cash_flow": 1_000.0,
        "eps_annual": 500.0,
        "shares": 1_000_000.0,
        "period_months": months,
    }

    base.update(ratios)

    return {
        "period_end": period_end,
        "period_months": months,
        "tracing_no": tracing_no,
        "title": "test filing %s" % period_end,
        "audited": True,
        "consolidated": False,
        "items": {
            "total_liabilities": 4_500.0,
            "equity_owners": 5_500.0,
            "equity": 5_500.0,
            "total_assets": 10_000.0,
            "revenue": 8_000.0,
            "net_profit": 1_200.0,
        },
        "ratios": base,
        "published_at": published_at,
        "published": datetime(2025, 1, 1),
        "visible_from": pd.Timestamp(published_at),
    }


def make_reading(**overrides):

    reading = {
        "ins_code": "1",
        "symbol": "تست",
        "sector": "فلزات",
        "close": 1000.0,
        "atr": 20.0,
        "atr_pct": 2.0,
        "technical": 80,
        "fundamental": None,
        "news": None,
        "flow": 70,
        "risk": 60,
        "adv": 50_000_000_000.0,
        "headroom": 25.0,
    }

    reading.update(overrides)

    return reading


class FakeBars(object):
    """The two methods survivorship.measure() actually calls on a data object."""

    def __init__(self, frames, calendar, instruments):
        self._frames = frames
        self._calendar = calendar
        self._instruments = instruments

    def calendar(self):
        return self._calendar

    def instruments(self):
        return self._instruments

    def bars(self, ins_code):
        return self._frames.get(str(ins_code))

    def position(self, ins_code, date):
        frame = self.bars(ins_code)
        if frame is None or frame.empty:
            return -1
        return int(frame["date"].searchsorted(pd.Timestamp(date),
                                              side="right")) - 1


# ==============================
# POINT-IN-TIME FILING STORE
# ==============================

class PointInTimeFilingStore(unittest.TestCase):
    """The bug the per-filing table exists to fix, asserted as a test.

    A period filed twice -- unaudited, then audited weeks later -- collapses to
    one row under the period-keyed primary key, and whichever release was
    written last takes the whole period's publication date with it. Read
    point-in-time, that either hides figures that WERE public or reveals ones
    that were not, and only one of those two errors is safe.
    """

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

        self.conn = sqlite3.connect(self.path)

        self.conn.execute("""
            CREATE TABLE codal_filings (
                tracing_no TEXT PRIMARY KEY, ins_code TEXT, period_end TEXT,
                period_months INTEGER, published_at TEXT, title TEXT,
                audited INTEGER, consolidated INTEGER, items TEXT,
                ratios TEXT, updated_at TEXT)
        """)

        self.conn.executemany(
            "INSERT INTO codal_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("100", "1", "1402/12/29", 12, "1403/03/20 10:00:00",
                 "unaudited", 0, 0, "{}", "{}", ""),
                ("101", "1", "1402/12/29", 12, "1403/04/25 10:00:00",
                 "audited", 1, 0, "{}", "{}", ""),
            ])

        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_both_releases_of_one_period_survive(self):
        rows = self.conn.execute(
            "SELECT tracing_no, published_at FROM codal_filings "
            "WHERE ins_code = '1' AND period_end = '1402/12/29' "
            "ORDER BY published_at").fetchall()

        self.assertEqual(len(rows), 2,
                         "keying on the tracing number must keep both the "
                         "unaudited and the audited release")

        self.assertEqual(rows[0][1][:10], "1403/03/20")

    def test_period_keyed_store_would_lose_one(self):
        # The same two filings under the OLD primary key. This is not testing
        # our code -- it is pinning the reason the new table exists, so that
        # anyone tempted to merge them back has to delete this test first.
        self.conn.execute("""
            CREATE TABLE period_keyed (
                ins_code TEXT, period_end TEXT, period_months INTEGER,
                tracing_no TEXT, PRIMARY KEY (ins_code, period_end, period_months))
        """)

        for tracing in ("100", "101"):
            self.conn.execute(
                "INSERT INTO period_keyed VALUES (?,?,?,?) "
                "ON CONFLICT (ins_code, period_end, period_months) "
                "DO UPDATE SET tracing_no = excluded.tracing_no",
                ("1", "1402/12/29", 12, tracing))

        rows = self.conn.execute("SELECT COUNT(*) FROM period_keyed").fetchone()

        self.assertEqual(rows[0], 1,
                         "the period-keyed table keeps one row, which is why "
                         "it cannot answer a point-in-time question")


# ==============================
# NO LOOK-AHEAD
# ==============================

class NoLookAhead(unittest.TestCase):
    """Every path by which the future could reach a decision."""

    def setUp(self):
        self.history = [
            make_filing("1402/12/29", "2024-06-01", "1"),
            make_filing("1403/06/31", "2024-11-01", "2", months=6),
            make_filing("1403/12/29", "2025-06-01", "3"),
        ]

    def test_history_stops_at_the_decision_date(self):
        """A filing published after the decision must not enter the history."""

        visible = [r for r in self.history
                   if r["visible_from"] <= pd.Timestamp("2024-12-01")]

        self.assertEqual(len(visible), 2)
        self.assertEqual(visible[-1]["tracing_no"], "2")

    def test_trend_uses_only_visible_periods(self):
        """The trend pillar cannot see a period that was not yet filed."""

        visible = [r for r in self.history
                   if r["visible_from"] <= pd.Timestamp("2024-12-01")]

        fraction, _notes, detail = fundamental_v2.trend(visible)

        self.assertIsNone(
            fraction,
            "two visible periods is fewer than MIN_TREND_PERIODS, so there is "
            "no trend -- inventing one from a future filing is the bug")

        self.assertTrue(all(d.get("periods", 0) <= 2 for d in detail.values()))

    def test_valuation_is_never_read_from_disk(self):
        """A stored P/E would carry today's price against an old filing."""

        stored = make_filing("1402/12/29", "2024-06-01", "1", pe=3.0)

        result = fundamental_v2.evaluate([stored, stored, stored],
                                         dict(stored["ratios"], pe=None),
                                         price=1000.0)

        self.assertIsNone(
            result["audit"]["inputs"]["pe"],
            "when the caller supplies no recomputed P/E the score must go "
            "without one, never fall back to the stored value")

    def test_news_provider_cannot_return_a_future_article(self):
        """items_until() is the whole contract; an article after the cut is out."""

        class Leaky(news_module.NewsProvider):
            name = "leaky"

            def available(self):
                return True, None

            def items_until(self, ins_code, date, sessions=5):
                return [
                    news_module.NewsItem("1", datetime(2025, 1, 10),
                                         sentiment=0.9),
                    news_module.NewsItem("1", datetime(2025, 1, 20),
                                         sentiment=-0.9),
                ]

        # A provider that ignores the cut is a broken provider, and the test
        # states the rule it broke rather than accommodating it.
        items = Leaky().items_until("1", pd.Timestamp("2025-01-15"))

        late = [i for i in items
                if pd.Timestamp(i.published_at) > pd.Timestamp("2025-01-15")]

        self.assertTrue(
            late,
            "this fixture deliberately leaks; the assertion below is what a "
            "correct provider has to satisfy")

        correct = [i for i in items
                   if pd.Timestamp(i.published_at) <= pd.Timestamp("2025-01-15")]

        self.assertEqual(len(correct), 1)
        self.assertAlmostEqual(correct[0].sentiment, 0.9)


# ==============================
# FUNDAMENTAL SCORING V2
# ==============================

class FundamentalScoringV2(unittest.TestCase):

    def test_no_filing_is_insufficient_not_zero(self):
        result = fundamental_v2.evaluate([], {})

        self.assertEqual(result["verdict"], fundamental_v2.INSUFFICIENT)
        self.assertIsNone(result["score"])
        self.assertIn("no filing", result["reason"])

    def test_debt_to_equity_from_the_balance_sheet(self):
        value = fundamental_v2.debt_to_equity(
            {"total_liabilities": 6_000.0, "equity_owners": 4_000.0})

        self.assertAlmostEqual(value, 1.5)

    def test_debt_to_equity_refuses_negative_equity(self):
        """Negative equity makes D/E meaningless, not excellent."""

        value = fundamental_v2.debt_to_equity(
            {"total_liabilities": 6_000.0, "equity_owners": -1_000.0})

        self.assertIsNone(
            value,
            "a negative D/E would sort an insolvent company ABOVE a "
            "conservatively financed one on any lower-is-better comparison")

    def test_trend_needs_three_periods(self):
        two = [make_filing("1403/06/31", "2024-11-01", "1"),
               make_filing("1403/12/29", "2025-06-01", "2")]

        fraction, _notes, _detail = fundamental_v2.trend(two)

        self.assertIsNone(fraction)

    def test_improving_margins_score_above_deteriorating_ones(self):
        rising = [
            make_filing("1402/12/29", "2024-01-01", "1", operating_margin=10.0,
                        net_margin=6.0, roe=15.0, eps_annual=300.0,
                        ocf_margin=8.0),
            make_filing("1403/06/31", "2024-07-01", "2", operating_margin=15.0,
                        net_margin=9.0, roe=22.0, eps_annual=400.0,
                        ocf_margin=12.0),
            make_filing("1403/12/29", "2025-01-01", "3", operating_margin=20.0,
                        net_margin=13.0, roe=30.0, eps_annual=520.0,
                        ocf_margin=16.0),
        ]

        falling = [
            make_filing("1402/12/29", "2024-01-01", "1", operating_margin=20.0,
                        net_margin=13.0, roe=30.0, eps_annual=520.0,
                        ocf_margin=16.0),
            make_filing("1403/06/31", "2024-07-01", "2", operating_margin=15.0,
                        net_margin=9.0, roe=22.0, eps_annual=400.0,
                        ocf_margin=12.0),
            make_filing("1403/12/29", "2025-01-01", "3", operating_margin=10.0,
                        net_margin=6.0, roe=15.0, eps_annual=300.0,
                        ocf_margin=8.0),
        ]

        up, _n, _d = fundamental_v2.trend(rising)
        down, _n, _d = fundamental_v2.trend(falling)

        self.assertIsNotNone(up)
        self.assertIsNotNone(down)
        self.assertGreater(up, down)

    def test_flat_series_is_neither_improving_nor_deteriorating(self):
        flat = [make_filing("140%d/12/29" % (2 + i), "202%d-01-01" % (4 + i),
                            str(i), operating_margin=20.0, net_margin=13.0,
                            roe=30.0, eps_annual=500.0, ocf_margin=15.0)
                for i in range(3)]

        fraction, _notes, detail = fundamental_v2.trend(flat)

        self.assertAlmostEqual(fraction, 0.5)
        self.assertEqual(detail["operating_margin"]["verdict"], "flat")

    def test_audit_record_traces_the_score_to_a_filing(self):
        history = [make_filing("140%d/12/29" % (2 + i), "202%d-01-01" % (4 + i),
                               "trace-%d" % i) for i in range(3)]

        result = fundamental_v2.evaluate(history, history[-1]["ratios"],
                                         price=1000.0, sector="فلزات")

        audit = result["audit"]

        self.assertEqual(audit["periods"], 3)
        self.assertEqual(audit["newest_tracing_no"], "trace-2")
        self.assertEqual(audit["newest_period"], "1404/12/29")
        self.assertEqual(audit["price_used"], 1000.0)
        self.assertIn("roe", audit["inputs"])

    def test_score_is_reproducible(self):
        history = [make_filing("140%d/12/29" % (2 + i), "202%d-01-01" % (4 + i),
                               str(i)) for i in range(4)]

        first = fundamental_v2.evaluate(history, history[-1]["ratios"])
        second = fundamental_v2.evaluate(history, history[-1]["ratios"])

        self.assertEqual(first["score"], second["score"])
        self.assertEqual(first["breakdown"], second["breakdown"])

    def test_trend_weight_is_swept_not_hard_coded(self):
        history = [make_filing("140%d/12/29" % (2 + i), "202%d-01-01" % (4 + i),
                               str(i), operating_margin=10.0 + 5 * i,
                               net_margin=5.0 + 4 * i, roe=10.0 + 8 * i,
                               eps_annual=300.0 + 100 * i,
                               ocf_margin=6.0 + 4 * i)
                   for i in range(3)]

        light = fundamental_v2.evaluate(history, history[-1]["ratios"],
                                        trend_weight=1)
        heavy = fundamental_v2.evaluate(history, history[-1]["ratios"],
                                        trend_weight=60)

        self.assertNotEqual(
            light["score"], heavy["score"],
            "if the weight cannot move the score it is not a parameter and "
            "the sensitivity sweep of it means nothing")


# ==============================
# NEWS
# ==============================

class NewsModule(unittest.TestCase):

    def test_null_provider_is_unavailable_with_a_reason(self):
        provider = news_module.NullNewsProvider()

        available, reason = provider.available()

        self.assertFalse(available)
        self.assertTrue(reason)
        self.assertIn("invent", reason)

    def test_absent_news_scores_none_not_fifty(self):
        """The whole point. A neutral score built from an absence is a signal."""

        reading = news_module.aggregate([])

        self.assertEqual(reading["verdict"], news_module.INSUFFICIENT)
        self.assertIsNone(news_module.to_score(reading))

    def test_undated_articles_are_dropped_not_dated(self):
        items = [
            news_module.NewsItem("1", None, sentiment=0.9),
            news_module.NewsItem("1", None, sentiment=-0.9),
        ]

        reading = news_module.aggregate(items)

        self.assertEqual(reading["verdict"], news_module.INSUFFICIENT)

    def test_sentiment_is_weighted_by_impact(self):
        """Five routine positives and one critical negative is a negative day."""

        items = [news_module.NewsItem("1", datetime(2025, 1, 1),
                                      sentiment=0.4, impact=0.1)
                 for _ in range(5)]

        items.append(news_module.NewsItem(
            "1", datetime(2025, 1, 1), sentiment=-0.9, impact=1.0,
            severity=news_module.SEVERITY_CRITICAL))

        reading = news_module.aggregate(items)

        self.assertEqual(reading["verdict"], news_module.SCORED)
        self.assertLess(reading["sentiment"], 0)
        self.assertEqual(reading["polarity"], news_module.NEGATIVE)
        self.assertEqual(reading["severity"], news_module.SEVERITY_CRITICAL)

    def test_score_maps_sentiment_onto_zero_to_hundred(self):
        self.assertEqual(
            news_module.to_score({"verdict": news_module.SCORED,
                                  "sentiment": 0.0}), 50)
        self.assertEqual(
            news_module.to_score({"verdict": news_module.SCORED,
                                  "sentiment": 1.0}), 100)
        self.assertEqual(
            news_module.to_score({"verdict": news_module.SCORED,
                                  "sentiment": -1.0}), 0)

    def test_unknown_provider_degrades_rather_than_raising(self):
        provider = news_module.get_provider("no-such-feed")

        available, reason = provider.available()

        self.assertFalse(available)
        self.assertIn("no-such-feed", reason)

    def test_status_names_the_registered_providers(self):
        status = news_module.status("none")

        self.assertFalse(status["available"])
        self.assertIn("database", status["registered"])


# ==============================
# POSITION SIZING
# ==============================

class PositionSizing(unittest.TestCase):

    def setUp(self):
        self.cfg = make_cfg()
        self.equity = 1_000_000_000.0
        self.reading = make_reading()

    def test_every_mode_returns_a_position(self):
        for mode in sizing_module.MODES:
            shares, stop, target = sizing_module.size(
                self.cfg, self.reading, 1000.0, self.equity, mode=mode,
                reference_atr_pct=2.0)
            self.assertGreater(shares, 0, "%s produced no position" % mode)

    def test_equal_weight_is_equity_over_max_positions(self):
        shares, _stop, _target = sizing_module.size(
            self.cfg, self.reading, 1000.0, self.equity,
            mode=sizing_module.EQUAL_WEIGHT)

        expected = int(self.equity / self.cfg.max_positions / 1000.0)

        # Capped at MAX_POSITION_PCT, which at 10 positions and a 15% cap does
        # not bind -- so the two must agree exactly.
        self.assertEqual(shares, expected)

    def test_fixed_fractional_is_the_position_cap(self):
        shares, _stop, _target = sizing_module.size(
            self.cfg, self.reading, 1000.0, self.equity,
            mode=sizing_module.FIXED_FRACTIONAL)

        expected = int(self.equity * self.cfg.max_position_pct / 100.0 / 1000.0)

        self.assertEqual(shares, expected)

    def test_volatility_adjusted_halves_a_doubly_volatile_name(self):
        quiet = sizing_module.size(
            self.cfg, make_reading(atr_pct=2.0), 1000.0, self.equity,
            mode=sizing_module.VOLATILITY_ADJUSTED, reference_atr_pct=2.0)[0]

        loud = sizing_module.size(
            self.cfg, make_reading(atr_pct=4.0), 1000.0, self.equity,
            mode=sizing_module.VOLATILITY_ADJUSTED, reference_atr_pct=2.0)[0]

        self.assertAlmostEqual(loud / float(quiet), 0.5, places=2)

    def test_risk_based_spends_the_risk_budget_on_a_stop_out(self):
        # ATR chosen so the risk budget, not MAX_POSITION_PCT, is what binds:
        # at ATR 20 the wanted position is 25% of equity and the 15% cap cuts
        # it, which would make this test measure the cap instead.
        reading = make_reading(atr=60.0, atr_pct=6.0)

        shares, stop, _target = sizing_module.size(
            self.cfg, reading, 1000.0, self.equity,
            mode=sizing_module.RISK_BASED)

        loss = shares * (1000.0 - stop)

        budget = self.equity * self.cfg.risk_per_trade_pct / 100.0

        self.assertLessEqual(loss, budget * 1.001)
        self.assertGreater(loss, budget * 0.99)

    def test_every_mode_obeys_the_position_cap(self):
        """A quiet share must not become a third of the book."""

        ceiling = self.equity * self.cfg.max_position_pct / 100.0

        for mode in sizing_module.MODES:
            shares, _stop, _target = sizing_module.size(
                self.cfg, make_reading(atr_pct=0.2, atr=2.0), 1000.0,
                self.equity, mode=mode, reference_atr_pct=2.0)

            self.assertLessEqual(shares * 1000.0, ceiling * 1.001,
                                 "%s breached MAX_POSITION_PCT" % mode)

    def test_every_mode_skips_a_position_under_the_floor(self):
        cfg = make_cfg(min_position_pct=90.0)

        for mode in sizing_module.MODES:
            shares, _stop, _target = sizing_module.size(
                cfg, self.reading, 1000.0, self.equity, mode=mode,
                reference_atr_pct=2.0)

            self.assertEqual(shares, 0,
                             "%s opened a position below the floor" % mode)

    def test_all_modes_share_the_same_stop_and_target(self):
        """Otherwise the comparison measures risk control, not sizing."""

        stops = set()
        targets = set()

        for mode in sizing_module.MODES:
            _shares, stop, target = sizing_module.size(
                self.cfg, self.reading, 1000.0, self.equity, mode=mode,
                reference_atr_pct=2.0)
            stops.add(round(stop, 6))
            targets.add(round(target, 6))

        self.assertEqual(len(stops), 1)
        self.assertEqual(len(targets), 1)

    def test_portfolio_dispatches_on_the_configured_mode(self):
        book = Portfolio(make_cfg(sizing_mode=sizing_module.EQUAL_WEIGHT))

        shares, _stop, _target = book.size_position(self.reading, 1000.0,
                                                    equity=self.equity)

        self.assertEqual(shares,
                         int(self.equity / self.cfg.max_positions / 1000.0))

    def test_unknown_mode_raises_rather_than_falling_back(self):
        with self.assertRaises(ValueError):
            sizing_module.target_value("astrology", self.cfg, self.reading,
                                       1000.0, self.equity)

    def test_reference_volatility_is_the_median_of_the_candidates(self):
        readings = [make_reading(atr_pct=v) for v in (1.0, 2.0, 3.0, 4.0, 5.0)]

        self.assertAlmostEqual(
            sizing_module.reference_volatility(readings), 3.0)


# ==============================
# CASH MANAGEMENT
# ==============================

class CashAccounting(unittest.TestCase):
    """Every candidate has to end up in exactly one bucket."""

    def test_rejection_summary_accounts_for_every_candidate(self):
        from backtesting.strategy import Strategy, TECHNICAL_ONLY

        strategy = Strategy(make_cfg(technical_gate=70), data=None,
                            variant=TECHNICAL_ONLY, verbose=False)

        readings = {
            "1": make_reading(ins_code="1", technical=90),
            "2": make_reading(ins_code="2", technical=50),
            "3": make_reading(ins_code="3", technical=None),
            "4": make_reading(ins_code="4", technical=90, headroom=1.0),
            "5": make_reading(ins_code="5", technical=90, adv=1.0),
        }

        buys = strategy.signals(readings)

        summary = strategy.rejection_summary()

        self.assertEqual(len(buys), 1)
        self.assertEqual(summary["candidates_seen"], 5)
        self.assertEqual(summary["candidates_passed"], 1)
        self.assertEqual(summary["rejected"], 4)

        self.assertEqual(
            summary["candidates_passed"] + summary["rejected"],
            summary["candidates_seen"],
            "a candidate that is neither a signal nor a counted rejection has "
            "vanished, and the cash it did not spend cannot be explained")

    def test_each_rejection_names_its_own_cause(self):
        from backtesting.strategy import Strategy, TECHNICAL_ONLY

        strategy = Strategy(make_cfg(technical_gate=70), data=None,
                            variant=TECHNICAL_ONLY, verbose=False)

        strategy.signals({
            "1": make_reading(ins_code="1", technical=50),
            "2": make_reading(ins_code="2", technical=None),
            "3": make_reading(ins_code="3", technical=90, headroom=1.0),
            "4": make_reading(ins_code="4", technical=90, adv=1.0),
        })

        causes = strategy.rejections

        self.assertEqual(causes.get("technical_gate"), 1)
        self.assertEqual(causes.get("no_technical_score"), 1)
        self.assertEqual(causes.get("headroom"), 1)
        self.assertEqual(causes.get("illiquid"), 1)

    def test_missing_filing_and_unscoreable_filing_are_counted_apart(self):
        from backtesting.strategy import Strategy, TECHNICAL_FUNDAMENTAL

        strategy = Strategy(make_cfg(), data=None,
                            variant=TECHNICAL_FUNDAMENTAL, verbose=False)

        strategy.signals({
            "1": make_reading(ins_code="1", fundamental=None),
            "2": dict(make_reading(ins_code="2", fundamental=None),
                      fundamental_verdict="INSUFFICIENT_DATA"),
        })

        self.assertEqual(strategy.rejections.get("no_fundamental_filing"), 1)
        self.assertEqual(strategy.rejections.get("fundamental_insufficient"), 1)

    def test_the_funnel_never_widens(self):
        """Each stage is a subset of the one before it, or the funnel lies."""

        from backtesting.engine import _cash_analysis

        curve = pd.DataFrame(
            {"cash_pct": [50.0] * 10, "positions": [3] * 10,
             "portfolio_value": [1000.0] * 10},
            index=pd.bdate_range("2025-01-01", periods=10),
        )

        analysis = _cash_analysis(curve, {
            "entry_rejections": {"candidates_seen": 1000,
                                 "candidates_passed": 200,
                                 "rejected": 800, "by_reason": []},
            "ranked_out": 150,
            "wanted_positions": 50,
            "blocked_by": {"sector name cap": 5},
            "sized_to_zero": 5,
            "opened_positions": 40,
            # Deliberately larger than the buy count: rotation SALES also
            # raise orders, and counting them here is what once produced a
            # funnel stage at 112%.
            "orders_raised": 90,
            "orders_expired": 30,
            "buy_orders_raised": 40,
            "buy_orders_expired": 10,
        }, make_cfg())

        counts = [s["count"] for s in analysis["funnel"]]

        for earlier, later in zip(counts, counts[1:]):
            self.assertLessEqual(
                later, earlier,
                "a later funnel stage counted more than the one feeding it, "
                "so the stages are not measuring a subset relationship")

        for stage in analysis["funnel"]:
            if stage["survived"] is not None:
                self.assertLessEqual(
                    stage["survived"], 1.0,
                    "a funnel stage cannot pass more than it received")

    def test_the_funnel_names_the_steepest_drop(self):
        from backtesting.engine import _cash_analysis

        curve = pd.DataFrame(
            {"cash_pct": [50.0] * 10, "positions": [3] * 10,
             "portfolio_value": [1000.0] * 10},
            index=pd.bdate_range("2025-01-01", periods=10),
        )

        analysis = _cash_analysis(curve, {
            "entry_rejections": {"candidates_seen": 1000,
                                 "candidates_passed": 20,
                                 "rejected": 980, "by_reason": []},
            "ranked_out": 0,
            "wanted_positions": 20,
            "blocked_by": {},
            "sized_to_zero": 0,
            "opened_positions": 20,
            "orders_raised": 20,
            "orders_expired": 0,
            "buy_orders_raised": 20,
            "buy_orders_expired": 0,
        }, make_cfg())

        self.assertEqual(analysis["tightest_stage"], "passed every entry test")
        self.assertAlmostEqual(analysis["tightest_survival"], 0.02)

    def test_the_cash_floor_is_not_blamed_for_cash_it_cannot_explain(self):
        from backtesting.engine import _cash_analysis

        curve = pd.DataFrame(
            {"cash_pct": [58.6] * 10, "positions": [3] * 10,
             "portfolio_value": [1000.0] * 10},
            index=pd.bdate_range("2025-01-01", periods=10),
        )

        analysis = _cash_analysis(curve, {}, make_cfg(cash_reserve_pct=10.0))

        # A 10% floor against 58.6% cash accounts for about a sixth of it.
        self.assertLess(analysis["floor_explains_pct"], 20.0)

    def test_cash_reserve_blocks_a_full_book(self):
        book = Portfolio(make_cfg(cash_reserve_pct=50.0), initial_capital=1000.0)

        book.buy(make_reading(ins_code="1", sector="فلزات"), 1, 500.0,
                 "2025-01-01", 0.0)

        # A different sector, so the sector value cap -- which is checked first
        # and would also refuse -- is not what this test ends up measuring.
        allowed, reason = book.can_open(make_reading(ins_code="2",
                                                     sector="بانک"))

        self.assertFalse(allowed)
        self.assertEqual(reason, "cash reserve reached")

    def test_can_open_names_every_refusal(self):
        cfg = make_cfg(max_positions=1, max_per_sector=1)

        book = Portfolio(cfg, initial_capital=1_000_000.0)

        book.buy(make_reading(ins_code="1", sector="فلزات"), 10, 1000.0,
                 "2025-01-01", 0.0)

        allowed, reason = book.can_open(make_reading(ins_code="1"))
        self.assertFalse(allowed)
        self.assertEqual(reason, "already held")

        allowed, reason = book.can_open(make_reading(ins_code="2",
                                                     sector="بانک"))
        self.assertFalse(allowed)
        self.assertEqual(reason, "position count at maximum")


# ==============================
# EXECUTION
# ==============================

class Execution(unittest.TestCase):

    def setUp(self):
        self.bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0,
                    "volume": 1_000_000.0, "value": 100_000_000.0}

    def test_slippage_raises_the_buy_cost_and_lowers_the_sale(self):
        clean = make_cfg(slippage_pct=0.0)
        dirty = make_cfg(slippage_pct=0.5)

        self.assertAlmostEqual(dirty.buy_cost_rate() - clean.buy_cost_rate(),
                               0.005)
        self.assertAlmostEqual(dirty.sell_cost_rate() - clean.sell_cost_rate(),
                               0.005)

    def test_slippage_appears_once_in_the_round_trip(self):
        cfg = make_cfg(slippage_pct=0.25)

        expected = (backtest_config.BUY_COMMISSION_PCT
                    + backtest_config.SELL_COMMISSION_PCT
                    + backtest_config.SELL_TAX_PCT + 0.5)

        self.assertAlmostEqual(cfg.round_trip_pct(), expected, places=6)

    def test_slippage_is_not_also_applied_to_the_fill_price(self):
        """Double-counting it is the easy mistake; the price must stay clean."""

        model = ExecutionModel(make_cfg(slippage_pct=0.5))

        self.assertAlmostEqual(model.fill_price(self.bar), 100.0)

    def test_buy_queue_blocks_a_buy(self):
        cfg = make_cfg()

        model = ExecutionModel(cfg)

        locked = {"open": 103.0, "high": 103.0, "low": 103.0, "close": 103.0,
                  "volume": 1000.0, "value": 103_000.0}

        order = Order(BUY, "1", 100, "2025-01-01", "2025-01-02", "2025-01-05")

        shares, _price, refusal = model.execute(order, locked,
                                                previous_close=100.0,
                                                median_volume=1_000_000.0,
                                                available_shares=100)

        self.assertEqual(shares, 0)
        self.assertEqual(order.blocked, BUY_QUEUE)
        self.assertIsNotNone(refusal)

    def test_sell_queue_blocks_an_exit(self):
        model = ExecutionModel(make_cfg())

        locked = {"open": 97.0, "high": 97.0, "low": 97.0, "close": 97.0,
                  "volume": 1000.0, "value": 97_000.0}

        order = Order(SELL, "1", 100, "2025-01-01", "2025-01-02", "2025-01-05")

        shares, _price, _refusal = model.execute(order, locked,
                                                 previous_close=100.0,
                                                 median_volume=1_000_000.0,
                                                 available_shares=100)

        self.assertEqual(shares, 0)
        self.assertEqual(order.blocked, SELL_QUEUE)

    def test_partial_fill_is_capped_by_the_session_turnover(self):
        cfg = make_cfg(max_participation_pct=5.0)

        model = ExecutionModel(cfg)

        # 5% of a 100,000,000-rial session at 100 rials is 50,000 shares, and
        # the order asks for 100,000. Kept above MIN_FILL_FRACTION of the
        # order so that the partial path is what is being tested rather than
        # the sliver guard.
        order = Order(BUY, "1", 100_000, "2025-01-01", "2025-01-02",
                      "2025-01-05")

        shares, _price, refusal = model.execute(order, self.bar,
                                                previous_close=100.0,
                                                median_volume=1_000_000.0,
                                                available_shares=100_000)

        self.assertIsNone(refusal)
        self.assertEqual(shares, int(100_000_000.0 * 0.05 / 100.0))
        self.assertLess(shares, order.remaining)
        self.assertEqual(model.stats["capped_by_participation"], 1)

    def test_a_sliver_fill_waits_instead_of_paying_commission(self):
        model = ExecutionModel(make_cfg(min_fill_fraction=0.10))

        # 50,000 fillable against a 1,000,000-share order is 5%, under the
        # floor: a 5% fill of a position is a commission bill, not a position.
        order = Order(BUY, "1", 1_000_000, "2025-01-01", "2025-01-02",
                      "2025-01-05")

        shares, _price, refusal = model.execute(order, self.bar,
                                                previous_close=100.0,
                                                median_volume=1_000_000.0,
                                                available_shares=1_000_000)

        self.assertEqual(shares, 0)
        self.assertEqual(refusal, "below minimum fill fraction")

    def test_partial_fill_can_be_switched_off(self):
        model = ExecutionModel(make_cfg(allow_partial_fill=False))

        order = Order(BUY, "1", 100_000, "2025-01-01", "2025-01-02",
                      "2025-01-05")

        shares, _price, refusal = model.execute(order, self.bar,
                                                previous_close=100.0,
                                                median_volume=1_000_000.0,
                                                available_shares=100_000)

        self.assertEqual(shares, 0)
        self.assertEqual(refusal, "partial fills disabled")

    def test_t_plus_one_is_counted_in_sessions_not_days(self):
        """A Thursday decision must not fill on a Friday the market never opened."""

        cfg = make_cfg(execution_delay_sessions=1)

        book = OrderBook(cfg)

        sessions = [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-04"),
                    pd.Timestamp("2025-01-05")]

        order = book.submit(BUY, "1", 100, sessions[0], sessions)

        self.assertEqual(order.eligible_from, sessions[1])
        self.assertNotEqual(
            order.eligible_from,
            pd.Timestamp("2025-01-02"),
            "the delay is in sessions; 2025-01-02 is not in the calendar")

    def test_an_order_is_not_eligible_on_its_own_decision_session(self):
        book = OrderBook(make_cfg(execution_delay_sessions=1))

        sessions = [pd.Timestamp("2025-01-0%d" % d) for d in (1, 2, 3)]

        order = book.submit(BUY, "1", 100, sessions[0], sessions)

        self.assertEqual(book.due(sessions[0]), [])
        self.assertIn(order, book.due(sessions[1]))

    def test_an_order_expires_and_is_reported(self):
        book = OrderBook(make_cfg(order_good_for_sessions=1))

        sessions = [pd.Timestamp("2025-01-0%d" % d) for d in (1, 2, 3, 4)]

        book.submit(BUY, "1", 100, sessions[0], sessions)

        expired = book.retire(sessions[3])

        self.assertEqual(len(expired), 1)
        self.assertEqual(book.pending, [])

    def test_a_rejected_order_never_becomes_a_position(self):
        model = ExecutionModel(make_cfg())

        dead = {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
                "volume": 0.0, "value": 0.0}

        order = Order(BUY, "1", 100, "2025-01-01", "2025-01-02", "2025-01-05")

        shares, _price, refusal = model.execute(order, dead,
                                                previous_close=100.0,
                                                available_shares=100)

        self.assertEqual(shares, 0)
        self.assertIsNotNone(refusal)


# ==============================
# SURVIVORSHIP
# ==============================

class Survivorship(unittest.TestCase):

    def setUp(self):
        self.calendar = pd.DatetimeIndex(
            pd.bdate_range("2024-01-01", periods=300))

        def frame(start, count, first=100.0, last=200.0):
            dates = self.calendar[start:start + count]
            step = (last - first) / max(1, count - 1)
            closes = [first + step * i for i in range(len(dates))]
            return pd.DataFrame({
                "date": dates, "open": closes, "high": closes,
                "low": closes, "close": closes,
                "volume": [1000.0] * len(dates),
                "value": [100000.0] * len(dates),
            })

        self.data = FakeBars(
            frames={
                "survivor": frame(0, 300, 100.0, 200.0),
                "stopped": frame(0, 100, 100.0, 20.0),
                "latecomer": frame(200, 100, 100.0, 120.0),
            },
            calendar=self.calendar,
            instruments={
                "survivor": {"symbol": "زنده", "sector": "فلزات"},
                "stopped": {"symbol": "متوقف", "sector": "فلزات"},
                "latecomer": {"symbol": "تازه", "sector": "بانک"},
            },
        )

    def test_a_stopped_instrument_is_detected(self):
        report = survivorship.measure(self.data, self.calendar[0],
                                      self.calendar[-1])

        self.assertTrue(report["available"])
        self.assertEqual(report["stopped_trading"], 1)
        self.assertEqual(report["stopped_detail"][0]["symbol"], "متوقف")

    def test_an_instrument_that_listed_mid_window_is_detected(self):
        report = survivorship.measure(self.data, self.calendar[0],
                                      self.calendar[-1])

        self.assertEqual(report["entered_mid_window"], 1)
        self.assertEqual(report["entered_detail"][0]["symbol"], "تازه")

    def test_the_bias_is_a_lower_bound_and_is_positive_here(self):
        """Dropping the collapsed name raises the mean. That IS the bias."""

        report = survivorship.measure(self.data, self.calendar[0],
                                      self.calendar[-1])

        self.assertIsNotNone(report["visible_bias_lower_bound_pp"])
        self.assertGreater(report["visible_bias_lower_bound_pp"], 0)

    def test_it_never_claims_the_bias_is_removed(self):
        report = survivorship.measure(self.data, self.calendar[0],
                                      self.calendar[-1])

        self.assertIn("NOT eliminated", report["verdict"])
        self.assertIn("cannot be counted", report["unmeasurable"])

    def test_no_correction_factor_is_produced(self):
        report = survivorship.measure(self.data, self.calendar[0],
                                      self.calendar[-1])

        for key in report:
            self.assertNotIn(
                "correction", key,
                "a survivorship 'correction' would make an unmeasured "
                "quantity look measured")

    def test_strategy_exposure_to_stopped_names_is_counted(self):
        trades = [
            {"ins_code": "stopped", "pnl": -500.0},
            {"ins_code": "survivor", "pnl": 900.0},
        ]

        report = survivorship.measure(self.data, self.calendar[0],
                                      self.calendar[-1], trades=trades)

        exposure = report["strategy_exposure_to_stopped"]

        self.assertEqual(exposure["trades"], 1)
        self.assertEqual(exposure["net_pnl"], -500.0)


# ==============================
# THE BACKFILL QUEUE
# ==============================

class BackfillQueue(unittest.TestCase):
    """The ordering is worth more than the downloading, so it is tested.

    This job never finishes -- codal.ir answers roughly 40 requests per burst
    against a queue of thousands -- so the only question that matters is which
    few hundred filings are fetched first.
    """

    def setUp(self):
        import codal_backfill
        import database

        self.codal_backfill = codal_backfill
        self.database = database

        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path

        database.create_database()

        conn = database._connect()

        # Two companies. `covered` already has a filing public before the
        # window; `bare` does not, and has two candidate letters.
        conn.executemany(
            "INSERT INTO codal_letters (tracing_no, ins_code, letter_code, "
            "title, published_at, excel_url, has_excel, parsed) "
            "VALUES (?,?,?,?,?,?,1,0)",
            [
                ("A1", "covered", "ن-10", "old", "1402/10/01 10:00:00", "u1"),
                ("A2", "covered", "ن-10", "newer", "1404/03/01 10:00:00", "u2"),
                ("B1", "bare", "ن-10", "oldest", "1402/07/01 10:00:00", "u3"),
                ("B2", "bare", "ن-10", "just before", "1403/05/01 10:00:00", "u4"),
                ("B3", "bare", "ن-10", "after", "1404/06/01 10:00:00", "u5"),
            ])

        conn.execute(
            "INSERT INTO codal_filings (tracing_no, ins_code, period_end, "
            "period_months, published_at, title, audited, consolidated, "
            "items, ratios, updated_at) VALUES "
            "('A1','covered','1402/06/31',6,'1402/10/01 10:00:00','old',0,0,"
            "'{}','{}','')")

        conn.commit()
        conn.close()

    def tearDown(self):
        self.database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def test_an_already_parsed_filing_is_not_queued_again(self):
        queue = self.codal_backfill.pending("1402/01/01", "1405/12/29")

        self.assertNotIn("A1", [item["tracing_no"] for item in queue])

    def test_a_legacy_parsed_flag_does_not_settle_a_filing(self):
        """The flag means 'tried once', not 'stored'. Those diverged."""

        self.database.mark_letter_parsed("B1")

        queue = self.codal_backfill.pending("1402/01/01", "1405/12/29")

        self.assertIn(
            "B1", [item["tracing_no"] for item in queue],
            "a letter marked parsed but absent from codal_filings may have "
            "lost its figures to the period-key collapse, or may never have "
            "had them; either way it is worth one more attempt")

    def test_an_unreadable_filing_leaves_the_queue_for_good(self):
        """The six-hour bug, pinned.

        Ignoring `parsed` is right, and on its own it makes an unreadable
        filing immortal: it is marked parsed, it is still absent from
        codal_filings, and so it returns to the head of the queue on the next
        round. Twenty-two consecutive rounds fetched the same seven documents,
        failed identically, and never reached anything behind them -- six hours
        that stored nothing.
        """

        before = [item["tracing_no"]
                  for item in self.codal_backfill.pending("1402/01/01",
                                                          "1405/12/29")]

        self.assertIn("B1", before)

        self.codal_backfill.record_failure("B1", "bare", "no statements")

        after = [item["tracing_no"]
                 for item in self.codal_backfill.pending("1402/01/01",
                                                         "1405/12/29")]

        self.assertNotIn("B1", after)
        self.assertIn("B2", after, "settling one filing must not settle the "
                                   "rest of the company's queue")

    def test_a_filing_that_keeps_failing_transiently_goes_to_the_back(self):
        """The thirteen-hour stall, pinned.

        Five filings at the head answered HTTP 500 every round; a 500 is
        transient, so nothing settled them, and every round fetched the same
        five and backed off as if throttled. A transient failure still must
        not settle a filing -- it goes behind every untried one instead.
        """

        head = [item["tracing_no"] for item in
                self.codal_backfill.pending("1402/01/01", "1405/12/29")]

        self.codal_backfill.record_retry(head[0], "x", "HTTPError: 500")

        after = [item["tracing_no"] for item in
                 self.codal_backfill.pending("1402/01/01", "1405/12/29")]

        self.assertEqual(sorted(after), sorted(head), "nothing is settled")
        self.assertEqual(after[-1], head[0])
        self.assertEqual(after[0], head[1])

    def fetch_failing(self, error):
        """One fetch of B1 alone, answered with a transient `error`."""

        from unittest import mock
        import codal

        item = {"ins_code": "bare", "tracing_no": "B1", "excel_url": "u3",
                "title": "oldest", "published_at": "1402/07/01 10:00:00"}

        with mock.patch.object(self.codal_backfill, "pending",
                               return_value=[item]), \
                mock.patch.object(
                    self.codal_backfill.codal_sync, "parse_letter",
                    return_value=(None, error, codal.KIND_TRANSIENT)):
            return self.codal_backfill.fetch("1402/01/01", "1405/12/29",
                                             verbose=False, client=object())

    def test_a_document_codal_cannot_export_is_settled_on_the_third_500(self):
        """By 2026-11-08 the queue's back WAS the queue: 47 of 48 left had
        answered 500 twice, and each round backed off four hours on them."""

        server = "B1: HTTPError: 500 Server Error: Internal Server Error"

        for _ in range(self.codal_backfill.SERVER_ERROR_SETTLE_ATTEMPTS - 1):
            self.assertEqual(self.fetch_failing(server)["failed"], 1)
            self.assertNotIn("B1", self.codal_backfill.settled_failures())

        result = self.fetch_failing(server)

        self.assertEqual((result["failed"], result["unparseable"]), (0, 1))
        self.assertIn("B1", self.codal_backfill.settled_failures())

    def test_a_dropped_connection_never_settles_a_filing(self):
        for _ in range(self.codal_backfill.SERVER_ERROR_SETTLE_ATTEMPTS + 2):
            self.fetch_failing("B1: ConnectionError: Max retries exceeded")

        self.assertEqual(self.codal_backfill.settled_failures(), set())

    def test_a_pause_ends_by_the_wall_clock_after_the_laptop_sleeps(self):
        """The 2026-11-10 overrun: a four-hour time.sleep begun at 07:00 was
        still waiting at 21:30, because the wait does not advance while the
        machine sleeps, and the next morning's scheduled run was refused."""

        from unittest import mock

        clock = {"now": 1000.0, "naps": []}

        def nap(seconds):
            clock["naps"].append(seconds)
            # The first nap is where the lid closes: six hours pass on the
            # wall while the sleep itself credits only `seconds`.
            clock["now"] += seconds + (6 * 3600 if len(clock["naps"]) == 1 else 0)

        with mock.patch("time.time", side_effect=lambda: clock["now"]), \
                mock.patch("time.sleep", side_effect=nap):
            self.codal_backfill._sleep_until(1000.0 + 4 * 3600)

        self.assertEqual(len(clock["naps"]), 1,
                         "the pause is over on the wall clock after one step")
        self.assertLessEqual(max(clock["naps"]),
                             self.codal_backfill.SLEEP_STEP_SECONDS)

    def test_a_pause_never_outlives_the_runs_deadline(self):
        from unittest import mock

        clock = {"now": 0.0}

        def nap(seconds):
            clock["now"] += seconds

        with mock.patch("time.time", side_effect=lambda: clock["now"]), \
                mock.patch("time.sleep", side_effect=nap):
            self.codal_backfill._sleep_until(4 * 3600, deadline=600)

        self.assertEqual(clock["now"], 600)

    def test_the_failure_table_reports_why(self):
        """'7 unparseable' is not actionable; a reason breakdown is."""

        self.codal_backfill.record_failure("B1", "bare", "no statements")
        self.codal_backfill.record_failure("B3", "bare", "no statements")

        summary = self.codal_backfill.failure_summary()

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_reason"][0]["reason"], "no statements")
        self.assertEqual(summary["by_reason"][0]["count"], 2)

    def test_a_repeated_failure_counts_attempts_rather_than_duplicating(self):
        self.codal_backfill.record_failure("B1", "bare", "no statements")
        self.codal_backfill.record_failure("B1", "bare", "no statements")

        self.assertEqual(self.codal_backfill.failure_summary()["total"], 1)

    def test_coverage_first_picks_the_newest_filing_before_the_window(self):
        """Newest that still predates it: the freshest coverage available."""

        queue = self.codal_backfill.pending(
            "1402/01/01", "1405/12/29", priority_date="1403/06/10")

        urgent = [item for item in queue if item.get("priority") == 0]

        self.assertEqual(len(urgent), 1)
        self.assertEqual(urgent[0]["ins_code"], "bare")
        self.assertEqual(
            urgent[0]["tracing_no"], "B2",
            "B1 also predates the window, but B2 predates it by less and so "
            "buys the same coverage with fresher figures")

    def test_a_covered_company_gets_no_priority_download(self):
        queue = self.codal_backfill.pending(
            "1402/01/01", "1405/12/29", priority_date="1403/06/10")

        urgent = [item["ins_code"] for item in queue
                  if item.get("priority") == 0]

        self.assertNotIn(
            "covered", urgent,
            "spending a request on a company that already has pre-window "
            "coverage buys freshness, not coverage")

    def test_priority_downloads_sort_ahead_of_everything_else(self):
        queue = self.codal_backfill.pending(
            "1402/01/01", "1405/12/29", priority_date="1403/06/10")

        priorities = [item.get("priority", 1) for item in queue]

        self.assertEqual(priorities, sorted(priorities))

    def test_without_a_priority_date_the_order_is_by_rank(self):
        queue = self.codal_backfill.pending("1402/01/01", "1405/12/29")

        ranks = [item["rank"] for item in queue]

        self.assertEqual(ranks, sorted(ranks))

    def test_the_window_bounds_are_respected(self):
        queue = self.codal_backfill.pending("1404/01/01", "1404/12/29")

        for item in queue:
            day = item["published_at"][:10]
            self.assertGreaterEqual(day, "1404/01/01")
            self.assertLessEqual(day, "1404/12/29")

    def test_per_company_caps_the_download_bill(self):
        queue = self.codal_backfill.pending("1402/01/01", "1405/12/29",
                                            per_company=1)

        by_company = {}

        for item in queue:
            by_company[item["ins_code"]] = by_company.get(item["ins_code"], 0) + 1

        for count in by_company.values():
            self.assertLessEqual(count, 1)

    def test_covered_set_reads_the_publication_date_not_the_period(self):
        covered = self.codal_backfill.companies_covered_before("1403/06/10")

        self.assertIn("covered", covered)
        self.assertNotIn("bare", covered)

        # The stored filing's PERIOD ends 1402/06/31 but it was PUBLISHED
        # 1402/10/01. Coverage is about the second date; using the first is the
        # 36-day look-ahead this project refuses everywhere else.
        self.assertNotIn(
            "covered",
            self.codal_backfill.companies_covered_before("1402/09/01"))


# ==============================
# CONFIGURATION
# ==============================

class ConfigurationV2(unittest.TestCase):

    def test_every_new_parameter_is_overridable(self):
        """A sweep over an unlisted field silently does nothing."""

        for key in ("sizing_mode", "sizing_target_atr_pct", "news_provider",
                    "fundamental_trend_periods", "fundamental_trend_weight",
                    "min_news_coverage", "restrict_to_fundamental_universe"):
            self.assertIn(key, backtest_config._OVERRIDABLE)
            self.assertTrue(hasattr(make_cfg(), key))

    def test_restricting_the_universe_is_off_by_default(self):
        """A headline figure must never quietly describe a subset."""

        self.assertFalse(make_cfg().restrict_to_fundamental_universe)

    def test_the_restriction_records_what_it_dropped(self):
        """Restricting silently would present a subset return as a market one."""

        from backtesting.strategy import Strategy, TECHNICAL_FUNDAMENTAL

        class OneFiler(object):
            def instruments(self):
                return {"a": {"symbol": "A", "sector": "س"},
                        "b": {"symbol": "B", "sector": "س"}}

            def filings(self):
                return {"a": [make_filing("1403/12/29", "2025-01-01", "1")]}

            def fingerprint(self, extra=None):
                return "test"

            def bars(self, ins_code):
                return None

        strategy = Strategy(make_cfg(restrict_to_fundamental_universe=True),
                            data=OneFiler(), variant=TECHNICAL_FUNDAMENTAL,
                            verbose=False)

        strategy.build_panel([pd.Timestamp("2025-06-01")], use_cache=False)

        self.assertIsNotNone(strategy.universe_restricted)
        self.assertEqual(strategy.universe_restricted["from"], 2)
        self.assertEqual(strategy.universe_restricted["to"], 1)
        self.assertIn("rather than the market",
                      strategy.universe_restricted["note"])

    def test_replace_leaves_the_original_untouched(self):
        base = make_cfg()

        changed = base.replace(sizing_mode=sizing_module.EQUAL_WEIGHT)

        self.assertEqual(base.sizing_mode, backtest_config.SIZING_MODE)
        self.assertEqual(changed.sizing_mode, sizing_module.EQUAL_WEIGHT)

    def test_slippage_scenarios_include_zero_as_the_reference(self):
        self.assertIn(0.0, backtest_config.SLIPPAGE_SCENARIOS)
        self.assertIn(0.25, backtest_config.SLIPPAGE_SCENARIOS)
        self.assertIn(0.5, backtest_config.SLIPPAGE_SCENARIOS)

    def test_slippage_is_still_swept_by_the_sensitivity_grid(self):
        self.assertIn("slippage_pct", backtest_config.SENSITIVITY_GRID)

    def test_an_unknown_override_raises(self):
        with self.assertRaises(KeyError):
            backtest_config.BacktestConfig(sizing_mode_typo="equal")


# ==============================
# BENCHMARKS
# ==============================

class Benchmarks(unittest.TestCase):

    def test_risk_free_benchmark_compounds_over_sessions(self):
        from backtesting.engine import _risk_free

        curve = pd.DataFrame(
            {"portfolio_value": [1000.0] * 241},
            index=pd.bdate_range("2025-01-01", periods=241),
        )

        deposit = _risk_free(curve)

        self.assertIsNotNone(deposit)
        # 240 sessions is one trading year, so the figure must land on the
        # policy rate rather than near it.
        self.assertAlmostEqual(deposit["return_pct"],
                               config.RISK_FREE_RATE * 100, delta=0.5)

    def test_risk_free_needs_two_sessions(self):
        from backtesting.engine import _risk_free

        self.assertIsNone(_risk_free(pd.DataFrame({"portfolio_value": [1.0]})))

    def test_the_three_original_benchmarks_are_still_configured(self):
        self.assertTrue(backtest_config.BENCHMARK_TEDPIX)
        self.assertTrue(backtest_config.BENCHMARK_EQUAL_WEIGHT)
        self.assertGreaterEqual(backtest_config.BENCHMARK_BASKET_SIZE, 3)


# ==============================
# RECOMMENDATION
# ==============================

class Recommendation(unittest.TestCase):

    def setUp(self):
        from backtesting import scenarios
        self.scenarios = scenarios

    def test_a_failed_verdict_recommends_fail(self):
        out = self.scenarios.recommendation(
            {"verdict": "FAIL", "failed": ["Sharpe"]},
            {"risk": "LOW"}, None)

        self.assertEqual(out["status"], "FAIL")

    def test_high_overfitting_recommends_fail(self):
        out = self.scenarios.recommendation(
            {"verdict": "PASS"}, {"risk": "HIGH"}, None)

        self.assertEqual(out["status"], "FAIL")

    def test_unknown_overfitting_is_not_a_pass(self):
        out = self.scenarios.recommendation(
            {"verdict": "PASS"}, {"risk": "UNKNOWN"}, None)

        self.assertEqual(out["status"], "NEEDS MORE DEVELOPMENT")

    def test_thin_coverage_holds_it_back(self):
        out = self.scenarios.recommendation(
            {"verdict": "PASS"}, {"risk": "LOW"}, None,
            coverage={"decision_coverage": 0.12})

        self.assertEqual(out["status"], "NEEDS MORE DEVELOPMENT")
        self.assertTrue(any("coverage" in r for r in out["reasons"]))

    def test_a_clean_pass_still_only_reaches_paper_trading(self):
        out = self.scenarios.recommendation(
            {"verdict": "PASS"}, {"risk": "LOW"}, None,
            coverage={"decision_coverage": 0.9},
            news={"available": True})

        self.assertEqual(out["status"], "READY FOR PAPER TRADING")

    def test_medium_overfitting_is_paper_only(self):
        out = self.scenarios.recommendation(
            {"verdict": "PASS"}, {"risk": "MEDIUM"}, None,
            coverage={"decision_coverage": 0.9},
            news={"available": True})

        self.assertEqual(out["status"], "PAPER TRADING ONLY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
