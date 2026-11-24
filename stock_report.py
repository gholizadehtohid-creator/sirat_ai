"""One company's forward fundamental analysis as a PDF report, on demand.

    python stock_report.py فولاد               reports/<date>/فولاد.pdf
    python stock_report.py فولاد --html        the HTML beside it, for a look
    python stock_report.py فولاد --out D:/x    another folder

WHY THIS EXISTS
---------------
The user asked on 2026-11-23 for "a separate analysis and a PDF report for
each share", three to four pages, built whenever they ask (not nightly). The
panel's outlook card shows one company at a time on screen; this puts the
same analysis -- fundamental_outlook.analyse(), nothing computed differently
-- on paper, with the history behind it: three years of statements, the
discrete quarters, the monthly reports, the price against the index.

  page 1  the summary: price, multiples, the estimate and its measured
          range, a fair-value range, what the price asks against bills, the
          warnings and the strengths
  page 2  how the year was estimated: the statements, the method, the range
          of past estimates like it, the dollar what-ifs, a capital raise
  page 3  the trend: quarter by quarter, month by month, product by product
  page 4  quality and the market: cash, debt, dividends, NAV; the price
          against the index; method notes

HOW
---
The page is HTML (templates/report.html, jinja2) with its charts drawn here
as SVG, printed to PDF by the Edge already on this machine (--print-to-pdf,
headless). No new dependency: Persian shaping and right-to-left layout are
the browser's, the font is the panel's own bundled Vazirmatn FD, tables are
in B Nazanin as in the panel. A report takes about three seconds.

The report says on its first page what it is not: the rankings built on
these numbers did not beat the index (E30-E33).
"""

import argparse
import glob
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import config
import database
import fundamental_db
import fundamental_outlook as fo
import jalali

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(ROOT, "reports")
TEMPLATES = os.path.join(ROOT, "templates")
FONT_PATH = os.path.join(ROOT, "static", "fonts", "VazirmatnFD-wght.woff")
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
EDGE_TIMEOUT = 90
PRICE_SESSIONS = 260            # about a year of sessions on the chart
REPORT_KEEP_DAYS = 30           # day folders under reports/ older than this go
STATEMENT_YEARS = 3

# The panel's validated data colours (static/css/panel.css, light theme) and
# its text tokens: a chart's text wears ink, never the series colour.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
INK, INK_2, INK_3 = "#0f1729", "#475467", "#667085"
GRID = "#e4e7ec"
UP, DOWN = "#067647", "#c0292b"


class ReportError(Exception):
    """A report that cannot be built, with a reason fit to show the user."""


# ---- numbers in the report's one format ------------------------------------
# Digits are written Latin: the bundled font draws them as Persian, and
# B Nazanin does too. Separators are the Persian ones.

def num(value, digits=0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(value):
        return "—"
    text = format(value, ",.%df" % digits)
    return text.replace(",", "٬").replace(".", "٫")


def signed(value, digits=0):
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + num(value, digits)


def pct(value, digits=1, sign=False):
    if value is None:
        return "—"
    return (signed(value, digits) if sign else num(value, digits)) + "٪"


def bn(rials, digits=0):
    """Rials as billions, the statements' unit here."""

    if rials is None:
        return "—"
    return num(rials / 1e9, digits)


def money(rials):
    if rials is None:
        return "—"
    r = abs(rials)
    if r >= 1e12:
        return num(rials / 1e12, 1) + " هزار میلیارد ریال"
    if r >= 1e9:
        return num(rials / 1e9, 1) + " میلیارد ریال"
    if r >= 1e6:
        return num(rials / 1e6, 0) + " میلیون ریال"
    return num(rials, 0) + " ریال"


def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _esc(text):
    from markupsafe import escape
    return str(escape(str(text)))


# ---- charts, as SVG ----------------------------------------------------------

def _scale(lo, hi, a, b):
    span = (hi - lo) or 1.0
    return lambda v: a + (v - lo) * (b - a) / span


def _nice_ticks(lo, hi, count=4):
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw),
               default=10 * mag)
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.001:
        ticks.append(v)
        v += step
    return ticks


def bar_chart(values, labels, width=520, height=170, color=SERIES[0],
              fmt=lambda v: num(v, 0), unit=""):
    """Vertical bars, oldest left; zero baseline; the last bar labelled."""

    pts = [(lab, v) for lab, v in zip(labels, values) if v is not None]
    if len(pts) < 2:
        return ""
    vals = [v for _l, v in pts]
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    ticks = _nice_ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    left, right, top, bottom = 46, 8, 18, 34
    y = _scale(lo, hi, height - bottom, top)
    n = len(pts)
    slot = (width - left - right) / n
    bar = max(min(slot - 4, 34), 4)
    out = ['<svg class="chart" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid meet" '
           'xmlns="http://www.w3.org/2000/svg" direction="ltr">' % (width, height)]
    for t in ticks:
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>'
                   % (left, width - right, y(t), y(t), GRID if t else INK_3))
        out.append('<text x="%d" y="%.1f" font-size="9" fill="%s" text-anchor="end">%s</text>'
                   % (left - 5, y(t) + 3, INK_3, _esc(fmt(t))))
    for i, (lab, v) in enumerate(pts):
        cx = left + slot * (i + 0.5)
        y0, y1 = y(0), y(v)
        top_y, h = min(y0, y1), abs(y1 - y0)
        r = min(3.0, h / 2, bar / 2)
        fill = color if v >= 0 else DOWN
        if v >= 0:      # rounded at the data end, square on the baseline
            out.append('<path d="M%.1f %.1f v%.1f q0 -%.1f %.1f -%.1f h%.1f q%.1f 0 %.1f %.1f v%.1f z" fill="%s"/>'
                       % (cx - bar / 2, y0, -(h - r), r, r, r, bar - 2 * r, r, r, r, h - r, fill))
        else:
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                       % (cx - bar / 2, top_y, bar, h, fill))
        if (n - 1 - i) % (1 if slot >= 30 else 2) == 0:     # the newest always
            out.append('<text x="%.1f" y="%d" font-size="8.5" fill="%s" text-anchor="middle">%s</text>'
                       % (cx, height - bottom + 13, INK_2, _esc(lab)))
    last_lab, last_v = pts[-1]
    out.append('<text x="%.1f" y="%.1f" font-size="9.5" font-weight="700" fill="%s" text-anchor="middle">%s%s</text>'
               % (left + slot * (n - 0.5), min(y(last_v), y(0)) - 4, INK, _esc(fmt(last_v)), unit))
    out.append("</svg>")
    return "".join(out)


def line_chart(series, width=520, height=190, fmt=lambda v: num(v, 0)):
    """Lines over one shared x (dates, oldest left), with a legend and end
    labels. `series`: [(name, color, [(label, value)], width)]."""

    series = [s for s in series if len([p for p in s[2] if p[1] is not None]) >= 2]
    if not series:
        return ""
    vals = [v for s in series for _l, v in s[2] if v is not None]
    lo, hi = min(vals), max(vals)
    ticks = _nice_ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    left, right, top, bottom = 40, 70, 22, 26
    n = max(len(s[2]) for s in series)
    x = _scale(0, max(n - 1, 1), left, width - right)
    y = _scale(lo, hi, height - bottom, top)
    out = ['<svg class="chart" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid meet" '
           'xmlns="http://www.w3.org/2000/svg" direction="ltr">' % (width, height)]
    for t in ticks:
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>'
                   % (left, width - right, y(t), y(t), GRID))
        out.append('<text x="%d" y="%.1f" font-size="9" fill="%s" text-anchor="end">%s</text>'
                   % (left - 5, y(t) + 3, INK_3, _esc(fmt(t))))
    labels = series[0][2]
    for i in (0, len(labels) // 2, len(labels) - 1):
        out.append('<text x="%.1f" y="%d" font-size="8.5" fill="%s" text-anchor="middle">%s</text>'
                   % (x(i), height - 8, INK_2, _esc(labels[i][0])))
    legend_x = left
    end_labels = []
    for name, color, pts, stroke in series:
        # A day without a value (a halted share) breaks the line rather than
        # bridging it: the gap is the halt.
        parts, pen = [], "M"
        for j, (_l, v) in enumerate(pts):
            if v is None:
                pen = "M"
                continue
            parts.append("%s%.1f %.1f" % (pen, x(j), y(v)))
            pen = "L"
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="%s" '
                   'stroke-linejoin="round" stroke-linecap="round"/>'
                   % (" ".join(parts), color, stroke))
        last = [(j, v) for j, (_l, v) in enumerate(pts) if v is not None][-1]
        end_labels.append([y(last[1]) + 3, x(last[0]) + 5, last[1]])
        out.append('<rect x="%d" y="6" width="14" height="3" rx="1.5" fill="%s"/>' % (legend_x, color))
        out.append('<text x="%d" y="11" font-size="9.5" fill="%s">%s</text>'
                   % (legend_x + 18, INK_2, _esc(name)))
        legend_x += 26 + 7 * len(name)
    # End labels kept apart: two lines ending close would print one number
    # over the other.
    end_labels.sort()
    for i in range(1, len(end_labels)):
        if end_labels[i][0] - end_labels[i - 1][0] < 11:
            end_labels[i][0] = end_labels[i - 1][0] + 11
    for ly, lx, value in end_labels:
        out.append('<text x="%.1f" y="%.1f" font-size="9.5" font-weight="600" fill="%s">%s</text>'
                   % (lx, ly, INK, _esc(fmt(value))))
    out.append("</svg>")
    return "".join(out)


def range_chart(price, low, central, high, marks=(), width=520, height=86):
    """A value range as a band, its central value as a tick, the price as a
    dot. `marks`: [(label, value)] shown as small ticks below the axis."""

    vals = [v for v in (price, low, central, high) if v] + [v for _l, v in marks if v]
    if not price or not vals or not central:
        return ""
    lo, hi = min(vals) * 0.9, max(vals) * 1.05
    left, right = 16, 16
    x = _scale(lo, hi, left, width - right)
    yb = 44
    out = ['<svg class="chart" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid meet" '
           'xmlns="http://www.w3.org/2000/svg" direction="ltr">' % (width, height)]
    out.append('<line x1="%d" x2="%d" y1="%d" y2="%d" stroke="%s"/>' % (left, width - right, yb, yb, GRID))
    if low and high:
        out.append('<rect x="%.1f" y="%d" width="%.1f" height="14" rx="7" fill="%s" fill-opacity="0.22"/>'
                   % (x(low), yb - 7, max(x(high) - x(low), 2), SERIES[0]))
        for v in (low, high):
            out.append('<text x="%.1f" y="%d" font-size="9" fill="%s" text-anchor="middle">%s</text>'
                       % (x(v), yb + 22, INK_3, _esc(num(v))))
    out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s" stroke-width="3" stroke-linecap="round"/>'
               % (x(central), x(central), yb - 11, yb + 11, SERIES[0]))
    out.append('<text x="%.1f" y="%d" font-size="10" font-weight="700" fill="%s" text-anchor="middle">مرکز %s</text>'
               % (x(central), yb - 15, INK, _esc(num(central))))
    out.append('<circle cx="%.1f" cy="%d" r="6" fill="%s" stroke="#fff" stroke-width="2"/>'
               % (x(price), yb, INK))
    out.append('<text x="%.1f" y="%d" font-size="10" font-weight="700" fill="%s" text-anchor="middle">قیمت %s</text>'
               % (x(price), yb + 36, INK, _esc(num(price))))
    for label, v in marks:
        if v:
            out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>'
                       % (x(v), x(v), yb + 6, yb + 10, INK_3))
    out.append("</svg>")
    return "".join(out)


# ---- what goes into the report -----------------------------------------------

def find_instrument(symbol):
    import persian

    wanted = persian.normalize((symbol or "").strip())
    if not wanted:
        raise ReportError("نماد خالی است")
    for row in database.load_instruments(equities_only=True):
        if persian.normalize(row.get("symbol_fa") or "") == wanted:
            return row
    raise ReportError("نماد پیدا نشد: %s" % symbol)


def statement_table(code, result):
    """Three fiscal years, the trailing twelve months and the year's
    estimate, on the basis the analysis chose."""

    # The same rows analyse() reads (its default 12): choose_basis() can
    # answer differently on a longer window, and the table must show the
    # statements the estimate was built on (شتران read 1401-1402 on 16).
    rows = fundamental_db.load_metrics(code, limit=12)
    periods = [p for p in fo._periods(rows) if fo._has_income(p["items"])]
    if not periods:
        return []
    ps, _basis = fo.choose_basis(periods)
    annual = [p for p in ps if p["months"] == 12][:STATEMENT_YEARS]
    annual.reverse()
    shares = (result.get("valuation") or {}).get("shares")
    # Every release of these years (pit_fundamentals): where the profit the
    # table shows -- the latest release -- differs materially from the one
    # the company first published, the first is shown beside it (2026-11-24).
    versions = first_releases(code)
    cols = []
    for p in annual:
        it = p["items"]
        owners = fo._profit(it)
        col = {"title": "سال %s" % p["end"][:7], "sub": "حسابرسی‌شده" if p.get("audited") else "",
               "revenue": _f(it.get("revenue")), "gross": _f(it.get("gross_profit")),
               "operating": _f(it.get("operating_profit")),
               "net": _f(it.get("net_profit")), "owners": owners,
               "eps": owners / shares if owners is not None and shares else None,
               "margin": (_f(it.get("net_profit")) / _f(it.get("revenue")) * 100
                          if _f(it.get("net_profit")) is not None and _f(it.get("revenue"))
                          else None)}
        revised = revision(code, p, versions)
        if revised:
            col.update(first_owners=revised["first_net_profit"],
                       first_published=str(revised["first_published_at"] or "")[:10],
                       revision_pct=revised["change"] * 100)
        cols.append(col)
    ttm, fwd = result.get("ttm") or {}, result.get("forward") or {}
    if ttm.get("revenue"):
        cols.append({"title": "۱۲ ماه گذشته", "sub": "تا %s" % result["latest"]["period_end"],
                     "revenue": ttm.get("revenue"), "gross": None, "operating": None,
                     "net": None, "owners": ttm.get("profit"), "eps": ttm.get("eps"),
                     "margin": (ttm["profit"] / ttm["revenue"] * 100
                                if ttm.get("profit") is not None and ttm.get("revenue") else None)})
    if fwd.get("revenue"):
        cols.append({"title": "برآورد سال جاری", "sub": "برآورد", "estimate": True,
                     "revenue": fwd.get("revenue"), "gross": None, "operating": None,
                     "net": None, "owners": fwd.get("profit"), "eps": fwd.get("eps"),
                     "margin": (fwd["profit"] / fwd["revenue"] * 100
                                if fwd.get("profit") is not None and fwd.get("revenue") else None)})
    return cols


def first_releases(code):
    """The company's stored releases (pit_fundamentals), or [] when the
    point-in-time store cannot be read -- the table stands without them."""

    try:
        import pit_fundamentals
        return pit_fundamentals.load_versions(code).get(str(code), [])
    except Exception:                                           # noqa: BLE001
        return []


def revision(code, period, versions):
    """pit_fundamentals.revision_of() for one of the table's periods: the
    first release's net profit where the latest differs from it by more
    than MATERIAL_REVISION, else None."""

    if not versions:
        return None
    import pit_fundamentals
    return pit_fundamentals.revision_of(
        code, period["end"], period["months"], period.get("consolidated"),
        period["items"], versions=versions)


DIVIDEND_ROWS = 5


def dividend_rows(code, limit=DIVIDEND_ROWS):
    """The last few dividends the exchange adjusted for, newest first:
    [{date (Jalali), dps, yield_pct}]. Empty when the lists were never
    fetched; never raises -- the rest of the report stands."""

    try:
        import corporate_actions
        rows = corporate_actions.dividend_history(code, codal=False)
    except Exception:                                           # noqa: BLE001
        return []
    out = []
    for row in rows[:limit]:
        if not row.get("dps"):
            continue
        out.append({"date": row.get("date_jalali") or row.get("date"),
                    "dps": row["dps"], "yield_pct": row.get("yield_pct")})
    return out


def price_series(code):
    """[(jalali label, adjusted close)] and TEDPIX's, both rebased to 100."""

    from backtest import back_adjust

    bars = database.load_history_by_inscode(code, limit=PRICE_SESSIONS)
    if bars is None or len(bars) < 20:
        return None
    # The exchange's own adjustment factors first (corporate_actions,
    # 2026-11-24): dividends reinvested and capital increases at the
    # exchange's base -- the same total-return basis as TEDPIX, which the
    # chart compares it with. back_adjust's gap rules are the fallback for
    # a name whose lists were never fetched.
    basis = "heuristic"
    try:
        import corporate_actions
        exchange = corporate_actions.exchange_adjust(bars, code, fill_untraded=True)
        if exchange is not None and exchange.attrs.get("exchange_events"):
            bars, basis = exchange, "exchange"
    except Exception:                                           # noqa: BLE001
        pass
    bars = bars[bars["volume"].fillna(0) > 0] if "volume" in bars.columns else bars
    # A halted share can have bars and not one trade among them (63 names on
    # 2026-11-23; the route answered HTTP 500 for دتهران and پرشیا): no
    # price chart, and the rest of the report stands.
    if len(bars) < 2:
        return None
    if basis == "exchange":
        adjusted = bars.reset_index(drop=True)
    else:
        adjusted, _events = back_adjust(bars)
        adjusted = adjusted.reset_index(drop=True)
    try:
        index = database.load_index_history(config.INDICES["TEDPIX"]["insCode"])
    except Exception:                                           # noqa: BLE001
        index = None
    dates = [str(d)[:10] for d in adjusted["date"]]
    closes = [float(c) for c in adjusted["close"]]
    base = closes[0]
    own = dict(zip(dates, closes))
    # The x axis is the market's calendar (the index's sessions), not the
    # share's own: across a halt the share's line breaks, and the index is
    # not drawn as one jump from the last session before it to the first
    # after (فولاد, 2026-11-23).
    calendar = []
    by_day = {}
    if index is not None and len(index):
        by_day = {str(d)[:10]: float(c) for d, c in zip(index["date"], index["close"])}
        calendar = [d for d in sorted(by_day) if dates[0] <= d <= dates[-1]]
    if len(calendar) < len(dates) // 2:
        calendar = dates
    stock, idx = [], []
    first = by_day.get(calendar[0]) if by_day else None
    for d in calendar:
        label = jalali_label(d)
        stock.append((label, own[d] / base * 100 if d in own else None))
        v = by_day.get(d)
        idx.append((label, v / first * 100 if v and first else None))
    out = {"stock": stock, "index": idx if any(v for _l, v in idx) else [],
           "stock_return_pct": (closes[-1] / base - 1) * 100,
           "index_return_pct": ((idx[-1][1] - 100) if idx and idx[-1][1] else None),
           "from": jalali_label(dates[0]), "to": jalali_label(dates[-1]),
           "basis": basis,
           "sessions": len(calendar), "traded": len(dates)}
    return out


def jalali_label(gregorian):
    from datetime import date

    try:
        y, m, d = (int(p) for p in str(gregorian)[:10].split("-"))
        jy, jm, jd = jalali.today_jalali(date(y, m, d))
        return "%04d/%02d/%02d" % (jy, jm, jd)
    except (TypeError, ValueError, AttributeError):
        return str(gregorian)[:10]


METHOD_TEXT = {
    "monthly_sales": "فروش سال جاری = فروش سال مالی قبل × رشد فروش «از ابتدای سال تا امروز» در تازه‌ترین گزارش ماهانه؛ سود = آن فروش × حاشیه سود خالص ۱۲ ماه گذشته.",
    "year_completed": "ماه‌های گذشته‌ی امسال از صورت مالی؛ ماه‌های باقی‌مانده = همان ماه‌های سال قبل × رشد امسال تا امروز، با حاشیه سود خالص ۱۲ ماه گذشته.",
    "next_year_trend": "صورت مالی سالانه آخرین داده است: سال بعد با رشد فروش امسال که نیمی از آن به سمت تورم برگردانده شده، و حاشیه‌ی امسال.",
    "annualised": "سال قبل ثبت نشده: دوره‌ی اخیر به سال تعمیم داده شده (ساده و کم‌دقت).",
}
BASIS_TEXT = {
    "consolidated": "صورت‌های تلفیقی (کل گروه)", "separate": "صورت‌های جداگانه (خود شرکت)",
    "mixed": "تلفیقی و جداگانه (هم‌اندازه‌اند و یک سری حساب شده‌اند)",
    "separate_fallback": "جداگانه — صورت تلفیقی تازه هنوز منتشر نشده",
}


_REFRESH = {"at": None}
REFRESH_GAP_SECONDS = 1800


def refresh_inputs_once(now=None):
    """Bring the dollar series and the calibration up to date, at most once
    per REFRESH_GAP_SECONDS for the process.

    Both are bootstrap's job (its post-run hook); this is the fallback for a
    day bootstrap had nothing to fetch. Each call answers at once when its
    file is fresh, but a FAILED dollar fetch stamps nothing, and with the
    source unreachable every report request would have retried it for up to
    two minutes (the review of 2026-11-23). True when it ran.
    """

    now = time.monotonic() if now is None else now
    if _REFRESH["at"] is not None and now - _REFRESH["at"] < REFRESH_GAP_SECONDS:
        return False
    _REFRESH["at"] = now
    import fx
    import outlook_calibration
    fx.refresh()
    outlook_calibration.ensure()
    return True


def gather(symbol, refresh_inputs=True):
    """Everything the report shows, from the analysis and the stores."""

    inst = find_instrument(symbol)
    code = str(inst["ins_code"])
    if refresh_inputs:
        refresh_inputs_once()
    price = fo.latest_prices().get(code)
    result = fo.analyse(code, price=price)
    if not result:
        raise ReportError("صورت مالی ثبت‌شده‌ای برای %s نیست" % symbol)
    result.update(symbol=inst.get("symbol_fa"), name=inst.get("name_fa"),
                  sector=(inst.get("sector_name") or "").strip())
    today = jalali.today_jalali()
    ctx = {"r": result, "inst": inst, "code": code,
           "today": "%04d/%02d/%02d" % today,
           "built_at": datetime.now().strftime("%H:%M"),
           "statements": statement_table(code, result),
           "prices": price_series(code),
           "dividends": dividend_rows(code),
           "method_text": METHOD_TEXT.get(result.get("method"), "برآورد ممکن نشد."),
           "basis_text": BASIS_TEXT.get(result.get("basis"), result.get("basis")),
           "opportunity": result.get("hurdle") or {}}
    import fx
    ctx["dollar"] = fx.summary()
    # One unit for every statement figure in the report, fitted to the
    # company: فولاد's year is ~1,500 thousand billion rials, a small maker's
    # a few hundred billion.
    sizes = [abs(x) for x in ((result.get("ttm") or {}).get("revenue"),
                              (result.get("forward") or {}).get("revenue"),
                              (result.get("valuation") or {}).get("market_cap")) if x]
    big = max(sizes) if sizes else 0
    ctx["unit"], ctx["unit_label"] = ((1e12, "هزار میلیارد ریال") if big >= 1e15
                                      else (1e9, "میلیارد ریال"))
    return ctx


def short_quarter(q):
    """'۰۵-ف۲': the fiscal year's last two digits and the quarter."""

    return "ف%d %02d" % (q["quarter"], int(q["fiscal_year"][:4]) % 100)


def charts(ctx):
    r = ctx["r"]
    unit = ctx.get("unit") or 1e9
    out = {}
    q = r.get("quarters") or []
    if len(q) >= 3:
        out["quarter_sales"] = bar_chart([x["revenue"] / unit for x in q],
                                         [short_quarter(x) for x in q],
                                         fmt=lambda v: num(v, 0), width=330, height=150)
        out["quarter_margin"] = bar_chart([x["net_margin_pct"] for x in q],
                                          [short_quarter(x) for x in q],
                                          color=SERIES[2], fmt=lambda v: num(v, 0), unit="٪", width=330, height=150)
    m = r.get("monthly") or {}
    series = [s for s in (m.get("series") or []) if s.get("sales") is not None]
    if len(series) >= 3:
        out["monthly_sales"] = bar_chart([s["sales"] / unit for s in series],
                                         [s["period_end"][2:7] for s in series],
                                         fmt=lambda v: num(v, 0))
    p = ctx.get("prices")
    if p:
        out["price"] = line_chart([(r["symbol"], SERIES[0], p["stock"], 2.2),
                                   ("شاخص کل", INK_3, p["index"], 1.6)])
    fv = r.get("fair_value") or {}
    price = (r.get("valuation") or {}).get("price")
    if fv.get("central") and price and fv.get("basis") == "pe":
        out["fair"] = range_chart(price, fv.get("low"), fv["central"], fv.get("high"),
                                  [(a["label"], a.get("value")) for a in fv.get("anchors") or []])
    return out


def render_html(ctx):
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from markupsafe import Markup

    env = Environment(loader=FileSystemLoader(TEMPLATES),
                      autoescape=select_autoescape(["html"]))
    unit = ctx.get("unit") or 1e9

    def amt(rials):
        """A statement figure in the report's unit."""
        if rials is None:
            return "—"
        value = rials / unit
        return num(value, 1 if abs(value) < 100 else 0)

    env.filters.update(num=num, pct=pct, signed=signed, bn=bn, money=money,
                       amt=amt)
    env.globals["font_url"] = "file:///" + FONT_PATH.replace("\\", "/")
    svg = {k: Markup(v) for k, v in charts(ctx).items() if v}
    return env.get_template("report.html").render(svg=svg, **ctx)


def edge_path():
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    found = shutil.which("msedge")
    if found:
        return found
    raise ReportError("مرورگر Edge پیدا نشد؛ ساخت PDF به آن نیاز دارد")


def html_to_pdf(html_text, out_path, timeout=EDGE_TIMEOUT):
    """Print an HTML page to PDF with headless Edge; the path written.

    Edge's launcher can return before the file lands, so the file is
    waited for until its size holds still. Old headless mode first: it has
    never failed on this machine (verifying-panel-ui-changes memory).
    """

    work = tempfile.mkdtemp(prefix="sirat-report-")
    try:
        page = os.path.join(work, "report.html")
        with open(page, "w", encoding="utf-8") as handle:
            handle.write(html_text)
        target = os.path.join(work, "report.pdf")         # ASCII path for Edge
        for mode in ("old", "new"):
            profile = os.path.join(work, "profile-" + mode)
            args = [edge_path(), "--headless=%s" % mode, "--disable-gpu",
                    "--no-first-run", "--no-default-browser-check",
                    "--user-data-dir=%s" % profile,
                    "--allow-file-access-from-files",
                    "--disable-blink-features=ForcedColors",
                    "--no-pdf-header-footer", "--print-to-pdf=%s" % target,
                    "file:///" + page.replace("\\", "/")]
            try:
                subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                pass
            deadline = time.monotonic() + 30
            size = -1
            while time.monotonic() < deadline:
                now = os.path.getsize(target) if os.path.exists(target) else -1
                if now > 0 and now == size:
                    break
                size = now
                time.sleep(0.5)
            if os.path.exists(target) and os.path.getsize(target) > 0:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                shutil.copyfile(target, out_path)
                return out_path
        raise ReportError("Edge فایل PDF را ننوشت")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def safe_name(symbol):
    keep = "".join(ch for ch in str(symbol) if ch.isalnum() or ch in "-_ ")
    return keep.strip() or "report"


def build(symbol, out_dir=None, keep_html=False, refresh_inputs=True):
    """Build one company's report; the PDF's path."""

    ctx = gather(symbol, refresh_inputs=refresh_inputs)
    html_text = render_html(ctx)
    day = ctx["today"].replace("/", "-")
    own_folder = out_dir is None
    out_dir = out_dir or os.path.join(REPORTS_DIR, day)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s-%s.pdf" % (safe_name(ctx["r"]["symbol"]), day))
    if keep_html:
        with open(out[:-4] + ".html", "w", encoding="utf-8") as handle:
            handle.write(html_text)
    path = html_to_pdf(html_text, out)
    # Reports are rebuilt on demand, so old days' folders are only clutter:
    # ~1 MB a report, and nothing called prune() (the same review).
    # Only ever the project's own reports/ folder: with --out the report
    # sits among the owner's other folders, and those are not ours to age.
    if own_folder:
        try:
            prune(REPORT_KEEP_DAYS)
        except OSError:
            pass
    return path


def prune(days=30, root=None):
    """Remove the day folders (YYYY-MM-DD) under reports/ older than `days`;
    the count removed. Anything not named as a day folder is left alone,
    whatever its age."""

    import re

    root = root or REPORTS_DIR
    cutoff = time.time() - days * 86400
    removed = 0
    for folder in glob.glob(os.path.join(root, "*")):
        try:
            if os.path.isdir(folder) and \
                    re.fullmatch(r"\d{4}-\d{2}-\d{2}", os.path.basename(folder)) and \
                    os.path.getmtime(folder) < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--out", default=None)
    parser.add_argument("--html", action="store_true", help="keep the HTML too")
    args = parser.parse_args(argv)
    status = 0
    for symbol in args.symbols:
        try:
            print(build(symbol, out_dir=args.out, keep_html=args.html))
        except ReportError as exc:
            print("%s: %s" % (symbol, exc))
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
