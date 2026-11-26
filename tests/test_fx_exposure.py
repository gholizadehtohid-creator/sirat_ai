"""fx_exposure.py: the currency position from the board's management report."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_content  # noqa: E402
import fx_exposure as fe  # noqa: E402

LAST, NOW, EST = ("سال مالي منتهي به ۱۴۰۴/۱۲/۲۹", "دوره ۶ ماهه منتهي به ۱۴۰۵/۰۶/۳۱",
                  "برآورد دوره ۶ ماهه منتهي به ۱۴۰۵/۱۲/۲۹")


def currency_html(rows):
    head = ("<tr><th rowspan='2'>شرح</th><th rowspan='2'>نوع ارز</th>"
            + "".join("<th colspan='3'>%s</th>" % p for p in (LAST, NOW, EST)) + "</tr><tr>"
            + "<th>نرخ ميانگين تسعير ارز</th><th>مبلغ ارزي</th><th>مبلغ ريالي-ميليون ريال</th>" * 3
            + "</tr>")
    body = "".join("<tr><td>%s</td><td>%s</td>%s</tr>" % (
        label, cur, "".join("<td>۰</td><td>%s</td><td>%s</td>" % (v, v) for v in values))
        for label, cur, values in rows)
    return ("<h3>خلاصه اطلاعات گزارش تفسیری - صفحه 3</h3><table><thead>%s</thead>"
            "<tbody>%s</tbody></table>" % (head, body))


def materials_html(domestic, imported):
    groups = ("موجودي اول دوره", "خريد طي دوره", "مصرف", "موجودی پایان دوره")
    head = ("<tr><th rowspan='3'>شرح</th><th rowspan='3'>واحد</th><th colspan='12'>%s</th></tr><tr>" % NOW
            + "".join("<th colspan='3'>%s</th>" % g for g in groups) + "</tr><tr>"
            + "<th>مقدار</th><th>نرخ</th><th>مبلغ</th>" * 4 + "</tr>")

    def row(label, used):
        cells = ["", "", "۱۰", "", "", "۲۰", "", "", used, "", "", "۵"]
        return "<tr><td>%s</td><td></td>%s</tr>" % (label, "".join("<td>%s</td>" % c for c in cells))

    return ("<h3>خلاصه اطلاعات گزارش تفسیری - صفحه 2</h3><table><thead>%s</thead><tbody>%s%s"
            "</tbody></table>" % (head, row("جمع مواد اولیه داخلی", domestic),
                                  row("جمع مواد اولیه وارداتی", imported)))


class ParseTests(unittest.TestCase):

    def test_numbers_as_the_report_writes_them(self):
        self.assertEqual(fe._num("۱,۲۳۴"), 1234)
        self.assertEqual(fe._num("(۱۲۴,۲۲۷)"), -124227)
        self.assertIsNone(fe._num("nan"))
        self.assertIsNone(fe._num("—"))

    def test_a_header_names_its_period(self):
        self.assertEqual(fe._period(NOW), ("1405/06/31", 6, False))
        self.assertEqual(fe._period(LAST), ("1404/12/29", 12, False))
        self.assertEqual(fe._period(EST), ("1405/12/29", 6, True))
        self.assertIsNone(fe._period("شرح"))

    def test_the_filings_own_period_summed_over_currencies(self):
        html = currency_html([
            ("منابع ارزی طی دوره", "یورو", ("۱۰۰", "۲۰", "۳۰")),
            ("مصارف ارزی طی دوره", "یورو", ("(۹۰)", "(۱۵)", "(۴۰)")),
            ("منابع ارزی طی دوره", "درهم", ("۵۰۰", "۷۰", "۸۰")),
            ("داراییهای ارزی پایان دوره", "دلار", ("۱۰", "۴۰", "۱۵")),
            ("بدهی های ارزی پایان دوره", "دلار", ("(۵)", "(۱۰)", "(۲)")),
        ]) + materials_html("۹۰۰", "۱۰۰")
        got = fe.read(html, "1405/06/31", 6)
        cur = got["currency"]
        self.assertEqual(cur["receipts"], 90 * fe.MILLION)       # 20 + 70, the period's column
        self.assertEqual(cur["payments"], 15 * fe.MILLION)
        self.assertEqual(cur["assets"], 40 * fe.MILLION)
        self.assertEqual(cur["liabilities"], 10 * fe.MILLION)
        self.assertEqual(got["currency_estimate"]["receipts"], 110 * fe.MILLION)
        self.assertEqual(got["materials"], {"domestic": 900 * fe.MILLION,
                                            "imported": 100 * fe.MILLION})

    def test_a_filing_of_another_period_is_not_read_as_this_one(self):
        html = currency_html([("منابع ارزی طی دوره", "یورو", ("۱", "۲", "۳"))])
        self.assertIsNone(fe.read(html, "1405/03/31", 3))

    def test_a_filing_without_a_report_is_none(self):
        self.assertIsNone(fe.read("<h3>صورت سود و زیان</h3><table><tr><td>1</td></tr></table>",
                                  "1405/06/31", 6))


class ExposureTests(unittest.TestCase):

    def test_shares_and_the_translation_per_share(self):
        row = {"period_end": "1405/06/31", "period_months": 6, "receipts": 180e12,
               "payments": 215e12, "assets": 432e12, "liabilities": 310e12,
               "materials_domestic": 1030e12, "materials_imported": 8e12,
               "est_end": None, "est_months": None, "est_receipts": None,
               "est_payments": None, "tracing_no": "1"}
        with mock.patch.object(fe, "latest", return_value=row):
            e = fe.exposure("x", revenue_for=lambda end, months: 2500e12, shares=1.935e12)
        self.assertAlmostEqual(e["receipts_share"], 180 / 2500)
        self.assertAlmostEqual(e["net_position"], 122e12)
        self.assertAlmostEqual(e["imported_share"], 8 / 1038)
        self.assertAlmostEqual(e["per_share_10pct"], 122e12 * 0.1 / 1.935e12)
        line = bot_content.exposure_line(e)
        self.assertTrue(line.startswith("💱"))
        self.assertIn("دریافت ارزی", line)

    def test_nothing_read_is_none(self):
        with mock.patch.object(fe, "latest", return_value=None):
            self.assertIsNone(fe.exposure("x"))
        self.assertEqual(bot_content.exposure_line(None), "")


if __name__ == "__main__":
    unittest.main()
