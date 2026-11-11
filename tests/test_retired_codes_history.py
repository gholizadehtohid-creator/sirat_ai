"""A board move's two codes are one company: once a date, and never erased.

`seed_import.py --repair-universe --apply` retires a board-moved company's old
code (kind superseded, is_equity 0) beside its live one and copies its
statements to the live code; the point-in-time and backtest universes still
load the old code, so a past date sees what traded then (2026-11-11). Read
without care, that makes one company two -- or none. These tests pin it on a
synthetic move: OLD trades to session OLD_LAST and then prints frozen
zero-volume bars, NEW trades from session NEW_FIRST.

    ONE PAIRING. database.superseded_listings pairs the codes by the repair's
    own rule (symbol and ISIN company code) and ends the old one at the
    company's first bar under another code.

    ONCE AT A DECISION DATE. From that session the old code leaves every
    point-in-time universe -- the staleness filter, market_valuation,
    regime.participation, the portfolio and combined backtests -- and the
    live code holds its filings. A sector median takes one record per
    company, the live code's.

    NOTHING ERASED PER SESSION. The market's daily flow and the derived
    sector index still count the old code on the sessions it traded, and
    never on one where the live code has a bar.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
import database                                                 # noqa: E402
import fundamental_db                                           # noqa: E402
import jalali                                                   # noqa: E402
from backtesting.pit import SUPERSEDED_STALE, PointInTimeData   # noqa: E402

SECTOR = "S"
OLD_LAST = 200      # OLD's last traded session; frozen bars follow it
NEW_FIRST = 205     # NEW's first session, where OLD ends


def stamp(day):
    """A Codal publication time (Jalali) before that session's close."""

    jy, jm, jd = jalali.today_jalali(pd.Timestamp(day).date())
    return "%04d/%02d/%02d 10:00:00" % (jy, jm, jd)


class MoveFixture(unittest.TestCase):
    """A, B: equities. OLD -> NEW: one company, moved from bourse to
    farabourse (IRO1MOVD0001 -> IRO7MOVD0001), as the repair leaves it."""

    sessions = 216

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = os.path.join(self.tmp, "market.db")
        self.env = os.environ.pop("SIRAT_FUNDAMENTAL_DB", None)
        self.previous = fundamental_db.set_path(
            os.path.join(self.tmp, "fundamentals.db"))
        database.create_database()

        self.dates = pd.bdate_range("2025-01-01", periods=self.sessions)

        database.save_instruments([
            {"ins_code": "A", "symbol_fa": "الف", "isin": "IRO1AAAA0001",
             "kind": "equity", "board": "bourse", "is_equity": 1,
             "sector_name": SECTOR},
            {"ins_code": "B", "symbol_fa": "ب", "isin": "IRO1BBBB0001",
             "kind": "equity", "board": "bourse", "is_equity": 1,
             "sector_name": SECTOR},
            {"ins_code": "NEW", "symbol_fa": "مو", "isin": "IRO7MOVD0001",
             "kind": "equity", "board": "farabourse", "is_equity": 1,
             "sector_name": SECTOR},
            {"ins_code": "OLD", "symbol_fa": "مو", "isin": "IRO1MOVD0001",
             "kind": "superseded", "board": "bourse", "is_equity": 0,
             "sector_name": SECTOR},
        ])

        n = self.sessions
        self.save_bars("A", [1000.0 + 2 * i for i in range(n)])
        self.save_bars("B", [1500.0 + 3 * i for i in range(n)])
        rising = [800.0 * 1.01 ** i for i in range(n)]
        self.save_bars("OLD",
                       rising[:OLD_LAST + 1]
                       + [rising[OLD_LAST]] * (n - OLD_LAST - 1),
                       [100.0] * (OLD_LAST + 1) + [0.0] * (n - OLD_LAST - 1))
        self.save_bars("NEW", rising[NEW_FIRST:], start=NEW_FIRST)

        database.save_index_history(backtest_config.BENCHMARK_TEDPIX,
                                    pd.DataFrame({"date": self.dates,
                                                  "close": 1.0, "low": 1.0,
                                                  "high": 1.0}))

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        if self.env is not None:
            os.environ["SIRAT_FUNDAMENTAL_DB"] = self.env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save_bars(self, code, closes, volumes=None, start=0):
        volumes = volumes or [100.0] * len(closes)
        database.save_history(code, pd.DataFrame({
            "date": self.dates[start:start + len(closes)],
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": volumes,
            "value": [c * v for c, v in zip(closes, volumes)]}))

    def file(self, code, tracing_no, period_end, session, months=12,
             audited=False, items=None):
        database.save_codal_filing(code, {
            "tracing_no": tracing_no, "period_end": period_end,
            "period_months": months, "audited": audited,
            "items": items or {"capital": 1e9},
        }, published_at=stamp(self.dates[session]))

    def execute(self, sql, params=()):
        conn = database._connect()
        conn.execute(sql, params)
        conn.commit()
        conn.close()

    def data(self):
        return PointInTimeData(backtest_config.BacktestConfig(), verbose=False)


class PairingTests(MoveFixture):

    def test_the_old_code_is_paired_with_the_live_one_and_ends_at_its_first_bar(self):
        self.assertEqual(database.superseded_listings(), {
            "OLD": {"successor": "NEW",
                    "until": str(self.dates[NEW_FIRST].date())}})

    def test_until_waits_for_the_live_code_to_trade(self):
        """The 17 codes of 2026-11-11: their live codes had no bar yet."""

        self.execute("DELETE FROM price_history WHERE ins_code = 'NEW'")

        self.assertEqual(database.superseded_listings()["OLD"],
                         {"successor": "NEW", "until": None})

    def test_only_the_same_symbol_and_company_code_pair(self):
        database.save_instruments([
            # Same symbol, another company.
            {"ins_code": "ELSE", "symbol_fa": "مو", "isin": "IRO1ELSE0001",
             "kind": "superseded", "is_equity": 0},
            # The same company code under another symbol.
            {"ins_code": "TWIN", "symbol_fa": "دو", "isin": "IRO3MOVD0001",
             "kind": "equity", "is_equity": 1},
        ])
        self.save_bars("ELSE", [100.0] * 10)
        self.save_bars("TWIN", [100.0] * 10, start=20)

        listings = database.superseded_listings()

        self.assertEqual(listings["ELSE"], {"successor": None, "until": None})
        self.assertEqual(listings["OLD"]["successor"], "NEW")
        self.assertEqual(listings["OLD"]["until"],
                         str(self.dates[NEW_FIRST].date()))

    def test_after_two_moves_each_listing_ends_at_the_next(self):
        database.save_instruments([
            {"ins_code": "FIRST", "symbol_fa": "مو", "isin": "IRO3MOVD0001",
             "kind": "superseded", "is_equity": 0}])
        self.execute("DELETE FROM price_history "
                     "WHERE ins_code = 'OLD' AND date < ?",
                     (str(self.dates[100].date()),))
        self.save_bars("FIRST", [100.0] * 100)

        listings = database.superseded_listings()

        self.assertEqual(listings["FIRST"], {
            "successor": "NEW", "until": str(self.dates[100].date())})
        self.assertEqual(listings["OLD"]["until"],
                         str(self.dates[NEW_FIRST].date()))

    def test_nothing_retired_is_nothing(self):
        self.execute("UPDATE instruments SET is_equity = 1, kind = 'equity' "
                     "WHERE kind = 'superseded'")

        self.assertEqual(database.superseded_listings(), {})


class PointInTimeTests(MoveFixture):

    def test_the_old_code_leaves_the_staleness_filter_at_the_live_codes_first_bar(self):
        """Its frozen prints read 0 sessions stale, so every universe that
        filters on staleness held the company twice."""

        data = self.data()
        data.instruments()
        before, on = self.dates[NEW_FIRST - 1], self.dates[NEW_FIRST]

        self.assertEqual(data.stale_sessions("OLD", before), 0)
        self.assertEqual(data.stale_sessions("OLD", on), SUPERSEDED_STALE)
        self.assertGreater(data.stale_sessions("OLD", on),
                           data.cfg.max_stale_sessions)
        self.assertEqual(data.stale_sessions("NEW", on), 0)
        self.assertEqual(data.stale_sessions("A", on), 0)

    def test_without_a_live_bar_the_old_code_is_the_company(self):
        self.execute("DELETE FROM price_history WHERE ins_code = 'NEW'")

        data = self.data()

        self.assertFalse(data.superseded_on("OLD", self.dates[-1]))
        self.assertEqual(data.stale_sessions("OLD", self.dates[-1]), 0)

    def test_the_universe_names_the_live_code(self):
        universe = self.data().instruments()

        self.assertEqual(set(universe), {"A", "B", "NEW", "OLD"})
        self.assertEqual(universe["OLD"]["successor"], "NEW")
        self.assertNotIn("successor", universe["NEW"])
        self.assertNotIn("successor", universe["A"])

    def test_the_live_code_holds_the_old_codes_filings_from_its_first_bar(self):
        """codal_filings keeps one row per filing under the code it was
        filed for: without the carry, the company had no fundamentals from
        the day the old code left the universe until it filed again."""

        self.file("OLD", "t1", "1403/12/29", session=100)
        self.file("NEW", "t2", "1404/06/31", session=NEW_FIRST + 5, months=6)

        data = self.data()

        self.assertEqual(data.fundamental_history("NEW",
                                                  self.dates[NEW_FIRST - 1]),
                         [])

        carried = data.fundamental_history("NEW", self.dates[NEW_FIRST])
        self.assertEqual([r["tracing_no"] for r in carried], ["t1"])
        self.assertEqual(carried[0]["visible_from"], self.dates[NEW_FIRST])

        both = data.fundamental_history("NEW", self.dates[NEW_FIRST + 6])
        self.assertEqual([r["tracing_no"] for r in both], ["t1", "t2"])

        # The old code's own reading is what it was.
        self.assertEqual(
            data.fundamental_as_of("OLD", self.dates[150])["tracing_no"], "t1")
        self.assertEqual(
            data.fundamental_as_of("OLD", self.dates[150])["visible_from"],
            self.dates[100])

    def test_the_later_release_of_a_period_still_wins_after_the_carry(self):
        self.file("OLD", "unaudited", "1403/12/29", session=100,
                  items={"capital": 1e9, "revenue": 1.0})
        self.file("OLD", "audited", "1403/12/29", session=150, audited=True,
                  items={"capital": 1e9, "revenue": 2.0})

        history = self.data().fundamental_history("NEW",
                                                  self.dates[NEW_FIRST + 1])

        self.assertEqual([r["tracing_no"] for r in history], ["audited"])

    def test_coverage_counts_the_company_once_a_date(self):
        self.file("OLD", "t1", "1403/12/29", session=100)

        data = self.data()
        report = data.fundamental_coverage_report(
            [self.dates[150], self.dates[NEW_FIRST + 2]])
        before, after = report["by_date"]

        self.assertEqual(before["universe"], 4)
        self.assertEqual(after["universe"], 3)          # OLD is NEW's now
        self.assertEqual((before["covered"], after["covered"]), (1, 1))
        self.assertAlmostEqual(
            data.fundamental_coverage(self.dates[NEW_FIRST + 2]), 1 / 3.0)


class ValuationTests(MoveFixture):
    """market_valuation.point: the market's P/E, a company once."""

    sessions = 240

    def setUp(self):
        MoveFixture.setUp(self)
        self.file("A", "a1", "1403/12/29", session=100)
        self.file("OLD", "o1", "1403/12/29", session=100)

    def point(self, session):
        import fundamental_outlook as fo
        import market_valuation

        reading = {"kind": fo.OPERATING, "ttm": {"profit": 10.0},
                   "valuation": {"market_cap": 100.0},
                   "latest": {"age_months": 1}}

        with mock.patch.object(market_valuation, "MIN_COMPANIES", 1), \
                mock.patch.object(fo, "analyse", return_value=reading), \
                mock.patch.object(fo, "suspect_reason", return_value=None):
            return market_valuation.point(self.data(), self.dates[session])

    def test_before_the_move_the_old_code_is_the_company(self):
        self.assertEqual(self.point(OLD_LAST)["companies"], 2)

    def test_after_the_move_the_company_is_summed_once(self):
        """Three sessions in, the old code had traded within HALT_EXCLUDE_
        DAYS and the live one had filed: both were summed."""

        self.file("NEW", "n1", "1404/06/31", session=NEW_FIRST + 1, months=6)

        point = self.point(NEW_FIRST + 3)

        self.assertEqual(point["companies"], 2)
        self.assertEqual(point["market_cap"], 200.0)

    def test_the_live_code_keeps_the_company_once_the_old_one_is_halted(self):
        """Six weeks on, the old code is halted and the live one has filed
        nothing yet: its statements are the old code's."""

        self.assertEqual(self.point(OLD_LAST + 30)["companies"], 2)


class ParticipationTests(MoveFixture):
    """regime.participation(as_of): the share of the market above its MA."""

    def setUp(self):
        MoveFixture.setUp(self)
        # The rule does not rest on the old code having stopped: here it
        # still prints trades beside the live one, which has 66 sessions.
        self.execute("DELETE FROM price_history WHERE ins_code = 'NEW'")
        self.save_bars("OLD", [800.0 + i for i in range(self.sessions)])
        self.save_bars("NEW", [900.0 + i for i in range(66)], start=150)

    def test_a_past_date_counts_the_company_once(self):
        import regime

        reading = regime.participation(as_of=self.dates[-1])

        self.assertEqual(reading["instruments"], 3)     # A, B, NEW

    def test_before_the_live_code_trades_the_old_one_counts(self):
        import regime

        reading = regime.participation(as_of=self.dates[149])

        self.assertEqual(reading["instruments"], 3)     # A, B, OLD

    def test_todays_reading_is_the_live_codes(self):
        import regime

        self.assertEqual(regime.participation()["instruments"], 3)


class SectorMedianTests(MoveFixture):
    """swing_advisor.sector_medians on the swing backtest's PIT universe."""

    def setUp(self):
        MoveFixture.setUp(self)
        # NEW's row is the copy the repair makes (seed_import._carry_over).
        for code, pe in (("A", 5.0), ("B", 10.0), ("OLD", 20.0),
                         ("NEW", 20.0)):
            self.metric(code, pe)

    def metric(self, code, pe, period_end="1404/06/31"):
        fundamental_db.save_metrics({
            "ins_code": code, "period_end": period_end, "period_months": 6,
            "ratios": {"pe": pe}})

    def test_the_backtests_median_counts_the_company_once(self):
        import swing_advisor

        medians = swing_advisor.sector_medians(self.data().instruments())

        self.assertEqual(medians, {SECTOR: {"pe": 10.0}})     # not 15

    def test_the_swing_strategy_reads_it_so(self):
        from backtesting.swing_strategy import SwingStrategy

        strategy = SwingStrategy(backtest_config.BacktestConfig(),
                                 self.data(), verbose=False)

        self.assertEqual(strategy.medians(), {SECTOR: {"pe": 10.0}})

    def test_the_live_codes_record_is_the_one_used(self):
        import swing_advisor

        database.save_instruments([
            {"ins_code": "C", "symbol_fa": "پ", "isin": "IRO1CCCC0001",
             "kind": "equity", "is_equity": 1, "sector_name": SECTOR}])
        self.metric("C", 50.0)
        self.metric("NEW", 40.0, period_end="1404/09/30")

        medians = swing_advisor.sector_medians(self.data().instruments())

        # 5, 10, 50 and the company's 40: 25. Its old code's 20 instead
        # would read 15, and both 20.
        self.assertEqual(medians, {SECTOR: {"pe": 25.0}})

    def test_without_a_live_record_the_old_one_counts_once(self):
        import swing_advisor

        conn = fundamental_db._connect()
        conn.execute("DELETE FROM metrics WHERE ins_code = 'NEW'")
        conn.commit()
        conn.close()

        medians = swing_advisor.sector_medians(self.data().instruments())

        self.assertEqual(medians, {SECTOR: {"pe": 10.0}})

    def test_the_panels_universe_reads_as_before(self):
        import swing_advisor

        live = swing_advisor._instruments()

        self.assertEqual(set(live), {"A", "B", "NEW"})
        self.assertEqual(swing_advisor.sector_medians(live),
                         {SECTOR: {"pe": 10.0}})


class MarketDailyTests(MoveFixture):
    """moneyflow.market_daily: net retail money per session."""

    def setUp(self):
        MoveFixture.setUp(self)
        rows = [("A", i, 50.0) for i in range(self.sessions)]
        rows += [("OLD", i, 50.0) for i in range(OLD_LAST + 1)]
        rows += [("NEW", i, 50.0) for i in range(NEW_FIRST, self.sessions)]
        # A print under the old code on a session the live code traded.
        rows.append(("OLD", NEW_FIRST + 1, 1000.0))
        conn = database._connect()
        conn.executemany(
            "INSERT INTO client_type_history (ins_code, date, "
            "individual_buy_value, individual_sell_value, "
            "institutional_buy_value, institutional_sell_value) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            [(code, str(self.dates[i].date()), 100.0 + net, 100.0, 100.0)
             for code, i, net in rows])
        conn.commit()
        conn.close()

    def sessions_by_date(self):
        import moneyflow

        reading = moneyflow.market_daily(sessions=400, baseline=400)
        return {d["date"]: d for d in reading["sessions"]}

    def test_the_old_codes_flow_counts_on_the_sessions_it_traded(self):
        day = self.sessions_by_date()[str(self.dates[150].date())]

        self.assertEqual(day["symbols"], 2)                 # A and OLD
        self.assertEqual(day["net_individual"], 300.0)

    def test_never_on_a_session_the_live_code_traded(self):
        day = self.sessions_by_date()[str(self.dates[NEW_FIRST + 1].date())]

        self.assertEqual(day["symbols"], 2)                 # A and NEW
        self.assertEqual(day["net_individual"], 300.0)


class SectorIndexTests(MoveFixture):
    """sectors.analyse_all: the derived, chain-linked sector index."""

    def analyse(self):
        import sectors

        seen = {}
        build = sectors.build_sector_index

        def spy(histories, bars=None):
            seen.update(histories)
            return build(histories, bars=bars)

        with mock.patch.object(sectors, "build_sector_index",
                               side_effect=spy):
            report = sectors.analyse_all(min_members=1)

        return report["sectors"][0], seen

    def test_the_old_codes_sessions_stay_in_the_index(self):
        sector, seen = self.analyse()

        self.assertEqual(set(seen), {"A", "B", "NEW", "OLD"})
        self.assertLess(seen["OLD"]["date"].max(), self.dates[NEW_FIRST])
        self.assertEqual(seen["OLD"]["date"].min(), self.dates[0])
        self.assertEqual(sector["index_contributors"], 3)   # companies

    def test_constituents_breadth_and_scores_are_the_live_codes(self):
        sector, _ = self.analyse()

        self.assertEqual(sector["fundamental"]["constituents"], 3)
        self.assertEqual({m["ins_code"] for m in sector["members"]},
                         {"A", "B", "NEW"})
        # A and B: NEW has too few sessions for a moving average yet.
        self.assertEqual(sector["breadth"]["measured"], 2)

    def test_a_halted_old_code_keeps_only_its_traded_sessions(self):
        # It used to leave the index once its own halt clock ran out (the
        # third review): its live member is halt-filtered already, so it
        # stays, cut at its last real trade -- the frozen tail stays out.
        self.execute("UPDATE price_history SET volume = 0 "
                     "WHERE ins_code = 'OLD' AND date > ?",
                     (str(self.dates[150].date()),))

        _, seen = self.analyse()

        self.assertIn("OLD", seen)
        self.assertEqual(seen["OLD"]["date"].max(), self.dates[150])


class BacktestUniverseTests(MoveFixture):

    def test_the_portfolio_panel_stops_scoring_the_old_code_at_the_move(self):
        import backtest_portfolio

        instruments = database.load_instruments(equities_only=True,
                                                with_superseded=True)
        before, after = self.dates[NEW_FIRST - 1], self.dates[NEW_FIRST + 2]

        panel = backtest_portfolio.score_panel(instruments, [before, after],
                                               gate=0, verbose=False)

        self.assertIn("OLD", panel[before])
        self.assertNotIn("OLD", panel[after])
        self.assertIn("A", panel[after])

    def test_the_combined_backtest_stops_its_signals_at_the_move(self):
        import backtest_combined

        bounds = {}

        def spy(code, symbol, bars, flow_rows, **kwargs):
            bounds[code] = kwargs.get("signal_before")
            return []

        with mock.patch.object(backtest_combined, "signals_for",
                               side_effect=spy):
            backtest_combined.collect(verbose=False)

        self.assertEqual(bounds["OLD"], self.dates[NEW_FIRST])
        self.assertIsNone(bounds["A"])

    def test_the_signal_bound_is_exclusive(self):
        import backtest_combined

        bars = database.load_history_by_inscode("A")
        cut = self.dates[150]

        bounded = backtest_combined.signals_for(
            "A", "الف", bars, None, holding_days=5, threshold=0, step=1,
            signal_before=cut)
        unbounded = backtest_combined.signals_for(
            "A", "الف", bars, None, holding_days=5, threshold=0, step=1)

        self.assertTrue(bounded)
        self.assertTrue(all(pd.Timestamp(t["signal_date"]) < cut
                            for t in bounded))
        self.assertTrue(any(pd.Timestamp(t["signal_date"]) >= cut
                            for t in unbounded))



# ---- the third review (2026-11-11) ------------------------------------------

class TwoMovesWithAFrozenTail(MoveFixture):
    """FIRST -> OLD -> NEW, where FIRST's frozen zero-volume prints run past
    OLD's first session -- the shape فاسمین's old code has."""

    def setUp(self):
        MoveFixture.setUp(self)
        database.save_instruments([
            {"ins_code": "FIRST", "symbol_fa": "مو", "isin": "IRO3MOVD0001",
             "kind": "superseded", "board": "farabourse", "is_equity": 0,
             "sector_name": SECTOR}])
        self.execute("DELETE FROM price_history WHERE ins_code = 'OLD' "
                     "AND date < ?", (str(self.dates[100].date()),))
        self.save_bars("FIRST", [700.0] * 130, [100.0] * 100 + [0.0] * 30)

    def test_a_predecessors_frozen_tail_does_not_end_the_middle_listing(self):
        listings = database.superseded_listings()
        self.assertEqual(listings["OLD"]["until"],
                         str(self.dates[NEW_FIRST].date()))

    def test_the_middle_listing_stays_while_it_is_the_company(self):
        data = self.data()
        data.instruments()
        self.assertEqual(data.stale_sessions("OLD", self.dates[150]), 0)


class CarryOrderTests(MoveFixture):
    """A release the live code holds itself, public before its first bar,
    against an older carried one: the newer must still win."""

    def test_the_newest_public_filing_wins_after_the_carry(self):
        self.file("OLD", "old_annual", "1403/12/29", session=100)
        self.file("NEW", "new_q1", "1404/03/31", session=190, months=3)
        newest = self.data().fundamental_as_of("NEW", self.dates[NEW_FIRST + 5])
        self.assertEqual(newest["tracing_no"], "new_q1")

    def test_the_audited_release_of_a_period_still_wins(self):
        self.file("OLD", "unaudited", "1403/12/29", session=100,
                  items={"capital": 1e9, "revenue": 1.0})
        self.file("NEW", "audited", "1403/12/29", session=150, audited=True,
                  items={"capital": 1e9, "revenue": 2.0})
        history = self.data().fundamental_history("NEW",
                                                  self.dates[NEW_FIRST + 5])
        self.assertEqual([r["tracing_no"] for r in history], ["audited"])


class SectorIndexAfterTheOldCodesHalt(MoveFixture):
    """The company's pre-move sessions stay in the derived index after the
    retired code's own 30-day halt clock has run out."""

    sessions = 240

    def test_old_sessions_stay_once_the_old_code_is_halted(self):
        import sectors

        seen = {}
        build = sectors.build_sector_index

        def spy(histories, bars=None):
            seen.update(histories)
            return build(histories, bars=bars)

        self.assertIn("OLD", database.halted_codes())
        with mock.patch.object(sectors, "build_sector_index", side_effect=spy):
            sectors.analyse_all(min_members=1)
        self.assertIn("OLD", seen)
        self.assertLessEqual(seen["OLD"]["date"].max(),
                             self.dates[OLD_LAST])


if __name__ == "__main__":
    unittest.main()
