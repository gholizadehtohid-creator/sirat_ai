"""Two-stage market scan: fundamental screen, then technical analysis.

This is the shape the whole feature exists for -- fundamentals decide WHICH
shares are worth looking at, technicals decide WHEN. Only symbols the
fundamental stage approves are analysed technically, which is also what makes
a full-market scan affordable: the screen runs over the whole universe from
one cheap snapshot, and only the survivors pay for a per-symbol history fetch.

    Stage 0  universe      -- all instruments, filtered to ordinary equities
    Stage 1  data          -- snapshot + cached history for the universe
    Stage 2  fundamental   -- hard gates, then a 0-100 score -> APPROVED
    Stage 3  technical     -- indicators + ranking, for approved symbols only

Symbols that cannot be judged are reported as INSUFFICIENT_DATA and are never
silently promoted into the approved set.
"""

from datetime import datetime, timezone

import codal_sync
import config
import database
import fundamentals
import moneyflow
import scoring
import screening
import settings
from backtest import back_adjust
from indicators import DEFAULT_PARAMS, calculate_indicators
from providers import get_provider
from ranking import rank_stocks


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_scan(
    limit=None,
    approve_score=None,
    max_technical=None,
    params=None,
    equities_only=True,
    refresh_snapshot=False,
    progress=None,
    score_weights=None,
):
    """Run the full two-stage scan and return a report dict.

    `score_weights` overrides the combined engine's blend for this run, so the
    panel can re-rank on a different weighting without refetching anything.
    """

    started_at = _now()

    params = params or DEFAULT_PARAMS
    max_technical = max_technical or settings.MAX_TECHNICAL_SYMBOLS
    approve_score = (
        config.FUND_APPROVE_SCORE if approve_score is None else approve_score
    )

    warnings = []

    def note(message):
        if progress:
            progress(message)

    # ---- Stage 0: universe ------------------------------------------------

    note("Loading instrument universe")

    instruments = database.load_instruments(equities_only=equities_only)

    if not instruments:
        return _empty_report(
            started_at,
            "No instruments stored. Run: python seed_import.py --universe",
            approve_score,
        )

    if limit:
        instruments = instruments[:limit]

    universe_size = len(instruments)

    # ---- Stage 1: data ----------------------------------------------------

    note(f"Loading market data for {universe_size} instruments")

    provider = get_provider()

    snapshot_by_code = {}

    if refresh_snapshot:
        result = provider.get_market_snapshot()
        if result.ok:
            database.save_snapshot(result.data)
        else:
            warnings.append(f"snapshot refresh failed: {result.error}")

    snapshot_df = database.load_snapshot()

    if snapshot_df is not None and not snapshot_df.empty:
        for _, row in snapshot_df.iterrows():
            snapshot_by_code[str(row.get("ins_code"))] = row.to_dict()
    else:
        warnings.append(
            "No market snapshot stored: EPS, P/E and market cap are unavailable, "
            "so the fundamental screen runs on price-derived metrics only. "
            "Most of the universe will report INSUFFICIENT_DATA. "
            "Fetch one with: python seed_import.py --snapshot "
            "(needs a route to tsetmc — see SIRAT_PROXY_URL)."
        )

    have_history = database.inscodes_with_history()

    if not have_history:
        warnings.append("No cached price history at all — nothing can be screened.")

    # How many of the instruments being screened have bars -- the same test the
    # screen below uses to load them. price_history also holds funds and names
    # outside this universe, so its own size is not that number: reported as-is
    # it put "832 of 818 shares have stored bars" on the coverage table.
    screened_with_history = sum(
        1 for instrument in instruments
        if instrument["ins_code"] in have_history
    )

    # ---- Stage 2: fundamental screen -------------------------------------

    note("Applying fundamental screen")

    # Book equity and portfolio position, for the sectors whose valuation is
    # NAV rather than earnings. Two whole-table queries rather than one per
    # instrument: the screen runs over the entire universe, so a per-symbol
    # lookup here would be 1,636 round trips before any analysis starts.
    #
    # Cache only, like every other Codal read on this path -- the screen must
    # finish whether or not codal.ir is reachable.
    codal_latest_by_code = database.load_codal_latest()
    portfolio_by_code = database.load_codal_portfolio_latest()

    approved = []
    provisional = []
    rejected = []
    insufficient = []

    gate_counts = {}

    for instrument in instruments:

        ins_code = instrument["ins_code"]

        history = (
            database.load_history_by_inscode(ins_code, limit=300)
            if ins_code in have_history else None
        )

        codal_row = codal_latest_by_code.get(str(ins_code)) or {}
        codal_items = codal_row.get("items") or {}

        metrics = fundamentals.build(
            history,
            snapshot_by_code.get(ins_code),
            sector=instrument.get("sector_name"),
            portfolio=portfolio_by_code.get(str(ins_code)),
            equity=codal_items.get("equity_owners") or codal_items.get("equity"),
        )

        verdict = screening.screen(metrics, approve_score=approve_score)

        row = {
            "ins_code": ins_code,
            "symbol": instrument["symbol_fa"],
            "name": instrument["name_fa"],
            "board": instrument.get("board"),
            "sector_name": instrument.get("sector_name"),
            "fundamental_score": verdict["score"],
            "fundamental_breakdown": verdict["breakdown"],
            "weight_covered": verdict["weight_covered"],
            "verdict": verdict["verdict"],
            "reasons": verdict["reasons"],
            "notes": verdict["notes"],
            "missing": verdict["missing"],
            "metrics": _public_metrics(metrics),
        }

        for failure in verdict["gates_failed"]:
            gate_counts[failure["label"]] = gate_counts.get(failure["label"], 0) + 1

        if verdict["verdict"] == screening.APPROVED:
            approved.append(row)
        elif verdict["verdict"] == screening.PROVISIONAL:
            provisional.append(row)
        elif verdict["verdict"] == screening.INSUFFICIENT:
            insufficient.append(row)
        else:
            rejected.append(row)

    for bucket in (approved, provisional, rejected):
        bucket.sort(key=lambda r: r["fundamental_score"], reverse=True)

    # ---- Stage 3: technical analysis of the passing set -------------------
    #
    # Provisional symbols are analysed too -- they cleared every gate that
    # could be evaluated and are the best available candidates -- but they stay
    # labelled so nobody mistakes a liquidity-only pass for a full one.

    passing = approved + provisional

    shortlist = passing[:max_technical]

    if len(passing) > len(shortlist):
        warnings.append(
            f"{len(passing)} symbols passed the fundamental screen but only the "
            f"top {len(shortlist)} by fundamental score were analysed technically "
            f"(SIRAT_MAX_TECHNICAL)."
        )

    note(f"Running technical analysis on {len(shortlist)} approved symbols")

    # One query for the whole shortlist rather than one per symbol. The tail is
    # cut to the baseline window because that is the longest span anything here
    # measures over; pulling a full listing history per name would dominate the
    # cost of the stage it is only a confirmation for.
    flow_by_code = database.load_client_type_tail(
        [r["ins_code"] for r in shortlist], config.FLOW_BASELINE_WINDOW
    )

    if shortlist and not flow_by_code:
        warnings.append(
            "No حقیقی/حقوقی data stored for any screened symbol, so the money "
            "flow reading is unavailable and the overall score is built "
            "without it. Fetch it with: python seed_import.py --flow "
            "(one request per symbol — see SIRAT_PROXY_URL)."
        )

    # Codal fundamentals, read from cache only. One query for the whole
    # shortlist and no network at any point: whether codal.ir is reachable
    # decides how *fresh* this is, never whether the scan finishes. That is the
    # entire reason the sync is a separate background job.
    codal_by_code = codal_sync.metrics_for(
        [r["ins_code"] for r in shortlist], market_by_code=snapshot_by_code
    )

    if shortlist and not codal_by_code:
        warnings.append(
            "No Codal filings stored for any screened symbol, so the "
            "fundamental score is the snapshot-based one only (no ROE, "
            "margins, growth or cash flow). They download in the background; "
            "progress is at /api/codal."
        )

    final = []

    for row in shortlist:

        history = database.load_history_by_inscode(row["ins_code"], limit=300)

        if history is None or history.empty:
            row["technical"] = None
            row["technical_status"] = "NO DATA"
            final.append(row)
            continue

        # Back-adjusted first, as the v2 frame (data_pipeline.frame) and the
        # panel's Technical tab are: stored bars are raw, and a capital
        # increase inside the window reads as a crash -- MAs above the price,
        # RSI near zero, an ATR the size of the adjustment -- which then
        # sizes the stop through _risk_inputs. Anchored at the newest bar, so
        # the last close is unchanged.
        history, _events = back_adjust(history)

        enriched = calculate_indicators(history, params)

        if enriched.empty:
            row["technical"] = None
            row["technical_status"] = "NO DATA"
            final.append(row)
            continue

        technical = rank_stocks(enriched)

        # Volatility and trend-strength readings travel with the row because
        # position sizing needs them downstream and re-deriving them there
        # would mean recomputing indicators over the whole history again.
        row["metrics"].update(_risk_inputs(enriched))

        row["technical"] = {
            "score": technical["score"],
            "scored": technical.get("scored", True),
            "status": technical["status"],
            "trend": technical["trend"],
            "rsi": technical["rsi"],
            "macd": technical["macd"],
            "momentum": technical["momentum"],
            "breakdown": technical["breakdown"],
            "signals": technical["signals"],
        }
        row["technical_status"] = technical["status"]
        row["technical_score"] = technical["score"] if technical.get("scored", True) else None

        # Who was on each side while that price action happened. Attached to
        # every row that has the data, whether or not it ends up moving the
        # score, because "flow says the opposite" is worth seeing even where
        # the tilt is too small to change a rank.
        flow = moneyflow.analyse(flow_by_code.get(row["ins_code"]))

        row["flow"] = _public_flow(flow)
        row["flow_score"] = (
            flow["score"] if flow["verdict"] == moneyflow.SCORED else None
        )

        # Codal fundamentals for this row, when its filings have arrived.
        codal_metrics = codal_by_code.get(str(row["ins_code"]))

        row["codal"] = _public_codal(codal_metrics)

        # The fundamental input to the blend. Codal's score is preferred when
        # it exists because it is built from filed statements -- ROE, margins,
        # growth, cash flow -- while screening.py's is built from a price
        # snapshot and knows none of that. The snapshot score remains the
        # gate that decided this symbol was worth analysing at all, and is
        # still reported; this is about which one the ranking blends.
        if codal_metrics and codal_metrics.get("score") is not None:
            fundamental_input = codal_metrics["score"]
            row["fundamental_source"] = "codal"
        else:
            fundamental_input = row["fundamental_score"]
            row["fundamental_source"] = "snapshot"

        row["risk"] = scoring.risk_score(row["metrics"])
        row["risk_score"] = row["risk"]["score"]

        # Combined view: fundamentals decide inclusion, technicals decide
        # timing. With no technical reading there is no combined view -- a
        # symbol that did not trade today is not a symbol that scored zero on
        # timing, and blending its placeholder 0 would rank it below names the
        # model genuinely dislikes.
        if technical.get("scored", True):

            overall = scoring.evaluate(
                technical=technical["score"],
                fundamental=fundamental_input,
                risk=row["risk_score"],
                flow=row["flow_score"],
                weights=score_weights,
            )

            row["overall"] = overall
            row["overall_score"] = overall["score"]
            row["final_status"] = overall["status"]
            row["divergence"] = overall["divergence"]
            row["divergence_note"] = overall["divergence_note"]

            # portfolio.py and the panel have always ranked on combined_score.
            # It now carries the engine's answer rather than a hand-rolled
            # average, so the basket reflects fundamentals and risk too --
            # under the same key, so nothing downstream had to change.
            row["combined_score"] = overall["score"]

            # What the basket sorts by is a separate decision from what the
            # report displays: the blend describes a name, the selection score
            # orders it. Keeping them in one field meant a change to either
            # silently changed the other.
            selection = scoring.selection_score(
                technical=technical["score"],
                flow=row["flow_score"],
                combined=overall["score"],
            )

            row["selection"] = selection
            row["rank_score"] = selection["score"]
            row["rank_basis"] = selection["basis"]
            row["rank_eligible"] = selection["eligible"]

        else:
            row["overall"] = None
            row["overall_score"] = None
            row["final_status"] = technical.get("status")
            row["divergence"] = None
            row["divergence_note"] = None
            row["combined_score"] = None
            row["selection"] = None
            row["rank_score"] = None
            row["rank_basis"] = None
            row["rank_eligible"] = False

        final.append(row)

    # Symbols with a combined score first, best down; then the unscored ones on
    # their fundamentals alone, rather than sunk to the bottom by a fake zero.
    final.sort(
        key=lambda r: (
            r.get("combined_score") is not None,
            r.get("combined_score") or 0,
            r["fundamental_score"],
        ),
        reverse=True
    )

    report = {
        "started_at": started_at,
        "finished_at": _now(),
        "approve_score": approve_score,
        "universe_size": universe_size,
        "equities_only": equities_only,
        "counts": {
            "universe": universe_size,
            "approved": len(approved),
            "provisional": len(provisional),
            "rejected": len(rejected),
            "insufficient": len(insufficient),
            "technically_analysed": len(final),
        },
        "gate_counts": gate_counts,
        "snapshot": database.snapshot_info(),
        # The panel's banner reads its "real data / simulated" verdict from the
        # screen report, and this key was never in it -- so the banner said
        # "real stored data" whatever the bars actually were. The same record
        # /api/status serves; one indexed row.
        "provenance": database.get_provenance("price_history"),
        "have_history": screened_with_history,
        "codal": {
            **database.codal_coverage(),
            "in_scan": sum(
                1 for r in final
                if (r.get("codal") or {}).get("score") is not None
            ),
        },
        "score_weights": score_weights or scoring.default_weights(),
        "warnings": warnings,
        "final": final,
        "approved": approved,
        "provisional": provisional,
        "rejected": rejected[:200],
        "insufficient_sample": insufficient[:50],
        "provider": provider.name,
    }

    return report


def _public_metrics(metrics):
    """The metric subset worth showing in the report."""

    keys = [
        "bars", "last_date", "last_trade_date", "stale_days", "close",
        "avg_value_20",
        "volatility_60", "range_position", "headroom_pct", "return_60d",
        "eps", "pe", "earnings_yield", "market_cap", "coverage",
    ]

    return {k: metrics.get(k) for k in keys}


def _public_flow(flow):
    """The flow subset worth carrying in a scan row.

    The full reading holds every summed rial of the window and its baseline
    twin; a whole-market scan would repeat that for hundreds of rows and the
    JSON would be mostly flow. What a row needs is the verdict, the score and
    the two numbers a reader would want to check it against.
    """

    if flow["verdict"] != moneyflow.SCORED:
        return {
            "verdict": flow["verdict"],
            "score": None,
            "label": None,
            "reason": flow["reason"],
            "sessions": flow["sessions"],
        }

    reading = flow["reading"]

    return {
        "verdict": flow["verdict"],
        "score": flow["score"],
        "label": flow["label"],
        "reason": None,
        "sessions": flow["sessions"],
        "window": reading["sessions"],
        "power_ratio": (
            round(reading["power_ratio"], 2)
            if reading["power_ratio"] is not None else None
        ),
        "net_pct": round(reading["net_pct"], 2),
        "net_individual": reading["net_individual"],
        "sessions_positive": reading["sessions_positive"],
        "breakdown": flow["breakdown"],
        "notes": flow["notes"],
    }


def _public_codal(metrics):
    """The Codal subset worth carrying in a scan row.

    The stored record holds every filed line item and every note; repeated
    across 300 rows that is most of the response body for data the ranking
    table never shows. What a row needs is the score, the headline ratios a
    reader would check it against, and enough provenance to know which filing
    it came from.
    """

    if not metrics:
        return None

    ratios = metrics.get("ratios") or {}

    keep = [
        "pe", "ps", "pb", "roe", "roa", "net_margin", "gross_margin",
        "operating_margin", "debt_ratio", "current_ratio", "earnings_quality",
        "revenue_growth", "profit_growth", "eps_growth", "eps", "eps_annual",
        "market_cap", "earnings_yield", "annualised", "period_months",
    ]

    return {
        "score": metrics.get("score"),
        "status": metrics.get("status"),
        "verdict": metrics.get("verdict"),
        "weight_covered": metrics.get("weight_covered"),
        "period_end": metrics.get("period_end"),
        "period_months": metrics.get("period_months"),
        "audited": metrics.get("audited"),
        "consolidated": metrics.get("consolidated"),
        "updated_at": metrics.get("updated_at"),
        "breakdown": metrics.get("breakdown"),
        "ratios": {key: ratios.get(key) for key in keep},
    }


def _risk_inputs(enriched):
    """Last-bar volatility and trend-strength readings, as plain floats.

    Every one of these can legitimately be absent -- ATR and ADX need a real
    high/low range, which not every cached frame carries -- so a missing value
    is None rather than a substituted zero. A zero ATR would size an infinitely
    large position, which is exactly the kind of silent nonsense the rest of
    this codebase refuses to produce.
    """

    if enriched is None or enriched.empty:
        return {}

    last = enriched.iloc[-1]

    def value(column):
        if column not in enriched.columns:
            return None
        raw = last[column]
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return round(number, 4)

    return {
        "atr": value("ATR"),
        "atr_pct": value("ATR_PCT"),
        "adx": value("ADX"),
        "bb_pct": value("BB_PCT"),
        "bb_width": value("BB_WIDTH"),
    }


def _empty_report(started_at, message, approve_score):
    return {
        "started_at": started_at,
        "finished_at": _now(),
        "approve_score": approve_score,
        "universe_size": 0,
        "counts": {
            "universe": 0, "approved": 0, "provisional": 0, "rejected": 0,
            "insufficient": 0, "technically_analysed": 0
        },
        "gate_counts": {},
        "snapshot": database.snapshot_info(),
        "provenance": database.get_provenance("price_history"),
        "have_history": 0,
        "warnings": [message],
        "final": [],
        "approved": [],
        "rejected": [],
        "insufficient_sample": [],
        "provider": None,
    }


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(description="SIRAT AI two-stage market scan")
    parser.add_argument("--limit", type=int, help="only screen the first N instruments")
    parser.add_argument("--approve", type=int, help="fundamental score needed to pass")
    parser.add_argument("--max-technical", type=int, help="cap on technical analyses")
    parser.add_argument("--all-instruments", action="store_true",
                        help="do not restrict to ordinary equities")
    args = parser.parse_args()

    report = run_scan(
        limit=args.limit,
        approve_score=args.approve,
        max_technical=args.max_technical,
        equities_only=not args.all_instruments,
        progress=lambda m: print(f"  ... {m}"),
    )

    counts = report["counts"]

    print()
    print("=" * 70)
    print("SIRAT AI - TWO-STAGE MARKET SCAN")
    print("=" * 70)
    print(f"Universe              : {counts['universe']}")
    print(f"Fundamentally approved: {counts['approved']}")
    print(f"Provisional (partial) : {counts['provisional']}")
    print(f"Rejected              : {counts['rejected']}")
    print(f"Insufficient data     : {counts['insufficient']}")
    print(f"Technically analysed  : {counts['technically_analysed']}")

    if report["gate_counts"]:
        print("\nRejections by gate:")
        for label, n in sorted(report["gate_counts"].items(), key=lambda kv: -kv[1]):
            print(f"  {label:<32} {n}")

    if report["final"]:
        print()
        print("=" * 78)
        print("FINAL REPORT - fundamentally screened, then technically ranked")
        print("=" * 78)
        print(f"{'SYMBOL':<12}{'VERDICT':<14}{'FUND':>5}{'TECH':>6}{'COMB':>6}  "
              f"{'TREND':<16}{'STATUS'}")
        print("-" * 78)
        for row in report["final"]:
            tech = row.get("technical") or {}
            scored = tech.get("scored", True)
            technical_score = f"{tech.get('score', 0)}" if scored else "-"
            combined = row.get("combined_score")
            print(
                f"{row['symbol']:<12}{row['verdict']:<14}{row['fundamental_score']:>5}"
                f"{technical_score:>6}"
                f"{combined if combined is not None else '-':>6}  "
                f"{tech.get('trend', 'n/a'):<16}{row.get('technical_status', 'n/a')}"
            )
        print()
        for row in report["final"]:
            if row["reasons"]:
                print(f"  {row['symbol']}: {row['reasons'][0]}")

    for warning in report["warnings"]:
        print(f"\nNOTE: {warning}")
