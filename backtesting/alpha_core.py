"""SIRAT-AI Alpha Core v1 -- a research variant, not a product.

THE HYPOTHESIS, STATED BEFORE THE RUN
-------------------------------------
The factor diagnostic measured, on 120 decision dates the shipped weights never
saw (2008-2024, 72,166 observations):

    Bollinger %B     top-decile alpha +2.30%   t = 7.04
    Momentum         top-decile alpha +1.14%   t = 3.68
    Technical score  top-decile alpha -0.14%   t = -0.46
    Liquidity        top-decile alpha -0.37%   t = -1.11
    shipped blend    top-decile alpha +0.11%   t =  0.31

The question this module exists to answer is narrow: do the two signals that
survived out of sample still produce an edge once they pass through the real
execution model -- T+1, صف, participation caps, commission, tax, the risk
engine's stops, the sizing model and the portfolio caps?

WHAT IS DIFFERENT FROM V2, AND ONLY THIS
----------------------------------------
    ranking      50% cross-sectional rank of momentum
                 50% cross-sectional rank of Bollinger %B
    no gate      the 70 technical gate is not applied
    no regime    no entry floor, no position multiplier, no CASH_ONLY veto
    no blend     no technical, fundamental, liquidity or catalyst in the score

EVERYTHING ELSE IS THE SAME OBJECT, NOT A COPY OF IT
----------------------------------------------------
    data_pipeline.frame()        same frames, same warm-up, same adjustment
    factor_engine.momentum()     unchanged, called as-is
    risk_engine.assess()         unchanged -- stops, sizing, sector capacity
    trade_manager.plan()         unchanged -- entry band, stop, two targets
    portfolio_engine.allocate()  unchanged -- caps, sector spread, cash floor
    backtesting/execution.py     unchanged -- T+1, queues, participation
    backtesting/portfolio.py     unchanged -- costs, marks, exits

Liquidity is therefore still present as an EXECUTION constraint (the ADV
participation cap inside risk_engine and the fill model) and is absent from the
ranking, which is exactly the separation the experiment asks for.

RANK, NOT SCORE. The two inputs are on different scales -- momentum is a 0-100
band, %B is roughly 0-1 and unbounded at the edges -- so averaging their raw
values would silently weight whichever has the larger spread. Percentile ranks
within each decision date put them on one scale and make "50/50" mean what it
says. Ranks are also what the diagnostic measured, so this is the same
construction being tested rather than a new one.

EXITS ARE ROTATION, and that is a choice worth naming. Alpha Core has no SELL
model of its own -- it is a ranking, not a decision engine -- so a held name
leaves when it drops out of the top N, which is the rule v1's _rebalance() has
always used. Stops and targets still fire daily from the engine, unchanged.
"""

import math
import random

import numpy as np
import pandas as pd

import config
import data_pipeline
import factor_engine
import risk_engine
import trade_manager
from indicators import DEFAULT_PARAMS, required_bars

import portfolio_engine


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class ReadingPanel(object):
    """{date: readings} shared across runs that differ only in the ranking.

    Holds what the indicator frames produced -- close, ATR, ADV, volatility,
    momentum, %B -- and nothing a run writes. It exists so the Random-Ranking
    Null Test can afford thirty seeds: the readings cost ~23 minutes to build
    and are identical in every arm, while the ranking that distinguishes the
    arms costs microseconds.

    It is a cache, so it carries the usual hazard: a stale or shared-by-
    reference entry would silently make two arms the same run. Both are guarded
    -- entries are stored stripped of per-run fields and handed out as copies --
    and the control is re-run through the cache and must reproduce its known
    CAGR before any seeded result is read.
    """

    def __init__(self):
        self.by_date = {}
        self.hits = 0
        self.misses = 0

    def get(self, date):
        row = self.by_date.get(pd.Timestamp(date))
        if row is None:
            self.misses += 1
        else:
            self.hits += 1
        return row

    def put(self, date, readings):
        self.by_date[pd.Timestamp(date)] = readings

    def __len__(self):
        return len(self.by_date)


class AlphaCoreStrategy(object):
    """Momentum + Bollinger %B, ranked, through the unchanged v2 plumbing."""

    # Same dispatch as SwingStrategy: engine.py's _rebalance_v2 records what
    # decide() returns and adds nothing of its own.
    is_v2 = True

    name = "alpha_core_v1"

    # Ranking modes. "alpha" is the strategy; "random" is the null model the
    # Random-Ranking Null Test needs and is not a strategy at all.
    ALPHA = "alpha"
    RANDOM = "random"

    def __init__(self, cfg, data, params=None, verbose=True, limit=None,
                 top_n=None, hold_sessions=None, equal_weight=False,
                 ranking=None, seed=None, panel=None):

        self.cfg = cfg
        self.data = data
        self.params = params or DEFAULT_PARAMS
        self.verbose = verbose

        # ---- the isolation experiment's two knobs, both off by default -----
        #
        # `hold_sessions` means the engine closes on age, so this strategy must
        # stop producing the two exits it otherwise produces: the stop that
        # trade_manager attaches to every plan, and the rotation SELL for a name
        # that left the top N. Leaving either in place would make the test
        # "time exit OR stop, whichever comes first", which is not the question.
        #
        # `equal_weight` follows necessarily. risk_engine sizes a position as
        # the risk budget divided by the stop distance; with no stop there is no
        # such quantity, and an equal slice is the honest replacement rather
        # than a number derived from a stop the book will never use.
        self.hold_sessions = int(hold_sessions) if hold_sessions else None
        self.equal_weight = bool(equal_weight) or bool(hold_sessions)

        # ---- the null model, and the cache that makes it affordable --------
        #
        # `ranking="random"` replaces ONLY alpha_scores() -- the one method that
        # turns readings into an order. Eligibility, sizing, exits, caps,
        # execution and costs are the same code on the same inputs, so a
        # difference between a seeded run and the control is the ranking and
        # nothing else. That is the entire point of the test.
        #
        # `panel` is a shared {date: readings} cache. Building the readings is
        # 98,000 indicator frames and ~23 minutes; ranking them is arithmetic.
        # Thirty seeds that each rebuilt the frames would be twelve hours of
        # recomputing identical numbers. The control is re-run through the same
        # cache and must reproduce its known CAGR before any seed is trusted.
        self.ranking = (ranking or self.ALPHA).lower()
        self.seed = seed
        self.panel = panel

        # How many names the ranking proposes. The allocator still decides what
        # fits; this is the depth of the shortlist handed to it.
        self.top_n = int(top_n or cfg.max_positions)

        self.warmup = max(required_bars(self.params),
                          max(config.MOMENTUM_HORIZONS) + 1)

        instruments = data.instruments()

        codes = sorted(instruments)

        if limit:
            codes = codes[:int(limit)]

        self.instruments = {code: instruments[code] for code in codes}

        self._bars = {}
        self._index_closes = None

        self.scanned = 0
        self.skipped = 0

        self.rejections = {}

        # Counted across every rebalance, so engine._cash_analysis() can build
        # the same funnel it builds for v1. Its first stages read these keys.
        self.candidates_seen = 0
        self.candidates_passed = 0

    # ==============================
    # INPUTS -- identical to the v2 path
    # ==============================

    def _bars_for(self, ins_code):

        bars = self._bars.get(ins_code)

        if bars is None:
            bars = self.data.bars(ins_code)
            if bars is None or len(bars) < self.warmup + 5:
                self._bars[ins_code] = False
                return None
            self._bars[ins_code] = bars

        return None if bars is False else bars

    def _frame_for(self, ins_code, date):

        bars = self._bars_for(ins_code)

        if bars is None:
            return None

        return data_pipeline.frame(bars=bars, as_of=date, params=self.params,
                                   adjusted=True)

    def _index_series(self, date):

        if self._index_closes is None:

            import backtest_config

            frame = self.data.index_series(backtest_config.BENCHMARK_TEDPIX)

            if frame is None or frame.empty:
                self._index_closes = ([], pd.DatetimeIndex([]))
            else:
                self._index_closes = (frame["close"].tolist(),
                                      pd.DatetimeIndex(frame["date"]))

        closes, stamps = self._index_closes

        if not len(stamps):
            return None

        cut = int(stamps.searchsorted(pd.Timestamp(date), side="right"))

        return closes[:cut] if cut else None

    # ==============================
    # THE RANKING
    # ==============================

    @staticmethod
    def _percentile(values):
        """Cross-sectional percentile rank, 0-100. NaN stays NaN."""

        series = pd.Series(values, dtype=float)

        ranked = series.rank(pct=True, na_option="keep") * 100.0

        return ranked.tolist()

    def alpha_scores(self, readings, date=None):
        """50% momentum rank + 50% %B rank, over the names scored on this date.

        A name missing either input is dropped rather than scored on the half it
        has: an average of one rank is not the same quantity as an average of
        two, and mixing them would rank a half-measured name against fully
        measured ones.

        Under `ranking="random"` the ELIGIBILITY test is unchanged -- exactly the
        same names are scoreable -- and only the order among them is replaced by
        a seeded permutation. Randomising eligibility too would compare two
        different universes and answer a question nobody asked.
        """

        momentum_rank = self._percentile([r["momentum"] for r in readings])
        bb_rank = self._percentile([r["bb_pct"] for r in readings])

        for reading, m, b in zip(readings, momentum_rank, bb_rank):
            reading["momentum_rank"] = None if pd.isna(m) else round(m, 2)
            reading["bb_rank"] = None if pd.isna(b) else round(b, 2)
            reading["alpha"] = (None if (pd.isna(m) or pd.isna(b))
                                else round(0.5 * m + 0.5 * b, 2))

        if self.ranking != self.RANDOM:
            return readings

        # Deterministic in (seed, date), so a re-run of seed 7 is the same book
        # it was last time and the whole distribution is reproducible.
        rng = random.Random("sirat|%s|%s" % (self.seed, pd.Timestamp(date)))

        eligible = [r for r in readings if r["alpha"] is not None]

        draw = list(range(len(eligible)))

        rng.shuffle(draw)

        for reading, position in zip(eligible, draw):
            # Same 0-100 scale as the real score, so nothing downstream can
            # behave differently because the numbers look different.
            reading["alpha"] = round(
                100.0 * (position + 1) / max(1, len(eligible)), 2)

        return readings

    # ==============================
    # ONE DECISION SET
    # ==============================

    def decide(self, date, positions=None, equity=None, cash=None):
        """The same return shape SwingStrategy.decide() produces."""

        date = pd.Timestamp(date)

        equity = _finite(equity) or config.ACCOUNT_EQUITY

        positions = list(positions or [])

        held = {str(p.get("ins_code")) for p in positions
                if p.get("ins_code") is not None}

        cached = None if self.panel is None else self.panel.get(date)

        if cached is not None:
            # A fresh copy per call: decide() writes alpha, the ranks and `held`
            # into each reading, and a cache handed out by reference would leak
            # one run's book into the next one's inputs.
            readings = [dict(row) for row in cached]

            for reading in readings:
                reading["held"] = str(reading["ins_code"]) in held

            self.scanned = len(readings)
            self.skipped = len(self.instruments) - len(readings)

            return self._from_readings(readings, date, positions, equity, cash,
                                       held)

        index_series = self._index_series(date)

        readings = []

        for ins_code, meta in self.instruments.items():

            frame = self._frame_for(ins_code, date)

            if frame is None or frame.empty:
                continue

            last = frame.iloc[-1]

            close = _finite(last.get("close"))

            if not close or close <= 0:
                continue

            stale = self.data.stale_sessions(ins_code, date)

            if stale is not None and stale > self.cfg.max_stale_sessions:
                continue

            closes = frame["close"].tolist()

            momentum = factor_engine.momentum(closes,
                                              index_closes=index_series)

            volatility = None

            if len(closes) > 5:
                deviation = frame["close"].tail(61).pct_change().std()
                volatility = _finite(deviation * math.sqrt(
                    config.TRADING_DAYS_PER_YEAR) * 100)

            adv = None

            if "value" in frame.columns:
                adv = _finite(frame["value"].tail(20).mean())

            readings.append({
                "ins_code": ins_code,
                "symbol": meta.get("symbol"),
                "name": meta.get("symbol"),
                "sector": meta.get("sector") or "?",
                "close": close,
                "atr": _finite(last.get("ATR")),
                "atr_pct": _finite(last.get("ATR_PCT")),
                "adv": adv,
                "volatility_60": volatility,
                "momentum": momentum.get("score"),
                "bb_pct": _finite(last.get("BB_PCT")),
                "held": str(ins_code) in held,
            })

        self.scanned = len(readings)
        self.skipped = len(self.instruments) - len(readings)

        if self.panel is not None:
            # Store WITHOUT the per-run fields, so the cache holds only what the
            # frames produced and never a previous run's ranking or holdings.
            self.panel.put(date, [{k: v for k, v in row.items()
                                   if k not in ("held", "alpha",
                                                "momentum_rank", "bb_rank")}
                                  for row in readings])

        return self._from_readings(readings, date, positions, equity, cash,
                                   held)

    def policy_for(self, date):
        """The portfolio policy for one decision date.

        A seam, not a feature. The base class returns exactly the policy it
        always built -- config's, with this run's basket size and sector cap --
        so nothing measured before this method existed can have moved. It is
        separated only so a subclass can impose a TARGET EXPOSURE above the
        selection without reaching into the ranking, which is what the V3
        architecture requires and what Experiment 3 needs to test.
        """

        return portfolio_engine.from_config().replace(
            max_positions=self.cfg.max_positions,
            max_per_sector=self.cfg.max_per_sector,
        )

    def _from_readings(self, readings, date, positions, equity, cash, held):
        """Rank, plan and allocate. Identical whether the readings were built
        on this call or replayed from the cache -- which is what makes the
        cached control's reproduction of its known CAGR a real check."""

        readings = self.alpha_scores(readings, date=date)

        scoreable = [r for r in readings if r["alpha"] is not None]

        scoreable.sort(key=lambda r: -r["alpha"])

        wanted = scoreable[:self.top_n]

        wanted_codes = {r["ins_code"] for r in wanted}

        self.candidates_seen += len(scoreable)

        # ---- signals -------------------------------------------------------
        #
        # SELL for a held name that has left the top N. Nothing else produces a
        # SELL: stops and targets are the engine's, checked every session.
        signals = []

        for reading in readings:

            if self.hold_sessions:
                # The engine owns every exit in this mode. Emitting a rotation
                # SELL here would close positions before their term.
                continue

            if reading["held"] and reading["ins_code"] not in wanted_codes:
                signals.append({
                    "ins_code": reading["ins_code"],
                    "symbol": reading["symbol"],
                    "action": "SELL",
                    "held": True,
                    "swing_score": reading["alpha"],
                    "reasons": ["rotated out of the top %d" % self.top_n],
                })

        # ---- entries: risk engine and trade manager, unchanged -------------
        buys = []

        for reading in wanted:

            if reading["held"]:
                continue

            risk = risk_engine.assess(
                reading, equity=equity, positions=positions, regime=None)

            decision = {
                "action": factor_engine.BUY,
                "label": factor_engine.ACTION_LABELS[factor_engine.BUY],
                "score": int(round(reading["alpha"])),
                # Conviction is where in the shortlist the name sits, on 0-1.
                # trade_manager only reports it; nothing sizes on it here.
                "conviction": round(reading["alpha"] / 100.0, 3),
                "reasons": ["alpha %.0f (momentum rank %.0f, %%B rank %.0f)"
                            % (reading["alpha"], reading["momentum_rank"],
                               reading["bb_rank"])],
            }

            signal = trade_manager.plan(reading, decision, risk,
                                        overhead_pct=None, held=False)

            signal["alpha"] = reading["alpha"]
            signal["momentum_rank"] = reading["momentum_rank"]
            signal["bb_rank"] = reading["bb_rank"]
            signal["position_value"] = signal.get("position_value") or 0.0

            if self.equal_weight:
                # One slice of the book per name. The allocator still applies
                # every cap on top of this; it is a request, not a grant.
                slice_value = equity / float(self.cfg.max_positions or 1)
                price = _finite(signal.get("entry_price")) or reading["close"]
                signal["position_value"] = slice_value
                signal["shares"] = int(slice_value // price) if price else 0

            if self.hold_sessions:
                # No stop and no target reach the position, so nothing but the
                # engine's clock can close it. Reported as None rather than
                # silently left at the value trade_manager computed, because a
                # stop that exists in the report and not in the book is the
                # worst of both.
                signal["stop_loss"] = None
                signal["target_1"] = None
                signal["target_2"] = None

            if signal.get("action") != factor_engine.BUY:
                # trade_manager downgrades a plan it cannot build -- no stop, no
                # size, reward/risk under the floor. Those are the risk engine's
                # refusals and they are counted, not overridden.
                key = (signal.get("reasons") or ["refused"])[0]
                self.rejections[key] = self.rejections.get(key, 0) + 1
                continue

            self.candidates_passed += 1

            buys.append(signal)
            signals.append(signal)

        # Already in rank order; the tiebreak is the alpha itself.
        buys.sort(key=lambda s: -(s.get("alpha") or 0))

        policy = self.policy_for(date)

        book = portfolio_engine.allocate(policy, buys, equity,
                                         positions=positions, cash=cash)

        return {
            "date": date,
            "regime": None,
            "policy": policy.as_dict(),
            "equity": equity,
            "signals": signals,
            "buys": buys,
            "holdings": [s for s in signals if s.get("held")],
            "book": book,
            "scanned": self.scanned,
            "skipped": self.skipped,
        }

    # ==============================
    # WHAT THE ENGINE ASKS OF A STRATEGY
    # ==============================

    def check_data(self, dates, panel=None):
        """Refusals before a number is produced."""

        problems = []

        if not self.instruments:
            problems.append("no instruments in the universe")

        return problems

    def rejection_summary(self):
        """The shape engine._cash_analysis() reads -- the same one v1 returns."""

        total = sum(self.rejections.values())

        rows = [
            {"reason": reason or "unknown", "count": count,
             "share": round(count / float(total), 4) if total else 0.0}
            for reason, count in sorted(self.rejections.items(),
                                        key=lambda kv: -kv[1])
        ]

        return {
            "candidates_seen": self.candidates_seen,
            "candidates_passed": self.candidates_passed,
            "pass_rate": (round(self.candidates_passed
                                / float(self.candidates_seen), 4)
                          if self.candidates_seen else None),
            "rejected": total,
            "by_reason": rows,
        }
