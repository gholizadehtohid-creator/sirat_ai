"""Does each answer carry what the panel reads from it, and describe the book?

Every test here is a contract between a server answer and the part of the page
built from it, and each one was broken without anything failing:

    /api/regime     the card printed regime.py's cash target beside a v2 book
                    sized from regime_engine's tighter one (10% vs 23.5%)
    /api/status     the footer counted the legacy five-symbol table: 1,505 bars
                    from 2025-05-07 to 2026-08-09, over ~2 million to 2026-11-10
    /api/screen     the banner's real-or-simulated check read a key the report
                    never carried, and the coverage table said "832 of 818"
    /api/portfolio  under v2, every edit of two inputs v2 never reads was a full
                    rescan that returned the same book
    v2 positions    the constraint column said "requested" on every row -- no
                    limit at all -- for positions cut to the even spread

Route tests use Flask's test client with the expensive builders stubbed: what is
under test is the wiring and the cache key, not the market read itself. The
database-backed ones use a temporary file, never sirat_market.db.
"""

import inspect
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import advisor  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import pipeline  # noqa: E402
import portfolio_engine  # noqa: E402
import regime  # noqa: E402
import regime_engine  # noqa: E402


class TempDatabase(object):
    """Point `database` at a fresh file for one test; put it back after."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def execute(self, sql, rows):
        conn = database._connect()
        conn.executemany(sql, rows)
        conn.commit()
        conn.close()


# ==============================
# /api/regime
# ==============================

# Every key renderRegime() in static/js/panel.js reads off this answer.
CARD_KEYS = (
    "available", "reason", "label", "votes", "max_invested_pct",
    "cash_target_pct", "confidence", "regime", "volatility_regime",
    "participation", "cap_weighted", "equal_weighted", "evidence", "caveat",
)


class RegimeCardTests(unittest.TestCase):

    def setUp(self):
        app.invalidate()
        self.client = app.app.test_client()
        self.detect = mock.patch.object(
            app.regime_module, "detect",
            return_value={"available": True, "from": "detect"}).start()
        self.assess = mock.patch.object(
            app.regime_engine, "assess",
            return_value={"available": True, "from": "assess"}).start()
        self.version = mock.patch.object(app, "data_version",
                                          return_value="v1-data").start()

    def tearDown(self):
        mock.patch.stopall()
        app.invalidate()

    def get(self, query):
        response = self.client.get("/api/regime?" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_under_v2_the_card_is_the_read_the_book_is_sized_from(self):
        self.assertEqual(self.get("engine=v2")["from"], "assess")
        # Called exactly as swing_advisor.scan() calls it: no arguments.
        self.assess.assert_called_once_with()
        self.detect.assert_not_called()

    def test_under_v1_the_card_is_unchanged(self):
        self.assertEqual(self.get("engine=v1&ma_slow=60")["from"], "detect")
        self.assertEqual(self.detect.call_args.kwargs["params"]["ma_slow"], 60)
        self.assess.assert_not_called()

    def test_with_no_engine_asked_the_configured_one_answers(self):
        with mock.patch.object(config, "DECISION_ENGINE", advisor.V2):
            self.assertEqual(self.get("")["from"], "assess")

    def test_one_engines_read_is_never_served_for_the_other(self):
        self.assertEqual(self.get("engine=v2")["from"], "assess")
        self.assertEqual(self.get("engine=v1")["from"], "detect")

    def test_a_finished_fetch_supersedes_a_cached_read(self):
        self.get("engine=v2")
        self.version.return_value = "v2-data"
        self.get("engine=v2")
        self.assertEqual(self.assess.call_count, 2)

    def test_the_indicator_windows_do_not_recompute_the_v2_read(self):
        # assess() never sees them, so a new key per edit is a wasted read.
        self.get("engine=v2&ma_fast=10")
        self.get("engine=v2&ma_fast=30")
        self.assertEqual(self.assess.call_count, 1)

    def test_the_v2_book_reads_the_market_the_same_way(self):
        """The card copies the call swing_advisor.scan() makes. If that call
        ever gains an argument, the card and the book diverge again."""

        import swing_advisor

        self.assertIn("regime = regime_engine.assess()\n",
                      inspect.getsource(swing_advisor.scan))


class AssessCarriesTheCardTests(unittest.TestCase):
    """The card was built for regime.detect(); assess() must fill it whole."""

    def report(self):
        trend = {"above_slow_ma": True, "fast_above_slow": True,
                 "return_60d": 12.0, "volatility_pct": 20.0}
        breadth = {"above_slow_ma_pct": 70.0, "above_fast_ma_pct": 65.0,
                   "instruments": 800, "slow_ma": 50, "fast_ma": 20}
        return regime.assemble(dict(trend), dict(trend), breadth)

    def test_every_key_the_card_reads_is_in_the_v2_read(self):
        # The 2026-11-10 market: a unanimous bull with turnover contracting.
        read = regime_engine.assess(report=self.report(),
                                    turnover={"state": regime_engine.CONTRACTING,
                                              "ratio": 0.73})

        self.assertEqual([k for k in CARD_KEYS if k not in read], [])
        self.assertEqual(read["max_invested_pct"], 76.5)
        self.assertEqual(read["cash_target_pct"], 23.5)

    def test_an_unreadable_market_still_says_why(self):
        read = regime_engine.assess(report={"available": False,
                                            "reason": "بدون داده"})

        self.assertFalse(read["available"])
        self.assertEqual(read["reason"], "بدون داده")


# ==============================
# /api/status
# ==============================

class StatusFooterTests(TempDatabase, unittest.TestCase):

    def setUp(self):
        TempDatabase.setUp(self)
        app.invalidate()
        self.version = mock.patch.object(app, "data_version",
                                          return_value="v1-data").start()

        # The legacy table, as it stands: a few bars for a hand-picked symbol.
        self.execute("INSERT INTO market_data (symbol, date, close) "
                     "VALUES (?, ?, 1)",
                     [("FOLAD", "2025-05-07"), ("FOLAD", "2026-08-09")])
        # The table every analysis reads.
        self.execute("INSERT INTO price_history (ins_code, date, close) "
                     "VALUES (?, ?, 1)",
                     [("1", "2001-03-25"), ("1", "2026-11-09"),
                      ("2", "2026-11-10")])

    def tearDown(self):
        mock.patch.stopall()
        app.invalidate()
        TempDatabase.tearDown(self)

    def status(self):
        response = app.app.test_client().get("/api/status")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_the_footer_counts_price_history(self):
        status = self.status()

        self.assertEqual(status["total_bars"], 3)
        self.assertEqual(status["date_min"], "2001-03-25")
        self.assertEqual(status["date_max"], "2026-11-10")

    def test_the_legacy_rows_are_still_served(self):
        symbols = self.status()["symbols"]

        self.assertEqual([(r["symbol"], r["bars"]) for r in symbols],
                         [("FOLAD", 2)])

    def test_the_whole_table_aggregate_is_paid_once_per_data_version(self):
        with mock.patch.object(database, "history_coverage",
                               wraps=database.history_coverage) as coverage:
            self.status()
            self.status()
            self.assertEqual(coverage.call_count, 1)

            self.version.return_value = "v2-data"
            self.status()
            self.assertEqual(coverage.call_count, 2)

    def test_no_database_file_is_an_empty_coverage_not_an_error(self):
        with mock.patch.object(database, "DATABASE_NAME",
                               self.path + ".absent"):
            self.assertEqual(database.history_coverage(),
                             {"bars": 0, "first_date": None, "last_date": None})


# ==============================
# /api/screen -- the report the top banner and coverage table read
# ==============================

class ScreenReportTests(TempDatabase, unittest.TestCase):

    def setUp(self):
        TempDatabase.setUp(self)
        # The scan only reads the provider's name unless asked to refresh; a
        # stub keeps constructing the real chain out of a unit test.
        provider = mock.Mock()
        provider.name = "test"
        mock.patch.object(pipeline, "get_provider",
                          return_value=provider).start()

    def tearDown(self):
        mock.patch.stopall()
        TempDatabase.tearDown(self)

    def universe(self):
        self.execute("INSERT INTO instruments (ins_code, symbol_fa, name_fa, "
                     "is_equity) VALUES (?, ?, ?, ?)",
                     [("e1", "سهم۱", "شرکت یک", 1),
                      ("e2", "سهم۲", "شرکت دو", 1),
                      ("f1", "صندوق", "صندوق طلا", 0)])
        # e1 and the fund have bars, e2 has none, and x9 is a code with bars
        # that is not in the universe at all.
        self.execute("INSERT INTO price_history (ins_code, date, close, volume) "
                     "VALUES (?, ?, 100, 1000)",
                     [("e1", "2026-11-09"), ("e1", "2026-11-10"),
                      ("f1", "2026-11-10"), ("x9", "2026-11-10")])

    def test_have_history_counts_only_the_screened_instruments(self):
        self.universe()

        report = pipeline.run_scan()

        self.assertEqual(report["counts"]["universe"], 2)
        self.assertEqual(report["have_history"], 1)

    def test_the_report_carries_the_provenance_the_banner_reads(self):
        self.universe()
        database.set_provenance("price_history", database.SIMULATED, "test")

        provenance = pipeline.run_scan()["provenance"]

        self.assertEqual(provenance["status"], database.SIMULATED)
        self.assertEqual(provenance, database.get_provenance("price_history"))

    def test_an_empty_universe_still_reports_its_provenance(self):
        report = pipeline.run_scan()

        self.assertEqual(report["counts"]["universe"], 0)
        self.assertEqual(report["provenance"]["status"], database.UNVERIFIED)


# ==============================
# /api/portfolio -- the cache key
# ==============================

class PortfolioKeyTests(unittest.TestCase):

    def setUp(self):
        app.invalidate()
        self.client = app.app.test_client()
        self.run = mock.patch.object(
            advisor, "run", return_value={"ok": True}).start()
        mock.patch.object(app.pipeline, "run_scan",
                          return_value={"final": []}).start()
        # No bootstrap state and no background fetch from a unit test.
        mock.patch.object(app, "_with_autofetch",
                          side_effect=lambda report, *c: report).start()
        mock.patch.object(app, "data_version", return_value="v1-data").start()
        mock.patch.object(app.settings, "FULL_SCAN_ENABLED", True).start()

    def tearDown(self):
        mock.patch.stopall()
        app.invalidate()

    def get(self, query):
        response = self.client.get("/api/portfolio?" + query)
        self.assertEqual(response.status_code, 200)

    def test_v2_is_not_rebuilt_for_inputs_it_never_reads(self):
        self.get("engine=v2&min_score=60&ma_fast=20")
        self.get("engine=v2&min_score=75&ma_fast=30")

        self.assertEqual(self.run.call_count, 1)
        self.assertNotIn("min_score", self.run.call_args.kwargs)

    def test_v2_is_rebuilt_for_an_input_it_does_read(self):
        self.get("engine=v2&size=10")
        self.get("engine=v2&size=8")

        self.assertEqual(self.run.call_count, 2)

    def test_v1_still_keys_on_both(self):
        self.get("engine=v1&min_score=60")
        self.get("engine=v1&min_score=75")
        self.get("engine=v1&min_score=75&ma_fast=30")

        self.assertEqual(self.run.call_count, 3)


# ==============================
# v2 positions -- the «قید» column
# ==============================

class ScaledByTests(unittest.TestCase):

    EQUITY = 1_000_000_000.0

    # Today's policy shape: ten names, 76.5% invested, 15% per position.
    POLICY = portfolio_engine.Policy(
        max_positions=10, max_position_pct=15.0, min_position_pct=2.0,
        max_per_sector=2, max_sector_pct=30.0, min_cash_pct=23.5,
        target_invested_pct=76.5)

    def signal(self, n, value):
        return {
            "symbol": "نماد%d" % n, "ins_code": str(n),
            "sector": "صنعت%d" % n, "entry_price": 1000.0,
            "position_value": value, "shares": int(value // 1000),
            "stop_distance_pct": 6.0, "bound_by": "risk_budget",
            "swing_score": 80,
        }

    def column(self, signals):
        """What the panel prints: scaled_by || bound_by, per allocated row."""

        book = portfolio_engine.allocate(self.POLICY, signals, self.EQUITY)
        rows = [advisor._v2_position(s, rank, self.EQUITY)
                for rank, s in enumerate(book["accepted"], start=1)]
        return rows, [r["scaled_by"] or r["bound_by"] for r in rows]

    def test_a_request_granted_in_full_shows_the_risk_engines_reason(self):
        rows, shown = self.column([self.signal(1, 5e7), self.signal(2, 5e7)])

        self.assertEqual([r["position_value"] for r in rows], [5e7, 5e7])
        self.assertEqual([r["scaled_by"] for r in rows], [None, None])
        self.assertEqual(shown, ["risk_budget", "risk_budget"])

    def test_a_position_cut_to_its_share_of_the_budget_says_so(self):
        # The 2026-11-10 book: ten requests of 15%, each cut to 7.65%.
        rows, shown = self.column([self.signal(n, 1.5e8) for n in range(10)])

        self.assertEqual({r["weight_pct"] for r in rows}, {7.65})
        self.assertEqual(set(shown), {"spread_ceiling"})

    def test_a_limit_admit_names_itself_is_passed_through(self):
        row = advisor._v2_position(
            dict(self.signal(1, 1.5e8), allocated_value=5e7,
                 allocated_shares=50_000, requested_value=1.5e8,
                 allocated_binding="sector_value"),
            rank=1, equity=self.EQUITY)

        self.assertEqual(row["scaled_by"], "sector_value")

    def test_rounding_to_whole_shares_is_not_a_cut(self):
        row = advisor._v2_position(
            dict(self.signal(1, 5e7), allocated_value=5e7 - 999.0,
                 allocated_shares=49_999, requested_value=5e7,
                 allocated_binding="requested"),
            rank=1, equity=self.EQUITY)

        self.assertIsNone(row["scaled_by"])


if __name__ == "__main__":
    unittest.main()
