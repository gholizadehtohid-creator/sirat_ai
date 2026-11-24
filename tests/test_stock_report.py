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

    def context(self, releases=()):
        r = fo.analyse("x", price=5_000, rows=rows(), today=(1405, 8, 14),
                       opportunity={"rate_pct": 44.0, "as_of": "t", "source": "t"},
                       peers={"sector_pe": {"s": 6.0}, "sector_n": {"s": 9},
                              "market_pe": 7.0, "own_pe": {}},
                       sector="s")
        r.update(symbol="آزمون", name="شرکت آزمون", sector="s")
        with mock.patch.object(sr.fundamental_db, "load_metrics", return_value=rows()), \
                mock.patch.object(sr, "first_releases", return_value=list(releases)):
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
        self.assertNotIn("first_owners", cols[0])
        self.assertNotIn("نخستین گزارش", sr.render_html(self.context()))

    def test_the_point_in_time_band_and_its_left_out_years_are_explained(self):
        ctx = self.context()
        ctx["r"]["forward"]["range"] = {
            "q10": .5, "q25": .8, "q50": 1.0, "q75": 1.2, "q90": 1.5, "n": 324,
            "key": "6|15_30", "basis": "statement", "months_known": 6, "months_bucket": 6,
            "band": "15_30", "band_label": "۱۵ تا ۳۰٪", "median_abs_error_pct": 21.7,
            "source": "pit", "years": [1400, 1405], "wide": False,
            "coverage": {"covered": 159, "total": 324, "rate": .49, "per10": 5,
                         "nominal": .5, "worst_year": {"year": 1405, "rate": .2}},
            "coverage80": {"covered": 258, "total": 324, "rate": .796, "nominal": .8},
            "profit_low": 1e9, "profit_high": 2e9, "profit_low80": 1e9, "profit_high80": 3e9,
            "eps_low": 1, "eps_high": 2, "eps_low80": 1, "eps_high80": 3,
            "pe_low": 5, "pe_high": 9}
        html = sr.render_html(ctx)
        self.assertIn("سال‌های 1400 تا 1405", html)
        self.assertIn("نخستین سودی که شرکت برای آن سال گزارش کرد", html)
        self.assertIn("حدود 5 از ۱۰", html)
        self.assertIn("در 1405 فقط 20٪", html)
        self.assertIn("pit_fundamentals", html)


def first_published(net_first, net_audited=60 * B):
    """The 1404 annual's two releases: first published at `net_first`,
    audited at the figure rows() holds."""

    import fundamental_db

    def rel(tracing, published, net):
        return {"tracing_no": tracing, "ins_code": "x", "period_end": "1404/12/29",
                "period_months": 12, "consolidated": False, "audited": False,
                "published_at": published,
                "published_iso": fundamental_db.published_iso(published),
                "title": "t", "items": {"revenue": 300 * B, "net_profit": net},
                "ratios": {}}
    return [rel("1", "1405/01/31 10:00:00", net_first),
            rel("2", "1405/03/20 10:00:00", net_audited)]


class RevisionTests(unittest.TestCase):
    """A year whose profit was later revised shows its first release."""

    context = PageTests.context

    def test_a_revised_year_shows_its_first_release(self):
        ctx = self.context(first_published(48 * B))
        col = [c for c in ctx["statements"] if c["title"] == "سال 1404/12"][0]
        self.assertAlmostEqual(col["first_owners"], 48 * B)
        self.assertAlmostEqual(col["revision_pct"], 25.0)
        html = sr.render_html(ctx)
        self.assertIn("نخستین گزارش: 48", html)
        self.assertIn("«نخستین گزارش»", html)               # the footnote
        self.assertIn("default-src 'none'", html)           # the CSP stays

    def test_a_revision_within_five_percent_is_not_marked(self):
        cols = self.context(first_published(59 * B))["statements"]
        self.assertFalse([c for c in cols if c.get("first_owners") is not None])


class HousekeepingTests(unittest.TestCase):

    def test_a_share_with_no_trade_among_its_bars_has_no_chart_not_a_crash(self):
        import pandas as pd
        bars = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"] * 30),
                             "close": [1000.0] * 30, "volume": [0] * 30})
        with mock.patch.object(sr.database, "load_history_by_inscode", return_value=bars):
            self.assertIsNone(sr.price_series("x"))

    def test_only_dated_folders_under_reports_are_aged_out(self):
        root = tempfile.mkdtemp()
        old_day = os.path.join(root, "1405-01-01")
        new_day = os.path.join(root, "1405-09-02")
        other = os.path.join(root, "my-notes")
        for folder in (old_day, new_day, other):
            os.makedirs(folder)
        long_ago = 1_000_000_000
        os.utime(old_day, (long_ago, long_ago))
        os.utime(other, (long_ago, long_ago))
        self.assertEqual(sr.prune(30, root), 1)
        self.assertFalse(os.path.exists(old_day))
        self.assertTrue(os.path.exists(new_day))
        self.assertTrue(os.path.exists(other))          # not ours to remove

    def test_a_custom_output_folder_is_never_pruned(self):
        ctx = {"today": "1405/09/02", "r": {"symbol": "x"}}
        out_dir = tempfile.mkdtemp()
        with mock.patch.object(sr, "gather", return_value=ctx), \
                mock.patch.object(sr, "render_html", return_value="<html></html>"), \
                mock.patch.object(sr, "html_to_pdf", side_effect=lambda _h, out: out), \
                mock.patch.object(sr, "prune") as prune:
            sr.build("x", out_dir=out_dir)
            prune.assert_not_called()
            sr.build("x")
            prune.assert_called_once_with(sr.REPORT_KEEP_DAYS)

    def test_the_inputs_are_refreshed_at_most_once_per_gap(self):
        calls = []
        fake_fx = mock.Mock(refresh=lambda: calls.append("fx"))
        fake_cal = mock.Mock(ensure=lambda: calls.append("cal"))
        with mock.patch.dict(sys.modules, {"fx": fake_fx, "outlook_calibration": fake_cal}), \
                mock.patch.dict(sr._REFRESH, {"at": None}):
            self.assertTrue(sr.refresh_inputs_once(now=1000.0))
            self.assertFalse(sr.refresh_inputs_once(now=1000.0 + 60))
            self.assertTrue(sr.refresh_inputs_once(now=1000.0 + sr.REFRESH_GAP_SECONDS + 1))
        self.assertEqual(calls, ["fx", "cal", "fx", "cal"])


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
