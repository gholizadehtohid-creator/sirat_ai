"""Smart Money scanner: the ways this model would lie if built carelessly.

Every test here guards a failure that produces a NUMBER rather than an error --
a score inflated by the data it is missing, one fact counted twice under two
component names, a wash trade scored as accumulation, a name at its high
admitted because its money flow was strong, or a "Smart Money Ratio" that
reaches 1.0 and stops distinguishing anything.
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from smart_money import accumulation, flow, microstructure, position, scoring


# ==============================
# FIXTURES
# ==============================

def price_frame(sessions=300, start=1000.0, drift=0.0, volume=1.0e6,
                seed=7):
    """A price frame shaped like database.load_history_tail's output."""

    rng = np.random.default_rng(seed)

    steps = rng.normal(drift, 0.01, sessions)
    closes = start * np.cumprod(1 + steps)

    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=sessions, freq="D"),
        "open": closes,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": [volume] * sessions,
        "value": [volume * c for volume, c in zip([volume] * sessions, closes)],
        "trades": [500.0] * sessions,
    })


def flow_frame(sessions=25, recent=5,
               buy_value=3.0e9, sell_value=1.0e9,
               buy_count=50.0, sell_count=400.0,
               base_buy_value=2.0e9, base_buy_count=400.0,
               buy_volume=3.0e6, inst_buy_volume=1.0e6):
    """A client-type frame whose last `recent` sessions differ from the rest.

    The baseline sessions carry `base_*`; the recent ones carry the figures
    under test. That separation is the whole point -- code_to_code measures the
    recent window AGAINST the sessions before it.
    """

    rows = []

    for i in range(sessions):
        tail = i >= sessions - recent
        rows.append({
            "date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=i),
            "individual_buy_value": buy_value if tail else base_buy_value,
            "individual_sell_value": sell_value if tail else base_buy_value,
            "institutional_buy_value": 1.0e9,
            "institutional_sell_value": 1.0e9,
            "individual_buy_volume": buy_volume if tail else 2.0e6,
            "individual_sell_volume": 1.0e6,
            "institutional_buy_volume": inst_buy_volume,
            "institutional_sell_volume": 1.0e6,
            "individual_buy_count": buy_count if tail else base_buy_count,
            "individual_sell_count": sell_count,
            "institutional_buy_count": 2.0,
            "institutional_sell_count": 2.0,
        })

    return pd.DataFrame(rows)


def candidate(**overrides):
    """A candidate that passes every gate, so a test can break exactly one."""

    base = {
        "ins_code": "1",
        "symbol": "آزمون",
        "close": 100.0,
        "integrity": {"clean": True, "reason": None},
        "tradability": {"tradable": True, "reason": None,
                        "traded_mfi_pct": 100.0, "traded_volume_pct": 100.0},
        "locks": {"locked_now": False, "locked_up_now": False,
                  "locked_down_now": False, "reason": None,
                  "locked_up_recent": 0, "locked_down_recent": 0,
                  "band_closes_recent": 2, "window": 20},
        "advance": {"return_pct": 4.0, "market_pct": 6.0, "excess_pct": -2.0,
                    "quiet": True, "reason": None, "window": 10,
                    "margin": 3.0},
        "mfi": {"value": 78.0, "change": 9.0, "rising": True,
                "rise_sessions": 5,
                "divergence": {"positive": True, "price_change_pct": -6.0,
                               "mfi_change": 12.0}},
        "range": {"window": 120, "position_pct": 12.0, "low": 90.0,
                  "high": 180.0, "at_ceiling": False},
        "supports": {"nearest": {"kind": "static", "label": "حمایت استاتیک",
                                 "price": 98.0, "distance_pct": 2.0,
                                 "touches": 3},
                     "levels": []},
        "volume": {"ratio": 2.2},
        "transfer": {"net_pct": 25.0, "sessions": 5, "sessions_positive": 4,
                     "persistence": 0.8, "power_ratio": 2.1,
                     "into_individual_hands": True},
        "code_to_code": {"genuine": True, "smart_money_ratio": 0.45,
                         "elevation": 3.0, "volume_share": 0.6,
                         "reasons": []},
        "valuation": {"pe": 5.0, "sector_pe": 8.0, "sector": "آزمون"},
        "size": {"market_cap": 2.0e13, "band": scoring.SMALL},
    }

    base.update(overrides)

    return base


# ==============================
# THE MODEL'S ARITHMETIC
# ==============================

class ScoringModel(unittest.TestCase):

    def test_weights_sum_to_one_hundred(self):
        """The brief's allocation, and the only place it exists."""

        self.assertEqual(sum(scoring.WEIGHTS.values()), 100)

    def test_missing_components_are_not_redistributed(self):
        """The bug that would put the market's worst-documented names on top.

        moneyflow.score() spreads the weight of an absent component across the
        rest, correctly, because it describes ONE name. Doing that in a RANKING
        lets a name with no P/E, no market cap and no client-type data score 100
        out of the three components it happens to have -- and it would do so
        every single run.
        """

        full = scoring.score(candidate())

        blind = scoring.score(candidate(
            valuation={"pe": None, "sector_pe": None, "sector": None},
            size={"market_cap": None, "band": None},
        ))

        self.assertLess(blind["score"], full["score"])
        self.assertIsNone(blind["breakdown"][scoring.VALUATION])
        self.assertIsNone(blind["breakdown"][scoring.SIZE])

        # And the shortfall is REPORTED, not hidden: 25 of the 100 points had
        # no inputs at all.
        self.assertEqual(blind["max_possible"], 75)
        self.assertEqual(blind["coverage"], 0.75)

    def test_score_is_bounded(self):
        best = scoring.score(candidate())

        self.assertLessEqual(best["score"], 100)
        self.assertGreaterEqual(best["score"], 0)

    def test_large_cap_kept_but_muted(self):
        """The brief: Large Cap حذف نشود ولی وزن کمتری دریافت کند."""

        small = scoring.score(candidate(
            size={"market_cap": 1.0e13, "band": scoring.SMALL}))
        large = scoring.score(candidate(
            size={"market_cap": 9.0e14, "band": scoring.LARGE}))

        self.assertGreater(small["breakdown"][scoring.SIZE],
                           large["breakdown"][scoring.SIZE])
        # Muted, not zero -- the large cap is still on the list.
        self.assertGreater(large["breakdown"][scoring.SIZE], 0)

    def test_extreme_discount_scores_below_moderate_one(self):
        """A 90% discount to the sector is a warning, not nine times a 10%.

        This encodes the brief's "هرچه فاصله منطقی کمتر باشد، امتیاز بالاتر"
        against its "ارزان‌تر از گروه رتبه بالاتر". If the intended reading was
        monotonic, this is the test that should fail first and loudest.
        """

        moderate = scoring.score(candidate(
            valuation={"pe": 6.0, "sector_pe": 8.0, "sector": "آزمون"}))
        extreme = scoring.score(candidate(
            valuation={"pe": 0.5, "sector_pe": 8.0, "sector": "آزمون"}))

        self.assertGreater(moderate["breakdown"][scoring.VALUATION],
                           extreme["breakdown"][scoring.VALUATION])

    def test_loss_making_company_has_no_valuation_reading(self):
        """A company with no earnings has no P/E. Not a zero -- an absence."""

        scored = scoring.score(candidate(
            valuation={"pe": -3.0, "sector_pe": 8.0, "sector": "آزمون"}))

        self.assertIsNone(scored["breakdown"][scoring.VALUATION])

    def test_volume_without_smart_money_is_capped(self):
        """حجم غیرعادی بدون ورود پول هوشمند امتیاز کامل نگیرد.

        Volume says a lot of stock changed hands. It does not say who took it,
        and a distribution day looks identical on this axis.
        """

        with_flow = scoring.score(candidate(volume={"ratio": 5.0}))

        without = scoring.score(candidate(
            volume={"ratio": 5.0},
            transfer={"net_pct": -4.0, "sessions": 5, "persistence": 0.2,
                      "into_individual_hands": False},
        ))

        self.assertEqual(with_flow["breakdown"][scoring.VOLUME],
                         scoring.WEIGHTS[scoring.VOLUME])
        self.assertLess(without["breakdown"][scoring.VOLUME],
                        with_flow["breakdown"][scoring.VOLUME])

    def test_rejected_code_to_code_scores_zero_not_none(self):
        """A failed test is evidence. Missing data is not.

        Zero says "we looked and it was not there"; None says "we could not
        look". Collapsing them would let a name with no client-type data score
        the same as one whose کد به کد was examined and rejected.
        """

        rejected = scoring.score(candidate(
            code_to_code={"genuine": False, "reasons": ["الگوی چرخش"],
                          "smart_money_ratio": 0.5, "elevation": 2.0}))

        absent = scoring.score(candidate(code_to_code=None))

        self.assertEqual(rejected["breakdown"][scoring.CODE_TO_CODE], 0)
        self.assertIsNone(absent["breakdown"][scoring.CODE_TO_CODE])

    def test_every_status_is_a_watch_status(self):
        """No label this scanner issues may read as a buy recommendation."""

        for score in (0, 44, 45, 59, 60, 74, 75, 100):
            self.assertNotIn("خرید", scoring.status_for(score))


# ==============================
# THE VETOES (بند ۱۱)
# ==============================

class RiskRules(unittest.TestCase):

    def test_name_at_its_ceiling_is_excluded(self):
        """سهم‌هایی که در سقف قیمتی هستند حذف شوند -- whatever the MFI says."""

        blocked = scoring.vetoes(candidate(
            range={"window": 120, "position_pct": 92.0, "low": 90.0,
                   "high": 180.0, "at_ceiling": True}))

        self.assertIn("at_ceiling", [rule for rule, _ in blocked])

    def test_high_mfi_far_from_support_gives_no_signal(self):
        """MFI بالا بدون حمایت قیمتی کافی نباید سیگنال بدهد.

        Without this rule an MFI above 70 is just "the market went up", and on
        the session this was written half the liquid universe qualified.
        """

        blocked = scoring.vetoes(candidate(
            supports={"nearest": {"kind": "dynamic", "label": "میانگین",
                                  "price": 70.0, "distance_pct": 30.0},
                      "levels": []}))

        self.assertIn("far_from_support", [rule for rule, _ in blocked])

    def test_outflow_is_excluded_not_merely_unscored(self):
        """بند ۴ states the transfer as a شرط مهم, so it is enforced as one."""

        blocked = scoring.vetoes(candidate(
            transfer={"net_pct": -9.0, "sessions": 5, "persistence": 0.2,
                      "into_individual_hands": False}))

        self.assertIn("no_transfer", [rule for rule, _ in blocked])

    def test_mfi_below_the_floor_is_excluded(self):
        blocked = scoring.vetoes(candidate(
            mfi={"value": 55.0, "change": 3.0, "rising": True,
                 "rise_sessions": 5, "divergence": None}))

        self.assertIn("mfi_floor", [rule for rule, _ in blocked])

    def test_suspended_name_is_excluded(self):
        """The defect Stage 1 validation found, kept out from now on.

        TSETMC emits a bar for every listed session whether or not anything
        traded, so a suspended name carries its close forward at zero volume.
        On the validation session فارس qualified with "MFI 74.8, rising 74.8"
        and "volume 4.95x average" -- computed over a window it traded 2 of 14
        sessions in. Both figures were a reopening gap.
        """

        blocked = scoring.vetoes(candidate(
            tradability={"tradable": False,
                         "reason": "در پنجره‌ی MFI فقط ۱۴٪ معامله شده",
                         "traded_mfi_pct": 14.3,
                         "traded_volume_pct": 25.0}))

        self.assertIn("thin_trading", [rule for rule, _ in blocked])

    def test_queue_locked_name_is_excluded(self):
        """A name you cannot buy is not an accumulation candidate.

        Raised by the user from live observation: two of three candidates were
        later sitting in a صف فروش. E21 had already measured the mechanism from
        the other side -- this flow signal predicts queue LOCKS, not returns --
        so a flow-based scanner that does not check for locks is reproducing
        E21's finding and calling it accumulation.
        """

        for direction in ("locked_up_now", "locked_down_now"):
            blocked = scoring.vetoes(candidate(
                locks={"locked_now": True, "locked_up_now": direction == "locked_up_now",
                       "locked_down_now": direction == "locked_down_now",
                       "reason": "در صف بوده", "band_closes_recent": 9,
                       "locked_up_recent": 3, "locked_down_recent": 0,
                       "window": 20}))

            self.assertIn("queue_locked", [rule for rule, _ in blocked])

    def test_a_name_that_already_ran_is_excluded(self):
        """The premise: money in WHILE the price has not yet moved.

        "Near the floor of the range" says where the price stands, not that it
        is standing still. A name that crashed and is bouncing violently sits in
        the bottom third of its range while being the opposite of quiet.
        """

        blocked = scoring.vetoes(candidate(
            advance={"return_pct": 19.0, "market_pct": 11.0, "excess_pct": 8.0,
                     "quiet": False, "reason": "حرکت شروع شده",
                     "window": 10, "margin": 3.0}))

        self.assertIn("already_moved", [rule for rule, _ in blocked])

    def test_an_unknown_advance_does_not_veto(self):
        """`quiet=None` means the market yardstick was unavailable.

        Absence of a benchmark must not read as a failed test -- that would
        empty the scanner on any run where fewer than 30 names could be
        measured, and it would do it silently.
        """

        blocked = scoring.vetoes(candidate(
            advance={"return_pct": 40.0, "market_pct": None,
                     "excess_pct": None, "quiet": None,
                     "reason": "میانه‌ی بازار در دسترس نیست", "window": 10}))

        self.assertNotIn("already_moved", [rule for rule, _ in blocked])

    def test_dirty_price_data_is_excluded(self):
        blocked = scoring.vetoes(candidate(
            integrity={"clean": False, "reason": "حرکت غیرممکن"}))

        self.assertIn("data_integrity", [rule for rule, _ in blocked])

    def test_a_clean_candidate_passes_every_gate(self):
        """The fixture must actually be admissible, or every test above is
        passing for the wrong reason."""

        self.assertEqual(scoring.vetoes(candidate()), [])


# ==============================
# حقوقی -> حقیقی, AND THE FACT THAT IS ONE FACT
# ==============================

class Transfer(unittest.TestCase):

    def test_institutional_supply_is_the_mirror_not_a_second_measurement(self):
        """Every rial bought was sold by someone.

        moneyflow.py refuses to report net حقیقی and net حقوقی as two agreeing
        signals, and this module inherits the rule. If these ever stopped being
        exact negatives, something recomputed one of them from the institutional
        columns -- and a caller would be entitled to read that as independent
        confirmation.
        """

        reading = {"net_pct": 20.0, "net_individual": 5.0e9, "turnover": 25e9,
                   "sessions": 5, "persistence": 0.8, "power_ratio": 2.0,
                   "sessions_positive": 4}

        out = accumulation.transfer(reading)

        self.assertEqual(out["institutional_supply"], -out["net_individual"])

    def test_absent_reading_is_none_not_zero(self):
        self.assertIsNone(accumulation.transfer(None))
        self.assertIsNone(accumulation.transfer({"net_pct": None}))


# ==============================
# کد به کد -- THE COUNTS, NOT THE VALUES
# ==============================

class CodeToCode(unittest.TestCase):

    def test_ratio_is_zero_when_tickets_are_normal_sized(self):
        """No elevation over the name's own baseline means no concentration.

        The Smart Money Ratio must not reward a name simply for having a large
        individual share of volume -- that is the market's normal state for
        most names on this exchange.
        """

        frame = flow_frame(buy_value=2.0e9, buy_count=400.0,
                           base_buy_value=2.0e9, base_buy_count=400.0)

        out = accumulation.code_to_code(frame, volume_ratio=2.0)

        self.assertAlmostEqual(out["elevation"], 1.0, places=6)
        self.assertAlmostEqual(out["smart_money_ratio"], 0.0, places=6)

    def test_ratio_is_bounded_below_one(self):
        """An absurd elevation must not saturate the measure.

        large_share = 1 - 1/elevation approaches 1 without reaching it, so a
        99x ticket and a 9x ticket stay distinguishable. A clamped measure
        would file every extreme case under "1.0, like everything else strong".
        """

        frame = flow_frame(buy_value=2.0e11, buy_count=4.0,
                           base_buy_value=2.0e9, base_buy_count=400.0)

        out = accumulation.code_to_code(frame, volume_ratio=3.0)

        self.assertLess(out["smart_money_ratio"], 1.0)
        self.assertGreater(out["elevation"], 100)

    def test_elevation_is_measured_against_the_baseline_not_itself(self):
        """The baseline must EXCLUDE the sessions it is the baseline for.

        A window compared against a mean that contains it is compared against
        itself, and the elevation collapses toward 1 exactly when the signal is
        strongest -- which is a silent failure, not a loud one.
        """

        frame = flow_frame(buy_value=8.0e9, buy_count=50.0,
                           base_buy_value=2.0e9, base_buy_count=400.0)

        out = accumulation.code_to_code(frame, volume_ratio=2.0)

        # recent ticket 160m, baseline ticket 5m -> 32x.
        self.assertAlmostEqual(out["elevation"], 32.0, places=4)

    def test_wash_pattern_is_rejected(self):
        """کد به کد ضعیف یا مصنوعی حذف شود.

        Bought 100bn and sold 98bn on the same side: enormous on the buy line,
        almost nothing actually transferred.
        """

        frame = flow_frame(buy_value=1.0e11, sell_value=0.98e11,
                           buy_count=50.0)

        out = accumulation.code_to_code(frame, volume_ratio=2.0)

        self.assertFalse(out["genuine"])
        self.assertTrue(any("wash" in r or "چرخش" in r
                            for r in out["reasons"]))

    def test_transfer_without_volume_is_rejected(self):
        """A transfer with no volume behind it is a quote, not a trade."""

        frame = flow_frame()

        out = accumulation.code_to_code(frame, volume_ratio=0.4)

        self.assertFalse(out["genuine"])

    def test_net_selling_by_individuals_is_not_a_transfer(self):
        frame = flow_frame(buy_value=1.0e9, sell_value=3.0e9)

        out = accumulation.code_to_code(frame, volume_ratio=2.0)

        self.assertFalse(out["genuine"])

    def test_too_short_a_baseline_returns_nothing(self):
        """A ratio to an anecdote is not a ratio."""

        self.assertIsNone(accumulation.code_to_code(flow_frame(sessions=6),
                                                    volume_ratio=2.0))


# ==============================
# MFI
# ==============================

class MoneyFlowIndex(unittest.TestCase):

    def test_close_only_frame_is_refused(self):
        """high == low == close is a different indicator wearing MFI's name."""

        frame = price_frame()
        frame["high"] = frame["close"]
        frame["low"] = frame["close"]

        self.assertIsNone(flow.series(frame))

    def test_short_frame_is_refused(self):
        self.assertIsNone(flow.read(price_frame(sessions=20)))

    def test_rising_is_a_strict_climb(self):
        """Sideways for a week is not momentum."""

        reading = flow.read(price_frame(sessions=300, drift=0.0, seed=3))

        if reading and reading["change"] is not None:
            self.assertEqual(reading["rising"], reading["change"] > 0)

    def test_positive_divergence_is_detected(self):
        """Price makes a lower low while MFI makes a higher low."""

        closes = pd.Series(
            [100.0] * 10 + [80.0] + [95.0] * 9 +      # prior bottom at 80
            [96.0] * 10 + [78.0] + [94.0] * 9         # deeper bottom at 78
        )

        mfi = pd.Series(
            [50.0] * 10 + [20.0] + [50.0] * 9 +       # MFI 20 at the first
            [55.0] * 10 + [45.0] + [60.0] * 9         # MFI 45 at the deeper
        )

        out = flow.divergence(closes, mfi, window=40)

        self.assertTrue(out["positive"])
        self.assertLess(out["price_change_pct"], 0)
        self.assertGreater(out["mfi_change"], 0)

    def test_lower_low_with_lower_mfi_is_not_a_divergence(self):
        closes = pd.Series([100.0] * 10 + [80.0] + [95.0] * 9 +
                           [96.0] * 10 + [78.0] + [94.0] * 9)
        mfi = pd.Series([50.0] * 10 + [40.0] + [50.0] * 9 +
                        [45.0] * 10 + [15.0] + [40.0] * 9)

        self.assertFalse(flow.divergence(closes, mfi, window=40)["positive"])


# ==============================
# PRICE POSITION
# ==============================

class Position(unittest.TestCase):

    def test_position_uses_traded_extremes(self):
        frame = price_frame(sessions=200, seed=11)

        out = position.range_position(frame)

        self.assertGreaterEqual(out["position_pct"], 0.0)
        self.assertLessEqual(out["position_pct"], 100.0)
        self.assertEqual(out["at_ceiling"],
                         out["position_pct"] > position.MAX_POSITION_PCT)

    def test_flat_series_has_no_position(self):
        """A name that traded at one price for six months has no position
        within a range, and calling that 0 would put it at the floor."""

        frame = price_frame(sessions=200)
        for column in ("open", "high", "low", "close"):
            frame[column] = 100.0

        self.assertIsNone(position.range_position(frame))

    def test_impossible_bar_fails_integrity(self):
        """price_history has carried 252 rows the market never printed.

        One is enough to redefine the top of the range and the position within
        it, so the window is checked before either reading is built.
        """

        frame = price_frame(sessions=200)
        frame.loc[150, "close"] = frame.loc[149, "close"] * 6

        out = position.integrity(frame)

        self.assertFalse(out["clean"])
        self.assertGreaterEqual(out["bad_bars"], 1)

    def test_clean_series_passes_integrity(self):
        self.assertTrue(position.integrity(price_frame())["clean"])

    def test_volume_average_excludes_the_session_it_measures(self):
        """A ratio whose denominator contains its own numerator is compressed
        toward 1 exactly when the session is unusual."""

        frame = price_frame(sessions=100, volume=1.0e6)
        frame.loc[len(frame) - 1, "volume"] = 1.0e6 * 21

        ratio = position.volume_ratio(frame, window=20)

        # Excluded: 21m / 1m = 21. Included, it would read 21/2 = 10.5.
        self.assertAlmostEqual(ratio, 21.0, places=6)

    def test_suspended_window_is_not_tradable(self):
        """Reproduces فارس: traded on 2 of the last 14 sessions."""

        frame = price_frame(sessions=100)
        frame.loc[len(frame) - 15:len(frame) - 3, "volume"] = 0.0

        out = position.tradability(frame)

        self.assertFalse(out["tradable"])
        self.assertLess(out["traded_mfi_pct"], position.MIN_TRADED_PCT)

    def test_a_name_can_fail_one_window_and_pass_the_other(self):
        """Reproduces نیان: MFI window sound, volume window not.

        The two windows are different lengths, so a gap that falls outside the
        shorter one still poisons the longer. Checking only one would have let
        this through.
        """

        frame = price_frame(sessions=100)
        # Zeros in sessions -20..-15: inside the 20-bar volume window, outside
        # the 14-bar MFI window.
        frame.loc[len(frame) - 20:len(frame) - 15, "volume"] = 0.0

        out = position.tradability(frame)

        self.assertEqual(out["traded_mfi_pct"], 100.0)
        self.assertLess(out["traded_volume_pct"], 100.0)

    def test_volume_average_ignores_sessions_that_did_not_trade(self):
        """Otherwise every reopening reads as a volume explosion.

        A name that traded 10 of 20 sessions at a normal 1m shares has a
        calendar mean of 0.5m, so a perfectly ordinary 1m-share session prints
        as "2x average". The question is whether today is unusual for a TRADING
        day.
        """

        frame = price_frame(sessions=100, volume=1.0e6)
        frame.loc[len(frame) - 11:len(frame) - 2, "volume"] = 0.0
        frame.loc[len(frame) - 1, "volume"] = 1.0e6

        ratio = position.volume_ratio(frame, window=20)

        # Over trading days only: 1m / 1m = 1.0. Over calendar days it would
        # read 2.0 and claim an abnormal session that did not happen.
        self.assertAlmostEqual(ratio, 1.0, places=6)

    def test_lock_detector_separates_a_queue_from_a_reopening(self):
        """Three things a careless reading pools: a lock, a stale bar, a gap.

        E14's detector separates them and this reuses it unchanged. A bar with
        no range is only a lock if it TRADED, if the session before it traded,
        and if the move is band-sized rather than a post-suspension gap.
        """

        frame = price_frame(sessions=60)
        n = len(frame) - 1

        # A genuine band lock: no range, +2.5%, both sessions traded.
        prev = float(frame.loc[n - 1, "close"])
        locked = prev * 1.025
        for column in ("open", "high", "low", "close"):
            frame.loc[n, column] = locked

        self.assertTrue(microstructure.locks(frame)["locked_up_now"])

        # The same bar, but nothing traded: a stale carry-forward, not a lock.
        frame.loc[n, "volume"] = 0.0
        self.assertFalse(microstructure.locks(frame)["locked_now"])

        # Traded, but the session before did not: a reopening, not a lock.
        frame.loc[n, "volume"] = 1.0e6
        frame.loc[n - 1, "volume"] = 0.0
        self.assertFalse(microstructure.locks(frame)["locked_now"])

    def test_advance_is_measured_against_the_market_not_a_fixed_number(self):
        """A fixed threshold breaks in a rally, which is when it matters.

        The same +12% name is loud in a flat market and unremarkable in one
        where everything is up 11%. This is the mechanism that collapsed 2026
        breadth to 2.9 candidates per session.
        """

        frame = price_frame(sessions=60, drift=0.0)
        n = len(frame) - 1
        base = float(frame.loc[n - 10, "close"])
        frame.loc[n, "close"] = base * 1.12

        quiet_market = microstructure.advance(frame, market=2.0)
        loud_market = microstructure.advance(frame, market=11.0)

        self.assertFalse(quiet_market["quiet"])
        self.assertTrue(loud_market["quiet"])

    def test_market_median_refuses_to_be_built_from_a_handful(self):
        """A median over five names is not a market reading."""

        few = {str(i): price_frame(sessions=60) for i in range(5)}

        self.assertIsNone(microstructure.market_advance(few))

    def test_supports_are_all_below_the_price(self):
        frame = price_frame(sessions=300, seed=5)
        close = float(frame["close"].iloc[-1])

        out = position.supports(frame, close,
                                range_reading=position.range_position(frame))

        for level in out["levels"]:
            self.assertLess(level["price"], close)
            self.assertGreater(level["distance_pct"], 0)

        # Nearest first, so the reported one really is the nearest.
        if len(out["levels"]) > 1:
            self.assertEqual(out["nearest"], out["levels"][0])


# ==============================
# MARKET-CAP BANDS
# ==============================

class SizeBands(unittest.TestCase):

    def test_terciles_not_absolute_rial_thresholds(self):
        """On a currency losing value yearly, a fixed cutoff silently
        reclassifies the whole exchange upward with nobody changing anything."""

        p33, p67 = 33e12, 121e12

        self.assertEqual(scoring.size_band(10e12, p33, p67), scoring.SMALL)
        self.assertEqual(scoring.size_band(60e12, p33, p67), scoring.MID)
        self.assertEqual(scoring.size_band(900e12, p33, p67), scoring.LARGE)

    def test_missing_cap_has_no_band(self):
        self.assertIsNone(scoring.size_band(None, 33e12, 121e12))
        self.assertIsNone(scoring.size_band(10e12, None, None))


# ==============================
# THE SENTENCE
# ==============================

class Narrative(unittest.TestCase):

    def test_narrative_names_only_what_fired(self):
        """A template asserting a condition the data did not support is worse
        than saying nothing."""

        item = candidate(code_to_code={"genuine": False,
                                       "reasons": ["الگوی چرخش"],
                                       "smart_money_ratio": 0.1,
                                       "elevation": 1.1})

        text = scoring.narrative(item, scoring.score(item))

        self.assertIn("کد به کد تأیید نشد", text)
        self.assertIn("سیگنال خرید", text)          # the standing disclaimer

    def test_narrative_reports_the_transfer_when_it_happened(self):
        item = candidate()

        text = scoring.narrative(item, scoring.score(item))

        self.assertIn("حقوقی", text)
        self.assertIn("MFI", text)


# ==============================
# WINDOW AGREEMENT WITH THE REST OF THE PLATFORM
# ==============================

class Staleness(unittest.TestCase):
    """The failure that cost two live readings.

    The scanner can only describe the last session it holds bars for. When that
    is not the most recent session, a name that was clean at that close can be
    sitting in a صف by the time the list is read -- which happened twice, on
    equity bars three sessions behind, while the panel reported the data as
    current.
    """

    def test_sessions_are_counted_not_calendar_days(self):
        """A Wednesday close read on Saturday is ONE session behind, not three.

        Tehran trades Saturday to Wednesday. Counting calendar days would call
        every normal weekend a staleness event and the warning would be ignored
        within a week.
        """

        from smart_money import scanner

        wednesday = pd.Timestamp("2026-08-19")          # a Wednesday
        self.assertEqual(wednesday.weekday(), 2)

        # Thursday and Friday are not sessions.
        days = pd.date_range(wednesday + pd.Timedelta(days=1),
                             pd.Timestamp("2026-08-22"), freq="D")
        sessions = sum(1 for d in days if d.weekday() not in (3, 4))

        self.assertEqual(sessions, 1)                   # only the Saturday

    def test_the_as_of_date_is_one_date_for_every_name(self):
        """A cross-section assembled from different dates is not one.

        The client-type store ran three sessions ahead of the price store, so
        the transfer component described a different week from the MFI
        component on the same row.
        """

        from smart_money import scanner

        frames = {}
        for i in range(20):
            frame = price_frame(sessions=60)
            frame["date"] = pd.date_range("2026-06-01", periods=60, freq="D")
            frames[str(i)] = frame

        # One name runs three days ahead of the other nineteen.
        ahead = price_frame(sessions=63)
        ahead["date"] = pd.date_range("2026-06-01", periods=63, freq="D")
        frames["ahead"] = ahead

        as_of = scanner._as_of(frames)

        # The date 90% of the population reaches, not the newest one name has.
        self.assertEqual(as_of, pd.Timestamp("2026-07-30"))

        cut = scanner._truncate(frames["ahead"], as_of)
        self.assertLessEqual(cut["date"].iloc[-1], as_of)

    def test_an_empty_population_has_no_as_of(self):
        from smart_money import scanner

        self.assertIsNone(scanner._as_of({}))


class MarketState(unittest.TestCase):
    """A relative gate cannot see that the WHOLE market has run.

    advance() asks whether a name outran the market. In a melt-up every name
    passes that test while every name is extended -- which is how two
    candidates matching the market (excess +0.3pp and -1.9pp) were nonetheless
    up 9.1% and 13.8% outright, and turned down three sessions later.
    """

    def _locked_up(self, frame):
        n = len(frame) - 1
        price = float(frame.loc[n - 1, "close"]) * 1.025
        for column in ("open", "high", "low", "close"):
            frame.loc[n, column] = price
        return frame

    def test_a_melt_up_is_flagged_as_uninformative(self):
        frames = {}
        for i in range(60):
            frame = price_frame(sessions=60, seed=i)
            if i < 20:                       # a third of the market limit-up
                frame = self._locked_up(frame)
            frames[str(i)] = frame

        state = microstructure.market_state(frames)

        self.assertEqual(state["regime"], microstructure.MELT_UP)
        self.assertFalse(state["informative"])
        self.assertGreater(state["locked_up_pct"],
                           microstructure.MELT_UP_LOCK_PCT)

    def test_a_calm_market_is_informative(self):
        frames = {str(i): price_frame(sessions=60, seed=i) for i in range(60)}

        state = microstructure.market_state(frames)

        self.assertEqual(state["regime"], microstructure.CALM)
        self.assertTrue(state["informative"])

    def test_a_handful_of_names_is_not_a_market(self):
        few = {str(i): price_frame(sessions=60, seed=i) for i in range(5)}

        self.assertIsNone(microstructure.market_state(few))


class Windows(unittest.TestCase):

    def test_flow_window_matches_moneyflow(self):
        """Two different "recent windows" in one report would be a bug the
        reader could never see."""

        self.assertEqual(accumulation.WINDOW, config.FLOW_WINDOW)
        self.assertEqual(accumulation.BASELINE_WINDOW,
                         config.FLOW_BASELINE_WINDOW)

    def test_impossible_move_is_the_governance_layer_s_constant(self):
        """Not a copy of it -- the same object.

        The scanner originally carried 200.0, copied from an example script,
        while sirat/experiments/gates.py -- the constant Stage 1 actually
        measures against -- says 100.0. A name could therefore pass the scanner
        on a bar the platform counts as impossible. Where two constants in one
        codebase mean the same thing, the governance layer's binds.
        """

        from sirat.experiments.gates import IMPOSSIBLE_SESSION_MOVE_PCT

        self.assertEqual(position.IMPOSSIBLE_MOVE_PCT,
                         IMPOSSIBLE_SESSION_MOVE_PCT)


if __name__ == "__main__":
    unittest.main()
