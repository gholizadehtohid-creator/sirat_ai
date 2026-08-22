"""Tests for the six stage gates.

Every panel here is synthetic and built with a known answer, so a gate that
passes when it should fail is visible immediately. Nothing touches the market
database.

The tests are organised around the defects this repository actually produced:
a forward return used as a feature, `shift(-h)` reaching across a suspension,
a fill rate that was assumed rather than measured, a slippage charge of 0.0%,
and a signal reported at the horizon where it looked best rather than the one
it was traded at.
"""

import math
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sirat.experiments import gates, stages


DATES = pd.date_range("2020-01-01", periods=40, freq="7D")

NAMES = 80


THRESHOLDS = {
    # stage 1
    "max_missing_pct": 5.0,
    "min_names_per_date": 50,
    "max_stale_pct": 30.0,
    # stage 2
    "min_ic": 0.02,
    "min_icir": 0.30,
    "min_pct_positive_ic": 55.0,
    "min_decay_retention": 0.50,
    # stage 3
    "max_queue_ratio": 1.50,
    "min_fill_rate_pct": 90.0,
    "max_participation_pct": 10.0,
    "slippage_pct": 0.10,
    "min_liquid_share": 0.70,
    # stage 4
    "min_positive_fold_share": 0.60,
    "max_oos_degradation": 0.50,
    # stage 5
    "min_control_percentile": 95.0,
    # stage 6
    "min_paper_sessions": 60,
}


def panel(signal_strength=0.6, horizons=(1, 5, 10, 20), seed=7,
          lock_rate=0.02, top_lock_rate=None, unbuyable_rate=0.02,
          decay=True):
    """A panel whose signal strength is known because it was put there.

    `signal_strength` is the weight on the score in the forward return, so 0.0
    is pure noise and 0.6 is a signal no real market has ever produced. `decay`
    shrinks that weight with the horizon, which is the shape E13 measured.
    """

    rng = np.random.default_rng(seed)

    rows = []

    for date in DATES:
        scores = rng.uniform(0, 100, NAMES)
        for index in range(NAMES):
            row = {"date": date, "ins_code": "S%03d" % index,
                   "score": scores[index],
                   "stale": bool(rng.random() < 0.05),
                   "adv": float(rng.lognormal(20, 1.5))}

            centred = (scores[index] - 50.0) / 50.0

            for horizon in horizons:
                weight = (signal_strength / math.sqrt(horizon) if decay
                          else signal_strength)
                row["fwd_%d" % horizon] = float(
                    weight * centred * math.sqrt(horizon)
                    + rng.normal(0, 3.0) * math.sqrt(horizon))

            top = scores[index] >= np.percentile(scores, 90)

            rate = (top_lock_rate if (top and top_lock_rate is not None)
                    else lock_rate)

            row["next_locked_up"] = bool(rng.random() < rate)
            row["next_unbuyable"] = bool(row["next_locked_up"]
                                         or rng.random() < unbuyable_rate)

            rows.append(row)

    return pd.DataFrame(rows)


# ==============================
# STAGE 1
# ==============================

class DataIntegrity(unittest.TestCase):

    def test_a_clean_panel_passes(self):
        result = gates.data_integrity(panel(), THRESHOLDS,
                                      universe_snapshot="u20260818-abcd1234")

        self.assertTrue(result.passed, result.failed_checks)

    def test_a_forward_return_used_as_the_score_is_a_leak(self):
        frame = panel()
        frame["fwd_1_copy"] = frame["fwd_1"]

        result = gates.data_integrity(
            frame, THRESHOLDS, universe_snapshot="u1",
            score="fwd_5")

        self.assertFalse(result.metrics["point_in_time"])
        self.assertTrue(result.detail["score_is_forward_return"])

    def test_row_shift_contamination_is_caught(self):
        """The defect that produced a -579% top-decile excess.

        `groupby.shift(-20)` moves twenty ROWS. Across a suspension that reaches
        a bar months later, and the resulting 'return' is impossible for twenty
        sessions of a 12% band.
        """

        frame = panel()
        frame.loc[frame.index[:5], "fwd_20"] = 4000.0

        result = gates.data_integrity(frame, THRESHOLDS, universe_snapshot="u1")

        self.assertFalse(result.metrics["point_in_time"])
        self.assertEqual(result.detail["impossible_forward_returns"], 5)

    def test_rows_outside_the_declared_window_fail(self):
        result = gates.data_integrity(
            panel(), THRESHOLDS, universe_snapshot="u1",
            window=("2020-01-01", "2020-03-01"))

        self.assertFalse(result.metrics["point_in_time"])
        self.assertGreater(result.detail["rows_outside_window"], 0)

    def test_no_universe_snapshot_fails_survivorship(self):
        """The one honest survivorship check available on this data.

        Nothing in a panel can reveal a company that was delisted before the
        database was built. What can be required is that the universe used was
        frozen, so the experiment stays re-runnable.
        """

        result = gates.data_integrity(panel(), THRESHOLDS,
                                      universe_snapshot=None)

        self.assertFalse(result.metrics["survivorship"])

    def test_unavailable_is_not_a_snapshot(self):
        result = gates.data_integrity(panel(), THRESHOLDS,
                                      universe_snapshot="UNAVAILABLE")

        self.assertFalse(result.metrics["survivorship"])

    def test_thin_cross_sections_fail_universe_validity(self):
        frame = panel()
        thin = frame[(frame["date"] != DATES[0])
                     | (frame["ins_code"] < "S010")]

        result = gates.data_integrity(thin, THRESHOLDS, universe_snapshot="u1")

        self.assertFalse(result.metrics["universe_valid"])

    def test_a_missing_stale_column_is_not_a_passing_staleness_check(self):
        """Absence is not zero. 22.3% of this market's bars are stale."""

        frame = panel().drop(columns=["stale"])

        result = gates.data_integrity(frame, THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertFalse(result.metrics["missing_data"])
        self.assertIsNone(result.detail["stale_pct"])

    def test_too_much_missing_score_fails(self):
        frame = panel()
        frame.loc[frame.index[:len(frame) // 4], "score"] = np.nan

        result = gates.data_integrity(frame, THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertFalse(result.metrics["missing_data"])

    def test_an_empty_panel_raises_rather_than_failing(self):
        with self.assertRaises(gates.GateInputMissing):
            gates.data_integrity(pd.DataFrame(), THRESHOLDS)

    def test_an_unfrozen_threshold_raises(self):
        with self.assertRaises(gates.ThresholdMissing):
            gates.data_integrity(panel(), {"max_missing_pct": 5.0})


# ==============================
# STAGE 2
# ==============================

class EconomicSignal(unittest.TestCase):

    def test_a_real_signal_passes_at_its_horizon(self):
        result = gates.economic_signal(
            panel(signal_strength=1.5, decay=False), THRESHOLDS,
            horizon=20, seed=1, permutations=60)

        self.assertTrue(result.passed, result.failed_checks)

    def test_pure_noise_fails_the_gate(self):
        result = gates.economic_signal(
            panel(signal_strength=0.0), THRESHOLDS,
            horizon=20, seed=1, permutations=60)

        # The GATE fails, which is the contract. Which individual check fires
        # is not asserted: on 40 dates the standard error of a mean IC is about
        # 0.018, so any single check is a coin flip near its threshold and
        # pinning one would make this test pass or fail on the seed.
        self.assertFalse(result.passed)

        # What is not a coin flip: noise cannot produce a large IC. At 5.6
        # standard errors this bound is never reached by chance, and it would
        # be reached instantly by a leak.
        self.assertLess(abs(result.detail["mean_ic"]), 0.10)

    def test_the_random_baseline_is_calibrated_rather_than_infallible(self):
        """A 95th-percentile null lets 5% of noise through, by construction.

        Asserting that one noise panel fails the baseline would be asserting
        that a coin came up tails -- it would pass or fail on the seed, and
        someone would eventually "fix" it by choosing a friendlier one. What is
        worth testing is the RATE: over a dozen independent noise panels the
        check should fire rarely, and if it fires half the time the null is
        broken rather than unlucky.
        """

        passes = 0

        for seed in range(12):
            result = gates.economic_signal(
                panel(signal_strength=0.0, seed=100 + seed), THRESHOLDS,
                horizon=20, seed=seed + 1, permutations=60)
            passes += int(result.metrics["random_baseline"])

        # Nominal 5% of 12 is 0.6. Three or more would put the null under
        # serious suspicion; the test is deliberately loose because a tight
        # bound here would itself be flaky.
        self.assertLessEqual(passes, 3,
                             "the permutation null passed %d of 12 pure-noise "
                             "panels; it is not a 95th percentile" % passes)

    def test_a_signal_that_decays_fails_at_the_traded_horizon(self):
        """E13, as a gate.

        The panel carries a strong h=1 signal that is nearly gone by h=20.
        Measuring at h=1 and trading at h=20 is what cost this programme a year.
        """

        frame = panel(signal_strength=0.0)

        rng = np.random.default_rng(3)

        # Put the whole signal at h=1 and none of it further out.
        frame["fwd_1"] = ((frame["score"] - 50.0) / 50.0) * 3.0 \
            + rng.normal(0, 1.0, len(frame))

        result = gates.economic_signal(frame, THRESHOLDS, horizon=20, seed=1,
                                       permutations=60)

        self.assertFalse(result.metrics["factor_decay"])
        self.assertGreater(result.detail["decay_curve"][1],
                           abs(result.detail["decay_curve"][20]))

    def test_the_declared_horizon_is_the_one_scored(self):
        frame = panel(signal_strength=1.5, decay=False)

        at_one = gates.economic_signal(frame, THRESHOLDS, horizon=1, seed=1,
                                       permutations=40)
        at_twenty = gates.economic_signal(frame, THRESHOLDS, horizon=20, seed=1,
                                          permutations=40)

        self.assertEqual(at_one.detail["horizon"], 1)
        self.assertEqual(at_twenty.detail["horizon"], 20)

    def test_a_missing_horizon_column_raises(self):
        with self.assertRaises(gates.GateInputMissing):
            gates.economic_signal(panel(horizons=(1, 5)), THRESHOLDS,
                                  horizon=60, seed=1, permutations=20)

    def test_the_permutation_null_is_reproducible_from_the_seed(self):
        frame = panel(signal_strength=0.4)

        first = gates.economic_signal(frame, THRESHOLDS, horizon=5, seed=99,
                                      permutations=40)
        second = gates.economic_signal(frame, THRESHOLDS, horizon=5, seed=99,
                                       permutations=40)

        self.assertEqual(first.detail["null_p95_ic"],
                         second.detail["null_p95_ic"])


# ==============================
# STAGE 3
# ==============================

class ExecutionRealism(unittest.TestCase):

    def test_an_assumed_fill_rate_is_refused(self):
        """E2 reported a 62% CAGR on a close-fill assumption."""

        frame = panel().drop(columns=["next_unbuyable"])

        with self.assertRaises(gates.GateInputMissing):
            gates.execution_realism(frame, THRESHOLDS, horizon=20, basket=10,
                                    round_trip_pct=1.04)

    def test_an_edge_smaller_than_the_round_trip_fails(self):
        """E13's arithmetic, in seconds, before any walk-forward."""

        result = gates.execution_realism(
            panel(signal_strength=0.05), THRESHOLDS,
            horizon=1, basket=10, round_trip_pct=1.04)

        self.assertFalse(result.metrics["transaction_costs"])
        self.assertLess(result.detail["net_per_period_pct"], 0)

    def test_queue_exposure_above_the_limit_fails(self):
        """E14: a top decile that is systematically unbuyable."""

        frame = panel(lock_rate=0.02, top_lock_rate=0.20)

        result = gates.execution_realism(frame, THRESHOLDS, horizon=20,
                                         basket=10, round_trip_pct=1.04)

        self.assertFalse(result.metrics["queue_effects"])
        self.assertGreater(result.detail["queue_ratio"],
                           THRESHOLDS["max_queue_ratio"])

    def test_the_shortfall_between_requested_and_realised_is_reported(self):
        frame = panel(signal_strength=1.2, unbuyable_rate=0.30)

        result = gates.execution_realism(frame, THRESHOLDS, horizon=20,
                                         basket=10, round_trip_pct=1.04)

        self.assertGreater(result.detail["requested_excess_pct"],
                           result.detail["realised_excess_pct"])
        self.assertIsNotNone(result.detail["shortfall_share"])

    def test_slippage_is_charged_rather_than_assumed_zero(self):
        """Every result this programme produced held slippage at 0.0%."""

        frame = panel(signal_strength=1.2, decay=False)

        result = gates.execution_realism(frame, THRESHOLDS, horizon=20,
                                         basket=10, round_trip_pct=1.04)

        self.assertLess(result.detail["net_after_slippage_pct"],
                        result.detail["net_per_period_pct"])

    def test_an_unmeasured_participation_rate_is_not_a_satisfied_limit(self):
        result = gates.execution_realism(panel(), THRESHOLDS, horizon=20,
                                         basket=10, round_trip_pct=1.04)

        self.assertIsNone(result.detail["participation_pct"])
        self.assertFalse(result.metrics["participation_limits"])

    def test_a_supplied_participation_rate_is_used(self):
        frame = panel()
        frame["participation_pct"] = 2.0

        result = gates.execution_realism(frame, THRESHOLDS, horizon=20,
                                         basket=10, round_trip_pct=1.04)

        self.assertTrue(result.metrics["participation_limits"])

    def test_an_unbuyable_slot_is_not_refilled(self):
        """The rule that separates +3.17% requested from +0.59% realised."""

        frame = panel(signal_strength=1.5, decay=False, unbuyable_rate=0.0)

        clean = gates.execution_realism(frame, THRESHOLDS, horizon=20,
                                        basket=10, round_trip_pct=1.04)

        blocked = frame.copy()
        blocked["next_unbuyable"] = True

        gapped = gates.execution_realism(blocked, THRESHOLDS, horizon=20,
                                         basket=10, round_trip_pct=1.04)

        self.assertEqual(gapped.detail["fill_rate_pct"], 0.0)
        self.assertLess(gapped.detail["realised_excess_pct"],
                        clean.detail["realised_excess_pct"])


# ==============================
# STAGE 4
# ==============================

class Robustness(unittest.TestCase):

    def test_a_stable_signal_survives_a_walk_forward(self):
        result = gates.robustness(
            panel(signal_strength=1.5, decay=False), THRESHOLDS, horizon=20,
            train_dates=10, test_dates=5)

        self.assertTrue(result.metrics["walk_forward"], result.detail)

    def test_noise_fails_the_walk_forward(self):
        result = gates.robustness(panel(signal_strength=0.0, seed=11),
                                  THRESHOLDS, horizon=20, train_dates=10,
                                  test_dates=5)

        self.assertLess(result.detail["positive_fold_share"],
                        THRESHOLDS["min_positive_fold_share"])
        self.assertFalse(result.metrics["walk_forward"])

    def test_too_few_dates_for_a_walk_forward_raises(self):
        with self.assertRaises(gates.GateInputMissing):
            gates.robustness(panel(), THRESHOLDS, horizon=20,
                             train_dates=100, test_dates=50)

    def test_an_untouched_holdout_is_scored_separately(self):
        frame = panel(signal_strength=1.5, decay=False)

        result = gates.robustness(frame, THRESHOLDS, horizon=20,
                                  train_dates=10, test_dates=5,
                                  holdout=panel(signal_strength=1.5,
                                                decay=False, seed=42))

        self.assertIsNotNone(result.detail["holdout_ic"])
        self.assertTrue(result.metrics["out_of_sample"])

    def test_a_negative_holdout_fails_out_of_sample(self):
        frame = panel(signal_strength=1.5, decay=False)

        holdout = panel(signal_strength=1.5, decay=False, seed=5)
        for horizon in (1, 5, 10, 20):
            holdout["fwd_%d" % horizon] = -holdout["fwd_%d" % horizon]

        result = gates.robustness(frame, THRESHOLDS, horizon=20,
                                  train_dates=10, test_dates=5,
                                  holdout=holdout)

        self.assertLess(result.detail["holdout_ic"], 0)
        self.assertFalse(result.metrics["out_of_sample"])

    def test_backtesting_validation_output_is_consumed(self):
        """The wire that connects walk_forward() to a governed experiment.

        Those functions existed and were tested throughout Phase 1 and no
        experiment ever called one.
        """

        result = gates.robustness(
            None, THRESHOLDS, horizon=20, train_dates=0, test_dates=0,
            backtest_validation={
                "walk_forward": {"summary": {"available": True, "folds": 5,
                                             "positive_test_folds": 4,
                                             "mean_test_metric": 1.2,
                                             "parameter_stability": 0.4}},
                "out_of_sample": {"available": True,
                                  "degradation": {"drop": 0.30}},
            })

        self.assertEqual(result.detail["source"], "backtesting.validation")
        self.assertTrue(result.metrics["walk_forward"])
        self.assertTrue(result.metrics["out_of_sample"])

    def test_a_walk_forward_that_produced_nothing_raises(self):
        with self.assertRaises(gates.GateInputMissing):
            gates.robustness(
                None, THRESHOLDS, horizon=20, train_dates=0, test_dates=0,
                backtest_validation={
                    "walk_forward": {"summary": {"available": False,
                                                 "reason": "no fold"}}})


# ==============================
# STAGE 5
# ==============================

class Statistical(unittest.TestCase):

    def _arms(self, edge, seed=3, arms=30, periods=60):
        rng = np.random.default_rng(seed)
        treatment = rng.normal(edge, 1.0, periods)
        controls = [rng.normal(0.0, 1.0, periods) for _ in range(arms)]
        return treatment, controls

    def test_a_strong_treatment_sits_above_the_controls(self):
        treatment, controls = self._arms(edge=2.0)

        result = gates.statistical(treatment, controls, THRESHOLDS, seed=1,
                                   draws=400)

        self.assertTrue(result.passed, result.failed_checks)
        self.assertGreaterEqual(result.detail["percentile"], 95.0)

    def test_a_losing_treatment_fails(self):
        """E11: 3.3rd percentile, empirical p = 0.9667."""

        treatment, controls = self._arms(edge=-2.0)

        result = gates.statistical(treatment, controls, THRESHOLDS, seed=1,
                                   draws=400)

        self.assertFalse(result.metrics["monte_carlo"])
        self.assertLess(result.detail["percentile"], 10.0)

    def test_an_interval_entirely_below_zero_is_not_a_pass(self):
        """Excluding zero is not enough; the sign is part of the condition."""

        treatment, controls = self._arms(edge=-3.0)

        result = gates.statistical(treatment, controls, THRESHOLDS, seed=1,
                                   draws=400)

        self.assertTrue(result.detail["ci_excludes_zero"])
        self.assertFalse(result.metrics["bootstrap"])

    def test_no_controls_raises(self):
        with self.assertRaises(gates.GateInputMissing):
            gates.statistical(np.random.normal(0, 1, 30), [], THRESHOLDS,
                              seed=1)

    def test_too_few_controls_raises(self):
        treatment, controls = self._arms(edge=1.0, arms=3)

        with self.assertRaises(gates.GateInputMissing):
            gates.statistical(treatment, controls, THRESHOLDS, seed=1)

    def test_the_bootstrap_is_reproducible_from_the_seed(self):
        treatment, controls = self._arms(edge=1.0)

        first = gates.statistical(treatment, controls, THRESHOLDS, seed=7,
                                  draws=300)
        second = gates.statistical(treatment, controls, THRESHOLDS, seed=7,
                                   draws=300)

        self.assertEqual(first.detail["ci95"], second.detail["ci95"])


# ==============================
# STAGE 6
# ==============================

class PaperReadiness(unittest.TestCase):

    def _evidence(self, sessions=90):
        return {
            "risk_limits": {"control": "8% position cap, 25% sector cap",
                            "artifact": "portfolio_engine.from_config()"},
            "kill_switch": {"control": "halt at -8% daily NAV, PM only",
                            "artifact": "backtesting/paper.py"},
            "monitoring": {"control": "fill rate and queue exposure, daily",
                           "artifact": "backtest_output/paper_daily.json"},
            "reconciliation": {"control": "paper fills vs the tape, daily",
                               "artifact": "backtesting/paper.py"},
            "paper_sessions": sessions,
        }

    def test_evidenced_controls_pass(self):
        result = gates.paper_readiness(self._evidence(), THRESHOLDS)

        self.assertTrue(result.passed, result.failed_checks)

    def test_a_bare_true_is_not_evidence(self):
        result = gates.paper_readiness(
            {"risk_limits": True, "kill_switch": True, "monitoring": True,
             "reconciliation": True, "paper_sessions": 90}, THRESHOLDS)

        self.assertFalse(result.passed)
        self.assertEqual(len(result.failed_checks), 4)

    def test_a_control_without_an_artifact_fails(self):
        evidence = self._evidence()
        evidence["kill_switch"] = {"control": "we would just stop",
                                   "artifact": ""}

        result = gates.paper_readiness(evidence, THRESHOLDS)

        self.assertFalse(result.metrics["kill_switch"])

    def test_too_few_paper_sessions_fails(self):
        result = gates.paper_readiness(self._evidence(sessions=5), THRESHOLDS)

        self.assertFalse(result.metrics["monitoring"])

    def test_no_evidence_at_all_fails_every_check(self):
        result = gates.paper_readiness({}, THRESHOLDS)

        self.assertEqual(sorted(result.failed_checks),
                         sorted(stages.REQUIRED_CHECKS[stages.PAPER_READINESS]))


# ==============================
# THE CONTRACT WITH THE MANAGER
# ==============================

class GateContract(unittest.TestCase):

    def test_every_gate_reports_exactly_its_required_checks(self):
        """A gate does not get to extend or shorten the stage definition."""

        produced = {
            stages.DATA_INTEGRITY: gates.data_integrity(
                panel(), THRESHOLDS, universe_snapshot="u1"),
            stages.ECONOMIC_SIGNAL: gates.economic_signal(
                panel(), THRESHOLDS, horizon=5, seed=1, permutations=30),
            stages.EXECUTION_REALISM: gates.execution_realism(
                panel(), THRESHOLDS, horizon=20, basket=10,
                round_trip_pct=1.04),
            stages.ROBUSTNESS: gates.robustness(
                panel(), THRESHOLDS, horizon=20, train_dates=10, test_dates=5),
            stages.PAPER_READINESS: gates.paper_readiness({}, THRESHOLDS),
        }

        for stage, result in produced.items():
            self.assertEqual(sorted(result.metrics),
                             sorted(stages.REQUIRED_CHECKS[stage]),
                             "stage %d reported the wrong check set" % stage)

    def test_every_metric_is_a_real_boolean(self):
        result = gates.data_integrity(panel(), THRESHOLDS,
                                      universe_snapshot="u1")

        for key, value in result.metrics.items():
            self.assertIsInstance(value, bool, key)

    def test_a_gate_result_is_accepted_by_run_stage(self):
        import tempfile

        from tests.test_experiment_manager import register

        path = os.path.join(tempfile.mkdtemp(prefix="sirat_gate_"), "r.db")

        handle = register(path)

        result = gates.data_integrity(panel(), THRESHOLDS,
                                      universe_snapshot="u1")

        recorded = handle.run_stage(stages.DATA_INTEGRITY, result)

        self.assertEqual(recorded["status"], stages.PASS)
        self.assertIn("rows", handle.record()["stages"][0]["metrics"]["detail"])


if __name__ == "__main__":
    unittest.main()


class PriceSanity(unittest.TestCase):
    """The check that was missing when E22 measured a contaminated panel.

    252 rows in price_history carry returns above 200% -- the largest
    +19,976,600%, which is a prior close of 1 rial. Every other Stage 1 check
    passed that panel, because the other four ask whether it is ALIGNED,
    RESTORABLE, WIDE and POPULATED, and none asks whether a number in it is
    possible.
    """

    def _with_prices(self, closes):
        """A panel carrying a raw price series, as E21's did."""

        rows = []

        for index, date in enumerate(DATES):
            for name in range(NAMES):
                rows.append({"date": date, "ins_code": "S%03d" % name,
                             "score": float(name),
                             "close": closes[index % len(closes)],
                             "fwd_1": 0.5, "adv": 1e9})

        return pd.DataFrame(rows)

    def test_a_clean_price_series_passes(self):
        frame = self._with_prices([100.0, 101.0, 102.0, 101.5])

        result = gates.data_integrity(frame, THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertTrue(result.metrics["price_sanity"])

    def test_a_placeholder_price_fails(self):
        """The exact E22 signature: a prior close of 1 rial."""

        frame = self._with_prices([100.0, 1.0, 199767.0, 101.0])

        result = gates.data_integrity(frame, THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertFalse(result.metrics["price_sanity"])
        self.assertGreater(
            result.detail["price_evidence"]["impossible_sessions"], 0)

    def test_a_legitimate_reopening_does_not_fail(self):
        """7,388 real bars clear the 12% band. Failing those switches the
        check off within a week, which is worse than not having it."""

        frame = self._with_prices([100.0, 130.0, 128.0, 131.0])

        result = gates.data_integrity(frame, THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertTrue(result.metrics["price_sanity"])
        self.assertGreater(
            result.detail["price_evidence"]["extreme_sessions"], 0)
        self.assertEqual(
            result.detail["price_evidence"]["impossible_sessions"], 0)

    def test_the_two_tiers_are_counted_separately(self):
        frame = self._with_prices([100.0, 130.0, 1.0, 500.0])

        evidence = gates.data_integrity(
            frame, THRESHOLDS, universe_snapshot="u1"
        ).detail["price_evidence"]

        self.assertGreater(evidence["extreme_sessions"],
                           evidence["impossible_sessions"])
        self.assertGreater(evidence["impossible_sessions"], 0)

    def test_a_panel_showing_no_return_evidence_cannot_be_certified(self):
        """Silence is not a certificate -- the platform's own rule, applied
        to the one input nobody was checking."""

        frame = pd.DataFrame([{"date": d, "ins_code": "S%03d" % n,
                               "score": float(n), "adv": 1e9}
                              for d in DATES for n in range(NAMES)])

        result = gates.data_integrity(frame, THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertFalse(result.metrics["price_sanity"])
        self.assertFalse(result.detail["price_evidence"]["evidence_seen"])

    def test_a_forward_only_panel_is_still_certifiable(self):
        """The synthetic factor panels carry fwd_* and no close. They are
        evidence, so they are checked rather than refused."""

        result = gates.data_integrity(panel(), THRESHOLDS,
                                      universe_snapshot="u1")

        self.assertTrue(result.metrics["price_sanity"])

    def test_the_impossible_tier_sits_far_above_the_band(self):
        """Not a tuned number: no legitimate session doubles a price."""

        self.assertGreater(gates.IMPOSSIBLE_SESSION_MOVE_PCT,
                           gates.MAX_SESSION_MOVE_PCT * 5)
