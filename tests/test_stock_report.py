"""stock_report: the number format, the charts, the page, and the printer
(Edge itself is not run here)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundamental_outlook as fo  # noqa: E402
import stock_report as sr  # noqa: E402

B = 1e9


class FormatTests(unittest.TestCase):

    def test_persian_separators_and_missing_values(self):
        self.assertEqual(sr.num(1234567.891, 1), "1٬234٬567٫9")
        self.assertEqual(sr.num(None), "—")
        self.assertEqual(sr.pct(12.345, 1, True), "+12٫3٪")
        self.assertEqual(sr.money(2.5e12), "2٫5 هزار میلیارد ریال")


class ChartTests(unittest.TestCase):

    def test_bars_scale_to_their_column_and_label_the_newest(self):
        svg = sr.bar_chart([1, 2, -1, 4], ["a", "b", "c", "d"])
        self.assertIn('viewBox="0 0 520 170"', svg)
        self.assertNotIn('width="520"', svg)
        self.assertIn(">4<", svg)

    def test_a_halt_breaks_the_line(self):
        svg = sr.line_chart([("s", "#000", [("1", 100), ("2", None), ("3", 110)], 2)])
        path = svg.split('d="')[1].split('"')[0]
        self.assertEqual(path.count("M"), 2)

    def test_nothing_to_draw_is_no_chart(self):
        self.assertEqual(sr.bar_chart([5], ["a"]), "")
        self.assertEqual(sr.range_chart(None, 1, 2, 3), "")


def rows():
    return [
        {"period_end": "1405/06/31", "period_months": 6, "consolidated": False,
         "items": {"revenue": 120 * B, "net_profit": 24 * B, "gross_profit": 36 * B,
                   "operating_profit": 30 * B, "capital": 100 * B, "equity": 400 * B},
         "ratios": {}, "published_at": "1405/07/20", "audited": False},
        {"period_end": "1404/12/29", "period_months": 12, "consolidated": False,
         "items": {"revenue": 300 * B, "net_profit": 60 * B, "gross_profit": 90 * B,
                   "operating_profit": 75 * B, "capital": 100 * B, "equity": 380 * B},
         "ratios": {}, "published_at": "1405/03/20", "audited": True},
        {"period_end": "1404/06/31", "period_months": 6, "consolidated": False,
         "items": {"revenue": 100 * B, "net_profit": 20 * B, "gross_profit": 30 * B,
                   "operating_profit": 25 * B, "capital": 100 * B, "equity": 360 * B},
         "ratios": {}, "published_at": "1404/07/20", "audited": False},
    ]


class PageTests(unittest.TestCase):

    def context(self):
        r = fo.analyse("x", price=5_000, rows=rows(), today=(1405, 8, 14),
                       opportunity={"rate_pct": 44.0, "as_of": "t", "source": "t"},
                       peers={"sector_pe": {"s": 6.0}, "sector_n": {"s": 9},
                              "market_pe": 7.0, "own_pe": {}},
                       sector="s")
        r.update(symbol="آزمون", name="شرکت آزمون", sector="s")
        with mock.patch.object(sr.fundamental_db, "load_metrics", return_value=rows()):
            statements = sr.statement_table("x", r)
        return {"r": r, "inst": {}, "code": "x", "today": "1405/09/02",
                "built_at": "10:00", "statements": statements, "prices": None,
                "method_text": sr.METHOD_TEXT[r["method"]], "basis_text": "x",
                "opportunity": r["hurdle"], "dollar": None,
                "unit": 1e9, "unit_label": "میلیارد ریال"}

    def test_the_page_renders_every_section_and_escapes_text(self):
        ctx = self.context()
        ctx["r"]["flags"].append("<script>x</script>")
        html = sr.render_html(ctx)
        for heading in ("ارزش با ضریب‌های مرجع", "برآورد سال مالی جاری چطور ساخته شد",
                        "فصل به فصل", "کیفیت سود، بدهی و سود نقدی",
                        "توصیه‌ی خرید یا فروش"):
            self.assertIn(heading, html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_statements_table_ends_on_the_estimate(self):
        cols = self.context()["statements"]
        self.assertEqual([c["title"] for c in cols][-2:], ["۱۲ ماه گذشته", "برآورد سال جاری"])
        self.assertTrue(cols[-1]["estimate"])


class PrinterTests(unittest.TestCase):

    def test_a_pdf_edge_never_writes_is_an_error_not_a_hang(self):
        out = os.path.join(tempfile.mkdtemp(), "x.pdf")
        with mock.patch.object(sr, "edge_path", return_value="edge"), \
                mock.patch.object(sr.subprocess, "run"), \
                mock.patch.object(sr.time, "sleep"), \
                mock.patch.object(sr.time, "monotonic", side_effect=[0, 100, 200, 300]):
            with self.assertRaises(sr.ReportError):
                sr.html_to_pdf("<html></html>", out)
        self.assertFalse(os.path.exists(out))

    def test_an_unknown_symbol_is_a_report_error(self):
        with mock.patch.object(sr.database, "load_instruments", return_value=[]):
            with self.assertRaises(sr.ReportError):
                sr.find_instrument("ناموجود")


if __name__ == "__main__":
    unittest.main()
