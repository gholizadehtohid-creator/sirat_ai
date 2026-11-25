"""The fundamental outlook's ranking: fast, and the same answer (2026-11-25).

The owner: the ranking takes very long. It took 26 s, and the button asked
for a fresh one on every click. Two fixes, pinned here:

  - screen() reads every company's statements, monthly reports and last
    traded day at once, not through a connection per company per store
    (16 of the 26 s). The batch reads must return exactly what the
    per-company reads return;
  - the page reads the cached ranking, and the panel keeps one computed.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_monthly  # noqa: E402
import fundamental_db  # noqa: E402
import fundamental_outlook as fo  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Store(unittest.TestCase):
    """A scratch fundamentals store."""

    def setUp(self):
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        self.previous = fundamental_db.set_path(self.store)
        fundamental_db.create_database()

    def tearDown(self):
        fundamental_db.set_path(self.previous)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.store + suffix)
            except OSError:
                pass


class MetricsBatchTests(Store):

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(self.store)
        for code, periods in (("a", 15), ("b", 3), ("c", 0)):
            for k in range(periods):
                year = 1390 + k // 4
                month = (k % 4 + 1) * 3
                conn.execute(
                    "INSERT INTO metrics (ins_code, period_end, period_months,"
                    " tracing_no, symbol, title, published_at, audited,"
                    " consolidated, items, ratios, source, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (code, "%d/%02d/30" % (year, month), month, "t%s%d" % (code, k),
                     code, "title", "2020-01-01", k % 2, 0,
                     json.dumps({"revenue": k}), json.dumps({"x": k}), "s", "u"))
            # Two lengths for one period end: the order must hold.
            if periods:
                conn.execute(
                    "INSERT INTO metrics (ins_code, period_end, period_months,"
                    " items) VALUES (?,?,?,?)",
                    (code, "%d/12/30" % (1390 + (periods - 1) // 4), 9,
                     json.dumps({"revenue": -1})))
        conn.commit()
        conn.close()

    def test_the_batch_is_the_per_company_read(self):
        many = fundamental_db.load_metrics_many(["a", "b", "c"], limit=12)
        for code in ("a", "b"):
            self.assertEqual(many[code], fundamental_db.load_metrics(code, limit=12))
        self.assertNotIn("c", many)                  # nothing stored
        self.assertEqual(len(many["a"]), 12)

    def test_without_a_limit_every_period(self):
        many = fundamental_db.load_metrics_many(None)
        self.assertEqual(many["a"], fundamental_db.load_metrics("a"))

    def test_only_the_codes_asked_for(self):
        self.assertEqual(set(fundamental_db.load_metrics_many(["b"])), {"b"})


class MonthlyBatchTests(Store):

    def setUp(self):
        super().setUp()
        codal_monthly._ensure_tables()
        conn = sqlite3.connect(self.store)
        rows = []
        for k in range(18):
            period = "1404/%02d/30" % (k % 12 + 1) if k < 12 else "1405/%02d/30" % (k - 11)
            rows.append(("t%d" % k, "a", period, "2025-%02d-05" % (k % 12 + 1)))
        # A correction: a later letter for the same month wins.
        rows.append(("fix", "a", "1405/06/30", "2099-01-01"))
        rows.append(("b1", "b", "1405/01/30", "2025-01-01"))
        for tracing, code, period, published in rows:
            conn.execute(
                "INSERT INTO monthly_reports (tracing_no, ins_code, period_end,"
                " published_at, title, payload) VALUES (?,?,?,?,?,?)",
                (tracing, code, period, published, "title",
                 json.dumps({"period_end": period, "tracing": tracing})))
        conn.commit()
        conn.close()

    def test_the_batch_is_the_per_company_read(self):
        many = codal_monthly.load_many(["a", "b", "z"], limit=13)
        self.assertEqual(many["a"], codal_monthly.load("a", limit=13))
        self.assertEqual(many["b"], codal_monthly.load("b", limit=13))
        self.assertNotIn("z", many)
        self.assertEqual(many["a"][0]["tracing"], "fix")
        self.assertEqual(len(many["a"]), 13)


class HaltTests(unittest.TestCase):

    def test_the_rule(self):
        self.assertIsNone(fo._halt_from("2026-11-20", None))
        self.assertEqual(fo._halt_from(None, "2026-11-24"), "never")
        self.assertIsNone(fo._halt_from("2026-11-17", "2026-11-24"))     # 7 days
        self.assertEqual(fo._halt_from("2026-11-16", "2026-11-24"), "2026-11-16")

    def test_every_code_at_once_matches_one_at_a_time(self):
        import database

        handle, market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        conn = sqlite3.connect(market)
        conn.execute("CREATE TABLE price_history (ins_code TEXT, date TEXT,"
                     " close REAL, volume REAL, PRIMARY KEY (ins_code, date))")
        for code, day, volume in (("a", "2026-11-24", 5), ("a", "2026-11-10", 5),
                                  ("b", "2026-11-24", 0), ("b", "2026-11-01", 9),
                                  ("c", "2026-11-24", 0)):
            conn.execute("INSERT INTO price_history VALUES (?,?,1,?)",
                         (code, day, volume))
        conn.commit()
        conn.close()
        try:
            with mock.patch.object(database, "DATABASE_NAME", market), \
                    mock.patch.object(database, "_ensure_wal"), \
                    mock.patch.object(fo, "_market_date", return_value="2026-11-24"):
                every = fo.halted_since_all()
                one = {code: fo.halted_since(code) for code in ("a", "b", "c")}
        finally:
            os.unlink(market)
        self.assertEqual(every, one)
        self.assertEqual(every, {"a": None, "b": "2026-11-01", "c": "never"})


class PrereadTests(unittest.TestCase):

    def test_a_failed_batch_leaves_that_store_to_the_per_company_read(self):
        with mock.patch.object(fundamental_db, "load_metrics_many",
                               side_effect=RuntimeError("locked")), \
                mock.patch.object(codal_monthly, "load_many",
                                  return_value={"a": [{"m": 1}]}), \
                mock.patch.object(fo, "halted_since_all", return_value={}), \
                mock.patch.object(fo, "_market_date", return_value="2026-11-24"):
            preread = fo._preread_all(["a", "b"])
        self.assertNotIn("metrics", preread("a"))
        self.assertEqual(preread("a")["monthly"], [{"m": 1}])
        self.assertEqual(preread("b")["monthly"], [])
        self.assertEqual(preread("b")["halted"], "never")

    def test_analyse_reads_the_preread_not_the_store(self):
        with mock.patch.object(fundamental_db, "load_metrics",
                               side_effect=AssertionError("read per company")):
            self.assertIsNone(fo.analyse("x", preread={"metrics": []},
                                         calibration=None, fx_record=None,
                                         capital_events=None, peers=None,
                                         dividend=None, shares=None,
                                         sector=None, portfolio=None,
                                         bars=None, band=None, published=None))


class WarmTests(unittest.TestCase):

    def setUp(self):
        import app
        self.app = app
        app.invalidate()

    def tearDown(self):
        self.app.invalidate()

    def warm(self, running=False):
        import bootstrap
        with mock.patch.object(bootstrap, "is_running", return_value=running), \
                mock.patch.object(self.app, "data_version", return_value="v1"), \
                mock.patch("fundamental_outlook.screen",
                           return_value={"ranked": []}) as screen:
            done = self.app.warm_outlook()
        return done, screen.call_count

    def test_it_computes_the_ranking_once_and_keeps_it(self):
        self.assertEqual(self.warm(), (True, 1))
        self.assertEqual(self.warm(), (False, 0))

    def test_it_recomputes_before_the_answer_ages_out(self):
        self.warm()
        key = self.app.cache_key("outlook", "v1")
        self.app._cache[key]["at"] = time.time() - self.app.OUTLOOK_REWARM_AGE - 1
        self.assertEqual(self.warm(), (True, 1))

    def test_not_while_a_data_run_is_writing(self):
        self.assertEqual(self.warm(running=True), (False, 0))

    def test_the_button_reads_the_cache(self):
        with open(os.path.join(ROOT, "static", "js", "panel.js"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('on("runOutlookBtn", "click", () => loadOutlook());', source)
        self.assertNotIn("loadOutlook(true)", source)

    def test_the_panel_starts_the_warmer(self):
        import inspect
        self.assertIn("start_warmer()", inspect.getsource(self.app.main))


if __name__ == "__main__":
    unittest.main()
