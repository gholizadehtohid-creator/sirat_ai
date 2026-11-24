"""What the Bale bot says: the panel's own answers, turned into short Persian
messages. Pure functions of the panel's JSON -- no network, no store -- so
every message can be tested on a recorded answer.

THE RULES THE PANEL ALREADY KEEPS, KEPT HERE TOO (2026-11-24)
-------------------------------------------------------------
* Information and education, not advice: every content message ends with
  DISCLAIMER. Offering investment advice to others needs a licence in Iran.
* Nothing a test of the bot's own rejected: no technical scanner, no stock
  basket, no entry/stop/target levels. A fair value is said to be no buy
  signal; the allocation profiles carry their «موقت» evidence.
* A range is given with how often it held on years it was not built from,
  as a natural frequency («۵ مورد از ۱۰»).
"""

FA_DIGITS = str.maketrans("0123456789-.", "۰۱۲۳۴۵۶۷۸۹−٫")

DISCLAIMER = "— اطلاعات و آموزش است، نه توصیه‌ی خرید و فروش."

METHOD_FA = {
    "monthly_sales": "از گزارش‌های فروش ماهانه",
    "year_completed": "از صورت‌های مالی امسال",
    "next_year_trend": "برای سال مالی بعد",
    "annualised": "تعمیم ساده‌ی دوره‌ی اخیر (کم‌دقت)",
}

MENU = [["🔎 بررسی نماد", "📊 خلاصه‌ی بازار"],
        ["🧭 تخصیص دارایی", "💼 سبد من"],
        ["🔔 هشدارها", "ℹ️ راهنما"]]


def fa(value, digits=0, sign=False):
    """A number in Persian digits with thousands separators, or «—»."""

    if value is None:
        return "—"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = ("{:+,.%df}" if sign else "{:,.%df}") % digits
    text = text.format(x).replace(",", "٬")
    return text.translate(FA_DIGITS)


def pct(value, digits=1, sign=False):
    return "—" if value is None else fa(value, digits, sign) + "٪"


def money(rials):
    """Rials in words a reader takes in at a glance."""

    if rials is None:
        return "—"
    x = float(rials)
    for scale, word in ((1e12, "هزار میلیارد"), (1e9, "میلیارد"),
                        (1e6, "میلیون")):
        if abs(x) >= scale:
            return "%s %s ریال" % (fa(x / scale, 1 if abs(x) < 10 * scale else 0),
                                   word)
    return "%s ریال" % fa(x)


_FA = "۰۱۲۳۴۵۶۷۸۹"


def _gregorian_to_jalali(match):
    from datetime import date
    import jalali
    try:
        jy, jm, jd = jalali.today_jalali(date(int(match.group(1)), int(match.group(2)),
                                              int(match.group(3))))
    except (TypeError, ValueError):
        return match.group(0)
    return "%04d/%02d/%02d" % (jy, jm, jd)


def jdate(text):
    """Prose or a date for a Persian reader: a Gregorian YYYY-MM-DD becomes its
    Jalali date, and digits become Persian ones -- digits only: a full stop
    stays a full stop, and a digit right after a Latin letter (E36) is left."""

    import re
    s = re.sub(r"(?<![0-9])(20[0-9]{2})-([0-9]{2})-([0-9]{2})(?![0-9])", _gregorian_to_jalali,
               str(text or "—"))
    return re.sub(r"(?<![A-Za-z0-9])[0-9]+(?:[.][0-9]+)?",
                  lambda m: m.group(0).translate(str.maketrans("0123456789.", _FA + "٫")), s)


def _lines(*parts):
    return "\n".join(p for p in parts if p)


# ---- account messages -------------------------------------------------------------

def welcome_pending(first_name=None):
    return _lines(
        "سلام%s 👋" % (" " + first_name if first_name else ""),
        "درخواست شما برای استفاده از ربات صراط ثبت شد.",
        "پس از تأیید ادمین، همین‌جا خبرتان می‌کنیم.")


def welcome_approved(first_name=None):
    return _lines(
        "سلام%s، خوش آمدید 🌱" % (" " + first_name if first_name else ""),
        "نماد یک سهم را بفرستید (مثلاً «فولاد») یا از دکمه‌های پایین استفاده کنید.",
        DISCLAIMER)


BLOCKED = "دسترسی شما به این ربات بسته است."
PENDING = "درخواست شما هنوز در انتظار تأیید ادمین است."
QUOTA = "سهمیه‌ی درخواست امروز شما تمام شده است؛ فردا دوباره امتحان کنید."
PDF_QUOTA = "سهمیه‌ی گزارش PDF امروز شما تمام شده است."
BUSY = "گزارش دیگری در حال ساخت است؛ چند ثانیه‌ی دیگر دوباره بزنید."
FAILED = "متأسفانه این درخواست انجام نشد. کمی بعد دوباره امتحان کنید."


def help_text():
    return _lines(
        "راهنمای ربات صراط",
        "",
        "🔎 بررسی نماد: نماد را بفرستید، مثلاً «فولاد». برآورد سود سال، بازه‌ی خطای آن، "
        "ارزش مرجع، مانع اخزا، هشدارها و رویدادهای مجمع را می‌بینید؛ با دکمه‌ی PDF "
        "گزارش سه تا چهار صفحه‌ای می‌گیرید.",
        "📊 خلاصه‌ی بازار: شاخص کل، ورود و خروج پول حقیقی، ارزان یا گران بودن بازار و "
        "بازده اخزا.",
        "🧭 تخصیص دارایی: ترکیب‌های سهام، طلا و درآمد ثابت و نتیجه‌ی آزمون ۱۵ ساله‌ی آن‌ها.",
        "💼 سبد من: فهرست سهم‌هایتان را بفرستید (هر خط «نماد تعداد»)؛ ارزش، هشدارها و "
        "وضعیت معامله‌ی هر کدام را می‌بینید.",
        "🔔 هشدارها: خلاصه‌ی روزانه‌ی عصر و خبر مجمع، سود و افزایش سرمایه‌ی نمادهای "
        "سبدتان را روشن یا خاموش کنید.",
        "",
        "این ربات سیگنال خرید و فروش نمی‌دهد: روش‌هایی که در آزمون‌های خودش رد شده‌اند "
        "(پویشگر تکنیکال، سبد پیشنهادی) در آن نیستند.",
        DISCLAIMER)


def admin_join_request(user):
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    handle = ("@" + user["username"]) if user.get("username") else "بدون نام کاربری"
    return _lines("درخواست عضویت تازه:", "%s (%s)" % (name or "—", handle),
                  "شناسه: %s" % jdate(user.get("chat_id")))


# ---- one company --------------------------------------------------------------

def stock_text(o, events=None):
    """The outlook of one company (GET /api/outlook?symbol=...)."""

    if not o or o.get("error"):
        return (o or {}).get("error") or "این نماد پیدا نشد."
    v = o.get("valuation") or {}
    f = o.get("forward") or {}
    rg = f.get("range") or {}
    cov = rg.get("coverage") or {}
    fv = o.get("fair_value") or {}
    h = o.get("hurdle") or {}
    latest = o.get("latest") or {}

    head = "📌 %s — %s" % (o.get("symbol") or "", o.get("name") or "")
    price = "قیمت %s ریال · P/E گذشته %s · P/E آینده %s" % (
        fa(v.get("price")), fa(v.get("pe_ttm"), 1), fa(v.get("pe_forward"), 1))

    estimate = ""
    if f.get("eps"):
        estimate = "سود هر سهم امسال (برآورد %s): %s ریال" % (
            METHOD_FA.get(o.get("method"), "برآورد"), fa(f.get("eps")))
        if rg.get("eps_low") is not None:
            estimate += "\nبازه‌ی محتمل: %s تا %s ریال (خطای میانه %s در %s برآورد گذشته)" % (
                fa(rg.get("eps_low")), fa(rg.get("eps_high")),
                pct(rg.get("median_abs_error_pct"), 0), fa(rg.get("n")))
        if cov.get("per10") is not None:
            worst = cov.get("worst_year") or {}
            estimate += "\nدر سال‌های کنارگذاشته %s مورد از ۱۰ داخل این بازه افتاد" % fa(
                cov.get("per10"))
            if worst.get("rate") is not None and worst.get("year"):
                estimate += "؛ در %s فقط %s از ۱۰" % (
                    jdate(worst["year"]), fa(round(worst["rate"] * 10)))

    fair = ""
    if fv.get("central"):
        if fv.get("basis") == "nav":
            fair = "ارزش خالص دارایی هر سهم: %s ریال (کف ارزش، نه هدف قیمت)" % fa(
                fv["central"])
        else:
            fair = ("ارزش مرجع با P/E صنعت: %s ریال (بازه %s تا %s) — نشانه‌ی خرید "
                    "نیست؛ آزمون‌های بات نشان داد ارزانی به‌تنهایی بازده بهتر نداد.") % (
                fa(fv["central"]), fa(fv.get("low")), fa(fv.get("high")))

    hurdle = ""
    if h.get("required_growth_pct") is not None:
        hurdle = "در برابر اخزای %s، قیمت امروز رشد سالانه‌ی حدود %s در سود لازم دارد؛ رشد فعلی %s." % (
            pct(h.get("rate_pct"), 0), pct(h.get("required_growth_pct"), 0),
            pct(h.get("actual_growth_pct"), 0))

    dividend = ""
    if v.get("dps"):
        dividend = "سود نقدی آخرین مجمع: %s ریال (بازده %s)" % (
            fa(v.get("dps")), pct(v.get("dividend_yield_pct")))

    flags = (o.get("flags") or [])[:4]
    positives = (o.get("positives") or [])[:3]
    trading = o.get("trading") or {}
    notes = (trading.get("notes") or [])[:2]

    parts = [head, price, "", estimate, fair, hurdle, dividend]
    if positives:
        parts += ["", "نکته‌های مثبت:"] + ["• " + jdate(x) for x in positives]
    if flags:
        parts += ["", "⚠️ هشدارها:"] + ["• " + jdate(x) for x in flags]
    if notes:
        parts += ["", "معامله‌پذیری:"] + ["• " + jdate(x) for x in notes]
    if events:
        parts += ["", "🗓 رویدادها:"] + ["• " + e for e in events[:4]]
    if latest.get("period_end"):
        parts += ["", "آخرین صورت مالی: %s ماهه‌ی منتهی به %s" % (
            fa(latest.get("months")), jdate(latest.get("period_end")))]
    parts.append(DISCLAIMER)
    return _lines(*parts)


def calendar_events_for(calendar, symbol):
    """Lines about one symbol from corporate_calendar.calendar()."""

    import persian
    want = persian.normalize(symbol or "").replace(" ", "")
    out = []

    def same(item):
        return persian.normalize(item.get("symbol") or "").replace(" ", "") == want

    for p in calendar.get("pending_meetings") or []:
        if same(p):
            out.append("%s: دعوت شده؛ بازه‌ی برگزاری %s تا %s (نماد معمولاً آن روز بسته است)" % (
                p.get("meeting_type_fa") or "مجمع",
                jdate((p.get("expected_from") or {}).get("jalali")),
                jdate((p.get("expected_to") or {}).get("jalali"))))
    for d in calendar.get("recent_decisions") or []:
        if same(d):
            dps = d.get("dps_exchange") if d.get("dps_exchange") is not None else d.get("dps_codal")
            line = "%s برگزار شد (%s)" % (d.get("meeting_type_fa") or "مجمع",
                                         jdate((d.get("meeting_date") or {}).get("jalali")))
            if dps:
                line += "؛ سود هر سهم %s ریال، مهلت پرداخت تا %s" % (
                    fa(dps), jdate((d.get("payment_deadline") or {}).get("jalali")))
            out.append(line)
    for c in calendar.get("capital_increases") or []:
        if same(c) and c.get("status") != "stale":
            stage = c.get("latest_stage") or {}
            out.append("افزایش سرمایه: %s (%s)" % (
                stage.get("label_fa") or "در جریان",
                jdate((stage.get("date") or {}).get("jalali"))))
    return out


# ---- the market ---------------------------------------------------------------

def market_text(indices=None, flow=None, valuation=None, bill_yield=None):
    parts = ["📊 خلاصه‌ی بازار"]
    tedpix = None
    for item in (indices or {}).get("indices") or []:
        if item.get("key") == "TEDPIX":
            tedpix = item
    if tedpix:
        r = tedpix.get("returns") or {}
        parts.append("شاخص کل: %s (%s) — یک روز %s، یک هفته %s، یک ماه %s" % (
            fa(tedpix.get("close")), jdate(tedpix.get("last_date")),
            pct(r.get("1d"), 1, True), pct(r.get("5d"), 1, True),
            pct(r.get("20d"), 1, True)))
    last = (flow or {}).get("last") or {}
    if last:
        parts.append("پول حقیقی آخرین جلسه (%s): %s؛ %s از نمادها ورود پول داشتند" % (
            jdate(last.get("date")), money(last.get("net_individual")),
            pct(last.get("breadth_pct"), 0)))
        if (flow or {}).get("sum_5") is not None:
            parts.append("جمع ۵ جلسه: %s · ۲۰ جلسه: %s" % (
                money(flow.get("sum_5")), money(flow.get("sum_20"))))
        parts.append("(جریان پول حقیقی در آزمون‌های بات پیش‌بینی‌کننده‌ی بازده نبود؛ "
                     "فقط برای شناخت فضای بازار است.)")
    now = (valuation or {}).get("now") or {}
    if now.get("pe"):
        parts.append("P/E بازار: %s — صدک %s در تاریخچه‌ی خودش (میانه %s)" % (
            fa(now.get("pe"), 1), fa((valuation or {}).get("percentile"), 0),
            fa((valuation or {}).get("median_pe"), 1)))
    by = bill_yield if bill_yield is not None else (valuation or {}).get("bill_yield_pct")
    if by:
        parts.append("بازده سالانه‌ی اخزا (بیش از ۹۰ روز تا سررسید): %s" % pct(by, 1))
    parts.append(DISCLAIMER)
    return _lines(*parts)


# ---- allocation ---------------------------------------------------------------

def allocation_text(rep):
    labels = (rep or {}).get("sleeve_labels") or {}
    badge = {"tested": "آزموده", "provisional": "موقت", "described": "توصیفی"}
    parts = ["🧭 تخصیص دارایی"]
    for p in (rep or {}).get("profiles") or []:
        weights = "، ".join("%s %s" % (pct(w, 0), labels.get(k, k))
                           for k, w in (p.get("weights") or {}).items() if w)
        parts.append("• %s [%s]\n  %s" % (p.get("label"), badge.get(p.get("evidence"), "—"),
                                          weights))
    rt = (rep or {}).get("retest") or {}
    if rt.get("text"):
        parts += ["", jdate(rt["text"])]
    rb = (rep or {}).get("rebalance") or {}
    if rb.get("text"):
        parts += ["", "توازن: " + rb["text"]]
    parts += ["", "این‌ها ترکیب‌های مرجع‌اند، نه دستور خرید و فروش؛ وزن مناسب به افق و "
                  "تحمل افت شما بستگی دارد.", DISCLAIMER]
    return _lines(*parts)


# ---- the user's own holdings ----------------------------------------------------

def holdings_text(rep):
    rows = (rep or {}).get("rows") or []
    if not rows:
        return "فهرست سبد شما خالی است. «✏️ ویرایش سبد» را بزنید."
    parts = ["💼 سبد شما — ارزش کل %s (قیمت‌ها تا %s)" % (
        money(rep.get("total_value")), jdate(rep.get("as_of")))]
    for r in rows[:30]:
        line = "• %s: %s سهم، %s، امروز %s" % (
            r.get("symbol"), fa(r.get("quantity")), money(r.get("value")),
            pct(r.get("change_pct"), 1, True))
        if r.get("halted"):
            line += " — متوقف"
        elif r.get("lock_note"):
            line += " — " + r["lock_note"]
        flags = ((r.get("outlook") or {}).get("flags") or [])[:1]
        if flags:
            line += "\n  ⚠️ " + jdate(flags[0])
        parts.append(line)
    warnings = (rep.get("warnings") or [])[:5]
    if warnings:
        parts += ["", "نکته‌ها:"] + ["• " + jdate(w) for w in warnings]
    parts.append(DISCLAIMER)
    return _lines(*parts)


def holdings_prompt(current_lines):
    now = "\n".join(current_lines) if current_lines else "(خالی)"
    return _lines(
        "فهرست سبدتان را بفرستید؛ هر خط یک نماد و تعداد، مثلاً:",
        "فولاد 2000", "عیار 30000", "",
        "فهرست فعلی:", now, "",
        "این فهرست جای فهرست فعلی را می‌گیرد. برای انصراف «لغو» بفرستید.")


def alerts_text(user):
    return _lines(
        "🔔 هشدارها",
        "خلاصه‌ی روزانه‌ی عصر: %s" % ("روشن ✅" if user.get("summary_on") else "خاموش"),
        "رویدادهای نمادهای سبد شما (مجمع، سود، افزایش سرمایه): %s" % (
            "روشن ✅" if user.get("alerts_on") else "خاموش"))
