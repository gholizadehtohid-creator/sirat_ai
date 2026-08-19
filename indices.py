"""Technical analysis of the market indices (شاخص کل، شاخص کل هم‌وزن).

An index is not a share, and two differences matter enough to shape this
module:

  NO VOLUME.  The exchange publishes a closing, low and high value for an
      index and nothing else. The scoring model in ranking.py weights volume
      at 15 of its 100 points, so an index either loses that component or is
      given a volume from somewhere. This module gives it market turnover --
      the total rials traded across every instrument we hold bars for -- and
      says so in `volume_source`. That is the figure a trader would look at
      anyway: an index rising on falling turnover is the classic warning.

  TWO INDICES, ONE MARKET.  شاخص کل is capitalisation-weighted, so a handful of
      giants can carry it while most of the market falls. شاخص کل هم‌وزن gives
      every constituent the same say. Read alone, either one misleads; read
      together, the gap between them measures breadth directly, which is why
      compare() exists and why the panel shows both.

    python indices.py
"""

import config
import database
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from ranking import rank_stocks


# A turnover figure computed from a handful of instruments is not market
# turnover. Dates below this share of the best-covered day are dropped rather
# than contributing a misleadingly small number.
TURNOVER_COVERAGE_RATIO = 0.25


def _turnover_series(limit=None):
    """Date -> total rials traded, restricted to well-covered sessions."""

    frame = database.market_turnover()

    if frame is None or frame.empty:
        return None

    best = frame["instruments"].max()

    if best:
        frame = frame[frame["instruments"] >= best * TURNOVER_COVERAGE_RATIO]

    frame = frame[frame["turnover"] > 0]

    if frame.empty:
        return None

    if limit:
        frame = frame.tail(limit)

    return frame[["date", "turnover"]].reset_index(drop=True)


def load_series(key, bars=None):
    """One index's series with market turnover attached as `volume`.

    Returns (frame, volume_source). The frame is empty if the index has not
    been imported; volume_source is None when no turnover could be attached,
    and the caller must then not pretend the volume component ran.
    """

    spec = config.INDICES.get(key)

    if spec is None:
        return None, None

    frame = database.load_index_history(spec["insCode"])

    if frame is None or frame.empty:
        return frame, None

    turnover = _turnover_series()

    if turnover is None:
        frame = frame.tail(bars or config.INDEX_ANALYSIS_BARS).copy()
        frame["volume"] = None
        return frame.reset_index(drop=True), None

    # Inner join: the analysed window is where BOTH a published index value
    # and a trustworthy turnover figure exist.
    merged = frame.merge(turnover, on="date", how="inner")

    merged = merged.rename(columns={"turnover": "volume"})

    if bars:
        merged = merged.tail(bars)

    return merged.reset_index(drop=True), "market turnover (sum of traded value)"


def analyse(key, params=None, bars=None):
    """Full technical read on one index."""

    spec = config.INDICES.get(key)

    if spec is None:
        return {"key": key, "error": f"unknown index '{key}'"}

    params = params or DEFAULT_PARAMS
    bars = bars or config.INDEX_ANALYSIS_BARS

    frame, volume_source = load_series(key, bars=bars)

    base = {
        "key": key,
        "name": spec["name"],
        "ins_code": spec["insCode"],
        "weighting": spec["weighting"],
        "volume_source": volume_source,
    }

    if frame is None or frame.empty:
        return {
            **base,
            "technical": None,
            "error": "no index history stored — run: "
                     "python seed_import.py --indices",
        }

    warmup = required_bars(params)

    if len(frame) < warmup:
        return {
            **base,
            "bars": len(frame),
            "technical": None,
            "error": (f"only {len(frame)} sessions overlap with market "
                      f"turnover; the indicators need {warmup}"),
        }

    enriched = calculate_indicators(frame, params)

    technical = rank_stocks(enriched)

    last = enriched.iloc[-1]

    return {
        **base,
        "bars": len(enriched),
        "first_date": enriched["date"].iloc[0].strftime("%Y-%m-%d"),
        "last_date": last["date"].strftime("%Y-%m-%d"),
        "close": float(last["close"]),
        "returns": _returns(enriched),
        "technical": technical,
        "series": _series_payload(enriched),
    }


def _returns(df):
    """Percentage change over the usual review horizons."""

    closes = df["close"]
    last = closes.iloc[-1]

    out = {}

    for label, back in (("1d", 1), ("5d", 5), ("20d", 20), ("60d", 60)):
        if len(closes) > back:
            past = closes.iloc[-1 - back]
            out[label] = round((last - past) / past * 100, 2) if past else None
        else:
            out[label] = None

    return out


def _series_payload(df, points=260):
    """Recent series for charting, as plain JSON-safe lists."""

    tail = df.tail(points)

    def column(name):
        if name not in tail.columns:
            return []
        return [None if value != value else round(float(value), 2)
                for value in tail[name]]

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in tail["date"]],
        "close": column("close"),
        "ma_fast": column("MA_FAST"),
        "ma_slow": column("MA_SLOW"),
        "rsi": column("RSI"),
        "volume": column("volume"),
    }


def compare(reports):
    """What the two indices say about breadth when read against each other.

    A capitalisation-weighted index rising while the equal-weighted one falls
    means the market is not rising -- a few large companies are. The reverse,
    equal-weighted leading, means the advance is broad and the giants are
    lagging. Neither is visible in either index alone.
    """

    cap = next((r for r in reports if r.get("weighting") == "capitalisation"
                and r.get("returns")), None)
    equal = next((r for r in reports if r.get("weighting") == "equal"
                  and r.get("returns")), None)

    if not cap or not equal:
        return None

    spreads = {}

    for horizon in ("1d", "5d", "20d", "60d"):
        a = equal["returns"].get(horizon)
        b = cap["returns"].get(horizon)
        spreads[horizon] = round(a - b, 2) if a is not None and b is not None else None

    spread_20 = spreads.get("20d")

    if spread_20 is None:
        verdict = "not enough overlapping history to judge breadth"
    elif spread_20 > 2:
        verdict = ("broad advance — the equal-weighted index is beating the "
                   "capitalisation-weighted one, so smaller names are leading")
    elif spread_20 < -2:
        verdict = ("narrow market — the capitalisation-weighted index is being "
                   "carried by large caps while the average share lags")
    else:
        verdict = "large caps and the broad market are moving together"

    return {
        "cap_weighted": cap["name"],
        "equal_weighted": equal["name"],
        "spreads": spreads,
        "verdict": verdict,
    }


def analyse_all(params=None, bars=None):
    """Both indices plus the breadth read between them."""

    reports = [analyse(key, params=params, bars=bars) for key in config.INDEX_LIST]

    return {
        "indices": reports,
        "breadth": compare(reports),
    }


if __name__ == "__main__":

    report = analyse_all()

    print("=" * 74)
    print("SIRAT AI - INDEX TECHNICAL ANALYSIS")
    print("=" * 74)

    for index in report["indices"]:

        print(f"\n{index['name']}  ({index['weighting']}-weighted)")

        if index.get("error"):
            print(f"  unavailable: {index['error']}")
            continue

        technical = index["technical"]
        returns = index["returns"]

        print(f"  {index['bars']} sessions, {index['first_date']} -> "
              f"{index['last_date']}")
        print(f"  close    {index['close']:,.1f}")
        print(f"  returns  1d {returns['1d']}%   5d {returns['5d']}%   "
              f"20d {returns['20d']}%   60d {returns['60d']}%")
        print(f"  score    {technical['score']}/100   {technical['status']}")
        print(f"  trend    {technical['trend']}   RSI {technical['rsi']}")
        print(f"  volume   {index['volume_source'] or 'not available'}")
        print("  signals  " + "; ".join(technical["signals"]))

    breadth = report["breadth"]

    if breadth:
        print()
        print("=" * 74)
        print("BREADTH — equal-weighted minus capitalisation-weighted")
        print("=" * 74)
        for horizon, spread in breadth["spreads"].items():
            print(f"  {horizon:<5} {spread:+.2f} pp" if spread is not None
                  else f"  {horizon:<5} n/a")
        print(f"\n  {breadth['verdict']}")
