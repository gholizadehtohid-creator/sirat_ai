"""fundamental_outlook's 2026-11-23 additions: the trailing margin, the
discrete quarters, the calibrated range, the dollar what-ifs, a capital
raise, the fair-value range and the monthly history's reads."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundamental_outlook as fo  # noqa: E402

B = 1e9
BILLS = {"rate_pct": 44.0, "as_of": None, "source": "test"}


def period(end, months, revenue, net, consolidated=False, **extra):
    items = {"revenue": revenue, "net_profit": net, "capital": 100 * B,
             "equity": 400 * B}
    items.update(extra)
    return {"period_end": end, "period_months": months, "items": items,
            "ratios": {}, "published_at": end, "audited": True,
            "consolidated": consolidated}


def analyse(rows, **kw):
    kw.setdefault("today", (1405, 8, 14))
    kw.setdefault("opportunity", BILLS)
    return fo.analyse("x", price=kw.pop("price", 5_000), rows=rows, **kw)


class TrailingMarginTests(unittest.TestCase):

    def test_the_rest_of_the_year_is_at_the_trailing_margin(self):
        rows = [period("1405/06/31", 6, 120 * B, 12 * B),     # 10% so far
                period("1404/12/29", 12, 300 * B, 60 * B),
                period("1404/06/31", 6, 100 * B, 20 * B)]
        r = analyse(rows)
        # TTM 52 / 320 = 16.25%; the remaining 240 at that, not at 10%
        self.assertAlmostEqual(r["forward"]["profit"], 12 * B + 240 * B * 52 / 320)
        self.assertEqual(r["method"], "year_completed")


class QuarterTests(unittest.TestCase):

    def test_cumulative_filings_are_differenced_into_quarters(self):
        rows = [period("1405/09/30", 9, 90 * B, 18 * B),
                period("1405/06/31", 6, 50 * B, 10 * B),
                period("1405/03/31", 3, 20 * B, 5 * B)]
        q = analyse(rows)["quarters"]
        self.assertEqual([x["revenue"] for x in q], [20 * B, 30 * B, 40 * B])
        self.assertEqual([x["net_profit"] for x in q], [5 * B, 5 * B, 8 * B])
        self.assertEqual(q[-1]["quarter"], 3)

    def test_a_quarter_across_two_bases_is_left_out(self):
        rows = [period("1405/06/31", 6, 50 * B, 10 * B, consolidated=True),
                period("1405/03/31", 3, 20 * B, 5 * B)]
        q = fo.quarterly_series(fo._periods(rows))
        self.assertEqual([x["quarter"] for x in q], [1])


CALIBRATION = {
    "buckets": {"6": {"n": 400, "q10": 0.5, "q25": 0.8, "q50": 1.0, "q75": 1.25,
                      "q90": 1.6, "median_abs_error": 0.25}},
    "fx": {"sectors": {"فلزات اساسي": {"revenue": 1.0, "revenue_raw": 1.0, "r2": 0.3,
                                         "n": 100, "cost": 0.5, "cost_r2": 0.3}},
           "companies": {"x": {"revenue": 1.0, "cost": 0.5, "n": 12, "r2": 0.3}}},
}
FX = {"fetched_at": "2026-11-23T00:00:00+00:00",
      "series": {"nima": [("2026-03-21", "1405/01/01", 1_500_000.0),
                          ("2026-11-22", "1405/09/01", 2_000_000.0)],
                 "free": [("2026-11-22", "1405/09/01", 2_700_000.0)]}}


class RangeAndDollarTests(unittest.TestCase):

    rows = [period("1405/06/31", 6, 120 * B, 24 * B, gross_profit=36 * B),
            period("1404/12/29", 12, 300 * B, 60 * B, gross_profit=90 * B),
            period("1404/06/31", 6, 100 * B, 20 * B, gross_profit=30 * B)]

    def setUp(self):
        self.r = analyse(self.rows, calibration=CALIBRATION, fx_record=FX,
                         sector="فلزات اساسي")

    def test_the_range_is_the_estimate_times_its_past_quantiles(self):
        rg = self.r["forward"]["range"]
        eps = self.r["forward"]["eps"]
        self.assertAlmostEqual(rg["eps_low"], eps * 0.8)
        self.assertAlmostEqual(rg["eps_high"], eps * 1.25)
        self.assertFalse(rg["wide"])

    def test_a_dearer_dollar_raises_an_exporters_year_and_a_cheaper_lowers_it(self):
        d = self.r["fx"]
        by = {s["shock_pct"]: s for s in d["scenarios"]}
        base = self.r["forward"]["eps"]
        self.assertGreater(by[20]["eps"], base)
        self.assertGreater(by[40]["eps"], by[20]["eps"])
        self.assertLess(by[-20]["eps"], base)
        self.assertEqual(d["sensitivity"]["cost_basis"], "measured")
        self.assertFalse(d["weak"])

    def test_the_dollar_never_moves_the_base_estimate(self):
        plain = analyse(self.rows, sector="فلزات اساسي")
        self.assertAlmostEqual(plain["forward"]["profit"], self.r["forward"]["profit"])
        self.assertIsNone(plain["fx"])


class CapitalTests(unittest.TestCase):

    def test_a_rights_offering_is_named_cash(self):
        c = fo.capital_reading([
            ("1405/08/20 10:00:00", "اعلامیه پذیره نویسی عمومی"),
            ("1405/07/01 10:00:00", "پیشنهاد هیئت مدیره به مجمع عمومی فوق العاده در خصوص افزایش سرمایه")])
        self.assertTrue(c["cash"])
        self.assertEqual(c["stage"], "subscription")
        self.assertIn("رقیق", c["text"])

    def test_a_registered_raise_is_done(self):
        c = fo.capital_reading([("1405/08/20 10:00:00", "اگهی ثبت افزایش سرمایه")])
        self.assertTrue(c["done"])

    def test_nothing_to_say_without_events(self):
        self.assertIsNone(fo.capital_reading([]))
        self.assertIsNone(fo.capital_reading([("1405/08/20", "تغییر نشانی")]))


class FairValueTests(unittest.TestCase):

    peers = {"sector_pe": {"فلزات اساسي": 5.0}, "sector_n": {"فلزات اساسي": 20},
             "market_pe": 7.0, "own_pe": {"x": {"median": 6.0, "low": 4, "high": 8,
                                                "n": 12, "from": "a", "to": "b"}}}

    def test_trailing_multiples_go_on_the_trailing_eps_industry_central(self):
        fv = fo.fair_value(fo.OPERATING, "x", "فلزات اساسي", 1_000, 1_400, None,
                           5_000, self.peers)
        self.assertEqual(fv["central_basis"], "sector")
        self.assertAlmostEqual(fv["central"], 5_000)
        self.assertAlmostEqual(fv["low"], 5_000)
        self.assertAlmostEqual(fv["high"], 7_000)
        self.assertAlmostEqual(fv["upside_pct"], 0.0)
        self.assertAlmostEqual(fv["forward"]["central"], 7_000)

    def test_no_trailing_profit_no_value(self):
        self.assertIsNone(fo.fair_value(fo.OPERATING, "x", "s", -10, 100, None,
                                        5_000, self.peers))

    def test_a_financial_is_valued_at_its_nav(self):
        fv = fo.fair_value(fo.FINANCIAL, "x", "s", None, None, None, 1_000, None,
                           nav_result={"nav": 300 * B}, shares=100e6)
        self.assertAlmostEqual(fv["central"], 3_000)
        self.assertAlmostEqual(fv["upside_pct"], 200.0)


class MonthlyHistoryTests(unittest.TestCase):

    @staticmethod
    def month(y, m, sales, purchases):
        return {"period_end": "%04d/%02d/30" % (y, m), "template": "manufacturer",
                "sales": {"month": sales, "ytd": sales * 8, "ytd_prev": sales * 4},
                "purchases": {"month": purchases, "ytd": purchases * 8,
                              "ytd_prev": purchases * 4}}

    def test_the_last_three_months_against_a_year_before(self):
        rows = []
        for k in range(15):            # newest first: 1405/08 back to 1404/06
            y, m = fo.jalali.add_months(1405, 8, -k)
            this_year = k < 3
            rows.append(self.month(y, m, (110 if this_year else 100) * B,
                                   (80 if this_year else 50) * B))
        out = fo.monthly_reads("x", 12, rows)
        self.assertAlmostEqual(out["recent_growth"], 0.10)
        self.assertAlmostEqual(out["recent_purchases_to_sales"], 80 / 110)
        self.assertEqual(out["months_stored"], 15)

    def test_one_month_says_nothing_about_the_last_three(self):
        out = fo.monthly_reads("x", 12, [self.month(1405, 8, 100 * B, 50 * B)])
        self.assertIsNone(out["recent_growth"])


if __name__ == "__main__":
    unittest.main()
