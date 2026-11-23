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
months-only one.

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
from collections import defaultdict
from datetime import datetime, timezone

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


def measure(statements):
    """[(months bucket, margin band, actual / estimate)] over the history."""

    import config
    import fundamental_outlook as fo

    bills = {"rate_pct": 40.0, "as_of": None, "source": "calibration"}
    past_inflation = getattr(config, "BACKTEST_INFLATION_RATE", 40.0)
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
        try:
            r = fo.analyse(code, price=None, rows=[_row(x) for x in rows],
                           today=(ey, em, 1), monthly_rows=[], dividend=None,
                           shares=None, sector=None, portfolio=None,
                           opportunity=bills, bars=None, band=None,
                           published=None, calibration=None, fx_record=None,
                           capital_events=None, peers=None,
                           inflation_pct=past_inflation)
        except Exception:                                       # noqa: BLE001
            continue
        if not r or r["method"] not in ("year_completed", "next_year_trend"):
            continue
        forward = r["forward"]["profit"]
        real = fo._profit(actual["items"])
        ttm_rev, ttm_net = r["ttm"]["revenue"], r["ttm"]["profit"]
        if not forward or forward <= 0 or real is None or not ttm_rev or \
                ttm_rev <= 0 or ttm_net is None:
            continue
        ratio = real / forward
        if not -5 < ratio < 10:             # a misread filing, not an error
            continue
        band = margin_band(ttm_net / ttm_rev)
        points.append(("%d" % months_bucket(m), band, ratio))

        # The monthly-sales method, emulated on the same point: the
        # statement's own year-to-date sales and growth in the monthly
        # report's place (see the module docstring).
        if m != 12:
            it, a_it, s_it = cur["items"], rows[1]["items"], rows[2]["items"]
            rev, net = _f(it.get("revenue")), _f(it.get("net_profit"))
            a_rev, s_rev = _f(a_it.get("revenue")), _f(s_it.get("revenue"))
            a_net, s_net = _f(a_it.get("net_profit")), _f(s_it.get("net_profit"))
            if None in (rev, net, a_rev, s_rev, a_net, s_net) or s_rev <= 0 or \
                    rev + a_rev - s_rev <= 0:
                continue
            run_margin = (net + a_net - s_net) / (rev + a_rev - s_rev)
            emulated = fo.monthly_year_estimate(a_rev, rev / s_rev - 1, m, rev,
                                                net, run_margin)
            owners = fo._profit(it)
            share = owners / net if owners is not None and net and net > 0 and \
                0 < owners / net <= 1.5 else 1.0
            if not emulated or emulated[1] * share <= 0:
                continue
            e_ratio = real / (emulated[1] * share)
            if -5 < e_ratio < 10:
                points.append(("M%d" % months_bucket(m), band, e_ratio))
    return points


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

def build(statements=None, fx_record=None, sector_of=None):
    """The whole calibration record (not written)."""

    import fx

    statements = statements if statements is not None else load_statements()
    if sector_of is None:
        import database
        sector_of = {str(r["ins_code"]): (r.get("sector_name") or "").strip()
                     for r in database.load_instruments(equities_only=True)}
    fx_record = fx_record if fx_record is not None else fx.load()

    points = measure(statements)
    buckets = defaultdict(list)
    for head, band, ratio in points:
        buckets[head].append(ratio)
        if band:
            buckets["%s|%s" % (head, band)].append(ratio)
    record = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "points": len(points),
              "buckets": {k: _summarise(v) for k, v in sorted(buckets.items())},
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
            return dict(b, key=key, basis=basis,
                        months_bucket=int(head.lstrip("M")),
                        band=band if "|" in key else None)
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
        for key, b in record["buckets"].items():
            print("  %-11s n=%5d  median |error| %5.1f%%  actual/estimate 25-75%%: %.2f-%.2f"
                  % (key, b["n"], (b["median_abs_error"] or 0) * 100,
                     b["q25"], b["q75"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
