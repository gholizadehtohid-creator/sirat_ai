"""The scan: six hundred names in, a handful out, and the funnel that explains it.

THE SHAPE OF THE RUN, AND WHY IT IS IN THIS ORDER
--------------------------------------------------
The expensive readings run last, on the survivors, not first on everybody:

    1  universe      equities with stored history
    2  liquidity     config.MIN_DAILY_VALUE, the same floor the screener, the
                     money-flow ranking and the technical scan all use. Ranking
                     names that cannot absorb a position is the failure E20/E21
                     documented in this programme's own research.
    3  integrity     no impossible bars in the window (price_history has
                     carried them before)
    4  MFI > 70      pure pandas, ~600 names, cheap
    5  range floor   pure pandas, cheap -- and this is the gate that does the
                     work. On the session this was written: 606 liquid, 306
                     past the MFI gate, 38 in the bottom third of their range,
                     8 with both.
    6  the rest      supports (pivot detection), client-type flow, valuation,
                     size, volume -- run on the tens of names that survived,
                     not on the hundreds that did not.

Every stage's count is reported. A screen that returns three names without
saying how it got from six hundred to three is asking to be trusted; one that
shows the funnel can be argued with.

WHICH ENGINES THIS MODULE CONNECTS TO, AND HOW
-----------------------------------------------
    Technical Engine    technical.support_resistance and levels.fibonacci for
                        the supports; indicators.calculate_indicators for the
                        finalists' indicator frame.
    Fundamental Engine  fundamental_engine.sector_medians for the sector P/E,
                        including its rule that fewer than three comparables is
                        not a median.
    Liquidity Engine    moneyflow.measure for the حقیقی/حقوقی window -- the
                        reading is TAKEN from it, never recomputed, so this
                        scanner and the panel's flow view cannot print two
                        different numbers for the same window.
    Decision Engine V2  factor_engine.evaluate_symbol on the finalists only.
                        Reported ALONGSIDE the accumulation score, never blended
                        into it. When V2 says AVOID and this scanner says 82,
                        that disagreement is the most useful thing on the row
                        and averaging it away would destroy it.
    News Engine         event_intelligence, market-level context only, worth
                        ZERO points. That package's own contract is that news
                        never reaches the decision path; attaching a headline to
                        a row is reporting, and giving it a weight would be
                        breaking that contract from the outside.
"""

import math
from datetime import datetime, timezone

import pandas as pd

import config
import database
import fundamental_engine
import moneyflow

from . import accumulation, flow, microstructure, position, scoring, store
from .scoring import WEIGHTS


# History depth per name. technical.SCAN_BARS is 300 and this matches it, so
# the two whole-market scans warm their indicators over the same window.
SCAN_BARS = 300

# The liquidity floor, shared with every other whole-market view here.
MIN_TURNOVER = config.MIN_DAILY_VALUE

# Client-type sessions loaded per survivor. FLOW_BASELINE_WINDOW, not
# FLOW_WINDOW: moneyflow.analyse reports INSUFFICIENT below FLOW_MIN_SESSIONS
# and the کد به کد baseline needs the sessions BEFORE the recent window. Loading
# only FLOW_WINDOW made every name INSUFFICIENT once already in this codebase --
# technical.py carries the same comment for the same reason.
FLOW_BARS = config.FLOW_BASELINE_WINDOW

# Sessions of staleness beyond which the list stops being a watchlist and
# becomes a historical record of a session that has already passed.
#
# WHY THIS BLOCKS RATHER THAN WARNS. It warned first, in a banner, and that was
# not enough: the list was acted on twice while the equity bars sat three
# sessions behind, and both times names that were clean at the last stored close
# were sitting in a صف by the time they were read. On a market whose daily band
# is 3%, two sessions is up to 6% of price and an entire change of queue state,
# and the scanner has no way to see any of it.
#
# One session behind is normal -- today's bars land after today's close.
MAX_STALE_SESSIONS = 1

# The standing caveat. It is not decoration: every ingredient in this model has
# been tested by this programme and none of them survived as a return edge.
CAVEAT = (
    "این اسکنر یک «فهرست تحت نظر» می‌سازد، نه سیگنال خرید. هر جزء آن قبلاً در "
    "همین برنامه آزمون شده و رد شده است: E19 نشان داد سیگنال تکنیکال خالص از "
    "هزینه لبه‌ای ندارد، E21 نشان داد جریان حقیقی/حقوقی «قابل معامله بودن» "
    "(قفل صف) را پیش‌بینی می‌کند نه بازده را، و E24 علامت آن سیگنال را رسماً "
    "رد کرد. ترکیب چند چیزِ بدون لبه، لبه نمی‌سازد. ارزش این فهرست در کوتاه "
    "بودن و ثبت‌شدن آن است: هر اسکن با زمان محاسبه ذخیره می‌شود تا بعداً قابل "
    "سنجش باشد."
)


def _finite(value):
    if value is None:
        return None

    try:
        out = float(value)
    except (TypeError, ValueError):
        return None

    return out if math.isfinite(out) else None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sessions_behind(as_of):
    """Trading sessions between `as_of` and today, Thursday/Friday aside.

    Counted in SESSIONS, not days: a Wednesday close read on the following
    Saturday is one session behind, not three, and telling the reader "three
    days" would overstate a normal weekend. Tehran trades Saturday to
    Wednesday.
    """

    if as_of is None:
        return 0

    today = pd.Timestamp.now().normalize()

    if today <= as_of:
        return 0

    days = pd.date_range(as_of + pd.Timedelta(days=1), today, freq="D")

    # Thursday = 3, Friday = 4 in pandas' Monday-based weekday numbering.
    return int(sum(1 for d in days if d.weekday() not in (3, 4)))



# ==============================
# THE AS-OF DATE
# ==============================

# Share of the eligible population that must have a bar on a date before it
# counts as a complete session. Below this the date is a partial fill -- some
# names collected, most not -- and scoring a cross-section on it compares names
# that were read on different days.
SESSION_COVERAGE = 0.90


def _as_of(frames, coverage=SESSION_COVERAGE):
    """The most recent date on which the population was actually read.

    WHY THIS EXISTS. The stores do not advance together. On the session this was
    written, 749 equities carried price bars to 2026-08-19 while 636 carried
    client-type rows to 2026-08-23 -- so a score built from "the latest of
    everything" mixed a money-flow reading from Sunday with a price reading from
    Wednesday, and called the pair one observation of one name.

    Worse, it did it unevenly: a handful of names had price bars three days
    fresher than the rest, so the cross-sectional median that the advance gate
    compares against was itself computed across different days.

    So the scan picks ONE date -- the newest on which `coverage` of the
    population has a bar -- and truncates every series to it. A cross-section
    assembled from different dates is not a cross-section.
    """

    if not frames:
        return None

    counts = {}

    for frame in frames.values():
        if frame is None or frame.empty or "date" not in frame.columns:
            continue
        stamp = frame["date"].iloc[-1]
        if pd.isna(stamp):
            continue
        day = pd.Timestamp(stamp).normalize()
        counts[day] = counts.get(day, 0) + 1

    if not counts:
        return None

    total = sum(counts.values())
    needed = total * coverage

    # Walk back from the newest date, accumulating names whose series reaches
    # at least that far. The first date where enough of them do is the last
    # complete session.
    reached = 0

    for day in sorted(counts, reverse=True):
        reached += counts[day]
        if reached >= needed:
            return day

    return max(counts)


def _truncate(frame, as_of):
    """The frame as it stood at the close of `as_of`. Never a copy of the
    whole thing when nothing needs removing -- most names end on that date."""

    if frame is None or as_of is None or "date" not in frame.columns:
        return frame

    last = frame["date"].iloc[-1]

    if pd.notna(last) and pd.Timestamp(last).normalize() <= as_of:
        return frame

    return frame[pd.to_datetime(frame["date"], errors="coerce") <= as_of]


# ==============================
# MARKET-WIDE CONTEXT, COMPUTED ONCE
# ==============================

def _market_context():
    """Sector P/E medians and the market-cap terciles, from one snapshot read.

    Both are cross-sectional facts about today's market, identical for every
    name, so they are computed once rather than per candidate. The snapshot's
    own `pe` column is the source for BOTH a name's multiple and its sector's
    median -- consistency matters more here than precision: a numerator struck
    at today's price against a denominator struck at last month's would produce
    a discount that is mostly calendar.
    """

    snapshot = database.load_snapshot()

    context = {
        "sector_pe": {},
        "cap_p33": None,
        "cap_p67": None,
        "by_code": {},
        "sector_counts": {},
    }

    if snapshot is None or snapshot.empty:
        return context

    snapshot = snapshot.copy()
    snapshot["ins_code"] = snapshot["ins_code"].astype(str)

    instruments = database.load_instruments(equities_only=True)
    sector_of = {str(r["ins_code"]): r.get("sector_name") for r in instruments}

    caps = []
    by_sector = {}

    for _, row in snapshot.iterrows():

        code = row["ins_code"]

        if code not in sector_of:
            # Not an equity. Funds and rights have a price and no sector to
            # compare a multiple against; gold funds are handled by gold.py.
            continue

        pe = _finite(row.get("pe"))
        close = _finite(row.get("close"))
        shares = _finite(row.get("total_shares"))

        cap = (close * shares) if (close and shares and close > 0 and shares > 0) \
            else None

        context["by_code"][code] = {
            "pe": pe if (pe is not None and pe > 0) else None,
            "market_cap": cap,
            "sector": sector_of.get(code),
        }

        if cap:
            caps.append(cap)

        if pe is not None and pe > 0:
            by_sector.setdefault(sector_of.get(code), []).append({"pe": pe})

    # The sector medians, through the fundamental engine's own function so its
    # "fewer than three comparables is an anecdote, not a median" rule applies
    # here too rather than being re-implemented slightly differently.
    for sector, rows in by_sector.items():
        if not sector:
            continue
        medians = fundamental_engine.sector_medians(rows)
        context["sector_counts"][sector] = len(rows)
        if medians.get("pe"):
            context["sector_pe"][sector] = medians["pe"]

    if caps:
        series = pd.Series(caps)
        context["cap_p33"] = float(series.quantile(1 / 3))
        context["cap_p67"] = float(series.quantile(2 / 3))

    return context


def _news_context():
    """Market-level news, REPORT ONLY, worth zero points.

    event_intelligence's contract is that news never reaches the decision path.
    This returns the TSE lean as a line of context for the reader and nothing
    else -- it is not passed to scoring, it is not in WEIGHTS, and it cannot
    move a rank. Failure is silent: a scanner that cannot run because a news
    feed is down would be a scanner with a dependency it was promised not to
    have.
    """

    try:
        from event_intelligence import report as news_report

        built = news_report.build(window_hours=72, top=5)
    except Exception:                                           # noqa: BLE001
        return None

    markets = (built or {}).get("markets") or {}

    if isinstance(markets, dict):
        tse = markets.get("tse")
    else:
        tse = next((m for m in markets if m.get("market") == "tse"), None)

    if not tse:
        return None

    outlook = tse.get("outlook") or {}

    return {
        "market": "tse",
        "label": tse.get("label"),
        "lean": outlook.get("lean"),
        "lean_label": outlook.get("lean_label"),
        "drivers": outlook.get("drivers") or [],
        "articles": tse.get("total"),
        "note": ("خبرها فقط زمینه‌ی اطلاعاتی‌اند و در امتیاز این اسکنر هیچ "
                 "وزنی ندارند."),
    }


# ==============================
# ONE NAME
# ==============================

def _build_candidate(code, meta, frame, flow_frame, context,
                     market_move=None):
    """Every reading for one name, before scoring and before the vetoes."""

    close = _finite(frame["close"].iloc[-1])

    candidate = {
        "ins_code": code,
        "symbol": meta.get("symbol_fa"),
        "name": meta.get("name_fa"),
        "sector": meta.get("sector_name"),
        "close": close,
        "turnover": _finite(frame["value"].iloc[-1])
        if "value" in frame.columns else None,
    }

    candidate["integrity"] = position.integrity(frame)
    candidate["tradability"] = position.tradability(frame)
    candidate["locks"] = microstructure.locks(frame)
    candidate["advance"] = microstructure.advance(frame, market=market_move)
    candidate["mfi"] = flow.read(frame)
    candidate["range"] = position.range_position(frame)
    candidate["supports"] = position.supports(
        frame, close, range_reading=candidate["range"])

    ratio = position.volume_ratio(frame)
    candidate["volume"] = {"ratio": ratio}

    # --- liquidity engine: the reading is taken, never recomputed ------------
    reading = moneyflow.measure(flow_frame) if flow_frame is not None else None
    candidate["transfer"] = accumulation.transfer(reading)
    candidate["code_to_code"] = accumulation.code_to_code(
        flow_frame, volume_ratio=ratio)

    # --- fundamental engine: the name against its own sector ----------------
    snapshot = (context.get("by_code") or {}).get(code) or {}
    sector = snapshot.get("sector") or meta.get("sector_name")

    candidate["valuation"] = {
        "pe": snapshot.get("pe"),
        "sector": sector,
        "sector_pe": (context.get("sector_pe") or {}).get(sector),
        "comparables": (context.get("sector_counts") or {}).get(sector),
    }

    cap = snapshot.get("market_cap")

    candidate["size"] = {
        "market_cap": cap,
        "band": scoring.size_band(cap, context.get("cap_p33"),
                                  context.get("cap_p67")),
    }

    return candidate


def _engine_v2(frame, params=None):
    """The Decision Engine V2 verdict for one finalist, or None.

    Run on finalists only -- V2 scoring the whole universe costs ~50s, and this
    scanner has already reduced the universe to a handful by the time it asks.
    Wrapped: a V2 failure must cost this scan a column, not the run.
    """

    try:
        import indicators
        import factor_engine
        import swing_advisor

        enriched = indicators.calculate_indicators(frame, params)

        if enriched is None or enriched.empty:
            return None

        result = factor_engine.evaluate_symbol(
            frame=enriched, index_closes=swing_advisor.index_closes())

        decision = (result or {}).get("decision") or {}
        swing = (result or {}).get("swing") or {}

        return {
            "action": decision.get("action"),
            "label": decision.get("label"),
            "swing_score": swing.get("score"),
            "reasons": (decision.get("reasons") or [])[:2],
        }
    except Exception:                                           # noqa: BLE001
        return None


# ==============================
# THE SCAN
# ==============================

def scan(top=20, min_turnover=MIN_TURNOVER, params=None, persist=True,
         with_engine=True, with_news=True):
    """Whole-market scan for names being accumulated at their floor.

    Returns the ranked candidates, the funnel that produced them, the vetoes
    that removed names at each gate, and the standing caveat. Writes the run to
    `smart_money_signals` unless `persist=False` (the tests pass False; nothing
    else should).
    """

    started = _now()

    instruments = database.load_instruments(equities_only=True)
    by_code = {str(r["ins_code"]): r for r in instruments}
    codes = list(by_code)

    frames = database.load_history_tail(codes, SCAN_BARS)

    # One date for the whole cross-section, before anything is read off it.
    as_of = _as_of(frames)

    if as_of is not None:
        frames = {code: _truncate(frame, as_of)
                  for code, frame in frames.items()}

    context = _market_context()

    counts = {
        "universe": len(codes),
        "no_data": 0,
        "illiquid": 0,
        "dirty_data": 0,
        "liquid": 0,
        "thin": 0,
        "traded_enough": 0,
        "queue_locked": 0,
        "already_moved": 0,
        "still_quiet": 0,
        "passed_mfi": 0,
        "passed_range": 0,
        "scored": 0,
        "qualified": 0,
    }

    # Why names fell out, counted by rule. Without this the scanner is a black
    # box that returns three names and no way to tell a working filter from a
    # broken one.
    rejected = {}

    survivors = []

    # The session this scan describes. One date for every name, taken from the
    # population rather than from whichever name happened to be read first.
    session_date = None if as_of is None else str(as_of.date())

    # The market yardstick for the advance gate. Built from the liquid, clean,
    # tradable population in a first pass so the benchmark and the candidates
    # come from exactly the same set -- a median over the whole universe would
    # include the suspended and the untradable, which move differently.
    market_move = None
    eligible = {}

    for code in codes:

        frame = frames.get(code)

        if frame is None or frame.empty or "close" not in frame.columns:
            counts["no_data"] += 1
            continue

        turnover = _finite(frame["value"].iloc[-1]) \
            if "value" in frame.columns else None

        if turnover is None or turnover < min_turnover:
            counts["illiquid"] += 1
            continue

        counts["liquid"] += 1

        integrity = position.integrity(frame)

        if not integrity.get("clean", False):
            counts["dirty_data"] += 1
            rejected["data_integrity"] = rejected.get("data_integrity", 0) + 1
            continue

        # Thin trading, checked BEFORE the MFI gate rather than after. A
        # suspended name's MFI is not a weak reading to be scored low; it is
        # arithmetic over a window that mostly did not happen, and letting it
        # reach the gate at all means the gate is deciding on noise.
        tradable = position.tradability(frame)

        if not tradable.get("tradable", False):
            counts["thin"] += 1
            rejected["thin_trading"] = rejected.get("thin_trading", 0) + 1
            continue

        counts["traded_enough"] += 1

        eligible[code] = frame

    # --- the market yardstick, between the two passes -----------------------
    #
    # The advance gate compares a name against the market, and "the market" has
    # to be the SAME population the candidates come from. A median over the
    # whole universe would fold in the suspended and the untradable, which do
    # not move the way tradable names move, and the benchmark would then be
    # measuring the wrong thing in exactly the regime where it matters most.
    market_move = microstructure.market_advance(eligible)
    market_state = microstructure.market_state(eligible)

    for code, frame in eligible.items():

        # --- gate 1: MFI above the brief's floor ---------------------------
        mfi = flow.read(frame)

        if mfi is None or mfi["value"] <= scoring.MFI_FLOOR:
            rejected["mfi_floor"] = rejected.get("mfi_floor", 0) + 1
            continue

        counts["passed_mfi"] += 1

        # --- gate 2: still at the floor of its own range --------------------
        ranged = position.range_position(frame)

        if ranged is None:
            rejected["no_range"] = rejected.get("no_range", 0) + 1
            continue

        if ranged["at_ceiling"]:
            rejected["at_ceiling"] = rejected.get("at_ceiling", 0) + 1
            continue

        counts["passed_range"] += 1

        # --- gate 3: not sitting in a queue right now ----------------------
        locked = microstructure.locks(frame)

        if (locked or {}).get("locked_now"):
            rejected["queue_locked"] = rejected.get("queue_locked", 0) + 1
            counts["queue_locked"] += 1
            continue

        # --- gate 4: the move has not already happened ----------------------
        moved = microstructure.advance(frame, market=market_move)

        if (moved or {}).get("quiet") is False:
            rejected["already_moved"] = rejected.get("already_moved", 0) + 1
            counts["already_moved"] += 1
            continue

        counts["still_quiet"] += 1

        survivors.append(code)

    # --- the expensive half, on survivors only ------------------------------
    flow_frames = database.load_client_type_tail(survivors, FLOW_BARS) \
        if survivors else {}

    qualified = []

    for code in survivors:

        candidate = _build_candidate(
            code, by_code.get(code, {}), frames[code],
            flow_frames.get(code), context, market_move=market_move)

        counts["scored"] += 1

        blocked = scoring.vetoes(candidate)

        if blocked:
            for rule, _ in blocked:
                rejected[rule] = rejected.get(rule, 0) + 1
            continue

        scored = scoring.score(candidate)

        counts["qualified"] += 1

        qualified.append((candidate, scored))

    qualified.sort(key=lambda pair: pair[1]["score"], reverse=True)

    # How much of the model actually had inputs. The market snapshot covers
    # fewer instruments than the universe does, so a candidate can be missing
    # its P/E and its market cap entirely -- and a reader comparing a 76 with a
    # 58 deserves to know when the 58 was scored out of 75 available points.
    counts["with_pe"] = sum(
        1 for c, _ in qualified if (c.get("valuation") or {}).get("pe"))
    counts["with_sector_pe"] = sum(
        1 for c, _ in qualified if (c.get("valuation") or {}).get("sector_pe"))
    counts["with_market_cap"] = sum(
        1 for c, _ in qualified if (c.get("size") or {}).get("market_cap"))
    counts["with_code_to_code"] = sum(
        1 for c, _ in qualified if (c.get("code_to_code") or {}).get("genuine"))

    rows = []

    for rank, (candidate, scored) in enumerate(qualified[:top], start=1):

        rows.append(_row(rank, candidate, scored, frames[candidate["ins_code"]],
                         params=params, with_engine=with_engine))

    # How old the picture is. The scanner can only ever describe the last
    # session it has data for, and a reader looking at a live market needs to
    # be told when that is not today -- a name that was fine at the last stored
    # close can be sitting in a صف by the time the list is read.
    stale = None

    if as_of is not None:
        age = (pd.Timestamp.now().normalize() - as_of).days
        behind = _sessions_behind(as_of)

        stale = {
            "as_of": str(as_of.date()),
            "age_days": int(age),
            "sessions_behind": behind,
            "current": behind <= MAX_STALE_SESSIONS,
            "actionable": behind <= MAX_STALE_SESSIONS,
            "note": None if behind <= MAX_STALE_SESSIONS else (
                "این فهرست وضعیت جلسه‌ی %s را توصیف می‌کند، نه امروز را — %d "
                "جلسه فاصله. در این فاصله قیمت تا %.0f%% و وضعیت صف کاملاً "
                "می‌تواند عوض شده باشد، و اسکنر هیچ‌کدام را نمی‌بیند. تا "
                "به‌روزرسانی داده، این یک سابقه است نه فهرست تحت نظر."
                % (str(as_of.date()), behind,
                   behind * config.PRICE_BAND_PCT)),
        }

    # A stale run still computes and still logs -- the record of what the
    # scanner said on that session is exactly what the signal log exists for --
    # but it does not hand back a ranked watchlist as if it described now.
    if stale is not None and not stale["actionable"]:
        rows = [dict(row, stale=True) for row in rows]

    report = {
        "top": rows,
        "counts": counts,
        "staleness": stale,
        "market_state": market_state,
        "rejected": rejected,
        "session_date": session_date,
        "computed_at": started,
        "params": {
            "mfi_floor": scoring.MFI_FLOOR,
            "mfi_window": flow.MFI_WINDOW,
            "range_window": position.RANGE_WINDOW,
            "max_position_pct": position.MAX_POSITION_PCT,
            "near_support_pct": position.NEAR_SUPPORT_PCT,
            "volume_ratio_floor": position.VOLUME_RATIO_FLOOR,
            "min_turnover": min_turnover,
            "flow_window": accumulation.WINDOW,
            # The size bands are computed from THIS run's market, so they must
            # travel with the run. Without them a stored signal's "Small Cap"
            # cannot be re-read a year later -- the boundary will have moved,
            # and the label alone would silently mean something else.
            "cap_p33": context.get("cap_p33"),
            "cap_p67": context.get("cap_p67"),
            "advance_window": microstructure.ADVANCE_WINDOW,
            "advance_margin_pct": microstructure.ADVANCE_MARGIN_PCT,
            # The regime this run was judged against. Without it a stored
            # signal's "quiet" cannot be re-read later -- quiet is relative,
            # and the thing it was relative to has to travel with the record.
            "market_advance_pct": (round(market_move, 2)
                                   if market_move is not None else None),
            "weights": WEIGHTS,
        },
        "weights": WEIGHTS,
        "caveat": CAVEAT,
    }

    if with_news:
        report["news"] = _news_context()

    if persist:
        report["run_id"] = store.record(report)

    return report


def _row(rank, candidate, scored, frame, params=None, with_engine=True):
    """One dashboard row: flat fields for the table, nested ones for the drawer."""

    mfi = candidate.get("mfi") or {}
    ranged = candidate.get("range") or {}
    supports = candidate.get("supports") or {}
    nearest = supports.get("nearest") or {}
    valuation = candidate.get("valuation") or {}
    size = candidate.get("size") or {}
    transfer = candidate.get("transfer") or {}
    code = candidate.get("code_to_code") or {}
    traded = candidate.get("tradability") or {}
    locked = candidate.get("locks") or {}
    moved = candidate.get("advance") or {}

    pe = _finite(valuation.get("pe"))
    sector_pe = _finite(valuation.get("sector_pe"))

    row = {
        "rank": rank,
        "ins_code": candidate["ins_code"],
        "symbol": candidate.get("symbol"),
        "name": candidate.get("name"),
        "sector": candidate.get("sector"),
        "close": candidate.get("close"),
        "turnover": candidate.get("turnover"),

        "score": scored["score"],
        "status": scored["status"],
        "coverage": scored["coverage"],
        "breakdown": scored["breakdown"],
        "notes": scored["notes"],

        "mfi": round(mfi["value"], 1) if mfi.get("value") is not None else None,
        "mfi_change": (round(mfi["change"], 1)
                       if mfi.get("change") is not None else None),
        "mfi_rising": bool(mfi.get("rising")),
        "divergence": bool((mfi.get("divergence") or {}).get("positive")),

        "pe": pe,
        "sector_pe": sector_pe,
        # The column the brief asks for -- "P/E گروه" as a RELATION, not a
        # second number the reader has to subtract in their head.
        "pe_vs_sector": (None if (pe is None or not sector_pe)
                         else round((sector_pe - pe) / sector_pe * 100.0, 1)),

        "market_cap": size.get("market_cap"),
        "size_band": size.get("band"),
        "size_band_fa": scoring.SIZE_FA.get(size.get("band")),

        "net_pct": (round(transfer["net_pct"], 2)
                    if transfer.get("net_pct") is not None else None),
        "power_ratio": (round(transfer["power_ratio"], 2)
                        if transfer.get("power_ratio") is not None else None),
        "smart_money_ratio": (round(code["smart_money_ratio"], 3)
                              if code.get("smart_money_ratio") is not None
                              else None),
        "elevation": (round(code["elevation"], 2)
                      if code.get("elevation") is not None else None),
        "code_to_code": bool(code.get("genuine")),
        "code_to_code_reasons": code.get("reasons") or [],

        "volume_ratio": (round(candidate["volume"]["ratio"], 2)
                         if (candidate.get("volume") or {}).get("ratio")
                         is not None else None),
        "traded_pct": (round(traded["traded_volume_pct"], 0)
                       if traded.get("traded_volume_pct") is not None else None),
        # Queue behaviour, reported even when it did not veto: a name that
        # closed at the band six times in twenty is describing itself, and the
        # reader should see that beside the score.
        "band_closes": locked.get("band_closes_recent"),
        "locks_recent": ((locked.get("locked_up_recent") or 0)
                         + (locked.get("locked_down_recent") or 0)),
        "advance_pct": (round(moved["return_pct"], 1)
                        if moved.get("return_pct") is not None else None),
        "advance_excess_pct": (round(moved["excess_pct"], 1)
                               if moved.get("excess_pct") is not None else None),

        "position_pct": (round(ranged["position_pct"], 1)
                         if ranged.get("position_pct") is not None else None),
        "long_position_pct": (round(ranged["long_position_pct"], 1)
                              if ranged.get("long_position_pct") is not None
                              else None),
        "range_low": ranged.get("low"),
        "range_high": ranged.get("high"),

        "support": {
            "price": nearest.get("price"),
            "distance_pct": (round(nearest["distance_pct"], 2)
                             if nearest.get("distance_pct") is not None
                             else None),
            "kind": nearest.get("kind"),
            "label": nearest.get("label"),
            "touches": nearest.get("touches"),
        },
        "support_levels": supports.get("levels") or [],
    }

    row["alert"] = scoring.narrative(candidate, scored)

    if with_engine:
        # Reported beside the score, never folded into it. A row where V2 says
        # AVOID and this scanner says 82 is the most informative row in the
        # table, and a blended number would erase exactly that.
        row["engine_v2"] = _engine_v2(frame, params)

    return row
