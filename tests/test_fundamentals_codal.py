"""Codal parsing, ratios, scoring and the combined engine.

Every test here guards a failure that produces a plausible number rather than
an error -- the only kind that survives to reach a user. The unit scale being
a million out, a quarterly EPS valued as if it were annual, a total-assets
lookup that silently returns current assets, a bank whose bottom line is
spelled without a space: each of those was a real defect during this build,
and each looked completely fine in the output.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal
import codal_sync
import config
import financials
import fundamental_scoring
import scoring


# ==============================
# NUMBER PARSING
# ==============================

class TestParseNumber(unittest.TestCase):

    def test_persian_digits_and_separators(self):
        self.assertEqual(codal.parse_number("۵,۲۴۳,۳۰۷,۸۸۲"), 5243307882.0)
        self.assertEqual(codal.parse_number("۱۲۳"), 123.0)

    def test_parentheses_are_negative(self):
        """Codal marks costs with parentheses, not a minus sign.

        A parser that only strips commas turns every cost line positive, which
        inflates profit instead of failing -- the statement still balances to
        something, just not to the truth.
        """

        self.assertEqual(codal.parse_number("(۳,۴۹۳,۴۱۴,۵۲۹)"), -3493414529.0)
        self.assertEqual(codal.parse_number("(۱۲۳)"), -123.0)

    def test_real_minus_sign_also_works(self):
        # The cash flow sheet uses real minus signs where the income statement
        # uses parentheses. Both appear in the same filing.
        self.assertEqual(codal.parse_number("-166,873,157"), -166873157.0)

    def test_blanks_and_dashes_are_none(self):
        for blank in (None, "", "   ", "-", "--", "—", "nan", "abc"):
            self.assertIsNone(codal.parse_number(blank), blank)

    def test_passes_through_numbers(self):
        self.assertEqual(codal.parse_number(42), 42.0)
        self.assertEqual(codal.parse_number(-1.5), -1.5)
        self.assertIsNone(codal.parse_number(float("nan")))


# ==============================
# ROW LOOKUP
# ==============================

class TestRowLookup(unittest.TestCase):

    def test_exact_match_beats_substring(self):
        """جمع دارایی‌ها is a substring of جمع دارایی‌های جاری.

        A purely substring lookup for total assets returns current assets --
        a smaller number that is entirely plausible and completely wrong, and
        that nothing downstream could detect.
        """

        rows = [
            ("جمع دارایی‌های جاری", [100.0]),
            ("جمع دارایی‌ها", [250.0]),
        ]

        _label, values = codal.find_row(rows, ["جمع دارایی‌ها"])

        self.assertEqual(values[0], 250.0)

    def test_matching_ignores_spaces(self):
        """A bank writes سود(زیان)خالص; a manufacturer writes سود(زیان) خالص.

        Neither equality nor substring relates those two strings, so an alias
        written one way found nothing in the other template -- which is how a
        whole industry came back with no revenue and no profit.
        """

        rows = [("سود(زیان)خالص", [1535376428.0])]

        _label, values = codal.find_row(rows, ["سود(زیان) خالص"])

        self.assertEqual(values[0], 1535376428.0)

    def test_exclude_rejects_lookalikes(self):
        rows = [
            ("سود(زیان) خالص عملیات متوقف شده", [0.0]),
            ("سود(زیان) خالص", [999.0]),
        ]

        _label, values = codal.find_row(
            rows, ["سود(زیان) خالص"], exclude=["متوقف شده"]
        )

        self.assertEqual(values[0], 999.0)

    def test_row_without_values_is_skipped(self):
        # Section headers carry a label and no numbers.
        rows = [("درآمدهای عملیاتی", []), ("جمع درآمدهای عملیاتی", [5.0])]

        _label, values = codal.find_row(rows, ["درآمدهای عملیاتی"])

        self.assertEqual(values[0], 5.0)

    def test_missing_returns_empty(self):
        label, values = codal.find_row([("x", [1.0])], ["y"])
        self.assertIsNone(label)
        self.assertEqual(values, [])


class TestLetterCodes(unittest.TestCase):

    def test_letter_code_comparison_survives_digit_folding(self):
        """persian.normalize folds ۱۰ to 10, so raw comparison finds nothing.

        The stored code is "ن-10" while the code as written by hand -- or
        copied off codal.ir, which shows Persian digits -- is "ن-۱۰". Comparing
        them raw returns no filings at all, silently.
        """

        stored = {"letter_code": "ن-10"}

        self.assertTrue(codal.is_letter(stored, "ن-۱۰"))
        self.assertTrue(codal.is_letter(stored, "ن-10"))
        self.assertFalse(codal.is_letter(stored, "ن-۳۰"))


# ==============================
# PERIOD HANDLING
# ==============================

class TestPeriod(unittest.TestCase):

    def test_interim_and_annual_titles(self):
        self.assertEqual(financials.period_months(
            "اطلاعات و صورت‌های مالی میاندوره‌ای دوره ۳ ماهه منتهی به ۱۴۰۵/۰۳/۳۱"
        ), 3)
        self.assertEqual(financials.period_months(
            "صورت‌های مالی تلفیقی سال مالی منتهی به ۱۴۰۴/۱۲/۲۹"
        ), 12)
        self.assertIsNone(financials.period_months("تصمیمات مجمع عمومی"))

    def test_period_end_is_kept_as_filed(self):
        self.assertEqual(financials.period_end(
            "صورت‌های مالی سال مالی منتهی به ۱۴۰۴/۱۲/۲۹"
        ), "1404/12/29")

    def test_audited_flag_is_tri_state(self):
        self.assertIs(financials.is_audited("... (حسابرسی شده)"), True)
        self.assertIs(financials.is_audited("... (حسابرسی نشده)"), False)
        self.assertIsNone(financials.is_audited("گزارش فعالیت ماهانه"))

    def test_annualise_scales_only_short_periods(self):
        self.assertEqual(financials.annualise(100, 12), 100)
        self.assertEqual(financials.annualise(100, 3), 400)
        self.assertEqual(financials.annualise(100, 6), 200)
        self.assertEqual(financials.annualise(100, None), 100)


# ==============================
# RATIOS
# ==============================

def statement(**overrides):
    """A canonical statement in rials, already unit-converted."""

    base = {
        "revenue": 1000e9,
        "cogs": -600e9,
        "gross_profit": 400e9,
        "operating_profit": 300e9,
        "net_profit": 200e9,
        "net_profit_owners": 200e9,
        "eps": 500.0,
        "capital": 400e9,
        "total_assets": 2000e9,
        "current_assets": 800e9,
        "total_liabilities": 800e9,
        "current_liabilities": 400e9,
        "equity": 1200e9,
        "equity_owners": 1200e9,
        "operating_cash_flow": 220e9,
    }

    base.update(overrides)

    return base


class TestRatios(unittest.TestCase):

    def test_core_ratios(self):
        r = financials.ratios(statement(), months=12)

        self.assertAlmostEqual(r["net_margin"], 20.0)
        self.assertAlmostEqual(r["gross_margin"], 40.0)
        self.assertAlmostEqual(r["roe"], 16.67, places=1)
        self.assertAlmostEqual(r["roa"], 10.0)
        self.assertAlmostEqual(r["debt_ratio"], 40.0)
        self.assertAlmostEqual(r["current_ratio"], 2.0)
        self.assertAlmostEqual(r["earnings_quality"], 1.1)

    def test_quarterly_pe_is_annualised(self):
        """The bug this exists for: شپنا came back at P/E 18.7 against 4.7.

        Price is a claim on a year of earnings. Dividing it by a quarter's EPS
        overstates the multiple fourfold -- expensive-looking rather than
        obviously broken, so nothing downstream would flag it.
        """

        # The filing's own share count (capital 400e9 at par), so the EPS and
        # the price are per the same share and only the period differs. A
        # count that has grown since is test_fundamental_valuation's case.
        market = {"close": 10_000.0, "total_shares": 400e6}

        annual = financials.ratios(statement(), market=market, months=12)
        quarter = financials.ratios(statement(), market=market, months=3)

        self.assertAlmostEqual(annual["pe"], 20.0)
        self.assertAlmostEqual(quarter["pe"], 5.0)
        self.assertTrue(quarter["annualised"])
        self.assertFalse(annual["annualised"])

    def test_quarterly_roe_is_annualised(self):
        quarter = financials.ratios(statement(), months=3)
        annual = financials.ratios(statement(), months=12)

        self.assertAlmostEqual(quarter["roe"], annual["roe"] * 4, places=1)

    def test_growth_off_a_negative_base_is_not_reported(self):
        """A loss narrowing is not -150% growth.

        The usual formula turns a turnaround into a large negative number and
        a deepening loss into a positive one, so both sort exactly backwards.
        None says "not comparable", which does not sort at all.
        """

        current = statement(net_profit=-500e9)
        previous = statement(net_profit=-1000e9)

        r = financials.ratios(current, previous, months=12)

        self.assertIsNone(r["profit_growth"])

    def test_growth_is_normal_off_a_positive_base(self):
        r = financials.ratios(
            statement(revenue=1500e9), statement(revenue=1000e9), months=12
        )
        self.assertAlmostEqual(r["revenue_growth"], 50.0)

    def test_zero_denominators_never_produce_inf(self):
        r = financials.ratios(
            statement(revenue=0, equity=0, equity_owners=0, total_assets=0,
                      current_liabilities=0),
            months=12,
        )

        for key in ("net_margin", "roe", "roa", "debt_ratio", "current_ratio"):
            self.assertIsNone(r[key], key)

    def test_missing_statement_yields_none_not_zero(self):
        r = financials.ratios({}, months=12)

        for key in ("roe", "roa", "net_margin", "pe", "pb", "ps"):
            self.assertIsNone(r[key], key)

        self.assertFalse(r["has_income"])
        self.assertFalse(r["has_balance"])

    def test_loss_making_has_no_pe(self):
        r = financials.ratios(
            statement(eps=-100.0, net_profit=-200e9),
            market={"close": 5000.0, "total_shares": 1e9},
            months=12,
        )
        self.assertIsNone(r["pe"])

    def test_earnings_quality_needs_a_profit(self):
        r = financials.ratios(statement(net_profit=-100e9), months=12)
        self.assertIsNone(r["earnings_quality"])


class TestDerivation(unittest.TestCase):

    def test_total_liabilities_derived_from_the_identity(self):
        items = {"total_assets": 1000.0, "equity": 400.0}

        financials._derive(items)

        self.assertEqual(items["total_liabilities"], 600.0)
        self.assertIn("total_liabilities", items["derived"])

    def test_nothing_is_invented_without_its_parts(self):
        items = {"total_assets": 1000.0}

        financials._derive(items)

        self.assertIsNone(items.get("total_liabilities"))
        self.assertIsNone(items.get("equity"))
        self.assertEqual(items["derived"], [])

    def test_shares_from_capital_at_par(self):
        # Every TSE share has a 1000-rial par value.
        self.assertEqual(
            financials.shares_outstanding({"capital": 1_935_000_000 * 1e6}),
            1.935e12
        )
        self.assertIsNone(financials.shares_outstanding({"capital": 0}))
        self.assertIsNone(financials.shares_outstanding({}))


# ==============================
# FUNDAMENTAL SCORE
# ==============================

class TestFundamentalScore(unittest.TestCase):

    def test_strong_company_scores_well(self):
        r = financials.ratios(
            statement(net_profit=400e9, net_profit_owners=400e9,
                      operating_cash_flow=460e9),
            previous=statement(revenue=500e9, net_profit=150e9),
            market={"close": 2000.0, "total_shares": 1e9},
            months=12,
        )

        result = fundamental_scoring.score(r)

        self.assertEqual(result["verdict"], fundamental_scoring.SCORED)
        self.assertGreaterEqual(result["score"], 65)

    def test_missing_pillars_redistribute_rather_than_score_zero(self):
        """A bank has no gross margin; an investment company has no cost line.

        Scoring an unavailable pillar as zero would rank an entire industry
        below a loss-making manufacturer for having a different filing
        template.
        """

        # Profitability, health and valuation present; growth, cash flow and
        # earnings quality absent -- the shape a bank's filing actually gives.
        partial = {"roe": 30.0, "roa": 12.0, "net_margin": 20.0,
                   "debt_ratio": 45.0, "current_ratio": 1.5,
                   "pe": 5.0, "ps": 1.5, "pb": 1.2}

        result = fundamental_scoring.score(partial)

        self.assertEqual(result["verdict"], fundamental_scoring.SCORED)
        self.assertLess(result["weight_covered"], 1.0)
        self.assertLessEqual(result["score"], 100)
        self.assertGreaterEqual(result["score"], 0)

        # The absent pillars are named, not silently scored.
        self.assertIsNone(result["breakdown"]["Growth"])
        self.assertIsNone(result["breakdown"]["Cash flow"])
        self.assertIsNotNone(result["breakdown"]["Profitability"])

    def test_too_little_data_is_insufficient_not_zero(self):
        result = fundamental_scoring.score({"roe": 30.0})

        self.assertEqual(result["verdict"], fundamental_scoring.INSUFFICIENT)
        self.assertIsNone(result["score"])
        self.assertIsNone(result["status"])

    def test_empty_input(self):
        result = fundamental_scoring.score({})
        self.assertEqual(result["verdict"], fundamental_scoring.INSUFFICIENT)
        self.assertIsNone(result["score"])

    def test_growth_bands_sit_on_inflation_not_zero(self):
        """Nominal growth under inflation is a real-terms contraction."""

        inflation = config.INFLATION_RATE

        def score(growth):
            return fundamental_scoring._score_growth({
                "revenue_growth": growth, "profit_growth": growth,
                "eps_growth": growth,
            })[0]

        below, above = score(inflation - 15), score(inflation + 5)

        self.assertLess(below, above)
        self.assertLess(below, 0.6)

    def test_backtests_keep_the_bands_they_were_registered_with(self):
        # 40% inflation, excellent from 80%: today's rate must not re-score
        # history (config.BACKTEST_INFLATION_RATE).
        def score(growth):
            return fundamental_scoring._score_growth(
                {"revenue_growth": growth, "profit_growth": growth,
                 "eps_growth": growth},
                inflation=config.BACKTEST_INFLATION_RATE)[0]

        self.assertAlmostEqual(score(39), 0.45)
        self.assertAlmostEqual(score(40), 0.8)
        self.assertAlmostEqual(score(79), 0.8)
        self.assertAlmostEqual(score(80), 1.0)

    def test_roe_below_the_policy_rate_is_not_rewarded(self):
        weak, _ = fundamental_scoring._score_profitability({"roe": 15.0})
        fair, _ = fundamental_scoring._score_profitability({"roe": 26.0})

        self.assertLess(weak, fair)

    def test_score_is_bounded(self):
        for extreme in ({"roe": 1e6, "roa": 1e6, "net_margin": 1e6,
                         "debt_ratio": 0, "current_ratio": 1e6,
                         "earnings_quality": 1e6},
                        {"roe": -1e6, "roa": -1e6, "net_margin": -1e6,
                         "debt_ratio": 1e6, "current_ratio": -1e6,
                         "earnings_quality": -1e6}):
            result = fundamental_scoring.score(extreme)
            if result["score"] is not None:
                self.assertGreaterEqual(result["score"], 0)
                self.assertLessEqual(result["score"], 100)


# ==============================
# COMBINED ENGINE
# ==============================

class TestCombinedEngine(unittest.TestCase):

    def test_all_four_components(self):
        result = scoring.combine(technical=80, fundamental=60, risk=70, flow=50)

        self.assertTrue(result["scored"])
        self.assertEqual(result["coverage"], 1.0)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)

    def test_missing_component_renormalises_rather_than_scoring_zero(self):
        """A company with no filings must not be pushed down the ranking.

        "We have not downloaded its statements yet" is a fact about us, not
        about the company, and blending a zero for it would rank it below
        companies the model actively dislikes.
        """

        with_all = scoring.combine(technical=80, fundamental=80, risk=80, flow=80)
        without_fundamental = scoring.combine(
            technical=80, fundamental=None, risk=80, flow=80
        )

        self.assertEqual(with_all["score"], 80)
        self.assertEqual(without_fundamental["score"], 80)
        self.assertLess(without_fundamental["coverage"], 1.0)

    def test_below_minimum_coverage_is_unscored(self):
        result = scoring.combine(technical=None, fundamental=None,
                                 risk=None, flow=90)

        self.assertFalse(result["scored"])
        self.assertIsNone(result["score"])
        self.assertIsNotNone(result["reason"])

    def test_weights_are_configurable(self):
        technical_heavy = scoring.combine(
            technical=100, fundamental=0, risk=None, flow=None,
            weights={"technical": 90, "fundamental": 10, "risk": 0, "flow": 0},
        )
        fundamental_heavy = scoring.combine(
            technical=100, fundamental=0, risk=None, flow=None,
            weights={"technical": 10, "fundamental": 90, "risk": 0, "flow": 0},
        )

        self.assertGreater(technical_heavy["score"], fundamental_heavy["score"])

    def test_divergence_names_both_directions(self):
        self.assertEqual(
            scoring.divergence(technical=40, fundamental=90),
            scoring.VALUE_TRAP
        )
        self.assertEqual(
            scoring.divergence(technical=90, fundamental=40),
            scoring.MOMENTUM_TRAP
        )
        self.assertEqual(
            scoring.divergence(technical=70, fundamental=72),
            scoring.ALIGNED
        )
        self.assertIsNone(scoring.divergence(technical=70, fundamental=None))

    def test_risk_score_is_oriented_so_higher_is_safer(self):
        """A component where higher means worse would invert the blend.

        The result would still be a plausible ranked list, which is the worst
        kind of wrong: nothing about the output looks incorrect.
        """

        calm = scoring.risk_score({"volatility_60": 20.0,
                                   "avg_value_20": 1e11})
        wild = scoring.risk_score({"volatility_60": 95.0,
                                   "avg_value_20": 1e8})

        self.assertGreater(calm["score"], wild["score"])

    def test_risk_score_without_inputs(self):
        self.assertIsNone(scoring.risk_score({})["score"])
        self.assertIsNone(scoring.risk_score(None)["score"])

    def test_evaluate_carries_every_score(self):
        result = scoring.evaluate(technical=75, fundamental=85,
                                  risk=60, flow=55)

        self.assertEqual(result["technical_score"], 75)
        self.assertEqual(result["fundamental_score"], 85)
        self.assertEqual(result["risk_score"], 60)
        self.assertEqual(result["flow_score"], 55)
        self.assertIsNotNone(result["status"])


# ==============================
# INCREMENTAL SYNC
# ==============================

class TestSyncPacing(unittest.TestCase):

    def test_never_checked_is_due(self):
        self.assertTrue(codal_sync.due_for_check(None))
        self.assertTrue(codal_sync.due_for_check({}))

    def test_recent_success_is_not_due(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        self.assertFalse(codal_sync.due_for_check(
            {"last_checked": now, "error": None}
        ))

    def test_transient_failure_retries_far_sooner_than_a_success(self):
        """A failed check says the route was down, not that nothing changed.

        Sharing the 20-hour success interval is what made a VPN toggle look
        like a permanent outage: the route came back in seconds and the panel
        stayed empty until the next day.
        """

        from datetime import datetime, timedelta, timezone

        stamp = (datetime.now(timezone.utc)
                 - timedelta(minutes=config.CODAL_RETRY_MINUTES + 1)).isoformat()

        self.assertTrue(codal_sync.due_for_check({
            "last_checked": stamp, "error": "429",
            "error_kind": codal.KIND_TRANSIENT,
        }))
        self.assertFalse(codal_sync.due_for_check(
            {"last_checked": stamp, "error": None}
        ))

    def test_no_filings_backs_off_for_days_not_minutes(self):
        """A fund that files nothing must not eat a run's budget every 6 minutes.

        "Codal has no filings for this symbol" is a settled answer, and the
        universe is full of instruments it is permanently true for -- funds,
        rights, newly listed names. Retrying them on the transient interval
        starves the companies that would actually return data.
        """

        from datetime import datetime, timedelta, timezone

        stamp = (datetime.now(timezone.utc)
                 - timedelta(hours=2)).isoformat()

        row = {"last_checked": stamp, "error": "no filings returned",
               "error_kind": codal.KIND_EMPTY}

        self.assertFalse(codal_sync.due_for_check(row))

        stale = {"last_checked": (datetime.now(timezone.utc) - timedelta(
            hours=config.CODAL_EMPTY_BACKOFF_HOURS + 1)).isoformat(),
            "error": "no filings returned", "error_kind": codal.KIND_EMPTY}

        self.assertTrue(codal_sync.due_for_check(stale))


class TestStaleFilings(unittest.TestCase):
    """A company that stopped filing must not rank on figures from years ago."""

    def test_age_in_months_across_a_year_boundary(self):
        self.assertEqual(
            financials.period_age_months("1404/12/29", "1405/03/31"), 3
        )
        self.assertEqual(
            financials.period_age_months("1400/09/30", "1405/03/31"), 54
        )
        self.assertEqual(
            financials.period_age_months("1405/03/31", "1405/03/31"), 0
        )

    def test_unparseable_dates_give_no_age(self):
        for bad in (None, "", "not a date", "1405"):
            self.assertIsNone(financials.period_age_months(bad, "1405/03/31"))
            self.assertIsNone(financials.period_age_months("1405/03/31", bad))

    def test_the_threshold_clears_a_normal_reporting_lag(self):
        """An annual filing plus audit lag must not read as abandoned.

        A company reporting 1404/12 while the fastest filers are already on
        1405/06 is nine months behind and entirely normal.
        """

        self.assertLessEqual(
            financials.period_age_months("1404/12/29", "1405/06/31"),
            config.CODAL_STALE_MONTHS
        )
        self.assertGreater(
            financials.period_age_months("1400/09/30", "1405/06/31"),
            config.CODAL_STALE_MONTHS
        )


class TestErrorClassification(unittest.TestCase):
    """Which failures mean "try again soon" and which mean "settled"."""

    def test_rate_limit_and_server_errors_are_transient(self):
        import requests

        for status in (429, 500, 502, 503, 504):
            response = requests.Response()
            response.status_code = status
            exc = requests.HTTPError(response=response)
            self.assertEqual(codal.classify(exc), codal.KIND_TRANSIENT, status)

    def test_connection_failures_are_transient(self):
        import requests
        self.assertEqual(
            codal.classify(requests.exceptions.ConnectionError()),
            codal.KIND_TRANSIENT
        )

    def test_client_errors_are_not_transient(self):
        import requests

        response = requests.Response()
        response.status_code = 404
        exc = requests.HTTPError(response=response)

        self.assertEqual(codal.classify(exc), codal.KIND_BAD)

    def test_result_kinds(self):
        self.assertTrue(codal.CodalResult(data=[1]).ok)
        self.assertFalse(codal.CodalResult(error="x").ok)
        self.assertTrue(
            codal.CodalResult(error="x", kind=codal.KIND_TRANSIENT).transient
        )
        self.assertFalse(
            codal.CodalResult(error="x", kind=codal.KIND_EMPTY).transient
        )


class TestUndersizedFiling(unittest.TestCase):
    """A short reply is a throttle, not an unreadable filing.

    This distinction decides whether the caller retries or discards. When a
    throttled ~3 KB page was classified BAD, sync_company marked the filing
    parsed so it would never be downloaded again -- 395 perfectly readable
    statements were lost that way in one run, silently, while the counters
    reported them as "unreadable".
    """

    class _Response:
        def __init__(self, body):
            self.content = body

    # An address on Codal: any other is refused before a request is made
    # (codal.filing_url_refusal, 2026-11-23), which would settle every case
    # below as "bad" for a reason that is not the one under test.
    URL = "https://excel.codal.ir/f"

    def _client_returning(self, body):
        client = codal.CodalClient()
        client._get = lambda *a, **k: self._Response(body)
        return client

    def test_a_throttled_short_page_is_transient(self):
        body = b"<html><body>too many requests</body></html>" * 20

        self.assertLess(len(body), codal.MIN_FILING_BYTES)

        result = self._client_returning(body).fetch_sheets(self.URL)

        self.assertFalse(result.ok)
        self.assertEqual(result.kind, codal.KIND_TRANSIENT)
        self.assertTrue(result.transient)

    def test_an_empty_body_stays_transient(self):
        result = self._client_returning(b"").fetch_sheets(self.URL)

        self.assertEqual(result.kind, codal.KIND_TRANSIENT)

    def test_a_full_size_unparseable_filing_is_still_bad(self):
        """Retrying a real document that we genuinely cannot read is waste.

        The fixture carries a TABLE, because that is what makes it a real
        document. Size alone used to stand in for that and it was not enough:
        the firewall also serves large interstitial pages -- 76 KB and 201 KB
        were both observed against this database -- and calling those
        unreadable marked ~600 filings parsed and never fetched them again.
        A statement is a table dump; a block page is not.
        """

        filler = b"<tr><td>x</td></tr>" * 2_000

        body = (b"<html><body><table>" + filler + b"</table></body></html>")

        self.assertGreater(len(body), codal.MIN_FILING_BYTES)

        result = self._client_returning(body).fetch_sheets(self.URL)

        self.assertFalse(result.ok)
        self.assertEqual(result.kind, codal.KIND_BAD)

    def test_a_large_page_with_no_table_is_transient_not_bad(self):
        """The 600-filing bug, pinned.

        A body over the size guard that contains no table at all is not a
        statement Codal declined to format -- it is not a statement. Settling
        it as unreadable is permanent, and a random re-check of eight filings
        settled that way found six parsed perfectly on the next attempt.
        """

        body = b"<html>" + (b"x" * (codal.MIN_FILING_BYTES + 5_000)) + b"</html>"

        result = self._client_returning(body).fetch_sheets(self.URL)

        self.assertFalse(result.ok)
        self.assertEqual(result.kind, codal.KIND_TRANSIENT)
        self.assertTrue(result.transient)

    def test_codals_empty_export_is_settled_not_retried(self):
        """Headings with no table: Codal's own empty export, not a throttle.

        Read as a throttle, five of these at the head of the backfill queue
        stopped every campaign from 2026-09 to 2026-11-05.
        """

        body = ("<html><head><style>table { font-family:Tahoma }</style>"
                "</head><body><div><hr><h3>\nصورت سود و زیان\n</h3><hr></div>"
                "<div ng-app='codalApp'><!-- ngRepeat: sheet in "
                "ctrl.datasource.sheets --></div><div><h3>ترازنامه</h3></div>"
                "</body></html>").encode("utf-8")

        self.assertLess(len(body), codal.MIN_FILING_BYTES)

        result = self._client_returning(body).fetch_sheets(self.URL)

        self.assertEqual(result.kind, codal.KIND_BAD)
        self.assertIn("empty statement export", result.error)

    def test_the_threshold_sits_between_measured_extremes(self):
        """Calibration, not a guess: 3 KB throttle vs 177 KB smallest filing."""

        self.assertGreater(codal.MIN_FILING_BYTES, 3_035 * 2)
        self.assertLess(codal.MIN_FILING_BYTES, 177_485 // 2)


class TestCooldownEscalation(unittest.TestCase):
    """Retrying a rate limit renews it; the client has to actually wait."""

    def setUp(self):
        # Never the tree's own store. Run from C:/SIRAT-AI that is the LIVE
        # sirat_market.db: a CI run on 2026-11-11 rewrote its real
        # "codal:cooldown" row, which also erases a cooldown Codal imposed.
        import tempfile
        from unittest import mock
        import database

        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.patch = mock.patch.object(database, "DATABASE_NAME", self.store)
        self.patch.start()
        codal_sync.clear_cooldown()

    def tearDown(self):
        self.patch.stop()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.store + suffix)
            except OSError:
                pass

    def test_no_cooldown_by_default(self):
        self.assertEqual(codal_sync.cooling_off(), 0)

    def test_each_throttled_run_doubles_the_wait(self):
        first = codal_sync.start_cooldown()
        self.assertEqual(first, config.CODAL_COOLDOWN_START_MINUTES)

        second = codal_sync.start_cooldown()
        self.assertEqual(second, first * 2)

        third = codal_sync.start_cooldown()
        self.assertEqual(third, second * 2)

    def test_escalation_is_capped(self):
        for _ in range(20):
            minutes = codal_sync.start_cooldown()

        self.assertEqual(minutes, config.CODAL_COOLDOWN_MAX_MINUTES)

    def test_cooldown_blocks_a_run_and_clearing_releases_it(self):
        codal_sync.start_cooldown()
        self.assertGreater(codal_sync.cooling_off(), 0)

        result = codal_sync.run(limit=1)
        self.assertEqual(result["checked"], 0)
        self.assertGreater(result.get("cooling_off", 0), 0)

        codal_sync.clear_cooldown()
        self.assertEqual(codal_sync.cooling_off(), 0)


class TestRateLimiter(unittest.TestCase):
    """Codal throttles on sustained access; the limiter has to learn that."""

    def test_penalty_widens_and_is_capped(self):
        limiter = codal.RateLimiter(1.0, maximum=8.0)

        self.assertEqual(limiter.delay, 1.0)

        limiter.penalise()
        self.assertEqual(limiter.delay, 2.0)

        for _ in range(10):
            limiter.penalise()

        self.assertEqual(limiter.delay, 8.0)

    def test_penalty_decays_back_toward_the_base(self):
        limiter = codal.RateLimiter(1.0, maximum=8.0)

        limiter.penalise()
        limiter.penalise()

        for _ in range(50):
            limiter.relax()

        self.assertEqual(limiter.delay, 1.0)

    def test_limiter_is_shared_process_wide(self):
        """A per-client limiter forgets the throttle the moment a run ends."""

        self.assertIs(codal.shared_limiter(), codal.shared_limiter())

    def test_priority_puts_held_and_screened_names_first(self):
        held = {"ins_code": "1"}
        screened = {"ins_code": "2"}
        other = {"ins_code": "3"}

        order = sorted(
            [other, screened, held],
            key=lambda r: codal_sync.priority(r, {"1"}, {"2"})
        )

        self.assertEqual([r["ins_code"] for r in order], ["1", "2", "3"])


class TestSheetSelection(unittest.TestCase):

    def test_consolidated_is_preferred(self):
        sheets = {
            "صورت سود و زیان": [pd.DataFrame({"a": [1]})],
            "صورت سود و زیان تلفیقی": [pd.DataFrame({"a": [1, 2]})],
        }

        name, _frame = financials.pick_sheet(sheets, financials.INCOME_SHEETS)

        self.assertIn("تلفیقی", name)

    def test_comprehensive_income_is_not_the_income_statement(self):
        """صورت سود و زیان جامع begins with the same words and is different."""

        sheets = {"صورت سود و زیان جامع": [pd.DataFrame({"a": [1]})]}

        name, frame = financials.pick_sheet(sheets, financials.INCOME_SHEETS)

        self.assertIsNone(name)
        self.assertIsNone(frame)

    def test_widest_table_wins_within_a_sheet(self):
        """Statements carry a small "دلایل تغییرات" note table after them."""

        note = pd.DataFrame({"a": [1], "b": [2]})
        real = pd.DataFrame({"a": list(range(30)), "b": list(range(30))})

        self.assertIs(codal.widest([note, real]), real)

    def test_missing_sheets_return_nothing(self):
        self.assertEqual(financials.pick_sheet({}, financials.INCOME_SHEETS),
                         (None, None))
        self.assertIsNone(financials.extract(None))
        self.assertIsNone(financials.extract({}))


class TestSplitSheets(unittest.TestCase):

    def test_headings_delimit_tables(self):
        """Sheet identity comes from the heading, never the table index.

        A bank, an investment company and a refiner return their balance sheet
        at different positions and all three label it صورت وضعیت مالی.
        """

        html = """
          <h2>صورت سود و زیان</h2>
          <table><tr><td>درآمدهای عملیاتی</td><td>100</td></tr></table>
          <h2>صورت وضعیت مالی</h2>
          <table><tr><td>جمع دارایی‌ها</td><td>200</td></tr></table>
        """

        sheets = codal.split_sheets(html)

        self.assertEqual(len(sheets), 2)
        self.assertIn("صورت وضعیت مالی", sheets)

    def test_empty_and_garbage_input(self):
        self.assertEqual(codal.split_sheets(""), {})
        self.assertEqual(codal.split_sheets(None), {})
        self.assertEqual(codal.split_sheets("<p>no tables here</p>"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
