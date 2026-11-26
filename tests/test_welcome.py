"""The welcome page the panel opens on (2026-11-26, the owner: first this
page, then the rest)."""

import os
import re
import sys
import unittest
from unittest import mock

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app  # noqa: E402


def source(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class WelcomeRouteTests(unittest.TestCase):

    def setUp(self):
        app.invalidate()
        self.client = app.app.test_client()

    def tearDown(self):
        app.invalidate()

    def test_the_root_is_the_welcome_page_and_its_button_opens_the_panel(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('class="welcome"', page)
        self.assertIn('id="wEnter" href="/panel"', page)
        self.assertIn("tab-panel", self.client.get("/panel").get_data(as_text=True))

    def test_a_query_meant_for_the_panel_goes_straight_on(self):
        response = self.client.get("/?engine=v1&tech=%D9%81%D9%88%D9%84%D8%A7%D8%AF")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(
            "/panel?engine=v1&tech=%D9%81%D9%88%D9%84%D8%A7%D8%AF"))

    def test_every_tab_on_the_page_is_a_tab_of_the_panel(self):
        page = self.client.get("/").get_data(as_text=True)
        panel = self.client.get("/panel").get_data(as_text=True)
        tabs = re.findall(r'href="/panel#(\w+)"', page)
        self.assertGreaterEqual(len(tabs), 7)
        for tab in tabs:
            self.assertIn('data-tab="%s"' % tab, panel, tab)

    def test_the_figures_come_from_stored_data(self):
        frame = pd.DataFrame({"date": ["2026-11-24", "2026-11-25"],
                              "close": [3_400_000.0, 3_434_000.0]})
        with mock.patch.object(app, "data_version", return_value="v1"), \
                mock.patch.object(app.database, "load_index_history", return_value=frame), \
                mock.patch("fx.summary", return_value={"rates": {"free": {
                    "close": 2_838_900.0, "date": "1405/09/03", "change_1w": 0.064}}}), \
                mock.patch("market_valuation.summary", return_value={
                    "now": {"pe": 7.44, "date": "2026-11-25"}, "percentile": 61.8,
                    "bill_yield_pct": 44.65}):
            d = self.client.get("/api/welcome").get_json()
        self.assertAlmostEqual(d["index"]["change_pct"], 1.0)
        self.assertEqual(len(d["index"]["series"]), 2)
        self.assertEqual(d["dollar"]["close"], 2_838_900.0)
        self.assertEqual((d["valuation"]["pe"], d["valuation"]["bill_yield_pct"]), (7.44, 44.65))

    def test_a_missing_source_leaves_its_figure_out_not_the_page(self):
        with mock.patch.object(app, "data_version", return_value="v2"), \
                mock.patch.object(app.database, "load_index_history",
                                  side_effect=RuntimeError("locked")), \
                mock.patch("fx.summary", side_effect=OSError("gone")), \
                mock.patch("market_valuation.summary", return_value=None):
            response = self.client.get("/api/welcome")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("index", response.get_json())


class WelcomeNeedsNothingThePolicyForbids(unittest.TestCase):

    def test_the_only_script_is_the_module_from_static(self):
        html = source("templates", "welcome.html")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.I | re.S)
        self.assertEqual(len(scripts), 1)
        attrs, body = scripts[0]
        self.assertIn('type="module"', attrs)
        self.assertIn("js/welcome.js", attrs)
        self.assertEqual(body.strip(), "")
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", html, flags=re.I))

    def test_its_files_are_served_as_what_they_are(self):
        client = app.app.test_client()
        for path, kind in (("/static/js/welcome.js", "javascript"),
                           ("/static/css/welcome.css", "text/css")):
            response = client.get(path)
            try:
                self.assertEqual(response.status_code, 200, path)
                self.assertIn(kind, response.headers["Content-Type"], path)
            finally:
                response.close()


if __name__ == "__main__":
    unittest.main()
