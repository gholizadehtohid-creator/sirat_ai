"""outlook_calibration: the estimate measured on its own history, and the
dollar slopes."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import outlook_calibration as oc  # noqa: E402

B = 1e9
# A steady company grows at the inflation past years are scored with: since
# 2026-11-25 the months to come are grown toward it (rest_growth), so only
# such a company is estimated exactly.
GROWTH = config.BACKTEST_INFLATION_RATE / 100.0
G = 1 + GROWTH


def rec(end, months, revenue, net, gross=None, cons=False):
    items = {"revenue": revenue, "net_profit": net, "capital": 100 * B}
    if gross is not None:
        items["gross_profit"] = gross
    return {"end": end, "months": months, "cons": cons, "items": items, "ratios": {}}


def company(code, years, margin=0.2, growth=GROWTH):
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
        sixes = [r for m, _b, r in points if m == "6"]
        self.assertTrue(sixes)
        for ratio in sixes:
            self.assertAlmostEqual(ratio, 1.0, places=6)

    def test_the_monthly_method_is_measured_by_emulation_in_its_own_buckets(self):
        # The statement's year-to-date sales stand in for the monthly
        # report's; on a steady company the emulated estimate is exact too.
        points = oc.measure(company("1", range(1400, 1405)))
        emulated = [r for m, _b, r in points if m == "M6"]
        self.assertEqual(len(emulated), len([1 for m, _b, _r in points if m == "6"]))
        for ratio in emulated:
            self.assertAlmostEqual(ratio, 1.0, places=6)

    def test_past_years_are_scored_with_the_inflation_they_faced(self):
        import config
        seen = []
        real = oc.__dict__.get("load_statements")
        self.assertIsNotNone(real)
        import fundamental_outlook as fo
        original = fo.analyse

        def spy(*args, **kwargs):
            seen.append(kwargs.get("inflation_pct"))
            return original(*args, **kwargs)

        with unittest.mock.patch.object(fo, "analyse", spy):
            oc.measure(company("1", range(1400, 1403)))
        self.assertTrue(seen)
        self.assertEqual(set(seen), {config.BACKTEST_INFLATION_RATE})

    def test_bases_are_never_mixed(self):
        statements = company("1", range(1400, 1404))
        statements[("1", (1401, 12), 6)]["cons"] = True
        points = oc.measure(statements)
        # 1401's six months (consolidated) and 1402's (compared with it) are
        # skipped; 1403's, all four rows separate, is measured
        self.assertEqual(len([p for p in points if p[0] == "6"]), 1)

    def test_the_range_falls_back_to_the_months_bucket_when_thin(self):
        record = {"buckets": {"6": {"n": 500, "q10": .5, "q25": .8, "q50": 1, "q75": 1.2,
                                    "q90": 1.5, "median_abs_error": .2},
                              "6|lt5": {"n": 10, "q10": .1, "q25": .2, "q50": 1, "q75": 3,
                                        "q90": 5, "median_abs_error": .9}}}
        b = oc.range_for(record, 7, 0.03)
        self.assertEqual(b["key"], "6")
        self.assertEqual(b["basis"], "statement")
        self.assertIsNone(oc.range_for(None, 7, 0.03))

    def test_each_method_reads_its_own_bucket(self):
        # a year not begun is "12" whatever the months; the monthly method
        # reads the emulated buckets, and twelve months of sales are still
        # not a statement
        self.assertEqual(oc.bucket_keys("next_year_trend", 12, "5_15"), ["12|5_15", "12"])
        self.assertEqual(oc.bucket_keys("monthly_sales", 7, None), [None, "M6"])
        self.assertEqual(oc.bucket_keys("monthly_sales", 12, "lt5"), ["M9|lt5", "M9"])
        self.assertEqual(oc.bucket_keys("year_completed", 2, None), [None, "3"])
        record = {"buckets": {"M6": {"n": 300, "q10": .5, "q25": .75, "q50": 1, "q75": 1.3,
                                     "q90": 1.6, "median_abs_error": .27}}}
        b = oc.range_for(record, 7, None, "monthly_sales")
        self.assertEqual((b["key"], b["basis"], b["months_bucket"]), ("M6", "monthly", 6))
        self.assertIsNone(oc.range_for(record, 7, None, "year_completed"))


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


# ---- point in time (2026-11-24) ---------------------------------------------

def release(code, tracing, end, months, published, revenue, net, cons=False):
    import fundamental_db
    return {"tracing_no": tracing, "ins_code": code, "period_end": end,
            "period_months": months, "consolidated": cons, "audited": False,
            "published_at": published,
            "published_iso": fundamental_db.published_iso(published), "title": "t",
            "items": {"revenue": revenue, "net_profit": net, "capital": 100 * B},
            "ratios": {}}


def releases(code, years, margin=0.2, growth=GROWTH):
    """company() as Codal releases: each statement published about a month
    (an annual, about two) after its period."""

    out = []
    revenue = 100 * B
    for y in years:
        half = revenue * 0.45
        out.append(release(code, "%s-%d-6" % (code, y), "%d/06/31" % y, 6,
                           "%d/07/28 10:00:00" % y, half, half * margin))
        out.append(release(code, "%s-%d-12" % (code, y), "%d/12/29" % y, 12,
                           "%d/02/20 10:00:00" % (y + 1), revenue, revenue * margin))
        revenue *= 1 + growth
    return out


def sixes(found, year=None):
    return [p[2] for p in found if p[0] == "6" and (year is None or p[3] == year)]


class PointInTimeTests(unittest.TestCase):

    def test_a_steady_company_is_estimated_exactly_point_in_time(self):
        found = oc.measure_pit({"1": releases("1", range(1400, 1405))}, lags=(0,))[0]
        self.assertEqual(sorted({p[3] for p in found["points"] if p[0] == "6"}),
                         [1401, 1402, 1403, 1404])
        for ratio in sixes(found["points"]):
            self.assertAlmostEqual(ratio, 1.0, places=6)
        self.assertTrue([p for p in found["points"] if p[0] == "12"])

    def test_the_actual_is_the_first_release_and_the_latest_is_kept_beside_it(self):
        rel = releases("1", range(1400, 1405))
        # 1402's annual restated a year later, its profit half as high again
        rel.append(release("1", "late", "1402/12/29", 12, "1404/05/01 10:00:00",
                           100 * B * G ** 2, 100 * B * G ** 2 * 0.2 * 1.5))
        found = oc.measure_pit({"1": rel}, lags=(0,))[0]
        self.assertAlmostEqual(sixes(found["points"], 1402)[0], 1.0, places=6)
        self.assertAlmostEqual(sixes(found["latest"], 1402)[0], 1.5, places=6)

    def test_inputs_are_the_releases_known_on_the_decision_day(self):
        rel = releases("1", range(1400, 1405))
        # last year's annual restated (sales +20%) eighteen days after this
        # year's six months were first published
        rel.append(release("1", "restated", "1401/12/29", 12, "1402/08/15 10:00:00",
                           100 * B * G * 1.2, 100 * B * G * 0.2))
        found = oc.measure_pit({"1": rel}, lags=(0, 30))
        self.assertAlmostEqual(sixes(found[0]["points"], 1402)[0], 1.0, places=6)
        self.assertNotAlmostEqual(sixes(found[30]["points"], 1402)[0], 1.0, places=3)

    def test_an_actual_already_public_is_not_an_estimate(self):
        rel = releases("1", range(1400, 1405))
        for r in rel:                       # 1403's six months filed after its year
            if r["tracing_no"] == "1-1403-6":
                r.update(published_at="1404/03/01 10:00:00",
                         published_iso="2025-05-22T10:00:00")
        found = oc.measure_pit({"1": rel}, lags=(0,))[0]
        self.assertEqual(sixes(found["points"], 1403), [])
        self.assertEqual(found["skipped"].get("actual_known"), 1)

    def test_the_decision_day_is_passed_as_its_jalali_date(self):
        import fundamental_outlook as fo
        seen = []
        original = fo.analyse

        def spy(*args, **kwargs):
            seen.append(kwargs.get("today"))
            return original(*args, **kwargs)

        with unittest.mock.patch.object(fo, "analyse", spy):
            oc.measure_pit({"1": releases("1", range(1400, 1403))}, lags=(0, 30))
        self.assertIn((1401, 7, 28), seen)          # the six months' first release
        self.assertIn((1401, 8, 28), seen)          # thirty days on

    def test_wilson_interval(self):
        low, high = oc.wilson(5, 10)
        self.assertAlmostEqual(low, 0.2366, places=3)
        self.assertAlmostEqual(high, 0.7634, places=3)
        self.assertEqual(oc.wilson(0, 0), (None, None))

    def test_leave_one_year_out_holds_on_exchangeable_years_and_names_a_shock(self):
        grid = [0.5 + (i + 0.5) / 200 for i in range(200)]
        points = [("6", None, r, y) for y in range(1400, 1405) for r in grid]
        held = oc.loyo(points)
        rate = held["overall"]["statement"]["q25_q75"]["rate"]
        self.assertAlmostEqual(rate, 0.5, delta=0.02)
        self.assertTrue(held["overall"]["statement"]["q25_q75"]["consistent"])
        self.assertAlmostEqual(held["buckets"]["6"]["q10_q90"]["rate"], 0.8, delta=0.02)

        shocked = points + [("6", None, r + 2.0, 1405) for r in grid]
        held = oc.loyo(shocked)
        worst = held["year_spread"]["statement"]["q25_q75"]["worst"]
        self.assertEqual((worst["year"], worst["covered"]), (1405, 0))
        self.assertGreaterEqual(held["year_spread"]["statement"]["q25_q75"]["outside"], 1)

    def test_the_band_falls_back_explicitly(self):
        s = {"n": 100, "q10": .5, "q25": .8, "q50": 1, "q75": 1.2, "q90": 1.5,
             "median_abs_error": .2}
        thin = dict(s, n=5)
        buckets, fallback = oc.choose_band({"6": s, "6|lt5": thin, "9": thin},
                                           {"6": s, "6|lt5": s, "9": dict(s, q25=.7)})
        self.assertEqual(buckets["6"]["source"], "pit")
        # a thin (months, margin) bucket stays thin: range_for drops to "6"
        self.assertEqual((buckets["6|lt5"]["n"], buckets["6|lt5"]["source"]), (5, "pit"))
        self.assertEqual(oc.range_for({"buckets": buckets}, 7, 0.02)["key"], "6")
        # a thin months bucket takes the restated one, and says so
        self.assertEqual((buckets["9"]["source"], buckets["9"]["q25"], buckets["9"]["pit_n"]),
                         ("restated", .7, 5))
        self.assertEqual(fallback, {"restated": ["9"], "thin": ["6|lt5"]})

    def test_the_range_carries_its_source_years_and_coverage(self):
        s = {"n": 100, "q10": .5, "q25": .8, "q50": 1, "q75": 1.2, "q90": 1.5,
             "median_abs_error": .2}
        cov = {"covered": 40, "total": 100, "nominal": 0.5, "rate": 0.4,
               "low95": .31, "high95": .5, "consistent": True}
        record = {"buckets": {"6": dict(s, source="pit")},
                  "band": {"years": [1400, 1405]},
                  "loyo": {"buckets": {"6": {"q25_q75": cov, "q10_q90": dict(cov, nominal=.8)}},
                           "year_spread": {"statement": {"q25_q75": {
                               "years": 6, "outside": 3,
                               "worst": {"year": 1405, "covered": 2, "total": 10, "rate": .2}}}}}}
        b = oc.range_for(record, 7, None)
        self.assertEqual((b["source"], b["years"]), ("pit", [1400, 1405]))
        self.assertEqual((b["coverage"]["per10"], b["coverage"]["years_outside"],
                          b["coverage"]["worst_year"]["year"]), (4, 3, 1405))
        self.assertEqual(b["coverage80"]["nominal"], .8)

        import fundamental_outlook as fo
        out = fo.calibrated_range(record, 7, None, 100 * B, 1e9, 1000)
        self.assertEqual((out["source"], out["coverage"]["covered"]), ("pit", 40))

        # a record from before 2026-11-24: restated, nothing measured on left-out years
        old = oc.range_for({"buckets": {"6": s}}, 7, None)
        self.assertEqual((old["source"], old["coverage"], old["years"]), ("restated", None, None))

    def test_without_releases_the_band_stays_restated_and_says_so(self):
        record = oc.build(statements=company("1", range(1400, 1405)), fx_record={},
                          sector_of={}, versions={})
        self.assertEqual(record["band"]["source"], "restated")
        self.assertEqual(record["buckets"], record["restated_buckets"])
        self.assertIsNone(record["loyo"])

    def test_the_band_is_read_from_the_point_in_time_points(self):
        with unittest.mock.patch.object(oc, "MIN_N", 1):
            record = oc.build(statements=company("1", range(1400, 1405)), fx_record={},
                              sector_of={}, versions={"1": releases("1", range(1400, 1405))})
        self.assertEqual(record["band"]["source"], "pit")
        self.assertEqual(record["buckets"]["6"]["source"], "pit")
        self.assertEqual(set(record["pit"]["lags"]), {"0", "30", "60", "90"})
        self.assertEqual(record["points"], record["pit"]["lags"]["0"]["points"])
        self.assertIn("6", record["restated_buckets"])
        self.assertIsNotNone(record["loyo"])
        self.assertEqual(record["pit"]["store"]["periods"], 10)


if __name__ == "__main__":
    unittest.main()
