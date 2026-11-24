"""AGM and capital-increase calendar, built only from what is stored.

    python corporate_calendar.py [--today YYYY-MM-DD] [--back 45] [--ahead 60]
    python corporate_calendar.py --coverage
    python corporate_calendar.py --lags      invitation -> decision, measured

OPERATIONAL INFORMATION, NOT A BUY SIGNAL. It says which companies are
about to hold a meeting (their symbol is normally halted for it), what the
recent meetings decided, when a dividend is due by law, and how far each
capital increase has gone. None of it says whether to buy.

No request is made: Codal throttles hard and the backfill is using it.

SOURCES
-------
  sirat_market.db        codal_letters     per-company Codal searches
                                           (codal_sync and friends): every
                                           code, but only as fresh as each
                                           company's last search
  sirat_fundamentals.db  announcements     the feed watch (codal_watch): the
                                           AGM and capital codes it reads,
                                           every company, since it started
                         dividends         DPS read from AGM decisions
                         watch_seen        what the watch has seen (coverage)
  sirat_market.db        corporate_actions the exchange's dividend events
                         share_changes     the exchange's share counts
                                           (corporate_actions.py)

COVERAGE (measured 2026-11-24; coverage() recomputes it on every call)
-----------------------------------------------------------------------
Since the watch started (1405/07/22 = 2026-10-14) every in-universe AGM
and capital letter it saw is stored (627 of 627 at 09:20, 640 of 640 at
13:20). Five calendar
codes are NOT in the watch's feed -- ن-63 (permit extension), ن-64 (rights
subscription window), ن-70, ن-72, ن-73 (board decisions on a delegated
increase) -- so they reach the store only through the per-company search,
which last ran on 2026-08-11/12 for ~742 of 828 companies. Letters of
1405/05/22 .. 1405/07/21 are thin for every code (84 stored in 1405/06
against 704 in 1405/05); a pending item whose letters fell in that gap can
be missing. Each answer carries its own `coverage` block.

THE RULES USED (labelled in every answer, `rules`)
--------------------------------------------------
  Invitation lead time: the meeting is 10 to 40 days after the invitation
      is published -- Commercial Code amendment of 1347 (لایحهٔ اصلاحی
      قسمتی از قانون تجارت), Art. 98, as commonly cited. Codal titles do
      not carry the meeting date, so a pending meeting is given as that
      window; the letter itself has the date. Measured over the 772
      stored invitation -> decision pairs (--lags, 2026-11-24): median 11
      days for every meeting type, p95 20-26; 654 (85%) inside 10..45
      days. The shorter ones are amended or
      second-call invitations re-published close to the meeting. Each
      pending item carries `typical_date` = invitation + that median.
  Dividend payment deadline: within 8 months of the AGM's decision -- same
      law, Art. 240, as commonly cited. The meeting date is the exchange's
      dividend event date when there is one (since 2020 the exchange dates
      a dividend at the meeting session, see corporate_actions), else the
      decision letter's publication date (flagged 'approx'). A company's
      own schedule is its ن-13 letter, attached when stored.
"""

import argparse
import json
import sys
from datetime import date, timedelta

import corporate_actions
import database
import jalali


CODAL_SITE = "https://codal.ir"

NOTE_FA = ("اطلاعات عملیاتی است، نه سیگنال خرید یا فروش. تاریخ دقیق مجمع "
           "در متن آگهی دعوت است؛ اینجا بازهٔ قانونی آن آمده است.")
NOTE_EN = ("Operational information, not a buy or sell signal. The exact "
           "meeting date is in the invitation letter; this gives its legal "
           "window.")
HALT_NOTE_FA = "نماد معمولاً در روز برگزاری مجمع متوقف می‌شود."

INVITE_MIN_DAYS, INVITE_MAX_DAYS = 10, 40
PAYMENT_MONTHS = 8
RULES = {
    "invitation_lead_days": [INVITE_MIN_DAYS, INVITE_MAX_DAYS],
    "invitation_lead_source": (
        "ماده ۹۸ لایحهٔ اصلاحی قسمتی از قانون تجارت (۱۳۴۷): فاصلهٔ نشر دعوت "
        "تا تشکیل مجمع حداقل ۱۰ و حداکثر ۴۰ روز (as commonly cited)"),
    "payment_deadline_months": PAYMENT_MONTHS,
    "payment_deadline_source": (
        "ماده ۲۴۰ لایحهٔ اصلاحی قسمتی از قانون تجارت (۱۳۴۷): پرداخت سود "
        "ظرف ۸ ماه پس از تصمیم مجمع عمومی (as commonly cited)"),
    "payment_deadline_label": "مهلت قانونی پرداخت سود (۸ ماه پس از مجمع)",
}

# A pending meeting is looked for this far back: the longest legal lead
# time plus room for the decision letter to arrive.
PENDING_LOOKBACK_DAYS = INVITE_MAX_DAYS + 20

# Median days from invitation to decision letter, by meeting type (measured,
# see the module docstring). A decision is published on the meeting day or
# the next, so this is the typical meeting date.
TYPICAL_LAG_DAYS = {"annual": 11, "ordinary_extra": 11, "extraordinary": 11}
CAPITAL_LOOKBACK_DAYS = 365
CAPITAL_STALE_DAYS = 120

MEETING_TYPES = {
    "annual": "مجمع عمومی عادی سالیانه",
    "ordinary_extra": "مجمع عمومی عادی به‌طور فوق‌العاده",
    "extraordinary": "مجمع عمومی فوق‌العاده",
}

# invitation code -> (meeting type, decision codes that close it)
INVITATIONS = {
    "ن-50": ("annual", ("ن-51", "ن-52")),
    "ن-53": ("annual", ("ن-51", "ن-52")),
    "ن-54": ("ordinary_extra", ("ن-51", "ن-55")),
    "ن-56": ("extraordinary", ("ن-57",)),
}
CANCELLATION = "ن-58"
DECISIONS = {"ن-52": "annual", "ن-51": None, "ن-55": "ordinary_extra",
             "ن-57": "extraordinary"}
AGM_DECISIONS = ("ن-51", "ن-52", "ن-55")
PAYMENT_SCHEDULE = "ن-13"

STAGES = {
    "ن-60": "پیشنهاد هیئت مدیره",
    "ن-61": "اظهارنظر حسابرس",
    "ن-62": "مدارک و مستندات به سازمان",
    "ن-56": "دعوت به مجمع فوق‌العاده",
    "ن-57": "تصمیمات مجمع فوق‌العاده",
    "ن-71": "زمان جلسهٔ هیئت مدیره (سرمایهٔ تفویضی)",
    "ن-72": "لغو جلسهٔ هیئت مدیره (سرمایهٔ تفویضی)",
    "ن-70": "تصمیم هیئت مدیره (سرمایهٔ تفویضی)",
    "ن-73": "تصمیمات هیئت مدیره دربارهٔ افزایش سرمایه",
    "ن-63": "تمدید مهلت مجوز",
    "ن-64": "مهلت استفاده از حق تقدم",
    "ن-65": "پذیره‌نویسی عمومی",
    "ن-66": "فروش حق تقدم‌های استفاده‌نشده",
    "ن-67": "ثبت افزایش سرمایه",
}
CAPITAL_CODES = tuple(STAGES)
REGISTRATION = "ن-67"
EXCHANGE_STAGE = "ثبت تعداد سهام جدید در بورس"

CALENDAR_CODES = tuple(sorted(set(INVITATIONS) | {CANCELLATION}
                              | set(DECISIONS) | {PAYMENT_SCHEDULE}
                              | set(CAPITAL_CODES)))

# The codes the feed watch reads (codal_watch.FEEDS). Spelled here so this
# module imports nothing that talks to Codal; coverage() checks it.
WATCHED_FALLBACK = ("ن-50", "ن-51", "ن-52", "ن-53", "ن-54", "ن-55", "ن-58",
                    "ن-13", "ن-85", "ن-56", "ن-57", "ن-60", "ن-61", "ن-62",
                    "ن-65", "ن-66", "ن-67", "ن-71")


# =============================================================================
# DATES
# =============================================================================

def _day(value):
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def to_jalali(iso):
    return corporate_actions.to_jalali(iso)


def jalali_to_iso(text):
    day = jalali.parse_date(text)
    return day.isoformat() if day else None


def _jalali_text(day):
    found = jalali.today_jalali(day)
    return "%04d/%02d/%02d" % found if found else None


def _month_length(jy, jm):
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if jalali.is_jalali_leap(jy) else 29


def add_jalali_months(iso, months):
    """ISO date + whole Jalali months, the day clamped to the month."""

    found = jalali.today_jalali(date.fromisoformat(iso[:10]))
    if not found:
        return None
    jy, jm, jd = found
    ny, nm = jalali.add_months(jy, jm, months)
    nd = min(jd, _month_length(ny, nm))
    return date(*jalali.to_gregorian(ny, nm, nd)).isoformat()


def payment_deadline(meeting_iso):
    """{'iso', 'jalali', 'label'}: the statutory dividend deadline."""

    if not meeting_iso:
        return None
    iso = add_jalali_months(meeting_iso, PAYMENT_MONTHS)
    return {"iso": iso, "jalali": to_jalali(iso),
            "label": RULES["payment_deadline_label"]}


def _dated(iso):
    return {"iso": iso, "jalali": to_jalali(iso)} if iso else None


# =============================================================================
# READING THE STORE
# =============================================================================

def _url(value):
    if not value:
        return None
    return CODAL_SITE + value if str(value).startswith("/") else value


def _fiscal_end(title):
    import re
    found = re.search(r"منتهی به\s*(\d{4}/\d{2}/\d{2})", title or "")
    return found.group(1) if found else None


def _letter(row, source):
    tracing, code, symbol, letter_code, title, published, url = row
    iso = jalali_to_iso(published)
    return {"tracing_no": str(tracing), "ins_code": str(code) if code else None,
            "symbol": symbol, "letter_code": letter_code, "title": title,
            "published_at": published, "published_iso": iso,
            "published_jalali": (published or "")[:10] or None,
            "url": _url(url), "fiscal_year_end": _fiscal_end(title),
            "is_amendment": "اصلاحیه" in (title or ""), "source": source}


def letters(since_jalali=None, until_jalali=None, codes=CALENDAR_CODES):
    """Calendar letters from both stores, one per tracing number, oldest
    first. `since_jalali`/`until_jalali` bound published_at ('1405/07/01')."""

    import fundamental_db

    where = ["letter_code IN (%s)" % ",".join("?" for _ in codes)]
    params = list(codes)
    if since_jalali:
        where.append("published_at >= ?")
        params.append(since_jalali)
    if until_jalali:
        where.append("published_at <= ?")
        params.append(until_jalali + " 99")
    query = ("SELECT tracing_no, ins_code, symbol, letter_code, title, "
             "published_at, url FROM %s WHERE " + " AND ".join(where))
    found = {}
    database.create_database()
    conn = database._connect()
    try:
        for row in conn.execute(query % "codal_letters", params):
            found[str(row[0])] = _letter(row, "codal_letters")
    finally:
        conn.close()
    try:
        conn = fundamental_db._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'announcements'"
            ).fetchone()
            if exists:
                for row in conn.execute(query % "announcements", params):
                    found[str(row[0])] = _letter(row, "announcements")
        finally:
            conn.close()
    except Exception:                                           # noqa: BLE001
        pass
    return sorted((l for l in found.values() if l["ins_code"]),
                  key=lambda l: (l["published_at"] or "", l["tracing_no"]))


def _by_company(stored):
    """{live ins_code: [letter]}, oldest first. A letter filed under a
    listing a board move retired counts for the live code: the watch filed
    بتهران's 1405/07 decisions under its old code (47563321799863211) and
    the per-company search its invitation under the new one."""

    try:
        successor = {code: row["successor"] for code, row in
                     database.superseded_listings().items()
                     if row.get("successor")}
    except Exception:                                           # noqa: BLE001
        successor = {}
    out = {}
    for letter in stored:
        code = successor.get(letter["ins_code"], letter["ins_code"])
        out.setdefault(code, []).append(dict(letter, ins_code=code))
    for items in out.values():
        items.sort(key=lambda l: (l["published_at"] or "", l["tracing_no"]))
    return out


def _symbols():
    return {str(r["ins_code"]): r["symbol_fa"]
            for r in database.load_instruments()}


def _exchange_events(kinds=("dividend",)):
    """{ins_code: [event]} from corporate_actions, oldest first."""

    corporate_actions.ensure_tables()
    conn = database._connect()
    try:
        rows = conn.execute(
            "SELECT ins_code, date, ex_date, kind, dps, not_adjusted, "
            "adjusted, factor FROM corporate_actions WHERE kind IN (%s) "
            "ORDER BY date" % ",".join("?" for _ in kinds), kinds).fetchall()
    finally:
        conn.close()
    out = {}
    for code, day, ex, kind, dps, before, after, factor in rows:
        out.setdefault(str(code), []).append(
            {"date": day, "ex_date": ex, "kind": kind, "dps": dps,
             "price_before": before, "price_after": after, "factor": factor})
    return out


def _share_changes():
    corporate_actions.ensure_tables()
    conn = database._connect()
    try:
        rows = conn.execute("SELECT ins_code, date, shares_old, shares_new, "
                            "ratio FROM share_changes ORDER BY date").fetchall()
    finally:
        conn.close()
    out = {}
    for code, day, old, new, ratio in rows:
        out.setdefault(str(code), []).append(
            {"date": day, "shares_old": old, "shares_new": new,
             "ratio": ratio})
    return out


def _codal_dividends():
    """{ins_code: [row]} and {tracing_no: row} from the dividends table."""

    by_code, by_tracing = {}, {}
    for row in corporate_actions._codal_dividends():
        by_code.setdefault(row["ins_code"], []).append(row)
        if row.get("tracing_no"):
            by_tracing[str(row["tracing_no"])] = row
    return by_code, by_tracing


def _ref(letter):
    if not letter:
        return None
    return {"tracing_no": letter["tracing_no"], "url": letter["url"],
            "letter_code": letter["letter_code"], "title": letter["title"],
            "published_jalali": letter["published_jalali"],
            "published_iso": letter["published_iso"]}


# =============================================================================
# COVERAGE
# =============================================================================

def coverage(today=None):
    """What the calendar can and cannot see, measured from the stores."""

    import fundamental_db

    today = _day(today)
    try:
        import codal_announcements
        watched = tuple(sorted(set(codal_announcements.CODES)
                               | {"ن-10", "ن-30", "ن-31"}))
    except Exception:                                           # noqa: BLE001
        watched = WATCHED_FALLBACK
    unwatched = [c for c in CALENDAR_CODES if c not in watched]
    out = {"watched_codes": [c for c in CALENDAR_CODES if c in watched],
           "unwatched_codes": unwatched}

    stored = {l["tracing_no"] for l in letters(
        since_jalali=_jalali_text(today - timedelta(days=400)))}
    try:
        conn = fundamental_db._connect()
        try:
            first = conn.execute(
                "SELECT MIN(published_at) FROM watch_seen").fetchone()[0]
            seen = conn.execute(
                "SELECT tracing_no, letter_code FROM watch_seen WHERE "
                "ins_code IS NOT NULL AND letter_code IN (%s)"
                % ",".join("?" for _ in CALENDAR_CODES),
                CALENDAR_CODES).fetchall()
        finally:
            conn.close()
    except Exception:                                           # noqa: BLE001
        first, seen = None, []
    present = sum(1 for t, _c in seen if str(t) in stored)
    out["watch_since"] = {"jalali": (first or "")[:10] or None,
                          "iso": jalali_to_iso(first)}
    out["watch_letters_stored"] = {"seen": len(seen), "stored": present}

    database.create_database()
    conn = database._connect()
    try:
        checks = [r[0] for r in conn.execute(
            "SELECT last_checked FROM codal_status WHERE last_checked "
            "IS NOT NULL ORDER BY last_checked")]
    finally:
        conn.close()
    recent = sum(1 for c in checks if c[:10] >= (today - timedelta(days=7))
                 .isoformat())
    out["per_company_search"] = {
        "companies": len(checks),
        "checked_within_7_days": recent,
        "median_last_checked": checks[len(checks) // 2][:10] if checks
        else None,
        "newest": checks[-1][:10] if checks else None,
    }
    monthly = {}
    for letter in letters(since_jalali=_jalali_text(
            today - timedelta(days=200))):
        month = (letter["published_at"] or "")[:7]
        monthly[month] = monthly.get(month, 0) + 1
    out["letters_by_month"] = dict(sorted(monthly.items()))
    since = out["watch_since"]["jalali"]
    out["summary_en"] = (
        "Watched codes are complete from %s (%d of %d watch letters "
        "stored). Unwatched codes (%s) come only from the per-company "
        "search, median last run %s. Letters between that run and the "
        "watch start are thin."
        % (since, present, len(seen), ", ".join(unwatched),
           out["per_company_search"]["median_last_checked"]))
    out["summary_fa"] = (
        "نامه‌های مجمع و افزایش سرمایه از %s کامل‌اند (پایش کدال). کدهای %s "
        "فقط از جست‌وجوی تک‌شرکتی می‌آیند که میانهٔ آخرین اجرای آن %s است؛ "
        "فاصلهٔ میان این دو ناقص است."
        % (since, "، ".join(unwatched),
           out["per_company_search"]["median_last_checked"]))
    return out


# =============================================================================
# THE CALENDAR
# =============================================================================

def meeting_kind(letter):
    """annual / ordinary_extra / extraordinary, from the title: ن-51 (a
    summary of decisions) serves every kind of meeting -- جوین 1405/08/29
    filed one for its AGM and two for its EGM."""

    title = letter.get("title") or ""
    if "فوق" in title:
        return "ordinary_extra" if "عادی" in title else "extraordinary"
    if DECISIONS.get(letter.get("letter_code")):
        return DECISIONS[letter["letter_code"]]
    return "annual"


def _decision_closes(invitation, letter):
    """Does `letter` decide the meeting `invitation` called?"""

    kind, codes = INVITATIONS[invitation["letter_code"]]
    if letter["letter_code"] not in codes and not (
            letter["letter_code"] == "ن-51" and meeting_kind(letter) == kind):
        return False
    if letter["letter_code"] == "ن-51" and meeting_kind(letter) != kind:
        return False
    if letter["published_at"] <= invitation["published_at"]:
        return False
    a, b = invitation["fiscal_year_end"], letter["fiscal_year_end"]
    return not a or not b or a == b


def _pending(by_company, today, symbols, dividends):
    since = (today - timedelta(days=PENDING_LOOKBACK_DAYS)).isoformat()
    out = []
    for code, items in by_company.items():
        invites = [l for l in items if l["letter_code"] in INVITATIONS
                   and l["published_iso"] and l["published_iso"] >= since]
        # The newest invitation per meeting type: an amendment or a second
        # call replaces the one before it.
        newest = {}
        for invite in invites:
            kind = INVITATIONS[invite["letter_code"]][0]
            newest[kind] = invite
        for kind, invite in newest.items():
            later = [l for l in items
                     if l["published_at"] > invite["published_at"]]
            if any(_decision_closes(invite, l) for l in later):
                continue
            if any(l["letter_code"] == CANCELLATION for l in later):
                continue
            start = (date.fromisoformat(invite["published_iso"])
                     + timedelta(days=INVITE_MIN_DAYS)).isoformat()
            end = (date.fromisoformat(invite["published_iso"])
                   + timedelta(days=INVITE_MAX_DAYS)).isoformat()
            held = [e for e in dividends.get(code, [])
                    if e["date"] >= invite["published_iso"]]
            status = ("expected" if today.isoformat() <= end
                      else "decision_not_seen")
            out.append({
                "symbol": invite["symbol"] or symbols.get(code),
                "ins_code": code, "meeting_type": kind,
                "meeting_type_fa": MEETING_TYPES[kind],
                "fiscal_year_end": invite["fiscal_year_end"],
                "invitation": _ref(invite),
                "expected_from": _dated(start), "expected_to": _dated(end),
                "typical_date": _dated((date.fromisoformat(
                    invite["published_iso"]) + timedelta(
                        days=TYPICAL_LAG_DAYS[kind])).isoformat()),
                "typical_date_basis": "invitation + measured median lag",
                "status": status,
                "status_fa": ("در انتظار برگزاری" if status == "expected"
                              else "تصمیمات هنوز دیده نشده"),
                "exchange_dividend_event": (
                    {"date": _dated(held[0]["date"]), "dps": held[0]["dps"]}
                    if held and kind != "extraordinary" else None),
                "halt_note": HALT_NOTE_FA,
            })
    out.sort(key=lambda i: (i["expected_from"]["iso"], i["symbol"] or ""))
    return out


def _schedule_after(items, iso):
    found = [l for l in items if l["letter_code"] == PAYMENT_SCHEDULE
             and l["published_iso"] and l["published_iso"] >= iso]
    return _ref(found[-1]) if found else None


def _recent_decisions(by_company, today, back_days, symbols, dividends,
                      codal_by_code, codal_by_tracing):
    since = (today - timedelta(days=back_days)).isoformat()
    until = today.isoformat()
    out = []
    covered = set()
    for code, items in by_company.items():
        groups = {}
        for letter in items:
            if letter["letter_code"] not in AGM_DECISIONS:
                continue
            if not letter["published_iso"] or not (
                    since <= letter["published_iso"] <= until):
                continue
            if "تنفس" in (letter["title"] or ""):
                continue                    # a recess decides nothing
            kind = meeting_kind(letter)
            if kind == "extraordinary":
                continue                    # an EGM: see capital_increases
            key = (kind, letter["fiscal_year_end"]
                   or letter["published_iso"][:7])
            groups.setdefault(key, []).append(letter)
        # Each exchange dividend event belongs to one meeting: the one whose
        # Codal DPS agrees with it, else the newest fiscal year (قتربت
        # 1405/08: an amendment for FY1404 and the FY1405 AGM in one month).
        entries = []
        for (kind, fiscal), group in groups.items():
            group.sort(key=lambda l: (l["letter_code"] != "ن-52",
                                      l["letter_code"] != "ن-55",
                                      l["published_at"]))
            main = group[0]
            published = min(l["published_iso"] for l in group)
            codal = None
            for letter in group:
                codal = codal or codal_by_tracing.get(letter["tracing_no"])
            if codal is None and main["fiscal_year_end"]:
                codal = next((r for r in codal_by_code.get(code, [])
                              if r["fiscal_year_end"]
                              == main["fiscal_year_end"]), None)
            entries.append([kind, group, main, published, codal,
                            _exchange_match(dividends.get(code, []),
                                            published)])
        claimed = {}
        for n, (_k, _g, main, published, codal, event) in enumerate(entries):
            if not event:
                continue
            rank = (bool(codal and corporate_actions.dps_agrees(
                        event["dps"], codal["dps"])),
                    main["fiscal_year_end"] or "", published)
            best = claimed.get(event["date"])
            if best is None or rank > best[0]:
                claimed[event["date"]] = (rank, n)
        for n, (kind, group, main, published, codal, event) in \
                enumerate(entries):
            if event and claimed[event["date"]][1] != n:
                event = None
            if event:
                covered.add((code, event["date"]))
            meeting = event["date"] if event else published
            out.append({
                "symbol": main["symbol"] or symbols.get(code),
                "ins_code": code, "meeting_type": kind,
                "meeting_type_fa": MEETING_TYPES[kind],
                "fiscal_year_end": main["fiscal_year_end"],
                "decision": _ref(main),
                "other_letters": [_ref(l) for l in group[1:]],
                "meeting_date": _dated(meeting),
                "meeting_date_basis": ("exchange_event" if event
                                       else "letter_published_approx"),
                "dps_codal": codal["dps"] if codal else None,
                "dps_exchange": event["dps"] if event else None,
                "dps_agree": (corporate_actions.dps_agrees(event["dps"],
                                                           codal["dps"])
                              if event and codal and codal["dps"] is not None
                              else None),
                "payment_deadline": (payment_deadline(meeting)
                                     if (codal and codal["dps"]) or event
                                     else None),
                "payment_schedule": _schedule_after(items, published),
                "source": "codal",
            })
    # The exchange's dividend events whose decision letter is not stored.
    for code, evs in dividends.items():
        for event in evs:
            if not (since <= event["date"] <= until):
                continue
            if (code, event["date"]) in covered:
                continue
            out.append({
                "symbol": symbols.get(code), "ins_code": code,
                "meeting_type": "annual",
                "meeting_type_fa": MEETING_TYPES["annual"],
                "fiscal_year_end": None, "decision": None,
                "other_letters": [],
                "meeting_date": _dated(event["date"]),
                "meeting_date_basis": "exchange_event",
                "dps_codal": None, "dps_exchange": event["dps"],
                "dps_agree": None,
                "payment_deadline": payment_deadline(event["date"]),
                "payment_schedule": _schedule_after(
                    by_company.get(code, []), event["date"]),
                "source": "exchange",
            })
    out.sort(key=lambda i: (i["meeting_date"]["iso"], i["symbol"] or ""),
             reverse=True)
    return out


def _exchange_match(events, published_iso):
    """The exchange dividend event dated nearest a decision letter."""

    low, high = corporate_actions.CODAL_MATCH_DAYS
    best = None
    for event in events:
        offset = corporate_actions._days(event["date"], published_iso)
        if low <= offset <= high and (best is None
                                      or abs(offset) < abs(best[0])):
            best = (offset, event)
    return best[1] if best else None


def _payment_deadlines(today, ahead_days, symbols, dividends, by_company):
    start, end = today.isoformat(), (today + timedelta(days=ahead_days)) \
        .isoformat()
    out = []
    for code, evs in dividends.items():
        for event in evs:
            if not event["dps"] or event["dps"] <= 0:
                continue
            deadline = payment_deadline(event["date"])
            if not deadline or not (start <= deadline["iso"] <= end):
                continue
            out.append({"symbol": symbols.get(code), "ins_code": code,
                        "meeting_date": _dated(event["date"]),
                        "dps_exchange": event["dps"],
                        "payment_deadline": deadline,
                        "payment_schedule": _schedule_after(
                            by_company.get(code, []), event["date"]),
                        "source": "exchange"})
    out.sort(key=lambda i: (i["payment_deadline"]["iso"], i["symbol"] or ""))
    return out


def _capital(by_company, today, back_days, symbols, shares):
    since = (today - timedelta(days=CAPITAL_LOOKBACK_DAYS)).isoformat()
    recent = (today - timedelta(days=back_days)).isoformat()
    stale = (today - timedelta(days=CAPITAL_STALE_DAYS)).isoformat()
    out = []
    for code, items in by_company.items():
        steps = [l for l in items if l["letter_code"] in CAPITAL_CODES
                 and l["published_iso"] and l["published_iso"] >= since]
        if not steps:
            continue
        # Split into processes: one starts at a board proposal (or a
        # delegated board decision) and ends at its registration.
        processes, current = [], []
        for letter in steps:
            code_ = letter["letter_code"]
            if current and current[-1]["letter_code"] == REGISTRATION and \
                    code_ != REGISTRATION:
                processes.append(current)
                current = []
            if code_ == "ن-60" and current and any(
                    l["letter_code"] in ("ن-57", "ن-62", "ن-64", "ن-65",
                                         "ن-66") for l in current):
                processes.append(current)
                current = []
            current.append(letter)
        if current:
            processes.append(current)
        for process in processes:
            # An EGM's letters alone (ن-56/ن-57) may concern anything; they
            # are listed as meetings, not as an increase.
            started = any(l["letter_code"] not in ("ن-56", "ن-57")
                          for l in process)
            if not started:
                continue            # a lone EGM letter: listed as a meeting
            last = process[-1]
            registered = [l for l in process
                          if l["letter_code"] == REGISTRATION]
            start_iso = process[0]["published_iso"]
            # The exchange's new share count closes a process only once the
            # increase was decided (EGM or the board under delegation): a
            # count registered before that belongs to an earlier increase.
            decided = [l["published_iso"] for l in process
                       if l["letter_code"] in ("ن-57", "ن-70", "ن-73")]
            exchange = [s for s in shares.get(code, [])
                        if decided and s["date"] >= min(decided)
                        and s["ratio"] and s["ratio"] > 1.0]
            done = bool(registered) or bool(exchange)
            if done:
                finished = max([l["published_iso"] for l in registered]
                               + [s["date"] for s in exchange])
                if finished < recent:
                    continue
                state = "completed_recently"
            else:
                state = ("in_progress" if last["published_iso"] >= stale
                         else "stale")
            out.append({
                "symbol": process[0]["symbol"] or symbols.get(code),
                "ins_code": code,
                "status": state,
                "status_fa": {"in_progress": "در جریان",
                              "stale": "بدون پیشرفت در ۱۲۰ روز اخیر",
                              "completed_recently": "اخیراً ثبت شده"}[state],
                "latest_stage": {
                    "letter_code": last["letter_code"],
                    "label_fa": STAGES[last["letter_code"]],
                    "date": _dated(last["published_iso"]),
                    "tracing_no": last["tracing_no"], "url": last["url"]},
                "started": _dated(start_iso),
                "stages": [dict(_ref(l), label_fa=STAGES[l["letter_code"]])
                           for l in process],
                "exchange_share_change": (
                    {"date": _dated(exchange[-1]["date"]),
                     "label_fa": EXCHANGE_STAGE,
                     "ratio": round(exchange[-1]["ratio"], 4),
                     "shares_new": exchange[-1]["shares_new"]}
                    if exchange else None),
            })
    order = {"in_progress": 0, "completed_recently": 1, "stale": 2}
    out.sort(key=lambda i: (order[i["status"]],
                            -int(i["latest_stage"]["date"]["iso"]
                                 .replace("-", "")),
                            i["symbol"] or ""))
    return out


def calendar(today=None, back_days=45, ahead_days=60):
    """The AGM / capital-increase calendar as of `today`.

    Returns {as_of, as_of_jalali, window, note, note_en, rules, coverage,
    pending_meetings, recent_decisions, payment_deadlines,
    capital_increases}. Every item carries symbol, ins_code, ISO and Jalali
    dates, and the source letter's tracing_no/url when one is stored.
    Nothing published after `today` is read.
    """

    today = _day(today)
    horizon = max(back_days, PENDING_LOOKBACK_DAYS, CAPITAL_LOOKBACK_DAYS)
    stored = letters(since_jalali=_jalali_text(today - timedelta(
        days=horizon + 30)), until_jalali=_jalali_text(today))
    by_company = _by_company(stored)
    symbols = _symbols()
    dividends = {code: [e for e in evs if e["date"] <= today.isoformat()]
                 for code, evs in _exchange_events().items()}
    shares = {code: [s for s in rows if s["date"] <= today.isoformat()]
              for code, rows in _share_changes().items()}
    codal_by_code, codal_by_tracing = _codal_dividends()
    return {
        "as_of": today.isoformat(), "as_of_jalali": _jalali_text(today),
        "window": {"back_days": back_days, "ahead_days": ahead_days,
                   "from": _dated((today - timedelta(days=back_days))
                                  .isoformat()),
                   "to": _dated((today + timedelta(days=ahead_days))
                                .isoformat())},
        "note": NOTE_FA, "note_en": NOTE_EN,
        "rules": RULES,
        "coverage": coverage(today),
        "pending_meetings": _pending(by_company, today, symbols, dividends),
        "recent_decisions": _recent_decisions(
            by_company, today, back_days, symbols, dividends, codal_by_code,
            codal_by_tracing),
        "payment_deadlines": _payment_deadlines(today, ahead_days, symbols,
                                                dividends, by_company),
        "capital_increases": _capital(by_company, today, back_days, symbols,
                                      shares),
    }


def measure_lags():
    """Days from an invitation to the decision that closed it, by meeting
    type, over every stored pair -- the check on the 10-40 day rule."""

    by_company = _by_company(letters())
    lags = {}
    for items in by_company.values():
        for i, invite in enumerate(items):
            if invite["letter_code"] not in INVITATIONS or \
                    not invite["published_iso"]:
                continue
            for letter in items[i + 1:]:
                if letter["letter_code"] == CANCELLATION:
                    break
                if letter["letter_code"] in INVITATIONS and \
                        INVITATIONS[letter["letter_code"]][0] == \
                        INVITATIONS[invite["letter_code"]][0]:
                    break           # superseded by a newer invitation
                if _decision_closes(invite, letter) and \
                        letter["published_iso"]:
                    kind = INVITATIONS[invite["letter_code"]][0]
                    lags.setdefault(kind, []).append(corporate_actions._days(
                        invite["published_iso"], letter["published_iso"]))
                    break
    return {kind: dict(corporate_actions._percentiles(values),
                       pairs=len(values),
                       within_10_40=sum(1 for v in values
                                        if INVITE_MIN_DAYS <= v
                                        <= INVITE_MAX_DAYS + 5))
            for kind, values in lags.items()}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--today")
    parser.add_argument("--back", type=int, default=45)
    parser.add_argument("--ahead", type=int, default=60)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--lags", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.coverage:
        print(json.dumps(coverage(args.today), ensure_ascii=False, indent=1))
        return 0
    if args.lags:
        print(json.dumps(measure_lags(), ensure_ascii=False, indent=1))
        return 0
    result = calendar(args.today, args.back, args.ahead)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    print("calendar as of %s (%s)  -- %s" % (result["as_of"],
                                             result["as_of_jalali"],
                                             result["note_en"]))
    print("coverage: %s" % result["coverage"]["summary_en"])
    print("\npending meetings: %d" % len(result["pending_meetings"]))
    for item in result["pending_meetings"][:40]:
        print("  %-10s %-34s %s..%s  %s" % (
            item["symbol"], item["meeting_type_fa"],
            item["expected_from"]["jalali"], item["expected_to"]["jalali"],
            item["status"]))
    print("\nrecent decisions: %d" % len(result["recent_decisions"]))
    for item in result["recent_decisions"][:40]:
        deadline = item["payment_deadline"] or {}
        print("  %-10s %s  DPS codal %s exchange %s  deadline %s" % (
            item["symbol"], item["meeting_date"]["jalali"],
            item["dps_codal"], item["dps_exchange"], deadline.get("jalali")))
    print("\npayment deadlines in the next %d days: %d"
          % (args.ahead, len(result["payment_deadlines"])))
    for item in result["payment_deadlines"][:40]:
        print("  %-10s DPS %s due %s" % (item["symbol"], item["dps_exchange"],
                                        item["payment_deadline"]["jalali"]))
    print("\ncapital increases: %d" % len(result["capital_increases"]))
    for item in result["capital_increases"][:60]:
        print("  %-10s %-18s %s  %s" % (
            item["symbol"], item["status"],
            item["latest_stage"]["date"]["jalali"],
            item["latest_stage"]["label_fa"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
