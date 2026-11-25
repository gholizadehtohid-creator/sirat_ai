"""stock_profile.py and the multiples beside P/E (2026-11-26)."""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_content  # noqa: E402
import fundamental_outlook as fo  # noqa: E402
import market_valuation as mv  # noqa: E402
import stock_profile as sp  # noqa: E402

B = 1e9


def annual(year, revenue, net, consolidated=False, months=12, end=None):
    return {"period_end": end or "%d/12/29" % year, "period_months": months,
            "items": {"revenue": revenue, "net_profit": net, "capital": 100 * B},
            "ratios": {}, "published_at": "x", "audited": True,
            "consolidated": consolidated}


class GrowthTests(unittest.TestCase):

    def test_compound_growth_over_three_and_five_years(self):
        rows = [annual(1405 - i, 100 * B * 1.5 ** (5 - i), 10 * B * 1.4 ** (5 - i))
                for i in range(6)]
        g = sp.growth(rows)
        self.assertAlmostEqual(g["revenue_3y"], 0.5)
        self.assertAlmostEqual(g["revenue_5y"], 0.5)
        self.assertAlmostEqual(g["profit_5y"], 0.4)

    def test_a_missing_year_is_not_bridged(self):
        rows = [annual(y, 100 * B, 10 * B) for y in (1405, 1404, 1403, 1401, 1400)]
        g = sp.growth(rows)
        self.assertIsNone(g["revenue_3y"])        # 1402 is not stored

    def test_a_loss_has_no_growth_rate(self):
        rows = [annual(1405 - i, 100 * B, (-5 if i == 3 else 10) * B) for i in range(4)]
        self.assertIsNone(sp.growth(rows)["profit_3y"])

    def test_one_basis_only(self):
        rows = [annual(1405, 200 * B, 20 * B, consolidated=True)] + \
               [annual(1405 - i, 100 * B, 10 * B) for i in range(1, 4)]
        self.assertIsNone(sp.growth(rows)["revenue_3y"])

    def test_a_short_year_breaks_the_chain(self):
        rows = [annual(1405, 160 * B, 16 * B, end="1405/09/30"),
                annual(1404, 90 * B, 9 * B, end="1404/09/30"),          # 9 months
                {"period_end": "1404/06/31", "period_months": 6,
                 "items": {"revenue": 60 * B, "net_profit": 6 * B}, "ratios": {},
                 "consolidated": False},
                annual(1403, 100 * B, 10 * B), annual(1402, 80 * B, 8 * B),
                annual(1401, 60 * B, 6 * B)]
        self.assertIsNone(sp.growth(rows)["revenue_3y"])


class PriceTests(unittest.TestCase):

    def frames(self, beta=1.5, weeks=70):
        days = pd.bdate_range("2025-01-01", periods=weeks * 5)
        import math
        idx = [1000.0]
        for i in range(1, len(days)):
            idx.append(idx[-1] * (1 + 0.01 * math.sin(i / 3.0)))
        stock = [100.0]
        for i in range(1, len(days)):
            stock.append(stock[-1] * (1 + beta * (idx[i] / idx[i - 1] - 1)))
        index = pd.DataFrame({"date": days, "close": idx})
        bars = pd.DataFrame({"date": days, "open": stock, "high": stock, "low": stock,
                             "close": stock, "volume": 1e6, "value": 1e9, "trades": 10})
        return bars, index

    def test_beta_is_read_on_weeks_against_the_index(self):
        bars, index = self.frames(beta=1.5)
        p = sp.price(bars, index)
        self.assertAlmostEqual(p["beta"], 1.5, delta=0.1)
        self.assertGreater(p["volatility"], 0)
        self.assertLessEqual(p["from_high"], 0)
        self.assertGreaterEqual(p["from_low"], 0)

    def test_too_few_bars_say_nothing(self):
        bars, index = self.frames(weeks=5)
        self.assertIsNone(sp.price(bars, index))


class MultiplesTests(unittest.TestCase):

    def test_ps_ev_ebit_and_p_fcf_on_twelve_months(self):
        rows = [
            {"period_end": "1405/06/31", "period_months": 6, "ratios": {}, "consolidated": False,
             "items": {"revenue": 120 * B, "net_profit": 24 * B, "operating_profit": 30 * B,
                       "capital": 100 * B, "equity": 400 * B, "borrowings_short": 50 * B,
                       "cash": 10 * B, "operating_cash_flow": 30 * B, "capex": -10 * B}},
            {"period_end": "1404/12/29", "period_months": 12, "ratios": {}, "consolidated": False,
             "items": {"revenue": 300 * B, "net_profit": 60 * B, "operating_profit": 70 * B}},
            {"period_end": "1404/06/31", "period_months": 6, "ratios": {}, "consolidated": False,
             "items": {"revenue": 100 * B, "net_profit": 20 * B, "operating_profit": 25 * B}},
        ]
        r = fo.analyse("x", price=5_000, rows=rows, today=(1405, 8, 14), monthly_rows=[],
                       inflation_pct=40)
        v = r["valuation"]
        self.assertAlmostEqual(r["ttm"]["operating"] / B, 75)          # 30 + 70 - 25
        self.assertAlmostEqual(v["ps"], round(500 / 320, 2))
        self.assertAlmostEqual(v["ev_ebit"], round((500 + 40) / 75, 2))
        self.assertAlmostEqual(v["p_fcf"], round(500 / 40, 2))         # fcf 20 x 2


class MarketMultiplesTests(unittest.TestCase):

    def test_the_sums_give_the_market_multiples(self):
        acc = {"book": [0.0, 0.0, 0.0, 0], "sales": [0.0, 0.0, 0], "ebit": [0.0, 0.0, 0]}
        for cap, book, ttm, rev, ebit, nd in ((100, 50, 10, 200, 15, 20), (300, 100, 30, 300, 45, -10)):
            mv._add_more(acc, {"valuation": {"book_equity": book, "net_debt": nd},
                               "ttm": {"revenue": rev, "operating": ebit}}, cap, ttm)
        m = mv._multiples(acc)
        self.assertAlmostEqual(m["pb"], round(400 / 150, 2))
        self.assertAlmostEqual(m["roe_pct"], round(40 / 150 * 100, 1))
        self.assertAlmostEqual(m["ps"], round(400 / 500, 2))
        self.assertAlmostEqual(m["ev_ebit"], round(410 / 60, 2))

    def test_the_dividend_yield_counts_only_recent_agms(self):
        point = {"by_company": {str(i): [1000 * B, 100 * B] for i in range(60)}}
        dividends = {str(i): {"dps": 50.0, "capital": 100_000.0, "fiscal_year_end": "1404/12/29"}
                     for i in range(55)}
        dividends["0"]["fiscal_year_end"] = "1399/12/29"             # stopped paying
        d = mv.dividend_yield(point, dividends, today=(1405, 9, 5))
        self.assertEqual(d["companies"], 54)
        # 50 rials x 100m shares = 5bn on 1,000bn each
        self.assertAlmostEqual(d["pct"], 0.5)
        self.assertIsNone(mv.dividend_yield(point, {}, today=(1405, 9, 5)))


class BotProfileTests(unittest.TestCase):

    def test_two_short_lines_or_nothing(self):
        line = bot_content.profile_line(
            {"growth": {"revenue_3y": 0.36, "profit_3y": 0.35},
             "price": {"beta": 1.19, "volatility": 0.47, "from_high": -0.12}},
            {"ps": 0.52, "ev_ebit": 2.85})
        self.assertIn("رشد سالانه‌ی ۳ ساله", line)
        self.assertIn("بتا", line)
        self.assertIn("EV/EBIT", line)
        self.assertEqual(bot_content.profile_line(None), "")


if __name__ == "__main__":
    unittest.main()
