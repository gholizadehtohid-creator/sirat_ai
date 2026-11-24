"""Every panel card's evidence, in one registry the panel reads at runtime.

WHY THIS EXISTS
---------------
The owner asked (2026-11-24) for "a track-record line and status badge on
every panel card, and no entry/exit levels shown for strategies the bot's own
tests rejected". The evidence existed -- in backtest_output's results and
preregistrations, the research ledger and the experiment reports -- but no
card said which of it applied to it, and the basket and the technical cards
went on quoting entry, stop and target prices for strategies the same
programme had rejected.

So each card here maps to its evidence, read from those files when asked: no
number below is typed by hand where a file has it. When a file is missing or
its shape changed, that card says so (`error`) and every other card is
unaffected.

THE FOUR BADGES
---------------
  تأییدشده  accepted     a preregistered test of the card's claim passed its
                         decision rule and no later re-test has overturned it.
  موقت      provisional  the evidence is in-sample only, or a re-test or a
                         forward test of it is still running: read it with
                         the stated limits.
  ردشده     rejected     the bot's own test of this strategy failed its
                         decision rule or lost to holding TEDPIX. Its entry,
                         stop and target levels are not to be shown without
                         an explicit reveal (`hide_levels`).
  توصیفی    descriptive  the card reports what is (a price, a flow, a yield, a
                         filing) and claims nothing about future returns, so
                         there is nothing of it to test. Where a test of a
                         USE of it exists, the warning names it.

COSTS
-----
Every registered backtest ran at the 1.039-1.04% round trip of its day;
config.LIVE_ROUND_TRIP_PCT (1.2512%, measured 2026-11-23) is what a share
trade costs now. None of the stock-picking experiments was re-run at the live
cost, so `vs_tedpix.after_live_cost_fa` says «اندازه‌گیری نشده» and that the
live result can only be worse; E35 (the successor of E34, which stopped at
Stage 1 on a bill-maturity defect) charges the live fund round trip and is
reported as such.

    python track_record.py              one line per card
    python track_record.py --json       the full registry
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(ROOT, "backtest_output")
RESEARCH_DB = os.path.join(ROOT, "sirat_research.db")
LEDGER_MD = os.path.join(ROOT, "RESEARCH_LEDGER.md")
CALIBRATION = os.path.join(ROOT, "data", "outlook_calibration.json")

ACCEPTED, PROVISIONAL, REJECTED, DESCRIPTIVE = (
    "accepted", "provisional", "rejected", "descriptive")

BADGES = {
    ACCEPTED: {"fa": "تأییدشده",
               "definition_fa": "یک آزمون از پیش ثبت‌شده ادعای این کارت را بر "
                                "پایه‌ی قاعده‌ی تصمیمش پذیرفت و هیچ آزمون بعدی آن "
                                "را برنگردانده است."},
    PROVISIONAL: {"fa": "موقت",
                  "definition_fa": "شواهد فقط درون‌نمونه است، یا آزمون دوباره یا "
                                   "آزمون پیش‌روی آن هنوز در جریان است؛ با همان "
                                   "محدودیت‌های گفته‌شده بخوانید."},
    REJECTED: {"fa": "ردشده",
               "definition_fa": "آزمون خود این ربات این راهبرد را رد کرد یا "
                                "نشان داد به نگه‌داشتن شاخص کل باخته است؛ سطوح "
                                "ورود، حد ضرر و هدف آن فقط با درخواست صریح "
                                "نشان داده می‌شود."},
    DESCRIPTIVE: {"fa": "توصیفی",
                  "definition_fa": "کارت واقعیتی را گزارش می‌کند (قیمت، جریان "
                                   "پول، بازده، اطلاعیه) و ادعایی درباره‌ی بازده "
                                   "آینده ندارد که آزمودنی باشد."},
}

NOT_MEASURED = "اندازه‌گیری نشده"
IN_TEST = "در حال آزمون"
SAMPLE_FA = {"out_of_sample": "بیرون از نمونه", "in_sample": "درون‌نمونه",
             "mixed": "بخشی بیرون از نمونه، بخشی درون‌نمونه",
             "forward": "آزمون پیش‌رو", "none": "آزموده نشده"}
HIDDEN_LEVELS_LABEL_FA = "ردشده در آزمون، فقط برای پژوهش"


# ==============================
# READERS -- each cached by mtime, none raises
# ==============================

_FILES = {}
_FILES_LOCK = threading.Lock()


def _cached(path, loader):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _FILES_LOCK:
        held = _FILES.get(path)
        if held and held[0] == mtime:
            return held[1]
    try:
        value = loader(path)
    except (OSError, ValueError):
        value = None
    with _FILES_LOCK:
        _FILES[path] = (mtime, value)
    return value


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _load_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def results(name):
    """backtest_output/<name> as JSON, or None."""

    return _cached(os.path.join(OUTPUT_DIR, name), _load_json)


def text(path):
    return _cached(path, _load_text)


def short_id(experiment_id):
    match = re.match(r"(E\d+)", str(experiment_id or ""))
    return match.group(1) if match else str(experiment_id)


def _ledger_md():
    """{short id: {full_id, decision, stage}} from RESEARCH_LEDGER.md."""

    out = {}
    body = text(LEDGER_MD) or ""
    for match in re.finditer(r"^\|\s*\d+\s*\|\s*`([^`]+)`\s*\|\s*\*\*(\w+)\*\*\s*\|"
                             r"\s*([^|]*)\|", body, re.M):
        full, decision, stage = match.groups()
        out[short_id(full)] = {"full_id": full, "decision": decision,
                               "stage": stage.strip() or None,
                               "source": "RESEARCH_LEDGER.md"}
    return out


def _ledger_db():
    """The same from sirat_research.db's hash-chained ledger (read-only):
    RESEARCH_LEDGER.md is an export and stops at entry 13."""

    if not os.path.exists(RESEARCH_DB):
        return {}
    out = {}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % RESEARCH_DB.replace("\\", "/"),
                               uri=True)
        try:
            rows = conn.execute("SELECT experiment_id, payload FROM ledger "
                                "ORDER BY seq").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    for full, payload in rows:
        try:
            verdict = (json.loads(payload) or {}).get("final_verdict") or {}
        except ValueError:
            continue
        out[short_id(full)] = {"full_id": full, "decision": verdict.get("decision"),
                               "stage": verdict.get("rejected_at_stage"),
                               "rationale": verdict.get("rationale"),
                               "source": "sirat_research.db ledger"}
    return out


def _windows_touched():
    if not os.path.exists(RESEARCH_DB):
        return {}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % RESEARCH_DB.replace("\\", "/"),
                               uri=True)
        try:
            rows = conn.execute("SELECT experiment_id, window_start, window_end "
                                "FROM windows_touched").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {short_id(e): (s, t) for e, s, t in rows}


_LEDGER = {"at": None, "value": None}


def ledger():
    """{short id: decision record}: the markdown export, overlaid by the
    database ledger where that is readable."""

    now = time.monotonic()
    if _LEDGER["at"] is not None and now - _LEDGER["at"] < 60:
        return _LEDGER["value"]
    value = _ledger_md()
    value.update(_ledger_db())
    _LEDGER.update(at=now, value=value)
    return value


def report_decision(name):
    """'**Decision: X' or the '# ' headline of an experiment report."""

    body = text(os.path.join(ROOT, name)) or ""
    match = re.search(r"\*\*Decision:?\s*([A-Z]+)", body)
    if match:
        return match.group(1)
    match = re.search(r"^#\s+(.+)$", body, re.M)
    return match.group(1).strip() if match else None


def cost_used(prereg_name):
    """The round-trip cost (%) a preregistration declares, or None."""

    body = text(os.path.join(OUTPUT_DIR, prereg_name)) or ""
    match = re.search(r"(\d+\.\d+)%\s*(?:per\s+)?(?:round[ -]trip|per full)", body)
    if match:
        return float(match.group(1))
    match = re.search(r"ROUND_TRIP_PCT\s*=\s*(\d+\.\d+)%", body)
    return float(match.group(1)) if match else None


def live_cost():
    try:
        import config
        return float(config.LIVE_ROUND_TRIP_PCT)
    except Exception:                                           # noqa: BLE001
        return None


def live_fund_cost():
    try:
        import config
        return float(config.LIVE_FUND_ROUND_TRIP_PCT)
    except Exception:                                           # noqa: BLE001
        return None


BILL_YIELD = os.path.join(ROOT, "data", "bill_yield.json")


def bill_yield():
    """The last bill yield opportunities.treasury_bills() stored, any age,
    or None. Read from its file (opportunities.BILL_YIELD_FILE) rather than
    through the module, whose import costs ~1.5 s on a cold panel."""

    record = _cached(BILL_YIELD, _load_json)
    try:
        return {"yield_pct": float(record["yield_pct"]), "as_of": record["as_of"],
                "bills": record.get("bills")}
    except (TypeError, KeyError, ValueError):
        return None


# ==============================
# FORMATTING
# ==============================

def pct(value, digits=1, signed=False):
    if value is None:
        return "?"
    return ("%+.*f٪" if signed else "%.*f٪") % (digits, float(value))


def num(value, digits=0):
    if value is None:
        return "?"
    return format(round(float(value), digits), ",.%df" % digits)


def period(start, end):
    return "%s..%s" % (start, end)


def _experiment(sid, decision=None, source=None):
    entry = dict(ledger().get(sid) or {})
    entry.pop("rationale", None)
    entry["id"] = sid
    if decision:
        entry["decision"] = decision
    if source:
        entry["source"] = source
    entry.setdefault("full_id", sid)
    return entry


def _after_live(cost):
    live = live_cost()
    if cost is None:
        return NOT_MEASURED
    return ("%s — با هزینه‌ی %s آزموده شد؛ هزینه‌ی امروز %s است، پس نتیجه‌ی "
            "واقعی از این بدتر است" % (NOT_MEASURED, pct(cost, 3), pct(live, 4)))


def _vs(text_fa, cost, measured=True, after=None):
    return {"measured": measured, "text_fa": text_fa, "cost_used_pct": cost,
            "live_cost_pct": live_cost(),
            "after_live_cost_fa": after if after is not None else
            (_after_live(cost) if measured else NOT_MEASURED)}


def _card(cid, title, tab, panel_titles, badge, *, reason, experiments,
          test_period, n, hit_rate, vs_tedpix, sample, warning, hide_levels,
          doubt=None, sources=(), extra=None, headline=None):
    sample_fa = SAMPLE_FA.get(sample, sample)
    ids = [e["id"] for e in experiments]
    line = [BADGES[badge]["fa"]]
    if ids:
        line.append("، ".join(ids))
    if test_period:
        line.append(test_period)
    if headline:
        line.append(headline)
    line.append(sample_fa)
    return {
        "id": cid, "title_fa": title, "tab": tab, "panel_titles": list(panel_titles),
        "badge": BADGES[badge]["fa"], "badge_key": badge, "badge_reason_fa": reason,
        "experiments": ids, "experiments_detail": experiments,
        "test_period": test_period, "n": n, "hit_rate": hit_rate,
        "vs_tedpix": vs_tedpix, "sample": sample, "sample_fa": sample_fa,
        "warning_fa": warning, "hide_levels": bool(hide_levels),
        "hidden_levels_label_fa": HIDDEN_LEVELS_LABEL_FA if hide_levels else None,
        "doubt": doubt, "track_line_fa": " · ".join(line),
        "sources": list(sources), "extra": extra or {}, "error": None,
    }


# ==============================
# THE CARDS
# ==============================

def _e34():
    """The real-bills re-test's state (E35, successor of E34): running, no
    result, or its per-hypothesis verdicts."""

    data = results("E35_results.json")
    if data is None:
        return {"state": "running", "state_fa": IN_TEST}
    verdict = data.get("verdict")
    if not isinstance(verdict, dict) or verdict.get("stage_1") == "FAIL":
        return {"state": "no_result", "state_fa": "بی‌نتیجه (مرحله‌ی ۱)",
                "verdict": verdict, "data": data}
    h1 = str(verdict.get("H1_gold_vs_real_bills") or "")
    h2 = str(verdict.get("H2_equity_vs_real_bills") or "")
    return {"state": "done", "state_fa": "انجام شد", "verdict": verdict,
            "h1": h1 == "ACCEPT", "h2": h2 == "ACCEPT", "data": data}


def _allocation_evidence():
    e27 = results("E27_results.json")
    e28 = results("E28_results.json")
    if not e27 or not e28:
        raise ValueError("E27_results.json or E28_results.json is missing")
    p, sec = e27["windows"]["primary"], e27["windows"]["secondary"]
    books = e28["books"]
    e34 = _e34()
    bills = bill_yield()
    exps = [_experiment("E27", "H1 %s، H2 %s" % (e27["verdict"]["H1_passive"],
                                                 e27["verdict"]["H2_trend"]),
                        "backtest_output/E27_results.json"),
            _experiment("E28", "H1 %s، H2 %s" % (e28["verdict"]["H1_gold_as_defence"],
                                                 e28["verdict"]["H2_gold_alone"]),
                        "backtest_output/E28_results.json"),
            _experiment("E35", e34["state_fa"] if e34["state"] != "done" else
                        "H1 %s، H2 %s" % (e34["verdict"].get("H1_gold_vs_real_bills"),
                                          e34["verdict"].get("H2_equity_vs_real_bills")),
                        "backtest_output/E35_results.json" if e34["state"] != "running"
                        else "backtest_output/E35_preregistration.json")]
    claim = ("سهام %s در سال در برابر صندوق %s (E27، %s)؛ 70/30 سهام/طلا شارپ %.2f "
             "در برابر %.2f برای 70/30 سهام/درآمد ثابت (E28، %s)"
             % (pct(p["H1_passive"]["cagr_pct"]), pct(p["fund_20pct"]["cagr_pct"], 0),
                period(*p["range"]), books["eq70_gold30"]["sharpe_vs_fund"],
                books["eq70_fund30"]["sharpe_vs_fund"], period(*e28["range"])))
    after = NOT_MEASURED + " (E35 %s)" % e34["state_fa"]
    if e34["state"] == "done":
        b = e34["data"].get("books") or {}
        bill_cagr = ((e34["data"].get("bills") or {}).get("cagr_pct"))
        eq = (b.get("equity_100") or {}).get("cagr_pct")
        after = ("E35 با هزینه‌ی امروز صندوق (%s): سهام %s در سال در برابر اخزای "
                 "واقعی %s" % (pct(live_fund_cost(), 4), pct(eq), pct(bill_cagr)))
    if e34["state"] == "done" and e34["h1"] and e34["h2"]:
        badge, reason = ACCEPTED, ("E27 و E28 پذیرفته شدند و E35 هر دو را در برابر "
                                   "بازده واقعی اخزا دوباره پذیرفت.")
    else:
        badge = PROVISIONAL
        if e34["state"] == "running":
            reason = ("E27 و E28 در برابر صندوق فرضی %s پذیرفته شدند؛ آزمون دوباره "
                      "با بازده واقعی اخزا (E35) %s است."
                      % (pct(p["fund_20pct"]["cagr_pct"], 0), IN_TEST))
        elif e34["state"] == "no_result":
            reason = ("E35 در مرحله‌ی ۱ بی‌نتیجه ماند؛ پذیرش E27 و E28 فقط در برابر "
                      "صندوق فرضی %s است." % pct(p["fund_20pct"]["cagr_pct"], 0))
        else:
            failed = [x for x, ok in (("طلا (H1)", e34["h1"]), ("سهام (H2)", e34["h2"]))
                      if not ok]
            reason = ("E35 برتری %s را بر اخزای واقعی نشان نداد؛ E27/E28 فقط در "
                      "برابر صندوق فرضی %s پذیرفته شده‌اند."
                      % (" و ".join(failed), pct(p["fund_20pct"]["cagr_pct"], 0)))
    bills_clause = ("، در حالی که اخزا آخرین بار %s داد (%s)"
                    % (pct(bills["yield_pct"]), bills["as_of"])) if bills else ""
    if e34["state"] == "done":
        tests_ = e34["data"].get("tests") or {}

        def said(key, ok):
            p_ = (tests_.get(key) or {}).get("p")
            return ("پذیرفته شد" if ok else "از آستانه‌ی آماری نگذشت") +                 (" (p=%.3f)" % p_ if isinstance(p_, (int, float)) else "")

        lines = []
        lines.append("طلا در برابر اخزای واقعی: %s" % said(
            "H1_gold_vs_real_bills", e34["h1"]))
        lines.append("سهام در برابر اخزای واقعی: %s" % said(
            "H2_equity_vs_real_bills", e34["h2"]))
        warning = ("آزمون دوباره با بازده واقعی اخزا (E35): %s؛ هر بخشی که پذیرفته "
                   "نشده، برتری‌اش بر درآمد ثابت واقعی نشان داده نشده است."
                   % "؛ ".join(lines))
    else:
        warning = ("تأیید E27 و E28 در برابر صندوق درآمد ثابت فرضیِ %s در سال بود%s؛ "
                   "برتری سهام و طلا بر درآمد ثابتِ واقعی تا نتیجه‌ی E35 ثابت نشده است."
                   % (pct(p["fund_20pct"]["cagr_pct"], 0), bills_clause))
    n = {"value": p["blocks"], "unit_fa": "بلوک ۲۰ جلسه‌ای",
         "denominator": p["sessions"],
         "text_fa": "%s بلوک ۲۰ جلسه‌ای از %s جلسه (E27، دوره‌ی اصلی)؛ %s بلوک از %s "
                    "جلسه (E28)" % (num(p["blocks"]), num(p["sessions"]),
                                    num(e28["blocks"]), num(e28["sessions"]))}
    if e34["state"] == "done" and e34["data"].get("months"):
        n["text_fa"] += "؛ %s ماه (E35)" % num(e34["data"]["months"])
    return {
        "badge": badge, "reason": reason, "experiments": exps,
        "test_period": "%s (E27)؛ %s (E28)" % (period(*p["range"]), period(*e28["range"])),
        "n": n,
        "vs": _vs("این کارت در بخش سهام خودِ شاخص را نگه می‌دارد؛ آزمون در برابر "
                  "درآمد ثابت بود: " + claim, cost_used("E27_preregistration.json"),
                  after=after),
        "warning": warning,
        "doubt": ("پیش‌ثبت E27 و E28 بازده درآمد ثابت را ثابتِ %s فرض کرد؛ عدد واقعی "
                  "اخزا در بیشتر این دوره بالاتر بود." % pct(p["fund_20pct"]["cagr_pct"], 0)
                  if e34["state"] != "done" else None),
        "extra": {"retest": {"id": "E35", "state": e34["state"],
                             "state_fa": e34["state_fa"],
                             "verdict": e34.get("verdict")},
                  "bill_yield": bills,
                  "secondary_window": {"range": sec["range"],
                                       "equity_cagr_pct": sec["H1_passive"]["cagr_pct"],
                                       "p_one_sided": sec["H1_test"]["p_one_sided"]}},
        "sources": ["backtest_output/E27_results.json", "backtest_output/E28_results.json",
                    "backtest_output/E35_results.json", "data/bill_yield.json"],
    }


def card_allocation():
    ev = _allocation_evidence()
    return _card("allocation", "چقدر در سهام، طلا و درآمد ثابت؟", "summary",
                 ["چقدر در سهام، طلا و درآمد ثابت؟"], ev["badge"],
                 reason=ev["reason"], experiments=ev["experiments"],
                 test_period=ev["test_period"], n=ev["n"], hit_rate=None,
                 vs_tedpix=ev["vs"], sample="mixed", warning=ev["warning"],
                 hide_levels=False, doubt=ev["doubt"], sources=ev["sources"],
                 extra=ev["extra"], headline="سهام و طلا در برابر درآمد ثابت")


def card_today():
    ev = _allocation_evidence()
    return _card("today", "امروز چه کنم؟", "summary", ["امروز چه کنم؟"],
                 ev["badge"], reason=ev["reason"], experiments=ev["experiments"],
                 test_period=ev["test_period"], n=ev["n"], hit_rate=None,
                 vs_tedpix=ev["vs"], sample="mixed",
                 warning=("سفارش‌های این کارت فقط وزن‌های کارت تخصیص را اجرا "
                          "می‌کنند؛ اعتبارشان همان اعتبار E27 و E28 است و انتخاب "
                          "صندوقِ مشخص یا روز خرید آزموده نشده است. " + ev["warning"]),
                 hide_levels=False, doubt=ev["doubt"], sources=ev["sources"],
                 extra=ev["extra"], headline="اجرای وزن‌های تخصیص")


def card_basket():
    e26 = results("E26_results.json")
    if not e26:
        raise ValueError("E26_results.json is missing")
    windows = sorted(e26["windows"].items())
    (w1, a), (w2, b) = windows[0], windows[-1]
    vs1 = (pct(a["total_return_pct"]["baseline"]), pct(a["benchmarks_pct"]["tedpix"]), w1)
    vs2 = (pct(b["total_return_pct"]["baseline"]), pct(b["benchmarks_pct"]["tedpix"]), w2)
    pctile = b["selection_percentile_vs_random"]["engine_exits"]["baseline"]
    v1v2 = results("backtest_v1_vs_v2.json") or {}
    v2 = (v1v2.get("profiles") or {}).get("v2") or {}
    hit = None
    if v2.get("win_rate_pct") is not None:
        hit = {"value": round(v2["win_rate_pct"] / 100, 4),
               "text_fa": "%s معاملات سودده (%s معامله، %s)" % (
                   pct(v2["win_rate_pct"]), num(v2.get("trades")),
                   period(v1v2.get("start"), v1v2.get("end"))),
               "definition_fa": "سهم معاملات بسته‌شده با سود، backtest_v1_vs_v2.json "
                                "(پیش از E26، بدون سود پول نقد)"}
    verdict = str(e26.get("verdict") or "")
    return _card(
        "basket", "ترکیب پیشنهادی سبد", "stocks",
        ["ترکیب پیشنهادی سبد", "توزیع صنعتی سبد", "ریسک سبد"], REJECTED,
        reason=("E26 (حد ضرر عریض‌تر و پر کردن هفتگی) طبق قاعده‌ی پیش‌ثبت بازگردانده "
                "شد (%s) و سبد با پیکربندی فعلی در هر دو دوره %s و %s واحد درصد از "
                "شاخص عقب ماند."
                % (verdict.split(".")[0],
                   num(a["benchmarks_pct"]["tedpix"] - a["total_return_pct"]["baseline"], 1),
                   num(b["benchmarks_pct"]["tedpix"] - b["total_return_pct"]["baseline"], 1))),
        experiments=[_experiment("E26", verdict.split(".")[0],
                                 "backtest_output/E26_results.json")],
        test_period="%s و %s" % (w1, w2),
        n={"value": b["trades"].get("baseline"),
           "unit_fa": "معامله",
           "text_fa": "%s معامله در %s با پیکربندی فعلی؛ بازوی E26: %s و %s معامله"
                      % (num(b["trades"].get("baseline")), w2,
                         num(a["trades"].get("E26")), num(b["trades"].get("E26")))},
        hit_rate=hit,
        vs_tedpix=_vs("سبد با پیکربندی فعلی %s در برابر شاخص %s (%s)؛ %s در برابر %s (%s)"
                      % (vs1 + vs2), cost_used("E26_preregistration.json")),
        sample="in_sample",
        warning=("انتخاب‌های این سبد در %s در صدک %s انتخاب‌های تصادفی بود و سبد %s "
                 "سود داد در حالی که شاخص %s بالا رفت (E26)."
                 % (w2, num(pctile, 1), vs2[0], vs2[1])),
        hide_levels=True,
        doubt=("هر دو دوره پیش از E26 خرج شده بود (پیش‌ثبت E26)؛ این نتیجه می‌تواند "
               "بدتر بودن را نشان دهد ولی برتری را نه."),
        sources=["backtest_output/E26_results.json",
                 "backtest_output/E26_preregistration.json",
                 "backtest_output/backtest_v1_vs_v2.json"],
        extra={"windows": {w: {"basket_pct": x["total_return_pct"]["baseline"],
                               "tedpix_pct": x["benchmarks_pct"]["tedpix"],
                               "risk_free_pct": x["benchmarks_pct"].get("risk_free"),
                               "selection_percentile": x["selection_percentile_vs_random"]
                               ["engine_exits"]["baseline"]}
                           for w, x in windows}},
        headline="سبد %s / شاخص %s" % (vs2[0], vs2[1]))


def _technical_evidence():
    gates = results("E19_gate_decision.json") or {}
    audit = results("E19_edge_audit.json") or {}
    e20 = results("E20_decision.json") or {}
    v1v2 = results("backtest_v1_vs_v2.json") or {}
    if not gates or not v1v2:
        raise ValueError("E19_gate_decision.json or backtest_v1_vs_v2.json is missing")
    observed = {g["gate"]: g.get("observed", "") for g in gates.get("gates") or ()}
    holdout = re.search(r"holdout IC (-?\d+\.\d+)", observed.get("G4_out_of_sample", ""))
    executed = ((audit.get("phase5_execution") or {}).get("executed") or {})
    primary = ((audit.get("phase2_prediction") or {}).get("primary") or {})
    v1 = (v1v2.get("profiles") or {}).get("v1") or {}
    bench = v1v2.get("benchmark") or {}
    windows = _windows_touched()
    e19_window = windows.get("E19")
    exps = [_experiment("E19", None, "backtest_output/E19_gate_decision.json"),
            _experiment("E20", None, "backtest_output/E20_decision.json"),
            _experiment("E26", None, "backtest_output/E26_results.json"),
            {"id": "v1", "full_id": "backtest_v1_vs_v2 (ثبت‌نشده)",
             "decision": "سبد v1 به شاخص باخت",
             "source": "backtest_output/backtest_v1_vs_v2.json"}]
    v1_text = ("سبد v1 که با همین امتیاز تکنیکال رتبه می‌دهد: %s در برابر شاخص %s "
               "(%s، با هزینه‌ی %s)" % (pct(v1.get("total_return_pct"), 2),
                                        pct(bench.get("total_return_pct")),
                                        period(v1v2.get("start"), v1v2.get("end")),
                                        pct(v1v2.get("round_trip_pct"), 3)))
    return {
        "experiments": exps,
        "test_period": "%s (نگه‌داشته‌ی E19)؛ %s (سبد v1)" % (
            period(*e19_window) if e19_window else "?",
            period(v1v2.get("start"), v1v2.get("end"))),
        "n": {"value": v1.get("trades"), "unit_fa": "معامله",
              "text_fa": "%s معامله‌ی سبد v1 در %s جلسه؛ %s دوره‌ی ۲۰ جلسه‌ای در E19"
                         % (num(v1.get("trades")), num(bench.get("sessions")),
                            num(primary.get("dates")))},
        "hit_rate": ({"value": round(v1["win_rate_pct"] / 100, 4),
                      "text_fa": "%s معاملات سبد v1 سودده بود" % pct(v1["win_rate_pct"]),
                      "definition_fa": "سهم معاملات بسته‌شده با سود"}
                     if v1.get("win_rate_pct") is not None else None),
        "vs": _vs(v1_text, v1v2.get("round_trip_pct")),
        "e19_net": executed.get("net_annualised_pct"),
        "e19_holdout_ic": float(holdout.group(1)) if holdout else None,
        "e20_case": e20.get("case"),
        "v1": v1, "bench": bench, "v1v2": v1v2,
        "sources": ["backtest_output/E19_gate_decision.json",
                    "backtest_output/E19_edge_audit.json",
                    "backtest_output/E20_decision.json",
                    "backtest_output/backtest_v1_vs_v2.json", "sirat_research.db"],
    }


TECH_DOUBT = ("امتیاز تکنیکال این کارت خودش فرضیه‌ی ثبت‌شده‌ای نبوده است: E19 و E20 "
              "آلفا کور (رتبه‌ی مومنتوم + %B بولینگر) را آزمودند که نزدیک‌ترین خویشاوند "
              "آن است؛ سبد v1 که با همین امتیاز ساخته می‌شود فقط در یک بک‌تست ثبت‌نشده "
              "سنجیده شد؛ و حد ضرر و هدف از risk_engine و trade_manager می‌آید، همان "
              "سازوکار سبد v2 که E26 بازگرداند.")


def card_technical():
    ev = _technical_evidence()
    return _card(
        "technical", "تحلیل تکنیکال نماد", "analysis",
        ["تحلیل تکنیکال نماد", "سیگنال تکنیکال", "اهداف قیمتی",
         "قیمت، سطوح و حجم", "مقاومت‌ها و حمایت‌ها"], REJECTED,
        reason="نزدیک‌ترین آزمون ثبت‌شده‌ی این نوع سیگنال (E19، E20) رد شد و سبد "
               "v1 که با همین امتیاز ساخته می‌شود به شاخص باخت.",
        experiments=ev["experiments"], test_period=ev["test_period"], n=ev["n"],
        hit_rate=ev["hit_rate"], vs_tedpix=ev["vs"], sample="mixed",
        warning=("نزدیک‌ترین آزمون ثبت‌شده‌ی این نوع سیگنال (E19) پس از هزینه %s در "
                 "سال زیان داد و همبستگی رتبه‌ای‌اش با بازده در دوره‌ی نگه‌داشته منفی شد (%s)؛ حد ضرر "
                 "و هدف این کارت پشتوانه‌ی آزمون ندارد."
                 % (pct(abs(ev["e19_net"]) if ev["e19_net"] is not None else None, 2),
                    ev["e19_holdout_ic"])),
        hide_levels=True, doubt=TECH_DOUBT, sources=ev["sources"],
        extra={"e20_case": ev["e20_case"]},
        headline="v1 %s / شاخص %s" % (pct(ev["v1"].get("total_return_pct"), 2),
                                      pct(ev["bench"].get("total_return_pct"))))


def card_technical_top():
    ev = _technical_evidence()
    return _card(
        "technical_top", "ده سهم برتر تکنیکال", "analysis", ["ده سهم برتر تکنیکال"],
        REJECTED,
        reason="این ده نام با همان امتیازی رتبه گرفته‌اند که سبد v1 را ساخت و به "
               "شاخص باخت؛ نزدیک‌ترین آزمون ثبت‌شده (E19، E20) هم رد شد.",
        experiments=ev["experiments"], test_period=ev["test_period"], n=ev["n"],
        hit_rate=ev["hit_rate"], vs_tedpix=ev["vs"], sample="mixed",
        warning=("این ده نام با همان امتیازی رتبه گرفته‌اند که سبد v1 را ساخت: %s در "
                 "برابر شاخص %s در %s."
                 % (pct(ev["v1"].get("total_return_pct"), 2),
                    pct(ev["bench"].get("total_return_pct")),
                    period(ev["v1v2"].get("start"), ev["v1v2"].get("end")))),
        hide_levels=True, doubt=TECH_DOUBT, sources=ev["sources"],
        headline="v1 %s / شاخص %s" % (pct(ev["v1"].get("total_return_pct"), 2),
                                      pct(ev["bench"].get("total_return_pct"))))


def card_technical_inflow():
    ev = _technical_evidence()
    e22 = results("E22_stage_results.json") or {}
    failed = ((e22.get("stage_3") or {}).get("failed")) or []
    exps = ev["experiments"] + [
        _experiment("E21", None, "backtest_output/E21_stage_results.json"),
        _experiment("E22", None, "backtest_output/E22_stage_results.json")]
    return _card(
        "technical_inflow", "تکنیکال قوی + ورود پول", "analysis",
        ["تکنیکال قوی + ورود پول"], REJECTED,
        reason="هر دو جزء این اشتراک در آزمون رد شده‌اند: امتیاز تکنیکال (E19، E20، "
               "سبد v1) و فیلتر جریان پول در اجرای واقعی (E22).",
        experiments=exps, test_period=ev["test_period"], n=ev["n"],
        hit_rate=ev["hit_rate"], vs_tedpix=ev["vs"], sample="mixed",
        warning=("دو خوانش بی‌لبه با هم لبه نمی‌سازند: امتیاز تکنیکال در سبد v1 به "
                 "شاخص باخت (%s در برابر %s) و فیلتر جریان پول E21 در اجرای واقعی "
                 "(E22) در %s بررسی رد شد."
                 % (pct(ev["v1"].get("total_return_pct"), 2),
                    pct(ev["bench"].get("total_return_pct")), num(len(failed)))),
        hide_levels=True, doubt=TECH_DOUBT, sources=ev["sources"] + [
            "backtest_output/E22_stage_results.json"],
        extra={"e22_failed_checks": failed},
        headline="E22: %s" % (report_decision("E22_REPORT.md") or "REJECT"))


def card_screen():
    ev = _technical_evidence()
    return _card(
        "screen", "غربال دومرحله‌ای", "analysis",
        ["غربال دومرحله‌ای", "قیف غربال", "امتیاز بنیادی فهرست نهایی",
         "گزارش نهایی — غربال بنیادی، سپس رتبه‌بندی تکنیکال",
         "چرا نمادها کنار گذاشته شدند"], REJECTED,
        reason="این غربال همان زنجیره‌ی v1 است (غربال بنیادی، سپس رتبه‌ی تکنیکال) که "
               "در بک‌تست به شاخص باخت.",
        experiments=ev["experiments"], test_period=ev["test_period"], n=ev["n"],
        hit_rate=ev["hit_rate"], vs_tedpix=ev["vs"], sample="in_sample",
        warning=("زنجیره‌ی همین غربال (v1) در %s فقط %s سود داد در حالی که شاخص %s "
                 "بالا رفت." % (period(ev["v1v2"].get("start"), ev["v1v2"].get("end")),
                                pct(ev["v1"].get("total_return_pct"), 2),
                                pct(ev["bench"].get("total_return_pct")))),
        hide_levels=True,
        doubt=("بک‌تست v1 ثبت‌نشده است و وزن‌هایش با داده‌ی آینده‌نگر برازش شده بود؛ "
               "باخت به شاخص را نشان می‌دهد، ولی هیچ آزمون ثبت‌شده‌ای خود این غربال را "
               "داوری نکرده است."),
        sources=ev["sources"],
        headline="v1 %s / شاخص %s" % (pct(ev["v1"].get("total_return_pct"), 2),
                                      pct(ev["bench"].get("total_return_pct"))))


def card_index_technical():
    e27 = results("E27_results.json")
    if not e27:
        raise ValueError("E27_results.json is missing")
    p, sec = e27["windows"]["primary"], e27["windows"]["secondary"]
    exps = [_experiment("E27", "H2 %s" % e27["verdict"]["H2_trend"],
                        "backtest_output/E27_results.json"),
            _experiment("E1"), _experiment("E4")]
    return _card(
        "index_technical", "سیگنال تکنیکال و سطوح شاخص", "analysis",
        ["سیگنال تکنیکال شاخص", "اهداف و سطوح شاخص",
         "سیگنال تکنیکال شاخص هم‌وزن", "اهداف و سطوح شاخص هم‌وزن"], REJECTED,
        reason="زمان‌بندی شاخص با قاعده‌ی روند در E27 (H2) و با موتور وضعیت بازار در "
               "E1 و E4 رد شد.",
        experiments=exps,
        test_period="%s و %s (E27)" % (period(*p["range"]), period(*sec["range"])),
        n={"value": p["H2_trend"].get("switches"), "unit_fa": "جابه‌جایی",
           "text_fa": "%s جابه‌جایی در %s جلسه (E27، دوره‌ی اصلی)"
                      % (num(p["H2_trend"].get("switches")), num(p["sessions"]))},
        hit_rate=None,
        vs_tedpix=_vs("قاعده‌ی روند در برابر نگه‌داشتن شاخص: شارپ %.2f در برابر %.2f "
                      "(دوره‌ی اصلی، p=%.2f) و %.2f در برابر %.2f (دوره‌ی دوم)"
                      % (p["H2_trend"]["sharpe_vs_fund"], p["H1_passive"]["sharpe_vs_fund"],
                         p["H2_test"]["p_one_sided"], sec["H2_trend"]["sharpe_vs_fund"],
                         sec["H1_passive"]["sharpe_vs_fund"]),
                      cost_used("E27_preregistration.json")),
        sample="mixed",
        warning=("زمان‌بندی شاخص با قاعده‌ی روند (میانگین ۲۰۰ جلسه‌ای) در E27 رد شد: "
                 "در دوره‌ی دوم شارپ %.2f در برابر %.2f نگه‌داشتن ساده؛ سیگنال و سطوح "
                 "این کارت را نشانه‌ی ورود یا خروج نگیرید."
                 % (sec["H2_trend"]["sharpe_vs_fund"], sec["H1_passive"]["sharpe_vs_fund"])),
        hide_levels=True,
        doubt=("رأی تکنیکال شاخص خودش ثبت نشده بود؛ E27-H2 (میانگین ۲۰۰ جلسه‌ای) و "
               "E1/E4 (موتور چهارسیگناله) نزدیک‌ترین آزمون‌های زمان‌بندی شاخص‌اند."),
        sources=["backtest_output/E27_results.json", "sirat_research.db ledger"],
        headline="روند: شارپ %.2f / نگه‌داشتن %.2f" % (
            sec["H2_trend"]["sharpe_vs_fund"], sec["H1_passive"]["sharpe_vs_fund"]))


def card_regime():
    led = ledger()
    e4 = (led.get("E4") or {}).get("rationale") or ""
    m = re.search(r"upside (\d+\.\d+)% vs downside (\d+\.\d+)%", e4)
    detail = ("؛ بیرون از نمونه %s از بالا رفتن‌ها و %s از افت‌ها را گرفت"
              % (pct(float(m.group(1))), pct(float(m.group(2))))) if m else ""
    return _card(
        "regime", "دمای بازار (فقط توصیف)", "analysis",
        ["دمای بازار (فقط توصیف)", "مشارکت بازار", "شواهد تصمیم"], DESCRIPTIVE,
        reason="کارت وضعیت بازار را توصیف می‌کند؛ استفاده‌ی آن برای زمان‌بندی در E1 و "
               "E4 رد شد.",
        experiments=[_experiment("E1"), _experiment("E4")],
        test_period=None,
        n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("موتور چهارسیگناله‌ی زمان‌بندی بازار در E1 و E4 رد شد%s؛ این دما را "
                 "نشانه‌ی ورود یا خروج نگیرید." % detail),
        hide_levels=False, sources=["sirat_research.db ledger", "RESEARCH_LEDGER.md"])


def _e21_evidence():
    st = results("E21_stage_results.json") or {}
    if not st:
        raise ValueError("E21_stage_results.json is missing")
    hold = st.get("holdout")
    if not hold:
        raise ValueError("E21_stage_results.json has no holdout block")
    window = _windows_touched().get("E21")
    return st, hold, window


def _e21_forward():
    try:
        import forward_scorecard
        s = forward_scorecard.e21_status()
    except Exception as exc:                                    # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    keys = ("reservation", "sessions_expected", "logged", "timely", "late",
            "missed_recorded", "absent", "thin", "clean", "clean_share",
            "outcome_scoring", "error")
    out = {k: s.get(k) for k in keys}
    out["chain_intact"] = (s.get("chain") or {}).get("intact")
    return out


def card_moneyflow():
    st, hold, window = _e21_evidence()
    lift = hold.get("lift_h1") or {}
    exps = [_experiment("E21", None, "backtest_output/E21_stage_results.json"),
            _experiment("E22", None, "backtest_output/E22_stage_results.json"),
            _experiment("E24", None, "backtest_output/E24_stage_results.json")]
    return _card(
        "moneyflow", "قدرت خریدار و ورود پول حقیقی", "analysis",
        ["قدرت خریدار و ورود پول حقیقی", "بیشترین ورود و خروج پول حقیقی",
         "بالاترین قدرت خریدار", "جدول جریان پول"], DESCRIPTIVE,
        reason="کارت جریان پول را گزارش می‌کند؛ E21 نشان داد این جریان صف فردا را "
               "پیش‌بینی می‌کند، نه بازده را، و E22 و E24 استفاده‌های معاملاتی آن را رد "
               "کردند.",
        experiments=exps,
        test_period=("%s (نگه‌داشته‌ی E21)" % period(*window)) if window else None,
        n={"value": hold.get("dates"), "unit_fa": "جلسه",
           "denominator": hold.get("rows"),
           "text_fa": "%s جلسه و %s نماد-روز در دوره‌ی نگه‌داشته"
                      % (num(hold.get("dates")), num(hold.get("rows")))},
        hit_rate={"value": round(lift.get("top_rate_pct", 0) / 100, 4),
                  "text_fa": "%s دهک بالا فردا در صف یا ناخریدنی بود، در برابر نرخ پایه‌ی %s"
                             % (pct(lift.get("top_rate_pct")),
                                pct(lift.get("universe_rate_pct"))),
                  "definition_fa": "سهم نام‌های دهک بالای امتیاز E21 که جلسه‌ی بعد "
                                   "قفل یا ناخریدنی شدند (هدف E21، نه بازده)"},
        vs_tedpix=_vs(NOT_MEASURED + " — E21 صف‌شدن فردا را سنجید، نه بازده را",
                      None, measured=False),
        sample="out_of_sample",
        warning=("طبق E21، قدرت خریدار حقیقی بالا صف خرید فردا را پیش‌بینی می‌کند (در "
                 "دوره‌ی نگه‌داشته %s در برابر نرخ پایه‌ی %s)، نه بازده را؛ سهمی که "
                 "این‌جا بالاست فردا %.1f برابر نرخ پایه احتمال دارد در صف بماند و "
                 "خریدنی نباشد."
                 % (pct(lift.get("top_rate_pct")), pct(lift.get("universe_rate_pct")),
                    lift.get("lift") or 0.0)),
        hide_levels=False,
        doubt=("یک جزء امتیاز E21 (inst_net_ratio) با علامت اشتباه پیش‌ثبت شده بود و "
               "جانشین آن (E24) رد شد؛ آزمون پیش‌روی E21 در R-FORWARD-2027 در جریان "
               "است و پیش از %s داوری نمی‌شود." % "2027-08-19"),
        sources=["backtest_output/E21_stage_results.json",
                 "backtest_output/E22_stage_results.json",
                 "backtest_output/E24_stage_results.json", "E21_REPORT.md",
                 "E24_REPORT.md"],
        extra={"forward_e21": _e21_forward(),
               "decisions": {"E21": report_decision("E21_REPORT.md"),
                             "E22": report_decision("E22_REPORT.md"),
                             "E24": report_decision("E24_REPORT.md")}},
        headline="صف فردا %s / پایه %s" % (pct(lift.get("top_rate_pct")),
                                          pct(lift.get("universe_rate_pct"))))


def card_market_flow():
    return _card(
        "market_flow", "ورود و خروج پول حقیقی به بازار", "summary",
        ["ورود و خروج پول حقیقی به بازار"], DESCRIPTIVE,
        reason="جمع جریان پول حقیقی و حقوقی کل بازار را گزارش می‌کند.",
        experiments=[_experiment("E21", None, "backtest_output/E21_stage_results.json")],
        test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("جمع ورود پول حقیقی به کل بازار هرگز به‌عنوان پیش‌بینی جهت شاخص "
                 "آزموده نشده است؛ E21 فقط جریان تک‌نماد را برای صف فردا سنجید."),
        hide_levels=False, sources=["backtest_output/E21_stage_results.json"])


def card_live():
    return _card(
        "live", "بازار زنده", "live",
        ["وضعیت جلسه", "صف خرید", "صف فروش", "تغییرات لحظه‌ای",
         "قدرت خریدار حقیقی — درون‌جلسه‌ای"], DESCRIPTIVE,
        reason="تصویر همین لحظه‌ی بازار است.",
        experiments=[_experiment("E21", None, "backtest_output/E21_stage_results.json")],
        test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("E21 جریان پایان روز را سنجید؛ قدرت خریدار درون‌جلسه‌ای و صفِ هنوز "
                 "بسته‌نشده هیچ آزمونی ندارند، و سهمی که در صف خرید است معمولاً امروز "
                 "خریدنی نیست."),
        hide_levels=False, sources=["backtest_output/E21_stage_results.json"])


def card_smart_money():
    e25 = ledger().get("E25") or {}
    return _card(
        "smart_money", "اسکنر ورود پول و خرید درشت حقیقی در کف قیمت", "analysis",
        ["اسکنر ورود پول و خرید درشت حقیقی در کف قیمت"], REJECTED,
        reason="E25 در مرحله‌ی ۱ (یکپارچگی داده) رد شد؛ بازده این پویشگر هرگز "
               "سنجیده نشد.",
        experiments=[_experiment("E25", None, e25.get("source"))],
        test_period=None,
        n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED + " — آزمون بازده اجرا نشد", None, measured=False),
        sample="none",
        warning=("این پویشگر هرگز روی بازده آزموده نشد: E25 در مرحله‌ی یکپارچگی داده "
                 "متوقف شد، چون P/E و تعداد سهمی که امتیازش به آن‌ها تکیه دارد "
                 "تاریخچه‌ی ذخیره‌شده ندارند."),
        hide_levels=True,
        doubt=("«ردشده» این‌جا یعنی رد در مرحله‌ی داده (تصمیم دفتر پژوهش: %s)؛ فرضیه‌ی "
               "بازده نه اثبات شد، نه ابطال." % (e25.get("decision") or "?")),
        sources=["sirat_research.db ledger", "E25_STAGE1_REPORT.md"])


def card_gold():
    e28 = results("E28_results.json")
    if not e28:
        raise ValueError("E28_results.json is missing")
    g = e28["books"]["gold_100"]
    e34 = _e34()
    tail = ("برتری‌اش بر اخزای واقعی در E35 %s است" % IN_TEST
            if e34["state"] != "done" else
            "در E35 برتری ترکیب ۷۰/۳۰ با طلا بر ۷۰/۳۰ با اخزای واقعی %s" % (
                "پذیرفته شد" if e34["h1"] else "از آستانه‌ی آماری نگذشت"))
    return _card(
        "gold", "صندوق‌های طلا", "analysis", ["صندوق‌های طلا"], DESCRIPTIVE,
        reason="کارت قیمت و NAV صندوق‌های طلا را گزارش می‌کند؛ جایگاه طلا در سبد را "
               "E28 آزمود.",
        experiments=[_experiment("E28", "H2 %s" % e28["verdict"]["H2_gold_alone"],
                                 "backtest_output/E28_results.json"),
                     _experiment("E35", e34["state_fa"], "backtest_output/E35_results.json")],
        test_period=period(*e28["range"]),
        n={"value": e28["blocks"], "unit_fa": "بلوک ۲۰ جلسه‌ای",
           "denominator": e28["sessions"],
           "text_fa": "%s بلوک از %s جلسه" % (num(e28["blocks"]), num(e28["sessions"]))},
        hit_rate=None,
        vs_tedpix=_vs("طلا %s در سال، شاخص (با هزینه‌ی نگه‌داری) %s، صندوق فرضی %s (E28)"
                      % (pct(g["cagr_pct"]), pct(e28["books"]["equity_100"]["cagr_pct"]),
                         pct(e28["fund_20pct"]["cagr_pct"], 0)),
                      cost_used("E28_preregistration.json")),
        sample="in_sample",
        warning=("بازده ریالی طلا (%s در سال در E28) بیشتر کاهش پله‌ای ارزش ریال است و "
                 "افت %s هم داشته؛ %s." % (pct(g["cagr_pct"]),
                                           pct(abs(g["max_drawdown_pct"])), tail)),
        hide_levels=False, sources=["backtest_output/E28_results.json",
                                    "backtest_output/E35_results.json"],
        extra={"retest": {"id": "E35", "state": e34["state"]}})


def card_valuation():
    e33 = results("E33_results.json")
    if not e33:
        raise ValueError("E33_results.json is missing")
    pr, bm = e33["primary"], e33["benchmarks"]
    return _card(
        "valuation", "بازار ارزان است یا گران؟", "summary",
        ["بازار ارزان است یا گران؟", "هر صنعت در برابر تاریخچه‌ی خودش"], DESCRIPTIVE,
        reason="P/E بازار و صنایع را در برابر تاریخچه‌ی خودشان گزارش می‌کند؛ خرید "
               "صنایع «ارزان» در E33 رد شد.",
        experiments=[_experiment("E33", e33.get("decision"),
                                 "backtest_output/E33_results.json")],
        test_period=period(*e33["window"]),
        n={"value": pr.get("periods"), "unit_fa": "دوره",
           "text_fa": "%s دوره‌ی تصمیم (%s فعال)" % (num(pr.get("periods")),
                                                     num(pr.get("active_periods")))},
        hit_rate=({"value": pr["beat_control_share_of_active_periods"],
                   "text_fa": "%s دوره‌های فعال از شرکت‌های واجد شرایط جلو افتاد"
                              % pct(pr["beat_control_share_of_active_periods"] * 100, 0),
                   "definition_fa": "سهم دوره‌هایی که سبد از میانگین هم‌وزن همه‌ی "
                                    "شرکت‌های واجد شرایط بهتر بود"}
                  if pr.get("beat_control_share_of_active_periods") is not None else None),
        vs_tedpix=_vs("خرید بهترین شرکتِ صنایع ارزان %s در سال در برابر شاخص %s (E33)"
                      % (pct(pr["strategy_annualised_net_pct"]),
                         pct(bm["tedpix_annualised_pct"])),
                      cost_used("E33_preregistration.json")),
        sample="in_sample",
        warning=("خرید بهترین شرکتِ صنایعی که این کارت «ارزان‌تر از معمول» می‌نامد، در "
                 "E33 به شاخص باخت: %s در برابر %s در سال."
                 % (pct(pr["strategy_annualised_net_pct"]), pct(bm["tedpix_annualised_pct"]))),
        hide_levels=False,
        doubt="پنجره‌ی E30 تا E33 چهار بار مصرف شده است؛ هیچ‌کدام بیرون از نمونه نیست.",
        sources=["backtest_output/E33_results.json"])


def card_fund_discount():
    return _card(
        "fund_discount", "ارزان‌ترین راه پر کردن هر بخش — تخفیف صندوق", "summary",
        ["ارزان‌ترین راه پر کردن هر بخش"], DESCRIPTIVE,
        reason="قیمت صندوق را در برابر NAV ابطال امروز گزارش می‌کند.",
        experiments=[], test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("تخفیف قیمت صندوق به NAV واقعیت امروز است، نه پیش‌بینی؛ هیچ آزمونی "
                 "نشان نداده که خرید صندوقِ پرتخفیف‌تر بازده بیشتری دارد."),
        hide_levels=False, sources=[])


def card_bills():
    bills = bill_yield()
    return _card(
        "bills", "ارزان‌ترین راه پر کردن هر بخش — اخزا", "summary",
        ["ارزان‌ترین راه پر کردن هر بخش"], DESCRIPTIVE,
        reason="بازده تا سررسید اوراق خزانه را از قیمت امروز حساب می‌کند.",
        experiments=[], test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("بازده اخزا%s فقط برای کسی قطعی است که تا سررسید نگه دارد؛ فروش پیش "
                 "از سررسید به قیمت همان روز است."
                 % ((" (%s در %s)" % (pct(bills["yield_pct"]), bills["as_of"]))
                    if bills else "")),
        hide_levels=False, sources=["data/bill_yield.json"],
        extra={"bill_yield": bills})


def card_news():
    return _card(
        "news", "هوش بازار — تحلیل خبری", "analysis", ["هوش بازار — تحلیل خبری"],
        DESCRIPTIVE,
        reason="خبرها را دسته‌بندی و گزارش می‌کند؛ قاعده‌ی پروژه: خبر سیگنال نیست.",
        experiments=[], test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("خبرها فقط گزارش می‌شوند: هیچ پیش‌بینی قیمتی از آن‌ها ساخته یا آزموده "
                 "نشده و جهت هر خبر برچسب یک دسته‌بند ماشینی است."),
        hide_levels=False, sources=["event_intelligence/"])


def card_holdings():
    return _card(
        "holdings", "دیده‌بان سبد من", "summary",
        ["دیده‌بان سبد من", "ریسک این سبد، با وزن‌های امروز"], DESCRIPTIVE,
        reason="سبد خود شما را با قیمت‌ها و نوسان گذشته گزارش می‌کند.",
        experiments=[], test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("ریسک این کارت از نوسان گذشته‌ی همین سهم‌ها حساب شده، نه پیش‌بینی "
                 "زیان آینده؛ در صف فروش، زیان واقعی از آن بیشتر است."),
        hide_levels=False, sources=[])


def card_announcements():
    return _card(
        "announcements", "اطلاعیه‌های مهم کدال", "summary", ["اطلاعیه‌های مهم کدال"],
        DESCRIPTIVE,
        reason="اطلاعیه‌های کدال را همان‌طور که منتشر شده‌اند فهرست می‌کند.",
        experiments=[], test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs(NOT_MEASURED, None, measured=False), sample="none",
        warning=("اثر هیچ نوع اطلاعیه‌ای (افزایش سرمایه، مجمع، سود) بر قیمت در این "
                 "ربات آزموده نشده است."),
        hide_levels=False, sources=[])


def card_fundamentals():
    e32 = results("E32_results.json")
    if not e32:
        raise ValueError("E32_results.json is missing")
    pr = e32["primary"]["strategy"]
    return _card(
        "fundamentals", "صورت‌های مالی و امتیاز بنیادی", "analysis",
        ["صورت‌های مالی و امتیاز بنیادی", "قوی‌ترین بنیادها", "جدول بنیادی",
         "توزیع امتیاز بنیادی در هر صنعت", "میانه‌ی امتیاز بنیادی",
         "نرخ تأیید در هر صنعت", "جدول بنیادی صنایع",
         "امتیاز تکنیکال و عمق هر صنعت"], DESCRIPTIVE,
        reason="صورت‌های مالی و نسبت‌ها را گزارش می‌کند؛ امتیاز آن هرگز به‌عنوان "
               "پیش‌بینی بازده آزموده نشده است.",
        experiments=[_experiment("E32", e32.get("decision"),
                                 "backtest_output/E32_results.json")],
        test_period=None, n=None, hit_rate=None,
        vs_tedpix=_vs("نزدیک‌ترین آزمون (E32، کیفیت بنیادی آینده‌نگر): %s در برابر "
                      "شاخص %s در سال" % (pct(pr["annualised_net_pct"]),
                                          pct(e32["tedpix_annualised_pct"])),
                      cost_used("E32_preregistration.json")),
        sample="none",
        warning=("این امتیاز بنیادی هرگز به‌عنوان پیش‌بینی بازده آزموده نشده؛ آزمون "
                 "نزدیک به آن (E32) به شاخص باخت: %s در برابر %s در سال."
                 % (pct(pr["annualised_net_pct"]), pct(e32["tedpix_annualised_pct"]))),
        hide_levels=False,
        doubt=("E32 امتیاز کیفیت آینده‌نگر چشم‌انداز را آزمود، نه همین امتیاز "
               "fundamental_engine را؛ نشان «توصیفی» یعنی آزموده‌نشده، نه بی‌خطر."),
        sources=["backtest_output/E32_results.json"])


def _calibration_summary():
    cal = _cached(CALIBRATION, _load_json)
    if not cal:
        return None
    heads = {k: b for k, b in (cal.get("buckets") or {}).items() if "|" not in k}
    errors = [(b.get("median_abs_error"), k, b.get("n")) for k, b in heads.items()
              if b.get("median_abs_error") is not None]
    coverage = {k: v for k, v in cal.items() if "coverage" in k.lower()}
    for k, b in heads.items():
        for key, value in b.items():
            if "coverage" in key.lower():
                coverage.setdefault("buckets", {}).setdefault(k, {})[key] = value
    # Since 2026-11-24 outlook_calibration measures the band point-in-time
    # and leave-one-fiscal-year-out ("loyo"): pooled coverage, and how many
    # held-out years fell outside their own interval around the nominal.
    loyo = cal.get("loyo") or {}
    held_out = None
    if loyo.get("overall"):
        family = "statement" if "statement" in loyo["overall"] else \
            sorted(loyo["overall"])[0]
        pooled = (loyo["overall"].get(family) or {}).get("q25_q75") or {}
        spread = ((loyo.get("year_spread") or {}).get(family) or {}).get("q25_q75") or {}
        held_out = {"family": family, "rate": pooled.get("rate"),
                    "low95": pooled.get("low95"), "high95": pooled.get("high95"),
                    "nominal": pooled.get("nominal"), "total": pooled.get("total"),
                    "years": spread.get("years"), "years_outside": spread.get("outside"),
                    "worst": spread.get("worst")}
    return {"built_at": cal.get("built_at"), "points": cal.get("points"),
            "source": (cal.get("band") or {}).get("source") or "restated",
            "years": (cal.get("band") or {}).get("years"),
            "median_abs_error_by_months": {k: b.get("median_abs_error")
                                           for k, b in sorted(heads.items())},
            "n_by_months": {k: b.get("n") for k, b in sorted(heads.items())},
            "error_range": (min(errors), max(errors)) if errors else None,
            "held_out_coverage": held_out,
            "coverage_fields": coverage or None}


MONTHS_FA = {"12": "سال آغازنشده", "3": "۳ ماه معلوم", "6": "۶ ماه معلوم",
             "9": "۹ ماه معلوم", "M3": "فروش ماهانه، ۳ ماه", "M6": "فروش ماهانه، ۶ ماه",
             "M9": "فروش ماهانه، ۹ ماه"}


def card_outlook():
    e30 = results("E30_results.json")
    if not e30:
        raise ValueError("E30_results.json is missing")
    st = e30["primary"]["strategy"]
    bm = e30["benchmarks"]
    cal = _calibration_summary()
    try:
        import forward_scorecard
        forward = forward_scorecard.status()
    except Exception as exc:                                    # noqa: BLE001
        forward = {"state": "unknown", "error": "%s: %s" % (type(exc).__name__, exc)}
    state = forward.get("state")
    if state == "active":
        badge, reason = ACCEPTED, ("محدوده‌ی برآورد در آزمون پیش‌رو بر %s سال-شرکت "
                                   "حل‌شده رد نشد." % forward.get("resolved_in_window"))
    elif state == "retired":
        badge, reason = REJECTED, ("محدوده‌ی برآورد در آزمون پیش‌رو رد شد و فقط برای "
                                   "گزارش است: %s" % "؛ ".join(
                                       forward.get("retire_reasons") or ()))
    else:
        badge, reason = PROVISIONAL, ("محدوده‌ی برآورد فقط روی تاریخچه‌ای سنجیده شده "
                                      "که برآوردگر با آن ساخته شد، و آزمون پیش‌رو در "
                                      "حال گردآوری است.")
    err_text = ""
    if cal and cal["error_range"]:
        (lo, lo_k, _), (hi, hi_k, _) = cal["error_range"]
        err_text = "میانه‌ی خطای سود %s (%s) تا %s (%s)" % (
            pct(lo * 100, 0), MONTHS_FA.get(lo_k, lo_k),
            pct(hi * 100, 0), MONTHS_FA.get(hi_k, hi_k))
    held = (cal or {}).get("held_out_coverage")
    cover_text = ""
    if held and held.get("rate") is not None:
        cover_text = ("در سال‌های کنارگذاشته محدوده‌ی ۵۰٪ روی هم %s را پوشاند" %
                      pct(held["rate"] * 100, 0))
        if held.get("years"):
            worst = held.get("worst") or {}
            cover_text += ("، اما سال‌به‌سال در %s از %s سال از بازه‌ی خودش بیرون افتاد"
                           % (num(held.get("years_outside") or 0), num(held["years"])))
            if worst.get("rate") is not None:
                cover_text += " (بدترین: %s با %s)" % (worst.get("year"),
                                                      pct(worst["rate"] * 100, 0))
    calibration_text = "؛ ".join(t for t in (err_text, cover_text) if t)
    n = {"value": (cal or {}).get("points"), "unit_fa": "نقطه‌ی برآورد گذشته",
         "text_fa": "%s نقطه‌ی برآورد گذشته (شرکت-سال × ماه معلوم)؛ آزمون پیش‌رو: %s از "
                    "%s سال-شرکت لازم حل شده"
                    % (num((cal or {}).get("points")),
                       num(forward.get("resolved_in_window") or 0),
                       num(forward.get("min_n")))}
    coverage = forward.get("coverage_inner")
    hit = ({"value": coverage["rate"],
            "text_fa": "%s سود واقعی در محدوده‌ی ۵۰٪ افتاد (۹۵٪: %s)"
                       % (pct(coverage["rate"] * 100, 0), coverage["ci95"]),
            "definition_fa": "پوشش تجربی محدوده‌ی q25..q75 در آزمون پیش‌رو"}
           if coverage else None)
    return _card(
        "outlook", "چشم‌انداز بنیادی — برآورد سال مالی جاری", "analysis",
        ["چشم‌انداز بنیادی — برآورد سال مالی جاری"], badge,
        reason=reason,
        experiments=[_experiment("E30", e30.get("decision"),
                                 "backtest_output/E30_results.json"),
                     {"id": "OUTLOOK-FORWARD-v1", "full_id": "OUTLOOK-FORWARD-v1",
                      "decision": state,
                      "source": "backtest_output/OUTLOOK_FORWARD_PROTOCOL.json"}],
        test_period="%s (E30)؛ آزمون پیش‌رو از %s" % (
            period(*e30["window"]), forward.get("first_date") or "—"),
        n=n, hit_rate=hit,
        vs_tedpix=_vs("ده نام اول با بازده سود آینده‌نگر: %s در سال در برابر شاخص %s "
                      "(E30)" % (pct(st["annualised_net_pct"]),
                                 pct(bm["tedpix_annualised_pct"])),
                      cost_used("E30_preregistration.json")),
        sample="mixed",
        warning=("فهرستی که با بازده سود آینده‌نگر مرتب شده، در E30 به شاخص باخت (%s در "
                 "برابر %s در سال، %s)؛ عدد برآورد را بخوانید، ترتیب فهرست را نخرید."
                 % (pct(st["annualised_net_pct"]), pct(bm["tedpix_annualised_pct"]),
                    period(*e30["window"]))),
        hide_levels=False,
        doubt=(("محدوده‌ی برآورد با کنار گذاشتن هر سال مالی سنجیده شده (%s)، ولی همه‌ی "
                "آن سال‌ها پیش از ساختن برآوردگر دیده شده بودند؛ آزمونی که برآوردگر "
                "نتوانسته باشد با آن تنظیم شود، فقط آزمون پیش‌رو است. " % cover_text
                if cover_text else
                "محدوده‌ی برآورد روی همان تاریخچه‌ای سنجیده شده که برآوردگر با آن "
                "ساخته شد و بیرون از نمونه هنوز اندازه‌گیری نشده. ")
               + "محدوده‌ی ارزش منصفانه هم آزموده نشده است؛ نزدیک‌ترین آزمون (E30) رد "
                 "شد — اگر سرپرست بخواهد، آن را هم پشت دکمه ببرد."),
        sources=["backtest_output/E30_results.json", "data/outlook_calibration.json",
                 "backtest_output/OUTLOOK_FORWARD_PROTOCOL.json",
                 "backtest_output/outlook_forward_log.jsonl"],
        extra={"calibration": cal, "calibration_text_fa": calibration_text,
               "forward": forward,
               "range_report_only": bool(forward.get("report_only"))},
        headline=err_text or None)


REGISTRY = (
    ("today", card_today),
    ("allocation", card_allocation),
    ("holdings", card_holdings),
    ("market_flow", card_market_flow),
    ("valuation", card_valuation),
    ("fund_discount", card_fund_discount),
    ("bills", card_bills),
    ("announcements", card_announcements),
    ("basket", card_basket),
    ("live", card_live),
    ("regime", card_regime),
    ("technical", card_technical),
    ("technical_top", card_technical_top),
    ("technical_inflow", card_technical_inflow),
    ("smart_money", card_smart_money),
    ("gold", card_gold),
    ("news", card_news),
    ("index_technical", card_index_technical),
    ("fundamentals", card_fundamentals),
    ("outlook", card_outlook),
    ("moneyflow", card_moneyflow),
    ("screen", card_screen),
)


def _failed(cid, exc):
    return {"id": cid, "title_fa": None, "badge": None, "badge_key": None,
            "track_line_fa": "سابقه‌ی آزمون خوانده نشد", "warning_fa": None,
            "hide_levels": False, "error": "%s: %s" % (type(exc).__name__,
                                                        str(exc)[:200])}


CARDS_TTL = 120.0
_CARDS = {"at": None, "value": None}
_CARDS_LOCK = threading.Lock()


def cards(use_cache=True):
    """Every card's record, in panel order. Never raises: a card whose
    evidence cannot be read carries `error` and no badge."""

    now = time.monotonic()
    with _CARDS_LOCK:
        if use_cache and _CARDS["at"] is not None and now - _CARDS["at"] < CARDS_TTL:
            return _CARDS["value"]
    out = []
    for cid, build in REGISTRY:
        try:
            out.append(build())
        except Exception as exc:                                # noqa: BLE001
            out.append(_failed(cid, exc))
    with _CARDS_LOCK:
        _CARDS.update(at=now, value=out)
    return out


def card(card_id, use_cache=True):
    for entry in cards(use_cache=use_cache):
        if entry["id"] == card_id:
            return entry
    return None


def registry(use_cache=True):
    """{badges, cards, by_id}: the shape the panel's route returns."""

    items = cards(use_cache=use_cache)
    return {"badges": BADGES, "live_round_trip_pct": live_cost(),
            "not_measured_fa": NOT_MEASURED,
            "cards": items, "by_id": {c["id"]: c for c in items}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    data = registry(use_cache=False)
    if args.json:
        print(json.dumps({k: v for k, v in data.items() if k != "by_id"},
                         ensure_ascii=False, indent=1, default=str))
        return 0
    for c in data["cards"]:
        flag = " [levels hidden]" if c.get("hide_levels") else ""
        if c.get("error"):
            print("%-16s ERROR %s" % (c["id"], c["error"]))
            continue
        print("%-16s %s%s" % (c["id"], c["track_line_fa"], flag))
        print("%-16s   %s" % ("", c["warning_fa"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(None))
