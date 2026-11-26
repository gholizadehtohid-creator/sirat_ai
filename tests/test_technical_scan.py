"""The two whole-market technical cards read one scan (2026-11-26).

The owner reported that «ده سهم برتر تکنیکال» and «تکنیکال قوی + ورود پول»
"do not work": each scanned the market itself (~60 s apiece), the run button
stayed live while a gated card hid its output, the top of the list was a tie
in an arbitrary order, and "inflow" meant any net individual flow above zero
although the median liquid share runs about +4.8%.
"""

import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import moneyflow  # noqa: E402
import technical  # noqa: E402


def row(code, score, turnover, label=None, net=None):
    return {"ins_code": code, "symbol": "S" + code, "name": "", "sector": "",
            "score": score, "status": "x", "trend": None, "rsi": 50.0,
            "macd": 1.0, "momentum": None, "turnover": turnover, "call": "HOLD",
            "call_fa": "", "net": 0, "flow_score": None, "flow_label": label,
            "net_pct": net, "power_ratio": None}


def scan(rows):
    return {"rows": rows, "min_turnover": 5e9,
            "counts": {"universe": len(rows) + 3, "liquid": len(rows) + 1,
                       "liquid_scored": len(rows), "illiquid_skipped": 2,
                       "halted_skipped": 0, "no_data": 0}}


class TopTests(unittest.TestCase):

    def test_equal_scores_go_by_traded_value_and_the_tie_is_counted(self):
        s = scan([row("a", 97, 1e10), row("b", 97, 5e10), row("c", 92, 9e10),
                  row("d", 92, 2e10), row("e", 92, 1e10), row("f", 60, 1e12)])
        with mock.patch.object(technical, "market_scan",
                               side_effect=AssertionError("scanned again")):
            rep = technical.top_technical(top=3, scan=s)
        self.assertEqual([r["ins_code"] for r in rep["top"]], ["b", "a", "c"])
        m = rep["market"]
        self.assertEqual(m["cut_score"], 92)
        self.assertEqual(m["tied_left_out"], 2)
        self.assertEqual(m["strong"], 5)
        self.assertEqual(rep["top"][0]["above_pct"], round(100 * 4 / 6))

    def test_an_empty_market_is_an_empty_list(self):
        rep = technical.top_technical(top=10, scan=scan([]))
        self.assertEqual(rep["top"], [])
        self.assertIsNone(rep["market"]["median_score"])


class InflowTests(unittest.TestCase):

    def test_inflow_is_the_flow_modules_own_label_not_net_above_zero(self):
        s = scan([row("a", 90, 1e10, moneyflow.BULLISH, 30.0),
                  row("b", 95, 1e10, moneyflow.NEUTRAL, 6.0),     # net > 0, not inflow
                  row("c", 80, 1e10, moneyflow.BEARISH, 2.0),     # «خروج» with net > 0
                  row("d", 60, 1e10, moneyflow.BULLISH, 50.0),    # not strong
                  row("e", 85, 1e10, None, None)])                 # no current flow
        rep = technical.best_technical_inflow(top=15, scan=s)
        self.assertEqual([r["ins_code"] for r in rep["top"]], ["a"])
        c = rep["counts"]
        self.assertEqual((c["strong_technical"], c["strong_positive_net"],
                          c["strong_and_inflow"]), (4, 3, 1))
        self.assertEqual(rep["flow_floor"], technical.config.FLOW_BULLISH_SCORE)


class RouteTests(unittest.TestCase):

    def setUp(self):
        import app
        self.app = app
        app.invalidate()

    def tearDown(self):
        self.app.invalidate()

    def test_both_cards_read_one_scan(self):
        s = scan([row("a", 90, 1e10, moneyflow.BULLISH, 30.0)])
        client = self.app.app.test_client()
        with mock.patch.object(self.app, "data_version", return_value="v1"), \
                mock.patch.object(technical, "market_scan", return_value=s) as ms:
            top = client.get("/api/technical/top?top=10").get_json()
            inflow = client.get("/api/technical/inflow?top=15").get_json()
        self.assertEqual(ms.call_count, 1)
        self.assertEqual(top["top"][0]["ins_code"], "a")
        self.assertEqual(inflow["top"][0]["ins_code"], "a")

    def warm(self, running=False):
        import bootstrap
        with mock.patch.object(bootstrap, "is_running", return_value=running), \
                mock.patch.object(self.app, "data_version", return_value="v1"), \
                mock.patch.object(technical, "market_scan",
                                  return_value=scan([])) as ms:
            done = self.app.warm_technical()
        return done, ms.call_count

    def test_the_warmer_computes_once_and_again_before_it_ages_out(self):
        self.assertEqual(self.warm(), (True, 1))
        self.assertEqual(self.warm(), (False, 0))
        with mock.patch.object(self.app, "data_version", return_value="v1"):
            key = self.app.technical_scan_key(self.app.clean_params(None))
        self.app._cache[key]["at"] = time.time() - self.app.OUTLOOK_REWARM_AGE - 1
        self.assertEqual(self.warm(), (True, 1))

    def test_not_while_a_data_run_is_writing(self):
        self.assertEqual(self.warm(running=True), (False, 0))


class PanelTests(unittest.TestCase):

    def setUp(self):
        with open(os.path.join(ROOT, "static", "js", "panel.js"), encoding="utf-8") as fh:
            self.js = fh.read()
        with open(os.path.join(ROOT, "templates", "dashboard.html"), encoding="utf-8") as fh:
            self.html = fh.read()

    def test_the_buttons_read_the_cache(self):
        self.assertIn('on("runTopTechnicalBtn", "click", () => loadTopTechnical());', self.js)
        self.assertIn('on("runInflowBtn", "click", () => loadInflow());', self.js)

    def test_a_gated_card_locks_its_run_button(self):
        for button in ("runTopTechnicalBtn", "runInflowBtn"):
            self.assertRegex(self.html, r'id="%s"[^>]*data-gate-run' % button)
        self.assertIn('.card.levels-gated [data-gate-run]', self.js)


if __name__ == "__main__":
    unittest.main()
