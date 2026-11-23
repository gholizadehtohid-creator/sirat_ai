"""The suggested asset allocation: how much in stocks, gold and fixed income.

WHY THIS COMES BEFORE THE STOCK BASKET
--------------------------------------
On 2026-11-05 the v2 stock basket was measured over 2024-09..2026-11: it lost
money in both windows while the index rose ~93% a year, its picks sat at or
below random selection, and it held 80-85% cash. Two preregistered experiments
then asked the question the project had never asked:

  E27  holding the index beat a 20% fixed-income fund, 2009-2023
       (43.5% vs 20% a year, p=0.007); a 200-session trend rule did not
       beat simply holding.
  E28  gold funds beat fixed income as the defensive part of the mix
       (70% index / 30% gold: Sharpe 1.87 vs 1.58, p=0.015) and on their own.

Those are the first results in this repository that passed their tests, and
both are about WHAT TO HOLD, not which stock to pick. So the panel leads with
an allocation; the stock basket stays below it, as something to paper-trade.

An allocation needs a handful of trades a year, which the user places by hand.
It does not need the order API no Iranian broker offers.

WHAT IS TESTED AND WHAT IS ONLY DESCRIBED
-----------------------------------------
Each profile carries `evidence`. "balanced" (70/30 gold) and "growth" (100%
index) are the arms the experiments accepted. "defensive" (50% index / 50%
fixed income) was not a hypothesis: it is shown because it had the shallowest
drawdown, and it is labelled as description. The history figures are
recomputed from the database on every call, so they move as sessions accrue;
the experiments' own numbers are frozen in backtest_output/E27_results.json
and E28_results.json.

Nominal rials throughout. Inflation was 87.9% y/y in 2026 and there is no CPI
series in the database, so nothing here says any profile beat inflation.
"""

import math

import numpy as np
import pandas as pd

import config
import database

SESSIONS_PER_YEAR = 240
FUND_RATE = 0.20                 # E27/E28's fixed-income assumption
INDEX_FUND_DRAG = 0.015          # an index fund's fees and tracking error
# What a rebalance costs TODAY: every sleeve here is held as fund units, and
# a fund unit pays no transfer tax (config.LIVE_FUND_ROUND_TRIP_PCT, 0.75%).
# It was 0.01039, the share round trip the old backtests assumed -- a tax
# these trades do not pay, and a commission that is no longer the rate (the
# review of 2026-11-23). Only the history this card recomputes reads it; E27
# and E28 keep their own figure in examples/allocation_study.py.
ROUND_TRIP = config.LIVE_FUND_ROUND_TRIP_PCT / 100.0
REBALANCE_SESSIONS = 60          # about three months, as E28 tested
DRIFT_BAND_PCT = 10.0            # or sooner, when a part drifts this far
GOLD_MIN_HISTORY = 60
GOLD_BAD_RETURN = 0.20

PROFILES = (
    {
        "key": "balanced",
        # Was «متعادل — پیشنهاد اصلی». "The main recommendation" claims more
        # than E28 measured: its edge was over a fixed-income fund ASSUMED to
        # pay 20%, and treasury bills pay about 44% today -- the card's own
        # banner says the edge over them is unproven (2026-11-23). The label
        # now says what the mix is; the evidence column says what was tested.
        "label": "متعادل — ۷۰٪ سهام، ۳۰٪ طلا",
        "weights": {"equity": 70, "gold": 30, "fixed": 0},
        "evidence": "tested",
        # Effect, not a p-value (the review panel, 2026-11-11): E28's
        # fixed-income leg had no variance, so the test was "gold improves
        # equity's Sharpe" over 2017-26, a window of rial devaluations only.
        "evidence_text": "آزمون E28 (۲۰۱۷ تا ۲۰۲۶): نسبت بازده به ریسک بالاتر از "
                         "۷۰٪ سهام + ۳۰٪ درآمد ثابت؛ آن دوره پر از جهش ارز بود "
                         "و طلا از آن سود برد",
    },
    {
        "key": "defensive",
        "label": "کم‌نوسان",
        "weights": {"equity": 50, "gold": 0, "fixed": 50},
        "evidence": "described",
        "evidence_text": "آزموده نشده — فقط کم‌عمق‌ترین افت تاریخی را داشت",
    },
    {
        "key": "growth",
        "label": "رشد — تمام سهام",
        "weights": {"equity": 100, "gold": 0, "fixed": 0},
        "evidence": "tested",
        # p=0.007 assumed independent 20-session blocks; their excess
        # returns are autocorrelated (rho ~0.3) and Newey-West puts p at
        # 0.024-0.040 (the review panel, 2026-11-11).
        "evidence_text": "آزمون E27: در ۱۴ سال (۲۰۰۹ تا ۲۰۲۳) سالانه حدود ۴۳٪ "
                         "در برابر ۲۰٪ صندوق درآمد ثابت، اسمی؛ بیشترین افت",
    },
)

SLEEVE_LABELS = {
    "equity": "صندوق شاخصی سهام",
    "gold": "صندوق طلا",
    "fixed": "صندوق درآمد ثابت",
}


def _per_session(annual):
    return (1.0 + annual) ** (1.0 / SESSIONS_PER_YEAR) - 1.0


def _index_returns():
    frame = database.load_index_history(config.INDICES["TEDPIX"]["insCode"])
    if frame is None or frame.empty:
        return None
    close = (frame.sort_values("date").drop_duplicates("date")
             .set_index("date")["close"].astype(float))
    return close.pct_change()


def _gold_returns(calendar):
    """Equal-weight daily return of the seasoned gold funds, as E28 built it."""

    import gold

    closes = {}
    for code in gold.gold_fund_codes():
        frame = database.load_history_by_inscode(code)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        closes[code] = (frame.drop_duplicates("date").set_index("date")["close"]
                        .astype(float))

    if not closes:
        return None

    panel = pd.DataFrame(closes).reindex(calendar)
    returns = panel.pct_change(fill_method=None)
    returns = returns.mask(returns.abs() > GOLD_BAD_RETURN)
    returns = returns.where(panel.notna().cumsum() >= GOLD_MIN_HISTORY)

    return returns.mean(axis=1)


def _mix(legs, weights):
    frame = pd.DataFrame(legs)
    target = np.array(weights, dtype=float)
    held = target.copy()
    out = []

    for i, row in enumerate(frame.to_numpy()):
        cost = 0.0
        if i > 0 and i % REBALANCE_SESSIONS == 0:
            cost = np.abs(held - target).sum() * ROUND_TRIP / 2
            held = target.copy()
        grown = held * (1 + row)
        value = grown.sum()
        out.append(value * (1 - cost) - 1)
        held = grown / value

    return pd.Series(out, index=frame.index)


def _stats(daily):
    wealth = (1 + daily).cumprod()
    years = len(daily) / SESSIONS_PER_YEAR

    # Worst 240-session stretch, not worst calendar year: the window starts
    # in September, and a part-year would pass for a "worst year".
    rolling = wealth / wealth.shift(SESSIONS_PER_YEAR) - 1

    return {
        "cagr_pct": round((wealth.iloc[-1] ** (1 / years) - 1) * 100, 1),
        "max_drawdown_pct": round(float((wealth / wealth.cummax() - 1).min())
                                  * 100, 1),
        "worst_12m_pct": round(float(rolling.min()) * 100, 1),
        "worst_12m_ending": str(rolling.idxmin().date()),
        "volatility_pct": round(float(daily.std())
                                * math.sqrt(SESSIONS_PER_YEAR) * 100, 1),
    }


def history():
    """Each profile's record over the window all three sleeves exist."""

    index = _index_returns()
    if index is None:
        return None

    gold_r = _gold_returns(index.index)
    if gold_r is None or gold_r.first_valid_index() is None:
        return None

    start = gold_r.first_valid_index()
    dates = index.index[index.index >= start]

    legs = {
        "equity": ((1 + index[dates].fillna(0.0))
                   * (1 - _per_session(INDEX_FUND_DRAG)) - 1),
        "gold": gold_r[dates].fillna(0.0),
        "fixed": pd.Series(_per_session(FUND_RATE), index=dates),
    }

    out = {"from": str(dates[0].date()), "to": str(dates[-1].date()),
           "profiles": {}}

    for profile in PROFILES:
        w = profile["weights"]
        names = [k for k in ("equity", "gold", "fixed") if w[k]]
        daily = _mix({k: legs[k] for k in names}, [w[k] / 100 for k in names])
        out["profiles"][profile["key"]] = _stats(daily)

    out["fixed_income"] = _stats(legs["fixed"])

    return out


def candidates(limit=3):
    """The most traded instruments for each sleeve, from the latest snapshot.

    Liquidity only -- no instrument is endorsed. An index fund that tracks
    TEDPIX closely matters more than one that trades a lot; the user should
    check the fund's tracking against the index before buying.
    """

    import gold

    conn = database._connect()
    rows = conn.execute("""
        SELECT i.ins_code, i.symbol_fa, i.name_fa, COALESCE(s.value, 0)
        FROM instruments i
        LEFT JOIN market_snapshot s ON s.ins_code = i.ins_code
        WHERE i.kind = 'fund'
    """).fetchall()
    conn.close()

    gold_codes = set(str(c) for c in gold.gold_fund_codes())

    def sleeve_of(code, name):
        name = name or ""
        if str(code) in gold_codes:
            return "gold"
        if "شاخص" in name:
            return "equity"
        if "ثابت" in name or name.strip().endswith("-د"):
            return "fixed"
        return None

    import opportunities

    picked = {"equity": [], "gold": [], "fixed": []}

    for code, symbol, name, value in sorted(rows, key=lambda r: -(r[3] or 0)):
        sleeve = sleeve_of(code, name)
        if not sleeve:
            continue
        item = {"symbol": symbol, "name": name, "value": float(value or 0)}
        if sleeve == "equity":
            item["tracks"], item["broad"] = opportunities.index_tracked(symbol)
        picked[sleeve].append(item)

    # The funds that follow the broad index first (most traded first within
    # each group): by turnover alone the list led with the equal-weight fund
    # and the top-30 fund, neither of which is the index E27 tested
    # (opportunities.BROAD_INDEX_FUNDS, 2026-11-23).
    picked["equity"].sort(key=lambda f: not f["broad"])

    return {sleeve: items[:limit] for sleeve, items in picked.items()}


def plan(equity=None):
    """The allocation report the panel renders."""

    equity = float(equity or config.ACCOUNT_EQUITY)

    profiles = []
    for profile in PROFILES:
        amounts = {k: round(equity * pct / 100.0, 0)
                   for k, pct in profile["weights"].items() if pct}
        profiles.append(dict(profile, amounts=amounts))

    return {
        "equity": equity,
        "currency": config.CURRENCY,
        "profiles": profiles,
        "sleeve_labels": SLEEVE_LABELS,
        "history": history(),
        "candidates": candidates(),
        "rebalance": {
            "sessions": REBALANCE_SESSIONS,
            "drift_band_pct": DRIFT_BAND_PCT,
            "text": "هر حدود سه ماه (۶۰ جلسه)، یا زودتر اگر سهم یک بخش بیش "
                    "از ۱۰ واحد درصد از هدف فاصله گرفت، دوباره به وزن‌های "
                    "هدف برگردانید.",
        },
        "notes": [
            "ارقام اسمی (ریالی) است. تورم ۱۴۰۵ حدود ۸۸٪ است؛ هیچ‌کدام از این "
            "ترکیب‌ها لزوماً از تورم جلو نزده‌اند.",
            "بیشترین افت تاریخی را جدی بگیرید: کسی که در افت می‌فروشد، بازده "
            "این جدول را نمی‌گیرد.",
            "سبد سهام انتخابی بات (پایین‌تر) در آزمون ۱۴۰۳–۱۴۰۵ زیان داد و "
            "از انتخاب تصادفی بهتر نبود؛ تا اثبات روی داده‌ی آینده فقط برای "
            "معامله‌ی کاغذی است.",
            "نمادهای پیشنهادی فقط بر اساس نقدشوندگی‌اند، نه توصیه‌ی خرید. "
            "پیش از خرید صندوق شاخصی، هم‌خوانی بازده آن با شاخص کل را بررسی "
            "کنید.",
        ],
        "evidence": ["backtest_output/E27_results.json",
                     "backtest_output/E28_results.json"],
        # What dealing costs today, for the today card to say beside its
        # amounts (config.LIVE_*): a fund unit pays no transfer tax.
        "costs": {
            "share_round_trip_pct": config.LIVE_ROUND_TRIP_PCT,
            "fund_round_trip_pct": config.LIVE_FUND_ROUND_TRIP_PCT,
            "buy_pct": config.LIVE_BUY_COST_PCT,
            "share_sell_pct": config.LIVE_SELL_COST_PCT,
            "fund_sell_pct": config.LIVE_FUND_SELL_COST_PCT,
        },
    }
