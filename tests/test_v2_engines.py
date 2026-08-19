"""Tests for the v2 data and scoring engines: fundamental_db, fundamental_engine,
regime_engine, factor_engine.

Every test here runs without a network and without the production database. The
fundamental store is pointed at a temporary file, and every regime/factor test
injects its inputs rather than reading them -- which is why those functions take
their data as arguments in the first place.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import factor_engine
import fundamental_db
import fundamental_engine
import regime
import regime_engine


# ==============================
# THE FUNDAMENTAL STORE
# ==============================

class FundamentalStoreTest(unittest.TestCase):
    """The three stages, and the failure log that stops the queue looping."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(handle if isinstance(handle, str) else self.path)
        self.previous = fundamental_db.set_path(self.path)
        fundamental_db.create_database()

    def tearDown(self):
        fundamental_db.set_path(self.previous)
        for suffix in ("", "-wal", "-shm"):
            target = self.path + suffix
            if os.path.exists(target):
                try:
                    os.unlink(target)
                except OSError:
                    pass

    def _raw(self, tracing_no="T1", ins_code="1001"):
        return {
            "tracing_no": tracing_no,
            "ins_code": ins_code,
            "symbol": "فولاد",
            "letter_code": "ن-۱۰",
            "title": "صورت‌های مالی دوره ۱۲ ماهه منتهی به ۱۴۰۳/۱۲/۲۹",
            "period_end": "1403/12/29",
            "period_months": 12,
            "published_at": "2025-05-01T09:00:00",
            "payload": {"sheets": []},
        }

    def test_a_raw_filing_round_trips(self):
        self.assertTrue(fundamental_db.save_raw_filing(self._raw()))

        rows = fundamental_db.load_raw_filings()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tracing_no"], "T1")
        self.assertEqual(rows[0]["period_months"], 12)
        self.assertIsNone(rows[0]["processed_at"])

    def test_re_downloading_a_letter_does_not_unparse_it(self):
        """The queue guard that a naive upsert breaks."""

        fundamental_db.save_raw_filing(self._raw())
        fundamental_db.mark_processed("T1")

        # The collector sees the same letter again on its next sweep.
        fundamental_db.save_raw_filing(self._raw())

        rows = fundamental_db.load_raw_filings()

        self.assertIsNotNone(rows[0]["processed_at"],
                             "re-downloading a letter must not clear its "
                             "processed stamp, or every filing returns to the "
                             "queue on every collector run")

    def test_processed_is_not_the_same_as_parsed(self):
        """A processed filing with no metrics row is the normal failure case."""

        fundamental_db.save_raw_filing(self._raw())
        fundamental_db.mark_processed("T1")
        fundamental_db.record_failure("T1", ins_code="1001",
                                      reason="unreadable_layout")

        report = fundamental_db.coverage()

        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["periods"], 0)
        self.assertEqual(report["failures"]["total"], 1)
        self.assertEqual(report["parse_rate"], 0.0)

    def test_a_settled_failure_leaves_the_queue(self):
        fundamental_db.save_raw_filing(self._raw("T1"))
        fundamental_db.save_raw_filing(self._raw("T2"))

        fundamental_db.record_failure("T1", reason="unreadable_layout")

        pending = fundamental_db.load_raw_filings(unprocessed_only=True)

        self.assertEqual([r["tracing_no"] for r in pending], ["T2"])
        self.assertEqual(fundamental_db.settled_failures(), {"T1"})

    def test_a_transient_failure_stays_in_the_queue(self):
        fundamental_db.save_raw_filing(self._raw("T1"))
        fundamental_db.record_failure("T1", reason="timeout")

        pending = fundamental_db.load_raw_filings(unprocessed_only=True)

        self.assertEqual([r["tracing_no"] for r in pending], ["T1"],
                         "a network timeout says nothing about whether the "
                         "filing can be parsed")
        self.assertEqual(fundamental_db.settled_failures(), set())

    def test_repeated_failures_increment_rather_than_duplicate(self):
        fundamental_db.record_failure("T1", reason="timeout")
        fundamental_db.record_failure("T1", reason="timeout")
        fundamental_db.record_failure("T1", reason="timeout")

        summary = fundamental_db.failure_summary()

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["by_reason"][0]["attempts"], 3)

    def test_success_clears_an_earlier_failure(self):
        fundamental_db.record_failure("T1", reason="timeout")

        fundamental_db.save_metrics({
            "ins_code": "1001", "period_end": "1403/12/29",
            "period_months": 12, "tracing_no": "T1",
            "ratios": {"roe": 30.0},
        })

        self.assertEqual(fundamental_db.failure_summary()["total"], 0)

    def test_an_empty_metrics_record_is_refused(self):
        """An empty row would be counted as coverage by every report."""

        self.assertFalse(fundamental_db.save_metrics({
            "ins_code": "1001", "period_end": "1403/12/29",
            "ratios": {}, "items": {},
        }))

        self.assertEqual(fundamental_db.coverage()["periods"], 0)

    def test_scalars_are_lifted_out_of_the_ratio_blob(self):
        fundamental_db.save_metrics({
            "ins_code": "1001", "period_end": "1403/12/29",
            "period_months": 12,
            "ratios": {"roe": 31.5, "pe": 5.2, "junk": "not a number"},
        })

        conn = fundamental_db._connect()
        row = conn.execute("SELECT roe, pe FROM metrics").fetchone()
        conn.close()

        self.assertAlmostEqual(row[0], 31.5)
        self.assertAlmostEqual(row[1], 5.2)

    def test_latest_metrics_prefers_the_longer_period(self):
        """Two filings share a period end; the annual one is the real figure."""

        for months, roe in ((6, 10.0), (12, 25.0)):
            fundamental_db.save_metrics({
                "ins_code": "1001", "period_end": "1403/12/29",
                "period_months": months, "ratios": {"roe": roe},
            })

        latest = fundamental_db.load_latest_metrics(["1001"])

        self.assertEqual(latest["1001"]["period_months"], 12)
        self.assertAlmostEqual(latest["1001"]["ratios"]["roe"], 25.0)

    def test_the_before_filter_cuts_on_publication_not_on_period(self):
        fundamental_db.save_metrics({
            "ins_code": "1001", "period_end": "1403/12/29",
            "period_months": 12, "published_at": "2025-05-01T09:00:00",
            "ratios": {"roe": 25.0},
        })

        # The quarter has ended but the statement was not public yet.
        self.assertEqual(
            fundamental_db.load_metrics("1001", before="2025-04-01T00:00:00"),
            [])

        self.assertEqual(
            len(fundamental_db.load_metrics("1001", before="2025-06-01T00:00:00")),
            1)

    def test_needs_refresh_distinguishes_never_from_stale_from_failed(self):
        stale, reason = fundamental_db.needs_refresh()
        self.assertTrue(stale)
        self.assertIn("never", reason)

        started = fundamental_db.start_refresh()
        fundamental_db.finish_refresh(fundamental_db.SCOPE_PROCESS, started,
                                      status=fundamental_db.STATUS_OK)

        stale, reason = fundamental_db.needs_refresh()
        self.assertFalse(stale)

        stale, reason = fundamental_db.needs_refresh(
            now=datetime.now() + timedelta(days=8))
        self.assertTrue(stale)
        self.assertIn("cadence", reason)

    def test_a_failed_run_is_always_due(self):
        started = fundamental_db.start_refresh()
        fundamental_db.finish_refresh(fundamental_db.SCOPE_PROCESS, started,
                                      status=fundamental_db.STATUS_FAILED,
                                      detail="codal 429")

        stale, reason = fundamental_db.needs_refresh()

        self.assertTrue(stale)
        self.assertIn("FAILED", reason)

    def test_an_unopened_run_does_not_count_as_completed(self):
        """A crashed run leaves finished_at NULL and must not reset the clock."""

        fundamental_db.start_refresh()

        self.assertIsNone(fundamental_db.last_refresh())

        stale, _reason = fundamental_db.needs_refresh()
        self.assertTrue(stale)


# ==============================
# THE FUNDAMENTAL ENGINE
# ==============================

class StabilityTest(unittest.TestCase):

    def test_two_periods_are_a_direction_not_a_variability(self):
        fraction, notes = fundamental_engine.stability([
            {"net_margin": 12.0}, {"net_margin": 13.0},
        ])

        self.assertIsNone(fraction)
        self.assertIn("3 filed periods", notes[0])

    def test_a_steady_margin_scores_full(self):
        fraction, _ = fundamental_engine.stability([
            {"net_margin": 12.0}, {"net_margin": 12.5},
            {"net_margin": 11.8}, {"net_margin": 12.2},
        ])

        self.assertEqual(fraction, 1.0)

    def test_an_erratic_margin_scores_low(self):
        fraction, _ = fundamental_engine.stability([
            {"net_margin": 2.0}, {"net_margin": 25.0},
            {"net_margin": 4.0}, {"net_margin": 30.0},
        ])

        self.assertLessEqual(fraction, 0.25)

    def test_one_loss_period_caps_stability_regardless_of_variation(self):
        """A loss inside the window is a live possibility, not history."""

        steady = fundamental_engine.stability([
            {"net_margin": 10.0}, {"net_margin": 10.0},
            {"net_margin": 10.0}, {"net_margin": 10.0},
        ])[0]

        with_loss = fundamental_engine.stability([
            {"net_margin": 10.0}, {"net_margin": 10.0},
            {"net_margin": -1.0}, {"net_margin": 10.0},
        ])[0]

        self.assertEqual(steady, 1.0)
        self.assertLessEqual(with_loss, 0.25)

    def test_a_near_zero_average_is_not_divided_by(self):
        """A coefficient of variation on a ~0 mean explodes."""

        fraction, notes = fundamental_engine.stability([
            {"net_margin": 0.4}, {"net_margin": 0.5}, {"net_margin": 0.3},
        ])

        self.assertIsNotNone(fraction)
        self.assertLess(fraction, 0.5)
        self.assertIn("too thin", notes[0])

    def test_records_and_bare_dicts_are_both_accepted(self):
        wrapped = fundamental_engine.stability([
            {"ratios": {"net_margin": 12.0}},
            {"ratios": {"net_margin": 12.5}},
            {"ratios": {"net_margin": 11.8}},
        ])[0]

        bare = fundamental_engine.stability([
            {"net_margin": 12.0}, {"net_margin": 12.5}, {"net_margin": 11.8},
        ])[0]

        self.assertEqual(wrapped, bare)


class SectorRelativeTest(unittest.TestCase):

    def test_fewer_than_three_comparables_is_not_a_median(self):
        medians = fundamental_engine.sector_medians([
            {"pe": 5.0}, {"pe": 7.0},
        ])

        self.assertEqual(medians, {})

    def test_non_positive_multiples_are_dropped_not_zeroed(self):
        medians = fundamental_engine.sector_medians([
            {"pe": 6.0}, {"pe": 8.0}, {"pe": 10.0},
            {"pe": None}, {"pe": -3.0}, {"pe": 0.0},
        ])

        self.assertAlmostEqual(medians["pe"], 8.0)

    def test_a_discount_to_the_sector_scores_higher_than_a_premium(self):
        cheap, _ = fundamental_engine.relative_valuation(
            {"pe": 4.0}, {"pe": 8.0})
        rich, _ = fundamental_engine.relative_valuation(
            {"pe": 16.0}, {"pe": 8.0})

        self.assertGreater(cheap, rich)

    def test_no_median_returns_none_rather_than_neutral(self):
        fraction, notes = fundamental_engine.relative_valuation({"pe": 5.0}, {})

        self.assertIsNone(fraction)
        self.assertIn("no sector median", notes[0])


class RiskFlagTest(unittest.TestCase):

    def test_a_loss_maker_is_flagged_critical_and_capped(self):
        flags, seen = fundamental_engine.risk_flags({"net_margin": -8.0})

        names = [f["flag"] for f in flags]

        self.assertIn(fundamental_engine.FLAG_LOSS_MAKING, names)
        self.assertIn("profitability", seen)

        cap, flag = fundamental_engine.ceiling_for(flags)

        self.assertEqual(cap, config.FUND_CAP_LOSS_MAKING)
        self.assertEqual(flag, fundamental_engine.FLAG_LOSS_MAKING)

    def test_negative_equity_caps_harder_than_a_loss(self):
        loss = fundamental_engine.ceiling_for(
            fundamental_engine.risk_flags({"net_margin": -8.0})[0])[0]

        insolvent = fundamental_engine.ceiling_for(
            fundamental_engine.risk_flags({"debt_ratio": 112.0})[0])[0]

        self.assertLess(insolvent, loss)

    def test_no_inputs_means_no_flags_and_no_reading(self):
        """The distinction between 'nothing found' and 'nothing to look at'."""

        flags, seen = fundamental_engine.risk_flags({})

        self.assertEqual(flags, [])
        self.assertEqual(seen, [])

        fraction, notes = fundamental_engine._score_risk_group(flags, seen)

        self.assertIsNone(fraction)
        self.assertIn("nothing in the store", notes[0])

    def test_a_clean_balance_sheet_scores_the_risk_group_full(self):
        ratios = {"net_margin": 14.0, "roe": 30.0, "debt_ratio": 35.0,
                  "current_ratio": 2.0, "operating_cash_flow": 5e11,
                  "earnings_quality": 1.1}

        flags, seen = fundamental_engine.risk_flags(ratios)

        self.assertEqual(flags, [])

        fraction, _ = fundamental_engine._score_risk_group(flags, seen)

        self.assertEqual(fraction, 1.0)

    def test_roe_below_the_deposit_rate_is_noted_without_a_cap(self):
        flags, _ = fundamental_engine.risk_flags(
            {"net_margin": 5.0, "roe": 15.0})

        names = [f["flag"] for f in flags]

        self.assertIn(fundamental_engine.FLAG_EQUITY_BELOW_DEPOSIT, names)
        self.assertIsNone(fundamental_engine.ceiling_for(flags)[0])


class FundamentalEngineTest(unittest.TestCase):

    GOOD = {
        "roe": 38.0, "roa": 18.0, "net_margin": 22.0,
        "revenue_growth": 55.0, "profit_growth": 60.0, "eps_growth": 58.0,
        "pe": 5.0, "pb": 1.4, "ps": 1.2,
        "debt_ratio": 32.0, "current_ratio": 2.1, "equity_ratio": 68.0,
        "operating_cash_flow": 9e11, "ocf_margin": 26.0,
        "earnings_quality": 1.15,
    }

    def test_a_strong_company_scores_high(self):
        result = fundamental_engine.evaluate(self.GOOD)

        self.assertEqual(result["verdict"], fundamental_engine.SCORED)
        self.assertGreaterEqual(result["score"], 75)
        self.assertFalse(result["capped"])

    def test_the_four_groups_are_all_reported(self):
        result = fundamental_engine.evaluate(self.GOOD)

        self.assertEqual(set(result["groups"]), set(fundamental_engine.GROUPS))

        for name in fundamental_engine.GROUPS:
            self.assertIsNotNone(result["groups"][name]["score"], name)

    def test_a_cheap_loss_maker_cannot_reach_the_buy_band(self):
        """The substantive change over an unweighted blend.

        Valuation alone must not carry an insolvent company into a buy band.
        Note `capped` is False here and that is correct: the raw score already
        sits under the ceiling, so the ceiling did not have to bind. What matters
        is that the ceiling EXISTS and that the result is below the buy band --
        `capped` reports whether it bound, not whether it applies.
        """

        cheap_and_broken = {
            "pe": None, "pb": 0.4, "ps": 0.3,          # very cheap
            "net_margin": -12.0, "roe": -20.0,          # loss-making
            "debt_ratio": 88.0,                          # heavily geared
            "operating_cash_flow": -4e11,                # burning cash
            "current_ratio": 0.7,
        }

        result = fundamental_engine.evaluate(cheap_and_broken)

        self.assertEqual(result["verdict"], fundamental_engine.SCORED)
        self.assertEqual(result["cap"], config.FUND_CAP_LOSS_MAKING)
        self.assertEqual(result["cap_flag"],
                         fundamental_engine.FLAG_LOSS_MAKING)
        self.assertLessEqual(result["score"], config.FUND_CAP_LOSS_MAKING)
        self.assertLess(result["score"], config.SWING_BUY_SCORE)

    def test_the_raw_score_survives_the_cap(self):
        """Both numbers matter: what the model read, and what may be ranked."""

        result = fundamental_engine.evaluate({
            "pe": 3.0, "pb": 0.8, "ps": 0.5,
            "roe": 26.0, "roa": 9.0, "net_margin": 11.0,
            "revenue_growth": 60.0, "profit_growth": 55.0, "eps_growth": 50.0,
            "debt_ratio": 30.0, "current_ratio": 2.0,
            "operating_cash_flow": -1e11,                # the only problem
            "earnings_quality": None,
        })

        self.assertTrue(result["capped"])
        self.assertGreater(result["raw_score"], result["score"])
        self.assertEqual(result["cap_flag"], fundamental_engine.FLAG_CASH_BURN)

    def test_thin_input_is_insufficient_rather_than_a_low_score(self):
        result = fundamental_engine.evaluate({"pe": 6.0})

        self.assertEqual(result["verdict"], fundamental_engine.INSUFFICIENT)
        self.assertIsNone(result["score"])
        self.assertIn("need", result["reason"])

    def test_missing_groups_renormalise_rather_than_score_zero(self):
        """A company with no growth comparative is not a company with no growth."""

        without_growth = dict(self.GOOD)
        for key in ("revenue_growth", "profit_growth", "eps_growth"):
            without_growth[key] = None

        result = fundamental_engine.evaluate(without_growth)

        self.assertEqual(result["verdict"], fundamental_engine.SCORED)
        self.assertIsNone(result["groups"][fundamental_engine.GROWTH]["score"])
        self.assertLess(result["coverage"], 1.0)
        self.assertGreaterEqual(result["score"], 70,
                                "dropping the growth pillar must not be the "
                                "same as scoring it zero")

    def test_a_sector_premium_lowers_the_valuation_group(self):
        expensive_for_its_sector = fundamental_engine.evaluate(
            self.GOOD, medians={"pe": 2.0, "pb": 0.5, "ps": 0.4})

        cheap_for_its_sector = fundamental_engine.evaluate(
            self.GOOD, medians={"pe": 14.0, "pb": 4.0, "ps": 3.0})

        self.assertLess(
            expensive_for_its_sector["groups"]["valuation"]["score"],
            cheap_for_its_sector["groups"]["valuation"]["score"])

    def test_history_feeds_stability_into_the_quality_group(self):
        steady = [dict(self.GOOD, net_margin=m)
                  for m in (21.0, 22.0, 21.5, 22.0)]
        erratic = [dict(self.GOOD, net_margin=m)
                   for m in (2.0, 40.0, 3.0, 22.0)]

        self.assertGreater(
            fundamental_engine.evaluate(history=steady)["groups"]["quality"]["score"],
            fundamental_engine.evaluate(history=erratic)["groups"]["quality"]["score"])

    def test_score_helper_returns_none_rather_than_zero(self):
        self.assertIsNone(fundamental_engine.score({"pe": 6.0}))

    def test_evaluate_stored_says_which_absence_it_hit(self):
        previous = fundamental_db.set_path(
            os.path.join(tempfile.gettempdir(), "sirat-no-such-store.db"))

        try:
            result = fundamental_engine.evaluate_stored("1001")
        finally:
            fundamental_db.set_path(previous)

        self.assertEqual(result["verdict"], fundamental_engine.INSUFFICIENT)
        self.assertIn("no fundamental store", result["reason"])


# ==============================
# THE REGIME ENGINE
# ==============================

class TurnoverTest(unittest.TestCase):

    def test_a_short_series_refuses_rather_than_reporting_steady(self):
        state = regime_engine.turnover_state([1.0] * 10)

        self.assertIsNone(state["state"])
        self.assertIn("need", state["reason"])

    def test_expanding_and_contracting_are_detected(self):
        base = [100.0] * 60

        expanding = regime_engine.turnover_state(base + [400.0] * 10)
        contracting = regime_engine.turnover_state(base + [10.0] * 10)

        self.assertEqual(expanding["state"], regime_engine.EXPANDING)
        self.assertEqual(contracting["state"], regime_engine.CONTRACTING)

    def test_a_flat_market_is_steady(self):
        state = regime_engine.turnover_state([100.0] * 80)

        self.assertEqual(state["state"], regime_engine.STEADY)
        self.assertAlmostEqual(state["ratio"], 1.0, places=3)

    def test_a_frame_with_a_turnover_column_is_accepted(self):
        import pandas as pd

        frame = pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=80),
            "turnover": [100.0] * 70 + [300.0] * 10,
        })

        self.assertEqual(regime_engine.turnover_state(frame)["state"],
                         regime_engine.EXPANDING)

    def test_a_zero_baseline_does_not_divide_by_zero(self):
        state = regime_engine.turnover_state([0.0] * 80)

        self.assertIsNone(state["state"])
        self.assertIn("zero", state["reason"])


class RegimeScoreTest(unittest.TestCase):

    def test_bull_scores_above_sideways_above_bear(self):
        bull = regime_engine.regime_score(regime.BULL, confidence=1.0)
        flat = regime_engine.regime_score(regime.SIDEWAYS, confidence=1.0)
        bear = regime_engine.regime_score(regime.BEAR, confidence=1.0)

        self.assertGreater(bull, flat)
        self.assertGreater(flat, bear)

    def test_low_confidence_pulls_the_score_toward_neutral(self):
        confident = regime_engine.regime_score(regime.BULL, confidence=1.0)
        split = regime_engine.regime_score(regime.BULL, confidence=0.34)

        self.assertGreater(confident, split)
        self.assertGreater(split, config.REGIME_SCORE_SIDEWAYS - 1)

    def test_expanding_volume_helps_a_rise_and_hurts_a_fall(self):
        expanding = {"state": regime_engine.EXPANDING}

        bull_with = regime_engine.regime_score(regime.BULL, 1.0,
                                              turnover=expanding)
        bull_without = regime_engine.regime_score(regime.BULL, 1.0)

        bear_with = regime_engine.regime_score(regime.BEAR, 1.0,
                                              turnover=expanding)
        bear_without = regime_engine.regime_score(regime.BEAR, 1.0)

        self.assertGreater(bull_with, bull_without)
        self.assertLess(bear_with, bear_without,
                        "volume arriving in a falling market is distribution")

    def test_an_unknown_verdict_scores_nothing(self):
        self.assertIsNone(regime_engine.regime_score("MOON"))

    def test_the_score_stays_on_the_scale(self):
        for verdict in (regime.BULL, regime.BEAR, regime.SIDEWAYS):
            for volatility in (regime.HIGH_VOLATILITY, regime.LOW_VOLATILITY,
                               None):
                for state in (regime_engine.EXPANDING, regime_engine.CONTRACTING,
                              None):
                    value = regime_engine.regime_score(
                        verdict, 1.0, volatility, {"state": state})
                    self.assertGreaterEqual(value, 0)
                    self.assertLessEqual(value, 100)


class TradingModeTest(unittest.TestCase):

    def test_a_confident_calm_bull_is_aggressive(self):
        mode, _ = regime_engine.trading_mode(regime.BULL, confidence=0.8)

        self.assertEqual(mode, regime_engine.AGGRESSIVE)

    def test_a_split_bull_vote_is_only_normal(self):
        mode, _ = regime_engine.trading_mode(regime.BULL, confidence=0.4)

        self.assertEqual(mode, regime_engine.NORMAL)

    def test_a_rising_index_on_shrinking_turnover_is_not_aggressive(self):
        """The most common trap: new highs while the exit door narrows."""

        mode, reasons = regime_engine.trading_mode(
            regime.BULL, confidence=0.9,
            turnover={"state": regime_engine.CONTRACTING})

        self.assertEqual(mode, regime_engine.NORMAL)
        self.assertTrue(any("ارزش معاملات" in r for r in reasons))

    def test_a_volatile_bear_permits_nothing(self):
        mode, _ = regime_engine.trading_mode(
            regime.BEAR, confidence=0.9,
            volatility_regime=regime.HIGH_VOLATILITY)

        self.assertEqual(mode, regime_engine.CASH_ONLY)

    def test_a_calm_bear_is_defensive_not_cash_only(self):
        mode, _ = regime_engine.trading_mode(regime.BEAR, confidence=0.9)

        self.assertEqual(mode, regime_engine.DEFENSIVE)

    def test_every_mode_has_a_budget_and_a_label(self):
        for mode in regime_engine.MODES:
            self.assertIn(mode, config.TRADING_MODE_BUDGET)
            self.assertIn(mode, regime_engine.MODE_LABELS)


class RegimeAssessTest(unittest.TestCase):

    def _report(self, verdict=regime.BULL, confidence=1.0, invested=90.0,
                volatility=None):
        return {
            "available": True,
            "regime": verdict,
            "confidence": confidence,
            "volatility_regime": volatility,
            "label": "بازار",
            "votes": {"bull": 3, "bear": 0, "sideways": 0},
            "cash_target_pct": 100.0 - invested,
            "max_invested_pct": invested,
            "evidence": ["شاخص کل بالای میانگین"],
            "missing": [],
        }

    def test_the_mode_budget_never_loosens_the_regime_figure(self):
        """The one-directional guarantee in the module docstring."""

        for verdict in (regime.BULL, regime.BEAR, regime.SIDEWAYS):
            for confidence in (0.34, 0.5, 1.0):
                for volatility in (None, regime.HIGH_VOLATILITY):

                    report = self._report(verdict, confidence,
                                          volatility=volatility)

                    result = regime_engine.assess(
                        report, turnover={"state": regime_engine.STEADY})

                    self.assertLessEqual(
                        result["max_invested_pct"],
                        report["max_invested_pct"] + 1e-9,
                        "%s/%s/%s loosened the regime's own ceiling"
                        % (verdict, confidence, volatility))

    def test_an_unavailable_regime_blocks_new_entries(self):
        result = regime_engine.assess({"available": False,
                                       "reason": "no index history"})

        self.assertFalse(result["available"])
        self.assertEqual(result["trading_mode"], regime_engine.CASH_ONLY)
        self.assertEqual(result["max_invested_pct"], 0.0)
        self.assertFalse(regime_engine.allows_new_entries(result))

    def test_turnover_evidence_reaches_the_report(self):
        result = regime_engine.assess(
            self._report(), turnover=[100.0] * 70 + [300.0] * 10)

        self.assertEqual(result["turnover"]["state"], regime_engine.EXPANDING)
        self.assertTrue(any("ارزش معاملات" in line
                            for line in result["evidence"]))

    def test_the_spec_vocabulary_is_mapped(self):
        self.assertEqual(
            regime_engine.assess(self._report(regime.BULL))["spec_name"],
            "Bullish")
        self.assertEqual(
            regime_engine.assess(self._report(regime.BEAR))["spec_name"],
            "Bearish")
        self.assertEqual(
            regime_engine.assess(self._report(regime.SIDEWAYS))["spec_name"],
            "Sideways")

    def test_cash_only_forbids_entries(self):
        result = regime_engine.assess(
            self._report(regime.BEAR, 0.9, volatility=regime.HIGH_VOLATILITY),
            turnover={"state": regime_engine.STEADY})

        self.assertEqual(result["trading_mode"], regime_engine.CASH_ONLY)
        self.assertEqual(result["max_invested_pct"], 0.0)
        self.assertFalse(regime_engine.allows_new_entries(result))

    def test_the_assessment_carries_the_two_levers(self):
        """The regime's job since it left the Swing Score: size, and whether."""

        result = regime_engine.assess(self._report(regime.BULL, 1.0),
                                      turnover={"state": regime_engine.STEADY})

        self.assertEqual(result["position_multiplier"],
                         result["mode_budget_share"])
        self.assertIsNotNone(result["entry_floor"])


class RegimeLeversTest(unittest.TestCase):
    """position_multiplier() and entry_floor() -- what the regime does now."""

    def test_a_confident_bull_gets_full_size_and_no_extra_bar(self):
        self.assertEqual(
            regime_engine.position_multiplier(regime_engine.AGGRESSIVE,
                                              regime.BULL), 1.0)
        self.assertEqual(
            regime_engine.entry_floor({"trading_mode": regime_engine.AGGRESSIVE}),
            0)

    def test_cash_only_sizes_to_nothing(self):
        self.assertEqual(
            regime_engine.position_multiplier(regime_engine.CASH_ONLY), 0.0)

    def test_a_sideways_market_never_gets_more_than_the_cap(self):
        """NORMAL is reached by a calm sideways market and by a fragile bull."""

        for mode in regime_engine.MODES:

            sideways = regime_engine.position_multiplier(mode, regime.SIDEWAYS)

            self.assertLessEqual(sideways,
                                 config.REGIME_SIDEWAYS_MAX_MULTIPLIER + 1e-9,
                                 "%s in a sideways market" % mode)

            self.assertLessEqual(sideways,
                                 regime_engine.position_multiplier(mode),
                                 "the sideways cap must only ever tighten")

    def test_a_bull_is_not_capped_by_the_sideways_rule(self):
        self.assertGreater(
            regime_engine.position_multiplier(regime_engine.NORMAL, regime.BULL),
            config.REGIME_SIDEWAYS_MAX_MULTIPLIER)

    def test_a_weak_market_demands_a_higher_score(self):
        defensive = regime_engine.entry_floor(
            {"trading_mode": regime_engine.DEFENSIVE})
        normal = regime_engine.entry_floor(
            {"trading_mode": regime_engine.NORMAL})

        self.assertGreater(defensive, normal)

    def test_no_assessment_demands_nothing_extra(self):
        self.assertEqual(regime_engine.entry_floor(None), 0)
        self.assertEqual(regime_engine.entry_floor({}), 0)


# ==============================
# THE FACTOR ENGINE
# ==============================

class MomentumTest(unittest.TestCase):

    def _ramp(self, start, end, bars=200):
        step = (end - start) / float(bars - 1)
        return [start + step * i for i in range(bars)]

    def test_a_steady_climb_scores_above_a_steady_fall(self):
        up = factor_engine.momentum(self._ramp(100.0, 200.0))
        down = factor_engine.momentum(self._ramp(200.0, 100.0))

        self.assertGreater(up["score"], down["score"])
        self.assertEqual(down["score"], 0)

    def test_a_short_history_is_insufficient_rather_than_zero(self):
        reading = factor_engine.momentum([100.0])

        self.assertEqual(reading["verdict"], factor_engine.INSUFFICIENT)
        self.assertIsNone(reading["score"])

    def test_the_longest_horizon_drops_out_when_history_is_short(self):
        reading = factor_engine.momentum(self._ramp(100.0, 140.0, bars=70))

        self.assertEqual(reading["verdict"], factor_engine.SCORED)
        self.assertIn(20, reading["returns"])
        self.assertNotIn(120, reading["returns"])

    def test_relative_strength_demotes_a_laggard_that_still_rose(self):
        stock = self._ramp(100.0, 112.0)
        weak_index = self._ramp(100.0, 103.0)
        strong_index = self._ramp(100.0, 140.0)

        against_weak = factor_engine.momentum(stock, weak_index)
        against_strong = factor_engine.momentum(stock, strong_index)

        self.assertGreater(against_weak["score"], against_strong["score"],
                           "a share that rose less than the index is a laggard "
                           "however positive its own return")

    def test_without_an_index_the_reading_says_so(self):
        reading = factor_engine.momentum(self._ramp(100.0, 150.0))

        self.assertEqual(reading["excess"], {})
        self.assertTrue(any("absolute return alone" in n
                            for n in reading["notes"]))

    def test_a_recent_pop_inside_a_downtrend_scores_low(self):
        """The claim the multi-horizon design actually makes.

        A share up ~3% over 20 sessions after falling for a year is not momentum.
        A single-horizon reading of the recent window calls it positive; the
        60- and 120-session horizons are what refuse it.
        """

        downtrend = self._ramp(200.0, 100.0, bars=190)
        pop = [100.0 * (1.0 + 0.008 * i) for i in range(1, 11)]

        steady_climb = self._ramp(100.0, 130.0, bars=200)

        popped = factor_engine.momentum(downtrend + pop)

        self.assertGreater(popped["returns"][20], 0,
                           "the short horizon does read the pop as positive")
        self.assertLess(popped["returns"][120], 0)

        self.assertLess(popped["score"],
                        factor_engine.momentum(steady_climb)["score"])

    def test_nans_are_dropped_not_forward_filled(self):
        values = self._ramp(100.0, 150.0)
        values[50] = float("nan")

        reading = factor_engine.momentum(values)

        self.assertEqual(reading["verdict"], factor_engine.SCORED)


class LiquidityTest(unittest.TestCase):

    def test_a_liquid_name_scores_above_a_thin_one(self):
        liquid = factor_engine.liquidity(adv=config.MIN_DAILY_VALUE * 20)
        thin = factor_engine.liquidity(adv=config.MIN_DAILY_VALUE * 0.1)

        self.assertGreater(liquid["score"], thin["score"])

    def test_no_input_is_insufficient(self):
        reading = factor_engine.liquidity()

        self.assertEqual(reading["verdict"], factor_engine.INSUFFICIENT)
        self.assertIsNone(reading["score"])

    def test_a_name_that_often_does_not_trade_is_penalised(self):
        traded = factor_engine.liquidity(
            adv=config.MIN_DAILY_VALUE * 5, traded_sessions_pct=100.0)
        halted = factor_engine.liquidity(
            adv=config.MIN_DAILY_VALUE * 5, traded_sessions_pct=55.0)

        self.assertGreater(traded["score"], halted["score"])

    def test_a_volume_explosion_is_not_the_top_band(self):
        """A 4x volume day on a swing horizon is as often a climax as a start."""

        strong = factor_engine.liquidity(adv=config.MIN_DAILY_VALUE * 5,
                                         volume_ratio=1.8)
        blowoff = factor_engine.liquidity(adv=config.MIN_DAILY_VALUE * 5,
                                          volume_ratio=4.0)

        self.assertGreater(strong["score"], blowoff["score"])

    def test_participation_over_the_cap_is_reported(self):
        reading = factor_engine.liquidity(
            adv=1e9, position_value=1e9)     # 100% of a day's turnover

        self.assertGreater(reading["adv_participation_pct"],
                           config.MAX_ADV_PARTICIPATION_PCT)

    def test_traded_share_counts_zero_volume_sessions(self):
        self.assertEqual(factor_engine.traded_share([1, 1, 0, 0], sessions=4),
                         50.0)
        self.assertIsNone(factor_engine.traded_share([]))


class CatalystTest(unittest.TestCase):

    def test_an_undirected_event_does_not_score(self):
        """The circularity guard. A material filing is not a buy signal."""

        reading = factor_engine.catalyst(events=[
            {"letter_code": "ن-۲۰", "published_at": "2026-08-01"},
        ])

        self.assertEqual(reading["verdict"], factor_engine.INSUFFICIENT)
        self.assertIsNone(reading["score"])
        self.assertEqual(reading["event_count"], 1)

    def test_sentiment_produces_a_score(self):
        reading = factor_engine.catalyst(news_reading={
            "verdict": "SCORED", "sentiment": 0.6, "items": 3,
        })

        self.assertEqual(reading["verdict"], factor_engine.SCORED)
        self.assertEqual(reading["score"], 80)

    def test_no_provider_carries_the_project_wide_reason(self):
        reading = factor_engine.catalyst()

        self.assertEqual(reading["verdict"], factor_engine.INSUFFICIENT)
        self.assertIn("no historical news store", reading["reason"])


class SwingScoreTest(unittest.TestCase):

    FULL = dict(regime=80, momentum=75, technical=80, fundamental=70,
                liquidity=90, catalyst=60)

    def test_all_six_factors_blend_to_the_expected_number(self):
        result = factor_engine.swing_score(**self.FULL)

        weights = factor_engine.default_weights()

        expected = sum(
            self.FULL[name] * weights[name] for name in self.FULL
        ) / float(sum(weights.values()))

        self.assertEqual(result["score"], int(round(expected)))
        self.assertEqual(result["coverage"], 1.0)

    def test_a_missing_factor_renormalises_rather_than_scoring_zero(self):
        without = dict(self.FULL)
        without["fundamental"] = None
        without["catalyst"] = None

        result = factor_engine.swing_score(**without)

        self.assertEqual(result["verdict"], factor_engine.SCORED)
        self.assertLess(result["coverage"], 1.0)
        self.assertGreater(result["score"], 70,
                           "absent fundamentals must not act as a zero")

    def test_below_the_coverage_floor_there_is_no_score(self):
        result = factor_engine.swing_score(liquidity=90, catalyst=80)

        self.assertEqual(result["verdict"], factor_engine.INSUFFICIENT)
        self.assertIsNone(result["score"])
        self.assertFalse(result["scored"])
        self.assertIn("need", result["reason"])

    def test_the_missing_factors_are_named(self):
        result = factor_engine.swing_score(regime=50, momentum=50,
                                           technical=50)

        self.assertIn(factor_engine.FACTOR_LABELS[factor_engine.FUNDAMENTAL],
                      result["missing"])

    def test_weights_are_overridable(self):
        momentum_only = factor_engine.swing_score(
            momentum=100, regime=0, technical=0, fundamental=0, liquidity=0,
            catalyst=0,
            weights={factor_engine.MOMENTUM: 100, factor_engine.REGIME: 0,
                     factor_engine.TECHNICAL: 0,
                     factor_engine.FUNDAMENTAL: 0,
                     factor_engine.LIQUIDITY: 0, factor_engine.CATALYST: 0})

        self.assertEqual(momentum_only["score"], 100)

    def test_contributions_sum_to_the_score(self):
        result = factor_engine.swing_score(**self.FULL)

        self.assertAlmostEqual(sum(result["contributions"].values()),
                               result["score"], delta=1.0)

    def test_the_market_read_does_not_move_a_stocks_score(self):
        """The correction this whole engine change is about.

        A regime score is the same number for every name on a session, so it
        cannot reorder them -- it could only dilute the factors that vary. At
        the shipped weights the market read must therefore be worth exactly
        nothing to an individual score, whatever value it takes.
        """

        for value in (0, 15, 50, 85, 100, None):
            scored = factor_engine.swing_score(
                **dict(self.FULL, regime=value))

            self.assertEqual(scored["score"],
                             factor_engine.swing_score(
                                 **dict(self.FULL, regime=50))["score"],
                             "regime=%r moved a stock's score" % value)

    def test_a_zero_weighted_regime_does_not_count_toward_coverage(self):
        """Otherwise the coverage floor would silently tighten for every name."""

        without_regime = factor_engine.swing_score(
            momentum=70, technical=70, liquidity=70)

        self.assertEqual(without_regime["verdict"], factor_engine.SCORED)

        self.assertAlmostEqual(
            without_regime["coverage"],
            (config.SWING_WEIGHT_MOMENTUM + config.SWING_WEIGHT_TECHNICAL
             + config.SWING_WEIGHT_LIQUIDITY)
            / float(sum(factor_engine.default_weights().values())),
            places=2)


class DecisionTest(unittest.TestCase):

    def _swing(self, score):
        return {"score": score, "scored": score is not None,
                "reason": None if score is not None else "no inputs"}

    def _regime(self, mode=regime_engine.NORMAL, available=True):
        return {"available": available, "trading_mode": mode,
                "mode_label": mode}

    def test_a_strong_score_in_a_normal_market_is_a_buy(self):
        verdict = factor_engine.decide(
            self._swing(82), technical=85, regime=self._regime())

        self.assertEqual(verdict["action"], factor_engine.BUY)
        self.assertIsNotNone(verdict["conviction"])

    def test_the_measured_technical_gate_still_binds(self):
        verdict = factor_engine.decide(
            self._swing(90), technical=60, regime=self._regime())

        self.assertEqual(verdict["action"], factor_engine.AVOID)
        self.assertTrue(any("دروازه" in r for r in verdict["reasons"]))

    def test_cash_only_blocks_a_perfect_score(self):
        verdict = factor_engine.decide(
            self._swing(99), technical=99,
            regime=self._regime(regime_engine.CASH_ONLY))

        self.assertEqual(verdict["action"], factor_engine.AVOID)

    def test_a_middling_score_is_watched_not_avoided(self):
        verdict = factor_engine.decide(
            self._swing(55), technical=80, regime=self._regime())

        self.assertEqual(verdict["action"], factor_engine.HOLD)
        self.assertIsNone(verdict["conviction"])

    def test_a_name_never_owned_is_never_sold(self):
        for score in (0, 20, 39, 40, 60, 90):
            verdict = factor_engine.decide(
                self._swing(score), technical=80, regime=self._regime())
            self.assertNotEqual(verdict["action"], factor_engine.SELL,
                                "SELL on an unheld name generates an order "
                                "nobody can fill")

    def test_a_held_name_below_the_exit_band_is_sold(self):
        verdict = factor_engine.decide(
            self._swing(30), technical=80, regime=self._regime(), held=True)

        self.assertEqual(verdict["action"], factor_engine.SELL)

    def test_a_held_name_with_no_score_is_held_not_dumped(self):
        verdict = factor_engine.decide(
            self._swing(None), technical=None, regime=self._regime(),
            held=True)

        self.assertEqual(verdict["action"], factor_engine.HOLD)

    def test_a_critical_fundamental_flag_exits_a_holding(self):
        fundamental = {"flags": [{"severity": "CRITICAL",
                                  "message": "زیان‌ده",
                                  "flag": "LOSS_MAKING", "cap": 45}]}

        held = factor_engine.decide(
            self._swing(75), technical=85, regime=self._regime(),
            fundamental=fundamental, held=True)

        entry = factor_engine.decide(
            self._swing(75), technical=85, regime=self._regime(),
            fundamental=fundamental)

        self.assertEqual(held["action"], factor_engine.SELL)
        self.assertEqual(entry["action"], factor_engine.AVOID)

    def test_a_low_risk_score_blocks_an_entry(self):
        verdict = factor_engine.decide(
            self._swing(85), technical=85, regime=self._regime(),
            risk_score=config.RISK_MIN_SCORE - 1)

        self.assertEqual(verdict["action"], factor_engine.AVOID)

    def test_an_unscoreable_candidate_is_avoided_with_the_reason(self):
        verdict = factor_engine.decide(
            self._swing(None), technical=85, regime=self._regime())

        self.assertEqual(verdict["action"], factor_engine.AVOID)
        self.assertIn("no inputs", verdict["reasons"][0])

    def test_a_defensive_market_raises_the_bar_without_reordering(self):
        """The regime's new lever: how good a name must be, not which is best."""

        floor = config.TRADING_MODE_ENTRY_FLOOR[regime_engine.DEFENSIVE]

        self.assertGreater(floor, 0, "this test needs a non-zero floor")

        marginal = config.SWING_BUY_SCORE + 1

        calm = factor_engine.decide(
            self._swing(marginal), technical=85,
            regime=self._regime(regime_engine.NORMAL))

        weak = factor_engine.decide(
            self._swing(marginal), technical=85,
            regime=self._regime(regime_engine.DEFENSIVE))

        self.assertEqual(calm["action"], factor_engine.BUY)
        self.assertEqual(weak["action"], factor_engine.HOLD,
                         "a marginal name in a defensive market is watched, "
                         "not bought")

        self.assertEqual(weak["buy_threshold"],
                         config.SWING_BUY_SCORE + floor)

    def test_a_strong_name_still_clears_a_defensive_market(self):
        verdict = factor_engine.decide(
            self._swing(95), technical=85,
            regime=self._regime(regime_engine.DEFENSIVE))

        self.assertEqual(verdict["action"], factor_engine.BUY)

    def test_the_entry_floor_never_touches_an_exit(self):
        """A raised bar must not liquidate a book on the day it is raised."""

        for mode in (regime_engine.NORMAL, regime_engine.DEFENSIVE,
                     regime_engine.SELECTIVE):
            verdict = factor_engine.decide(
                self._swing(config.SWING_EXIT_SCORE + 1), technical=85,
                regime=self._regime(mode), held=True)

            self.assertEqual(verdict["action"], factor_engine.HOLD, mode)

    def test_conviction_is_measured_against_the_bar_actually_used(self):
        weak = factor_engine.decide(
            self._swing(90), technical=85,
            regime=self._regime(regime_engine.DEFENSIVE))

        calm = factor_engine.decide(
            self._swing(90), technical=85,
            regime=self._regime(regime_engine.NORMAL))

        self.assertEqual(weak["action"], factor_engine.BUY)
        self.assertLess(weak["conviction"], calm["conviction"])

    def test_conviction_only_exists_for_a_buy(self):
        for score, action in ((99, factor_engine.BUY),
                              (55, factor_engine.HOLD),
                              (10, factor_engine.AVOID)):
            verdict = factor_engine.decide(
                self._swing(score), technical=80, regime=self._regime())
            self.assertEqual(verdict["action"], action)
            if action != factor_engine.BUY:
                self.assertIsNone(verdict["conviction"])


class EvaluateSymbolTest(unittest.TestCase):
    """The end-to-end assembly, on a synthetic frame."""

    def _frame(self, bars=250, start=1000.0, end=1600.0):
        import numpy as np
        import pandas as pd

        from indicators import calculate_indicators

        closes = np.linspace(start, end, bars)

        frame = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=bars, freq="D"),
            "open": closes * 0.995,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": [1_000_000] * bars,
            "value": [config.MIN_DAILY_VALUE * 6] * bars,
        })

        return calculate_indicators(frame)

    def test_a_rising_name_in_a_bull_market_produces_a_signal(self):
        result = factor_engine.evaluate_symbol(
            frame=self._frame(),
            regime={"available": True, "score": 85,
                    "trading_mode": regime_engine.AGGRESSIVE,
                    "mode_label": "تهاجمی"},
        )

        self.assertEqual(result["swing"]["verdict"], factor_engine.SCORED)
        self.assertIn(result["decision"]["action"], factor_engine.ACTIONS)
        self.assertIsNotNone(result["readings"]["momentum"]["score"])
        self.assertIsNotNone(result["readings"]["liquidity"]["score"])

    def test_the_catalyst_factor_is_absent_without_a_news_provider(self):
        result = factor_engine.evaluate_symbol(
            frame=self._frame(),
            regime={"available": True, "score": 60,
                    "trading_mode": regime_engine.NORMAL},
        )

        self.assertIsNone(result["swing"]["factors"][factor_engine.CATALYST])
        self.assertLess(result["swing"]["coverage"], 1.0)

    def test_a_supplied_technical_score_is_not_recomputed(self):
        result = factor_engine.evaluate_symbol(
            frame=self._frame(), technical=42,
            regime={"available": True, "score": 60,
                    "trading_mode": regime_engine.NORMAL},
        )

        self.assertEqual(result["swing"]["factors"][factor_engine.TECHNICAL],
                         42.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
