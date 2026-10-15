"""Central configuration for SIRAT AI.

Every module reads its constants from here. Symbols used to be duplicated
across stocks.py, run_backtest.py, threshold_optimizer.py and tsetmc_data.py,
which meant adding a stock required four edits.
"""

import os


# ==============================
# PATHS
# ==============================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_NAME = os.path.join(BASE_DIR, "sirat_market.db")


# ==============================
# UNIVERSE
# ==============================

# insCode is the TSETMC instrument id used by the CDN API.
SYMBOLS = {
    "FOLAD": {
        "name": "فولاد",
        "insCode": "46348559193224090"
    },
    "FMLI": {
        "name": "فملی",
        "insCode": "35425587644337450"
    },
    "SHESTA": {
        "name": "شستا",
        "insCode": "2400322364771558"
    },
    "SHPNA": {
        "name": "شپنا",
        "insCode": "7745894403636165"
    },
    "KHODRO": {
        "name": "خودرو",
        "insCode": "65883838195688438"
    }
}

STOCK_LIST = list(SYMBOLS)


# ==============================
# INDICES (شاخص)
# ==============================

# The two the market is actually read by. TEDPIX is capitalisation-weighted, so
# a handful of giants move it; the equal-weighted index gives every constituent
# the same say. They diverge when large caps and the broad market disagree, and
# that divergence is the single most useful read on market breadth available
# without computing anything -- which is why both are here rather than just the
# headline number.
INDICES = {
    "TEDPIX": {
        "name": "شاخص کل",
        "insCode": "32097828799138957",
        "weighting": "capitalisation",
    },
    "TEDPIX_EW": {
        "name": "شاخص کل هم‌وزن",
        "insCode": "67130298613737946",
        "weighting": "equal",
    },
}

INDEX_LIST = list(INDICES)

# Sessions of index history the technical read is computed over. The published
# series runs to 2008, but market turnover -- the volume input -- only exists
# for the window we hold constituent bars for.
INDEX_ANALYSIS_BARS = 300


# ==============================
# SECTORS (صنایع)
# ==============================

# A sector needs at least this many constituents with usable history before it
# is worth aggregating. Below it, one company's move is the whole "sector".
MIN_SECTOR_CONSTITUENTS = 3

# Sessions the derived sector index is built over.
SECTOR_INDEX_BARS = 300


# ==============================
# DATA FETCHING
# ==============================

TSETMC_URL = "https://cdn.tsetmc.com"

HISTORY_BARS = 300

REQUEST_TIMEOUT = 20

# Cached bars younger than this are served straight from SQLite instead of
# re-hitting the network. A threshold sweep used to refetch identical data
# once per (threshold, symbol) pair.
CACHE_MAX_AGE_HOURS = 12


# ==============================
# INDICATORS
# ==============================

MA_FAST = 20
MA_SLOW = 50

RSI_WINDOW = 14

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

VOLUME_MA = 20

# Volatility and trend-strength windows. ATR sizes stops and positions,
# ADX says whether a trend is worth trading at all, Bollinger locates price
# inside its own recent range.
ATR_WINDOW = 14
ADX_WINDOW = 14
BB_WINDOW = 20

# Band width in standard deviations. Not a clamped window parameter -- it is a
# float, and clean_params() only accepts integers.
BB_STD = 2.0

# ADX above this is a trend strong enough to follow; below it the market is
# ranging and trend rules mean less.
ADX_TRENDING = 25


def min_bars(ma_slow=MA_SLOW, macd_slow=MACD_SLOW,
             macd_signal=MACD_SIGNAL, volume_ma=VOLUME_MA,
             rsi_window=RSI_WINDOW, atr_window=ATR_WINDOW,
             adx_window=ADX_WINDOW, bb_window=BB_WINDOW):
    """Longest lookback the given windows need.

    Bars before this have NaN indicators and must not be scored. It depends on
    the windows, so it is a function now that the panel can change them.

    ADX counts double: it smooths the directional movement over `adx_window`
    and then smooths that, so its first trustworthy reading is about twice the
    window in. At the default settings this whole expression still resolves to
    ma_slow (50), so adding the new indicators did not lengthen the warm-up.
    """

    return max(
        ma_slow,
        macd_slow + macd_signal,
        volume_ma,
        rsi_window,
        atr_window,
        adx_window * 2,
        bb_window,
    )


MIN_BARS = min_bars()

# Bounds for user-supplied windows, so a panel request cannot ask for a
# 1-bar or 5000-bar moving average.
WINDOW_MIN = 2
WINDOW_MAX = 200


# ==============================
# SCORING WEIGHTS (sum = 100)
# ==============================

WEIGHT_TREND = 25
WEIGHT_MACD = 20
WEIGHT_RSI = 15
WEIGHT_VOLUME = 15
WEIGHT_MOMENTUM = 15
WEIGHT_RISK = 10


# ==============================
# DECISION BANDS
# ==============================

STATUS_BANDS = [
    (80, "STRONG BUY CANDIDATE"),
    (70, "BUY WATCHLIST"),
    (60, "WATCHLIST"),
    (45, "HOLD / WAIT"),
    (0,  "NO ENTRY")
]

RSI_OVERBOUGHT = 70


# ==============================
# BACKTEST
# ==============================

HOLDING_DAYS = 10

# Holding periods compared side by side in the panel's holding sweep.
HOLDING_PERIODS = [5, 10, 15, 20, 30]

THRESHOLDS = [60, 65, 70, 75, 80, 85, 90]

DEFAULT_THRESHOLD = 70

# Round-trip cost in percent, deducted from every trade's return.
# Tehran Stock Exchange retail equity: ~0.464% buy + ~0.575% sell.
TRANSACTION_COST_PCT = 1.04

# Sessions per year on the Tehran Stock Exchange: Saturday-Wednesday minus
# public holidays, which lands near 240 rather than the 252 used for Western
# markets. Every annualised figure -- Sharpe, CAGR, volatility -- scales by the
# square root of this, so using 252 here would overstate them by ~2.5%.
TRADING_DAYS_PER_YEAR = 240


# ==============================
# EXIT RULES
# ==============================

# Holding for a fixed number of days is a placeholder, not a strategy: it exits
# a winner and a collapsing position on the same schedule. These give the
# position a stop and a target measured in units of its own volatility (ATR),
# so a quiet share gets a tight stop and a violent one gets room to breathe.
#
# Set STOP_ATR_MULTIPLE to 0 to fall back to the pure time-based exit.
STOP_ATR_MULTIPLE = 2.0
TARGET_ATR_MULTIPLE = 3.0

# Trailing stop distance in ATRs, applied to the highest close reached since
# entry. 0 disables trailing and leaves the initial stop fixed.
#
# Deliberately WIDER than STOP_ATR_MULTIPLE. The effective stop is
# max(initial stop, peak close - trail distance), so a trail tighter than the
# initial stop would bite the moment the position opened; at 3 ATR it stays
# below the initial stop until price has advanced ~2 ATR, then takes over and
# locks in what the move has already paid.
#
# Measured on 296 trades over 60 symbols (2025-05..2026-08): holding to a fixed
# +12.3% target returned +1.55% a trade while exiting on time alone returned
# +5.92%, because the average winner ran to +19.1%. Capping winners at 2:1 was
# costing more than the stop was saving, and a trail is the standard answer to
# exactly that.
TRAIL_ATR_MULTIPLE = 3.0

# When a bar's low pierces the stop AND its high reaches the target, daily OHLC
# cannot say which came first. Assuming the stop is the pessimistic reading and
# the only defensible default -- the optimistic one turns every ambiguous bar
# into a win and quietly inflates the whole backtest.
AMBIGUOUS_BAR_RESOLVES_TO = "stop"


# ==============================
# POSITION SIZING
# ==============================

# Rials committed per trade. Shares are floor(capital / entry price), so a
# position is always a whole number of shares and rial P&L is exact.
#
# In "risk" sizing this becomes the CEILING on a position rather than its size.
CAPITAL_PER_TRADE = 100_000_000

# "risk"  -- size so that being stopped out costs RISK_PER_TRADE_PCT of equity,
#            capped at CAPITAL_PER_TRADE. Two positions then carry the same
#            risk even when one share is four times as volatile as the other.
# "fixed" -- the old behaviour: every position is CAPITAL_PER_TRADE rials, so
#            the amount at risk is whatever the market decides.
POSITION_SIZING = "risk"

# Share of account equity a single stopped-out trade may cost.
RISK_PER_TRADE_PCT = 1.0

# Account equity the risk budget is a percentage of.
ACCOUNT_EQUITY = 1_000_000_000

CURRENCY = "IRR"


# ==============================
# PORTFOLIO CONSTRUCTION
# ==============================

# Names in the suggested basket.
PORTFOLIO_SIZE = 10

# Hard cap on names drawn from one industry. Ten names picked purely by score
# routinely land most of the basket in a single sector, which is one position
# wearing several names -- when that industry turns, every holding turns with
# it. Diversification here is a constraint, not a preference.
MAX_PER_SECTOR = 2

# No single position may exceed this share of equity. At ten names an equal
# split is 10%, so this leaves room for conviction without letting one name
# dominate the outcome.
MAX_POSITION_PCT = 15.0

# Floor on a position, as a share of equity. Below this the commission drag and
# the attention it costs outweigh anything the position can contribute.
MIN_POSITION_PCT = 2.0

# Cap on how much of a share's average daily traded value one position may be.
# This is the Tehran-specific constraint: a position worth several days of a
# share's entire turnover cannot be exited at the modelled price, and in a sell
# queue (صف فروش) cannot be exited at all.
MAX_ADV_PARTICIPATION_PCT = 5.0

# Cap on the share of equity any one industry may hold in total.
MAX_SECTOR_PCT = 30.0

# Cash floor. A fully invested book has no capacity to act on anything new.
MIN_CASH_PCT = 10.0

# Deployment floor for markets quiet enough that risk sizing under-deploys.
#
# It almost never binds on this market, and the reason is worth recording
# because the opposite was assumed first. Traced at the fullest rebalance date
# in the recent bull window, risk sizing wanted 145.3% of equity: nine of ten
# positions were already pinned at MAX_POSITION_PCT. Sizing is not what leaves
# the book in cash.
#
# What leaves it in cash is idle time. Three of sixteen rebalance dates in that
# window produced ZERO names clearing TECHNICAL_GATE, and with a 20-session
# rebalance the basket then sat fully in cash for 60 of 301 sessions. On the
# dates that did fill, stop-outs were not replaced until the next rebalance, so
# an average of 4.8 names were held out of ten. That -- not position size -- is
# why beta stayed near 0.25.
#
# Kept because a genuinely quiet market would under-deploy, and a floor costs
# nothing when it does not bind. Set to None to disable.
TARGET_INVESTED_PCT = 90.0

# Ceiling on the scale-up factor. Without it, a market so quiet that every
# position is tiny would lever the book into a handful of names at many times
# their risk-sized weight -- the target is meant to remove idle cash, not to
# manufacture leverage.
MAX_EXPOSURE_SCALE = 3.0

# Combined score a name must reach before it counts as "recommended for buying"
# and becomes eligible for the basket.
PORTFOLIO_MIN_SCORE = 60

# Minimum reward-to-risk before a trade is worth taking: the distance to target
# divided by the distance to stop. Below 2.0 the strategy needs a win rate most
# systems do not have.
MIN_RISK_REWARD = 2.0


# ==============================
# FUNDAMENTAL SCREEN
# ==============================

# Tehran Stock Exchange P/E sits far below Western levels because the
# risk-free rate is ~23%: a fair multiple is roughly 1/r, i.e. 4-5x. These
# bands are calibrated from a long-run TSE median P/E near 5.4.
# They are a defensible starting point, NOT a validated result -- sweep them
# with threshold_optimizer.py before trusting them.
PE_EXCELLENT = 4.0
PE_GOOD = 6.0
PE_FAIR = 9.0
PE_EXPENSIVE = 14.0

# Above this a P/E is treated as meaningless rather than merely expensive
# (usually a collapsed-earnings denominator).
PE_ABSURD = 60.0

# Minimum average daily value traded, in rials. Thin names cannot be entered
# or exited at the modelled price, so liquidity is a hard gate rather than a
# score component.
MIN_DAILY_VALUE = 5_000_000_000

# Bars of history required before a symbol can be screened at all.
MIN_HISTORY_BARS = 60

# A symbol whose last bar is older than this is stale (suspended/halted).
MAX_STALE_DAYS = 15

# Fundamental score weights (sum = 100). Weights of components that cannot be
# computed are redistributed across the rest, and the coverage is reported.
FUND_WEIGHT_VALUATION = 30      # P/E band
FUND_WEIGHT_PROFITABILITY = 25  # earnings yield vs the risk-free rate
FUND_WEIGHT_LIQUIDITY = 20      # tradability
FUND_WEIGHT_SIZE = 15           # market cap / institutional viability
FUND_WEIGHT_STABILITY = 10      # realised volatility

# Minimum fundamental score to pass to the technical stage.
FUND_APPROVE_SCORE = 50

# Iran's policy rate. Used as the earnings-yield hurdle: a share yielding less
# than cash has to justify itself on growth, which this screen cannot measure.
RISK_FREE_RATE = 0.23

# Base volume (حجم مبنا) was set to 1 for بورس and فرابورس on 1 Dey 1404
# (~2025-12-22). Closing prices either side of that date are not comparable:
# before it, closes were volume-dampened; after, they travel the full band on
# trivial volume. Backtests spanning it are flagged.
REGIME_BREAK_DATE = "2025-12-22"

# TSE daily price limit (دامنه نوسان). A single-session move materially beyond
# this cannot come from trading, so it marks a corporate action -- a capital
# increase (افزایش سرمایه) or dividend adjustment -- in an unadjusted series.
PRICE_BAND_PCT = 3.0

# Sectors where an earnings multiple does not measure what it measures
# elsewhere, so P/E and earnings yield are withheld rather than scored.
#
# An investment company's profit is the realised gain on holdings it chose to
# sell. The timing is discretionary and the size is unrelated to what the
# company owns, so its P/E says "did it sell something last year", not "is it
# cheap". The right yardstick is price to NAV, which neither the TSETMC
# snapshot nor a Codal income statement carries.
#
# Banks and insurers fail the same test for different reasons -- a bank's
# earnings depend on provisioning policy, an insurer's on reserve releases --
# and both are conventionally valued on book, not earnings.
#
# Measured on this universe: 14 of 136 investment/holding names, 3 of 19
# banks and 6 of 30 insurers currently show P/E below 4, which the valuation
# component reads as "very cheap". وگردش at 1.5, پارسیان at 1.7 and اسیا at
# 1.9 are disposal-year and reserve artefacts, not bargains.
#
# Withholding is deliberate: score_fundamentals redistributes a missing
# component's weight and reports reduced coverage, which is honest. Scoring
# them on a meaningless ratio is not.
EARNINGS_MULTIPLE_NOT_MEANINGFUL = frozenset({
    "سرمايه گذاريها",
    "شرکتهاي چند رشته اي صنعتي",
    "ساير واسطه گريهاي مالي",
    "فعاليتهاي كمكي به نهادهاي مالي واسط",
    "بانكها و موسسات اعتباري",
    "بيمه وصندوق بازنشستگي به جزتامين اجتماعي",
})


# Slack over the band before a move is called a corporate action.
CORPORATE_ACTION_THRESHOLD = 3.6

# Threshold for BACK-ADJUSTING a price series, which is a different job from
# flagging one bar as suspicious and needs a different number.
#
# CORPORATE_ACTION_THRESHOLD is calibrated for today's +/-3% band. Applied to
# deep history it flags one bar in eight, because the band has been wider in
# other years -- so a backtest that dropped those windows would drop 94% of
# them. Measured over 1,910,808 overnight moves in this database:
#
#     p50 0.70%   p90 4.13%   p95 4.85%   p99 7.81%   p99.5 10.0%   p99.9 41.2%
#
# The distribution breaks between p99.5 and p99.9. At 12% only 0.39% of bars
# qualify, and just 1-2% of them follow a trading halt -- so these are not
# reopenings, they are adjustments.
#
# The justification for adjusting rather than discarding: the exchange enforces
# a price band, so a single-session move past the widest band ever in force
# cannot be a trading move. Discarding those windows would also be a biased
# filter -- 63% of moves beyond 20% are negative, so dropping them removes more
# losses than gains and flatters every result.
PRICE_ADJUST_THRESHOLD = 12.0


# ==============================
# MONEY FLOW (حقیقی / حقوقی)
# ==============================

# TSETMC splits every session's turnover between individuals (حقیقی) and legal
# entities (حقوقی), and reports how many participants were on each side. The
# per-participant average is the signal Tehran traders actually read: one buyer
# committing a billion rials means something different from a thousand buyers
# committing a million each, and the value columns alone cannot tell them apart.

# Sessions averaged into the current reading. Five is one trading week, which
# is short enough to still be about the present and long enough that a single
# block trade does not become the verdict.
FLOW_WINDOW = 5

# Longer baseline the recent window is compared against, to answer "is this
# unusual for this share" rather than "is this a big number".
FLOW_BASELINE_WINDOW = 25

# Sessions of client-type data required before any of this is reported. Below
# this the reading is an accident of which days happened to be fetched.
FLOW_MIN_SESSIONS = 10

# How many sessions of flow history the DAILY REFRESH keeps per instrument.
# TSETMC returns the entire series on every call -- خودرو alone answers with
# 3,600 sessions back to 2008 -- and there is no way to ask for less. Storing
# all of it for 818 equities is ~2.5 million rows on every refresh, for the
# sake of a signal measured over the last 5.
#
# This is a REFRESH budget, not a retention limit. save_client_type_history
# upserts on (ins_code, date), so rows already stored behind this window are
# updated, never deleted -- a deep backfill done once survives every later
# refresh.
#
# It was previously described as "long enough to backtest a flow rule". That
# was wrong: 400 sessions is ~18 months and all of it falls inside one rising
# market, so a rule tested only within it has never been shown a falling one.
# Regime testing needs the depth the API already serves.
FLOW_HISTORY_SESSIONS = 400

# Both sets of bands below are placed on the observed cross-sectional
# distribution of the TSE itself, measured over the 706 equities that clear the
# liquidity floor, so each band holds roughly a fifth of the market. Bands
# chosen by intuition instead put the *median* share in the top bucket -- a
# score that calls half the market "strong inflow" ranks nothing against
# anything. Re-measure these if the market's character changes; the percentiles
# each one came from are noted beside it.

# قدرت خریدار: mean rials per individual buyer / mean rials per individual
# seller. Above 1 the average buyer is committing more than the average seller,
# i.e. concentrated accumulation against distributed selling. 1.0 is kept as a
# band edge even though the median sits just above it, because "the two sides
# are the same size" is a real boundary and not merely a percentile.
FLOW_POWER_STRONG = 1.8      # p80
FLOW_POWER_GOOD = 1.25       # p60
FLOW_POWER_WEAK = 1.0        # ~p43, and the point where the sides are equal
FLOW_POWER_BAD = 0.75        # p20

# Net individual inflow over the window as a share of the window's turnover.
# Normalising by the share's own turnover is what makes a 50B rial inflow into
# a small company rank above the same figure into a giant.
#
# Note how far from symmetric this is: the median share runs a +4.8% retail
# inflow, because حقوقی are net sellers to retail across the market most weeks.
# Zero is therefore not the neutral point -- placing the middle band at ±2%, as
# though it were, put 60% of the market above it.
FLOW_NET_STRONG = 20.0       # p80
FLOW_NET_GOOD = 8.0          # p60
FLOW_NET_WEAK = 0.0          # ~p40
FLOW_NET_BAD = -8.0          # p20

# Flow score weights (sum = 100), same redistribute-on-missing contract as the
# fundamental screen.
FLOW_WEIGHT_POWER = 45          # قدرت خریدار حقیقی
FLOW_WEIGHT_NET = 35            # ورود/خروج پول حقیقی, normalised by turnover
FLOW_WEIGHT_PERSISTENCE = 20    # how many of the window's sessions agreed

# Flow score above/below which the reading is called supportive or opposed.
FLOW_BULLISH_SCORE = 65
FLOW_BEARISH_SCORE = 35

# How many points of the combined score the flow reading may move, in either
# direction. Deliberately a tilt rather than a third weight: flow data covers
# only the symbols whose per-share fetch has succeeded, and folding it in as a
# full component would score covered and uncovered names on different models
# inside one ranked list. A bounded tilt leaves an uncovered name exactly where
# fundamentals and technicals put it -- absence moves nothing.
FLOW_TILT_MAX = 8.0


# ==============================
# CODAL (صورت‌های مالی)
# ==============================

# codal.ir publishes every listed company's filings. Two endpoints matter:
#
#   search.codal.ir/api/search/v2/q      announcement metadata, ~16KB per
#                                        symbol -- cheap enough to poll
#   excel.codal.ir/service/Excel/GetAll  the filing itself. Despite the name
#                                        and the application/ms-excel header it
#                                        returns HTML tables, 0.5-1 MB each.
#
# That size ratio is the whole architecture: the cheap call decides whether the
# expensive one is needed. A statement is fetched only when the search shows a
# filing we have not already stored.
CODAL_SEARCH_URL = "https://search.codal.ir/api/search/v2/q"

# Letter codes, from the exchange's own taxonomy. Verified against live
# responses -- these are the ones this project reads.
CODAL_LETTER_STATEMENTS = "ن-۱۰"      # صورت‌های مالی / میاندوره‌ای
CODAL_LETTER_MONTHLY = "ن-۳۰"         # گزارش فعالیت ماهانه (فروش)
CODAL_LETTER_MATERIAL = "ن-۲۰"        # افشای اطلاعات بااهمیت
CODAL_LETTER_BOARD = "ن-۱۱"           # گزارش فعالیت هیئت مدیره

# The monthly report an INVESTMENT company files. Same cadence as ن-۳۰ and a
# near-identical title, but a different document: where a manufacturer reports
# sales, this carries صورت وضعیت پرتفوی -- every holding with its cost and its
# market value. It is the only source in this project for net asset value, and
# NAV is the only yardstick that values an investment company properly.
# Measured: 615 of these are already on disk for 95 companies.
CODAL_LETTER_PORTFOLIO = "ن-۳۱"       # گزارش فعالیت ماهانه (پرتفوی)

# Filings whose figures are parsed. The rest are stored as headlines only:
# an announcement's *existence* and date is useful context, and parsing every
# letter type would be a large amount of template-specific code for filings
# that carry no ratio input.
CODAL_PARSED_LETTERS = (CODAL_LETTER_STATEMENTS, CODAL_LETTER_MONTHLY)

# Codal states statement figures in **million rials** while per-share figures
# on the same sheet are in rials. Mixing them silently is a factor of a
# million, so the multiplier is named here rather than sprinkled as `* 1e6`.
CODAL_AMOUNT_SCALE = 1_000_000        # million rials -> rials

# How long a stored filing set is trusted before the cheap search is repeated.
# Companies file on a quarterly cadence; a daily re-check is generous.
CODAL_RECHECK_HOURS = 20

# How soon a company is retried after its last check FAILED. This is a
# different question from the one above and must not share its answer: a
# successful check that found nothing new is genuinely good for a day, while a
# check that died because the route to codal.ir was down says nothing about
# the company at all. Sharing the 20-hour interval meant that toggling a VPN
# left the fundamentals empty until the next day even though the network was
# fixed seconds later.
CODAL_RETRY_MINUTES = 6

# How long a "Codal has no filings for this symbol" answer is trusted. Long,
# because it is usually permanent: the universe holds funds, rights issues and
# freshly listed names that file nothing at all. Retrying those on the
# six-minute transient interval would spend an entire run's budget on symbols
# that will never have data, starving the ones that would.
CODAL_EMPTY_BACKOFF_HOURS = 96

# Consecutive route-level failures before a run gives up early. When codal.ir
# is unreachable, every company in the queue fails identically; grinding
# through all of them costs minutes and achieves nothing. Stopping early means
# the next run -- minutes later, per CODAL_RETRY_MINUTES -- picks straight up
# once the route is back.
CODAL_ABORT_AFTER_FAILURES = 5

# Escalating cool-off after a run aborts on transient failures.
#
# Retrying a rate limit every six minutes does not wait it out -- it renews it.
# Codal's throttle is a rolling window, so a client that keeps knocking keeps
# the window open, and the panel sat in a permanent 429 of its own making.
# Each consecutive aborted run doubles the wait; one successful run clears it.
CODAL_COOLDOWN_START_MINUTES = 15
CODAL_COOLDOWN_MAX_MINUTES = 240

# Statements kept per company. Growth and trend need several periods; the
# whole history would be hundreds of filings for an old company.
CODAL_KEEP_PERIODS = 12

# How many months behind the market's newest filing a company's own newest
# filing may be before its fundamentals stop counting as current.
#
# This is not pedantry. A company that last reported for 1400/09 -- five years
# ago, usually because it is suspended or in dispute -- otherwise ranks on
# those figures as though they described today, and lands near the top of a
# fundamental ranking on numbers nobody has confirmed since. Eighteen months
# is generous: it clears an annual reporting cycle plus the audit lag, and
# still catches a company that has simply stopped filing.
CODAL_STALE_MONTHS = 18

# Ceiling on statement downloads per automatic run. A full universe backfill is
# ~800 MB and hours; capping the run means the panel fills in progressively
# over several days instead of blocking on one enormous first fetch. Symbols
# are prioritised by how much the rest of the system already cares about them.
CODAL_MAX_FETCH_PER_RUN = 25

# Seconds between Codal requests. Measured, not guessed: at 0.6s a batch of
# ~50 companies earned a sustained HTTP 429 that a 20-second pause did not
# clear. Codal is a disclosure site serving megabyte documents, not a market
# feed built for polling, and it throttles on sustained access rather than on
# instantaneous rate -- so the fix is to be slower throughout, not to burst
# and back off. At this pace a full run is a few minutes of background work,
# which costs nothing because nothing waits on it.
CODAL_REQUEST_DELAY = 2.0

# Ceiling the adaptive limiter will widen to after repeated 429s.
CODAL_MAX_DELAY = 15.0


# ==============================
# FUNDAMENTAL SCORE (کدال)
# ==============================

# Six pillars, summing to 100. This is a different score from the one in
# screening.py: that one gates the universe on a market snapshot (P/E,
# liquidity, size) and runs for every symbol, while this one reads filed
# financial statements and only exists for companies whose filings we hold.
FUND_WEIGHT_PROFIT = 22         # ROE, ROA, net margin
FUND_WEIGHT_GROWTH = 22         # revenue, profit and EPS growth
FUND_WEIGHT_VALUE = 18          # P/E, P/S, P/B against TSE-calibrated bands
FUND_WEIGHT_HEALTH = 16         # debt ratio, current ratio
FUND_WEIGHT_CASHFLOW = 12       # operating cash flow, and its trend
FUND_WEIGHT_QUALITY = 10        # does reported profit arrive as cash

# ROE bands. Anchored on the ~23% policy rate: equity returning less than cash
# is destroying value in real terms, however profitable it looks nominally.
ROE_EXCELLENT = 45.0
ROE_GOOD = 30.0
ROE_FAIR = 23.0                 # the risk-free rate itself
ROE_WEAK = 12.0

ROA_EXCELLENT = 25.0
ROA_GOOD = 15.0
ROA_FAIR = 8.0
ROA_WEAK = 3.0

NET_MARGIN_EXCELLENT = 30.0
NET_MARGIN_GOOD = 18.0
NET_MARGIN_FAIR = 8.0
NET_MARGIN_WEAK = 2.0

# Growth bands are nominal and must be read against inflation: with ~40% CPI,
# nominal revenue growth below that is a real-terms contraction. The bands say
# so rather than rewarding a 25% grower for beating zero.
INFLATION_RATE = 40.0
GROWTH_EXCELLENT = 80.0
GROWTH_GOOD = INFLATION_RATE
GROWTH_FAIR = 15.0
GROWTH_WEAK = 0.0

# Price to sales / price to book, on TSE levels.
PS_EXCELLENT = 1.0
PS_GOOD = 2.5
PS_FAIR = 5.0

PB_EXCELLENT = 1.5
PB_GOOD = 3.0
PB_FAIR = 5.0

# Debt ratio = total liabilities / total assets.
DEBT_SAFE = 40.0
DEBT_FAIR = 60.0
DEBT_HIGH = 75.0

CURRENT_RATIO_STRONG = 1.8
CURRENT_RATIO_FAIR = 1.2
CURRENT_RATIO_WEAK = 1.0

# Earnings quality = operating cash flow / net profit. Below 1 the company is
# booking profit it has not collected; sustained, that is the single most
# reliable warning in a filing.
QUALITY_EXCELLENT = 1.2
QUALITY_GOOD = 0.8
QUALITY_FAIR = 0.5

# Labels for the fundamental verdict.
FUND_STATUS_BANDS = [
    (80, "Strong"),
    (65, "Good"),
    (45, "Fair"),
    (30, "Weak"),
    (0, "Poor"),
]


# ==============================
# COMBINED SCORING ENGINE
# ==============================

# How the basket picks among approved names.
#
#   "blend"           rank by the combined score (the original behaviour)
#   "gate_then_flow"  require technical >= TECHNICAL_GATE, then rank by flow
#   "combined"        gate, then rank by relative flow AND headroom together
#
# "combined" exists because the two survivors of this project's measurement
# turned out to be independent. Over 6,565 point-in-time signals the
# correlation between the flow score and headroom to the 120-session high was
# -0.012, and headroom still laddered inside every flow band:
#
#     flow \ room    0-3%    3-6%   6-10%  10-20%   20%+
#     <40            2.59    2.42    1.11    4.25    6.24
#     40-59         -0.89    2.20    3.02    2.92    7.89
#     60-79          2.49    0.40    1.86    5.80    7.77
#     80-100         5.05    4.40    8.40    9.04   11.11
#
# Worst cell -0.89%, best +11.11%. Two independent signals multiply where one
# only sorts.
#
# The second is what the measurement supports. Over 3,393 point-in-time signals
# the technical score did NOT sort by outcome above its own threshold -- the
# 70-79 bucket returned more than the 90-100 bucket -- while the flow score
# rose monotonically across all five of its buckets. Ranking by a component
# that does not rank is how a basket ends up looking selective without being
# selective. See scoring.selection_score() for the full table.
#
# Set back to "blend" to restore the previous behaviour; both are tested.
SELECTION_MODE = "gate_then_flow"

# The technical score is a gate, not a ranking. Clearing it is what carries
# information; clearing it by more does not.
TECHNICAL_GATE = 70

# Minimum clear air to the 120-session high before an entry is worth taking,
# as a percentage of price. None disables the filter.
#
# Measured over 6,411 point-in-time signals across 814 symbols, returns over
# the following 20 sessions sorted almost perfectly by this one number:
#
#     headroom     n      mean      win%
#     0-3%       489    +2.17%     55.2
#     3-6%       609    +2.37%     53.4
#     6-10%      632    +2.71%     52.1
#     10-20%   1,195    +5.14%     56.3
#     20%+     3,017    +8.08%     69.0
#
# The break sits at 10%: below it the three buckets average +2.4%, above it
# the two average +7.4%. Buying into a share pressed against its own
# multi-month ceiling is the single most expensive thing this scan was doing.
#
# The 250-session high ladders the same way and the 60-session one is weaker,
# so 120 is the horizon that carries the information. Notably, Fibonacci
# retracements and swing pivots produced NO ladder at all -- the simplest
# construction won and the most elaborate one measured nothing.
MIN_HEADROOM_PCT = 10.0

# Sessions the ceiling is measured over. Changing this invalidates the bands
# above; they were measured at 120.
HEADROOM_WINDOW = 120

# Weights for SELECTION_MODE = "combined". Each input is converted to a
# percentile among the candidates available on that date before blending, so
# the two are comparable despite one being sigmas and the other percent, and
# so the ranking self-adjusts when a whole market is stretched or compressed.
COMBINED_WEIGHT_FLOW = 0.5
COMBINED_WEIGHT_HEADROOM = 0.5

# Sessions of a symbol's own flow history the relative measure is standardised
# against.
#
# Measured against the flow score currently in use, over the same 6,565
# signals -- the relative construction is both steeper and monotone where the
# score in use puts nearly all its lift in the top band alone:
#
#     relative (sigma)          flow score (in use)
#     <-0.5    +3.92  57.8%     <40      +4.40  59.1%
#     -0.5..0  +4.10  58.4%     40-59    +4.65  60.2%
#     0-0.5    +4.95  60.4%     60-79    +5.29  60.5%
#     0.5-1    +6.21  63.9%     80-100   +8.69  67.6%
#     1+       +9.25  67.1%
#
# The reason it separates better: 2% net retail buying is an event for a share
# that normally runs at 0.5% and a quiet day for one that runs at 3%. The
# absolute score cannot tell those apart.
FLOW_RELATIVE_BASELINE = 120

# What breaks a tie when two candidates share a ranking score.
#
#   "liquidity"    average daily value traded -- raw measured turnover
#   "risk"         the composite risk score (higher = safer)
#   "fundamental"  the fundamental screen score (the previous behaviour)
#
# This is not a detail. Flow scores saturate: 63 of 779 equities currently
# score exactly 100, and a ten-name basket drawn from that group is decided
# ENTIRELY by the tiebreak. While it was "fundamental", an unvalidated
# component was silently choosing the basket while appearing to have been
# removed from the ranking -- the worst of both arrangements.
#
# Liquidity is the default because it is measured rather than modelled: no
# bands, no weights, no assumption about what a good business looks like. It
# also answers a real question at that moment -- of two names the model likes
# equally, prefer the one that can actually be exited at the modelled price.
TIEBREAK = "liquidity"

# The overall score is a weighted blend of the readings the system produces.
# Weights are renormalised over whatever is actually available, so a company
# with no Codal filings is ranked on the components it does have rather than
# being pushed down a list by an absent one. Everything here is tunable, which
# is the point -- the blend is a preference, not a fact.
SCORE_WEIGHT_TECHNICAL = 35
SCORE_WEIGHT_FUNDAMENTAL = 35
SCORE_WEIGHT_RISK = 15
SCORE_WEIGHT_FLOW = 15

# A component missing on its own is fine. Below this share of total weight the
# overall score is reported as unscored instead: blending 15% of a model into
# a confident-looking number is how "we know almost nothing" becomes "82".
SCORE_MIN_COVERAGE = 0.45

# Divergence flags. These name the two cases a combined average hides -- a
# cheap sound company the market has not turned yet, and a strong chart with
# nothing behind it.
DIVERGENCE_GAP = 25             # score points between the two readings

FINAL_STATUS_BANDS = [
    (80, "STRONG BUY CANDIDATE"),
    (68, "BUY WATCHLIST"),
    (55, "WATCHLIST"),
    (40, "HOLD / WAIT"),
    (0, "NO ENTRY"),
]


# ==============================
# V2 — THE SWING DECISION ENGINE
# ==============================
#
# Everything below is read only by the v2 engines (regime_engine, factor_engine,
# fundamental_engine, risk_engine, trade_manager, portfolio_engine). Nothing
# above this line reads any of it, which is what makes the v2 layer additive:
# changing a number here cannot move a v1 score.

# WHICH ENGINE PRODUCES THE RECOMMENDED BOOK. "v1" or "v2".
#
# This constant exists because the two chains were, until it did, connected to
# nothing in common. The v2 engines were reachable only by running
# swing_advisor.py directly, so every change to a Swing Score weight was
# invisible to `python advisor.py`, to `python main.py` and to the panel --
# MEASURED: a full panel run made 0 calls into factor_engine and
# regime_engine, and setting the momentum weight to 50% moved 0 of 300 scores.
# The engine was not broken; it was not plugged in.
#
# WHAT EACH ONE IS:
#
#   v1   pipeline.run_scan -> scoring.evaluate -> portfolio.build
#        The chain every measured result in this project came from. Ranks on a
#        technical/fundamental/risk/flow blend over the whole market.
#
#   v2   swing_advisor.scan -> factor_engine -> risk_engine -> trade_manager
#        -> portfolio_engine. A multi-week swing horizon, an explicit
#        BUY/HOLD/SELL/AVOID, stops and two targets per name.
#
# THE DEFAULT IS v2 BY EXPLICIT CHOICE of whoever runs this system: the panel is
# expected to show the swing engine's book. It is recorded here rather than in a
# commit message because the consequence is large and worth seeing next to the
# switch -- on the same session and the same universe the two chains' ten-name
# books shared ZERO names, so this is not a tweak to a list, it is a different
# list. v1 remains the chain every measured number in this project came from.
#
# To go back, for one run or for good:
#     python advisor.py --engine v1        one run
#     http://127.0.0.1:5000/?engine=v1     one page load
#     DECISION_ENGINE = "v1"               here
#
# COST, measured end to end through the panel's own endpoint on this machine:
# v2 scores the whole universe itself (816 names, 49.6s) where v1's screen hands
# the technical stage only ~300 survivors (22.0s). The panel memoises for
# CACHE_TTL, so a cached load is ~0.03s and it is the first one after a restart
# that pays.
DECISION_ENGINE = "v2"

# ---- the factors of the Swing Score -------------------------------------
#
# THE SWING SCORE RANKS STOCKS, SO EVERY WEIGHT IN IT BELONGS TO A STOCK.
#
# The regime weight is 0, and that is the whole change: a market read is a
# property of the DATE, identical for every name scored on that session, and a
# component with no cross-sectional variance cannot reorder a list. Its only
# effect on a ranking was to dilute the factors that do vary -- measured, on
# 17,672 point-in-time observations: 2.54% mean forward return at 20% regime
# weight, 4.14% with it off, 1.50% at double. Monotone in the wrong direction.
#
# The market read did not go away. It moved to the job it can actually do, in
# regime_engine: how much capital is at risk (TRADING_MODE_BUDGET), how large a
# position may be, and whether new entries are permitted at all
# (TRADING_MODE_ENTRY_FLOOR below). Nothing about the market's state now moves
# one name above another.
#
# The surviving weights come from weight_optimizer.py -- 264 grid vectors over
# the ranges the specification asked for, fitted on the first 14 decision dates
# and judged only on the last 9, with each vector averaged against its grid
# neighbours so a lucky spike between two bad points cannot win. They are a
# plateau, not a peak, and the plateau is wide: several nearby vectors do about
# as well, so none of these numbers is precise to the point.
#
# The shape barely moved. Against the previous weights the only real changes are
# the regime going to zero and the fundamental weight doubling from 15 to 30 --
# which is the same conclusion the fundamental filter study reached from the
# other direction, and the reason to believe it is not a fit to one window.
#
# MOMENTUM AND TECHNICAL OVERLAP and that is stated rather than hidden: the
# technical score already spends 15 of its 100 points on 5- and 10-session
# momentum and 15 more on the same volume ratio the liquidity factor reads. The
# momentum factor here is measured over 20/60/120 sessions and against the
# index, so the two agree on direction and disagree on horizon -- a share that
# popped this week inside a year-long downtrend scores well on one and badly on
# the other, which is the distinction a swing horizon needs.
#
# THE CATALYST WEIGHT IS NOT A MEASUREMENT. No news archive exists, so the
# catalyst factor is absent on every observation in the study, and a weight on a
# factor that is never present cannot change any score -- it is excluded from
# the blend and from the covered weight alike. Its 10% is the design intent for
# the day news.py has a provider, held at the value it always had. The four
# weights the grid actually selected are momentum, technical, fundamental and
# liquidity, in the ratio 25:20:30:15.
SWING_WEIGHT_REGIME = 0
SWING_WEIGHT_MOMENTUM = 25
SWING_WEIGHT_TECHNICAL = 20
SWING_WEIGHT_FUNDAMENTAL = 30
SWING_WEIGHT_LIQUIDITY = 15
SWING_WEIGHT_CATALYST = 10

# Below this share of total factor weight there is no Swing Score at all. Set
# higher than SCORE_MIN_COVERAGE because two of the five scoring factors
# (fundamental, catalyst) are routinely absent on this market.
#
# The regime weight being 0 does NOT tighten this. A zero-weighted factor
# contributes nothing to the covered weight and nothing to the total, so the
# ratio is unchanged: the common case -- momentum, technical and liquidity
# present, no filing and no news -- covers 75% of the model, exactly as the
# 25/20/10 + 20 regime arrangement did. What the floor now refuses is a name
# with no technical reading AND no filing, which used to be carried over the
# line by a market read that said nothing about it.
SWING_MIN_COVERAGE = 0.50

# The action bands. A Swing Score is a ranking device; these turn it into the
# BUY / HOLD / AVOID vocabulary the spec asks for.
SWING_BUY_SCORE = 68
SWING_HOLD_SCORE = 50
SWING_EXIT_SCORE = 40

SWING_STATUS_BANDS = [
    (80, "خرید قوی"),
    (SWING_BUY_SCORE, "خرید"),
    (SWING_HOLD_SCORE, "نگهداری"),
    (SWING_EXIT_SCORE, "زیر نظر"),
    (0, "اجتناب"),
]

# ---- fundamental engine: the four groups --------------------------------
#
# The spec's grouping, which is not the same as the six pillars in
# fundamental_scoring.py. The pillars stay as they are -- they are what the
# backtest measured -- and these weights govern only how the four-group view is
# assembled from them. Leverage appears in Quality as a LEVEL and in Risk as an
# EXTREME, deliberately: moderate gearing is a quality question, gearing past
# DEBT_HIGH is a solvency one.
FUND_GROUP_WEIGHT_QUALITY = 32
FUND_GROUP_WEIGHT_GROWTH = 28
FUND_GROUP_WEIGHT_VALUATION = 22
FUND_GROUP_WEIGHT_RISK = 18

FUND_GROUP_MIN_COVERAGE = 0.40

# Score ceilings imposed by a risk flag. A cheap loss-maker must not reach a
# buy band on valuation alone, which an unweighted blend lets it do: a company
# with a 90 valuation score and nothing else can average into the sixties.
FUND_CAP_LOSS_MAKING = 45
FUND_CAP_NEGATIVE_EQUITY = 25
FUND_CAP_HEAVY_DEBT = 60
FUND_CAP_CASH_BURN = 55
FUND_CAP_PROFIT_UNCOLLECTED = 65

# Coefficient of variation of net margin across filed periods, above which
# earnings are called unstable. Measured on margin rather than on profit
# because periods differ in length and a margin is already period-free.
FUND_STABILITY_STABLE_CV = 0.25
FUND_STABILITY_VOLATILE_CV = 0.60

# ---- regime engine ------------------------------------------------------
#
# Turnover trend as a regime input. The spec asks for ارزش معاملات and حجم کل
# بازار, which regime.py does not read at all; these bands are the ratio of
# recent market turnover to its own longer baseline.
REGIME_TURNOVER_WINDOW = 10
REGIME_TURNOVER_BASELINE = 60
REGIME_TURNOVER_EXPANDING = 1.25
REGIME_TURNOVER_CONTRACTING = 0.75

# The regime factor's 0-100 contribution, before confidence and turnover
# adjust it. 50 is neutral, so a sideways market neither helps nor hurts a name.
REGIME_SCORE_BULL = 85.0
REGIME_SCORE_SIDEWAYS = 50.0
REGIME_SCORE_BEAR = 15.0

# Trading modes, and the share of the invested budget each permits. Multiplied
# by the regime's own max_invested_pct rather than replacing it, so the cash
# floor regime.py computes is never loosened by this layer.
#
# THIS IS THE REGIME'S ONLY LEVER ON SIZE, and since the regime left the Swing
# Score it is the regime's main lever altogether. The same number is reported as
# `position_multiplier` and is what shrinks the per-trade risk budget in
# risk_engine -- one knob, named twice, rather than two knobs doing one job.
#
# THESE NUMBERS ARE A RISK POLICY AND NOT A MEASURED EDGE, and on the one window
# that can be measured the evidence points the other way. Over 23 decision dates
# (weight_optimizer.py, section 6) the mode labels sorted forward returns like
# this:
#
#     AGGRESSIVE   11 dates   +1.06% mean   worst -11.56%
#     DEFENSIVE    10 dates   +4.35% mean   worst  -5.25%
#     NORMAL        2 dates  +20.37% mean   worst +14.95%
#
# The bearish label was followed by BETTER 20-session returns than the bullish
# one, and both double-digit losses landed on AGGRESSIVE dates. So cutting size
# in a defensive market would have cost return on this window rather than saving
# it -- the read is late, which is what a trend-following detector is on a market
# that mean-reverts after a fall.
#
# The multipliers are kept anyway, because capping exposure when the index is
# falling is a statement about how much can be lost when the read is WRONG, and
# 23 periods spanning roughly one cycle cannot settle that. But nothing here
# should be described as validated, and the honest next step is to measure
# regime.py's detector as a timing signal in its own right.
#
# The entry floor below is a different matter: it IS measured, on the same data.
TRADING_MODE_BUDGET = {
    "AGGRESSIVE": 1.0,
    "NORMAL": 0.85,
    "SELECTIVE": 0.6,
    "DEFENSIVE": 0.35,
    "CASH_ONLY": 0.0,
}

# A sideways market never gets full size, whatever mode it reached. NORMAL is
# the mode both a calm sideways market and a fragile bull arrive at, and those
# two deserve the same caution about the vote but not the same exposure. The
# ceiling is applied to the sideways case only, and it can only ever tighten.
REGIME_SIDEWAYS_MAX_MULTIPLIER = 0.7

# Extra Swing Score points a name must clear to be ENTERED in each mode -- the
# specification's "in a bear market only strong signals are allowed", expressed
# as the one thing a score-based system can enforce.
#
# It raises the entry bar; it never reorders. A name is not moved above another
# because the market is weak, it is simply not bought unless it is further into
# the buy band than a calm market would require. Exits are untouched: a floor
# that also raised the exit threshold would liquidate a whole book on the
# session the market turned defensive, which is selling the bottom by policy.
# MEASURED, on the same 23 decision dates, compounding an equal-weight top-10
# basket and treating a period with no qualifying name as FLAT rather than as
# absent -- which is the only comparison that is fair, since the floor changes
# how many periods are traded at all:
#
#     floor    total return    max drawdown    Sharpe    periods sat out
#      +0         146.3%          -15.5%        1.63           0
#      +5         126.0%          -11.6%        1.62           3
#     +10         128.6%          -11.6%        1.66           3
#     +15         105.6%          -11.6%        1.45           3
#     +20          87.4%          -11.9%        1.28           4
#
# +10 buys 3.9 points of drawdown for 17.7 points of two-year return at an
# unchanged Sharpe. That trade is taken because the stated objective is the
# return-to-risk ratio rather than the return -- but the whole effect rests on
# three skipped dates, one of which would have returned +20%, and the Sharpe
# differences here are inside the noise of 23 periods. Set this back to 0 and
# nothing else in the system needs to change.
#
# SELECTIVE's 5 is an interpolation, not a measurement: no date in the window was
# labelled SELECTIVE, so the floor above was measured on DEFENSIVE dates alone.
TRADING_MODE_ENTRY_FLOOR = {
    "AGGRESSIVE": 0,
    "NORMAL": 0,
    "SELECTIVE": 5,
    "DEFENSIVE": 10,
    "CASH_ONLY": 0,      # unreachable -- CASH_ONLY refuses entries outright
}

# ---- momentum factor ----------------------------------------------------
#
# Swing horizons. 20/60/120 sessions is roughly one/three/six months on a
# 240-session year, and the shortest of them is already longer than the
# 5/10-session window the technical score reads.
MOMENTUM_HORIZONS = (20, 60, 120)
MOMENTUM_HORIZON_WEIGHTS = (0.45, 0.35, 0.20)

# Bands for a single horizon's return, in percent. Set above zero because a
# nominal gain in a ~40% inflation economy is not momentum, and because the
# index itself carries most names along -- relative strength is scored
# separately and these are the absolute bands.
MOMENTUM_STRONG_PCT = 15.0
MOMENTUM_GOOD_PCT = 6.0
MOMENTUM_FLAT_PCT = 0.0
MOMENTUM_WEAK_PCT = -8.0

# Relative strength against the index, in percentage points of excess return.
MOMENTUM_RS_STRONG = 8.0
MOMENTUM_RS_GOOD = 2.0
MOMENTUM_RS_WEAK = -5.0

# Share of the momentum factor carried by relative strength rather than by the
# absolute return. Half, because in a market this correlated an absolute
# reading alone mostly measures the index.
MOMENTUM_RS_WEIGHT = 0.40

# ---- risk engine --------------------------------------------------------
#
# Sessions of average turnover a position may represent. A holding worth four
# days of a name's entire volume cannot be exited at the modelled price,
# whatever the volatility reading says.
RISK_MAX_EXIT_SESSIONS = 3.0

# Stop distance sanity band, as a percent of price. A stop tighter than the
# daily band is noise and will be hit by nothing; one wider than this makes the
# position too small to matter after risk sizing.
RISK_MIN_STOP_PCT = PRICE_BAND_PCT
RISK_MAX_STOP_PCT = 18.0

# Risk score floor for a position to be opened at all.
RISK_MIN_SCORE = 35

# ---- trade manager ------------------------------------------------------
#
# Two targets rather than one, and T1 is deliberately set ABOVE the minimum
# acceptable reward/risk rather than at it.
#
# Setting TARGET_1_RR = MIN_RISK_REWARD looks natural and quietly breaks the
# ceiling logic: the target then sits exactly on the acceptance boundary, so ANY
# trim for overhead supply -- a single rial -- drops the ratio below the minimum
# and rejects the trade. The "pull the target under the wall and recompute"
# behaviour could only ever reject, never trim-and-accept, which makes it a
# rejection rule wearing the costume of an adjustment.
#
# At 2.5 there is room to trim. A name with a 4% stop and a wall 9% overhead
# gets a target at 8.95% and a reward/risk of 2.2 -- accepted, with an honest
# target -- while a name whose wall sits inside 2x its stop still fails, which is
# the case that should fail.
TARGET_1_RR = 2.5
TARGET_2_RR = 3.5

# A target sitting above a remembered ceiling is a target the tape has to chew
# through. When overhead supply is within this fraction of the computed target,
# the target is pulled back to just under it -- and the resulting reward/risk is
# recomputed, which is the point: a capped target can fail MIN_RISK_REWARD and
# turn a BUY into an AVOID.
TARGET_CEILING_TOLERANCE = 0.35

# Expected holding period, in sessions. Derived from how far the target is and
# how fast the share actually moves, then clamped to the swing horizon -- an
# estimate of duration, never a promise.
HOLD_MIN_SESSIONS = 5
HOLD_MAX_SESSIONS = 40

# Fraction of a session's average true range that a trending share converts
# into net directional progress. Price does not travel its full ATR in one
# direction every day; 0.45 is a deliberately conservative reading, and it only
# affects the duration estimate, never a price level.
HOLD_ATR_EFFICIENCY = 0.45


# ==============================
# ADMIN PANEL
# ==============================

SERVER_HOST = "127.0.0.1"

# app.py refuses to start when the port is taken and tells the reader to "set
# SIRAT_SERVER_PORT=..." to serve elsewhere. Nothing read that variable, so the
# advertised escape hatch did nothing and the second panel had no way to come
# up at all. An unparseable value falls back rather than crashing the panel on
# a typo.
try:
    SERVER_PORT = int(os.environ.get("SIRAT_SERVER_PORT", "") or 5000)
except ValueError:
    SERVER_PORT = 5000
