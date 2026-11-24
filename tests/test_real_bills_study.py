"""E34's machinery on synthetic data: the bill-yield series and the study's
arithmetic, checked before the study ever touched the real series."""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

import numpy as np
import pandas as pd

import tests  # noqa: F401  (live stores read-only)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "examples")):
    if path not in sys.path:
        sys.path.insert(0, path)

import bill_history                                             # noqa: E402
import real_bills_study as study                                # noqa: E402


def _sessions(start, end):
    """Sat..Wed sessions, Tehran's week."""
    out, d = [], start
    while d <= end:
        if d.weekday() in (5, 6, 0, 1, 2):
            out.append(d)
        d += timedelta(days=1)
    return out


class BillDb(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "m.db")
        self.patch = mock.patch.object(bill_history, "DB", self.path)
        self.patch.start()
        conn = bill_history._connect()
        # one bill maturing 2018-06-30, priced so its yield is ~20% throughout
        due = date(2018, 6, 30)
        conn.execute("INSERT INTO bill_series (ins_code, symbol, name, maturity,"
                     " active, source) VALUES ('B1', 'x', 'اسناد خزانه-970409',"
                     " ?, 0, 'test')", (due.isoformat(),))
        for d in _sessions(date(2017, 8, 1), date(2017, 12, 31)):
            days = (due - (d + timedelta(days=1))).days
            price = 1_000_000 / (1.20 ** (days / 365.0))
            conn.execute("INSERT INTO bill_bars VALUES ('B1', ?, ?, 10, 1e9)",
                         (d.isoformat(), price))
        # a misprint: yield far above 100%
        conn.execute("INSERT OR REPLACE INTO bill_bars VALUES ('B1', '2017-09-26',"
                     " 300000, 10, 1e9)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.patch.stop()

    def test_month_start_yield_is_the_previous_tail_and_point_in_time(self):
        sessions = _sessions(date(2017, 8, 1), date(2017, 12, 31))
        out, rejected = bill_history.month_start_yields(
            sessions, date(2017, 10, 1), date(2017, 12, 31))
        self.assertEqual(sorted(out), [(2017, 10), (2017, 11), (2017, 12)])
        for (y, m), r in out.items():
            self.assertAlmostEqual(r["yield"], 0.20, places=3)
            self.assertLess(date.fromisoformat(r["to"]), date(y, m, 1))
            self.assertEqual(r["basis"], "tail")
        self.assertEqual(len(rejected), 1)

    def test_a_month_after_a_closed_month_is_carried(self):
        sessions = [d for d in _sessions(date(2017, 8, 1), date(2017, 12, 31))
                    if d.month != 10]
        out, _ = bill_history.month_start_yields(
            sessions, date(2017, 10, 1), date(2017, 12, 31))
        self.assertEqual(out[(2017, 11)]["basis"], "carried")
        self.assertAlmostEqual(out[(2017, 11)]["yield"], out[(2017, 10)]["yield"])

    def test_options_are_not_bills(self):
        self.assertFalse(bill_history.is_bill("طاخزا704101",
                                              "اختيارف اخز101-662606-14020709"))
        self.assertTrue(bill_history.is_bill("اخزا209",
                                             "اسنادخزانه-م9بودجه02-050811"))


class StudyArithmetic(unittest.TestCase):

    def setUp(self):
        self.p = [mock.patch.object(study, "START", date(2018, 1, 1)),
                  mock.patch.object(study, "END", date(2018, 12, 31))]
        for p in self.p:
            p.start()
        days = _sessions(date(2017, 11, 1), date(2018, 12, 31))
        self.calendar = pd.DatetimeIndex(days)
        rng = np.random.default_rng(1)
        self.index = pd.Series(np.cumprod(1 + rng.normal(0.001, 0.01, len(days))),
                               index=self.calendar)
        self.gold = pd.Series(rng.normal(0.001, 0.008, len(days)),
                              index=self.calendar)
        self.yields = {(2018, m): {"yield": 0.30} for m in range(1, 13)}

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_bill_leg_earns_exactly_the_calendar_year(self):
        frame, is_session = study.legs(self.calendar, self.index, self.gold,
                                       self.yields)
        self.assertAlmostEqual(float((1 + frame["b"]).prod()), 1.30, places=6)
        monthly = study.monthly(frame["b"])
        self.assertEqual(len(monthly), 12)

    def test_closed_month_earns_bills_and_no_market_move(self):
        cal = self.calendar[~((self.calendar.month == 5) &
                              (self.calendar.year == 2018))]
        frame, is_session = study.legs(cal, self.index.reindex(cal),
                                       self.gold.reindex(cal), self.yields)
        may = frame[(frame.index.month == 5)]
        self.assertEqual(len(may), 1)                  # only the month-end point
        self.assertEqual(float(may["e"].iloc[0]), 0.0)
        self.assertAlmostEqual(float(may["b"].iloc[0]), 1.30 ** (31 / 365) - 1,
                               places=9)

    def test_book_with_all_weight_in_one_leg_is_that_leg(self):
        frame, is_session = study.legs(self.calendar, self.index, self.gold,
                                       self.yields)
        only_bills = study.book(frame, is_session, {"b": 1.0})
        np.testing.assert_allclose(only_bills.to_numpy(), frame["b"].to_numpy())

    def test_rebalancing_costs_are_charged(self):
        frame, is_session = study.legs(self.calendar, self.index, self.gold,
                                       self.yields)
        with mock.patch.object(study, "COST", 0.0):
            free = study.book(frame, is_session, {"e": 0.5, "g": 0.5})
        paid = study.book(frame, is_session, {"e": 0.5, "g": 0.5})
        self.assertLess(float((1 + paid).prod()), float((1 + free).prod()))

    def test_bootstrap_p_is_small_for_a_clear_effect_and_large_for_none(self):
        rng = np.random.default_rng(3)
        strong = rng.normal(0.05, 0.02, 120)
        noise = rng.normal(0.0, 0.05, 120)
        mean = lambda x: float(np.mean(x))              # noqa: E731
        self.assertLess(study.bootstrap_p(mean, (strong,))["p"], 0.01)
        self.assertGreater(study.bootstrap_p(mean, (noise - noise.mean(),))["p"],
                           0.2)

    def test_holm_is_step_down(self):
        out = study.holm({"a": 0.001, "b": 0.03, "c": 0.04})
        self.assertTrue(out["a"]["null_rejected"])
        self.assertFalse(out["b"]["null_rejected"])      # 0.03 > 0.05/2
        self.assertFalse(out["c"]["null_rejected"])      # stops after b
        out = study.holm({"a": 0.001, "b": 0.02, "c": 0.04})
        self.assertTrue(all(v["null_rejected"] for v in out.values()))


if __name__ == "__main__":
    unittest.main()
