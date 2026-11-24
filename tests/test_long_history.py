"""E36's fifteen-year inputs and the owner's fifteen-year window rule
(2026-11-24), on synthetic data only."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd

import tests  # noqa: F401  (live stores read-only)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "examples")):
    if path not in sys.path:
        sys.path.insert(0, path)

import long_history as lh                                       # noqa: E402
from sirat.experiments import multiplicity                      # noqa: E402


class LeaseBonds(unittest.TestCase):

    def test_coupon_and_frequency_from_the_name(self):
        self.assertAlmostEqual(lh.coupon_of("اجاره ماهان سه ماهه 20 درصد"), 0.20)
        self.assertEqual(lh.frequency_of("اجاره ماهان سه ماهه 20 درصد"), 4)
        self.assertEqual(lh.frequency_of("مرابحه شش ماهه 18 درصد"), 2)
        self.assertIsNone(lh.coupon_of("اجاره رایتل"))

    def test_options_are_not_bonds(self):
        self.assertFalse(lh.is_lease("ضاجا", "اختيارخ اجاره 1000"))
        self.assertTrue(lh.is_lease("ماهان", "اجاره ماهان سه ماهه 20 درصد"))

    def test_face_is_read_from_the_print(self):
        self.assertEqual(lh.face_of(100.5), 100.0)          # early series
        self.assertEqual(lh.face_of(1_010_000), 1_000_000.0)
        self.assertIsNone(lh.face_of(500_000))              # not near par
        self.assertIsNone(lh.face_of(5_000))

    def test_current_yield_at_par_is_the_compounded_coupon(self):
        # quarterly 20% at par: (1 + 0.05)^4 - 1
        conn = mock.MagicMock()
        rows = [("2012-01-01", 100.0, 10)]
        conn.execute.side_effect = [
            iter([("c1", "ماهان", "اجاره ماهان سه ماهه 20 درصد")]),
            iter(rows)]
        quotes, rejected = lh.lease_quotes(conn)
        self.assertEqual(len(quotes), 1)
        self.assertAlmostEqual(quotes[0][2], 1.05 ** 4 - 1, places=9)
        self.assertEqual(rejected, [])


class CoinFile(unittest.TestCase):

    def test_round_trip(self):
        path = os.path.join(tempfile.mkdtemp(), "coin.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"series": [["2011-08-01", "1390/05/10", 4_000_000.0]]}, fh)
        self.assertEqual(lh.coin_series(path)[0][2], 4_000_000.0)
        self.assertEqual(lh.coin_series(path + ".missing"), [])


class WindowRule(unittest.TestCase):

    def test_fifteen_years_pass(self):
        self.assertEqual(multiplicity.window_rule(
            {"window_start": "2011-08-01", "window_end": "2026-07-31"}), [])

    def test_shorter_needs_a_reason(self):
        doc = {"window_start": "2021-06-01", "window_end": "2026-07-31"}
        self.assertEqual(len(multiplicity.window_rule(doc)), 1)
        doc["window_shorter_reason"] = "Codal statements before 1399 cover 30-110 companies"
        self.assertEqual(multiplicity.window_rule(doc), [])

    def test_dates_are_required(self):
        self.assertEqual(multiplicity.window_rule({"window": "2011..2026"}),
                         ["window_start and window_end as dates"])

    def test_the_live_e36_file_passes_both_rules(self):
        with open(os.path.join(ROOT, "backtest_output", "E36_preregistration.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(multiplicity.prereg_completeness(doc), [])
        self.assertEqual(multiplicity.window_rule(doc), [])


class Splice(unittest.TestCase):

    def test_coin_before_the_splice_funds_after(self):
        import long_allocation_study as study
        days = pd.DatetimeIndex(pd.bdate_range("2017-08-01", "2017-11-30"))
        coin = [(d.date().isoformat(), "", 100.0 * (1.01 ** i))
                for i, d in enumerate(days)]
        fund = pd.Series(0.002, index=days)
        with mock.patch.object(lh, "coin_series", return_value=coin), \
                mock.patch.object(study, "START", date(2017, 8, 1)), \
                mock.patch.object(study, "gold_sleeve",
                                  return_value=(fund, days[0], 0, 3)):
            gold, info = study.gold_leg(days)
        before = gold[days < pd.Timestamp("2017-10-01")].dropna()
        after = gold[days >= pd.Timestamp("2017-10-01")]
        np.testing.assert_allclose(before.iloc[1:].to_numpy(), 0.01, rtol=1e-9)
        np.testing.assert_allclose(after.to_numpy(), 0.002)
        self.assertGreaterEqual(info["coin_fresh_share"], 0.99)


if __name__ == "__main__":
    unittest.main()
