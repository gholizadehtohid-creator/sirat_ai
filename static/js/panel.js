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
  "STRONG BUY CANDIDATE": "good",
  "BUY WATCHLIST": "good",
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
  gold: null, inflow: null, intel: null, smartMoney: null, allocation: null
};

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
    if (/NotFound|404/.test(message)) {
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

function num(v, digits = 2) {
  return (v === null || v === undefined || Number.isNaN(v))
    ? "n/a" : Number(v).toFixed(digits);
}

function badge(status, map = STATUS_CLASS) {
  return `<span class="badge ${map[status] || "neutral"}"><span class="dot"></span>${status}</span>`;
}

function signed(v, digits = 2, suffix = "%") {
  if (v === null || v === undefined) return "n/a";
  const cls = v > 0 ? "up" : v < 0 ? "down" : "";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(digits)}${suffix}</span>`;
}

// Rial amounts run to billions; abbreviate but keep the sign meaningful.
// A trillion tier is not decorative here: whole-market turnover is quadrillions
// of rials, and without it the money-flow totals render as "1189993.49B".
function money(v) {
  if (v === null || v === undefined) return "n/a";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(0)}K`;
  return `${sign}${a.toFixed(0)}`;
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
  node.classList.remove("is-error", "is-live");
  if (kind) node.classList.add(kind);
  node.querySelector(".icon").textContent =
    kind === "is-live" ? "✓" : kind === "is-error" ? "✕" : "⚠";
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

  b.classList.remove("is-live", "is-error");

  const prov = rep && rep.provenance;

  if (prov && prov.status !== "verified-real") {
    b.classList.add("is-error");
    b.querySelector(".icon").textContent = prov.status === "simulated" ? "⛔" : "⚠";
    const headline = prov.status === "simulated"
      ? "این قیمت‌ها شبیه‌سازی‌شده‌اند، نه داده‌ی واقعی بازار."
      : "این قیمت‌ها تأییدنشده‌اند.";
    t.innerHTML = `<strong>${headline}</strong> ${prov.detail || ""} ` +
      `هر امتیاز و هر پیشنهاد سبد در این صفحه همین سری شبیه‌سازی‌شده را توصیف می‌کند — ` +
      `نه بورس تهران را. برای بررسی کامل: <code>python authenticity.py</code>`;
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
    (snap.rows ? `، تصویر بنیادی ${snap.rows} سطر (${snap.captured_at})`
               : `، بدون تصویر بنیادی — EPS و P/E در دسترس نیست`) +
    `. برای دریافت مجدد «↻ Refresh data» را بزنید.`;
}

/* ---------- market regime ---------- */

function renderRegime(rep) {
  state.regime = rep;

  if (!rep || !rep.available) {
    banner("regimeStatus", "regimeStatusText", "is-error",
      `<strong>وضعیت بازار قابل تشخیص نیست.</strong> ${esc(rep?.reason || "")}`);
    tiles("regimeKpis", []);
    $("participationChart").innerHTML = '<div class="empty">داده‌ای نیست</div>';
    bulletList("regimeEvidence", []);
    return;
  }

  banner("regimeStatus", "regimeStatusText", "is-live",
    `<strong>${esc(rep.label)}</strong> — رأی‌گیری ${rep.votes.bull} صعودی، ` +
    `${rep.votes.bear} نزولی، ${rep.votes.sideways} خنثی. ` +
    `سقف سرمایه‌گذاری روی ${rep.max_invested_pct}٪ تنظیم شد.`);

  const cap = rep.cap_weighted || {};
  const ew = rep.equal_weighted || {};
  const part = rep.participation || {};

  tiles("regimeKpis", [
    {
      k: "رژیم بازار",
      v: badge(rep.regime, REGIME_CLASS),
      sub: rep.volatility_regime || "—"
    },
    { k: "نقد هدف", v: `${rep.cash_target_pct}%`, sub: `حداکثر ${rep.max_invested_pct}% سرمایه‌گذاری` },
    { k: "اطمینان", v: rep.confidence, sub: "سهم رأی اکثریت" },
    {
      k: "مشارکت بازار",
      v: part.above_slow_ma_pct != null ? `${part.above_slow_ma_pct}%` : "—",
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
      format: (v) => `${fmt(v, 0)}%`
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
      rep.available ? rep.regime : "");
  } catch (err) {
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

  const cls = sig.call === "BUY" ? "good" : sig.call === "SELL" ? "bad" : "neutral";
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
    ? "" : ` · اتفاق نظر ${Math.round(Math.abs(sig.strength) * 100)}٪`;

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
        ? "—" : `${num(tg.max_session_move_pct, 2)}%`,
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
    { k: "روند", v: t.trend || "n/a", sub: `RSI ${num(t.rsi, 1)}` },
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
    divergingBar($("breadthChart"), {
      data: Object.entries(breadth.spreads)
        .filter(([, v]) => v !== null)
        .map(([k, v]) => ({ label: k, value: v })),
      format: (v) => `${v > 0 ? "+" : ""}${fmt(v, 1)}pp`
    });
    $("breadthVerdict").innerHTML = `<strong>${esc(breadth.verdict)}</strong>`;
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
  "Unknown": "نامشخص"
};

const TREND_CLASS = {
  "Strong Uptrend": "good", "Uptrend": "good",
  "Weak Downtrend": "warn", "Downtrend": "bad", "Unknown": "neutral"
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
  const rr = tg.risk_reward;
  const floor = tg.min_risk_reward;
  const rrCls = (rr === null || rr === undefined) ? "neutral"
    : (floor !== null && floor !== undefined && rr < floor) ? "bad" : "good";
  const rrNote = (rr === null || rr === undefined) ? "بدون نسبت"
    : (floor !== null && floor !== undefined && rr < floor)
      ? `زیر حد ${num(floor, 1)} — این پلتفرم چنین موقعیتی را باز نمی‌کند`
      : `بالای حد ${num(floor, 1)}`;

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
      sub: `${signed(-Math.abs(tg.stop_distance_pct), 1)} · ${stopSourceFa(tg.stop_source)}` },
    { k: "سود به زیان",
      v: `<span class="badge ${rrCls}"><span class="dot"></span>${n(rr, 2)}</span>`,
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
      sub: `پهنای باند ${n(L.bb_width, 1)}%` },
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

const CALL_CLASS = { BUY: "good", SELL: "bad", HOLD: "neutral" };

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
    esc(r.status || "—"),
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
const SM_V2_CLASS = { BUY: "good", HOLD: "neutral", SELL: "bad", AVOID: "bad" };

// Market caps are read in همت on this market (۱ همت = ۱۰ هزار میلیارد ریال),
// not in the T/B abbreviations money() uses for turnover. A reader who knows
// this exchange knows instantly what "۱۲ همت" is and has to convert "120.00B".
function hemat(v) {
  return (v === null || v === undefined) ? "n/a"
    : `${(v / 1e13).toFixed(v < 1e13 ? 2 : 1)} همت`;
}

function smartMoneyFunnel(c) {
  tiles("smartMoneyFunnel", [
    { k: "کل نمادها", v: c.universe ?? "n/a",
      sub: `${c.illiquid ?? 0} زیر کف نقدشوندگی` },
    { k: "نقدشونده", v: c.liquid ?? "n/a",
      sub: `${c.dirty_data ?? 0} با داده مخدوش` },
    { k: "MFI بالای ۷۰", v: c.passed_mfi ?? "n/a",
      sub: "شرط اول — به‌تنهایی عادی است" },
    { k: "در کف محدوده", v: c.passed_range ?? "n/a",
      sub: `${c.queue_locked ?? 0} در صف، حذف شد` },
    { k: "هنوز حرکت نکرده", v: c.still_quiet ?? "n/a",
      sub: `${c.already_moved ?? 0} قبلاً حرکت کرده بودند` },
    { k: "واجد شرایط", v: c.qualified ?? "n/a",
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
    const pe = (r.pe == null || r.sector_pe == null)
      ? "n/a"
      : `<span class="${r.pe < r.sector_pe ? "up" : "down"}" ` +
        `title="P/E ${num(r.pe, 1)} در برابر میانه صنعت ${num(r.sector_pe, 1)}">` +
        `${r.pe < r.sector_pe ? "کمتر" : "بیشتر"} از گروه ` +
        `(${signed(r.pe_vs_sector, 0)})</span>`;

    const smart = r.code_to_code
      ? `<span class="badge warning"><span class="dot"></span>کد به کد ` +
        `${num(r.smart_money_ratio, 2)}</span>`
      : `<span class="badge neutral" title="${esc((r.code_to_code_reasons || [])[0] || "")}">` +
        `<span class="dot"></span>تأیید نشد</span>`;

    const sup = r.support || {};
    const support = sup.price == null
      ? "n/a"
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
            `<span class="dot"></span>V2: ${esc(v2.action)}</span>`
          : "")
    ];
  });

  $("smartMoneyTable").innerHTML = table(
    ["#", "نماد", "امتیاز", "MFI", "P/E گروه", "ارزش بازار",
     "ورود پول هوشمند", "مازاد بر بازار", "حمایت", "وضعیت"], body);

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
    ? `<a href="${esc(e.link)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>`
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
    format: (v) => `${fmt(v, 0)}%`
  });

  $("sectorFundTable").innerHTML = table(
    ["صنعت", "تعداد", "میانه‌ی بنیادی", "نرخ تأیید", "P/E صنعت", "میانه‌ی P/E", "ارزش بازار"],
    list.map((s, i) => {
      const f = s.fundamental;
      return [
        `<a href="#" class="link sector-row" data-index="${i}">${esc(s.sector_name)}</a>`,
        f.constituents,
        f.median_score ?? "—",
        f.approval_rate == null ? "—" : `${(f.approval_rate * 100).toFixed(0)}%`,
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
    format: (v) => `${fmt(v, 0)}%`
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
          t.trend || "n/a",
          b ? `${b.pct_above_slow_ma}%` : "—",
          signed(t.returns ? t.returns["60d"] : null),
          esc(s.index_method || "—")
        ];
      })
  );
}

function showSectorMembers(sector) {
  const wrap = $("sectorMembersTable");
  wrap.classList.remove("hidden");

  wrap.innerHTML =
    `<p class="hint"><strong>${esc(sector.sector_name)}</strong> — ${esc(sector.index_method)}</p>` +
    table(
      ["نماد", "شرکت", "امتیاز بنیادی", "حکم", "P/E", "بازده سود", "ارزش معاملات", "۶۰ روزه", "کندل"],
      sector.members.map(m => [
        m.symbol, esc(m.name || "—"),
        // An unjudgeable share carries a score the model could not stand
        // behind; showing the number would rank it as if it could.
        m.scored ? m.fundamental_score : "—",
        m.verdict, m.pe ?? "—",
        m.earnings_yield == null ? "—" : `${m.earnings_yield}%`,
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

function fundBadge(status) {
  const cls = status === "Strong" ? "good"
    : status === "Good" ? "good"
    : status === "Fair" ? "warning"
    : status === "Weak" ? "serious" : "critical";
  return `<span class="badge ${cls}"><span class="dot"></span>${esc(status)}</span>`;
}

function fundRows(list) {
  return table(
    ["نماد", "صنعت", "بنیاد", "امتیاز", "پوشش", "P/E", "P/B", "ROE",
     "حاشیه خالص", "رشد فروش", "بدهی", "کیفیت سود", "دوره"],
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
          ? `<span class="badge ${coverCls}"><span class="dot"></span>${covered}%</span>`
          : `${covered}%`,
        num(x.pe, 1),
        num(x.pb, 1),
        x.roe == null ? "—" : `${num(x.roe, 0)}%`,
        x.net_margin == null ? "—" : `${num(x.net_margin, 0)}%`,
        signed(x.revenue_growth, 0),
        x.debt_ratio == null ? "—" : `${num(x.debt_ratio, 0)}%`,
        num(x.earnings_quality, 2),
        period
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
        <div class="t-row"><span>ROE</span><b>${num(x.roe, 0)}%</b></div>
        <div class="t-row"><span>P/E</span><b>${num(x.pe, 1)}</b></div>
        <div class="t-row"><span>رشد فروش</span><b>${num(x.revenue_growth, 0)}%</b></div>
        <div class="t-row"><span>پوشش مدل</span><b>${Math.round((row.weight_covered || 0) * 100)}%</b></div>`;
    }
  });

  $("fundTable").innerHTML = fundRows(top);
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
  const cls = label === "ورود پول حقیقی" ? "good"
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
    `${cov.with_flow || 0} از ${cov.equities || 0} سهم پوشش دارند.`);

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
      v: `${market.breadth_pct.toFixed(0)}%`,
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
    format: (v) => `${fmt(v, 0)}%`,
    tooltip: (d) => {
      const row = extremes.find(r => r.symbol === d.label) || {};
      return `<div class="t-title">${esc(d.label)}</div>
        <div class="t-row"><span>خالص حقیقی</span><b>${money(row.net_individual)}</b></div>
        <div class="t-row"><span>٪ از گردش</span><b>${num(row.net_pct, 1)}%</b></div>
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
        <div class="t-row"><span>٪ از گردش</span><b>${num(row.net_pct, 1)}%</b></div>
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
  const cls = v === "APPROVED" ? "good"
    : v === "PROVISIONAL" ? "warning"
    : v === "REJECTED" ? "critical" : "neutral";
  return `<span class="badge ${cls}"><span class="dot"></span>${v}</span>`;
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
        r.symbol,
        ...FUND_COMPONENTS.map(k => r.fundamental_breakdown?.[k] ?? "—"),
        r.fundamental_score,
        `${Math.round((r.weight_covered || 0) * 100)}%`
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
            `<strong>${r.symbol}</strong>`,
            esc((r.name || "").slice(0, 26)),
            verdictBadge(r.verdict),
            t.score ?? "—",
            fundCell,
            r.risk_score ?? "—",
            r.flow_score ?? "—",
            `<strong>${r.overall_score ?? "—"}</strong>`,
            t.trend ?? "—",
            r.final_status ?? "—",
            cr.pe != null ? num(cr.pe, 1) : (m.pe == null ? "n/a" : m.pe),
            cr.roe == null ? "—" : `${num(cr.roe, 0)}%`,
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
        sample.map(r => [r.symbol, esc((r.name || "").slice(0, 34)), r.board || "—",
          esc((r.reasons || [])[0] || "—")]))
    : '<div class="empty">چیزی کنار گذاشته نشد</div>';

  const snap = rep.snapshot || {};
  $("coverageTable").innerHTML = table(
    ["ورودی", "وضعیت", "جزئیات"],
    [
      ["فهرست نمادها", rep.counts.universe ? "✓ بارگذاری شد" : "✗ موجود نیست",
        `${rep.counts.universe} سهم عادی`],
      ["سابقه‌ی قیمت", rep.have_history ? "◐ جزئی" : "✗ موجود نیست",
        `${rep.have_history} از ${rep.counts.universe} سهم کندل ذخیره‌شده دارند`],
      ["تصویر بنیادی (EPS، P/E، ارزش بازار)",
        snap.rows ? "✓ بارگذاری شد" : "✗ موجود نیست",
        snap.rows ? `${snap.rows} سطر، ثبت ${snap.captured_at}`
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

function renderPortfolio(rep) {
  state.portfolio = rep;

  if (!rep || !rep.ok) {
    banner("portfolioStatus", "portfolioStatusText", "is-error",
      `<strong>ساخت سبد انجام نشد.</strong> ${esc(rep?.error || "")}`);
    tiles("portfolioKpis", []);
    $("portfolioTable").innerHTML = "";
    $("allocationChart").innerHTML = '<div class="empty">سبدی نیست</div>';
    $("riskTable").innerHTML = "";
    bulletList("riskNotes", []);
    bulletList("portfolioNotes", []);
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
    tiles("portfolioKpis", []);
    $("portfolioTable").innerHTML = "";
    $("allocationChart").innerHTML = '<div class="empty">سبدی نیست</div>';
    bulletList("portfolioNotes", basket.notes);
    return;
  }

  // Which engine built this book is part of the result, not a footnote: the two
  // produce completely different lists from the same data, and a table that does
  // not say which one it came from is a table nobody can check.
  const engine = rep.engine === "v2"
    ? "موتور سوئینگ (v2)"
    : "موتور ترکیبی (v1)";

  banner("portfolioStatus", "portfolioStatusText", "is-live",
    `<strong>${totals.positions} موقعیت در ${totals.sectors} صنعت.</strong> ` +
    `${basket.candidates} نماد به حد نصاب امتیاز ${settings.min_score} رسیدند. ` +
    `ریسک کل اگر همه‌ی حد ضررها فعال شوند: ${totals.total_risk_pct}٪ سرمایه. ` +
    `<span class="hint-inline">— ${engine}</span>`);

  const vol = riskRep.volatility || {};

  tiles("portfolioKpis", [
    { k: "موقعیت‌ها", v: totals.positions, sub: `در ${totals.sectors} صنعت` },
    {
      k: "سرمایه‌گذاری‌شده",
      v: `${totals.invested_pct}%`,
      sub: `${money(totals.invested)} ${basket.currency}`
    },
    {
      k: "نقد",
      v: `${(100 - totals.invested_pct).toFixed(1)}%`,
      sub: `${money(totals.cash)} ${basket.currency}`
    },
    {
      k: "ریسک کل",
      v: `<span class="down">${totals.total_risk_pct}%</span>`,
      sub: "اگر همه‌ی حد ضررها بخورند"
    },
    {
      k: "نوسان سبد",
      v: vol.annualised_pct != null ? `${vol.annualised_pct}%` : "—",
      sub: vol.undiversified_pct != null
        ? `بدون تنوع: ${vol.undiversified_pct}%` : "سالانه"
    },
    {
      k: "نسبت تنوع‌بخشی",
      v: vol.diversification_ratio ?? "—",
      sub: "هرچه بالاتر، تنوع مؤثرتر"
    }
  ]);

  $("portfolioTable").innerHTML = table(
    ["#", "نماد", "شرکت", "صنعت", "بنیادی", "تکنیکال", "ترکیبی",
     "قیمت", "محدوده‌ی ورود", "حد ضرر", "حد سود", "R/R",
     "تعداد سهم", "ارزش", "وزن", "ریسک", "قید"],
    positions.map(p => [
      p.rank,
      `<strong>${p.symbol}</strong>`,
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
      `${num(p.entry_low, 0)} – ${num(p.entry_high, 0)}`,
      `<span class="down">${num(p.stop_price, 0)}</span>`,
      `<span class="up">${num(p.target_price, 0)}</span>`,
      p.risk_reward != null ? `1:${p.risk_reward}` : "—",
      (p.shares ?? 0).toLocaleString(),
      money(p.position_value),
      p.weight_pct != null ? `${p.weight_pct}%` : "—",
      p.risk_pct != null ? `${p.risk_pct}%` : "—",
      esc(p.scaled_by || p.bound_by)
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
      format: (v) => `${fmt(v, 1)}%`
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
      ["نوسان سالانه", vol.annualised_pct != null ? `${vol.annualised_pct}%` : "—",
        "انحراف معیار بازده سبد"],
      ["اگر کاملاً همبسته بود", vol.undiversified_pct != null ? `${vol.undiversified_pct}%` : "—",
        "میانگین وزنی نوسان تک‌تک نمادها"],
      ["نسبت تنوع‌بخشی", vol.diversification_ratio ?? "—",
        "فاصله‌ی این دو، همان تنوع است"],
      ["میانگین همبستگی جفتی", corr.average ?? "—",
        "نزدیک ۱ یعنی تنوع اسمی است"],
      [`VaR ${varRep.confidence ? Math.round(varRep.confidence * 100) : 95}٪ روزانه`,
        varRep.historical_pct != null
          ? `${varRep.historical_pct}% (${money(varRep.historical_rials)})` : "—",
        "بدترین روز در ۹۵٪ موارد"],
      ["CVaR / زیان مورد انتظار",
        varRep.conditional_pct != null
          ? `${varRep.conditional_pct}% (${money(varRep.conditional_rials)})` : "—",
        "میانگین زیان وقتی از VaR رد شود"],
      ["بدترین جلسه‌ی مشاهده‌شده",
        varRep.worst_day_pct != null ? `${varRep.worst_day_pct}%` : "—",
        `در ${varRep.sessions || 0} جلسه`],
      [`بتا نسبت به ${esc(riskRep.benchmark || "شاخص")}`, beta.beta ?? "—",
        beta.r_squared != null ? `R² ${beta.r_squared}` : "حساسیت به بازار"],
      ["حداکثر افت تاریخی",
        dd.max_drawdown_pct != null ? `${dd.max_drawdown_pct}%` : "—",
        dd.peak_date ? `${dd.peak_date} → ${dd.trough_date}` : "قله تا کف"],
      ["تعداد مؤثر موقعیت‌ها", conc.effective_positions ?? "—",
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
        cls: "done",
        count: step.seconds != null ? `${step.seconds}s` : "",
        note: step.detail || st.detail || "fetched"
      };
    case "kept":
      return { cls: "done", count: "", note: step.detail || "already current" };
    case "failed":
      return { cls: "failed", count: "", note: step.detail || "failed" };
    case "skipped":
    case "cooling-off":
      return { cls: "warn", count: "", note: step.detail || step.action };
    default:
      break;
  }

  if (p.running && (p.planned || []).includes(name)) {
    return { cls: "queued", count: "", note: `queued — ${st.detail || ""}` };
  }

  // Never run this session: report what is stored, not a fake outcome.
  return {
    cls: st.needs_fetch ? "warn" : st.present ? "done" : "queued",
    count: "",
    note: st.detail || "not checked yet"
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
    $("loadPct").textContent = `${pct.toFixed(0)}%`;

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
    $("loadStage").textContent = [
      absent.length ? `Never downloaded: ${names(absent)}` : "",
      stale.length ? `Out of date: ${names(stale)}` : ""
    ].filter(Boolean).join("  ·  ") + " — press “Fetch what is missing or stale”.";

  } else {
    fill.style.width = "100%";
    bar.classList.add(failed.length ? "is-error" : "is-done");
    bar.setAttribute("aria-valuenow", "100");
    $("loadPct").textContent = failed.length ? "!" : "✓";
    $("loadStage").textContent = failed.length
      ? `Finished — ${failed.length} stage(s) failed; the rest is stored.`
      : "همه‌ی داده‌ها موجود و به‌روز است — all data present and current.";
  }

  // Recede only when there is genuinely nothing to look at. A panel reporting
  // missing data or a failed stage should not be the faintest thing on screen.
  $("loadPanel").classList.toggle(
    "is-idle", !running && !missing.length && !failed.length);

  const elapsed = clock(overall.elapsed_seconds);
  $("loadElapsed").textContent = elapsed
    ? (running ? `${elapsed} elapsed` : `${elapsed} total`) : "";

  $("loadLog").textContent = (p.log || []).length
    ? p.log.map(l => `${(l.at || "").replace("T", " ").replace("+00:00", "")}  ${l.message}`).join("\n")
    : "Nothing fetched this session.";
}

function renderTasks() {
  if (!$("taskSteps")) return;

  $("taskSteps").innerHTML = TASKS.map((t) => {
    const s = taskState[t.id] || { state: "queued", detail: "not started" };
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
    $("loadStage").textContent = `Progress unavailable — ${err.message}`;
    console.error(err);
  } finally {
    bootPolling = false;
  }

  bootTimer = setTimeout(() => pollBootstrap(),
    (bootRunning || fast) ? BOOT_POLL_FAST : BOOT_POLL_IDLE);
}

on("loadLogBtn", "click", () => {
  const hidden = $("loadLog").classList.toggle("hidden");
  $("loadLogBtn").textContent = hidden ? "Show log" : "Hide log";
});

on("loadFetchBtn", "click", async () => {
  const btn = $("loadFetchBtn");
  btn.disabled = true;
  $("loadStage").textContent = "Starting the download…";
  try {
    await getJSON("/api/bootstrap", { method: "POST" });
  } catch (err) {
    $("loadStage").textContent = `Could not start the fetch — ${err.message}`;
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
    const ev = p.evidence === "tested"
      ? `<span class="badge good"><span class="dot"></span>آزموده‌شده</span>`
      : `<span class="badge neutral"><span class="dot"></span>توصیفی</span>`;
    return [
      `<strong>${esc(p.label)}</strong><br>${ev}`,
      weights,
      amounts,
      st ? `${num(st.cagr_pct, 1)}٪` : "n/a",
      st ? signed(st.max_drawdown_pct, 1) : "n/a",
      st ? signed(st.worst_12m_pct, 1) : "n/a",
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

  banner("allocationStatus", "allocationStatusText", h ? "is-live" : "",
    h ? `سابقه‌ی ${esc(h.from)} تا ${esc(h.to)} — صندوق درآمد ثابت در همین ` +
        `دوره ${num(h.fixed_income.cagr_pct, 0)}٪ در سال فرض شده است.`
      : "سابقه‌ی شاخص یا صندوق‌های طلا در دیتابیس نیست — ارقام تاریخی " +
        "محاسبه نشد.");
}

async function loadAllocation(force = false) {
  const btn = $("runAllocationBtn");
  if (btn) btn.disabled = true;

  try {
    const rep = await getJSON(`/api/allocation?${query()}${force ? "&refresh=1" : ""}`);
    renderAllocation(rep);
  } catch (err) {
    state.allocation = null;
    banner("allocationStatus", "allocationStatusText", "is-error",
      `<strong>تخصیص محاسبه نشد.</strong> ${esc(err.message)}`);
    console.error(err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadAll() {
  // First because it is the cheapest (three indexed reads plus one session
  // probe) and the only panel whose value expires. A reader who opens the
  // page during a session should not watch the basket build before being
  // told whether the market is even trading.
  loadLive();
  loadAllocation();
  loadRegime();
  loadIndices();
  loadSectors();
  loadFundamentals();
  loadMoneyflow();
  loadIntelligence();
  await loadScreen();
  await loadPortfolio();
}

function rerender() {
  if (state.allocation) renderAllocation(state.allocation);
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
}

/* ---------- import actions ---------- */

async function withButton(btn, label, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try {
    await fn();
  } catch (err) {
    banner("screenStatus", "screenStatusText", "is-error",
      `<strong>${esc(original)}:</strong> ${esc(err.message)}`);
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
  }));

// One request per symbol over the whole universe: slow enough that firing it
// by accident deserves a confirmation.
on("importSectorsBtn", "click", () => {
  const count = (state.screen && state.screen.counts)
    ? state.screen.counts.universe : "all";
  if (!confirm(
    `دریافت صنایع برای هر سهم یک درخواست جداگانه می‌فرستد (${count} سهم) و ` +
    `چند دقیقه طول می‌کشد. ادامه می‌دهید؟`
  )) return;
  withButton($("importSectorsBtn"), "در حال دریافت…", async () => {
    await getJSON("/api/import/sectors", { method: "POST" });
    await loadSectors(true);
  });
});

// Same cost shape as the sector import: one request per symbol across the
// whole universe, minutes long, so it asks first.
on("importFlowBtn", "click", () => {
  const count = (state.screen && state.screen.counts)
    ? state.screen.counts.universe : "all";
  if (!confirm(
    `دریافت داده‌ی حقیقی/حقوقی برای هر سهم یک درخواست جداگانه می‌فرستد ` +
    `(${count} سهم) و چند دقیقه طول می‌کشد. ادامه می‌دهید؟`
  )) return;
  withButton($("importFlowBtn"), "در حال دریافت…", async () => {
    await getJSON("/api/import/flow", { method: "POST" });
    await loadMoneyflow(true);
    // The screen's combined score is tilted by flow, so it is now stale.
    await loadScreen(true);
  });
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
  }));

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

function ageTag(minutes) {
  if (minutes === null || minutes === undefined) return "";
  const stale = minutes > LIVE_STALE_MINUTES;
  const text = minutes < 90
    ? `${minutes.toFixed(0)} دقیقه پیش`
    : `${(minutes / 60).toFixed(1)} ساعت پیش`;
  return `<span class="age-tag${stale ? " stale" : ""}">${text}${
    stale ? " — کهنه" : ""}</span>`;
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
    : `${s.traded} نماد گردش معامله دارند`;

  if (tick) {
    detail += ` · آخرین تیک جمع‌آورنده: ${esc(tick.outcome)} ${
      ageTag(tick.age_minutes)}`;
  } else {
    detail += " · جمع‌آورنده هنوز هیچ تیکی ثبت نکرده است";
  }

  banner("liveStatus", "liveStatusText", kind,
         `<strong>${headline}</strong> — ${detail}`);

  const q = rep.queues || {};

  tiles("liveKpis", [
    { k: "صف خرید", v: counts.buy ?? "n/a",
      sub: `از ${counts.instruments ?? "n/a"} سهم` },
    { k: "صف فروش", v: counts.sell ?? "n/a",
      sub: `از ${counts.instruments ?? "n/a"} سهم` },
    { k: "نمادهای دارای گردش", v: s.traded ?? "n/a",
      sub: "همین حالا، از روی تابلو" },
    { k: "آخرین عکس دفتر", v: q.age_minutes === null
        || q.age_minutes === undefined ? "—" : `${q.age_minutes.toFixed(0)}′`,
      sub: q.captured_at ? esc(q.captured_at.slice(11, 16)) + " UTC" : "ثبت نشده" },
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
      `${esc((e.observed_at || "").slice(11, 19))} ${ageTag(e.age_minutes)}`,
      esc(e.symbol || e.ins_code),
      esc(EVENT_FA[e.event] || e.event),
      num(e.last, 0),
    ]));
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
  btn.disabled = true;
  btn.textContent = "در حال دریافت…";
  try {
    await getJSON("/api/refresh", { method: "POST" });
    pollBootstrap(true);
  } catch (err) {
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Refresh data";
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
    btn.textContent = nowHidden ? original : "Hide";
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

getJSON("/api/status").then(status => {
  $("footnote").textContent =
    `${status.total_bars.toLocaleString()} کندل ذخیره‌شده` +
    (status.date_min ? ` از ${status.date_min} تا ${status.date_max}` : "") +
    ` · سرمایه‌ی پیش‌فرض ${status.config.equity.toLocaleString()} ${status.config.currency}` +
    ` · ریسک هر موقعیت ${status.config.risk_per_trade_pct}%`;
}).catch(() => {});

loadAll();
