"""fundamental_outlook: TTM, the completed year, and the warnings."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundamental_outlook as fo  # noqa: E402

B = 1e9


def period(end, months, revenue, net, capital=100 * B, **extra):
    items = {"revenue": revenue, "net_profit": net, "capital": capital,
             "equity": 400 * B}
    items.update(extra)
    return {"period_end": end, "period_months": months, "items": items,
            "ratios": {}, "published_at": end, "audited": True}


class FiscalYearTests(unittest.TestCase):

    def test_an_interim_belongs_to_the_year_it_ends_in(self):
        self.assertEqual(fo.fiscal_year_end("1405/06/31", 6), (1405, 12))
        self.assertEqual(fo.fiscal_year_end("1405/02/31", 12), (1405, 2))
        self.assertEqual(fo.fiscal_year_end("1404/11/30", 9), (1405, 2))


class CompletionTests(unittest.TestCase):
    """Six months in, prior year known: complete the year, do not double it."""

    rows = [
        period("1405/06/31", 6, 120 * B, 24 * B),          # this year so far
        period("1404/12/29", 12, 300 * B, 60 * B),         # last full year
        period("1404/06/31", 6, 100 * B, 20 * B),          # last year, same 6m
    ]

    def setUp(self):
        self.r = fo.analyse("x", price=5_000, rows=self.rows,
                            today=(1405, 8, 14))

    def test_ttm_is_this_half_plus_last_years_second_half(self):
        self.assertAlmostEqual(self.r["ttm"]["revenue"], 320 * B)
        self.assertAlmostEqual(self.r["ttm"]["profit"], 64 * B)

    def test_forward_grows_the_remaining_months_at_the_ytd_rate(self):
        # remaining = (300 - 100) * 1.2 = 240; year = 120 + 240 = 360
        self.assertAlmostEqual(self.r["forward"]["revenue"], 360 * B)
        # profit at the 20% YTD margin on the remaining 240
        self.assertAlmostEqual(self.r["forward"]["profit"], 72 * B)
        self.assertEqual(self.r["method"], "year_completed")

    def test_per_share_and_valuation(self):
        # 100bn rials of capital at 1,000 par = 100m shares
        self.assertAlmostEqual(self.r["forward"]["eps"], 720.0)
        # market cap 500bn / forward profit 72bn
        self.assertAlmostEqual(self.r["valuation"]["pe_forward"], 6.94, places=2)


class OwnersShareTests(unittest.TestCase):

    def test_margins_use_total_profit_and_eps_the_owners_part(self):
        rows = [period("1405/12/29", 12, 100 * B, 40 * B,
                       net_profit_owners=20 * B)]
        rows[0]["ratios"] = {"revenue_growth": 25.0, "profit_growth": 25.0}
        r = fo.analyse("x", price=1_000, rows=rows, today=(1406, 2, 1))
        # prior year backed out of the filing's own growth: 32bn / 80bn = 40%
        self.assertEqual(r["margins"]["net_pct"], 40.0)
        self.assertEqual(r["margins"]["net_prev_pct"], 40.0)
        self.assertFalse(any("حاشیه" in f for f in r["flags"]))
        self.assertAlmostEqual(r["ttm"]["eps"], 200.0)


class FlagTests(unittest.TestCase):

    def test_receivables_outrunning_sales_is_flagged(self):
        rows = [
            period("1405/06/31", 6, 110 * B, 20 * B, receivables=90 * B),
            period("1404/12/29", 12, 200 * B, 40 * B),
            period("1404/06/31", 6, 100 * B, 18 * B, receivables=50 * B),
        ]
        r = fo.analyse("x", price=None, rows=rows, today=(1405, 8, 1))
        self.assertTrue(any("مطالبات" in f for f in r["flags"]))

    def test_an_old_statement_is_flagged_stale(self):
        rows = [period("1403/12/29", 12, 100 * B, 10 * B)]
        r = fo.analyse("x", rows=rows, today=(1405, 8, 1))
        self.assertTrue(any("ماه پیش" in f for f in r["flags"]))

    def test_nothing_stored_is_none_not_zero(self):
        self.assertIsNone(fo.analyse("x", rows=[]))



class MonthlyTests(unittest.TestCase):
    """The monthly report replaces an older statement's year estimate."""

    statements = [
        period("1405/06/31", 6, 120 * B, 24 * B),
        period("1404/12/29", 12, 300 * B, 60 * B),
        period("1404/06/31", 6, 100 * B, 20 * B),
    ]

    monthly = [{
        "period_end": "1405/08/30", "template": "manufacturer",
        "sales": {"month": 30 * B, "ytd": 200 * B, "ytd_prev": 160 * B},
        "purchases": {"ytd": 150 * B, "ytd_prev": 80 * B},
        "products": [{"name": "ورق", "unit": "تن",
                      "ytd": {"rate": 150, "sales_qty": 10, "production": 15,
                              "amount": 1.0},
                      "ytd_prev": {"rate": 100, "sales_qty": 12}}],
    }]

    def setUp(self):
        self.r = fo.analyse("x", price=5_000, rows=self.statements,
                            monthly_rows=self.monthly, today=(1405, 9, 10))

    def test_the_year_grows_at_the_monthly_ytd_rate(self):
        # 300bn last year x (200/160) = 375bn
        self.assertEqual(self.r["method"], "monthly_sales")
        self.assertAlmostEqual(self.r["forward"]["revenue"], 375 * B)
        self.assertEqual(self.r["monthly"]["months_elapsed"], 8)

    def test_price_and_volume_are_separated(self):
        p = self.r["monthly"]["products"][0]
        self.assertAlmostEqual(p["price_change"], 0.5)
        self.assertAlmostEqual(p["volume_change"], 10 / 12 - 1)

    def test_purchases_outrunning_sales_and_inventory_build_are_flagged(self):
        self.assertTrue(any("خرید مواد اولیه" in f for f in self.r["flags"]))
        self.assertTrue(any("انباشت موجودی" in f for f in self.r["flags"]))

    def test_an_older_monthly_report_does_not_override_the_statement(self):
        old = [dict(self.monthly[0], period_end="1405/05/31")]
        r = fo.analyse("x", price=5_000, rows=self.statements,
                       monthly_rows=old, today=(1405, 9, 10))
        self.assertEqual(r["method"], "year_completed")


if __name__ == "__main__":
    unittest.main()
