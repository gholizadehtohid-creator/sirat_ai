"""The v2 decision engine, end to end: market -> factors -> risk -> signals -> book.

WHAT THIS IS, AND WHY IT IS NOT advisor.py
------------------------------------------
`advisor.py` runs the v1 chain and is unchanged: regime -> two-stage screen ->
combined score -> portfolio -> risk analysis. It stays the reference, because it
is the chain every measured result in this project came from.

This is the same shape wired through the v2 engines instead, so the two can be
run side by side on the same day and compared:

    regime_engine       what the market permits, and the trading mode
        |
    fundamental_engine  from the WEEKLY STORE, never from codal.ir
        |
    factor_engine       stock factors -> Swing Score -> BUY/HOLD/SELL/AVOID
        |
    risk_engine         may this be opened, and at what size
        |
    trade_manager       entry, stop, two targets, reward/risk, duration
        |
    portfolio_engine    which of them fit the limits, one per sector first

NOTHING HERE TOUCHES THE NETWORK. Prices come from the stored history and
fundamentals from sirat_fundamentals.db. A missing fundamental store degrades the
run to a five-factor score with the fundamental weight renormalised away, which is
reported rather than silent.

THE OUTPUT IS A PROPOSAL, NOT AN ORDER. Every number is a score, an estimate or a
size -- and the risk figures are floors, not ceilings, for the reason risk_engine
states in every report.
"""

import argparse
import math
import sys
import time

import config
import data_pipeline
import database
import factor_engine
import fundamental_db
import fundamental_engine
import portfolio_engine
import regime_engine
import risk_engine
import trade_manager
from indicators import DEFAULT_PARAMS, required_bars


# Bars of history handed to the factor engine. The longest momentum horizon is
# 120 sessions and the indicators need their own warm-up on top of that.
#
# Defined in data_pipeline now rather than here, because the backtest has to use
# the same number: a recursive indicator computed over a different span is a
# different indicator, and two of them made the same name score differently in
# the panel and in the backtest.
HISTORY_BARS = data_pipeline.WINDOW_BARS


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ==============================
# INPUTS
# ==============================

def index_closes(key="TEDPIX"):
    """The index series momentum measures relative strength against."""

    code = config.INDICES.get(key, {}).get("insCode")

    if not code:
        return None

    frame = database.load_index_history(code)

    if frame is None or frame.empty or "close" not in frame.columns:
        return None

    return frame["close"].tolist()


def sector_medians(instruments):
    """{sector: median multiples} from the fundamental store.

    Computed once for the whole run. A per-symbol query would be ~800 round trips
    for a figure that is a property of the sector, not of the name.

    One record per company. A point-in-time universe also holds the listings
    a board move retired, each marked with its live code as "successor"
    (pit.instruments). The retired code keeps its own statements and the
    repair copies them to the live code (seed_import._carry_over), so the
    swing backtest counted such a company twice in its sector's median
    (found 2026-11-11). The live code's record is used when the universe
    holds one, else the retired code's -- the newest, after two moves. The
    panel's universe is live codes only, and reads exactly as before.
    """

    latest = fundamental_db.load_latest_metrics()

    if not latest:
        return {}

    chosen = {}

    for code, record in latest.items():

        meta = instruments.get(str(code))

        if not meta:
            continue

        company = str(meta.get("successor") or code)

        # The live code first, then the newer statement.
        rank = (str(code) == company, str(record.get("period_end") or ""))

        if company not in chosen or rank > chosen[company][0]:
            chosen[company] = (rank, meta, record)

    by_sector = {}

    for _, meta, record in chosen.values():

        sector = (meta.get("sector") or "?").strip() or "?"

        by_sector.setdefault(sector, []).append(record)

    return {
        sector: fundamental_engine.sector_medians(rows)
        for sector, rows in by_sector.items()
    }


def _instruments():
    """{ins_code: {symbol, name, sector}} for every equity."""

    out = {}

    for row in database.load_instruments(equities_only=True):

        code = str(row.get("ins_code"))

        # A partly paid share («... ۵۰٪ تادیه») owes the unpaid part of its
        # par on call: its price is not a full share's, and the basket's #1
        # pick was one (وفردا, the review panel, 2026-11-11).
        name = row.get("name_fa") or row.get("name") or ""
        if "تادیه" in name or "تأدیه" in name:
            continue

        out[code] = {
            "symbol": row.get("symbol_fa") or row.get("symbol"),
            "name": row.get("name_fa") or row.get("name"),
            "sector": (row.get("sector_name") or "").strip(),
        }

    return out


# ==============================
# ONE NAME
# ==============================

def evaluate(ins_code, meta, frame, regime=None, medians=None, equity=None,
             positions=None, held=False, index_series=None, fundamental=None,
             risk_pct=None, shares=None, band_pct=None):
    """One name, all the way to a signal. Returns None when it cannot be scored.

    `band_pct`, the name's daily price band from today's market watch, is
    passed by the live scan only (a backtest has no point-in-time watch), so
    a close at the top of the band blocks a new entry (_entry_blocker).

    `fundamental` is the ONE input a historical caller cannot read the same way
    a live one does. The store holds the newest filing for a company; a backtest
    standing on a past date needs the newest filing THAT DATE could see, which
    only backtesting/pit.py can answer. So it may be injected -- and when it is
    not, this reads the store exactly as it always did.

    Nothing else is injectable, deliberately. Every other reading is derived
    from `frame`, so a caller that hands in a point-in-time frame gets a
    point-in-time answer from the same code the live panel runs.
    """

    if frame is None or frame.empty:
        return None

    last = frame.iloc[-1]

    close = _finite(last.get("close"))

    if not close or close <= 0:
        return None

    closes = frame["close"]

    volatility = None

    if len(closes) > 5:
        deviation = closes.tail(61).pct_change().std()
        volatility = _finite(deviation
                             * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100)

    adv = None

    if "value" in frame.columns:
        adv = _finite(frame["value"].tail(20).mean())

    reading = {
        "ins_code": ins_code,
        "symbol": meta.get("symbol"),
        "name": meta.get("name"),
        "sector": meta.get("sector") or "?",
        "close": close,
        "atr": _finite(last.get("ATR")),
        "atr_pct": _finite(last.get("ATR_PCT")),
        "adx": _finite(last.get("ADX")),
        "adv": adv,
        "volatility_60": volatility,
        "entry_blocker": _entry_blocker(frame, band_pct),
    }

    if fundamental is None:
        # `price` re-strikes the valuation multiples at today's close through
        # the same function the backtest uses. Without it the panel scored P/E
        # against whatever price ruled when the collector last ran, which
        # disagreed with the backtest for 203 of 284 covered companies and
        # always in the flattering direction.
        fundamental = fundamental_engine.evaluate_stored(
            ins_code, medians=(medians or {}).get(reading["sector"]),
            price=close, sector=reading["sector"], shares=shares)

    if fundamental.get("verdict") != fundamental_engine.SCORED:
        # Kept rather than discarded: "no filing in the store" is a fact about
        # this project's collection, and the factor blend renormalises around it.
        fundamental_for_blend = None
    else:
        fundamental_for_blend = fundamental

    result = factor_engine.evaluate_symbol(
        frame=frame,
        index_closes=index_series,
        regime=regime,
        fundamental=fundamental_for_blend,
        held=held,
    )

    decision = result["decision"]

    risk = risk_engine.assess(reading, equity=equity, positions=positions,
                              regime=regime, risk_pct=risk_pct)

    # The risk score is a gate as well as a report, so the decision is re-run
    # with it. Cheap, and the alternative is a BUY the sizing engine will refuse
    # -- a signal that looks actionable and is not.
    if decision["action"] in (factor_engine.BUY, factor_engine.HOLD):
        decision = factor_engine.decide(
            result["swing"],
            technical=result["swing"]["factors"].get(factor_engine.TECHNICAL),
            regime=regime, risk_score=risk.get("risk_score"),
            fundamental=fundamental_for_blend, held=held,
        )

    overhead = _overhead(frame, close)

    signal = trade_manager.plan(reading, decision, risk,
                               overhead_pct=overhead, held=held)

    signal["swing"] = result["swing"]
    signal["fundamental_verdict"] = fundamental.get("verdict")
    signal["fundamental_reason"] = fundamental.get("reason")
    signal["fundamental_flags"] = [f["flag"] for f in fundamental.get("flags") or []]
    signal["readings"] = result["readings"]
    signal["position_value"] = signal.get("position_value") or 0.0

    return signal


# How close to the top of its band a close counts as "at the limit": the
# band is ±3% on most boards, and a close of +2.7% or more on the session high
# is the buy queue in all but name.
BAND_TOP_TOLERANCE_PCT = 0.3


def _entry_blocker(frame, band_pct=None):
    """Why this name cannot be BOUGHT at the price the plan quotes, or None.

    Two conditions, both read off the last bar and neither a threshold:

    NO TRADE IN THE LAST SESSION. TSETMC publishes a bar for a halted name
    every day, the previous close carried forward at zero volume -- so a
    halted name has a fresh bar, a flat series, a low ATR and therefore a
    flattering risk score. The plan would quote an entry nobody can fill.

    A BUY QUEUE (صف خرید). The momentum factor rewards a close at the upper
    limit, which is exactly the close nobody could buy at. The detector is
    E14's, from smart_money.microstructure, unchanged.

    Entry only. A held name is never blocked from anything by this.
    """

    if "volume" in frame.columns:
        volume = _finite(frame["volume"].iloc[-1])

        if volume is not None and volume <= 0:
            return "در آخرین جلسه معامله‌ای نداشته — نماد متوقف یا بی‌معامله است"

    from smart_money import microstructure

    state = microstructure.locks(frame)

    if state and state.get("locked_up_now"):
        return state.get("reason")

    # CLOSED AT THE TOP OF THE BAND, without a full-day lock. Found
    # 2026-11-11: 9 of the basket's 10 names had closed +2.8% to +3.0% on a
    # ±3% band, after +17% to +97% in 60 sessions; the next morning's buy
    # queue is where a plan priced at that close would have to wait. Live
    # scan only (band_pct comes from today's watch).
    if band_pct and band_pct > 0 and len(frame) >= 2 and             {"close", "high"} <= set(frame.columns):
        close = _finite(frame["close"].iloc[-1])
        before = _finite(frame["close"].iloc[-2])
        high = _finite(frame["high"].iloc[-1])
        if close and before and high and before > 0:
            change = (close / before - 1) * 100
            if change >= band_pct - BAND_TOP_TOLERANCE_PCT and                     close >= high * 0.997:
                return ("در آخرین جلسه در سقف دامنه‌ی نوسان (+%.1f٪) بسته شد — "
                        "خرید در قیمت پایانی عملاً در صف خرید است" % change)

    return None


def _bands():
    """{ins_code: daily band in %} from the stored market watch."""

    try:
        conn = database._connect()
        rows = conn.execute("SELECT ins_code, limit_up, prev_close "
                            "FROM market_snapshot").fetchall()
        conn.close()
    except Exception:                                   # noqa: BLE001
        return {}
    out = {}
    for code, limit_up, prev in rows:
        if limit_up and prev and prev > 0 and limit_up > prev:
            out[str(code)] = round((limit_up / prev - 1) * 100, 2)
    return out


# The constructions in levels.overhead() that represent a price the market has
# ACTUALLY TRADED, as opposed to a level someone drew.
#
# This distinction decides how often a target gets capped, so it is worth being
# explicit about. levels.overhead()['nearest_pct'] is the nearest of every
# construction it knows, and that set includes Fibonacci EXTENSIONS -- 1.272,
# 1.618, 2.0 and 2.618 of the last swing -- which are projected targets sitting
# above the price by construction, and Ichimoku spans, which are moving averages
# plotted forward. Treating those as overhead supply caps almost every target on
# a rising market and would turn most BUY signals into AVOID for a level nobody
# has ever transacted at.
#
# What caps a real move is a price where holders are waiting to get out even: a
# prior pivot high, or a rolling high the market has already refused. Those are
# the only sources read here.
TRADED_LEVELS = ("pivot", "high_60", "high_120", "high_250")


def _overhead(frame, close):
    """Distance to the nearest price the market has actually refused, or None.

    Wrapped in a try: levels.py works on swings and a thin or oddly shaped series
    can leave it with nothing to measure. A failure to find a ceiling must not
    stop a name being scored -- it means the target is not capped, which is the
    same outcome as there being no ceiling.
    """

    try:
        import levels
        result = levels.overhead(frame, price=close)
    except Exception:
        return None

    by_source = (result or {}).get("by_source") or {}

    gaps = [
        value for key, value in by_source.items()
        if key in TRADED_LEVELS and _finite(value) is not None and value > 0
    ]

    return min(gaps) if gaps else None


# ==============================
# THE RUN
# ==============================

def scan(equity=None, size=None, max_per_sector=None, limit=None,
         positions=None, verbose=True, params=None, min_action=None,
         risk_pct=None, regime=None):
    """Score the market and build the proposed book. Returns everything computed.

    `regime` is a regime_engine.assess() already read for the same data -- the
    panel's regime card and its book were two 20 s reads of one thing (the
    full test, 2026-11-12). None reads it here, as every other caller does.
    """

    params = params or DEFAULT_PARAMS

    equity = _finite(equity) or config.ACCOUNT_EQUITY

    started = time.time()

    # ---- 1. the market ----------------------------------------------------
    regime = regime if regime is not None else regime_engine.assess()

    if verbose:
        print("=" * 78)
        print("SIRAT-AI — موتور تصمیم سوئینگ / SWING DECISION ENGINE")
        print("=" * 78)
        print("\n1. وضعیت بازار")
        print("   %s" % (regime.get("label") or regime.get("reason")))
        print("   حالت معاملاتی   %s" % regime.get("mode_label"))
        print("   امتیاز رژیم     %s" % regime.get("score"))
        print("   حداکثر سرمایه‌گذاری %s%%" % regime.get("max_invested_pct"))

    # ---- 2. the policy ----------------------------------------------------
    policy = portfolio_engine.from_config()

    if size:
        policy = policy.replace(max_positions=int(size))

    if max_per_sector:
        policy = policy.replace(max_per_sector=int(max_per_sector))

    policy = policy.for_regime(regime)

    # ---- 3. the universe --------------------------------------------------
    instruments = _instruments()

    codes = sorted(instruments)

    if limit:
        codes = codes[:int(limit)]

    store = fundamental_db.exists()

    medians = sector_medians(instruments) if store else {}

    index_series = index_closes()

    if verbose:
        print("\n2. جهان سرمایه‌گذاری")
        print("   %d نماد" % len(codes))
        print("   پایگاه بنیادی   %s"
              % ("موجود — %s" % fundamental_db.path() if store
                 else "موجود نیست — عامل بنیادی از ترکیب حذف می‌شود"))
        print("   شاخص مرجع       %s"
              % ("موجود" if index_series else "موجود نیست — قدرت نسبی محاسبه نمی‌شود"))

    warmup = max(required_bars(params), max(config.MOMENTUM_HORIZONS) + 1)

    # Which names are already owned. This is what makes SELL reachable at all:
    # factor_engine.decide() answers a different question for a held name, and
    # without the flag every position in the book would be re-evaluated as a
    # fresh entry -- so a holding that had fallen apart would come back as AVOID,
    # which reads as "do not buy it" rather than "get out of it".
    held_codes = {
        str(position.get("ins_code"))
        for position in (positions or [])
        if position.get("ins_code") is not None
    }

    # A held name must be scored even when it would be skipped as a candidate,
    # so the holdings are always in the universe regardless of --limit.
    for code in held_codes:
        if code in instruments and code not in codes:
            codes.append(code)

    signals = []

    scanned = 0
    skipped = 0

    # Today's share counts, read once: evaluate_stored() re-strikes each P/E
    # at today's close on today's shares, and left to itself it opened a
    # fresh connection per name (3-20 ms each, seconds over the market).
    try:
        import fundamental_outlook
        snapshot_shares = fundamental_outlook.exchange_shares()
    except Exception:                                           # noqa: BLE001
        snapshot_shares = {}
    bands = _bands()            # today's price band per name, for the entry check

    for number, code in enumerate(codes, start=1):

        # The shared builder: back-adjusted for capital increases and windowed
        # to the same span the backtest uses. Before this, the panel scored raw
        # prices and disagreed with the backtest on any name that had raised
        # capital inside the momentum window.
        frame = data_pipeline.frame(code, params=params)

        if frame is None or frame.empty:
            skipped += 1
            continue

        signal = evaluate(code, instruments[code], frame, regime=regime,
                          medians=medians, equity=equity, positions=positions,
                          index_series=index_series,
                          held=code in held_codes, risk_pct=risk_pct,
                          shares=snapshot_shares.get(str(code)),
                          band_pct=bands.get(str(code)))

        if signal is None:
            skipped += 1
            continue

        scanned += 1

        signals.append(signal)

        if verbose and scanned % 200 == 0:
            print("   %d/%d نماد، %.0f ثانیه"
                  % (number, len(codes), time.time() - started), flush=True)

    # ---- 4. rank and allocate ---------------------------------------------
    #
    # A held name can never appear here: decide() returns HOLD or SELL for one,
    # never BUY. The filter is on the action rather than on the held set for that
    # reason -- one rule, enforced in one place.
    buys = [s for s in signals if s["action"] == factor_engine.BUY]

    holdings = [s for s in signals if s.get("held")]

    # Ranked by Swing Score, tie-broken by liquidity. Liquidity is used for the
    # tiebreak because it is measured rather than modelled -- the same reason
    # backtesting/strategy.py breaks its ties on it.
    buys.sort(key=lambda s: ((s.get("swing_score") or 0),
                             (s.get("readings", {}).get("liquidity") or {})
                             .get("score") or 0),
              reverse=True)

    book = portfolio_engine.allocate(policy, buys, equity,
                                     positions=positions)

    out = {
        "regime": regime,
        "policy": policy.as_dict(),
        "equity": equity,
        "signals": signals,
        "buys": buys,
        "holdings": holdings,
        "book": book,
        "scanned": scanned,
        "skipped": skipped,
        "fundamental_store": store,
        "seconds": round(time.time() - started, 1),
        "actions": _action_counts(signals),
    }

    if verbose:
        print_report(out, min_action=min_action)

    return out


def _action_counts(signals):

    counts = {action: 0 for action in factor_engine.ACTIONS}

    for signal in signals:
        action = signal.get("action")
        if action in counts:
            counts[action] += 1
        else:
            counts.setdefault("NONE", 0)
            counts["NONE"] += 1

    return counts


# ==============================
# THE REPORT
# ==============================

def print_report(out, min_action=None):

    print("\n3. نتیجه‌ی غربال   (%d نماد در %s ثانیه، %d رد شده)"
          % (out["scanned"], out["seconds"], out["skipped"]))

    for action in factor_engine.ACTIONS:
        print("   %-6s %s   %d"
              % (action, factor_engine.ACTION_LABELS[action],
                 out["actions"].get(action, 0)))

    coverage = [s["swing"]["coverage"] for s in out["signals"]
                if s.get("swing") and s["swing"].get("coverage") is not None]

    if coverage:
        print("\n   میانگین پوشش مدل   %.0f%% از عوامل امتیازدهنده"
              % (sum(coverage) / len(coverage) * 100))

    scored = [s for s in out["signals"] if s.get("swing_score") is not None]

    if not scored:
        print("\nهیچ نمادی امتیاز سوئینگ نگرفت — پوشش داده کافی نیست.")
        return

    print("\n\n4. برترین کاندیداها")
    print("-" * 78)
    print("   %-10s %6s %6s %6s %6s %6s %6s  %s"
          % ("نماد", "سوئینگ", "رژیم", "مومنت", "تکنیک", "بنیاد", "نقد", "تصمیم"))

    top = sorted(scored, key=lambda s: -(s.get("swing_score") or 0))[:20]

    for signal in top:

        factors = signal["swing"]["factors"]

        def show(key):
            value = factors.get(key)
            return "—" if value is None else "%.0f" % value

        print("   %-10s %6s %6s %6s %6s %6s %6s  %s"
              % (signal.get("symbol") or "?",
                 signal.get("swing_score"),
                 show(factor_engine.REGIME), show(factor_engine.MOMENTUM),
                 show(factor_engine.TECHNICAL), show(factor_engine.FUNDAMENTAL),
                 show(factor_engine.LIQUIDITY),
                 factor_engine.ACTION_LABELS.get(signal["action"], "—")))

    # ---- existing holdings ------------------------------------------------
    holdings = out.get("holdings") or []

    if holdings:
        print("\n\n5. موقعیت‌های موجود")
        print("-" * 78)
        print("   %-10s %6s %8s  %s" % ("نماد", "سوئینگ", "تصمیم", "دلیل"))

        for signal in sorted(holdings,
                             key=lambda s: (s["action"] != factor_engine.SELL,
                                            -(s.get("swing_score") or 0))):
            print("   %-10s %6s %8s  %s"
                  % (signal.get("symbol") or "?",
                     signal.get("swing_score") if signal.get("swing_score")
                     is not None else "—",
                     factor_engine.ACTION_LABELS.get(signal["action"], "—"),
                     (signal.get("reasons") or ["—"])[0]))

    # ---- the book ---------------------------------------------------------
    accepted = out["book"]["accepted"]

    print("\n\n%d. سبد پیشنهادی" % (6 if holdings else 5))
    print("-" * 78)

    if not accepted:
        print("   هیچ موقعیتی باز نمی‌شود.")

        reasons = {}

        for row in out["book"]["rejected"]:
            key = row["reason_key"]
            reasons[key] = reasons.get(key, 0) + 1

        for key, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print("   %-22s %d" % (key, count))

        if not out["book"]["rejected"]:
            print("   دلیل: هیچ سیگنال خریدی تولید نشد.")
    else:
        for signal in accepted:
            print()
            for line in trade_manager.describe(signal):
                print("   " + line)

        total = sum(row["allocated_value"] for row in accepted)

        print("\n   %d موقعیت، %.1f%% سرمایه، %.1f%% نقد"
              % (len(accepted), total / out["equity"] * 100,
                 100 - total / out["equity"] * 100))

    if not out["fundamental_store"]:
        print("\n\nتوجه: پایگاه بنیادی موجود نیست. امتیاز سوئینگ روی پنج عامل "
              "محاسبه شده\nاست. برای ساختنش:  python fundamental_db.py "
              "--import-market-db")

    print("\n" + risk_engine.CAVEAT)


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="The v2 swing decision engine: score the market and propose "
                    "a book.")

    parser.add_argument("--equity", type=float, default=None,
                        help="account equity in rials (default config.ACCOUNT_EQUITY)")
    parser.add_argument("--size", type=int, default=None,
                        help="maximum positions")
    parser.add_argument("--max-per-sector", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the universe, for a quick run")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    result = scan(equity=args.equity, size=args.size,
                  max_per_sector=args.max_per_sector, limit=args.limit,
                  verbose=not args.quiet)

    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
