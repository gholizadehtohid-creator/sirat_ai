"""fund_flows.py: individuals' money among the funds, in a table of its own."""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_content  # noqa: E402
import database  # noqa: E402
import fund_flows as ff  # noqa: E402
from providers.base import ProviderResult  # noqa: E402


class CategoryTests(unittest.TestCase):

    def test_the_kind_is_read_from_the_name(self):
        self.assertEqual(ff.category("صندوق س.اعتماد افرین پارسیان-د"), "fixed")
        self.assertEqual(ff.category("صندوق س نوع دوم افق اتی-ثابت"), "fixed")
        self.assertEqual(ff.category("صندوق س.پشتوانه طلااسمان التون"), "gold")
        self.assertEqual(ff.category("صندوق س.سهام اوای معیار-س"), "equity")
        self.assertEqual(ff.category("صندوق س صنایع مفید1- بخشی"), "equity")
        self.assertEqual(ff.category("صندوق س رشدافرین سرمایه-مختلط"), "mixed")
        self.assertEqual(ff.category("صندوق س سهامی کاریزما- اهرمی"), "leveraged")
        self.assertEqual(ff.category("ص.س.املاک ومستغلات ارزش مسکن"), "real_estate")
        self.assertIsNone(ff.category("ص.ج.فیروزه70%تادیه"))


class StoreTests(unittest.TestCase):
    """A temporary market store: the flow lands in fund_flow_history and
    never in client_type_history, which is read as the share market's."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.patch = mock.patch.object(database, "DATABASE_NAME", self.path)
        self.patch.start()
        database.create_database()
        self.kinds = {"1": {"category": "fixed"}, "2": {"category": "fixed"},
                      "3": {"category": "gold"}}

    def tearDown(self):
        self.patch.stop()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                try:
                    os.unlink(self.path + suffix)
                except OSError:
                    pass

    def frame(self, days, buy, sell):
        rows = []
        for i, d in enumerate(days):
            row = {f: 0.0 for f in database.CLIENT_TYPE_FIELDS}
            row.update(date=d, individual_buy_value=buy * (i + 1),
                       individual_sell_value=sell, institutional_buy_value=5.0)
            rows.append(row)
        return pd.DataFrame(rows)

    def test_update_stores_in_its_own_table_and_summary_sums_by_kind(self):
        days = ["2026-11-%02d" % d for d in (22, 23, 24, 25)]
        provider = mock.Mock()
        provider.get_client_type_histories.return_value = {
            "1": ProviderResult(data=self.frame(days, 10.0, 4.0), source="t"),
            "2": ProviderResult(data=self.frame(days, 1.0, 1.0), source="t"),
            "3": ProviderResult.failure("t", "no data"),
        }
        with mock.patch.object(ff, "funds", return_value=self.kinds):
            message = ff.update(provider=provider, force=True)
            s = ff.summary(kinds=self.kinds)
        self.assertIn("8 rows stored, 1 failed", message)
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM client_type_history").fetchone()[0], 0)
        finally:
            conn.close()
        fixed = [c for c in s["categories"] if c["key"] == "fixed"][0]
        # last day: fund 1 buys 40 sells 4, fund 2 buys 4 sells 1
        self.assertEqual(fixed["net_1"], 39)
        self.assertEqual(fixed["funds"], 2)
        self.assertEqual(s["as_of"], "2026-11-25")
        self.assertFalse([c for c in s["categories"] if c["key"] == "gold"])

    def test_a_recent_run_is_not_repeated(self):
        provider = mock.Mock()
        provider.get_client_type_histories.return_value = {}
        with mock.patch.object(ff, "funds", return_value=self.kinds):
            ff.update(provider=provider, force=True)
            message = ff.update(provider=provider)
        self.assertIn("current", message)
        self.assertEqual(provider.get_client_type_histories.call_count, 1)

    def test_update_never_raises(self):
        provider = mock.Mock()
        provider.get_client_type_histories.side_effect = OSError("no route")
        with mock.patch.object(ff, "funds", return_value=self.kinds):
            self.assertIn("failed", ff.update(provider=provider, force=True))


class BotLineTests(unittest.TestCase):

    def test_five_sessions_of_the_main_kinds(self):
        rep = {"categories": [{"key": "fixed", "label": "درآمد ثابت", "net_5": 3.2e12},
                              {"key": "gold", "label": "طلا", "net_5": -1.5e12}]}
        line = bot_content.fund_flows_line(rep)
        self.assertTrue(line.startswith("💧"))
        self.assertIn("درآمد ثابت", line)
        self.assertIn("طلا", line)
        self.assertEqual(bot_content.fund_flows_line(None), "")


if __name__ == "__main__":
    unittest.main()
