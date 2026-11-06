"""moneyflow.market_daily: the whole market's net retail money, per session."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import moneyflow  # noqa: E402

# (date, {ins_code: (individual_buy, individual_sell)}); institutional buy
# is the same as individual sell so turnover is easy to check.
DAYS = [
    ("2026-10-25", {"1": (100, 60), "2": (50, 70), "3": (10, 10)}),   # +20
    ("2026-10-26", {"1": (80, 100), "2": (40, 60), "3": (5, 15)}),    # -50
    ("2026-10-27", {"1": (90, 50), "2": (60, 40), "3": (20, 10)}),    # +70
    # a fetch that stopped part-way: one symbol of three
    ("2026-10-28", {"1": (10, 500)}),
    ("2026-10-31", {"1": (70, 60), "2": (30, 20), "3": (15, 5)}),     # +30
]


class MarketFlowTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        conn = database._connect()
        for code in ("1", "2", "3", "9"):
            conn.execute("INSERT INTO instruments (ins_code, symbol_fa, is_equity) "
                         "VALUES (?, ?, ?)", (code, "n" + code, 0 if code == "9" else 1))
        for day, rows in DAYS:
            for code, (buy, sell) in rows.items():
                conn.execute(
                    "INSERT INTO client_type_history (ins_code, date, "
                    "individual_buy_value, individual_sell_value, "
                    "institutional_buy_value, institutional_sell_value) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (code, day, buy, sell, sell, buy))
        # a fund (not an equity) with a huge inflow must not count
        conn.execute("INSERT INTO client_type_history (ins_code, date, "
                     "individual_buy_value, individual_sell_value, "
                     "institutional_buy_value, institutional_sell_value) "
                     "VALUES ('9', '2026-10-31', 9999, 0, 0, 9999)")
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def test_net_is_summed_over_equities_per_session(self):
        r = moneyflow.market_daily(sessions=10)
        self.assertEqual([d["net_individual"] for d in r["sessions"]],
                         [20.0, -50.0, 70.0, 30.0])
        self.assertEqual(r["last"]["date"], "2026-10-31")
        self.assertEqual(r["last"]["inflow_count"], 3)
        # turnover = individual buy + institutional buy
        self.assertEqual(r["last"]["turnover"], 115.0 + 85.0)

    def test_a_part_fetched_day_is_left_out_and_counted(self):
        r = moneyflow.market_daily(sessions=10)
        self.assertNotIn("2026-10-28", [d["date"] for d in r["sessions"]])
        self.assertEqual(r["partial_days_skipped"], 1)

    def test_sums_streak_and_typical_range(self):
        r = moneyflow.market_daily(sessions=10)
        self.assertEqual(r["sum_5"], 70.0)
        self.assertEqual(r["streak"], {"days": 2, "direction": "in"})
        self.assertEqual(r["typical"]["median"], 25.0)
        self.assertEqual(r["typical"]["inflow_day_share"], 0.75)

    def test_sessions_trims_the_chart_not_the_summary(self):
        r = moneyflow.market_daily(sessions=2)
        self.assertEqual(len(r["sessions"]), 2)
        self.assertEqual(r["sum_5"], 70.0)


if __name__ == "__main__":
    unittest.main()
