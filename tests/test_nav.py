"""NAV from portfolio filings.

The failure this guards against is quiet: NAV is a large number that looks
reasonable at almost any value, so a parser reading the wrong column, or the
opening balance instead of the closing one, produces a P/NAV nobody can spot
as wrong by eye.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nav


def portfolio_sheet(rows, total_cost, total_market, total_gain,
                    with_market=True):
    """A listed-portfolio table shaped like Codal's, with a جمع row."""

    columns = pd.MultiIndex.from_tuples([
        ("نام شرکت", "نام شرکت"),
        ("سرمایه (میلیون ریال)", "سرمایه (میلیون ریال)"),
        ("ابتدای دوره", "بهای تمام شده"),
        ("ابتدای دوره", "ارزش بازار"),
        ("انتهای دوره", "بهای تمام شده"),
        ("انتهای دوره", "ارزش بازار"),
        ("انتهای دوره", "افزایش (کاهش)"),
    ])

    data = []

    for name, cost, market in rows:
        data.append([name, "۱,۰۰۰", "۹۹۹", "۹۹۹", cost, market, "۰"])

    # A per-board subtotal, which must NOT be mistaken for the grand total.
    data.append(["جمع سهام درج شده", None, "۱", "۱", "۱", "۱", "۱"])
    data.append(["جمع", None, "۹۹۹", "۹۹۹",
                 total_cost, total_market if with_market else None, total_gain])

    return pd.DataFrame(data, columns=columns)


def unlisted_sheet(total_cost):
    columns = pd.MultiIndex.from_tuples([
        ("نام شرکت", "نام شرکت"),
        ("ابتدای دوره", "بهای تمام شده"),
        ("انتهای دوره", "بهای تمام شده"),
    ])

    return pd.DataFrame(
        [["شرکت خصوصی", "۱۰۰", "۱۰۰"], ["جمع", "۱۰۰", total_cost]],
        columns=columns,
    )


MILLION = config.CODAL_AMOUNT_SCALE


class TestExtractPortfolio(unittest.TestCase):

    def _sheets(self, **kwargs):
        return {
            "صورت وضعیت پرتفوی سهام قابل معامله در بازار سرمایه": [
                portfolio_sheet(
                    [("پالايش نفت", "۳۴,۸۵۵", "۸۴,۳۶۵")],
                    kwargs.get("cost", "۲,۶۳۲,۵۶۱"),
                    kwargs.get("market", "۶,۲۲۹,۳۸۳"),
                    kwargs.get("gain", "۳,۵۹۶,۸۲۲"),
                    with_market=kwargs.get("with_market", True),
                )
            ],
            "صورت وضعیت پرتفوی سهام سایر شرکتها": [
                unlisted_sheet(kwargs.get("unlisted", "۵۰۰"))
            ],
        }

    def test_totals_are_read_from_the_grand_total_row(self):
        result = nav.extract_portfolio(self._sheets())

        self.assertAlmostEqual(result["listed_cost"], 2_632_561 * MILLION)
        self.assertAlmostEqual(result["listed_market"], 6_229_383 * MILLION)
        self.assertAlmostEqual(result["listed_unrealised"], 3_596_822 * MILLION)

    def test_a_board_subtotal_is_not_mistaken_for_the_total(self):
        """The sheet carries جمع سهام درج شده above the real جمع."""

        result = nav.extract_portfolio(self._sheets())

        # The subtotal row holds 1s; reading it would give a millionth of this.
        self.assertGreater(result["listed_market"], 1_000_000 * MILLION)

    def test_the_closing_column_is_used_not_the_opening_one(self):
        sheets = self._sheets(cost="۱۰۰", market="۲۰۰", gain="۱۰۰")

        result = nav.extract_portfolio(sheets)

        # Opening columns hold ۹۹۹ in the fixture.
        self.assertAlmostEqual(result["listed_cost"], 100 * MILLION)
        self.assertAlmostEqual(result["listed_market"], 200 * MILLION)

    def test_unlisted_holdings_are_reported_separately(self):
        result = nav.extract_portfolio(self._sheets(unlisted="۵۰۰"))

        self.assertAlmostEqual(result["unlisted_cost"], 500 * MILLION)

    def test_unrealised_is_derived_when_the_filing_omits_it(self):
        sheets = self._sheets(cost="۱۰۰", market="۲۵۰", gain=None)

        result = nav.extract_portfolio(sheets)

        self.assertAlmostEqual(result["listed_unrealised"], 150 * MILLION)

    def test_a_filing_with_no_portfolio_returns_none(self):
        """A manufacturer files a monthly report under the same code."""

        sheets = {"درامد حاصل از فروش": [pd.DataFrame({"a": [1]})]}

        self.assertIsNone(nav.extract_portfolio(sheets))

    def test_the_alternate_persian_spelling_is_matched(self):
        sheets = {
            "صورت وضعیت پورتفوی سهام قابل معامله در بازار سرمایه": [
                portfolio_sheet([("x", "۱", "۲")], "۱۰۰", "۳۰۰", "۲۰۰")
            ]
        }

        result = nav.extract_portfolio(sheets)

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["listed_market"], 300 * MILLION)


class TestNetAssetValue(unittest.TestCase):

    def test_nav_adds_the_unrealised_gain_to_book_equity(self):
        portfolio = {"listed_unrealised": 3_596_822 * MILLION,
                     "unlisted_cost": 0}

        result = nav.net_asset_value(portfolio, equity=6_181_000 * MILLION)

        self.assertAlmostEqual(result["nav"], (6_181_000 + 3_596_822) * MILLION)
        self.assertTrue(result["complete"])

    def test_unlisted_holdings_mark_the_nav_incomplete(self):
        portfolio = {"listed_unrealised": 100.0, "unlisted_cost": 50.0}

        result = nav.net_asset_value(portfolio, equity=1000.0)

        self.assertFalse(result["complete"])
        self.assertIn("understates", result["basis"])

    def test_a_portfolio_that_lost_money_lowers_nav(self):
        portfolio = {"listed_unrealised": -16_800.0, "unlisted_cost": 0}

        result = nav.net_asset_value(portfolio, equity=3_512_800.0)

        self.assertLess(result["nav"], 3_512_800.0)

    def test_missing_equity_yields_no_nav(self):
        result = nav.net_asset_value({"listed_unrealised": 1.0}, equity=None)

        self.assertIsNone(result["nav"])

    def test_a_filing_without_market_value_yields_no_nav(self):
        result = nav.net_asset_value({"listed_unrealised": None}, equity=1000.0)

        self.assertIsNone(result["nav"])
        self.assertIn("no market value", result["basis"])


class TestPriceToNav(unittest.TestCase):

    def test_ratio_below_one_means_priced_under_assets(self):
        ratio = nav.price_to_nav(500.0, {"nav": 1000.0})

        self.assertAlmostEqual(ratio, 0.5)

    def test_a_negative_nav_yields_no_ratio(self):
        self.assertIsNone(nav.price_to_nav(500.0, {"nav": -100.0}))

    def test_a_missing_market_cap_yields_no_ratio(self):
        self.assertIsNone(nav.price_to_nav(None, {"nav": 1000.0}))


class TestNavScoring(unittest.TestCase):
    """The screen must reward a discount and refuse to reward a guess."""

    def _metrics(self, ratio, complete=True):
        return {
            "price_to_nav": ratio,
            "nav_complete": complete,
            "earnings_multiple_meaningful": False,
        }

    def test_a_deep_discount_scores_highest(self):
        import screening

        deep, _ = screening._score_nav(self._metrics(0.5))
        around, _ = screening._score_nav(self._metrics(1.0))
        premium, _ = screening._score_nav(self._metrics(2.0))

        self.assertGreater(deep, around)
        self.assertGreater(around, premium)

    def test_an_understated_nav_cannot_earn_the_top_score(self):
        import screening

        complete, _ = screening._score_nav(self._metrics(0.5, complete=True))
        partial, _ = screening._score_nav(self._metrics(0.5, complete=False))

        self.assertEqual(complete, 1.0)
        self.assertLess(partial, complete)

    def test_the_valuation_component_uses_nav_for_these_sectors(self):
        import screening

        metrics = self._metrics(0.6)
        metrics["pe"] = 2.0          # would read as "very cheap" if used

        fraction, note = screening._score_valuation(metrics)

        self.assertIsNotNone(fraction)
        self.assertIn("P/NAV", note)

    def test_without_a_portfolio_filing_the_component_stays_withheld(self):
        import screening

        metrics = {"earnings_multiple_meaningful": False,
                   "price_to_nav": None, "pe": 2.0}

        fraction, note = screening._score_valuation(metrics)

        self.assertIsNone(fraction)
        self.assertIn("withheld", note)


if __name__ == "__main__":
    unittest.main()
