"""E27: does simply holding the market beat a fixed-income fund?

Implements backtest_output/E27_preregistration.json exactly; read that first.
Every number this prints was committed to before the script was written.

    python examples/allocation_study.py

H1  always in the index (TEDPIX, dividends included), less a 1.5%/yr fund fee
H2  in the index while it closes above its 200-session average, else in a
    fixed-income fund; the signal at a close is executed at the NEXT close,
    half the 1.039% round trip charged per switch

One choice the preregistration did not make, made before running: the
200-session average does not exist for TEDPIX's first 200 sessions (from
2008-12-04), so until it does H2 holds what H1 holds. That can only shrink
the difference between them.
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_config                                          # noqa: E402
import config                                                   # noqa: E402
import database                                                 # noqa: E402

SESSIONS_PER_YEAR = 240
BLOCK = 20                       # sessions per "month"
FUND_RATE = 0.20
FUND_SENSITIVITY = (0.15, 0.25)
HOLDING_DRAG = 0.015
ROUND_TRIP = 0.01039
SMA = 200
ALPHA = 0.025

WINDOWS = {
    "primary": ("2009-01-01", "2023-02-28"),
    "secondary": ("2023-03-01", "2026-11-04"),
}

OUT = os.path.join(backtest_config.OUTPUT_DIR, "E27_results.json")


def per_session(annual):
    return (1.0 + annual) ** (1.0 / SESSIONS_PER_YEAR) - 1.0


def series(name):
    frame = database.load_index_history(config.INDICES[name]["insCode"])
    frame = frame.sort_values("date").drop_duplicates("date")
    return frame.set_index("date")["close"].astype(float)


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def t_sf(t, df):
    """One-sided p for a t statistic; normal beyond df 100 (here df > 40)."""
    try:
        from scipy import stats
        return float(stats.t.sf(t, df))
    except ImportError:
        return norm_sf(t)


def blocks(daily):
    """Compound daily returns into consecutive 20-session blocks."""
    values = daily.to_numpy()
    n = len(values) // BLOCK
    return np.array([np.prod(1 + values[i * BLOCK:(i + 1) * BLOCK]) - 1
                     for i in range(n)])


def stats_of(daily, fund_daily):
    wealth = (1 + daily).cumprod()
    years = len(daily) / SESSIONS_PER_YEAR
    cagr = wealth.iloc[-1] ** (1 / years) - 1
    excess = daily - fund_daily
    sharpe = (excess.mean() / excess.std() * math.sqrt(SESSIONS_PER_YEAR)
              if excess.std() > 0 else None)
    drawdown = float((wealth / wealth.cummax() - 1).min())
    return {
        "total_pct": round((wealth.iloc[-1] - 1) * 100, 1),
        "cagr_pct": round(cagr * 100, 2),
        "vol_pct": round(daily.std() * math.sqrt(SESSIONS_PER_YEAR) * 100, 2),
        "sharpe_vs_fund": None if sharpe is None else round(float(sharpe), 3),
        "max_drawdown_pct": round(drawdown * 100, 2),
    }


def memmel(a, b):
    """One-sided p that Sharpe(a) > Sharpe(b), Jobson-Korkie with Memmel."""
    sa, sb = a.mean() / a.std(), b.mean() / b.std()
    rho = float(np.corrcoef(a, b)[0, 1])
    t = len(a)
    variance = (2 * (1 - rho) + 0.5 * (sa ** 2 + sb ** 2
                                       - 2 * sa * sb * rho ** 2)) / t
    z = (sa - sb) / math.sqrt(variance)
    return float(z), norm_sf(z), float(sa), float(sb), rho


def run():
    index = series("TEDPIX")
    returns = index.pct_change()

    # Signal at close t, switch at close t+1, so it governs the return from
    # close t+1 to close t+2: position for return r_t is signal_{t-2}.
    sma = index.rolling(SMA, min_periods=SMA).mean()
    signal = (index > sma).astype(float)
    signal[sma.isna()] = 1.0            # before the average exists: as H1
    position = signal.shift(2).fillna(1.0)

    out = {"preregistration": "backtest_output/E27_preregistration.json",
           "windows": {}}

    for label, (start, end) in WINDOWS.items():
        mask = (returns.index >= pd.Timestamp(start)) & \
               (returns.index <= pd.Timestamp(end))
        r = returns[mask].fillna(0.0)
        pos = position[mask]

        fund = pd.Series(per_session(FUND_RATE), index=r.index)
        drag = per_session(HOLDING_DRAG)

        h1 = (1 + r) * (1 - drag) - 1
        h2_gross = pos * ((1 + r) * (1 - drag) - 1) + (1 - pos) * fund
        switches = pos.diff().abs().fillna(0.0)
        h2 = (1 + h2_gross) * (1 - switches * ROUND_TRIP / 2) - 1

        m_h1, m_h2, m_fund = blocks(h1), blocks(h2), blocks(fund)

        diff = m_h1 - m_fund
        t1 = diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))
        p1 = t_sf(t1, len(diff) - 1)

        z2, p2, sr2, sr1, rho = memmel(m_h2 - m_fund, m_h1 - m_fund)

        s1, s2 = stats_of(h1, fund), stats_of(h2, fund)

        result = {
            "range": [str(r.index[0].date()), str(r.index[-1].date())],
            "sessions": int(len(r)),
            "blocks": int(len(diff)),
            "fund_20pct": stats_of(fund, fund),
            "H1_passive": s1,
            "H2_trend": dict(s2, time_in_equity_pct=round(float(pos.mean()) * 100, 1),
                             switches=int(switches.sum())),
            "H1_test": {"t": round(float(t1), 3), "p_one_sided": round(p1, 5),
                        "mean_monthly_excess_pct": round(float(diff.mean()) * 100, 3)},
            "H2_test": {"z_memmel": round(z2, 3), "p_one_sided": round(p2, 5),
                        "monthly_sharpe_H2": round(sr2, 4),
                        "monthly_sharpe_H1": round(sr1, 4), "rho": round(rho, 3),
                        "smaller_drawdown": s2["max_drawdown_pct"] > s1["max_drawdown_pct"]},
            "H1_vs_fund_sensitivity": {},
        }

        for rate in FUND_SENSITIVITY:
            alt = pd.Series(per_session(rate), index=r.index)
            d = m_h1 - blocks(alt)
            t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))
            result["H1_vs_fund_sensitivity"]["%d%%" % (rate * 100)] = {
                "t": round(float(t), 3), "p_one_sided": round(t_sf(t, len(d) - 1), 5)}

        try:
            ew = series("TEDPIX_EW").pct_change()
            ew = ew[(ew.index >= pd.Timestamp(start)) & (ew.index <= pd.Timestamp(end))]
            result["equal_weight_index_buy_hold"] = stats_of(
                ew.fillna(0.0), pd.Series(per_session(FUND_RATE), index=ew.index))
            result["equal_weight_index_buy_hold"]["from"] = str(ew.index[0].date())
        except Exception as exc:                                # noqa: BLE001
            result["equal_weight_index_buy_hold"] = "unavailable: %s" % exc

        out["windows"][label] = result

    primary, secondary = out["windows"]["primary"], out["windows"]["secondary"]

    h1_pass = primary["H1_test"]["p_one_sided"] < ALPHA
    h1_same = secondary["H1_test"]["mean_monthly_excess_pct"] > 0
    h2_pass = (primary["H2_test"]["p_one_sided"] < ALPHA
               and primary["H2_test"]["smaller_drawdown"])
    h2_same = (secondary["H2_test"]["z_memmel"] > 0
               and secondary["H2_test"]["smaller_drawdown"])

    out["verdict"] = {
        "H1_passive": "ACCEPT" if h1_pass and h1_same else "REJECT",
        "H2_trend": "ACCEPT" if h2_pass and h2_same else "REJECT",
        "alpha_per_hypothesis": ALPHA,
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    run()
