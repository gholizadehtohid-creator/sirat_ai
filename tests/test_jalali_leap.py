"""jalali: one leap rule, the day count's own (the full test, 2026-11-12).

The 2820-year formula disagreed with to_gregorian's 33-year day count in ten
years of 1206-1498: 1437/12/30 was accepted and landed on 1438/01/01.
"""

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jalali  # noqa: E402


class LeapTests(unittest.TestCase):

    def test_the_last_day_of_every_year_is_the_eve_of_the_next(self):
        for year in range(1206, 1498):
            last = 30 if jalali.is_jalali_leap(year) else 29
            end = date(*jalali.to_gregorian(year, 12, last))
            start = date(*jalali.to_gregorian(year + 1, 1, 1))
            self.assertEqual(end + timedelta(days=1), start, year)

    def test_a_30th_of_esfand_in_a_common_year_is_refused(self):
        self.assertFalse(jalali.is_jalali_leap(1437))
        with self.assertRaises(ValueError):
            jalali.to_gregorian(1437, 12, 30)

    def test_the_years_the_data_lives_in_are_unchanged(self):
        # 1399 and 1403 are leap years; 1404 and 1405 are not.
        self.assertEqual([y for y in range(1395, 1410) if jalali.is_jalali_leap(y)],
                         [1395, 1399, 1403, 1408])


if __name__ == "__main__":
    unittest.main()
