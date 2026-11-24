"""How many tries, in which family, what luck alone would show, and which
windows are already spent.

WHY THIS EXISTS (2026-11-24)
-----------------------------
The research report of 2026-11-23 (item 4) found that the programme had run
at least 25 experiments on overlapping Tehran history and judged each one on
its own p-value. With N tries, the best of N strategies that have NO skill is
expected to show an in-sample Sharpe ratio well above zero (Bailey, Borwein,
Lopez de Prado & Zhu 2014): with 25 tries on a 5.3-year window, about 0.87 a
year from luck alone. A p below 0.05 means little in that company. This
module makes the count, the hurdle and the spent windows part of the record.

WHAT IS COMPUTED
-----------------
* `trials()` -- every experiment in the ledger with its family, its primary
  hypotheses and their p-values where a result file states them, and whether
  it was confirmatory (a dated preregistration preceded its result) or
  exploratory. N counts every experiment that computed a statistic.
* `expected_max_z(n)` and `luck_hurdle(n, years)` -- the expected best
  standardised Sharpe of n skill-less tries, and what it is per year on a
  window of `years`.
* `psr(...)` / `dsr(...)` -- the probabilistic Sharpe ratio (Bailey & Lopez
  de Prado 2012, "The Sharpe Ratio Efficient Frontier", eq. for PSR):
      PSR(SR*) = Phi( (SR - SR*) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) )
  with SR, SR* per period, g3 the skewness and g4 the (non-excess) kurtosis
  of the returns; and the deflated Sharpe ratio (Bailey & Lopez de Prado
  2014), DSR = PSR(SR0) with
      SR0 = sqrt(V[SR]) ((1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)))
  gamma the Euler-Mascheroni constant. Under the null each trial's SR has
  variance about 1/T, which is what is used when the cross-section of trial
  Sharpe ratios is unknown (it is, for most of this ledger). With normal
  returns (g3 = 0, g4 = 3) the denominator is sqrt(1 + SR^2 / 2).
* `holm(...)` and `benjamini_hochberg(...)`.
* `readings()` -- three readings of the same p-values, side by side:
    A. every hypothesis the programme tested, one family, Holm at 0.05;
    B. each experiment's own preregistered correction (what was declared);
    C. the families declared HERE, on 2026-11-24.
  C is prospective for E35 onward and POST HOC for everything earlier: the
  family boundaries were drawn after E27 and E28 were known. That is stated
  wherever C is shown; it is why A and B are always shown beside it.
* `window_use()` and `window_budget(start, end)` -- for each calendar year,
  which experiments evaluated it; and the stop rule: once the expected
  best-of-N annual Sharpe on a window reaches `STOP_SHARPE` (1.0), another
  historical test on that window cannot make a discovery -- only new data can.
* `prereg_completeness(doc)` -- the five things every new preregistration
  must name: family and correction, the exact benchmark series, the window,
  the cost model, the acceptance rule. `window_rule(doc)` -- the owner's
  rule of 2026-11-24: the window as dates, fifteen years (about two market
  cycles) or a stated reason it is shorter.

Nothing here changes a verdict in the ledger. It reports.
"""

import json
import math
import os
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT = os.path.join(ROOT, "backtest_output")

EULER_GAMMA = 0.5772156649015329
FAMILY_ALPHA = 0.05
STOP_SHARPE = 1.0          # expected best annual Sharpe from luck alone
DSR_HURDLE = 0.95
T_HURDLE = 3.0             # Harvey, Liu & Zhu: a new factor needs t > 3

DECLARED_ON = "2026-11-24"

# Families declared on 2026-11-24. Prospective for E35 onward; post hoc for
# everything earlier (see the module docstring, reading C).
FAMILIES = {
    "allocation": "what to hold: equity, gold, fixed income, bills -- not "
                  "which stock (E1, E4, E27, E28, E34, E35, E36)",
    "selection": "which stock: baskets, fundamental and price rules "
                 "(E2, E6, E9, E11, E13, E19, E20, E26, E30-E33)",
    "execution": "can a signal be traded: costs, queues, fills "
                 "(E3, E5, E14, E15, E22)",
    "flow": "what client-type flow predicts (E21, E24, E25)",
}

FAMILY_OF = {
    "E1": "allocation", "E4": "allocation", "E27": "allocation",
    "E28": "allocation", "E34": "allocation", "E35": "allocation",
    "E36": "allocation",
    "E2": "selection", "E6": "selection", "E9": "selection",
    "E11": "selection", "E13": "selection", "E19": "selection",
    "E20": "selection", "E26": "selection", "E30": "selection",
    "E31": "selection", "E32": "selection", "E33": "selection",
    "E3": "execution", "E5": "execution", "E14": "execution",
    "E15": "execution", "E22": "execution",
    "E21": "flow", "E24": "flow", "E25": "flow",
}

# Experiments that computed no statistic: they are not draws from the
# distribution the hurdle describes, so they do not count in N.
NO_STATISTIC = {
    "E34": "stopped at Stage 1 on a data-layer defect before any return "
           "existed (successor E35)",
}

# Phase 1 was replayed into the ledger on 2026-08-19 from documents written
# without preregistration; those are exploratory whatever they concluded.
EXPLORATORY = {"E1", "E2", "E3", "E4", "E5", "E6", "E9", "E11", "E13",
               "E14", "E15"}

# Where a result file states the primary hypotheses' p-values, and the
# correction each experiment preregistered for itself (reading B).
PRIMARY = {
    "E27": {"file": "E27_results.json",
            "p": {"H1_passive": ("windows", "primary", "H1_test",
                                 "p_one_sided"),
                  "H2_trend": ("windows", "primary", "H2_test",
                               "p_one_sided")},
            "own_alpha": 0.025, "own_rule": "Bonferroni over 2 (0.025 each)"},
    "E28": {"file": "E28_results.json",
            "p": {"H1_gold_as_defence": ("H1_test", "p_one_sided"),
                  "H2_gold_alone": ("H2_test", "p_one_sided")},
            "own_alpha": 0.025, "own_rule": "Bonferroni over 2 (0.025 each)"},
    "E30": {"file": "E30_results.json",
            "p": {"H1": ("primary", "p_one_sided")},
            "own_alpha": 0.05, "own_rule": "one primary test"},
    "E31": {"file": "E31_results.json",
            "p": {"H1": ("primary", "p_one_sided")},
            "own_alpha": 0.025, "own_rule": "second test on E30's window"},
    "E32": {"file": "E32_results.json",
            "p": {"H1": ("primary", "p_one_sided")},
            "own_alpha": 0.0167, "own_rule": "third test on E30's window"},
    "E33": {"file": "E33_results.json",
            "p": {"H1": ("primary", "p_one_sided")},
            "own_alpha": 0.0125, "own_rule": "fourth test on the same data"},
    "E35": {"file": "E35_results.json",
            "p": {"H1_gold_vs_real_bills": ("tests", "H1_gold_vs_real_bills",
                                            "p"),
                  "H2_equity_vs_real_bills": ("tests",
                                              "H2_equity_vs_real_bills", "p"),
                  "H3_three_part_vs_gold": ("tests", "H3_three_part_vs_gold",
                                            "p")},
            "own_alpha": None, "own_rule": "Holm at 0.05 over H1-H3"},
    "E36": {"file": "E36_results.json",
            "p": {"H1_gold_vs_fixed_income": ("tests", "H1_gold_vs_fixed_income",
                                              "p"),
                  "H2_equity_vs_fixed_income": ("tests",
                                                "H2_equity_vs_fixed_income", "p"),
                  "H3_three_part_vs_gold": ("tests", "H3_three_part_vs_gold",
                                            "p")},
            "own_alpha": None,
            "own_rule": "Holm at 0.05 over H1-H3, 12-month blocks"},
}


# ---- arithmetic -------------------------------------------------------------

def norm_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_ppf(p):
    """Inverse standard normal (Acklam's rational approximation, |err|<1e-9
    after one Newton step)."""

    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    low = 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p <= 1 - low:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def expected_max_z(n):
    """Expected maximum of n independent standard normals (the approximation
    Bailey et al. use). 1 try: 0."""

    if n is None or n < 2:
        return 0.0
    return ((1 - EULER_GAMMA) * norm_ppf(1 - 1.0 / n)
            + EULER_GAMMA * norm_ppf(1 - 1.0 / (n * math.e)))


def luck_hurdle(n, years):
    """The best annual Sharpe n skill-less tries are expected to show on a
    window of `years`: E[max Z] / sqrt(years)."""

    if not years or years <= 0:
        return None
    return expected_max_z(n) / math.sqrt(years)


def psr(sr, n_obs, sr_star=0.0, skew=0.0, kurtosis=3.0):
    """Probabilistic Sharpe ratio; sr and sr_star PER PERIOD."""

    if n_obs is None or n_obs < 2:
        return None
    denominator = 1 - skew * sr + (kurtosis - 1) / 4.0 * sr * sr
    if denominator <= 0:
        return None
    return norm_cdf((sr - sr_star) * math.sqrt(n_obs - 1)
                    / math.sqrt(denominator))


def deflated_benchmark(n_trials, n_obs, sr_variance=None):
    """SR0, per period: the best Sharpe n_trials skill-less tries reach."""

    variance = sr_variance if sr_variance is not None else 1.0 / n_obs
    return math.sqrt(variance) * expected_max_z(n_trials)


def dsr(sr, n_obs, n_trials, skew=0.0, kurtosis=3.0, sr_variance=None):
    """Deflated Sharpe ratio = PSR at SR0."""

    return psr(sr, n_obs, deflated_benchmark(n_trials, n_obs, sr_variance),
               skew, kurtosis)


def holm(pvalues, alpha=FAMILY_ALPHA):
    """{key: {p, threshold, rejected}} -- step-down Holm."""

    usable = {k: v for k, v in pvalues.items() if v is not None}
    order = sorted(usable, key=usable.get)
    m = len(pvalues)
    out, still = {}, True
    for i, key in enumerate(order):
        threshold = alpha / (m - i)
        rejected = still and usable[key] <= threshold
        still = rejected
        out[key] = {"p": usable[key], "threshold": round(threshold, 6),
                    "rejected": bool(rejected)}
    for key in pvalues:
        if key not in usable:
            out[key] = {"p": None, "threshold": None, "rejected": False}
    return out


def benjamini_hochberg(pvalues, q=FAMILY_ALPHA):
    usable = {k: v for k, v in pvalues.items() if v is not None}
    order = sorted(usable, key=usable.get)
    m = len(pvalues)
    cutoff = 0
    for i, key in enumerate(order, 1):
        if usable[key] <= q * i / m:
            cutoff = i
    return {key: {"p": pvalues[key],
                  "rejected": key in order[:cutoff]} for key in pvalues}


# ---- the record ----------------------------------------------------------------

def _short(experiment_id):
    return experiment_id.split("-")[0]


def _dig(doc, path):
    for key in path:
        if not isinstance(doc, dict) or key not in doc:
            return None
        doc = doc[key]
    return doc if isinstance(doc, (int, float)) else None


def _load(name):
    try:
        with open(os.path.join(OUTPUT, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def trials(path=None):
    """Every experiment in the research store, with what this module needs."""

    from sirat.experiments import store

    store.initialise(path)
    conn = store._connect(path) if hasattr(store, "_connect") else None
    rows = []
    try:
        if conn is None:
            import sqlite3
            conn = sqlite3.connect(path or store.DB_PATH)
        found = conn.execute(
            "SELECT e.experiment_id, e.registered_at, d.decision, "
            "d.rejected_at_stage FROM experiments e LEFT JOIN decisions d "
            "ON d.experiment_id = e.experiment_id ORDER BY e.registered_at"
        ).fetchall()
    finally:
        conn.close()

    for experiment_id, registered_at, decision, stage in found:
        short = _short(experiment_id)
        spec = PRIMARY.get(short)
        pvalues = {}
        if spec:
            doc = _load(spec["file"])
            for name, where in spec["p"].items():
                pvalues[name] = _dig(doc, where) if doc else None
        rows.append({
            "experiment_id": experiment_id,
            "short": short,
            "family": FAMILY_OF.get(short, "unassigned"),
            "decision": decision,
            "rejected_at_stage": stage,
            "registered_at": registered_at,
            "counts_in_n": short not in NO_STATISTIC,
            "why_not_counted": NO_STATISTIC.get(short),
            "confirmatory": short not in EXPLORATORY,
            "pvalues": pvalues,
            "own_alpha": spec["own_alpha"] if spec else None,
            "own_rule": spec["own_rule"] if spec else None,
        })
    return rows


def readings(rows=None, path=None):
    """Readings A, B and C of every stated p-value (module docstring)."""

    rows = rows if rows is not None else trials(path)
    counted = [r for r in rows if r["counts_in_n"]]

    everything = {}
    for r in counted:
        if r["pvalues"]:
            for name, p in r["pvalues"].items():
                everything["%s %s" % (r["short"], name)] = p
        else:
            everything["%s (no p stated)" % r["short"]] = None

    a = holm(everything)

    b = {}
    for r in counted:
        for name, p in r["pvalues"].items():
            key = "%s %s" % (r["short"], name)
            if r["own_alpha"] is None:
                own = holm(r["pvalues"])[name]["rejected"]
            else:
                own = p is not None and p < r["own_alpha"]
            b[key] = {"p": p, "rule": r["own_rule"], "rejected": bool(own)}

    c = {}
    for family in FAMILIES:
        members = {k: v for k, v in everything.items()
                   if FAMILY_OF.get(k.split(" ")[0]) == family}
        if members:
            c[family] = holm(members)

    return {"A_one_family": a, "B_as_preregistered": b,
            "C_declared_families": c,
            "C_note": "families declared %s: prospective for E35 onward, post "
                      "hoc for everything earlier" % DECLARED_ON,
            "n_hypotheses": len(everything)}


def window_use(path=None):
    """{year: [experiment ids that evaluated any part of it]}."""

    from sirat.experiments import store
    import sqlite3

    store.initialise(path)
    conn = sqlite3.connect(path or store.DB_PATH)
    try:
        rows = conn.execute("SELECT experiment_id, window_start, window_end, "
                            "purpose FROM windows_touched").fetchall()
    finally:
        conn.close()

    use = {}
    for experiment_id, start, end, purpose in rows:
        if _short(experiment_id) in NO_STATISTIC:
            continue
        y0, y1 = int(str(start)[:4]), int(str(end)[:4])
        for year in range(y0, y1 + 1):
            use.setdefault(year, set()).add(_short(experiment_id))
    return {y: sorted(v, key=lambda s: int(s[1:])) for y, v in sorted(use.items())}


def window_budget(start, end, path=None, use=None, n_all=None):
    """The stop rule for a proposed window.

    trials = experiments that already evaluated any year of it; the next test
    would be trial n + 1. Exhausted when the expected best annual Sharpe of
    n + 1 skill-less tries reaches STOP_SHARPE: from then on only data the
    programme has not seen can confirm anything there.
    """

    use = use if use is not None else window_use(path)
    s, e = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    years = max((e - s).days / 365.25, 1e-9)
    seen = set()
    for year in range(s.year, e.year + 1):
        seen.update(use.get(year, ()))
    n = len(seen)
    # Phase 1's replays and E20/E22/E24/E25 recorded no window, though they
    # read Tehran history too: the recorded count is a LOWER bound, every
    # trial in the record an upper one. The stop rule uses the upper bound.
    upper = max(n, n_all if n_all is not None else
                sum(1 for r in trials(path) if r["counts_in_n"]))
    hurdle_next = luck_hurdle(upper + 1, years)
    max_trials = 1
    while luck_hurdle(max_trials + 1, years) < STOP_SHARPE and max_trials < 10_000:
        max_trials += 1
    return {"window": [str(s), str(e)], "years": round(years, 2),
            "trials_recorded": n, "trials_upper_bound": upper,
            "experiments_recorded": sorted(seen, key=lambda x: int(x[1:])),
            "luck_hurdle_next_annual_sharpe": round(hurdle_next, 3),
            "max_trials_before_stop": max_trials,
            "exhausted": hurdle_next >= STOP_SHARPE}


def deflated(path=None, rows=None):
    """DSR of the results that were accepted under their own rule, at the
    programme's N, from the statistics their result files state (normal
    returns assumed where the series is not stored -- declared)."""

    rows = rows if rows is not None else trials(path)
    n = sum(1 for r in rows if r["counts_in_n"])
    out = []

    e27 = _load("E27_results.json")
    if e27:
        w = e27["windows"]["primary"]
        t, blocks = w["H1_test"]["t"], w["blocks"]
        sr = t / math.sqrt(blocks)
        out.append(_dsr_row("E27 H1 passive equity vs 20% fund", sr, blocks,
                            12.0, n, "t / sqrt(blocks), 20-session blocks"))

    e28 = _load("E28_results.json")
    if e28:
        t, blocks = e28["H2_test"]["t"], e28["blocks"]
        out.append(_dsr_row("E28 H2 gold alone vs 20% fund",
                            t / math.sqrt(blocks), blocks, 12.0, n,
                            "t / sqrt(blocks), 20-session blocks"))

    e35 = _load("E35_results.json")
    if e35 and e35.get("books"):
        months = e35["months"]
        for name, label in (("eq70_gold30", "E35 70/30 equity/gold vs real bills"),
                            ("equity_100", "E35 equity vs real bills")):
            sr = e35["books"][name]["sharpe_monthly_vs_bills"]
            if sr is not None:
                out.append(_dsr_row(label, sr, months, 12.0, n, "monthly"))
    return {"n_trials": n, "rows": out}


def _dsr_row(label, sr, n_obs, per_year, n_trials, basis):
    value = dsr(sr, n_obs, n_trials)
    p0 = psr(sr, n_obs, 0.0)
    years = n_obs / per_year if per_year else None
    return {"result": label, "sr_per_period": round(sr, 4), "n_obs": n_obs,
            "basis": basis, "years": round(years, 1) if years else None,
            "psr_vs_zero": round(p0, 4) if p0 is not None else None,
            "dsr": round(value, 4) if value is not None else None,
            "passes_dsr_hurdle": bool(value is not None and value >= DSR_HURDLE)}


def prereg_completeness(doc):
    """The five fields every preregistration from E36 on must carry.

    Returns the list of missing items (empty = complete). Matching is on
    content, not one fixed schema: the experiments write their files in
    their own words, and the point is that the question was answered.
    """

    text = json.dumps(doc, ensure_ascii=False).lower()
    missing = []
    if "family" not in text or not any(w in text for w in
                                       ("holm", "bonferroni", "benjamini",
                                        "hochberg")):
        missing.append("family and multiple-testing correction")
    if not any(w in text for w in ("benchmark", "control", "against")):
        missing.append("the exact benchmark series")
    if "window" not in text:
        missing.append("the data window")
    if not any(w in text for w in ("cost", "round trip", "commission")):
        missing.append("the cost model")
    if not any(w in text for w in ("accept", "decision_rule", "decision rule")):
        missing.append("the acceptance rule")
    return missing


# The owner's rule of 2026-11-24: Tehran's market moves in cycles of six to
# seven years (in dollars it bottomed in 1391/07, 1397/07 and 1404/06), so a
# test that sees five years sees one phase. From E36 on a preregistration
# names its window as dates, and a window under MIN_WINDOW_YEARS says why.
MIN_WINDOW_YEARS = 15.0
WINDOW_TOLERANCE_YEARS = 0.05


def _date_of(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def window_rule(doc):
    """[] when the window is fifteen years or explained; else what is missing."""

    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    start = _date_of(doc.get("window_start") or data.get("window_start"))
    end = _date_of(doc.get("window_end") or data.get("window_end"))
    if not (start and end) or end <= start:
        return ["window_start and window_end as dates"]
    years = (end - start).days / 365.25
    reason = str(doc.get("window_shorter_reason") or "").strip()
    if years < MIN_WINDOW_YEARS - WINDOW_TOLERANCE_YEARS and not reason:
        return ["a %.0f-year window (about two market cycles), or "
                "window_shorter_reason (this one is %.1f years)"
                % (MIN_WINDOW_YEARS, years)]
    return []


def summary(path=None):
    rows = trials(path)
    n = sum(1 for r in rows if r["counts_in_n"])
    use = window_use(path)
    return {
        "n_experiments": len(rows),
        "n_trials": n,
        "not_counted": {r["short"]: r["why_not_counted"]
                        for r in rows if not r["counts_in_n"]},
        "expected_best_z": round(expected_max_z(n), 3),
        "luck_hurdle_annual_sharpe": {
            "5.3y": round(luck_hurdle(n, 5.3), 3),
            "9y": round(luck_hurdle(n, 9.0), 3),
            "15y": round(luck_hurdle(n, 15.0), 3)},
        "readings": readings(rows),
        "deflated": deflated(rows=rows),
        "window_use": use,
        "budgets": {
            "selection 2021-06..2026-08": window_budget(
                "2021-06-02", "2026-08-18", use=use, n_all=n),
            "allocation 2017-10..2026-07": window_budget(
                "2017-10-01", "2026-07-31", use=use, n_all=n),
            "full 2009..2026-08": window_budget(
                "2009-01-01", "2026-08-18", use=use, n_all=n)},
        "rules": {"family_alpha": FAMILY_ALPHA, "dsr_hurdle": DSR_HURDLE,
                  "t_hurdle_for_new_positive": T_HURDLE,
                  "stop_sharpe": STOP_SHARPE,
                  "exploratory_policy": "counted in N; never accepted; may at "
                                        "most nominate a new test on new data"},
    }


def markdown(path=None):
    """The section the generated ledger carries after its summary table."""

    s = summary(path)
    r = s["readings"]
    lines = [
        "## Multiple testing (generated)",
        "",
        "Every number here is recomputed from the record and the result files "
        "on each export (sirat/experiments/multiplicity.py). Nothing here "
        "changes a verdict above; it says how much each one is worth.",
        "",
        "| | |", "|---|---|",
        "| experiments in the record | %d |" % s["n_experiments"],
        "| trials counted (N) | %d |" % s["n_trials"],
        "| not counted | %s |" % ("; ".join("%s: %s" % kv for kv in
                                             s["not_counted"].items()) or "—"),
        "| expected best standardised Sharpe of N skill-less tries | %.2f |"
        % s["expected_best_z"],
        "| ... per year on 5.3 / 9 / 15 years | %.2f / %.2f / %.2f |"
        % tuple(s["luck_hurdle_annual_sharpe"][k] for k in ("5.3y", "9y", "15y")),
        "| a new positive needs | t > %.0f or DSR > %.2f, not p < 0.05 |"
        % (T_HURDLE, DSR_HURDLE),
        "",
        "### Reading A — every hypothesis one family (Holm at 0.05, m = %d)"
        % r["n_hypotheses"], "",
        "| hypothesis | p | threshold | survives |", "|---|---|---|---|",
    ]
    for key, v in sorted(r["A_one_family"].items(),
                         key=lambda kv: (kv[1]["p"] is None, kv[1]["p"] or 0)):
        if v["p"] is None:
            continue
        lines.append("| %s | %.5f | %s | %s |" % (
            key, v["p"], v["threshold"], "**yes**" if v["rejected"] else "no"))
    lines += ["", "Hypotheses with no p-value in a result file count in m "
              "and never survive.", "",
              "### Reading B — each experiment's own preregistered rule", "",
              "| hypothesis | p | rule | accepted |", "|---|---|---|---|"]
    for key, v in sorted(r["B_as_preregistered"].items()):
        lines.append("| %s | %s | %s | %s |" % (
            key, "—" if v["p"] is None else "%.5f" % v["p"], v["rule"],
            "**yes**" if v["rejected"] else "no"))
    lines += ["", "### Reading C — families declared %s" % DECLARED_ON, "",
              "Prospective for E35 onward; post hoc for everything earlier "
              "(the boundaries were drawn after E27 and E28 were known), "
              "which is why A and B stand beside it.", ""]
    for family, members in r["C_declared_families"].items():
        lines += ["**%s** — %s" % (family, FAMILIES[family]), "",
                  "| hypothesis | p | threshold | survives |",
                  "|---|---|---|---|"]
        for key, v in sorted(members.items(),
                             key=lambda kv: (kv[1]["p"] is None, kv[1]["p"] or 0)):
            lines.append("| %s | %s | %s | %s |" % (
                key, "—" if v["p"] is None else "%.5f" % v["p"],
                v["threshold"] if v["threshold"] is not None else "—",
                "**yes**" if v["rejected"] else "no"))
        lines.append("")
    d = s["deflated"]
    lines += ["### Deflated Sharpe ratio at N = %d" % d["n_trials"], "",
              "| result | SR per period | obs | basis | PSR vs 0 | DSR | > %.2f |"
              % DSR_HURDLE, "|---|---|---|---|---|---|---|"]
    for row in d["rows"]:
        lines.append("| %s | %.4f | %d | %s | %s | %s | %s |" % (
            row["result"], row["sr_per_period"], row["n_obs"], row["basis"],
            row["psr_vs_zero"], row["dsr"],
            "**yes**" if row["passes_dsr_hurdle"] else "no"))
    lines += ["", "Normal returns assumed (skew 0, kurtosis 3) where the "
              "result file does not store the series; fat tails lower the DSR.",
              "", "### Windows: use and the stop rule", "",
              "| year | experiments that evaluated it |", "|---|---|"]
    for year, ids in s["window_use"].items():
        lines.append("| %d | %d — %s |" % (year, len(ids), ", ".join(ids)))
    lines += ["", "| window | years | trials recorded / upper bound | next "
              "trial's luck hurdle (annual Sharpe) | max trials | exhausted |",
              "|---|---|---|---|---|---|"]
    for name, b in s["budgets"].items():
        lines.append("| %s | %.1f | %d / %d | %.2f | %d | %s |" % (
            name, b["years"], b["trials_recorded"], b["trials_upper_bound"],
            b["luck_hurdle_next_annual_sharpe"], b["max_trials_before_stop"],
            "**yes**" if b["exhausted"] else "no"))
    lines += ["", "Windows recorded by the Manager only: Phase 1's replays and "
              "E20/E22/E24/E25 recorded none, so the per-year counts are a "
              "lower bound; the stop rule uses every trial as the upper bound.",
              "", "Stop rule: once a window's next trial would expect a best "
              "annual Sharpe of %.1f from luck alone, no further historical "
              "test on it can confirm anything; only data the programme has "
              "not seen can (the forward log)." % STOP_SHARPE,
              "Exploratory experiments (Phase 1, replayed without "
              "preregistration) count in N and are never accepted.", "", "---",
              ""]
    return lines


def main():
    print(json.dumps(summary(), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
