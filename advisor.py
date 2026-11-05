"""The full decision chain, in the order the decisions actually depend on.

    1. regime      what kind of market is this, and how much cash to hold
    2. screen      which shares are worth owning at all (fundamentals, then
                   technicals -- pipeline.run_scan)
    3. portfolio   how many of each, sized by risk and spread across sectors
    4. risk        what the resulting basket is actually exposed to

The order is not arbitrary and cannot be rearranged. Regime comes first because
it sets the size of the whole bet; a bear market does not make the ten best
names bad picks, it makes owning ten names of anything the wrong exposure. Risk
comes last because it can only be measured on a basket that already exists --
correlation is a property of a set, not of any share in it.

Each stage degrades on its own rather than taking the chain down with it. No
index history means no regime, which means the cash floor falls back to the
config constant and the run says so; it does not mean no portfolio.

TWO ENGINES, ONE ENTRY POINT
----------------------------
`engine="v1"` is the chain described above and is the default. `engine="v2"`
runs the swing decision engine instead -- regime_engine, factor_engine,
risk_engine, trade_manager, portfolio_engine -- and returns a report in exactly
the same shape, so the panel, the CLI and the risk analysis need no knowledge of
which one produced it.

That shared entry point is the whole point of this parameter. Before it existed
the v2 engines were reachable only by running swing_advisor.py by hand, which
meant every change to a Swing Score weight was invisible here: a full run of
this module called into factor_engine exactly zero times. Selecting the engine
is now one argument, and `report["engine"]` always says which one answered.

    python advisor.py
    python advisor.py --engine v2
    python advisor.py --equity 5000000000 --size 12
"""

import logging

import config
import portfolio as portfolio_module
import regime as regime_module
import risk as risk_module
from logging_setup import get_logger, setup_logging


log = get_logger(__name__)


V1 = "v1"
V2 = "v2"

ENGINES = (V1, V2)


def engine_name(engine=None):
    """Which engine a caller asked for, normalised. Unknown names fall back.

    Falls back rather than raising because this is read from a query string and
    from an environment-driven config: a typo in a URL should produce the
    documented default and a log line, not a 500 on the panel's main table.
    """

    name = str(engine or getattr(config, "DECISION_ENGINE", V1) or V1).lower()

    if name not in ENGINES:
        log.warning("unknown decision engine %r; falling back to %s", name, V1)
        return V1

    return name


def run(equity=None, size=None, max_per_sector=None, min_score=None,
        risk_pct=None, use_regime=True, scan_report=None, progress=None,
        engine=None, positions=None):
    """Run the whole chain and return one report."""

    import pipeline

    if engine_name(engine) == V2:
        return _run_v2(
            equity=equity, size=size, max_per_sector=max_per_sector,
            risk_pct=risk_pct, use_regime=use_regime, progress=progress,
            positions=positions,
        )

    equity = equity or config.ACCOUNT_EQUITY

    def note(message):
        log.info(message)
        if progress:
            progress(message)

    warnings = []

    # ---- 1. regime -------------------------------------------------------

    market_regime = None

    if use_regime:

        note("تشخیص وضعیت بازار")

        try:
            market_regime = regime_module.detect()
        except Exception as exc:
            log.exception("regime detection failed")
            warnings.append(f"تشخیص وضعیت بازار انجام نشد: {exc}")
            market_regime = None

        if market_regime and not market_regime.get("available"):
            warnings.append(market_regime.get("reason", "وضعیت بازار نامشخص"))
            market_regime = None

    # ---- 2. screen -------------------------------------------------------

    if scan_report is None:

        note("پایش کل بازار — غربال بنیادی سپس تحلیل تکنیکال")

        try:
            scan_report = pipeline.run_scan(progress=progress)
        except Exception as exc:
            log.exception("market scan failed")
            return {
                "ok": False,
                "error": f"پایش بازار شکست خورد: {exc}",
                "warnings": warnings,
            }

    warnings.extend(scan_report.get("warnings", []))

    # ---- 3. portfolio ----------------------------------------------------

    note("ساخت سبد با مدیریت ریسک و تنوع صنعتی")

    try:
        basket = portfolio_module.build(
            scan_report.get("final", []),
            equity=equity,
            size=size,
            max_per_sector=max_per_sector,
            min_score=min_score,
            risk_pct=risk_pct,
            market_regime=market_regime,
        )
    except Exception as exc:
        log.exception("portfolio construction failed")
        return {
            "ok": False,
            "error": f"ساخت سبد شکست خورد: {exc}",
            "warnings": warnings,
            "regime": market_regime,
        }

    # ---- 4. risk ---------------------------------------------------------

    note("تحلیل ریسک سبد")

    try:
        risk_report = risk_module.analyse(basket["positions"], equity=equity)
    except Exception as exc:
        log.exception("risk analysis failed")
        risk_report = {"available": False, "reason": str(exc), "sector_exposure": []}
        warnings.append(f"تحلیل ریسک انجام نشد: {exc}")

    log.info(
        "advisor run complete: %d positions, %d sectors, %d candidates",
        basket["totals"]["positions"],
        basket["totals"]["sectors"],
        basket["candidates"],
    )

    return {
        "ok": True,
        "engine": V1,
        "regime": market_regime,
        "counts": scan_report.get("counts", {}),
        "portfolio": basket,
        "risk": risk_report,
        "warnings": warnings,
        "generated_at": scan_report.get("finished_at"),
    }


# ==============================
# THE V2 ENGINE, IN THE V1 REPORT SHAPE
# ==============================

def v2_scan(equity=None, size=None, max_per_sector=None, positions=None,
            verbose=False, risk_pct=None):
    """THE one call that runs the v2 chain. Every caller comes through here.

    regime_engine -> factor_engine -> risk_engine -> trade_manager ->
    portfolio_engine, in swing_advisor.scan().

    It exists as a named function with one line in it because two callers now
    need the v2 book -- the panel and the paper trader -- and the failure mode
    of letting each call the chain its own way is not a crash. It is the two
    quietly diverging: a different policy here, an extra filter there, and a
    paper record that is no longer evidence about the thing the panel shows.
    Whatever this returns is what BOTH of them traded.
    """

    import swing_advisor

    return swing_advisor.scan(
        equity=equity, size=size, max_per_sector=max_per_sector,
        positions=positions, verbose=verbose, risk_pct=risk_pct,
    )


def _run_v2(equity=None, size=None, max_per_sector=None, risk_pct=None,
            use_regime=True, progress=None, positions=None):
    """The swing chain, adapted to the report shape every caller already reads.

    An ADAPTER and nothing more: no scoring, no ranking and no selection happens
    here. swing_advisor.scan() produces the book and this function renames its
    fields, so a difference between the two engines' output is always a
    difference between the engines rather than something this layer introduced.
    """

    from datetime import datetime, timezone

    equity = equity or config.ACCOUNT_EQUITY

    warnings = []

    def note(message):
        log.info(message)
        if progress:
            progress(message)

    if not use_regime:
        # Refused rather than ignored. In v1 the regime only sets a cash floor,
        # so switching it off degrades gracefully; in v2 it decides position
        # size and the entry floor, and a run with it disabled would be a
        # different strategy wearing the same name.
        warnings.append(
            "موتور v2 بدون خواندن بازار اجرا نمی‌شود — رژیم در این موتور اندازه‌ی "
            "موقعیت و کف ورود را تعیین می‌کند، نه فقط کف نقدینگی.")

    note("اجرای موتور تصمیم سوئینگ (v2)")

    try:
        out = v2_scan(equity=equity, size=size,
                      max_per_sector=max_per_sector, positions=positions,
                      risk_pct=risk_pct)
    except Exception as exc:
        log.exception("v2 scan failed")
        return {
            "ok": False,
            "engine": V2,
            "error": "اجرای موتور سوئینگ شکست خورد: %s" % exc,
            "warnings": warnings,
        }

    accepted = out["book"]["accepted"]

    basket_positions = []

    for rank, signal in enumerate(accepted, start=1):
        position = _v2_position(signal, rank, equity)
        if position is not None:
            basket_positions.append(position)

    note("تحلیل ریسک سبد")

    try:
        risk_report = risk_module.analyse(basket_positions, equity=equity)
    except Exception as exc:
        log.exception("risk analysis failed")
        risk_report = {"available": False, "reason": str(exc),
                       "sector_exposure": []}
        warnings.append("تحلیل ریسک انجام نشد: %s" % exc)

    regime = out.get("regime") or {}

    invested = sum(p["position_value"] for p in basket_positions)

    total_risk = sum(p["risk_rials"] or 0 for p in basket_positions)

    sectors_used = {}

    for position in basket_positions:
        sectors_used[position["sector_name"]] = (
            sectors_used.get(position["sector_name"], 0) + 1)

    notes = []

    if not out.get("fundamental_store"):
        notes.append("پایگاه بنیادی موجود نیست — عامل بنیادی از ترکیب حذف شده "
                     "است.")

    if not basket_positions:
        notes.append("هیچ نمادی به سبد راه نیافت. %s"
                     % (out["book"].get("reason") or
                        "سیگنال خریدی تولید نشد یا محدودیت‌های سبد اجازه ندادند."))

    # The unallocated share is part of the advice, not a remainder. The v2 book
    # averaged 80-85% cash over 2024-09..2026-11 (E26); left in a current
    # account that money loses to inflation while a fixed-income fund pays
    # about the policy rate with no market risk.
    idle = equity - invested if equity else 0.0

    if equity and idle / equity >= 0.05:
        notes.append(
            "%.0f%% سرمایه (%s ریال) در سبد سهام نیست — آن را در صندوق درآمد "
            "ثابت نگه دارید، نه در حساب کارگزاری؛ سود حدود %.0f%% سالانه "
            "بدون ریسک بازار است."
            % (idle / equity * 100.0, "{:,.0f}".format(idle),
               config.RISK_FREE_RATE * 100.0))

    policy = out.get("policy") or {}

    basket = {
        "positions": basket_positions,
        "settings": {
            "equity": equity,
            "size": policy.get("max_positions"),
            "max_per_sector": policy.get("max_per_sector"),
            # The threshold this run ACTUALLY used, floor included -- not the
            # base one. In a defensive market the two differ, and reporting the
            # base number beside a book built on the raised one would explain
            # the wrong thing.
            "min_score": config.SWING_BUY_SCORE + (regime.get("entry_floor") or 0),
            "risk_pct": risk_pct if risk_pct is not None
                        else config.RISK_PER_TRADE_PCT,
            "cash_pct": round(100.0 - (regime.get("max_invested_pct") or 0.0), 1),
        },
        "market_regime": {
            "regime": regime.get("regime"),
            "label": regime.get("label"),
            "confidence": regime.get("confidence"),
            "trading_mode": regime.get("trading_mode"),
            "mode_label": regime.get("mode_label"),
            "position_multiplier": regime.get("position_multiplier"),
            "entry_floor": regime.get("entry_floor"),
            "cash_target_pct": regime.get("cash_target_pct"),
        } if regime.get("available") else None,
        "totals": {
            "positions": len(basket_positions),
            "sectors": len(sectors_used),
            "invested": round(invested, 0),
            "cash": round(equity - invested, 0),
            "invested_pct": round(invested / equity * 100.0, 1) if equity else 0.0,
            "total_risk": round(total_risk, 0),
            "total_risk_pct": (round(total_risk / equity * 100.0, 2)
                               if equity else 0.0),
        },
        "sector_counts": sectors_used,
        "candidates": len(out.get("buys") or []),
        "notes": notes,
        "currency": config.CURRENCY,
    }

    actions = out.get("actions") or {}

    return {
        "ok": True,
        "engine": V2,
        "regime": regime if regime.get("available") else None,
        "counts": {
            "universe": out.get("scanned", 0) + out.get("skipped", 0),
            "approved": actions.get("BUY", 0),
            "provisional": actions.get("HOLD", 0),
            "rejected": actions.get("AVOID", 0),
            "insufficient": out.get("skipped", 0),
            "technically_analysed": out.get("scanned", 0),
        },
        "portfolio": basket,
        "risk": risk_report,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _v2_position(signal, rank, equity):
    """One allocated swing signal in the field names every reader expects.

    `combined_score` carries the Swing Score. The name is kept because the panel,
    portfolio.py and every existing report sort and display on that key -- and a
    second name for "the number this basket was ranked by" is how two rankings
    end up disagreeing in one report.
    """

    value = signal.get("allocated_value")
    shares = signal.get("allocated_shares")

    if not value or not shares:
        return None

    close = signal.get("entry_price")

    factors = (signal.get("swing") or {}).get("factors") or {}

    risk_rials = signal.get("risk_rials")

    # The risk figures are per the sizing engine's REQUESTED size; the allocator
    # may have cut the position to fit the book, so they are rescaled by what
    # was actually allocated rather than reported as though nothing was trimmed.
    requested = signal.get("position_value") or 0.0

    if risk_rials and requested > 0:
        risk_rials = risk_rials * (value / requested)

    return {
        "rank": rank,
        "ins_code": signal.get("ins_code"),
        "symbol": signal.get("symbol"),
        "name": signal.get("name"),
        "sector_name": signal.get("sector") or "بدون صنعت",
        "verdict": signal.get("action"),
        "status": signal.get("action_label"),
        "combined_score": signal.get("swing_score"),
        "rank_score": signal.get("swing_score"),
        "rank_basis": "swing_score",
        "technical_score": factors.get("technical"),
        "fundamental_score": factors.get("fundamental"),
        "momentum_score": factors.get("momentum"),
        "liquidity_score": factors.get("liquidity"),
        "flow_score": None,
        "risk_score": signal.get("risk_score"),
        "conviction": signal.get("conviction"),
        "close": close,
        "shares": shares,
        "position_value": round(value, 0),
        "weight_pct": (round(value / equity * 100.0, 2) if equity else None),
        "entry_low": signal.get("entry_low"),
        "entry_high": signal.get("entry_high"),
        "stop_price": signal.get("stop_loss"),
        "initial_stop_price": signal.get("stop_loss"),
        "stop_distance_pct": signal.get("stop_distance_pct"),
        "stop_source": signal.get("stop_source"),
        "target_price": signal.get("target_1"),
        "target_2_price": signal.get("target_2"),
        "target_distance_pct": signal.get("target_1_pct"),
        "risk_reward": signal.get("risk_reward"),
        "risk_rials": None if risk_rials is None else round(risk_rials, 0),
        "risk_pct": (round(risk_rials / equity * 100.0, 3)
                     if (risk_rials and equity) else None),
        "bound_by": signal.get("bound_by"),
        "scaled_by": signal.get("allocated_binding"),
        "expected_holding_sessions": signal.get("expected_holding_sessions"),
        "reasons": signal.get("reasons") or [],
        "notes": signal.get("notes") or [],
    }


def _print(report):
    """Console rendering of a full run."""

    if not report.get("ok"):
        print(f"\nخطا: {report.get('error')}")
        return

    basket = report["portfolio"]
    risk_report = report["risk"]
    market_regime = report.get("regime")
    settings = basket["settings"]
    totals = basket["totals"]

    print()
    print("=" * 118)
    print("SIRAT AI — گزارش پیشنهاد سبد دارایی   (موتور تصمیم: %s)"
          % report.get("engine", V1))
    print("=" * 118)

    # ---- market ----------------------------------------------------------

    if market_regime:
        print(f"\nوضعیت بازار : {market_regime['label']}   "
              f"(اطمینان {market_regime['confidence']})")
        for line in market_regime["evidence"]:
            print(f"              • {line}")
    else:
        print("\nوضعیت بازار : نامشخص — کف نقدینگی از تنظیمات ثابت خوانده شد")

    counts = report.get("counts", {})
    if counts:
        print(f"\nپایش بازار  : {counts.get('universe', 0)} نماد بررسی شد، "
              f"{counts.get('approved', 0)} تأیید، "
              f"{counts.get('provisional', 0)} مشروط، "
              f"{counts.get('rejected', 0)} رد، "
              f"{counts.get('insufficient', 0)} بدون داده‌ی کافی")

    print(f"\nسرمایه      : {settings['equity']:,.0f} {basket['currency']}   "
          f"ریسک هر موقعیت {settings['risk_pct']}%   "
          f"حداکثر {settings['max_per_sector']} نماد در هر صنعت   "
          f"کف نقد {settings['cash_pct']:.0f}%")
    print(f"نامزدها     : {basket['candidates']} نماد به حد نصاب امتیاز "
          f"{settings['min_score']} رسیدند")

    # ---- positions -------------------------------------------------------

    if not basket["positions"]:
        print()
        for note in basket["notes"]:
            print(f"  {note}")
        return

    print()
    print("-" * 118)
    print(f"{'#':<3}{'نماد':<11}{'صنعت':<24}{'امتیاز':>7}{'قیمت':>10}"
          f"{'حد ضرر':>9}{'حد سود':>9}{'تعداد':>10}{'ارزش':>15}"
          f"{'وزن':>7}{'ریسک':>7}")
    print("-" * 118)

    for p in basket["positions"]:
        print(
            f"{p['rank']:<3}{p['symbol']:<11}{(p['sector_name'] or '')[:22]:<24}"
            f"{p['combined_score']:>7}{p['close']:>10,.0f}"
            f"{p['stop_price']:>9,.0f}{p['target_price']:>9,.0f}"
            f"{p['shares']:>10,}{p['position_value']:>15,.0f}"
            f"{p['weight_pct']:>6.1f}%{p['risk_pct']:>6.2f}%"
        )

    print("-" * 118)
    print(f"{totals['positions']} موقعیت در {totals['sectors']} صنعت   "
          f"سرمایه‌گذاری‌شده {totals['invested']:,.0f} ({totals['invested_pct']}%)   "
          f"نقد {totals['cash']:,.0f} ({100 - totals['invested_pct']:.1f}%)")
    print(f"ریسک کل اگر همه‌ی حد ضررها فعال شوند: "
          f"{totals['total_risk']:,.0f} ({totals['total_risk_pct']}% سرمایه)")

    # ---- risk ------------------------------------------------------------

    if risk_report.get("available"):

        print()
        print("=" * 118)
        print("تحلیل ریسک سبد")
        print("=" * 118)

        vol = risk_report.get("volatility")
        if vol:
            print(f"  نوسان سالانه‌ی سبد        {vol['annualised_pct']:.2f}%")
            print(f"  اگر کاملاً همبسته بودند   {vol['undiversified_pct']:.2f}%")
            print(f"  نسبت تنوع‌بخشی            {vol['diversification_ratio']}")

        corr = risk_report.get("correlation")
        if corr and corr.get("average") is not None:
            print(f"  میانگین همبستگی جفتی      {corr['average']}")

        var = risk_report.get("var")
        if var:
            print(f"  VaR {int(var['confidence']*100)}% روزانه           "
                  f"{var['historical_pct']:.2f}%  "
                  f"({var.get('historical_rials', 0):,.0f} ریال)")
            print(f"  CVaR (زیان مورد انتظار)   {var['conditional_pct']:.2f}%  "
                  f"({var.get('conditional_rials', 0):,.0f} ریال)")

        b = risk_report.get("beta")
        if b:
            print(f"  بتا نسبت به {risk_report.get('benchmark', 'شاخص')}          "
                  f"{b['beta']}  (R² {b['r_squared']})")

        conc = risk_report.get("concentration")
        if conc:
            print(f"  تعداد مؤثر موقعیت‌ها       {conc['effective_positions']} "
                  f"از {conc['positions']}")

        print("\n  توزیع صنعتی:")
        for row in risk_report["sector_exposure"]:
            bar = "█" * int(row["pct_of_invested"] / 2)
            print(f"    {row['sector_name'][:30]:<32}{row['pct_of_invested']:>6.2f}%  {bar}")

        if risk_report.get("price_band_warning"):
            print(f"\n  {risk_report['price_band_warning']}")

    else:
        print(f"\nتحلیل ریسک در دسترس نیست: {risk_report.get('reason', '')}")

    for note in basket["notes"]:
        print(f"\nیادداشت: {note}")

    for warning in report.get("warnings", [])[:5]:
        print(f"\nهشدار: {warning}")

    print("\n" + "=" * 118)
    print("این گزارش پیشنهاد سرمایه‌گذاری نیست. هر عدد یک امتیاز یا برآورد ریسک است،")
    print("نه پیش‌بینی قطعی. مدیریت ریسک همیشه بر سیگنال مقدم است.")
    print("=" * 118)


if __name__ == "__main__":

    import argparse

    setup_logging()

    parser = argparse.ArgumentParser(
        description="SIRAT AI — full advisory run: regime, screen, portfolio, risk"
    )
    parser.add_argument("--equity", type=float, help="account equity in rials")
    parser.add_argument("--size", type=int, help="number of positions")
    parser.add_argument("--max-per-sector", type=int, help="cap per industry")
    parser.add_argument("--min-score", type=int, help="combined score floor")
    parser.add_argument("--risk", type=float, help="risk per position, percent")
    parser.add_argument("--no-regime", action="store_true",
                        help="skip market regime detection")
    parser.add_argument("--engine", default=None, choices=list(ENGINES),
                        help="which decision engine produces the book "
                             f"(default: {config.DECISION_ENGINE})")

    args = parser.parse_args()

    _print(run(
        equity=args.equity,
        size=args.size,
        max_per_sector=args.max_per_sector,
        min_score=args.min_score,
        risk_pct=args.risk,
        use_regime=not args.no_regime,
        engine=args.engine,
        progress=lambda m: print(f"  ... {m}"),
    ))
