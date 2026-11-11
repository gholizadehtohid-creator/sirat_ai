"""Valuation on today's share count, and two inputs a score must not take.

Each of these produced a plausible number on the fundamentals card rather than
an error, which is why each is pinned:

  P/E was today's price over the EPS the filing stood on. After a capital
  increase that EPS is per OLD share, so the multiple came out cheaper by the
  increase -- شصفها read P/E 0.04 and an earnings yield of 2,613%.

  The card scored investment companies, banks and insurers on P/E, which put
  وجامی (P/E 1.16) first and 14 such names in the top 40.

  Growth off a near-zero base: فناوا's EPS +175,200% took full growth points.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_sync  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import financials  # noqa: E402
import fundamental_db  # noqa: E402
import fundamental_engine  # noqa: E402
import fundamental_scoring  # noqa: E402


# شصفها's six months to 1405/06/31 as filed (rials), and the exchange on
# 2026-11-10: 300 billion rials of capital is 300 million shares; the market
# trades 15.3 billion.
SHASAFAHA = {
    "revenue": 114_509_006e6,
    "net_profit": 13_482_954e6,
    "eps": 44_943.0,
    "capital": 300_000e6,
    "equity": 36_551_253e6,
}

SHASAFAHA_MARKET = {"close": 3_440.0, "total_shares": 15.3e9}


def stored(ratios=None, **items):
    """A codal_financials row as database.load_codal_latest() returns it."""

    base = {
        "revenue": 250e9, "net_profit": 200e9, "net_profit_owners": 200e9,
        "eps": 200.0, "capital": 1_000e9,
        "total_assets": 1_000e9, "total_liabilities": 800e9,
        "equity": 200e9, "equity_owners": 200e9,
        "current_assets": 600e9, "current_liabilities": 300e9,
        "operating_cash_flow": 220e9,
    }
    base.update(items)

    return {"period_end": "1405/06/31", "period_months": 12, "title": "t",
            "audited": True, "consolidated": False, "items": base,
            "ratios": dict(ratios or {}), "updated_at": None}


# ==============================
# P/E IN TODAY'S SHARES
# ==============================

class PriceAndEarningsInTodaysSharesTest(unittest.TestCase):
    """financials.ratios(): what the card and the v1 scan value."""

    def test_a_capital_increase_does_not_make_the_share_cheap(self):
        r = financials.ratios(SHASAFAHA, market=SHASAFAHA_MARKET, months=6)

        # Market cap 52,632 bn over a year's profit 26,966 bn. The filed EPS
        # against today's price said 0.04 and 2,612.97%.
        self.assertAlmostEqual(r["pe"], 1.95)
        self.assertAlmostEqual(r["earnings_yield"], 51.23)

        # P/B was already struck on today's count; it stays there.
        self.assertAlmostEqual(r["pb"], 1.44)

    def test_an_eps_already_on_todays_count_is_not_corrected_twice(self):
        """A misread capital line must not move the multiple.

        ملت's capital line read 78,044 billion rials against 30.7 billion
        shares, and its EPS is on the 30.7. Rescaling the EPS by the two counts
        would have cut its P/E 2.5x; market cap over profit never reads the
        capital line.
        """

        statement = {"net_profit": 100e9, "eps": 1_000.0, "capital": 250e9}

        r = financials.ratios(
            statement, market={"close": 5_000.0, "total_shares": 100e6},
            months=12)

        self.assertAlmostEqual(r["pe"], 5.0)

    def test_without_todays_count_the_filed_eps_is_used_as_before(self):
        r = financials.ratios(SHASAFAHA, market={"close": 3_440.0}, months=6)

        self.assertAlmostEqual(r["pe"], round(3_440.0 / (44_943.0 * 2), 2))

    def test_a_loss_or_an_eps_of_the_wrong_sign_has_no_pe(self):
        for statement in (dict(SHASAFAHA, net_profit=-1e12, eps=-100.0),
                          # EPS positive, profit a loss: a misread filing.
                          dict(SHASAFAHA, net_profit=-1e12),
                          dict(SHASAFAHA, eps=-100.0)):
            r = financials.ratios(statement, market=SHASAFAHA_MARKET, months=6)

            self.assertIsNone(r["pe"])
            self.assertIsNone(r["earnings_yield"])


class MarketRatiosAtTodaysCountTest(unittest.TestCase):
    """fundamental_engine.market_ratios(): what the v2 engine values."""

    RECORD = {
        "period_months": 6,
        "items": {"revenue": 500e9, "net_profit": 50e9,
                  "equity_owners": 1_000e9},
        "ratios": {"shares": 100e6, "eps_annual": 1_000.0},
    }

    def test_todays_count_replaces_the_filed_one(self):
        r = fundamental_engine.market_ratios(self.RECORD, 500.0, shares=1e9)

        self.assertAlmostEqual(r["market_cap"], 500e9)
        self.assertEqual(r["shares"], 1e9)
        self.assertAlmostEqual(r["pe"], 5.0)            # 500e9 / (50e9 * 2)
        self.assertAlmostEqual(r["earnings_yield"], 20.0)
        self.assertAlmostEqual(r["pb"], 0.5)
        self.assertAlmostEqual(r["ps"], 0.5)

    def test_without_it_the_point_in_time_answer_is_unchanged(self):
        r = fundamental_engine.market_ratios(self.RECORD, 500.0)

        self.assertEqual(r["shares"], 100e6)
        self.assertAlmostEqual(r["pe"], 0.5)            # 500 / 1000
        self.assertAlmostEqual(r["pb"], 0.05)

    def test_the_multiple_free_sectors_stay_without_one(self):
        sector = sorted(config.EARNINGS_MULTIPLE_NOT_MEANINGFUL)[0]

        r = fundamental_engine.market_ratios(self.RECORD, 500.0,
                                             sector=sector, shares=1e9)

        self.assertIsNone(r["pe"])
        self.assertIsNone(r["earnings_yield"])
        self.assertAlmostEqual(r["pb"], 0.5)


class LiveV2PathTest(unittest.TestCase):
    """evaluate_stored() reads today's count when it strikes today's price."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = os.path.join(self.tmp, "market.db")
        database.create_database()
        database.save_snapshot(pd.DataFrame([
            {"ins_code": "777", "symbol_fa": "x", "close": 500.0,
             "total_shares": 1e9},
        ]))

        self.previous = fundamental_db.set_path(
            os.path.join(self.tmp, "store.db"))
        fundamental_db.create_database()
        fundamental_db.save_metrics(dict(
            MarketRatiosAtTodaysCountTest.RECORD, ins_code="777",
            period_end="1405/06/31", published_at="1405/07/30 10:00:00"))

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _evaluate(self, **kwargs):
        with mock.patch.object(fundamental_engine, "market_ratios",
                               wraps=fundamental_engine.market_ratios) as spy:
            result = fundamental_engine.evaluate_stored("777", price=500.0,
                                                        **kwargs)

        return result, spy.call_args.kwargs.get("shares")

    def test_the_snapshot_count_is_read_per_company(self):
        self.assertEqual(fundamental_engine._exchange_shares("777"), 1e9)
        self.assertIsNone(fundamental_engine._exchange_shares("404"))

    def test_a_live_restrike_values_on_it(self):
        result, shares = self._evaluate()

        self.assertEqual(shares, 1e9)

        notes = result["groups"][fundamental_engine.VALUATION]["notes"]

        # 0.5 -- "very cheap" -- on the filing's 100 million shares.
        self.assertTrue(any(n.startswith("P/E 5.0") for n in notes), notes)

    def test_a_store_read_as_of_a_past_date_gets_no_count_of_today(self):
        _result, shares = self._evaluate(before="9999")

        self.assertIsNone(shares)


# ==============================
# P/E WHERE IT MEANS NOTHING
# ==============================

class CardWithholdsMeaninglessMultiplesTest(unittest.TestCase):
    """codal_sync.metrics_for(): the card scores no bank on its P/E."""

    BANK = sorted(config.EARNINGS_MULTIPLE_NOT_MEANINGFUL)[0]
    METAL = "فلزات اساسي"
    MARKET = {"close": 600.0, "total_shares": 1e9}

    def _metrics(self, sector_by_code=None, instruments=()):
        latest = {"1": stored(), "2": stored()}

        with mock.patch.object(database, "load_codal_latest",
                               return_value=latest), \
                mock.patch.object(database, "load_instruments",
                                  return_value=list(instruments)):
            return codal_sync.metrics_for(
                ["1", "2"], market_by_code={"1": self.MARKET, "2": self.MARKET},
                sector_by_code=sector_by_code)

    def test_a_bank_is_not_scored_on_its_pe(self):
        out = self._metrics(sector_by_code={"1": self.BANK, "2": self.METAL})

        bank, metal = out["1"], out["2"]

        # Shown for both: the multiple is a fact, its verdict is the error.
        self.assertAlmostEqual(bank["ratios"]["pe"], 3.0)
        self.assertAlmostEqual(metal["ratios"]["pe"], 3.0)

        # Nor on its profitability or growth: an investment company's,
        # bank's or insurer's are disposals and provisioning (2026-11-11).
        without_pe = fundamental_scoring.score(
            dict(bank["ratios"], pe=None, earnings_yield=None),
            skip=("Profitability", "Growth"))

        self.assertEqual(bank["score"], without_pe["score"])
        self.assertIsNone(bank["breakdown"]["Profitability"])
        self.assertIsNone(bank["breakdown"]["Growth"])
        self.assertNotEqual(bank["breakdown"]["Valuation"],
                            metal["breakdown"]["Valuation"])

        self.assertIn("Valuation: P/E 3.0 — very cheap", metal["notes"])
        self.assertFalse([n for n in bank["notes"]
                          if n.startswith("Valuation: P/E")], bank["notes"])
        self.assertTrue([n for n in bank["notes"] if "حساب نشد" in n])

    def test_sectors_are_read_when_the_caller_passes_none(self):
        out = self._metrics(instruments=[
            {"ins_code": "1", "sector_name": self.BANK + " "},
            {"ins_code": "2", "sector_name": self.METAL},
        ])

        self.assertTrue([n for n in out["1"]["notes"] if "حساب نشد" in n])
        self.assertFalse([n for n in out["2"]["notes"] if "حساب نشد" in n])


# ==============================
# GROWTH OFF A NEGLIGIBLE BASE
# ==============================

class NegligibleGrowthBaseTest(unittest.TestCase):

    def test_growth_off_a_negligible_base_is_not_comparable(self):
        # فناوا: EPS 1 rial -> 1,753; profit 10.2 bn -> 15,426 bn.
        r = financials.ratios(
            {"eps": 1_753.0, "net_profit": 15_425_707e6, "revenue": 1_000e9},
            {"eps": 1.0, "net_profit": 10_224e6, "revenue": 1_000e9},
            months=6)

        self.assertIsNone(r["eps_growth"])
        self.assertIsNone(r["profit_growth"])
        self.assertEqual(r["revenue_growth"], 0.0)

    def test_ordinary_growth_is_returned_exactly_as_before(self):
        for current, previous, expected in ((150.0, 100.0, 50.0),
                                            (451.53, 100.0, 351.53),
                                            (2_000.0, 100.0, 1_900.0),
                                            (40.0, 100.0, -60.0),
                                            (0.0, 100.0, -100.0)):
            self.assertEqual(financials._growth(current, previous), expected)

    def test_a_swing_into_a_loss_keeps_its_sign(self):
        # Scored "contracting" either way; None would lift it for the loss.
        self.assertEqual(financials._growth(-500.0, 10.0), -5_100.0)

    def test_a_stored_figure_is_read_through_the_same_rule(self):
        for value, expected in ((175_200.0, None), (150_777.42, None),
                                (3_996.19, None), (1_900.0, 1_900.0),
                                (351.53, 351.53), (-5_100.0, -5_100.0),
                                (None, None), ("x", None)):
            self.assertEqual(financials.comparable_growth(value), expected,
                             value)

    def test_the_card_does_not_carry_a_stored_near_zero_base_growth(self):
        """Filings parsed before the rule still hold the old figures."""

        latest = {"1": stored(ratios={
            "revenue_growth": 3_996.19, "profit_growth": 150_777.42,
            "eps_growth": 175_200.0, "equity_growth": 45.0})}

        with mock.patch.object(database, "load_codal_latest",
                               return_value=latest):
            out = codal_sync.metrics_for(
                ["1"], market_by_code={"1": {"close": 600.0,
                                             "total_shares": 1e9}},
                sector_by_code={})

        ratios = out["1"]["ratios"]

        for key in ("revenue_growth", "profit_growth", "eps_growth"):
            self.assertIsNone(ratios[key], key)

        self.assertEqual(ratios["equity_growth"], 45.0)
        self.assertIsNone(out["1"]["breakdown"]["Growth"])


if __name__ == "__main__":
    unittest.main()


class SharedGrowthRuleTests(unittest.TestCase):
    """The near-zero-base rule where every scorer reads growth (2026-11-11)."""

    def test_the_shared_scorer_drops_growth_off_a_negligible_base(self):
        import fundamental_scoring

        fraction, notes = fundamental_scoring._score_growth(
            {"revenue_growth": 30.0, "profit_growth": 150777.0,
             "eps_growth": 175200.0}, inflation=35.0)
        joined = " ".join(notes)
        self.assertIn("profit growth not comparable", joined)
        self.assertIn("EPS growth not comparable", joined)
        self.assertNotIn("+175200%", joined)
        # Scored on revenue alone: 30% is "growing, but shrinking in real terms".
        alone, _ = fundamental_scoring._score_growth(
            {"revenue_growth": 30.0}, inflation=35.0)
        self.assertEqual(fraction, alone)

    def test_ordinary_growth_is_scored_as_before(self):
        import fundamental_scoring

        fraction, notes = fundamental_scoring._score_growth(
            {"revenue_growth": 60.0, "profit_growth": 90.0, "eps_growth": 1800.0},
            inflation=35.0)
        self.assertTrue(all("not comparable" not in n for n in notes))
        self.assertIsNotNone(fraction)


class ZeroFiledEpsTest(unittest.TestCase):
    """A filed EPS of exactly 0 beside a non-zero profit is a template row
    left at zero (385814: net profit 14,173 bn, every basic-EPS row 0); it
    must not block the EPS and P/E derived from profit and shares."""

    def test_ratios_derive_eps_past_a_zero_row(self):
        statement = dict(SHASAFAHA, eps=0.0)
        r = financials.ratios(statement, market=SHASAFAHA_MARKET, months=6)

        self.assertTrue(r.get("eps_derived"))
        self.assertGreater(r["eps"], 0)
        self.assertIsNotNone(r["pe"])

    def test_a_zero_eps_with_no_profit_stays_zero(self):
        statement = dict(SHASAFAHA, eps=0.0, net_profit=0.0)
        r = financials.ratios(statement, market=SHASAFAHA_MARKET, months=6)

        self.assertEqual(r["eps"], 0.0)

    def test_extract_leaves_eps_blank_on_a_zero_row(self):
        import codal
        items = financials.extract(codal.split_sheets(
            "<h2>صورت سود و زیان</h2><table>"
            "<tr><td>سود (زیان) خالص</td><td>14,173,000</td><td>9,000,000</td></tr>"
            "<tr><td>سود (زیان) پایه هر سهم</td><td>0</td><td>0</td></tr>"
            "</table>"))

        self.assertIsNotNone(items)
        self.assertNotEqual(items.get("net_profit") or 0, 0)
        self.assertIsNone(items.get("eps"))


# ==============================
# THE SECOND AUDIT (2026-11-11): TTM, ONE-OFFS, EMPTY PERIODS, THE EXCHANGE
# ==============================

def period_row(end, months, revenue, net, operating=None, consolidated=False):
    """A load_codal_periods() row."""
    items = {"revenue": revenue, "net_profit": net, "net_profit_owners": net,
             "operating_profit": net if operating is None else operating,
             "capital": 1_000e9, "equity": 400e9, "equity_owners": 400e9,
             "total_assets": 1_000e9, "total_liabilities": 600e9}
    return {"period_end": end, "period_months": months, "audited": True,
            "consolidated": consolidated, "items": items, "ratios": {},
            "published_at": end}


class CardSecondAuditTest(unittest.TestCase):

    MARKET = {"close": 1_000.0, "total_shares": 1e9, "pe": 10.0}
    # A half-year of 60bn against last year's 100bn with 40bn in its first
    # half: TTM 120bn, where the half annualised reads 120bn as well -- so
    # the profits below differ by half to tell the two apart.
    HISTORY = [period_row("1405/06/31", 6, 300e9, 70e9),
               period_row("1404/12/29", 12, 400e9, 100e9),
               period_row("1404/06/31", 6, 200e9, 40e9)]

    def _metrics(self, periods, latest=None, market=None):
        latest = latest or {"1": dict(periods[0], title="t", updated_at=None)}
        with mock.patch.object(database, "load_codal_latest",
                               return_value=latest), \
                mock.patch.object(database, "load_codal_periods",
                                  return_value={"1": periods}):
            return codal_sync.metrics_for(
                ["1"], market_by_code={"1": market or self.MARKET},
                sector_by_code={"1": "فلزات اساسي"})["1"]

    def test_pe_is_on_the_trailing_year_not_the_half_doubled(self):
        out = self._metrics(self.HISTORY)
        # TTM 70 + 100 - 40 = 130bn; market cap 1,000bn.
        self.assertAlmostEqual(out["ratios"]["pe"], 7.69, places=2)
        self.assertAlmostEqual(out["ratios"]["pe_annualised"], 7.14, places=2)
        self.assertEqual(out["ratios"]["pe_basis"], "ttm")

    def test_a_profit_far_above_operating_profit_is_not_scored_on_pe(self):
        rows = [period_row("1405/06/31", 6, 300e9, 70e9, operating=10e9)] + \
            self.HISTORY[1:]
        out = self._metrics(rows)
        self.assertTrue(any("سود عملیاتی" in w for w in out["warnings"]))
        self.assertFalse([n for n in out["notes"]
                          if n.startswith("Valuation: P/E 7")], out["notes"])

    def test_a_gap_to_the_exchanges_pe_is_shown(self):
        out = self._metrics(self.HISTORY, market=dict(self.MARKET, pe=20.0))
        self.assertTrue(any("اعلامی بورس" in w for w in out["warnings"]))
        self.assertFalse(any("اعلامی بورس" in w for w in
                             self._metrics(self.HISTORY)["warnings"]))

    def test_a_newest_period_without_an_income_statement_is_passed_over(self):
        empty = period_row("1405/09/30", 9, None, None)
        empty["items"]["operating_profit"] = None
        out = self._metrics([empty] + self.HISTORY)
        self.assertEqual(out["period_end"], "1405/06/31")
        self.assertTrue(any("1405/09/30" in w for w in out["warnings"]))

    def test_without_a_prior_year_the_annualised_pe_stays_and_says_so(self):
        out = self._metrics(self.HISTORY[:1])
        self.assertEqual(out["ratios"]["pe_basis"], "annualised")
        self.assertTrue(any("سالانه‌شده" in w for w in out["warnings"]))
