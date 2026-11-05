"""monthly_reports: column groups are read by meaning, not position."""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monthly_reports as mr  # noqa: E402

PERIOD = (1405, 7, 30)


class GroupTests(unittest.TestCase):

    def test_the_groups_of_the_manufacturer_template(self):
        g = mr.classify_group
        self.assertEqual(g("دوره یک ماهه منتهی به ۱۴۰۵/۰۷/۳۰", PERIOD), mr.GROUP_MONTH)
        self.assertEqual(g("از ابتدای سال مالی تا تاریخ ۱۴۰۵/۰۷/۳۰", PERIOD), mr.GROUP_YTD)
        self.assertEqual(g("از ابتدای سال مالی تا تاریخ ۱۴۰۴/۰۷/۳۰", PERIOD), mr.GROUP_YTD_PREV)
        # to the PREVIOUS month: superseded by the month + ytd columns
        self.assertIsNone(g("از ابتدای سال مالی تا تاریخ ۱۴۰۵/۰۶/۳۱", PERIOD))
        self.assertIsNone(g("از ابتدای سال مالی تا تاریخ ۱۴۰۵/۰۶/۳۱ (اصلاح شده)", PERIOD))
        self.assertIsNone(g("اصلاحات", PERIOD))
        self.assertEqual(g("برآورد مانده تا پایان سال مالی ۱۴۰۵/۱۲/۲۹", PERIOD),
                         mr.GROUP_FORECAST)

    def test_the_bank_template_names_only_year_and_month(self):
        self.assertEqual(mr.classify_group(
            "جمع درآمد محقق شده از ابتدای سال مالی تا پایان ۱۴۰۵/۰۷", PERIOD),
            mr.GROUP_YTD)

    def test_measures(self):
        m = mr.classify_measure
        self.assertEqual(m("دوره || تعداد تولید"), "production")
        self.assertEqual(m("دوره || مبلغ فروش (میلیون ریال)"), "amount")
        self.assertEqual(m("دوره || خسارت پرداختی || مبلغ (میلیون ریال)"), "claims")
        self.assertIsNone(m("دوره || حق بیمه صادره || سهم(درصد)"))


class ParseTests(unittest.TestCase):

    def test_a_sales_table_totals_in_rials(self):
        cols = pd.MultiIndex.from_tuples([
            ("شرح", "نام محصول"), ("شرح", "واحد"),
            ("دوره یک ماهه منتهی به ۱۴۰۵/۰۷/۳۰", "مبلغ فروش (میلیون ریال)"),
            ("از ابتدای سال مالی تا تاریخ ۱۴۰۵/۰۷/۳۰", "تعداد فروش"),
            ("از ابتدای سال مالی تا تاریخ ۱۴۰۵/۰۷/۳۰", "مبلغ فروش (میلیون ریال)"),
            ("از ابتدای سال مالی تا تاریخ ۱۴۰۴/۰۷/۳۰", "مبلغ فروش (میلیون ریال)"),
        ])
        frame = pd.DataFrame([
            ["فروش داخلی:", None, None, None, None, None],
            ["ورق", "تن", "10", "50", "70", "40"],
            ["جمع", None, "10", None, "70", "40"],
        ], columns=cols)
        r = mr.parse({"گزارش فعالیت ماهانه": [frame]},
                     "گزارش فعالیت ماهانه دوره ۱ ماهه منتهی به ۱۴۰۵/۰۷/۳۰")
        self.assertEqual(r["template"], "manufacturer")
        self.assertEqual(r["sales"], {"month": 10e6, "ytd": 70e6, "ytd_prev": 40e6})
        self.assertEqual(r["products"][0]["name"], "ورق")


if __name__ == "__main__":
    unittest.main()
