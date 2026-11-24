"""How far the outlook's estimate has been from the year companies then reported.

WHY THIS EXISTS
---------------
The forward estimate was one number. On 2026-11-23 the user asked for the
forward analysis to be made more accurate and more useful "as a professional
analyst". A professional's first question about an estimate is how wrong
estimates like it have been, so the outlook now carries a range measured on
its own history:

  For every company-year whose 12-month statement is stored, and for each
  point the estimate could have been made from (the 3, 6 and 9-month
  statement of that year, with the prior year's annual and same interim; or
  the prior annual alone for a year not yet begun), fundamental_outlook's own
  analyse() is run on exactly those rows, one basis throughout, and its
  forward profit compared with the profit the year then closed on.

What it showed on 2026-11-23 (3,763 points, fiscal years 1399-1404):

  * Sales were estimated within a median 12%; the profit's error was a
    median 28% (40% three months in, 21% nine months in). The margin, not
    the sales, is where the estimate goes wrong.
  * Completing the year at the trailing-twelve-month margin instead of the
    year-to-date one cut the error to 26.7% (36% three months in) and halved
    the low bias -- fundamental_outlook now does that.
  * Run-rate and last-quarter variants of the sales estimate did not beat
    the year-to-date growth it uses.
  * The error depends most on how thin the margin is: under 5% net margin a
    median 60-90%, at 15-30% about 15-25%.

So the range is read per (months known, margin band), from the quantiles of
actual / estimate. A bucket with fewer than MIN_N points falls back to the
months-only one. (Since 2026-11-24 those quantiles are point-in-time: see
POINT IN TIME below.)

The monthly-sales estimate (most of the ranked names) cannot be measured
on real monthly reports: they are stored only from 1405. A first version
lent it the statement bucket and called that "if anything too wide"; the
review of 2026-11-23 emulated it and found the opposite (a median error of
32% against 26.5% six months in). So it is now measured by EMULATION, in
buckets of its own ("M3", "M6", "M9"): fundamental_outlook's own
monthly_year_estimate() run on each past statement point, the statement's
year-to-date sales standing in for the monthly report's. The real report
is fresher than the statement it stands in for, so this is the band of an
estimate with somewhat less information -- stated as an emulation wherever
it is shown, not as a measurement of the live method.

Past years are scored with the inflation they faced
(config.BACKTEST_INFLATION_RATE), not today's: the "year not yet begun"
estimate leans half on inflation, and at today's 76.8% its bucket read a
median 0.82 of the actual where 40% gives 0.93 (the same review).

POINT IN TIME (2026-11-24)
--------------------------
All of the above reads `metrics`: every input and the "actual" are the
LATEST release of their period, and the estimate is dated at its period's
end -- before its statement was even published. Look-ahead twice. That
measurement (measure()) stays, as `restated_buckets`. measure_pit() runs the
same estimator as it could have been run: decided on the day the statement
was first published (plus 0, 30, 60 or 90 days), from the releases public
on that day (pit_fundamentals), and scored against the FIRST release of the
year's annual statement (and, beside it, against the latest). The band the
panel and the report read (`buckets`) is the lag-0, first-release one;
choose_band() says where it falls back, and the record says so too.

What it showed on 2026-11-24 (9,954 points at lag 0; fiscal years 1400-1405
hold 97% of them):

  * Look-ahead had not flattered the headline: the statement method's
    median error was 26.6% point-in-time, the same as restated (a year
    ahead 35.8% against 36.6%; the monthly emulation 26.9% against 26.8%).
    Scored against the latest release instead of the first, 26.9%.
  * It had flattered thin margins: under 5% net margin, 48.6% nine months
    in against 37.0% restated, 53.7% six months in against 47.7%. A
    restatement moves a small profit by a large share.
  * The lag hardly matters: read 30, 60 or 90 days after its first release,
    with whatever was revised by then, the error moves by under a point
    (six months in: 27.0, 26.7, 26.0, 26.2%).
  * Years left out (loyo()): pooled, the 25-75% band held 49% of held-out
    points and the 10-90% band 79% -- on nominal. Year by year it did not:
    in 3 of 6 years the 25-75% band missed its own interval, and in 1405
    (the devaluation) only 20% of the six- and nine-month estimates fell
    inside it. Right on average, wrong in a shock year, which is when it is
    read. range_for() carries both numbers.

DOLLAR SENSITIVITY
------------------
The same pass measures, per industry, how each discrete quarter's sales and
cost of sales moved against the same quarter a year before, set against the
exchange centre's (NIMA) dollar over the two quarters (fx.py). The fit is
weak -- R² 0.02-0.15 in every industry on 2026-11-23, because volume, world
prices and regulated price steps move sales as much as the rate does -- so
the scenarios in fundamental_outlook are labelled as what-ifs and never move
the base estimate. A company's own slope is shrunk toward its industry's
(SHRINK quarters of weight) and clipped to SENSITIVITY_CLIP.

Built by ensure(), at most every MAX_AGE_DAYS (bootstrap's post-run hook
and the stock report call it), into data/outlook_calibration.json.
"""

import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "data", "outlook_calibration.json")
MAX_AGE_DAYS = 7
MIN_N = 40
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
MARGIN_BANDS = ((None, 0.05, "lt5"), (0.05, 0.15, "5_15"),
                (0.15, 0.30, "15_30"), (0.30, None, "ge30"))
MARGIN_LABELS = {"lt5": "زیر ۵٪", "5_15": "۵ تا ۱۵٪", "15_30": "۱۵ تا ۳۰٪",
                 "ge30": "بالای ۳۰٪"}
# Wider than this between the 25th and 75th percentile of actual/estimate
# and the estimate is called unreliable on the row.
WIDE_BAND = 0.8
SHRINK = 12
SENSITIVITY_CLIP = (0.0, 1.2)
COST_FIT_R2 = 0.10
MIN_FIT_QUARTERS = 6

_CACHE = {"mtime": None, "data": None}


def months_bucket(months_known):
    """The statement point an estimate with `months_known` months is read as:
    the one with as many months known, or fewer."""

    m = int(months_known or 12)
    if m >= 12:
        return 12
    if m >= 9:
        return 9
    if m >= 6:
        return 6
    return 3


def bucket_keys(method, months_known, band):
    """The bucket keys to try for an estimate, most specific first.

    A year not yet begun ("next_year_trend") is the "12" bucket whatever
    else; a monthly-sales estimate reads the emulated "M" buckets (twelve
    months of sales known is still not a statement: "M9"); a statement
    estimate its own months.
    """

    if method == "next_year_trend":
        head = "12"
    elif method == "monthly_sales":
        head = "M%d" % min(months_bucket(months_known), 9)
    else:
        head = "%d" % min(months_bucket(months_known), 9)
    return [("%s|%s" % (head, band)) if band else None, head]


def margin_band(margin):
    if margin is None:
        return None
    for low, high, key in MARGIN_BANDS:
        if (low is None or margin >= low) and (high is None or margin < high):
            return key
    return None


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _summarise(ratios):
    values = sorted(ratios)
    out = {"n": len(values)}
    for q in QUANTILES:
        out["q%02d" % int(q * 100)] = round(_quantile(values, q), 4)
    errors = sorted(abs(1 / r - 1) if r else 1.0 for r in values if r > 0)
    out["median_abs_error"] = round(_quantile(errors, 0.5), 4) if errors else None
    return out


# ---- the estimate's own history --------------------------------------------

def _row(rec):
    return {"period_end": rec["end"], "period_months": rec["months"],
            "items": rec["items"], "ratios": rec.get("ratios") or {},
            "published_at": rec["end"], "audited": True,
            "consolidated": rec["cons"]}


def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def load_statements():
    """{(ins_code, fiscal year, months): record} from the fundamental store."""

    import fundamental_db
    import fundamental_outlook as fo

    conn = fundamental_db._connect()
    try:
        rows = conn.execute(
            "SELECT ins_code, period_end, period_months, consolidated, items, "
            "ratios FROM metrics").fetchall()
    finally:
        conn.close()
    out = {}
    for code, end, months, cons, items, ratios in rows:
        months = int(months or 12)
        if months not in (3, 6, 9, 12):
            continue
        fy = fo.fiscal_year_end(end, months)
        if fy is None:
            continue
        try:
            items = json.loads(items) if items else {}
            ratios = json.loads(ratios) if ratios else {}
        except ValueError:
            continue
        out[(str(code), fy, months)] = {"end": end, "months": months,
                                        "cons": bool(cons), "items": items,
                                        "ratios": ratios}
    return out


def _scoring_inputs():
    import config

    bills = {"rate_pct": 40.0, "as_of": None, "source": "calibration"}
    return bills, getattr(config, "BACKTEST_INFLATION_RATE", 40.0)


def _estimate(code, m, rows, today, bills, inflation):
    """The outlook's forward profit from `rows` (cur first, then for an
    interim the prior annual and the prior same period), or None.

        {"forward": statement method's profit, "band": margin band,
         "emulated": the monthly-sales method's profit, emulated, or None}
    """

    import fundamental_outlook as fo

    try:
        r = fo.analyse(code, price=None, rows=[_row(x) for x in rows],
                       today=today, monthly_rows=[], dividend=None,
                       shares=None, sector=None, portfolio=None,
                       opportunity=bills, bars=None, band=None,
                       published=None, calibration=None, fx_record=None,
                       capital_events=None, peers=None,
                       inflation_pct=inflation)
    except Exception:                                           # noqa: BLE001
        return None
    if not r or r["method"] not in ("year_completed", "next_year_trend"):
        return None
    forward = r["forward"]["profit"]
    ttm_rev, ttm_net = r["ttm"]["revenue"], r["ttm"]["profit"]
    if not forward or forward <= 0 or not ttm_rev or ttm_rev <= 0 or \
            ttm_net is None:
        return None
    out = {"forward": forward, "band": margin_band(ttm_net / ttm_rev),
           "emulated": None}

    # The monthly-sales method, emulated on the same point: the
    # statement's own year-to-date sales and growth in the monthly
    # report's place (see the module docstring).
    if m != 12:
        it, a_it, s_it = rows[0]["items"], rows[1]["items"], rows[2]["items"]
        rev, net = _f(it.get("revenue")), _f(it.get("net_profit"))
        a_rev, s_rev = _f(a_it.get("revenue")), _f(s_it.get("revenue"))
        a_net, s_net = _f(a_it.get("net_profit")), _f(s_it.get("net_profit"))
        if None in (rev, net, a_rev, s_rev, a_net, s_net) or s_rev <= 0 or \
                rev + a_rev - s_rev <= 0:
            return out
        run_margin = (net + a_net - s_net) / (rev + a_rev - s_rev)
        emulated = fo.monthly_year_estimate(a_rev, rev / s_rev - 1, m, rev,
                                            net, run_margin)
        owners = fo._profit(it)
        share = owners / net if owners is not None and net and net > 0 and \
            0 < owners / net <= 1.5 else 1.0
        if emulated and emulated[1] * share > 0:
            out["emulated"] = emulated[1] * share
    return out


def _ratios(estimate, m, real):
    """[(bucket, band, actual / estimate)] of one estimate against one
    actual profit: the statement method's point, and the emulated monthly
    method's beside it. A ratio outside (-5, 10) is a misread filing, not
    an error, and drops the point."""

    if estimate is None or real is None:
        return []
    ratio = real / estimate["forward"]
    if not -5 < ratio < 10:
        return []
    out = [("%d" % months_bucket(m), estimate["band"], ratio)]
    if estimate["emulated"]:
        e_ratio = real / estimate["emulated"]
        if -5 < e_ratio < 10:
            out.append(("M%d" % months_bucket(m), estimate["band"], e_ratio))
    return out


def measure(statements):
    """[(months bucket, margin band, actual / estimate)] over the history,
    RESTATED: every input and the actual are the latest release, and the
    estimate is dated at its period's end (see measure_pit)."""

    import fundamental_outlook as fo

    bills, past_inflation = _scoring_inputs()
    points = []
    for (code, fy, m), cur in statements.items():
        prev_fy = (fy[0] - 1, fy[1])
        if m == 12:
            # a year not yet begun: next year from this annual
            actual = statements.get((code, (fy[0] + 1, fy[1]), 12))
            rows = [cur]
            prior = statements.get((code, prev_fy, 12))
            if prior and prior["cons"] == cur["cons"]:
                rows.append(prior)
        else:
            actual = statements.get((code, fy, 12))
            a_prev = statements.get((code, prev_fy, 12))
            s_prev = statements.get((code, prev_fy, m))
            if not (a_prev and s_prev):
                continue
            rows = [cur, a_prev, s_prev]
        if not actual or len({r["cons"] for r in rows + [actual]}) != 1:
            continue
        ey, em = (int(p) for p in str(cur["end"]).split("/")[:2])
        estimate = _estimate(code, m, rows, (ey, em, 1), bills, past_inflation)
        points.extend(_ratios(estimate, m, fo._profit(actual["items"])))
    return points


# ---- the same, point in time (2026-11-24) ------------------------------------

LAGS = (0, 30, 60, 90)
# The panel's band and the leave-one-year-out check read this lag.
BAND_LAG = 0


def _pit_record(rel):
    return {"end": rel["period_end"], "months": rel["period_months"],
            "cons": bool(rel["consolidated"]), "items": rel["items"],
            "ratios": rel.get("ratios") or {}}


def _pit_index(releases):
    """{(fiscal year, months): {basis: [release, ...]}} of one company; the
    3/6/9/12-month statements only. Two spellings of one period end (an
    Esfand 29/30) are one period."""

    import fundamental_outlook as fo

    index = defaultdict(lambda: defaultdict(list))
    for rel in releases:
        m = rel["period_months"]
        if m not in (3, 6, 9, 12) or rel["consolidated"] is None:
            continue
        fy = fo.fiscal_year_end(rel["period_end"], m)
        if fy is None:
            continue
        index[(fy, m)][bool(rel["consolidated"])].append(rel)
    return index


def measure_pit(versions_by_code, lags=LAGS):
    """The estimate as it could have been made, at each lag:

        {lag: {"points": [(bucket, band, actual / estimate, target year)],
               "latest": the same against the actual's LATEST release,
               "skipped": {reason: count}}}

    For every company-period with a 3/6/9/12-month statement:
      * the decision day is the period's first release (the first parse
        carrying an income statement, pit_fundamentals.earliest) plus `lag`
        days; its basis (consolidated or separate) is the series followed;
      * every input -- the statement, and the prior year's annual and same
        period -- is the release of that basis known on the decision day,
        chosen as `metrics` chooses (pit_fundamentals.winner);
      * the actual is the FIRST release of the year's annual statement on
        the same basis ("latest" scores the same estimate against its
        latest release);
      * a point whose actual was already public on the decision day is
        not an estimate and is skipped ("actual_known");
      * analyse() gets the decision day's Jalali date as `today`. It reads
        only the year and month (the age of the statement, in whole
        months), and with one basis in the rows that changes a flag, never
        the forward profit.
    """

    import jalali
    import pit_fundamentals as pit

    bills, past_inflation = _scoring_inputs()
    out = {lag: {"points": [], "latest": [], "skipped": Counter()} for lag in lags}

    def skip(reason):
        for lag in lags:
            out[lag]["skipped"][reason] += 1

    for code, releases in versions_by_code.items():
        index = _pit_index(releases)
        for (fy, m), by_basis in index.items():
            first = pit.earliest([r for group in by_basis.values() for r in group])
            if first is None or not pit.has_income(first["items"]):
                skip("no_income_statement")
                continue
            basis = bool(first["consolidated"])
            start = pit.parse_iso(first["published_iso"])
            target = fy if m != 12 else (fy[0] + 1, fy[1])
            actual_releases = index.get((target, 12), {}).get(basis, [])
            a_first = pit.earliest(actual_releases)
            a_latest = pit.winner(actual_releases)
            real_first = pit.net_profit(a_first["items"]) if a_first else None
            real_latest = pit.net_profit(a_latest["items"]) if a_latest else None
            if start is None or real_first is None:
                skip("no_actual")
                continue
            prev_fy = (fy[0] - 1, fy[1])
            for lag in lags:
                when = start + timedelta(days=lag)
                stamp = when.isoformat(timespec="seconds")
                if a_first["published_iso"] <= stamp:
                    out[lag]["skipped"]["actual_known"] += 1
                    continue

                def known(key):
                    rel = [r for r in index.get(key, {}).get(basis, [])
                           if r["published_iso"] <= stamp]
                    return pit.winner(rel) if rel else None

                cur = known((fy, m))
                if m == 12:
                    rows = [cur]
                    prior = known((prev_fy, 12))
                    if prior:
                        rows.append(prior)
                else:
                    a_prev, s_prev = known((prev_fy, 12)), known((prev_fy, m))
                    if not (a_prev and s_prev):
                        out[lag]["skipped"]["no_prior_year"] += 1
                        continue
                    rows = [cur, a_prev, s_prev]
                estimate = _estimate(code, m, [_pit_record(r) for r in rows],
                                     jalali.today_jalali(when.date()), bills,
                                     past_inflation)
                if estimate is None:
                    out[lag]["skipped"]["no_estimate"] += 1
                    continue
                found = _ratios(estimate, m, real_first)
                if not found:
                    out[lag]["skipped"]["misread_ratio"] += 1
                    continue
                out[lag]["points"].extend(p + (target[0],) for p in found)
                out[lag]["latest"].extend(
                    p + (target[0],) for p in _ratios(estimate, m, real_latest))
    for lag in lags:
        out[lag]["skipped"] = dict(out[lag]["skipped"])
    return out


# ---- does the band hold on years it has not seen? ----------------------------
# The panel's band is the 25-75% one ("half of past estimates like it"); the
# per-stock report also prints the 10-90% one.

COVERAGE_BANDS = (("q25_q75", 0.25, 0.75, 0.50), ("q10_q90", 0.10, 0.90, 0.80))
# A held-out year with fewer points than this is too few to judge alone.
YEAR_MIN_POINTS = 50


def wilson(covered, total, z=1.96):
    """The binomial 95% (Wilson) interval of covered / total, or (None, None)."""

    if not total:
        return None, None
    p = covered / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - half) / denom, (centre + half) / denom


def _coverage(covered, total, nominal):
    low, high = wilson(covered, total)
    return {"covered": covered, "total": total, "nominal": nominal,
            "rate": round(covered / total, 4) if total else None,
            "low95": None if low is None else round(low, 4),
            "high95": None if high is None else round(high, 4),
            "consistent": (None if low is None else low <= nominal <= high)}


def _family(head):
    if head.startswith("M"):
        return "monthly"
    return "next_year" if head == "12" else "statement"


def _keys_in_use(head, band, sizes, min_n):
    """The bucket range_for reads for a point, given the buckets' sizes."""

    if band and sizes.get("%s|%s" % (head, band), 0) >= min_n:
        return "%s|%s" % (head, band)
    return head if sizes.get(head, 0) >= min_n else None


def loyo(points, min_n=None):
    """Leave one fiscal year out: each year's points scored against the
    quantiles of every OTHER year's, per band (COVERAGE_BANDS).

      buckets   per bucket, every held-out point of it against that
                bucket's own band (when the other years fill it to min_n);
      overall   per family (statement 3/6/9, next_year, monthly), each
                point against the bucket range_for would have given it;
      by_year   the same per held-out year.

    Each tally: {covered, total, nominal, rate, low95, high95, consistent}
    with a Wilson 95% interval. The points of one year share its shocks
    (a devaluation moves every company at once), so the binomial interval
    is narrower than the truth; by_year shows the spread it hides.
    """

    min_n = MIN_N if min_n is None else min_n
    years = sorted({p[3] for p in points})
    by_year = defaultdict(list)
    for p in points:
        by_year[p[3]].append(p)
    totals = defaultdict(lambda: {name: [0, 0] for name, *_ in COVERAGE_BANDS})
    unassigned = 0
    for year in years:
        train = defaultdict(list)
        for head, band, ratio, y in points:
            if y == year:
                continue
            train[head].append(ratio)
            if band:
                train["%s|%s" % (head, band)].append(ratio)
        sizes = {k: len(v) for k, v in train.items()}
        cache = {}

        def bounds(key):
            if key not in cache:
                values = sorted(train[key])
                cache[key] = {q: _quantile(values, q) for q in (0.10, 0.25, 0.75, 0.90)}
            return cache[key]

        for head, band, ratio, _y in by_year[year]:
            members = [head] + (["%s|%s" % (head, band)] if band else [])
            in_use = _keys_in_use(head, band, sizes, min_n)
            if in_use is None:
                unassigned += 1
            family = _family(head)
            for key in dict.fromkeys(members + ([in_use] if in_use else [])):
                if sizes.get(key, 0) < min_n:
                    continue
                q = bounds(key)
                tallies = ([("bucket", key)] if key in members else []) + \
                    ([("overall", family), ("year", str(year), family)]
                     if key == in_use else [])
                for name, lo, hi, _nominal in COVERAGE_BANDS:
                    hit = q[lo] <= ratio <= q[hi]
                    for tally in tallies:
                        totals[tally][name][0] += hit
                        totals[tally][name][1] += 1

    nominal = {name: nom for name, _lo, _hi, nom in COVERAGE_BANDS}

    def done(t):
        return {name: _coverage(k, n, nominal[name]) for name, (k, n) in t.items()}

    out = {"lag_days": BAND_LAG, "min_n": min_n, "years": years,
           "unassigned": unassigned, "nominal": nominal,
           "buckets": {}, "overall": {}, "by_year": defaultdict(dict),
           "year_spread": {}}
    for tally, t in sorted(totals.items()):
        if tally[0] == "bucket":
            out["buckets"][tally[1]] = done(t)
        elif tally[0] == "overall":
            out["overall"][tally[1]] = done(t)
        else:
            out["by_year"][tally[1]][tally[2]] = done(t)
    out["by_year"] = dict(out["by_year"])

    # The pooled tally above treats every point as independent; the year's
    # shock is shared. Per family and band: how many years that carry
    # YEAR_MIN_POINTS held their own interval around the nominal, and the
    # worst of them -- the number a reader should see beside "5 in 10".
    for family in out["overall"]:
        spread = {}
        for name, _lo, _hi, nom in COVERAGE_BANDS:
            rows = [(year, fams[family][name]) for year, fams in out["by_year"].items()
                    if family in fams and fams[family][name]["total"] >= YEAR_MIN_POINTS]
            worst = min(rows, key=lambda r: r[1]["rate"]) if rows else None
            spread[name] = {
                "years": len(rows),
                "outside": sum(1 for _y, c in rows if not c["consistent"]),
                "worst": (dict(year=int(worst[0]), covered=worst[1]["covered"],
                               total=worst[1]["total"], rate=worst[1]["rate"])
                          if worst else None)}
        out["year_spread"][family] = spread
    return out


# ---- dollar sensitivity ----------------------------------------------------

def _quarter_span(fy, m):
    import jalali

    ey, em = jalali.add_months(fy[0], fy[1], m - 12)
    sy, sm = jalali.add_months(ey, em, -2)
    last = 29 if em == 12 else (30 if em > 6 else 31)
    return "%04d/%02d/01" % (sy, sm), "%04d/%02d/%02d" % (ey, em, last)


def quarter_observations(statements, fx_record, rate="nima"):
    """{ins_code: [(log dollar change, log sales change, log cost change)]}
    over discrete quarters against the same quarter a year before."""

    import fx

    quarters = defaultdict(dict)
    for (code, fy, m), cur in statements.items():
        prev = statements.get((code, fy, m - 3)) if m > 3 else None
        if m > 3 and (not prev or prev["cons"] != cur["cons"]):
            continue
        rev = _f(cur["items"].get("revenue"))
        gp = _f(cur["items"].get("gross_profit"))
        if prev is not None:
            p_rev = _f(prev["items"].get("revenue"))
            p_gp = _f(prev["items"].get("gross_profit"))
            rev = rev - p_rev if rev is not None and p_rev is not None else None
            gp = gp - p_gp if gp is not None and p_gp is not None else None
        if rev is None or rev <= 0:
            continue
        cost = rev - gp if gp is not None else None
        quarters[code][(fy, m)] = (rev, cost, cur["cons"])

    averages = {}

    def dollar(fy, m):
        key = (fy, m)
        if key not in averages:
            a, b = _quarter_span(fy, m)
            averages[key] = fx.average(a, b, rate, fx_record)
        return averages[key]

    out = defaultdict(list)
    for code, qs in quarters.items():
        for (fy, m), (rev, cost, cons) in qs.items():
            before = qs.get(((fy[0] - 1, fy[1]), m))
            if not before or before[2] != cons or before[0] <= 0:
                continue
            u1, u0 = dollar(fy, m), dollar((fy[0] - 1, fy[1]), m)
            if not u1 or not u0:
                continue
            y = math.log(rev / before[0])
            if abs(y) > 2:                  # a misread, or a merger
                continue
            yc = (math.log(cost / before[1]) if cost and before[1] and
                  cost > 0 and before[1] > 0 else None)
            out[code].append((math.log(u1 / u0), y, yc))
    return out


def fit(points):
    """(slope, R², n) of y on x with an intercept, or None."""

    pts = [(x, y) for x, y in points if y is not None]
    n = len(pts)
    if n < MIN_FIT_QUARTERS:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    if sxx < 1e-6 or syy < 1e-9:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    return sxy / sxx, sxy * sxy / (sxx * syy), n


def _clip(value):
    return min(max(value, SENSITIVITY_CLIP[0]), SENSITIVITY_CLIP[1])


def sensitivities(observations, sector_of):
    """Industry fits, and each company's slope shrunk toward its industry's."""

    by_sector = defaultdict(list)
    for code, pts in observations.items():
        by_sector[sector_of.get(code) or "?"].extend(pts)
    sectors = {}
    for name, pts in by_sector.items():
        rev = fit([(x, y) for x, y, _ in pts])
        cost = fit([(x, yc) for x, _, yc in pts])
        if rev:
            sectors[name] = {"revenue": round(_clip(rev[0]), 3),
                             "revenue_raw": round(rev[0], 3),
                             "r2": round(rev[1], 3), "n": rev[2],
                             "cost": round(_clip(cost[0]), 3) if cost else None,
                             "cost_r2": round(cost[1], 3) if cost else None}
    companies = {}
    for code, pts in observations.items():
        sector = sectors.get(sector_of.get(code) or "?")
        own_rev = fit([(x, y) for x, y, _ in pts])
        own_cost = fit([(x, yc) for x, _, yc in pts])
        if not sector and not own_rev:
            continue
        base_rev = sector["revenue_raw"] if sector else 0.0
        base_cost = (sector or {}).get("cost") or 0.0

        def shrunk(own, base):
            if not own:
                return _clip(base)
            return _clip((own[2] * own[0] + SHRINK * base) / (own[2] + SHRINK))

        companies[code] = {"revenue": round(shrunk(own_rev, base_rev), 3),
                           "cost": round(shrunk(own_cost, base_cost), 3),
                           "n": own_rev[2] if own_rev else 0,
                           "r2": round(own_rev[1], 3) if own_rev else None}
    return sectors, companies


# ---- build, store, read ----------------------------------------------------

def summarise_points(points):
    """{bucket key: summary} of [(head, band, ratio, ...)]: each point in its
    months bucket and, with a band, in its (months, band) bucket."""

    buckets = defaultdict(list)
    for p in points:
        head, band, ratio = p[0], p[1], p[2]
        buckets[head].append(ratio)
        if band:
            buckets["%s|%s" % (head, band)].append(ratio)
    return {k: _summarise(v) for k, v in sorted(buckets.items())}


def summarise_families(points):
    """{family: summary} -- statement (3/6/9), next_year (12), monthly (M):
    one headline error per method rather than per bucket."""

    families = defaultdict(list)
    for p in points:
        families[_family(p[0])].append(p[2])
    return {k: _summarise(v) for k, v in sorted(families.items())}


# A fiscal year with fewer than this share of the points is outside the span
# the band is said to cover: 1386-1398 hold a few dozen company-years between
# them, and "1386 to 1405" would overstate the history behind the band.
YEAR_SPAN_SHARE = 0.02


def year_span(points):
    """([first, last] fiscal year holding YEAR_SPAN_SHARE of the points or
    more, {year: points})."""

    counts = Counter(p[3] for p in points)
    total = sum(counts.values())
    held = sorted(y for y, n in counts.items() if total and n / total >= YEAR_SPAN_SHARE)
    return ([held[0], held[-1]] if held else None,
            {str(y): n for y, n in sorted(counts.items())})


def choose_band(pit_buckets, restated_buckets, min_n=None):
    """The buckets the panel reads: the point-in-time ones (lag BAND_LAG,
    first release) wherever they reach `min_n`.

    Below it the fallback is explicit and recorded: a (months, margin)
    bucket too thin point-in-time is left thin, so range_for drops to the
    point-in-time months bucket as it always has; a months bucket too thin
    point-in-time takes the RESTATED one, marked source "restated" -- never
    silently. Returns (buckets, {"restated": [keys], "thin": [keys]}).
    """

    min_n = MIN_N if min_n is None else min_n
    buckets, restated, thin = {}, [], []
    for key in sorted(set(pit_buckets) | set(restated_buckets)):
        p, r = pit_buckets.get(key), restated_buckets.get(key)
        if p and p["n"] >= min_n:
            buckets[key] = dict(p, source="pit")
            continue
        if "|" not in key and r and r["n"] >= min_n:
            buckets[key] = dict(r, source="restated", pit_n=(p or {}).get("n", 0))
            restated.append(key)
            continue
        if p:
            buckets[key] = dict(p, source="pit")
            thin.append(key)
    return buckets, {"restated": restated, "thin": thin}


def build(statements=None, fx_record=None, sector_of=None, versions=None):
    """The whole calibration record (not written).

    `buckets` -- what range_for, and so the panel and the report, read --
    holds the point-in-time measurement at lag BAND_LAG against the first
    release (see choose_band for the fallback). The restated measurement
    the band was read from until 2026-11-24 stays beside it as
    `restated_buckets`; `pit` carries every lag, against the first and the
    latest release; `loyo` the leave-one-year-out coverage of the band.
    `versions` ({ins_code: releases}, pit_fundamentals.load_versions())
    replaces the store read; without any, the band is the restated one,
    and says so.
    """

    import fx
    import pit_fundamentals

    statements = statements if statements is not None else load_statements()
    if sector_of is None:
        import database
        sector_of = {str(r["ins_code"]): (r.get("sector_name") or "").strip()
                     for r in database.load_instruments(equities_only=True)}
    fx_record = fx_record if fx_record is not None else fx.load()
    versions = versions if versions is not None else pit_fundamentals.load_versions()

    restated_points = measure(statements)
    restated = summarise_points(restated_points)
    measured = measure_pit(versions) if versions else {}
    lags = {}
    for lag, found in sorted(measured.items()):
        lags[str(lag)] = {"points": len(found["points"]),
                          "skipped": found["skipped"],
                          "families": summarise_families(found["points"]),
                          "families_latest": summarise_families(found["latest"]),
                          "buckets": summarise_points(found["points"]),
                          "buckets_latest": summarise_points(found["latest"])}
    band_points = (measured.get(BAND_LAG) or {}).get("points") or []
    pit_buckets = summarise_points(band_points)
    buckets, fallback = choose_band(pit_buckets, restated)
    years, year_points = year_span(band_points)
    record = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "points": len(band_points) if band_points else len(restated_points),
              "band": {"source": "pit" if band_points else "restated",
                       "lag_days": BAND_LAG, "actual": "first_release",
                       "years": years, "year_points": year_points,
                       "fallback_restated": fallback["restated"],
                       "thin": fallback["thin"]},
              "buckets": buckets if band_points else restated,
              "restated_points": len(restated_points),
              "restated_families": summarise_families(restated_points),
              "restated_buckets": restated,
              "pit": {"lags": lags,
                      "store": (pit_fundamentals.statistics(versions)
                                if versions else None)},
              "loyo": loyo(band_points) if band_points else None,
              "fx": None}
    if fx_record:
        observations = quarter_observations(statements, fx_record)
        sectors, companies = sensitivities(observations, sector_of)
        record["fx"] = {"rate": "nima", "sectors": sectors,
                        "companies": companies}
    return record


def write(record, path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    os.replace(partial, path)


def load(path=None):
    """The stored record, or None. Cached by mtime."""

    path = path or PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if path == PATH and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if path == PATH:
        _CACHE.update(mtime=mtime, data=data)
    return data


def ensure(max_age_days=MAX_AGE_DAYS):
    """Rebuild when missing or older than `max_age_days`. A message; never raises."""

    try:
        age = (time.time() - os.path.getmtime(PATH)) / 86400
    except OSError:
        age = None
    if age is not None and age < max_age_days:
        return "outlook calibration: fresh (%.1f days old)" % age
    started = time.monotonic()
    try:
        record = build()
        write(record)
    except Exception as exc:                                    # noqa: BLE001
        return "outlook calibration: build failed (%s: %s)" % (
            type(exc).__name__, str(exc)[:160])
    return "outlook calibration: %d points, %d industries in %.0f s" % (
        record["points"], len((record.get("fx") or {}).get("sectors") or {}),
        time.monotonic() - started)


def range_for(calibration, months_known, margin, method=None):
    """The bucket an estimate falls in: its quantiles of actual / estimate,
    the bucket's key and size, or None without a calibration.

    `basis` says what the bucket is: "statement" (the method measured on
    its own past), "monthly" (the monthly-sales method, emulated) or
    "next_year" (a year no statement of which exists yet).

    Since 2026-11-24 also:
      `source`      "pit" (measured point-in-time: decided on the day the
                    statement was first published, with the releases public
                    then, scored against the first release of the year) or
                    "restated" (the older measurement on the latest
                    releases; a record built before then, or a fallback);
      `years`       [first, last] fiscal year the points cover, or None;
      `coverage`    how the 25-75% band held on years left out of it (loyo):
                    {covered, total, nominal (0.5), rate, low95, high95,
                    consistent, per10 (of ten estimates, how many fell
                    inside), years_judged, years_outside (held-out years
                    of YEAR_MIN_POINTS whose own interval missed the
                    nominal), worst_year {year, covered, total, rate}}
                    -- the last three for the method's family, not the
                    bucket -- or None;
      `coverage80`  the same for the 10-90% band (nominal 0.8).
    """

    if not calibration:
        return None
    buckets = calibration.get("buckets") or {}
    band = margin_band(margin)
    basis = {"next_year_trend": "next_year",
             "monthly_sales": "monthly"}.get(method, "statement")
    for key in bucket_keys(method, months_known, band):
        b = buckets.get(key) if key else None
        if b and b.get("n", 0) >= MIN_N:
            head = key.split("|")[0]
            source = b.get("source") or "restated"
            loyo_record = (calibration.get("loyo") or {}) if source == "pit" else {}
            held = (loyo_record.get("buckets") or {}).get(key) or {}
            spread = (loyo_record.get("year_spread") or {}).get(_family(head)) or {}
            years = (calibration.get("band") or {}).get("years") \
                if source == "pit" else None

            def coverage(name):
                c = held.get(name)
                if not c:
                    return None
                s = spread.get(name) or {}
                return dict(c, per10=round(10 * c["rate"]) if c.get("rate") is not None
                            else None,
                            years_judged=s.get("years"),
                            years_outside=s.get("outside"),
                            worst_year=s.get("worst"))

            return dict(b, key=key, basis=basis,
                        months_bucket=int(head.lstrip("M")),
                        band=band if "|" in key else None,
                        source=source, years=years,
                        coverage=coverage("q25_q75"),
                        coverage80=coverage("q10_q90"))
    return None


def sensitivity_for(calibration, ins_code, sector):
    """{revenue, cost, cost_basis, source, r2, n} of the dollar slopes, or None.

    The cost slope is the measured one only where the industry's cost fit
    reaches COST_FIT_R2; otherwise costs are taken to move with the dollar
    as sales do (the margin kept). A refiner's feed is priced in dollars,
    yet its cost slope fitted at about zero (R² 0.00): world prices moved
    against the rate in the sample, and a cost that "ignores the dollar"
    would have read every devaluation as pure profit.
    """

    fxcal = (calibration or {}).get("fx") or {}
    own = (fxcal.get("companies") or {}).get(str(ins_code))
    industry = (fxcal.get("sectors") or {}).get((sector or "").strip())
    if not own and not industry:
        return None
    if own:
        out = {"revenue": own["revenue"], "cost": own["cost"],
               "source": "company" if own.get("n") else "industry",
               "n": own["n"] or (industry or {}).get("n", 0),
               "r2": own["r2"] if own.get("r2") is not None
               else (industry or {}).get("r2")}
    else:
        out = {"revenue": industry["revenue"], "cost": industry.get("cost"),
               "source": "industry", "n": industry["n"], "r2": industry["r2"]}
    out["industry_r2"] = (industry or {}).get("r2")
    cost_r2 = (industry or {}).get("cost_r2")
    if out["cost"] is None or cost_r2 is None or cost_r2 < COST_FIT_R2:
        out["cost"], out["cost_basis"] = out["revenue"], "same_as_sales"
    else:
        out["cost_basis"] = "measured"
    return out


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if "--force" in argv:
        print(ensure(max_age_days=0))
    else:
        print(ensure())
    record = load()
    if record:
        report(record)
    return 0


def report(record):
    """The record, printed: the band beside the restated one, every lag,
    and the leave-one-year-out coverage."""

    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    band = record.get("band") or {}
    print("band: %s, lag %s days, actual = %s, years %s; restated fallback %s; thin %s"
          % (band.get("source", "restated"), band.get("lag_days"), band.get("actual"),
             band.get("years"), band.get("fallback_restated"), band.get("thin")))
    restated = record.get("restated_buckets") or {}
    print("  %-9s %-28s  %-28s" % ("bucket", "band (n, med|err|, 25-75)",
                                   "restated (n, med|err|, 25-75)"))
    for key, b in record["buckets"].items():
        r = restated.get(key) or {}
        print("  %-9s %5d %5.1f%% %.2f-%.2f %-8s  %5s %5.1f%% %s"
              % (key, b["n"], (b["median_abs_error"] or 0) * 100, b["q25"], b["q75"],
                 b.get("source", ""), r.get("n", "—"),
                 (r.get("median_abs_error") or 0) * 100,
                 "%.2f-%.2f" % (r["q25"], r["q75"]) if r else "—"))
    lags = (record.get("pit") or {}).get("lags") or {}
    print("\nper method (median |error|; restated, then each lag: first release / latest):")
    for family, r in (record.get("restated_families") or {}).items():
        print("  %-10s restated n=%5d %4.1f%%   %s" % (
            family, r["n"], r["median_abs_error"] * 100, "   ".join(
                "lag %s n=%5d %4.1f%% / %4.1f%%" % (
                    lag, d["families"].get(family, {}).get("n", 0),
                    d["families"].get(family, {}).get("median_abs_error", 0) * 100,
                    d["families_latest"].get(family, {}).get("median_abs_error", 0) * 100)
                for lag, d in lags.items())))
    if lags:
        print("\nby lag (months buckets; median |error|, 25-75 and 10-90 of actual/estimate;"
              " first release | latest release):")
        heads = sorted({k for lag in lags.values() for k in lag["buckets"] if "|" not in k},
                       key=lambda k: (k.startswith("M"), int(k.lstrip("M"))))
        for key in heads:
            cells = []
            for lag, data in lags.items():
                b = data["buckets"].get(key)
                l = data["buckets_latest"].get(key)
                if not b:
                    cells.append("lag %3s: —" % lag)
                    continue
                cells.append("lag %3s n=%4d %4.1f%% [%.2f-%.2f | %.2f-%.2f] latest %4.1f%%"
                             % (lag, b["n"], b["median_abs_error"] * 100, b["q25"], b["q75"],
                                b["q10"], b["q90"],
                                (l or {}).get("median_abs_error", 0) * 100))
            print("  %-4s %s" % (key, "\n       ".join(cells)))
        for lag, data in lags.items():
            print("  lag %s: %d points; skipped %s" % (lag, data["points"], data["skipped"]))
    held = record.get("loyo")
    if held:
        print("\nleave one fiscal year out (lag %s), coverage against nominal:"
              % held["lag_days"])
        for family, t in held["overall"].items():
            print("  %-10s %s" % (family, _coverage_text(t)))
            for name, s in (held.get("year_spread") or {}).get(family, {}).items():
                w = s.get("worst") or {}
                print("  %-10s   %s: %d of %d years (>= %d points) outside their own "
                      "interval; worst %s %s/%s = %.0f%%"
                      % ("", name, s["outside"], s["years"], YEAR_MIN_POINTS,
                         w.get("year"), w.get("covered"), w.get("total"),
                         100 * (w.get("rate") or 0)))
        for year, fams in held["by_year"].items():
            for family in ("statement", "next_year"):
                if fams.get(family):
                    print("  %-4s %-9s %s" % (year, family, _coverage_text(fams[family])))
        for key, t in held["buckets"].items():
            print("  %-10s %s" % (key, _coverage_text(t)))


def _coverage_text(t):
    out = []
    for name in ("q25_q75", "q10_q90"):
        c = t.get(name)
        if not c or not c["total"]:
            continue
        out.append("%s %4d/%-4d = %4.1f%% [%4.1f-%4.1f] vs %2.0f%%%s"
                   % (name, c["covered"], c["total"], 100 * c["rate"],
                      100 * c["low95"], 100 * c["high95"], 100 * c["nominal"],
                      "" if c["consistent"] else " *"))
    return "   ".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
