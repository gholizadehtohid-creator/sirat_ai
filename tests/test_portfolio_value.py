"""Investment companies and holdings: assets at today's prices and at the
bot's fundamental values (portfolio_value.py, 2026-11-25).

The owner asked for the current value of these companies' assets, and a
forward-looking value from the fundamental state of the shares they hold.
These pin: the filing's rows are read; each is matched to its symbol and
valued today with the capital increases and dividends since; the parent's
NAV is built from them; the forward value uses only shares the bot values
on earnings; and nothing of it changes how the company itself is read.
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import database  # noqa: E402
import nav  # noqa: E402
import portfolio_value as pv  # noqa: E402

M = config.CODAL_AMOUNT_SCALE


def listed_sheet(rows):
    """The listed sheet as Codal lays it out (2026-11-25): no end-of-period
    share count, so start + change is the count."""

    columns = pd.MultiIndex.from_tuples([
        ("نام شرکت", "نام شرکت"),
        ("سرمایه (میلیون ریال)", "سرمایه (میلیون ریال)"),
        ("ارزش اسمی هر سهم (ریال)", "ارزش اسمی هر سهم (ریال)"),
        ("ابتدای دوره", "تعداد سهام"), ("ابتدای دوره", "بهای تمام شده"),
        ("ابتدای دوره", "ارزش بازار"),
        ("تغییرات", "تعداد سهام"), ("تغییرات", "بهای تمام شده"),
        ("تغییرات", "ارزش بازار"),
        ("انتهای دوره", "درصد مالکیت"), ("انتهای دوره", "بهای تمام شده"),
        ("انتهای دوره", "ارزش بازار"),
        ("انتهای دوره", "بهای تمام شده هر سهم (ریال)"),
        ("انتهای دوره", "ارزش هر سهم (ریال)"),
        ("انتهای دوره", "افزایش (کاهش)"),
    ])
    data = [["سهام پذیرفته شده در بورس و فرابورس"] + [None] * 14]
    for name, start, change, cost, market, per_share in rows:
        data.append([name, "۱,۰۰۰", "۱,۰۰۰", start, cost, market, change, "۰", "۰",
                     None, cost, market, None, per_share, None])
    data.append(["جمع", None, None, None, None, None, None, None, None,
                 None, "۰", "۰", None, None, None])
    return pd.DataFrame(data, columns=columns)


def unlisted_sheet(rows):
    columns = pd.MultiIndex.from_tuples([
        ("نام شرکت", "نام شرکت"),
        ("سرمایه (میلیون ریال)", "سرمایه (میلیون ریال)"),
        ("ابتدای دوره", "تعداد سهام"), ("ابتدای دوره", "بهای تمام شده"),
        ("تغییرات", "تعداد سهام"), ("تغییرات", "بهای تمام شده"),
        ("انتهای دوره", "درصد مالکیت"), ("انتهای دوره", "بهای تمام شده"),
    ])
    data = [[name, "۱۰", count, cost, "۰", "۰", "۵۰", cost] for name, count, cost in rows]
    data.append(["جمع", None, None, None, None, None, None, "۰"])
    return pd.DataFrame(data, columns=columns)


SHEETS = {
    "صورت وضعیت پرتفوی سهام قابل معامله در بازار سرمایه": [listed_sheet([
        ("پتروشيمي مارون", "۱,۰۰۰,۰۰۰", "۲۰۰,۰۰۰", "۱۰", "۳۶", "۳۰,۰۰۰"),
        ("سنگ اهن گل گهر (حق تقدم)", "۵۰۰,۰۰۰", "۰", "۱", "۲", "۴,۰۰۰"),
    ])],
    "صورت وضعیت پرتفوی سهام سایر شرکتها": [unlisted_sheet([
        ("توسعه خصوصی", "۷۰۰", "۵"),
    ])],
}


class ExtractTests(unittest.TestCase):

    def test_every_holding_row_and_nothing_else(self):
        rows = nav.extract_holdings(SHEETS)
        self.assertEqual([r["section"] for r in rows], ["listed", "listed", "unlisted"])
        first = rows[0]
        self.assertEqual(first["shares"], 1_200_000)            # start + change
        self.assertEqual(first["cost"], 10 * M)
        self.assertEqual(first["market"], 36 * M)
        self.assertEqual(first["value_per_share"], 30_000)
        self.assertEqual(first["capital"], 1000 * M)
        self.assertEqual(rows[2]["cost"], 5 * M)
        self.assertIsNone(rows[2]["market"])

    def test_the_totals_reader_carries_the_rows(self):
        out = nav.extract_portfolio(SHEETS)
        self.assertEqual(len(out["rows"]), 3)


class StoreTests(unittest.TestCase):

    def setUp(self):
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        database.create_database()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.market + suffix)
            except OSError:
                pass

    def record(self, published, rows):
        return {"period_end": "1405/08/30", "tracing_no": published,
                "published_at": published, "listed_cost": 1.0,
                "listed_market": 2.0, "rows": rows}

    def test_the_rows_go_and_come_with_their_portfolio(self):
        rows = nav.extract_holdings(SHEETS)
        self.assertEqual(database.save_codal_portfolio("P", self.record("1405/09/05", rows)), 1)
        got = database.load_portfolio_rows([("P", "1405/08/30")])
        self.assertEqual([r["name"] for r in got[("P", "1405/08/30")]],
                         [r["name"] for r in rows])

    def test_an_older_release_leaves_the_newer_rows(self):
        # Which release is newer is read from the letter index.
        database.save_codal_letters("P", [
            {"tracing_no": "1405/09/05", "letter_code": "ن-31", "title": "اصلاحیه",
             "published_at": "1405/09/05 10:00:00"},
            {"tracing_no": "1405/09/01", "letter_code": "ن-31", "title": "اصلی",
             "published_at": "1405/09/01 10:00:00"}])
        rows = nav.extract_holdings(SHEETS)
        database.save_codal_portfolio("P", self.record("1405/09/05", rows))
        self.assertEqual(database.save_codal_portfolio(
            "P", self.record("1405/09/01", rows[:1])), 0)
        self.assertEqual(len(database.load_portfolio_rows()[("P", "1405/08/30")]), 3)


class Closes(pv._Closes):
    day = "2026-11-20"


INDEX = None


def index():
    shares = {}
    rights = {}
    names = {}
    symbols = {}
    for code, symbol, name, right in (
            ("marun", "مارون", "پتروشیمی مارون", False),
            ("golgohar", "کگل", "معدنی و صنعتی گل گهر", False),
            ("golgohar_r", "کگلح", "ح . معدنی و صنعتی گل گهر", True),
            ("sang", "کگهر", "سنگ آهن گل گهر", False),
            ("tamin", "تاپیکو", "س. نفت و گاز و پتروشیمی تأمین", False),
            ("twin1", "فاسمین", "کالسیمین", False),
            ("twin2", "فاسمین2", "کالسیمین", False)):
        key, _ = pv.name_key(name)
        (rights if right else shares).setdefault(key, []).append(code)
        names[code] = pv._words(name)[0]
        symbols[code] = symbol
    by_symbol = {pv.normalize(s).translate(pv._FOLD): [c] for c, s in symbols.items()
                 if c not in ("golgohar_r",)}
    return {"shares": shares, "rights": rights, "names": names, "symbols": symbols,
            "by_symbol": by_symbol, "rights_codes": {"golgohar_r"}}


class MatchTests(unittest.TestCase):

    def setUp(self):
        pv._matches.clear()
        self.index = index()
        self.closes = Closes({"marun": 30_000.0, "sang": 4_000.0, "tamin": 44_350.0,
                              "twin1": 5_000.0, "twin2": 7_000.0,
                              "golgohar": 9_000.0})

    def match(self, name, vps=None):
        return pv.match({"name": name, "value_per_share": vps}, self.closes, self.index)

    def test_spellings_fold_together(self):
        self.assertEqual(pv.name_key("سنگ آهن گل گهر"), pv.name_key("سنگ اهن گل گهر"))
        self.assertEqual(pv.name_key("س. نفت و گاز و پتروشیمی تأمین")[0],
                         pv.name_key("سرمایه گذاری نفت و گاز و پتروشیمی تامین")[0])
        self.assertTrue(pv.name_key("تامین (حق تقدم)")[1])

    def test_by_name(self):
        self.assertEqual(self.match("پتروشيمي مارون", 30_000), ("marun", "name"))
        self.assertEqual(self.match("سرمایه گذاری نفت و گاز و پتروشیمی تامین", 44_350),
                         ("tamin", "name"))

    def test_the_price_tells_two_namesakes_apart(self):
        self.assertEqual(self.match("کالسیمین", 7_000), ("twin2", "name+price"))
        self.assertEqual(self.match("کالسیمین", 9_999), (None, None))

    def test_a_symbol_written_as_the_name(self):
        self.assertEqual(self.match("مارون"), ("marun", "symbol"))

    def test_a_right_finds_the_right_or_its_share(self):
        self.assertEqual(self.match("معدنی و صنعتی گل گهر (حق تقدم)"), ("golgohar_r", "name"))
        self.assertEqual(self.match("سنگ اهن گل گهر (حق تقدم)"), ("sang", "underlying"))

    def test_a_shared_word_and_the_same_price(self):
        self.assertEqual(self.match("شرکت مارون پتروشیمی بندر", 30_000)[0], "marun")


class SinceTests(unittest.TestCase):

    ACTIONS = {"x": [("2026-10-01", "capital", 2.0, None),       # before: ignored
                     ("2026-11-02", "dividend", None, 100.0),
                     ("2026-11-10", "capital", 1.5, None),
                     ("2026-11-15", "dividend", None, 10.0)]}

    def test_increases_multiply_and_later_dividends_count_on_the_new_shares(self):
        factor, paid = pv.since("x", date(2026, 10, 22), self.ACTIONS)
        self.assertEqual(factor, 1.5)
        self.assertEqual(paid, 100.0 + 10.0 * 1.5)


class ValueTests(unittest.TestCase):

    ROWS = [
        {"section": "listed", "name": "پتروشیمی مارون", "shares": 1000.0, "cost": 1_000_000.0,
         "market": 30_000_000.0, "value_per_share": 30_000.0},
        {"section": "listed", "name": "نامعلوم بزرگ", "shares": 10.0, "cost": 100.0,
         "market": 500.0, "value_per_share": 50.0},
        {"section": "unlisted", "name": "خصوصی", "shares": 5.0, "cost": 2_000_000.0,
         "market": None, "value_per_share": None},
    ]

    def value(self, actions=None):
        pv._matches.clear()
        with mock.patch.object(pv, "closes_on", return_value=Closes({"marun": 30_000.0})), \
                mock.patch.object(pv, "_instrument_index", return_value=index()):
            return pv.value_today({"period_end": "1405/07/30"}, self.ROWS,
                                  equity=10_000_000.0, shares=1000.0, price=20_000.0,
                                  prices={"marun": 36_000.0}, parent="parent",
                                  actions=actions or {})

    def test_today_price_for_what_is_matched_period_value_for_the_rest(self):
        today, valued = self.value()
        self.assertEqual(today["listed_today"], 1000 * 36_000.0 + 500.0)
        self.assertEqual(today["unpriced"], 1)
        # equity + (today - cost) over the listed rows
        self.assertEqual(today["nav"], 10_000_000 + (36_000_500 - 1_000_100))
        self.assertAlmostEqual(today["price_to_nav"], 20_000 * 1000 / today["nav"])
        self.assertEqual(today["unlisted_cost"], 2_000_000.0)
        self.assertFalse(today["complete"])
        self.assertAlmostEqual(today["listed_change_pct"],
                               (36_000_500 / 30_000_500 - 1) * 100)

    def test_increases_dividends_and_the_parents_own_dividend(self):
        actions = {"marun": [("2026-11-01", "capital", 1.2, None),
                             ("2026-11-05", "dividend", None, 1_000.0)],
                   "parent": [("2026-11-03", "dividend", None, 500.0)]}
        today, _ = self.value(actions)
        self.assertEqual(today["listed_today"], 1000 * 1.2 * 36_000.0 + 500.0)
        self.assertEqual(today["dividends_in"], 1000 * 1.2 * 1_000.0)
        self.assertEqual(today["own_dividend"], 500.0 * 1000)
        self.assertEqual(today["capital_adjusted"], 1)

    def test_a_right_takes_its_share_from_the_same_filing(self):
        """The share matched by price, the right not at all: the right is a
        right to that share, worth its price less the par it costs."""
        rows = [{"section": "listed", "name": "شرکت ایکس", "shares": 10.0,
                 "cost": 10.0, "market": 100.0, "value_per_share": 10.0},
                {"section": "listed", "name": "شرکت ایکس (حق تقدم)", "shares": 4.0,
                 "cost": 4.0, "market": 8.0, "value_per_share": 2.0}]

        def fake(row, closes, index=None):
            return ("marun", "price+word") if "حق" not in row["name"] else (None, None)

        with mock.patch.object(pv, "closes_on", return_value=Closes({})), \
                mock.patch.object(pv, "_instrument_index", return_value=index()), \
                mock.patch.object(pv, "match", side_effect=fake):
            today, valued = pv.value_today({"period_end": "1405/07/30"}, rows, 0.0,
                                           1.0, 1.0, {"marun": 3000.0}, actions={})
        self.assertEqual(valued[1]["match"], "underlying")
        self.assertTrue(valued[1]["right"])
        self.assertEqual(valued[1]["value_today"], 4.0 * (3000.0 - pv.PAR))
        self.assertEqual(today["unpriced"], 0)

    def test_no_rows_or_no_equity_is_no_value(self):
        self.assertIsNone(pv.value_today({"period_end": "1405/07/30"}, [], 1.0, 1.0, 1.0, {}))
        self.assertIsNone(pv.value_today({"period_end": "1405/07/30"}, self.ROWS, None, 1.0, 1.0, {}))


class ForwardTests(unittest.TestCase):

    def valued(self):
        return [
            {"ins_code": "a", "priced": True, "match": "name", "shares": 10.0, "factor": 1.0,
             "value_today": 1000.0, "period_value": 900.0, "name": "a"},
            {"ins_code": "b", "priced": True, "match": "name", "shares": 10.0, "factor": 1.0,
             "value_today": 1000.0, "period_value": 1000.0, "name": "b"},
            {"ins_code": "c", "priced": True, "match": "name", "shares": 10.0, "factor": 1.0,
             "value_today": 1000.0, "period_value": 1000.0, "name": "c"},
        ]

    def test_only_shares_valued_on_earnings_move_it(self):
        today = {"nav": 5000.0, "listed_today": 3000.0}
        fair = {"a": {"basis": "pe", "central": 150.0, "low": 120.0, "high": 200.0},
                "b": {"basis": "pe", "central": 300.0, "doubtful": True},   # doubtful
                "c": {"basis": "nav", "central": 500.0}}                    # a financial
        valued = self.valued()
        out = pv.fundamental(today, valued, fair, price=1.0, shares=1000.0)
        self.assertEqual(out["nav"], 5000.0 + (1500.0 - 1000.0))
        self.assertEqual(out["low"], 5000.0 + (1200.0 - 1000.0))
        self.assertEqual(out["high"], 5000.0 + (2000.0 - 1000.0))
        self.assertAlmostEqual(out["covered_pct"], 1000 / 3000 * 100)
        self.assertEqual(valued[0]["fair_per_share"], 150.0)
        self.assertIsNone(valued[1]["fair_per_share"])

    def test_the_top_holdings_by_todays_value(self):
        valued = self.valued()
        valued[2]["value_today"] = 5000.0
        top = pv.top(valued, limit=2)
        self.assertEqual([t["name"] for t in top], ["c", "a"])
        self.assertAlmostEqual(top[0]["weight_pct"], 5000 / 7000 * 100)


class OutlookTests(unittest.TestCase):

    def test_attach_forward_drops_the_working_list(self):
        import fundamental_outlook as fo
        result = {"valuation": {"nav_today": {"nav": 100.0, "listed_today": 10.0},
                                "price": 1.0, "shares": 10.0},
                  "_valued": [{"ins_code": "a", "priced": True, "match": "name",
                               "shares": 1.0, "factor": 1.0, "value_today": 10.0,
                               "period_value": 10.0, "name": "a"}]}
        fo.attach_forward(result, {"a": {"basis": "pe", "central": 20.0}})
        self.assertNotIn("_valued", result)
        self.assertEqual(result["valuation"]["nav_forward"]["nav"], 110.0)
        self.assertEqual(result["holdings"][0]["fair_per_share"], 20.0)

    def test_a_financial_is_valued_on_todays_nav_where_held(self):
        import fundamental_outlook as fo
        fair = fo.fair_value(fo.FINANCIAL, "x", None, None, None, None, 500.0, None,
                             nav_result={"nav": 1000.0, "complete": False},
                             shares=10.0, nav_today={"nav": 2000.0})
        self.assertEqual(fair["central"], 200.0)
        self.assertIn("قیمت امروز", fair["anchors"][0]["label"])


if __name__ == "__main__":
    unittest.main()
