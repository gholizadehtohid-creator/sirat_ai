"""Codal monthly activity reports (ن-۳۰): production, sales, purchases, energy.

WHY THIS EXISTS
---------------
A quarterly statement arrives one to four months after the quarter ends. The
monthly report arrives about ten days after the MONTH ends and says, product
by product, how much was produced and sold, at what price, for how much --
plus what the company bought (raw materials), what its energy cost, and in
some templates its own estimate for the rest of the year. It is the most
current hard number a listed company publishes, and the forward estimate in
fundamental_outlook.py uses it in place of the stale quarterly YTD when it
is newer (requested 2026-11-05).

TEMPLATES, AND WHY COLUMNS ARE READ BY MEANING
---------------------------------------------
Measured on 2026-11-05 samples (فملی, خودرو, کچاد, وبملت, اخابر):

  manufacturer   sales table (per product: تعداد تولید / تعداد فروش /
                 نرخ فروش / مبلغ فروش) in column groups "since the start of
                 the year to the previous month", corrections, the month,
                 "since the start of the year to this month", the same date
                 last year; then raw-material purchases, energy, FX.
  bank           single-level tables of realised income (درآمد محقق شده)
                 and expense (هزینه محقق شده) to the previous month, the
                 month, and to this month.
  services       recognised revenue (درآمد شناسایی شده) per contract.

The group a column belongs to is decided from its header text and the
report's own period end -- never from its position, which differs by
template and by year.

Amounts are in million rials on every template seen; they are stored in
rials. Quantities and per-unit prices are stored as filed.
"""

import re

import persian

AMOUNT_SCALE = 1_000_000

GROUP_MONTH = "month"
GROUP_YTD = "ytd"
GROUP_YTD_PREV = "ytd_prev"
GROUP_FORECAST = "forecast"

_DATE = re.compile(r"(1[34]\d\d)\s*/\s*(\d{1,2})(?:\s*/\s*(\d{1,2}))?")


def _norm(text):
    return persian.normalize(str(text or "")).replace("‌", " ")


def _flat(text):
    return re.sub(r"\s+", "", _norm(text))


def period_end_of(title):
    """(yyyy, mm, dd) from 'گزارش فعالیت ماهانه ... منتهی به 1405/07/30'."""

    match = _DATE.search(_norm(title).split("منتهی")[-1])
    if not match:
        return None
    y, m, d = match.groups()
    return int(y), int(m), int(d or 0)


def _header(col):
    parts = col if isinstance(col, tuple) else (col,)
    seen = []
    for part in parts:
        text = _norm(part)
        if text and not text.lower().startswith("unnamed") and text not in seen:
            seen.append(text)
    return " || ".join(seen)


def _has(flat, *words):
    """True when any word occurs in an already-flattened text.

    Words go through the same folding as the text: persian.normalize turns
    آ into ا, so a literal "درآمد" never matches a normalised header (the
    bank template read as having no income until this existed).
    """

    return any(_flat(word) in flat for word in words)


def classify_group(header, period):
    """Which period a column reports, or None for one to ignore."""

    flat = _flat(header)

    if _has(flat, "اصلاح"):
        return None
    if _has(flat, "پیش بینی", "برآورد"):
        return GROUP_FORECAST
    if _has(flat, "یکماهه", "طی دوره"):
        return GROUP_MONTH

    dates = [(int(y), int(m)) for y, m, _d in _DATE.findall(_norm(header))]
    if not dates or not period:
        return None
    y, m = dates[-1]

    if (y, m) == (period[0], period[1]):
        if _has(flat, "ابتدای", "تا پایان", "جمع"):
            return GROUP_YTD
        return None
    if (y, m) == (period[0] - 1, period[1]):
        return GROUP_YTD_PREV
    return None              # e.g. "since the start of the year to LAST month"


def classify_measure(header):
    flat = _flat(header)
    if _has(flat, "تعداد تولید"):
        return "production"
    if _has(flat, "تعداد فروش"):
        return "sales_qty"
    if _has(flat, "مقدار"):
        return "qty"
    if _has(flat, "نرخ"):
        return "rate"
    if _has(flat, "درصد"):
        return None
    # Insurance: premiums and claims share a column group, so claims need
    # their own measure or the first "مبلغ" in the group swallows both.
    if _has(flat, "خسارت"):
        return "claims"
    if _has(flat, "مبلغ", "درآمد", "هزینه", "سود", "حق بیمه"):
        return "amount"
    return None


def table_kind(frame):
    headers = " ".join(_flat(_header(c)) for c in frame.columns)
    labels = " ".join(_flat(v) for v in frame.iloc[:, 0].tolist())

    if _has(headers, "منابع ارزی", "نوع ارز"):
        return "fx"
    if _has(headers, "حق بیمه"):
        return "insurance"
    if _has(headers, "نوع انرژی"):
        return "energy"
    if _has(headers, "مبلغ فروش"):
        return "sales"
    if _has(labels, "مواد اولیه") or (_has(headers, "خرید") and _has(headers, "مقدار")):
        return "purchases"
    if _has(headers, "بهای تمام شده"):
        return "cost"
    if _has(headers, "هزینه محقق شده"):
        return "expense"
    if _has(headers, "سود(زیان) محقق شده"):
        return "profit"
    if _has(headers, "درآمد محقق شده", "درآمد شناسایی", "درآمد شناساسی"):
        return "revenue"
    return None


def _number(cell):
    import codal
    return codal.parse_number(cell)


def read_table(frame, period):
    """{label: {group: {measure: value}}} and the column map that read it."""

    columns = []
    for index, col in enumerate(frame.columns):
        header = _header(col)
        group = classify_group(header, period)
        measure = classify_measure(header)
        if group and measure:
            columns.append((index, group, measure))

    rows = []
    for _, row in frame.iterrows():
        cells = list(row.values)
        label = _norm(cells[0]) if cells else ""
        if not label or label.lower() == "nan":
            continue
        unit = _norm(cells[1]) if len(cells) > 1 else ""
        values = {}
        for index, group, measure in columns:
            number = _number(cells[index])
            if number is None:
                continue
            values.setdefault(group, {})
            # First column of a measure in a group wins: some templates repeat
            # a header in a sub-total column further right.
            values[group].setdefault(measure, number)
        rows.append((label, unit, values))

    return rows, columns


def _is_total(label):
    return _flat(label).startswith("جمع")


def _grand_total(rows, prefer=("جمع", "جمعکل")):
    """The table's bottom line: an exact 'جمع' / 'جمع کل' row, or None."""

    by_flat = {}
    for label, _unit, values in rows:
        by_flat[_flat(label)] = values
    for name in prefer:
        if _flat(name) in by_flat:
            return by_flat[_flat(name)]
    return None


def _sum_rows(rows):
    """Sum of the non-total rows, for templates without a total line."""

    out = {}
    for label, _unit, values in rows:
        flat = _flat(label)
        if _is_total(label) or flat.startswith("سرفصل") or flat.endswith(":"):
            continue
        for group, measures in values.items():
            amount = measures.get("amount")
            if amount is not None:
                bucket = out.setdefault(group, {})
                bucket["amount"] = bucket.get("amount", 0.0) + amount
    return out


def _amounts(values):
    """{group: rials} from {group: {measure: value}}."""

    return {group: m["amount"] * AMOUNT_SCALE
            for group, m in (values or {}).items() if m.get("amount") is not None}


def _products(rows, limit=12):
    out = []
    for label, unit, values in rows:
        flat = _flat(label)
        if _is_total(label) or flat.endswith(":") or not values:
            continue
        item = {"name": label, "unit": unit}
        for group in (GROUP_MONTH, GROUP_YTD, GROUP_YTD_PREV):
            m = values.get(group) or {}
            item[group] = {
                "production": m.get("production"),
                "sales_qty": m.get("sales_qty") if "sales_qty" in m else m.get("qty"),
                "rate": m.get("rate"),
                "amount": m["amount"] * AMOUNT_SCALE
                if m.get("amount") is not None else None,
            }
        out.append(item)
    out.sort(key=lambda p: -abs((p[GROUP_YTD] or {}).get("amount") or 0.0))
    return out[:limit]


def parse(sheets, title):
    """One monthly report -> a record, or None when nothing was readable."""

    period = period_end_of(title)
    if not period or not sheets:
        return None

    frames = [frame for frames in sheets.values() for frame in frames
              if frame is not None and frame.shape[1] >= 3]

    record = {"period_end": "%04d/%02d/%02d" % period, "template": None,
              "sales": None, "purchases": None, "energy": None, "fx": None,
              "expense": None, "profit": None, "products": [],
              "purchase_items": []}

    for frame in frames:
        kind = table_kind(frame)
        if not kind or record.get(kind) is not None and kind != "revenue":
            continue

        rows, columns = read_table(frame, period)
        if not columns:
            continue

        if kind == "sales":
            total = _grand_total(rows)
            record["sales"] = _amounts(total or _sum_rows(rows))
            record["products"] = _products(rows)
            record["template"] = "manufacturer"
        elif kind in ("revenue",) and record["sales"] is None:
            total = _grand_total(rows)
            record["sales"] = _amounts(total or _sum_rows(rows))
            record["template"] = record["template"] or "income"
        elif kind == "insurance":
            total = _grand_total(rows) or {}
            record["sales"] = _amounts(total)
            record["expense"] = {g: m["claims"] * AMOUNT_SCALE
                                 for g, m in total.items()
                                 if m.get("claims") is not None}
            record["template"] = "insurance"
        elif kind == "purchases":
            total = _grand_total(rows, prefer=("جمعکل", "جمع"))
            record["purchases"] = _amounts(total or _sum_rows(rows))
            record["purchase_items"] = _products(rows, limit=8)
        elif kind == "energy":
            total = _grand_total(rows, prefer=("جمعکل", "جمع"))
            record["energy"] = _amounts(total or _sum_rows(rows))
        elif kind == "fx":
            total = _grand_total(rows, prefer=("جمعمنابعارزی",))
            record["fx"] = _amounts(total) if total else None
        elif kind in ("expense", "cost"):
            total = _grand_total(rows)
            record["expense"] = _amounts(total or _sum_rows(rows))
        elif kind == "profit":
            total = _grand_total(rows)
            record["profit"] = _amounts(total or _sum_rows(rows))

    if not record["sales"] and not record["purchases"] and not record["expense"]:
        return None

    return record
