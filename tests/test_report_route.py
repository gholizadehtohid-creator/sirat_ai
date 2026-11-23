"""/api/report: the PDF route's answers, with the builder stood in for
(no browser is started and no store is read)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as panel  # noqa: E402
import stock_report  # noqa: E402


class ReportRouteTests(unittest.TestCase):

    def setUp(self):
        self.client = panel.app.test_client()

    def test_a_built_report_comes_back_as_a_pdf(self):
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.write(handle, b"%PDF-1.4 test")
        os.close(handle)
        try:
            with mock.patch.object(stock_report, "build", return_value=path) as build:
                response = self.client.get("/api/report?symbol=x")
            build.assert_called_once_with("x")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/pdf")
            self.assertTrue(response.data.startswith(b"%PDF"))
            response.close()
        finally:
            os.unlink(path)

    def test_no_symbol_is_a_short_page_not_a_build(self):
        with mock.patch.object(stock_report, "build") as build:
            response = self.client.get("/api/report")
        build.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertIn("text/html", response.content_type)

    def test_a_report_that_cannot_be_built_says_why_escaped(self):
        error = stock_report.ReportError("نماد پیدا نشد: <b>x</b>")
        with mock.patch.object(stock_report, "build", side_effect=error):
            response = self.client.get("/api/report?symbol=x")
        self.assertEqual(response.status_code, 404)
        text = response.get_data(as_text=True)
        self.assertIn("نماد پیدا نشد", text)
        self.assertNotIn("<b>x</b>", text)

    def test_an_unexpected_failure_is_a_page_and_frees_the_builder(self):
        with mock.patch.object(stock_report, "build", side_effect=IndexError("boom")):
            response = self.client.get("/api/report?symbol=x")
        self.assertEqual(response.status_code, 500)
        self.assertIn("IndexError", response.get_data(as_text=True))
        # the lock was released: the next request builds
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.write(handle, b"%PDF-1.4 test")
        os.close(handle)
        try:
            with mock.patch.object(stock_report, "build", return_value=path):
                again = self.client.get("/api/report?symbol=x")
            self.assertEqual(again.status_code, 200)
            again.close()
        finally:
            os.unlink(path)

    def test_another_sites_page_cannot_ask_for_one(self):
        with mock.patch.object(stock_report, "build") as build:
            response = self.client.get("/api/report?symbol=x",
                                       headers={"Sec-Fetch-Site": "cross-site"})
        build.assert_not_called()
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
