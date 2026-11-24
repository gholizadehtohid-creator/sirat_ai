"""corporate_actions and corporate_calendar: the exchange's adjustment lists,
the total-return basis built on them, and the AGM / capital calendar.

Offline: the network is a fake session, the stores are temporary files.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import corporate_actions as ca  # noqa: E402
import corporate_calendar as cc  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402


def frame(rows):
    """[(date, open, close, volume)] -> a bars frame as the store returns."""

    return pd.DataFrame({
        "date": pd.to_datetime([r[0] for r in rows]),
        "open": [float(r[1]) for r in rows],
        "high": [float(max(r[1], r[2])) for r in rows],
        "low": [float(min(r[1], r[2])) for r in rows],
        "close": [float(r[2]) for r in rows],
        "volume": [float(r[3]) for r in rows],
    })


# فولاد around its FY1404 AGM, as stored: the AGM session closes at the old
# price, the next is the exchange's untraded re-price (DPS 55).
DIVIDEND_BARS = [
    ("2026-07-29", 2600, 2604, 1e6),
    ("2026-08-02", 2604, 2604, 0),
    ("2026-08-03", 2604, 2604, 0),
    ("2026-08-05", 2549, 2549, 0),
    ("2026-08-06", 2560, 2570, 5e5),
]
DIVIDEND_LIST = [{"insCode": 1, "dEven": 20260803, "pClosing": 2549.0,
                  "pClosingNotAdjusted": 2604.0}]

# فولاد 2019-02-19: the old convention, a capital increase the price list
# carries, dated at its first ex session.
OLD_BARS = [
    ("2019-02-12", 4920, 4888, 1e6),
    ("2019-02-18", 4888, 4888, 0),
    ("2019-02-19", 2820, 2820, 0),
    ("2019-02-20", 3010, 3046, 1e6),
]
OLD_LIST = [{"dEven": 20190219, "pClosing": 2820.0,
             "pClosingNotAdjusted": 4888.0}]
OLD_SHARES = [{"dEven": 20190219, "numberOfShareOld": 75e9,
               "numberOfShareNew": 130e9}]


class ParseTests(unittest.TestCase):

    def test_adjust_rows_oldest_first_with_same_day_order(self):
        rows = ca.parse_adjust([
            {"dEven": 20260803, "pClosing": 2549, "pClosingNotAdjusted": 2604},
            # newest first: on one day the later row is the earlier event
            {"dEven": 20110705, "pClosing": 4911, "pClosingNotAdjusted": 12991},
            {"dEven": 20110705, "pClosing": 12991,
             "pClosingNotAdjusted": 15241},
            {"dEven": 20100101, "pClosing": None, "pClosingNotAdjusted": 5},
            "junk",
        ])
        self.assertEqual([r["date"] for r in rows],
                         ["2011-07-05", "2011-07-05", "2026-08-03"])
        self.assertEqual([(r["seq"], r["not_adjusted"]) for r in rows[:2]],
                         [(0, 15241.0), (1, 12991.0)])

    def test_share_rows_carry_the_ratio(self):
        rows = ca.parse_shares([{"dEven": 20260330, "numberOfShareOld": 1.5e12,
                                 "numberOfShareNew": 1.935e12},
                                {"dEven": 20260101, "numberOfShareOld": 0,
                                 "numberOfShareNew": 1}])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["ratio"], 1.29)
        self.assertEqual(rows[0]["date"], "2026-03-30")

    def test_dates(self):
        self.assertEqual(ca.iso_from_deven(20260803), "2026-08-03")
        self.assertIsNone(ca.iso_from_deven("x"))
        self.assertEqual(ca.to_jalali("2026-08-03"), "1405/05/12")
        self.assertEqual(ca.to_jalali("2026-11-24"), "1405/09/03")


class ClassifyTests(unittest.TestCase):

    def test_new_convention_dividend_starts_at_the_next_session(self):
        bars = ca.Bars(frame(DIVIDEND_BARS))
        found = ca.classify(ca.parse_adjust(DIVIDEND_LIST), [], bars)
        self.assertEqual(len(found), 1)
        event = found[0]
        self.assertEqual(event["kind"], "dividend")
        self.assertEqual(event["convention"], "last_cum")
        self.assertEqual(event["ex_date"], "2026-08-05")
        self.assertEqual(event["dps"], 55.0)
        self.assertAlmostEqual(event["factor"], 2549 / 2604)

    def test_old_convention_increase_matched_to_its_share_change(self):
        bars = ca.Bars(frame(OLD_BARS))
        found = ca.classify(ca.parse_adjust(OLD_LIST),
                            ca.parse_shares(OLD_SHARES), bars)
        self.assertEqual(len(found), 1)
        event = found[0]
        self.assertEqual(event["source"], "adjust_list")
        self.assertEqual(event["kind"], "capital")
        self.assertEqual(event["convention"], "first_ex")
        self.assertEqual(event["ex_date"], "2019-02-19")
        self.assertEqual(event["matched_share_change"], "2019-02-19")
        self.assertIsNone(event["dps"])

    def test_increase_missing_from_the_price_list_rebuilt_exactly(self):
        # فولاد 2026-03-30: 3359 -> 2604 on an untraded bar = 1/1.29.
        bars = ca.Bars(frame([("2026-03-25", 3350, 3359, 1e6),
                              ("2026-03-29", 3359, 3359, 0),
                              ("2026-03-30", 2604, 2604, 0),
                              ("2026-03-31", 2604, 2604, 0)]))
        shares = ca.parse_shares([{"dEven": 20260330,
                                   "numberOfShareOld": 1.5e12,
                                   "numberOfShareNew": 1.935e12}])
        (event,) = ca.classify([], shares, bars)
        self.assertEqual(event["source"], "share_change")
        self.assertEqual(event["kind"], "capital")
        self.assertEqual(event["convention"], "observed_reprice")
        self.assertEqual(event["ex_date"], "2026-03-30")
        self.assertAlmostEqual(event["factor"], 2604 / 3359)

    def test_traded_reopening_clamped_into_the_bonus_rights_interval(self):
        # فولاد 2023-03-19: opened 5770 after 7940, x1.5094. Bonus would be
        # 5260, all-cash rights 5598: the base is taken as 5598.
        bars = ca.Bars(frame([("2023-03-18", 7940, 7940, 0),
                              ("2023-03-19", 5770, 5890, 8.9e8)]))
        shares = ca.parse_shares([{"dEven": 20230319,
                                   "numberOfShareOld": 530e9,
                                   "numberOfShareNew": 800e9}])
        (event,) = ca.classify([], shares, bars)
        self.assertEqual(event["convention"], "observed_open")
        ratio = 800 / 530
        low, high = 1 / ratio, (7940 + 1000 * (ratio - 1)) / (ratio * 7940)
        self.assertAlmostEqual(event["factor_low"], low)
        self.assertAlmostEqual(event["factor_high"], high)
        self.assertAlmostEqual(event["factor"], high)

    def test_reopening_after_a_halt_may_open_well_above_the_base(self):
        # اعتلا 2023-02-13: halted at 4455, x1.488, first trade 3720 -- 12%
        # above the all-rights base. Read as the increase at that base.
        bars = ca.Bars(frame([("2023-02-08", 4455, 4455, 1e5),
                              ("2023-02-12", 4455, 4455, 0),
                              ("2023-02-13", 3720, 3663, 1e6)]))
        shares = ca.parse_shares([{"dEven": 20230213,
                                   "numberOfShareOld": 100,
                                   "numberOfShareNew": 148.8}])
        (event,) = ca.classify([], shares, bars)
        self.assertEqual(event["convention"], "observed_open")
        self.assertAlmostEqual(event["factor"], event["factor_high"])

    def test_an_untraded_reprice_must_fit_the_increase(self):
        # A frozen close cut 5% (a dividend's re-price) is not a x1.2 bonus.
        bars = ca.Bars(frame([("2024-05-01", 5000, 5000, 1e5),
                              ("2024-05-02", 5000, 5000, 0),
                              ("2024-05-04", 4750, 4750, 0),
                              ("2024-05-05", 4750, 4750, 0)]))
        shares = ca.parse_shares([{"dEven": 20240504,
                                   "numberOfShareOld": 100,
                                   "numberOfShareNew": 120}])
        (event,) = ca.classify([], shares, bars)
        self.assertEqual(event["kind"], "capital_unpriced")

    def test_open_inside_the_interval_is_kept(self):
        bars = ca.Bars(frame([("2026-07-29", 20020, 20020, 0),
                              ("2026-08-01", 14600, 14860, 1e9)]))
        shares = ca.parse_shares([{"dEven": 20260801,
                                   "numberOfShareOld": 1.05e12,
                                   "numberOfShareNew": 1.44e12}])
        (event,) = ca.classify([], shares, bars)
        self.assertAlmostEqual(event["factor"], 14600 / 20020)

    def test_share_change_without_a_repricing_is_not_applied(self):
        flat = [("2025-01-%02d" % d, 5000, 5000, 1e6) for d in range(1, 20)]
        shares = ca.parse_shares([{"dEven": 20250110,
                                   "numberOfShareOld": 100e9,
                                   "numberOfShareNew": 150e9}])
        (event,) = ca.classify([], shares, ca.Bars(frame(flat)))
        self.assertEqual(event["kind"], "capital_unpriced")
        self.assertEqual(event["factor"], 1.0)

    def test_share_change_before_the_history_is_assumed_bonus(self):
        shares = ca.parse_shares([{"dEven": 20110214,
                                   "numberOfShareOld": 100e9,
                                   "numberOfShareNew": 200e9}])
        (event,) = ca.classify([], shares, ca.Bars(frame(DIVIDEND_BARS)))
        self.assertEqual(event["convention"], "assumed")
        self.assertEqual(event["factor"], 0.5)
        self.assertEqual(event["ex_observed"], 0)

    def test_dividend_and_increase_on_one_reopening(self):
        # The exchange's dividend and an unlisted increase land on the same
        # bar: the increase is priced from the close net of the dividend.
        bars = ca.Bars(frame([("2024-01-01", 1000, 1100, 1e6),
                              ("2024-01-02", 1100, 1100, 0),
                              ("2024-01-03", 500, 500, 0)]))
        adjust = ca.parse_adjust([{"dEven": 20240102, "pClosing": 1000,
                                   "pClosingNotAdjusted": 1100}])
        shares = ca.parse_shares([{"dEven": 20240103,
                                   "numberOfShareOld": 1, "numberOfShareNew": 2}])
        found = ca.classify(adjust, shares, bars)
        capital = [e for e in found if e["source"] == "share_change"][0]
        self.assertAlmostEqual(capital["not_adjusted"], 1000.0)
        self.assertAlmostEqual(capital["factor"], 0.5)

    def test_a_row_per_session_is_not_a_corporate_action(self):
        # اکالا: one "adjustment" a session, each to the next day's price.
        days = ["2019-02-%02d" % d for d in (6, 10, 12, 13, 16)]
        rows = [{"dEven": int(d.replace("-", "")), "pClosing": 900 + i,
                 "pClosingNotAdjusted": 950 + i} for i, d in enumerate(days)]
        # A dividend and an increase a week apart are two real events.
        rows += [{"dEven": 20160712, "pClosing": 1397,
                  "pClosingNotAdjusted": 1597},
                 {"dEven": 20160719, "pClosing": 1344,
                  "pClosingNotAdjusted": 1397}]
        found = ca.classify(ca.parse_adjust(rows), [], ca.Bars(frame([])))
        kinds = {e["date"]: e["kind"] for e in found}
        self.assertTrue(all(kinds[d] == "suspect" for d in days))
        self.assertEqual(kinds["2016-07-12"], "dividend")
        self.assertEqual(kinds["2016-07-19"], "dividend")
        out = ca.exchange_adjust(
            frame([("2019-02-01", 950, 950, 1), ("2019-03-01", 960, 960, 1)]),
            events=found)
        self.assertEqual(list(out["close"]), [950.0, 960.0])

    def test_a_registration_reversed_days_later_changes_nothing(self):
        shares = ca.parse_shares([
            {"dEven": 20220124, "numberOfShareOld": 100, "numberOfShareNew": 78},
            {"dEven": 20220126, "numberOfShareOld": 78, "numberOfShareNew": 100}])
        found = ca.classify([], shares, ca.Bars(frame(DIVIDEND_BARS)))
        self.assertEqual([e["kind"] for e in found],
                         ["share_correction", "share_correction"])
        self.assertTrue(all(e["factor"] == 1.0 for e in found))

    def test_a_reversed_pair_the_exchange_repriced_is_flattened(self):
        # وبیمه 2021-01-16/19: x0.4 then x2.5; the frozen close went x2.149
        # and back. The spike leaves the total-return series.
        bars = frame([("2021-01-13", 1000, 1000, 1e6),
                      ("2021-01-16", 2149, 2149, 0),
                      ("2021-01-17", 2149, 2149, 0),
                      ("2021-01-19", 994, 994, 0),
                      ("2021-01-20", 1000, 1010, 1e6)])
        shares = ca.parse_shares([
            {"dEven": 20210116, "numberOfShareOld": 100, "numberOfShareNew": 40},
            {"dEven": 20210119, "numberOfShareOld": 40, "numberOfShareNew": 100}])
        found = ca.classify([], shares, ca.Bars(bars))
        self.assertEqual({e["convention"] for e in found},
                         {"reversed_pair_reprice"})
        out = ca.exchange_adjust(bars, events=found)
        self.assertEqual([round(v) for v in out["close"]],
                         [1000, 1000, 1000, 994, 1010])

    def test_event_after_the_newest_bar(self):
        bars = ca.Bars(frame(DIVIDEND_BARS[:3]))
        (event,) = ca.classify(ca.parse_adjust(DIVIDEND_LIST), [], bars)
        self.assertEqual(event["ex_date"], "2026-08-04")
        self.assertEqual(event["ex_observed"], 0)


class AdjustTests(unittest.TestCase):

    def test_prices_before_the_ex_date_scaled_volume_untouched(self):
        bars = frame(DIVIDEND_BARS)
        evs = ca.classify(ca.parse_adjust(DIVIDEND_LIST), [],
                          ca.Bars(bars))
        out = ca.exchange_adjust(bars, events=evs)
        factor = 2549 / 2604
        self.assertAlmostEqual(out["close"].iloc[2], 2604 * factor)
        self.assertEqual(out["close"].iloc[3], 2549)
        self.assertEqual(list(out["volume"]), list(bars["volume"]))
        self.assertEqual(list(bars["close"]), [2604, 2604, 2604, 2549, 2570])
        # Continuous across the re-price: the dividend is not a loss.
        self.assertAlmostEqual(out["close"].iloc[3] / out["close"].iloc[2], 1)
        self.assertEqual(len(out.attrs["exchange_events"]), 1)

    def test_factors_compound_and_unpriced_is_ignored(self):
        bars = frame([("2024-01-01", 100, 100, 1), ("2024-02-01", 50, 50, 1),
                      ("2024-03-01", 45, 45, 1), ("2024-04-01", 45, 45, 1)])
        evs = [{"ex_date": "2024-02-01", "factor": 0.5, "kind": "capital"},
               {"ex_date": "2024-03-01", "factor": 0.9, "kind": "dividend"},
               {"ex_date": "2024-04-01", "factor": 1.0,
                "kind": "capital_unpriced"}]
        out = ca.exchange_adjust(bars, events=evs)
        self.assertAlmostEqual(out["close"].iloc[0], 100 * 0.5 * 0.9)
        self.assertAlmostEqual(out["close"].iloc[1], 50 * 0.9)
        self.assertEqual(len(out.attrs["exchange_events"]), 2)

    def test_fill_untraded_takes_out_what_the_lists_miss(self):
        # شمواد 2023-12-06: the frozen close cut 97.8%, nothing listed. A
        # traded move is left alone, and so is the listed dividend.
        bars = frame([("2023-12-01", 141000, 141050, 1e5),
                      ("2023-12-06", 3050, 3050, 0),
                      ("2023-12-07", 3100, 3200, 1e5),
                      ("2023-12-09", 3200, 2600, 1e5),
                      ("2023-12-10", 2500, 2500, 0),
                      ("2023-12-11", 2500, 2510, 1e5)])
        evs = [{"ex_date": "2023-12-10", "factor": 2500 / 2600,
                "kind": "dividend"}]
        plain = ca.exchange_adjust(bars, events=evs)
        self.assertEqual(plain.attrs["filled"], [])
        out = ca.exchange_adjust(bars, events=evs, fill_untraded=True)
        self.assertEqual([f["date"] for f in out.attrs["filled"]],
                         ["2023-12-06"])
        closes = list(out["close"])
        self.assertAlmostEqual(closes[1] / closes[0], 1.0)
        self.assertAlmostEqual(closes[3] / closes[2], 2600 / 3200)
        self.assertAlmostEqual(closes[4] / closes[3], 1.0)

    def test_empty_frame(self):
        out = ca.exchange_adjust(frame([]), events=[])
        self.assertTrue(out.empty)

    def test_needs_events_or_a_code(self):
        with self.assertRaises(ValueError):
            ca.exchange_adjust(frame(DIVIDEND_BARS))

    def test_codal_agreement_rule(self):
        self.assertTrue(ca.dps_agrees(55.0, 55.0))
        self.assertTrue(ca.dps_agrees(55.0, 55.9))
        self.assertTrue(ca.dps_agrees(2000.0, 2019.0))
        self.assertFalse(ca.dps_agrees(2000.0, 2100.0))
        self.assertFalse(ca.dps_agrees(None, 10.0))

    def test_bounds(self):
        low, high = ca.capital_bounds(2.0, 3000.0)
        self.assertEqual(low, 0.5)
        self.assertAlmostEqual(high, (3000 + 1000) / 6000)
        self.assertEqual(ca.capital_bounds(0.5, 3000.0), (2.0, 2.0))


class StoreCase(unittest.TestCase):
    """Temporary market and fundamental stores."""

    def setUp(self):
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.market)
        handle, self.fundamentals = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.fundamentals)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.fundamentals)
        database.create_database()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.fundamentals):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    try:
                        os.unlink(path + suffix)
                    except OSError:
                        pass

    def instrument(self, code, symbol):
        database.save_instruments([{
            "ins_code": code, "symbol_fa": symbol, "isin": "IRO1TEST0001",
            "kind": "equity", "board": "bourse", "is_equity": True}])

    def dividends_table(self, rows):
        conn = fundamental_db._connect()
        conn.execute("""CREATE TABLE IF NOT EXISTS dividends (
            ins_code TEXT, fiscal_year_end TEXT, dps REAL, capital REAL,
            tracing_no TEXT, published_at TEXT, stored_at TEXT,
            PRIMARY KEY (ins_code, fiscal_year_end))""")
        conn.executemany("INSERT INTO dividends VALUES (?, ?, ?, NULL, ?, ?, "
                         "NULL)", rows)
        conn.commit()
        conn.close()


class FakeResponse:

    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload
        self.content = json.dumps(payload).encode() if payload is not None \
            else b"<html>"

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


class FakeSession:

    def __init__(self, responder):
        self.responder = responder
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.responder(url)


class StoreTests(StoreCase):

    def test_round_trip_and_dividend_history_with_codal(self):
        database.save_history("1", frame(DIVIDEND_BARS))
        adjust = ca.parse_adjust(DIVIDEND_LIST)
        found = ca.classify(adjust, [], ca._load_bars("1"))
        ca.store("1", adjust, [], found, symbol="فولاد")
        self.dividends_table([("1", "1404/12/29", 55.0, "777",
                               "1405/05/13 10:00:00")])
        (event,) = ca.events("1")
        self.assertEqual(event["kind_fa"], "سود نقدی")
        self.assertEqual(event["ex_date_jalali"], "1405/05/14")
        (row,) = ca.dividend_history("1")
        self.assertEqual(row["dps"], 55.0)
        self.assertAlmostEqual(row["yield_pct"], 55 / 2604 * 100, places=3)
        self.assertEqual(row["codal_dps"], 55.0)
        self.assertEqual(row["codal_tracing_no"], "777")
        self.assertEqual(ca.status()["1"]["events"], 1)

    def test_total_return_index(self):
        database.save_history("1", frame(DIVIDEND_BARS))
        adjust = ca.parse_adjust(DIVIDEND_LIST)
        ca.store("1", adjust, [], ca.classify(adjust, [], ca._load_bars("1")))
        index = ca.total_return_index("1")
        self.assertEqual(list(index.columns),
                         ["date", "close", "tr_close", "tr_index"])
        self.assertAlmostEqual(index["tr_index"].iloc[0], 100.0)
        # 2570 after a 55-rial dividend on 2604: +0.82% on raw, more on TR.
        self.assertAlmostEqual(index["tr_index"].iloc[-1],
                               2570 / (2604 * 2549 / 2604) * 100)
        later = ca.total_return_index("1", start="2026-08-05")
        self.assertAlmostEqual(later["tr_index"].iloc[0], 100.0)

    def test_reclassify_uses_stored_lists_only(self):
        database.save_history("1", frame(DIVIDEND_BARS))
        adjust = ca.parse_adjust(DIVIDEND_LIST)
        ca.store("1", adjust, [], [])
        self.assertEqual(ca.events("1"), [])
        self.assertEqual(ca.reclassify(verbose=False), 1)
        self.assertEqual(len(ca.events("1")), 1)


class FetchTests(StoreCase):

    INSTRUMENTS = [{"ins_code": str(i), "symbol": "S%d" % i}
                   for i in range(1, 9)]

    def ok(self, url):
        if "GetPriceAdjustList" in url:
            return FakeResponse(200, {"priceAdjust": DIVIDEND_LIST})
        return FakeResponse(200, {"instrumentShareChange": []})

    def test_fetch_stores_and_paces(self):
        session = FakeSession(self.ok)
        sleeps = []
        summary = ca.fetch(session=session, gap=0.5, verbose=False,
                           sleep=sleeps.append,
                           instruments=self.INSTRUMENTS[:2])
        self.assertEqual(summary["fetched"], 2)
        self.assertEqual(len(session.urls), 4)
        self.assertTrue(all(s <= 0.5 for s in sleeps))
        self.assertEqual(ca.status()["1"]["adjust_rows"], 1)
        # Resumable: a fresh fetch is skipped.
        again = ca.fetch(session=session, verbose=False, sleep=lambda s: None,
                         instruments=self.INSTRUMENTS[:2])
        self.assertEqual(again["due"], 0)
        self.assertEqual(len(session.urls), 4)

    def test_refusal_stops_the_run_at_once(self):
        session = FakeSession(lambda url: FakeResponse(429, {}))
        summary = ca.fetch(session=session, verbose=False,
                           sleep=lambda s: None, instruments=self.INSTRUMENTS)
        self.assertEqual(len(session.urls), 1)
        self.assertIn("refused", summary["stopped"])
        self.assertIn("refused", ca.status()["1"]["error"])

    def test_consecutive_failures_stop_the_run_with_backoff(self):
        session = FakeSession(lambda url: FakeResponse(500, None))
        sleeps = []
        clock = [0.0]

        def sleep(seconds):
            sleeps.append(seconds)

        summary = ca.fetch(session=session, verbose=False, sleep=sleep,
                           instruments=self.INSTRUMENTS)
        self.assertEqual(summary["attempted"], ca.MAX_CONSECUTIVE_FAILURES)
        self.assertIn("consecutive", summary["stopped"])
        self.assertTrue(sleeps and max(sleeps) > 1.0)
        del clock

    def test_a_failure_does_not_store_half_an_instrument(self):
        def responder(url):
            if "GetPriceAdjustList" in url:
                return FakeResponse(200, {"priceAdjust": DIVIDEND_LIST})
            return FakeResponse(200, {"unexpected": []})
        summary = ca.fetch(session=FakeSession(responder), verbose=False,
                           sleep=lambda s: None,
                           instruments=self.INSTRUMENTS[:1])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(ca.load_raw("1"), ([], []))
        self.assertIsNone(ca.status()["1"]["fetched_at"])

    def test_pacer_counts_the_gap_from_the_end_of_a_request(self):
        now = [100.0]
        slept = []
        pacer = ca._Pacer(0.6, sleep=slept.append, clock=lambda: now[0])
        pacer.wait()
        self.assertEqual(slept, [])
        now[0] += 2.0                      # a slow request
        pacer.done()
        now[0] += 0.1
        pacer.wait()
        self.assertAlmostEqual(slept[-1], 0.5)
        pacer.wait(failures=2)             # 0.6 * 2**2 * 2 = 4.8 s
        self.assertAlmostEqual(slept[-1], 4.7)

    def test_dry_run_makes_no_request(self):
        session = FakeSession(self.ok)
        summary = ca.fetch(dry_run=True, session=session,
                           instruments=self.INSTRUMENTS, max_count=3)
        self.assertEqual(summary["requests"], 6)
        self.assertEqual(session.urls, [])


class AuditTests(StoreCase):

    def test_audit_is_descriptive_and_stops_at_its_end(self):
        self.instrument("1", "آزمون")
        days = pd.bdate_range("2021-05-30", "2026-08-30")
        closes = [1000.0 + i for i in range(len(days))]
        bars = pd.DataFrame({"date": days, "open": closes, "high": closes,
                             "low": closes, "close": closes,
                             "volume": [1e6] * len(days)})
        # A dividend the exchange lists; the heuristic cannot see a 2% cut
        # on a traded bar.
        cut = days.get_loc(pd.Timestamp("2024-01-02"))
        bars.loc[cut:, ["open", "high", "low", "close"]] *= 0.98
        database.save_history("1", bars)
        before = float(bars["close"].iloc[cut - 1])
        adjust = ca.parse_adjust([{"dEven": 20240101,
                                   "pClosing": before * 0.98,
                                   "pClosingNotAdjusted": before}])
        ca.store("1", adjust, [], ca.classify(adjust, [], ca._load_bars("1")))
        path = os.path.join(tempfile.mkdtemp(), "audit.json")
        summary = ca.audit(path=path, verbose=False)
        self.assertTrue(summary["label"].startswith("DESCRIPTIVE"))
        self.assertEqual(summary["names"], 1)
        row = summary["per_name"][0]
        self.assertGreater(row["gap_pct_yr"], 0.0)
        self.assertEqual(summary["window"]["end"], "2026-08-18")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["names"], 1)


class CalendarCase(StoreCase):

    def setUp(self):
        super().setUp()
        ca.ensure_tables()
        self.instrument("10", "نمونه")
        self.instrument("20", "دوم")
        conn = fundamental_db._connect()
        conn.executescript("""
            CREATE TABLE announcements (tracing_no TEXT PRIMARY KEY,
                ins_code TEXT, symbol TEXT, letter_code TEXT, kind TEXT,
                title TEXT, published_at TEXT, url TEXT, seen_at TEXT);
            CREATE TABLE watch_seen (tracing_no TEXT PRIMARY KEY,
                letter_code TEXT, ins_code TEXT, published_at TEXT,
                outcome TEXT, seen_at TEXT);
        """)
        conn.commit()
        conn.close()

    def announce(self, tracing, code, symbol, letter_code, title, published):
        conn = fundamental_db._connect()
        conn.execute("INSERT INTO announcements VALUES (?, ?, ?, ?, NULL, ?, "
                     "?, ?, NULL)", (tracing, code, symbol, letter_code, title,
                                     published,
                                     "https://codal.ir/x?%s" % tracing))
        conn.execute("INSERT INTO watch_seen VALUES (?, ?, ?, ?, 'stored', "
                     "NULL)", (tracing, letter_code, code, published))
        conn.commit()
        conn.close()


class CalendarTests(CalendarCase):

    TITLE = "دوره 12 ماهه منتهی به 1405/03/31"

    def test_pending_meeting_and_its_legal_window(self):
        self.announce("1", "10", "نمونه", "ن-50",
                      "اگهی دعوت به مجمع عمومی عادی سالیانه " + self.TITLE,
                      "1405/08/25 10:00:00")
        result = cc.calendar(today="2026-11-24")
        (item,) = result["pending_meetings"]
        self.assertEqual(item["meeting_type"], "annual")
        self.assertEqual(item["invitation"]["tracing_no"], "1")
        self.assertEqual(item["expected_from"]["jalali"], "1405/09/05")
        self.assertEqual(item["expected_to"]["jalali"], "1405/10/05")
        self.assertEqual(item["status"], "expected")
        self.assertIn("متوقف", item["halt_note"])
        self.assertIn("نه سیگنال", result["note"])
        self.assertEqual(result["coverage"]["watch_letters_stored"],
                         {"seen": 1, "stored": 1})

    def test_decided_or_cancelled_meeting_is_not_pending(self):
        self.announce("1", "10", "نمونه", "ن-50", "دعوت " + self.TITLE,
                      "1405/08/01 10:00:00")
        self.announce("2", "10", "نمونه", "ن-52", "تصمیمات " + self.TITLE,
                      "1405/08/20 18:00:00")
        self.announce("3", "20", "دوم", "ن-56", "دعوت به مجمع فوق العاده",
                      "1405/08/10 10:00:00")
        self.announce("4", "20", "دوم", "ن-58", "لغو اگهی دعوت",
                      "1405/08/12 10:00:00")
        result = cc.calendar(today="2026-11-24")
        self.assertEqual(result["pending_meetings"], [])
        (decision,) = result["recent_decisions"]
        self.assertEqual(decision["decision"]["tracing_no"], "2")

    def test_an_egm_summary_closes_the_egm_and_is_not_an_agm_decision(self):
        self.announce("1", "10", "نمونه", "ن-56",
                      "اگهی دعوت به مجمع عمومی فوق العاده", "1405/08/10 10:00")
        self.announce("2", "10", "نمونه", "ن-51",
                      "خلاصه تصمیمات مجمع عمومی فوق العاده دوره 12 ماهه "
                      "منتهی به 1406/04/31", "1405/08/25 10:00")
        result = cc.calendar(today="2026-11-24")
        self.assertEqual(result["pending_meetings"], [])
        self.assertEqual(result["recent_decisions"], [])

    def test_letters_under_a_retired_listing_count_for_the_live_one(self):
        # بتهران: the invitation under the live code, the decision under
        # the code a board move retired.
        database.save_instruments([{
            "ins_code": "11", "symbol_fa": "نمونه", "isin": "IRO3TEST0001",
            "kind": "superseded", "board": None, "is_equity": False}])
        self.announce("1", "10", "نمونه", "ن-54",
                      "اگهی دعوت به مجمع عمومی عادی بطور فوق العاده",
                      "1405/08/01 10:00")
        self.announce("2", "11", "نمونه", "ن-55",
                      "تصمیمات مجمع عمومی عادی به طور فوق العاده " + self.TITLE,
                      "1405/08/15 10:00")
        result = cc.calendar(today="2026-11-24")
        self.assertEqual(result["pending_meetings"], [])
        (item,) = result["recent_decisions"]
        self.assertEqual(item["ins_code"], "10")
        self.assertEqual(item["meeting_type"], "ordinary_extra")

    def test_letters_after_today_are_not_read(self):
        self.announce("1", "10", "نمونه", "ن-50", "دعوت " + self.TITLE,
                      "1405/08/25 10:00:00")
        self.announce("2", "10", "نمونه", "ن-52", "تصمیمات " + self.TITLE,
                      "1405/09/10 18:00:00")
        result = cc.calendar(today="2026-11-24")
        self.assertEqual(len(result["pending_meetings"]), 1)
        self.assertEqual(result["recent_decisions"], [])

    def test_decision_with_dps_and_the_statutory_deadline(self):
        self.announce("2", "10", "نمونه", "ن-52", "تصمیمات " + self.TITLE,
                      "1405/08/20 18:00:00")
        self.announce("5", "10", "نمونه", "ن-13", "زمانبندی پرداخت سود",
                      "1405/08/28 09:00:00")
        self.dividends_table([("10", "1405/03/31", 400.0, "2",
                               "1405/08/20 18:00:00")])
        # The exchange's event at the meeting session, two days before.
        conn = database._connect()
        conn.execute("INSERT INTO corporate_actions (ins_code, date, seq, "
                     "source, kind, ex_date, factor, dps, not_adjusted, "
                     "adjusted) VALUES ('10', '2026-11-09', 0, "
                     "'adjust_list', 'dividend', '2026-11-10', 0.9, 400, "
                     "4000, 3600)")
        conn.commit()
        conn.close()
        result = cc.calendar(today="2026-11-24")
        (item,) = result["recent_decisions"]
        self.assertEqual(item["dps_codal"], 400.0)
        self.assertEqual(item["dps_exchange"], 400.0)
        self.assertTrue(item["dps_agree"])
        self.assertEqual(item["meeting_date"]["iso"], "2026-11-09")
        self.assertEqual(item["meeting_date_basis"], "exchange_event")
        self.assertEqual(item["payment_deadline"]["jalali"], "1406/04/18")
        self.assertEqual(item["payment_schedule"]["tracing_no"], "5")

    def test_payment_deadlines_ahead(self):
        conn = database._connect()
        conn.execute("INSERT INTO corporate_actions (ins_code, date, seq, "
                     "source, kind, ex_date, factor, dps) VALUES ('20', "
                     "'2026-04-20', 0, 'adjust_list', 'dividend', "
                     "'2026-04-21', 0.9, 120)")
        conn.commit()
        conn.close()
        result = cc.calendar(today="2026-11-24", ahead_days=60)
        (item,) = result["payment_deadlines"]
        self.assertEqual(item["symbol"], "دوم")
        self.assertEqual(item["payment_deadline"]["jalali"], "1405/09/30")

    def test_capital_increase_stage_and_completion(self):
        self.announce("11", "10", "نمونه", "ن-60", "پیشنهاد هیئت مدیره",
                      "1405/07/01 10:00:00")
        self.announce("12", "10", "نمونه", "ن-61", "اظهارنظر حسابرس",
                      "1405/07/20 10:00:00")
        self.announce("13", "10", "نمونه", "ن-56", "دعوت فوق العاده",
                      "1405/08/01 10:00:00")
        self.announce("21", "20", "دوم", "ن-60", "پیشنهاد", "1405/05/01 10:00")
        self.announce("22", "20", "دوم", "ن-67", "ثبت", "1405/08/20 10:00")
        # A lone EGM is a meeting, not an increase.
        self.instrument("30", "سوم")
        self.announce("31", "30", "سوم", "ن-56", "دعوت", "1405/08/20 10:00")
        result = cc.calendar(today="2026-11-24")
        by_symbol = {i["symbol"]: i for i in result["capital_increases"]}
        self.assertEqual(set(by_symbol), {"نمونه", "دوم"})
        self.assertEqual(by_symbol["نمونه"]["status"], "in_progress")
        self.assertEqual(by_symbol["نمونه"]["latest_stage"]["letter_code"],
                         "ن-56")
        self.assertEqual(by_symbol["نمونه"]["latest_stage"]["label_fa"],
                         "دعوت به مجمع فوق‌العاده")
        self.assertEqual(len(by_symbol["نمونه"]["stages"]), 3)
        self.assertEqual(by_symbol["دوم"]["status"], "completed_recently")

    def test_exchange_share_count_closes_only_a_decided_increase(self):
        self.announce("11", "10", "نمونه", "ن-60", "پیشنهاد", "1405/07/01 10:00")
        self.announce("12", "10", "نمونه", "ن-57", "تصمیمات فوق العاده",
                      "1405/08/10 10:00")
        self.announce("21", "20", "دوم", "ن-60", "پیشنهاد", "1405/07/01 10:00")
        self.announce("22", "20", "دوم", "ن-61", "حسابرس", "1405/07/20 10:00")
        conn = database._connect()
        # 1405/08/20 = 2026-11-11 for the first; for the second a count
        # registered before anything was decided (an earlier increase).
        conn.execute("INSERT INTO share_changes VALUES ('10', '2026-11-11', "
                     "0, 100, 150, 1.5, NULL)")
        conn.execute("INSERT INTO share_changes VALUES ('20', '2026-10-20', "
                     "0, 100, 150, 1.5, NULL)")
        conn.commit()
        conn.close()
        result = cc.calendar(today="2026-11-24")
        by_symbol = {i["symbol"]: i for i in result["capital_increases"]}
        self.assertEqual(by_symbol["نمونه"]["status"], "completed_recently")
        self.assertEqual(by_symbol["نمونه"]["exchange_share_change"]["ratio"],
                         1.5)
        self.assertEqual(by_symbol["دوم"]["status"], "in_progress")

    def test_jalali_month_arithmetic(self):
        self.assertEqual(cc.add_jalali_months("2026-08-03", 8),
                         date(*__import__("jalali").to_gregorian(1406, 1, 12))
                         .isoformat())
        # 1405/06/31 + 2 months -> Azar has 30 days.
        iso = date(*__import__("jalali").to_gregorian(1405, 6, 31)).isoformat()
        self.assertEqual(cc.to_jalali(cc.add_jalali_months(iso, 2)),
                         "1405/08/30")


if __name__ == "__main__":
    unittest.main()
