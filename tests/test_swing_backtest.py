"""Tests for the Swing Score study: bucketing, the fundamental-filter comparison,
the signal book's drawdown and win rate, and the weight sweep.

Everything here runs on synthetic observations. That is deliberate and it is the
only way these functions can be checked: on real data a bucket table has one
correct answer nobody knows in advance, while on constructed data the answer is
known and a sign error or an off-by-one in the cost subtraction is visible.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import factor_engine
import swing_backtest


def observation(date, swing, forward, fundamental=None, technical=None,
                factors=None):
    """One synthetic study row."""

    return {
        "date": date,
        "ins_code": "X",
        "symbol": "X",
        "sector": "?",
        "swing": swing,
        "forward_pct": forward,
        "fundamental": fundamental,
        "technical": technical,
        "factors": factors or {},
    }


class StatsTest(unittest.TestCase):

    def test_an_empty_band_reports_nothing_rather_than_zero(self):
        row = swing_backtest._stats("band", [])

        self.assertEqual(row["n"], 0)
        self.assertIsNone(row["mean"])
        self.assertIsNone(row["win_rate"])
        self.assertIsNone(row["profit_factor"])

    def test_the_win_rate_counts_strictly_positive_outcomes(self):
        row = swing_backtest._stats("band", [5.0, 0.0, -5.0, 10.0])

        self.assertEqual(row["win_rate"], 50.0)

    def test_the_profit_factor_is_gains_over_losses(self):
        row = swing_backtest._stats("band", [10.0, 10.0, -5.0])

        self.assertAlmostEqual(row["profit_factor"], 4.0)

    def test_an_all_winning_band_has_an_infinite_profit_factor(self):
        row = swing_backtest._stats("band", [3.0, 4.0])

        self.assertEqual(row["profit_factor"], float("inf"))

    def test_the_median_is_not_moved_by_one_outlier(self):
        row = swing_backtest._stats("band", [1.0, 2.0, 3.0, 400.0])

        self.assertEqual(row["median"], 2.5)
        self.assertGreater(row["mean"], 100)

    def test_worst_and_best_bracket_the_band(self):
        row = swing_backtest._stats("band", [-8.0, 2.0, 15.0])

        self.assertEqual(row["worst"], -8.0)
        self.assertEqual(row["best"], 15.0)


class MonotonicTest(unittest.TestCase):

    def test_a_rising_sequence_is_monotonic(self):
        self.assertTrue(swing_backtest._monotonic([1.0, 2.0, 5.0]))

    def test_a_dip_breaks_it(self):
        self.assertFalse(swing_backtest._monotonic([1.0, 5.0, 2.0]))

    def test_too_few_bands_is_unanswerable_rather_than_true(self):
        self.assertIsNone(swing_backtest._monotonic([1.0, 2.0]))
        self.assertIsNone(swing_backtest._monotonic([]))

    def test_nones_are_ignored(self):
        self.assertTrue(swing_backtest._monotonic([1.0, None, 2.0, 3.0]))


class BucketTest(unittest.TestCase):

    def test_costs_are_subtracted_from_every_band(self):
        rows = [observation("d", 75, 5.0)] * 10

        gross = swing_backtest.buckets(rows, cost_pct=0.0)
        net = swing_backtest.buckets(rows)

        band = next(r for r in gross["rows"] if r["n"])
        net_band = next(r for r in net["rows"] if r["n"])

        self.assertAlmostEqual(band["mean"] - net_band["mean"],
                               config.TRANSACTION_COST_PCT, places=2)

    def test_a_signal_that_sorts_by_outcome_is_reported_monotonic(self):
        rows = []

        for score, forward in ((30, -5.0), (45, -1.0), (55, 2.0),
                               (65, 5.0), (75, 9.0), (90, 14.0)):
            rows.extend([observation("d", score, forward)] * 40)

        table = swing_backtest.buckets(rows, cost_pct=0.0)

        self.assertTrue(table["monotonic"])

    def test_a_signal_that_does_not_sort_is_reported_as_such(self):
        """The technical score's real behaviour, reproduced synthetically."""

        rows = []

        for score, forward in ((30, 1.0), (45, 2.0), (55, 3.0),
                               (65, 8.0), (75, 5.0), (90, 6.0)):
            rows.extend([observation("d", score, forward)] * 40)

        table = swing_backtest.buckets(rows, cost_pct=0.0)

        self.assertFalse(table["monotonic"])

    def test_thin_bands_are_excluded_from_the_monotonicity_call(self):
        """A band of three must not decide whether the score ranks."""

        rows = [observation("d", 75, 9.0)] * 40
        rows.extend([observation("d", 90, -20.0)] * 3)
        rows.extend([observation("d", 55, 2.0)] * 40)
        rows.extend([observation("d", 65, 5.0)] * 40)

        table = swing_backtest.buckets(rows, cost_pct=0.0)

        self.assertTrue(table["monotonic"],
                        "the 3-observation band must not break the verdict")

    def test_unscored_observations_are_counted_not_bucketed(self):
        rows = [observation("d", None, 5.0)] * 7 + [observation("d", 70, 5.0)]

        table = swing_backtest.buckets(rows)

        self.assertEqual(table["unscored"], 7)
        self.assertEqual(sum(r["n"] for r in table["rows"]), 1)

    def test_a_missing_forward_return_is_dropped(self):
        rows = [observation("d", 70, None), observation("d", 70, 5.0)]

        table = swing_backtest.buckets(rows, cost_pct=0.0)

        self.assertEqual(sum(r["n"] for r in table["rows"]), 1)

    def test_the_bands_align_with_the_action_thresholds(self):
        self.assertIn(config.SWING_BUY_SCORE, swing_backtest.DEFAULT_EDGES)

    def test_a_perfect_score_is_inside_the_top_band(self):
        table = swing_backtest.buckets([observation("d", 100, 5.0)] * 5,
                                       cost_pct=0.0)

        self.assertEqual(table["rows"][-1]["n"], 5)


class FilterComparisonTest(unittest.TestCase):

    def test_a_filter_that_separates_outcomes_is_reported_as_such(self):
        rows = ([observation("d", 70, 12.0, fundamental=80)] * 50
                + [observation("d", 70, -4.0, fundamental=20)] * 50)

        result = swing_backtest.filter_comparison(rows, cost_pct=0.0)

        self.assertIn("SEPARATES", result["verdict"])
        self.assertGreater(result["passed"]["mean"], result["failed"]["mean"])

    def test_a_filter_with_no_edge_says_no_edge(self):
        rows = ([observation("d", 70, 4.0, fundamental=80)] * 50
                + [observation("d", 70, 6.0, fundamental=20)] * 50)

        result = swing_backtest.filter_comparison(rows, cost_pct=0.0)

        self.assertIn("NO EDGE", result["verdict"])

    def test_uncovered_names_are_a_third_population_not_a_failure(self):
        """The distinction that decides whether the filter or coverage is working."""

        rows = ([observation("d", 70, 10.0, fundamental=80)] * 40
                + [observation("d", 70, -2.0, fundamental=20)] * 40
                + [observation("d", 70, 3.0, fundamental=None)] * 60)

        result = swing_backtest.filter_comparison(rows, cost_pct=0.0)

        self.assertEqual(result["no_reading"]["n"], 60)
        self.assertEqual(result["with_reading"]["n"], 80)
        self.assertAlmostEqual(result["coverage"], 80 / 140.0, places=3)

    def test_the_coverage_caveat_fires_when_uncovered_names_did_better(self):
        rows = ([observation("d", 70, 6.0, fundamental=80)] * 40
                + [observation("d", 70, 1.0, fundamental=20)] * 40
                + [observation("d", 70, 20.0, fundamental=None)] * 40)

        result = swing_backtest.filter_comparison(rows, cost_pct=0.0)

        self.assertIn("SEPARATES", result["verdict"])
        self.assertIn("CAVEAT", result["verdict"])
        self.assertIn("Codal coverage", result["verdict"])

    def test_too_few_observations_refuses_a_verdict(self):
        rows = ([observation("d", 70, 10.0, fundamental=80)] * 5
                + [observation("d", 70, -2.0, fundamental=20)] * 5)

        result = swing_backtest.filter_comparison(rows, cost_pct=0.0)

        self.assertIn("INSUFFICIENT", result["verdict"])

    def test_the_floor_is_the_projects_own_approval_score(self):
        result = swing_backtest.filter_comparison([], cost_pct=0.0)

        self.assertEqual(result["floor"], config.FUND_APPROVE_SCORE)


class SignalBookTest(unittest.TestCase):

    def test_only_signals_above_the_score_enter_the_book(self):
        rows = [observation("2026-01-01", 30, 50.0),
                observation("2026-01-01", 90, 5.0)]

        book = swing_backtest.signal_book(rows, min_score=80, cost_pct=0.0)

        self.assertEqual(book["signals"], 1)
        self.assertAlmostEqual(book["total_return_pct"], 5.0, places=2)

    def test_signals_on_one_date_are_equally_weighted(self):
        rows = [observation("2026-01-01", 90, 10.0),
                observation("2026-01-01", 90, 0.0)]

        book = swing_backtest.signal_book(rows, min_score=80, cost_pct=0.0)

        self.assertEqual(book["periods"], 1)
        self.assertAlmostEqual(book["curve"][0]["return_pct"], 5.0, places=3)

    def test_periods_compound(self):
        rows = [observation("2026-01-01", 90, 10.0),
                observation("2026-02-01", 90, 10.0)]

        book = swing_backtest.signal_book(rows, min_score=80, cost_pct=0.0)

        self.assertAlmostEqual(book["total_return_pct"], 21.0, places=2)

    def test_the_drawdown_is_measured_from_the_running_peak(self):
        rows = [observation("2026-01-01", 90, 20.0),
                observation("2026-02-01", 90, -50.0),
                observation("2026-03-01", 90, 10.0)]

        book = swing_backtest.signal_book(rows, min_score=80, cost_pct=0.0)

        self.assertAlmostEqual(book["max_drawdown_pct"], -50.0, places=1)

    def test_a_monotonically_rising_book_has_no_drawdown(self):
        rows = [observation("2026-0%d-01" % m, 90, 3.0) for m in range(1, 6)]

        book = swing_backtest.signal_book(rows, min_score=80, cost_pct=0.0)

        self.assertEqual(book["max_drawdown_pct"], 0.0)

    def test_the_win_rate_is_per_period_not_per_signal(self):
        rows = [observation("2026-01-01", 90, 10.0),
                observation("2026-01-01", 90, -2.0),   # same period, still +
                observation("2026-02-01", 90, -5.0)]

        book = swing_backtest.signal_book(rows, min_score=80, cost_pct=0.0)

        self.assertEqual(book["periods"], 2)
        self.assertEqual(book["win_rate"], 50.0)

    def test_no_qualifying_signal_is_reported_rather_than_crashing(self):
        book = swing_backtest.signal_book(
            [observation("2026-01-01", 10, 5.0)], min_score=80)

        self.assertEqual(book["periods"], 0)
        self.assertIsNone(book["max_drawdown_pct"])
        self.assertIn("no signal", book["reason"])

    def test_the_book_states_that_it_is_not_a_portfolio(self):
        book = swing_backtest.signal_book(
            [observation("2026-01-01", 90, 5.0)], min_score=80)

        self.assertIn("سبد نیست", book["caveat"])


class WeightSensitivityTest(unittest.TestCase):

    def _factored(self, date, forward, **factors):
        row = observation(date, None, forward)
        row["factors"] = {
            factor_engine.REGIME: factors.get("regime"),
            factor_engine.MOMENTUM: factors.get("momentum"),
            factor_engine.TECHNICAL: factors.get("technical"),
            factor_engine.FUNDAMENTAL: factors.get("fundamental"),
            factor_engine.LIQUIDITY: factors.get("liquidity"),
            factor_engine.CATALYST: factors.get("catalyst"),
        }
        return row

    def _population(self):
        """Momentum carries the information; technical points the other way.

        The two are set in opposition on purpose. If the weak names simply
        scored lower on every factor, dropping momentum would leave both sides
        below the buy band and the variant would select nothing -- which proves
        nothing. Here removing momentum lets the technical reading select the
        losers, which is the failure the sweep exists to expose.
        """

        rows = []

        for i in range(200):
            strong = i % 2 == 0
            rows.append(self._factored(
                "2026-01-%02d" % (i % 28 + 1),
                12.0 if strong else -6.0,
                regime=70,
                momentum=95 if strong else 15,
                technical=70 if strong else 95,
                liquidity=80, fundamental=60))

        return rows

    def test_every_variant_is_rescored_from_the_stored_factors(self):
        result = swing_backtest.weight_sensitivity(self._population(),
                                                   cost_pct=0.0)

        self.assertGreater(len(result["rows"]), 5)
        self.assertTrue(all(row["selected"] >= 0 for row in result["rows"]))

    def test_turning_off_the_informative_factor_hurts_the_selection(self):
        result = swing_backtest.weight_sensitivity(self._population(),
                                                   cost_pct=0.0)

        rows = {row["variant"]: row for row in result["rows"]}

        self.assertIn("momentum off", rows)

        baseline = rows["baseline"]["selected_mean"]
        without = rows["momentum off"]["selected_mean"]

        self.assertIsNotNone(baseline)

        if without is not None:
            self.assertGreater(baseline, without,
                               "removing the only informative factor must not "
                               "improve the selection")

    def test_the_default_grid_moves_one_factor_at_a_time(self):
        variants = swing_backtest.default_variants()

        base = factor_engine.default_weights()

        for label, weights in variants:

            if label in ("baseline", "equal weights"):
                continue

            changed = [name for name in factor_engine.FACTORS
                       if abs(weights[name] - base[name]) > 1e-9]

            self.assertEqual(len(changed), 1,
                             "%s changed %d factors" % (label, len(changed)))

    def test_every_factor_appears_in_the_grid(self):
        labels = [label for label, _ in swing_backtest.default_variants()]

        for factor in factor_engine.FACTORS:
            self.assertTrue(any(label.startswith(factor) for label in labels),
                            factor)

    def test_a_wildly_weight_dependent_result_is_called_fragile(self):
        rows = []

        # Momentum and technical point in opposite directions and the remaining
        # factors are high enough that the baseline clears the buy band, so the
        # weights alone decide which half of the population gets selected.
        for i in range(200):
            momentum_strong = i % 2 == 0
            rows.append(self._factored(
                "2026-01-%02d" % (i % 28 + 1),
                30.0 if momentum_strong else -30.0,
                regime=85, momentum=95 if momentum_strong else 5,
                technical=5 if momentum_strong else 95,
                liquidity=95, fundamental=80))

        result = swing_backtest.weight_sensitivity(rows, cost_pct=0.0)

        self.assertIn(result["verdict"].split(":")[0],
                      ("FRAGILE", "MISCALIBRATED"))

    def test_no_observation_refuses_a_verdict(self):
        result = swing_backtest.weight_sensitivity([], cost_pct=0.0)

        self.assertIn("INSUFFICIENT", result["verdict"])
        self.assertIsNone(result["spread_pct"])


class ReportTest(unittest.TestCase):
    """The printer must not raise on any shape the study can produce."""

    def test_printing_a_full_result_does_not_raise(self):
        import io
        from contextlib import redirect_stdout

        rows = []

        for score, forward in ((30, -5.0), (45, -1.0), (55, 2.0),
                               (65, 5.0), (75, 9.0), (90, 14.0)):
            for i in range(40):
                row = observation("2026-01-%02d" % (i % 28 + 1), score, forward,
                                  fundamental=score, technical=score)
                row["factors"] = {
                    factor_engine.REGIME: 60,
                    factor_engine.MOMENTUM: score,
                    factor_engine.TECHNICAL: score,
                    factor_engine.FUNDAMENTAL: score,
                    factor_engine.LIQUIDITY: 70,
                    factor_engine.CATALYST: None,
                }
                rows.append(row)

        out = {
            "observations": rows,
            "dates": ["2026-01-01"],
            "symbols": 10,
            "skipped": 0,
            "hold_sessions": 20,
            "used_regime": True,
            "swing_buckets": swing_backtest.buckets(rows, key="swing"),
            "technical_buckets": swing_backtest.buckets(rows, key="technical"),
            "momentum_buckets": swing_backtest.buckets(rows, key="momentum"),
            "filter": swing_backtest.filter_comparison(rows),
            "book": swing_backtest.signal_book(rows),
            "sensitivity": swing_backtest.weight_sensitivity(rows),
        }

        buffer = io.StringIO()

        with redirect_stdout(buffer):
            swing_backtest.print_report(out)

        text = buffer.getvalue()

        self.assertIn("SWING SCORE", text)
        self.assertIn("FUNDAMENTAL FILTER", text)
        self.assertIn("DRAWDOWN", text)
        self.assertIn("WEIGHT SENSITIVITY", text)

    def test_printing_an_empty_book_does_not_raise(self):
        import io
        from contextlib import redirect_stdout

        rows = [observation("2026-01-01", 10, 1.0)]

        out = {
            "observations": rows, "dates": ["2026-01-01"], "symbols": 1,
            "skipped": 0, "hold_sessions": 20, "used_regime": False,
            "swing_buckets": swing_backtest.buckets(rows, key="swing"),
            "technical_buckets": swing_backtest.buckets(rows, key="technical"),
            "momentum_buckets": swing_backtest.buckets(rows, key="momentum"),
            "filter": swing_backtest.filter_comparison(rows),
            "book": swing_backtest.signal_book(rows),
            "sensitivity": swing_backtest.weight_sensitivity(rows),
        }

        buffer = io.StringIO()

        with redirect_stdout(buffer):
            swing_backtest.print_report(out)

        self.assertIn("regime factor was DISABLED", buffer.getvalue())


class UnfillableEntryTest(unittest.TestCase):
    """The forward return starts where a buy could actually have filled."""

    def test_a_buy_queue_session_is_unfillable(self):
        # Opened, traded and closed at +3% with no range: a صف خرید.
        self.assertTrue(swing_backtest._unfillable(
            1, 100.0, [100.0, 103.0], [100.0, 103.0], [100.0, 103.0],
            [1000.0, 5000.0]))

    def test_a_halted_session_is_unfillable(self):
        self.assertTrue(swing_backtest._unfillable(
            1, 100.0, [100.0, 100.0], [100.0, 100.0], [100.0, 100.0],
            [1000.0, 0.0]))

    def test_an_ordinary_session_fills(self):
        self.assertFalse(swing_backtest._unfillable(
            1, 100.0, [100.0, 101.0], [100.0, 103.0], [100.0, 99.5],
            [1000.0, 5000.0]))

    def test_a_flat_untraded_looking_but_unchanged_session_fills(self):
        """No range at an unchanged price is not a queue."""

        self.assertFalse(swing_backtest._unfillable(
            1, 100.0, [100.0, 100.0], [100.0, 100.0], [100.0, 100.0],
            [1000.0, 50.0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
