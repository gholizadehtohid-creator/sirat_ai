"""outlook_calibration: the estimate measured on its own history, and the
dollar slopes."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import outlook_calibration as oc  # noqa: E402

B = 1e9


def rec(end, months, revenue, net, gross=None, cons=False):
    items = {"revenue": revenue, "net_profit": net, "capital": 100 * B}
    if gross is not None:
        items["gross_profit"] = gross
    return {"end": end, "months": months, "cons": cons, "items": items, "ratios": {}}


def company(code, years, margin=0.2, growth=0.5):
    """A Esfand-year company: 6- and 12-month statements for `years`."""

    out = {}
    revenue = 100 * B
    for y in years:
        half = revenue * 0.45
        out[(code, (y, 12), 6)] = rec("%d/06/31" % y, 6, half, half * margin)
        out[(code, (y, 12), 12)] = rec("%d/12/29" % y, 12, revenue, revenue * margin)
        revenue *= 1 + growth
    return out


class BucketTests(unittest.TestCase):

    def test_months_known_map_to_the_statement_point_at_or_below(self):
        # as many months known or fewer: five months read as three
        self.assertEqual([oc.months_bucket(m) for m in (1, 4, 5, 6, 8, 9, 11, 12)],
                         [3, 3, 3, 6, 6, 9, 9, 12])

    def test_margin_bands(self):
        self.assertEqual(oc.margin_band(0.02), "lt5")
        self.assertEqual(oc.margin_band(0.15), "15_30")
        self.assertEqual(oc.margin_band(0.5), "ge30")
        self.assertIsNone(oc.margin_band(None))


class MeasureTests(unittest.TestCase):

    def test_a_steady_company_is_estimated_exactly(self):
        # Same seasonality and margin every year: completing the year from
        # six months at the trailing margin is exact, actual / estimate = 1.
        statements = company("1", range(1400, 1405))
        points = oc.measure(statements)
        sixes = [r for m, _b, r in points if m == 6]
        self.assertTrue(sixes)
        for ratio in sixes:
            self.assertAlmostEqual(ratio, 1.0, places=6)

    def test_bases_are_never_mixed(self):
        statements = company("1", range(1400, 1404))
        statements[("1", (1401, 12), 6)]["cons"] = True
        points = oc.measure(statements)
        # 1401's six months (consolidated) and 1402's (compared with it) are
        # skipped; 1403's, all four rows separate, is measured
        self.assertEqual(len([p for p in points if p[0] == 6]), 1)

    def test_the_range_falls_back_to_the_months_bucket_when_thin(self):
        record = {"buckets": {"6": {"n": 500, "q10": .5, "q25": .8, "q50": 1, "q75": 1.2,
                                    "q90": 1.5, "median_abs_error": .2},
                              "6|lt5": {"n": 10, "q10": .1, "q25": .2, "q50": 1, "q75": 3,
                                        "q90": 5, "median_abs_error": .9}}}
        b = oc.range_for(record, 7, 0.03)
        self.assertEqual(b["key"], "6")
        self.assertIsNone(oc.range_for(None, 7, 0.03))


class SensitivityTests(unittest.TestCase):

    def test_a_clean_dollar_slope_is_recovered_and_clipped(self):
        obs = {"a": [(x, 0.3 + 0.8 * x, 0.3 + 0.4 * x) for x in
                     (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.05)]}
        sectors, companies = oc.sensitivities(obs, {"a": "فلزات"})
        self.assertAlmostEqual(sectors["فلزات"]["revenue"], 0.8, places=6)
        self.assertAlmostEqual(sectors["فلزات"]["cost"], 0.4, places=6)
        self.assertAlmostEqual(companies["a"]["revenue"], 0.8, places=6)

    def test_a_weak_cost_fit_moves_costs_with_sales(self):
        record = {"fx": {"sectors": {"پالایش": {"revenue": 0.25, "revenue_raw": 0.25,
                                                 "r2": 0.02, "n": 100, "cost": 0.0,
                                                 "cost_r2": 0.0}},
                         "companies": {}}}
        s = oc.sensitivity_for(record, "x", "پالایش")
        self.assertEqual(s["cost_basis"], "same_as_sales")
        self.assertEqual(s["cost"], 0.25)
        self.assertEqual(s["source"], "industry")

    def test_quarters_are_differenced_on_one_basis(self):
        record = {"series": {"nima": [("2020-%02d-01" % m, "x", 100.0 * (1.05 ** m))
                                      for m in range(1, 13)]}}
        self.assertIsNotNone(record)
        statements = {
            ("1", (1400, 12), 3): rec("1400/03/31", 3, 10 * B, 1 * B, gross=3 * B),
            ("1", (1400, 12), 6): rec("1400/06/31", 6, 25 * B, 2 * B, gross=7 * B, cons=True),
        }
        # different bases: no second quarter is formed
        with unittest.mock.patch("fx.average", return_value=1.0):
            obs = oc.quarter_observations(statements, record)
        self.assertEqual(dict(obs), {})


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
