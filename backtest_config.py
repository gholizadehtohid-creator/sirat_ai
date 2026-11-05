"""Every parameter the backtesting engine reads. Nothing is hard-coded inside it.

WHY A SEPARATE FILE AND NOT config.py
-------------------------------------
config.py describes the LIVE system: what the scanner believes, how the basket
is built, what a good company looks like. This file describes the MEASUREMENT
of that system: what a trade costs, how an order fills, what counts as a pass.
Mixing them would let a backtest quietly change the live scanner, and -- worse
-- would let someone tune a live threshold to make a backtest pass.

Where a value already exists in config.py it is imported rather than copied.
A cost that differs between the live sizing and the backtest is a bug that
produces no error, and two files each holding "1.04" is how it starts.

WHY NOT A config/ PACKAGE DIRECTORY
-----------------------------------
`config.py` is a top-level module in this project. A `config/` package beside
it shadows the module -- every `import config` in twenty-odd files would start
resolving to the package instead, and the failure is an AttributeError far from
its cause. Flat module, same as every other file here.


THE NUMBERS AND WHERE THEY COME FROM
------------------------------------
Read this before trusting a result. Each cost below is labelled with how it was
obtained, because they are not equally solid.

  DOCUMENTED   the figure this project has used and stated since its first
               commit (config.TRANSACTION_COST_PCT = 1.04% round trip,
               documented there as ~0.464% buy + ~0.575% sell for Tehran
               retail equity). The engine reproduces exactly that total.
  STATUTORY    fixed by law, not by a broker. The 0.5% sale tax on listed
               shares (ماده ۱۴۳ قانون مالیات‌های مستقیم) is the only one here.
  INFERRED     the split of a documented total into its parts. The total is
               solid; how it divides is an assumption, stated as one.
  UNMEASURED   no data in this project can produce it. Slippage is the whole
               of this category. It defaults to zero, which is NOT a claim
               that slippage is zero -- it is a refusal to invent a number,
               and the sensitivity sweep prices what a non-zero value costs.

Verify BUY_COMMISSION_PCT and SELL_COMMISSION_PCT against your own broker's
statement before acting on any result. They are the two numbers that change
every figure in the report, and brokerage rates have moved more than once.
"""

import json
import os

import config


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Everything the engine writes lands here: curves, trades, reports, and the
# score-panel cache. One directory so a run can be deleted in one action.
OUTPUT_DIR = os.path.join(BASE_DIR, "backtest_output")

CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")


# ==============================
# TRANSACTION COSTS
# ==============================

# Buy-side total: brokerage, exchange, SEO, CSD and technology fees.
# DOCUMENTED -- config.py states ~0.464% for the buy side.
BUY_COMMISSION_PCT = 0.464

# Sell-side fees EXCLUDING tax. INFERRED: config.py documents 0.575% for the
# whole sell side, of which the statutory 0.5% is tax, leaving this residual.
SELL_COMMISSION_PCT = 0.075

# STATUTORY. Sale tax on listed shares, ماده ۱۴۳ قانون مالیات‌های مستقیم.
# Set to 0.0 to model an instrument or account it does not apply to.
SELL_TAX_PCT = 0.5

# Sanity anchor, not a parameter. If the three figures above stop summing to
# config.TRANSACTION_COST_PCT the engine says so at startup rather than
# silently reporting a cheaper strategy than every other module assumes.
EXPECTED_ROUND_TRIP_PCT = config.TRANSACTION_COST_PCT

# UNMEASURED. Price concession per side, in percent, on top of the fill price.
#
# This project stores daily OHLCV. Measuring slippage needs the order book or
# intraday prints, and neither is collected, so any default other than zero
# would be a number with no evidence behind it. The Tehran-specific friction
# that CAN be measured -- being locked out by a queue, and being too large for
# the day's turnover -- is modelled explicitly below instead.
#
# validation.sensitivity() sweeps this so the cost of being wrong about it is
# visible even though the value is not known.
SLIPPAGE_PCT = 0.0


# ==============================
# EXECUTION MODEL
# ==============================

# Sessions between a decision and its fill. 1 means: decide on session T's
# close, fill at session T+1's open.
#
# This is not conservatism for its own sake. Client-type (حقیقی/حقوقی) figures
# for a session are published AFTER it closes, and the live selection ranks on
# them. Filling at T's close would spend a number that did not exist yet, and
# in a flow-ranked strategy that single day is worth tens of percentage points
# of invented return -- it has already happened once in this project, see
# tests/test_backtest_portfolio.py.
EXECUTION_DELAY_SESSIONS = 1

# Fill at the next session's open, or at its close.
#   "open"   -- the first tradeable price after the decision. Default.
#   "close"  -- later, and usually kinder to the strategy. Available for
#               comparison, not recommended.
EXECUTION_PRICE = "open"

# Sessions an unfilled order stays alive before it is abandoned. An order
# blocked by a buy queue on day one is often fillable on day three, and
# cancelling instantly models a trader who never tries twice.
ORDER_GOOD_FOR_SESSIONS = 3


# ==============================
# LIQUIDITY, QUEUES AND PARTIAL FILLS  (APPROXIMATION -- see report)
# ==============================

# Whether to model queues at all. Off makes every result more optimistic and
# is provided only to measure how much the queue model is worth.
MODEL_QUEUES = True

# A session is treated as locked in a queue when its high equals its low (no
# range at all) AND the move from the previous close reaches this fraction of
# the daily band. صف خرید locks at the ceiling, صف فروش at the floor.
#
# APPROXIMATION. The exact test would read the order book, which is not
# stored. This one is derived from data that is: a session that traded in a
# single price at the band limit was queued, with very few exceptions.
QUEUE_BAND_FRACTION = 0.9

# Volume below this fraction of the symbol's 20-session median also reads as a
# locked session: a queue day usually prints a token volume rather than zero.
QUEUE_VOLUME_FRACTION = 0.25

# Can a buy be filled at all on a locked-up session? Modelling it as fillable
# is how a backtest buys the entire limit-up move for free.
FILL_INTO_BUY_QUEUE = False
FILL_OUT_OF_SELL_QUEUE = False

# Share of a session's own traded VALUE one order may take. The position is
# cut to whatever this allows and the remainder retries next session, which is
# what a real order does.
#
# 5% matches config.MAX_ADV_PARTICIPATION_PCT, the live portfolio's cap on
# position size against average daily value. Using a different number here
# would test a position size the live system would never build.
MAX_PARTICIPATION_PCT = config.MAX_ADV_PARTICIPATION_PCT

# Allow an order to fill for less than it asked for. Off means all-or-nothing,
# which understates how often capital actually gets deployed.
ALLOW_PARTIAL_FILL = True

# An order whose filled fraction never reaches this is treated as not worth
# keeping and the remainder is cancelled: a 3% fill of a position is a
# commission bill, not a position.
MIN_FILL_FRACTION = 0.10


# ==============================
# PORTFOLIO
# ==============================

INITIAL_CAPITAL = config.ACCOUNT_EQUITY

MAX_POSITIONS = config.PORTFOLIO_SIZE

MAX_POSITION_PCT = config.MAX_POSITION_PCT

MIN_POSITION_PCT = config.MIN_POSITION_PCT

MAX_PER_SECTOR = config.MAX_PER_SECTOR

MAX_SECTOR_PCT = config.MAX_SECTOR_PCT

# Cash never falls below this share of the book. The market regime can raise
# it (see USE_REGIME_CASH) but nothing lowers it.
CASH_RESERVE_PCT = config.MIN_CASH_PCT

# Does idle cash earn the policy rate?
#
# It does in reality -- a rial sitting in a broker account in this economy earns
# roughly RISK_FREE_RATE -- and it did not in this engine, while metrics.py
# charged the same 23% as a Sharpe hurdle. A book that is 71% in cash was
# therefore penalised twice, which is why every Sharpe Experiment 3 produced is
# uninterpretable as a LEVEL (its treatment-vs-control RANKING is unaffected,
# because both arms were penalised identically).
#
# DEFAULT FALSE, DELIBERATELY. Turning it on changes every portfolio-level
# number this project has ever produced. Left off, v2.0 results stay
# reproducible bit for bit; the v2.1 experiments set it explicitly and are
# reported as a separate version rather than as a correction to the old ones.
CREDIT_IDLE_CASH = False

# ...except for the v2 book, from 2026-11-05 (E26). It averaged 80-85% cash
# over 2024-09..2026-11, and an engine that pays nothing on that measures a
# book nobody would hold: the panel now tells the user to park unallocated
# money in a fixed-income fund. Applied by engine.run() for engine="v2" only,
# so every published v1 and research result above stays reproducible.
V2_CREDIT_IDLE_CASH = True

CASH_ANNUAL_RATE = config.RISK_FREE_RATE

RISK_PER_TRADE_PCT = config.RISK_PER_TRADE_PCT

# How a position's size is decided. Four methods, run as separate backtests and
# compared rather than blended:
#
#   "risk_based"   the live system's own rule: the risk budget divided by the
#                  stop distance, so a stop-out costs RISK_PER_TRADE_PCT
#                  whatever the name's volatility. The default, because
#                  changing it would measure a strategy nobody runs.
#   "equal_weight" equity / MAX_POSITIONS. The null hypothesis of sizing: if
#                  nothing beats it, the sizing model is decoration.
#   "fixed_fractional"  a fixed share of equity per position
#                  (MAX_POSITION_PCT), which is what most retail sizing
#                  actually is.
#   "volatility_adjusted"  equal RISK rather than equal money -- the target
#                  weight scaled by the median ATR% over the candidates, so a
#                  name that moves twice as much gets half the money. Differs
#                  from risk_based in not needing a stop to exist.
SIZING_MODE = "risk_based"

SIZING_MODES = ("risk_based", "equal_weight", "fixed_fractional",
                "volatility_adjusted")

# Target volatility for "volatility_adjusted", in percent of position value per
# day. 2.0 is close to the median ATR% of this universe, so the mode neither
# systematically inflates nor deflates the book against the other three -- the
# comparison is then about the SHAPE of the sizing rather than about who was
# handed more money.
SIZING_TARGET_ATR_PCT = 2.0

# Stop distance in ATRs. 0 disables the stop entirely.
STOP_ATR_MULTIPLE = config.STOP_ATR_MULTIPLE

# Take-profit distance in ATRs. 0 disables it.
#
# config.TARGET_ATR_MULTIPLE is 3.0 and the live basket does set a target, but
# the measurement recorded in config.py found a fixed target cost more than it
# saved (+1.55% a trade against +5.92% for letting winners run). Both are
# reachable here: the sensitivity sweep runs it at 0, 2, 3 and 4.
TAKE_PROFIT_ATR_MULTIPLE = config.TARGET_ATR_MULTIPLE

# Trailing stop distance in ATRs, from the highest close since entry. 0 off.
TRAIL_ATR_MULTIPLE = config.TRAIL_ATR_MULTIPLE

# When a bar's low pierces the stop AND its high reaches the target, daily OHLC
# cannot say which came first. Inherited from config so the backtester and the
# live exit logic resolve the ambiguity the same way.
AMBIGUOUS_BAR_RESOLVES_TO = config.AMBIGUOUS_BAR_RESOLVES_TO

# Let regime.detect(as_of=) drive the cash floor instead of the fixed reserve.
USE_REGIME_CASH = False


# ==============================
# SCHEDULE AND UNIVERSE
# ==============================

# Default backtest window when the command line gives none.
#
# The start is not a round number and not a preference. Client-type (حقیقی/
# حقوقی) coverage reaches the whole market in 2024-09; before that month the
# table holds a few dozen instruments, and a flow-ranked strategy measured
# there would describe those names rather than the market. The end defaults to
# the newest session in the database.
DEFAULT_START = "2024-09-01"
DEFAULT_END = None

# Sessions between rebalances. 20 is one trading month and is also the horizon
# every signal measurement in this project was made over.
REBALANCE_SESSIONS = 20

# The v2 book is refilled weekly (E26). Its stops free capital between
# monthly rebalances that then sat idle; the live panel can be re-run any day,
# so a monthly refill also understated how it is used. v1 keeps 20.
V2_REBALANCE_SESSIONS = 5

# Stops and targets are checked EVERY session regardless of the rebalance
# schedule. A risk rule that only fires on rebalance day is not a risk rule.
CHECK_EXITS_DAILY = True

# Instruments considered. None = every equity in the database.
UNIVERSE_LIMIT = None

# Bars a symbol must have before the rebalance date to be scoreable at all.
# Below this the indicators have not warmed up and the score is noise.
MIN_HISTORY_BARS = 120

# Sessions a symbol may have gone without trading before it is excluded as
# untradeable on that date.
MAX_STALE_SESSIONS = 15


# ==============================
# POINT-IN-TIME DATA RULES
# ==============================

# Tehran Stock Exchange closes at 12:30. A filing published at 14:00 was not
# available to a decision made from that session's closing data.
MARKET_CLOSE_HOUR = 12
MARKET_CLOSE_MINUTE = 30

# Extra sessions to wait after a filing becomes public before the model may
# act on it. 0 uses the timestamp alone.
#
# Raise this to 1 to model a system that reads filings overnight. It only ever
# makes results worse, which is the direction an assumption should fail in.
FUNDAMENTAL_LAG_SESSIONS = 0

# What to do with a filing whose publication timestamp cannot be recovered.
#
#   "exclude"    never usable. The strictest reading, and the default.
#   "next_day"   usable from the session after its period end -- which is
#                wrong by weeks and flatters the strategy. Provided only so
#                the cost of the assumption can be measured against "exclude".
#
# There is no third option in which an undated filing is used as if dated:
# that is the look-ahead this whole module exists to prevent.
UNDATED_FILING_POLICY = "exclude"

# Minimum share of the universe that must have a point-in-time fundamental
# reading before a fundamental-dependent strategy variant is allowed to report
# a result at all. Below it the variant returns INSUFFICIENT_DATA.
#
# 0.30 is a judgement, and the reasoning is stated so it can be argued with: a
# fundamental ranking computed over less than a third of the candidates is not
# ranking the market, it is ranking whichever names happened to be downloaded,
# and that subset is not random -- it is the largest and most-followed names.
MIN_FUNDAMENTAL_COVERAGE = 0.30

# Same idea for news. There is currently no news table in this database at
# all, so every news-dependent variant reports INSUFFICIENT_DATA. The
# threshold exists for when that changes.
MIN_NEWS_COVERAGE = 0.30

# Which news provider to ask. See news.py for the registry and for what a
# provider has to supply; "database" reads a `news_items` table that nothing in
# this project creates, so it reports unavailable until an archive is imported.
NEWS_PROVIDER = "database"

# Sessions of news that feed one reading.
NEWS_WINDOW_SESSIONS = 5

# Reporting periods handed to the fundamental trend pillar. Four is one year of
# quarterly filings, which is the shortest series in which a seasonal business
# is not read as a collapsing one.
FUNDAMENTAL_TREND_PERIODS = 4

# Weight of the trend pillar, in the same units as config.FUND_WEIGHT_*. Set
# equal to the cash-flow pillar and NOT chosen by trying values and keeping the
# best -- that would be fitting the scorer to the answer. The sensitivity sweep
# moves it so its influence is measured.
FUNDAMENTAL_TREND_WEIGHT = config.FUND_WEIGHT_CASHFLOW


# ==============================
# BENCHMARKS
# ==============================

# شاخص کل. The index the strategy has to beat to have earned anything.
BENCHMARK_TEDPIX = "32097828799138957"

# شاخص هم‌وزن. A better comparison for a ten-name basket than the cap-weighted
# index, because the basket is closer to equal-weight than to cap-weight.
BENCHMARK_EQUAL_WEIGHT = "67130298613737946"

# Buy and hold a fixed basket of the most liquid names, rebalanced never.
# Built at run time from the top N by traded value on the start date, so it is
# a market basket rather than a hand-picked one.
BENCHMARK_BASKET_SIZE = 10

# A bank deposit, compounded daily over the same window. The alternative that
# actually competes for this money: at a 23% policy rate, doing nothing is a
# benchmark with no drawdown and no effort, and a strategy that loses to it has
# not underperformed a market -- it has underperformed a savings account.
BENCHMARK_RISK_FREE = True

# Sensitivity scenarios run and reported as whole backtests rather than as one
# line of a sweep, because slippage is the assumption most likely to be wrong
# and the one whose cost compounds with turnover.
SLIPPAGE_SCENARIOS = [0.0, 0.25, 0.50]


# ==============================
# WALK-FORWARD AND OUT-OF-SAMPLE
# ==============================

# One window = TRAIN months to fit on, VALIDATE months to check on, then the
# window slides forward by STEP months.
#
# These are short, and the reason is the data rather than a view about how much
# training a threshold needs. Client-type coverage reaches the whole market in
# 2024-09, so the usable window is about 23 months. At 12/6/6 exactly ONE fold
# fits inside it, and one fold is not a walk-forward -- it is a single split
# with extra vocabulary. At 9/3/3 five folds fit, which is enough for the
# parameter-stability figure to mean something.
#
# Raise them once several more years of client-type history are backfilled.
WALK_FORWARD_TRAIN_MONTHS = 9
WALK_FORWARD_VALIDATE_MONTHS = 3
WALK_FORWARD_STEP_MONTHS = 3

# Everything after this date is out of sample and must never be used to choose
# a parameter. It is a date rather than a fraction so it cannot drift when the
# database gains new bars -- a fraction would silently move the boundary every
# time data is fetched, and the point of an out-of-sample set is that it does
# not move.
OUT_OF_SAMPLE_START = "2026-01-01"


# ==============================
# MONTE CARLO
# ==============================

MONTE_CARLO_RUNS = 2000

# Resample trades with replacement (bootstrap) or only reorder them (shuffle).
#
# "shuffle" keeps the exact set of trades and asks only whether their ORDER
# mattered. "bootstrap" also asks whether the SET was lucky, which is the
# larger question and the harsher answer.
MONTE_CARLO_METHOD = "bootstrap"

MONTE_CARLO_PERCENTILES = [5, 25, 50, 75, 95]

MONTE_CARLO_SEED = 20260813


# ==============================
# SENSITIVITY SWEEP
# ==============================

# Parameter -> values to try. Each is run as a full backtest with everything
# else held fixed. Keep these lists short: the cost is one backtest per value.
SENSITIVITY_GRID = {
    "technical_gate": [60, 65, 70, 75, 80],
    "stop_atr_multiple": [1.5, 2.0, 2.5, 3.0],
    "take_profit_atr_multiple": [0.0, 2.0, 3.0, 4.0],
    "max_positions": [5, 8, 10, 15],
    "risk_per_trade_pct": [0.5, 1.0, 1.5, 2.0],
    "min_headroom_pct": [0.0, 5.0, 10.0, 15.0],
    "slippage_pct": [0.0, 0.25, 0.5],
}

# A strategy whose result collapses when a parameter moves one step is fitted
# to that step. Measured as the coefficient of variation of the metric across
# a parameter's values; above this the parameter is flagged as fragile.
SENSITIVITY_FRAGILE_CV = 0.60

# Metric the sweep is judged on. Total return rewards taking more risk;
# Calmar does not, which is why it is the default.
SENSITIVITY_METRIC = "calmar"


# ==============================
# OVERFITTING RISK
# ==============================

# Each test contributes points; the total maps to LOW / MEDIUM / HIGH. The
# weights are here so the verdict can be argued with instead of trusted.
#
# Nothing in this section is calibrated against an outside standard, because
# no such standard exists. What it is: a written-down version of the five
# questions a careful person asks before believing a backtest, with the
# thresholds visible instead of implied.
OVERFIT_WEIGHTS = {
    "oos_degradation": 30,      # out-of-sample far below in-sample
    "parameter_fragility": 25,  # result depends on an exact parameter value
    "trade_count": 20,          # too few trades to mean anything
    "period_concentration": 15, # one window carries the whole result
    "regime_failure": 10,       # works only in a rising market
}

# In-sample CAGR minus out-of-sample CAGR, as a fraction of in-sample. Above
# this the strategy did not generalise.
OVERFIT_OOS_DROP = 0.50

# Trades below this cannot support a conclusion whatever the numbers say. At
# 30 trades a 60% win rate has a 95% interval roughly 41%-77% -- wide enough
# to contain both "edge" and "coin".
OVERFIT_MIN_TRADES = 30

# Share of total profit one window may contribute before the result is called
# concentrated rather than repeatable.
OVERFIT_MAX_WINDOW_SHARE = 0.60

OVERFIT_BANDS = [(60, "HIGH"), (30, "MEDIUM"), (0, "LOW")]

# Share of the weights above that must actually have been evaluated before a
# band is claimed at all. Below it the verdict is UNKNOWN.
#
# Without this, a run with no hold-out and no sweep scores zero points out of
# the two tests that could run and reports "OVERFITTING RISK: LOW" -- which
# reads as evidence of robustness when it is the absence of evidence. Half is
# the point where at least the out-of-sample test or the sweep has run, and
# those are the two that carry most of the weight.
OVERFIT_MIN_WEIGHT_MEASURED = 0.5


# ==============================
# PASS / FAIL
# ==============================

# The bar the strategy has to clear before anyone risks money on it.
#
# THE HURDLE IS NOT ZERO. Iran's policy rate is 23% (config.RISK_FREE_RATE), so
# a strategy returning 25% a year has not made 25% -- it has made 2% for
# carrying market risk, drawdowns and effort that a bank deposit does not. Every
# threshold below is set against that reality rather than against the
# near-zero-rate assumptions almost every published trading rule of thumb
# carries.
PASS_CRITERIA = {
    # CAGR must beat cash by a real margin, not by rounding. 5 points over the
    # risk-free rate is the smallest gap that survives being wrong about costs.
    "min_cagr_over_risk_free_pct": 5.0,

    # Gross profit / gross loss. Below 1.2 the edge disappears inside a
    # commission change.
    "min_profit_factor": 1.2,

    # Sharpe here is already excess of the 23% hurdle (performance.py subtracts
    # it before dividing), so 0.5 means half a unit of volatility earned per
    # unit taken, above cash.
    "min_sharpe": 0.5,

    # A drawdown past this is not survivable in practice: the account stops
    # being traded before it recovers, whatever the equity curve says.
    "max_drawdown_pct": 35.0,

    # Out-of-sample must not collapse. Expressed as a share of in-sample CAGR
    # retained.
    "min_oos_cagr_retained": 0.50,

    # Beating cash while losing to the index means the index was the better
    # trade. Excess is in points of total return over the window.
    "min_excess_over_benchmark_pct": 0.0,

    # Below this the statistics are not statistics.
    "min_trades": 30,

    # LOW or MEDIUM. HIGH fails regardless of every number above it.
    "max_overfitting_risk": "MEDIUM",
}


# ==============================
# OUTPUT
# ==============================

WRITE_CSV = True
WRITE_JSON = True
WRITE_HTML = True

# Charts need matplotlib, which is NOT in requirements.txt. When it is absent
# the HTML report is still written -- with inline SVG drawn from the curve
# itself, no dependency -- and the PNG files are skipped with a note.
WRITE_CHARTS = True

CHART_DPI = 110


# ==============================
# PAPER TRADING
# ==============================

PAPER_INITIAL_CAPITAL = config.ACCOUNT_EQUITY

# Paper trading uses the same execution model as the backtest, so a signal
# raised today fills at tomorrow's open and is not marked until it does.
PAPER_EXECUTION_DELAY_SESSIONS = EXECUTION_DELAY_SESSIONS


# ==============================
# THE RUNTIME OBJECT
# ==============================

# Fields a run may override. Anything not listed here cannot be swept, which
# is deliberate: a sweep over an unlisted field would silently do nothing.
_OVERRIDABLE = (
    "buy_commission_pct", "sell_commission_pct", "sell_tax_pct",
    "slippage_pct", "execution_delay_sessions", "execution_price",
    "order_good_for_sessions", "model_queues", "queue_band_fraction",
    "queue_volume_fraction", "fill_into_buy_queue", "fill_out_of_sell_queue",
    "max_participation_pct", "allow_partial_fill", "min_fill_fraction",
    "initial_capital", "max_positions", "max_position_pct",
    "min_position_pct", "max_per_sector", "max_sector_pct",
    "cash_reserve_pct", "credit_idle_cash", "cash_annual_rate",
    "risk_per_trade_pct", "stop_atr_multiple",
    "take_profit_atr_multiple", "trail_atr_multiple", "use_regime_cash",
    "rebalance_sessions", "v2_rebalance_sessions", "v2_credit_idle_cash",
    "universe_limit",
    "min_history_bars",
    # check_exits_daily was a field that could be read but never overridden, so
    # a run could not turn the stop path off without editing the module. Added
    # for Experiment 3, which has to hold the exit rule constant while it
    # varies execution -- the default is unchanged, so no existing result moves.
    "check_exits_daily",
    "max_stale_sessions", "fundamental_lag_sessions",
    "undated_filing_policy", "min_fundamental_coverage",
    "technical_gate", "min_headroom_pct", "selection_mode",
    "ambiguous_bar_resolves_to",
    # V2
    "sizing_mode", "sizing_target_atr_pct", "news_provider",
    "news_window_sessions", "min_news_coverage",
    "fundamental_trend_periods", "fundamental_trend_weight",
    "restrict_to_fundamental_universe",
)


class BacktestConfig(object):
    """A frozen snapshot of the settings, with per-run overrides.

    Why an object rather than reading the module directly: the sensitivity
    sweep runs the same engine twenty times with one value changed each time.
    Mutating module globals to do that leaks state between runs, and a leak
    that only shows up as a slightly different number in run seventeen is not
    a bug anyone finds.
    """

    def __init__(self, **overrides):

        self.buy_commission_pct = BUY_COMMISSION_PCT
        self.sell_commission_pct = SELL_COMMISSION_PCT
        self.sell_tax_pct = SELL_TAX_PCT
        self.slippage_pct = SLIPPAGE_PCT

        self.execution_delay_sessions = EXECUTION_DELAY_SESSIONS
        self.execution_price = EXECUTION_PRICE
        self.order_good_for_sessions = ORDER_GOOD_FOR_SESSIONS

        self.model_queues = MODEL_QUEUES
        self.queue_band_fraction = QUEUE_BAND_FRACTION
        self.queue_volume_fraction = QUEUE_VOLUME_FRACTION
        self.fill_into_buy_queue = FILL_INTO_BUY_QUEUE
        self.fill_out_of_sell_queue = FILL_OUT_OF_SELL_QUEUE
        self.max_participation_pct = MAX_PARTICIPATION_PCT
        self.allow_partial_fill = ALLOW_PARTIAL_FILL
        self.min_fill_fraction = MIN_FILL_FRACTION

        self.initial_capital = INITIAL_CAPITAL
        self.max_positions = MAX_POSITIONS
        self.max_position_pct = MAX_POSITION_PCT
        self.min_position_pct = MIN_POSITION_PCT
        self.max_per_sector = MAX_PER_SECTOR
        self.max_sector_pct = MAX_SECTOR_PCT
        self.cash_reserve_pct = CASH_RESERVE_PCT
        self.credit_idle_cash = CREDIT_IDLE_CASH
        self.cash_annual_rate = CASH_ANNUAL_RATE
        self.risk_per_trade_pct = RISK_PER_TRADE_PCT

        self.stop_atr_multiple = STOP_ATR_MULTIPLE
        self.take_profit_atr_multiple = TAKE_PROFIT_ATR_MULTIPLE
        self.trail_atr_multiple = TRAIL_ATR_MULTIPLE
        self.ambiguous_bar_resolves_to = AMBIGUOUS_BAR_RESOLVES_TO
        self.use_regime_cash = USE_REGIME_CASH

        self.rebalance_sessions = REBALANCE_SESSIONS
        self.v2_rebalance_sessions = V2_REBALANCE_SESSIONS
        self.v2_credit_idle_cash = V2_CREDIT_IDLE_CASH
        self.check_exits_daily = CHECK_EXITS_DAILY
        self.universe_limit = UNIVERSE_LIMIT
        self.min_history_bars = MIN_HISTORY_BARS
        self.max_stale_sessions = MAX_STALE_SESSIONS

        self.market_close_hour = MARKET_CLOSE_HOUR
        self.market_close_minute = MARKET_CLOSE_MINUTE
        self.fundamental_lag_sessions = FUNDAMENTAL_LAG_SESSIONS
        self.undated_filing_policy = UNDATED_FILING_POLICY
        self.min_fundamental_coverage = MIN_FUNDAMENTAL_COVERAGE
        self.min_news_coverage = MIN_NEWS_COVERAGE

        self.sizing_mode = SIZING_MODE
        self.sizing_target_atr_pct = SIZING_TARGET_ATR_PCT

        self.news_provider = NEWS_PROVIDER
        self.news_window_sessions = NEWS_WINDOW_SESSIONS

        self.fundamental_trend_periods = FUNDAMENTAL_TREND_PERIODS
        self.fundamental_trend_weight = FUNDAMENTAL_TREND_WEIGHT

        # Restrict the universe to companies that HAVE a point-in-time
        # fundamental reading, instead of refusing to run when coverage is
        # thin. Off by default and never on for a headline result: the covered
        # subset is not a random sample of the market, it is the largest and
        # most-followed names, so a return measured on it is a return on that
        # subset and the report has to say so.
        self.restrict_to_fundamental_universe = False

        # Selection parameters live in config.py because the live scanner uses
        # them; copied in so a sweep can move them without touching it.
        self.technical_gate = config.TECHNICAL_GATE
        self.min_headroom_pct = config.MIN_HEADROOM_PCT
        self.selection_mode = config.SELECTION_MODE

        for key, value in (overrides or {}).items():
            if key not in _OVERRIDABLE:
                raise KeyError(
                    "%r is not an overridable backtest parameter. "
                    "Add it to backtest_config._OVERRIDABLE if it should be."
                    % key
                )
            setattr(self, key, value)

    # ---- costs -------------------------------------------------------------

    def buy_cost_rate(self):
        """Fraction added to a buy: commission plus slippage."""

        return (self.buy_commission_pct + self.slippage_pct) / 100.0

    def sell_cost_rate(self):
        """Fraction deducted from a sale: commission, tax and slippage."""

        return (self.sell_commission_pct + self.sell_tax_pct
                + self.slippage_pct) / 100.0

    def round_trip_pct(self):
        return (self.buy_cost_rate() + self.sell_cost_rate()) * 100.0

    # ---- plumbing ----------------------------------------------------------

    def replace(self, **overrides):
        """A copy with some fields changed. The original is untouched."""

        merged = {key: getattr(self, key) for key in _OVERRIDABLE}
        merged.update(overrides)

        return BacktestConfig(**merged)

    def as_dict(self):
        return {key: getattr(self, key) for key in sorted(vars(self))}

    def __repr__(self):
        return "BacktestConfig(round_trip=%.3f%%, positions=%d, rebalance=%d)" % (
            self.round_trip_pct(), self.max_positions, self.rebalance_sessions
        )


def load(path=None):
    """Config from a JSON file, falling back to the defaults above.

    Lets the parameters be changed without editing Python, which matters when
    the person changing them is deciding where their money goes.
    """

    path = path or os.path.join(BASE_DIR, "backtest_config.json")

    if not os.path.exists(path):
        return BacktestConfig()

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    unknown = [key for key in payload if key not in _OVERRIDABLE]

    if unknown:
        raise KeyError(
            "unknown keys in %s: %s" % (path, ", ".join(sorted(unknown)))
        )

    return BacktestConfig(**payload)


def cost_provenance():
    """One line per cost figure, for the report. Nothing here is decorative.

    A backtest report that states a return without stating what it assumed a
    trade costs is not a result, and the reader should not have to open a file
    to find out.
    """

    return [
        ("buy commission", BUY_COMMISSION_PCT, "DOCUMENTED",
         "config.TRANSACTION_COST_PCT, buy side"),
        ("sell commission", SELL_COMMISSION_PCT, "INFERRED",
         "documented 0.575% sell side minus the statutory 0.5% tax"),
        ("sell tax", SELL_TAX_PCT, "STATUTORY",
         "ماده ۱۴۳ قانون مالیات‌های مستقیم"),
        ("slippage", SLIPPAGE_PCT, "UNMEASURED",
         "no intraday or order-book data is stored; swept, not assumed"),
    ]


def check_costs():
    """Warn if the split has drifted from the round trip the project uses."""

    total = BUY_COMMISSION_PCT + SELL_COMMISSION_PCT + SELL_TAX_PCT

    if abs(total - EXPECTED_ROUND_TRIP_PCT) > 0.005:
        return (
            "cost split sums to %.3f%% but config.TRANSACTION_COST_PCT is "
            "%.3f%% -- the backtest and the rest of the project disagree "
            "about what a trade costs" % (total, EXPECTED_ROUND_TRIP_PCT)
        )

    return None
