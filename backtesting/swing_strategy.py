"""The v2 decision engine, standing on a past date. An ADAPTER, not a strategy.

WHAT THIS FILE IS ALLOWED TO DO
-------------------------------
Assemble point-in-time inputs and hand them to the v2 engines. That is all. It
contains no factor, no weight, no threshold, no stop arithmetic and no ranking
rule of its own, and the moment it grows one there are two v2 strategies in this
project and the backtest stops being evidence about the live one.

    regime          regime.assemble() + regime_engine.assess()   <- as of date
    per symbol      swing_advisor.evaluate()                     <- unchanged
    allocation      portfolio_engine.allocate()                  <- unchanged

`swing_advisor.evaluate()` is the same function the panel and the paper trader
call. It reads everything from the frame it is handed, so handing it a frame
that stops at the decision bar is what makes it historical -- with ONE
exception, the fundamental reading, which lives in a store that only knows
"newest". That is injected from backtesting/pit.py, and it is the only injected
input.

WHY IT IS NOT backtesting/strategy.py
-------------------------------------
strategy.py builds a panel of component READINGS and lets the engine blend,
rank and size them. The v2 chain does not decompose that way: its sizing depends
on the book that exists at the moment of the decision, because risk_engine reads
current equity and current sector exposure. A cached panel of finished decisions
would therefore be wrong the first time a position changed the book. So this
object caches the expensive, book-independent half -- the indicator frames --
and re-runs the cheap half on every rebalance against the real book.

WHAT IS SLOWER THAN V1, AND WHY IT HAS TO BE
--------------------------------------------
V1 scores each name once per rebalance date and caches the panel to disk. V2
cannot: the same name on the same date sizes differently depending on what the
book already holds. One full window is minutes rather than seconds. Use
`--limit` while iterating.
"""

import math

import pandas as pd

import config
import data_pipeline
import fundamental_engine
import portfolio_engine
import regime
import regime_engine
import swing_advisor
from indicators import DEFAULT_PARAMS, required_bars


# One constant, owned by the shared builder. Not re-declared here: two names for
# the same span is how they drift apart.
WINDOW_BARS = data_pipeline.WINDOW_BARS


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class SwingStrategy(object):
    """The v2 chain over PointInTimeData. One decision set per date."""

    # Marks this object for engine.py's dispatch. An attribute rather than an
    # isinstance() check so a test double can stand in for it.
    is_v2 = True

    def __init__(self, cfg, data, params=None, verbose=True, limit=None,
                 exposure_floor_pct=None, disable_regime=False):

        self.cfg = cfg
        self.data = data
        self.params = params or DEFAULT_PARAMS
        self.verbose = verbose

        # ---- research knobs, both OFF by default ---------------------------
        #
        # These exist for research_exposure.py and change nothing about what is
        # selected. `exposure_floor_pct` lifts the portfolio's invested target
        # and drops its cash floor, then allocates greedily in rank order
        # instead of spreading the budget -- so the SAME ranked list is allowed
        # to occupy more of the book. `disable_regime` removes the market read's
        # three exposure levers (entry floor, position multiplier, CASH_ONLY
        # veto) while leaving every score untouched, which is the only way to
        # ask whether that read is worth anything beyond braking.
        #
        # Neither touches a factor, a weight, a threshold, a stop or the
        # per-trade risk budget. A run with both off is the shipped system.
        self.exposure_floor_pct = _finite(exposure_floor_pct)
        self.disable_regime = bool(disable_regime)

        self.warmup = max(required_bars(self.params),
                          max(config.MOMENTUM_HORIZONS) + 1)

        instruments = data.instruments()

        codes = sorted(instruments)

        if limit:
            codes = codes[:int(limit)]

        self.instruments = {code: instruments[code] for code in codes}

        self._frames = {}
        self._index_closes = None
        self._turnover = None
        self._medians = None

        self.skipped = 0
        self.scanned = 0

        # Set on the first decision and reported by the engine, so a reader can
        # see the market read the book was built under.
        self.regimes = {}

    # ==============================
    # POINT-IN-TIME INPUTS
    # ==============================

    def _frame_for(self, ins_code, date):
        """Indicator frame ending at `date`. Never a bar after it.

        Built by data_pipeline, the same builder the panel and the paper trader
        use, so the warm-up span is identical. It used to compute the indicators
        once over the whole history and slice the tail, which is cheaper and
        WRONG: ATR, RSI and MACD are recursive, so the same bar carries a
        different value depending on how many bars preceded the computation, and
        two names with identical bars produced different stops in the panel and
        in the backtest.

        `adjusted=True` because pit.bars() has already applied back_adjust --
        the same function data_pipeline would call. Applying it twice would be
        harmless (the gaps are gone, so it finds nothing) but stating it is
        cheaper than relying on that.
        """

        bars = self._bars_for(ins_code)

        if bars is None:
            return None

        return data_pipeline.frame(bars=bars, as_of=date, params=self.params,
                                   adjusted=True)

    def _bars_for(self, ins_code):
        """Point-in-time bars for one symbol, loaded and cached once."""

        bars = self._frames.get(ins_code)

        if bars is None:

            bars = self.data.bars(ins_code)

            if bars is None or len(bars) < self.warmup + 5:
                self._frames[ins_code] = False
                return None

            self._frames[ins_code] = bars

        return None if bars is False else bars

    def _index_series(self, date):
        """TEDPIX closes up to `date`, for the momentum factor's relative read."""

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

    def _fundamental_at(self, ins_code, date, close, sector):
        """The v2 fundamental reading from filings public before this close.

        The same two calls swing_backtest.py makes, for the same reason: the
        multiples are recomputed at THIS date's price rather than read off disk
        with today's price already inside them.
        """

        try:
            history = self.data.fundamental_history(
                ins_code, date,
                periods=int(getattr(self.cfg, "fundamental_trend_periods", 4)
                            or 4))
        except Exception:
            return None

        if not history:
            return None

        try:
            ratios = self.data.market_ratios(history[-1], close, sector=sector)
        except Exception:
            return None

        return fundamental_engine.evaluate(
            ratios=ratios,
            history=[dict(record, ratios=record.get("ratios") or {})
                     for record in history],
        )

    def regime_at(self, date, breadth=None):
        """The market read as of `date`, voted by the same functions live uses.

        `breadth` is (above_slow_ma, counted) collected during this date's own
        pass over the universe -- the participation input, which is an aggregate
        over the symbols and cannot be read from one series.
        """

        cap = regime.index_state("TEDPIX", params=self.params, as_of=date)
        equal = regime.index_state("TEDPIX_EW", params=self.params, as_of=date)

        participation = None

        if breadth and breadth[1]:
            participation = {
                "instruments": breadth[1],
                "above_slow_ma_pct": round(breadth[0] / breadth[1] * 100, 2),
                "above_fast_ma_pct": None,
                "slow_ma": self.params["ma_slow"],
                "fast_ma": self.params["ma_fast"],
            }

        report = regime.assemble(cap, equal, participation, params=self.params)

        return regime_engine.assess(report,
                                    turnover=self._turnover_at(date))

    def _turnover_at(self, date):

        if self._turnover is None:
            try:
                import database
                frame = database.market_turnover()
            except Exception:
                frame = None

            if frame is None or frame.empty:
                self._turnover = ([], pd.DatetimeIndex([]))
            else:
                self._turnover = (frame["turnover"].tolist(),
                                  pd.DatetimeIndex(frame["date"]))

        values, stamps = self._turnover

        if not len(stamps):
            return {"state": None, "reason": "no turnover series"}

        cut = int(stamps.searchsorted(pd.Timestamp(date), side="right"))

        if not cut:
            return {"state": None, "reason": "no turnover before this date"}

        return regime_engine.turnover_state(values[:cut])

    def medians(self):
        """Sector median multiples, from the fundamental store.

        NOT point-in-time, and said out loud rather than hidden: the store keeps
        one median per sector from the newest filings. It moves the valuation
        sub-score of the fundamental factor only, and building a per-date median
        would mean re-deriving every company's multiples on every date.
        """

        if self._medians is None:
            self._medians = swing_advisor.sector_medians(self.instruments)

        return self._medians

    # ==============================
    # ONE DECISION SET
    # ==============================

    def decide(self, date, positions=None, equity=None, cash=None):
        """Everything the v2 chain says on `date`, given the book it is handed.

        Returns the same shape swing_advisor.scan() returns, because it is the
        same chain: {regime, policy, signals, buys, holdings, book}.
        """

        date = pd.Timestamp(date)

        equity = _finite(equity) or config.ACCOUNT_EQUITY

        positions = list(positions or [])

        held_codes = {str(p.get("ins_code")) for p in positions
                      if p.get("ins_code") is not None}

        medians = self.medians()

        index_series = self._index_series(date)

        # ---- pass one: frames, and the breadth the regime vote needs --------
        frames = {}

        above = counted = 0

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

            frames[ins_code] = frame

            level = _finite(last.get("MA_SLOW"))

            if level is not None:
                counted += 1
                if close > level:
                    above += 1

        assessment = self.regime_at(date, breadth=(above, counted))

        self.regimes[date] = assessment

        # The assessment the DECISION sees. With the regime disabled the read is
        # still computed and still reported -- only its three exposure levers
        # are withheld, so the comparison is regime-as-brake against no brake
        # rather than against a different market.
        acting = None if self.disable_regime else assessment

        policy = portfolio_engine.from_config().replace(
            max_positions=self.cfg.max_positions,
            max_per_sector=self.cfg.max_per_sector,
        ).for_regime(acting)

        if self.exposure_floor_pct is not None:
            # Exposure rules only: how much of the book the same ranked list may
            # occupy. Nothing about which names, or how large each one is on its
            # own risk terms.
            policy = policy.replace(
                target_invested_pct=max(policy.target_invested_pct,
                                        self.exposure_floor_pct),
                min_cash_pct=min(policy.min_cash_pct,
                                 max(0.0, 100.0 - self.exposure_floor_pct)),
            )

        # ---- pass two: the v2 chain, per name -------------------------------
        signals = []

        for ins_code, frame in frames.items():

            meta = self.instruments[ins_code]

            close = _finite(frame.iloc[-1].get("close"))

            fundamental = self._fundamental_at(
                ins_code, date, close, meta.get("sector"))

            signal = swing_advisor.evaluate(
                ins_code, meta, frame,
                regime=acting,
                medians=medians,
                equity=equity,
                positions=positions,
                index_series=index_series,
                held=str(ins_code) in held_codes,
                fundamental=fundamental,
            )

            if signal is None:
                continue

            signals.append(signal)

        self.scanned = len(signals)
        self.skipped = len(self.instruments) - len(signals)

        buys = [s for s in signals if s["action"] == swing_advisor.factor_engine.BUY]

        # The same ordering swing_advisor.scan() uses: Swing Score, then
        # liquidity as the tiebreak.
        buys.sort(key=lambda s: ((s.get("swing_score") or 0),
                                 (s.get("readings", {}).get("liquidity") or {})
                                 .get("score") or 0),
                  reverse=True)

        # `spread` divides the budget across the names that can be filled; the
        # greedy alternative lets the highest-ranked take their full risk-sized
        # value until the money runs out. Under an exposure floor the greedy
        # form is the one being asked about -- "own more of the same list" --
        # and it is still the same list in the same order.
        book = portfolio_engine.allocate(policy, buys, equity,
                                         positions=positions, cash=cash,
                                         spread=self.exposure_floor_pct is None)

        return {
            "date": date,
            "regime": assessment,
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
        """Refusals before a number is produced. Same contract as strategy.py."""

        problems = []

        if not self.instruments:
            problems.append(
                "no instruments in the universe; run seed_import.py --universe")

        return problems

    def rejection_summary(self):
        """Kept for the report writer. The v2 chain reports its own reasons."""

        return []
