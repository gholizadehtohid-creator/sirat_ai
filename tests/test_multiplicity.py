"""The multiple-testing arithmetic, against the numbers the literature and the
research report state."""

import math
import os
import sys
import unittest

import tests  # noqa: F401  (live stores read-only)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments import multiplicity as m                  # noqa: E402


class Arithmetic(unittest.TestCase):

    def test_inverse_normal(self):
        self.assertAlmostEqual(m.norm_ppf(0.975), 1.959964, places=5)
        self.assertAlmostEqual(m.norm_ppf(0.5), 0.0, places=9)
        self.assertAlmostEqual(m.norm_ppf(0.001), -3.090232, places=5)

    def test_expected_best_of_25_is_about_two(self):
        # The report: with N = 25 the expected best standardised Sharpe is
        # about 2.0, i.e. 0.87 a year on 5.3 years and 0.52 on 15.
        self.assertAlmostEqual(m.expected_max_z(25), 1.997, places=2)
        self.assertAlmostEqual(m.luck_hurdle(25, 5.3), 0.87, places=2)
        self.assertAlmostEqual(m.luck_hurdle(25, 15), 0.52, places=2)
        self.assertEqual(m.expected_max_z(1), 0.0)

    def test_bailey_et_al_trial_budgets(self):
        # Bailey et al. 2014: on five years no more than ~45 independent
        # configurations keep the expected best annual Sharpe under 1; on two
        # years about seven reach it.
        self.assertLess(m.luck_hurdle(45, 5), 1.0 + 1e-3)
        self.assertGreater(m.luck_hurdle(46, 5), 1.0 - 0.01)
        self.assertAlmostEqual(m.luck_hurdle(7, 2), 0.98, places=1)

    def test_psr_is_one_half_at_its_own_benchmark(self):
        self.assertAlmostEqual(m.psr(0.2, 100, sr_star=0.2), 0.5)

    def test_psr_normal_case_matches_the_closed_form(self):
        sr, n = 0.25, 120
        expected = m.norm_cdf(sr * math.sqrt(n - 1) / math.sqrt(1 + sr * sr / 2))
        self.assertAlmostEqual(m.psr(sr, n), expected, places=12)

    def test_fat_tails_and_negative_skew_lower_psr(self):
        base = m.psr(0.25, 120)
        self.assertLess(m.psr(0.25, 120, skew=-1.0, kurtosis=6.0), base)

    def test_dsr_is_psr_at_the_deflated_benchmark(self):
        sr, n, trials = 0.27, 106, 25
        sr0 = m.expected_max_z(trials) / math.sqrt(n)
        self.assertAlmostEqual(m.dsr(sr, n, trials), m.psr(sr, n, sr0))
        self.assertLess(m.dsr(sr, n, trials), m.psr(sr, n, 0.0))

    def test_holm_step_down_and_missing_p(self):
        out = m.holm({"a": 0.001, "b": 0.03, "c": None})
        self.assertTrue(out["a"]["rejected"])          # 0.001 <= 0.05/3
        self.assertFalse(out["b"]["rejected"])         # 0.03 > 0.05/2
        self.assertFalse(out["c"]["rejected"])
        self.assertAlmostEqual(out["a"]["threshold"], 0.05 / 3, places=6)

    def test_benjamini_hochberg(self):
        out = m.benjamini_hochberg({"a": 0.01, "b": 0.03, "c": 0.04, "d": 0.5})
        self.assertTrue(out["a"]["rejected"])
        self.assertFalse(out["d"]["rejected"])


class Budgets(unittest.TestCase):

    def test_window_budget_counts_recorded_and_upper_bound(self):
        use = {2021: ["E30"], 2022: ["E30", "E31"], 2023: ["E32"]}
        out = m.window_budget("2021-06-01", "2023-12-31", use=use, n_all=25)
        self.assertEqual(out["trials_recorded"], 3)
        self.assertEqual(out["trials_upper_bound"], 25)
        self.assertGreater(out["luck_hurdle_next_annual_sharpe"], 1.0)
        self.assertTrue(out["exhausted"])

    def test_a_long_fresh_window_is_not_exhausted(self):
        out = m.window_budget("2009-01-01", "2026-08-18", use={}, n_all=3)
        self.assertFalse(out["exhausted"])


class Completeness(unittest.TestCase):

    def test_a_bare_file_lacks_all_five(self):
        self.assertEqual(len(m.prereg_completeness({"question": "x"})), 5)

    def test_a_complete_file(self):
        doc = {"test": {"family": "allocation; Holm at 0.05"},
               "data": {"window": "2017..2026", "benchmark": "TEDPIX"},
               "costs": "0.75% round trip", "decision_rule": "accept if ..."}
        self.assertEqual(m.prereg_completeness(doc), [])


class Readings(unittest.TestCase):

    def test_three_readings_on_fixed_rows(self):
        rows = [
            {"short": "E27", "counts_in_n": True, "pvalues": {"H1": 0.007},
             "own_alpha": 0.025, "own_rule": "Bonferroni"},
            {"short": "E28", "counts_in_n": True,
             "pvalues": {"H1": 0.015, "H2": 0.0002},
             "own_alpha": 0.025, "own_rule": "Bonferroni"},
            {"short": "E30", "counts_in_n": True, "pvalues": {"H1": 0.24},
             "own_alpha": 0.05, "own_rule": "one"},
            {"short": "E2", "counts_in_n": True, "pvalues": {},
             "own_alpha": None, "own_rule": None},
            {"short": "E34", "counts_in_n": False, "pvalues": {},
             "own_alpha": None, "own_rule": None},
        ]
        out = m.readings(rows)
        self.assertEqual(out["n_hypotheses"], 5)          # E34 not counted
        a = out["A_one_family"]
        self.assertTrue(a["E28 H2"]["rejected"])           # 0.0002 <= 0.01
        self.assertTrue(a["E27 H1"]["rejected"])           # 0.007 <= 0.0125
        self.assertTrue(a["E28 H1"]["rejected"])           # 0.015 <= 0.05/3
        self.assertFalse(a["E30 H1"]["rejected"])          # 0.24 > 0.05/2
        b = out["B_as_preregistered"]
        self.assertTrue(b["E27 H1"]["rejected"] and b["E28 H1"]["rejected"])
        self.assertIn("allocation", out["C_declared_families"])


if __name__ == "__main__":
    unittest.main()
