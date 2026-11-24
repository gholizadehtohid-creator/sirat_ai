/* Dashboard wiring: read the controls, fetch the API, render charts + tables.

   The page has no per-symbol demo section any more. It used to open with five
   hard-coded shares in depth, which put the least representative numbers on the
   page in the most prominent position. Every view here is driven by the whole
   universe, and the four analyses the user reads in sequence -- شاخص کل,
   شاخص کل هم‌وزن, تحلیل بنیادی صنایع, تحلیل تکنیکال صنایع -- each own a
   section rather than sharing one card. */

import {
  hBarChart, stackedHBar, lineChart, divergingBar,
  seriesColors, cssVar, fmt, hideTip
} from "./charts.js";

/* Reference-line styling, in one place.

   A support line, an RSI 70 band and a MACD zero line are not data series --
   they are the ruler the data is read against, and they must not look like a
   sixth indicator. Thin, dashed, and coloured by MEANING rather than by
   palette slot: resistance is the same red as the resistance badge, support
   the same green, everything neutral recedes to the axis colour. Resolved
   through cssVar() because a CSS var() is not honoured inside an SVG
   presentation attribute. */
const refLine = (color, dash = "5 5") =>
  ({ color: cssVar(color), dash, width: 1.25, ref: true });

const RESISTANCE_LINE = () => refLine("--status-critical");
const SUPPORT_LINE = () => refLine("--status-good");
const GUIDE_LINE = (dash) => refLine("--axis", dash);

const $ = (id) => document.getElementById(id);

// Wiring goes through here rather than $(id).addEventListener, because this
// module is one scope: a single TypeError on a missing element stops the rest
// of the file dead -- no handlers bound, no load, and a page frozen on
// "Loading data status…" with every table empty. That happens for real whenever
// the served HTML is older than this file, which is exactly what a cached
// template gives you. A missing node should cost its own feature, nothing else.
function on(id, event, handler) {
  const node = $(id);

  if (!node) {
    console.warn(`panel: #${id} is not on this page — its ${event} handler was skipped. ` +
      `The HTML is probably older than panel.js; restart the panel.`);
    return;
  }

  node.addEventListener(event, handler);
}

const COMPONENTS = ["Trend", "MACD", "RSI", "Volume", "Momentum", "Risk"];

const FUND_COMPONENTS = ["Valuation", "Profitability", "Liquidity", "Size", "Stability"];

// Status band -> status token. Reserved for state, never reused as a series hue.
const STATUS_CLASS = {
  // No green on a stock-level reading: no ranking here beat holding the
  // index (E19, E26, E30-E33), and green over "buy" reads as an order.
  "STRONG BUY CANDIDATE": "neutral",
  "BUY WATCHLIST": "neutral",
  "WATCHLIST": "warning",
  "HOLD / WAIT": "serious",
  "NO ENTRY": "critical",
  "NO TRADE": "neutral",
  "NO DATA": "neutral"
};

const REGIME_CLASS = {
  BULL: "good",
  SIDEWAYS: "warning",
  BEAR: "critical"
};

const PERIOD_FIELDS = {
  ma_fast: "maFast",
  ma_slow: "maSlow",
  rsi: "rsiWin",
  macd_fast: "macdFast",
  macd_slow: "macdSlow",
  macd_signal: "macdSignal",
  volume_ma: "volumeMa"
};

const DEFAULT_PERIODS = {};

const state = {
  screen: null, indices: null, sectors: null,
  regime: null, portfolio: null, flow: null,
  fundamentals: null, codal: null, technical: null, topTechnical: null,
  gold: null, inflow: null, intel: null, smartMoney: null, allocation: null,
  marketflow: null
};

// rerender() repaints each section's banner from its analysis, which an import
// failure leaves valid -- so the failure was wiped by the next tab switch. It
// is kept here, laid back over the banner after every rerender, and dropped
// when the same import is tried again.
const importErrors = {};

/* ---------- helpers ---------- */

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json();

  if (!res.ok || body.error) {
    const message = body.error || `HTTP ${res.status}`;

    // A route this page knows about that the server has never heard of means
    // the running process loaded its Python before that route existed. It goes
    // on serving the current template and this very file, so the section
    // renders and then fails -- indistinguishable from a broken feature unless
    // the message says otherwise. (Windows lets a second `python app.py` bind
    // a port the first one already holds, so the stale copy is not obvious.)
    //
    // Matched on the PREFIX app.handle_error gives an unknown route -- werkzeug's
    // NotFound as "NotFound: 404 Not Found: ..." -- and on nothing looser. A bare
    // /NotFound|404/ also caught FileNotFoundError, ModuleNotFoundError and an
    // upstream "404 Client Error", and replaced each real cause with advice to
    // restart a panel that was not stale.
    if (/^NotFound: 404/.test(message)) {
      throw new Error(
        `این نسخه‌ی در حال اجرای پنل، مسیر ${url.split("?")[0]} را نمی‌شناسد — ` +
        `یعنی کدِ پایتونش قدیمی‌تر از این صفحه است. پنل را ببندید و دوباره ` +
        `اجرا کنید (python app.py).`
      );
    }

    // The code survives the throw so a caller can tell a switched-off
    // feature from a broken one without matching on message text -- message
    // text is written for a reader and is free to change.
    const err = new Error(message);
    err.code = body.code || null;
    err.status = res.status;
    throw err;
  }

  return body;
}

// One number format for the whole panel: Persian digits, the Persian
// thousands separator and decimal mark, from the browser itself. The digits
// used to come from B Yekan, a font only this PC has: on a phone the page
// mixed "902" and "26%" with "۷۰۰,۰۰۰,۰۰۰", and prices had no grouping at
// all ("۱۰۲۱۷۴۲") -- found 2026-11-11.
const FA_NUMBER = {};

function faNumber(digits) {
  if (!FA_NUMBER[digits]) {
    try {
      FA_NUMBER[digits] = new Intl.NumberFormat("fa-IR", {
        minimumFractionDigits: digits, maximumFractionDigits: digits });
    } catch (_err) {
      FA_NUMBER[digits] = { format: x => Number(x).toFixed(digits) };
    }
  }
  return FA_NUMBER[digits];
}

function num(v, digits = 2) {
  if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return "—";
  return faNumber(digits).format(Number(v));
}

// A server number shown at the precision it was sent with, in the panel's
// one format. The basket's risk card printed them raw -- "1.565" and
// "21.41" beside "۲۱۸٫۷۸" (v3 design, 2026-11-23).
function numAsSent(v) {
  if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return "—";
  const text = String(v);
  const digits = /\.(\d+)$/.test(text) ? Math.min(text.split(".")[1].length, 4) : 0;
  return num(v, digits);
}

// The engines' status codes are English; the reader is not. Shown in
// Persian, with the code kept in the tooltip for anyone checking the logs.
const STATUS_FA = {
  // Descriptions of a score, not orders (the review panel, 2026-11-11).
  "STRONG BUY CANDIDATE": "امتیاز بالا",
  "BUY WATCHLIST": "امتیاز خوب",
  "WATCHLIST": "امتیاز متوسط",
  "HOLD / WAIT": "امتیاز پایین",
  "NO ENTRY": "امتیاز ضعیف",
  "NO TRADE": "بدون معامله",
  "NO DATA": "بدون داده",
  "BULL": "صعودی", "BEAR": "نزولی", "SIDEWAYS": "خنثی",
  "Strong": "قوی", "Moderate": "متوسط", "Weak": "ضعیف",
  "APPROVED": "از غربال گذشت", "PROVISIONAL": "مشروط", "REJECTED": "رد شد",
  "BUY": "مثبت", "SELL": "منفی", "HOLD": "خنثی", "AVOID": "پرهیز",
};

// The session collector's tick outcomes (monitor.py), as the live tab says them.
const TICK_FA = {
  trading: "در جریان", frozen: "بی‌تغییر", closed: "بسته",
  unreadable: "خوانده نشد", error: "خطا",
};

const VOL_FA = {
  LOW_VOLATILITY: "نوسان کم", NORMAL_VOLATILITY: "نوسان عادی",
  HIGH_VOLATILITY: "نوسان بالا", EXTREME_VOLATILITY: "نوسان بسیار بالا",
};

function votesText(rep) {
  // regime.py counts the rules' votes per side: {"bull": 3, "bear": 0, ...}.
  const votes = rep.votes || {};
  const counts = Object.values(votes).filter(v => typeof v === "number");
  const total = counts.reduce((a, b) => a + b, 0);
  const agree = votes[String(rep.regime || "").toLowerCase()];
  if (total > 0 && typeof agree === "number") return `${num(agree, 0)} از ${num(total, 0)}`;
  return rep.confidence == null ? "—" : `${num(rep.confidence * 100, 0)}٪`;
}

function badge(status, map = STATUS_CLASS) {
  const label = STATUS_FA[status] || status;
  return `<span class="badge ${map[status] || "neutral"}" title="${esc(String(status ?? ""))}">` +
         `<span class="dot"></span>${esc(String(label ?? ""))}</span>`;
}

function signed(v, digits = 2, suffix = "٪") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const cls = v > 0 ? "up" : v < 0 ? "down" : "";
  const body = num(Math.abs(v), digits);
  return `<span class="${cls}">${v > 0 ? "+" : v < 0 ? "−" : ""}${body}${suffix === "%" ? "٪" : suffix}</span>`;
}

// Rial amounts run to billions; abbreviate but keep the sign meaningful.
// A trillion tier is not decorative here: whole-market turnover is quadrillions
// of rials, and without it the money-flow totals render as "1189993.49B".
// In Persian units ("۲۰٫۵ میلیارد") rather than Latin K/M/B/T beside
// Persian text.
function money(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e12) return `${sign}${num(a / 1e12, 2)} هزار میلیارد`;
  if (a >= 1e9) return `${sign}${num(a / 1e9, 2)} میلیارد`;
  if (a >= 1e6) return `${sign}${num(a / 1e6, 1)} میلیون`;
  if (a >= 1e3) return `${sign}${num(a / 1e3, 0)} هزار`;
  return `${sign}${num(a, 0)}`;
}

// The currency code the engines send, as the reader says it.
function currencyName(code) {
  return code === "IRR" || !code ? "ریال" : code;
}

function table(headers, rows) {
  return `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function tiles(node, items) {
  $(node).innerHTML = items.map(t => `
    <div class="tile">
      <div class="k">${t.k}</div>
      <div class="v">${t.v}</div>
      <div class="sub">${t.sub}</div>
    </div>`).join("");
}

function setLoading(on) {
  document.querySelectorAll(".chart, .table-wrap").forEach(n =>
    n.classList.toggle("loading", on));
}

function banner(id, textId, kind, html) {
  const node = $(id);
  if (!node) return;
  // "is-idle" is waiting or loading: neutral, not the amber of a warning
  // (every banner used to start amber, so "در حال بارگذاری…" read as a fault).
  node.classList.remove("is-error", "is-live", "is-idle");
  if (kind) node.classList.add(kind);
  node.querySelector(".icon").textContent =
    kind === "is-live" ? "✓" : kind === "is-error" ? "✕" : kind === "is-idle" ? "…" : "⚠";
  $(textId).innerHTML = html;
}

// SIRAT_FULL_SCAN=0 switches off the two routes the screen and the basket
// are built from. That is a setting somebody chose, not a fault, and styling
// it as a crash sends the reader looking for a bug that does not exist. The
// server's own message carries the command that undoes it, so it is shown
// whole rather than summarised.
function scanLocked(err, statusId, textId, taskId, headline) {
  if (err?.code !== "full_scan_disabled") return false;

  banner(statusId, textId, "", `<strong>${esc(headline)}</strong> ${esc(err.message)}`);
  setTask(taskId, "warn", "اسکن کامل بازار خاموش است (SIRAT_FULL_SCAN=0)");
  return true;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function bulletList(id, items) {
  const node = $(id);
  if (!node) return;
  node.innerHTML = (items || []).length
    ? items.map(t => `<li>${esc(t)}</li>`).join("")
    : "";
}

/* ---------- control state ---------- */

// ?engine=v1|v2 picks the decision engine that builds the book, the same way
// ?theme= overrides the stored theme. Read from the PAGE url and forwarded to
// every API call, so one page never mixes a v1 book with a v2 anything. With no
// parameter the server uses config.DECISION_ENGINE.
const urlEngine = new URLSearchParams(location.search).get("engine");

function query(extra = {}) {
  const p = new URLSearchParams();

  if (urlEngine) p.set("engine", urlEngine);

  const equity = $("equityInput")?.value;
  if (equity) p.set("equity", equity);

  const size = $("sizeInput")?.value;
  if (size) p.set("size", size);

  const cap = $("sectorCapInput")?.value;
  if (cap) p.set("max_per_sector", cap);

  const minScore = $("minScoreInput")?.value;
  if (minScore) p.set("min_score", minScore);

  const risk = $("riskInput")?.value;
  if (risk) p.set("risk", risk);

  for (const [key, id] of Object.entries(PERIOD_FIELDS)) {
    const v = $(id)?.value;
    if (v) p.set(key, v);
  }

  for (const [k, v] of Object.entries(extra)) p.set(k, v);

  return p.toString();
}

/* ---------- provenance banner ----------

   Data authenticity outranks every other message. If the cached bars are not
   exchange data, nothing computed from them means anything about the real
   market, and saying so is more important than reporting freshness. */

function renderBanner(rep) {
  const b = $("banner");
  const t = $("bannerText");

  b.classList.remove("is-live", "is-error", "is-idle");

  const prov = rep && rep.provenance;

  if (prov && prov.status !== "verified-real") {
    b.classList.add("is-error");
    b.querySelector(".icon").textContent = prov.status === "simulated" ? "⛔" : "⚠";
    // Simulated and unverified are different claims. The panel's own fetch
    // records no provenance, so an install whose bars all came from TSETMC
    // reads UNVERIFIED -- and calling those bars "simulated" would be false.
    // The detail is shown only when it was recorded by a check (checked_at);
    // the default one is an English placeholder.
    t.innerHTML = prov.status === "simulated"
      ? `<strong>این قیمت‌ها شبیه‌سازی‌شده‌اند، نه داده‌ی واقعی بازار.</strong> ` +
        `${prov.checked_at ? esc(prov.detail || "") + " " : ""}` +
        `هر امتیاز و هر پیشنهاد سبد در این صفحه همین سری شبیه‌سازی‌شده را توصیف ` +
        `می‌کند — نه بورس تهران را. برای بررسی کامل: <code>python authenticity.py</code>`
      : `<strong>منشأ این قیمت‌ها تأیید نشده است.</strong> ` +
        `${prov.checked_at ? esc(prov.detail || "") + " " : ""}` +
        `یعنی هنوز بررسی نشده که داده‌ی واقعی بورس است — نه اینکه ساختگی باشد. ` +
        `برای بررسی: <code>python authenticity.py</code>`;
    return;
  }

  const counts = (rep && rep.counts) || {};

  if (!counts.universe) {
    b.classList.add("is-error");
    b.querySelector(".icon").textContent = "✕";
    t.innerHTML = `<strong>هیچ نمادی بارگذاری نشده.</strong> دکمه‌ی «ورود نمادها» را بزنید.`;
    return;
  }

  b.classList.add("is-live");
  b.querySelector(".icon").textContent = "✓";

  const snap = rep.snapshot || {};

  t.innerHTML = `<strong>داده‌ی ذخیره‌شده‌ی واقعی.</strong> ` +
    `${counts.universe} نماد، ${rep.have_history || 0} نماد دارای سابقه‌ی قیمت` +
    (snap.rows ? `، تصویر بنیادی ${num(snap.rows, 0)} سطر (${faTime(snap.captured_at)})`
               : `، بدون تصویر بنیادی — EPS و P/E در دسترس نیست`) +
    `. برای دریافت مجدد «به‌روزرسانی داده‌ها» را بزنید.`;
}

/* ---------- market regime ---------- */

function renderRegime(rep) {
  state.regime = rep;

  // Under the v2 engine this payload is regime_engine.assess(): the same keys
  // plus the trading mode it derives -- and the mode, not the vote, is what
  // sizes and gates the book. v1 sends no mode_label, and its banner stays
  // word for word what it was. Unavailable carries a mode too (cash only, no
  // new entries), which is worth saying beside the reason.
  const mode = rep?.mode_label ? ` حالت معاملاتی: ${esc(rep.mode_label)}.` : "";

  if (!rep || !rep.available) {
    banner("regimeStatus", "regimeStatusText", "is-error",
      `<strong>وضعیت بازار قابل تشخیص نیست.</strong> ${esc(rep?.reason || "")}` + mode);
    tiles("regimeKpis", []);
    $("participationChart").innerHTML = '<div class="empty">داده‌ای نیست</div>';
    bulletList("regimeEvidence", []);
    return;
  }

  // A description, not an instruction (the review panel, 2026-11-11). It
  // used to say "investment ceiling set to X%" and show a cash target -- 60%
  // in a bear vote -- while E27 found timing the market on these very rules
  // did not pay and the allocation card holds a fixed mix: in a fall it told
  // the reader to sell near the bottom. The ceiling still sizes the paper
  // basket, and that card says so.
  banner("regimeStatus", "regimeStatusText", "is-idle",
    `<strong>دمای بازار: ${esc(rep.label)}</strong> — رأی‌گیری ${rep.votes.bull} صعودی، ` +
    `${rep.votes.bear} نزولی، ${rep.votes.sideways} خنثی. ` +
    `<strong>این فقط توصیف است، نه دستور:</strong> وزن سهام شما را فقط کارت تخصیص تعیین می‌کند. ` +
    `زمان‌بندی ورود و خروج با همین قواعد در آزمون E27 سودی نداشت.`);

  const cap = rep.cap_weighted || {};
  const ew = rep.equal_weighted || {};
  const part = rep.participation || {};

  tiles("regimeKpis", [
    {
      k: "دمای بازار",
      v: badge(rep.regime, REGIME_CLASS),
      sub: VOL_FA[rep.volatility_regime] || rep.volatility_regime || "—"
    },
    // The votes themselves, not "confidence 100%": three rules agreeing is a
    // count, and E27 found that timing the market on such rules did not pay
    // (the 20-year reviewer's point, 2026-11-11).
    { k: "رأی‌ها", v: votesText(rep),
      sub: "توافق قواعد روند، نه احتمال؛ زمان‌بندی با این قواعد در آزمون سودی نداشت" },
    {
      k: "مشارکت بازار",
      v: part.above_slow_ma_pct != null ? `${part.above_slow_ma_pct}٪` : "—",
      sub: `از ${part.instruments || 0} نماد بالای MA${part.slow_ma || ""}`
    },
    {
      k: "شاخص کل ۶۰ روزه",
      v: signed(cap.return_60d),
      sub: cap.above_slow_ma ? "بالای میانگین کند" : "زیر میانگین کند"
    },
    {
      k: "هم‌وزن ۶۰ روزه",
      v: signed(ew.return_60d),
      sub: ew.above_slow_ma ? "بالای میانگین کند" : "زیر میانگین کند"
    }
  ]);

  if (part.above_slow_ma_pct != null) {
    hBarChart($("participationChart"), {
      data: [
        { label: `بالای MA${part.fast_ma}`, value: part.above_fast_ma_pct },
        { label: `بالای MA${part.slow_ma}`, value: part.above_slow_ma_pct }
      ],
      max: 100,
      format: (v) => `${fmt(v, 0)}٪`
    });
  } else {
    $("participationChart").innerHTML = '<div class="empty">مشارکت محاسبه نشد</div>';
  }

  bulletList("regimeEvidence", [...(rep.evidence || []), rep.caveat]);
}

async function loadRegime(force = false) {
  const btn = $("runRegimeBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تشخیص…"; }
  setTask("regime", "running", "خواندن شاخص‌ها و مشارکت بازار");

  try {
    const rep = await getJSON(`/api/regime?${query()}${force ? "&refresh=1" : ""}`);
    renderRegime(rep);
    setTask("regime", rep.available ? "done" : "failed",
      rep.available ? rep.label : (rep.reason || "نامشخص"),
      rep.available ? (STATUS_FA[rep.regime] || rep.regime) : "");
  } catch (err) {
    state.regime = null;
    banner("regimeStatus", "regimeStatusText", "is-error",
      `<strong>تشخیص وضعیت بازار شکست خورد.</strong> ${esc(err.message)}`);
    setTask("regime", "failed", err.message);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تشخیص"; }
  }
}

/* ---------- shared readers: one instrument OR one index ----------

   The equity panel and the two index cards show the same four things -- the
   indicator reading, the pivot levels, the transparent vote, and a ladder of
   levels. They are rendered by the helpers below rather than by three copies,
   because a support level that read one way on the share card and another way
   on the index card would be a bug nobody could see. Each helper takes an id
   PREFIX; every element it touches is `prefix + Suffix`, and a missing element
   is skipped rather than thrown on, so a card can opt out of any piece.
*/

// One element of a prefixed card. Returns null when the card does not have
// that piece, which is how a card opts out of a section.
function part(prefix, suffix) { return $(prefix + suffix); }

// One indicator reading -> the rows of the "خوانش اندیکاتورها" table. The
// single list means an indicator cannot be labelled or rounded differently in
// two places. A null indicator shows "—", never a fabricated zero.
function indicatorRows(L) {
  const n = (v, d = 2) => (v === null || v === undefined ? "—" : num(v, d));

  return [
    ["بسته", n(L.close, 0)],
    ["میانگین متحرک کوتاه", n(L.ma_fast, 0)],
    ["میانگین متحرک بلند", n(L.ma_slow, 0)],
    ["RSI", n(L.rsi, 1)],
    ["MACD", n(L.macd, 2)],
    ["خط سیگنال MACD", n(L.macd_signal, 2)],
    ["هیستوگرام MACD", n(L.macd_hist, 2)],
    ["باند بالای بولینگر", n(L.bb_upper, 0)],
    ["باند پایین بولینگر", n(L.bb_lower, 0)],
    ["%B بولینگر", n(L.bb_pct, 3)],
    ["پهنای باند بولینگر %", n(L.bb_width, 1)],
    ["ATR", n(L.atr, 0)],
    ["ATR %", n(L.atr_pct, 2)],
    ["ADX (قدرت روند)", n(L.adx, 1)],
    ["قدرت جهت صعودی (DI+)", n(L.di_plus, 1)],
    ["قدرت جهت نزولی (DI−)", n(L.di_minus, 1)],
    ["حجم", L.volume === null || L.volume === undefined ? "—" : money(L.volume)],
    ["میانگین حجم",
     L.volume_ma === null || L.volume_ma === undefined ? "—" : money(L.volume_ma)]
  ];
}

function renderReading(prefix, latest) {
  const node = part(prefix, "Reading");
  if (!node) return;
  node.innerHTML = latest
    ? table(["اندیکاتور", "مقدار"], indicatorRows(latest))
    : '<div class="empty">خوانشی در دسترس نیست</div>';
}

// Support and resistance from confirmed pivots. Resistance is red and support
// green throughout the dashboard; the target ladder below reuses the same two
// colours for the same two meanings.
function renderLevels(prefix, lv, digits = 0, valueHeader = "قیمت") {
  const node = part(prefix, "Levels");
  if (!node) return;

  const n = (v) => (v === null || v === undefined ? "—" : num(v, digits));

  // How many confirmed pivots formed the level. It is the only strength
  // measure price alone offers -- a shelf the market has turned at four times
  // is not the same object as one it touched once -- and it is shown as
  // "N بار" rather than a bare integer so it cannot be misread as a price.
  const touches = (x) => {
    const t = x.touches || 1;
    return t > 1
      ? `<strong>${t}</strong> بار`
      : `<span class="hint-inline">۱ بار</span>`;
  };

  const rows = [];

  (lv?.resistances || []).forEach(r =>
    rows.push([`<span class="badge bad"><span class="dot"></span>مقاومت</span>`,
               n(r.price), signed(r.distance_pct, 1), touches(r),
               esc(r.date || "—")]));

  (lv?.supports || []).forEach(s =>
    rows.push([`<span class="badge good"><span class="dot"></span>حمایت</span>`,
               n(s.price), signed(s.distance_pct, 1), touches(s),
               esc(s.date || "—")]));

  node.innerHTML = rows.length
    ? table(["نوع", valueHeader, "فاصله", "برخورد", "آخرین برخورد"], rows)
    : '<div class="empty">پیوت تأییدشده‌ای یافت نشد</div>';
}

// Support and resistance as flat reference lines across a price chart, and the
// one rule that makes them safe to draw: A LEVEL OFF THE CHART IS NOT DRAWN.
//
// The lines are added to the chart's own data, so the chart scales to include
// them. A pivot low 40% under the current price -- perfectly ordinary after a
// long advance -- therefore used to drag the axis down until the whole visible
// price series flattened into a band a few pixels tall, hiding the very thing
// the reader opened the chart to see. Levels outside the plotted range plus a
// margin are dropped here instead; they are still in the table beside the
// chart, with their exact price, which is where a level that far away belongs.
//
// Mutates `rows` (adding one constant column per drawn level) and returns the
// series descriptors for them.
const LEVEL_MARGIN = 0.25;

function levelSeries(rows, lv, perSide = 2) {
  const span = [];
  for (const row of rows) {
    for (const key of ["close", "bb_upper", "bb_lower"]) {
      const v = row[key];
      if (v !== null && v !== undefined && !Number.isNaN(v)) span.push(v);
    }
  }

  if (!span.length) return [];

  const lo = Math.min(...span), hi = Math.max(...span);
  const margin = (hi - lo) * LEVEL_MARGIN || Math.abs(hi) * 0.02;
  const visible = (p) =>
    p !== null && p !== undefined && p >= lo - margin && p <= hi + margin;

  const out = [];

  const add = (list, prefix, label, style) =>
    (list || []).filter(x => visible(x.price)).slice(0, perSide)
      .forEach((x, i) => {
        const key = `${prefix}${i}`;
        rows.forEach(row => { row[key] = x.price; });
        // Only the nearest of each kind is named: three identical dashed reds
        // stacked at the right edge is a legend nobody can read, and the table
        // below already names every one of them.
        out.push({ key, label: i === 0 ? label : "", ...style() });
      });

  add(lv?.resistances, "res", "مقاومت", RESISTANCE_LINE);
  add(lv?.supports, "sup", "حمایت", SUPPORT_LINE);

  return out;
}

// Volume under the price, coloured by the day's direction.
//
// A price chart with no volume beneath it cannot answer whether a move was
// paid for -- which is exactly the question the signal's volume vote answers
// in words, and the reader should be able to check that claim against the
// picture. The average is drawn over the columns as the line they are read
// against: "above average" is the whole point and is invisible without it.
function renderVolume(prefix, points) {
  const node = part(prefix, "VolumeChart");
  if (!node) return;

  if (!points.length) {
    node.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    return;
  }

  // A volume bar is always positive, so its own sign cannot colour it. What
  // the reader wants is whether that volume traded the price up or down.
  let previous = null;
  const rows = points.map(p => {
    const dir = (previous === null || p.close === null || p.close === undefined)
      ? 0 : p.close - previous;
    if (p.close !== null && p.close !== undefined) previous = p.close;
    return { ...p, dir };
  });

  lineChart(node, {
    data: rows, xKey: "date", height: 130,
    bars: { key: "volume", label: "حجم", signKey: "dir",
            posColor: cssVar("--status-good"),
            negColor: cssVar("--status-critical") },
    // Not a ref line: unlike an RSI 70 band this one MOVES, and its value at
    // the hovered bar is the number that makes "above average" mean something.
    // --text-muted rather than --axis because the line carries a LABEL, and
    // the axis token is chrome dark enough to render that label unreadable on
    // the dark theme.
    series: [{ key: "volume_ma", label: "میانگین حجم",
               color: cssVar("--text-muted"), dash: "5 5", width: 1.5 }],
    format: (v) => fmt(v, 0)
  });
}

// MACD and RSI, on two charts rather than one.
//
// They used to share an axis, which is wrong wherever the two have different
// units -- and on an index it is fatal: MACD is in index points and runs to
// hundreds of thousands, so RSI (0..100) drew as a flat line on the zero grid
// and told the reader nothing. Splitting them costs one more chart and makes
// both readable.
//
// Two further things the split made room for:
//
//   The MACD HISTOGRAM. It is the difference the two lines already show, but
//   as columns around zero it turns BEFORE the crossover does -- which is the
//   only part of MACD that is early. Drawing the pair without it showed the
//   lagging half of the indicator and hid the leading half.
//
//   RSI PINNED TO 0..100. It is a bounded oscillator and only means anything
//   read against its own bounds. Auto-scaled, a drift between 45 and 55 filled
//   the panel and looked like a violent swing; overbought at 71 and overbought
//   at 95 drew identically. The 70/50/30 guides are reference lines now, not
//   palette series, so they no longer compete with the reading itself.
function renderMomentum(prefix, points) {
  const macd = part(prefix, "MomentumChart");
  const rsi = part(prefix, "RsiChart");

  if (!points.length) {
    [macd, rsi].forEach(n => {
      if (n) n.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    return;
  }

  if (macd) {
    lineChart(macd, {
      data: points, xKey: "date", height: 210,
      bars: { key: "macd_hist", label: "هیستوگرام" },
      zeroLine: true,
      series: [
        { key: "macd", label: "MACD" },
        { key: "macd_signal", label: "سیگنال" }
      ]
    });
  }

  if (rsi) {
    const withBands = points.map(p =>
      ({ ...p, rsi_ob: 70, rsi_mid: 50, rsi_os: 30 }));

    lineChart(rsi, {
      data: withBands, xKey: "date", height: 170,
      yMin: 0, yMax: 100,
      series: [
        { key: "rsi", label: "RSI" },
        { key: "rsi_ob", label: "۷۰ اشباع خرید", ...RESISTANCE_LINE() },
        { key: "rsi_mid", ...GUIDE_LINE("2 6") },
        { key: "rsi_os", label: "۳۰ اشباع فروش", ...SUPPORT_LINE() }
      ],
      format: (v) => fmt(v, 0)
    });
  }
}

// The transparent vote. Every component's vote and reason is printed, because
// the whole point of this signal is that the reader can see the mechanism --
// and its standing caveat rides along with it.
function renderVoteCard(prefix, sig) {
  const card = part(prefix, "SignalCard");
  if (!card) return;

  if (!sig || !sig.call) { card.style.display = "none"; return; }

  card.style.display = "";

  const cls = CALL_CLASS[sig.call] || "neutral";
  const conv = sig.conviction === "trending" ? "روند‌دار (ADX بالا)"
    : sig.conviction === "weak" ? "ضعیف (ADX پایین، بازار بی‌روند)"
    : "معمولی";

  // The tally as a bar, because the tally is the signal. Three segments sized
  // by the actual vote counts: a call resting on 2 positives out of 8 and one
  // resting on 6 out of 8 print the same badge, and only this shows which is
  // which at a glance. It is drawn from the same numbers printed beside it --
  // a meter that could disagree with its own caption would be worse than none.
  const total = sig.total || 0;
  const pct = (n) => (total ? (n / total) * 100 : 0);

  const meter = total
    ? `<div class="vote-meter" role="img"
            aria-label="${sig.bullish} مثبت، ${sig.neutral} خنثی، ${sig.bearish} منفی">
         <span class="seg pos" style="width:${pct(sig.bullish)}%"></span>
         <span class="seg mid" style="width:${pct(sig.neutral)}%"></span>
         <span class="seg neg" style="width:${pct(sig.bearish)}%"></span>
       </div>`
    : "";

  // The margin as a share of the votes cast. The number of votes VARIES with
  // what the data supports -- a series with no high/low range casts no
  // directional vote at all -- so the bare net is not comparable between two
  // symbols and this is.
  const strength = (sig.strength === null || sig.strength === undefined)
    ? "" : ` · ${Math.round(Math.abs(sig.strength) * 100)}٪ رأی‌ها هم‌جهت (شمارش، نه احتمال سود)`;

  part(prefix, "SignalHead").innerHTML =
    `<span class="badge ${cls}" style="font-size:1.05em">` +
    `<span class="dot"></span>${esc(sig.call_fa)}</span> ` +
    `<span class="hint" style="margin-inline-start:8px">` +
    `${sig.bullish} رأی مثبت · ${sig.bearish} منفی · ${sig.neutral} خنثی ` +
    `(خالص ${signed(sig.net, 0, "")})${strength} · قوت: ${conv}</span>` +
    meter;

  const note = part(prefix, "SignalNote");
  if (note) note.textContent = sig.note || "";

  part(prefix, "SignalCaveat").textContent = "⚠ " + (sig.caveat || "");

  const voteRows = (sig.votes || []).map(v => {
    const vb = v.vote > 0
      ? `<span class="badge good"><span class="dot"></span>مثبت</span>`
      : v.vote < 0
        ? `<span class="badge bad"><span class="dot"></span>منفی</span>`
        : `<span class="badge neutral"><span class="dot"></span>خنثی</span>`;
    return [esc(v.name), vb, esc(v.reason)];
  });

  part(prefix, "Votes").innerHTML = voteRows.length
    ? table(["اندیکاتور", "رأی", "دلیل"], voteRows)
    : '<div class="empty">رأیی ثبت نشد</div>';
}

/* ---------- indices, one section each ----------

   Both cards are driven by one renderer keyed on an id prefix ("capIndex",
   "ewIndex"), so the equal-weighted card cannot silently fall behind the
   cap-weighted one when something is added to either.
*/

// The two-sided target ladder. It is two-sided because an index is not a
// position: there is no entry to be long or short of, so the honest question
// is "what does it meet on the way up, and on the way down", not "where is my
// stop". Colours follow the level table above -- a level overhead is red
// whether it is called resistance or a target.
function renderIndexTargets(prefix, tg) {
  const card = part(prefix, "TargetCard");
  if (!card) return;

  if (!tg || (!(tg.upside || []).length && !(tg.downside || []).length)) {
    card.style.display = "none";
    return;
  }

  card.style.display = "";

  const n = (v, d = 0) => (v === null || v === undefined ? "—" : num(v, d));

  // A rung's "how long" is honest about being unknown: no ATR, no estimate.
  const when = (row) => row?.sessions ? `${row.sessions} جلسه` : "نامعلوم";

  const rungTile = (label, row, fallback) => ({
    k: label,
    v: row ? n(row.price) : "—",
    sub: row ? `${signed(row.distance_pct, 2)} · ${when(row)} · ${esc(row.label)}`
             : fallback
  });

  tiles(prefix + "TargetKpis", [
    rungTile("نزدیک‌ترین سطح بالا", tg.objective_1,
             "هیچ سطحی بالای شاخص نیست — سقف پنجره"),
    rungTile("سطح بالای بعدی", tg.objective_2, "سطح دومی بالای شاخص نیست"),
    rungTile("نزدیک‌ترین سطح پایین", tg.break_level,
             "هیچ سطحی زیر شاخص نیست"),
    { k: "واحد نوسان (ATR)", v: n(tg.unit),
      sub: tg.unit_pct === null || tg.unit_pct === undefined
        ? "بدون ATR" : `${num(tg.unit_pct, 2)}٪ از شاخص · دامنه‌ی معمول یک جلسه` },
    { k: "سقف مؤثر",
      v: tg.overhead_pct === null || tg.overhead_pct === undefined
        ? "—" : signed(tg.overhead_pct, 2),
      sub: `از ${Object.values(tg.overhead_sources || {}).filter(v => v !== null).length} منبع سطحی` },
    { k: "بیشترین حرکت یک‌جلسه‌ای",
      v: tg.max_session_move_pct === null || tg.max_session_move_pct === undefined
        ? "—" : `${num(tg.max_session_move_pct, 2)}٪`,
      sub: "سقف سرعت تجربی در همین پنجره" }
  ]);

  // Read top-down like a chart: the furthest level above, down through the
  // current value, to the furthest level below.
  const rows = [];

  [...(tg.upside || [])].reverse().forEach(row => rows.push([
    `<span class="badge bad"><span class="dot"></span>سطح بالا</span>`,
    n(row.price), signed(row.distance_pct, 2), when(row),
    `${esc(row.label)} — ${esc(row.kind_fa)}` +
    (row.date ? ` (${esc(row.date)})` : "")
  ]));

  rows.push([
    `<span class="badge neutral"><span class="dot"></span>اکنون</span>`,
    n(tg.price), "—", "—", "آخرین مقدار منتشرشده‌ی شاخص"
  ]);

  (tg.downside || []).forEach(row => rows.push([
    `<span class="badge good"><span class="dot"></span>سطح پایین</span>`,
    n(row.price), signed(row.distance_pct, 2), when(row),
    `${esc(row.label)} — ${esc(row.kind_fa)}` +
    (row.date ? ` (${esc(row.date)})` : "")
  ]));

  part(prefix, "TargetTable").innerHTML =
    table(["جهت", "مقدار", "فاصله", "زمان تخمینی", "از کجا آمده"], rows);

  part(prefix, "TargetCaveat").textContent = "⚠ " + (tg.caveat || "");

  bulletList(prefix + "TargetNotes", tg.notes || []);
}

function renderIndexCard(index, prefix, rep) {
  const chartNode = part(prefix, "Chart");

  if (!index || index.error) {
    banner(prefix + "Status", prefix + "StatusText", "is-error",
      `<strong>داده‌ی این شاخص موجود نیست.</strong> ${esc(index?.error || "")}` +
      bootstrapNote(rep));
    tiles(prefix + "Kpis", []);
    if (chartNode) chartNode.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    ["MomentumChart", "RsiChart", "Breakdown"].forEach(s => {
      const node = part(prefix, s);
      if (node) node.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    ["Table", "Reading", "Levels"].forEach(s => {
      const node = part(prefix, s);
      if (node) node.innerHTML = "";
    });
    renderVoteCard(prefix, null);
    renderIndexTargets(prefix, null);
    return;
  }

  const t = index.technical || {};
  const r = index.returns || {};
  const sig = index.signal || {};
  const lv = index.levels || { resistances: [], supports: [] };

  banner(prefix + "Status", prefix + "StatusText", "is-live",
    `<strong>${esc(index.name)}</strong> — ${index.bars} جلسه تا ${index.last_date}. ` +
    (index.stored_date
      ? `شاخص ذخیره‌شده تا ${esc(index.stored_date)} (${num(index.stored_close, 0)})؛ تحلیل تا ${esc(index.last_date)}، ` +
        "چون گردش معاملات روز آخر هنوز ذخیره نشده است. "
      : "") +
    `مؤلفه‌ی حجم از ${esc(index.volume_source || "هیچ منبعی")} تغذیه شده.`);

  const call = sig.call
    ? `<span class="badge ${CALL_CLASS[sig.call] || "neutral"}">` +
      `<span class="dot"></span>${esc(sig.call_fa)}</span>`
    : "—";

  tiles(prefix + "Kpis", [
    { k: "مقدار شاخص", v: num(index.close, 0), sub: index.last_date },
    { k: "امتیاز تکنیکال", v: t.scored === false ? "—" : t.score,
      sub: badge(t.status || "NO DATA") },
    { k: "سیگنال تکنیکال", v: call,
      sub: sig.call ? `خالص ${signed(sig.net, 0, "")} از ${sig.total} رأی` : "بدون رأی" },
    { k: "روند", v: TREND_FA[t.trend] || t.trend || "—", sub: `RSI ${num(t.rsi, 1)}` },
    { k: "۲۰ روزه", v: signed(r["20d"]), sub: "بازده" },
    { k: "۶۰ روزه", v: signed(r["60d"]), sub: "بازده" }
  ]);

  const s = index.series || {};

  const col = (name, k) => (s[name] && s[name].length ? s[name][k] : null);

  const points = (s.dates || []).map((d, k) => ({
    date: d,
    close: col("close", k),
    ma_fast: col("ma_fast", k),
    ma_slow: col("ma_slow", k),
    bb_upper: col("bb_upper", k),
    bb_lower: col("bb_lower", k),
    macd: col("macd", k),
    macd_signal: col("macd_signal", k),
    macd_hist: col("macd_hist", k),
    rsi: col("rsi", k),
    volume: col("volume", k)
  }));

  const hint = part(prefix, "ChartHint");
  if (hint) {
    hint.textContent = `${index.name} — بسته، میانگین‌های متحرک و باندهای ` +
      `بولینگر. خطوط چین: حمایت‌ها و مقاومت‌های پیوتی.`;
  }

  if (chartNode) {
    if (points.length) {
      const withLevels = points.map(p => ({ ...p }));

      const priceSeries = [
        { key: "close", label: "بسته" },
        { key: "ma_fast", label: "کوتاه" },
        { key: "ma_slow", label: "بلند" },
        { key: "bb_upper", label: "باند بالا" },
        { key: "bb_lower", label: "باند پایین" },
        ...levelSeries(withLevels, lv, 2)
      ];

      lineChart(chartNode, {
        data: withLevels, xKey: "date",
        series: priceSeries,
        format: (v) => fmt(v, 0)
      });

      // Built from the series actually drawn, so an added line can never go
      // missing from the key or borrow the wrong colour. Reference lines carry
      // their own colour; only the data series draw from the palette, and they
      // must be counted the same way the chart counts them or the swatches
      // shift by one for every level line added.
      const legend = part(prefix, "Legend");
      if (legend) {
        const auto = seriesColors(priceSeries.filter(x => !x.color).length);
        let slot = 0;
        legend.innerHTML = priceSeries
          .map((x) => {
            const color = x.color || auto[slot++];
            // .key/<i> were never styled -- the swatch was a zero-size inline
            // element, so the whole legend rendered as bare words.
            return x.label
              ? `<span class="item"><span class="swatch" style="background:${color}"></span>${esc(x.label)}</span>`
              : "";
          })
          .join("");
      }
    } else {
      chartNode.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    }
  }

  renderMomentum(prefix, points);

  const breakdown = part(prefix, "Breakdown");
  if (breakdown && t.breakdown) {
    hBarChart(breakdown, {
      data: COMPONENTS.map(c => ({ label: c, value: t.breakdown[c] ?? 0 })),
      max: 25,
      format: (v) => fmt(v, 0)
    });
  }

  const returnsTable = part(prefix, "Table");
  if (returnsTable) {
    returnsTable.innerHTML = table(
      ["بازه", "بازده"],
      [["۱ روزه", signed(r["1d"])], ["۵ روزه", signed(r["5d"])],
       ["۲۰ روزه", signed(r["20d"])], ["۶۰ روزه", signed(r["60d"])]]
    );
  }

  // An index value runs to millions; 0 decimals is the readable rounding, and
  // "مقدار" rather than "قیمت" because an index has a value, not a price.
  renderReading(prefix, index.latest);
  renderLevels(prefix, lv, 0, "مقدار");
  renderVoteCard(prefix, index.signal);
  renderIndexTargets(prefix, index.targets);
}

function renderIndices(rep) {
  state.indices = rep;

  const list = rep.indices || [];

  renderIndexCard(list.find(i => i.weighting === "capitalisation"), "capIndex", rep);
  renderIndexCard(list.find(i => i.weighting === "equal"), "ewIndex", rep);

  const breadth = rep.breadth;

  if (breadth) {
    // Shortest window first, the order indices.py builds them in. Flask sorts
    // JSON keys, so the object arrives as 1d, 20d, 5d, 60d, and drawing it in
    // arrival order put the 20-day bar between the 1-day and the 5-day. A
    // window this list does not know still gets drawn, after the four it does.
    const spreads = breadth.spreads || {};
    const order = ["1d", "5d", "20d", "60d"];
    const keys = [...order.filter(k => k in spreads),
                  ...Object.keys(spreads).filter(k => !order.includes(k))];

    divergingBar($("breadthChart"), {
      data: keys
        .map(k => [k, spreads[k]])
        .filter(([, v]) => v !== null)
        .map(([k, v]) => ({ label: k, value: v })),
      format: (v) => `${v > 0 ? "+" : ""}${fmt(v, 1)}pp`
    });
    $("breadthVerdict").innerHTML = `<strong>${esc(breadth.verdict_fa || breadth.verdict)}</strong>`;
  } else {
    $("breadthChart").innerHTML = '<div class="empty">داده‌ای نیست</div>';
    $("breadthVerdict").textContent = "";
  }
}

async function loadIndices(force = false) {
  const btn = $("runIndicesBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تحلیل…"; }
  setTask("indices", "running", "امتیازدهی شاخص کل و شاخص کل هم‌وزن");

  try {
    const rep = await getJSON(`/api/indices?${query()}${force ? "&refresh=1" : ""}`);
    renderIndices(rep);
    const ok = (rep.indices || []).filter(i => !i.error).length;
    setTask("indices", ok ? "done" : "failed",
      ok ? `${ok} سری شاخص امتیازدهی شد` : "هیچ سری شاخصی ذخیره نشده",
      `${ok}/${(rep.indices || []).length}`);
  } catch (err) {
    state.indices = null;
    banner("capIndexStatus", "capIndexStatusText", "is-error",
      `<strong>تحلیل شاخص شکست خورد.</strong> ${esc(err.message)}`);
    setTask("indices", "failed", err.message);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
  }
}

/* ---------- technical: one instrument, on demand ---------- */

// Every chart on the single-symbol card, named once. Both the reset path and
// the failure path have to blank all of them, and the two lists had already
// drifted from the panel by one chart -- a stale volume panel left standing
// under a failed lookup reads as the new symbol's volume.
const TECHNICAL_CHARTS = [
  "technicalPriceChart", "technicalVolumeChart",
  "technicalMomentumChart", "technicalRsiChart"
];

// Persian labels for the trend classifier's English states.
const TREND_FA = {
  "Strong Uptrend": "روند صعودی قوی",
  "Uptrend": "روند صعودی",
  "Weak Downtrend": "روند نزولی ضعیف",
  "Downtrend": "روند نزولی",
  "Recovery": "بالای میانگین بلند، زیر کوتاه",
  "Bearish": "نزولی",
  "Not Traded": "معامله نشده",
  "Unknown": "نامشخص"
};

// Rising is not coloured green: a trend on one stock is a reading, not a
// call (STATUS_CLASS).
const TREND_CLASS = {
  "Strong Uptrend": "neutral", "Uptrend": "neutral", "Recovery": "neutral",
  "Weak Downtrend": "warn", "Downtrend": "bad", "Bearish": "bad",
  "Not Traded": "neutral", "Unknown": "neutral"
};

// ADX measures a trend's STRENGTH and is silent about its direction; +DI/-DI
// carry the sign. Printing ADX alone let a big number read as bullish, which
// it never was -- a strong downtrend scores exactly as high as a strong
// uptrend. The two are shown together or not at all.
function adxNote(L) {
  if (L.adx === null || L.adx === undefined) return "بدون ADX";

  const where = L.adx >= 25 ? "روند قوی"
    : L.adx < 20 ? "بی‌روند" : "روند ضعیف";

  if (L.di_plus === null || L.di_plus === undefined
      || L.di_minus === null || L.di_minus === undefined) return where;

  if (L.adx < 20) return `${where} (جهت بی‌معنا)`;

  return L.di_plus > L.di_minus ? `${where} صعودی` : `${where} نزولی`;
}

// Where the stop distance came from. risk_engine names it in English and
// composes suffixes onto it (`atr+floor`), so map the head and keep any
// suffix's meaning rather than falling through to a raw token.
const STOP_SOURCE_FA = {
  "atr": "از ATR",
  "price_band": "از دو دامنه‌ی نوسان",
};

function stopSourceFa(source) {
  if (!source) return "—";
  const [head, ...rest] = String(source).split("+");
  const base = STOP_SOURCE_FA[head] || esc(head);
  if (rest.includes("floor")) return `${base}، رسیده به کف مجاز`;
  if (rest.includes("ceiling")) return `${base}، محدود به سقف مجاز`;
  return base;
}

// Price targets. Rendered from what the backend computed -- the ladder, the
// stop it is measured in, and the two states that must not read as ordinary
// targets: a target crushed against a ceiling (no room), and a second target
// that sits ABOVE that ceiling and so requires it to break.
function renderTechnicalTargets(tg, signal) {
  const card = $("technicalTargetCard");
  if (!card) return;

  if (!tg || tg.target_1 === null || tg.target_1 === undefined) {
    card.style.display = "none";
    return;
  }

  card.style.display = "";

  const n = (v, d = 0) => (v === null || v === undefined ? "—" : num(v, d));

  // The reward/risk verdict, against the platform's own minimum. A ratio below
  // it is not a small edge, it is a trade this system would refuse to open.
  //
  // Read on the ratio AFTER the round trip (tg.risk_reward_net, from
  // technical.price_targets): gross, 27 of 92 names read "above 2.0" in
  // green and one did net of today's 1.25% (2026-11-23). The gross ratio is
  // kept beside it -- it is the engine's own number, and the basket still
  // selects on it.
  const rr = tg.risk_reward;
  const hasNet = tg.risk_reward_net !== null && tg.risk_reward_net !== undefined;
  const judged = hasNet ? tg.risk_reward_net : rr;
  const floor = tg.min_risk_reward;
  const hasFloor = floor !== null && floor !== undefined;
  const rrCls = (judged === null || judged === undefined) ? "neutral"
    : (hasFloor && judged < floor) ? "bad" : "good";
  const rrNote = ((judged === null || judged === undefined) ? "بدون نسبت"
    : (hasFloor && rr !== null && rr !== undefined && rr < floor)
      ? `زیر حد ${num(floor, 1)} — این پلتفرم چنین موقعیتی را باز نمی‌کند`
      : (hasFloor && judged < floor)
        ? `خالص زیر حد ${num(floor, 1)} — پس از کارمزد به این نسبت نمی‌رسد`
        : `${hasNet ? "خالص " : ""}بالای حد ${num(floor, 1)}`) +
    (hasNet ? ` · ناخالص ${num(rr, 2)}، هزینه‌ی رفت‌وبرگشت ${num(tg.cost_pct, 2)}٪` : "");

  const horizon = tg.expected_sessions === null || tg.expected_sessions === undefined
    ? "—" : `${tg.expected_sessions} سشن`;
  const horizonSub = tg.band_min_sessions
    ? `دست‌کم ${tg.band_min_sessions} سشن به‌خاطر دامنه‌ی ${num(tg.band_pct, 0)}٪`
    : "بدون فاصله‌ای برای پیمودن";

  tiles("technicalTargetKpis", [
    { k: "هدف اول",
      v: tg.no_room ? '<span class="badge warn"><span class="dot"></span>فضایی نمانده</span>'
                    : n(tg.target_1),
      sub: tg.no_room ? "مقاومت چسبیده به قیمت است"
                      : `${signed(tg.target_1_pct, 1)} از قیمت` +
                        (tg.capped ? " · زیر مقاومت محدود شد" : "") },
    { k: "هدف دوم", v: n(tg.target_2),
      sub: `${signed(tg.target_2_pct, 1)} از قیمت` +
           (tg.target_2_above_wall ? " · بالای مقاومت" : "") },
    { k: "حد ضرر", v: n(tg.stop_loss),
      sub: `${signed(-Math.abs(tg.stop_distance_pct), 1)} · ${stopSourceFa(tg.stop_source)}` +
        (tg.loss_at_stop_net_pct != null ? ` · با کارمزد ${num(tg.loss_at_stop_net_pct, 1)}٪ زیان` : "") },
    { k: hasNet ? "سود به زیان (خالص از کارمزد)" : "سود به زیان",
      v: `<span class="badge ${rrCls}"><span class="dot"></span>${n(judged, 2)}</span>`,
      sub: rrNote },
    { k: "افق زمانی", v: horizon, sub: horizonSub },
    { k: "نزدیک‌ترین مقاومت",
      v: tg.nearest_resistance ? n(tg.nearest_resistance.price) : "—",
      sub: tg.nearest_resistance
        ? `${signed(tg.nearest_resistance.distance_pct, 1)} · ` +
          `${tg.nearest_resistance.touches || 1} برخورد تا ` +
          `${esc(tg.nearest_resistance.date || "—")}`
        : "پیوتی بالای قیمت نیست" }
  ]);

  // The ladder, read bottom-up: the stop, the price now, then every objective
  // above it in order. Putting the stop in the same table as the targets is
  // the point -- a target without the loss that pays for it is half a number.
  //
  // Every rung now carries its R -- what it is worth in units of the risk
  // taken to reach it. The measured targets always showed theirs; the
  // structural ones did not, so a Fibonacci extension sitting 0.4R above the
  // price (inside the noise the stop is sized to absorb) looked exactly like a
  // real objective. One column, and the ladder can be read down.
  const r = (v) => (v === null || v === undefined ? "—" : `${num(v, 2)}R`);

  const rows = [];

  rows.push([
    `<span class="badge bad"><span class="dot"></span>حد ضرر</span>`,
    n(tg.stop_loss), signed(-Math.abs(tg.stop_distance_pct), 1), "−1R",
    `${stopSourceFa(tg.stop_source)} — فاصله‌ای که اهداف با آن اندازه گرفته می‌شوند`
  ]);

  rows.push([
    `<span class="badge neutral"><span class="dot"></span>قیمت فعلی</span>`,
    n(tg.price), "—", "0R", "بسته‌ی آخرین کندل"
  ]);

  if (!tg.no_room) {
    rows.push([
      `<span class="badge good"><span class="dot"></span>هدف اول</span>`,
      n(tg.target_1), signed(tg.target_1_pct, 1), r(tg.risk_reward),
      tg.capped
        ? `اندازه‌گیری‌شده، سپس زیر مقاومت ${signed(tg.overhead_pct, 1)} کشیده شد`
        : `اندازه‌گیری‌شده — ${n(tg.risk_reward, 2)} برابر فاصله‌ی حد ضرر`
    ]);
  } else {
    rows.push([
      `<span class="badge warn"><span class="dot"></span>هدف اول</span>`,
      "—", "—", "—",
      `مقاومت تنها ${signed(tg.overhead_pct, 1)} بالاتر است — فضایی برای هدف نمانده`
    ]);
  }

  rows.push([
    `<span class="badge ${tg.target_2_above_wall ? "warn" : "good"}">` +
    `<span class="dot"></span>هدف دوم</span>`,
    n(tg.target_2), signed(tg.target_2_pct, 1), r(tg.risk_reward_target_2),
    tg.target_2_above_wall
      ? `بالای مقاومت — تنها با شکستن آن در دسترس است (${n(tg.risk_reward_target_2, 2)} برابر حد ضرر)`
      : `${n(tg.risk_reward_target_2, 2)} برابر فاصله‌ی حد ضرر`
  ]);

  // The swing an extension projects is dated, because an extension of a move
  // that ended two years ago is arithmetic on history, not on this market.
  const swingWhen = tg.swing_to
    ? ` — سوئینگی که تا ${esc(tg.swing_to)} کامل شد` : "";

  (tg.structural || []).forEach(t => rows.push([
    `<span class="badge neutral"><span class="dot"></span>هدف ساختاری</span>`,
    n(t.price), signed(t.distance_pct, 1), r(t.r_multiple),
    `بسط ${esc(t.ratio)} برابرِ سوئینگ آخر` +
    (tg.swing_direction === "up" ? " (صعودی)"
      : tg.swing_direction === "down" ? " (نزولی)" : "") + swingWhen
  ]));

  $("technicalTargetTable").innerHTML =
    table(["سطح", "قیمت", "فاصله", "برحسب ریسک", "از کجا آمده"], rows);

  $("technicalTargetCaveat").textContent = "⚠ " + (tg.caveat || "");

  // The ladder is long-only because the exchange is. On a SELL reading that
  // has to be said on the card -- an upward ladder under a sell call reads as
  // an invitation unless something states otherwise.
  const notes = [...(tg.notes || [])];
  if (signal && signal.call === "SELL") {
    notes.unshift("خوانش تکنیکال «فروش» است. این نردبان رو به بالاست چون در " +
      "این بازار فروش استقراضی وجود ندارد — پس این اهداف می‌گویند مقاومت کجاست، " +
      "نه اینکه بخرید.");
  }

  bulletList("technicalTargetNotes", notes);
}

function renderTechnical(rep) {
  state.technical = rep;

  const clear = () => {
    TECHNICAL_CHARTS.forEach(id => {
      const n = $(id);
      if (n) n.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    $("technicalTable").innerHTML = "";
    const lvNode = $("technicalLevels");
    if (lvNode) lvNode.innerHTML = "";
    const card = $("technicalSignalCard");
    if (card) card.style.display = "none";
    const tCard = $("technicalTargetCard");
    if (tCard) tCard.style.display = "none";
    tiles("technicalKpis", []);
  };

  if (!rep || rep.error || !rep.latest) {
    clear();
    return;
  }

  const L = rep.latest;
  const trend = L.trend || "Unknown";

  banner("technicalStatus", "technicalStatusText", "is-live",
    `<strong>${esc(rep.symbol || "—")}</strong>` +
    (rep.name ? ` — ${esc(rep.name)}` : "") +
    ` · ${esc(rep.sector || "بدون صنعت")} · ` +
    `${rep.bars_used} کندل تا ${esc(rep.as_of || "—")}. ` +
    `خوانش و اهداف، هر دو با هشدارِ خودشان.`);

  // Reading tiles. A null indicator shows "—", never a fabricated zero.
  const n = (v, d = 2) => (v === null || v === undefined ? "—" : num(v, d));

  tiles("technicalKpis", [
    { k: "بسته", v: n(L.close, 0),
      sub: `میانگین کوتاه ${n(L.ma_fast, 0)} · بلند ${n(L.ma_slow, 0)}` },
    { k: "روند",
      v: `<span class="badge ${TREND_CLASS[trend] || "neutral"}">` +
         `<span class="dot"></span>${TREND_FA[trend] || esc(trend)}</span>`,
      sub: "در برابر دو میانگین متحرک" },
    { k: "RSI", v: n(L.rsi, 1),
      sub: L.rsi === null ? "بدون داده"
        : L.rsi >= 70 ? "اشباع خرید" : L.rsi <= 30 ? "اشباع فروش" : "میانه" },
    { k: "MACD", v: n(L.macd, 2),
      sub: `سیگنال ${n(L.macd_signal, 2)} · هیستوگرام ${n(L.macd_hist, 2)}` },
    { k: "%B بولینگر", v: n(L.bb_pct, 2),
      sub: `پهنای باند ${n(L.bb_width, 1)}٪` },
    { k: "ATR%", v: n(L.atr_pct, 2),
      sub: `ADX ${n(L.adx, 1)} · ${adxNote(L)}` },
    // Volume against its own average. "Above average" is the reading; the raw
    // share count is not comparable to anything, which is why the ratio is the
    // headline number. A zero-volume bar gets its own words rather than a
    // 0.00x ratio: the instrument did not trade, which is a different
    // statement from "it traded on no interest".
    { k: "حجم / میانگین",
      v: (!L.volume || !L.volume_ma)
        ? (L.volume === 0
            ? '<span class="badge neutral"><span class="dot"></span>معامله نشد</span>'
            : "—")
        : `${num(L.volume / L.volume_ma, 2)}×`,
      sub: L.volume === null || L.volume === undefined ? "بدون داده‌ی حجم"
        : L.volume === 0 ? "آخرین جلسه بدون معامله"
        : `حجم امروز ${money(L.volume)}` }
  ]);

  const series = rep.series || [];
  const lv = rep.levels || { resistances: [], supports: [] };

  const priceNode = $("technicalPriceChart");
  if (priceNode) {
    if (series.length) {
      const withLevels = series.map(pt => ({ ...pt }));

      lineChart(priceNode, {
        data: withLevels, xKey: "date", height: 300,
        series: [
          { key: "close", label: "بسته" },
          { key: "ma_fast", label: "کوتاه" },
          { key: "ma_slow", label: "بلند" },
          { key: "bb_upper", label: "باند بالا" },
          { key: "bb_lower", label: "باند پایین" },
          ...levelSeries(withLevels, lv, 2)
        ],
        format: (v) => fmt(v, 0)
      });
    } else {
      priceNode.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    }
  }

  renderVolume("technical", series);

  renderMomentum("technical", series);

  // A compact current-reading table beneath the charts, and the pivot levels
  // and vote beside it. All three come from the shared renderers the index
  // cards use, so the same indicator cannot be rounded or labelled two ways.
  $("technicalTable").innerHTML =
    table(["اندیکاتور", "مقدار"], indicatorRows(L));

  renderLevels("technical", lv, 0);

  renderVoteCard("technical", rep.signal);

  // ---- price targets ----
  renderTechnicalTargets(rep.targets, rep.signal);
}

async function loadTechnical(force = false) {
  const input = $("techSymbolInput");
  const symbol = (input && input.value || "").trim();

  if (!symbol) {
    banner("technicalStatus", "technicalStatusText", "is-warn",
      "یک نماد وارد کنید، مثل وبملت.");
    return;
  }

  const btn = $("runTechnicalBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تحلیل…"; }

  try {
    const rep = await getJSON(
      `/api/technical?symbol=${encodeURIComponent(symbol)}&${query()}` +
      `${force ? "&refresh=1" : ""}`);
    renderTechnical(rep);
  } catch (err) {
    state.technical = null;
    banner("technicalStatus", "technicalStatusText", "is-error",
      `<strong>تحلیل نشد.</strong> ${esc(err.message)}`);
    TECHNICAL_CHARTS.forEach(id => {
      const node = $(id);
      if (node) node.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    $("technicalTable").innerHTML = "";
    const lvNode = $("technicalLevels");
    if (lvNode) lvNode.innerHTML = "";
    const card = $("technicalSignalCard");
    if (card) card.style.display = "none";
    const tCard = $("technicalTargetCard");
    if (tCard) tCard.style.display = "none";
    tiles("technicalKpis", []);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
  }
}

/* ---------- top-N technical, over the liquid universe ---------- */

// An indicator vote is coloured as a description, never as a go-ahead.
const CALL_CLASS = { BUY: "neutral", SELL: "serious", HOLD: "neutral" };

function renderTopTechnical(rep) {
  state.topTechnical = rep;

  const rows = rep.top || [];
  const c = rep.counts || {};

  if (!rows.length) {
    banner("topTechnicalStatus", "topTechnicalStatusText", "is-warn",
      "هیچ نمادی بالای کف نقدشوندگی امتیاز نگرفت.");
    $("topTechnicalTable").innerHTML = "";
    return;
  }

  banner("topTechnicalStatus", "topTechnicalStatusText", "is-live",
    `<strong>${rows.length} نماد برتر</strong> از ${c.liquid_scored} نماد ` +
    `نقدشونده (${c.illiquid_skipped} نماد زیر کف ${money(rep.min_turnover)} ` +
    `ریال کنار گذاشته شد). ⚠ ${esc(rep.caveat || "")}`);

  const callBadge = (call, fa) =>
    `<span class="badge ${CALL_CLASS[call] || "neutral"}">` +
    `<span class="dot"></span>${esc(fa)}</span>`;

  const body = rows.map((r, i) => [
    String(i + 1),
    `<a href="?tech=${encodeURIComponent(r.symbol || "")}"
        title="تحلیل تک‌نماد">${esc(r.symbol || "—")}</a>`,
    esc((r.sector || "").slice(0, 22)),
    num(r.score, 0),
    r.status ? badge(r.status) : "—",
    callBadge(r.call, r.call_fa),
    num(r.rsi, 1),
    num(r.macd, 2),
    money(r.turnover)
  ]);

  $("topTechnicalTable").innerHTML = table(
    ["#", "نماد", "صنعت", "امتیاز", "وضعیت", "سیگنال", "RSI", "MACD",
     "ارزش معاملات"], body);
}

async function loadTopTechnical(force = false) {
  const btn = $("runTopTechnicalBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال رتبه‌بندی…"; }
  banner("topTechnicalStatus", "topTechnicalStatusText", "is-loading",
    "اسکن کل بازار — چند ده ثانیه طول می‌کشد…");

  try {
    const rep = await getJSON(
      `/api/technical/top?top=10&${query()}${force ? "&refresh=1" : ""}`);
    renderTopTechnical(rep);
  } catch (err) {
    state.topTechnical = null;
    banner("topTechnicalStatus", "topTechnicalStatusText", "is-error",
      `<strong>رتبه‌بندی نشد.</strong> ${esc(err.message)}`);
    $("topTechnicalTable").innerHTML = "";
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ رتبه‌بندی بازار"; }
  }
}

/* ---------- technical strength + money inflow ---------- */

function renderInflow(rep) {
  state.inflow = rep;

  const rows = rep.top || [];
  const c = rep.counts || {};

  if (!rows.length) {
    banner("inflowStatus", "inflowStatusText", "is-warn",
      `<strong>هیچ سهمی هر دو شرط را ندارد.</strong> ` +
      `${c.strong_technical || 0} نماد تکنیکال قوی بودند، اما هیچ‌کدام همزمان ` +
      `ورود پول حقیقی نداشتند. ⚠ ${esc(rep.caveat || "")}`);
    $("inflowTable").innerHTML = "";
    return;
  }

  banner("inflowStatus", "inflowStatusText", "is-live",
    `<strong>${rows.length} سهم</strong> با تکنیکال قوی و ورود پول، از ` +
    `${c.strong_and_inflow} واجد شرط (${c.strong_technical} تکنیکال قوی از ` +
    `${c.liquid} نقدشونده). ⚠ ${esc(rep.caveat || "")}`);

  const callBadge = (call, fa) =>
    `<span class="badge ${CALL_CLASS[call] || "neutral"}">` +
    `<span class="dot"></span>${esc(fa)}</span>`;

  const body = rows.map((r, i) => [
    String(i + 1),
    `<a href="?tech=${encodeURIComponent(r.symbol || "")}"
        title="تحلیل تک‌نماد">${esc(r.symbol || "—")}</a>`,
    esc((r.sector || "").slice(0, 20)),
    num(r.score, 0),
    signed(r.net_pct, 1),
    r.power_ratio == null ? "—" : num(r.power_ratio, 2),
    esc(r.flow_label || "—"),
    callBadge(r.call, r.call_fa)
  ]);

  $("inflowTable").innerHTML = table(
    ["#", "نماد", "صنعت", "امتیاز تکنیکال", "خالص پول٪", "قدرت خریدار",
     "جریان", "سیگنال"], body);
}

async function loadInflow(force = false) {
  const btn = $("runInflowBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال یافتن…"; }
  banner("inflowStatus", "inflowStatusText", "is-loading",
    "اسکن کل بازار — چند ده ثانیه طول می‌کشد…");

  try {
    const rep = await getJSON(
      `/api/technical/inflow?top=15&${query()}${force ? "&refresh=1" : ""}`);
    renderInflow(rep);
  } catch (err) {
    state.inflow = null;
    banner("inflowStatus", "inflowStatusText", "is-error",
      `<strong>یافتن نشد.</strong> ${esc(err.message)}`);
    $("inflowTable").innerHTML = "";
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ یافتن سهم‌ها"; }
  }
}

/* ---------- Smart Money Accumulation Scanner ---------- */

// Every status this scanner issues is a WATCH status. There is deliberately no
// green "buy" here: the platform's own research rejected the return claim
// behind each of the scanner's ingredients, and painting a row green would
// overturn that with a colour rather than with evidence.
const SM_STATUS_CLASS = {
  "تحت نظر ویژه": "warning",
  "تحت نظر": "warning",
  "زیر نظر": "neutral",
  "ضعیف": "neutral"
};

// The V2 decision is shown as-is, including when it contradicts the scanner.
// A row where the swing engine says AVOID and the scanner says 82 is the most
// informative row in the table.
const SM_V2_CLASS = { BUY: "neutral", HOLD: "neutral", SELL: "bad", AVOID: "bad" };

// Market caps are read in همت on this market (۱ همت = ۱۰ هزار میلیارد ریال),
// not in the T/B abbreviations money() uses for turnover. A reader who knows
// this exchange knows instantly what "۱۲ همت" is and has to convert "120.00B".
function hemat(v) {
  return (v === null || v === undefined) ? "—"
    : `${(v / 1e13).toFixed(v < 1e13 ? 2 : 1)} همت`;
}

function smartMoneyFunnel(c) {
  tiles("smartMoneyFunnel", [
    { k: "کل نمادها", v: c.universe ?? "—",
      sub: `${c.illiquid ?? 0} زیر کف نقدشوندگی` },
    { k: "نقدشونده", v: c.liquid ?? "—",
      sub: `${c.dirty_data ?? 0} با داده مخدوش` },
    { k: "MFI بالای ۷۰", v: c.passed_mfi ?? "—",
      sub: "شرط اول — به‌تنهایی عادی است" },
    { k: "در کف محدوده", v: c.passed_range ?? "—",
      sub: `${c.queue_locked ?? 0} در صف، حذف شد` },
    { k: "هنوز حرکت نکرده", v: c.still_quiet ?? "—",
      sub: `${c.already_moved ?? 0} قبلاً حرکت کرده بودند` },
    { k: "واجد شرایط", v: c.qualified ?? "—",
      sub: `${c.with_code_to_code ?? 0} با کد به کد تأییدشده` }
  ]);
}

function renderSmartMoney(rep) {
  state.smartMoney = rep;

  const rows = rep.top || [];
  const c = rep.counts || {};

  smartMoneyFunnel(c);
  renderSmartMoneyRejected(rep.rejected || {});

  const ms = rep.market_state;
  const regime = (ms && !ms.informative)
    ? `<br><strong style="color:var(--status-warning)">⚠ وضعیت بازار — ${esc(ms.regime)}:</strong> ${esc(ms.note || "")}`
    : "";

  const stale = (rep.staleness && !rep.staleness.actionable)
    ? `<br><strong style="color:var(--status-warning)">⚠ داده کهنه:</strong> ${esc(rep.staleness.note || "")}`
    : "";

  const news = rep.news
    ? ` خبر بازار: ${esc(rep.news.lean_label || "—")} (${rep.news.articles || 0} خبر) — ${esc(rep.news.note || "")}`
    : "";

  if (!rows.length) {
    banner("smartMoneyStatus", "smartMoneyStatusText", "is-warn",
      `<strong>امروز هیچ نمادی هر دو شرط را ندارد.</strong> ` +
      `از ${c.liquid || 0} نماد نقدشونده، ${c.passed_mfi || 0} نماد MFI بالای ` +
      `۷۰ داشتند اما ${c.passed_range || 0} نماد همزمان در کف محدوده‌ی خود ` +
      `بودند. این یک نتیجه است، نه خطا — و مثل هر اجرای دیگری ثبت شد. ` +
      `⚠ ${esc(rep.caveat || "")}${news}${regime}${stale}`);
    $("smartMoneyTable").innerHTML = "";
    $("smartMoneyAlerts").innerHTML = "";
    return;
  }

  const live = !(rep.staleness && !rep.staleness.actionable);

  banner("smartMoneyStatus", "smartMoneyStatusText", live ? "is-live" : "is-warn",
    `<strong>${rows.length} نماد</strong> ` +
    (live ? "در حال جمع‌آوری" : "— سابقه‌ی یک جلسه‌ی گذشته، نه فهرست جاری") + `، از ` +
    `${c.qualified || 0} واجد شرایط (${c.passed_mfi || 0} با MFI بالای ۷۰، ` +
    `${c.passed_range || 0} از آن‌ها در کف محدوده). جلسه‌ی داده: ` +
    `${esc(rep.session_date || "—")}` +
    (rep.run_id ? ` — ثبت شد با شناسه‌ی اجرای ${rep.run_id}` : "") +
    `. ⚠ ${esc(rep.caveat || "")}${news}${regime}${stale}`);

  const body = rows.map(r => {
    const v2 = r.engine_v2 || {};

    // P/E as a RELATION, which is what the reader is asked to judge. The raw
    // pair is kept in the title so the claim can be checked, not just trusted.
    //
    // The word carries the direction and the number only the size of the gap.
    // pe_vs_sector is a DISCOUNT -- (sector - pe) / sector, positive when
    // cheaper (smart_money/scanner.py) -- so printed signed it contradicted the
    // word: قشهد at P/E 8.3 against a median of 7.6 read «بیشتر از گروه (−9%)».
    const pe = (r.pe == null || r.sector_pe == null)
      ? "—"
      : `<span class="${r.pe < r.sector_pe ? "up" : "down"}" ` +
        `title="P/E ${num(r.pe, 1)} در برابر میانه صنعت ${num(r.sector_pe, 1)}">` +
        `${r.pe < r.sector_pe ? "کمتر" : "بیشتر"} از گروه` +
        (r.pe_vs_sector == null ? "" : ` (${num(Math.abs(r.pe_vs_sector), 0)}٪)`) +
        `</span>`;

    const smart = r.code_to_code
      ? `<span class="badge warning"><span class="dot"></span>کد به کد ` +
        `${num(r.smart_money_ratio, 2)}</span>`
      : `<span class="badge neutral" title="${esc((r.code_to_code_reasons || [])[0] || "")}">` +
        `<span class="dot"></span>تأیید نشد</span>`;

    const sup = r.support || {};
    const support = sup.price == null
      ? "—"
      : `${num(sup.distance_pct, 1)}٪ بالای ${esc(sup.label || "حمایت")}` +
        (sup.touches ? ` (${sup.touches} بار تست)` : "");

    return [
      String(r.rank),
      `<a href="?tech=${encodeURIComponent(r.symbol || "")}"
          title="تحلیل تک‌نماد">${esc(r.symbol || "—")}</a>`,
      `<strong>${r.score}</strong>` +
        (r.coverage < 1
          ? `<span class="hint" title="بخشی از مدل ورودی نداشت و صفر گرفت — ` +
            `امتیاز بازتوزیع نمی‌شود">` +
            ` ${Math.round(r.coverage * 100)}٪ مدل اجرا شد</span>`
          : ""),
      num(r.mfi, 1) + (r.divergence ? " ⚡" : ""),
      pe,
      `${esc(r.size_band_fa || "—")}<span class="hint"> ${hemat(r.market_cap)}</span>`,
      `${signed(r.net_pct, 1)} ${smart}`,
      // What the scanner is trying to precede: if this is already positive
      // against the market, the move started without you.
      `${signed(r.advance_excess_pct, 1)}` +
        `<span class="hint"> ${r.band_closes ?? 0} بار در باند</span>`,
      support,
      `<span class="badge ${SM_STATUS_CLASS[r.status] || "neutral"}">` +
        `<span class="dot"></span>${esc(r.status)}</span>` +
        (v2.action
          ? ` <span class="badge ${SM_V2_CLASS[v2.action] || "neutral"}" ` +
            `title="نظر موتور تصمیم V2 — جدا از این اسکنر">` +
            `<span class="dot"></span>موتور V2: ${esc(STATUS_FA[v2.action] || v2.action)}</span>`
          : "")
    ];
  });

  $("smartMoneyTable").innerHTML = table(
    ["#", "نماد", "امتیاز", "MFI", "P/E گروه", "ارزش بازار",
     "خرید حقیقی (خالص و درشت)", "مازاد بر بازار", "حمایت", "وضعیت"], body);

  // The per-name sentence. Written out in full rather than hidden behind a
  // tooltip: the score is the summary, and this is the reason.
  $("smartMoneyAlerts").innerHTML =
    `<p class="hint">توضیح هر نماد — قابل بررسی روی نمودار:</p>` +
    rows.map(r => `
      <div class="banner" style="margin-top:6px">
        <span class="icon">◆</span>
        <div><strong>${esc(r.symbol || "—")}</strong> —
          ${esc(r.alert || "")}</div>
      </div>`).join("");
}

// The rules that removed names, with their counts. A name failing two rules is
// counted under both, so these do not sum to the number of names rejected --
// they describe the market, not a partition of it.
const SM_RULE_FA = {
  mfi_floor: "MFI زیر ۷۰",
  at_ceiling: "قیمت در سقف محدوده — در کف نیست",
  no_range: "محدوده‌ی چندماهه قابل محاسبه نبود",
  no_support: "هیچ حمایتی زیر قیمت نبود (کف در حال ساخته شدن)",
  far_from_support: "فاصله از نزدیک‌ترین حمایت بیش از آستانه",
  no_transfer: "انتقال حقوقی به حقیقی رخ نداده بود",
  no_flow_data: "داده‌ی حقیقی/حقوقی ذخیره نشده",
  data_integrity: "داده‌ی قیمتی مخدوش (حرکت غیرممکن در یک جلسه)",
  thin_trading: "متوقف یا قفل در صف — پنجره‌ی خوانش واقعاً معامله نشده",
  queue_locked: "در آخرین جلسه در صف خرید یا فروش بوده",
  already_moved: "حرکت قیمت قبلاً شروع شده (مازاد بر بازار)"
};

function renderSmartMoneyRejected(rejected) {
  const entries = Object.entries(rejected).sort((a, b) => b[1] - a[1]);

  $("smartMoneyRejectedTable").innerHTML = entries.length
    ? `<p class="hint">یک نماد می‌تواند در چند قانون بیفتد، پس جمع این اعداد
       از تعداد نمادهای حذف‌شده بیشتر است.</p>` +
      table(["قانون", "تعداد"],
        entries.map(([rule, n]) => [esc(SM_RULE_FA[rule] || rule), String(n)]))
    : "";
}

async function loadSmartMoney(force = false) {
  const btn = $("runSmartMoneyBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال اسکن…"; }
  banner("smartMoneyStatus", "smartMoneyStatusText", "is-loading",
    "اسکن کل بازار — دو دروازه‌ی ارزان اول اجرا می‌شوند و بقیه فقط روی " +
    "بازماندگان…");

  try {
    const rep = await getJSON(
      `/api/smartmoney?top=20&${query()}${force ? "&refresh=1" : ""}`);
    renderSmartMoney(rep);
  } catch (err) {
    state.smartMoney = null;
    banner("smartMoneyStatus", "smartMoneyStatusText", "is-error",
      `<strong>اسکن انجام نشد.</strong> ${esc(err.message)}`);
    $("smartMoneyTable").innerHTML = "";
    $("smartMoneyAlerts").innerHTML = "";
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ اجرای اسکنر"; }
  }
}

/* ---------- gold funds (صندوق‌های طلا) ---------- */

function renderGold(rep) {
  state.gold = rep;

  const rows = rep.funds || [];
  const c = rep.counts || {};

  if (!rows.length) {
    banner("goldStatus", "goldStatusText",
      c.missing ? "is-warn" : "is-error",
      c.missing
        ? `<strong>هیچ صندوقی داده ندارد.</strong> ${c.missing} صندوق منتظر ` +
          `دریافت‌اند — دکمه‌ی «دریافت تاریخچه» را بزنید.`
        : "<strong>هیچ صندوق طلایی یافت نشد.</strong>");
    $("goldTable").innerHTML = "";
    return;
  }

  const navMissing = c.with_data - (c.with_nav || 0);

  banner("goldStatus", "goldStatusText", "is-live",
    `<strong>${c.with_data} صندوق طلا</strong> با داده` +
    (c.missing ? ` (${c.missing} منتظر دریافت)` : "") +
    (navMissing > 0
      ? ` — NAV برای ${navMissing} صندوق نیامد` +
        (rep.nav_error ? ` (${esc(rep.nav_error)})` : "")
      : " — NAV زنده از TSETMC") +
    `. ⚠ ${esc(rep.caveat || "")}`);

  const callBadge = (call, fa) =>
    `<span class="badge ${CALL_CLASS[call] || "neutral"}">` +
    `<span class="dot"></span>${esc(fa)}</span>`;

  // The حباب cell: the ratio the user asked for, with the premium it implies
  // spelled out underneath. Coloured only outside ±BUBBLE_BAND, because a
  // fund half a percent off its NAV is at its NAV, and painting that red would
  // turn rounding into a verdict.
  const band = rep.nav_band || 0.02;

  const bubbleCell = (r) => {
    if (r.price_to_nav === null || r.price_to_nav === undefined) {
      return r.nav_error
        ? `<span class="muted" title="${esc(r.nav_error)}">—</span>`
        : "—";
    }
    const b = r.bubble;
    const cls = b > band ? "down" : b < -band ? "up" : "muted";
    // The sign is spelled out rather than printed as + / -. A leading minus is
    // a bidi-neutral character: in this RTL cell it resolves to the far end of
    // the run, so "-1.16٪ حباب" renders with the sign detached from its
    // number and a discount reads exactly like a premium. مثبت / منفی cannot
    // move.
    const word = b > 0 ? "حباب مثبت" : b < 0 ? "حباب منفی" : "بدون حباب";
    return `<span class="nav-ratio">${r.price_to_nav.toFixed(4)}</span>` +
      `<span class="nav-bubble ${cls}">` +
      (b === 0 ? word
        : `${(Math.abs(b) * 100).toFixed(2)}٪ ${word}`) + `</span>`;
  };

  const body = rows.map(r => [
    `<a href="?tech=${encodeURIComponent(r.symbol || "")}"
        title="تحلیل تک‌نماد">${esc(r.symbol || "—")}</a>`,
    esc((r.name || "").slice(0, 30)),
    // The live پایانی when the pull got one, else the last stored bar. The
    // tooltip names the source, so a price and a NAV struck at different
    // moments can never be mistaken for a matched pair.
    r.day_close != null
      ? `<span title="قیمت پایانی امروز — از TSETMC">${num(r.day_close, 0)}</span>`
      : `<span class="muted" title="آخرین کندل ذخیره‌شده (${esc(r.as_of || "؟")})">` +
        `${num(r.close, 0)}</span>`,
    r.nav != null
      ? `<span title="NAV ابطال؛ صدور: ${num(r.nav_issue, 0)}">` +
        `${num(r.nav, 0)}</span>`
      : "—",
    bubbleCell(r),
    num(r.rsi, 1),
    num(r.macd, 2),
    esc(r.trend || "—"),
    callBadge(r.call, r.call_fa),
    esc(r.as_of || "—")
  ]);

  $("goldTable").innerHTML = table(
    ["نماد", "نام", "قیمت روز", "NAV ابطال", "قیمت / NAV",
     "RSI", "MACD", "روند", "سیگنال", "تا تاریخ"], body);
}

async function loadGold(force = false) {
  const btn = $("runGoldBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تحلیل…"; }

  try {
    const rep = await getJSON(`/api/gold?${query()}${force ? "&refresh=1" : ""}`);
    renderGold(rep);
  } catch (err) {
    state.gold = null;
    banner("goldStatus", "goldStatusText", "is-error",
      `<strong>تحلیل نشد.</strong> ${esc(err.message)}`);
    $("goldTable").innerHTML = "";
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
  }
}

async function importGold() {
  const btn = $("importGoldBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال دریافت…"; }
  banner("goldStatus", "goldStatusText", "is-loading",
    "دریافت تاریخچه‌ی صندوق‌های طلا …");

  try {
    const res = await getJSON("/api/import/gold", { method: "POST" });
    banner("goldStatus", "goldStatusText", "is-live",
      `<strong>${res.stored} صندوق دریافت شد.</strong> در حال تحلیل…`);
    await loadGold(true);
  } catch (err) {
    banner("goldStatus", "goldStatusText", "is-error",
      `<strong>دریافت نشد.</strong> ${esc(err.message)}`);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "↓ دریافت تاریخچه"; }
  }
}

/* ---------- market intelligence (هوش بازار) ----------
   A REPORT, never a signal. This panel reads /api/intelligence, which is
   isolated from every trading route; nothing here produces a buy/sell. */

const TONE_FA = { up: "مثبت", down: "منفی", neutral: "خنثی" };

function whenText(iso) {
  if (!iso) return "بدون تاریخ";
  try {
    return new Date(iso).toLocaleString("fa-IR", {
      timeZone: "Asia/Tehran", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit"
    });
  } catch (e) {
    return iso.slice(0, 16).replace("T", " ");
  }
}

function toneDot(tone) {
  const cls = tone === "up" ? "up" : tone === "down" ? "down" : "flat";
  return `<span class="tone-dot ${cls}" title="${TONE_FA[tone] || ""}"></span>`;
}

// A link from a feed or a filing, made safe to put in href: esc() stops a
// quote breaking out of the attribute but lets "javascript:" through, and a
// relative path (12 stored EIA links) would resolve against this panel.
function safeUrl(url) {
  const text = String(url || "").trim();
  return /^https?:\/\//i.test(text) ? text : "#";
}

function intelBadges(e, opts = {}) {
  const b = [];
  if (e.is_price_forecast && !opts.hideForecast)
    b.push(`<span class="intel-badge forecast">پیش‌بینی قیمت</span>`);
  else if (e.is_analysis)
    b.push(`<span class="intel-badge analysis">تحلیلی</span>`);
  if (opts.showMarkets && (e.market_labels || []).length)
    b.push(`<span class="intel-badge market">${esc(e.market_labels.join("، "))}</span>`);
  return b.join("");
}

function intelItem(e, opts = {}) {
  const title = e.link
    ? `<a href="${esc(safeUrl(e.link))}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>`
    : esc(e.title);
  const srcs = (e.sources || []).slice(0, 3).join("، ");
  const cats = (e.category_labels || []).slice(0, 2).join("، ");
  const corr = e.corroboration > 1
    ? ` · <b class="intel-corr">${e.corroboration} منبع</b>` : "";
  const meta = [esc(srcs), whenText(e.published_at), esc(cats)]
    .filter(Boolean).join(" · ");
  return `<li class="intel-item">
    <div class="intel-title">${toneDot(e.tone)}${title} ${intelBadges(e, opts)}</div>
    <div class="intel-meta">${meta}${corr}</div>
  </li>`;
}

function outlookLine(o) {
  if (!o) return "";
  const cls = o.lean === "supportive" ? "up"
    : o.lean === "pressure" ? "down" : "muted";
  const drivers = (o.drivers || []).length
    ? ` — محرک‌ها: ${esc(o.drivers.join("، "))}` : "";
  const counts = o.total
    ? ` <span class="muted">(▲${o.up} ▼${o.down} از ${o.total})</span>` : "";
  return `<div class="intel-outlook">
    <span class="intel-outlook-tag">چشم‌انداز خبری:</span>
    <span class="${cls}">${esc(o.lean_label)}</span>${drivers}${counts}
  </div>`;
}

function intelColumn(sec) {
  const t = sec.tone || {};
  const list = (sec.events || []).length
    ? (sec.events || []).map(e => intelItem(e)).join("")
    : `<li class="muted">موردی در این بازه نیست.</li>`;
  return `<div class="intel-col">
    <div class="intel-col-head">
      <span class="intel-col-name">${esc(sec.label)}</span>
      <span class="intel-col-tone">
        <span class="up">▲${t.up || 0}</span>
        <span class="down">▼${t.down || 0}</span>
        <span class="muted">●${t.neutral || 0}</span>
      </span>
      <span class="muted">(${sec.economic || 0}/${sec.total || 0})</span>
    </div>
    ${outlookLine(sec.outlook)}
    <ul class="intel-list">${list}</ul>
  </div>`;
}

function renderIntelligence(rep) {
  state.intel = rep;

  const o = rep.overview || {};
  const bm = o.by_market || {};
  const tn = o.tone || {};

  // The disclaimer is not decorative -- it is the scope of the whole panel.
  const disc = $("intelDisclaimer");
  if (disc) disc.textContent = "ℹ " + (rep.disclaimer || "");

  const rng = o.time_range || {};
  banner("intelStatus", "intelStatusText", "is-live",
    `<strong>${o.forecasts || 0} پیش‌بینی قیمت</strong> · ${o.economic || 0} خبر ` +
    `اقتصادی از ${o.articles || 0} خبر · بازه‌ی ${rep.window_hours || 0} ساعت` +
    (rng.to ? ` · تازه‌ترین: ${whenText(rng.to)}` : ""));

  // overview tiles -- economic and forecast counts lead, tone lives in outlooks
  const tiles = [
    ["خبر", o.articles || 0],
    ["رویداد", o.events || 0],
    ["اقتصادی", o.economic || 0],
    ["پیش‌بینی قیمت", o.forecasts || 0],
    ["تحلیلی", o.analysis || 0],
    ["نفت", bm.oil || 0],
    ["طلا", bm.gold || 0],
    ["دلار", bm.fx || 0],
    ["بورس", bm.tse || 0],
  ];
  $("intelOverview").innerHTML = tiles.map(([k, v]) =>
    `<div class="intel-tile"><div class="intel-tile-v">${v}</div>` +
    `<div class="intel-tile-k">${k}</div></div>`).join("");

  // PRICE FORECASTS -- first, per the user's priority
  const fc = rep.price_forecasts || [];
  const fcNode = $("intelForecasts");
  if (fcNode) {
    fcNode.innerHTML =
      `<div class="intel-fc-head">🔮 پیش‌بینی قیمت — نفت، طلا، دلار، سهام` +
      ` <span class="muted">(${fc.length})</span></div>` +
      (fc.length
        ? `<ul class="intel-list">${fc.map(e =>
            intelItem(e, { showMarkets: true, hideForecast: true })).join("")}</ul>`
        : `<div class="muted intel-fc-empty">در این بازه خبرِ پیش‌بینی قیمتی یافت نشد. ` +
          `بازه را بلندتر کنید یا «دریافت اخبار جدید» را بزنید.</div>`);
  }

  // themes chips
  const themes = o.themes || [];
  $("intelThemes").innerHTML = themes.length
    ? `<span class="muted">موضوع‌های پرتکرار:</span> ` + themes.map(t =>
      `<span class="chip">${esc(t.label)} <b>${t.count}</b></span>`).join(" ")
    : "";

  // market columns (oil, gold, dollar, stocks), each with its news outlook
  const order = rep.market_order || ["oil", "gold", "fx", "tse"];
  const m = rep.markets || {};
  $("intelMarkets").innerHTML =
    order.map(k => m[k]).filter(Boolean).map(intelColumn).join("");

  // source health -- ok / failed / html-only / unavailable, all on record
  const s = rep.sources || {};
  const okNames = (s.ok || []).map(x => x.name);
  const failed = (s.failed || []).map(x => `${x.name} (${x.status})`);
  const html = (s.html_only || []).map(x => x.name);
  const unavail = (s.unavailable || []).map(x => `${x.name} — ${x.reason}`);
  $("intelSources").innerHTML =
    `<div class="intel-src-line"><b>${okNames.length} منبع فعال:</b> ` +
    `<span class="muted">${esc(okNames.join("، "))}</span></div>` +
    (failed.length ? `<div class="intel-src-line down"><b>ناموفق:</b> ${esc(failed.join("، "))}</div>` : "") +
    (html.length ? `<div class="intel-src-line"><b>فقط HTML (بدون فید):</b> <span class="muted">${esc(html.join("، "))}</span></div>` : "") +
    (unavail.length ? `<div class="intel-src-line"><b>در دسترس نیست (ثبت‌شده، جعل‌نشده):</b> <span class="muted">${esc(unavail.join("؛ "))}</span></div>` : "");
}

async function loadIntelligence(force = false) {
  const win = $("intelWindow")?.value || 72;
  const btn = $("runIntelBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال بارگذاری…"; }
  try {
    const rep = await getJSON(
      `/api/intelligence?window=${win}&top=20${force ? "&refresh=1" : ""}`);
    renderIntelligence(rep);
  } catch (err) {
    state.intel = null;
    banner("intelStatus", "intelStatusText", "is-error",
      `<strong>گزارش ساخته نشد.</strong> ${esc(err.message)}`);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "↻ گزارش"; }
  }
}

async function fetchIntelligence() {
  const btn = $("fetchIntelBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال دریافت…"; }
  banner("intelStatus", "intelStatusText", "is-loading",
    "در حال دریافت و دسته‌بندی اخبار از همه‌ی منابع …");
  try {
    const res = await getJSON("/api/intelligence/fetch", { method: "POST" });
    banner("intelStatus", "intelStatusText", "is-live",
      `<strong>${res.inserted} خبر تازه</strong> (${res.updated} به‌روزرسانی) ` +
      `از ${res.sources_ok}/${res.sources_ok + res.sources_failed} منبع. در حال ساخت گزارش…`);
    await loadIntelligence(true);
  } catch (err) {
    banner("intelStatus", "intelStatusText", "is-error",
      `<strong>دریافت نشد.</strong> ${esc(err.message)}`);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "↓ دریافت اخبار جدید"; }
  }
}

/* ---------- sectors: fundamental and technical, separately ---------- */

function renderSectors(rep) {
  state.sectors = rep;

  const list = rep.sectors || [];

  if (!list.length) {
    banner("sectorStatus", "sectorStatusText", "is-error",
      `<strong>داده‌ی صنایع موجود نیست.</strong> ${esc((rep.warnings || []).join(" "))}` +
      bootstrapNote(rep));
    ["sectorFundChart", "sectorApprovalChart", "sectorTechChart", "sectorBreadthChart"]
      .forEach(id => { $(id).innerHTML = '<div class="empty">داده‌ای نیست</div>'; });
    $("sectorFundTable").innerHTML = "";
    $("sectorTechTable").innerHTML = "";
    return;
  }

  const cov = rep.coverage || {};

  banner("sectorStatus", "sectorStatusText", "is-live",
    `<strong>${list.length} صنعت تحلیل شد.</strong> ` +
    `${cov.with_sector || 0} از ${cov.equities || 0} سهم صنعت دارند.` +
    ((rep.warnings || []).length ? ` ${esc(rep.warnings.join(" "))}` : ""));

  // ---- fundamental ----

  const byFund = list
    .filter(s => s.fundamental && s.fundamental.median_score != null)
    .slice()
    .sort((a, b) => b.fundamental.median_score - a.fundamental.median_score);

  hBarChart($("sectorFundChart"), {
    data: byFund.slice(0, 14).map(s => ({
      label: s.sector_name.slice(0, 18),
      value: s.fundamental.median_score
    })),
    max: 100,
    format: (v) => fmt(v, 0)
  });

  const byApproval = list
    .filter(s => s.fundamental && s.fundamental.approval_rate != null)
    .slice()
    .sort((a, b) => b.fundamental.approval_rate - a.fundamental.approval_rate);

  hBarChart($("sectorApprovalChart"), {
    data: byApproval.slice(0, 14).map(s => ({
      label: s.sector_name.slice(0, 18),
      value: s.fundamental.approval_rate * 100
    })),
    max: 100,
    format: (v) => `${fmt(v, 0)}٪`
  });

  $("sectorFundTable").innerHTML = table(
    ["صنعت", "تعداد", "میانه‌ی بنیادی", "نرخ تأیید", "P/E صنعت", "میانه‌ی P/E", "ارزش بازار"],
    list.map((s, i) => {
      const f = s.fundamental;
      return [
        `<a href="#" class="link sector-row" data-index="${i}">${esc(s.sector_name)}</a>`,
        f.constituents,
        f.median_score ?? "—",
        f.approval_rate == null ? "—" : `${(f.approval_rate * 100).toFixed(0)}٪`,
        f.published_sector_pe ?? "—",
        f.median_pe ?? "—",
        money(f.total_market_cap)
      ];
    })
  );

  document.querySelectorAll(".sector-row").forEach(node =>
    node.addEventListener("click", (event) => {
      event.preventDefault();
      showSectorMembers(list[Number(node.dataset.index)]);
    }));

  // ---- technical ----

  const scored = list.filter(s => s.technical && !s.technical.error);

  hBarChart($("sectorTechChart"), {
    data: scored
      .slice()
      .sort((a, b) => b.technical.score - a.technical.score)
      .slice(0, 14)
      .map(s => ({ label: s.sector_name.slice(0, 18), value: s.technical.score })),
    max: 100,
    format: (v) => fmt(v, 0)
  });

  const withBreadth = list.filter(s => s.breadth);

  hBarChart($("sectorBreadthChart"), {
    data: withBreadth
      .slice()
      .sort((a, b) => b.breadth.pct_above_slow_ma - a.breadth.pct_above_slow_ma)
      .slice(0, 14)
      .map(s => ({
        label: s.sector_name.slice(0, 18),
        value: s.breadth.pct_above_slow_ma
      })),
    max: 100,
    format: (v) => `${fmt(v, 0)}٪`
  });

  $("sectorTechTable").innerHTML = table(
    ["صنعت", "امتیاز تکنیکال", "روند", "عمق", "۶۰ روزه", "روش شاخص"],
    scored
      .slice()
      .sort((a, b) => b.technical.score - a.technical.score)
      .map(s => {
        const t = s.technical;
        const b = s.breadth;
        return [
          esc(s.sector_name),
          t.score,
          esc(TREND_FA[t.trend] || t.trend || "—"),
          b ? `${b.pct_above_slow_ma}٪` : "—",
          signed(t.returns ? t.returns["60d"] : null),
          esc(s.index_method_fa || s.index_method || "—")
        ];
      })
  );
}

function showSectorMembers(sector) {
  const wrap = $("sectorMembersTable");
  wrap.classList.remove("hidden");

  wrap.innerHTML =
    `<p class="hint"><strong>${esc(sector.sector_name)}</strong> — ${esc(sector.index_method_fa || sector.index_method)}</p>` +
    table(
      ["نماد", "شرکت", "امتیاز بنیادی", "حکم", "P/E", "بازده سود", "ارزش معاملات", "۶۰ روزه", "کندل"],
      sector.members.map(m => [
        esc(m.symbol), esc(m.name || "—"),
        // An unjudgeable share carries a score the model could not stand
        // behind; showing the number would rank it as if it could.
        m.scored ? m.fundamental_score : "—",
        m.verdict, m.pe ?? "—",
        m.earnings_yield == null ? "—" : `${m.earnings_yield}٪`,
        money(m.avg_value_20), signed(m.return_60d), m.bars
      ])
    );

  wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function loadSectors(force = false) {
  const btn = $("runSectorsBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تحلیل…"; }
  setTask("sectors", "running", "تجمیع اعضا در شاخص صنعت");

  try {
    const rep = await getJSON(`/api/sectors?${query()}${force ? "&refresh=1" : ""}`);
    renderSectors(rep);
    const n = (rep.sectors || []).length;
    setTask("sectors", n ? "done" : "failed",
      n ? `${n} صنعت تحلیل شد` : ((rep.warnings || [])[0] || "داده‌ی صنعت نیست"),
      n ? String(n) : "");
  } catch (err) {
    state.sectors = null;
    banner("sectorStatus", "sectorStatusText", "is-error",
      `<strong>تحلیل صنایع شکست خورد.</strong> ${esc(err.message)}`);
    setTask("sectors", "failed", err.message);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
  }
}

/* ---------- fundamentals (کدال) ---------- */

const FUND_PILLARS = [
  "Profitability", "Growth", "Valuation",
  "Financial health", "Cash flow", "Earnings quality"
];

// No green on a stock-level reading (STATUS_CLASS): a "Strong" in green read
// as a buy, and no ranking here beat the index (the full test, 2026-11-12).
const FUND_FA = { Strong: "قوی", Good: "خوب", Fair: "متوسط", Weak: "ضعیف", Poor: "بسیار ضعیف" };

function fundBadge(status) {
  const cls = status === "Strong" || status === "Good" ? "neutral"
    : status === "Fair" ? "warning"
    : status === "Weak" ? "serious" : "critical";
  return `<span class="badge ${cls}" title="${esc(String(status ?? ""))}"><span class="dot"></span>` +
         `${esc(FUND_FA[status] || status || "—")}</span>`;
}

// An empty P/E said nothing about why. Three different facts sit behind it,
// and the reader asked (2026-11-05, about the تهران symbols).
function peCell(x) {
  if (x.pe != null) return num(x.pe, 1);
  if (x.pe_basis === "ttm" && x.ttm_profit != null && x.ttm_profit <= 0) {
    return '<span class="hint" title="سود ۱۲ ماه گذشته‌ی سهامداران منفی است">زیان‌ده (۱۲ ماه)</span>';
  }
  if (x.eps_annual != null && x.eps_annual <= 0) {
    return '<span class="hint" title="سود هر سهم منفی است؛ P/E برای شرکت زیان‌ده معنا ندارد">زیان‌ده</span>';
  }
  if (x.market_cap == null) {
    return '<span class="hint" title="نماد امروز معامله نشده یا متوقف است، پس قیمت روز ندارد">بدون قیمت</span>';
  }
  // ratios() leaves an earnings yield only when the P/E was dropped as absurd
  // (above 60). With P/E struck on owners' profit, a positive filed EPS beside
  // a loss now has neither -- and "above 60" would call a loss "profit
  // negligible against price".
  if (x.earnings_yield != null) {
    return '<span class="hint" title="P/E بالای ۶۰ — سود نسبت به قیمت ناچیز است">بالای ۶۰</span>';
  }
  return '<span class="hint" title="سود ۱۲ ماهه‌ی سهامداران مثبت نیست یا با سود هر سهم نمی‌خواند">نامعلوم</span>';
}

// TSETMC's own P/E beside ours: where they part by half, the row's warning
// says why (consolidated vs separate, a one-off gain).
function publishedPeCell(x) {
  if (x.pe_published == null) return "—";
  const off = x.pe != null && x.pe_published > 0 &&
    (x.pe / x.pe_published < 0.67 || x.pe / x.pe_published > 1.5);
  return off ? `<span class="down">${num(x.pe_published, 1)}</span>` : num(x.pe_published, 1);
}

function fundRows(list) {
  return table(
    ["نماد", "صنعت", "بنیاد", "امتیاز", "پوشش", "P/E (۱۲ ماه)", "P/E بورس", "P/B", "ROE",
     "حاشیه خالص", "رشد فروش", "بدهی", "کیفیت سود", "دوره", "هشدار"],
    list.map(r => {
      const x = r.ratios || {};
      // How much of the six-pillar model actually ran. A 90 built from two
      // pillars and a 90 built from six are not the same claim, and without
      // this column they look identical.
      const covered = Math.round((r.weight_covered || 0) * 100);
      const coverCls = covered >= 80 ? "" : covered >= 55 ? "warning" : "serious";
      // A quarterly filing is annualised for valuation; saying so on the row
      // is the difference between a P/E the reader can check and one they
      // cannot reconcile against the statement.
      const period = `${esc(r.period_end || "—")}` +
        (r.period_months ? ` / ${r.period_months}m` : "") +
        (r.audited === true ? " ✓" : r.audited === false ? " ~" : "");
      return [
        `<strong>${esc(r.symbol || "—")}</strong>`,
        esc((r.sector_name || "—").slice(0, 20)),
        fundBadge(r.status),
        r.score,
        coverCls
          ? `<span class="badge ${coverCls}"><span class="dot"></span>${covered}٪</span>`
          : `${covered}٪`,
        peCell(x) + (x.pe != null && x.pe_basis === "annualised"
          ? ' <span class="hint" title="دوره‌ی آخر سالانه‌شده؛ سال قبلِ قابل‌مقایسه ذخیره نشده">*</span>' : ""),
        publishedPeCell(x),
        num(x.pb, 1),
        x.roe == null ? "—" : `${num(x.roe, 0)}٪`,
        x.net_margin == null ? "—" : `${num(x.net_margin, 0)}٪`,
        signed(x.revenue_growth, 0),
        x.debt_ratio == null ? "—" : `${num(x.debt_ratio, 0)}٪`,
        num(x.earnings_quality, 2),
        period,
        (r.warnings || []).length
          ? (r.warnings || []).map(w => `<span class="hint">⚠ ${esc(w)}</span>`).join("<br>")
          : "—"
      ];
    })
  );
}

function renderRoutes(routes, coverage) {
  const node = $("codalRoutes");
  if (!node) return;

  const entries = Object.entries(routes || {});

  if (!entries.length) {
    node.innerHTML = '<div class="empty">هنوز مسیری کشف نشده</div>';
    return;
  }

  node.innerHTML = table(
    ["میزبان", "مسیر", "معنی"],
    entries.map(([host, ip]) => [
      esc(host),
      ip ? `<code>${esc(ip)}</code>` : "مسیر پیش‌فرض",
      ip ? "از کارت شبکه‌ی فیزیکی — تونل به آن نمی‌رسد"
         : "از مسیر پیش‌فرض سیستم"
    ])
  );
}

function renderFundamentals(rep) {
  state.fundamentals = rep;

  const top = rep.top || [];
  const cov = rep.coverage || {};
  const counts = rep.counts || {};

  if (!top.length) {
    banner("fundStatus", "fundStatusText", "is-error",
      `<strong>هنوز صورت مالی‌ای ذخیره نشده.</strong> ` +
      `${cov.with_financials || 0} از ${cov.equities || 0} شرکت داده دارند، ` +
      `${cov.checked || 0} شرکت بررسی شده. دکمه‌ی «دریافت کدال» را بزنید — ` +
      `دریافت در پس‌زمینه انجام می‌شود و اسکن را متوقف نمی‌کند.` +
      bootstrapNote(rep));
    $("fundTopChart").innerHTML = '<div class="empty">داده‌ای نیست</div>';
    $("fundTable").innerHTML = "";
    $("fundWeakTable").innerHTML = "";
    tiles("fundKpis", []);
    return;
  }

  banner("fundStatus", "fundStatusText", "is-live",
    `<strong>${counts.scored} شرکت از صورت‌های مالی کدال امتیاز گرفتند.</strong> ` +
    `${cov.with_financials || 0} از ${cov.equities || 0} شرکت داده دارند ` +
    `(${cov.periods || 0} دوره، ${cov.letters || 0} اطلاعیه). ` +
    `میانه‌ی امتیاز ${rep.median_score ?? "—"}.`);

  tiles("fundKpis", [
    { k: "شرکت امتیازگرفته", v: counts.scored,
      sub: `از ${cov.equities || 0} سهم بازار` },
    { k: "بنیاد قوی", v: counts.strong, sub: "امتیاز ۸۰ به بالا" },
    { k: "بنیاد ضعیف", v: counts.weak, sub: "امتیاز زیر ۴۵" },
    { k: "متوقف، کنار گذاشته", v: counts.halted ?? 0, sub: "بیش از یک ماه بدون معامله" },
    { k: "مالی و سرمایه‌گذاری، جدا", v: num(counts.asset_valued ?? 0, 0),
      sub: "با P/NAV و P/B در کارت چشم‌انداز سنجیده می‌شوند، نه با سود" },
    { k: "کم‌معامله، جدا", v: num(counts.thin ?? 0, 0),
      sub: "زیر کف نقدشوندگی؛ رتبه‌بندی نمی‌شوند" },
    { k: "میانه‌ی امتیاز", v: rep.median_score ?? "—",
      sub: "نقطه‌ی میانی بازارِ پوشش‌داده‌شده" },
    { k: "دوره‌های ذخیره‌شده", v: cov.periods || 0,
      sub: `${cov.letters || 0} اطلاعیه فهرست شده` },
    { k: "پوشش", v: `${cov.with_financials || 0}/${cov.equities || 0}`,
      sub: "بقیه در پس‌زمینه دریافت می‌شوند" }
  ]);

  hBarChart($("fundTopChart"), {
    data: top.slice(0, 14).map(r => ({ label: r.symbol, value: r.score })),
    max: 100,
    format: (v) => fmt(v, 0),
    tooltip: (d) => {
      const row = top.find(r => r.symbol === d.label) || {};
      const x = row.ratios || {};
      return `<div class="t-title">${esc(d.label)}</div>
        <div class="t-row"><span>امتیاز بنیادی</span><b>${row.score}</b></div>
        <div class="t-row"><span>ROE</span><b>${num(x.roe, 0)}٪</b></div>
        <div class="t-row"><span>P/E</span><b>${num(x.pe, 1)}</b></div>
        <div class="t-row"><span>رشد فروش</span><b>${num(x.revenue_growth, 0)}٪</b></div>
        <div class="t-row"><span>پوشش مدل</span><b>${Math.round((row.weight_covered || 0) * 100)}٪</b></div>`;
    }
  });

  // Below the liquidity floor: scored, but not ranked -- a strong company at
  // a price nobody can deal at is not a buy (شصفها, 2026-11-11).
  const thin = (rep.thin || []).map(x =>
    `${esc(x.symbol)} <span class="hint">(امتیاز ${num(x.score, 0)}، ${money(x.avg_value)} در روز)</span>`).join("، ");
  const thinMore = (counts.thin || 0) - (rep.thin || []).length;
  $("fundTable").innerHTML = fundRows(top) +
    (thin ? `<p class="hint" style="margin-top:12px">⚠ کم‌معامله — رتبه‌بندی نشد ` +
      `(میانگین معاملات زیر ${money(rep.liquidity_floor)} ریال در روز؛ به قیمت تابلو نمی‌شود خرید): ${thin}` +
      (thinMore > 0 ? ` و ${num(thinMore, 0)} نماد دیگر` : "") + `</p>` : "");
  $("fundWeakTable").innerHTML =
    `<p class="hint">${(rep.bottom || []).length} شرکت با ضعیف‌ترین بنیاد.</p>` +
    fundRows(rep.bottom || []);
}

async function loadFundamentals(force = false) {
  const btn = $("runFundBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تحلیل…"; }
  setTask("fundamentals", "running", "خواندن صورت‌های مالی ذخیره‌شده");

  try {
    const rep = await getJSON(`/api/fundamentals?top=40${force ? "&refresh=1" : ""}`);
    renderFundamentals(rep);

    // The route table lives on the Codal endpoint, not this one -- it is
    // about the connection rather than the data.
    try {
      const codal = await getJSON("/api/codal");
      renderRoutes(codal.routes, codal.coverage);
      state.codal = codal;
    } catch (err) { console.warn("codal status unavailable", err); }

    const n = (rep.counts || {}).scored || 0;
    setTask("fundamentals", n ? "done" : "failed",
      n ? `${n} شرکت امتیاز گرفت` : "هنوز صورت مالی‌ای ذخیره نشده",
      n ? String(n) : "");
  } catch (err) {
    state.fundamentals = null;
    banner("fundStatus", "fundStatusText", "is-error",
      `<strong>تحلیل بنیادی شکست خورد.</strong> ${esc(err.message)}`);
    setTask("fundamentals", "failed", err.message);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
  }
}

/* ---------- money flow (حقیقی / حقوقی) ---------- */

// A flow score is a 0-100 like every other score on this page, but the bands
// mean something different: 50 is the market's own middle, not a pass mark.
function flowBadge(label) {
  const cls = label === "ورود پول حقیقی" ? "neutral"
    : label === "خروج پول حقیقی" ? "critical" : "warning";
  return `<span class="badge ${cls}"><span class="dot"></span>${esc(label)}</span>`;
}

function flowRows(list) {
  return table(
    ["نماد", "صنعت", "حکم", "امتیاز", "قدرت خریدار", "خالص حقیقی", "٪ از گردش",
     "جلسات مثبت", "گردش دوره"],
    list.map(r => [
      esc(r.symbol || "—"),
      esc((r.sector_name || "—").slice(0, 22)),
      flowBadge(r.label),
      r.score,
      // قدرت خریدار has no meaning where one side had no participants, and a
      // dash says that far better than a 0.00 the reader would compare.
      r.power_ratio == null ? "—" : r.power_ratio.toFixed(2),
      money(r.net_individual),
      signed(r.net_pct, 1),
      `${r.sessions_positive}/${state.flow?.window ?? 5}`,
      money(r.turnover)
    ])
  );
}

function renderMoneyflow(rep) {
  state.flow = rep;

  const inflow = rep.inflow || [];
  const outflow = rep.outflow || [];
  const market = rep.market;
  const counts = rep.counts || {};
  const cov = rep.coverage || {};

  const clear = () => {
    ["flowNetChart", "flowPowerChart"].forEach(id => {
      const n = $(id);
      if (n) n.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    $("flowInflowTable").innerHTML = "";
    $("flowOutflowTable").innerHTML = "";
    tiles("flowKpis", []);
  };

  if (!market || !inflow.length) {
    banner("flowStatus", "flowStatusText", "is-error",
      `<strong>داده‌ی حقیقی/حقوقی موجود نیست.</strong> ` +
      `${cov.with_flow || 0} از ${cov.equities || 0} سهم داده دارند. ` +
      `دکمه‌ی «دریافت حقیقی/حقوقی» را بزنید.` + bootstrapNote(rep));
    clear();
    return;
  }

  banner("flowStatus", "flowStatusText", "is-live",
    `<strong>${counts.scored} نماد در ${rep.window} جلسه‌ی اخیر سنجیده شد</strong> ` +
    `(${counts.liquid} نماد بالای کف نقدشوندگی ${money(rep.min_turnover)} ریال). ` +
    `داده تا ${esc(cov.last_date || "—")}، ` +
    `${cov.with_flow || 0} از ${cov.equities || 0} سهم پوشش دارند.` +
    (counts.young
      ? ` ${num(counts.young, 0)} عرضه‌ی اولیه (کمتر از ۶۰ جلسه معامله) از فهرست‌ها کنار ` +
        "گذاشته شد: خرید حقیقی آن‌ها سهمیه‌ی عرضه و صف است، نه پول هوشمند."
      : ""));

  tiles("flowKpis", [
    {
      k: "خالص پول حقیقی بازار",
      v: signed(market.net_pct, 1),
      sub: `${money(market.net_individual)} ریال روی ${market.instruments} نماد`
    },
    {
      k: "قرینه‌ی حقوقی",
      v: signed(-market.net_pct, 1),
      sub: "همان عدد با علامت معکوس — نه تأیید دوم"
    },
    {
      k: "گستردگی",
      v: `${market.breadth_pct.toFixed(0)}٪`,
      sub: `${market.inflow_count} از ${market.instruments} نماد ورود پول داشتند`
    },
    {
      k: "گردش دوره",
      v: money(market.turnover),
      sub: `مجموع ${rep.window} جلسه`
    },
    {
      k: "پوشش داده",
      v: `${cov.with_flow || 0}/${cov.equities || 0}`,
      sub: `${(cov.rows || 0).toLocaleString("en")} سطر جلسه‌ای`
    },
    {
      k: "داده‌ی ناکافی",
      v: counts.insufficient ?? 0,
      sub: `کمتر از حداقل جلسات لازم`
    }
  ]);

  // Inflow and outflow on one axis: the reader wants to know which way the
  // market leans, and two separate charts with independent scales hide that.
  // Six a side, not more -- Persian symbol names are wide enough that a
  // sixteen-bar version ran its axis labels into each other.
  const extremes = [
    ...inflow.slice(0, 6),
    ...outflow.slice(0, 6).slice().reverse()
  ];

  divergingBar($("flowNetChart"), {
    data: extremes.map(r => ({ label: r.symbol, value: r.net_pct })),
    height: 260,
    format: (v) => `${fmt(v, 0)}٪`,
    tooltip: (d) => {
      const row = extremes.find(r => r.symbol === d.label) || {};
      return `<div class="t-title">${esc(d.label)}</div>
        <div class="t-row"><span>خالص حقیقی</span><b>${money(row.net_individual)}</b></div>
        <div class="t-row"><span>٪ از گردش</span><b>${num(row.net_pct, 1)}٪</b></div>
        <div class="t-row"><span>قدرت خریدار</span><b>${row.power_ratio ?? "—"}</b></div>
        <div class="t-row"><span>امتیاز جریان</span><b>${row.score}</b></div>`;
    }
  });

  const power = rep.strongest_power || [];

  hBarChart($("flowPowerChart"), {
    data: power.slice(0, 12).map(r => ({
      label: r.symbol, value: r.power_ratio
    })),
    format: (v) => fmt(v, 1),
    tooltip: (d) => {
      const row = power.find(r => r.symbol === d.label) || {};
      return `<div class="t-title">${esc(d.label)}</div>
        <div class="t-row"><span>قدرت خریدار</span><b>${num(row.power_ratio, 2)}</b></div>
        <div class="t-row"><span>٪ از گردش</span><b>${num(row.net_pct, 1)}٪</b></div>
        <div class="t-row"><span>امتیاز جریان</span><b>${row.score}</b></div>`;
    }
  });

  $("flowInflowTable").innerHTML =
    `<p class="hint">${inflow.length} نماد با بیشترین ورود پول حقیقی.</p>` +
    flowRows(inflow);

  $("flowOutflowTable").innerHTML =
    `<p class="hint">${outflow.length} نماد با بیشترین خروج پول حقیقی.</p>` +
    flowRows(outflow);
}

async function loadMoneyflow(force = false) {
  const btn = $("runFlowBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال تحلیل…"; }
  setTask("flow", "running", "سنجش حقیقی/حقوقی");

  try {
    const rep = await getJSON(`/api/moneyflow?top=25${force ? "&refresh=1" : ""}`);
    renderMoneyflow(rep);
    const n = (rep.counts || {}).scored || 0;
    setTask("flow", n ? "done" : "failed",
      n ? `${n} نماد سنجیده شد` : "داده‌ی حقیقی/حقوقی نیست",
      n ? String(n) : "");
  } catch (err) {
    state.flow = null;
    banner("flowStatus", "flowStatusText", "is-error",
      `<strong>تحلیل جریان پول شکست خورد.</strong> ${esc(err.message)}`);
    setTask("flow", "failed", err.message);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
  }
}

/* ---------- full-market screen ---------- */

function verdictBadge(v) {
  const cls = v === "PROVISIONAL" ? "warning"
    : v === "REJECTED" ? "critical" : "neutral";
  return `<span class="badge ${cls}" title="${esc(String(v ?? ""))}"><span class="dot"></span>${esc(STATUS_FA[v] || v || "—")}</span>`;
}

function renderScreen(rep) {
  state.screen = rep;

  renderBanner(rep);

  const c = rep.counts;

  const passed = c.approved + c.provisional;

  if (c.universe === 0) {
    banner("screenStatus", "screenStatusText", "is-error",
      `<strong>هیچ نمادی بارگذاری نشده.</strong> «ورود نمادها» را بزنید.`);
  } else if (c.approved > 0) {
    banner("screenStatus", "screenStatusText", "is-live",
      `<strong>${c.approved} از ${c.universe} سهم از غربال کامل بنیادی عبور کردند</strong>، ` +
      `و ${c.technically_analysed} سهم تحلیل تکنیکال شدند.`);
  } else {
    banner("screenStatus", "screenStatusText", null,
      `<strong>غربال ناقص.</strong> ` +
      `${c.insufficient} از ${c.universe} سهم سابقه‌ی قیمت ندارند، پس به‌عنوان ` +
      `<em>داده‌ی ناکافی</em> گزارش شدند نه ردشده. ` +
      `${c.provisional} سهم فقط بر پایه‌ی معیارهای قیمتی عبور کردند.`);
  }

  tiles("screenKpis", [
    { k: "کل نمادها", v: c.universe, sub: "سهام عادی" },
    { k: "تأییدشده", v: c.approved, sub: "عبور کامل بنیادی" },
    { k: "مشروط", v: c.provisional, sub: "عبور با داده‌ی ناقص" },
    { k: "داده‌ی ناکافی", v: c.insufficient, sub: "قضاوت نشد" },
    { k: "ردشده", v: c.rejected, sub: "دروازه یا امتیاز" },
    { k: "تحلیل تکنیکال", v: c.technically_analysed, sub: "سطرهای گزارش نهایی" }
  ]);

  hBarChart($("funnelChart"), {
    data: [
      { label: "کل نمادها", value: c.universe },
      { label: "دارای داده", value: c.universe - c.insufficient },
      { label: "عبورکرده", value: passed },
      { label: "تحلیل‌شده", value: c.technically_analysed }
    ],
    max: Math.max(c.universe, 1),
    format: (v) => v.toFixed(0)
  });

  const colors = seriesColors(5);
  const rows = rep.final || [];

  if (rows.length) {
    stackedHBar($("fundBreakdownChart"), {
      rows: rows.slice(0, 20).map(r => ({
        label: r.symbol,
        values: Object.fromEntries(
          FUND_COMPONENTS.map(k => [k, r.fundamental_breakdown?.[k] ?? 0]))
      })),
      keys: FUND_COMPONENTS,
      colors,
      total: 100,
      format: (v) => v.toFixed(0)
    });

    $("fundLegend").innerHTML = FUND_COMPONENTS.map((cName, i) =>
      `<span class="item"><span class="swatch" style="background:${colors[i]}"></span>${cName}</span>`
    ).join("");

    $("fundBreakdownTable").innerHTML = table(
      ["نماد", ...FUND_COMPONENTS, "جمع", "پوشش"],
      rows.map(r => [
        esc(r.symbol),
        ...FUND_COMPONENTS.map(k => r.fundamental_breakdown?.[k] ?? "—"),
        r.fundamental_score,
        `${Math.round((r.weight_covered || 0) * 100)}٪`
      ])
    );
  } else {
    $("fundBreakdownChart").innerHTML = '<div class="empty">هیچ نمادی عبور نکرد</div>';
    $("fundLegend").innerHTML = "";
  }

  $("finalTable").innerHTML = rows.length
    ? table(
        ["نماد", "شرکت", "حکم", "تکنیکال", "بنیادی", "ریسک", "جریان پول",
         "کل", "روند", "سیگنال نهایی", "P/E", "ROE", "رشد فروش", "واگرایی"],
        rows.map(r => {
          const t = r.technical || {};
          const m = r.metrics || {};
          const c = r.codal || {};
          const cr = c.ratios || {};

          // Which model the fundamental number came from. A Codal score is
          // built from filed statements; a snapshot score knows only price,
          // EPS and liquidity. Showing them in one column without saying
          // which is which would make them look comparable when they are not.
          const fundCell = c.score != null
            ? `${c.score} <span class="hint-inline">کدال</span>`
            : `${r.fundamental_score} <span class="hint-inline">تصویر</span>`;

          const divergence = r.divergence === "VALUE_WITHOUT_MOMENTUM"
            ? '<span class="badge warning"><span class="dot"></span>ارزنده، بی‌حرکت</span>'
            : r.divergence === "MOMENTUM_WITHOUT_SUBSTANCE"
            ? '<span class="badge serious"><span class="dot"></span>حرکت بی‌پشتوانه</span>'
            : "";

          return [
            `<strong>${esc(r.symbol)}</strong>`,
            esc((r.name || "").slice(0, 26)),
            verdictBadge(r.verdict),
            t.score ?? "—",
            fundCell,
            r.risk_score ?? "—",
            r.flow_score ?? "—",
            `<strong>${r.overall_score ?? "—"}</strong>`,
            esc(TREND_FA[t.trend] || t.trend || "—"),
            r.final_status ? badge(r.final_status) : "—",
            cr.pe != null ? num(cr.pe, 1) : (m.pe == null ? "—" : m.pe),
            cr.roe == null ? "—" : `${num(cr.roe, 0)}٪`,
            signed(cr.revenue_growth, 0),
            divergence
          ];
        }))
    : '<div class="empty">هیچ نمادی به مرحله‌ی تکنیکال نرسید</div>';

  const gates = Object.entries(rep.gate_counts || {});

  const gateData = gates.length ? gates.map(([label, n]) => ({ label, value: n })) : [];

  if (c.insufficient) gateData.push({ label: "بدون داده‌ی ذخیره‌شده", value: c.insufficient });

  if (gateData.length) {
    gateData.sort((a, b) => b.value - a.value);
    hBarChart($("gateChart"), { data: gateData, format: (v) => v.toFixed(0) });
  } else {
    $("gateChart").innerHTML = '<div class="empty">چیزی کنار گذاشته نشد</div>';
  }

  const sample = rep.insufficient_sample || [];
  $("excludedTable").innerHTML = sample.length
    ? table(["نماد", "شرکت", "تابلو", "دلیل"],
        sample.map(r => [esc(r.symbol), esc((r.name || "").slice(0, 34)), esc(r.board || "—"),
          esc((r.reasons || [])[0] || "—")]))
    : '<div class="empty">چیزی کنار گذاشته نشد</div>';

  const snap = rep.snapshot || {};
  $("coverageTable").innerHTML = table(
    ["ورودی", "وضعیت", "جزئیات"],
    [
      ["فهرست نمادها", rep.counts.universe ? "✓ بارگذاری شد" : "✗ موجود نیست",
        `${rep.counts.universe} سهم عادی`],
      ["سابقه‌ی قیمت",
        !rep.have_history ? "✗ موجود نیست"
          : rep.have_history >= rep.counts.universe ? "✓ کامل" : "◐ جزئی",
        `${rep.have_history} از ${rep.counts.universe} سهم کندل ذخیره‌شده دارند`],
      ["تصویر بنیادی (EPS، P/E، ارزش بازار)",
        snap.rows ? "✓ بارگذاری شد" : "✗ موجود نیست",
        snap.rows ? `${num(snap.rows, 0)} سطر، ثبت ${faTime(snap.captured_at)}`
                  : "نیازمند مسیر فعال به tsetmc — SIRAT_PROXY_URL"],
      ["ارائه‌دهنده", rep.provider || "—", "منبع فعال داده"]
    ]
  );
}

async function loadScreen(force = false) {
  const btn = $("runScreenBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال غربال…"; }
  setTask("screen", "running", "غربال بنیادی کل بازار، سپس تکنیکال");

  try {
    const rep = await getJSON(`/api/screen?${query()}${force ? "&refresh=1" : ""}`);
    renderScreen(rep);
    setTask("screen", "done",
      `${rep.counts.technically_analysed} از ${rep.counts.universe} تحلیل شد`,
      `${rep.counts.technically_analysed}/${rep.counts.universe}`);
  } catch (err) {
    state.screen = null;
    if (!scanLocked(err, "screenStatus", "screenStatusText", "screen",
                    "غربال اجرا نشد — تنظیمات است، نه خطا.")) {
      banner("screenStatus", "screenStatusText", "is-error",
        `<strong>غربال شکست خورد.</strong> ${esc(err.message)}`);
      setTask("screen", "failed", err.message);
    }
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ اجرای غربال"; }
  }
}

/* ---------- suggested portfolio ---------- */

// Everything a book is drawn into, blanked as one list so the two failure
// paths -- a payload that says it failed, and a request that never answered --
// cannot drift apart. A basket left standing under either was sized for the
// equity of the run that built it, not the one just applied.
function clearPortfolio() {
  tiles("portfolioKpis", []);
  $("portfolioTable").innerHTML = "";
  $("allocationChart").innerHTML = '<div class="empty">سبدی نیست</div>';
  $("riskTable").innerHTML = "";
  bulletList("riskNotes", []);
  bulletList("portfolioNotes", []);
}

// The min-score box is read by the v1 book only. v2 builds on its own floor --
// SWING_BUY_SCORE plus the regime's entry_floor, reported back as
// settings.min_score -- so under v2 the box took a number and ignored it,
// which reads as a setting that worked. Off under v2, naming the floor in
// force; on again for v1. Its value is left alone: v1 still wants it.
// panel.css greys only a disabled BUTTON, and its input colours override the
// browser's disabled ones, so the input gets the same 0.5 inline.
function syncMinScore(rep) {
  const input = $("minScoreInput");
  if (!input || !rep) return;

  const v2 = rep.engine === "v2";
  const floor = rep.portfolio?.settings?.min_score;
  const why = v2
    ? "در موتور v2 کف امتیاز را خود موتور تعیین می‌کند" +
      (floor != null ? `: ${floor}` : "") +
      " — این کادر فقط در موتور v1 خوانده می‌شود."
    : "";

  input.disabled = v2;
  input.style.opacity = v2 ? "0.5" : "";
  input.title = why;

  // On the field as well, so the label explains it too: not every browser
  // shows a tooltip for a disabled control.
  const field = input.closest(".field");
  if (field) field.title = why;

  // And visibly: a phone shows no tooltip, and a faded "60" beside a book
  // built on 68 explains nothing by itself.
  let note = field?.querySelector(".field-note");
  if (field && !note && v2) {
    note = document.createElement("span");
    note.className = "hint field-note";
    field.appendChild(note);
  }
  if (note) note.textContent = v2 ? `موتور v2: کف ${floor ?? "—"}` : "";
}

// The engines name the limit that sized a position in their own vocabulary
// (risk_engine.BOUND_*, portfolio_engine.admit, portfolio.py, and advisor's
// spread_ceiling); the «قید» column printed those identifiers as they came.
// An unknown code is still shown as it came, so a new limit is never blank.
const BINDING_LABELS = {
  risk_budget: "بودجه‌ی ریسک", risk: "بودجه‌ی ریسک",
  max_position_pct: "سقف وزن هر نماد", position_cap: "سقف وزن هر نماد",
  "weight cap": "سقف وزن هر نماد",
  adv_participation: "نقدشوندگی", liquidity: "نقدشوندگی",
  sector_budget: "بودجه‌ی صنعت", sector_value: "سقف ارزشی صنعت",
  "sector cap": "سقف صنعت",
  regime_budget: "بودجه‌ی رژیم بازار", available_cash: "نقدینگی آزاد",
  portfolio_room: "ظرفیت باقی‌مانده‌ی سبد", "cash floor": "کف نقدینگی",
  below_min_position: "کمتر از حداقل موقعیت",
  spread_ceiling: "سهم برابر از بودجه",
};

function bindingLabel(code) {
  return code ? (BINDING_LABELS[code] || code) : "—";
}

function renderPortfolio(rep) {
  state.portfolio = rep;
  syncMinScore(rep);

  if (!rep || !rep.ok) {
    banner("portfolioStatus", "portfolioStatusText", "is-error",
      `<strong>ساخت سبد انجام نشد.</strong> ${esc(rep?.error || "")}`);
    clearPortfolio();
    return;
  }

  const basket = rep.portfolio;
  const totals = basket.totals;
  const settings = basket.settings;
  const positions = basket.positions || [];
  const riskRep = rep.risk || {};

  if (!positions.length) {
    banner("portfolioStatus", "portfolioStatusText", "is-error",
      `<strong>سبدی ساخته نشد.</strong> ${esc((basket.notes || [])[0] || "")}`);
    // clearPortfolio, so the previous book's risk table and notes go too: an
    // empty book after a full one left the old risk table standing.
    clearPortfolio();
    bulletList("portfolioNotes", basket.notes);
    return;
  }

  // Which engine built this book is part of the result, not a footnote: the two
  // produce completely different lists from the same data, and a table that does
  // not say which one it came from is a table nobody can check.
  const engine = rep.engine === "v2"
    ? "موتور سوئینگ (v2)"
    : "موتور ترکیبی (v1)";

  // The basket's own tests come first, in amber: it is the most concrete card
  // on the page and its record is the worst (found 2026-11-11 -- the only
  // caution used to be "(معامله‌ی کاغذی)" in the title and a folded note on
  // another tab).
  const record = (rep.track_record && rep.track_record.windows) || [];
  const recordText = record.length
    ? "<strong>کارنامه‌ی آزمون همین روش — باخت:</strong> " + record.map(w => {
        const [from, to] = String(w.window).split("..");
        // The shortfall in words: a green "+24%" beside the index's +93%
        // read as a gain (the review panel, 2026-11-11).
        const behind = w.tedpix_pct != null && w.basket_pct != null ? w.tedpix_pct - w.basket_pct : null;
        return `${faDay(from, true)} تا ${faDay(to, true)}: سبد ${num(w.basket_pct, 1)}٪ ` +
               `در برابر شاخص کل ${num(w.tedpix_pct, 1)}٪` +
               (behind != null ? ` — <strong>${num(behind, 0)} واحد کمتر از شاخص</strong>` : "") +
               (w.risk_free_pct != null ? `، اوراق ${num(w.risk_free_pct, 1)}٪` : "");
      }).join("؛ ") +
      `. انتخاب سهم در آزمون از انتخاب تصادفی بهتر نبود (بدتر از ${record.map(w => num(100 - w.selection_percentile, 0)).join(" و ")}٪ سبدهای تصادفی). ` +
      "این فهرست نامزدهای بررسی است، نه توصیه‌ی خرید؛ نگه‌داشتن خود بازار و طلا در آزمون‌ها نتیجه‌ی بهتری داشته است.<br>"
    : "";
  // Which session the prices are from, what a round trip costs, and what a
  // newer stored market watch says of the entry ranges (advisor._live_view,
  // 2026-11-23). The card gave closes, entry ranges and R/R with no date and
  // no cost: on the evening of 11-23 they were the 11-22 closes, and eight
  // of nine names had already traded outside the range printed for them.
  const costs = basket.costs || {};
  const dateText = basket.data_as_of
    ? `<strong>قیمت‌ها، محدوده‌های ورود و حد ضررها از بسته‌ی جلسه‌ی ${faDay(basket.data_as_of, true)} است.</strong> ` +
      (basket.watch_newer
        ? `<strong>تابلوی ذخیره‌شده‌ی ${faDay(basket.watch_as_of, true)} تازه‌تر است:</strong> ` +
          `${num(basket.watch_outside_entry, 0)} نماد از ${num(basket.watch_checked, 0)} در آن بیرون از محدوده‌ی ورودِ این جدول معامله شد` +
          (basket.watch_at_limit ? ` (${num(basket.watch_at_limit, 0)} در سقف یا کف دامنه)` : "") + ". "
        : "")
    : "";
  const costText = costs.round_trip_pct != null
    ? `پس از هزینه‌ی رفت‌وبرگشت ${num(costs.round_trip_pct, 2)}٪ (خرید ${num(costs.buy_pct, 2)}٪، فروش ${num(costs.sell_pct, 2)}٪ با مالیات)، ` +
      `${num(totals.net_below_floor, 0)} از ${num(totals.positions, 0)} موقعیت به سود به زیانِ ${num(costs.min_risk_reward, 1)} نمی‌رسد. `
    : "";
  const riskAll = totals.total_risk_net_pct != null ? totals.total_risk_net_pct : totals.total_risk_pct;
  banner("portfolioStatus", "portfolioStatusText", record.length ? "" : "is-live",
    recordText + dateText +
    `<strong>${totals.positions} موقعیت در ${totals.sectors} صنعت (کاغذی).</strong> ` +
    `${basket.candidates} نماد به حد نصاب امتیاز ${settings.min_score} رسیدند. ` + costText +
    `ریسک کل اگر همه‌ی حد ضررها در قیمت خودشان اجرا شوند، با کارمزد: دست‌کم ${riskAll}٪ سرمایه ` +
    "(در صف فروش و چند روز منفی پشت سر هم، زیان واقعی بیشتر است). " +
    `<span class="hint-inline">— ${engine}</span>`);

  const vol = riskRep.volatility || {};

  tiles("portfolioKpis", [
    { k: "موقعیت‌ها", v: totals.positions, sub: `در ${totals.sectors} صنعت` },
    {
      k: "سرمایه‌گذاری‌شده",
      v: `${numAsSent(totals.invested_pct)}٪`,
      sub: `${money(totals.invested)} ${currencyName(basket.currency)}`
    },
    {
      k: "نقد",
      v: `${num(100 - totals.invested_pct, 1)}٪`,
      sub: `${money(totals.cash)} ${currencyName(basket.currency)}`
    },
    {
      k: "ریسک کل",
      v: `<span class="down">${numAsSent(riskAll)}٪</span>`,
      sub: totals.total_risk_net_pct != null
        ? `اگر همه‌ی حد ضررها بخورند، با کارمزد (بدون آن ${numAsSent(totals.total_risk_pct)}٪)`
        : "اگر همه‌ی حد ضررها بخورند"
    },
    {
      k: "نوسان سبد",
      v: vol.annualised_pct != null ? `${numAsSent(vol.annualised_pct)}٪` : "—",
      sub: vol.undiversified_pct != null
        ? `بدون تنوع: ${numAsSent(vol.undiversified_pct)}٪` : "سالانه"
    },
    {
      k: "نسبت تنوع‌بخشی",
      v: numAsSent(vol.diversification_ratio),
      sub: "هرچه بالاتر، تنوع مؤثرتر"
    }
  ]);

  $("portfolioTable").innerHTML = table(
    ["#", "نماد", "شرکت", "صنعت", "بنیادی", "تکنیکال", "ترکیبی",
     "قیمت", "محدوده‌ی ورود", "حد ضرر", "حد سود", "R/R (خالص از کارمزد)",
     "تعداد سهم", "ارزش", "وزن", "ریسک با کارمزد", "قید"],
    positions.map(p => [
      p.rank,
      `<strong>${esc(p.symbol)}</strong>`,
      esc((p.name || "").slice(0, 22)),
      esc((p.sector_name || "").slice(0, 22)),
      // Every reading here is null-guarded, because the v2 engine renormalises
      // around an absent factor rather than scoring it zero: a company with no
      // filing has NO fundamental score, and printing "null" in that cell would
      // read as a company that scored badly.
      p.fundamental_score ?? "—",
      p.technical_score ?? "—",
      `<strong>${p.combined_score}</strong>`,
      num(p.close, 0),
      // The newer market watch against the range printed from the last bar.
      `${num(p.entry_low, 0)} – ${num(p.entry_high, 0)}` +
        (p.watch_state && p.watch_state !== "inside"
          ? ` <span class="down" title="آخرین قیمت در تابلوی تازه‌تر">تابلو ${num(p.watch_last, 0)} — ${
            { limit_up: "سقف دامنه", limit_down: "کف دامنه", above_entry: "بالای محدوده", below_entry: "زیر محدوده" }[p.watch_state] || ""}</span>`
          : ""),
      `<span class="down">${num(p.stop_price, 0)}</span>`,
      `<span class="up">${num(p.target_price, 0)}</span>`,
      // Gross is the engine's ratio; net is what is left after the round trip.
      (p.risk_reward != null ? `1:${p.risk_reward}` : "—") +
        (p.risk_reward_net != null
          ? ` <span class="${p.net_below_floor ? "down" : "hint"}" title="پس از کسر هزینه‌ی رفت‌وبرگشت">(خالص ${num(p.risk_reward_net, 2)})</span>`
          : ""),
      (p.shares ?? 0).toLocaleString(),
      money(p.position_value),
      p.weight_pct != null ? `${p.weight_pct}٪` : "—",
      p.risk_pct_net != null ? `${p.risk_pct_net}٪` : p.risk_pct != null ? `${p.risk_pct}٪` : "—",
      esc(bindingLabel(p.scaled_by || p.bound_by))
    ])
  );

  const exposure = riskRep.sector_exposure || [];

  if (exposure.length) {
    hBarChart($("allocationChart"), {
      data: exposure.map(s => ({
        label: s.sector_name.slice(0, 20),
        value: s.pct_of_invested
      })),
      max: Math.max(...exposure.map(s => s.pct_of_invested), 10),
      format: (v) => `${fmt(v, 1)}٪`
    });
  } else {
    $("allocationChart").innerHTML = '<div class="empty">داده‌ای نیست</div>';
  }

  renderRisk(riskRep, basket);

  bulletList("portfolioNotes",
    [...(basket.notes || []), ...(rep.warnings || []).slice(0, 3)]);
}

function renderRisk(riskRep, basket) {
  if (!riskRep || !riskRep.available) {
    $("riskTable").innerHTML =
      `<div class="empty">${esc(riskRep?.reason || "تحلیل ریسک در دسترس نیست")}</div>`;
    bulletList("riskNotes", []);
    return;
  }

  const vol = riskRep.volatility || {};
  const varRep = riskRep.var || {};
  const beta = riskRep.beta || {};
  const conc = riskRep.concentration || {};
  const dd = riskRep.drawdown || {};
  const corr = riskRep.correlation || {};

  $("riskTable").innerHTML = table(
    ["سنجه", "مقدار", "معنی"],
    [
      ["نوسان سالانه", vol.annualised_pct != null ? `${numAsSent(vol.annualised_pct)}٪` : "—",
        "انحراف معیار بازده سبد"],
      ["اگر کاملاً همبسته بود", vol.undiversified_pct != null ? `${numAsSent(vol.undiversified_pct)}٪` : "—",
        "میانگین وزنی نوسان تک‌تک نمادها"],
      ["نسبت تنوع‌بخشی", numAsSent(vol.diversification_ratio),
        "فاصله‌ی این دو، همان تنوع است"],
      ["میانگین همبستگی جفتی", numAsSent(corr.average),
        "نزدیک ۱ یعنی تنوع اسمی است"],
      [`VaR ${varRep.confidence ? Math.round(varRep.confidence * 100) : 95}٪ روزانه`,
        varRep.historical_pct != null
          ? `${numAsSent(varRep.historical_pct)}٪ (${money(varRep.historical_rials)})` : "—",
        "بدترین روز در ۹۵٪ موارد"],
      ["CVaR / زیان مورد انتظار",
        varRep.conditional_pct != null
          ? `${numAsSent(varRep.conditional_pct)}٪ (${money(varRep.conditional_rials)})` : "—",
        "میانگین زیان وقتی از VaR رد شود"],
      ["بدترین جلسه‌ی مشاهده‌شده",
        varRep.worst_day_pct != null ? `${numAsSent(varRep.worst_day_pct)}٪` : "—",
        `در ${varRep.sessions || 0} جلسه — دامنه‌ی نوسان سقف این عدد است`],
      // Beside the one-day figures the band caps: what five sessions in a
      // row cost over the same window (risk.worst_run, 2026-11-23).
      [`بدترین ${num((riskRep.worst_run || {}).sessions || 5, 0)} جلسه‌ی پیاپی`,
        (riskRep.worst_run || {}).worst_pct != null
          ? `${numAsSent(riskRep.worst_run.worst_pct)}٪ (${money(riskRep.worst_run.worst_rials)})` : "—",
        (riskRep.worst_run || {}).to_date
          ? `تا ${faDay(riskRep.worst_run.to_date, true)} — زیانی که در صف فروش نمی‌شود از آن بیرون رفت`
          : "زیانی که در صف فروش نمی‌شود از آن بیرون رفت"],
      [`بتا نسبت به ${esc(riskRep.benchmark || "شاخص")}`, numAsSent(beta.beta),
        beta.r_squared != null ? `R² ${numAsSent(beta.r_squared)}` : "حساسیت به بازار"],
      // Of the window measured, not of history: said in the label.
      [(riskRep.window || {}).sessions
        ? `حداکثر افت در ${num(riskRep.window.sessions, 0)} جلسه‌ی اخیر` : "حداکثر افت تاریخی",
        dd.max_drawdown_pct != null ? `${numAsSent(dd.max_drawdown_pct)}٪` : "—",
        dd.peak_date ? `${faDay(dd.peak_date, true)} → ${faDay(dd.trough_date, true)}` : "قله تا کف"],
      ["تعداد مؤثر موقعیت‌ها", numAsSent(conc.effective_positions),
        `از ${conc.positions || 0} موقعیت واقعی`]
    ]
  );

  // Everything qualifying the numbers above, in one list under them: the
  // ±3% band warning, the names dropped for want of history, the short
  // overlap window. These used to be split between a hint that overwrote the
  // card's own explanation and a hint under the correlation matrix -- so
  // removing the matrix would have taken the caveats with it, even though
  // they qualify volatility, VaR and beta just as much.
  //
  // "همبستگی محاسبه نشد" earns a line of its own: without a correlation
  // matrix behind it the diversification ratio and the average-correlation
  // row in the table above are not measured, and a blank cell cannot say so.
  const notes = [];

  if (riskRep.price_band_warning) notes.push(riskRep.price_band_warning);

  if (!(corr.labels && corr.values)) {
    notes.push("همبستگی محاسبه نشد — سابقه‌ی مشترک کافی بین نمادهای سبد " +
               "نیست، پس نسبت تنوع‌بخشی و میانگین همبستگی خوانده نمی‌شوند.");
  }

  notes.push(...(riskRep.notes || []));

  bulletList("riskNotes", notes);
}

async function loadPortfolio(force = false) {
  const btn = $("runPortfolioBtn");
  if (btn) { btn.disabled = true; btn.textContent = "در حال ساخت…"; }
  setTask("portfolio", "running", "انتخاب نمادها، اندازه‌گذاری ریسک و تحلیل همبستگی");
  setLoading(true);

  try {
    const rep = await getJSON(`/api/portfolio?${query()}${force ? "&refresh=1" : ""}`);
    renderPortfolio(rep);
    const n = rep.ok ? rep.portfolio.totals.positions : 0;
    setTask("portfolio", rep.ok && n ? "done" : "failed",
      rep.ok
        ? `${n} موقعیت در ${rep.portfolio.totals.sectors} صنعت`
        : (rep.error || "ساخته نشد"),
      n ? String(n) : "");
  } catch (err) {
    // No book came back, so none is drawn. Kept in state, the last one was
    // repainted by the next rerender() -- after an apply with a new equity, a
    // basket sized for the old equity, green again after one tab switch.
    state.portfolio = null;
    clearPortfolio();
    if (!scanLocked(err, "portfolioStatus", "portfolioStatusText", "portfolio",
                    "سبد ساخته نشد — تنظیمات است، نه خطا.")) {
      banner("portfolioStatus", "portfolioStatusText", "is-error",
        `<strong>ساخت سبد شکست خورد.</strong> ${esc(err.message)}`);
      setTask("portfolio", "failed", err.message);
    }
    console.error(err);
  } finally {
    setLoading(false);
    if (btn) { btn.disabled = false; btn.textContent = "▶ ساخت سبد"; }
  }
}

/* ---------- data loading progress ----------

   Two things can be "loading" at once and the user needs to tell them apart:
   the background download from TSETMC (minutes, five ordered stages, hundreds
   of requests inside one of them) and the analysis this page runs over what is
   already stored (seconds). Both are reported as stages, with the download's
   position driving a real percentage rather than a spinner. */

const BOOT_POLL_FAST = 1000;      // while a fetch is in flight
const BOOT_POLL_IDLE = 20000;     // otherwise: /api/bootstrap re-probes the DB

const TASKS = [
  { id: "regime", label: "وضعیت بازار — market regime" },
  { id: "indices", label: "شاخص‌ها — index analysis" },
  { id: "sectors", label: "صنایع — sector analysis" },
  { id: "fundamentals", label: "بنیادی کدال — Codal fundamentals" },
  { id: "flow", label: "جریان پول — money flow" },
  { id: "screen", label: "پایش کل بازار — full-market screen" },
  { id: "portfolio", label: "سبد پیشنهادی — portfolio & risk" }
];

const taskState = {};

let bootTimer = null;
let bootRunning = false;

function bootstrapNote(payload) {
  // The panel fetches its own data. When an endpoint reports a download in
  // flight, say so rather than showing an empty table that looks like a bug.
  const boot = payload && payload.bootstrap;
  if (!boot || !boot.fetching || !boot.fetching.length) return "";
  return ` <em>در حال دریافت ${esc(boot.fetching.join("، "))} از TSETMC — ` +
    `چند لحظه دیگر دوباره اجرا کنید.</em>`;
}

// Pills carry the Persian half of the label; the full bilingual name and the
// current detail go in the tooltip, so the row stays one line wide.
function shortLabel(label) {
  return String(label || "").split("—")[0].trim() || String(label || "");
}

function pill(cls, label, title, count) {
  return `<li class="${cls}" title="${esc(title)}"><span class="st"></span>` +
    `${esc(label)}${count ? `<span class="n">${esc(count)}</span>` : ""}</li>`;
}

function clock(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function bootStep(p, name) {
  const step = (p.steps || {})[name] || {};
  const st = (p.status || {})[name] || {};
  const item = (p.overall || {}).item;
  // Indices, bars and flow gain a row per session: "behind" for them is a
  // session missing or incomplete, whatever the last attempt's outcome. A
  // fetch that brought 469 of 751 names' flow drew a green pill, and an
  // index held back by a recent attempt read as current (2026-11-23).
  const lagging = !!(st.session && st.behind);

  if (p.running && p.current === name) {
    return {
      cls: "running",
      count: item && item.total ? `${item.done}/${item.total}` : "",
      note: (item && item.label) || "fetching…"
    };
  }

  switch (step.action) {
    case "fetched":
      return {
        cls: lagging ? "warn" : "done",
        count: step.seconds != null ? `${step.seconds}s` : "",
        note: step.detail || st.detail || "دریافت شد"
      };
    case "kept":
      return { cls: lagging ? "warn" : "done", count: "",
               note: step.detail || "به‌روز بود" };
    case "failed":
      return { cls: "failed", count: "", note: step.detail || "ناموفق" };
    case "skipped":
    case "cooling-off":
      return { cls: "warn", count: "", note: step.detail || step.action };
    default:
      break;
  }

  if (p.running && (p.planned || []).includes(name)) {
    return { cls: "queued", count: "", note: `در صف — ${st.detail || ""}` };
  }

  // Never run this session: report what is stored, not a fake outcome.
  return {
    cls: (st.needs_fetch || lagging) ? "warn" : st.present ? "done" : "queued",
    count: "",
    note: st.detail || "هنوز بررسی نشده"
  };
}

function renderBootstrap(p) {
  if (!$("loadPanel")) return;

  const overall = p.overall || {};
  const order = p.order || [];
  const labels = p.labels || {};
  const running = !!p.running;

  $("loadSteps").innerHTML = order.map((name) => {
    const s = bootStep(p, name);
    return pill(s.cls, shortLabel(labels[name] || name),
      `${labels[name] || name}\n${s.note}`, s.count);
  }).join("");

  const bar = $("loadBarWrap");
  const fill = $("loadBar");
  const failed = order.filter(n => ((p.steps || {})[n] || {}).action === "failed");
  const missing = order.filter(n => ((p.status || {})[n] || {}).needs_fetch);
  // Behind, but not to be fetched yet: the exchange has not published the
  // session in full and the last attempt is too recent to repeat. The strip
  // keyed on needs_fetch alone and said «همه‌ی داده‌ها موجود و به‌روز است»
  // on the evening of 2026-11-23 with that day's session missing.
  const waiting = order.filter(n => {
    const s = (p.status || {})[n] || {};
    return s.session && s.behind && !s.needs_fetch;
  });

  bar.classList.remove("is-done", "is-error", "is-behind", "is-indeterminate");

  if (running) {
    const pct = Number(overall.percent || 0);
    // No planned list yet means the run is still deciding what to fetch —
    // a stripe, not a percentage invented for the occasion. The inline width
    // has to go with it, or it would override the animated one.
    if (overall.steps_total) {
      fill.style.width = `${pct}%`;
    } else {
      bar.classList.add("is-indeterminate");
      fill.style.width = "";
    }
    bar.setAttribute("aria-valuenow", pct.toFixed(0));
    $("loadPct").textContent = `${pct.toFixed(0)}٪`;

    const parts = [];
    if (overall.steps_total) {
      parts.push(`Step ${overall.step_number} of ${overall.steps_total}`);
    }
    if (overall.step_label) parts.push(overall.step_label);
    const item = overall.item;
    if (item && item.label) {
      parts.push(item.total ? `${item.label} ${item.done}/${item.total}`
                            : item.label);
    }
    $("loadStage").textContent = parts.join("  ·  ") || "Starting…";

  } else if (missing.length) {
    // "Missing" and "stale" are different facts and the panel must not blur
    // them: one means there is nothing to analyse, the other means what is
    // stored is real but a session or two behind.
    const names = (list) => list.map(n => shortLabel(labels[n] || n)).join("، ");
    const absent = missing.filter(n => !((p.status || {})[n] || {}).present);
    const stale = missing.filter(n => ((p.status || {})[n] || {}).present);

    const ready = order.length - missing.length;
    fill.style.width = `${(ready / Math.max(order.length, 1)) * 100}%`;
    bar.classList.add(absent.length ? "is-error" : "is-behind");
    bar.setAttribute("aria-valuenow", String(Math.round(ready / order.length * 100)));
    $("loadPct").textContent = `${ready}/${order.length}`;
    // The button is named as the page labels it (#loadFetchBtn). This line
    // used to send the reader to an English "Fetch what is missing or stale"
    // that appears nowhere on a Persian page.
    $("loadStage").textContent = [
      absent.length ? `هرگز دریافت نشده: ${names(absent)}` : "",
      stale.length ? `کهنه: ${names(stale)}` : ""
    ].filter(Boolean).join("  ·  ") + " — دکمه‌ی «دریافت داده‌های ناقص یا کهنه» را بزنید.";

  } else if (waiting.length && !failed.length) {
    const ready = order.length - waiting.length;
    const tries = waiting
      .map(n => ((p.status || {})[n] || {}).next_try).filter(Boolean).sort();
    fill.style.width = `${(ready / Math.max(order.length, 1)) * 100}%`;
    bar.classList.add("is-behind");
    bar.setAttribute("aria-valuenow", String(Math.round(ready / Math.max(order.length, 1) * 100)));
    $("loadPct").textContent = `${ready}/${order.length}`;
    $("loadStage").textContent =
      `داده‌ی آخرین جلسه هنوز کامل نیست: ` +
      waiting.map(n => shortLabel(labels[n] || n)).join("، ") +
      ` — بورس آن را کامل منتشر نکرده (یا امروز بازار تعطیل بوده) و تلاش قبلی تازه است` +
      (tries.length ? `؛ تلاش بعدی از ساعت ${tehranClock(tries[0])}.` : ".");

  } else {
    fill.style.width = "100%";
    bar.classList.add(failed.length ? "is-error" : "is-done");
    bar.setAttribute("aria-valuenow", "100");
    $("loadPct").textContent = failed.length ? "!" : "✓";
    $("loadStage").textContent = failed.length
      ? `پایان — ${failed.length} مرحله ناموفق بود؛ بقیه ذخیره شد.`
      : "همه‌ی داده‌ها موجود و به‌روز است.";
  }

  // Recede only when there is genuinely nothing to look at. A panel reporting
  // missing data or a failed stage should not be the faintest thing on screen.
  $("loadPanel").classList.toggle(
    "is-idle", !running && !missing.length && !failed.length && !waiting.length);

  const elapsed = clock(overall.elapsed_seconds);
  $("loadElapsed").textContent = elapsed
    ? (running ? `${elapsed} از شروع` : `کل زمان ${elapsed}`) : "";

  $("loadLog").textContent = (p.log || []).length
    ? p.log.map(l => `${tehranClock(l.at)}  ${l.message}`).join("\n")
    : "در این نوبت چیزی دریافت نشده است.";
}

/* ---------- system health (/api/health, health.py) ----------
   What ran and what did not: the scheduled tasks' done lines, sessions
   without an intraday capture, missed forward-log entries, the backup's
   age, the power source. Until 2026-11-23 none of it was on the page: five
   evenings with no run and eight sessions without a capture showed nowhere.

   The server decides the levels and writes the sentences; this only draws
   them. All well is one quiet line; anything warn or bad gets a row with
   what to do. A server without the route (a panel started before this
   existed) leaves the card hidden rather than alarming. */

const HEALTH_POLL = 5 * 60 * 1000;

function renderHealth(rep) {
  const card = $("healthCard");
  if (!card || !rep) return;

  const rows = (rep.items || [])
    .filter(i => i.level === "warn" || i.level === "bad")
    .sort((a, b) => (b.level === "bad") - (a.level === "bad"));

  card.hidden = false;
  card.classList.toggle("is-quiet", !rows.length);
  card.classList.toggle("is-bad", rows.some(i => i.level === "bad"));
  $("healthSummary").textContent = rep.summary || "";
  $("healthList").innerHTML = rows.map(i =>
    `<li class="${i.level === "bad" ? "bad" : "warn"}">` +
    `<span class="what">${esc(i.text || "")}</span>` +
    (i.action ? `<span class="todo">${esc(i.action)}</span>` : "") +
    `</li>`).join("");
}

async function loadHealth() {
  if (!$("healthCard") || document.hidden) return;
  try {
    renderHealth(await getJSON("/api/health"));
  } catch (err) {
    // Not a finding about the system: the card keeps what it last showed.
  }
}

loadHealth();
setInterval(loadHealth, HEALTH_POLL);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadHealth();
});

/* ---------- track records (2026-11-24) ----------
   One line under each card's title: its badge, one SPECIFIC warning, and
   its record behind a fold (period, count, hit rate with its denominator,
   result against TEDPIX and at today's cost, in or out of sample) -- all
   from track_record.registry(), which reads the result files. A card whose
   strategy the bot's own tests rejected is greyed, and its list and levels
   stay hidden until the reader makes an active choice (research report,
   item 6: a specific warning works, a generic one does not; levels of a
   rejected scanner are not shown by default). */
var trackState = { reg: null };

const TRACK_BADGE_CLS = { accepted: "good", provisional: "warn",
                          rejected: "bad", descriptive: "neutral" };

function trackRevealKey(id) { return `sirat.reveal.${id}`; }

function trackRevealed(id) {
  try { return sessionStorage.getItem(trackRevealKey(id)) === "1"; }
  catch (err) { return false; }
}

function trackCardsByTitle(title) {
  const want = String(title || "").trim();
  return [...document.querySelectorAll(".card h2")]
    .filter(h => h.textContent.trim() === want)
    .map(h => ({ h, card: h.closest(".card") }))
    .filter(x => x.card);
}

function trackLineHtml(c, revealed) {
  if (c.error) return `<span class="hint">کارنامه‌ی این کارت خوانده نشد.</span>`;
  const cls = TRACK_BADGE_CLS[c.badge_key] || "neutral";
  const def = ((trackState.reg || {}).badges || {})[c.badge_key] || {};
  const vs = c.vs_tedpix || {};
  const ex = c.extra || {};
  const rows = [];
  if (c.badge_reason_fa) rows.push(c.badge_reason_fa);
  if (c.test_period) rows.push(`دوره‌ی آزمون: ${c.test_period}`);
  if (c.n && c.n.text_fa) rows.push(`شمار: ${c.n.text_fa}`);
  if (c.hit_rate && c.hit_rate.text_fa) rows.push(`نرخ موفقیت: ${c.hit_rate.text_fa}`);
  if (vs.text_fa) rows.push(`در برابر شاخص: ${vs.text_fa}`);
  if (vs.after_live_cost_fa) rows.push(`با هزینه‌ی امروز: ${vs.after_live_cost_fa}`);
  if (c.sample_fa) rows.push(c.sample_fa);
  if (ex.calibration_text_fa) rows.push(ex.calibration_text_fa);
  if (ex.forward && ex.forward.label_fa) rows.push(ex.forward.label_fa);
  const e21 = ex.forward_e21;
  if (e21 && e21.sessions_expected != null) {
    rows.push(`دفتر پیش‌بینی E21: از ${num(e21.sessions_expected, 0)} جلسه‌ی مورد انتظار ` +
              `${num(e21.timely, 0)} به‌موقع ثبت شد و ${num(e21.clean, 0)} پاک بود؛ ` +
              `سنجش آن با نتیجه تا پایان دوره‌ی رزرو (${(e21.reservation || {}).end || ""}) بسته است.`);
  }
  if (c.doubt) rows.push(`تردید: ${c.doubt}`);
  const hide = c.hide_levels && revealed
    ? ` <button type="button" class="link" data-gate-hide="${esc(c.id)}">پنهان کردن دوباره</button>` : "";
  return `<span class="badge ${cls}" title="${esc(def.definition_fa || "")}"><span class="dot"></span>${esc(c.badge || "—")}</span> ` +
    `<span class="track-warning">${esc(c.warning_fa || "")}</span>${hide}` +
    `<details class="track-details"><summary>کارنامه</summary><ul class="evidence">` +
    rows.map(r => `<li>${esc(r)}</li>`).join("") + `</ul></details>`;
}

function trackGateHtml(c) {
  return `<div class="gate-note" data-gate="${esc(c.id)}">` +
    `<strong>${esc(c.hidden_levels_label_fa || "ردشده در آزمون، فقط برای پژوهش")}</strong> — ` +
    `فهرست، سطوح ورود، حد ضرر و هدفِ این کارت پنهان است، چون آزمون خود بات این روش را رد کرده است.` +
    `<label class="gate-ack"><input type="checkbox" data-gate-ack="${esc(c.id)}"> ` +
    `می‌دانم این روش در آزمون رد شده و آنچه نشان داده می‌شود توصیه‌ی خرید نیست.</label>` +
    `<button type="button" data-gate-reveal="${esc(c.id)}" disabled>نمایش برای پژوهش</button></div>`;
}

function renderTrackRecords(reg) {
  if (!reg || !Array.isArray(reg.cards)) return;
  trackState.reg = reg;
  document.querySelectorAll(".track-line, .gate-note").forEach(n => n.remove());
  document.querySelectorAll(".levels-gated").forEach(n => n.classList.remove("levels-gated"));
  const lined = new Set();
  for (const c of reg.cards) {
    const revealed = trackRevealed(c.id);
    const gated = !!c.hide_levels && !revealed;
    (c.panel_titles || []).forEach((title, i) => {
      for (const { h, card } of trackCardsByTitle(title)) {
        if (gated) card.classList.add("levels-gated");
        if (gated && i !== 0 && !card.querySelector(".gate-note")) {
          // A sub-card of a rejected strategy: say why it is empty.
          (h.closest(".card-head") || h).insertAdjacentHTML("afterend",
            `<div class="gate-note gate-mini">${esc(c.hidden_levels_label_fa || "ردشده در آزمون، فقط برای پژوهش")} — ` +
            `با تأیید در کارت «${esc((c.panel_titles || [])[0] || "")}» نمایش داده می‌شود.</div>`);
        }
        if (i !== 0 || lined.has(card)) continue;
        lined.add(card);
        const line = document.createElement("div");
        line.className = "track-line";
        line.dataset.track = c.id;
        line.innerHTML = trackLineHtml(c, revealed);
        const anchor = h.closest(".card-head") || h;
        anchor.insertAdjacentElement("afterend", line);
        if (gated) line.insertAdjacentHTML("afterend", trackGateHtml(c));
      }
    });
  }
}

async function loadTrackRecords() {
  try {
    renderTrackRecords(await getJSON("/api/track_record"));
  } catch (err) {
    // An older panel process without the route: the cards stay as they were.
  }
}

document.addEventListener("change", (ev) => {
  const box = ev.target.closest && ev.target.closest("[data-gate-ack]");
  if (!box) return;
  const btn = document.querySelector(`[data-gate-reveal="${CSS.escape(box.dataset.gateAck)}"]`);
  if (btn) btn.disabled = !box.checked;
});

document.addEventListener("click", (ev) => {
  const reveal = ev.target.closest && ev.target.closest("[data-gate-reveal]");
  const hide = ev.target.closest && ev.target.closest("[data-gate-hide]");
  if (!reveal && !hide) return;
  const id = reveal ? reveal.dataset.gateReveal : hide.dataset.gateHide;
  try {
    if (reveal) sessionStorage.setItem(trackRevealKey(id), "1");
    else sessionStorage.removeItem(trackRevealKey(id));
  } catch (err) { /* storage refused: the choice lasts this render only */ }
  renderTrackRecords(trackState.reg);
});

loadTrackRecords();
setInterval(loadTrackRecords, 10 * 60 * 1000);

function renderTasks() {
  if (!$("taskSteps")) return;

  $("taskSteps").innerHTML = TASKS.map((t) => {
    const s = taskState[t.id] || { state: "queued", detail: "شروع نشده" };
    const cls = ["running", "done", "failed", "warn"].includes(s.state)
      ? s.state : "queued";
    return pill(cls, shortLabel(t.label), `${t.label}\n${s.detail || s.state}`,
      s.count || "");
  }).join("");
}

function setTask(id, state, detail, count) {
  taskState[id] = { state, detail, count };
  renderTasks();
}

// A finished download only helps if the views waiting on it are re-run.
function afterBootstrap(p) {
  const fetched = Object.entries(p.steps || {})
    .filter(([, s]) => s.action === "fetched")
    .map(([name]) => name);

  if (!fetched.length) return;

  if (fetched.includes("indices") || fetched.includes("history")) {
    loadIndices(true);
    loadRegime(true);
  }
  if (fetched.some(n => ["sectors", "history", "universe"].includes(n))) loadSectors(true);
  if (fetched.some(n => ["universe", "snapshot", "history"].includes(n))) {
    loadScreen(true).then(() => loadPortfolio(true));
  }
}

let bootPolling = false;

async function pollBootstrap(fast = false) {
  // No panel on the page means nothing to draw, and /api/bootstrap walks the
  // whole price_history table — don't poll into the void.
  if (!$("loadPanel")) return;

  // One chain only. Without this, a tab regaining focus mid-request would
  // start a second chain and every later one would double the poll rate.
  if (bootPolling) return;

  clearTimeout(bootTimer);

  // A hidden tab has nobody watching the bar; /api/bootstrap walks the whole
  // price_history table, so don't pay for it.
  if (document.hidden && !bootRunning) {
    bootTimer = setTimeout(() => pollBootstrap(), BOOT_POLL_IDLE);
    return;
  }

  bootPolling = true;

  try {
    const p = await getJSON("/api/bootstrap");
    const wasRunning = bootRunning;
    bootRunning = !!p.running;
    renderBootstrap(p);
    if (wasRunning && !bootRunning) afterBootstrap(p);
  } catch (err) {
    $("loadStage").textContent = `وضعیت پیشرفت در دسترس نیست — ${err.message}`;
    console.error(err);
  } finally {
    bootPolling = false;
  }

  bootTimer = setTimeout(() => pollBootstrap(),
    (bootRunning || fast) ? BOOT_POLL_FAST : BOOT_POLL_IDLE);
}

on("loadLogBtn", "click", () => {
  const hidden = $("loadLog").classList.toggle("hidden");
  $("loadLogBtn").textContent = hidden ? "نمایش گزارش" : "بستن گزارش";
});

on("loadFetchBtn", "click", async () => {
  const btn = $("loadFetchBtn");
  btn.disabled = true;
  $("loadStage").textContent = "شروع دریافت…";
  try {
    await getJSON("/api/bootstrap", { method: "POST" });
  } catch (err) {
    $("loadStage").textContent = `دریافت شروع نشد — ${err.message}`;
  } finally {
    btn.disabled = false;
    pollBootstrap(true);
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) pollBootstrap();
});

/* ---------- orchestration ---------- */

// Market first, then sectors, then the screen, then the basket the screen
// feeds -- the order an analyst reads them in, and the order the page is laid
// out in. The portfolio waits for the screen because it is built from it.
/* ---------- asset allocation (E27, E28) ----------
   The one section built on results that passed a preregistered test. Every
   history figure is recomputed server-side from the stored series; the
   experiments' frozen numbers live in backtest_output/E27/E28_results.json. */

function renderAllocation(rep) {
  state.allocation = rep;

  const h = rep.history;
  const labels = rep.sleeve_labels || {};

  const rows = rep.profiles.map(p => {
    const st = h ? h.profiles[p.key] : null;
    const weights = Object.entries(p.weights)
      .filter(([, w]) => w > 0)
      .map(([k, w]) => `${w}٪ ${esc(labels[k] || k)}`).join("<br>");
    const amounts = Object.entries(p.amounts || {})
      .map(([k, v]) => `${money(v)} ${esc(labels[k] || k)}`).join("<br>");
    // "provisional": tested once, then re-tested against real treasury
    // bills (E35) without clearing the preregistered bar (2026-11-24).
    const ev = p.evidence === "tested"
      ? `<span class="badge good"><span class="dot"></span>آزموده‌شده</span>`
      : p.evidence === "provisional"
        ? `<span class="badge warn"><span class="dot"></span>موقت</span>`
        : `<span class="badge neutral"><span class="dot"></span>توصیفی</span>`;
    return [
      `<strong>${esc(p.label)}</strong><br>${ev}`,
      weights,
      amounts,
      st ? `${num(st.cagr_pct, 1)}٪` : "—",
      st ? signed(st.max_drawdown_pct, 1) : "—",
      st ? signed(st.worst_12m_pct, 1) : "—",
      `<span class="hint">${esc(p.evidence_text)}</span>`,
    ];
  });

  $("allocationTable").innerHTML = table(
    ["ترکیب", "وزن‌ها", "مبلغ (ریال)", "بازده سالانه",
     "بیشترین افت", "بدترین ۱۲ ماه", "شواهد"], rows);

  const c = rep.candidates || {};
  const fundRows = [];
  for (const k of ["equity", "gold", "fixed"]) {
    for (const f of c[k] || []) {
      fundRows.push([esc(labels[k] || k), `<strong>${esc(f.symbol)}</strong>`,
                     esc(f.name), money(f.value)]);
    }
  }
  $("allocationFunds").innerHTML = fundRows.length
    ? table(["بخش", "نماد", "نام", "ارزش معاملات آخرین جلسه"], fundRows)
    : "";

  bulletList("allocationNotes", [rep.rebalance.text, ...(rep.notes || [])]);
  renderToday();

  // The tests behind these profiles (E27, E28) measured the edge against a
  // 20% fixed-income return; bills pay 43-44% today, about what TEDPIX made a
  // year over 2023-26 (46.6%). Said here, on the card that leans on them
  // (the 20-year reviewer, 2026-11-11).
  // E35 (2026-11-24) re-tested both against what bills really paid; its
  // own sentence, built from its result file, replaces the old caveat.
  const bill = state.valuation && state.valuation.bill_yield_pct;
  const rt = rep.retest;
  banner("allocationStatus", "allocationStatusText", h ? "" : "",
    h ? `سابقه‌ی ${esc(h.from)} تا ${esc(h.to)} — صندوق درآمد ثابت در جدول ` +
        `${num(h.fixed_income.cagr_pct, 0)}٪ در سال فرض شده است. ` +
        (rt ? `<strong>${esc(rt.text)}</strong>` +
              (bill ? ` اخزا امروز حدود ${num(bill, 0)}٪ می‌دهد.` : "")
            : `<strong>برتری سهام و طلا در آزمون‌ها نسبت به همین ۲۰٪ سنجیده شده؛ ` +
              `اوراق خزانه امروز ${bill ? `حدود ${num(bill, 0)}٪` : "بسیار بیشتر از آن"} ` +
              "می‌دهد؛ پس برتری ترکیب‌ها بر اوراقِ امروز ثابت نشده است.</strong>")
      : "سابقه‌ی شاخص یا صندوق‌های طلا در دیتابیس نیست — ارقام تاریخی " +
        "محاسبه نشد.");
}

async function loadAllocation(force = false) {
  const btn = $("runAllocationBtn");
  if (btn) btn.disabled = true;

  state.allocationFailed = false;

  try {
    const rep = await getJSON(`/api/allocation?${query()}${force ? "&refresh=1" : ""}`);
    renderAllocation(rep);
  } catch (err) {
    state.allocation = null;
    // The today card is built from this answer and has no banner of its own:
    // left alone it kept the previous equity's rial amounts after a failed
    // apply, with nothing marking them as stale.
    state.allocationFailed = true;
    banner("allocationStatus", "allocationStatusText", "is-error",
      `<strong>تخصیص محاسبه نشد.</strong> ${esc(err.message)}`);
    renderToday();
    console.error(err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------- cheapest way to fill each sleeve (opportunities.py) ----------
   Fund price against its own ابطال NAV, and treasury-bill yield to maturity.
   Neither is a forecast. */

function renderOpportunities(rep) {
  state.opportunitiesFailed = false;
  const labels = rep.sleeve_labels || {};

  // The headline (cheapest fund, best bill) now lives in the "today" card.
  bulletList("opportunitiesSummary", []);

  state.opportunities = rep;

  // The three cheapest of each sleeve first; every other fund behind
  // "show all", so the table answers "which one" at a glance.
  const fundRows = [];
  const ordered = [], rest = [];
  for (const k of ["gold", "equity", "fixed", "equity_active"]) {
    ((rep.funds || {})[k] || []).forEach((f, i) =>
      (i < 3 && k !== "equity_active" ? ordered : rest).push([k, f]));
  }
  for (const [k, f] of [...ordered, ...rest]) {
    {
      const flag = f.suspect
        ? `<span class="badge neutral"><span class="dot"></span>بررسی در سایت صندوق</span>`
        : f.notable
          ? `<span class="badge good"><span class="dot"></span>زیر NAV</span>`
          : "";
      fundRows.push([esc(labels[k] || k), `<strong>${esc(f.symbol)}</strong>`,
                     num(f.close, 0), num(f.nav_redeem, 0),
                     `${signed(f.discount_pct, 2)} ${flag}`, money(f.traded_value)]);
    }
  }
  $("opportunitiesFunds").innerHTML = fundRows.length
    ? table(["بخش", "صندوق", "قیمت پایانی", "NAV ابطال", "فاصله از NAV",
             "ارزش معاملات"], fundRows)
    : '<div class="empty">NAV صندوق‌ها خوانده نشد</div>';
  capRows("opportunitiesFunds", ordered.length);

  const bills = (rep.bills || {}).bills || [];
  $("opportunitiesBills").innerHTML = bills.length
    ? table(["اخزا", "سررسید", "روز مانده", "قیمت", "بازده سالانه تا سررسید",
             "ارزش معاملات"],
        bills.map(b => [`<strong>${esc(b.symbol)}</strong>`, esc(b.maturity_jalali || b.maturity),
                        num(b.days, 0), num(b.price, 0),
                        `${num(b.ytm_pct, 1)}٪` + (b.beats_fund
                          ? ` <span class="badge good"><span class="dot"></span>بیشتر از ۲۰٪</span>` : ""),
                        money(b.traded_value)]))
    : `<div class="empty">اخزا خوانده نشد ${esc((rep.bills || {}).error || "")}</div>`;

  capRows("opportunitiesBills", 5);

  bulletList("opportunitiesNotes", rep.notes || []);
  renderToday();

  banner("opportunitiesStatus", "opportunitiesStatusText", "is-live",
    `${fundRows.length} صندوق و ${bills.length} سری اخزا، از آخرین قیمت پایانی TSETMC.`);
}

async function loadOpportunities(force = false) {
  const btn = $("runOpportunitiesBtn");
  if (btn) btn.disabled = true;
  state.opportunitiesFailed = false;

  try {
    renderOpportunities(await getJSON(`/api/opportunities${force ? "?refresh=1" : ""}`));
  } catch (err) {
    // The slot goes with the failure (see rerender). The today card reads the
    // same slot, and with it empty would show "reading prices…" for a read
    // that has already failed; the flag lets it say so instead.
    state.opportunities = null;
    state.opportunitiesFailed = true;
    banner("opportunitiesStatus", "opportunitiesStatusText", "is-error",
      `<strong>خوانده نشد.</strong> ${esc(err.message)}`);
    renderToday();
    console.error(err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------- market money flow (moneyflow.market_daily) ----------
   Net individual money into the whole equity market, one bar per session.
   Rials from the server; shown in billion tomans, the unit the market
   quotes this figure in. */

const toBillionToman = v => v / 1e10;

function tomanCell(v) {
  if (v == null) return "—";
  const b = Math.round(toBillionToman(v));
  const cls = b > 0 ? "up" : b < 0 ? "down" : "";
  return `<span class="${cls}">${b > 0 ? "+" : ""}${b.toLocaleString("fa-IR")}</span>`;
}

// A stored UTC timestamp as the reader's clock reads it: Jalali date and
// Tehran time, instead of the raw "2026-11-11T03:30:55+00:00" the banner and
// the screen printed (found 2026-11-11).
// A server stamp (UTC) as Tehran's clock, seconds kept: the live events and
// the load log printed the UTC digits, 3h30 early (the full test).
function tehranClock(iso) {
  if (!iso) return "—";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return esc(String(iso));
  return d.toLocaleTimeString("fa-IR", { timeZone: "Asia/Tehran", hour: "2-digit",
                                        minute: "2-digit", second: "2-digit" });
}

function faTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return esc(String(iso));
    return d.toLocaleString("fa-IR-u-ca-persian", {
      timeZone: "Asia/Tehran", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit" });
  } catch (_err) { return esc(String(iso)); }
}

function faDay(iso, withYear = false) {
  // The market snapshot's price_date is compact (20261111) while the rest of
  // the panel sends ISO days; the holdings card fed it straight in and
  // printed "Invalid Date" (2026-11-23). An unreadable value is shown as it
  // came, never as that phrase.
  const text = String(iso ?? "");
  const day = /^\d{8}$/.test(text)
    ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
    : text.slice(0, 10);
  try {
    const d = new Date(`${day}T12:00:00`);
    if (Number.isNaN(d.getTime())) return esc(text);
    return d.toLocaleDateString("fa-IR-u-ca-persian",
      withYear ? { year: "numeric", month: "2-digit", day: "2-digit" }
               : { month: "2-digit", day: "2-digit" });
  } catch (e) { return esc(text); }
}

async function loadMarketflow() {
  const sessions = ($("marketflowSessions") || {}).value || "60";
  try {
    state.marketflow = await getJSON(`/api/marketflow?sessions=${encodeURIComponent(sessions)}`);
    renderMarketflow(state.marketflow);
  } catch (err) {
    state.marketflow = null;
    banner("marketflowStatus", "marketflowStatusText", "is-error",
      `<strong>خوانده نشد.</strong> ${esc(err.message)}`);
    console.error(err);
  }
}

function renderMarketflow(d) {
  if (!d || !d.last) {
    banner("marketflowStatus", "marketflowStatusText", "is-error",
      "<strong>داده‌ی حقیقی/حقوقی موجود نیست.</strong> بعد از دریافت عصرگاهی پر می‌شود." +
      bootstrapNote(d || {}));
    tiles("marketflowKpis", []);
    $("marketflowChart").innerHTML = "";
    return;
  }
  const last = d.last, typ = d.typical || {}, st = d.streak || {};
  const days = (d.sessions || []);
  banner("marketflowStatus", "marketflowStatusText", "is-live",
    `آخرین جلسه‌ی ثبت‌شده ${faDay(last.date, true)} — ${num(last.symbols, 0)} سهم` +
    (d.partial_days_skipped ? ` · ${num(d.partial_days_skipped, 0)} روز با داده‌ی ناقص کنار گذاشته شد` : ""));

  tiles("marketflowKpis", [
    { k: "آخرین جلسه (میلیارد تومان)", v: tomanCell(last.net_individual),
      sub: `${signed(last.net_pct, 1)} از ارزش معاملات` },
    { k: "۵ جلسه‌ی اخیر", v: tomanCell(d.sum_5), sub: "جمع خالص پول حقیقی" },
    { k: "۲۰ جلسه‌ی اخیر", v: tomanCell(d.sum_20), sub: "جمع خالص پول حقیقی" },
    { k: "روند", v: `${Number(st.days || 0).toLocaleString("fa-IR")} روز`,
      sub: st.direction === "in" ? "پشت‌سرهم ورود پول" : "پشت‌سرهم خروج پول" },
    { k: "نمادهای با ورود پول",
      v: `${Math.round(last.breadth_pct || 0).toLocaleString("fa-IR")}٪`,
      sub: `${last.inflow_count.toLocaleString("fa-IR")} از ${last.symbols.toLocaleString("fa-IR")} سهم در آخرین جلسه` },
    { k: "روز عادی (یک سال اخیر)", v: tomanCell(typ.median),
      sub: `میانه؛ ۶۰٪ روزها بین ${tomanCell(typ.p20)} و ${tomanCell(typ.p80)}` },
  ]);

  const data = days.map(x => ({
    label: faDay(x.date), value: toBillionToman(x.net_individual), day: x,
  }));
  divergingBar($("marketflowChart"), {
    data, height: 240, padLeft: 64,
    // About one date per 75px, at most twelve: on a phone twelve overlapped.
    labelEvery: Math.max(1, Math.ceil(data.length / Math.min(12,
      Math.max(3, Math.floor(($("marketflowChart").clientWidth || 900) / 75))))),
    format: v => Math.round(v).toLocaleString("fa-IR"),
    tooltip: p => `<div class="t-title">${esc(faDay(p.day.date, true))}</div>
      <div class="t-row"><span>خالص حقیقی</span><b>${Math.round(p.value).toLocaleString("fa-IR")} میلیارد تومان</b></div>
      <div class="t-row"><span>از ارزش معاملات</span><b>${num(p.day.net_pct, 1)}٪</b></div>
      <div class="t-row"><span>نمادهای با ورود پول</span><b>${num(p.day.inflow_count, 0)} از ${num(p.day.symbols, 0)}</b></div>`,
  });
}

/* ---------- Codal announcements (codal_announcements.py) ----------
   What the feed watch kept of the letters that move a price on the day.
   A read of the store: no network, so it loads with the summary tab. */

async function loadAnnouncements() {
  const kind = ($("announcementsKind") || {}).value || "";
  const days = ($("announcementsDays") || {}).value || "3";
  try {
    const d = await getJSON(`/api/announcements?days=${encodeURIComponent(days)}` +
      (kind ? `&kind=${encodeURIComponent(kind)}` : ""));
    renderAnnouncements(d);
  } catch (err) {
    banner("announcementsStatus", "announcementsStatusText", "is-error",
      `<strong>خوانده نشد.</strong> ${esc(err.message)}`);
    console.error(err);
  }
}

function renderAnnouncements(d) {
  const rows = d.rows || [];
  banner("announcementsStatus", "announcementsStatusText", rows.length ? "is-live" : "",
    rows.length
      ? `${num(rows.length, 0)} اطلاعیه در ${num(d.days, 0)} روز اخیر`
      : `در ${num(d.days, 0)} روز اخیر اطلاعیه‌ای از این نوع ثبت نشده است.`);
  const body = rows.map(r => `<tr>
      <td>${esc((r.published_at || "").slice(0, 16))}</td>
      <td><strong>${esc(r.symbol || "")}</strong></td>
      <td>${esc(r.kind_label || r.kind || "")}</td>
      <td>${r.url ? `<a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title || "")}</a>`
                  : esc(r.title || "")}</td>
    </tr>`).join("");
  $("announcementsTable").innerHTML = rows.length ? `<table>
      <thead><tr><th>زمان انتشار</th><th>نماد</th><th>نوع</th><th>عنوان</th></tr></thead>
      <tbody>${body}</tbody></table>` : "";
  capRows("announcementsTable", 15);
}

/* ---------- fundamental outlook (fundamental_outlook.py) ----------
   The current fiscal year completed from last year's remaining months,
   plus cash, funding and working-capital checks. Analysis, not a signal. */

const OUTLOOK_METHOD = {
  year_completed: "تکمیل سال با ماه‌های سال قبل",
  next_year_trend: "سال بعد با رشد و حاشیه‌ی امسال",
  annualised: "سالانه‌سازی ساده (سال قبل در دست نیست)",
  monthly_sales: "سال قبل × رشد فروش گزارش ماهانه (تازه‌ترین داده)",
};

function pctCell(v, digits = 1) {
  return v == null ? "—" : `${num(v, digits)}٪`;
}

// Which statements the read rests on: never a group against its parent.
const OUTLOOK_BASIS = {
  consolidated: "تلفیقی (کل گروه)",
  separate: "جداگانه (خود شرکت)",
  mixed: "تلفیقی و جداگانه (هم‌اندازه‌اند)",
  separate_fallback: "جداگانه — تلفیقی تازه هنوز منتشر نشده",
};

// The price's growth hurdle against the bill yield, as one readable cell.
function hurdleCell(h) {
  if (!h || h.required_growth_pct == null) return "—";
  if (h.required_growth_pct <= 0) return '<span class="hint">لازم نیست</span>';
  const ok = h.cushion_pct != null && h.cushion_pct >= 0;
  // Set against what is paid out, when an AGM is on record (the earnings
  // kept back are what pays for the growth).
  return `${num(h.required_growth_pct, 0)}٪` + (h.actual_growth_pct == null ? "" :
    ` <span class="${ok ? "up" : "down"}">(فعلی ${num(h.actual_growth_pct, 0)}٪)</span>`) +
    (h.payout_pct == null ? "" : ` <span class="hint">با تقسیم سود ${num(h.payout_pct, 0)}٪</span>`);
}

// Whether the price the multiples rest on can be dealt at (trading_read in
// fundamental_outlook.py): mean daily value, the band, queue sessions.
function tradingCell(t) {
  if (!t) return "—";
  const parts = [`میانگین ${money(t.avg_value)} ریال در روز` +
    (t.illiquid ? ` <span class="down">(زیر کف ${money(t.floor)})</span>` : "")];
  if (t.traded_sessions < t.sessions)
    parts.push(`${num(t.sessions - t.traded_sessions, 0)} جلسه بدون معامله`);
  if (t.band_pct != null)
    parts.push(`دامنه ±${num(t.band_pct, 1)}٪` + (t.narrow_band ? ' <span class="down">(محدود)</span>' : ""));
  if (t.queue_up_sessions) parts.push(`${num(t.queue_up_sessions, 0)} جلسه صف خرید`);
  if (t.queue_down_sessions) parts.push(`${num(t.queue_down_sessions, 0)} جلسه صف فروش`);
  return parts.join(" · ");
}

// The estimate's range: where the year's profit landed against past
// estimates of the same kind (months known, margin band).
function rangeCell(rg) {
  if (!rg) return "—";
  // What the band was measured on: the statement method on its own past, the
  // monthly method by emulation, or a year no statement of which exists yet.
  const on = {monthly: "شبیه‌سازی روش گزارش ماهانه روی", next_year: "سال هنوز شروع‌نشده؛"}[rg.basis] || "";
  // How often this band held on years it was NOT built from (leave one
  // fiscal year out, point-in-time figures; outlook_calibration, 2026-11-24).
  // A natural frequency, not a percentage, and the worst year beside the
  // pooled rate: one shock year (1405) hits every company at once.
  const cv = rg.coverage;
  const expect = cv && cv.nominal != null ? Math.round(cv.nominal * 10) : null;
  const held = cv && cv.per10 != null
    ? `؛ در سال‌های کنارگذاشته ${num(cv.per10, 0)} مورد از ۱۰ داخل این بازه افتاد` +
      (expect != null ? ` (انتظار ${num(expect, 0)})` : "") +
      (cv.worst_year && cv.worst_year.rate != null && cv.worst_year.year
        ? `، در ${esc(cv.worst_year.year)} فقط ${num(Math.round(cv.worst_year.rate * 10), 0)} از ۱۰` : "")
    : "";
  const pit = rg.source === "pit" ? "، با ارقامی که در روز تصمیم منتشر شده بود" : "";
  // The forward test's retirement rule (OUTLOOK_FORWARD_PROTOCOL.json):
  // once retired, the range is shown as a report only.
  const outlookCard = typeof trackState !== "undefined" && trackState.reg
    ? (trackState.reg.by_id || {}).outlook : null;
  const reportOnly = outlookCard && outlookCard.extra && outlookCard.extra.range_report_only
    ? ' <span class="badge warn"><span class="dot"></span>فقط گزارش — در آزمون پیش‌رو بازنشسته شد</span>' : "";
  return `${num(rg.eps_low, 0)} تا ${num(rg.eps_high, 0)} ریال` +
    ` <span class="hint">(خطای میانه ${num(rg.median_abs_error_pct, 0)}٪ در ${on} ${num(rg.n, 0)} برآورد گذشته` +
    `${rg.band_label ? "، حاشیه‌ی " + esc(rg.band_label) : ""}${pit}${held})</span>` +
    (rg.wide ? ' <span class="down">پراکنده</span>' : "") + reportOnly;
}

// Reference multiples on the trailing EPS; the industry's is the centre.
function fairCell(fv) {
  if (!fv || fv.central == null) return "—";
  if (fv.basis === "nav") {
    // A NAV with unlisted holdings at cost is a floor, and holdings trade
    // below NAV as a rule: the price's share of it, never an "upside".
    return `${num(fv.central, 0)} ریال <span class="hint">(ارزش خالص دارایی هر سهم${fv.floor ? "؛ کف، غیربورسی‌ها به بهای تمام‌شده" : ""})</span>` +
      (fv.price_to_value == null ? "" : ` — قیمت ${num(fv.price_to_value * 100, 0)}٪ آن است`);
  }
  const by = {sector: "P/E صنعت", own: "P/E خود سهم", market: "P/E بازار"}[fv.central_basis] || "";
  return `${num(fv.central, 0)} ریال <span class="hint">(${by} ${num(fv.central_multiple, 2)})</span> ` +
    `${signed(fv.upside_pct, 0)}` +
    ` <span class="hint">— بازه ${num(fv.low, 0)} تا ${num(fv.high, 0)}` +
    `${fv.disagree ? "؛ P/E صنعت و خود سهم از هم دورند، مرکز قطعی نیست" : ""}؛ نشانه‌ی خرید نیست (E30 تا E33)</span>`;
}

// What-ifs on the exchange-centre rate; the fit is weak and says so.
function dollarCell(d) {
  if (!d || !(d.scenarios || []).length) return "—";
  // Two readings per scenario: the measured (weak) slope, and a business
  // priced wholly in dollars.
  const parts = d.scenarios.map(s => `نیما ${signed(s.shock_pct, 0)}: سود سال ${signed(s.change_pct, 1)}` +
    (s.full_change_pct == null ? "" : ` تا ${signed(s.full_change_pct, 1)}`));
  return parts.join(" · ") +
    ` <span class="hint">(عدد اول با رابطه‌ی گذشته، R² ${num((d.sensitivity || {}).r2, 2)}` +
    `${d.weak ? " — ضعیف" : ""}؛ عدد دوم اگر شرکت کاملاً دلاری باشد. برآورد پایه فرضی درباره‌ی دلار ندارد)</span>`;
}

// The report takes 3 to 11 seconds to build, and a new tab that stays
// blank that long reads as broken (the UI/UX review, 2026-11-23): the tab
// opens at once saying what is happening, then goes to the PDF.
function openReport(symbol) {
  const sym = (symbol || "").trim();
  if (!sym) return false;
  const url = `/api/report?symbol=${encodeURIComponent(sym)}`;
  const tab = window.open("", "_blank");
  if (!tab) { location.href = url; return true; }      // popups blocked
  try {
    tab.document.title = `گزارش ${sym}`;
    tab.document.body.innerHTML =
      `<p dir="rtl" style="font-family:Tahoma,sans-serif;padding:32px;color:#475467">` +
      `گزارش «${esc(sym)}» در حال ساخت است؛ چند ثانیه طول می‌کشد…</p>`;
  } catch (e) { /* the tab still navigates */ }
  tab.location.href = url;
  return true;
}

function openOutlookPdf() {
  if (!openReport($("outlookSymbol").value)) $("outlookSymbol").focus();
}

// The header's symbol box: the same two actions from anywhere on the page.
function quickLookup() {
  const sym = ($("quickSymbolInput").value || "").trim();
  if (!sym) { $("quickSymbolInput").focus(); return; }
  $("outlookSymbol").value = sym;
  gotoCard("analysis:outlookSymbol");
  lookupOutlook();
}

function renderOutlookDetail(r) {
  const v = r.valuation || {}, g = r.growth || {}, mg = r.margins || {};
  const c = r.cash || {}, b = r.balance || {}, l = r.latest || {};
  const rows = [
    ["آخرین صورت مالی", `${esc(l.period_end)} — ${l.months} ماهه` +
      (l.audited ? " (حسابرسی‌شده)" : " (حسابرسی‌نشده)")],
    ["روش برآورد", esc(OUTLOOK_METHOD[r.method] || "برآورد ممکن نیست")],
    ["مبنای صورت‌های مالی", esc(OUTLOOK_BASIS[r.basis] || r.basis || "—")],
    ["رشد فروش / سود (نسبت به همان دوره‌ی سال قبل)",
      `${pctCell(g.revenue_pct)} / ${pctCell(g.profit_pct)} — تورم مبنا ${pctCell(g.inflation_pct, 0)} (سالانه${g.inflation_as_of ? "، " + esc(g.inflation_as_of) : ""}؛ نقطه‌به‌نقطه ${pctCell(g.inflation_point_pct, 0)})`],
    ["حاشیه سود ناخالص / عملیاتی / خالص",
      `${pctCell(mg.gross_pct)} / ${pctCell(mg.operating_pct)} / ${pctCell(mg.net_pct)}` +
      (mg.net_prev_pct != null ? ` (سال قبل ${pctCell(mg.net_prev_pct)})` : "")],
    ["سود هر سهم: ۱۲ ماه گذشته / برآورد سال جاری",
      `${num((r.ttm || {}).eps, 0)} / ${num((r.forward || {}).eps, 0)} ریال`],
    // Measured on the method's own past estimates (outlook_calibration.py).
    ["بازه‌ی محتمل سود هر سهم (نیمی از برآوردهای مشابه گذشته)", rangeCell((r.forward || {}).range)],
    ["ارزش با ضریب‌های مرجع (روی سود ۱۲ ماهه)", fairCell(r.fair_value)],
    ["سناریوی دلار نیما (ماه‌های باقی‌مانده)", dollarCell(r.fx)],
    ["P/E گذشته / آینده‌نگر", `${num(v.pe_ttm, 1)} / ${num(v.pe_forward, 1)}`],
    ["P/E اعلامی بورس (TSETMC)", publishedPeCell({pe: v.pe_ttm, pe_published: v.pe_published})],
    ["بازده سود برآوردی", pctCell(v.earnings_yield_forward_pct)],
    ["رشد لازم برای توجیه قیمت (در برابر اخزا) / رشد فعلی",
      r.hurdle && r.hurdle.required_growth_pct != null
        ? `${hurdleCell(r.hurdle)} — اخزا ${pctCell(r.hurdle.rate_pct, 1)} (${esc(r.hurdle.as_of || "")})`
        : "—"],
    ["P/NAV (ارزش خالص دارایی‌ها)", v.price_to_nav == null ? "—" :
      `${num(v.price_to_nav, 2)} — پرتفوی ${esc(v.nav_period || "")}` +
      (v.nav_stale && v.price_to_nav_est != null
        ? ` (کهنه؛ با حرکت شاخص حدود ${num(v.price_to_nav_est, 2)}، تخمینی)` : "") +
      ((v.nav_notes || []).length
        ? `<br><span class="hint">⚠ ${(v.nav_notes || []).map(esc).join("<br>⚠ ")}</span>` : "") +
      (v.nav_complete === false ? " (سهام غیربورسی به بهای تمام‌شده؛ NAV کف است)" : "")],
    ["معاملات ۲۰ جلسه‌ی اخیر", tradingCell(r.trading)],
    ["ارزش بازار", v.market_cap == null ? "—" :
      `${money(v.market_cap)} ریال` + (v.shares_source === "capital"
        ? " (تعداد سهم از سرمایه‌ی صورت مالی؛ از بورس در دست نبود)" : "")],
    ["سود نقدی آخرین مجمع / بازده نقدی", v.dps == null ? "— (مجمع ثبت‌شده‌ای در دست نیست)" :
      `${num(v.dps, 0)} ریال (سال مالی ${esc(v.dividend_fiscal_year)}) / ${pctCell(v.dividend_yield_pct)}`],
    ["P/B و ROE دوازده‌ماهه", `${num(v.pb, 2)} / ${pctCell(v.roe_ttm_pct)}`],
    ["تبدیل سود به نقد عملیاتی", c.cash_conversion == null ? "—" :
      `${num(c.cash_conversion, 2)} برابر` + (c.cash_conversion_basis === "ttm" ? " (۱۲ ماه گذشته)" : " (همین دوره)")],
    ["بازده جریان نقد آزاد", pctCell(v.fcf_yield_pct)],
    ["بدهی به حقوق صاحبان سهام / پوشش هزینه مالی",
      `${num(b.debt_to_equity, 2)} / ${b.interest_cover == null ? "—" : num(b.interest_cover, 1) + " برابر"}`],
    ["دوره وصول مطالبات", b.receivable_days == null ? "—" : `${num(b.receivable_days, 0)} روز`],
  ];
  const flags = (r.flags || []).map(f => `<li>⚠ ${esc(f)}</li>`).join("") +
    (r.notices || []).map(f => `<li>ⓘ ${esc(f)}</li>`).join("");
  const pos = (r.positives || []).map(f => `<li>✓ ${esc(f)}</li>`).join("");
  // A lookup without the ranking showed its ticks with nothing of the
  // record: the ranking these numbers feed lost to the index (E30-E33).
  const record = '<p class="hint help-warn">این اعداد برای شناخت شرکت‌اند، نه انتخاب خرید: ' +
    'خرید ده نام بالای رتبه‌بندی بنیادی در ۱۴۰۰ تا ۱۴۰۵ سالانه حدود ۳۵٪ داد در برابر ۴۴٪ ' +
    'شاخص کل (E30، رد شد)؛ هیچ‌کدام از آزمون‌های E30 تا E33 از شاخص جلو نزد ' +
    '(این ارقام بدون سود نقدی‌اند و فاصله را حدود ۳ تا ۴ واحد در سال بیشتر نشان می‌دهند).</p>';

  // The monthly activity report (ن-۳۰): newer than any statement.
  let monthlyHtml = "";
  const m = r.monthly;
  if (m) {
    const pctOf = x => x == null ? "—" : `${signed(x * 100, 0)}`;
    const mrows = [
      ["آخرین گزارش ماهانه", `${esc(m.period_end)} — ماه ${m.months_elapsed} سال مالی`],
      ["فروش این ماه / از ابتدای سال", `${money(m.sales_month)} / ${money(m.sales_ytd)} ریال`],
      ["رشد فروش از ابتدای سال نسبت به سال قبل", pctOf(m.ytd_growth)],
      ["فروش این ماه نسبت به میانگین ماهانه‌ی امسال",
        m.momentum == null ? "—" : `${num(m.momentum, 2)} برابر`],
      ["رشد خرید مواد اولیه", pctOf(m.purchases_growth)],
      ["خرید مواد اولیه به فروش", m.purchases_to_sales == null ? "—" : pctCell(m.purchases_to_sales * 100, 0)],
    ];
    if (m.expense_to_income != null) {
      mrows.push([m.template === "insurance" ? "خسارت به حق بیمه" : "هزینه به درآمد",
                  pctCell(m.expense_to_income * 100, 0)]);
    }
    if (m.fx_forecast_rest_of_year) {
      mrows.push(["برآورد خود شرکت از درآمد ارزی تا پایان سال", `${money(m.fx_forecast_rest_of_year)} ریال`]);
    }
    const prows = (m.products || []).filter(p => p.amount_ytd).map(p => [
      esc(p.name), esc(p.unit || ""), money(p.amount_ytd),
      pctOf(p.price_change), pctOf(p.volume_change),
      p.production_to_sales == null ? "—" : num(p.production_to_sales, 2)]);
    monthlyHtml = `<h3 style="margin-top:12px">گزارش فعالیت ماهانه</h3>` +
      table(["شاخص", "مقدار"], mrows) +
      (prows.length ? table(["محصول", "واحد", "فروش از ابتدای سال (ریال)",
        "تغییر نرخ", "تغییر مقدار فروش", "تولید به فروش"], prows) : "");
  }

  $("outlookDetail").innerHTML =
    `${record}<h3>${esc(r.symbol || "")} <span class="hint">${esc(r.name || "")}</span>` +
    ` <a class="link" href="/api/report?symbol=${encodeURIComponent(r.symbol || "")}" target="_blank" rel="noopener">گزارش PDF ←</a></h3>` +
    table(["شاخص", "مقدار"], rows) + monthlyHtml +
    `<ul class="evidence" style="margin-top:8px">${flags}${pos}</ul>`;
}

function renderOutlook(rep) {
  const rows = (rep.ranked || []).map(r => {
    const v = r.valuation || {}, g = r.growth || {};
    const vs = v.pe_vs_sector == null ? "—"
      : `<span class="${v.pe_vs_sector < 1 ? "up" : "down"}">${num(v.pe_vs_sector, 2)}</span>` +
        ` <span class="hint">(میانه ${num(v.sector_pe_median, 1)})</span>`;
    return [`<strong>${esc(r.symbol)}</strong>`, esc(r.sector || ""),
      `${esc(r.latest.period_end)} (${r.latest.months}م${r.basis === "consolidated" ? "، تلفیقی" : ""})`,
      pctCell(g.revenue_pct), num(v.pe_ttm, 1), num(v.pe_forward, 1), vs,
      pctCell(v.earnings_yield_forward_pct), hurdleCell(r.hurdle),
      r.monthly && r.monthly.ytd_growth != null
        ? `${signed(r.monthly.ytd_growth * 100, 0)} <span class="hint">(${esc(r.monthly.period_end)})</span>`
        : "—",
      (r.flags || []).map(esc).join("<br>") || "—"];
  });
  const fin = (rep.financials || []).map(r => {
    const v = r.valuation || {};
    return [`<strong>${esc(r.symbol)}</strong>`, esc(r.sector || ""),
      v.price_to_nav == null ? "—"
        : v.nav_stale
          ? (v.price_to_nav_est != null ? `${num(v.price_to_nav_est, 2)} <span class="hint">تخمینی</span> ` : "") +
            `<span class="hint">(به قیمت ${esc(v.nav_period || "")}: ${num(v.price_to_nav, 2)}؛ شاخص از آن زمان ${signed(v.nav_index_move_pct, 0)})</span>`
          : `${num(v.price_to_nav, 2)} <span class="hint">(${esc(v.nav_period || "")})</span>`,
      num(v.pb, 2), pctCell(v.roe_ttm_pct), pctCell(v.dividend_yield_pct)];
  });
  const sus = (rep.suspect || []).map(x =>
    `${esc(x.symbol)} <span class="hint">(${esc(x.reason)}، ${esc(x.period_end)})</span>`).join("، ");
  // Below the liquidity floor: the board price is not one a buyer can deal
  // at, so a low P/E on it is not a bargain (شصفها, 2026-11-11).
  const thin = (rep.thin || []).map(x =>
    `${esc(x.symbol)} <span class="hint">(${money(x.avg_value)} در روز` +
    (x.pe_forward != null ? `، P/E آینده‌نگر ${num(x.pe_forward, 1)}` : "") + `)</span>`).join("، ");
  const thinMore = (rep.thin_count || 0) - (rep.thin || []).length;
  // A multiple that is not a valuation: a one-off profit, a gap to the
  // exchange's own P/E, a year projected from its first months.
  const doubt = (rep.doubtful || []).map(x =>
    `${esc(x.symbol)} <span class="hint">(${(x.reasons || []).map(esc).join("؛ ")})</span>`).join("، ");
  $("outlookTable").innerHTML = (rows.length
    ? table(["نماد", "صنعت", "آخرین دوره", "رشد فروش", "P/E گذشته",
             "P/E آینده‌نگر", "P/E به میانه‌ی صنعت", "بازده سود برآوردی",
             "رشد لازم (فعلی)", "رشد فروش ماهانه", "هشدار"], rows)
    : '<div class="empty">هنوز شرکتی با داده‌ی کافی برای برآورد نیست</div>') +
    (fin.length ? `<h3 style="margin-top:16px">بانک، بیمه، سرمایه‌گذاری و هلدینگ
      <span class="hint">— با P/NAV و P/B سنجیده می‌شوند، نه P/E (ارزان‌ترین اول)</span></h3>` +
      table(["نماد", "صنعت", "P/NAV (تاریخ پرتفوی)", "P/B", "ROE دوازده‌ماهه", "بازده نقدی"], fin) : "") +
    (sus ? `<p class="hint" style="margin-top:12px">⚠ داده‌ی ناممکن — رتبه‌بندی نشد، صورت مالی را دستی ببینید: ${sus}</p>` : "") +
    (doubt ? `<p class="hint" style="margin-top:12px">⚠ P/E غیرقابل اتکا — رتبه‌بندی نشد؛ ` +
      `این فهرست بر همین P/E مرتب می‌شود و این‌جا P/E ارزش‌گذاری نیست (برای جزئیات نماد را بررسی کنید): ${doubt}</p>` : "") +
    (thin ? `<p class="hint" style="margin-top:12px">⚠ کم‌معامله — رتبه‌بندی نشد؛ قیمت تابلو قیمتی نیست ` +
      `که بتوان با آن خرید، پس P/E پایینشان ارزانی نیست (ارزان‌نماترین اول): ${thin}` +
      (thinMore > 0 ? ` و ${num(thinMore, 0)} نماد دیگر` : "") + `</p>` : "");
  const x = rep.excluded || {}, o = rep.opportunity || {};
  // E30 tested this very ranking (registered, 2021-06..2026-10): its top ten
  // returned 35.0%/yr against TEDPIX 44.4% (p=0.24); only the bottom fifth
  // was informative, as names to avoid. "Untested analysis" understated that
  // (the 20-year reviewer, 2026-11-11), so the record leads, in amber.
  banner("outlookStatus", "outlookStatusText", "",
    "<strong>این رتبه‌بندی آزموده شده است:</strong> خرید ده نام بالای همین فهرست " +
    "در ۱۴۰۰ تا ۱۴۰۵ سالانه حدود ۳۵٪ داد در برابر ۴۴٪ شاخص کل (آزمون E30، رد شد). " +
    "قیمت‌های آن آزمون سود نقدی را نداشتند و شاخص کل دارد، پس فاصله‌ی واقعی حدود ۳ تا ۴ واحد در سال کمتر است؛ " +
    "حکم رد عوض نمی‌شود: ده نام بالا از نگه‌داشتن همه‌ی شرکت‌های واجد شرایط بهتر نبودند. " +
    "فهرست برای شناخت شرکت‌هاست، نه انتخاب خرید. (اینکه پایین فهرست بدتر بود فقط مشاهده‌ی جانبی همان آزمون است و خودش آزموده نشده.)<br>" +
    `${rep.count} شرکت تولیدی/خدماتی رتبه‌بندی شد (بیشترین بازده سود برآوردی اول) ` +
    `و ${rep.financials_count || 0} شرکت مالی جداگانه. ` +
    `کنار گذاشته: ${x.no_estimate || 0} بدون صورت مالی سال قبل، ` +
    `${x.basis_gap || 0} که صورت تلفیقی تازه‌شان هنوز منتشر نشده، ` +
    `${x.suspect || 0} با داده‌ی ناممکن، ` +
    `${x.illiquid || 0} کم‌معامله (میانگین کمتر از ${money(rep.liquidity_floor)} ریال در روز)، ` +
    `${x.doubtful || 0} با P/E غیرقابل اتکا، ` +
    `${x.stale || 0} با صورت مالی کهنه، ${x.flags || 0} با بیش از یک هشدار، ` +
    `${x.loss || 0} زیان‌ده، ${x.no_filing || 0} بدون صورت مالی، ` +
    `${x.halted || 0} متوقف بیش از یک ماه. ` +
    `معیار مقایسه: بازده اخزا ${num(rep.bar_pct, 1)}٪` +
    (o.as_of ? ` (${esc(o.as_of)})` : "") + ".");
}

async function loadOutlook(force = false) {
  const btn = $("runOutlookBtn");
  if (btn) btn.disabled = true;
  try {
    renderOutlook(await getJSON(`/api/outlook${force ? "?refresh=1" : ""}`));
  } catch (err) {
    banner("outlookStatus", "outlookStatusText", "is-error",
      `<strong>محاسبه نشد.</strong> ${esc(err.message)}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function lookupOutlook() {
  const sym = ($("outlookSymbol").value || "").trim();
  if (!sym) return;
  try {
    renderOutlookDetail(await getJSON(`/api/outlook?symbol=${encodeURIComponent(sym)}`));
  } catch (err) {
    $("outlookDetail").innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

/* ---------- AGM, dividend and capital-increase calendar (2026-11-24) ----------
   Research report item 2: the dates a hand trader acts on -- a meeting
   halts the symbol, a dividend is due within the legal deadline, a capital
   increase moves through its stages. corporate_calendar.calendar() builds it
   from stored Codal letters and the exchange's own adjustment list, with no
   request of its own. Operational information, never a buy signal. */
var calendarState = { data: null };

function calendarHeld() {
  const held = new Set();
  for (const line of holdingsText().split(/\r?\n/)) {
    const first = line.trim().split(/\s+/)[0];
    if (first) held.add(first.replace(/ي/g, "ی").replace(/ك/g, "ک"));
  }
  return held;
}

function calendarDate(d) {
  return d && d.jalali ? esc(d.jalali) : "—";
}

function calendarLink(letter, text) {
  if (!letter || !letter.url) return esc(text || "");
  return `<a href="${esc(safeUrl(letter.url))}" target="_blank" rel="noopener noreferrer">${esc(text || letter.title || "")}</a>`;
}

function calendarSymbol(sym, held) {
  const mine = held.has(String(sym || "").replace(/ي/g, "ی").replace(/ك/g, "ک"));
  return `<strong>${esc(sym || "")}</strong>` +
    (mine ? ` <span class="badge good"><span class="dot"></span>در سبد شما</span>` : "");
}

function calendarSort(items, held) {
  const key = (x) => held.has(String(x.symbol || "").replace(/ي/g, "ی").replace(/ك/g, "ک")) ? 0 : 1;
  return [...items].sort((a, b) => key(a) - key(b));
}

function renderCalendar(rep) {
  calendarState.data = rep;
  const held = calendarHeld();
  const onlyMine = !!($("calendarMine") && $("calendarMine").checked);
  const keep = (items) => calendarSort(items || [], held).filter(x =>
    !onlyMine || held.has(String(x.symbol || "").replace(/ي/g, "ی").replace(/ك/g, "ک")));
  const LIMIT = 15;
  const cut = (items) => items.slice(0, LIMIT);
  const more = (items) => items.length > LIMIT
    ? `<p class="hint">${num(items.length - LIMIT, 0)} مورد دیگر نشان داده نشد؛ «فقط نمادهای سبد من» را بزنید یا کارت اطلاعیه‌ها را ببینید.</p>` : "";

  const pending = keep(rep.pending_meetings);
  const decided = keep(rep.recent_decisions);
  const capital = keep((rep.capital_increases || []).filter(c => c.status !== "stale"));
  const stale = (rep.capital_increases || []).filter(c => c.status === "stale").length;

  const pendingRows = cut(pending).map(p => [
    calendarSymbol(p.symbol, held),
    esc(p.meeting_type_fa || ""),
    calendarLink(p.invitation, (p.invitation || {}).published_jalali || "دعوت"),
    `${calendarDate(p.expected_from)} تا ${calendarDate(p.expected_to)}`,
    calendarDate(p.typical_date),
    esc(p.status_fa || ""),
  ]);
  const decidedRows = cut(decided).map(d => {
    const dps = d.dps_exchange != null ? d.dps_exchange : d.dps_codal;
    const src = d.dps_exchange != null ? "تعدیل بورس" : (d.dps_codal != null ? "کدال" : "");
    return [
      calendarSymbol(d.symbol, held),
      esc(d.meeting_type_fa || ""),
      calendarLink(d.decision, (d.meeting_date || {}).jalali || "—") +
        (d.meeting_date_basis === "letter_published_approx" ? ' <span class="hint">(تاریخ نامه)</span>' : ""),
      dps != null ? `${num(dps, 0)} ریال <span class="hint">(${esc(src)})</span>` : "—",
      d.payment_deadline ? calendarDate(d.payment_deadline) : "—",
    ];
  });
  const capitalRows = cut(capital).map(c => [
    calendarSymbol(c.symbol, held),
    calendarLink(c.latest_stage, (c.latest_stage || {}).label_fa || ""),
    calendarDate((c.latest_stage || {}).date),
    calendarDate(c.started),
    esc(c.status_fa || ""),
  ]);

  const section = (title, count, open, body) =>
    `<details class="calendar-part"${open ? " open" : ""}><summary>${esc(title)} — ${num(count, 0)} مورد</summary>${body}</details>`;

  $("calendarBody").innerHTML =
    section("مجمع‌های در پیش (نماد معمولاً در روز مجمع متوقف می‌شود)", pending.length, true,
      pendingRows.length ? table(["نماد", "نوع مجمع", "دعوت", "بازه‌ی قانونی برگزاری", "تاریخ معمول", "وضعیت"], pendingRows) + more(pending)
                         : `<p class="hint">موردی نیست.</p>`) +
    section("تصمیم‌های اخیر مجمع و سود نقدی", decided.length, true,
      decidedRows.length ? table(["نماد", "نوع مجمع", "تاریخ مجمع", "سود هر سهم", (rep.rules || {}).payment_deadline_label || "مهلت پرداخت"], decidedRows) + more(decided)
                         : `<p class="hint">موردی نیست.</p>`) +
    section("افزایش سرمایه در جریان", capital.length, false,
      (capitalRows.length ? table(["نماد", "آخرین مرحله", "تاریخ", "شروع", "وضعیت"], capitalRows) + more(capital)
                          : `<p class="hint">موردی نیست.</p>`) +
      (stale ? `<p class="hint">${num(stale, 0)} پرونده‌ی افزایش سرمایه مدتی است نامه‌ی تازه‌ای ندارد و نشان داده نشد.</p>` : ""));

  const cov = rep.coverage || {};
  const watch = cov.watch_since && cov.watch_since.jalali;
  const unwatched = (cov.unwatched_codes || []).length;
  banner("calendarStatus", "calendarStatusText", "is-live",
    `${esc(rep.note || "")} ` +
    (watch ? `پایش خوراک کدال از ${esc(watch)} همه‌ی نامه‌های مجمع و افزایش سرمایه را نگه داشته؛ ` : "") +
    (unwatched ? `${num(unwatched, 0)} نوع نامه (مثل مهلت حق تقدم) فقط با جست‌وجوی تک‌شرکتی می‌رسد و ممکن است جا افتاده باشد. ` : "") +
    `تاریخ‌ها تا ${esc(rep.as_of_jalali || "")}.`);
}

async function loadCalendar() {
  if (!$("calendarBody")) return;
  try {
    renderCalendar(await getJSON("/api/corporate_calendar"));
  } catch (err) {
    banner("calendarStatus", "calendarStatusText", "is-error",
      `تقویم خوانده نشد: ${esc(err.message || err)}`);
  }
}

on("calendarMine", "change", () => { if (calendarState.data) renderCalendar(calendarState.data); });

/* ---------- my holdings (holdings.py) ----------
   The list is the user's own and lives in this browser only; the server is
   asked for the numbers, never told to remember the book. */

const HOLDINGS_KEY = "sirat-holdings";

function holdingsText() {
  try { return localStorage.getItem(HOLDINGS_KEY) || ""; } catch (e) { return ""; }
}

function renderHoldings(rep) {
  state.holdingsBook = rep;           // the today card sizes against it
  const rows = rep.rows || [];
  const warn = rep.warnings || [];
  const flagged = rows.filter(r => ((r.outlook || {}).flags || []).length).length;
  tiles("holdingsKpis", [
    { k: "ارزش کل فهرست", v: rep.total_value ? money(rep.total_value) : "—",
      sub: rep.total_value ? "ریال، به آخرین قیمت پایانی" : "تعداد سهم وارد نشده" },
    // The newest price dates the book; the lines priced earlier are counted
    // beside it and named in the notes below (2026-11-23).
    { k: "نمادها", v: num(rows.length, 0),
      sub: rep.as_of ? `قیمت‌ها تا ${faDay(rep.as_of, true)}` +
        ((rep.stale || []).length ? ` — ${num(rep.stale.length, 0)} نماد با قیمت قدیمی‌تر` : "") : "" },
    { k: "با هشدار بنیادی", v: num(flagged, 0), sub: "از کارت چشم‌انداز بنیادی" },
    { k: "نکته‌ها", v: num(warn.length, 0), sub: warn.length ? "فهرست زیر" : "موردی نیست" },
  ]);
  $("holdingsWarnings").innerHTML = warn.map(w => `<li>⚠ ${esc(w)}</li>`).join("");
  renderHoldingsRisk(rep.risk);
  if (state.allocation) renderToday();   // the plan is sized on this book

  const body = rows.map(r => {
    if (r.error) {
      return [`<strong>${esc(r.symbol)}</strong>`, `<span class="down">${esc(r.error)}</span>`,
              "—", "—", "—", "—", "—", "—", "—", "—"];
    }
    if (r.cash) {
      // «نقد <مبلغ>»: money waiting in the account, counted in the
      // fixed-income sleeve at face value (holdings.py, 2026-11-23).
      return [`<strong>${esc(r.symbol)}</strong> <span class="hint">وجه نقد — در بخش درآمد ثابت</span>`,
              "—", "—", "—", r.value == null ? "—" : money(r.value),
              r.weight_pct == null ? "—" : `${num(r.weight_pct, 1)}٪`, "—", "—", "—", "—"];
    }
    if (r.bill) {
      // Priced on the server from the bills card's last read; the bills read
      // in this page stand in when the server had none.
      const b = r.price ? { price: r.price, ytm_pct: r.ytm_pct } : billQuote(r.symbol);
      return [`<strong>${esc(r.symbol)}</strong> <span class="hint">اخزا — درآمد ثابت</span>`,
              b ? num(b.price, 0) : "—", "—", r.quantity == null ? "—" : num(r.quantity, 0),
              b && r.quantity ? money(b.price * r.quantity) : "—",
              r.weight_pct == null ? "—" : `${num(r.weight_pct, 1)}٪`,
              b && b.ytm_pct != null ? `<span class="hint">${num(b.ytm_pct, 1)}٪ سالانه تا سررسید</span>` : '<span class="hint">قیمت اخزا هنوز خوانده نشده</span>',
              "—", "—", "—"];
    }
    const o = r.outlook || {};
    const flags = o.flags || [];
    // A move past the price band is a capital increase, a unit split or a
    // reopening: shown, but not in the colour of a day's gain or loss.
    const chg = r.halted
      ? `<span class="down">متوقف ${r.halted_since ? `از ${esc(r.halted_since)}` : "— هرگز معامله نشده"}</span>`
      : r.missed_last_session
        ? `<span class="hint" title="در آخرین جلسه معامله نشد؛ قیمت، قیمت آخرین معامله است">بی‌معامله از ${esc(r.last_trade || "")}</span>`
      : (r.change_pct == null ? "—"
        : r.change_beyond_band
          ? `<span class="hint" title="بیش از دامنه‌ی نوسان روزانه: احتمالاً افزایش سرمایه، تقسیم واحد یا بازگشایی نماد — نه سود یا زیان واقعی">⚠ ${num(r.change_pct, 1)}٪</span>`
          : signed(r.change_pct, 2, "٪"));
    const flagCell = !r.equity ? '<span class="hint">صندوق</span>'
      : (flags.length
        ? `<span class="down" title="${esc(flags.join("\n"))}">⚠ ${num(flags.length, 0)}</span>` +
          ` <span class="hint">${esc(flags[0].slice(0, 48))}${flags[0].length > 48 ? "…" : ""}</span>`
        : '<span class="up">✓ بدون هشدار</span>');
    const pe = !r.equity ? "—"
      : o.kind === "financial"
        ? (o.price_to_nav == null ? '<span class="hint">مالی</span>'
          : o.nav_stale && o.price_to_nav_est != null
            ? `P/NAV ~${num(o.price_to_nav_est, 2)} <span class="hint" title="${esc((o.nav_notes || []).join("\n"))}">(تخمینی؛ پرتفوی ${esc(o.nav_period || "")}: ${num(o.price_to_nav, 2)})</span>`
            : `P/NAV ${num(o.price_to_nav, 2)}` + (o.nav_period ? ` <span class="hint">(${esc(o.nav_period)})</span>` : ""))
        : (o.pe_forward == null ? "—" : num(o.pe_forward, 1));
    const div = !r.dividend ? "—"
      : !r.dividend.dps ? '<span class="hint">بدون سود نقدی</span>'
        : r.dividend.stale
          ? `<span class="hint" title="مجمع سال بعد ثبت نشده؛ بازده امروز حساب نشد">${num(r.dividend.dps, 0)} — سال ${esc(r.dividend.fiscal_year_end || "")}، قدیمی</span>`
          : `${num(r.dividend.dps, 0)} <span class="hint">(${pctCell(r.dividend.yield_pct, 1)})</span>`;
    const a = (r.announcements || [])[0];
    const ann = a
      ? `<a href="${esc(safeUrl(/^https?:/i.test(a.url || "") ? a.url : `https://codal.ir${a.url || ""}`))}" target="_blank" rel="noopener">${esc((a.title || "").slice(0, 40))}${(a.title || "").length > 40 ? "…" : ""}</a>` +
        ` <span class="hint">${esc((a.published_at || "").slice(0, 10))}</span>`
      : '<span class="hint">در ۹۰ روز اخیر نبوده</span>';
    return [`<strong>${esc(r.symbol)}</strong> <span class="hint">${esc((r.name || "").slice(0, 22))}</span>`,
            num(r.price, 0), chg, r.quantity == null ? "—" : num(r.quantity, 0),
            r.value == null ? "—" : money(r.value),
            r.weight_pct == null ? "—" : `${num(r.weight_pct, 1)}٪`,
            pe, flagCell, div, ann];
  });
  $("holdingsTable").innerHTML = body.length
    ? table(["نماد", "قیمت", "تغییر روز", "تعداد", "ارزش (ریال)", "وزن",
             "P/E آینده‌نگر", "هشدار بنیادی", "سود نقدی (بازده)", "آخرین اطلاعیه‌ی کدال"], body)
    : "";
  banner("holdingsStatus", "holdingsStatusText", "is-live",
    `${num(rows.length, 0)} نماد بررسی شد` + (rep.as_of ? ` — قیمت‌ها تا ${faDay(rep.as_of, true)}` : "") + ".");
}

function renderHoldingsRisk(risk) {
  const box = $("holdingsRisk");
  if (!box) return;
  if (!risk) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  if (!risk.available) {
    tiles("holdingsRiskKpis", []);
    $("holdingsRiskNote").textContent = risk.reason || "سابقه‌ی کافی برای محاسبه‌ی ریسک نیست.";
    return;
  }
  const vol = risk.volatility || {};
  const v = risk.var || {};
  const dd = risk.drawdown || {};
  const b = risk.beta || {};
  const run = risk.worst_run || {};
  const win = risk.window || {};
  tiles("holdingsRiskKpis", [
    { k: "نوسان سالانه", v: vol.annualised_pct == null ? "—" : `${num(vol.annualised_pct, 0)}٪`,
      sub: vol.undiversified_pct == null ? "" : `اگر همه هم‌جهت بودند ${num(vol.undiversified_pct, 0)}٪` },
    { k: `بتا نسبت به ${esc(risk.benchmark || "شاخص کل")}`, v: b.beta == null ? "—" : num(b.beta, 2),
      sub: "۱ یعنی هم‌اندازه‌ی بازار" },
    { k: "زیان یک روز بد (۹۵٪)", v: v.historical_rials == null ? "—" : `${money(v.historical_rials)} ریال`,
      sub: v.historical_pct == null ? ""
        : `${num(Math.abs(v.historical_pct), 1)}٪ سبد؛ بدترین روز ${num(Math.abs(v.worst_day_pct), 1)}٪ — دامنه‌ی نوسان سقف این عدد است` },
    // The loss that cannot be left in one day: a share's one-day figure is
    // the band itself, its worst five sessions were 4.6 times that (the
    // median of the 150 most-traded shares, 2026-11-23).
    { k: `بدترین ${num(run.sessions || 5, 0)} جلسه‌ی پیاپی`,
      v: run.worst_pct == null ? "—" : `${num(Math.abs(run.worst_pct), 1)}٪`,
      sub: run.worst_rials == null ? "" : `${money(run.worst_rials)} ریال` +
        (run.to_date ? ` — تا ${faDay(run.to_date, true)}` : "") },
    // "Max drawdown" of WHAT window: it read -13% beside the allocation
    // card's -41% for the same mix, with nothing saying it was 289 sessions.
    { k: win.sessions ? `بیشترین افت در ${num(win.sessions, 0)} جلسه‌ی اخیر` : "بیشترین افت",
      v: dd.max_drawdown_pct == null ? "—" : `${num(Math.abs(dd.max_drawdown_pct), 0)}٪`,
      sub: (win.from ? `از ${faDay(win.from, true)}؛ ` : "") + (dd.current_pct == null ? ""
        : (dd.current_pct < -0.5 ? `اکنون ${num(Math.abs(dd.current_pct), 0)}٪ زیر قله` : "اکنون نزدیک قله")) },
  ]);
  const notes = [`${num(risk.sessions, 0)} جلسه‌ی مشترک، ${num(risk.names_measured, 0)} نماد` +
    (risk.measured_pct != null && risk.measured_pct < 99.5
      ? ` — فقط ${num(risk.measured_pct, 0)}٪ ارزش سبد؛ این اعداد ریسک همان بخش است` : "")];
  if (risk.price_band_warning) notes.push(esc(risk.price_band_warning));
  if ((risk.proxied || []).length) {
    notes.push(`صندوق بدون سابقه‌ی روزانه، به جای آن: ${esc(risk.proxied.join("، "))}`);
  }
  if (risk.average_correlation != null) {
    notes.push(`میانگین همبستگی دوبه‌دو ${num(risk.average_correlation, 2)}`);
  }
  if ((risk.names_dropped || []).length) {
    notes.push(`بدون سابقه‌ی کافی و بیرون از این اعداد: ${esc(risk.names_dropped.join("، "))}`);
  }
  $("holdingsRiskNote").innerHTML = notes.join(" · ") + ".";
}

async function loadHoldings(force = false) {
  const text = holdingsText();
  if (!text.trim()) {
    $("holdingsEditor").classList.remove("hidden");
    $("holdingsKpis").innerHTML = "";
    $("holdingsWarnings").innerHTML = "";
    $("holdingsTable").innerHTML = "";
    renderHoldingsRisk(null);
    banner("holdingsStatus", "holdingsStatusText", "",
      "فهرستی ثبت نشده است. نمادهایی را که دارید بنویسید و «ذخیره و بررسی» را بزنید.");
    return;
  }
  banner("holdingsStatus", "holdingsStatusText", "is-idle", "در حال بررسی…");
  try {
    // POST: the list is the reader's own book, and a query string would land
    // in the server's request log line by line.
    renderHoldings(await getJSON("/api/holdings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ list: text, refresh: force }),
    }));
  } catch (err) {
    // Nothing of the previous answer stays under the error: it was for the
    // list as it was, and may not be the list now.
    $("holdingsKpis").innerHTML = "";
    $("holdingsWarnings").innerHTML = "";
    $("holdingsTable").innerHTML = "";
    renderHoldingsRisk(null);
    banner("holdingsStatus", "holdingsStatusText", "is-error",
      `<strong>بررسی نشد.</strong> ${esc(err.message)}`);
  }
}

on("holdingsEditBtn", "click", () => {
  $("holdingsInput").value = holdingsText();
  $("holdingsEditor").classList.toggle("hidden");
});

on("holdingsSaveBtn", "click", () => {
  try { localStorage.setItem(HOLDINGS_KEY, $("holdingsInput").value || ""); } catch (e) { /* private mode */ }
  $("holdingsEditor").classList.add("hidden");
  loadHoldings(true);
});

on("holdingsRefreshBtn", "click", () => loadHoldings(true));

/* ---------- is the market cheap or dear? (market_valuation.py) ---------- */

function valuationWord(pct) {
  if (pct == null) return "—";
  if (pct < 25) return "ارزان‌تر از معمول";
  if (pct > 75) return "گران‌تر از معمول";
  return "در محدوده‌ی معمول";
}

// Industry and market cheapness against a 3-4 year nominal history: in
// 40-80% inflation trailing P/E drifts down by itself, and E33 found buying
// into "cheap" industries did not pay -- so no green "go" colour on it.
function valuationCell(pct) {
  const word = valuationWord(pct);
  if (pct == null) return word;
  return pct < 25 ? `<span class="hint">${word}</span>`
    : pct > 75 ? `<span class="down">${word}</span>`
      : `<span class="hint">${word}</span>`;
}

function renderValuationSectors(rows) {
  const box = $("valuationSectorsBox");
  if (!box) return;
  box.classList.toggle("hidden", !rows.length);
  $("valuationSectors").innerHTML = rows.length
    ? table(["صنعت", "P/E امروز", "میانه‌ی خودش", "صدک", "در برابر خودش", "شرکت‌ها"],
        rows.map(s => [esc(s.sector), num(s.pe, 1), num(s.median_pe, 1),
                       num(s.percentile, 0), valuationCell(s.percentile),
                       num(s.companies, 0)]))
    : "";
  if (rows.length) capRows("valuationSectors", 8);
}

function renderValuation(rep) {
  state.valuation = rep;              // the allocation card quotes its bill rate
  if (state.allocation) renderAllocation(state.allocation);
  if (!rep.available) {
    banner("valuationStatus", "valuationStatusText", "", esc(rep.reason || "در دسترس نیست"));
    $("valuationKpis").innerHTML = "";
    $("valuationChart").innerHTML = "";
    renderValuationSectors([]);
    return;
  }
  const now = rep.now || {};
  // The level comparable with history is the LINKED one (constant
  // composition); the raw aggregate shifts whenever the set of companies
  // with a usable profit changes (489 -> 423 between 10-10 and 11-08).
  const pe = now.pe;
  tiles("valuationKpis", [
    { k: "P/E کل بازار", v: num(pe, 1),
      sub: `${num(now.companies, 0)} شرکت — ${now.date ? faDay(now.date, true) : ""}` },
    { k: "صدک در تاریخچه", v: `${num(rep.percentile, 0)}`,
      sub: `${valuationWord(rep.percentile)} (از ${faDay(rep.since, true)})` },
    { k: "میانه‌ی تاریخی P/E", v: num(rep.median_pe, 1),
      sub: `۶۰٪ ماه‌ها بین ${num(rep.p20_pe, 1)} و ${num(rep.p80_pe, 1)}` },
    { k: "بازده سود بازار / اخزا", v: `${num(now.earnings_yield_pct, 1)}٪`,
      sub: `اخزا ${num(rep.bill_yield_pct, 1)}٪ — فاصله ${signed(rep.yield_gap_pct, 1, "")} واحد` },
  ]);
  const data = (rep.series || []).map(p => ({
    date: p.date, pe: p.pe,
    median: rep.median_pe, p20: rep.p20_pe, p80: rep.p80_pe,
  }));
  lineChart($("valuationChart"), {
    data, xKey: "date", height: 220,
    series: [
      { key: "pe", label: "P/E بازار" },
      { key: "median", label: "میانه", color: cssVar("--text-muted"), dash: "5 5", width: 1.5, ref: true },
      { key: "p20", label: "صدک ۲۰", color: cssVar("--status-good"), dash: "2 4", width: 1, ref: true },
      { key: "p80", label: "صدک ۸۰", color: cssVar("--status-critical"), dash: "2 4", width: 1, ref: true },
    ],
    format: v => num(v, 1),
  });
  renderValuationSectors(rep.sectors || []);
  // Said from the side the number is on: "dearer than 85% of months" reads
  // straight, "cheaper than 15%" makes the reader do the subtraction.
  const cheaper = rep.percentile <= 50;
  banner("valuationStatus", "valuationStatusText", "is-live",
    `بازار با P/E ${num(pe, 1)} ${valuationWord(rep.percentile)} است: ` +
    `${cheaper ? "ارزان‌تر" : "گران‌تر"} از ` +
    `${num(cheaper ? 100 - rep.percentile : rep.percentile, 0)}٪ ماه‌های ` +
    `${faDay(rep.since, true)} تا امروز. ` +
    "زمینه‌ای برای تصمیم تخصیص، نه سیگنال آزموده‌شده." +
    (rep.linked ? " تاریخچه با ترکیب ثابت ساخته شده: هر ماه با ماه قبل فقط روی شرکت‌های " +
      "مشترک مقایسه شده تا ورود و خروج شرکت‌ها از شمارش، عدد را جابه‌جا نکند." : "") +
    // Coverage is part of the number: a reader comparing 423 companies today
    // with 489 last month should see it, not infer it.
    (now.left_out != null
      ? ` <span class="hint">حساب‌شده روی ${num(now.companies, 0)} شرکت؛ ` +
        `${num(now.left_out, 0)} شرکت بدون سود ۱۲ ماهه‌ی قابل اتکا` +
        (now.halted != null ? ` و ${num(now.halted, 0)} نماد متوقف` : "") +
        " کنار گذاشته شدند.</span>"
      : ""));
}

async function loadValuation() {
  try {
    renderValuation(await getJSON("/api/valuation"));
  } catch (err) {
    $("valuationKpis").innerHTML = "";
    $("valuationChart").innerHTML = "";
    renderValuationSectors([]);
    banner("valuationStatus", "valuationStatusText", "is-error",
      `<strong>بارگذاری نشد.</strong> ${esc(err.message)}`);
  }
}

/* ---------- tabs ----------
   Four tabs instead of one long page (2026-11-05). Each tab's data is
   requested the first time the tab is opened, so the summary is on screen
   in seconds instead of waiting behind the whole-market scan. */

const TAB_LOADERS = {
  // Live first in the summary too: the "today" card's market line reads it.
  summary: [() => loadLive(), () => loadAllocation(), () => loadOpportunities(),
            () => loadMarketflow(), () => loadAnnouncements(), () => loadCalendar(),
            () => loadHoldings(), () => loadValuation()],
  stocks: [async () => { await loadScreen(); await loadPortfolio(); }],
  live: [() => loadLive()],
  // The guide is static HTML: nothing to fetch.
  guide: [],
  analysis: [() => loadRegime(), () => loadIndices(), () => loadSectors(),
             () => loadFundamentals(), () => loadMoneyflow(),
             () => loadIntelligence()],
};
const loadedTabs = new Set();

function loadTab(name, force = false) {
  if (loadedTabs.has(name) && !force) return Promise.resolve();
  loadedTabs.add(name);
  return Promise.allSettled((TAB_LOADERS[name] || []).map(run => run()));
}

// The heavy tabs (a full-market scan is a minute or more) are loaded in the
// background, one tab at a time, once the tab on screen has its data -- so
// opening them later is instant. The page requests exactly what opening
// the tab would, with the same settings, so it lands in the same server
// cache entry. A tab opened mid-prefetch is not requested twice.
const PREFETCH_ORDER = ["summary", "stocks", "analysis", "live"];

async function prefetchTabs(first) {
  await loadTab(first);
  for (const name of PREFETCH_ORDER) {
    if (loadedTabs.has(name)) continue;
    await loadTab(name);
  }
}

function showTab(name) {
  if (!TAB_LOADERS[name]) name = "summary";
  document.querySelectorAll(".tab").forEach(b => {
    b.classList.toggle("is-active", b.dataset.tab === name);
    b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach(p =>
    p.classList.toggle("is-active", p.dataset.tab === name));
  try { localStorage.setItem("sirat-tab", name); } catch (e) { /* private mode */ }
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  loadTab(name);
  // Charts drawn while their tab was hidden measured a zero width.
  hideTip();
  rerender();
}

// Re-run what has been opened so far; tabs never opened stay unloaded.
async function loadAll() {
  for (const name of [...loadedTabs]) loadTab(name, true);
}

/* ---------- long tables: first rows, then "show all" ---------- */

function capRows(containerId, keep) {
  const node = $(containerId);
  if (!node) return;
  const rows = node.querySelectorAll("tbody tr");
  node.querySelectorAll(".cap-toggle").forEach(b => b.remove());
  if (rows.length <= keep) return;
  rows.forEach((tr, i) => tr.classList.toggle("is-capped", i >= keep));
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "cap-toggle";
  btn.textContent = `نمایش همه (${rows.length})`;
  btn.addEventListener("click", () => {
    const open = btn.dataset.open === "1";
    rows.forEach((tr, i) => tr.classList.toggle("is-capped", open && i >= keep));
    btn.dataset.open = open ? "0" : "1";
    btn.textContent = open ? `نمایش همه (${rows.length})` : "نمایش کمتر";
  });
  node.appendChild(btn);
}

/* ---------- "what do I do today" ----------
   The picked profile in rials, each part with the cheapest instrument that
   fills it today. Facts only: weights from the tested allocation (E27/E28),
   instruments from fund NAV discounts and treasury-bill yields. */

// The fund to name for a purchase of `order` rials, by the rule of
// opportunities.pick_fund (change both together): not a suspect NAV, not last
// traded at its upper limit (a buy queue -- the band, not the market, made
// that discount), and with a day's traded value that carries the order. For
// the equity sleeve a fund that tracks the broad index comes first; another
// is offered only when no tracker can be bought, and is said not to be one.
// Until 2026-11-23 this was "the first fund that is not suspect": the
// equal-weight fund, locked at +3.00%, could be named for the TEDPIX sleeve.
//
// An order too large for any right fund's day names the most traded of them
// with `split` -- the same fund over several sessions is the same exposure,
// a different index is not. `fallback` (not a tracker) is the last resort.
function pickFund(items, order, needBroad, share) {
  const skipped = [], able = [], thin = [];
  const value = f => Number(f.traded_value) || 0;
  (items || []).forEach(f => {
    if (f.suspect) return;
    if (f.locked === "buy") {
      skipped.push({ symbol: f.symbol, broad: !!f.broad, why: "در سقف دامنه‌ی نوسان — صف خرید" });
    } else if (order > 0 && value(f) * share < order) {
      skipped.push({ symbol: f.symbol, broad: !!f.broad, why: "ارزش معاملات روزش برای این مبلغ کم است" });
      thin.push(f);
    } else {
      able.push(f);
    }
  });
  // Only what was passed over on the way to THIS fund is worth a line.
  const answer = (fund, fallback, split, broadOnly) => ({
    fund, fallback, split,
    skipped: skipped.filter(s => s.symbol !== (fund || {}).symbol && (s.broad || !broadOnly)),
  });
  const byValue = (a, b) => value(b) - value(a);
  const right = needBroad ? able.filter(f => f.broad) : able;
  if (right.length) return answer(right[0], false, false, needBroad);
  const rightThin = thin.filter(f => f.broad || !needBroad).sort(byValue);
  if (rightThin.length) return answer(rightThin[0], false, true, needBroad);
  if (needBroad && (able.length || thin.length)) {
    return able.length ? answer(able[0], true, false, false)
      : answer(thin.slice().sort(byValue)[0], true, true, false);
  }
  return answer(null, false, false, false);
}

// The whole book's instruction as numbers. `mine` is sleevesOf(); `weights`
// is {sleeve: target %}. One sleeve past the band sends every sleeve back to
// target (the allocation's rule) -- but a sale is capped at what can be sold
// in that sleeve today, cash there is spent before anything is sold, and the
// purchases are scaled down to the money those sales raise. Before
// 2026-11-23 a book with a halted name and one in a sell queue was told to
// sell 14.7bn rials (5.1bn of it dealable) and to buy the full 14.7bn.
function todayOrders(mine, weights, band) {
  const base = mine.total;
  const keys = Object.keys(weights);
  const small = base * 0.005;
  const nowPct = k => base ? (mine.sleeves[k] || 0) / base * 100 : 0;
  const rebalance = keys.some(k => Math.abs(nowPct(k) - weights[k]) > band);
  const orders = {};
  let raised = 0, wanted = 0;
  keys.forEach(k => {
    const gap = base * weights[k] / 100 - (mine.sleeves[k] || 0);
    const o = orders[k] = { now: nowPct(k), gap, act: "hold", amount: 0, short: 0, cash: 0 };
    if (!rebalance) return;
    if (gap < 0) {
      o.act = "sell";
      o.amount = Math.min(-gap, mine.sellable[k] || 0);
      o.short = -gap - o.amount;
      o.cash = Math.min(o.amount, mine.cash[k] || 0);
      raised += o.amount;
    } else {
      o.act = "buy";
      o.amount = gap;
      wanted += gap;
    }
  });
  const scale = wanted > 0 ? Math.min(1, raised / wanted) : 1;
  keys.forEach(k => {
    const o = orders[k];
    if (o.act === "buy") {
      o.short = o.amount * (1 - scale);
      o.amount = o.amount * scale;
    }
    // Too small to trade: "nothing to do", unless what is missing is not
    // small -- then the instruction is withheld and the card says why.
    if (o.act !== "hold" && o.amount < small) {
      o.act = o.short >= small ? `${o.act}-blocked` : "none";
    }
  });
  return { rebalance, orders, raised, wanted, scale };
}

// About how many units a rial amount is, where a price is known.
function todayUnits(amount, price) {
  const n = price > 0 ? Math.floor(amount / price) : 0;
  return n > 0 ? ` <span class="hint">≈ ${num(n, 0)} واحد به قیمت ${num(price, 0)}</span>` : "";
}

function renderToday() {
  const rep = state.allocation;
  const opp = state.opportunities;
  const live = state.live;

  if (live) {
    const s = live.session || {};
    const open = s.trading === true ? "باز است" : s.trading === false ? "بسته است" : "نامعلوم";
    const c = live.counts || {};
    // The counts are one capture of the order book, and the collector runs
    // only in session, so out of session they are the last session's (one
    // recording: 57 hours old) -- dated here the way the live tab dates the
    // same numbers, with the same marker.
    const captured = live.queues?.captured_at;
    // Re-dated at every render: renderToday runs again on each tab switch and
    // profile change, and the server's age_minutes is the age at fetch time.
    const ageNow = captured
      ? (Date.now() - Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(captured) ? captured : `${captured}Z`)) / 60000
      : live.queues?.age_minutes;
    const age = ageTag(Number.isFinite(ageNow) ? ageNow : live.queues?.age_minutes);
    // The session's state only: queue counts here invited acting on the
    // day's mood, which the plan below does not depend on (the review
    // panel, 2026-11-11). They stay on the live tab.
    $("todayMarket").innerHTML = `بازار <strong>${open}</strong>` + (captured && age ? ` — ${age}` : "");
  }

  if (!rep) {
    $("todayPlan").innerHTML = state.allocationFailed
      ? '<div class="empty">تخصیص محاسبه نشد — کارت «چقدر در سهام، طلا و درآمد ثابت؟» را ببینید.</div>'
      : "";
    $("todayHint").innerHTML = "";
    return;
  }

  const key = $("todayProfile").value || "balanced";
  const profile = rep.profiles.find(p => p.key === key) || rep.profiles[0];
  const labels = rep.sleeve_labels || {};
  const funds = (opp && opp.funds) || {};
  const bills = ((opp && opp.bills) || {}).bills || [];

  // The fund to name for a purchase of `order` rials: one that can be bought
  // today and, for the equity sleeve, one that tracks the broad index.
  const share = (opp && opp.limits && opp.limits.max_order_share_of_day) || 0.1;
  const pickFor = (sleeve, order) => pickFund(funds[sleeve], order, sleeve === "equity", share);

  // What the empty cell means: the read failed, or finished with nothing for
  // this sleeve (TSETMC unreachable answers 200 with empty lists), or is still
  // in flight. "Reading…" for the first two would never stop saying it.
  const pending = state.opportunitiesFailed ? "قیمت‌ها خوانده نشد"
    : opp ? "قیمتی برای این بخش خوانده نشد" : "در حال خواندن قیمت‌ها…";

  // The user's own book, when they entered quantities in «سبد من»: the plan
  // is a rebalance of THAT, not of the default capital. As first written the
  // card sized a 1-billion-rial example while the holdings card right below
  // held 20 billion with gold at 100% of a 30% target (found 2026-11-11).
  const book = state.holdingsBook;
  const mine = book && (book.rows || []).some(r => r.quantity) ? sleevesOf(book.rows || []) : null;
  const own = mine && mine.total > 0 ? mine.sleeves : null;
  const base = own ? mine.total : null;
  // A position without a price is left out of the total, and a line that
  // could not be read (an unknown symbol, a quantity in two pieces) may be a
  // position too: every weight and amount computed without them is wrong by
  // their value, so no instruction is given until they are fixed.
  const blocked = mine && (mine.unpriced.length || mine.errors.length)
    ? mine.unpriced.concat(mine.errors) : null;
  // The allocation's own rule: back to target when a sleeve drifts more than
  // this many points (allocation.DRIFT_BAND_PCT), or every ~60 sessions. The
  // card switched to "buy/sell X" at 2% of the book -- five times as often.
  const band = (rep.rebalance && rep.rebalance.drift_band_pct) || 10;

  // The sleeves to show: the profile's, and any the reader holds that the
  // profile gives 0% -- a held sleeve the card skipped was never sold, and
  // the others' "buy" had nothing to pay for it (the full test, 2026-11-12).
  const weights = Object.assign({}, profile.weights);
  if (own) Object.keys(own).forEach(k => { if (own[k] > 0 && !(k in weights)) weights[k] = 0; });
  const shown = Object.entries(weights).filter(([k, w]) => w > 0 || (own && own[k] > 0));

  // One decision for the whole book, then each sleeve's part of it. Decided
  // per sleeve, one could read "in range, nothing to do" beside another's
  // "buy 5 billion" with no sale to fund it (the full test). The rule: when
  // any sleeve is past the band, every sleeve goes back to target -- the
  // sales then pay for the purchases, since the base is the sleeves' total.
  //
  // The sales are the ones that can HAPPEN (todayOrders, 2026-11-23): a
  // halted holding or one locked at its lower limit is not sold, and the
  // purchases shrink to what the possible sales raise.
  const nowPct = (k) => base ? (own[k] || 0) / base * 100 : 0;
  const plan = own && !blocked ? todayOrders(mine, weights, band) : null;
  // What a sale in a sleeve can come from: the reader's holdings there that
  // can be sold today (sleevesOf), with a price for a unit count.
  const heldIn = (sleeve) => (mine && mine.saleable[sleeve]) || [];

  const items = shown.map(([sleeve, w]) => {
    const o = plan ? plan.orders[sleeve] : null;
    // The rials this sleeve is to buy -- the order on the reader's own book,
    // the example amount without one. The fund is chosen FOR that amount.
    const order = o ? (o.act === "buy" ? o.amount : 0)
      : Number((profile.amounts || {})[sleeve]) || 0;
    let pick = "", why = "", price = 0;
    if (sleeve === "gold" || sleeve === "equity") {
      const chosen = pickFor(sleeve, order);
      const f = chosen.fund;
      if (f) {
        pick = `<strong>${esc(f.symbol)}</strong>`;
        price = Number(f.close) || 0;
        why = `${signed(f.discount_pct, 1)} نسبت به NAV` +
          (f.price_date ? `، قیمت ${faDay(f.price_date)}` : "") +
          (sleeve !== "equity" ? " — ارزان‌ترین صندوق پرمعامله‌ی این بخش که امروز می‌شود خرید"
            : chosen.fallback
              ? ` — <strong>ردیاب شاخص کل نیست</strong>${f.tracks ? ` (${esc(f.tracks)})` : ""}؛ صندوق ردیابی که امروز بشود خرید پیدا نشد`
              : " — ارزان‌ترین صندوقِ ردیاب شاخص کل که امروز می‌شود خرید");
        if (chosen.split) {
          why += ` <strong>این مبلغ بیش از ${num(share * 100, 0)}٪ ارزش معاملات یک روزِ آن است (${money(f.traded_value)} ریال) — خرید را در چند روز پخش کنید.</strong>`;
        }
      } else if ((funds[sleeve] || []).length) {
        why = "هیچ صندوقی از این بخش امروز قابل خرید نیست";
      }
      if (chosen.skipped.length) {
        why += ` <span class="hint">کنار گذاشته شد: ${chosen.skipped.map(s => `${esc(s.symbol)} (${esc(s.why)})`).join("، ")}</span>`;
      }
    } else if (sleeve === "fixed") {
      // The server's pick (opportunities.parking_bill): a fresh price, 90+
      // days, the most traded -- not the top of a list sorted by yield,
      // whose head was often days from maturity or priced on a stale close.
      const b = (opp && opp.bills && opp.bills.parking) || null;
      const f = pickFor("fixed", order).fund;
      if (b) {
        pick = `<strong>${esc(b.symbol)}</strong> (اخزا)`;
        price = Number(b.price) || 0;
        why = `${num(b.ytm_pct, 1)}٪ سالانه تا ${esc(b.maturity_jalali || b.maturity)}، اگر تا سررسید نگه دارید — ثابت به ریال، نه واقعی`;
      } else if (f) {
        pick = `<strong>${esc(f.symbol)}</strong>`;
        price = Number(f.close) || 0;
        why = "صندوق درآمد ثابت پرمعامله";
      }
    }
    let amount = `${num(Math.round(order), 0)} <span class="pct">ریال</span>` + todayUnits(order, price) +
      ` <span class="hint">(مثال برای سرمایه‌ی ${money(rep.equity)} ریال؛ دارایی خودتان را در «سبد من» وارد کنید)</span>`;
    // What the instrument line says: an order only where this sleeve buys.
    let line = pick ? `بخرید: ${pick}` : `<span class="hint">${pending}</span>`;
    let meter = "";
    if (own) {
      const now = nowPct(sleeve);
      const small = base * 0.005;
      const act = blocked ? "blocked" : o.act;
      // What actually has to be SOLD: the sleeve's cash is spent first.
      const toSell = o ? o.amount - o.cash : 0;
      const held = heldIn(sleeve);
      const cant = (mine.locked[sleeve] || []).map(x => `${esc(x.symbol)} (${esc(x.why)})`).join("، ");
      // Functions, not strings: only the chosen one is built (`blocked` is
      // null on every other path).
      // Now against target, drawn (v3 design, 2026-11-23): the bar is
      // today's weight, the tick the target; the order below it is a pill.
      // It used to be one 24-px sentence that wrapped over three lines.
      const clamp = (x) => Math.max(0, Math.min(100, Number(x) || 0));
      meter = `<div class="today-meter" role="img" aria-label="الان ${num(now, 0)}٪، هدف ${num(w, 0)}٪">` +
        `<span class="now" style="width:${clamp(now)}%"></span>` +
        `<span class="goal" style="inset-inline-start:${clamp(w)}%"></span></div>` +
        `<div class="today-nums"><span>الان <b>${num(now, 0)}٪</b></span><span>هدف <b>${num(w, 0)}٪</b></span></div>`;
      const pill = act === "buy" || act === "sell";
      amount = (pill ? `<span class="today-act ${act}">` : "") + {
        blocked: () => `<span class="hint">ارزش ${blocked.map(esc).join("، ")} معلوم نیست؛ دستوری داده نمی‌شود</span>`,
        hold: () => `<strong>در محدوده‌ی هدف</strong> <span class="hint">(همه‌ی بخش‌ها درون ±${num(band, 0)} واحد) — کاری لازم نیست</span>`,
        none: () => `<strong>بدون تغییر</strong> <span class="hint">(کمتر از نیم درصد سبد)</span>`,
        buy: () => `<strong>بخرید ${money(o.amount)}</strong> <span class="pct">ریال</span>`,
        sell: () => toSell >= small
          ? `<strong>بفروشید ${money(toSell)}</strong> <span class="pct">ریال</span>`
          : `<strong>از نقد خرج کنید ${money(o.cash)}</strong> <span class="pct">ریال</span>`,
        // The instruction is withheld, not shrunk to nothing: the reader is
        // told what is missing and that today it cannot be done.
        "buy-blocked": () => `<strong>خریدی داده نمی‌شود</strong> <span class="hint">(${money(o.short)} ریال کم دارد، ولی فروشی که پولش را بدهد امروز شدنی نیست)</span>`,
        "sell-blocked": () => `<strong>فروشی داده نمی‌شود</strong> <span class="hint">(${money(o.short)} ریال بیش از هدف است، ولی هیچ‌کدام امروز قابل فروش نیست)</span>`,
      }[act]() + (pill ? "</span>" : "");
      if (act === "buy") {
        amount += todayUnits(o.amount, price) + (o.short >= small
          ? ` <span class="hint">از ${money(o.amount + o.short)} ریالِ لازم — بقیه وقتی که فروش‌های قفل‌شده شدنی شود</span>` : "");
      }
      if (act === "sell") {
        amount += (toSell >= small && o.cash >= small ? ` <span class="hint">و ${money(o.cash)} ریال از نقد</span>` : "") +
          (o.short >= small ? ` <span class="hint">از ${money(o.amount + o.short)} ریالِ لازم — بقیه امروز قابل فروش نیست</span>` : "");
      }
      line = act === "buy"
        ? (pick ? `بخرید: ${pick}` : `<span class="hint">${pending}</span>`)
        : act === "sell"
          ? (toSell >= small && held.length
            ? `از دارایی قابل‌فروش خودتان در این بخش: <strong>${held.map(x => esc(x.symbol)).join("، ")}</strong>` +
              (held.length === 1 ? todayUnits(toSell, held[0].price) : "")
            : "")
          : (pick ? `<span class="hint">ابزار این بخش، اگر روزی خرید لازم شد: ${pick}</span>` : "");
      if (act === "sell" || act === "sell-blocked" || act === "buy-blocked") why = "";
      // The holdings that cannot be dealt, by name and reason, on every path.
      if (cant) why = `<span class="hint">⚠ امروز قابل فروش نیست: ${cant}</span> ${why}`;
      // Cash is counted in this sleeve but earns nothing while it waits.
      const idle = (mine.cash[sleeve] || 0) - (o ? o.cash : 0);
      if (idle >= small && act !== "blocked") {
        why = `<span class="hint">${money(idle)} ریال از این بخش نقد است و تا خرید نشده بازدهی ندارد.</span> ${why}`;
      }
    }
    return `<div class="today-item">
      <div class="pct">${num(w, 0)}٪ ${esc(labels[sleeve] || sleeve)}</div>
      ${meter}
      <div class="amt">${amount}</div>
      <div class="buy">${line}</div>
      <div class="why">${why}</div>
    </div>`;
  });

  $("todayPlan").innerHTML = items.join("");

  const h = rep.history && rep.history.profiles ? rep.history.profiles[key] : null;

  // The day the amounts above are priced on, and every line priced earlier:
  // the card gave rial orders with no date, and a name halted for two months
  // sat in them at its frozen close (2026-11-23).
  const stale = (own && book.stale) || [];
  const dated = own && book.as_of
    ? `<strong>قیمت‌های سبد شما تا ${faDay(book.as_of, true)}</strong>` +
      (stale.length
        ? ` — قیمتِ قدیمی‌تر: ${stale.map(s => `${esc(s.symbol)} (${faDay(s.as_of, true)})`).join("، ")}`
        : "") + ". "
    : "";

  // What dealing costs, said beside the amounts: none of them deducts it.
  const costs = rep.costs;
  const costText = costs
    ? `کارمزد از این مبالغ کسر نشده است: خرید و فروش واحد صندوق روی‌هم حدود ${num(costs.fund_round_trip_pct, 2)}٪ ` +
      `(سهم ${num(costs.share_round_trip_pct, 2)}٪ با مالیات فروش). `
    : "";

  // A bill sold before maturity gives up the yield it was bought for, and
  // the profiles' edge was measured against a 20% fund, not today's bills
  // (the allocation card's own banner). Said here whenever the card would
  // sell fixed income or the reader holds bills: under the default profile
  // it printed "sell" for a held bill with neither sentence (2026-11-23).
  const fixed = plan ? plan.orders.fixed : null;
  const sellsFixed = !!fixed && (fixed.act === "sell" || fixed.act === "sell-blocked") &&
    (fixed.amount - fixed.cash >= base * 0.005 || fixed.short >= base * 0.005);
  const holdsBills = !!own && (book.rows || []).some(r => r.bill && !r.error && r.quantity);
  const billYield = (state.valuation && state.valuation.bill_yield_pct) ||
    (opp && opp.bills && opp.bills.parking && opp.bills.parking.ytm_pct) || null;
  const billText = sellsFixed || holdsBills
    ? `<strong>${sellsFixed ? "پیش از فروش درآمد ثابت:" : "درباره‌ی اخزایی که دارید:"}</strong> ` +
      (holdsBills ? "اخزایی که پیش از سررسید فروخته شود بازده ثابتش را از دست می‌دهد و به قیمت روز می‌رود. " : "") +
      `در بازآزمایی ۱۵ ساله با درآمد ثابت واقعی (E36، ۱۳۹۰ تا ۱۴۰۵) برتری سهام و طلا بر درآمد ثابت از آستانه‌ی آماری نگذشت؛ اوراق خزانه امروز ` +
      `${billYield ? `حدود ${num(billYield, 0)}٪` : "بازده بالایی"} می‌دهد. `
    : "";

  // Outside the three sleeves, and why that matters for a leveraged fund.
  const kindOf = (symbol) => (((book && book.rows) || []).find(r => r.symbol === symbol) || {}).fund_kind;
  const leveraged = mine ? mine.outside.filter(s => kindOf(s) === "leveraged") : [];

  $("todayHint").innerHTML = dated + (h
    ? `این ترکیب از ${esc(rep.history.from)} سالانه ${num(h.cagr_pct, 0)}٪ داده (اسمی؛ با تورم ۴۰ تا ۹۰ درصدی لزوماً سود واقعی نیست) و ` +
      `بدترین افتش ${num(h.max_drawdown_pct, 0)}٪ بوده است. <strong>کسی که در افت می‌فروشد، این بازده را نمی‌گیرد.</strong> `
    : "") +
    `هر حدود سه ماه، یا وقتی سهم یک بخش بیش از ${num(band, 0)} واحد از هدف دور شد، به همین وزن‌ها برگردانید؛ ` +
    `بین این دو، کاری لازم نیست. ` + costText + billText +
    (mine && mine.outside.length
      ? `<span class="hint">${mine.outside.map(esc).join("، ")} در هیچ‌کدام از سه بخش نیست و در درصدها و مبالغ بالا حساب نشد` +
        (leveraged.length
          ? `؛ ${leveraged.map(esc).join("، ")} صندوق اهرمی است و سهام را با اهرم نگه می‌دارد، پس سهم واقعی سهام شما از عدد بالا بیشتر است`
          : "") + `. </span>`
      : "") +
    `<span class="hint">${esc(profile.evidence_text || "")}</span>`;
}

// A treasury bill's price and yield from the bills the opportunities card
// read (bills are not in the instrument list the holdings card reads).
function billQuote(symbol) {
  const bills = ((state.opportunities || {}).bills || {}).bills || [];
  const key = String(symbol || "").replace(/\s/g, "");
  return bills.find(b => String(b.symbol || "").replace(/\s/g, "") === key) || null;
}

// Which allocation sleeve each holding counts toward, and what is left out.
// The sleeve comes from the server (holdings.sleeve_of, the rule the
// opportunities card files funds by): the name match this replaced read 33 of
// 40 fixed-income funds ending «-د» as equity and «زر» equity funds as gold
// (the panel review, 2026-11-11). A bill is priced from the bills read here.
//
// And what of each sleeve can be turned into money today (2026-11-23): a
// halted holding, or one whose last trade is at its lower limit (a sell
// queue), is still the reader's exposure and stays in the weights, but it is
// not `sellable` -- the server says which and why (holdings.lock_states,
// `can_sell` / `why_not`). Cash is spendable without a sale, so it is counted
// apart; `saleable` lists the holdings a sale can actually come from.
function sleevesOf(rows) {
  const out = {}, sellable = {}, cash = {}, locked = {}, saleable = {};
  const unpriced = [], outside = [], errors = [];
  let total = 0;
  rows.forEach(r => {
    if (r.error) { errors.push(r.symbol); return; }
    let value = Number(r.value) || 0;
    let price = Number(r.price) || 0;
    if (r.bill && !value) {
      const b = billQuote(r.symbol);
      value = b && r.quantity ? b.price * r.quantity : 0;
      price = b ? Number(b.price) || 0 : 0;
    }
    if (!value) {
      if (r.quantity) unpriced.push(r.symbol);
      return;
    }
    const sleeve = r.sleeve !== undefined ? r.sleeve : (r.equity ? "equity" : null);
    // Outside the three sleeves (a leveraged or mixed fund): not in the base
    // the plan is sized on, so the plan's sales pay for its purchases.
    if (!sleeve) { outside.push(r.symbol); return; }
    total += value;
    out[sleeve] = (out[sleeve] || 0) + value;
    if (r.can_sell === false) {
      (locked[sleeve] = locked[sleeve] || []).push(
        { symbol: r.symbol, why: r.why_not || "قابل فروش نیست", value });
      return;
    }
    sellable[sleeve] = (sellable[sleeve] || 0) + value;
    if (r.cash) cash[sleeve] = (cash[sleeve] || 0) + value;
    else (saleable[sleeve] = saleable[sleeve] || []).push({ symbol: r.symbol, value, price });
  });
  return { sleeves: out, sellable, cash, locked, saleable, total, unpriced, outside, errors };
}

// Repaints whatever each slot holds, on every tab switch, resize, theme toggle
// and evidence-section open. That is why each loader empties its slot when it
// fails: a slot still holding the last good payload is painted straight back
// over the failure's banner, marked live.
function rerender() {
  if (state.allocation) renderAllocation(state.allocation);
  if (state.opportunities) renderOpportunities(state.opportunities);
  if (state.marketflow) renderMarketflow(state.marketflow);
  if (state.regime) renderRegime(state.regime);
  if (state.indices) renderIndices(state.indices);
  if (state.sectors) renderSectors(state.sectors);
  if (state.fundamentals) renderFundamentals(state.fundamentals);
  if (state.codal) renderRoutes(state.codal.routes, state.codal.coverage);
  if (state.flow) renderMoneyflow(state.flow);
  if (state.technical) renderTechnical(state.technical);
  if (state.topTechnical) renderTopTechnical(state.topTechnical);
  if (state.inflow) renderInflow(state.inflow);
  if (state.smartMoney) renderSmartMoney(state.smartMoney);
  if (state.gold) renderGold(state.gold);
  if (state.intel) renderIntelligence(state.intel);
  if (state.screen) renderScreen(state.screen);
  if (state.portfolio) renderPortfolio(state.portfolio);
  for (const [id, e] of Object.entries(importErrors)) banner(id, e.textId, "is-error", e.html);
}

/* ---------- import actions ---------- */

// A failure is reported in the banner of the section the button sits in. Every
// one used to go to the screen's banner, inside a section that is collapsed by
// default: with TSETMC unreachable /api/import/indices answers 502, and the
// reason landed where nobody was looking while the button simply went back to
// its label. The default is the screen's banner, which is where the universe
// and snapshot imports live.
async function withButton(btn, label, fn,
                          statusId = "screenStatus", textId = "screenStatusText") {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  delete importErrors[statusId];
  try {
    await fn();
  } catch (err) {
    const html = `<strong>${esc(original)}:</strong> ${esc(err.message)}`;
    importErrors[statusId] = { textId, html };
    banner(statusId, textId, "is-error", html);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

on("importUniverseBtn", "click", () =>
  withButton($("importUniverseBtn"), "در حال ورود…", async () => {
    await getJSON("/api/import/universe", { method: "POST" });
    await loadScreen(true);
  }));

on("importSnapshotBtn", "click", () =>
  withButton($("importSnapshotBtn"), "در حال دریافت…", async () => {
    await getJSON("/api/import/snapshot", { method: "POST" });
    await loadScreen(true);
    await loadPortfolio(true);
  }));

on("runScreenBtn", "click", () => loadScreen(true).then(() => loadPortfolio(true)));

on("importIndicesBtn", "click", () =>
  withButton($("importIndicesBtn"), "در حال دریافت…", async () => {
    await getJSON("/api/import/indices", { method: "POST" });
    await loadIndices(true);
    await loadRegime(true);
  }, "capIndexStatus", "capIndexStatusText"));

// One request per symbol over the whole universe: slow enough that firing it
// by accident deserves a confirmation.
on("importSectorsBtn", "click", () => {
  const count = (state.screen && state.screen.counts)
    ? state.screen.counts.universe : "همه‌ی";
  if (!confirm(
    `دریافت صنایع برای هر سهم یک درخواست جداگانه می‌فرستد (${count} سهم) و ` +
    `چند دقیقه طول می‌کشد. ادامه می‌دهید؟`
  )) return;
  withButton($("importSectorsBtn"), "در حال دریافت…", async () => {
    await getJSON("/api/import/sectors", { method: "POST" });
    await loadSectors(true);
  }, "sectorStatus", "sectorStatusText");
});

// Same cost shape as the sector import: one request per symbol across the
// whole universe, minutes long, so it asks first.
on("importFlowBtn", "click", () => {
  const count = (state.screen && state.screen.counts)
    ? state.screen.counts.universe : "همه‌ی";
  if (!confirm(
    `دریافت داده‌ی حقیقی/حقوقی برای هر سهم یک درخواست جداگانه می‌فرستد ` +
    `(${count} سهم) و چند دقیقه طول می‌کشد. ادامه می‌دهید؟`
  )) return;
  withButton($("importFlowBtn"), "در حال دریافت…", async () => {
    await getJSON("/api/import/flow", { method: "POST" });
    await loadMoneyflow(true);
    // The screen's combined score is tilted by flow, so it is now stale.
    await loadScreen(true);
  }, "flowStatus", "flowStatusText");
});

// Codal downloads run in the background and are paced against a rate limit,
// so this starts a bounded batch rather than blocking on a full fetch. The
// rebind flag re-probes every route first: the usual reason someone presses
// this is that they just changed their VPN.
on("importCodalBtn", "click", () =>
  withButton($("importCodalBtn"), "در حال شروع…", async () => {
    const started = await getJSON("/api/codal?rebind=1", { method: "POST" });

    if (started.cooling_off_seconds && !started.started) {
      const minutes = Math.ceil(started.cooling_off_seconds / 60);
      banner("fundStatus", "fundStatusText", "is-error",
        `<strong>کدال در حال حاضر سرعت ما را محدود کرده (HTTP 429).</strong> ` +
        `برنامه ${minutes} دقیقه صبر می‌کند و بعد خودکار ادامه می‌دهد — ` +
        `تلاش زودتر، محدودیت را تمدید می‌کند نه رفع. ` +
        `داده‌های موجود دست‌نخورده‌اند و بقیه‌ی ربات عادی کار می‌کند.`);
      return;
    }

    banner("fundStatus", "fundStatusText", "is-live",
      started.started
        ? "<strong>دریافت کدال در پس‌زمینه شروع شد.</strong> " +
          "چند دقیقه طول می‌کشد و اسکن را متوقف نمی‌کند؛ برای دیدن پیشرفت " +
          "دوباره «تحلیل» را بزنید."
        : "<strong>یک دریافت کدال از قبل در حال اجراست.</strong>");
  }, "fundStatus", "fundStatusText"));

/* ---------- live session watch ----------

   The one view here whose value expires. Everything else on this page is as
   true in an hour as it is now; this is not, and the whole of the rendering
   below is shaped by that.

   THE AGE IS RENDERED EVERYWHERE, AND NEVER AS COLOUR ALONE. The collector
   runs only during a session, so this panel opened on a Thursday is showing
   Wednesday's close and opened with a broken scheduled task could be showing
   last month. `ageTag` prints the number and adds the word "کهنه" past the
   threshold -- a reader who does not know the palette still gets told.

   AUTO-REFRESH IS OFF BY DEFAULT AND POLLS THE DATABASE, NOT THE EXCHANGE.
   /api/live issues no outbound request of its own beyond one session probe,
   so leaving this on costs three indexed reads a minute. It is still off by
   default, because a panel that quietly generates traffic forever is how a
   source starts refusing you. */

// Past this, a reading is labelled stale in words. One session is 210
// minutes, and the collector captures the book every 15 -- so an hour is
// four missed captures, which is a collector that has stopped rather than
// one that is merely between ticks.
const LIVE_STALE_MINUTES = 60;

const LIVE_POLL_MS = 60000;

let liveTimer = null;

const EVENT_FA = {
  buy_queue_formed:   "صف خرید تشکیل شد",
  buy_queue_broke:    "صف خرید شکست",
  sell_queue_formed:  "صف فروش تشکیل شد",
  sell_queue_broke:   "صف فروش شکست",
  reached_limit_up:   "به سقف دامنه رسید",
  left_limit_up:      "از سقف دامنه برگشت",
  reached_limit_down: "به کف دامنه رسید",
  left_limit_down:    "از کف دامنه برگشت",
};

// Tehran's trading clock, read off the browser's clock: Saturday to
// Wednesday, 09:00-12:35 (Iran keeps UTC+3:30 all year since 2022). Holidays
// are not known here, so the day after one may read a session early.
const TEHRAN_OFFSET_MIN = 210;
const SESSION_DAYS = new Set([6, 0, 1, 2, 3]);      // Sat..Wed (getUTCDay)

function sessionClock(nowMs = Date.now()) {
  const t = new Date(nowMs + TEHRAN_OFFSET_MIN * 60000);   // UTC fields = Tehran wall clock
  const mins = t.getUTCHours() * 60 + t.getUTCMinutes();
  const open = SESSION_DAYS.has(t.getUTCDay()) && mins >= 540 && mins <= 755;
  let back = 0;
  for (; back < 8; back++) {
    const d = new Date(t.getTime() - back * 86400000);
    if (SESSION_DAYS.has(d.getUTCDay()) && (back > 0 || mins >= 540)) break;
  }
  const day = new Date(t.getTime() - back * 86400000);
  const startTehran = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), 9, 0);
  return { open, lastStartMs: startTehran - TEHRAN_OFFSET_MIN * 60000 };
}

// Stale by the market's clock, not the wall clock. As first written anything
// over an hour old was "کهنه" in red, so outside trading hours and at
// weekends most of the panel was red while the green strip above said all
// was current (found 2026-11-11). While the market is open the hour rule
// stands; while it is shut, a capture is stale only if it predates the start
// of the last session.
function ageTag(minutes) {
  if (minutes === null || minutes === undefined) return "";
  const { open, lastStartMs } = sessionClock();
  const stale = open
    ? minutes > LIVE_STALE_MINUTES
    : Date.now() - minutes * 60000 < lastStartMs;
  const text = minutes < 90
    ? `${num(minutes, 0)} دقیقه پیش`
    : minutes < 48 * 60
      ? `${num(minutes / 60, 1)} ساعت پیش`
      : `${num(minutes / 1440, 0)} روز پیش`;
  return `<span class="age-tag${stale ? " stale" : ""}">${text}${
    stale ? " — کهنه" : open ? "" : " — بازار بسته"}</span>`;
}

async function loadLive() {
  const btn = $("refreshLiveBtn");

  if (btn) { btn.disabled = true; btn.textContent = "در حال خواندن…"; }

  let rep;

  try {
    rep = await getJSON("/api/live?top=15");
  } catch (err) {
    banner("liveStatus", "liveStatusText", "is-error",
      `<strong>رصد زنده در دسترس نیست.</strong> ${esc(err.message)}`);
    console.error(err);
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "↻ به‌روزرسانی"; }
  }

  state.live = rep;
  renderToday();

  const s = rep.session || {};
  const tick = rep.collector;
  const counts = rep.counts || {};

  // Three states, not two. An unreachable feed is not a closed market: the
  // first says nothing about the exchange, the second is a claim about it.
  let kind, headline;

  if (s.trading === true) {
    kind = "is-live";
    headline = "جلسه در حال اجراست";
  } else if (s.trading === false) {
    kind = "";
    headline = "جلسه در حال اجرا نیست";
  } else {
    kind = "is-error";
    headline = "وضعیت جلسه نامعلوم است — فید خوانده نشد";
  }

  // market_session returns its reason in English because it is a library and
  // that string goes to logs. The panel is read in Persian, so the figure is
  // restated here rather than echoed.
  let detail = (s.traded === null || s.traded === undefined)
    ? esc(s.reason || "")
    : `${num(s.traded, 0)} نماد گردش معامله دارند`;

  if (tick) {
    detail += ` · آخرین تیک جمع‌آورنده: ${esc(TICK_FA[tick.outcome] || tick.outcome)} ${
      ageTag(tick.age_minutes)}`;
  } else {
    detail += " · جمع‌آورنده هنوز هیچ تیکی ثبت نکرده است";
  }

  banner("liveStatus", "liveStatusText", kind,
         `<strong>${headline}</strong> — ${detail}`);

  const q = rep.queues || {};

  tiles("liveKpis", [
    { k: "صف خرید", v: counts.buy ?? "—",
      sub: `از ${counts.instruments ?? "—"} سهم` },
    { k: "صف فروش", v: counts.sell ?? "—",
      sub: `از ${counts.instruments ?? "—"} سهم` },
    { k: "نمادهای دارای گردش", v: s.traded === null || s.traded === undefined ? "—" : num(s.traded, 0),
      sub: "همین حالا، از روی تابلو" },
    { k: "آخرین عکس دفتر", v: q.age_minutes === null
        || q.age_minutes === undefined ? "—"
        : q.age_minutes < 90 ? `${num(q.age_minutes, 0)} دقیقه`
        : q.age_minutes < 48 * 60 ? `${num(q.age_minutes / 60, 1)} ساعت`
        : `${num(q.age_minutes / 1440, 0)} روز`,
      sub: q.captured_at ? faTime(q.captured_at) : "ثبت نشده" },
  ]);

  renderQueueSide("liveBuyTable", "liveBuyHint", q.buy, q, "خرید");
  renderQueueSide("liveSellTable", "liveSellHint", q.sell, q, "فروش");
  renderLiveEvents(rep.events);
  renderLiveFlow(rep.flow);
}

function renderQueueSide(tableId, hintId, rows, q, side) {
  $(hintId).innerHTML = q.captured_at
    ? `از عکس دفتر سفارش — ${ageTag(q.age_minutes)}`
    : "هیچ عکسی از دفتر سفارش ثبت نشده است.";

  if (!rows || !rows.length) {
    $(tableId).innerHTML =
      `<p class="hint">هیچ نمادی در صف ${side} نیست.</p>`;
    return;
  }

  $(tableId).innerHTML = table(
    ["نماد", "آخرین", "تغییر", "حجم صف", "سفارش‌ها", "ارزش صف (ریال)"],
    rows.map(r => [
      esc(r.symbol || r.ins_code),
      num(r.last, 0),
      signed(r.change_pct),
      money(r.queue_shares),
      num(r.queue_orders, 0),
      money(r.queue_value),
    ]));
  capRows(tableId, 10);
}

function renderLiveEvents(events) {
  if (!events || !events.length) {
    $("liveEventsTable").innerHTML =
      `<p class="hint">هیچ رویدادی ثبت نشده است.</p>`;
    return;
  }

  $("liveEventsTable").innerHTML = table(
    ["زمان", "نماد", "چه شد", "آخرین"],
    events.map(e => [
      `${tehranClock(e.observed_at)} ${ageTag(e.age_minutes)}`,
      esc(e.symbol || e.ins_code),
      esc(EVENT_FA[e.event] || e.event),
      num(e.last, 0),
    ]));
  capRows("liveEventsTable", 10);
}

function renderLiveFlow(flow) {
  flow = flow || {};

  if (!flow.names || !flow.names.length) {
    $("liveFlowTable").innerHTML = flow.captured_at
      ? `<p class="hint">هیچ نمادی به کف ۲۰ مشارکت‌کننده نرسید.</p>`
      : `<p class="hint">هنوز هیچ عکس درون‌جلسه‌ای از جریان حقیقی/حقوقی
         گرفته نشده است. جمع‌آورنده فقط در جلسه اجرا می‌شود.</p>`;
    return;
  }

  $("liveFlowTable").innerHTML =
    `<p class="hint">${flow.universe} نماد واجد شرط —
       ${ageTag(flow.age_minutes)}</p>` +
    table(["نماد", "قدرت خریدار", "خریداران", "فروشندگان", "خالص سهم حقیقی"],
      flow.names.map(r => [
        esc(r.symbol || r.ins_code),
        num(r.buyer_power, 2),
        num(r.buyers, 0),
        num(r.sellers, 0),
        money(r.net_retail_shares),
      ]));
  capRows("liveFlowTable", 10);
}

function setLiveAuto(on) {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }

  // document.hidden: a backgrounded tab polling forever is traffic nobody
  // is reading. The visibilitychange handler below resumes it.
  if (on) liveTimer = setInterval(() => {
    if (!document.hidden) loadLive();
  }, LIVE_POLL_MS);
}

on("runIndicesBtn", "click", () => loadIndices(true));
on("runSectorsBtn", "click", () => loadSectors(true));
on("runFundBtn", "click", () => loadFundamentals(true));
on("runFlowBtn", "click", () => loadMoneyflow(true));
on("runRegimeBtn", "click", () => loadRegime(true));
on("runPortfolioBtn", "click", () => loadPortfolio(true));
on("runAllocationBtn", "click", () => loadAllocation(true));
on("runOpportunitiesBtn", "click", () => loadOpportunities(true));
on("announcementsKind", "change", () => loadAnnouncements());
on("marketflowSessions", "change", () => loadMarketflow());
on("announcementsDays", "change", () => loadAnnouncements());
on("runOutlookBtn", "click", () => loadOutlook(true));
on("outlookLookupBtn", "click", lookupOutlook);
on("outlookPdfBtn", "click", openOutlookPdf);
on("quickLookupBtn", "click", quickLookup);
on("quickPdfBtn", "click", () => {
  if (!openReport($("quickSymbolInput").value)) $("quickSymbolInput").focus();
});
$("quickSymbolInput")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { ev.preventDefault(); quickLookup(); }
});
on("runTechnicalBtn", "click", () => loadTechnical(true));
on("runTopTechnicalBtn", "click", () => loadTopTechnical(true));
on("runInflowBtn", "click", () => loadInflow(true));
on("runSmartMoneyBtn", "click", () => loadSmartMoney(true));
on("runGoldBtn", "click", () => loadGold(true));
on("importGoldBtn", "click", importGold);
on("refreshLiveBtn", "click", loadLive);
on("liveAuto", "change", (e) => setLiveAuto(e.target.checked));

// A tab that was hidden while auto-refresh ran comes back showing whatever
// it had when it was hidden, which on this panel is the one thing that must
// never be stale without saying so.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && $("liveAuto")?.checked) loadLive();
});
on("runIntelBtn", "click", () => loadIntelligence(true));
on("fetchIntelBtn", "click", fetchIntelligence);
on("intelWindow", "change", () => loadIntelligence(true));

// Enter in the symbol box runs the analysis, so the button is optional.
on("techSymbolInput", "keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loadTechnical(true); }
});

/* ---------- events ---------- */

on("applyBtn", "click", loadAll);

on("advancedBtn", "click", () => {
  const row = $("advancedRow");
  const hidden = row.classList.toggle("hidden");
  $("advancedBtn").textContent = hidden ? "دوره‌ها ▾" : "دوره‌ها ▴";
});

on("resetBtn", "click", () => {
  for (const id of Object.values(PERIOD_FIELDS)) {
    if (DEFAULT_PERIODS[id] !== undefined) $(id).value = DEFAULT_PERIODS[id];
  }
  loadAll();
});

on("refreshBtn", "click", async () => {
  const btn = $("refreshBtn");
  // The template's own label, read back as withButton does rather than
  // restated: a copy here once put an English "↻ Refresh data" back on a
  // Persian button after every refresh.
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "در حال دریافت…";
  try {
    await getJSON("/api/refresh", { method: "POST" });
    pollBootstrap(true);
  } catch (err) {
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

on("themeBtn", "click", () => {
  const root = document.documentElement;
  const current = root.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  localStorage.setItem("sirat-theme", next);
  rerender();   // charts read colors from CSS tokens at render time
});

document.querySelectorAll(".toggle-table").forEach(btn => {
  const original = btn.textContent;
  btn.addEventListener("click", () => {
    const nowHidden = $(btn.dataset.target).classList.toggle("hidden");
    btn.textContent = nowHidden ? original : "پنهان کردن";
  });
});

// Evidence sections are collapsed by default, and a chart drawn inside a
// closed <details> measures its container as 0 wide -- charts.js then falls
// back to a fixed 520px, so the chart opens at the wrong size and never
// corrects itself. Redraw on the first open of each group; after that the
// resize handler keeps it right.
document.querySelectorAll("details.evidence-group").forEach(group => {
  group.addEventListener("toggle", () => {
    if (group.open) {
      hideTip();
      rerender();
    }
  });
});

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  hideTip();
  resizeTimer = setTimeout(rerender, 180);
});

/* ---------- init ---------- */

// ?theme=light|dark overrides the stored choice (handy for checking both modes).
const urlTheme = new URLSearchParams(location.search).get("theme");
const savedTheme = urlTheme || localStorage.getItem("sirat-theme");
if (savedTheme === "light" || savedTheme === "dark") {
  document.documentElement.setAttribute("data-theme", savedTheme);
}

// Remember the server-rendered window defaults so "Reset periods" can restore them.
for (const id of Object.values(PERIOD_FIELDS)) {
  if ($(id)) DEFAULT_PERIODS[id] = $(id).value;
}

// Draw the stage list before anything is requested, so the first paint already
// says what is about to happen rather than showing an empty panel.
renderTasks();
pollBootstrap();

// Fill the technical panel's symbol autocomplete once, quietly. A failure here
// costs only the suggestions -- the field still accepts a typed نماد.
getJSON("/api/universe?list=1&limit=2000").then(rep => {
  const list = $("techSymbolList");
  if (!list || !rep.instruments) return;
  list.innerHTML = rep.instruments
    .map(r => `<option value="${esc((r.symbol_fa || "").trim())}">`)
    .join("");
}).catch(() => {});

// ?tech=<symbol> deep-links straight to one instrument's technical read:
// opens the panel and runs it, so a view can be bookmarked or shared.
const techParam = new URLSearchParams(location.search).get("tech");
if (techParam) {
  const group = $("evidenceTechnical");
  if (group) group.open = true;
  const input = $("techSymbolInput");
  if (input) input.value = techParam;
  loadTechnical(true);
}

// ?intel=1 opens the Market-Intelligence panel straight away (bookmark/share,
// and it lets the panel be captured without a manual click).
if (new URLSearchParams(location.search).get("intel")) {
  const group = $("evidenceIntel");
  if (group) group.open = true;
  loadIntelligence(true);
}

// ?indices=1 does the same for the index section. No extra fetch: loadAll()
// already runs loadIndices(), so this only unfolds what is being loaded anyway.
if (new URLSearchParams(location.search).get("indices")) {
  const group = $("evidenceIndices");
  if (group) group.open = true;
}

// Tabs: the hash (#stocks) or the last tab used, else the summary.
document.querySelectorAll(".tab").forEach(b =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

on("todayProfile", "change", () => {
  try { localStorage.setItem("sirat-profile", $("todayProfile").value); } catch (e) { /* */ }
  renderToday();
});
try {
  const savedProfile = localStorage.getItem("sirat-profile");
  if (savedProfile) $("todayProfile").value = savedProfile;
} catch (e) { /* */ }

// The data panel shows one line when all is well; a click opens the detail.
on("loadPanel", "click", (ev) => {
  if (ev.target.closest("button")) return;
  $("loadPanel").classList.toggle("is-open");
});

getJSON("/api/status").then(status => {
  $("footnote").textContent =
    `${status.total_bars.toLocaleString()} کندل ذخیره‌شده` +
    (status.date_min ? ` از ${status.date_min} تا ${status.date_max}` : "") +
    ` · سرمایه‌ی پیش‌فرض ${num(status.config.equity, 0)} ${currencyName(status.config.currency)}` +
    ` · ریسک هر موقعیت ${status.config.risk_per_trade_pct}٪`;
}).catch(() => {});

/* ---------- the guide ----------
   "برو به" buttons carry data-goto="tab:elementId": open the tab, bring the
   card into view and let it glow for a moment so the eye finds it. */

function gotoCard(target) {
  const [tab, id] = String(target).split(":");
  showTab(tab);
  const node = id && $(id);
  if (!node) return;
  // The analysis tab folds its cards into <details>; a card inside a closed
  // one has no box to scroll to, so every fold around it opens first.
  for (let fold = node.closest("details"); fold; fold = fold.parentElement?.closest("details")) {
    fold.open = true;
  }
  const card = node.closest(".card") || node;
  // After the tab is laid out, or the scroll lands on a hidden element.
  requestAnimationFrame(() => {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.remove("guide-flash");
    void card.offsetWidth;
    card.classList.add("guide-flash");
  });
  // A tab opened for the first time is still filling in above the card, and
  // each table that lands pushes it down (1,491px in a test). Settle it
  // again while that happens -- only if it has drifted, so a reader who has
  // already scrolled on is not pulled back.
  const settle = () => {
    const top = card.getBoundingClientRect().top;
    if (top > window.innerHeight * 0.5 || top < -40) {
      card.scrollIntoView({ behavior: "auto", block: "start" });
    }
  };
  setTimeout(settle, 900);
  setTimeout(settle, 2200);
}

document.addEventListener("click", (ev) => {
  const button = ev.target.closest("[data-goto]");
  if (!button) return;
  ev.preventDefault();
  gotoCard(button.dataset.goto);
});

function guideSeen() {
  try { return localStorage.getItem("sirat-guide-seen") === "1"; } catch (e) { return true; }
}

function markGuideSeen() {
  try { localStorage.setItem("sirat-guide-seen", "1"); } catch (e) { /* private mode */ }
  $("guideNudge")?.classList.add("hidden");
}

on("guideNudgeClose", "click", markGuideSeen);
document.querySelector('.tab[data-tab="guide"]')?.addEventListener("click", markGuideSeen);
if (!guideSeen()) $("guideNudge")?.classList.remove("hidden");

{
  const params = new URLSearchParams(location.search);
  let first = location.hash.slice(1);
  if (params.get("tech") || params.get("intel") || params.get("indices")) first = "analysis";
  if (!first) {
    // A first visit opens on the guide; afterwards, the last tab used.
    let stored = null;
    try { stored = localStorage.getItem("sirat-tab"); } catch (e) { /* */ }
    first = stored || (guideSeen() ? "summary" : "guide");
  }
  showTab(first);
  if (first === "guide") markGuideSeen();
  // The guide fetches nothing; the data tabs still load behind it.
  prefetchTabs(TAB_LOADERS[first] && first !== "guide" ? first : "summary");
}
