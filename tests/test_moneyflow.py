"""Money-flow correctness, and the ways this data lies if handled carelessly.

Every test here guards a failure that produces a number rather than an error:
a per-capita ratio computed from a zero participant count, a window scored off
three cached sessions, a "confirmation" that is the same fact counted twice,
or a micro-cap topping the ranking on a rounding error's worth of turnover.
"""

import math
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import moneyflow


def make_flow(
    sessions=20,
    individual_buy=3.0e9,
    individual_sell=2.0e9,
    institutional_buy=1.0e9,
    institutional_sell=2.0e9,
    individual_buy_count=100.0,
    individual_sell_count=400.0,
):
    """A client-type frame with the same shape the provider emits.

    Defaults describe concentrated retail buying: 100 buyers committing 3B
    against 400 sellers releasing 2B, i.e. قدرت خریدار of 6.
    """

    return pd.DataFrame({
        "date": pd.date_range("2026-06-01", periods=sessions, freq="D"),
        "individual_buy_value": [individual_buy] * sessions,
        "individual_sell_value": [individual_sell] * sessions,
        "institutional_buy_value": [institutional_buy] * sessions,
        "institutional_sell_value": [institutional_sell] * sessions,
        "individual_buy_volume": [3.0e6] * sessions,
        "individual_sell_volume": [2.0e6] * sessions,
        "institutional_buy_volume": [1.0e6] * sessions,
        "institutional_sell_volume": [2.0e6] * sessions,
        "individual_buy_count": [individual_buy_count] * sessions,
        "individual_sell_count": [individual_sell_count] * sessions,
        "institutional_buy_count": [1.0] * sessions,
        "institutional_sell_count": [1.0] * sessions,
    })


class TestMeasure(unittest.TestCase):

    def test_per_capita_uses_counts_not_values(self):
        """قدرت خریدار is a ratio of averages, not of totals.

        The whole reason the counts are stored: these two frames move identical
        rials, and only the participant counts separate one buyer from four.
        """

        concentrated = moneyflow.measure(
            make_flow(individual_buy_count=100.0, individual_sell_count=400.0)
        )
        distributed = moneyflow.measure(
            make_flow(individual_buy_count=400.0, individual_sell_count=100.0)
        )

        self.assertAlmostEqual(concentrated["power_ratio"], 6.0)
        self.assertAlmostEqual(distributed["power_ratio"], 0.375)

        # Same money either way -- proving the ratio is not a value proxy.
        self.assertEqual(
            concentrated["net_individual"], distributed["net_individual"]
        )

    def test_window_is_aggregated_not_averaged_over_ratios(self):
        """One thin session must not dominate the reading.

        A day where four people sold 1000 rials has a per-session ratio in the
        millions. Averaging ratios would carry that into the verdict; summing
        values and counts first weights it by how little it traded.
        """

        frame = make_flow(sessions=5)

        # One near-dead session with an absurd per-session ratio.
        frame.loc[4, "individual_buy_value"] = 1.0e9
        frame.loc[4, "individual_buy_count"] = 1.0
        frame.loc[4, "individual_sell_value"] = 1000.0
        frame.loc[4, "individual_sell_count"] = 4.0

        reading = moneyflow.measure(frame, window=5)

        # Per-session that day is (1e9/1) / (1000/4) = 4,000,000. The window
        # figure stays within sight of the other four sessions' 6.0.
        self.assertLess(reading["power_ratio"], 20.0)

    def test_zero_participants_gives_no_ratio(self):
        """A side nobody took has no per-participant figure, not an infinite one."""

        reading = moneyflow.measure(make_flow(individual_sell_count=0.0))

        self.assertIsNone(reading["sell_power"])
        self.assertIsNone(reading["power_ratio"])
        # The rest of the reading still stands.
        self.assertIsNotNone(reading["net_pct"])

    def test_net_institutional_is_the_exact_mirror(self):
        """Both sides sum to the same turnover, so one net is minus the other.

        Reporting them as two agreeing signals is counting one fact twice.
        """

        reading = moneyflow.measure(make_flow())

        self.assertAlmostEqual(
            reading["net_individual"], -reading["net_institutional"]
        )

    def test_net_pct_is_relative_to_own_turnover(self):
        """A minnow and a giant with the same proportional flow read alike."""

        small = moneyflow.measure(make_flow(
            individual_buy=3.0e9, individual_sell=2.0e9,
            institutional_buy=1.0e9, institutional_sell=2.0e9,
        ))
        large = moneyflow.measure(make_flow(
            individual_buy=3.0e12, individual_sell=2.0e12,
            institutional_buy=1.0e12, institutional_sell=2.0e12,
        ))

        self.assertAlmostEqual(small["net_pct"], large["net_pct"])
        self.assertGreater(large["net_individual"], small["net_individual"])

    def test_persistence_counts_direction_not_agreement(self):
        """A consistently bleeding share earns no conviction credit.

        Persistence is the share of sessions retail finished as a net buyer. If
        it instead measured "sessions agreeing with the window total", a steady
        outflow would score full marks for being steady.
        """

        frame = make_flow(sessions=5)

        for day in range(5):
            frame.loc[day, "individual_buy_value"] = 1.0e9
            frame.loc[day, "individual_sell_value"] = 3.0e9

        reading = moneyflow.measure(frame, window=5)

        self.assertEqual(reading["sessions_positive"], 0)
        self.assertEqual(reading["persistence"], 0.0)

        fraction, _note = moneyflow._score_persistence(reading)
        self.assertLess(fraction, 0.1)

    def test_no_turnover_returns_nothing(self):
        frame = make_flow(
            individual_buy=0.0, individual_sell=0.0,
            institutional_buy=0.0, institutional_sell=0.0,
        )

        self.assertIsNone(moneyflow.measure(frame))


class TestAnalyse(unittest.TestCase):

    def test_short_history_is_insufficient_not_zero(self):
        """Too few sessions must not score 0.

        A zero would rank a share nothing is known about below one measured and
        found to be bleeding money -- exactly the wrong way round.
        """

        result = moneyflow.analyse(make_flow(sessions=3))

        self.assertEqual(result["verdict"], moneyflow.INSUFFICIENT)
        self.assertIsNone(result["score"])
        self.assertIsNone(result["label"])
        self.assertIn("3 session", result["reason"])

    def test_empty_and_none_are_insufficient(self):
        for value in (None, pd.DataFrame()):
            result = moneyflow.analyse(value)
            self.assertEqual(result["verdict"], moneyflow.INSUFFICIENT)
            self.assertIsNone(result["score"])

    def test_strong_accumulation_scores_high(self):
        result = moneyflow.analyse(make_flow())

        self.assertEqual(result["verdict"], moneyflow.SCORED)
        self.assertGreaterEqual(result["score"], config.FLOW_BULLISH_SCORE)
        self.assertEqual(result["label"], moneyflow.BULLISH)
        self.assertEqual(result["weight_covered"], 1.0)

    def test_distribution_scores_low(self):
        result = moneyflow.analyse(make_flow(
            individual_buy=1.0e9, individual_sell=3.0e9,
            institutional_buy=3.0e9, institutional_sell=1.0e9,
            individual_buy_count=400.0, individual_sell_count=100.0,
        ))

        self.assertEqual(result["verdict"], moneyflow.SCORED)
        self.assertLessEqual(result["score"], config.FLOW_BEARISH_SCORE)
        self.assertEqual(result["label"], moneyflow.BEARISH)

    def test_missing_counts_redistribute_weight_and_are_reported(self):
        """Absent counts cost قدرت خریدار, not the whole reading.

        This is the shape of a real bug already survived once: the provider had
        the count field names wrong, every count parsed to NaN, and the score
        carried on using the two components that were left. The score must stay
        on a 0-100 scale, and weight_covered must say what actually ran.
        """

        frame = make_flow()

        for column in ("individual_buy_count", "individual_sell_count"):
            frame[column] = float("nan")

        result = moneyflow.analyse(frame)

        self.assertEqual(result["verdict"], moneyflow.SCORED)
        self.assertIsNone(result["breakdown"]["قدرت خریدار"])
        self.assertLess(result["weight_covered"], 1.0)
        self.assertLessEqual(result["score"], 100)
        self.assertGreaterEqual(result["score"], 0)
        self.assertTrue(
            any("unavailable" in note for note in result["notes"]),
            "the missing component must be named in the notes"
        )

    def test_score_is_bounded(self):
        for frame in (
            make_flow(individual_buy=1e15, individual_buy_count=1.0),
            make_flow(individual_sell=1e15, individual_sell_count=1.0),
        ):
            score = moneyflow.analyse(frame)["score"]
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_baseline_covers_a_longer_span_than_the_window(self):
        result = moneyflow.analyse(make_flow(sessions=25))

        self.assertEqual(result["reading"]["sessions"], config.FLOW_WINDOW)
        self.assertEqual(
            result["baseline"]["sessions"], config.FLOW_BASELINE_WINDOW
        )


class TestAggregate(unittest.TestCase):

    def test_sums_rials_rather_than_averaging_percentages(self):
        """A 2B minnow must not out-vote a 2000B heavyweight."""

        minnow = moneyflow.measure(make_flow(
            individual_buy=2.0e9, individual_sell=0.1e9,
            institutional_buy=0.1e9, institutional_sell=2.0e9,
        ))
        giant = moneyflow.measure(make_flow(
            individual_buy=1.0e12, individual_sell=1.1e12,
            institutional_buy=1.0e12, institutional_sell=0.9e12,
        ))

        combined = moneyflow.aggregate([minnow, giant])

        # The minnow is +90%-ish and the giant mildly negative. Averaging the
        # percentages would call the market strongly positive; summing rials
        # lets the giant's outflow decide, as it should.
        self.assertLess(combined["net_pct"], 0)
        self.assertEqual(combined["instruments"], 2)

    def test_breadth_is_separate_from_direction(self):
        """Net inflow with most shares bleeding is a real and different state."""

        bleeding = [
            moneyflow.measure(make_flow(
                individual_buy=1.0e9, individual_sell=1.2e9,
                institutional_buy=1.0e9, institutional_sell=0.8e9,
            ))
            for _ in range(9)
        ]

        absorber = moneyflow.measure(make_flow(
            individual_buy=1.0e12, individual_sell=0.5e12,
            institutional_buy=0.5e12, institutional_sell=1.0e12,
        ))

        combined = moneyflow.aggregate(bleeding + [absorber])

        self.assertGreater(combined["net_individual"], 0)
        self.assertLess(combined["breadth_pct"], 20)

    def test_empty_input(self):
        self.assertIsNone(moneyflow.aggregate([]))
        self.assertIsNone(moneyflow.aggregate([None, None]))


class TestBandCalibration(unittest.TestCase):
    """The bands are placed on the TSE's own cross-sectional distribution.

    They are ordinary numbers in config.py and easy to "tidy" into something
    symmetric. These tests record why they are not: zero is not the neutral
    point of Iranian retail flow, because حقوقی are net sellers to retail in
    most weeks and the median share runs a positive net.
    """

    def test_net_bands_descend(self):
        self.assertGreater(config.FLOW_NET_STRONG, config.FLOW_NET_GOOD)
        self.assertGreater(config.FLOW_NET_GOOD, config.FLOW_NET_WEAK)
        self.assertGreater(config.FLOW_NET_WEAK, config.FLOW_NET_BAD)

    def test_power_bands_descend_and_straddle_parity(self):
        self.assertGreater(config.FLOW_POWER_STRONG, config.FLOW_POWER_GOOD)
        self.assertGreater(config.FLOW_POWER_GOOD, config.FLOW_POWER_WEAK)
        self.assertGreater(config.FLOW_POWER_WEAK, config.FLOW_POWER_BAD)
        # 1.0 -- "the two sides are the same size" -- stays a band edge.
        self.assertLessEqual(config.FLOW_POWER_BAD, 1.0)
        self.assertGreaterEqual(config.FLOW_POWER_GOOD, 1.0)

    def test_a_median_share_is_not_called_strong_inflow(self):
        """+5% net is the middle of this market, and must not top the band.

        Measured over the 706 equities clearing the liquidity floor, the median
        net retail inflow is about +4.8%. A threshold below that would label
        half the market "strong inflow", which ranks nothing against anything.
        """

        fraction, _note = moneyflow._score_net({"net_pct": 4.8})

        self.assertLess(fraction, 0.8)

    def test_weights_sum_to_one_hundred(self):
        self.assertEqual(
            config.FLOW_WEIGHT_POWER
            + config.FLOW_WEIGHT_NET
            + config.FLOW_WEIGHT_PERSISTENCE,
            100
        )


class TestHelpers(unittest.TestCase):

    def test_value_rejects_non_finite(self):
        for bad in (None, float("nan"), float("inf"), "abc", [1]):
            self.assertIsNone(moneyflow._value(bad))

    def test_per_capita_rejects_zero_and_negative_counts(self):
        self.assertIsNone(moneyflow._per_capita(1000.0, 0))
        self.assertIsNone(moneyflow._per_capita(1000.0, -5))
        self.assertIsNone(moneyflow._per_capita(0.0, 10))
        self.assertEqual(moneyflow._per_capita(1000.0, 10), 100.0)

    def test_total_treats_missing_column_as_zero(self):
        frame = make_flow(sessions=2)
        self.assertEqual(moneyflow._total(frame, "not_a_column"), 0.0)

    def test_total_ignores_nan(self):
        frame = make_flow(sessions=3)
        frame.loc[1, "individual_buy_value"] = float("nan")
        self.assertAlmostEqual(
            moneyflow._total(frame, "individual_buy_value"), 6.0e9
        )


class TestAnalyseMarketIsCurrent(unittest.TestCase):
    """The whole-market card ranks this week's flow, not a dead share's last.

    On 2026-11-11 the #1 outflow was وسگلستا, whose window ended 2021-07-31,
    and two of the strongest buyers were windows from 2024-11 and 2025-01:
    every stored window scored, however old, and fed the market KPIs too.
    """

    CALENDAR = pd.bdate_range(end="2026-11-10", periods=20)

    def setUp(self):
        import tempfile

        import database

        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        newest = str(self.CALENDAR[-1].date())
        cal = self.CALENDAR

        # (code, flow sessions, individual buy, individual sell, institutional
        # buy, price bars as (date, volume)). Turnover clears the liquidity
        # floor everywhere, so only currency decides who is listed.
        names = [
            # trades and is fetched up to the newest session
            ("live", cal, 6e9, 3e9, 2e9, [(newest, 1000)]),
            # last traded in July 2021, frozen zero-volume bars since: halted
            ("halted", pd.bdate_range(end="2021-07-30", periods=12),
             1e9, 9e9, 9e9, [("2021-07-30", 1000), (newest, 0)]),
            # trades, but its stored flow stops six sessions back: stale
            ("stale", cal[-18:-6], 1e9, 9e9, 9e9, [(newest, 1000)]),
            # stops four sessions back -- still inside this week's window
            ("recent", cal[-16:-4], 2e9, 6e9, 8e9, [(newest, 1000)]),
        ]

        conn = database._connect()
        for code, days, buy, sell, inst_buy, prices in names:
            conn.execute("INSERT INTO instruments (ins_code, symbol_fa, "
                         "is_equity) VALUES (?, ?, 1)", (code, "n" + code))
            for day in days:
                conn.execute(
                    "INSERT INTO client_type_history (ins_code, date, "
                    "individual_buy_value, individual_sell_value, "
                    "institutional_buy_value, institutional_sell_value, "
                    "individual_buy_count, individual_sell_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, 100, 300)",
                    (code, str(day.date()), buy, sell, inst_buy,
                     buy + inst_buy - sell))
            for day, volume in prices:
                conn.execute("INSERT INTO price_history (ins_code, date, close, "
                             "volume) VALUES (?, ?, 1000, ?)", (code, day, volume))
        conn.commit()
        conn.close()

    def tearDown(self):
        import database

        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    @staticmethod
    def listed(report):
        return {row["ins_code"] for key in ("inflow", "outflow", "strongest_power")
                for row in report[key]}

    def test_halted_and_stale_windows_are_neither_ranked_nor_summed(self):
        report = moneyflow.analyse_market(top=15)

        self.assertEqual(self.listed(report), {"live", "recent"})
        self.assertEqual(report["market"]["instruments"], 2)
        self.assertEqual(report["counts"]["halted"], 1)
        self.assertEqual(report["counts"]["stale"], 1)
        self.assertEqual(report["counts"]["scored"], 2)
        # The oldest session a window must reach: the 5th newest, one week.
        self.assertEqual(report["current_from"], str(self.CALENDAR[-5].date()))

    def test_the_staleness_threshold_is_the_window_being_measured(self):
        """Asked for ten sessions, a window six sessions old still overlaps."""

        report = moneyflow.analyse_market(top=15, window=10)

        self.assertEqual(self.listed(report), {"live", "recent", "stale"})
        self.assertEqual(report["counts"]["stale"], 0)
        self.assertEqual(report["counts"]["halted"], 1)
        self.assertEqual(report["current_from"], str(self.CALENDAR[-10].date()))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CurrentWeekRuleTests(unittest.TestCase):
    """One rule for every reader of a name's flow (2026-11-11)."""

    def test_a_reading_must_reach_this_weeks_window(self):
        import moneyflow

        since = "2026-11-04"
        self.assertTrue(moneyflow.is_current(
            {"reading": {"last_date": "2026-11-10"}}, since))
        self.assertTrue(moneyflow.is_current(
            {"reading": {"last_date": "2026-11-04"}}, since))
        self.assertFalse(moneyflow.is_current(
            {"reading": {"last_date": "2026-10-20"}}, since))
        self.assertFalse(moneyflow.is_current({"reading": None}, since))

    def test_nothing_dated_means_nothing_is_stale(self):
        import moneyflow

        self.assertTrue(moneyflow.is_current(
            {"reading": {"last_date": "2021-07-31"}}, None))

    def test_the_window_is_counted_in_stored_sessions(self):
        import pandas as pd

        import moneyflow

        days = pd.to_datetime(["2026-11-01", "2026-11-02", "2026-11-03",
                               "2026-11-04", "2026-11-07", "2026-11-08",
                               "2026-11-09", "2026-11-10"])
        frames = {"a": pd.DataFrame({"date": days}),
                  "b": pd.DataFrame({"date": days[:3]})}
        # Five newest stored sessions: 11-10, 09, 08, 07, 04.
        self.assertEqual(moneyflow.current_from(frames, 5), "2026-11-04")
