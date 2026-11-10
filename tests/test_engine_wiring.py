"""Is the decision engine the caller asked for the one that produced the book?

THE BUG THESE TESTS EXIST FOR
-----------------------------
Every v2 engine was fully built, fully tested and reachable only by running
swing_advisor.py by hand. `advisor.run()`, `main.py` and the panel all ran the v1
chain, so a change to a Swing Score weight moved nothing a user could see -- a
full panel run called into factor_engine exactly ZERO times, and setting the
momentum weight to 50% moved 0 of 300 scores. Nothing failed. Every test passed.
The engine simply was not plugged in.

Unit tests cannot catch that by construction: each one imports the module it
tests, so it proves the module works and says nothing about whether anything
calls it. These tests assert the WIRING instead, and they do it the only way that
cannot be faked -- by counting calls into the real engines during a real run, and
by changing an input and requiring the output to move.

    1. the engine the caller asked for is the one that runs
    2. changing a factor input changes the Swing Score
    3. the Swing Score is the number the book is ranked by
    4. the ranking decides which names are selected
    5. one engine's cached book can never be served for the other
    6. paper trading raises orders from the engine it was asked for

They use injected data, not the database: the point is the connection between
the stages, and a test that needs 800 symbols of price history to prove a
function is called is a test nobody runs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import advisor
import config
import factor_engine
import portfolio_engine
import regime_engine


class CallCounter:
    """Replace a module attribute with a counting wrapper, and put it back."""

    def __init__(self, *targets):
        self.targets = targets
        self.counts = {}
        self._originals = []

    def __enter__(self):

        for module, name in self.targets:

            original = getattr(module, name)
            key = "%s.%s" % (module.__name__, name)

            self.counts[key] = 0
            self._originals.append((module, name, original))

            def make(key=key, original=original):
                def wrapper(*args, **kwargs):
                    self.counts[key] += 1
                    return original(*args, **kwargs)
                return wrapper

            setattr(module, name, make())

        return self

    def __exit__(self, *exc):
        for module, name, original in self._originals:
            setattr(module, name, original)
        return False

    def total(self):
        return sum(self.counts.values())


# ==============================
# 1. THE ENGINE THE CALLER ASKED FOR
# ==============================

class EngineSelectionTest(unittest.TestCase):

    def test_both_engines_are_selectable_by_name(self):
        self.assertEqual(advisor.engine_name("v1"), advisor.V1)
        self.assertEqual(advisor.engine_name("v2"), advisor.V2)
        self.assertEqual(advisor.engine_name("V2"), advisor.V2)

    def test_an_unknown_name_falls_back_rather_than_raising(self):
        """This is read from a query string; a typo must not 500 the panel."""

        self.assertEqual(advisor.engine_name("v9"), advisor.V1)
        self.assertEqual(advisor.engine_name("swing"), advisor.V1)

    def test_no_choice_means_the_configured_default(self):
        """Asserted against config rather than against a literal.

        A test that hardcodes today's default turns a deliberate change of
        default into a red suite, which teaches people to edit the test.
        """

        for default in ("v1", "v2"):

            original = config.DECISION_ENGINE

            try:
                config.DECISION_ENGINE = default
                self.assertEqual(advisor.engine_name(None), default)
                self.assertEqual(advisor.engine_name(""), default)
            finally:
                config.DECISION_ENGINE = original

    def test_the_default_comes_from_config_and_can_be_moved(self):
        original = config.DECISION_ENGINE
        try:
            config.DECISION_ENGINE = "v2"
            self.assertEqual(advisor.engine_name(None), advisor.V2)
            config.DECISION_ENGINE = "v1"
            self.assertEqual(advisor.engine_name(None), advisor.V1)
        finally:
            config.DECISION_ENGINE = original

    def test_asking_for_v2_reaches_the_v2_chain(self):
        """The regression itself: v2 selected must mean v2 called.

        swing_advisor.scan is stubbed, so this asserts the ROUTING rather than
        re-running an 800-symbol scan. If advisor.run() ever stops dispatching
        to the swing chain, the stub is not called and this fails.
        """

        import swing_advisor

        seen = {}

        original = swing_advisor.scan

        def fake_scan(**kwargs):
            seen.update(kwargs)
            return {
                "regime": {"available": False},
                "policy": {"max_positions": 10, "max_per_sector": 2},
                "signals": [], "buys": [], "holdings": [],
                "book": {"accepted": [], "rejected": [], "reason": "stub"},
                "scanned": 0, "skipped": 0, "fundamental_store": True,
                "seconds": 0.0, "actions": {},
            }

        swing_advisor.scan = fake_scan

        try:
            report = advisor.run(engine="v2", equity=1e9)
        finally:
            swing_advisor.scan = original

        self.assertTrue(seen, "advisor.run(engine='v2') did not call the v2 scan")
        self.assertEqual(report["engine"], advisor.V2)
        self.assertEqual(seen.get("equity"), 1e9)

    def test_every_report_says_which_engine_produced_it(self):
        """A book with no engine field is a book nobody can attribute."""

        import swing_advisor

        original = swing_advisor.scan

        swing_advisor.scan = lambda **k: {
            "regime": {"available": False}, "policy": {},
            "signals": [], "buys": [], "holdings": [],
            "book": {"accepted": [], "rejected": []},
            "scanned": 0, "skipped": 0, "fundamental_store": False,
            "seconds": 0.0, "actions": {},
        }

        try:
            self.assertEqual(advisor.run(engine="v2")["engine"], advisor.V2)
        finally:
            swing_advisor.scan = original


# ==============================
# 2 & 3. INPUT -> SCORE -> FINAL SCORE
# ==============================

class ScoreReachesTheFinalScoreTest(unittest.TestCase):

    BASE = dict(momentum=60, technical=75, fundamental=55, liquidity=70,
                catalyst=None, regime=50)

    def test_moving_a_factor_moves_the_swing_score(self):
        for factor in ("momentum", "technical", "fundamental", "liquidity"):

            low = factor_engine.swing_score(**dict(self.BASE, **{factor: 20}))
            high = factor_engine.swing_score(**dict(self.BASE, **{factor: 95}))

            self.assertIsNotNone(low["score"], factor)
            self.assertGreater(high["score"], low["score"],
                               "%s does not reach the Swing Score" % factor)

    def test_moving_a_weight_moves_the_swing_score(self):
        """The exact failure the panel had: a weight change with no effect."""

        original = config.SWING_WEIGHT_MOMENTUM

        try:
            config.SWING_WEIGHT_MOMENTUM = 10
            quiet = factor_engine.swing_score(**dict(self.BASE, momentum=95))

            config.SWING_WEIGHT_MOMENTUM = 60
            loud = factor_engine.swing_score(**dict(self.BASE, momentum=95))
        finally:
            config.SWING_WEIGHT_MOMENTUM = original

        self.assertNotEqual(quiet["score"], loud["score"],
                            "the momentum weight changed nothing")

    def test_the_swing_score_is_what_the_decision_reads(self):
        weak = factor_engine.decide({"score": 30, "scored": True},
                                    technical=85)
        strong = factor_engine.decide({"score": 95, "scored": True},
                                      technical=85)

        self.assertEqual(weak["action"], factor_engine.AVOID)
        self.assertEqual(strong["action"], factor_engine.BUY)

    def test_evaluating_a_symbol_goes_through_the_blend(self):
        with CallCounter((factor_engine, "swing_score")) as counter:

            factor_engine.evaluate_symbol(
                frame=None, technical=80,
                regime={"available": True, "trading_mode": "NORMAL"},
            )

        self.assertGreater(counter.counts["factor_engine.swing_score"], 0,
                           "evaluate_symbol did not blend anything")

    def test_deciding_an_entry_consults_the_regime_engine(self):
        """The market read must reach the entry rule, not just the report."""

        with CallCounter((regime_engine, "entry_floor")) as counter:

            factor_engine.decide(
                {"score": 90, "scored": True}, technical=85,
                regime={"available": True,
                        "trading_mode": regime_engine.DEFENSIVE},
            )

        self.assertGreater(counter.counts["regime_engine.entry_floor"], 0,
                           "the regime's entry floor is not consulted")


# ==============================
# 4 & 5. RANKING -> SELECTION -> THE BOOK
# ==============================

class RankingReachesTheBookTest(unittest.TestCase):

    # 10% of this equity per name: above the 2% position floor and below the
    # 15% ceiling, so the allocator's own limits are not what is under test.
    EQUITY = 1_000_000_000.0

    def _signal(self, symbol, score, sector="فلز", value=100_000_000.0):
        return {
            "symbol": symbol, "ins_code": symbol, "sector": sector,
            "action": factor_engine.BUY, "swing_score": score,
            "conviction": 0.5, "entry_price": 1000.0,
            "position_value": value, "shares": int(value // 1000),
            "stop_loss": 950.0, "target_1": 1150.0, "risk_rials": value * 0.02,
            "swing": {"factors": {"technical": 80, "fundamental": 60}},
        }

    def test_the_allocator_takes_the_higher_ranked_name_first(self):
        policy = portfolio_engine.from_config().replace(max_positions=1,
                                                        max_per_sector=1)

        best = self._signal("بهترین", 90)
        worst = self._signal("بدترین", 70)

        book = portfolio_engine.allocate(policy, [best, worst], self.EQUITY)

        self.assertEqual([row["symbol"] for row in book["accepted"]],
                         ["بهترین"],
                         "rejected: %s" % [r["reason"] for r in book["rejected"]])

    def test_reversing_the_ranking_reverses_the_selection(self):
        """Ranking must decide selection, not merely accompany it."""

        policy = portfolio_engine.from_config().replace(max_positions=1,
                                                        max_per_sector=1)

        a = self._signal("الف", 90)
        b = self._signal("ب", 70)

        forward = portfolio_engine.allocate(policy, [a, b], self.EQUITY)
        reverse = portfolio_engine.allocate(policy, [b, a], self.EQUITY)

        self.assertNotEqual([r["symbol"] for r in forward["accepted"]],
                            [r["symbol"] for r in reverse["accepted"]])

    def test_the_v2_book_carries_the_swing_score_as_the_ranked_number(self):
        """The adapter must not invent a score of its own."""

        position = advisor._v2_position(
            dict(self._signal("نماد", 84), allocated_value=5e7,
                 allocated_shares=50_000),
            rank=1, equity=self.EQUITY)

        self.assertEqual(position["combined_score"], 84)
        self.assertEqual(position["rank_score"], 84)
        self.assertEqual(position["rank_basis"], "swing_score")
        self.assertEqual(position["technical_score"], 80)

    def test_the_adapter_reports_allocated_size_not_requested_size(self):
        """The allocator trims; a report of the untrimmed size is a wrong book."""

        position = advisor._v2_position(
            dict(self._signal("نماد", 84, value=1e8),
                 allocated_value=5e7, allocated_shares=50_000),
            rank=1, equity=self.EQUITY)

        self.assertEqual(position["position_value"], 5e7)
        self.assertAlmostEqual(position["weight_pct"], 5.0, places=2)
        # risk scales with the cut, rather than being reported at full size
        self.assertLess(position["risk_rials"], 1e8 * 0.02)

    def test_a_position_that_was_never_allocated_is_not_in_the_book(self):
        self.assertIsNone(advisor._v2_position(
            dict(self._signal("نماد", 84), allocated_value=0,
                 allocated_shares=0), rank=1, equity=self.EQUITY))


# ==============================
# 6. THE CACHE CANNOT CROSS ENGINES
# ==============================

class CacheIsolationTest(unittest.TestCase):
    """One engine's memoised book must never be served for the other."""

    def test_the_portfolio_cache_key_separates_the_engines(self):
        import app

        with app.app.test_request_context("/api/portfolio?engine=v1"):
            args = app.wanted_portfolio_args()
            v1_key = app.cache_key(
                "portfolio", advisor.engine_name("v1"), args["equity"],
                args["size"], args["max_per_sector"], args["min_score"],
                args["risk_pct"], True, app.params_key(app.wanted_params()))

        with app.app.test_request_context("/api/portfolio?engine=v2"):
            args = app.wanted_portfolio_args()
            v2_key = app.cache_key(
                "portfolio", advisor.engine_name("v2"), args["equity"],
                args["size"], args["max_per_sector"], args["min_score"],
                args["risk_pct"], True, app.params_key(app.wanted_params()))

        self.assertNotEqual(v1_key, v2_key,
                            "both engines would share one cache entry")

    def test_the_endpoint_builds_a_key_that_contains_the_engine(self):
        """Guards the wiring in app.api_portfolio itself, not a copy of it."""

        import inspect

        import app

        source = inspect.getsource(app.api_portfolio)

        self.assertIn('"portfolio", engine', source,
                      "the portfolio cache key does not include the engine")

        self.assertIn("advisor.engine_name(", source,
                      "the endpoint does not resolve an engine at all")

    def test_new_data_supersedes_a_cached_book(self):
        """A fetch that finishes must not leave the old basket on screen.

        Every import endpoint invalidates on START, but bootstrap finishes in
        its own thread minutes later -- so without this the pre-fetch book was
        served for the rest of the TTL after the new bars had landed.
        """

        import app
        import bootstrap

        original = bootstrap._state.get("finished_at")

        try:
            bootstrap._state["finished_at"] = "2026-08-15T09:00:00"
            before = app.data_version()

            bootstrap._state["finished_at"] = "2026-08-15T09:04:00"
            after = app.data_version()
        finally:
            bootstrap._state["finished_at"] = original

        self.assertNotEqual(before, after,
                            "a completed fetch does not change the cache key")

    def test_the_data_version_is_stable_between_fetches(self):
        """Otherwise the cache would never hit and every reload would rescan."""

        import app

        self.assertEqual(app.data_version(), app.data_version())

    def test_both_cached_endpoints_key_on_the_data_version(self):
        import inspect

        import app

        for view in (app.api_portfolio, app.api_screen):
            self.assertIn("data_version()", inspect.getsource(view),
                          "%s can serve a book built from replaced data"
                          % view.__name__)

    def test_a_forced_refresh_bypasses_the_cache(self):
        import app

        app.invalidate()

        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"value": calls["n"]}

        first = app.memoise("k", producer)
        again = app.memoise("k", producer)
        forced = app.memoise("k", producer, force=True)

        self.assertEqual(first["value"], 1)
        self.assertEqual(again["value"], 1, "the cache did not hold")
        self.assertEqual(forced["value"], 2, "refresh=1 did not bypass it")


# ==============================
# 7. PAPER TRADING RUNS THE ENGINE IT WAS ASKED FOR
# ==============================

class PaperEngineSelectionTest(unittest.TestCase):
    """The same disconnection, one path further down: paper.py ran v1 only.

    Everything here drives PaperTrader._decide directly with a stub scan and a
    stub strategy, so the assertion is about which engine is consulted rather
    than about what the market did today.
    """

    def _trader(self, engine):

        from backtesting import paper

        trader = paper.PaperTrader.__new__(paper.PaperTrader)

        trader.account = "test"
        trader.cfg = self._cfg()
        trader.verbose = False
        trader.variant = "technical"
        trader.engine = advisor.engine_name(engine)
        trader.data = None
        trader.strategy = None
        trader.filler = None

        trader.raised = []

        def record(side, ins_code, shares, date, calendar, **kwargs):
            trader.raised.append({"side": side, "ins_code": ins_code,
                                  "shares": shares, **kwargs})
            return True

        trader._raise_order = record

        return trader

    def _cfg(self):
        import backtest_config
        return backtest_config.BacktestConfig()

    class _Book:
        def __init__(self):
            self.positions = {}

        def value(self):
            return 1_000_000_000.0

    def _scan_result(self):
        return {
            "regime": {"trading_mode": "NORMAL", "position_multiplier": 0.85,
                       "entry_floor": 0},
            "signals": [{
                "ins_code": "111", "symbol": "الف", "action": "BUY",
                "swing_score": 88, "risk_score": 71, "target_1": 1150.0,
                "entry_price": 1000.0, "stop_loss": 950.0,
            }],
            "book": {"accepted": [{
                "ins_code": "111", "symbol": "الف", "entry_price": 1000.0,
                "stop_loss": 950.0, "target_1": 1150.0,
                "allocated_shares": 5000, "allocated_value": 5_000_000.0,
            }], "rejected": []},
        }

    def test_v2_raises_its_orders_through_the_shared_scan(self):
        """Not a copy of the chain -- the same advisor.v2_scan the panel calls."""

        import pandas as pd

        seen = {}

        original = advisor.v2_scan

        def fake(**kwargs):
            seen.update(kwargs)
            return self._scan_result()

        advisor.v2_scan = fake

        try:
            trader = self._trader("v2")
            today = pd.Timestamp("2026-08-15")
            raised = trader._decide(self._Book(), today, [today])
        finally:
            advisor.v2_scan = original

        self.assertTrue(seen, "the v2 paper path did not call advisor.v2_scan")
        self.assertEqual(raised, 1)
        self.assertEqual(trader.raised[0]["ins_code"], "111")
        self.assertEqual(trader.raised[0]["shares"], 5000)
        self.assertEqual(trader.raised[0]["stop"], 950.0)
        self.assertEqual(trader.raised[0]["target"], 1150.0)

    def test_v2_never_reaches_the_v1_strategy(self):
        """The fallback this whole exercise is about."""

        import pandas as pd

        original = advisor.v2_scan
        advisor.v2_scan = lambda **k: self._scan_result()

        try:
            trader = self._trader("v2")

            # Any attribute access on the v1 strategy is a failure, so make it
            # explode rather than quietly answering.
            class Exploding:
                def __getattr__(self, name):
                    raise AssertionError(
                        "the v2 paper path touched the v1 strategy (.%s)" % name)

            trader.strategy = Exploding()

            today = pd.Timestamp("2026-08-15")
            trader._decide(self._Book(), today, [today])
        finally:
            advisor.v2_scan = original

    def test_v1_still_uses_the_v1_strategy_and_not_the_v2_scan(self):

        import pandas as pd

        called = {"v2": 0}

        original = advisor.v2_scan
        advisor.v2_scan = lambda **k: called.__setitem__("v2", called["v2"] + 1)

        try:
            trader = self._trader("v1")

            class Strategy:
                def __init__(self):
                    self.built = 0

                def build_panel(self, dates, use_cache=True):
                    self.built += 1
                    return {}

                def check_data(self, dates, panel):
                    return ["stub: no data"]

            trader.strategy = Strategy()

            today = pd.Timestamp("2026-08-15")
            trader._decide(self._Book(), today, [today])

            self.assertEqual(trader.strategy.built, 1,
                             "v1 did not go through the v1 strategy")
            self.assertEqual(called["v2"], 0,
                             "v1 reached the v2 scan")
        finally:
            advisor.v2_scan = original

    def test_v2_refuses_to_decide_a_past_session(self):
        """Deciding last week with this week's bars is look-ahead."""

        import pandas as pd

        original = advisor.v2_scan

        called = {"n": 0}

        def fake(**kwargs):
            called["n"] += 1
            return self._scan_result()

        advisor.v2_scan = fake

        try:
            trader = self._trader("v2")

            calendar = [pd.Timestamp("2026-08-10"), pd.Timestamp("2026-08-15")]

            raised = trader._decide(self._Book(), calendar[0], calendar)
        finally:
            advisor.v2_scan = original

        self.assertEqual(raised, 0)
        self.assertEqual(called["n"], 0,
                         "the v2 engine scored a session that is not the latest")

    def test_a_held_name_is_sold_on_the_engines_own_sell(self):
        """Not on 'it fell out of the top ten' -- that is the v1 rule."""

        import pandas as pd

        from backtesting.portfolio import Position

        result = self._scan_result()

        result["signals"].append({
            "ins_code": "222", "symbol": "ب", "action": "SELL",
            "swing_score": 22, "reasons": ["امتیاز زیر حد خروج"],
        })

        original = advisor.v2_scan
        advisor.v2_scan = lambda **k: result

        try:
            trader = self._trader("v2")

            book = self._Book()

            for code, action_held in (("222", True), ("333", True)):
                position = Position(ins_code=code, symbol=code, sector="x",
                                    shares=100, entry_price=10.0,
                                    entry_date="2026-08-01")
                position.last_price = 10.0
                book.positions[code] = position

            today = pd.Timestamp("2026-08-15")
            trader._decide(book, today, [today])
        finally:
            advisor.v2_scan = original

        sold = [r["ins_code"] for r in trader.raised
                if r["side"] == "SELL"]

        self.assertIn("222", sold, "the engine's SELL did not reach an order")
        self.assertNotIn("333", sold,
                         "a held name with no SELL signal was rotated out")

    def test_the_cli_passes_the_engine_through(self):
        import inspect

        import main

        source = inspect.getsource(main._paper)

        self.assertIn("engine=args.engine", source,
                      "main.py --paper does not hand the engine to PaperTrader")

    def test_the_panel_and_paper_share_one_scan_function(self):
        """Requirement: no duplicate v2 chain."""

        import inspect

        from backtesting import paper

        self.assertIn("v2_scan", inspect.getsource(advisor._run_v2))
        self.assertIn("advisor.v2_scan", inspect.getsource(paper.PaperTrader._decide_v2))


if __name__ == "__main__":
    unittest.main()


class DataVersionCostTests(unittest.TestCase):
    """The panel's cache key is read on nearly every request (2026-11-11)."""

    def test_the_cache_key_never_probes_the_database(self):
        from unittest import mock

        import app
        import bootstrap

        probed = AssertionError("data_version() re-probed the database")
        with mock.patch.object(bootstrap, "status", side_effect=probed), \
                mock.patch.object(bootstrap, "progress", side_effect=probed):
            self.assertNotEqual(app.data_version(), "unknown")
