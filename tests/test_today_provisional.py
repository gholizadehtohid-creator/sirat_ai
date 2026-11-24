"""today_provisional: today's figures before the exchange publishes them.

2026-11-24: the panel showed the session before until the evening -- "a day
or two behind" -- while the overview already carried the day's index and the
session task had recorded the day's حقیقی/حقوقی volumes. These tests pin the
promises: display only, labelled, bounded, and hidden once official.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import today_provisional as tp  # noqa: E402

TUESDAY = date(2026, 11, 24)                    # a trading day


def tehran(hh, mm, day=TUESDAY):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tp.TEHRAN)


OVERVIEW = {
    "indexLastValue": 8591498.89, "indexChange": 201684.82,
    "indexEqualWeightedLastValue": 2580954.28,
    "indexEqualWeightedChange": 50629.74,
    "marketActivityDEven": 20261124, "marketState": "N",
    "marketStateTitle": "بسته", "marketActivityZTotTran": 883811,
    "marketActivityQTotCap": 571173388566828.0,
}


class Overview:
    """A provider whose one request is the overview; nothing else answers."""

    base_url = "https://example.invalid"

    def __init__(self, payload=None, fail=False):
        self.payload = payload or OVERVIEW
        self.fail = fail
        self.urls = []

    def _get(self, url, timeout=None):
        self.urls.append(url)
        if self.fail:
            raise ConnectionError("unreachable")
        payload = {"marketOverview": self.payload}

        class Response:
            def json(self_inner):
                return payload

        return Response()

    def get_client_type_all(self):
        self.urls.append("GetClientTypeAll")

        class Failed:
            ok = False
            data = None

        return Failed()


def make_db(path, captures=(), snapshot_at=None, snapshot=(), bars=()):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE client_type_live (ins_code TEXT, captured_at TEXT,
            individual_buy_volume REAL, individual_sell_volume REAL,
            institutional_buy_volume REAL, institutional_sell_volume REAL,
            individual_buy_count REAL, individual_sell_count REAL,
            institutional_buy_count REAL, institutional_sell_count REAL);
        CREATE TABLE market_snapshot (ins_code TEXT, close REAL,
            captured_at TEXT);
        CREATE TABLE price_history (ins_code TEXT, date TEXT, close REAL);
        CREATE TABLE index_history (ins_code TEXT, date TEXT, close REAL);
        CREATE TABLE client_type_history (ins_code TEXT, date TEXT);
    """)
    for code, at, ib, isell, nb, nsell in captures:
        conn.execute("INSERT INTO client_type_live VALUES (?,?,?,?,?,?,0,0,0,0)",
                     (code, at, ib, isell, nb, nsell))
    for code, close in snapshot:
        conn.execute("INSERT INTO market_snapshot VALUES (?,?,?)",
                     (code, close, snapshot_at))
    for code, day, close in bars:
        conn.execute("INSERT INTO price_history VALUES (?,?,?)",
                     (code, day, close))
    conn.commit()
    conn.close()


class Temp(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat-prov-")
        self.db = os.path.join(self.dir, "m.db")
        self.json = os.path.join(self.dir, "today.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, True)


class OverviewTests(unittest.TestCase):

    def test_the_days_index_figures_and_their_date(self):
        parsed = tp.parse_overview(OVERVIEW)
        self.assertEqual(parsed["session"], "2026-11-24")
        self.assertEqual(parsed["market_state"], "N")
        cap = parsed["indices"]["TEDPIX"]
        self.assertEqual(cap["value"], 8591498.89)
        # The change is against yesterday's close: value - change.
        self.assertAlmostEqual(cap["change_pct"],
                               201684.82 / (8591498.89 - 201684.82) * 100, 2)
        self.assertEqual(parsed["indices"]["TEDPIX_EW"]["value"], 2580954.28)

    def test_an_overview_without_a_date_is_refused(self):
        with self.assertRaises(ValueError):
            tp.parse_overview(dict(OVERVIEW, marketActivityDEven=0))


class FlowEstimateTests(Temp):

    def test_volume_times_price_net_turnover_and_breadth(self):
        flow = tp.estimate_flow(
            {"a": (100, 40, 0, 60), "b": (10, 30, 20, 0), "c": (0, 0, 0, 0),
             "d": (5, 1, 0, 4)},
            {"a": 1000.0, "b": 2000.0, "c": 50.0})
        self.assertEqual(flow["net_individual"], 60 * 1000 - 20 * 2000)
        self.assertEqual(flow["turnover"], 100 * 1000 + 30 * 2000)
        self.assertEqual((flow["symbols"], flow["inflow_count"]), (2, 1))
        self.assertEqual(flow["unpriced"], 1)       # "d" traded, no price
        self.assertEqual(flow["breadth_pct"], 50.0)

    def test_nothing_traded_is_no_estimate(self):
        self.assertIsNone(tp.estimate_flow({"a": (0, 0, 0, 0)}, {"a": 1.0}))

    def test_the_sessions_last_capture_after_the_close_is_final(self):
        make_db(self.db, captures=[
            ("a", "2026-11-24T07:00:00+00:00", 1, 1, 0, 0),       # 10:30
            ("a", "2026-11-24T10:50:17+00:00", 100, 40, 0, 60),   # 14:20
            ("b", "2026-11-24T10:50:17+00:00", 10, 30, 20, 0),
            ("a", "2026-11-23T10:50:00+00:00", 9, 9, 9, 9)],      # yesterday
            bars=[("a", "2026-11-23", 1000.0), ("b", "2026-11-23", 2000.0),
                  ("a", "2026-11-22", 7.0)])
        flow = tp.flow_for(TUESDAY, codes=["a", "b"], db=self.db,
                           allow_bulk=False)
        self.assertTrue(flow["complete"])
        self.assertEqual(flow["volumes_from"], "live_capture")
        self.assertEqual(flow["price_from"], "previous_close")
        self.assertEqual(flow["net_individual"], 60 * 1000 - 20 * 2000)
        self.assertTrue(flow["volumes_at"].startswith("2026-11-24T14:20"))

    def test_a_capture_before_the_close_is_flagged_incomplete(self):
        make_db(self.db, captures=[
            ("a", "2026-11-24T08:43:00+00:00", 5, 1, 0, 4)],      # 12:13
            bars=[("a", "2026-11-23", 10.0)])
        flow = tp.flow_for(TUESDAY, codes=["a"], db=self.db, allow_bulk=False)
        self.assertFalse(flow["complete"])

    def test_a_snapshot_taken_after_the_close_prices_the_volumes(self):
        make_db(self.db, captures=[
            ("a", "2026-11-24T10:50:00+00:00", 10, 0, 0, 10)],
            snapshot_at="2026-11-24T10:35:00+00:00",               # 14:05
            snapshot=[("a", 1100.0), ("zz", 1.0)],
            bars=[("a", "2026-11-23", 1000.0)])
        flow = tp.flow_for(TUESDAY, codes=["a"], db=self.db, allow_bulk=False)
        self.assertEqual(flow["price_from"], "snapshot")
        self.assertEqual(flow["net_individual"], 10 * 1100)

    def test_a_mid_session_snapshot_is_not_the_days_price(self):
        make_db(self.db, captures=[
            ("a", "2026-11-24T10:50:00+00:00", 10, 0, 0, 10)],
            snapshot_at="2026-11-24T07:55:00+00:00",               # 11:25
            snapshot=[("a", 1100.0)],
            bars=[("a", "2026-11-23", 1000.0)])
        flow = tp.flow_for(TUESDAY, codes=["a"], db=self.db, allow_bulk=False)
        self.assertEqual(flow["price_from"], "previous_close")

    def test_no_record_of_the_session_is_said(self):
        make_db(self.db)
        flow = tp.flow_for(TUESDAY, codes=["a"], db=self.db, allow_bulk=False)
        self.assertIn("error", flow)


class DueTests(unittest.TestCase):
    """At most two overview requests a session day."""

    NONE = {"indices": "2026-11-23", "flow": "2026-11-23"}

    def due(self, now, stored=None, official=None):
        return tp.due(now=now, stored=stored or {},
                      official=official or self.NONE)[0]

    def test_not_on_a_thursday(self):
        self.assertFalse(self.due(tehran(15, 0, date(2026, 11, 26))))

    def test_not_before_the_close_settles(self):
        self.assertFalse(self.due(tehran(12, 35)))

    def test_once_after_the_close(self):
        self.assertTrue(self.due(tehran(12, 45)))
        read = {"fetched_at": tehran(12, 45).isoformat()}
        self.assertFalse(self.due(tehran(13, 30), stored=read))

    def test_again_once_the_final_capture_is_in(self):
        read = {"fetched_at": tehran(12, 45).isoformat()}
        self.assertTrue(self.due(tehran(14, 40), stored=read))
        late = {"fetched_at": tehran(14, 40).isoformat()}
        self.assertFalse(self.due(tehran(18, 0), stored=late))

    def test_a_reading_from_yesterday_does_not_count(self):
        read = {"fetched_at": tehran(14, 40, date(2026, 11, 23)).isoformat()}
        self.assertTrue(self.due(tehran(13, 0), stored=read))

    def test_never_once_the_official_flow_carries_today(self):
        official = {"indices": "2026-11-24", "flow": "2026-11-24"}
        self.assertFalse(self.due(tehran(19, 0), official=official))


class CurrentTests(unittest.TestCase):

    STORED = {"session": "2026-11-24", "fetched_at": "x", "market_state": "N",
              "indices": {"TEDPIX": {"value": 1.0}},
              "flow": {"net_individual": 5.0}}

    def test_both_parts_while_nothing_is_official(self):
        shown = tp.current(stored=self.STORED,
                           official={"indices": "2026-11-23",
                                     "flow": "2026-11-23"})
        self.assertEqual(shown["session"], "2026-11-24")
        self.assertIsNotNone(shown["indices"])
        self.assertIsNotNone(shown["flow"])

    def test_the_official_index_replaces_the_provisional_one(self):
        shown = tp.current(stored=self.STORED,
                           official={"indices": "2026-11-24",
                                     "flow": "2026-11-23"})
        self.assertIsNone(shown["indices"])
        self.assertIsNotNone(shown["flow"])

    def test_nothing_once_both_are_official(self):
        self.assertIsNone(tp.current(stored=self.STORED,
                                     official={"indices": "2026-11-24",
                                               "flow": "2026-11-24"}))

    def test_a_failed_estimate_is_not_shown(self):
        stored = dict(self.STORED, flow={"error": "no record"})
        shown = tp.current(stored=stored, official={"indices": "2026-11-23",
                                                    "flow": "2026-11-23"})
        self.assertIsNone(shown["flow"])

    def test_no_reading_is_nothing(self):
        self.assertIsNone(tp.current(stored={}, official={}))


class RefreshTests(Temp):

    def test_one_overview_request_and_a_file_never_the_series(self):
        make_db(self.db, captures=[
            ("a", "2026-11-24T10:50:00+00:00", 10, 0, 0, 10)],
            bars=[("a", "2026-11-23", 1000.0)])
        before = os.path.getsize(self.db)
        provider = Overview()
        reading = tp.refresh(provider=provider, codes=["a"], db=self.db,
                             now=tehran(14, 40), path=self.json)
        self.assertEqual(provider.urls, [provider.base_url +
                                         "/api/MarketData/GetMarketOverview/1"])
        self.assertEqual(reading["session"], "2026-11-24")
        self.assertEqual(reading["flow"]["net_individual"], 10000)
        with open(self.json, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["session"], "2026-11-24")
        # Display only: the store is opened read-only and is unchanged.
        self.assertEqual(os.path.getsize(self.db), before)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM index_history").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM client_type_history").fetchone()[0], 0)
        finally:
            conn.close()

    def test_without_a_capture_one_bulk_request_on_the_day(self):
        make_db(self.db, bars=[("a", "2026-11-23", 1000.0)])
        provider = Overview()
        reading = tp.refresh(provider=provider, codes=["a"], db=self.db,
                             now=tehran(14, 40), path=self.json)
        self.assertEqual(provider.urls[1:], ["GetClientTypeAll"])
        self.assertIn("error", reading["flow"])

    def test_an_unreachable_exchange_stores_nothing(self):
        reading = tp.refresh(provider=Overview(fail=True), codes=[],
                             db=self.db, now=tehran(14, 40), path=self.json)
        self.assertIn("error", reading)
        self.assertFalse(os.path.exists(self.json))

    def test_the_suite_writes_to_scratch_not_the_live_tree(self):
        live = os.path.join(tp.ROOT, "data")
        self.assertFalse(os.path.normcase(tp.PATH).startswith(
            os.path.normcase(live) + os.sep))


if __name__ == "__main__":
    unittest.main()
