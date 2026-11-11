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


# ==============================
# 2026-11-10: like with like, the exchange's shares, the bill hurdle
# ==============================

BILLS = {"rate_pct": 44.0, "as_of": "1405/08/19", "source": "test"}


def basis_period(end, months, revenue, net, consolidated):
    row = period(end, months, revenue, net)
    row["consolidated"] = consolidated
    return row


class BasisTests(unittest.TestCase):
    """A holding company's parent alone is not its group (خبهمن, 2026-11-10)."""

    holding = [
        basis_period("1405/06/31", 6, 8 * B, 6 * B, False),        # parent alone
        basis_period("1405/03/31", 3, 4 * B, 3 * B, False),
        basis_period("1404/12/29", 12, 1000 * B, 140 * B, True),   # the group
        basis_period("1404/09/30", 9, 3 * B, 2 * B, False),
        basis_period("1404/06/31", 6, 450 * B, 35 * B, True),
    ]

    def test_a_holding_is_read_on_its_consolidated_statements_only(self):
        kept, basis = fo.choose_basis(fo._periods(self.holding), (1405, 4, 1))
        self.assertEqual(basis, "consolidated")
        self.assertTrue(all(p["consolidated"] for p in kept))

    def test_no_growth_is_computed_across_the_two(self):
        r = fo.analyse("x", price=1_000, rows=self.holding, today=(1405, 4, 1),
                       opportunity=BILLS)
        self.assertEqual(r["latest"]["period_end"], "1404/12/29")
        self.assertNotAlmostEqual(r["growth"]["revenue_pct"] or 0, -98.2, 0)

    def test_stale_group_statements_give_way_to_the_parents(self):
        _, basis = fo.choose_basis(fo._periods(self.holding), (1405, 8, 19))
        self.assertEqual(basis, "separate_fallback")

    def test_bases_of_one_size_stay_one_series(self):
        rows = [basis_period("1405/06/31", 6, 120 * B, 24 * B, False),
                basis_period("1404/12/29", 12, 230 * B, 50 * B, True),
                basis_period("1404/06/31", 6, 100 * B, 20 * B, True)]
        kept, basis = fo.choose_basis(fo._periods(rows), (1405, 8, 1))
        self.assertEqual((basis, len(kept)), ("mixed", 3))


class SharesTests(unittest.TestCase):

    def test_the_exchange_share_count_beats_a_stale_capital_line(self):
        rows = [period("1405/06/31", 6, 120 * B, 24 * B),
                period("1404/12/29", 12, 300 * B, 60 * B),
                period("1404/06/31", 6, 100 * B, 20 * B)]
        r = fo.analyse("x", price=5_000, rows=rows, today=(1405, 8, 14),
                       shares=1e9, opportunity=BILLS)    # 10x the capital line
        self.assertEqual(r["valuation"]["shares_source"], "exchange")
        self.assertAlmostEqual(r["valuation"]["pe_forward"], 69.44, places=2)
        self.assertAlmostEqual(r["forward"]["eps"], 72.0)


class HurdleTests(unittest.TestCase):

    rows = [period("1405/06/31", 6, 120 * B, 24 * B),
            period("1404/12/29", 12, 300 * B, 60 * B),
            period("1404/06/31", 6, 100 * B, 20 * B)]

    def analyse(self, price):
        return fo.analyse("x", price=price, rows=self.rows, today=(1405, 8, 14),
                          opportunity=BILLS)

    def test_the_growth_a_price_requires_is_the_bill_yield_less_its_own(self):
        r = self.analyse(5_000)          # forward yield 14.4%, growth 20%
        self.assertAlmostEqual(r["hurdle"]["required_growth_pct"], 29.6)
        self.assertAlmostEqual(r["hurdle"]["cushion_pct"], -9.6)

    def test_a_price_asking_for_far_more_growth_than_there_is_is_flagged(self):
        r = self.analyse(12_000)         # yield 6%: needs 38%, has 20%
        self.assertTrue(any("لازم دارد" in f for f in r["flags"]))

    def test_a_yield_above_the_bills_needs_no_growth(self):
        r = self.analyse(1_500)          # yield 48%
        self.assertTrue(any("نیاز ندارد" in p for p in r["positives"]))


class FinancialTests(unittest.TestCase):

    def test_an_investment_company_is_valued_on_nav_not_earnings(self):
        rows = [basis_period("1405/06/31", 6, 50 * B, 45 * B, False)]
        r = fo.analyse("x", price=2_000, rows=rows, today=(1405, 8, 14),
                       sector="سرمايه گذاريها", shares=1e8,
                       portfolio={"listed_unrealised": 100 * B,
                                  "unlisted_cost": 0, "period_end": "1405/07/30"},
                       opportunity=BILLS)
        self.assertEqual(r["kind"], fo.FINANCIAL)
        # NAV = equity 400bn + unrealised 100bn; market cap 200bn
        self.assertAlmostEqual(r["valuation"]["price_to_nav"], 0.4)
        self.assertIsNone(r["hurdle"]["required_growth_pct"])


class SuspectTests(unittest.TestCase):

    def result(self, pe=5.0, growth=50.0, margin=20.0, kind=fo.OPERATING):
        return {"kind": kind, "valuation": {"pe_forward": pe},
                "growth": {"revenue_pct": growth}, "margins": {"net_pct": margin}}

    def test_implausible_results_are_named_not_ranked(self):
        self.assertIsNone(fo.suspect_reason(self.result()))
        self.assertIn("P/E", fo.suspect_reason(self.result(pe=0.03)))
        self.assertIn("رشد", fo.suspect_reason(self.result(growth=-99.9)))
        self.assertIn("حاشیه", fo.suspect_reason(self.result(margin=1252)))
        self.assertIsNone(fo.suspect_reason(self.result(margin=1252,
                                                        kind=fo.FINANCIAL)))


class NextYearTests(unittest.TestCase):

    def test_a_years_growth_is_not_carried_forward_whole(self):
        rows = [period("1405/12/29", 12, 267 * B, 50 * B)]
        rows[0]["ratios"] = {"revenue_growth": 167.0, "profit_growth": 150.0}
        r = fo.analyse("x", price=1_000, rows=rows, today=(1406, 2, 1),
                       opportunity=BILLS)
        expected = 0.5 * 1.67 + 0.5 * fo.config.INFLATION_RATE / 100.0
        self.assertAlmostEqual(r["forward"]["revenue"],
                               267 * B * (1 + min(expected, fo.GROWTH_CAP[1])))


class CashConversionTests(unittest.TestCase):

    def test_an_interim_is_read_over_twelve_months(self):
        rows = [period("1405/06/31", 6, 120 * B, 24 * B,
                       operating_cash_flow=6 * B),        # 25% on the half
                period("1404/12/29", 12, 300 * B, 60 * B,
                       operating_cash_flow=66 * B),
                period("1404/06/31", 6, 100 * B, 20 * B,
                       operating_cash_flow=8 * B)]
        r = fo.analyse("x", price=5_000, rows=rows, today=(1405, 8, 14),
                       opportunity=BILLS)
        # TTM cash 6 + 66 - 8 = 64 against TTM profit 64
        self.assertEqual(r["cash"]["cash_conversion_basis"], "ttm")
        self.assertAlmostEqual(r["cash"]["cash_conversion"], 1.0)
        self.assertFalse(any("نقد عملیاتی تبدیل" in f for f in r["flags"]))


def tape(closes, single=(), volume=50_000, value=None):
    """Bars oldest first; sessions in `single` trade at one price."""
    import pandas as pd
    rows = []
    for i, close in enumerate(closes):
        one = i in single
        rows.append({"date": pd.Timestamp("2026-10-01") + pd.Timedelta(days=i),
                     "open": close, "close": close,
                     "high": close if one else close * 1.02,
                     "low": close if one else close * 0.98,
                     "volume": volume,
                     "value": value if value is not None else volume * close})
    return pd.DataFrame(rows)


class TradingReadTests(unittest.TestCase):
    """شصفها, 2026-11-11: P/E 1.6 on a price nobody could deal at."""

    def test_a_thin_queue_locked_narrow_band_share_is_named_so(self):
        closes = [3170 + 30 * i for i in range(20)]           # +1% a session
        t = fo.trading_read(tape(closes, single=range(1, 20), value=2e8),
                            band=(3440, 3470, 3410))
        self.assertTrue(t["illiquid"])
        self.assertTrue(t["narrow_band"])
        self.assertEqual(t["queue_up_sessions"], 19)
        self.assertEqual(len(t["notes"]), 3)
        self.assertIn("کم‌معامله", t["notes"][0])

    def test_a_liquid_share_on_an_ordinary_band_has_nothing_to_say(self):
        t = fo.trading_read(tape([10_000] * 20, value=50e9),
                            band=(10_000, 10_300, 9_700))
        self.assertFalse(t["illiquid"])
        self.assertFalse(t["narrow_band"])
        self.assertEqual(t["notes"], [])

    def test_a_three_percent_band_cut_to_the_tick_is_not_narrow(self):
        # 3% of 500 is 15 rials, cut to the 10-rial tick: ±2% on paper.
        pct, narrow = fo._band_pct((500, 510, 490))
        self.assertAlmostEqual(pct, 2.0)
        self.assertFalse(narrow)

    def test_zero_volume_days_count_toward_the_mean(self):
        bars = tape([1_000] * 20, value=10e9)
        bars.loc[:9, ["volume", "value"]] = 0                 # half not traded
        t = fo.trading_read(bars)
        self.assertAlmostEqual(t["avg_value"], 5e9)
        self.assertEqual(t["traded_sessions"], 10)

    rows = [period("1405/06/31", 6, 120 * B, 24 * B),
            period("1404/12/29", 12, 300 * B, 60 * B),
            period("1404/06/31", 6, 100 * B, 20 * B)]

    def test_a_point_in_time_read_is_not_judged_on_todays_tape(self):
        r = fo.analyse("x", price=5_000, rows=self.rows, today=(1405, 8, 14),
                       opportunity=BILLS)
        self.assertIsNone(r["trading"])

    def test_the_tape_leads_the_warnings_when_given(self):
        r = fo.analyse("x", price=5_000, rows=self.rows, today=(1405, 8, 14),
                       opportunity=BILLS, bars=tape([5_000] * 20, value=1e8),
                       band=(5_000, 5_150, 4_850))
        self.assertTrue(r["trading"]["illiquid"])
        self.assertIn("کم‌معامله", r["flags"][0])


class ScreenTradingGateTests(unittest.TestCase):
    """A thin share is named under `thin`, not ranked; a queue note alone
    does not push a company over max_flags."""

    @staticmethod
    def fake(code, trading, flags=()):
        return {"kind": fo.OPERATING, "basis": "separate", "method": "year_completed",
                "latest": {"age_months": 2, "period_end": "1405/06/31"},
                "growth": {"revenue_pct": 50.0}, "margins": {"net_pct": 10.0},
                "valuation": {"pe_forward": 3.0, "earnings_yield_forward_pct": 33.3,
                              "pb": 1.0, "price_to_nav": None},
                "flags": list(flags), "trading": trading}

    def test_the_gate(self):
        from unittest import mock
        thin = {"illiquid": True, "avg_value": 3e8, "traded_sessions": 15,
                "sessions": 20, "band_pct": 0.9, "notes": ["thin"]}
        queued = {"illiquid": False, "notes": ["queue"]}
        results = {
            "1": self.fake("1", thin, flags=["thin"]),
            "2": self.fake("2", queued, flags=["queue", "one real flag"]),
        }
        instruments = [{"ins_code": c, "symbol_fa": "s" + c, "name_fa": "",
                        "sector_name": "x"} for c in results]
        import codal_announcements
        with mock.patch.object(fo.database, "load_instruments", return_value=instruments), \
             mock.patch.object(fo.database, "halted_codes", return_value={}), \
             mock.patch.object(fo.database, "load_history_tail", return_value={}), \
             mock.patch.object(fo.database, "load_codal_portfolio_latest", return_value={}), \
             mock.patch.object(fo, "latest_prices", return_value={}), \
             mock.patch.object(fo, "exchange_shares", return_value={}), \
             mock.patch.object(fo, "price_bands", return_value={}), \
             mock.patch.object(fo, "opportunity_rate", return_value=BILLS), \
             mock.patch.object(codal_announcements, "latest_dividends", return_value={}), \
             mock.patch.object(fo, "analyse", side_effect=lambda code, **_: dict(results[code])):
            s = fo.screen()
        self.assertEqual(s["excluded"]["illiquid"], 1)
        self.assertEqual([x["symbol"] for x in s["thin"]], ["s1"])
        self.assertEqual([r["symbol"] for r in s["ranked"]], ["s2"])


class LikeForLikeGrowthTests(unittest.TestCase):

    def test_growth_uses_the_filings_own_column_across_a_basis_change(self):
        """فولاد: separate six months against last year's consolidated six."""
        latest = basis_period("1405/06/31", 6, 168 * B, 30 * B, False)
        latest["ratios"] = {"revenue_growth": 68.0, "profit_growth": 40.0}
        rows = [latest,
                basis_period("1404/12/29", 12, 300 * B, 60 * B, True),
                basis_period("1404/06/31", 6, 130 * B, 25 * B, True)]
        r = fo.analyse("x", price=1_000, rows=rows, today=(1405, 8, 14),
                       opportunity=BILLS)
        self.assertEqual(r["basis"], "mixed")
        self.assertAlmostEqual(r["growth"]["revenue_pct"], 68.0)
        # The year is still completed from the annual's own basis.
        self.assertAlmostEqual(r["ttm"]["revenue"], (168 + 300 - 130) * B)


class SecondAuditTests(unittest.TestCase):
    """حافرین's one-off, همراه's gap to TSETMC, جوین's early-year doubling."""

    def test_a_profit_outside_the_business_is_named(self):
        self.assertIsNotNone(fo.one_off_reason(
            {"net_profit": 23.6 * B, "operating_profit": 3.05 * B}))
        self.assertIsNotNone(fo.one_off_reason(
            {"net_profit": 5 * B, "operating_profit": -1 * B}))
        self.assertIsNone(fo.one_off_reason(
            {"net_profit": 12 * B, "operating_profit": 10 * B}))
        self.assertIsNone(fo.one_off_reason({"net_profit": -5 * B,
                                             "operating_profit": 1 * B}))

    def test_a_gap_to_the_published_pe_is_named(self):
        self.assertIsNotNone(fo.published_gap_reason(4.8, 8.6))
        self.assertIsNone(fo.published_gap_reason(2.18, 2.2))
        self.assertIsNone(fo.published_gap_reason(None, 2.2))

    def test_a_year_projected_from_two_months_is_flagged(self):
        rows = [period("1405/04/31", 12, 100 * B, 20 * B)]
        monthly = [{"period_end": "1405/06/31", "template": "manufacturer",
                    "sales": {"month": 150 * B, "ytd": 300 * B,
                              "ytd_prev": 100 * B}}]
        r = fo.analyse("x", price=1_000, rows=rows, monthly_rows=monthly,
                       today=(1405, 7, 10), opportunity=BILLS)
        self.assertEqual(r["method"], "monthly_sales")
        self.assertEqual(r["monthly"]["months_elapsed"], 2)
        self.assertTrue(any("فقط بر 2 ماه" in f for f in r["flags"]), r["flags"])

    def test_the_trailing_multiples_are_the_outlooks_ttm(self):
        rows = [period("1405/06/31", 6, 120 * B, 24 * B),
                period("1404/12/29", 12, 300 * B, 60 * B),
                period("1404/06/31", 6, 100 * B, 20 * B)]
        t = fo.trailing_multiples({"c": rows},
                                  {"c": {"close": 5_000, "total_shares": 1e8}})
        # market cap 500bn / TTM profit 64bn; revenue 320bn
        self.assertAlmostEqual(t["c"]["pe"], 7.81, places=2)
        self.assertAlmostEqual(t["c"]["ps"], 1.56, places=2)
        self.assertEqual(fo.trailing_multiples({"c": rows}, {}), {})


class ScreenDoubtfulTests(unittest.TestCase):
    """A multiple that is not a valuation is named, not ranked."""

    def test_doubtful_multiples_are_listed_apart(self):
        from unittest import mock
        import codal_announcements
        clean = ScreenTradingGateTests.fake("1", None)
        doubted = dict(ScreenTradingGateTests.fake("2", None, flags=["one-off"]),
                       doubts=[{"code": "one_off", "label": "سود غیرعملیاتی"}])
        results = {"1": clean, "2": doubted}
        instruments = [{"ins_code": c, "symbol_fa": "s" + c, "name_fa": "",
                        "sector_name": "x"} for c in results]
        with mock.patch.object(fo.database, "load_instruments", return_value=instruments), \
             mock.patch.object(fo.database, "halted_codes", return_value={}), \
             mock.patch.object(fo.database, "load_history_tail", return_value={}), \
             mock.patch.object(fo.database, "load_codal_portfolio_latest", return_value={}), \
             mock.patch.object(fo, "latest_prices", return_value={}), \
             mock.patch.object(fo, "exchange_shares", return_value={}), \
             mock.patch.object(fo, "price_bands", return_value={}), \
             mock.patch.object(fo, "published_pe", return_value={}), \
             mock.patch.object(fo, "opportunity_rate", return_value=BILLS), \
             mock.patch.object(codal_announcements, "latest_dividends", return_value={}), \
             mock.patch.object(fo, "analyse", side_effect=lambda code, **_: dict(results[code])):
            s = fo.screen()
        self.assertEqual([r["symbol"] for r in s["ranked"]], ["s1"])
        self.assertEqual(s["excluded"]["doubtful"], 1)
        self.assertEqual(s["doubtful"][0]["reasons"], ["سود غیرعملیاتی"])


class NavScopeTests(unittest.TestCase):
    """The NAV verification of 2026-11-12: who gets a P/NAV, and its caveats."""

    rows = [basis_period("1405/06/31", 6, 50 * B, 20 * B, False)]

    def analyse(self, sector, listed_market, shares=1e9, price=1_000, **portfolio):
        pf = {"listed_market": listed_market, "listed_cost": listed_market / 4,
              "listed_unrealised": listed_market * 0.75, "unlisted_cost": 0,
              "holdings": 5, "period_end": "1405/07/30"}
        pf.update(portfolio)
        return fo.analyse("x", price=price, rows=self.rows, today=(1405, 8, 14),
                          sector=sector, shares=shares, portfolio=pf, opportunity=BILLS)

    def test_a_manufacturer_with_a_few_listed_shares_gets_no_pnav(self):
        r = self.analyse("خودرو و ساخت قطعات", listed_market=10 * B)      # 1% of 1,000bn
        self.assertIsNone(r["valuation"]["price_to_nav"])

    def test_a_holding_listed_under_its_subsidiaries_industry_does(self):
        r = self.analyse("سيمان، آهك و گچ", listed_market=800 * B)
        self.assertIsNotNone(r["valuation"]["price_to_nav"])

    def test_an_impossible_stake_withholds_the_pnav_and_says_so(self):
        r = self.analyse("سرمايه گذاريها", listed_market=800 * B, inconsistent_rows=1)
        self.assertIsNone(r["valuation"]["price_to_nav"])
        self.assertTrue(any("ناسازگار" in n for n in r["valuation"]["nav_notes"]))

    def test_a_raise_since_the_statement_is_named(self):
        # capital 100bn rials = 100m shares; the exchange counts 150m.
        r = self.analyse("سرمايه گذاريها", listed_market=80 * B, shares=1.5e8)
        self.assertTrue(any("افزایش" in n for n in r["valuation"]["nav_notes"]))
