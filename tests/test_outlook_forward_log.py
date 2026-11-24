"""The outlook forward log: what is written, when, and when it refuses.

Offline. Every write goes to a temporary directory; the live log
(backtest_output/outlook_forward_log.jsonl) is never touched -- the module
itself refuses to write it while the suite runs, and one test holds that.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import tests  # noqa: F401  -- opens the live stores read-only first

import outlook_forward_log as ofl


def outlook_result(code="111", symbol="الف", method="monthly_sales",
                   period_end="1405/06/31", months=6, consolidated=False,
                   basis="separate", profit=1000.0, with_range=True,
                   fiscal_year=(1405, 12), shares=10.0, ttm=800.0):
    rng = None
    if with_range:
        rng = {"q10": 0.5, "q25": 0.8, "q50": 1.0, "q75": 1.25, "q90": 1.5,
               "n": 400, "key": "M6|15_30", "basis": "monthly",
               "months_known": 7, "months_bucket": 6, "band": "15_30"}
    return {
        "ins_code": code, "symbol": symbol, "method": method, "basis": basis,
        "latest": {"period_end": period_end, "months": months,
                   "consolidated": consolidated},
        "monthly": {"fiscal_year": fiscal_year},
        "forward": {"profit": profit, "eps": profit / shares, "range": rng},
        "ttm": {"profit": ttm},
        "valuation": {"price": 100.0, "shares": shares, "pe_forward": 1.0},
    }


def screen_of(*results):
    return {"ranked": list(results), "count": len(results), "financials_count": 3,
            "excluded": {"stale": 1}, "calibration": {"built_at": "2026-11-23"}}


class Paths(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ofl-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = os.path.join(self.dir, "log.jsonl")
        self.protocol = os.path.join(self.dir, "protocol.json")
        shutil.copyfile(ofl.PROTOCOL_PATH, self.protocol)

    def run_once(self, now, screen=None, annuals=None):
        return ofl.log_once(
            now=now, log_path=self.log, protocol_path=self.protocol,
            screen_fn=lambda: screen if screen is not None else screen_of(outlook_result()),
            annuals_fn=lambda codes: annuals if annuals is not None else {},
            session_fn=lambda: "2026-11-23")


class TargetYearTests(unittest.TestCase):
    def test_each_method_names_its_fiscal_year(self):
        self.assertEqual(ofl.target_fiscal_year(outlook_result()), "1405/12")
        self.assertEqual(ofl.target_fiscal_year(outlook_result(
            method="year_completed", period_end="1405/06/31", months=6)), "1405/12")
        # A year not yet begun: the year after the annual statement.
        self.assertEqual(ofl.target_fiscal_year(outlook_result(
            method="next_year_trend", period_end="1404/12/29", months=12)), "1405/12")
        self.assertIsNone(ofl.target_fiscal_year(outlook_result(method="annualised")))


class RecordTests(unittest.TestCase):
    def test_a_record_carries_the_protocol_fields(self):
        annuals = [("1405/12/29", False, 999.0),      # the target year itself
                   ("1404/12/29", True, 700.0),
                   ("1404/12/29", False, 600.0),
                   ("1403/12/29", False, 500.0)]
        rec = ofl.record(outlook_result(), annuals)
        self.assertEqual(rec["fy"], "1405/12")
        self.assertEqual(rec["profit"], {"central": 1000, "low": 800, "high": 1250,
                                         "low80": 500, "high80": 1500})
        self.assertEqual(rec["eps"], {"central": 100.0, "low": 80.0, "high": 125.0})
        self.assertEqual(rec["band"]["nominal_inner"], 0.5)
        self.assertEqual(rec["band"]["nominal_outer"], 0.8)
        # Last year's, on the estimate's basis -- never the target year's own.
        self.assertEqual(rec["naive"]["last_annual_profit"], 600)
        self.assertEqual(rec["naive"]["period_end"], "1404/12/29")
        self.assertFalse(rec["naive"]["basis_substituted"])
        self.assertEqual(rec["naive"]["ttm_profit"], 800)

    def test_the_naive_falls_back_to_the_other_basis_and_says_so(self):
        rec = ofl.record(outlook_result(), [("1404/12/29", True, 700.0)])
        self.assertEqual(rec["naive"]["last_annual_profit"], 700)
        self.assertTrue(rec["naive"]["basis_substituted"])

    def test_no_range_or_no_year_is_not_logged(self):
        self.assertIsNone(ofl.record(outlook_result(with_range=False), []))
        self.assertIsNone(ofl.record(outlook_result(method="annualised"), []))
        self.assertIsNone(ofl.record(outlook_result(profit=-5.0), []))

    def test_only_a_stated_nominal_coverage_replaces_the_default(self):
        result = outlook_result()
        result["forward"]["range"]["coverage_inner"] = 0.31     # empirical
        self.assertEqual(ofl.record(result, [])["band"]["nominal_inner"], 0.5)
        result["forward"]["range"]["nominal_inner"] = 0.6
        self.assertEqual(ofl.record(result, [])["band"]["nominal_inner"], 0.6)

    def test_the_calibration_tally_gives_its_nominal_not_its_rate(self):
        result = outlook_result()
        result["forward"]["range"].update(
            coverage={"covered": 31, "total": 100, "nominal": 0.5, "rate": 0.31},
            coverage80={"covered": 60, "total": 100, "nominal": 0.8, "rate": 0.6},
            source="pit", years=[1401, 1402, 1403])
        band = ofl.record(result, [])["band"]
        self.assertEqual((band["nominal_inner"], band["nominal_outer"]), (0.5, 0.8))
        self.assertEqual((band["source"], band["years"]), ("pit", [1401, 1402, 1403]))

    def test_the_entry_counts_what_it_did_not_log(self):
        entry = ofl.build_entry(
            screen_of(outlook_result(code="2"), outlook_result(code="1", with_range=False)),
            {}, now=datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc), protocol_hash="h")
        self.assertEqual([n["ins_code"] for n in entry["names"]], ["2"])
        self.assertEqual(entry["population"]["ranked_without_range"], 1)
        self.assertEqual(entry["population"]["financials"], 3)
        self.assertEqual(entry["date"], "2026-11-24")
        self.assertEqual(entry["logged_at_jalali"], "1405/09/03 09:30:00")

    def test_the_jalali_stamp_is_tehran_local(self):
        # 21:00 UTC is 00:30 the next day in Tehran.
        self.assertEqual(ofl.jalali_stamp(datetime(2026, 11, 23, 21, 0, tzinfo=timezone.utc)),
                         "1405/09/03 00:30:00")


class LogOnceTests(Paths):
    def test_once_per_tehran_date_and_chained(self):
        day1 = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
        self.assertIn(": logged 2026-11-24, 1 names", self.run_once(day1))
        self.assertIn("already logged", self.run_once(day1.replace(hour=17)))
        # 21:00 UTC on 11-24 is already 11-25 in Tehran.
        self.assertIn(": logged 2026-11-25", self.run_once(day1.replace(hour=21)))
        rows = ofl.read_log(self.log)
        self.assertEqual([r["date"] for r in rows], ["2026-11-24", "2026-11-25"])
        self.assertTrue(ofl.verify_chain(rows)["intact"])
        digest = ofl.protocol_sha256(self.protocol)
        self.assertTrue(all(r["protocol_sha256"] == digest for r in rows))

    def test_a_changed_protocol_is_refused(self):
        self.run_once(datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc))
        with open(self.protocol, "a", encoding="utf-8") as handle:
            handle.write("\n")
        out = self.run_once(datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc))
        self.assertIn("REFUSING", out)
        self.assertEqual(len(ofl.read_log(self.log)), 1)

    def test_a_missing_protocol_is_refused(self):
        os.remove(self.protocol)
        out = self.run_once(datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc))
        self.assertIn("protocol file missing", out)
        self.assertFalse(os.path.exists(self.log))

    def test_a_broken_chain_is_not_extended(self):
        self.run_once(datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc))
        rows = ofl.read_log(self.log)
        rows[0]["names"][0]["profit"]["central"] += 1
        with open(self.log, "w", encoding="utf-8") as handle:
            handle.write(ofl.canonical(rows[0]) + "\n")
        out = self.run_once(datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc))
        self.assertIn("broken chain", out)
        self.assertEqual(len(ofl.read_log(self.log)), 1)

    def test_a_day_with_no_range_writes_nothing(self):
        out = self.run_once(datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc),
                            screen=screen_of(outlook_result(with_range=False)))
        self.assertIn("nothing logged", out)
        self.assertFalse(os.path.exists(self.log))

    def test_it_never_raises(self):
        def broken():
            raise RuntimeError("store gone")

        out = ofl.log_once(now=datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc),
                           log_path=self.log, protocol_path=self.protocol,
                           screen_fn=broken)
        self.assertIn("failed: RuntimeError", out)

    def test_the_suite_never_writes_the_live_log(self):
        def asked():
            raise AssertionError("the screen ran under the suite")

        before = os.path.getsize(ofl.LOG_PATH) if os.path.exists(ofl.LOG_PATH) else None
        out = ofl.log_once(screen_fn=asked)
        self.assertIn("never writes the live log", out)
        after = os.path.getsize(ofl.LOG_PATH) if os.path.exists(ofl.LOG_PATH) else None
        self.assertEqual(before, after)


class ProtocolFileTests(unittest.TestCase):
    def test_the_protocol_fixes_the_rules_the_scorer_reads(self):
        protocol = ofl.load_protocol()
        p = protocol["parameters"]
        self.assertEqual(p["genesis"], ofl.GENESIS)
        self.assertEqual(p["spec"], ofl.SPEC)
        self.assertEqual(p["quantiles"], [0.10, 0.25, 0.50, 0.75, 0.90])
        self.assertEqual((p["inner_nominal"], p["outer_nominal"]), (0.50, 0.80))
        self.assertGreaterEqual(p["min_resolved_n"], 30)
        self.assertTrue(p["retirement_sticky"])
        self.assertIn("first", protocol["resolution"])

    def test_the_live_log_if_any_is_under_this_protocol(self):
        rows = ofl.read_log()
        if not rows:
            self.skipTest("no live entry yet")
        self.assertTrue(ofl.verify_chain(rows)["intact"])
        self.assertEqual(rows[0]["protocol_sha256"], ofl.protocol_sha256())


class BootstrapHookTests(unittest.TestCase):
    def test_a_failing_log_never_breaks_the_data_run(self):
        import bootstrap

        notes = []
        with mock.patch.object(ofl, "log_once", side_effect=RuntimeError("boom")), \
                mock.patch.object(bootstrap, "_note", side_effect=notes.append):
            bootstrap._outlook_forward_log()
        self.assertTrue(any("outlook forward log failed to run" in n for n in notes))

    def test_the_hook_runs_after_the_calibration_refresh(self):
        import inspect

        import bootstrap

        source = inspect.getsource(bootstrap.ensure)
        self.assertLess(source.index("_outlook_inputs()"),
                        source.index("_outlook_forward_log()"))


if __name__ == "__main__":
    unittest.main()
