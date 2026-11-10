"""Portfolio-level backtest: does the basket beat simply owning the index?

WHY THIS EXISTS
---------------
Every measurement in this project until now was per-trade and independent:
"a signal at technical>=70 with high flow returned +5.85% over 20 sessions".
That answers a question nobody holds. What the user holds is a ten-name basket
with a sector cap, a cash floor and a risk budget, rebalanced periodically --
and a basket has properties no per-trade average contains:

  * positions fall together, so basket drawdown is not the average of trade
    drawdowns
  * capital is finite, so a good signal arrives when there is nothing to sell
  * the sector cap binds, rejecting the highest-ranked names by construction
  * every rotation pays the round trip twice

Most importantly: in a market whose index rose 78% in fifteen months, a basket
returning 50% is a failure that a per-trade profit factor of 2.95 reports as a
success. performance.relative_to() has been able to answer this from the start
and nothing ever called it with a portfolio. This is that caller.

HOW IT WORKS
------------
Two phases, because scoring is the expensive part and selection is cheap:

  1. SCORE PANEL -- walk each symbol once, recording its technical and flow
     score at every rebalance date, using only bars up to that date.
  2. SIMULATION -- at each rebalance date, select with the live rules from
     config (gate, flow ranking, sector cap, position cap, cash floor), size
     by risk, then mark to market daily until the next one.

WHAT IT STILL CANNOT SHOW
-------------------------
The universe is the instruments listed today, so every historical window is
run on the survivors of that window. Companies that delisted are absent, and
they are absent precisely where it matters most -- the bad periods. Results
here are therefore optimistic, and the size of that bias is unknown.
"""

import argparse
import math
import sys

import pandas as pd

import config
import database
import moneyflow
import performance
import regime
from backtest import back_adjust
from backtest_combined import SCORING_WINDOW
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from ranking import rank_stocks


# Sessions between rebalances. Twenty is one trading month, and it is also the
# horizon the signal evidence was measured over.
DEFAULT_REBALANCE = 20

TEDPIX = "32097828799138957"


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# PHASE 1 -- THE SCORE PANEL
# ==============================

def _rebalance_dates(index_frame, start, end, every):
    """Session dates on which the basket is rebuilt."""

    dates = index_frame["date"]

    window = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]

    return list(window.iloc[::every])


def score_panel(instruments, dates, gate=None, params=None, verbose=True):
    """{date: {ins_code: reading}} using only data available on each date.

    One pass per symbol rather than one per (symbol, date): the indicator
    frame is computed once over the whole history and then read at each date,
    which is the difference between minutes and hours.
    """

    params = params or DEFAULT_PARAMS
    gate = config.TECHNICAL_GATE if gate is None else gate

    panel = {pd.Timestamp(d): {} for d in dates}

    date_list = [pd.Timestamp(d) for d in dates]

    warmup = max(required_bars(params), SCORING_WINDOW)

    scanned = 0

    for instrument in instruments:

        ins_code = str(instrument["ins_code"])

        bars = database.load_history_by_inscode(ins_code)

        if bars is None or len(bars) < warmup + 5:
            continue

        bars, _ = back_adjust(bars)

        bars = bars.reset_index(drop=True)
        bars["date"] = pd.to_datetime(bars["date"])

        flow_rows = database.load_client_type_history(ins_code)

        if flow_rows is not None and not flow_rows.empty:
            flow_rows = flow_rows.copy()
            flow_rows["date"] = pd.to_datetime(flow_rows["date"])
            flow_rows = flow_rows.sort_values("date").reset_index(drop=True)

            # Net retail flow as a share of the session's own turnover, so a
            # minnow and a giant with the same proportional flow read alike.
            buy = flow_rows["individual_buy_value"].astype(float)
            sell = flow_rows["individual_sell_value"].astype(float)
            inst_buy = flow_rows["institutional_buy_value"].astype(float)

            turnover = (buy + inst_buy).replace(0, float("nan"))

            flow_rows["net_pct"] = (buy - sell) / turnover * 100
        else:
            flow_rows = None

        try:
            enriched = calculate_indicators(bars, params)
        except Exception:
            continue

        scanned += 1

        if verbose and scanned % 100 == 0:
            print("  scored %d symbols" % scanned, flush=True)

        bar_dates = bars["date"]
        bar_highs = bars["high"] if "high" in bars.columns else bars["close"]

        for date in date_list:

            # Last bar on or before the rebalance date. Anything later is the
            # future and would make every result meaningless.
            position = bar_dates.searchsorted(date, side="right") - 1

            if position < warmup:
                continue

            window = enriched.iloc[max(0, position - SCORING_WINDOW + 1):position + 1]

            if len(window) < warmup:
                continue

            try:
                technical = rank_stocks(window)
            except Exception:
                continue

            score = technical.get("score")

            if score is None or score < gate:
                continue

            row = enriched.iloc[position]

            close = _finite(row.get("close"))
            atr = _finite(row.get("ATR"))

            if not close or close <= 0:
                continue

            flow_score = None
            relative_flow = None

            if flow_rows is not None:
                cut = flow_rows["date"].searchsorted(date, side="right")

                # Today's net flow against this symbol's OWN distribution.
                # 2% net buying is an event for a name that normally runs at
                # 0.5% and a quiet day for one that runs at 3%; the absolute
                # score cannot tell those apart.
                baseline_window = getattr(config, "FLOW_RELATIVE_BASELINE", 120)

                net = flow_rows["net_pct"].iloc[:cut].dropna()

                if len(net) >= baseline_window:
                    baseline = net.tail(baseline_window)
                    sigma = float(baseline.std())
                    if sigma and sigma > 0:
                        recent = float(net.tail(5).mean())
                        relative_flow = (recent - float(baseline.mean())) / sigma

                # FLOW_BASELINE_WINDOW, not FLOW_WINDOW. The latter is the
                # short averaging window used INSIDE the reading; slicing the
                # frame with it hands analyse() five sessions when it needs
                # FLOW_MIN_SESSIONS, so every score comes back unscored and
                # the whole basket silently empties.
                tail = flow_rows.iloc[
                    max(0, cut - config.FLOW_BASELINE_WINDOW):cut
                ]

                if len(tail) >= config.FLOW_MIN_SESSIONS:
                    reading = moneyflow.analyse(tail)
                    if reading.get("verdict") == moneyflow.SCORED:
                        flow_score = reading.get("score")

            # Clear air to the ceiling the market remembers. Computed directly
            # rather than through levels.overhead() because the measurement
            # found only this one horizon carries information, and the full
            # construction costs a swing scan per symbol per date.
            headroom = None

            window = getattr(config, "HEADROOM_WINDOW", 120)

            if position >= window:
                prior = bar_highs.iloc[position - window:position]
                ceiling = _finite(prior.max())
                if ceiling is not None:
                    headroom = (ceiling - close) / close * 100

            panel[date][ins_code] = {
                "symbol": instrument.get("symbol_fa"),
                "sector": (instrument.get("sector_name") or "").strip(),
                "technical": score,
                "flow": flow_score,
                "close": close,
                "atr": atr,
                "headroom": headroom,
                "relative_flow": relative_flow,
            }

    if verbose:
        print("  panel built from %d symbols" % scanned, flush=True)

    return panel


# ==============================
# PHASE 2 -- SELECTION
# ==============================

def _percentiles(readings, fields):
    """Percentile rank of each field among the candidates on one date.

    Ties share the average rank, and a symbol missing a field gets None for
    it rather than a middling default -- an absent reading is not an average
    one, and treating it as such is how a name with no data lands mid-table.
    """

    out = {code: {} for code in readings}

    for field in fields:

        pairs = [(code, reading.get(field)) for code, reading in readings.items()]

        live = [(code, value) for code, value in pairs
                if value is not None and value == value]

        if len(live) < 2:
            continue

        live.sort(key=lambda pair: pair[1])

        n = len(live)

        i = 0

        while i < n:
            j = i
            while j + 1 < n and live[j + 1][1] == live[i][1]:
                j += 1
            # Average rank across the tied block, scaled to 0-100.
            rank = (i + j) / 2.0 / (n - 1) * 100
            for k in range(i, j + 1):
                out[live[k][0]][field] = rank
            i = j + 1

    return out


def select(readings, size, max_per_sector, mode=None):
    """The basket for one date, under the live selection rules.

    Deliberately mirrors portfolio.py rather than reimplementing a simpler
    rule: a backtest of different logic than the one that runs would measure
    something nobody uses.
    """

    mode = mode or getattr(config, "SELECTION_MODE", "blend")

    minimum_headroom = getattr(config, "MIN_HEADROOM_PCT", None)

    # Percentile ranks are computed across the candidates available on THIS
    # date, so sigmas and percents become comparable and the ranking adjusts
    # itself when a whole market is stretched or compressed.
    percentiles = (_percentiles(readings, ("relative_flow", "headroom"))
                   if mode == "combined" else {})

    candidates = []

    for ins_code, reading in readings.items():

        # A share pressed against its own multi-month ceiling returned +2.4%
        # over 20 sessions against +7.4% for one with room. Unknown headroom
        # is not a failure -- a young listing has no 120-session high and is
        # judged on everything else.
        if minimum_headroom is not None:
            room = reading.get("headroom")
            if room is not None and room < minimum_headroom:
                continue

        if mode == "combined":

            ranks = percentiles.get(ins_code) or {}

            flow_rank = ranks.get("relative_flow")
            room_rank = ranks.get("headroom")

            # Both inputs are required. Scoring a name on one of them would
            # put it on a different scale from every other candidate, which
            # is how a partial reading quietly wins a ranking.
            if flow_rank is None or room_rank is None:
                continue

            rank_value = (
                config.COMBINED_WEIGHT_FLOW * flow_rank
                + config.COMBINED_WEIGHT_HEADROOM * room_rank
            )

        elif mode == "gate_then_relative":
            # The same shape as gate_then_flow with a different construction
            # of the same signal. Negative values are NOT excluded: below-
            # average retail buying is a real reading, and dropping it would
            # thin a pool that already empties the basket a fifth of the time.
            rank_value = reading.get("relative_flow")
            if rank_value is None:
                continue

        elif mode == "gate_then_flow":
            rank_value = reading.get("flow")
            if rank_value is None or rank_value <= 0:
                continue
        else:
            rank_value = reading.get("technical")

        candidates.append((ins_code, rank_value, reading))

    # Liquidity is unavailable in the panel, so ties fall back to the
    # technical score -- stated here because it differs from the live
    # TIEBREAK and could otherwise be mistaken for it.
    candidates.sort(key=lambda c: (c[1], c[2]["technical"]), reverse=True)

    picked = []
    per_sector = {}

    for ins_code, rank_value, reading in candidates:

        if len(picked) >= size:
            break

        sector = reading["sector"] or "?"

        if per_sector.get(sector, 0) >= max_per_sector:
            continue

        per_sector[sector] = per_sector.get(sector, 0) + 1

        picked.append((ins_code, reading))

    return picked


def _next_open(frame, date):
    """Opening price of the first session strictly after `date`.

    The tradeable price for a decision made on `date`. Returns None when
    there is no next session -- at the end of the window, or for an
    instrument that stopped trading, and in both cases the position simply is
    not opened rather than being filled at an invented price.
    """

    if frame is None or frame.empty:
        return None

    idx = frame["date"].searchsorted(pd.Timestamp(date), side="right")

    if idx >= len(frame):
        return None

    row = frame.iloc[idx]

    price = _finite(row.get("open")) or _finite(row.get("close"))

    return price if price and price > 0 else None


def _size_position(reading, equity, risk_pct, max_position_pct, stop_atr=None):
    """Shares to hold, sized so a stop-out costs risk_pct of equity.

    `stop_atr` overrides STOP_ATR_MULTIPLE so stop width can be swept without
    rebuilding the score panel, which does not depend on it.
    """

    close = reading["close"]
    atr = reading.get("atr")

    stop_atr = config.STOP_ATR_MULTIPLE if stop_atr is None else stop_atr

    if not close or close <= 0:
        return 0, None

    if atr and atr > 0:
        stop_distance = atr * stop_atr
    else:
        stop_distance = close * (config.PRICE_BAND_PCT * 2 / 100.0)

    if stop_distance <= 0:
        return 0, None

    risk_budget = equity * (risk_pct / 100.0)

    shares = int(risk_budget / stop_distance)

    ceiling = int(equity * (max_position_pct / 100.0) / close)

    shares = min(shares, ceiling)

    return max(0, shares), close - stop_distance


# ==============================
# PHASE 3 -- SIMULATION
# ==============================

def simulate(panel, dates, price_by_code, size=None, max_per_sector=None,
             equity=None, risk_pct=None, use_trailing=True, mode=None,
             cash_by_date=None, stop_atr=None, trail_atr=None,
             hold_index_on=None, index_returns=None, verbose=True):
    """Walk the basket forward, marking to market every session.

    `hold_index_on` is a {rebalance_date: bool} map. Where it is True the book
    leaves stock picking entirely and holds the index until the next
    rebalance. This is a different proposition from the cash target, which
    only shrinks the same basket: the measured picture is that this engine
    beats the index when the index falls and lags it badly when it rises, so
    the question worth testing is whether to be in the engine at all, not how
    much of it to hold.

    `cash_by_date` supplies a per-rebalance cash floor -- normally from
    regime.detect(as_of=) -- replacing the fixed MIN_CASH_PCT. It is applied
    in both directions: a rising cash target TRIMS existing positions rather
    than merely blocking new ones, because a defensive signal that only stops
    buying leaves the book fully exposed to the fall it just warned about.
    """

    size = size or config.PORTFOLIO_SIZE
    max_per_sector = (config.MAX_PER_SECTOR if max_per_sector is None
                      else max_per_sector)
    equity = equity or config.ACCOUNT_EQUITY
    risk_pct = config.RISK_PER_TRADE_PCT if risk_pct is None else risk_pct

    trail_atr = (config.TRAIL_ATR_MULTIPLE if trail_atr is None else trail_atr)

    cash = float(equity)
    positions = {}

    curve = []
    trades = []

    # One rate per side, from the same BacktestConfig backtesting/engine.py
    # charges. This used to apply config.TRANSACTION_COST_PCT -- already a
    # ROUND-TRIP figure, 1.04% -- on the buy AND again on the sale: 2.08% per
    # trade on the equity curve, while the trade rows deducted it once.
    from backtest_config import BacktestConfig

    _costs = BacktestConfig()
    buy_rate = _costs.buy_cost_rate()
    sell_rate = _costs.sell_cost_rate()

    def net_return(entry, sold_at):
        return ((sold_at * (1 - sell_rate)) / (entry * (1 + buy_rate))
                - 1) * 100

    def exit_price(ins_code, state):
        # A decision made on a session's close is executed at the NEXT open,
        # exactly like the buys below. These sales used to fill at the
        # decision close itself -- a price the decision did not exist for yet.
        return (_next_open(price_by_code.get(ins_code), date)
                or state.get("last", state["entry"]))

    rebalance_set = {pd.Timestamp(d) for d in dates}

    # Value of the index holding, when the switch has the book out of stock
    # picking. Kept separate from `cash` so the daily mark is unambiguous.
    index_value = 0.0
    in_index = False

    # Every session between the first and last rebalance date.
    all_dates = sorted({
        d for frame in price_by_code.values() for d in frame["date"]
        if pd.Timestamp(dates[0]) <= d <= pd.Timestamp(dates[-1])
    })

    for date in all_dates:

        # ---- the index holding compounds at the index's own return -------
        if in_index and index_value > 0 and index_returns is not None:
            daily = index_returns.get(date)
            if daily is not None and daily == daily:
                index_value *= (1 + daily)

        # ---- mark to market and apply stops ------------------------------
        for ins_code in list(positions):

            state = positions[ins_code]
            frame = price_by_code.get(ins_code)

            if frame is None:
                continue

            idx = frame["date"].searchsorted(date, side="right") - 1

            if idx < 0:
                continue

            bar = frame.iloc[idx]

            if pd.Timestamp(bar["date"]) != date:
                continue          # did not trade today; hold at last mark

            close = _finite(bar.get("close"))
            low = _finite(bar.get("low")) or close

            if close is None:
                continue

            state["last"] = close

            # The stop is tested BEFORE today's close can raise it. Ratcheting
            # first let a bar with a low of 97 and a close of 110 lift the stop
            # to 104 and then exit at 104 -- a fill that never existed.
            if low is not None and low <= state["stop"]:
                # A bar that OPENS through the stop fills at the open, not at
                # the stop: a 3% gap down does not stop at the level drawn.
                opened = _finite(bar.get("open")) or state["stop"]
                fill = min(opened, state["stop"])
                cash += state["shares"] * fill * (1 - sell_rate)
                trades.append({
                    "ins_code": ins_code, "symbol": state["symbol"],
                    "entry_date": state["entry_date"], "exit_date": str(date.date()),
                    "entry": state["entry"], "exit": fill,
                    "shares": state["shares"], "reason": "stop",
                    "return_pct": net_return(state["entry"], fill),
                })
                del positions[ins_code]
                continue

            if use_trailing and trail_atr > 0:
                state["peak"] = max(state.get("peak", close), close)
                # The trail is measured in the position's own ATR units, which
                # `atr_stop_distance` already carries divided by its own
                # multiple -- so a 3-ATR trail is three of those units.
                trail = state["peak"] - state["atr_unit"] * trail_atr
                state["stop"] = max(state["stop"], trail)

        # ---- rebalance ----------------------------------------------------
        if date in rebalance_set:

            # ---- the switch: pick the instrument before picking names ----
            if hold_index_on is not None:

                want_index = bool(hold_index_on.get(date, False))

                if want_index and not in_index:
                    # Leave stock picking entirely: sell the basket and buy
                    # the index with the proceeds.
                    for ins_code in list(positions):
                        state = positions[ins_code]
                        price = exit_price(ins_code, state)
                        cash += state["shares"] * price * (1 - sell_rate)
                        trades.append({
                            "ins_code": ins_code, "symbol": state["symbol"],
                            "entry_date": state["entry_date"],
                            "exit_date": str(date.date()),
                            "entry": state["entry"], "exit": price,
                            "shares": state["shares"], "reason": "switch to index",
                            "return_pct": net_return(state["entry"], price),
                        })
                        del positions[ins_code]

                    index_value = cash * (1 - buy_rate)
                    cash = 0.0
                    in_index = True

                elif not want_index and in_index:
                    cash = index_value * (1 - sell_rate)
                    index_value = 0.0
                    in_index = False

                if in_index:
                    # Nothing else happens on this date: the book is the index.
                    curve.append({
                        "date": date,
                        "equity": cash + index_value,
                        "cash": cash,
                        "positions": 0,
                        "cash_pct": 0.0,
                        "in_index": True,
                    })
                    continue

            readings = panel.get(date) or {}

            wanted = select(readings, size, max_per_sector, mode=mode)
            wanted_codes = {code for code, _ in wanted}

            # Exit what is no longer wanted.
            for ins_code in list(positions):
                if ins_code in wanted_codes:
                    continue
                state = positions[ins_code]
                price = exit_price(ins_code, state)
                cash += state["shares"] * price * (1 - sell_rate)
                trades.append({
                    "ins_code": ins_code, "symbol": state["symbol"],
                    "entry_date": state["entry_date"], "exit_date": str(date.date()),
                    "entry": state["entry"], "exit": price,
                    "shares": state["shares"], "reason": "rotated out",
                    "return_pct": net_return(state["entry"], price),
                })
                del positions[ins_code]

            # Portfolio value drives sizing, so a drawdown shrinks new
            # positions rather than betting the original equity forever.
            held = sum(s["shares"] * s.get("last", s["entry"])
                       for s in positions.values())
            total = cash + held

            cash_floor = config.MIN_CASH_PCT
            if cash_by_date is not None:
                supplied = cash_by_date.get(date)
                if supplied is not None:
                    cash_floor = supplied

            investable = total * (1 - cash_floor / 100.0)

            # Trim to the ceiling before buying. Selling a fraction of each
            # holding rather than closing whole names keeps the basket's
            # composition and only changes its size, which is what a cash
            # target is asking for.
            if held > investable and held > 0:

                keep = investable / held

                for ins_code in list(positions):
                    state = positions[ins_code]
                    price = exit_price(ins_code, state)
                    target_shares = int(state["shares"] * keep)
                    sold = state["shares"] - target_shares

                    if sold <= 0:
                        continue

                    cash += sold * price * (1 - sell_rate)

                    if target_shares <= 0:
                        trades.append({
                            "ins_code": ins_code, "symbol": state["symbol"],
                            "entry_date": state["entry_date"],
                            "exit_date": str(date.date()),
                            "entry": state["entry"], "exit": price,
                            "shares": state["shares"], "reason": "cash target",
                            "return_pct": net_return(state["entry"], price),
                        })
                        del positions[ins_code]
                    else:
                        state["shares"] = target_shares

                held = sum(s["shares"] * s.get("last", s["entry"])
                           for s in positions.values())

            # Risk sizing fixes the ratio between positions; it does not fix
            # how much of the book ends up deployed. Work out the scale that
            # brings the total to the target, and apply it to every new
            # position so their risk-derived proportions survive.
            scale = 1.0

            target_pct = getattr(config, "TARGET_INVESTED_PCT", None)

            if target_pct and wanted:

                risk_sized = 0.0

                for ins_code, reading in wanted:
                    if ins_code in positions:
                        continue
                    probe = _next_open(price_by_code.get(ins_code), date)
                    if probe is None:
                        continue
                    sized = dict(reading)
                    sized["close"] = probe
                    shares, _ = _size_position(
                        sized, total, risk_pct, config.MAX_POSITION_PCT,
                        stop_atr=stop_atr,
                    )
                    risk_sized += shares * probe

                room = total * (target_pct / 100.0) - held

                if risk_sized > 0 and room > risk_sized:
                    scale = min(room / risk_sized,
                                getattr(config, "MAX_EXPOSURE_SCALE", 3.0))

            for ins_code, reading in wanted:

                if ins_code in positions:
                    continue

                # Entry is the NEXT session's open, never this session's close.
                # Client-type figures for a session are published after it
                # closes, so buying at that close would be acting on a number
                # that did not exist yet -- a one-day look-ahead, and in a
                # strategy ranked entirely on flow it is the one place a
                # backtest can flatter itself beyond recognition.
                price = _next_open(price_by_code.get(ins_code), date)

                if price is None:
                    continue

                # Size on the price actually paid, so the risk budget means
                # what it says.
                sized = dict(reading)
                sized["close"] = price

                shares, stop = _size_position(
                    sized, total, risk_pct, config.MAX_POSITION_PCT,
                    stop_atr=stop_atr,
                )

                if not shares or stop is None:
                    continue

                if scale > 1.0:
                    # The stop is untouched: scaling changes how much is at
                    # risk, never where the exit sits.
                    ceiling = int(total * (config.MAX_POSITION_PCT / 100.0) / price)
                    shares = min(int(shares * scale), ceiling)

                if shares <= 0:
                    continue
                outlay = shares * price * (1 + buy_rate)

                if outlay > cash or (held + outlay) > investable:
                    # Shrink to what is affordable rather than skipping: a
                    # basket that silently holds eight names because two were
                    # unaffordable is not the basket being tested.
                    affordable = int(min(cash, investable - held) /
                                     (price * (1 + buy_rate)))
                    shares = max(0, min(shares, affordable))
                    outlay = shares * price * (1 + buy_rate)

                if shares <= 0:
                    continue

                cash -= outlay
                held += shares * price

                positions[ins_code] = {
                    "symbol": reading["symbol"],
                    "shares": shares,
                    "entry": price,
                    "last": price,
                    "peak": price,
                    "stop": stop,
                    # One ATR in rials, so a trail of N ATRs is unambiguous
                    # however wide the initial stop was set.
                    "atr_unit": (price - stop) / max(
                        1e-9, (config.STOP_ATR_MULTIPLE if stop_atr is None
                               else stop_atr)
                    ),
                    "entry_date": str(date.date()),
                }

        # ---- record ---------------------------------------------------------
        held = sum(s["shares"] * s.get("last", s["entry"])
                   for s in positions.values())

        total = cash + held + index_value

        curve.append({
            "date": date,
            "equity": total,
            "cash": cash,
            "positions": len(positions),
            "cash_pct": (cash / total * 100) if total else 100.0,
            "in_index": in_index,
        })

    return pd.DataFrame(curve), trades


# ==============================
# DRIVER
# ==============================

def run(start="2025-05-01", end="2026-08-11", rebalance=DEFAULT_REBALANCE,
        size=None, max_per_sector=None, equity=None, risk_pct=None,
        limit=None, use_trailing=True, mode=None, use_regime=False,
        stop_atr=None, trail_atr=None, regime_switch=False, verbose=True):
    """Full portfolio backtest with a benchmark comparison.

    `mode` overrides config.SELECTION_MODE for this run. Needed to reach the
    pre-2024 crashes at all: client-type data covers a few dozen instruments
    before 2024-09, so "gate_then_flow" has almost nothing to rank there and
    would report an empty basket as a result.
    """

    index_frame = database.load_index_history(TEDPIX)

    if index_frame is None or index_frame.empty:
        print("no index history — cannot benchmark")
        return None

    index_frame = index_frame.dropna(subset=["close"]).reset_index(drop=True)
    index_frame["date"] = pd.to_datetime(index_frame["date"])

    dates = _rebalance_dates(index_frame, start, end, rebalance)

    if len(dates) < 2:
        print("not enough rebalance dates in that window")
        return None

    # A historical universe: with the listings a board move retired, so a
    # past date sees the code that traded then (load_instruments).
    instruments = database.load_instruments(equities_only=True,
                                            with_superseded=True)

    if limit:
        instruments = instruments[:limit]

    if verbose:
        print("portfolio backtest %s .. %s" % (start, end))
        print("  %d rebalance dates every %d sessions, %d instruments"
              % (len(dates), rebalance, len(instruments)))

    hold_index_on = None

    if regime_switch:

        if verbose:
            print("  regime switch: index when bull, engine otherwise")

        hold_index_on = {}

        for date in dates:
            try:
                read = regime.detect(as_of=date)
            except Exception as exc:
                if verbose:
                    print("    %s: regime failed (%s)" % (date.date(), exc))
                continue

            if not read.get("available"):
                continue

            bull = read.get("regime") == regime.BULL

            hold_index_on[pd.Timestamp(date)] = bull

            if verbose:
                print("    %s  %-12s -> %s"
                      % (date.date(), read.get("regime"),
                         "INDEX" if bull else "engine"), flush=True)

    cash_by_date = None

    if use_regime:

        if verbose:
            print("  reading the market regime at %d dates" % len(dates))

        cash_by_date = {}

        for date in dates:
            try:
                read = regime.detect(as_of=date)
            except Exception as exc:
                if verbose:
                    print("    %s: regime failed (%s)" % (date.date(), exc))
                continue

            if read.get("available"):
                cash_by_date[pd.Timestamp(date)] = read.get("cash_target_pct")
                if verbose:
                    print("    %s  %-12s cash %s%%"
                          % (date.date(), read.get("regime"),
                             read.get("cash_target_pct")), flush=True)

    panel = score_panel(instruments, dates, verbose=verbose)

    # Prices for everything the panel ever proposes.
    wanted = {code for readings in panel.values() for code in readings}

    if verbose:
        print("  loading prices for %d instruments the panel selected" % len(wanted))

    price_by_code = {}

    for ins_code in wanted:
        frame = database.load_history_by_inscode(ins_code)
        if frame is None or frame.empty:
            continue
        frame, _ = back_adjust(frame)
        frame = frame.reset_index(drop=True)
        frame["date"] = pd.to_datetime(frame["date"])
        price_by_code[ins_code] = frame

    # Daily index returns, keyed by session, so the switch can compound the
    # index holding without re-deriving it inside the loop.
    index_series = index_frame.set_index("date")["close"]
    index_returns = index_series.pct_change().to_dict()

    curve, trades = simulate(
        panel, dates, price_by_code, size=size,
        max_per_sector=max_per_sector, equity=equity, risk_pct=risk_pct,
        use_trailing=use_trailing, mode=mode, cash_by_date=cash_by_date,
        stop_atr=stop_atr, trail_atr=trail_atr,
        hold_index_on=hold_index_on, index_returns=index_returns,
        verbose=verbose,
    )

    if curve.empty:
        print("no equity curve produced")
        return None

    curve = curve.set_index("date")

    returns = curve["equity"].pct_change().dropna()

    benchmark = index_frame.set_index("date")["close"]
    benchmark = benchmark.reindex(curve.index).ffill().pct_change().dropna()

    report = performance.from_daily_returns(
        returns, benchmark=benchmark, equity=float(curve["equity"].iloc[0])
    )

    return {
        "curve": curve,
        "trades": trades,
        "performance": report,
        "rebalance_dates": dates,
    }


def print_report(result):
    if not result:
        return

    curve = result["curve"]
    report = result["performance"]
    trades = result["trades"]

    start_equity = float(curve["equity"].iloc[0])
    end_equity = float(curve["equity"].iloc[-1])

    print()
    print("=" * 72)
    print("PORTFOLIO BACKTEST")
    print("=" * 72)
    print("  %s .. %s   %d sessions"
          % (curve.index[0].date(), curve.index[-1].date(), len(curve)))
    print("  equity %,.0f -> %,.0f   (%+.1f%%)".replace(",", "")
          % (start_equity, end_equity,
             (end_equity / start_equity - 1) * 100))
    print("  trades: %d" % len(trades))

    print()
    for line in performance.summary_lines(report):
        print("  " + line)

    bench = report.get("benchmark")

    print()
    print("=" * 72)
    print("VERSUS شاخص کل — the question that matters")
    print("=" * 72)

    if not bench:
        print("  no benchmark computed")
        return

    print("  strategy        %+8.2f%%" % bench["strategy_return_pct"])
    print("  index           %+8.2f%%" % bench["benchmark_return_pct"])
    print("  excess          %+8.2f%%" % bench["excess_return_pct"])
    print("  alpha (ann.)    %+8.2f%%" % bench.get("alpha_pct", float("nan")))
    print("  beta            %8.2f" % bench.get("beta", float("nan")))
    print("  info ratio      %8.2f" % bench.get("information_ratio", float("nan")))

    print()
    if bench["excess_return_pct"] > 0:
        print("  The basket beat owning the index over this window.")
    else:
        print("  The basket did NOT beat owning the index. Every per-trade")
        print("  statistic measured so far is consistent with this.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Portfolio-level backtest against شاخص کل."
    )
    parser.add_argument("--start", default="2025-05-01")
    parser.add_argument("--end", default="2026-08-11")
    parser.add_argument("--rebalance", type=int, default=DEFAULT_REBALANCE)
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument("--max-per-sector", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-trailing", action="store_true")
    parser.add_argument("--mode", default=None,
                        choices=["blend", "gate_then_flow", "combined",
                                 "gate_then_relative"],
                        help="override SELECTION_MODE; blend is the only one "
                             "reachable before 2024-09, when flow data is thin")
    parser.add_argument("--regime-switch", action="store_true",
                        dest="regime_switch",
                        help="hold the index while the regime reads bull and "
                             "run the engine otherwise")
    parser.add_argument("--regime", action="store_true",
                        help="drive the cash floor from regime.detect(as_of=) "
                             "instead of the fixed MIN_CASH_PCT")

    args = parser.parse_args(argv)

    result = run(
        start=args.start, end=args.end, rebalance=args.rebalance,
        size=args.size, max_per_sector=args.max_per_sector,
        limit=args.limit, use_trailing=not args.no_trailing,
        mode=args.mode, use_regime=args.regime,
        regime_switch=args.regime_switch,
    )

    print_report(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
