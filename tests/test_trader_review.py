"""The trader / portfolio / risk review of 2026-11-23, pinned.

What the review found the live cards doing, and what they must do now:

  * the today card ordered sales that could not happen (a halted holding, a
    holding locked at its lower limit) and purchases to be paid for by them;
  * reward/risk and per-trade risk were shown with no cost -- all ten basket
    names read 2.33-2.5 against a floor of 2.0 and 1.72-1.98 after today's
    round trip;
  * the basket and the technical card gave prices with no date, and the
    technical card gave an entry to names that closed at the top of the band;
  * the one-day VaR is the price band; nothing beside it said so;
  * a bill's compact date outranked every ISO day and dated the whole book;
  * the equity sleeve named whichever «شاخص» fund stood furthest below NAV.

Nothing here reads the live stores, today's date or the network: every
database is a throwaway file and every network read is patched.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import advisor            # noqa: E402
import allocation         # noqa: E402
import config             # noqa: E402
import database           # noqa: E402
import factor_engine      # noqa: E402
import holdings           # noqa: E402
import opportunities      # noqa: E402
import risk               # noqa: E402
import risk_engine        # noqa: E402
import technical          # noqa: E402
import trade_manager      # noqa: E402


def scratch_db(statements):
    """A throwaway sqlite file holding `statements`; returns its path."""

    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    conn = sqlite3.connect(path)
    for statement in statements:
        if isinstance(statement, tuple):
            conn.execute(*statement)
        else:
            conn.execute(statement)
    conn.commit()
    conn.close()
    return path


WATCH = ("CREATE TABLE market_snapshot (ins_code TEXT, last REAL, close REAL, "
         "prev_close REAL, limit_up REAL, limit_down REAL, volume REAL, "
         "value REAL, captured_at TEXT)")
DEPTH = ("CREATE TABLE depth_levels (ins_code TEXT, captured_at TEXT, "
         "level INTEGER, bid_qty REAL, ask_qty REAL)")


def watch_row(code, last, volume=1000.0, prev=100.0, up=103.0, down=97.0,
              captured="2026-11-23T12:16:43+00:00", value=5e10):
    return ("INSERT INTO market_snapshot VALUES (?,?,?,?,?,?,?,?,?)",
            (code, last, last, prev, up, down, volume, value, captured))


# ==============================
# 3. ONE REAL COST MODEL, FOR THE LIVE CARDS ONLY
# ==============================

class LiveCostTests(unittest.TestCase):

    def test_todays_costs_sit_beside_the_registered_figure_not_in_it(self):
        self.assertAlmostEqual(config.LIVE_ROUND_TRIP_PCT, 1.2512)
        self.assertAlmostEqual(config.LIVE_FUND_ROUND_TRIP_PCT, 0.7512)
        self.assertAlmostEqual(config.LIVE_FUND_SELL_COST_PCT, 0.38)
        # The backtests' figure is what they were run with, and stays.
        self.assertEqual(config.TRANSACTION_COST_PCT, 1.04)

    def test_the_round_trip_comes_off_the_winner_and_onto_the_loser(self):
        # A basket name of 2026-11-23: +14.06% against -5.62%, 2.5 gross.
        self.assertEqual(trade_manager.net_risk_reward(14.06, 5.62), 1.86)
        self.assertEqual(trade_manager.net_risk_reward(14.06, 5.62, cost_pct=0), 2.5)
        # A fund unit pays no transfer tax.
        self.assertGreater(
            trade_manager.net_risk_reward(14.06, 5.62, config.LIVE_FUND_ROUND_TRIP_PCT),
            1.86)
        self.assertIsNone(trade_manager.net_risk_reward(None, 5.0))
        self.assertIsNone(trade_manager.net_risk_reward(10.0, 0))

    def test_the_engine_still_gates_on_the_gross_ratio(self):
        # The paper trader and the registered backtests run through plan():
        # a 5% stop is 2.5 gross and 1.8 net, and must stay a BUY there.
        reading = {"symbol": "x", "close": 1000.0, "atr": 25.0, "atr_pct": 2.5}
        out = trade_manager.plan(reading, {"action": factor_engine.BUY, "score": 80})
        self.assertEqual(out["action"], factor_engine.BUY)
        self.assertEqual(out["risk_reward"], 2.5)
        self.assertNotIn("risk_reward_net", out)
        self.assertLess(trade_manager.net_risk_reward(
            out["target_1_pct"], out["stop_distance_pct"]), config.MIN_RISK_REWARD)

    def test_a_stopped_trade_still_pays_to_get_in_and_out(self):
        # 9% of a 1bn book, stopped 5.62% away: 0.506% of equity, plus fees.
        net = risk_engine.risk_with_costs(5_058_000, 90_000_000)
        self.assertEqual(net, 5_058_000 + round(90_000_000 * 1.2512 / 100))
        self.assertEqual(risk_engine.risk_with_costs(100.0, 1000.0, cost_pct=0), 100.0)
        self.assertIsNone(risk_engine.risk_with_costs(None, 1000.0))

    def test_the_sizing_engine_is_untouched(self):
        size = risk_engine.position_size(1000.0, 50.0, 1_000_000_000)
        self.assertEqual(size["risk_rials"], size["shares"] * 50.0)


# ==============================
# 3 + 4. THE TECHNICAL CARD: NET RATIOS, AND NO ENTRY AT THE BAND TOP
# ==============================

def bars(n, seed, last_move=None, last_on_high=False):
    rng = np.random.default_rng(seed)
    moves = np.clip(rng.normal(0.0005, 0.012, n), -0.028, 0.028)
    if last_move is not None:
        moves[-1] = last_move
    close = 10_000.0 * np.cumprod(1.0 + moves)
    high = close * (1 + rng.uniform(0.004, 0.02, n))
    low = close * (1 - rng.uniform(0.004, 0.02, n))
    if last_on_high:
        high[-1] = close[-1]
    return pd.DataFrame({
        "date": pd.bdate_range(end="2026-11-10", periods=n),
        "open": close, "high": high, "low": low, "close": close,
        "volume": 1_000_000.0, "value": 2e10, "trades": 500.0,
    })


class TechnicalTargetCostTests(unittest.TestCase):

    def targets(self, cost_pct=None):
        frame = pd.DataFrame({"close": np.linspace(900.0, 1000.0, 80)})
        frame["high"] = frame["close"]
        frame["low"] = frame["close"] * 0.99
        latest = {"close": 1000.0, "atr": 30.0, "atr_pct": 3.0}
        return technical.price_targets(frame, latest, {}, cost_pct=cost_pct)

    def test_the_ratio_is_given_gross_and_net(self):
        out = self.targets()
        self.assertEqual(out["risk_reward"], 2.5)
        self.assertEqual(out["cost_pct"], config.LIVE_ROUND_TRIP_PCT)
        # 6% stop, 15% target: (15 - 1.2512) / (6 + 1.2512).
        self.assertEqual(out["risk_reward_net"], 1.9)
        self.assertLess(out["risk_reward_net"], out["min_risk_reward"])
        self.assertAlmostEqual(out["loss_at_stop_net_pct"], 7.25, places=2)
        self.assertTrue(any("خالص" in note for note in out["notes"]))

    def test_a_fund_is_charged_the_fund_round_trip(self):
        fund = self.targets(cost_pct=config.LIVE_FUND_ROUND_TRIP_PCT)
        self.assertEqual(fund["cost_pct"], config.LIVE_FUND_ROUND_TRIP_PCT)
        self.assertGreater(fund["risk_reward_net"], self.targets()["risk_reward_net"])

    def test_the_caveat_names_todays_cost_beside_the_tests_cost(self):
        self.assertIn("۱.۰۴", technical.SIGNAL_CAVEAT)
        self.assertIn("۱.۲۵", technical.SIGNAL_CAVEAT)


class TechnicalEntryBlockerTests(unittest.TestCase):
    """A name that closed at the top of its band gets a reading and no entry."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        frames = {"top": bars(240, 1, last_move=0.029, last_on_high=True),
                  "mid": bars(240, 2, last_move=0.004)}
        conn = database._connect()
        for code, frame in frames.items():
            conn.execute("INSERT INTO instruments (ins_code, symbol_fa, name_fa, "
                         "kind, is_equity) VALUES (?, ?, ?, 'equity', 1)",
                         (code, "T" + code, code))
            for row in frame.itertuples(index=False):
                conn.execute(
                    "INSERT INTO price_history (ins_code, date, open, high, low, "
                    "close, volume, value, trades) VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, row.date.strftime("%Y-%m-%d"), row.open, row.high,
                     row.low, row.close, row.volume, row.value, row.trades))
            previous = float(frame["close"].iloc[-2])
            conn.execute(
                "INSERT INTO market_snapshot (ins_code, prev_close, limit_up, "
                "limit_down, captured_at) VALUES (?,?,?,?,?)",
                (code, previous, previous * 1.03, previous * 0.97,
                 "2026-11-10T12:00:00+00:00"))
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_a_close_at_the_band_top_gets_no_entry_stop_or_target(self):
        out = technical.analyse("Ttop")
        self.assertNotIn("error", out)
        self.assertIsNone(out["targets"])
        self.assertTrue(out["tradability"]["entry_blocker"])
        self.assertIn("سقف دامنه", out["tradability"]["note"])
        self.assertTrue(out["signal"]["caveat"].startswith("⚠"))
        # The reading itself is still given.
        self.assertIsNotNone(out["latest"]["close"])

    def test_a_close_inside_the_band_keeps_its_targets_with_the_net_ratio(self):
        out = technical.analyse("Tmid")
        self.assertNotIn("error", out)
        self.assertIsNone(out["tradability"]["entry_blocker"])
        self.assertIsNotNone(out["targets"])
        self.assertEqual(out["targets"]["cost_pct"], config.LIVE_ROUND_TRIP_PCT)
        self.assertLess(out["targets"]["risk_reward_net"],
                        out["targets"]["risk_reward"])

    def test_a_failing_check_does_not_take_the_reading_down(self):
        import swing_advisor
        with mock.patch.object(swing_advisor, "_entry_blocker",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(technical._entry_blocker(pd.DataFrame(), 3.0))


# ==============================
# 3 + 4. THE BASKET: NET FIGURES AND THE DATE OF ITS PRICES
# ==============================

def position(code, close, stop_pct, target_pct, value=90_000_000.0):
    return {"ins_code": code, "symbol": "S" + code, "close": close,
            "entry_low": close * 0.99, "entry_high": close * 1.01,
            "stop_distance_pct": stop_pct, "target_distance_pct": target_pct,
            "risk_reward": round(target_pct / stop_pct, 2),
            "position_value": value, "risk_rials": value * stop_pct / 100.0}


class BasketLiveViewTests(unittest.TestCase):

    def test_each_position_says_what_is_left_after_the_round_trip(self):
        basket = {"positions": [position("1", 8240.0, 5.62, 14.06),
                                position("2", 2162.0, 9.0, 22.5)],
                  "totals": {"total_risk_pct": 1.32}, "notes": []}
        with mock.patch.object(advisor, "_price_dates", return_value={}):
            advisor._live_view(basket, 1_000_000_000)

        first, second = basket["positions"]
        self.assertEqual(first["risk_reward"], 2.5)           # the engine's, kept
        self.assertEqual(first["risk_reward_net"], 1.86)
        self.assertTrue(first["net_below_floor"])
        self.assertFalse(second["net_below_floor"])           # a 9% stop clears it
        self.assertAlmostEqual(first["risk_pct_net"], 0.506 + 0.113, places=2)
        self.assertEqual(basket["totals"]["net_below_floor"], 1)
        self.assertGreater(basket["totals"]["total_risk_net_pct"], 1.32)
        self.assertEqual(basket["costs"]["round_trip_pct"], config.LIVE_ROUND_TRIP_PCT)
        self.assertTrue(any("۱ از ۲" in note for note in basket["notes"]))

    def test_an_empty_book_gets_no_cost_note(self):
        basket = {"positions": [], "totals": {}, "notes": []}
        with mock.patch.object(advisor, "_price_dates", return_value={}):
            advisor._live_view(basket, 1_000_000_000)
        self.assertEqual(basket["notes"], [])
        self.assertEqual(basket["totals"]["net_below_floor"], 0)


class BasketPriceDateTests(unittest.TestCase):

    def dates(self, captured):
        path = scratch_db([WATCH,
                           watch_row("1", 100.5, captured=captured),      # inside
                           watch_row("2", 103.0, captured=captured),      # at the upper limit
                           watch_row("3", 98.0, captured=captured)])      # below the range
        self.addCleanup(os.remove, path)
        positions = [position(code, 100.0, 5.0, 12.5) for code in ("1", "2", "3", "4")]
        with mock.patch.object(database, "newest_equity_bar",
                               return_value=pd.Timestamp("2026-11-22")), \
             mock.patch.object(database, "_connect",
                               side_effect=lambda: sqlite3.connect(path)):
            return advisor._price_dates(positions), positions

    def test_a_newer_watch_counts_the_names_that_left_their_entry_range(self):
        out, positions = self.dates("2026-11-23T12:16:43+00:00")
        self.assertEqual(out["data_as_of"], "2026-11-22")
        self.assertEqual(out["watch_as_of"], "2026-11-23")
        self.assertTrue(out["watch_newer"])
        self.assertEqual((out["watch_checked"], out["watch_outside_entry"],
                          out["watch_at_limit"]), (3, 2, 1))
        self.assertEqual([p.get("watch_state") for p in positions],
                         ["inside", "limit_up", "below_entry", None])

    def test_a_watch_of_the_same_session_says_nothing(self):
        out, positions = self.dates("2026-11-22T12:16:43+00:00")
        self.assertFalse(out["watch_newer"])
        self.assertEqual(out["watch_outside_entry"], 0)
        self.assertNotIn("watch_state", positions[1])

    def test_an_unreadable_store_still_gives_a_book(self):
        with mock.patch.object(database, "newest_equity_bar",
                               side_effect=RuntimeError("no store")), \
             self.assertLogs(advisor.log, level="ERROR"):
            out = advisor._price_dates([position("1", 100.0, 5.0, 12.5)])
        self.assertIsNone(out["data_as_of"])
        self.assertFalse(out["watch_newer"])


# ==============================
# 5. THE RISK HEADLINE
# ==============================

class WorstRunTests(unittest.TestCase):

    def test_five_limit_down_sessions_are_not_a_three_percent_loss(self):
        daily = pd.Series([0.01] * 10 + [-0.03] * 5 + [0.01] * 10,
                          index=pd.bdate_range("2026-01-01", periods=25))
        out = risk.worst_run(daily, equity=1_000_000.0)
        self.assertEqual(out["sessions"], 5)
        self.assertAlmostEqual(out["worst_pct"], (0.97 ** 5 - 1) * 100, places=2)
        self.assertEqual(out["from_date"], "2026-01-15")
        self.assertEqual(out["to_date"], "2026-01-21")
        self.assertAlmostEqual(out["worst_rials"], (1 - 0.97 ** 5) * 1_000_000, delta=1)
        # The one-day figure the card showed alone:
        self.assertAlmostEqual(risk.value_at_risk(
            pd.Series([-0.03] * 5 + [0.01] * 45))["worst_day_pct"], -3.0)

    def test_too_short_a_series_gives_nothing(self):
        self.assertIsNone(risk.worst_run(pd.Series([0.01, -0.02])))
        self.assertIsNone(risk.worst_run(None))

    def test_a_series_that_only_rose_has_no_rial_loss(self):
        out = risk.worst_run(pd.Series([0.01] * 8), equity=100.0)
        self.assertGreater(out["worst_pct"], 0)
        self.assertEqual(out["worst_rials"], 0)

    def test_the_report_carries_the_run_and_the_window_it_measured(self):
        index = pd.bdate_range("2026-01-01", periods=60)
        matrix = pd.DataFrame({"الف": np.tile([0.01, -0.02, 0.005], 20),
                               "ب": np.tile([-0.01, 0.01, 0.0], 20)}, index=index)
        positions = [{"ins_code": "1", "symbol": "الف", "position_value": 60.0},
                     {"ins_code": "2", "symbol": "ب", "position_value": 40.0}]
        with mock.patch.object(risk, "returns_matrix", return_value=(matrix, [])), \
             mock.patch.object(risk, "benchmark_returns",
                               return_value=pd.Series(dtype=float)):
            report = risk.analyse(positions)
        self.assertEqual(report["window"], {"from": "2026-01-01",
                                            "to": str(index[-1].date()),
                                            "sessions": 60})
        self.assertEqual(report["worst_run"]["sessions"], risk.WORST_RUN_SESSIONS)
        self.assertLessEqual(report["worst_run"]["worst_pct"],
                             report["var"]["worst_day_pct"])
        self.assertIn("یک روز", report["price_band_warning"])

    def test_the_holdings_card_carries_what_the_basket_card_did(self):
        answer = {"available": True, "sessions": 60, "names_measured": 1,
                  "measured_pct": 100.0, "names_dropped": [],
                  "worst_run": {"sessions": 5, "worst_pct": -9.5},
                  "window": {"from": "2026-01-01", "to": "2026-03-25", "sessions": 60},
                  "price_band_warning": "دامنه", "notes": []}
        rows = [{"symbol": "الف", "ins_code": "1", "value": 100.0}]
        with mock.patch.object(risk, "analyse", return_value=answer), \
             mock.patch.object(holdings, "_proxies", return_value=({}, [])):
            out = holdings._risk(rows, 100.0)
        self.assertEqual(out["worst_run"]["worst_pct"], -9.5)
        self.assertEqual(out["window"]["sessions"], 60)
        self.assertEqual(out["price_band_warning"], "دامنه")


# ==============================
# 1. THE BOOK: CASH, DATES, AND WHAT CAN BE DEALT
# ==============================

class CashLineTests(unittest.TestCase):

    def test_a_cash_line_is_rials_at_face_value(self):
        entries, errors = holdings.parse_with_errors("فولاد 1,000\nنقد 500,000,000")
        self.assertEqual(entries, [("فولاد", 1000.0), ("نقد", 500_000_000.0)])
        self.assertEqual(errors, {})

    def test_scale_words_and_tomans_are_read(self):
        self.assertEqual(holdings.parse("نقد ۵۰ میلیون تومان"), [("نقد", 500_000_000.0)])
        self.assertEqual(holdings.parse("نقد 20 میلیارد"), [("نقد", 20e9)])
        self.assertEqual(holdings.parse("نقد 3 میلیون ریال\nنقد 2 میلیون"),
                         [("نقد", 5_000_000.0)])

    def test_an_unreadable_amount_is_an_error_not_a_guess(self):
        for text in ("نقد", "نقد 5 بشکه", "نقد 12 500"):
            entries, errors = holdings.parse_with_errors(text)
            self.assertEqual(entries, [("نقد", None)], text)
            self.assertIn("نقد", errors, text)

    def test_the_words_do_not_touch_an_ordinary_symbol(self):
        self.assertFalse(holdings.is_cash("فولاد"))
        self.assertTrue(holdings.is_cash("نقد"))
        # «میلیون» after a share is not a multiplier.
        self.assertEqual(holdings.parse("فولاد 5 میلیون"), [("فولاد", 5.0)])

    def test_cash_is_measured_as_part_of_the_book_with_no_price_risk(self):
        seen = {}

        def analyse(positions, equity=None, proxies=None):
            seen["positions"], seen["proxies"] = positions, proxies
            return {"available": False, "reason": "x"}

        index = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5),
                              "close": [100.0, 101.0, 99.0, 102.0, 103.0]})
        rows = [{"symbol": "نقد", "cash": True, "value": 500.0, "sleeve": "fixed"},
                {"symbol": "الف", "ins_code": "1", "value": 500.0, "equity": True}]
        with mock.patch.object(risk, "analyse", side_effect=analyse), \
             mock.patch.object(holdings.database, "load_index_history",
                               return_value=index):
            holdings._risk(rows, 1000.0)
        self.assertEqual([p["ins_code"] for p in seen["positions"]], ["1", "cash"])
        self.assertTrue((seen["proxies"]["cash"] == 0.0).all())


class BookDateTests(unittest.TestCase):

    def test_a_compact_date_is_a_day_like_any_other(self):
        self.assertEqual(holdings.iso_day("20261123"), "2026-11-23")
        self.assertEqual(holdings.iso_day("2026-11-23T12:00:00"), "2026-11-23")
        self.assertIsNone(holdings.iso_day(None))
        self.assertIsNone(holdings.iso_day("دیروز"))

    def test_an_old_bill_quote_does_not_date_the_book(self):
        # As text, '20261101' sorts above '2026-11-23'.
        book = holdings._summarise([
            {"symbol": "الف", "value": 60.0, "quantity": 1, "as_of": "2026-11-23", "equity": True},
            {"symbol": "اخزا404", "value": 40.0, "quantity": 1, "as_of": "20261101", "bill": True},
        ])
        self.assertEqual(book["as_of"], "2026-11-23")
        self.assertEqual(book["stale"], [{"symbol": "اخزا404", "as_of": "2026-11-01"}])
        self.assertTrue(any("اخزا404" in w and "قدیمی" in w for w in book["warnings"]))

    def test_every_line_priced_earlier_is_named_with_its_day(self):
        book = holdings._summarise([
            {"symbol": "الف", "value": 46.0, "quantity": 1, "as_of": "2026-11-23", "equity": True},
            {"symbol": "ب", "value": 54.0, "quantity": 1, "as_of": "2026-09-30", "equity": True,
             "halted": True, "halted_since": "2026-09-23", "can_sell": False,
             "why_not": "متوقف از 2026-09-23"},
            {"symbol": "نقد", "value": 10.0, "quantity": 10.0, "as_of": None, "cash": True},
        ])
        self.assertEqual(book["as_of"], "2026-11-23")
        self.assertEqual(book["stale"], [{"symbol": "ب", "as_of": "2026-09-30"}])
        self.assertTrue(book["rows"][1]["stale"])
        self.assertFalse(book["rows"][0]["stale"])
        self.assertFalse(book["rows"][2]["stale"])            # cash has no price date
        # The halt already has its own line; no second one for its date.
        self.assertEqual(sum("ب" in w.split()[0:2] for w in book["warnings"]
                             if "قدیمی" in w), 0)
        self.assertEqual(book["undealable"],
                         [{"symbol": "ب", "why": "متوقف از 2026-09-23"}])

    def test_a_holding_in_a_sell_queue_is_called_out(self):
        book = holdings._summarise([
            {"symbol": "الف", "value": 100.0, "quantity": 1, "as_of": "2026-11-23",
             "equity": True, "lock": "sell", "lock_note": "در صف فروش است",
             "can_sell": False, "why_not": "در صف فروش است"}])
        self.assertTrue(any("صف فروش" in w and "حساب نکنید" in w
                            for w in book["warnings"]))
        self.assertEqual(book["undealable"][0]["symbol"], "الف")


class LockStateTests(unittest.TestCase):
    """What cannot be dealt at the last stored market watch."""

    def locks(self, rows, depth=None, not_before=None, with_depth_table=True):
        statements = [WATCH] + rows
        if with_depth_table:
            statements.append(DEPTH)
            for code, bid, ask, captured in depth or ():
                statements.append(("INSERT INTO depth_levels VALUES (?,?,1,?,?)",
                                   (code, captured, bid, ask)))
        path = scratch_db(statements)
        self.addCleanup(os.remove, path)
        with mock.patch.object(holdings.database, "_connect",
                               side_effect=lambda: sqlite3.connect(path)):
            return holdings.lock_states([r[1][0] for r in rows], not_before=not_before)

    def test_a_last_trade_at_the_lower_limit_cannot_be_sold(self):
        out = self.locks([watch_row("1", 97.0), watch_row("2", 103.0),
                          watch_row("3", 100.5)])
        self.assertEqual(out["1"]["lock"], "sell")
        self.assertEqual(out["2"]["lock"], "buy")
        self.assertNotIn("3", out)
        self.assertEqual(out["1"]["as_of"], "2026-11-23")
        self.assertFalse(out["1"]["queue_confirmed"])
        self.assertIn("محتمل", out["1"]["note"])

    def test_the_limit_is_the_symbols_own_band(self):
        # A gold fund at -3% of a ±10% band is trading, not locked.
        out = self.locks([watch_row("1", 97.0, up=110.0, down=90.0)])
        self.assertEqual(out, {})

    def test_the_order_book_of_the_same_session_confirms_the_queue(self):
        stamp = "2026-11-23T08:43:11+00:00"
        out = self.locks([watch_row("1", 97.0), watch_row("2", 103.0)],
                         depth=[("1", 0, 5000, stamp), ("2", 9000, 0, stamp)])
        self.assertTrue(out["1"]["queue_confirmed"])
        self.assertIn("در صف فروش است", out["1"]["note"])
        self.assertIn("در صف خرید است", out["2"]["note"])

    def test_a_name_that_did_not_trade_is_locked_only_on_the_books_word(self):
        stamp = "2026-11-23T08:43:11+00:00"
        rows = [watch_row("1", 99.0, volume=0), watch_row("2", 99.0, volume=0)]
        out = self.locks(rows, depth=[("1", 0, 5000, stamp)])
        self.assertEqual(out["1"]["lock"], "sell")
        self.assertIn("معامله نشده", out["1"]["note"])
        self.assertNotIn("2", out)

    def test_an_order_book_of_another_session_is_not_used(self):
        out = self.locks([watch_row("1", 99.0, volume=0)],
                         depth=[("1", 0, 5000, "2026-11-22T08:43:11+00:00")])
        self.assertEqual(out, {})

    def test_a_watch_older_than_the_newest_bar_locks_nothing(self):
        rows = [watch_row("1", 97.0, captured="2026-11-22T12:16:43+00:00")]
        self.assertEqual(self.locks(rows, not_before="2026-11-23"), {})
        self.assertEqual(self.locks(rows, not_before="2026-11-22")["1"]["lock"], "sell")

    def test_no_order_book_table_still_reads_the_watch(self):
        out = self.locks([watch_row("1", 97.0)], with_depth_table=False)
        self.assertEqual(out["1"]["lock"], "sell")

    def test_no_codes_and_no_watch_lock_nothing(self):
        self.assertEqual(holdings.lock_states([]), {})
        path = scratch_db(["CREATE TABLE other (x)"])
        self.addCleanup(os.remove, path)
        with mock.patch.object(holdings.database, "_connect",
                               side_effect=lambda: sqlite3.connect(path)):
            self.assertEqual(holdings.lock_states(["1"]), {})


# ==============================
# 6. STAND-INS
# ==============================

class FundKindTests(unittest.TestCase):

    def kind(self, symbol, name):
        return holdings.fund_kind({"kind": "fund", "symbol_fa": symbol, "name_fa": name})

    def test_what_each_fund_is(self):
        self.assertEqual(self.kind("ارام", "صندوق س شاخصی ارام مفید"), "index")
        self.assertEqual(self.kind("اطلس", "صندوق س.توسعه اطلس مفید-س"), "active")
        self.assertEqual(self.kind("اهرم", "صندوق س سهامی کاریزما- اهرمی"), "leveraged")
        self.assertEqual(self.kind("توان", "صندوق س. اهرمی توان مفید-س"), "leveraged")
        self.assertEqual(self.kind("پالایش", "صندوق پالایشی یکم-سهام"), "sector")
        self.assertEqual(self.kind("دارایکم", "صندوق واسطه گری مالی یکم-سهام"), "sector")
        self.assertEqual(self.kind("استیل", "صندوق س صنایع مفید1- بخشی"), "sector")
        self.assertEqual(self.kind("اذرین", "صندوق س.بخشی فلزات کیمیا-ب"), "sector")
        self.assertIsNone(self.kind("عیار", "صندوق طلای عیار مفید"))
        self.assertIsNone(holdings.fund_kind({"kind": "equity", "symbol_fa": "فولاد"}))


class StandInTests(unittest.TestCase):

    def proxies(self, rows):
        index = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5),
                              "close": [100.0, 101.0, 99.0, 102.0, 103.0]})
        with mock.patch.object(holdings.database, "load_index_history",
                               return_value=index) as load_index, \
             mock.patch.object(holdings.database, "load_history_by_inscode",
                               return_value=pd.DataFrame()):
            out, named = holdings._proxies(rows)
        return out, named, [c.args[0] for c in load_index.call_args_list]

    def test_the_equal_weight_fund_is_known_by_its_symbol(self):
        # It trades as «هموزن» under a name that does not say so.
        rows = [{"symbol": "هموزن", "ins_code": "1", "value": 10.0, "equity": False,
                 "sleeve": "equity", "name": "صندوق س. شاخصی کیان-س",
                 "fund_kind": "index"}]
        out, named, codes = self.proxies(rows)
        self.assertIn("1", out)
        self.assertEqual(codes, [config.INDICES["TEDPIX_EW"]["insCode"]])
        self.assertIn(config.INDICES["TEDPIX_EW"]["name"], named[0])

    def test_one_industry_does_not_stand_in_on_the_whole_market(self):
        rows = [{"symbol": "پالایش", "ins_code": "1", "value": 10.0, "equity": False,
                 "sleeve": "equity", "name": "صندوق پالایشی یکم-سهام",
                 "fund_kind": "sector"}]
        out, named, _codes = self.proxies(rows)
        self.assertEqual((out, named), ({}, []))

    def test_the_kind_is_worked_out_when_the_row_does_not_carry_it(self):
        rows = [{"symbol": "پالایش", "ins_code": "1", "value": 10.0, "equity": False,
                 "sleeve": "equity", "name": "صندوق پالایشی یکم-سهام"}]
        self.assertEqual(self.proxies(rows)[0], {})

    def test_an_active_fund_on_the_index_is_said_to_be_an_approximation(self):
        rows = [{"symbol": "اطلس", "ins_code": "1", "value": 10.0, "equity": False,
                 "sleeve": "equity", "name": "صندوق س.توسعه اطلس مفید-س",
                 "fund_kind": "active"},
                {"symbol": "ارام", "ins_code": "2", "value": 10.0, "equity": False,
                 "sleeve": "equity", "name": "صندوق س شاخصی ارام مفید",
                 "fund_kind": "index"}]
        out, named, _codes = self.proxies(rows)
        self.assertEqual(sorted(out), ["1", "2"])
        self.assertIn("تقریب", named[0])
        self.assertNotIn("تقریب", named[1])


# ==============================
# 6. THE FUND THE CARD NAMES
# ==============================

def fund(symbol, discount, value, locked=None, broad=True, suspect=False):
    return {"symbol": symbol, "discount_pct": discount, "traded_value": value,
            "locked": locked, "broad": broad, "suspect": suspect}


class FundPickTests(unittest.TestCase):

    POOL = [fund("هموزن", -4.1, 7.7e11, locked="buy", broad=False),
            fund("وبازار", -1.2, 1.56e10),
            fund("فیروزه", -0.9, 1.9e11, broad=False),
            fund("ارام", -0.4, 1.1e11)]

    def test_which_index_a_fund_follows(self):
        self.assertEqual(opportunities.index_tracked("ارام"),
                         (config.INDICES["TEDPIX"]["name"], True))
        self.assertEqual(opportunities.index_tracked("هموزن")[1], False)
        self.assertEqual(opportunities.index_tracked("فیروزه")[1], False)
        self.assertEqual(opportunities.index_tracked("ناشناس"), (None, False))

    def test_a_last_trade_at_the_band_limit_is_a_queue(self):
        band = (0.03, 0.03)
        self.assertEqual(opportunities.lock_side(103.0, 100.0, band), "buy")
        self.assertEqual(opportunities.lock_side(97.0, 100.0, band), "sell")
        self.assertIsNone(opportunities.lock_side(102.0, 100.0, band))
        # A gold fund's +3% inside its ±10% band is a trade, not a queue.
        self.assertIsNone(opportunities.lock_side(103.0, 100.0, (0.10, 0.10)))
        self.assertIsNone(opportunities.lock_side(103.0, 100.0, None))
        self.assertIsNone(opportunities.lock_side(None, 100.0, band))

    def test_the_cheapest_is_not_named_when_it_cannot_be_bought(self):
        # Without the tracker rule: the locked fund is passed over, by name.
        out = opportunities.pick_fund(self.POOL)
        self.assertEqual(out["fund"]["symbol"], "وبازار")
        self.assertEqual([s["symbol"] for s in out["skipped"]], ["هموزن"])
        self.assertIn("صف خرید", out["skipped"][0]["why"])

    def test_the_equity_sleeve_takes_a_tracker_of_the_broad_index(self):
        out = opportunities.pick_fund(self.POOL, need_broad=True)
        self.assertEqual(out["fund"]["symbol"], "وبازار")
        self.assertFalse(out["fallback"])
        self.assertEqual(out["skipped"], [])        # no tracker was passed over

    def test_a_fund_too_small_for_the_order_is_passed_over_and_said_so(self):
        # 5.2bn is a third of وبازار's day; ارام carries it.
        out = opportunities.pick_fund(self.POOL, order_value=5.2e9, need_broad=True)
        self.assertEqual(out["fund"]["symbol"], "ارام")
        self.assertFalse(out["split"])
        self.assertEqual([s["symbol"] for s in out["skipped"]], ["وبازار"])
        self.assertIn("ارزش معاملات", out["skipped"][0]["why"])

    def test_an_order_too_large_for_any_tracker_is_split_not_sent_elsewhere(self):
        # 17bn: more than a tenth of ارام's day, and فیروزه would carry it --
        # but a different index is not the same exposure.
        out = opportunities.pick_fund(self.POOL, order_value=1.7e10, need_broad=True)
        self.assertEqual(out["fund"]["symbol"], "ارام")
        self.assertTrue(out["split"])
        self.assertFalse(out["fallback"])
        self.assertEqual([s["symbol"] for s in out["skipped"]], ["وبازار"])

    def test_another_index_fund_is_the_last_resort_and_is_flagged(self):
        pool = [fund("ارام", -0.4, 1.1e11, locked="buy"),
                fund("فیروزه", -0.9, 1.9e11, broad=False)]
        out = opportunities.pick_fund(pool, need_broad=True)
        self.assertEqual(out["fund"]["symbol"], "فیروزه")
        self.assertTrue(out["fallback"])
        self.assertEqual([s["symbol"] for s in out["skipped"]], ["ارام"])

    def test_nothing_to_buy_is_nothing_named(self):
        pool = [fund("الف", -1.0, 1e11, locked="buy"),
                fund("ب", -30.0, 1e11, suspect=True)]
        out = opportunities.pick_fund(pool, need_broad=True)
        self.assertIsNone(out["fund"])
        self.assertEqual(opportunities.pick_fund([])["fund"], None)

    def test_the_today_card_applies_the_same_rule(self):
        # pickFund in panel.js mirrors pick_fund; the reasons are one text.
        with open(os.path.join(ROOT, "static", "js", "panel.js"), encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("function pickFund(items, order, needBroad, share)", script)
        for reason in ("در سقف دامنه‌ی نوسان — صف خرید",
                       "ارزش معاملات روزش برای این مبلغ کم است"):
            self.assertIn(reason, script)
        self.assertIn("max_order_share_of_day", script)


class FundDiscountFlagTests(unittest.TestCase):

    def test_each_fund_carries_whether_it_can_be_bought_and_what_it_tracks(self):
        path = scratch_db([
            "CREATE TABLE instruments (ins_code TEXT, symbol_fa TEXT, name_fa TEXT, kind TEXT)",
            WATCH,
            ("INSERT INTO instruments VALUES ('1','هموزن','صندوق س. شاخصی کیان-س','fund')",),
            ("INSERT INTO instruments VALUES ('2','ارام','صندوق س شاخصی ارام مفید','fund')",),
            ("INSERT INTO instruments VALUES ('3','عیار','صندوق طلای عیار مفید','fund')",),
            watch_row("1", 103.0, value=7.7e11),
            watch_row("2", 101.0, value=1.1e11),
            watch_row("3", 104.0, value=4.5e13, up=110.0, down=90.0),
        ])
        self.addCleanup(os.remove, path)
        nav = lambda last, bubble: {"bubble": bubble, "nav_redeem": 100.0,    # noqa: E731
                                    "close": last, "last": last, "prev_close": 100.0,
                                    "date": "20261123"}
        import gold
        with mock.patch.object(opportunities.database, "_connect",
                               side_effect=lambda: sqlite3.connect(path)), \
             mock.patch.object(gold, "nav_snapshot",
                               return_value={"1": nav(103.0, -0.04), "2": nav(101.0, -0.03),
                                             "3": nav(104.0, -0.03)}):
            out = opportunities.fund_discounts()

        by_symbol = {f["symbol"]: f for items in out.values() for f in items}
        locked = by_symbol["هموزن"]
        self.assertEqual(locked["locked"], "buy")
        self.assertFalse(locked["broad"])
        # A discount the band made is not flagged as a bargain.
        self.assertFalse(locked["notable"])
        self.assertIsNone(by_symbol["ارام"]["locked"])
        self.assertTrue(by_symbol["ارام"]["broad"])
        self.assertTrue(by_symbol["ارام"]["notable"])
        # +4% inside a gold fund's ±10% band is not a queue.
        self.assertIsNone(by_symbol["عیار"]["locked"])
        self.assertTrue(by_symbol["عیار"]["broad"])
        self.assertEqual(opportunities.pick_fund(out["equity"], need_broad=True)
                         ["fund"]["symbol"], "ارام")


# ==============================
# 2 + 3. THE ALLOCATION CARD
# ==============================

class AllocationWordingTests(unittest.TestCase):

    def test_no_profile_is_called_the_main_recommendation(self):
        for profile in allocation.PROFILES:
            self.assertNotIn("پیشنهاد اصلی", profile["label"])

    def test_a_rebalance_is_charged_the_fund_round_trip(self):
        self.assertAlmostEqual(allocation.ROUND_TRIP,
                               config.LIVE_FUND_ROUND_TRIP_PCT / 100.0)

    def test_the_plan_says_what_dealing_costs(self):
        saved = allocation.history, allocation.candidates
        allocation.history = lambda: None
        allocation.candidates = lambda limit=3: {}
        try:
            costs = allocation.plan(equity=1_000_000_000)["costs"]
        finally:
            allocation.history, allocation.candidates = saved
        self.assertEqual(costs["fund_round_trip_pct"], config.LIVE_FUND_ROUND_TRIP_PCT)
        self.assertEqual(costs["share_round_trip_pct"], config.LIVE_ROUND_TRIP_PCT)

    def test_trackers_of_the_broad_index_lead_the_equity_list(self):
        path = scratch_db([
            "CREATE TABLE instruments (ins_code TEXT, symbol_fa TEXT, name_fa TEXT, kind TEXT)",
            WATCH,
            ("INSERT INTO instruments VALUES ('1','هموزن','صندوق س. شاخصی کیان-س','fund')",),
            ("INSERT INTO instruments VALUES ('2','فیروزه','صندوق شاخص30 شرکت فیروزه- سهام','fund')",),
            ("INSERT INTO instruments VALUES ('3','ارام','صندوق س شاخصی ارام مفید','fund')",),
            ("INSERT INTO instruments VALUES ('4','کاردان','صندوق س تجارت شاخصی کاردان','fund')",),
            watch_row("1", 100.0, value=7.7e11), watch_row("2", 100.0, value=1.9e11),
            watch_row("3", 100.0, value=1.1e11), watch_row("4", 100.0, value=4.2e11),
        ])
        self.addCleanup(os.remove, path)
        import gold
        with mock.patch.object(allocation.database, "_connect",
                               side_effect=lambda: sqlite3.connect(path)), \
             mock.patch.object(gold, "gold_fund_codes", return_value=[]):
            equity = allocation.candidates(limit=3)["equity"]
        self.assertEqual([f["symbol"] for f in equity], ["کاردان", "ارام", "هموزن"])
        self.assertEqual([f["broad"] for f in equity], [True, True, False])


# ==============================
# THE FIELDS THE PANEL READS
# ==============================

class PanelContractTests(unittest.TestCase):
    """panel.js cannot be run here (no JavaScript engine in CI), so what is
    pinned is the contract: every field these cards read is one the server
    sends under that name, and the functions the today card is built from
    are there."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "static", "js", "panel.js"), encoding="utf-8") as handle:
            cls.script = handle.read()

    def test_the_today_card_is_built_from_the_dealable_part_of_the_book(self):
        for piece in ("function todayOrders(mine, weights, band)",
                      "function todayUnits(amount, price)",
                      "r.can_sell === false", "r.why_not", "r.cash",
                      "mine.sellable[k]", "mine.cash[k]", "mine.saleable[sleeve]",
                      '"buy-blocked"', '"sell-blocked"',
                      "book.as_of", "book.stale", "fund_kind",
                      'f.locked === "buy"', "f.broad", "chosen.split",
                      "rep.costs"):
            self.assertIn(piece, self.script, piece)

    def test_the_server_sends_the_fields_under_those_names(self):
        book = holdings._summarise([
            {"symbol": "الف", "value": 1.0, "quantity": 1, "as_of": "2026-11-23"}])
        for key in ("as_of", "stale", "undealable"):
            self.assertIn(key, book)
        basket = {"positions": [position("1", 100.0, 5.0, 12.5)], "totals": {}}
        with mock.patch.object(advisor, "_price_dates",
                               return_value={"data_as_of": "2026-11-22",
                                             "watch_newer": False}):
            advisor._live_view(basket, 1e9)
        for key in ("risk_reward_net", "net_below_floor", "risk_pct_net"):
            self.assertIn(key, basket["positions"][0])
            self.assertIn("p." + key, self.script)
        for key in ("data_as_of", "watch_newer", "costs"):
            self.assertIn(key, basket)
            self.assertIn("basket." + key, self.script)
        for key in ("total_risk_net_pct", "net_below_floor"):
            self.assertIn(key, basket["totals"])
            self.assertIn("totals." + key, self.script)

    def test_the_risk_cards_read_the_run_the_window_and_the_warning(self):
        for piece in ("risk.worst_run", "risk.window", "risk.price_band_warning",
                      "riskRep.worst_run", "riskRep.window",
                      "tg.risk_reward_net", "tg.cost_pct", "tg.loss_at_stop_net_pct"):
            self.assertIn(piece, self.script, piece)


if __name__ == "__main__":
    unittest.main()
