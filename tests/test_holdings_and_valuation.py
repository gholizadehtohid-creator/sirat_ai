"""holdings: the user's own book; market_valuation: the market's P/E in context."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import holdings  # noqa: E402
import market_valuation as mv  # noqa: E402


class ParseTests(unittest.TestCase):

    def test_lines_commas_and_persian_digits(self):
        self.assertEqual(
            holdings.parse("فولاد 1000، شپنا 5000\nزر 20000, وبملت\r\nفولاد ۵۰۰"),
            [("فولاد", 1500.0), ("شپنا", 5000.0), ("زر", 20000.0), ("وبملت", None)])

    def test_nothing_is_an_empty_book(self):
        self.assertEqual(holdings.parse(""), [])
        self.assertEqual(holdings.parse(" ,، \n "), [])

    def test_the_book_is_capped(self):
        text = "\n".join("س%d" % i for i in range(holdings.MAX_SYMBOLS + 5))
        self.assertEqual(len(holdings.parse(text)), holdings.MAX_SYMBOLS)


def row(symbol, value, sector="فلزات", flags=0, halted=None, equity=True):
    return {"symbol": symbol, "value": value, "sector": sector, "as_of": "2026-11-10",
            "equity": equity,
            "halted_since": halted,
            "outlook": {"flags": ["x"] * flags} if flags is not None else None}


class SummaryTests(unittest.TestCase):

    def test_weights_add_to_the_book(self):
        out = holdings._summarise([row("الف", 300), row("ب", 700, sector="سیمان")])
        self.assertEqual([r["weight_pct"] for r in out["rows"]], [30.0, 70.0])
        self.assertEqual(out["total_value"], 1000)

    def test_one_name_or_one_industry_carrying_the_book_is_called_out(self):
        rows = [row("الف", 500), row("ب", 300), row("ج", 100, sector="سیمان"),
                row("د", 100, sector="دارو")]
        warnings = holdings._summarise(rows)["warnings"]
        self.assertTrue(any("الف" in w and "تمرکز" in w for w in warnings))
        self.assertTrue(any("فلزات" in w and "صنعت" in w for w in warnings))
        self.assertFalse(any("«ج»" in w for w in warnings))

    def test_halts_warnings_and_unknown_symbols_are_listed(self):
        rows = [row("الف", 100, flags=3), row("ب", 100, halted="2026-10-01"),
                {"symbol": "زززز", "error": "نماد پیدا نشد"}]
        warnings = holdings._summarise(rows)["warnings"]
        self.assertTrue(any("3 هشدار" in w for w in warnings))
        self.assertTrue(any("متوقف" in w for w in warnings))
        self.assertTrue(any("زززز" in w for w in warnings))

    def test_no_quantities_means_no_weights_and_no_concentration(self):
        out = holdings._summarise([row("الف", None), row("ب", None)])
        self.assertIsNone(out["total_value"])
        self.assertFalse(any("تمرکز" in w for w in out["warnings"]))


class FundBookTests(unittest.TestCase):
    """The plan's own book: an index fund, gold, bills (panel review, 2026-11-11)."""

    def test_an_index_fund_carrying_the_book_is_not_one_names_risk(self):
        warnings = holdings._summarise([row("فیروزه", 700, sector=None, equity=False),
                                        row("عیار", 300, sector=None, equity=False)])["warnings"]
        self.assertFalse(any("تمرکز" in w for w in warnings))

    def test_a_quantity_without_a_price_is_named_and_listed(self):
        out = holdings._summarise([row("الف", 100),
                                   {"symbol": "اخزا512", "quantity": 10, "bill": True,
                                    "sleeve": "fixed", "error": None}])
        self.assertEqual(out["unpriced"], ["اخزا512"])
        self.assertTrue(any("اخزا512" in w and "قیمتی" in w for w in out["warnings"]))

    def test_funds_are_filed_by_the_opportunities_rule(self):
        fund = lambda name: {"kind": "fund", "is_equity": 0, "name_fa": name}
        self.assertEqual(holdings.sleeve_of({"is_equity": 1}), "equity")
        self.assertEqual(holdings.sleeve_of(fund("صندوق سرمایه گذاری آوند مفید-د")), "fixed")
        self.assertEqual(holdings.sleeve_of(fund("صندوق شاخص30 شرکت فیروزه-سهام")), "equity")
        self.assertEqual(holdings.sleeve_of(fund("صندوق طلای عیار مفید")), "gold")
        self.assertIsNone(holdings.sleeve_of(fund("صندوق اهرمی کاریزما")))

    def test_a_fund_without_bars_is_priced_from_the_market_watch(self):
        from unittest import mock
        import pandas as pd
        with mock.patch.object(holdings.database, "load_history_by_inscode",
                               return_value=pd.DataFrame()),                 mock.patch.object(holdings.database, "_connect") as connect:
            connect.return_value.execute.return_value.fetchone.return_value = (
                46972.0, 46518.0, 46500.0, "2026-11-11T15:30:09+00:00")
            close, previous, as_of, source = holdings._last_two("x")
        self.assertEqual((close, previous, as_of, source),
                         (46972.0, 46500.0, "2026-11-11", "snapshot"))


class BookRiskTests(unittest.TestCase):
    """The reader's own book through the basket's risk arithmetic."""

    def test_valued_rows_become_positions(self):
        import risk
        seen = {}

        def analyse(positions, equity=None):
            seen["positions"], seen["equity"] = positions, equity
            return {"available": True, "sessions": 120, "names_measured": 2,
                    "names_dropped": [], "volatility": {"annualised_pct": 30.0},
                    "var": {"historical_pct": -2.0, "historical_rials": 2.0},
                    "drawdown": {"max_drawdown_pct": -20.0}, "beta": {"beta": 0.9},
                    "benchmark": "شاخص کل", "correlation": {"average": 0.4},
                    "concentration": {"hhi": 0.5}, "notes": []}

        rows = [{"symbol": "الف", "ins_code": "1", "value": 60.0, "sector": "فلزات"},
                {"symbol": "ب", "ins_code": "2", "value": 40.0, "sector": None},
                {"symbol": "پ", "ins_code": "3", "value": None},
                {"symbol": "ت", "error": "نماد پیدا نشد"}]
        with mock.patch.object(risk, "analyse", side_effect=analyse):
            out = holdings._risk(rows, 100.0)
        self.assertEqual([p["symbol"] for p in seen["positions"]], ["الف", "ب"])
        # The rial base is the measured holdings (risk.py), not a passed equity.
        self.assertIsNone(seen["equity"])
        self.assertEqual(seen["positions"][1]["sector_name"], "بدون صنعت")
        self.assertTrue(out["available"])
        self.assertEqual(out["average_correlation"], 0.4)

    def test_no_quantities_means_no_risk(self):
        self.assertIsNone(holdings._risk([{"symbol": "الف", "ins_code": "1", "value": None}], None))

    def test_a_move_past_the_band_is_marked(self):
        self.assertTrue(holdings._beyond_band(-40.0))
        self.assertFalse(holdings._beyond_band(2.9))
        self.assertFalse(holdings._beyond_band(None))


class ValuationSummaryTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        points = [{"date": "2021-%02d-01" % (i + 1), "pe": pe,
                   "earnings_yield_pct": round(100 / pe, 2), "companies": 300}
                  for i, pe in enumerate([4.0, 5.0, 6.0, 7.0, 8.0, 5.0])]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"points": points}, handle)
        self.patch = mock.patch.object(mv, "FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        os.unlink(self.path)

    def test_now_is_placed_in_its_own_history(self):
        s = mv.summary(opportunity={"rate_pct": 43.0, "as_of": "x", "source": "t"})
        self.assertTrue(s["available"])
        self.assertEqual(s["now"]["pe"], 5.0)
        # below: 4.0; equal: two 5.0s -> (1 + 0.5*2) / 6
        self.assertAlmostEqual(s["percentile"], 33.3)
        self.assertEqual(s["median_pe"], 5.5)
        self.assertAlmostEqual(s["yield_gap_pct"], 20.0 - 43.0)

    def test_no_history_is_said_plainly(self):
        with mock.patch.object(mv, "FILE", self.path + ".missing"):
            self.assertFalse(mv.summary()["available"])


class RouteTests(unittest.TestCase):
    """/api/holdings and /api/valuation, and how a wrong request is answered."""

    @classmethod
    def setUpClass(cls):
        import app as panel
        cls.panel = panel
        cls.client = panel.app.test_client()

    def setUp(self):
        self.panel.invalidate()

    def test_the_list_travels_in_the_body_not_the_url(self):
        seen = {}

        def review(entries):
            seen["entries"] = entries
            return {"rows": [], "warnings": [], "count": len(entries)}

        with mock.patch.object(holdings, "review", side_effect=review):
            r = self.client.post("/api/holdings", json={"list": "فولاد 1000\nشپنا"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["count"], 2)
        self.assertEqual(seen["entries"][0][0], "فولاد")

    def test_a_get_is_refused_with_its_own_status(self):
        r = self.client.get("/api/holdings?list=x")
        self.assertEqual(r.status_code, 405)
        self.assertTrue(r.get_json()["error"].startswith("MethodNotAllowed: 405"))

    def test_an_unknown_route_keeps_the_message_the_panel_reads(self):
        r = self.client.get("/api/no-such-route")
        self.assertEqual(r.status_code, 404)
        self.assertTrue(r.get_json()["error"].startswith("NotFound: 404"))

    def test_valuation_reads_the_stored_summary(self):
        with mock.patch.object(mv, "summary", return_value={"available": False, "reason": "x"}):
            r = self.client.get("/api/valuation")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["available"])


class SectorValuationTests(unittest.TestCase):
    """Each industry against its own history."""

    def points(self, sectors_by_point):
        return [{"date": "2025-%02d-01" % (i + 1), "pe": 7.0, "earnings_yield_pct": 14.0,
                 "companies": 300, "sectors": s} for i, s in enumerate(sectors_by_point)]

    def test_an_industry_is_placed_in_its_own_history(self):
        # P/E 10, 9, ..., 1 then 5.5 now: 4.5 of 13 points below... spelled out:
        pes = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 6, 7, 5.5]
        pts = self.points([{"فلزات": [pe * 100, 100, 6]} for pe in pes])
        out = mv._sectors(pts)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["sector"], "فلزات")
        self.assertEqual(row["pe"], 5.5)
        self.assertEqual(row["points"], 13)
        below = sum(1 for v in pes if v < 5.5)
        self.assertAlmostEqual(row["percentile"], round((below + 0.5) / 13 * 100, 1))

    def test_thin_loss_making_and_short_histories_are_not_listed(self):
        good = {"الف": [700, 100, 5]}
        pts = self.points([dict(good, **{"کم": [500, 100, 2], "زیان": [500, -10, 9]})
                           for _ in range(12)] )
        names = [r["sector"] for r in mv._sectors(pts)]
        self.assertEqual(names, ["الف"])
        # eleven points are not a history
        self.assertEqual(mv._sectors(pts[:11]), [])

    def test_an_industry_missing_today_is_not_listed(self):
        pts = self.points([{"الف": [700, 100, 5]}] * 12 + [{"ب": [700, 100, 5]}])
        self.assertEqual(mv._sectors(pts), [])

    def test_summary_keeps_the_sector_detail_out_of_now(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"points": self.points([{"الف": [700, 100, 5]}] * 12)}, f)
            with mock.patch.object(mv, "FILE", path):
                s = mv.summary(opportunity={"rate_pct": 40.0, "as_of": "x", "source": "t"})
            self.assertNotIn("sectors", s["now"])
            self.assertEqual(s["sectors"][0]["sector"], "الف")
        finally:
            os.unlink(path)


class LatestWithTtmTests(unittest.TestCase):
    """A newest filing that cannot give a trailing year (found 2026-11-11)."""

    def outlook(self, ttm, kind=None):
        import fundamental_outlook as fo
        return {"kind": kind or fo.OPERATING, "ttm": {"profit": ttm}}

    def test_the_filing_before_gives_the_trailing_year(self):
        newest, older = {"p": "1405/06/31"}, {"p": "1405/03/31"}
        seen = []

        def analyse(rows):
            seen.append(list(rows))
            return self.outlook(None if newest in rows else 7.0)

        out = mv._latest_with_ttm(analyse, [older, newest], newest)
        self.assertEqual(out["ttm"]["profit"], 7.0)
        self.assertEqual(seen[1], [older])

    def test_a_newest_filing_with_a_trailing_year_is_used_as_is(self):
        calls = []

        def analyse(rows):
            calls.append(rows)
            return self.outlook(5.0)

        newest = {"p": "n"}
        self.assertEqual(mv._latest_with_ttm(analyse, [{"p": "o"}, newest], newest)
                         ["ttm"]["profit"], 5.0)
        self.assertEqual(len(calls), 1)

    def test_financials_and_hopeless_cases_are_returned_unchanged(self):
        import fundamental_outlook as fo
        newest = {"p": "n"}
        fin = self.outlook(None, kind=fo.FINANCIAL)
        self.assertIs(mv._latest_with_ttm(lambda rows: fin, [newest], newest), fin)
        none = self.outlook(None)
        self.assertIs(mv._latest_with_ttm(lambda rows: none, [{"p": "o"}, newest], newest), none)


class _Bars:
    """The two PointInTimeData calls the trading rule makes."""

    def __init__(self, frames):
        self.frames = frames

    def bars(self, code):
        return self.frames.get(code)

    def position(self, code, date):
        import pandas as pd
        frame = self.frames.get(code)
        if frame is None or frame.empty:
            return -1
        return int(frame["date"].searchsorted(pd.Timestamp(date), side="right")) - 1


class TradedRecentlyTests(unittest.TestCase):
    """Which companies the market P/E counts on a date (found 2026-11-11)."""

    def setUp(self):
        import pandas as pd
        days = pd.to_datetime(["2026-10-01", "2026-10-20", "2026-11-04", "2026-11-07"])
        self.data = _Bars({
            # traded on 11-07 and has no bar on 11-08: a short halt, still in
            "short": pd.DataFrame({"date": days, "volume": [5, 5, 5, 5]}),
            # frozen since 10-01, zero-volume bars after: halted, out
            "frozen": pd.DataFrame({"date": days, "volume": [5, 0, 0, 0]}),
            # no zero-volume bars at all after 10-01: the same halt, also out
            "gone": pd.DataFrame({"date": days[:1], "volume": [5]}),
            "never": pd.DataFrame({"date": days, "volume": [0, 0, 0, 0]}),
        })

    def test_a_missing_bar_on_the_day_is_not_a_halt(self):
        self.assertTrue(mv._traded_recently(self.data, "short", "2026-11-08"))

    def test_a_long_halt_is_out_whether_or_not_frozen_bars_were_written(self):
        self.assertFalse(mv._traded_recently(self.data, "frozen", "2026-11-08"))
        self.assertFalse(mv._traded_recently(self.data, "gone", "2026-11-08"))

    def test_it_is_point_in_time(self):
        # On 10-20 the frozen name had traded 19 days earlier: still in.
        self.assertTrue(mv._traded_recently(self.data, "frozen", "2026-10-20"))
        # Before its first bar a name is simply not there.
        self.assertFalse(mv._traded_recently(self.data, "short", "2026-09-01"))

    def test_never_traded_and_unknown_are_out(self):
        self.assertFalse(mv._traded_recently(self.data, "never", "2026-11-08"))
        self.assertFalse(mv._traded_recently(self.data, "missing", "2026-11-08"))


if __name__ == "__main__":
    unittest.main()
