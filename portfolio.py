"""Turn the screen's ranked list into an executable, risk-managed basket.

The scan answers "which shares are worth owning". It does not answer "how many
of each", and those are different questions with different inputs: the score
decides INCLUSION, volatility decides SIZE. A 90-scoring share with twice the
daily range of an 80-scoring one does not deserve twice the money -- at equal
rial amounts it contributes twice the risk, and the portfolio quietly becomes a
bet on that single name.

So every position here is sized to lose the same amount if it hits its stop.
Three caps then bind it further, and the one that actually bound is reported
per row rather than left to be inferred:

    risk        the intended size -- RISK_PER_TRADE_PCT of equity at the stop
    weight      no single name may exceed MAX_POSITION_PCT of equity
    liquidity   no position may exceed MAX_ADV_PARTICIPATION_PCT of the share's
                average daily traded value

That last one is the Tehran-specific cap and the reason it is not optional. A
position worth several days of a share's entire turnover cannot be exited at
the modelled price; in a sell queue (صف فروش) it cannot be exited at all. The
screen's liquidity gate already removes the untradeable, but a name that clears
a 5B-rial floor still cannot absorb a 500M-rial order.

Sector diversification is a hard constraint, not a preference. Ten names drawn
purely by score routinely land eight-deep in one industry, and a portfolio of
eight petrochemicals is one position wearing eight names.
"""

import math

import config


# Fill one name per sector before allowing any sector a second seat, so the
# basket reaches the widest sector spread it can before it doubles up.
def _passes(row, min_score, ignore_score=False):
    """Is this a name the model actually recommends buying?

    Four separate reasons to say no, and none of them is "scored badly":

      * the fundamental verdict was REJECTED or INSUFFICIENT_DATA
      * there is no technical reading -- the share did not trade in the last
        session, so `scored` is False and its 0 is a placeholder, not a verdict
      * under gate_then_flow, the technical score did not clear the gate
      * the inputs sizing needs (price, and something to measure risk with)
        are missing
    """

    if row.get("verdict") not in ("APPROVED", "PROVISIONAL"):
        return False

    technical = row.get("technical") or {}

    if not technical or technical.get("scored") is False:
        return False

    # The gate is a hard admission test, separate from the minimum score: under
    # gate_then_flow the ranking value is a FLOW score, so comparing it to
    # min_score would be comparing two different measurements. A row that
    # pipeline marked ineligible has already failed the gate.
    if not ignore_score:

        if row.get("selection") is not None and not row.get("rank_eligible"):
            return False

        if getattr(config, "SELECTION_MODE", "blend") == "gate_then_flow":

            technical_score = technical.get("score")

            if technical_score is None or technical_score < config.TECHNICAL_GATE:
                return False

            if _rank_value(row) <= 0:
                return False

        else:

            combined = row.get("combined_score")

            if combined is None or combined < min_score:
                return False

    metrics = row.get("metrics") or {}

    close = metrics.get("close")

    if not close or close <= 0:
        return False

    # Clear air to the 120-session ceiling. Over 6,565 point-in-time signals a
    # share within 3% of that high returned +2.17% over the next 20 sessions
    # against +8.08% for one with 20% or more of room, and the filter improved
    # the basket in four windows of five -- the only change tested this way
    # that survived. Unknown headroom is not a rejection: a young listing has
    # no 120-session high and is judged on everything else.
    minimum = getattr(config, "MIN_HEADROOM_PCT", None)

    if minimum is not None and not ignore_score:
        headroom = metrics.get("headroom_pct")
        if headroom is not None and headroom < minimum:
            return False

    return _stop_distance_pct(row) is not None


def _stop_distance_pct(row):
    """How far below entry the stop sits, as a percent of price.

    Preference order matters. ATR is a measured average of the share's own
    daily range and is what a stop should be built from. Realised volatility is
    a fallback: it is annualised, so it has to be converted back to a daily
    figure, and it is computed from closes only -- it cannot see an intraday
    spike through a stop. Which one was used travels with the row.
    """

    metrics = row.get("metrics") or {}

    atr_pct = metrics.get("atr_pct")

    if atr_pct and atr_pct > 0 and math.isfinite(atr_pct):
        return atr_pct * config.STOP_ATR_MULTIPLE

    volatility = metrics.get("volatility_60")

    if volatility and volatility > 0 and math.isfinite(volatility):
        daily = volatility / math.sqrt(250)
        return daily * config.STOP_ATR_MULTIPLE

    return None


def _stop_source(row):
    metrics = row.get("metrics") or {}
    atr_pct = metrics.get("atr_pct")

    if atr_pct and atr_pct > 0 and math.isfinite(atr_pct):
        return "ATR"

    return "realised volatility"


def _trail_distance_pct(row):
    """Trailing distance below the peak close, as a percent of price.

    Built from the same volatility unit as the initial stop so the two are
    directly comparable; None when TRAIL_ATR_MULTIPLE is 0, which leaves the
    initial stop fixed and is the old behaviour.
    """

    multiple = getattr(config, "TRAIL_ATR_MULTIPLE", 0.0)

    if not multiple or multiple <= 0:
        return None

    metrics = row.get("metrics") or {}

    atr_pct = metrics.get("atr_pct")

    if atr_pct and atr_pct > 0 and math.isfinite(atr_pct):
        return atr_pct * multiple

    volatility = metrics.get("volatility_60")

    if volatility and volatility > 0 and math.isfinite(volatility):
        return (volatility / math.sqrt(250)) * multiple

    return None


def _rank_value(row):
    """What this row is ordered by, falling back to the blend.

    pipeline.run_scan sets `rank_score` from scoring.selection_score. Rows that
    never went through it -- hand-built inputs, older cached runs, tests --
    still carry combined_score, so the fallback keeps them orderable instead of
    collapsing every one of them to zero and ranking by the tiebreak alone.
    """

    value = row.get("rank_score")

    if value is None:
        value = row.get("combined_score")

    return value or 0


def _tiebreak_value(row):
    """Separates candidates whose ranking score is identical.

    Worth as much care as the ranking itself. The flow score saturates -- tens
    of names sit at exactly 100 -- so for a ten-name basket this is frequently
    the field that decides membership, not the one that merely orders it.
    """

    mode = getattr(config, "TIEBREAK", "liquidity")

    metrics = row.get("metrics") or {}

    if mode == "liquidity":
        value = metrics.get("avg_value_20")
        if value is not None:
            return value
        # No turnover figure is not a reason to win a tie.
        return 0

    if mode == "risk":
        return row.get("risk_score") or 0

    return row.get("fundamental_score") or 0


def _select(candidates, size, max_per_sector):
    """Greedy pick by score under a per-sector cap, widest spread first.

    Two passes. The first allows one name per sector, which maximises the
    number of distinct industries in the basket. Only if that leaves the
    portfolio short does the second pass hand out second and third seats.
    Sorting by score inside each pass keeps the best name of each sector.
    """

    ranked = sorted(
        candidates,
        key=lambda r: (_rank_value(r), _tiebreak_value(r)),
        reverse=True,
    )

    picked = []
    per_sector = {}

    def sector_of(row):
        return row.get("sector_name") or "بدون صنعت"

    for allowance in range(1, max(1, max_per_sector) + 1):

        for row in ranked:

            if len(picked) >= size:
                break

            if row in picked:
                continue

            sector = sector_of(row)

            if per_sector.get(sector, 0) >= allowance:
                continue

            picked.append(row)
            per_sector[sector] = per_sector.get(sector, 0) + 1

        if len(picked) >= size:
            break

    return picked


def _intended_value(row, equity, risk_budget):
    """Rial value this position wants, before the portfolio gets a say.

    Three per-position ceilings apply here. A fourth and fifth -- the sector
    cap and the cash floor -- cannot be decided one position at a time and are
    applied afterwards by _fit_to_budget().
    """

    metrics = row["metrics"]

    close = float(metrics["close"])

    stop_pct = _stop_distance_pct(row)
    stop_distance = close * (stop_pct / 100.0)

    if stop_distance <= 0:
        return None

    # Intended size: being stopped out costs exactly the risk budget.
    by_risk = risk_budget / stop_distance * close

    # Cap 1 -- concentration.
    by_weight = equity * (config.MAX_POSITION_PCT / 100.0)

    # Cap 2 -- liquidity. Unknown turnover is not permission to ignore this;
    # the screen's gate let the name through on some other basis, so fall back
    # to the concentration cap rather than to no cap at all.
    adv = metrics.get("avg_value_20")

    if adv and adv > 0:
        by_liquidity = adv * (config.MAX_ADV_PARTICIPATION_PCT / 100.0)
    else:
        by_liquidity = by_weight

    value, binding = min(
        (by_risk, "risk"),
        (by_weight, "weight cap"),
        (by_liquidity, "liquidity"),
        key=lambda pair: pair[0],
    )

    return {
        "close": close,
        "stop_distance": stop_distance,
        "stop_pct": stop_pct,
        "stop_source": _stop_source(row),
        "adv": adv,
        "value": value,
        "bound_by": binding,
    }


def _fit_to_budget(sized, equity, notes, cash_pct=None):
    """Scale intended positions down until the whole basket fits.

    Per-position caps cannot keep a portfolio solvent on their own: ten names
    each allowed 15% of equity add up to 150%. Two portfolio-level limits are
    applied here, in this order, because the sector cap can only tighten and
    the cash floor has to be measured on what survives it.

        MAX_SECTOR_PCT   total held in any one industry
        MIN_CASH_PCT     equity that must remain uninvested

    Scaling is proportional within the affected group, so the relative sizes
    the risk model chose are preserved -- a name sized down for volatility
    stays smaller than its neighbours after the cut.
    """

    if not sized:
        return

    # ---- sector cap ------------------------------------------------------

    by_sector = {}

    for item in sized:
        by_sector.setdefault(item["sector_name"], []).append(item)

    sector_ceiling = equity * (config.MAX_SECTOR_PCT / 100.0)

    for sector, group in by_sector.items():

        total = sum(item["value"] for item in group)

        if total <= sector_ceiling or total <= 0:
            continue

        scale = sector_ceiling / total

        for item in group:
            item["value"] *= scale
            item["scaled_by"] = "sector cap"

        notes.append(
            f"صنعت «{sector}» به سقف {config.MAX_SECTOR_PCT:.0f}٪ سرمایه محدود شد."
        )

    # ---- cash floor ------------------------------------------------------
    #
    # The floor is a config constant by default, but the market regime can
    # raise it. A bear market does not make the model's ten best names bad
    # picks; it makes owning ten names of anything the wrong size of bet.

    cash = config.MIN_CASH_PCT if cash_pct is None else max(cash_pct, 0.0)

    investable = equity * (1 - cash / 100.0)

    total = sum(item["value"] for item in sized)

    if total > investable and total > 0:

        scale = investable / total

        for item in sized:
            item["value"] *= scale
            item.setdefault("scaled_by", "cash floor")

        notes.append(
            f"مجموع موقعیت‌ها به {100 - cash:.0f}٪ سرمایه مقیاس شد "
            f"تا کف نقدینگی {cash:.0f}٪ حفظ شود "
            f"(ضریب {scale:.2f})."
        )


def _finalise(row, item, equity):
    """Turn a fitted rial value into whole shares and a full position row."""

    close = item["close"]

    shares = int(math.floor(item["value"] / close)) if close > 0 else 0

    if shares <= 0:
        return None

    value = shares * close

    weight_pct = value / equity * 100 if equity else 0

    # A position too small to matter is not a small position -- it is noise
    # that still costs a round trip in commission and a line of attention.
    if weight_pct < config.MIN_POSITION_PCT:
        return None

    stop_distance = item["stop_distance"]

    # Target is set from the same volatility unit as the stop, so the
    # reward-to-risk ratio is a decision the config makes once rather than a
    # number that drifts per symbol. Distance to target = MIN_RISK_REWARD
    # times distance to stop, by construction.
    target_distance = stop_distance * config.MIN_RISK_REWARD

    risk_rials = shares * stop_distance

    adv = item["adv"]

    technical = row.get("technical") or {}
    metrics = row.get("metrics") or {}

    # ---- trailing stop ---------------------------------------------------
    #
    # The level quoted is where the stop sits *today*, on a position opened
    # today: the peak close since entry is the entry itself, so the trail has
    # not yet moved. It only starts to govern once price advances far enough
    # for (peak - trail distance) to clear the initial stop, which is why the
    # effective stop is the max of the two and never moves down.
    trail_pct = _trail_distance_pct(row)

    if trail_pct is not None:
        trail_distance = close * (trail_pct / 100.0)
        trail_price_now = close - trail_distance
        effective_stop = max(close - stop_distance, trail_price_now)
        # Advance needed before the trail overtakes the fixed stop.
        activates_at = close + max(0.0, trail_distance - stop_distance)
    else:
        trail_distance = None
        trail_price_now = None
        effective_stop = close - stop_distance
        activates_at = None

    return {
        "ins_code": row.get("ins_code"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "sector_name": item["sector_name"],
        "verdict": row.get("verdict"),
        "fundamental_score": row.get("fundamental_score"),
        # The screen's fundamental score and the one the blend actually used
        # are not always the same number: where Codal statements are parsed the
        # engine substitutes the Codal score, and reporting only the former
        # showed a column that did not explain the rank beside it.
        "codal_score": (row.get("codal") or {}).get("score"),
        "fundamental_source": row.get("fundamental_source"),
        "technical_score": technical.get("score"),
        # Both readings that feed the blend travel with the position. Without
        # them a row shows an overall score with no way to see which component
        # earned it -- and flow in particular moves the ranking enough that its
        # absence from the output made baskets look arbitrary.
        "flow_score": row.get("flow_score"),
        "risk_score": row.get("risk_score"),
        "combined_score": row.get("combined_score"),
        # What this position was actually ordered by, and on what basis. A
        # basket that cannot say why one name outranked another is not
        # auditable, and under gate_then_flow the ordering value is no longer
        # the score displayed beside it.
        "rank_score": row.get("rank_score"),
        "rank_basis": row.get("rank_basis"),
        "status": technical.get("status"),
        "trend": technical.get("trend"),
        "close": round(close, 2),
        "volatility_60": metrics.get("volatility_60"),
        "atr_pct": metrics.get("atr_pct"),
        "adx": metrics.get("adx"),
        "avg_value_20": adv,
        "pe": metrics.get("pe"),
        "shares": shares,
        "position_value": round(value, 0),
        "weight_pct": round(weight_pct, 2),
        "entry_low": round(close * 0.99, 2),
        "entry_high": round(close * 1.01, 2),
        "stop_price": round(effective_stop, 2),
        "initial_stop_price": round(close - stop_distance, 2),
        "stop_distance_pct": round(item["stop_pct"], 2),
        "stop_source": item["stop_source"],
        "trail_atr_multiple": (
            getattr(config, "TRAIL_ATR_MULTIPLE", 0.0) if trail_pct else None
        ),
        "trail_distance_pct": round(trail_pct, 2) if trail_pct else None,
        "trail_price": round(trail_price_now, 2) if trail_price_now else None,
        "trail_activates_above": round(activates_at, 2) if activates_at else None,
        "target_price": round(close + target_distance, 2),
        "target_distance_pct": round(item["stop_pct"] * config.MIN_RISK_REWARD, 2),
        "risk_reward": config.MIN_RISK_REWARD,
        "risk_rials": round(risk_rials, 0),
        "risk_pct": round(risk_rials / equity * 100, 3) if equity else None,
        # Two different facts, kept apart. `bound_by` is why this position is
        # not larger on its own terms; `scaled_by` is the portfolio-level limit
        # that then cut it further. Collapsing them into one field hid the
        # per-position reason behind whichever cap happened to apply last.
        "bound_by": item["bound_by"],
        "scaled_by": item.get("scaled_by"),
        "adv_participation_pct": (
            round(value / adv * 100, 2) if adv and adv > 0 else None
        ),
    }


def build(final_rows, equity=None, size=None, max_per_sector=None,
          min_score=None, risk_pct=None, market_regime=None):
    """Build the basket. Returns a report dict, never raises on thin input.

    `market_regime` is the report from regime.detect(). When supplied, its cash
    target overrides MIN_CASH_PCT -- exposure follows the market's condition
    rather than being fixed regardless of it.
    """

    equity = equity or config.ACCOUNT_EQUITY
    size = size or config.PORTFOLIO_SIZE
    max_per_sector = max_per_sector or config.MAX_PER_SECTOR
    risk_pct = config.RISK_PER_TRADE_PCT if risk_pct is None else risk_pct
    min_score = config.PORTFOLIO_MIN_SCORE if min_score is None else min_score

    rows = list(final_rows or [])

    notes = []

    cash_pct = None

    if market_regime and market_regime.get("available"):
        cash_pct = market_regime.get("cash_target_pct")
        notes.append(
            f"وضعیت بازار «{market_regime.get('label')}» تشخیص داده شد — "
            f"کف نقدینگی روی {cash_pct:.0f}٪ تنظیم شد."
        )

    eligible = [r for r in rows if _passes(r, min_score)]

    # Nothing cleared the bar. Say why rather than returning a silent blank:
    # "no candidates" and "candidates exist but none scores 60" are different
    # facts and lead to different actions.
    if not eligible:

        # Relaxing the score test is what separates "nothing qualifies" from
        # "things qualify but none scores well enough". Under gate_then_flow
        # the bar that was missed is the TECHNICAL GATE, not the blend, so the
        # note has to name the number the user would actually have to change.
        relaxed = [r for r in rows if _passes(r, 0, ignore_score=True)]

        gated = getattr(config, "SELECTION_MODE", "blend") == "gate_then_flow"

        if relaxed and gated:
            best = max(
                (r.get("technical") or {}).get("score") or 0 for r in relaxed
            )
            notes.append(
                f"هیچ نمادی به دروازه‌ی تکنیکال {config.TECHNICAL_GATE} نرسید — "
                f"بهترین امتیاز تکنیکال موجود {best} است. سبدی ساخته نشد."
            )
        elif relaxed:
            best = max(r.get("combined_score") or 0 for r in relaxed)
            notes.append(
                f"هیچ نمادی به حد نصاب امتیاز ترکیبی {min_score} نرسید — "
                f"بهترین امتیاز موجود {best} است. سبدی ساخته نشد."
            )
        else:
            notes.append(
                "هیچ نماد واجد شرایطی وجود ندارد: یا حکم بنیادی تأییدشده نیست، "
                "یا نماد در آخرین جلسه معامله نشده، یا داده‌ی لازم برای "
                "اندازه‌گیری ریسک در دسترس نیست."
            )

        return _empty(equity, size, max_per_sector, min_score, risk_pct, notes,
                      candidates=len(relaxed))

    picked = _select(eligible, size, max_per_sector)

    if len(picked) < size:
        notes.append(
            f"تنها {len(picked)} نماد از {size} نماد درخواستی واجد شرایط بود "
            f"(سقف {max_per_sector} نماد در هر صنعت)."
        )

    risk_budget = equity * (risk_pct / 100.0)

    # Three stages, deliberately separate. Sizing decides what each position
    # WANTS on its own risk terms; fitting decides what the portfolio can
    # AFFORD across all of them; finalising rounds to whole shares. Collapsing
    # them was the bug that let ten 15% positions add up to 150% of equity.
    sized = []
    dropped = []

    for row in picked:

        item = _intended_value(row, equity, risk_budget)

        if item is None:
            dropped.append(row["symbol"])
            continue

        item["sector_name"] = row.get("sector_name") or "بدون صنعت"
        item["row"] = row
        sized.append(item)

    _fit_to_budget(sized, equity, notes, cash_pct=cash_pct)

    positions = []

    for item in sized:

        position = _finalise(item["row"], item, equity)

        if position is None:
            dropped.append(item["row"]["symbol"])
            continue

        positions.append(position)

    if dropped:
        notes.append(
            f"حذف‌شده چون پس از اعمال محدودیت‌ها به کف {config.MIN_POSITION_PCT:.0f}٪ "
            "سرمایه نرسیدند: " + "، ".join(dropped)
        )

    # Rank after the drops so the numbering has no holes.
    for n, position in enumerate(positions, start=1):
        position["rank"] = n

    invested = sum(p["position_value"] for p in positions)
    total_risk = sum(p["risk_rials"] for p in positions)

    sectors_used = {}
    for position in positions:
        sectors_used[position["sector_name"]] = (
            sectors_used.get(position["sector_name"], 0) + 1
        )

    # Total risk is the sum of per-position risks, which assumes every stop is
    # hit at once and nothing offsets anything. That is the pessimistic reading
    # and the right one to plan against: correlations go to 1 in exactly the
    # market that hits ten stops on the same day.
    if invested > equity:
        notes.append(
            f"ارزش کل موقعیت‌ها ({invested/1e9:.2f}B) از سرمایه "
            f"({equity/1e9:.2f}B) بیشتر است — سقف وزن هر نماد را کم کنید."
        )

    return {
        "positions": positions,
        "settings": {
            "equity": equity,
            "size": size,
            "max_per_sector": max_per_sector,
            "min_score": min_score,
            "risk_pct": risk_pct,
            "max_position_pct": config.MAX_POSITION_PCT,
            "max_sector_pct": config.MAX_SECTOR_PCT,
            "min_position_pct": config.MIN_POSITION_PCT,
            "cash_pct": config.MIN_CASH_PCT if cash_pct is None else cash_pct,
            "max_adv_participation_pct": config.MAX_ADV_PARTICIPATION_PCT,
            "stop_atr_multiple": config.STOP_ATR_MULTIPLE,
            "trail_atr_multiple": getattr(config, "TRAIL_ATR_MULTIPLE", 0.0),
            "risk_reward": config.MIN_RISK_REWARD,
        },
        "market_regime": (
            {
                "regime": market_regime.get("regime"),
                "label": market_regime.get("label"),
                "cash_target_pct": market_regime.get("cash_target_pct"),
                "confidence": market_regime.get("confidence"),
            }
            if market_regime and market_regime.get("available") else None
        ),
        "totals": {
            "positions": len(positions),
            "sectors": len(sectors_used),
            "invested": round(invested, 0),
            "cash": round(equity - invested, 0),
            "invested_pct": round(invested / equity * 100, 2) if equity else None,
            "total_risk": round(total_risk, 0),
            "total_risk_pct": round(total_risk / equity * 100, 2) if equity else None,
        },
        "sector_counts": sectors_used,
        "candidates": len(eligible),
        "notes": notes,
        "currency": config.CURRENCY,
    }


def _empty(equity, size, max_per_sector, min_score, risk_pct, notes,
           candidates=0):
    return {
        "positions": [],
        "settings": {
            "equity": equity,
            "size": size,
            "max_per_sector": max_per_sector,
            "min_score": min_score,
            "risk_pct": risk_pct,
            "max_position_pct": config.MAX_POSITION_PCT,
            "max_sector_pct": config.MAX_SECTOR_PCT,
            "min_position_pct": config.MIN_POSITION_PCT,
            "cash_pct": config.MIN_CASH_PCT,
            "max_adv_participation_pct": config.MAX_ADV_PARTICIPATION_PCT,
            "stop_atr_multiple": config.STOP_ATR_MULTIPLE,
            "trail_atr_multiple": getattr(config, "TRAIL_ATR_MULTIPLE", 0.0),
            "risk_reward": config.MIN_RISK_REWARD,
        },
        "market_regime": None,
        "totals": {
            "positions": 0, "sectors": 0, "invested": 0,
            "cash": equity, "invested_pct": 0.0,
            "total_risk": 0, "total_risk_pct": 0.0,
        },
        "sector_counts": {},
        "candidates": candidates,
        "notes": notes,
        "currency": config.CURRENCY,
    }


if __name__ == "__main__":

    import pipeline

    report = pipeline.run_scan(progress=lambda m: print(f"  ... {m}"))

    basket = build(report["final"])

    print()
    print("=" * 104)
    print("SIRAT AI — پیشنهاد سبد دارایی / RISK-MANAGED PORTFOLIO")
    print("=" * 104)

    settings_ = basket["settings"]
    print(f"Equity {settings_['equity']:,.0f} {basket['currency']}   "
          f"risk/position {settings_['risk_pct']}%   "
          f"max {settings_['max_per_sector']} per sector   "
          f"min combined score {settings_['min_score']}")
    print(f"Candidates that qualified: {basket['candidates']}")
    print("-" * 104)

    if not basket["positions"]:
        for note in basket["notes"]:
            print(f"  {note}")
    else:
        print(f"{'#':<3}{'SYMBOL':<12}{'SECTOR':<22}{'COMB':>5}{'CLOSE':>10}"
              f"{'STOP':>9}{'TARGET':>9}{'SHARES':>10}{'VALUE':>14}"
              f"{'WGT':>7}{'RISK%':>7}{'BOUND BY':>12}")
        print("-" * 116)

        for p in basket["positions"]:
            print(
                f"{p['rank']:<3}{p['symbol']:<12}{(p['sector_name'] or '')[:20]:<22}"
                f"{p['combined_score']:>5}{p['close']:>10,.0f}"
                f"{p['stop_price']:>9,.0f}{p['target_price']:>9,.0f}"
                f"{p['shares']:>10,}{p['position_value']:>14,.0f}"
                f"{p['weight_pct']:>6.1f}%{p['risk_pct']:>6.2f}%{p['bound_by']:>12}"
            )

        totals = basket["totals"]
        print("-" * 104)
        print(f"{totals['positions']} positions across {totals['sectors']} sectors   "
              f"invested {totals['invested']:,.0f} ({totals['invested_pct']}%)   "
              f"cash {totals['cash']:,.0f}")
        print(f"Total risk if every stop is hit: {totals['total_risk']:,.0f} "
              f"({totals['total_risk_pct']}% of equity)")

        for note in basket["notes"]:
            print(f"\nNOTE: {note}")
