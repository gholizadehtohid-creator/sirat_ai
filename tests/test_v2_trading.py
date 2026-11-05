"""Tests for the v2 execution-side engines: risk_engine, trade_manager,
portfolio_engine.

No network, no database, no production state. Every function under test takes its
inputs as arguments, which is the property that makes the risk and sizing rules
checkable at all -- a sizing rule that can only be exercised by building a real
basket against a real equity is a sizing rule nobody verifies.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import factor_engine
import portfolio_engine
import regime_engine
import risk_engine
import trade_manager


EQUITY = 1_000_000_000.0


# ==============================
# STOP GEOMETRY
# ==============================

class StopDistanceTest(unittest.TestCase):

    def test_an_atr_stop_is_the_atr_multiple(self):
        distance, source, _notes = risk_engine.stop_distance(1000.0, atr=40.0)

        self.assertAlmostEqual(distance, 40.0 * config.V2_STOP_ATR_MULTIPLE)
        self.assertEqual(source, "atr")

    def test_without_an_atr_it_falls_back_to_two_price_bands(self):
        distance, source, notes = risk_engine.stop_distance(1000.0, atr=None)

        self.assertAlmostEqual(distance,
                               1000.0 * config.PRICE_BAND_PCT * 2 / 100.0)
        self.assertTrue(source.startswith("price_band"))
        self.assertTrue(notes)

    def test_a_stop_inside_the_daily_band_is_widened_to_the_floor(self):
        """A stop tighter than the band is hit by nothing but noise."""

        distance, source, notes = risk_engine.stop_distance(1000.0, atr=1.0)

        self.assertAlmostEqual(distance,
                               1000.0 * config.V2_MIN_STOP_PCT / 100.0)
        self.assertIn("floor", source)
        self.assertTrue(any("کف" in n for n in notes))

    def test_an_absurdly_wide_stop_is_pulled_to_the_ceiling(self):
        distance, source, _notes = risk_engine.stop_distance(1000.0, atr=500.0)

        self.assertAlmostEqual(distance,
                               1000.0 * config.RISK_MAX_STOP_PCT / 100.0)
        self.assertIn("ceiling", source)

    def test_no_price_means_no_stop(self):
        distance, source, notes = risk_engine.stop_distance(None, atr=40.0)

        self.assertIsNone(distance)
        self.assertIsNone(source)
        self.assertTrue(notes)


# ==============================
# THE RISK SCORE
# ==============================

class RiskScoreTest(unittest.TestCase):

    CALM = {"volatility_60": 22.0, "atr_pct": 1.8,
            "avg_value_20": config.MIN_DAILY_VALUE * 15}

    WILD = {"volatility_60": 95.0, "atr_pct": 6.5,
            "avg_value_20": config.MIN_DAILY_VALUE * 0.2}

    def test_higher_is_safer(self):
        calm = risk_engine.risk_score(self.CALM)["score"]
        wild = risk_engine.risk_score(self.WILD)["score"]

        self.assertGreater(calm, wild,
                           "the orientation is inherited from "
                           "scoring.risk_score and must not invert")

    def test_no_input_returns_none_rather_than_zero(self):
        result = risk_engine.risk_score({})

        self.assertIsNone(result["score"])

    def test_a_position_that_cannot_be_exited_is_riskier(self):
        quick = risk_engine.risk_score(self.CALM, exit_sessions=0.5)["score"]
        stuck = risk_engine.risk_score(self.CALM, exit_sessions=12.0)["score"]

        self.assertGreater(quick, stuck)

    def test_a_pending_undirected_event_reduces_the_score(self):
        clean = risk_engine.risk_score(self.CALM)["score"]
        pending = risk_engine.risk_score(self.CALM, event_count=2)["score"]

        self.assertGreater(clean, pending)

    def test_a_crowded_sector_reduces_the_score(self):
        room = risk_engine.risk_score(self.CALM, sector_share_pct=5.0)["score"]
        full = risk_engine.risk_score(
            self.CALM, sector_share_pct=config.MAX_SECTOR_PCT)["score"]

        self.assertGreater(room, full)

    def test_the_price_band_caveat_always_travels_with_the_number(self):
        result = risk_engine.risk_score(self.CALM)

        self.assertIn("کف ریسک", result["caveat"])


# ==============================
# POSITION SIZE
# ==============================

class PositionSizeTest(unittest.TestCase):

    def test_the_risk_budget_sets_the_size(self):
        """Risking 1% of equity to a 10% stop buys a 10% position."""

        result = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY, risk_pct=1.0)

        self.assertAlmostEqual(result["risk_pct_of_equity"], 1.0, places=2)
        self.assertAlmostEqual(result["weight_pct"], 10.0, places=1)
        self.assertEqual(result["bound_by"], risk_engine.BOUND_RISK)

    def test_the_position_cap_binds_before_the_risk_budget(self):
        """A very tight stop would otherwise buy the whole account."""

        result = risk_engine.position_size(
            price=1000.0, distance=5.0, equity=EQUITY, risk_pct=1.0)

        self.assertLessEqual(result["weight_pct"], config.MAX_POSITION_PCT)
        self.assertEqual(result["bound_by"], risk_engine.BOUND_POSITION_CAP)

    def test_a_name_with_no_turnover_gets_no_position(self):
        """adv == 0 used to skip the liquidity cap entirely."""

        result = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY, risk_pct=1.0,
            adv=0.0)

        self.assertEqual(result.get("shares") or 0, 0)
        self.assertEqual(result["bound_by"], risk_engine.BOUND_LIQUIDITY)

    def test_liquidity_binds_before_the_position_cap(self):
        thin_adv = 1_000_000_000.0        # 1 bn rials a day

        result = risk_engine.position_size(
            price=1000.0, distance=20.0, equity=EQUITY, adv=thin_adv)

        self.assertEqual(result["bound_by"], risk_engine.BOUND_LIQUIDITY)
        self.assertLessEqual(result["adv_participation_pct"],
                             config.MAX_ADV_PARTICIPATION_PCT + 0.01)

    def test_a_full_sector_refuses_the_position_outright(self):
        room = {"sector": "فلزات", "full": True, "value_left": 0.0}

        result = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY, sector_room=room)

        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["bound_by"], risk_engine.BOUND_SECTOR)

    def test_a_partly_full_sector_shrinks_the_position(self):
        room = {"sector": "فلزات", "full": False,
                "value_left": EQUITY * 0.04}

        result = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY)

        limited = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY, sector_room=room)

        self.assertLess(limited["position_value"], result["position_value"])
        self.assertEqual(limited["bound_by"], risk_engine.BOUND_SECTOR)

    def test_available_cash_binds_last(self):
        result = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY,
            cash_available=EQUITY * 0.05)

        self.assertEqual(result["bound_by"], risk_engine.BOUND_EQUITY)
        self.assertLessEqual(result["position_value"], EQUITY * 0.05)

    def test_a_position_below_the_floor_is_refused_not_rounded_up(self):
        result = risk_engine.position_size(
            price=1000.0, distance=100.0, equity=EQUITY,
            cash_available=EQUITY * 0.001)

        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["bound_by"], risk_engine.BOUND_TOO_SMALL)

    def test_shares_round_down_never_up(self):
        result = risk_engine.position_size(
            price=999.0, distance=100.0, equity=EQUITY, risk_pct=1.0)

        self.assertLessEqual(result["shares"] * 999.0,
                             (EQUITY * 0.01 / 100.0) * 999.0 + 0.01)

    def test_no_stop_degrades_to_the_position_cap_and_says_so(self):
        result = risk_engine.position_size(
            price=1000.0, distance=None, equity=EQUITY)

        self.assertAlmostEqual(result["weight_pct"], config.MAX_POSITION_PCT,
                               places=1)
        self.assertTrue(any("حد ضرر" in n for n in result["notes"]))

    def test_no_equity_means_no_position(self):
        self.assertEqual(
            risk_engine.position_size(1000.0, 100.0, 0)["shares"], 0)


# ==============================
# SECTOR CAPACITY
# ==============================

class SectorCapacityTest(unittest.TestCase):

    def _positions(self, count, sector="فلزات", value=None):
        value = value if value is not None else EQUITY * 0.1
        return [{"symbol": "S%d" % i, "sector_name": sector,
                 "position_value": value} for i in range(count)]

    def test_an_empty_book_has_full_room(self):
        room = risk_engine.sector_capacity("فلزات", [], EQUITY)

        self.assertEqual(room["held_count"], 0)
        self.assertFalse(room["full"])
        self.assertAlmostEqual(room["value_left"],
                               EQUITY * config.MAX_SECTOR_PCT / 100.0)

    def test_the_count_limit_fills_before_the_value_limit(self):
        room = risk_engine.sector_capacity(
            "فلزات", self._positions(config.MAX_PER_SECTOR, value=EQUITY * 0.02),
            EQUITY)

        self.assertTrue(room["full"])
        self.assertGreater(room["value_left"], 0,
                           "there is value room left, but no name room")

    def test_other_sectors_do_not_count(self):
        positions = (self._positions(2, "فلزات")
                     + self._positions(2, "پتروشیمی"))

        room = risk_engine.sector_capacity("خودرو", positions, EQUITY)

        self.assertEqual(room["held_count"], 0)

    def test_a_missing_sector_name_groups_under_a_single_bucket(self):
        positions = [{"symbol": "A", "position_value": EQUITY * 0.05},
                     {"symbol": "B", "sector_name": "", "position_value": EQUITY * 0.05}]

        room = risk_engine.sector_capacity(None, positions, EQUITY)

        self.assertEqual(room["sector"], "?")
        self.assertEqual(room["held_count"], 2)


# ==============================
# THE FULL RISK ASSESSMENT
# ==============================

class RiskAssessTest(unittest.TestCase):

    READING = {
        "symbol": "فولاد", "ins_code": "1001", "sector": "فلزات",
        "close": 5000.0, "atr": 150.0, "atr_pct": 3.0,
        "adv": config.MIN_DAILY_VALUE * 10, "volatility_60": 35.0,
    }

    def test_a_normal_candidate_is_tradable(self):
        result = risk_engine.assess(self.READING, equity=EQUITY)

        self.assertTrue(result["tradable"])
        self.assertGreater(result["size"]["shares"], 0)
        self.assertIsNotNone(result["risk_score"])
        self.assertEqual(result["blockers"], [])

    def test_the_stop_price_sits_below_the_entry(self):
        result = risk_engine.assess(self.READING, equity=EQUITY)

        self.assertLess(result["stop_price"], self.READING["close"])
        self.assertAlmostEqual(
            result["stop_price"] + result["stop_distance"],
            self.READING["close"], places=2)

    def test_a_defensive_regime_shrinks_the_position(self):
        normal = risk_engine.assess(self.READING, equity=EQUITY)

        defensive = risk_engine.assess(
            self.READING, equity=EQUITY,
            regime={"available": True,
                    "trading_mode": regime_engine.DEFENSIVE,
                    "mode_label": "تدافعی",
                    "mode_budget_share":
                        config.TRADING_MODE_BUDGET[regime_engine.DEFENSIVE]})

        self.assertLess(defensive["size"]["position_value"],
                        normal["size"]["position_value"])
        self.assertLess(defensive["risk_pct_used"], normal["risk_pct_used"])

    def test_an_aggressive_regime_does_not_exceed_the_base_risk(self):
        """The budget share is capped at 1.0, so nothing loosens the base rule."""

        normal = risk_engine.assess(self.READING, equity=EQUITY)

        aggressive = risk_engine.assess(
            self.READING, equity=EQUITY,
            regime={"available": True,
                    "trading_mode": regime_engine.AGGRESSIVE,
                    "mode_budget_share":
                        config.TRADING_MODE_BUDGET[regime_engine.AGGRESSIVE]})

        self.assertLessEqual(aggressive["risk_pct_used"],
                             normal["risk_pct_used"] + 1e-9)

    def test_an_illiquid_volatile_name_is_refused(self):
        result = risk_engine.assess({
            "symbol": "X", "sector": "?", "close": 500.0, "atr": 40.0,
            "atr_pct": 8.0, "adv": config.MIN_DAILY_VALUE * 0.05,
            "volatility_60": 120.0,
        }, equity=EQUITY)

        self.assertFalse(result["tradable"])
        self.assertTrue(result["blockers"])

    def test_a_full_sector_blocks_the_candidate(self):
        held = [{"symbol": "S%d" % i, "sector_name": "فلزات",
                 "position_value": EQUITY * 0.05}
                for i in range(config.MAX_PER_SECTOR)]

        result = risk_engine.assess(self.READING, equity=EQUITY,
                                    positions=held)

        self.assertFalse(result["tradable"])
        self.assertEqual(result["size"]["bound_by"], risk_engine.BOUND_SECTOR)


# ==============================
# TARGETS
# ==============================

class TargetTest(unittest.TestCase):

    def test_the_targets_are_multiples_of_the_stop_distance(self):
        level = trade_manager.targets(entry=1000.0, stop_distance=50.0)

        self.assertAlmostEqual(level["target_1"],
                               1000.0 + 50.0 * config.TARGET_1_RR, places=2)
        self.assertAlmostEqual(level["target_2"],
                               1000.0 + 50.0 * config.TARGET_2_RR, places=2)
        self.assertAlmostEqual(level["rr_1"], config.TARGET_1_RR, places=2)
        self.assertFalse(level["capped"])

    def test_overhead_supply_pulls_the_first_target_down(self):
        free = trade_manager.targets(1000.0, 50.0)
        walled = trade_manager.targets(1000.0, 50.0, overhead_pct=6.0)

        self.assertLess(walled["target_1"], free["target_1"])
        self.assertTrue(walled["capped"])
        self.assertEqual(walled["cap_source"], "overhead")

    def test_a_capped_target_lowers_the_reported_reward_to_risk(self):
        """The recomputation that changes decisions."""

        walled = trade_manager.targets(1000.0, 50.0, overhead_pct=6.0)

        self.assertLess(walled["rr_1"], config.TARGET_1_RR)

    def test_a_distant_ceiling_does_not_raise_a_target(self):
        free = trade_manager.targets(1000.0, 50.0)
        far = trade_manager.targets(1000.0, 50.0, overhead_pct=40.0)

        self.assertAlmostEqual(free["target_1"], far["target_1"], places=2)
        self.assertFalse(far["capped"])

    def test_a_wall_right_above_the_price_leaves_no_trade(self):
        tight = trade_manager.targets(1000.0, 50.0, overhead_pct=0.4)

        self.assertTrue(tight["capped"])
        self.assertEqual(tight["cap_source"], "overhead_tight")
        self.assertLess(tight["rr_1"], config.MIN_RISK_REWARD)

    def test_a_target_is_never_placed_below_the_entry(self):
        """A ceiling within half a percent lands the trimmed level under the entry."""

        for room in (0.1, 0.2, 0.4, 0.49):
            level = trade_manager.targets(1000.0, 50.0, overhead_pct=room)
            self.assertGreaterEqual(level["target_1"], 1000.0,
                                    "overhead=%s produced a target below the "
                                    "entry price" % room)
            self.assertGreaterEqual(level["rr_1"], 0.0)

    def test_the_first_target_sits_above_the_minimum_acceptable_ratio(self):
        """Otherwise any trim at all rejects, and the trim logic is dead code."""

        self.assertGreater(config.TARGET_1_RR, config.MIN_RISK_REWARD)

    def test_a_moderate_wall_trims_the_target_without_killing_the_trade(self):
        """The case that could not exist when T1 sat on the acceptance boundary."""

        level = trade_manager.targets(1000.0, 40.0, overhead_pct=9.0)

        self.assertTrue(level["capped"])
        self.assertEqual(level["cap_source"], "overhead")
        self.assertLess(level["target_1"], 1000.0 + 40.0 * config.TARGET_1_RR)
        self.assertGreaterEqual(level["rr_1"], config.MIN_RISK_REWARD)

    def test_the_second_target_always_sits_above_the_first(self):
        for room in (0.2, 1.0, 3.0, 6.0, 12.0, 40.0, None):
            level = trade_manager.targets(1000.0, 50.0, overhead_pct=room)
            self.assertGreater(level["target_2"], level["target_1"],
                               "overhead=%s" % room)

    def test_no_stop_distance_means_no_targets(self):
        level = trade_manager.targets(1000.0, None)

        self.assertIsNone(level["target_1"])
        self.assertIsNone(level["rr_1"])


# ==============================
# HOLDING PERIOD
# ==============================

class HoldingPeriodTest(unittest.TestCase):

    def test_a_wider_target_takes_longer(self):
        near, _ = trade_manager.holding_period(6.0, atr_pct=2.0)
        far, _ = trade_manager.holding_period(20.0, atr_pct=2.0)

        self.assertGreater(far, near)

    def test_a_faster_share_reaches_the_same_target_sooner(self):
        slow, _ = trade_manager.holding_period(15.0, atr_pct=1.5)
        fast, _ = trade_manager.holding_period(15.0, atr_pct=5.0)

        self.assertGreater(slow, fast)

    def test_without_an_atr_the_duration_is_unknown_not_guessed(self):
        sessions, note = trade_manager.holding_period(10.0, atr_pct=None)

        self.assertIsNone(sessions)
        self.assertIn("cannot be estimated", note)

    def test_the_estimate_is_clamped_to_the_swing_horizon(self):
        floored, note = trade_manager.holding_period(1.0, atr_pct=8.0)
        capped, note2 = trade_manager.holding_period(90.0, atr_pct=1.0)

        self.assertEqual(floored, config.HOLD_MIN_SESSIONS)
        self.assertEqual(capped, config.HOLD_MAX_SESSIONS)
        self.assertIn("افق", note2)

    def test_a_non_positive_target_has_no_duration(self):
        self.assertIsNone(trade_manager.holding_period(0.0, atr_pct=2.0)[0])
        self.assertIsNone(trade_manager.holding_period(-4.0, atr_pct=2.0)[0])


# ==============================
# THE SIGNAL
# ==============================

class PlanTest(unittest.TestCase):

    READING = {
        "symbol": "فولاد", "ins_code": "1001", "sector": "فلزات",
        "close": 5000.0, "atr": 150.0, "atr_pct": 3.0,
        "adv": config.MIN_DAILY_VALUE * 10, "volatility_60": 35.0,
    }

    def _buy(self):
        return {"action": factor_engine.BUY, "score": 82, "conviction": 0.44,
                "reasons": ["امتیاز سوئینگ ۸۲"]}

    def test_a_signal_carries_every_field_the_spec_asks_for(self):
        risk = risk_engine.assess(self.READING, equity=EQUITY)

        signal = trade_manager.plan(self.READING, self._buy(), risk)

        for field in ("symbol", "action", "entry_price", "stop_loss",
                      "target_1", "target_2", "risk_reward",
                      "expected_holding_sessions"):
            self.assertIn(field, signal)
            self.assertIsNotNone(signal[field], field)

    def test_the_levels_are_ordered(self):
        risk = risk_engine.assess(self.READING, equity=EQUITY)
        signal = trade_manager.plan(self.READING, self._buy(), risk)

        self.assertLess(signal["stop_loss"], signal["entry_price"])
        self.assertLess(signal["entry_price"], signal["target_1"])
        self.assertLess(signal["target_1"], signal["target_2"])

    def test_an_unbuyable_name_is_watched_not_bought(self):
        """A buy queue or a halt: HOLD, with the reason on the signal."""

        reading = dict(self.READING, entry_blocker="صف خرید")
        risk = risk_engine.assess(reading, equity=EQUITY)

        signal = trade_manager.plan(reading, self._buy(), risk)

        self.assertEqual(signal["action"], factor_engine.HOLD)
        self.assertTrue(any("صف خرید" in d
                            for d in signal.get("downgrades") or []))

    def test_a_nearby_ceiling_downgrades_a_buy_to_avoid(self):
        """The whole reason the reward/risk is recomputed."""

        risk = risk_engine.assess(self.READING, equity=EQUITY)

        signal = trade_manager.plan(self.READING, self._buy(), risk,
                                    overhead_pct=1.0)

        self.assertEqual(signal["action"], factor_engine.AVOID)
        self.assertTrue(signal["downgrades"])
        self.assertLess(signal["risk_reward"], config.MIN_RISK_REWARD)

    def test_a_trimmed_but_still_adequate_target_keeps_the_buy(self):
        """The trim must be able to ACCEPT, not only reject."""

        reading = dict(self.READING, close=1000.0, atr=20.0, atr_pct=2.0)

        # Stop at the 6% floor (E26), so target 1 sits 15% up; resistance at
        # 13% trims it and still leaves 13/6 = 2.17 >= MIN_RISK_REWARD. (At
        # 9%, the overhead this test used with the old 3% floor, the trimmed
        # target no longer pays for the stop and the BUY is refused.)
        signal = trade_manager.plan(reading, self._buy(), overhead_pct=13.0)

        self.assertEqual(signal["action"], factor_engine.BUY)
        self.assertTrue(signal["target_capped"])
        self.assertEqual(signal["downgrades"], [])
        self.assertGreaterEqual(signal["risk_reward"], config.MIN_RISK_REWARD)

    def test_a_plan_can_only_downgrade_never_upgrade(self):
        avoid = {"action": factor_engine.AVOID, "score": 20, "reasons": []}

        risk = risk_engine.assess(self.READING, equity=EQUITY)

        signal = trade_manager.plan(self.READING, avoid, risk)

        self.assertEqual(signal["action"], factor_engine.AVOID)

    def test_an_untradable_risk_verdict_downgrades_the_buy(self):
        illiquid = dict(self.READING, adv=config.MIN_DAILY_VALUE * 0.02,
                        atr_pct=9.0, volatility_60=130.0)

        risk = risk_engine.assess(illiquid, equity=EQUITY)

        signal = trade_manager.plan(illiquid, self._buy(), risk)

        self.assertEqual(signal["action"], factor_engine.AVOID)
        self.assertTrue(signal["downgrades"])

    def test_a_plan_without_a_risk_assessment_still_has_a_stop(self):
        signal = trade_manager.plan(self.READING, self._buy())

        self.assertIsNotNone(signal["stop_loss"])
        self.assertLess(signal["stop_loss"], signal["entry_price"])
        self.assertTrue(any("risk_engine" in n for n in signal["notes"]))

    def test_the_same_stop_rule_is_used_with_and_without_the_risk_engine(self):
        with_engine = trade_manager.plan(
            self.READING, self._buy(),
            risk_engine.assess(self.READING, equity=EQUITY))

        without = trade_manager.plan(self.READING, self._buy())

        self.assertAlmostEqual(with_engine["stop_loss"], without["stop_loss"],
                               places=2)

    def test_describe_renders_without_raising_on_a_sparse_signal(self):
        lines = trade_manager.describe(trade_manager.plan({"symbol": "X"}))

        self.assertTrue(lines)
        self.assertTrue(all(isinstance(line, str) for line in lines))

    def test_the_entry_band_straddles_the_close(self):
        signal = trade_manager.plan(self.READING, self._buy())

        self.assertLess(signal["entry_low"], signal["entry_price"])
        self.assertGreater(signal["entry_high"], signal["entry_price"])


# ==============================
# PORTFOLIO POLICY
# ==============================

class PolicyTest(unittest.TestCase):

    def test_the_default_policy_mirrors_config(self):
        policy = portfolio_engine.from_config()

        self.assertEqual(policy.max_positions, config.PORTFOLIO_SIZE)
        self.assertEqual(policy.max_per_sector, config.MAX_PER_SECTOR)
        self.assertEqual(policy.max_sector_pct, config.MAX_SECTOR_PCT)

    def test_replace_returns_a_new_policy(self):
        policy = portfolio_engine.from_config()
        tighter = policy.replace(max_positions=5)

        self.assertEqual(tighter.max_positions, 5)
        self.assertEqual(policy.max_positions, config.PORTFOLIO_SIZE,
                         "replace must not mutate the original")

    def test_for_regime_only_ever_tightens(self):
        policy = portfolio_engine.from_config()

        for permitted in (0.0, 20.0, 55.0, 90.0, 100.0):
            adjusted = policy.for_regime({"available": True,
                                          "max_invested_pct": permitted})

            self.assertLessEqual(adjusted.target_invested_pct,
                                 policy.target_invested_pct + 1e-9)
            self.assertGreaterEqual(adjusted.min_cash_pct,
                                    policy.min_cash_pct - 1e-9)

    def test_an_undetectable_market_permits_no_exposure(self):
        adjusted = portfolio_engine.from_config().for_regime(
            {"available": False})

        self.assertEqual(adjusted.target_invested_pct, 0.0)
        self.assertEqual(adjusted.min_cash_pct, 100.0)


class StateAndCapacityTest(unittest.TestCase):

    def _positions(self):
        return [
            {"ins_code": "1", "symbol": "A", "sector_name": "فلزات",
             "position_value": EQUITY * 0.10},
            {"ins_code": "2", "symbol": "B", "sector_name": "فلزات",
             "position_value": EQUITY * 0.08},
            {"ins_code": "3", "symbol": "C", "sector_name": "پتروشیمی",
             "position_value": EQUITY * 0.12},
        ]

    def test_state_groups_by_sector(self):
        state = portfolio_engine.state_of(self._positions(), EQUITY)

        self.assertEqual(state["positions"], 3)
        self.assertEqual(state["by_sector"]["فلزات"]["count"], 2)
        self.assertAlmostEqual(state["by_sector"]["فلزات"]["share_pct"], 18.0,
                               places=1)
        self.assertAlmostEqual(state["invested_pct"], 30.0, places=1)

    def test_cash_is_inferred_when_not_supplied_and_labelled_as_such(self):
        state = portfolio_engine.state_of(self._positions(), EQUITY)

        self.assertAlmostEqual(state["cash_pct"], 70.0, places=1)
        self.assertIn("inferred", state["cash_source"])

    def test_supplied_cash_is_used_verbatim(self):
        state = portfolio_engine.state_of(self._positions(), EQUITY,
                                          cash=EQUITY * 0.2)

        self.assertAlmostEqual(state["cash_pct"], 20.0, places=1)
        self.assertEqual(state["cash_source"], "supplied")

    def test_ten_positions_at_the_cap_do_not_sum_past_the_portfolio_limit(self):
        """The 150% basket the first portfolio.py produced."""

        policy = portfolio_engine.from_config()

        positions = [
            {"ins_code": str(i), "symbol": "S%d" % i,
             "sector_name": "S%d" % i,
             "position_value": EQUITY * policy.max_position_pct / 100.0}
            for i in range(policy.max_positions)
        ]

        room = portfolio_engine.capacity(
            policy, portfolio_engine.state_of(positions, EQUITY))

        self.assertEqual(room["value_left"], 0.0)
        self.assertTrue(room["full"])

    def test_capacity_reports_which_limit_binds(self):
        policy = portfolio_engine.from_config()

        state = portfolio_engine.state_of([], EQUITY, cash=EQUITY * 0.2)

        room = portfolio_engine.capacity(policy, state)

        self.assertEqual(room["limited_by"], "cash_floor")

    def test_sector_room_shrinks_as_a_sector_fills(self):
        policy = portfolio_engine.from_config()
        state = portfolio_engine.state_of(self._positions(), EQUITY)

        room = portfolio_engine.sector_room(policy, state, "فلزات")

        self.assertEqual(room["held_count"], 2)
        self.assertAlmostEqual(
            room["value_left"],
            EQUITY * config.MAX_SECTOR_PCT / 100.0 - EQUITY * 0.18, places=0)


class AdmitTest(unittest.TestCase):

    def setUp(self):
        self.policy = portfolio_engine.from_config()

    def _candidate(self, sector="خودرو", value=None, code="99"):
        return {"ins_code": code, "symbol": "NEW", "sector": sector,
                "position_value": value if value is not None else EQUITY * 0.05}

    def test_an_empty_book_admits_a_normal_candidate(self):
        state = portfolio_engine.state_of([], EQUITY)

        verdict = portfolio_engine.admit(self.policy, state, self._candidate())

        self.assertTrue(verdict["admitted"])
        self.assertFalse(verdict["resized"])

    def test_a_duplicate_is_refused(self):
        state = portfolio_engine.state_of(
            [{"ins_code": "99", "symbol": "NEW", "sector_name": "خودرو",
              "position_value": EQUITY * 0.05}], EQUITY)

        verdict = portfolio_engine.admit(self.policy, state, self._candidate())

        self.assertFalse(verdict["admitted"])
        self.assertEqual(verdict["reason_key"],
                         portfolio_engine.REFUSE_DUPLICATE)

    def test_a_full_book_is_refused(self):
        positions = [
            {"ins_code": str(i), "symbol": "S%d" % i, "sector_name": "S%d" % i,
             "position_value": EQUITY * 0.05}
            for i in range(self.policy.max_positions)
        ]

        verdict = portfolio_engine.admit(
            self.policy, portfolio_engine.state_of(positions, EQUITY),
            self._candidate())

        self.assertFalse(verdict["admitted"])
        self.assertEqual(verdict["reason_key"], portfolio_engine.REFUSE_FULL)

    def test_a_full_sector_is_refused_by_count(self):
        positions = [
            {"ins_code": str(i), "symbol": "S%d" % i, "sector_name": "خودرو",
             "position_value": EQUITY * 0.02}
            for i in range(self.policy.max_per_sector)
        ]

        verdict = portfolio_engine.admit(
            self.policy, portfolio_engine.state_of(positions, EQUITY),
            self._candidate())

        self.assertFalse(verdict["admitted"])
        self.assertEqual(verdict["reason_key"],
                         portfolio_engine.REFUSE_SECTOR_COUNT)

    def test_a_candidate_is_resized_to_the_room_that_is_left(self):
        """A verdict of yes with no resize would break the limit that allowed it."""

        state = portfolio_engine.state_of([], EQUITY, cash=EQUITY * 0.14)

        verdict = portfolio_engine.admit(
            self.policy, state, self._candidate(value=EQUITY * 0.12))

        self.assertTrue(verdict["admitted"])
        self.assertTrue(verdict["resized"])
        self.assertLess(verdict["allowed_value"], EQUITY * 0.12)

    def test_a_candidate_over_the_position_cap_is_cut_to_it(self):
        state = portfolio_engine.state_of([], EQUITY)

        verdict = portfolio_engine.admit(
            self.policy, state, self._candidate(value=EQUITY * 0.9))

        self.assertTrue(verdict["admitted"])
        self.assertAlmostEqual(
            verdict["allowed_value"],
            EQUITY * self.policy.max_position_pct / 100.0, places=0)
        self.assertEqual(verdict["binding"], "position_cap")

    def test_a_zero_size_candidate_is_refused(self):
        state = portfolio_engine.state_of([], EQUITY)

        verdict = portfolio_engine.admit(self.policy, state,
                                         self._candidate(value=0.0))

        self.assertFalse(verdict["admitted"])
        self.assertEqual(verdict["reason_key"], portfolio_engine.REFUSE_NO_SIZE)


class AllocateTest(unittest.TestCase):

    def _signal(self, symbol, sector, price=1000.0, value=None):
        return {
            "ins_code": symbol, "symbol": symbol, "sector": sector,
            "entry_price": price,
            "position_value": value if value is not None else EQUITY * 0.08,
            "action": factor_engine.BUY,
        }

    def test_one_name_per_sector_comes_before_any_second(self):
        """The two-pass rule. Rank order alone fills the book from one sector."""

        policy = portfolio_engine.from_config().replace(max_positions=3)

        signals = [
            self._signal("A", "فلزات"),
            self._signal("B", "فلزات"),
            self._signal("C", "فلزات"),
            self._signal("D", "پتروشیمی"),
            self._signal("E", "خودرو"),
        ]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        chosen = [row["symbol"] for row in result["accepted"]]

        self.assertEqual(chosen[:3], ["A", "D", "E"],
                         "the first pass takes one name from each sector "
                         "before any sector gets a second")

    def test_second_names_are_taken_when_there_is_room(self):
        policy = portfolio_engine.from_config().replace(max_positions=4)

        signals = [
            self._signal("A", "فلزات"),
            self._signal("B", "فلزات"),
            self._signal("C", "پتروشیمی"),
        ]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        chosen = [row["symbol"] for row in result["accepted"]]

        self.assertEqual(chosen, ["A", "C", "B"])

    def test_the_sector_count_cap_is_respected_in_the_second_pass(self):
        policy = portfolio_engine.from_config().replace(
            max_positions=10, max_per_sector=2)

        signals = [self._signal(name, "فلزات", value=EQUITY * 0.03)
                   for name in ("A", "B", "C", "D")]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        self.assertEqual(len(result["accepted"]), 2)
        self.assertTrue(any(r["reason_key"]
                            == portfolio_engine.REFUSE_SECTOR_COUNT
                            for r in result["rejected"]))

    def test_the_book_fills_its_position_count_rather_than_concentrating(self):
        """Greedy allocation at the per-position cap produces six names, not ten."""

        policy = portfolio_engine.from_config()

        # Every candidate wants the per-position cap, as a risk-based size does
        # when stops are around 5-6% of price.
        signals = [self._signal("S%d" % i, "SEC%d" % i,
                                value=EQUITY * policy.max_position_pct / 100.0)
                   for i in range(30)]

        greedy = portfolio_engine.allocate(policy, signals, EQUITY,
                                           spread=False)
        spread = portfolio_engine.allocate(policy, signals, EQUITY)

        self.assertLess(len(greedy["accepted"]), policy.max_positions)
        self.assertEqual(len(spread["accepted"]), policy.max_positions)

        self.assertGreater(len(spread["accepted"]), len(greedy["accepted"]))

    def test_spreading_still_lets_risk_sizing_ask_for_less(self):
        """The ceiling cuts; it never inflates a position to the even share."""

        policy = portfolio_engine.from_config()

        signals = [self._signal("BIG", "SEC1",
                                value=EQUITY * policy.max_position_pct / 100.0)]

        # A volatile name whose risk-based size is only 3% of equity.
        signals.extend(
            self._signal("S%d" % i, "SEC%d" % (i + 2), value=EQUITY * 0.03)
            for i in range(5))

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        small = [row for row in result["accepted"] if row["symbol"] != "BIG"]

        for row in small:
            self.assertAlmostEqual(row["allocated_weight_pct"], 3.0, places=1)

    def test_spreading_never_exceeds_the_per_position_cap(self):
        """A budget divided among two names must not hand either one 45%."""

        policy = portfolio_engine.from_config()

        signals = [self._signal("A", "SEC1", value=EQUITY * 0.9),
                   self._signal("B", "SEC2", value=EQUITY * 0.9)]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        for row in result["accepted"]:
            self.assertLessEqual(row["allocated_weight_pct"],
                                 policy.max_position_pct + 0.01)

    def test_the_allocated_size_replaces_the_requested_one(self):
        """Ten lines reading '15% of equity' under a 90% total is three numbers
        that cannot all be true."""

        policy = portfolio_engine.from_config()

        signals = [self._signal("S%d" % i, "SEC%d" % i,
                                value=EQUITY * policy.max_position_pct / 100.0)
                   for i in range(10)]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        total = 0.0

        for row in result["accepted"]:
            self.assertEqual(row["shares"], row["allocated_shares"])
            self.assertEqual(row["position_value"], row["allocated_value"])
            self.assertEqual(row["weight_pct"], row["allocated_weight_pct"])
            self.assertAlmostEqual(row["requested_weight_pct"] or 0.0, 0.0)
            total += row["weight_pct"]

        self.assertAlmostEqual(total, policy.target_invested_pct, delta=1.0)

    def test_the_risk_carried_follows_the_allocated_size(self):
        policy = portfolio_engine.from_config()

        signal = self._signal("A", "SEC1", price=1000.0,
                              value=EQUITY * 0.5)
        signal["stop_distance_pct"] = 5.0

        result = portfolio_engine.allocate(policy, [signal], EQUITY)

        row = result["accepted"][0]

        self.assertAlmostEqual(
            row["risk_rials"], row["shares"] * 1000.0 * 0.05, delta=1.0)
        self.assertAlmostEqual(
            row["risk_pct_of_equity"],
            row["risk_rials"] / EQUITY * 100.0, places=3)

    def test_the_spread_ceiling_is_reported(self):
        result = portfolio_engine.allocate(
            portfolio_engine.from_config(),
            [self._signal("A", "SEC1")], EQUITY)

        self.assertTrue(result["spread"])
        self.assertIsNotNone(result["spread_ceiling"])

    def test_a_full_book_has_no_spread_ceiling_to_compute(self):
        policy = portfolio_engine.from_config().replace(max_positions=1)

        held = [{"ins_code": "H", "symbol": "H", "sector_name": "X",
                 "position_value": EQUITY * 0.1}]

        room = portfolio_engine.capacity(
            policy, portfolio_engine.state_of(held, EQUITY))

        self.assertIsNone(portfolio_engine.spread_ceiling(
            policy, room, [self._signal("A", "SEC1")]))

    def test_the_allocated_total_stays_inside_the_invested_target(self):
        policy = portfolio_engine.from_config()

        signals = [self._signal("S%d" % i, "SEC%d" % i, value=EQUITY * 0.14)
                   for i in range(10)]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        total = sum(row["allocated_value"] for row in result["accepted"])

        self.assertLessEqual(total / EQUITY * 100.0,
                             policy.target_invested_pct + 0.01)

    def test_supplied_cash_is_spent_as_positions_are_added(self):
        """Without this the cash floor never binds however much is allocated."""

        policy = portfolio_engine.from_config()

        signals = [self._signal("S%d" % i, "SEC%d" % i, value=EQUITY * 0.10)
                   for i in range(6)]

        result = portfolio_engine.allocate(policy, signals, EQUITY,
                                           cash=EQUITY * 0.30)

        total = sum(row["allocated_value"] for row in result["accepted"])

        self.assertLessEqual(total, EQUITY * 0.30 + 1.0)

    def test_a_refused_candidate_is_not_counted_twice(self):
        policy = portfolio_engine.from_config().replace(max_positions=1)

        signals = [
            self._signal("A", "فلزات"),
            self._signal("B", "پتروشیمی"),
        ]

        result = portfolio_engine.allocate(policy, signals, EQUITY)

        symbols = [r["signal"]["symbol"] for r in result["rejected"]]

        self.assertEqual(len(symbols), len(set(symbols)))

    def test_an_existing_book_is_respected(self):
        policy = portfolio_engine.from_config().replace(max_positions=3)

        held = [{"ins_code": "H", "symbol": "H", "sector_name": "فلزات",
                 "position_value": EQUITY * 0.10}]

        result = portfolio_engine.allocate(
            policy, [self._signal("A", "فلزات"),
                     self._signal("B", "پتروشیمی")],
            EQUITY, positions=held)

        self.assertLessEqual(len(result["accepted"]), 2)
        self.assertEqual(result["state"]["positions"],
                         len(result["accepted"]) + 1)

    def test_a_signal_with_no_price_cannot_be_allocated(self):
        result = portfolio_engine.allocate(
            portfolio_engine.from_config(),
            [self._signal("A", "فلزات", price=0.0)], EQUITY)

        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"][0]["reason_key"],
                         portfolio_engine.REFUSE_NO_SIZE)

    def test_no_signals_is_not_an_error(self):
        result = portfolio_engine.allocate(portfolio_engine.from_config(),
                                           [], EQUITY)

        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["rejected"], [])


class ReviewTest(unittest.TestCase):

    def test_a_compliant_book_reports_no_violations(self):
        positions = [
            {"ins_code": "1", "symbol": "A", "sector_name": "فلزات",
             "position_value": EQUITY * 0.10},
            {"ins_code": "2", "symbol": "B", "sector_name": "پتروشیمی",
             "position_value": EQUITY * 0.10},
        ]

        result = portfolio_engine.review(portfolio_engine.from_config(),
                                         positions, EQUITY)

        self.assertTrue(result["compliant"])
        self.assertEqual(result["violations"], [])

    def test_drift_past_a_sector_cap_is_caught_without_any_order(self):
        """A holding doubling in price breaks a limit nobody placed an order to break."""

        positions = [
            {"ins_code": "1", "symbol": "A", "sector_name": "فلزات",
             "position_value": EQUITY * 0.25},
            {"ins_code": "2", "symbol": "B", "sector_name": "فلزات",
             "position_value": EQUITY * 0.20},
        ]

        result = portfolio_engine.review(portfolio_engine.from_config(),
                                         positions, EQUITY)

        self.assertFalse(result["compliant"])

        limits = {v["limit"] for v in result["violations"]}

        self.assertIn("max_sector_pct", limits)
        self.assertIn("max_position_pct", limits)

    def test_a_cash_floor_breach_is_reported(self):
        positions = [
            {"ins_code": str(i), "symbol": "S%d" % i,
             "sector_name": "SEC%d" % i, "position_value": EQUITY * 0.14}
            for i in range(7)
        ]

        result = portfolio_engine.review(portfolio_engine.from_config(),
                                         positions, EQUITY)

        limits = {v["limit"] for v in result["violations"]}

        self.assertIn("min_cash_pct", limits)

    def test_too_many_positions_is_reported(self):
        policy = portfolio_engine.from_config().replace(max_positions=2)

        positions = [
            {"ins_code": str(i), "symbol": "S%d" % i,
             "sector_name": "SEC%d" % i, "position_value": EQUITY * 0.05}
            for i in range(4)
        ]

        result = portfolio_engine.review(policy, positions, EQUITY)

        limits = {v["limit"] for v in result["violations"]}

        self.assertIn("max_positions", limits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
